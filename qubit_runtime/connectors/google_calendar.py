"""Read-only Google Calendar connector.

Credential ownership stays where it already is: the bundled
``google-workspace`` skill writes ``$QUBIT_HOME/google_token.json``. This
connector reads that same file, reuses google-auth's authorized-user refresh
machinery when the token is expired, and writes the refreshed payload back to
the same file. It does not start an OAuth flow, store a second token, or accept
a token argument.

Reads only: the Calendar API v3 ``events.list`` GET endpoint (also used with
the ``q`` parameter for free-text search). There is no insert/patch/delete/
move/send path here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional
from urllib.parse import quote

from connectors._google_token_store import (
    persist_authorized_user_credentials,
    token_file_lock,
)

logger = logging.getLogger(__name__)

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
CALENDAR_FULL_SCOPE = "https://www.googleapis.com/auth/calendar"
CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
PRIMARY_CALENDAR_ID = "primary"
EVENTS_URL_TEMPLATE = f"{CALENDAR_API_BASE}/calendars/{{calendar_id}}/events"

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
LIST_PAGE_SIZE = 250  # Calendar API caps maxResults at 2500; stay conservative.
# Hard cap on paginated requests. A well-behaved server needs one page for the
# 100-result limit; this bounds an upstream that keeps returning fresh page
# tokens with no usable events (a request storm, not progress).
MAX_PAGES = 20

MAX_CALENDAR_ID_CHARS = 256
MAX_EVENT_ID_CHARS = 256
MAX_TITLE_CHARS = 200
MAX_TIME_CHARS = 64
MAX_QUERY_CHARS = 256
MAX_ATTENDEE_COUNT = 1000
MAX_STATUS_CHARS = 32
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


class GoogleCalendarError(Exception):
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
class Event:
    """One bounded calendar event; no raw API payload is retained."""

    event_id: str
    title: str = ""
    start: str = ""
    end: str = ""
    all_day: bool = False
    attendee_count: int = 0
    status: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "start": self.start,
            "end": self.end,
            "all_day": self.all_day,
            "attendee_count": self.attendee_count,
            "status": self.status,
        }


def _coerce_limit(value: object) -> int:
    try:
        limit = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    if limit <= 0:
        return 0
    return min(limit, MAX_LIMIT)


def _coerce_calendar_id(value: object) -> str:
    calendar_id = _bounded(value, MAX_CALENDAR_ID_CHARS)
    return calendar_id or PRIMARY_CALENDAR_ID


def _event_time(value: object) -> tuple[str, bool]:
    """Return a bounded ISO time and whether it is an all-day date."""
    if not isinstance(value, dict):
        return "", False
    date_time = _bounded(value.get("dateTime"), MAX_TIME_CHARS)
    if date_time:
        return date_time, False
    date = _bounded(value.get("date"), MAX_TIME_CHARS)
    if date:
        return date, True
    return "", False


def _attendee_count(value: object) -> int:
    if not isinstance(value, list):
        return 0
    return min(len(value), MAX_ATTENDEE_COUNT)


def _event_from_payload(payload: object) -> Optional[Event]:
    if not isinstance(payload, dict):
        return None
    event_id = _bounded(payload.get("id"), MAX_EVENT_ID_CHARS)
    if not event_id:
        return None
    start, all_day = _event_time(payload.get("start"))
    end, _ = _event_time(payload.get("end"))
    return Event(
        event_id=event_id,
        title=_bounded(payload.get("summary"), MAX_TITLE_CHARS),
        start=start,
        end=end,
        all_day=all_day,
        attendee_count=_attendee_count(payload.get("attendees")),
        status=_bounded(payload.get("status"), MAX_STATUS_CHARS),
    )


def _events_from_payload(data: dict[str, Any]) -> Iterator[Event]:
    items = data.get("items")
    if items is None:
        return
    if not isinstance(items, list):
        raise GoogleCalendarError(
            REASON_INVALID_RESPONSE,
            "the Google Calendar API returned an unexpected items payload",
        )
    for item in items:
        event = _event_from_payload(item)
        if event is not None:
            yield event


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
        raise TimeoutError("Google Calendar API request timed out") from exc
    except httpx.RequestError as exc:
        raise OSError("Google Calendar API request failed") from exc


class GoogleCalendarConnector:
    """Read-only Calendar API connector over the existing Google Workspace token."""

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
        except GoogleCalendarError:
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
            raise GoogleCalendarError(
                REASON_MISSING_CREDENTIAL,
                f"google_token.json not found at {path}; run the google-workspace setup",
            ) from None
        except (OSError, UnicodeError):
            raise GoogleCalendarError(
                REASON_MISSING_CREDENTIAL,
                "google_token.json could not be read; re-run the google-workspace setup",
            ) from None
        try:
            payload = json.loads(raw)
        except ValueError:
            raise GoogleCalendarError(
                REASON_MISSING_CREDENTIAL,
                "google_token.json is not valid JSON; re-run the google-workspace setup",
            ) from None
        if not isinstance(payload, dict):
            raise GoogleCalendarError(
                REASON_MISSING_CREDENTIAL,
                "google_token.json is not a token object; re-run the google-workspace setup",
            )
        if not (
            payload.get("token")
            or payload.get("access_token")
            or payload.get("refresh_token")
        ):
            raise GoogleCalendarError(
                REASON_MISSING_CREDENTIAL,
                "google_token.json has no access or refresh token; re-run the google-workspace setup",
            )
        return payload

    def _require_calendar_scope(self, payload: dict[str, Any]) -> None:
        granted = _granted_scopes(payload)
        if granted is None:
            # An older token may not record its scopes. Let Google answer; a
            # 403 is still typed rather than a silent empty list.
            return
        if CALENDAR_SCOPE in granted or CALENDAR_FULL_SCOPE in granted:
            return
        raise GoogleCalendarError(
            REASON_MISSING_SCOPE,
            "the stored Google token is missing the Google Calendar scope "
            f"{CALENDAR_SCOPE}; re-run the google-workspace setup to authorize "
            "Google Calendar",
            scope=CALENDAR_SCOPE,
        )

    def _access_token(self, payload: dict[str, Any]) -> str:
        try:
            from google.oauth2.credentials import Credentials
        except ImportError:
            token = payload.get("token") or payload.get("access_token")
            if token:
                return str(token)
            raise GoogleCalendarError(
                REASON_MISSING_CREDENTIAL,
                "google-auth is not installed and google_token.json has no access token; "
                "run the google-workspace setup",
            ) from None

        try:
            credentials = Credentials.from_authorized_user_info(payload)
        except Exception:
            raise GoogleCalendarError(
                REASON_MISSING_CREDENTIAL,
                "google_token.json could not be loaded; re-run the google-workspace setup",
            ) from None

        if credentials.valid and credentials.token:
            return str(credentials.token)

        if credentials.expired and credentials.refresh_token:
            with token_file_lock(self.token_path):
                # Another process may have refreshed while this one waited for
                # the advisory lock. Re-read so only one refresh reaches Google
                # and a stale token never overwrites the fresh one.
                try:
                    credentials = Credentials.from_authorized_user_info(
                        self._read_payload())
                except Exception:
                    pass

                if credentials.valid and credentials.token:
                    return str(credentials.token)

                if credentials.expired and credentials.refresh_token:
                    request = self._refresh_request or self._default_refresh_request()
                    try:
                        credentials.refresh(request)
                    except Exception as exc:
                        raise GoogleCalendarError(
                            REASON_REAUTHORIZE_REQUIRED,
                            "Google token refresh failed "
                            f"({type(exc).__name__}); re-run the google-workspace setup",
                        ) from None
                    self._persist_refreshed(credentials)
                    if credentials.token:
                        return str(credentials.token)

        raise GoogleCalendarError(
            REASON_MISSING_CREDENTIAL,
            "google_token.json has no usable access token; re-run the google-workspace setup",
        )

    @staticmethod
    def _default_refresh_request() -> Callable[..., Any]:
        from google.auth.transport.requests import Request

        return Request()

    def _persist_refreshed(self, credentials: Any) -> None:
        """Write the refreshed token back to the same file; never to a new store.

        The shared writer replaces the file atomically (0600 from creation,
        fsync, ``os.replace``) so a concurrent reader never sees a partial
        token. A failed write is non-fatal: this turn keeps the in-memory
        credentials and the next process simply refreshes again.
        """
        if not persist_authorized_user_credentials(self.token_path, credentials):
            logger.warning("google_calendar: refreshed token could not be persisted")

    # -- Calendar API ----------------------------------------------------

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
            raise GoogleCalendarError(
                REASON_TIMEOUT, "the Google Calendar API request timed out") from None
        except OSError:
            raise GoogleCalendarError(
                REASON_NETWORK_ERROR, "the Google Calendar API request failed") from None
        except Exception as exc:
            raise GoogleCalendarError(
                REASON_NETWORK_ERROR,
                f"the Google Calendar API request failed ({type(exc).__name__})",
            ) from None

        status = getattr(response, "status_code", None)
        if status == 200:
            try:
                data = response.json()
            except Exception:
                raise GoogleCalendarError(
                    REASON_INVALID_RESPONSE,
                    "the Google Calendar API returned unreadable JSON",
                ) from None
            if not isinstance(data, dict):
                raise GoogleCalendarError(
                    REASON_INVALID_RESPONSE,
                    "the Google Calendar API returned an unexpected payload",
                )
            error = data.get("error")
            if error:
                # A proxy/edge can wrap an error in a 200 envelope; never render
                # that as "no events" (the Contacts connector already refuses it).
                code = error.get("code") if isinstance(error, dict) else None
                raise GoogleCalendarError(
                    REASON_HTTP_ERROR,
                    "the Google Calendar API returned an error payload",
                    status=code if isinstance(code, int) else None,
                )
            return data
        if status == 401:
            raise GoogleCalendarError(
                REASON_UNAUTHORIZED,
                "the Google Calendar API rejected the access token; re-run the "
                "google-workspace setup",
                status=401,
            )
        if status == 403:
            raise GoogleCalendarError(
                REASON_FORBIDDEN,
                "the Google Calendar API refused access; the token may lack the "
                f"{CALENDAR_SCOPE} scope",
                status=403,
            )
        if status == 429:
            raise GoogleCalendarError(
                REASON_RATE_LIMITED,
                "the Google Calendar API rate limit was reached; retry later",
                status=429,
            )
        raise GoogleCalendarError(
            REASON_HTTP_ERROR,
            f"the Google Calendar API returned HTTP {status}",
            status=status if isinstance(status, int) else None,
        )

    def _collect(
        self,
        *,
        time_min: str,
        time_max: str,
        limit: int,
        query: str,
        calendar_id: str,
    ) -> list[Event]:
        payload = self._read_payload()
        self._require_calendar_scope(payload)
        token = self._access_token(payload)

        url = EVENTS_URL_TEMPLATE.format(calendar_id=quote(calendar_id, safe=""))
        base_params: dict[str, Any] = {
            "timeMin": time_min,
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        if time_max:
            base_params["timeMax"] = time_max
        if query:
            base_params["q"] = query

        events: list[Event] = []
        page_token: Optional[str] = None
        seen_tokens: set[str] = set()
        pages = 0
        while len(events) < limit:
            if pages >= MAX_PAGES:
                # Endless fresh page tokens with no usable rows must not loop.
                logger.warning(
                    "google_calendar: pagination stopped after %d pages", pages)
                break
            pages += 1
            page_params = dict(base_params)
            page_params["maxResults"] = max(
                1, min(limit - len(events), LIST_PAGE_SIZE))
            if page_token:
                page_params["pageToken"] = page_token
            data = self._get_json(url, page_params, token)
            for event in _events_from_payload(data):
                if len(events) >= limit:
                    break
                events.append(event)
            next_token = data.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token:
                break
            if next_token in seen_tokens:
                break
            seen_tokens.add(next_token)
            page_token = next_token
        return events

    def list_events(
        self,
        time_min: object,
        time_max: object = "",
        limit: object = DEFAULT_LIMIT,
        *,
        calendar_id: object = PRIMARY_CALENDAR_ID,
    ) -> list[Event]:
        """List events in ``[time_min, time_max)``, bounded by ``limit`` (max 100)."""
        start = _bounded(time_min, MAX_TIME_CHARS)
        if not start:
            raise GoogleCalendarError(
                REASON_INVALID_REQUEST, "time_min is required (RFC3339)")
        end = _bounded(time_max, MAX_TIME_CHARS)
        bounded = _coerce_limit(limit)
        if bounded == 0:
            return []
        return self._collect(
            time_min=start,
            time_max=end,
            limit=bounded,
            query="",
            calendar_id=_coerce_calendar_id(calendar_id),
        )

    def search(
        self,
        query: object,
        time_min: object,
        time_max: object = "",
        limit: object = DEFAULT_LIMIT,
        *,
        calendar_id: object = PRIMARY_CALENDAR_ID,
    ) -> list[Event]:
        """Free-text search via the Calendar API ``q`` parameter (read-only)."""
        text = _bounded(query, MAX_QUERY_CHARS)
        if not text:
            raise GoogleCalendarError(
                REASON_INVALID_REQUEST, "a non-empty search query is required")
        start = _bounded(time_min, MAX_TIME_CHARS)
        if not start:
            raise GoogleCalendarError(
                REASON_INVALID_REQUEST, "time_min is required (RFC3339)")
        end = _bounded(time_max, MAX_TIME_CHARS)
        bounded = _coerce_limit(limit)
        if bounded == 0:
            return []
        return self._collect(
            time_min=start,
            time_max=end,
            limit=bounded,
            query=text,
            calendar_id=_coerce_calendar_id(calendar_id),
        )


__all__ = [
    "CALENDAR_FULL_SCOPE",
    "CALENDAR_SCOPE",
    "DEFAULT_LIMIT",
    "Event",
    "GoogleCalendarConnector",
    "GoogleCalendarError",
    "MAX_LIMIT",
    "MAX_PAGES",
    "PRIMARY_CALENDAR_ID",
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
