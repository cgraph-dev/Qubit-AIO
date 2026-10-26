"""Read-only Dropbox connector.

Credential ownership: no Dropbox credential path existed in this tree, so this
connector defines exactly one: ``$QUBIT_HOME/dropbox_token.json`` (resolved at
call time through :func:`qubit_constants.get_qubit_home`). The file is a JSON
object carrying a Dropbox access token under ``access_token`` (or ``token``).
This module never starts an OAuth flow, never refreshes a token, and never
accepts a caller-supplied token. A file that carries only a refresh token is a
typed ``missing_credential`` because the exchange belongs to an OAuth client we
deliberately do not implement here.

Reads only: ``files/list_folder`` (+ ``continue``), ``files/get_metadata`` and
``files/search_v2``. There is no upload, delete, create, move, copy or share
path in this module.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

logger = logging.getLogger(__name__)

DROPBOX_API_BASE = "https://api.dropboxapi.com/2"
LIST_FOLDER_URL = f"{DROPBOX_API_BASE}/files/list_folder"
LIST_FOLDER_CONTINUE_URL = f"{DROPBOX_API_BASE}/files/list_folder/continue"
GET_METADATA_URL = f"{DROPBOX_API_BASE}/files/get_metadata"
SEARCH_V2_URL = f"{DROPBOX_API_BASE}/files/search_v2"

TOKEN_FILENAME = "dropbox_token.json"

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
PAGE_LIMIT = 100
MAX_QUERY_CHARS = 256
MAX_PATH_CHARS = 1024
MAX_NAME_CHARS = 255
MAX_ID_CHARS = 128
MAX_REV_CHARS = 64
MAX_HASH_CHARS = 64
MAX_TIME_CHARS = 64
MAX_RESPONSE_ENTRIES = 2000
MAX_DETAIL_CHARS = 300
REQUEST_TIMEOUT_S = 20.0

# Stable machine-readable refusal reasons. A caller may show these verbatim.
REASON_MISSING_CREDENTIAL = "missing_credential"
REASON_UNAUTHORIZED = "unauthorized"
REASON_FORBIDDEN = "forbidden"
REASON_RATE_LIMITED = "rate_limited"
REASON_TIMEOUT = "timeout"
REASON_NETWORK_ERROR = "network_error"
REASON_INVALID_REQUEST = "invalid_request"
REASON_INVALID_RESPONSE = "invalid_response"
REASON_NOT_FOUND = "not_found"
REASON_CONFLICT = "conflict"
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


def _coerce_limit(value: object) -> int:
    try:
        limit = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    if limit <= 0:
        return 0
    return min(limit, MAX_LIMIT)


def _optional_int(value: object) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


class DropboxError(Exception):
    """A typed connector refusal: no network state, token, or raw body in the message."""

    def __init__(
        self,
        reason: str,
        detail: str = "",
        *,
        status: Optional[int] = None,
    ) -> None:
        self.reason = reason
        self.detail = _bounded_redacted_detail(detail)
        self.status = status
        super().__init__(self.detail or self.reason)


@dataclass(frozen=True)
class DropboxEntry:
    """One bounded Dropbox metadata row; no raw API payload is retained."""

    entry_type: str
    name: str = ""
    path: str = ""
    entry_id: str = ""
    size: Optional[int] = None
    client_modified: str = ""
    server_modified: str = ""
    rev: str = ""
    content_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.entry_type,
            "name": self.name,
            "path": self.path,
            "id": self.entry_id,
        }
        if self.size is not None:
            payload["size"] = self.size
        if self.client_modified:
            payload["client_modified"] = self.client_modified
        if self.server_modified:
            payload["server_modified"] = self.server_modified
        if self.rev:
            payload["rev"] = self.rev
        if self.content_hash:
            payload["content_hash"] = self.content_hash
        return payload


def _entry_from_metadata(metadata: object) -> Optional[DropboxEntry]:
    """Map one Dropbox metadata object; deleted/unknown tags are not entries."""
    if not isinstance(metadata, dict):
        return None
    tag = str(metadata.get(".tag") or "").strip().lower()
    if tag not in {"file", "folder"}:
        return None
    name = _bounded(metadata.get("name"), MAX_NAME_CHARS)
    path = _bounded(metadata.get("path_display"), MAX_PATH_CHARS)
    if not name and not path:
        return None
    entry = DropboxEntry(
        entry_type=tag,
        name=name,
        path=path,
        entry_id=_bounded(metadata.get("id"), MAX_ID_CHARS),
        size=_optional_int(metadata.get("size")) if tag == "file" else None,
        client_modified=_bounded(metadata.get("client_modified"), MAX_TIME_CHARS),
        server_modified=_bounded(metadata.get("server_modified"), MAX_TIME_CHARS),
        rev=_bounded(metadata.get("rev"), MAX_REV_CHARS) if tag == "file" else "",
        content_hash=_bounded(metadata.get("content_hash"), MAX_HASH_CHARS)
        if tag == "file" else "",
    )
    return entry


def _search_metadata(match: object) -> Optional[DropboxEntry]:
    """Unwrap ``search_v2``'s match union, then map the metadata row."""
    if not isinstance(match, dict):
        return None
    metadata = match.get("metadata")
    if not isinstance(metadata, dict):
        return None
    tag = str(metadata.get(".tag") or "").strip().lower()
    if tag == "deleted":
        return None
    if tag == "metadata":
        nested = metadata.get("metadata")
        if not isinstance(nested, dict):
            return None
        return _entry_from_metadata(nested)
    # Some servers/tests return the metadata row directly.
    return _entry_from_metadata(metadata)


