"""Etsy OAuth 2.0 connection setup — configuration, PKCE, tokens, secure store.

This module is **connection only**. It builds the authorization URL, validates
the OAuth ``state`` and PKCE verifier on the callback, exchanges an
authorization code for tokens, refreshes an expiring token, and reports a typed
status. It never lists, drafts, publishes, prices, refunds, messages or spends:
the Etsy adapter's approval gates remain the only path to those operations.

Provider I/O is an injected ``OAuthTransport``. The module itself imports no
network client, so a unit test can exercise the whole flow with a fake and a
live host must inject the real transport explicitly.

Tokens live under ``$QUBIT_HOME/voice/state/integrations/etsy`` with a ``0700``
directory and ``0600`` files, written atomically. A status payload contains
timestamps and booleans only — never an access token, refresh token, client
secret or any value derived from them.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from ..paths import VOICE_SUBDIR, qubit_home

# -- stable, typed codes ----------------------------------------------------
ETSY_NOT_CONFIGURED = "etsy_not_configured"
ETSY_AUTHORIZATION_REQUIRED = "etsy_authorization_required"
ETSY_CONNECTED = "etsy_connected"
ETSY_EXPIRED = "etsy_expired"
ETSY_ERROR = "etsy_error"
ETSY_BAD_INPUT = "etsy_bad_input"
ETSY_STATE_INVALID = "etsy_state_invalid"
ETSY_PKCE_MISMATCH = "etsy_pkce_mismatch"
ETSY_TRANSPORT_ERROR = "etsy_transport_error"
ETSY_EXCHANGE_FAILED = "etsy_exchange_failed"
ETSY_REFRESH_FAILED = "etsy_refresh_failed"
ETSY_STORE_ERROR = "etsy_store_error"

#: The canonical typed statuses the Office status route serves.
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_AUTHORIZATION_REQUIRED = "authorization_required"
STATUS_CONNECTED = "connected"
STATUS_EXPIRED = "expired"
STATUS_ERROR = "error"

#: Etsy Open API v3 OAuth endpoints (official documentation).
DEFAULT_AUTHORIZE_URL = "https://www.etsy.com/oauth/connect"
DEFAULT_TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
DEFAULT_SCOPES: tuple[str, ...] = (
    "shops_r",
    "listings_r",
    "transactions_r",
    "profile_r",
)

#: A pending authorization is short-lived; a stale callback is refused.
PENDING_TTL_SECONDS = 600.0
#: Refresh early so a token does not expire mid-request.
REFRESH_SKEW_SECONDS = 60.0

SCHEMA = "qubit-etsy-oauth/1"
PENDING_SCHEMA = "qubit-etsy-oauth-pending/1"

_ENV_CLIENT_ID = "QUBIT_ETSY_CLIENT_ID"
_ENV_CLIENT_SECRET = "QUBIT_ETSY_CLIENT_SECRET"
_ENV_REDIRECT_URI = "QUBIT_ETSY_REDIRECT_URI"


class EtsyAuthError(Exception):
    """A typed Etsy OAuth refusal. ``code`` is stable API.

    ``detail`` is safe to serve: it never contains a token, secret, verifier or
    provider response body.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "state": "REFUSED",
            "code": self.code,
            "detail": self.detail,
        }


@runtime_checkable
class OAuthTransport(Protocol):
    """The provider seam. A fake in tests; the HTTP client in production."""

    def request(
        self,
        method: str,
        url: str,
        *,
        form: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Mapping[str, Any]:  # pragma: no cover - protocol
        ...


def _as_text(value: Any, *, max_len: int = 4096) -> str:
    if value is None:
        return ""
    return str(value).strip()[:max_len]


def _first_text(*values: Any) -> str:
    for value in values:
        clean = _as_text(value)
        if clean:
            return clean
    return ""


def _normalise_scopes(value: Any) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_SCOPES
    if isinstance(value, str):
        parts = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple, set)):
        parts = [str(item) for item in value]
    else:
        return DEFAULT_SCOPES
    cleaned = tuple(dict.fromkeys(part.strip() for part in parts if part.strip()))
    return cleaned or DEFAULT_SCOPES


def _cfg_get(cfg: Any, *path: str, default: Any = None) -> Any:
    """Read a nested key from a Config, a mapping, or ``None``."""
    if cfg is None:
        return default
    if isinstance(cfg, Mapping):
        node: Any = cfg
        for key in path:
            if not isinstance(node, Mapping) or key not in node:
                return default
            node = node[key]
        return node
    getter = getattr(cfg, "get", None)
    if callable(getter):
        try:
            return getter(*path, default=default)
        except TypeError:
            try:
                return getter(*path)
            except Exception:  # noqa: BLE001 - defensive duck typing
                return default
    return default


