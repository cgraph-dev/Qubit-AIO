"""Read-only Google Contacts connector.

Credential ownership stays where it already is: the bundled
``google-workspace`` skill writes ``$QUBIT_HOME/google_token.json`` and requests
``https://www.googleapis.com/auth/contacts.readonly``. This connector reads that
same file, reuses google-auth's authorized-user refresh machinery when the
token is expired, and writes the refreshed payload back to the same file. It
does not start an OAuth flow, store a second token, or accept a token argument.

Reads only: the People API ``connections.list`` and ``searchContacts`` GET
endpoints. There is no create/update/delete/send path here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Optional

logger = logging.getLogger(__name__)

CONTACTS_SCOPE = "https://www.googleapis.com/auth/contacts.readonly"
CONTACTS_FULL_SCOPE = "https://www.googleapis.com/auth/contacts"
PEOPLE_API_BASE = "https://people.googleapis.com/v1"
CONNECTIONS_URL = f"{PEOPLE_API_BASE}/people/me/connections"
SEARCH_URL = f"{PEOPLE_API_BASE}/people:searchContacts"

PERSON_FIELDS = "names,emailAddresses,phoneNumbers,organizations"
SEARCH_READ_MASK = PERSON_FIELDS

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
SEARCH_MAX_RESULTS = 30  # People API caps people:searchContacts at 30 results.
LIST_PAGE_SIZE = 100
MAX_QUERY_CHARS = 256

MAX_RESOURCE_CHARS = 128
MAX_NAME_CHARS = 200
MAX_EMAIL_CHARS = 320
MAX_PHONE_CHARS = 64
MAX_ORGANIZATION_CHARS = 200
MAX_TITLE_CHARS = 200
MAX_EMAILS = 5
MAX_PHONES = 5
MAX_DETAIL_CHARS = 300
REQUEST_TIMEOUT_S = 20.0

# Stable machine-readable refusal reasons. A caller may show these verbatim.
REASON_MISSING_CREDENTIAL = "missing_credential"
REASON_MISSING_SCOPE = "missing_scope"
REASON_REAUTHORIZE_REQUIRED = "reauthorize_required"
REASON_UNAUTHORIZED = "unauthorized"
REASON_FORBIDDEN = "forbidden"
REASON_RATE_LIMITED = "rate_limited"
REASON_TIMEOUT = "timeout"
REASON_NETWORK_ERROR = "network_error"
REASON_INVALID_REQUEST = "invalid_request"
REASON_INVALID_RESPONSE = "invalid_response"
REASON_HTTP_ERROR = "http_error"


def _bounded_redacted_detail(text: object, limit: int = MAX_DETAIL_CHARS) -> str:
    """One-line, bounded, redacted error detail; fails closed if redaction breaks."""
    collapsed = " ".join(str(text or "").split())
    try:
        from agent.redact import redact_for_egress

        collapsed = redact_for_egress(collapsed)
    except Exception:
        return "[redacted]"
    return collapsed[:limit]


def _bounded(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


class GoogleContactsError(Exception):
    """A typed connector refusal: no network state, token, or raw PII in the message."""

    def __init__(
        self,
        reason: str,
        detail: str = "",
        *,
        scope: str = "",
        status: Optional[int] = None,
    ) -> None:
        self.reason = reason
        self.detail = _bounded_redacted_detail(detail)
        self.scope = scope
        self.status = status
        super().__init__(self.detail or self.reason)


@dataclass(frozen=True)
class Contact:
    """One bounded contact row; no raw API payload is retained."""

    resource_name: str
    name: str = ""
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()
    organization: str = ""
    title: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "resource_name": self.resource_name,
            "name": self.name,
            "emails": list(self.emails),
            "phones": list(self.phones),
            "organization": self.organization,
            "title": self.title,
        }


def _coerce_limit(value: object) -> int:
    try:
        limit = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    if limit <= 0:
        return 0
    return min(limit, MAX_LIMIT)


def _unique_bounded(values: Iterable[object], *, limit: int, chars: int) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = _bounded(value, chars)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return tuple(out)


def _contact_from_person(person: object) -> Optional[Contact]:
    if not isinstance(person, dict):
        return None
    resource_name = _bounded(person.get("resourceName"), MAX_RESOURCE_CHARS)
    if not resource_name:
        return None

    names = person.get("names")
    name = ""
    if isinstance(names, list):
        for entry in names:
            if isinstance(entry, dict):
                name = _bounded(entry.get("displayName"), MAX_NAME_CHARS)
                if name:
                    break

    emails_raw = person.get("emailAddresses")
    emails = _unique_bounded(
        (entry.get("value") for entry in emails_raw if isinstance(entry, dict))
        if isinstance(emails_raw, list) else (),
        limit=MAX_EMAILS, chars=MAX_EMAIL_CHARS)

    phones_raw = person.get("phoneNumbers")
    phones = _unique_bounded(
        (entry.get("value") for entry in phones_raw if isinstance(entry, dict))
        if isinstance(phones_raw, list) else (),
        limit=MAX_PHONES, chars=MAX_PHONE_CHARS)

    organization = ""
    title = ""
    organizations = person.get("organizations")
    if isinstance(organizations, list):
        for entry in organizations:
            if not isinstance(entry, dict):
                continue
            if not organization:
                organization = _bounded(entry.get("name"), MAX_ORGANIZATION_CHARS)
            if not title:
                title = _bounded(entry.get("title"), MAX_TITLE_CHARS)
            if organization and title:
                break

    return Contact(
        resource_name=resource_name,
        name=name,
        emails=emails,
        phones=phones,
        organization=organization,
        title=title,
    )


def _connections_people(data: dict[str, Any]) -> Iterator[Contact]:
    connections = data.get("connections")
    if not isinstance(connections, list):
        return
    for person in connections:
        contact = _contact_from_person(person)
        if contact is not None:
            yield contact


def _search_people(data: dict[str, Any]) -> Iterator[Contact]:
    results = data.get("results")
    if not isinstance(results, list):
        return
    for result in results:
        if not isinstance(result, dict):
            continue
        contact = _contact_from_person(result.get("person"))
        if contact is not None:
            yield contact


def _granted_scopes(payload: dict[str, Any]) -> Optional[set[str]]:
    raw = payload.get("scopes")
    if raw is None:
        raw = payload.get("scope")
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.split()
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return None
    return {str(scope).strip() for scope in raw if str(scope).strip()}


def _default_http_get(url: str, *, params: dict[str, Any], headers: dict[str, str],
                      timeout: float):
    """GET through httpx, translating transport failures into stdlib-shaped errors."""
    import httpx

    try:
        return httpx.get(url, params=params, headers=headers, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise TimeoutError("Google People API request timed out") from exc
    except httpx.RequestError as exc:
        raise OSError("Google People API request failed") from exc


class GoogleContactsConnector:
    """Read-only People API connector over the existing Google Workspace token."""

    def __init__(
        self,
        *,
        token_path: Optional[Path] = None,
        http_get: Optional[Callable[..., Any]] = None,
        refresh_request: Optional[Callable[..., Any]] = None,
        timeout: float = REQUEST_TIMEOUT_S,
    ) -> None:
        self._token_path = Path(token_path) if token_path is not None else None
        self._http_get = http_get
        self._refresh_request = refresh_request
        self._timeout = timeout
        self._search_warmed = False

    # -- credential path -------------------------------------------------

    @property
    def token_path(self) -> Path:
        """Resolve at call time so a served profile's own home is used."""
        if self._token_path is not None:
            return self._token_path
        from qubit_constants import get_qubit_home

        return Path(get_qubit_home()) / "google_token.json"

    def configured(self) -> bool:
        """True when the existing token file carries an access or refresh token."""
        try:
            payload = self._read_payload()
        except GoogleContactsError:
            return False
        return bool(
            payload.get("token")
            or payload.get("access_token")
            or payload.get("refresh_token")
        )

    def _read_payload(self) -> dict[str, Any]:
        path = self.token_path
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise GoogleContactsError(
                REASON_MISSING_CREDENTIAL,
                f"google_token.json not found at {path}; run the google-workspace setup",
            ) from None
        except (OSError, UnicodeError):
            raise GoogleContactsError(
                REASON_MISSING_CREDENTIAL,
                "google_token.json could not be read; re-run the google-workspace setup",
            ) from None
        try:
            payload = json.loads(raw)
        except ValueError:
            raise GoogleContactsError(
                REASON_MISSING_CREDENTIAL,
                "google_token.json is not valid JSON; re-run the google-workspace setup",
            ) from None
        if not isinstance(payload, dict):
            raise GoogleContactsError(
                REASON_MISSING_CREDENTIAL,
                "google_token.json is not a token object; re-run the google-workspace setup",
            )
        if not (
            payload.get("token")
            or payload.get("access_token")
            or payload.get("refresh_token")
        ):
            raise GoogleContactsError(
                REASON_MISSING_CREDENTIAL,
                "google_token.json has no access or refresh token; re-run the google-workspace setup",
            )
        return payload

    def _require_contacts_scope(self, payload: dict[str, Any]) -> None:
        granted = _granted_scopes(payload)
        if granted is None:
            # An older token may not record its scopes. Let Google answer; a
            # 403 is still typed rather than a silent empty list.
            return
        if CONTACTS_SCOPE in granted or CONTACTS_FULL_SCOPE in granted:
            return
        raise GoogleContactsError(
            REASON_MISSING_SCOPE,
            "the stored Google token is missing the Google Contacts scope "
            f"{CONTACTS_SCOPE}; re-run the google-workspace setup to authorize "
            "Google Contacts",
            scope=CONTACTS_SCOPE,
        )

    def _access_token(self, payload: dict[str, Any]) -> str:
        try:
            from google.oauth2.credentials import Credentials
        except ImportError:
            token = payload.get("token") or payload.get("access_token")
            if token:
                return str(token)
            raise GoogleContactsError(
                REASON_MISSING_CREDENTIAL,
                "google-auth is not installed and google_token.json has no access token; "
                "run the google-workspace setup",
            ) from None

        try:
            credentials = Credentials.from_authorized_user_info(payload)
        except Exception:
            raise GoogleContactsError(
                REASON_MISSING_CREDENTIAL,
                "google_token.json could not be loaded; re-run the google-workspace setup",
            ) from None

        if credentials.valid and credentials.token:
            return str(credentials.token)

        if credentials.expired and credentials.refresh_token:
            request = self._refresh_request or self._default_refresh_request()
            try:
                credentials.refresh(request)
            except Exception as exc:
                raise GoogleContactsError(
                    REASON_REAUTHORIZE_REQUIRED,
                    "Google token refresh failed "
                    f"({type(exc).__name__}); re-run the google-workspace setup",
                ) from None
            self._persist_refreshed(credentials)
            if credentials.token:
                return str(credentials.token)

        raise GoogleContactsError(
            REASON_MISSING_CREDENTIAL,
            "google_token.json has no usable access token; re-run the google-workspace setup",
        )

    @staticmethod
    def _default_refresh_request() -> Callable[..., Any]:
        from google.auth.transport.requests import Request

        return Request()

    def _persist_refreshed(self, credentials: Any) -> None:
        """Write the refreshed token back to the same file; never to a new store."""
        try:
            payload = json.loads(credentials.to_json())
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        if not payload.get("type"):
            payload["type"] = "authorized_user"
        try:
            self.token_path.write_text(
                json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            logger.warning("google_contacts: refreshed token could not be persisted")
            return
        try:
            self.token_path.chmod(0o600)
        except OSError:
            pass

    # -- People API ------------------------------------------------------

    def _get_json(self, url: str, params: dict[str, Any], token: str) -> dict[str, Any]:
        http_get = self._http_get or _default_http_get
        try:
            response = http_get(
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
                timeout=self._timeout,
            )
        except TimeoutError:
            raise GoogleContactsError(
                REASON_TIMEOUT, "the Google People API request timed out") from None
        except OSError:
            raise GoogleContactsError(
                REASON_NETWORK_ERROR, "the Google People API request failed") from None
        except Exception as exc:
            raise GoogleContactsError(
                REASON_NETWORK_ERROR,
                f"the Google People API request failed ({type(exc).__name__})",
            ) from None

        status = getattr(response, "status_code", None)
        if status == 200:
            try:
                data = response.json()
            except Exception:
                raise GoogleContactsError(
                    REASON_INVALID_RESPONSE,
                    "the Google People API returned unreadable JSON",
                ) from None
            if not isinstance(data, dict):
                raise GoogleContactsError(
                    REASON_INVALID_RESPONSE,
                    "the Google People API returned an unexpected payload",
                )
            error = data.get("error")
            if error:
                code = error.get("code") if isinstance(error, dict) else None
                raise GoogleContactsError(
                    REASON_HTTP_ERROR,
                    "the Google People API returned an error payload",
                    status=code if isinstance(code, int) else None,
                )
            return data
        if status == 401:
            raise GoogleContactsError(
                REASON_UNAUTHORIZED,
                "the Google People API rejected the access token; re-run the "
                "google-workspace setup",
                status=401,
            )
        if status == 403:
            raise GoogleContactsError(
                REASON_FORBIDDEN,
                "the Google People API refused access; the token may lack the "
                f"{CONTACTS_SCOPE} scope",
                status=403,
            )
        if status == 429:
            raise GoogleContactsError(
                REASON_RATE_LIMITED,
                "the Google People API rate limit was reached; retry later",
                status=429,
            )
        raise GoogleContactsError(
            REASON_HTTP_ERROR,
            f"the Google People API returned HTTP {status}",
            status=status if isinstance(status, int) else None,
        )

    def _collect(
        self,
        *,
        url: str,
        token: str,
        params: dict[str, Any],
        limit: int,
        extract: Callable[[dict[str, Any]], Iterable[Contact]],
        page_size_cap: int,
        payload_key: str,
    ) -> list[Contact]:
        contacts: list[Contact] = []
        page_token: Optional[str] = None
        seen_tokens: set[str] = set()
        while len(contacts) < limit:
            page_params = dict(params)
            page_params["pageSize"] = max(1, min(limit - len(contacts), page_size_cap))
            if page_token:
                page_params["pageToken"] = page_token
            data = self._get_json(url, page_params, token)
            if payload_key in data and not isinstance(data.get(payload_key), list):
                raise GoogleContactsError(
                    REASON_INVALID_RESPONSE,
                    f"the Google People API returned a non-list {payload_key!r}",
                )
            for contact in extract(data):
                if len(contacts) >= limit:
                    break
                contacts.append(contact)
            next_token = data.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token:
                break
            if next_token in seen_tokens:
                break
            seen_tokens.add(next_token)
            page_token = next_token
        return contacts

    def _warm_search_cache(self, token: str) -> None:
        """People search uses a lazy cache; an empty-query GET warms it first.

        Google's sample waits a few seconds after this request. A voice/chat
        turn cannot block on that, so the warmup request is issued and the
        query follows immediately; callers needing maximum freshness can issue
        their own read after a pause.
        """
        self._get_json(
            SEARCH_URL,
            {"query": "", "readMask": SEARCH_READ_MASK, "pageSize": 1},
            token,
        )

    def list(self, limit: object = DEFAULT_LIMIT) -> list[Contact]:
        """List connections, bounded by ``limit`` (max 100) and field lengths."""
        bounded = _coerce_limit(limit)
        if bounded == 0:
            return []
        payload = self._read_payload()
        self._require_contacts_scope(payload)
        token = self._access_token(payload)
        return self._collect(
            url=CONNECTIONS_URL,
            token=token,
            params={"personFields": PERSON_FIELDS},
            limit=bounded,
            extract=_connections_people,
            page_size_cap=LIST_PAGE_SIZE,
            payload_key="connections",
        )

    def search(self, query: object, limit: object = DEFAULT_LIMIT) -> list[Contact]:
        """Search contacts via ``people:searchContacts`` (read-only, max 30 results).

        The People API has no page token for this method; ``limit`` is clamped
        to its 30-result cap.
        """
        text = _bounded(query, MAX_QUERY_CHARS)
        if not text:
            raise GoogleContactsError(
                REASON_INVALID_REQUEST, "a non-empty search query is required")
        bounded = min(_coerce_limit(limit), SEARCH_MAX_RESULTS)
        if bounded == 0:
            return []
        payload = self._read_payload()
        self._require_contacts_scope(payload)
        token = self._access_token(payload)
        if not self._search_warmed:
            self._warm_search_cache(token)
            self._search_warmed = True
        data = self._get_json(
            SEARCH_URL,
            {"query": text, "readMask": SEARCH_READ_MASK, "pageSize": bounded},
            token,
        )
        if "results" in data and not isinstance(data.get("results"), list):
            raise GoogleContactsError(
                REASON_INVALID_RESPONSE,
                "the Google People API returned a non-list 'results'",
            )
        return list(_search_people(data))[:bounded]


__all__ = [
    "Contact",
    "CONTACTS_SCOPE",
    "DEFAULT_LIMIT",
    "GoogleContactsConnector",
    "GoogleContactsError",
    "MAX_LIMIT",
    "REASON_FORBIDDEN",
    "REASON_HTTP_ERROR",
    "REASON_INVALID_REQUEST",
    "REASON_INVALID_RESPONSE",
    "REASON_MISSING_CREDENTIAL",
    "REASON_MISSING_SCOPE",
    "REASON_NETWORK_ERROR",
    "REASON_RATE_LIMITED",
    "REASON_REAUTHORIZE_REQUIRED",
    "REASON_TIMEOUT",
    "REASON_UNAUTHORIZED",
]
