"""Read-only Apple Calendar connector.

On macOS a caller injects an EventKit-shaped adapter exposing
``list_events(days_ahead=, days_behind=, limit=)`` and returning a sequence of
mappings. The connector maps those rows to bounded :class:`CalendarEvent`
records. It has no create/update/delete path and never writes to Calendar.

On any other platform ``list_events`` raises ``not_supported`` with the host
platform and the required framework before the adapter is touched.

Field shape was informed by OpenJarvis ``0583901``
(``src/openjarvis/connectors/apple_calendar.py``, Apache-2.0); no upstream code
is copied. See ``connectors/APPLE_PROVENANCE.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from connectors.apple_common import (
    MAX_ID_CHARS,
    MAX_LONG_TEXT_CHARS,
    MAX_TEXT_CHARS,
    AppleConnectorError,
    backend_rows,
    bounded_text,
    coerce_bool,
    coerce_days,
    coerce_limit,
    current_platform,
    ensure_supported,
    map_backend_error,
    normalize_timestamp,
    require_backend,
)

CONNECTOR_NAME = "Apple Calendar"
REQUIRED_FRAMEWORK = "EventKit (Calendar.app)"
SUPPORTED_PLATFORM = "darwin"

DEFAULT_LIMIT = 20
MAX_LIMIT = 100
DEFAULT_DAYS_AHEAD = 7
DEFAULT_DAYS_BEHIND = 1
MAX_DAYS = 366


@dataclass(frozen=True)
class CalendarEvent:
    event_id: str
    title: str
    calendar: str = ""
    start: str = ""
    end: str = ""
    all_day: bool = False
    location: str = ""
    has_attendees: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "calendar": self.calendar,
            "start": self.start,
            "end": self.end,
            "all_day": self.all_day,
            "location": self.location,
            "has_attendees": self.has_attendees,
        }


def _event_from_row(row: dict[str, Any]) -> Optional[CalendarEvent]:
    title = bounded_text(
        row.get("title") or row.get("summary"), MAX_LONG_TEXT_CHARS)
    event_id = bounded_text(
        row.get("event_id") or row.get("id") or row.get("uuid") or row.get("uid"),
        MAX_ID_CHARS,
    )
    if not title and not event_id:
        return None
    if not event_id:
        event_id = f"{title}:{normalize_timestamp(row.get('start'))}"[:MAX_ID_CHARS]
    return CalendarEvent(
        event_id=event_id,
        title=title,
        calendar=bounded_text(
            row.get("calendar") or row.get("calendar_title"), MAX_TEXT_CHARS),
        start=normalize_timestamp(row.get("start") or row.get("start_date")),
        end=normalize_timestamp(row.get("end") or row.get("end_date")),
        all_day=coerce_bool(row.get("all_day", row.get("allDay"))),
        location=bounded_text(row.get("location"), MAX_LONG_TEXT_CHARS),
        has_attendees=coerce_bool(
            row.get("has_attendees", row.get("hasAttendees"))),
    )


class AppleCalendarConnector:
    """Narrow read-only Calendar surface over an injected framework adapter."""

    connector_id = "apple_calendar"

    def __init__(
        self,
        *,
        platform: Optional[str] = None,
        backend: Any = None,
        days_ahead: int = DEFAULT_DAYS_AHEAD,
        days_behind: int = DEFAULT_DAYS_BEHIND,
    ) -> None:
        self._platform = platform or current_platform()
        self._backend = backend
        self._days_ahead = days_ahead
        self._days_behind = days_behind

    @property
    def platform(self) -> str:
        return self._platform

    def available(self) -> bool:
        return self._platform == SUPPORTED_PLATFORM and self._backend is not None

    def list_events(
        self,
        *,
        days_ahead: object = None,
        days_behind: object = None,
        limit: object = DEFAULT_LIMIT,
    ) -> list[CalendarEvent]:
        ensure_supported(
            platform=self._platform,
            required_framework=REQUIRED_FRAMEWORK,
            connector=CONNECTOR_NAME,
        )
        ahead = coerce_days(
            days_ahead if days_ahead is not None else self._days_ahead,
            default=DEFAULT_DAYS_AHEAD,
            maximum=MAX_DAYS,
        )
        behind = coerce_days(
            days_behind if days_behind is not None else self._days_behind,
            default=DEFAULT_DAYS_BEHIND,
            maximum=MAX_DAYS,
        )
        bounded = coerce_limit(limit, default=DEFAULT_LIMIT, maximum=MAX_LIMIT)
        backend = require_backend(
            self._backend,
            platform=self._platform,
            required_framework=REQUIRED_FRAMEWORK,
            connector=CONNECTOR_NAME,
        )
        try:
            payload = backend.list_events(
                days_ahead=ahead, days_behind=behind, limit=bounded)
        except AppleConnectorError as exc:
            raise exc
        except Exception as exc:
            raise map_backend_error(
                exc,
                connector=CONNECTOR_NAME,
                required_framework=REQUIRED_FRAMEWORK,
                platform=self._platform,
            ) from None
        rows = backend_rows(
            payload,
            connector=CONNECTOR_NAME,
            required_framework=REQUIRED_FRAMEWORK,
            platform=self._platform,
        )
        events: list[CalendarEvent] = []
        for row in rows:
            event = _event_from_row(row)
            if event is None:
                continue
            events.append(event)
            if len(events) >= bounded:
                break
        return events