@dataclass(frozen=True, slots=True)
class EtsyOAuthConfig:
    """The three required OAuth settings plus endpoint/scope overrides."""

    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    scopes: tuple[str, ...] = DEFAULT_SCOPES
    authorize_url: str = DEFAULT_AUTHORIZE_URL
    token_url: str = DEFAULT_TOKEN_URL

    @property
    def missing(self) -> tuple[str, ...]:
        missing: list[str] = []
        if not self.client_id:
            missing.append("client_id")
        if not self.client_secret:
            missing.append("client_secret")
        if not self.redirect_uri:
            missing.append("redirect_uri")
        return tuple(missing)

    @property
    def configured(self) -> bool:
        return not self.missing

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "EtsyOAuthConfig":
        source = data or {}
        return cls(
            client_id=_as_text(source.get("client_id")),
            client_secret=_as_text(source.get("client_secret")),
            redirect_uri=_as_text(source.get("redirect_uri")),
            scopes=_normalise_scopes(source.get("scopes")),
            authorize_url=_first_text(
                source.get("authorize_url"), DEFAULT_AUTHORIZE_URL
            ),
            token_url=_first_text(source.get("token_url"), DEFAULT_TOKEN_URL),
        )

    @classmethod
    def from_config(cls, cfg: Any) -> "EtsyOAuthConfig":
        """Resolve ``etsy.*`` from a Config/mapping, with env overrides.

        Environment variables exist so an operator can keep the client secret
        out of ``voice.yaml``: ``QUBIT_ETSY_CLIENT_ID``,
        ``QUBIT_ETSY_CLIENT_SECRET`` and ``QUBIT_ETSY_REDIRECT_URI``.
        """
        section = _cfg_get(cfg, "etsy", default=None)
        if not isinstance(section, Mapping):
            section = {}
        oauth = section.get("oauth")
        if isinstance(oauth, Mapping):
            section = {**section, **oauth}
        return cls.from_mapping(
            {
                "client_id": _first_text(
                    os.environ.get(_ENV_CLIENT_ID), section.get("client_id")
                ),
                "client_secret": _first_text(
                    os.environ.get(_ENV_CLIENT_SECRET), section.get("client_secret")
                ),
                "redirect_uri": _first_text(
                    os.environ.get(_ENV_REDIRECT_URI), section.get("redirect_uri")
                ),
                "scopes": section.get("scopes"),
                "authorize_url": section.get("authorize_url"),
                "token_url": section.get("token_url"),
            }
        )