def _entries_from_listing(data: object, *, limit: int) -> Iterator[DropboxEntry]:
    if not isinstance(data, dict):
        return
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        return
    emitted = 0
    for raw in raw_entries[:MAX_RESPONSE_ENTRIES]:
        entry = _entry_from_metadata(raw)
        if entry is None:
            continue
        yield entry
        emitted += 1
        if limit and emitted >= limit:
            return


def _entries_from_search(data: object, *, limit: int) -> Iterator[DropboxEntry]:
    if not isinstance(data, dict):
        return
    matches = data.get("matches")
    if not isinstance(matches, list):
        return
    emitted = 0
    for match in matches[:MAX_RESPONSE_ENTRIES]:
        entry = _search_metadata(match)
        if entry is None:
            continue
        yield entry
        emitted += 1
        if limit and emitted >= limit:
            return


def _default_http_post(url: str, *, body: dict[str, Any], headers: dict[str, str],
                       timeout: float):
    """POST through httpx, translating transport failures into stdlib-shaped errors."""
    import httpx

    try:
        return httpx.post(url, json=body, headers=headers, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise TimeoutError("Dropbox API request timed out") from exc
    except httpx.RequestError as exc:
        raise OSError("Dropbox API request failed") from exc


class DropboxConnector:
    """Read-only Dropbox connector over ``$QUBIT_HOME/dropbox_token.json``."""

    def __init__(
        self,
        *,
        token_path: Optional[Path] = None,
        http_post: Optional[Callable[..., Any]] = None,
        timeout: float = REQUEST_TIMEOUT_S,
    ) -> None:
        self._token_path = Path(token_path) if token_path is not None else None
        self._http_post = http_post
        self._timeout = timeout

    # -- credential path -------------------------------------------------

    @property
    def token_path(self) -> Path:
        """Resolve at call time so a served profile's own home is used."""
        if self._token_path is not None:
            return self._token_path
        from qubit_constants import get_qubit_home

        return Path(get_qubit_home()) / TOKEN_FILENAME

    def configured(self) -> bool:
        """True when the documented token file carries a Dropbox access token."""
        try:
            payload = self._read_payload()
        except DropboxError:
            return False
        return bool(payload.get("access_token") or payload.get("token"))

    def _read_payload(self) -> dict[str, Any]:
        path = self.token_path
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise DropboxError(
                REASON_MISSING_CREDENTIAL,
                f"{TOKEN_FILENAME} not found at {path}; provide a Dropbox access "
                "token there",
            ) from None
        except (OSError, UnicodeError):
            raise DropboxError(
                REASON_MISSING_CREDENTIAL,
                f"{TOKEN_FILENAME} could not be read",
            ) from None
        try:
            payload = json.loads(raw)
        except ValueError:
            raise DropboxError(
                REASON_MISSING_CREDENTIAL,
                f"{TOKEN_FILENAME} is not valid JSON",
            ) from None
        if not isinstance(payload, dict):
            raise DropboxError(
                REASON_MISSING_CREDENTIAL,
                f"{TOKEN_FILENAME} is not a token object",
            ) from None
        if not (payload.get("access_token") or payload.get("token")):
            raise DropboxError(
                REASON_MISSING_CREDENTIAL,
                f"{TOKEN_FILENAME} has no access token; this connector does not "
                "perform OAuth token exchange or refresh",
            )
        return payload

    def _access_token(self) -> str:
        payload = self._read_payload()
        return str(payload.get("access_token") or payload.get("token"))

    # -- HTTP ------------------------------------------------------------

    @staticmethod
    def _conflict_reason(response: Any) -> str:
        """Classify a 409 as not_found when Dropbox says so, without keeping the body."""
        try:
            data = response.json()
        except Exception:
            return REASON_CONFLICT
        if not isinstance(data, dict):
            return REASON_CONFLICT
        summary = str(data.get("error_summary") or "").strip().lower()
        if summary.startswith("path/not_found") or summary.startswith("not_found"):
            return REASON_NOT_FOUND
        return REASON_CONFLICT

    def _post_json(self, url: str, body: dict[str, Any], token: str) -> dict[str, Any]:
        http_post = self._http_post or _default_http_post
        try:
            response = http_post(
                url,
                body=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        except TimeoutError:
            raise DropboxError(
                REASON_TIMEOUT, "the Dropbox API request timed out") from None
        except OSError:
            raise DropboxError(
                REASON_NETWORK_ERROR, "the Dropbox API request failed") from None
        except Exception as exc:
            raise DropboxError(
                REASON_NETWORK_ERROR,
                f"the Dropbox API request failed ({type(exc).__name__})",
            ) from None

        status = getattr(response, "status_code", None)
        if status == 200:
            try:
                data = response.json()
            except Exception:
                raise DropboxError(
                    REASON_INVALID_RESPONSE,
                    "the Dropbox API returned unreadable JSON",
                ) from None
            if not isinstance(data, dict):
                raise DropboxError(
                    REASON_INVALID_RESPONSE,
                    "the Dropbox API returned an unexpected payload",
                )
            return data
        if status == 401:
            raise DropboxError(
                REASON_UNAUTHORIZED,
                "the Dropbox API rejected the access token; replace dropbox_token.json",
                status=401,
            )
        if status == 403:
            raise DropboxError(
                REASON_FORBIDDEN,
                "the Dropbox API refused access; the token may lack the required scope",
                status=403,
            )
        if status == 429:
            raise DropboxError(
                REASON_RATE_LIMITED,
                "the Dropbox API rate limit was reached; retry later",
                status=429,
            )
        if status == 409:
            reason = self._conflict_reason(response)
            detail = (
                "Dropbox could not find that path"
                if reason == REASON_NOT_FOUND
                else "Dropbox rejected the request (invalid path or conflict)"
            )
            raise DropboxError(reason, detail, status=409)
        if status == 400:
            raise DropboxError(
                REASON_INVALID_REQUEST,
                "the Dropbox API rejected the request parameters",
                status=400,
            )
        raise DropboxError(
            REASON_HTTP_ERROR,
            f"the Dropbox API returned HTTP {status}",
            status=status if isinstance(status, int) else None,
        )

    # -- reads -----------------------------------------------------------

    def list_folder(
        self,
        path: object = "",
        *,
        limit: object = DEFAULT_LIMIT,
        recursive: bool = False,
    ) -> list[DropboxEntry]:
        """List one folder, bounded by ``limit`` (max 100) and field lengths."""
        bounded = _coerce_limit(limit)
        if bounded == 0:
            return []
        token = self._access_token()
        target = _bounded(path, MAX_PATH_CHARS)
        body: dict[str, Any] = {
            "path": target,
            "recursive": bool(recursive),
            "include_deleted": False,
            "limit": min(bounded, PAGE_LIMIT),
        }
        data = self._post_json(LIST_FOLDER_URL, body, token)

        entries: list[DropboxEntry] = []
        seen_cursors: set[str] = set()
        while True:
            remaining = bounded - len(entries)
            if remaining <= 0:
                break
            entries.extend(_entries_from_listing(data, limit=remaining))
            if len(entries) > bounded:
                del entries[bounded:]
            if len(entries) >= bounded:
                break
            if not data.get("has_more"):
                break
            cursor = data.get("cursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
            data = self._post_json(
                LIST_FOLDER_CONTINUE_URL, {"cursor": cursor}, token)
        return entries[:bounded]

    def get_metadata(self, path: object) -> DropboxEntry:
        """Read one file/folder metadata row by path (read-only)."""
        target = _bounded(path, MAX_PATH_CHARS)
        if not target:
            raise DropboxError(
                REASON_INVALID_REQUEST, "a non-empty path is required for metadata")
        token = self._access_token()
        data = self._post_json(
            GET_METADATA_URL,
            {"path": target, "include_deleted": False},
            token,
        )
        entry = _entry_from_metadata(data)
        if entry is None:
            raise DropboxError(
                REASON_INVALID_RESPONSE,
                "Dropbox returned metadata that is not a file or folder",
            )
        return entry

    def search(
        self,
        query: object,
        *,
        path: object = "",
        limit: object = DEFAULT_LIMIT,
    ) -> list[DropboxEntry]:
        """Search names/content via ``files/search_v2`` (read-only, bounded)."""
        text = _bounded(query, MAX_QUERY_CHARS)
        if not text:
            raise DropboxError(
                REASON_INVALID_REQUEST, "a non-empty search query is required")
        bounded = _coerce_limit(limit)
        if bounded == 0:
            return []
        options: dict[str, Any] = {
            "max_results": bounded,
            "file_status": "active",
        }
        target = _bounded(path, MAX_PATH_CHARS)
        if target:
            options["path"] = target
        token = self._access_token()
        data = self._post_json(
            SEARCH_V2_URL, {"query": text, "options": options}, token)
        return list(_entries_from_search(data, limit=bounded))[:bounded]


__all__ = [
    "DEFAULT_LIMIT",
    "DropboxConnector",
    "DropboxEntry",
    "DropboxError",
    "MAX_LIMIT",
    "REASON_CONFLICT",
    "REASON_FORBIDDEN",
    "REASON_HTTP_ERROR",
    "REASON_INVALID_REQUEST",
    "REASON_INVALID_RESPONSE",
    "REASON_MISSING_CREDENTIAL",
    "REASON_NETWORK_ERROR",
    "REASON_NOT_FOUND",
    "REASON_RATE_LIMITED",
    "REASON_TIMEOUT",
    "REASON_UNAUTHORIZED",
    "TOKEN_FILENAME",
]