def default_token_path() -> Path:
    """``$QUBIT_HOME/voice/state/integrations/etsy/oauth.json``."""
    return (
        qubit_home()
        / VOICE_SUBDIR
        / "state"
        / "integrations"
        / "etsy"
        / "oauth.json"
    )


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically write one JSON file with ``0600`` in a ``0700`` directory."""
    parent = path.parent
    _private_dir(parent)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise EtsyAuthError(
            ETSY_STORE_ERROR,
            f"the Etsy OAuth store could not be read ({type(exc).__name__})",
        ) from exc
    if not isinstance(data, dict):
        raise EtsyAuthError(
            ETSY_STORE_ERROR, "the Etsy OAuth store is not a JSON object"
        )
    return data


class EtsyTokenStore:
    """The private on-disk token/pending store. No other module writes it."""

    def __init__(
        self,
        path: Path | str | None = None,
        *,
        pending_path: Path | str | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else default_token_path()
        self.pending_path = (
            Path(pending_path)
            if pending_path is not None
            else self.path.with_name("pending.json")
        )

    # -- tokens --------------------------------------------------------
    def load(self) -> dict[str, Any] | None:
        return _read_json(self.path)

    def save(self, record: Mapping[str, Any]) -> None:
        try:
            _write_private_json(self.path, record)
        except OSError as exc:
            raise EtsyAuthError(
                ETSY_STORE_ERROR,
                f"the Etsy OAuth token could not be stored ({type(exc).__name__})",
            ) from exc

    def clear(self) -> None:
        for target in (self.path, self.pending_path):
            try:
                target.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise EtsyAuthError(
                    ETSY_STORE_ERROR,
                    f"the Etsy OAuth store could not be cleared ({type(exc).__name__})",
                ) from exc

    # -- pending authorization ----------------------------------------
    def load_pending(self) -> dict[str, Any] | None:
        return _read_json(self.pending_path)

    def save_pending(self, record: Mapping[str, Any]) -> None:
        try:
            _write_private_json(self.pending_path, record)
        except OSError as exc:
            raise EtsyAuthError(
                ETSY_STORE_ERROR,
                f"the Etsy authorization state could not be stored"
                f" ({type(exc).__name__})",
            ) from exc

    def clear_pending(self) -> None:
        try:
            self.pending_path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise EtsyAuthError(
                ETSY_STORE_ERROR,
                f"the Etsy authorization state could not be cleared"
                f" ({type(exc).__name__})",
            ) from exc


_VERIFIER_RE = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")


def _safe_compare(left: str, right: str) -> bool:
    """Constant-time text compare that refuses non-ASCII instead of raising."""
    try:
        return hmac.compare_digest(left, right)
    except TypeError:
        return False


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def pkce_challenge(verifier: str) -> str:
    """The S256 challenge for a PKCE verifier (RFC 7636)."""
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def _valid_token(record: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(record, Mapping):
        return None
    access = _as_text(record.get("access_token"))
    if not access:
        return None
    refresh = _as_text(record.get("refresh_token"))
    try:
        expires_at = float(record.get("expires_at"))
    except (TypeError, ValueError):
        expires_at = 0.0
    try:
        obtained_at = float(record.get("obtained_at"))
    except (TypeError, ValueError):
        obtained_at = 0.0
    scope = _as_text(record.get("scope"))
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": _first_text(record.get("token_type"), "Bearer"),
        "expires_at": expires_at,
        "obtained_at": obtained_at,
        "scope": scope,
    }


def _token_record(
    response: Mapping[str, Any],
    *,
    now: float,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Normalise a token endpoint response; ``None`` when unusable."""
    access = _as_text(response.get("access_token"))
    if not access:
        return None
    previous = previous or {}
    refresh = _first_text(response.get("refresh_token"), previous.get("refresh_token"))
    try:
        expires_in = int(response.get("expires_in") or 0)
    except (TypeError, ValueError):
        expires_in = 0
    if expires_in <= 0:
        expires_in = 3600
    try:
        obtained_at = float(previous.get("obtained_at") or now)
    except (TypeError, ValueError):
        obtained_at = now
    return {
        "schema": SCHEMA,
        "access_token": access,
        "refresh_token": refresh,
        "token_type": _first_text(response.get("token_type"), "Bearer"),
        "expires_at": now + float(expires_in),
        "obtained_at": obtained_at,
        "scope": _first_text(response.get("scope"), previous.get("scope")),
    }


class EtsyOAuth:
    """The Etsy OAuth connection lifecycle over an injected transport."""

    def __init__(
        self,
        config: EtsyOAuthConfig,
        *,
        transport: OAuthTransport | None = None,
        store: EtsyTokenStore | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self._transport = transport
        self._store = store or EtsyTokenStore()
        self._clock = clock or time.time

    @classmethod
    def from_config(
        cls,
        cfg: Any,
        *,
        transport: OAuthTransport | None = None,
        store: EtsyTokenStore | None = None,
        clock: Callable[[], float] | None = None,
    ) -> "EtsyOAuth":
        return cls(
            EtsyOAuthConfig.from_config(cfg),
            transport=transport,
            store=store,
            clock=clock,
        )

    # -- status --------------------------------------------------------
    def status(self, *, now: float | None = None) -> dict[str, Any]:
        """The typed connection status. Never includes a token or secret."""
        moment = self._clock() if now is None else float(now)
        config = self.config
        payload: dict[str, Any] = {
            "platform": "etsy",
            "configured": config.configured,
            "missing": list(config.missing),
            "scopes": list(config.scopes),
            "redirect_uri": config.redirect_uri,
            "connected": False,
            "pending": False,
        }
        if not config.configured:
            payload.update(
                state=STATUS_NOT_CONFIGURED,
                code=ETSY_NOT_CONFIGURED,
                detail=(
                    "Etsy OAuth is not configured; set "
                    + ", ".join(config.missing)
                ),
            )
            return payload

        try:
            pending = self._store.load_pending()
            record = self._store.load()
        except EtsyAuthError as exc:
            payload.update(
                state=STATUS_ERROR,
                code=exc.code,
                detail=exc.detail,
            )
            return payload

        if isinstance(pending, Mapping):
            try:
                pending_expires = float(pending.get("expires_at") or 0)
            except (TypeError, ValueError):
                pending_expires = 0.0
            if pending_expires > moment:
                payload["pending"] = True
                payload["pending_expires_at"] = pending_expires

        token = _valid_token(record)
        if token is None:
            if record is not None:
                payload.update(
                    state=STATUS_ERROR,
                    code=ETSY_STORE_ERROR,
                    detail="the stored Etsy OAuth record has no access token",
                )
                return payload
            payload.update(
                state=STATUS_AUTHORIZATION_REQUIRED,
                code=ETSY_AUTHORIZATION_REQUIRED,
                detail="Etsy OAuth is configured but no account is authorized",
            )
            return payload

        expires_at = float(token["expires_at"])
        if expires_at <= moment:
            payload.update(
                state=STATUS_EXPIRED,
                code=ETSY_EXPIRED,
                detail=(
                    "the Etsy access token has expired; refresh it or"
                    " reconnect the account"
                ),
                expires_at=expires_at,
                has_refresh=bool(token["refresh_token"]),
                scope=token["scope"],
            )
            return payload

        payload.update(
            state=STATUS_CONNECTED,
            code=ETSY_CONNECTED,
            detail="Etsy is connected",
            connected=True,
            expires_at=expires_at,
            expires_in=max(0, int(expires_at - moment)),
            refresh_recommended=(
                expires_at - moment <= REFRESH_SKEW_SECONDS
            ),
            obtained_at=token["obtained_at"],
            has_refresh=bool(token["refresh_token"]),
            scope=token["scope"],
        )
        return payload

    # -- authorization -------------------------------------------------
    def begin_authorization(
        self,
        *,
        now: float | None = None,
        state: str | None = None,
        code_verifier: str | None = None,
    ) -> dict[str, Any]:
        """Create pending state/PKCE and return the authorization URL."""
        moment = self._clock() if now is None else float(now)
        if not self.config.configured:
            raise EtsyAuthError(
                ETSY_NOT_CONFIGURED,
                "Etsy OAuth is not configured; missing "
                + ", ".join(self.config.missing),
            )
        chosen_state = _as_text(state) or secrets.token_urlsafe(32)
        verifier = _as_text(code_verifier) or secrets.token_urlsafe(64)
        if not _VERIFIER_RE.match(verifier):
            raise EtsyAuthError(
                ETSY_BAD_INPUT,
                "the PKCE verifier must be 43-128 unreserved characters",
            )
        challenge = pkce_challenge(verifier)
        pending = {
            "schema": PENDING_SCHEMA,
            "state": chosen_state,
            "code_verifier": verifier,
            "code_challenge": challenge,
            "created_at": moment,
            "expires_at": moment + PENDING_TTL_SECONDS,
            "redirect_uri": self.config.redirect_uri,
        }
        self._store.save_pending(pending)
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": " ".join(self.config.scopes),
            "state": chosen_state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        from urllib.parse import urlencode

        separator = "&" if "?" in self.config.authorize_url else "?"
        url = f"{self.config.authorize_url}{separator}{urlencode(params)}"
        return {
            "url": url,
            "state": chosen_state,
            "expires_at": pending["expires_at"],
        }

    def complete_authorization(
        self,
        code: str,
        state: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Validate state/PKCE, exchange the code, store tokens, return status."""
        moment = self._clock() if now is None else float(now)
        if not self.config.configured:
            raise EtsyAuthError(
                ETSY_NOT_CONFIGURED,
                "Etsy OAuth is not configured; missing "
                + ", ".join(self.config.missing),
            )
        clean_code = _as_text(code)
        clean_state = _as_text(state)
        if not clean_code or not clean_state:
            raise EtsyAuthError(
                ETSY_BAD_INPUT,
                "the Etsy callback requires both code and state",
            )
        pending = self._store.load_pending()
        if not isinstance(pending, Mapping):
            raise EtsyAuthError(
                ETSY_STATE_INVALID,
                "no Etsy authorization is pending; start the connect flow again",
            )
        expected_state = _as_text(pending.get("state"))
        if not expected_state or not _safe_compare(clean_state, expected_state):
            raise EtsyAuthError(
                ETSY_STATE_INVALID,
                "the Etsy callback state does not match the pending authorization",
            )
        try:
            expires_at = float(pending.get("expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0.0
        if expires_at and expires_at <= moment:
            self._store.clear_pending()
            raise EtsyAuthError(
                ETSY_STATE_INVALID,
                "the pending Etsy authorization has expired; start again",
            )
        verifier = _as_text(pending.get("code_verifier"))
        expected_challenge = _as_text(pending.get("code_challenge"))
        if not verifier or not expected_challenge:
            raise EtsyAuthError(
                ETSY_PKCE_MISMATCH,
                "the pending Etsy authorization has no PKCE verifier",
            )
        if not _VERIFIER_RE.match(verifier) or not _safe_compare(
                pkce_challenge(verifier), expected_challenge
        ):
            raise EtsyAuthError(
                ETSY_PKCE_MISMATCH,
                "the pending Etsy authorization failed its PKCE check",
            )

        response = self._request_tokens(
            {
                "grant_type": "authorization_code",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "redirect_uri": self.config.redirect_uri,
                "code": clean_code,
                "code_verifier": verifier,
            },
            failure_code=ETSY_EXCHANGE_FAILED,
        )
        record = _token_record(response, now=moment)
        if record is None:
            raise EtsyAuthError(
                ETSY_EXCHANGE_FAILED,
                "the Etsy token exchange returned no access token",
            )
        self._store.save(record)
        self._store.clear_pending()
        return self.status(now=moment)

    # -- refresh -------------------------------------------------------
    def refresh(self, *, now: float | None = None) -> dict[str, Any]:
        """Refresh an expiring token. Does not touch authorization state."""
        moment = self._clock() if now is None else float(now)
        if not self.config.configured:
            raise EtsyAuthError(
                ETSY_NOT_CONFIGURED,
                "Etsy OAuth is not configured; missing "
                + ", ".join(self.config.missing),
            )
        record = self._validated_record()
        refresh_value = _as_text(record.get("refresh_token"))
        if not refresh_value:
            raise EtsyAuthError(
                ETSY_AUTHORIZATION_REQUIRED,
                "the Etsy token has no refresh value; reconnect the account",
            )
        response = self._request_tokens(
            {
                "grant_type": "refresh_token",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "refresh_token": refresh_value,
            },
            failure_code=ETSY_REFRESH_FAILED,
        )
        next_record = _token_record(response, now=moment, previous=record)
        if next_record is None:
            raise EtsyAuthError(
                ETSY_REFRESH_FAILED,
                "the Etsy refresh returned no access token",
            )
        self._store.save(next_record)
        return self.status(now=moment)

    def disconnect(self) -> dict[str, Any]:
        """Remove the local token and any pending authorization."""
        self._store.clear()
        return self.status()

    # -- internals -----------------------------------------------------
    def _validated_record(self) -> dict[str, Any]:
        record = self._store.load()
        token = _valid_token(record)
        if token is None:
            raise EtsyAuthError(
                ETSY_AUTHORIZATION_REQUIRED,
                "no Etsy token is stored; authorize the account first",
            )
        return token

    def _request_tokens(
        self,
        form: Mapping[str, str],
        *,
        failure_code: str,
    ) -> Mapping[str, Any]:
        transport = self._transport
        if transport is None:
            raise EtsyAuthError(
                ETSY_TRANSPORT_ERROR,
                "no Etsy OAuth transport is configured; the connection cannot"
                " reach the provider",
            )
        try:
            response = transport.request(
                "POST",
                self.config.token_url,
                form=dict(form),
                headers={"Accept": "application/json"},
            )
        except EtsyAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - injected provider seam
            raise EtsyAuthError(
                failure_code,
                f"the Etsy token request failed ({type(exc).__name__})",
            ) from exc
        if not isinstance(response, Mapping):
            raise EtsyAuthError(
                failure_code,
                "the Etsy token endpoint returned a non-object response",
            )
        error = _as_text(response.get("error"))
        if error:
            raise EtsyAuthError(
                failure_code,
                f"the Etsy token endpoint refused the request: {error[:80]}",
            )
        return response


__all__ = [
    "DEFAULT_AUTHORIZE_URL",
    "DEFAULT_SCOPES",
    "DEFAULT_TOKEN_URL",
    "ETSY_AUTHORIZATION_REQUIRED",
    "ETSY_BAD_INPUT",
    "ETSY_CONNECTED",
    "ETSY_ERROR",
    "ETSY_EXCHANGE_FAILED",
    "ETSY_EXPIRED",
    "ETSY_NOT_CONFIGURED",
    "ETSY_PKCE_MISMATCH",
    "ETSY_REFRESH_FAILED",
    "ETSY_STATE_INVALID",
    "ETSY_STORE_ERROR",
    "ETSY_TRANSPORT_ERROR",
    "EtsyAuthError",
    "EtsyOAuth",
    "EtsyOAuthConfig",
    "EtsyTokenStore",
    "OAuthTransport",
    "PENDING_TTL_SECONDS",
    "REFRESH_SKEW_SECONDS",
    "SCHEMA",
    "STATUS_AUTHORIZATION_REQUIRED",
    "STATUS_CONNECTED",
    "STATUS_ERROR",
    "STATUS_EXPIRED",
    "STATUS_NOT_CONFIGURED",
    "default_token_path",
    "pkce_challenge",
]
