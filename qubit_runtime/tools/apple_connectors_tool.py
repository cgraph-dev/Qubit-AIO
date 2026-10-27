"""Model-facing definitions and registry wiring for the read-only Apple pack.

The four tools register in their own toolsets at import, each behind a
``check_fn`` that is true only on macOS when the matching framework module is
installed. Callers that provide their own read-only framework adapter can still
use the handler factories directly.

On non-macOS hosts every handler returns a typed ``not_supported`` error naming
the host platform and the required macOS framework. It never returns an empty
success payload in place of a refusal.
"""

from __future__ import annotations

import importlib.util
import logging
import shutil
import sys
from typing import Any, Callable, Optional

from connectors.apple_calendar import (
    DEFAULT_DAYS_AHEAD,
    DEFAULT_DAYS_BEHIND,
    DEFAULT_LIMIT as CALENDAR_DEFAULT_LIMIT,
    MAX_DAYS,
    MAX_LIMIT as CALENDAR_MAX_LIMIT,
    AppleCalendarConnector,
)
from connectors.apple_common import AppleConnectorError, REASON_INVALID_REQUEST
from connectors.apple_contacts import (
    DEFAULT_LIMIT as CONTACTS_DEFAULT_LIMIT,
    MAX_LIMIT as CONTACTS_MAX_LIMIT,
    AppleContactsConnector,
)
from connectors.apple_health import (
    DEFAULT_DAYS as HEALTH_DEFAULT_DAYS,
    MAX_DAYS as HEALTH_MAX_DAYS,
    AppleHealthConnector,
)
from connectors.apple_music import (
    DEFAULT_LIMIT as MUSIC_DEFAULT_LIMIT,
    MAX_LIMIT as MUSIC_MAX_LIMIT,
    AppleMusicConnector,
)
from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)


APPLE_CALENDAR_SCHEMA = {
    "name": "apple_calendar",
    "description": (
        "Read-only Apple Calendar events from the local macOS Calendar. Use this "
        "to list upcoming events in a bounded window. It returns event title, "
        "calendar, start, end, all-day flag, location and attendee presence. It "
        "cannot create, edit, delete, invite or respond to events. It is only "
        "available on macOS (darwin) with the EventKit framework; other platforms "
        "return a typed not_supported error naming the host platform."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "days_ahead": {
                "type": "integer",
                "minimum": 0,
                "maximum": MAX_DAYS,
                "default": DEFAULT_DAYS_AHEAD,
                "description": "Days into the future to include (default 7).",
            },
            "days_behind": {
                "type": "integer",
                "minimum": 0,
                "maximum": MAX_DAYS,
                "default": DEFAULT_DAYS_BEHIND,
                "description": "Days into the past to include (default 1).",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": CALENDAR_MAX_LIMIT,
                "default": CALENDAR_DEFAULT_LIMIT,
                "description": f"Maximum events to return (1-{CALENDAR_MAX_LIMIT}).",
            },
        },
        "required": [],
    },
}

APPLE_CONTACTS_SCHEMA = {
    "name": "apple_contacts",
    "description": (
        "Read-only Apple Contacts search and listing from the local macOS address "
        "book. Returns bounded names, email addresses, phone numbers, "
        "organization, job title and birthday fields. It cannot create, edit, "
        "delete or message contacts. It is only available on macOS (darwin) with "
        "the Contacts framework; other platforms return a typed not_supported "
        "error naming the host platform."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "list"],
                "description": "Search a query, or list contacts.",
            },
            "query": {
                "type": "string",
                "description": (
                    "Search text matched against names, organizations, job titles "
                    "and nicknames. Required when action='search'."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": CONTACTS_MAX_LIMIT,
                "default": CONTACTS_DEFAULT_LIMIT,
                "description": f"Maximum contacts to return (1-{CONTACTS_MAX_LIMIT}).",
            },
        },
        "required": ["action"],
    },
}

APPLE_HEALTH_SCHEMA = {
    "name": "apple_health",
    "description": (
        "Read-only Apple Health daily summary from the local macOS HealthKit data. "
        "Returns per-day steps, sleep seconds, heart-rate average/min/max, active "
        "energy and workout count when the framework reports them; a missing "
        "metric stays null rather than being invented. It cannot write samples, "
        "log workouts or delete data. It is only available on macOS (darwin) with "
        "HealthKit; other platforms return a typed not_supported error naming the "
        "host platform."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "minimum": 1,
                "maximum": HEALTH_MAX_DAYS,
                "default": HEALTH_DEFAULT_DAYS,
                "description": f"Days of summary to read (1-{HEALTH_MAX_DAYS}).",
            },
        },
        "required": [],
    },
}

APPLE_MUSIC_SCHEMA = {
    "name": "apple_music",
    "description": (
        "Read-only Apple Music library listing from the local macOS Music app. "
        "Returns bounded track name, artist, album, duration, genre, play count "
        "and last-played time. It cannot play, add, remove, rate or download "
        "tracks. It is only available on macOS (darwin) with MusicKit/Music.app; "
        "other platforms return a typed not_supported error naming the host "
        "platform."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MUSIC_MAX_LIMIT,
                "default": MUSIC_DEFAULT_LIMIT,
                "description": f"Maximum tracks to return (1-{MUSIC_MAX_LIMIT}).",
            },
        },
        "required": [],
    },
}


def _args(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _error_payload(exc: AppleConnectorError) -> str:
    return tool_error(exc.detail or exc.reason, **exc.as_extra())


def _tool_failure(name: str, exc: Exception) -> str:
    if isinstance(exc, AppleConnectorError):
        return _error_payload(exc)
    logger.warning("%s tool failed with %s", name, type(exc).__name__)
    return tool_error(f"{name} failed", reason="tool_error")


def _framework_module_available(module_name: str) -> bool:
    """True when the macOS framework bridge module is importable on this host."""
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _apple_host_ready(*framework_modules: str) -> bool:
    """macOS only, and at least one of the tool's framework modules must exist."""
    if sys.platform != "darwin":
        return False
    return any(_framework_module_available(name) for name in framework_modules)


def apple_calendar_configured() -> bool:
    return _apple_host_ready("EventKit")


def apple_contacts_configured() -> bool:
    return _apple_host_ready("Contacts")


def apple_health_configured() -> bool:
    return _apple_host_ready("HealthKit")


def apple_music_configured() -> bool:
    if _apple_host_ready("MusicKit", "ScriptingBridge"):
        return True
    return sys.platform == "darwin" and shutil.which("osascript") is not None


def make_calendar_handler(
    *,
    platform: Optional[str] = None,
    backend: Any = None,
    connector_factory: Optional[Callable[[], AppleCalendarConnector]] = None,
) -> Callable[[dict[str, Any]], str]:
    def handler(args: object) -> str:
        params = _args(args)
        try:
            connector = (
                connector_factory()
                if connector_factory is not None
                else AppleCalendarConnector(platform=platform, backend=backend)
            )
            events = connector.list_events(
                days_ahead=params.get("days_ahead"),
                days_behind=params.get("days_behind"),
                limit=params.get("limit", CALENDAR_DEFAULT_LIMIT),
            )
        except Exception as exc:
            return _tool_failure("apple_calendar", exc)
        return tool_result({
            "source": "apple_calendar",
            "count": len(events),
            "events": [event.as_dict() for event in events],
        })

    return handler


def make_contacts_handler(
    *,
    platform: Optional[str] = None,
    backend: Any = None,
    connector_factory: Optional[Callable[[], AppleContactsConnector]] = None,
) -> Callable[[dict[str, Any]], str]:
    def handler(args: object) -> str:
        params = _args(args)
        action = str(params.get("action") or "list").strip().lower()
        if action not in {"search", "list"}:
            return tool_error(
                "action must be 'search' or 'list'; Apple Contacts is read-only",
                reason=REASON_INVALID_REQUEST,
            )
        query = str(params.get("query") or "").strip()
        if action == "search" and not query:
            return tool_error(
                "query is required for action='search'",
                reason=REASON_INVALID_REQUEST,
            )
        try:
            connector = (
                connector_factory()
                if connector_factory is not None
                else AppleContactsConnector(platform=platform, backend=backend)
            )
            contacts = (
                connector.search_contacts(
                    query, limit=params.get("limit", CONTACTS_DEFAULT_LIMIT))
                if action == "search"
                else connector.list_contacts(
                    limit=params.get("limit", CONTACTS_DEFAULT_LIMIT))
            )
        except Exception as exc:
            return _tool_failure("apple_contacts", exc)
        payload: dict[str, Any] = {
            "source": "apple_contacts",
            "action": action,
            "count": len(contacts),
            "contacts": [contact.as_dict() for contact in contacts],
        }
        if action == "search":
            payload["query"] = query[:256]
        return tool_result(payload)

    return handler


def make_health_handler(
    *,
    platform: Optional[str] = None,
    backend: Any = None,
    connector_factory: Optional[Callable[[], AppleHealthConnector]] = None,
) -> Callable[[dict[str, Any]], str]:
    def handler(args: object) -> str:
        params = _args(args)
        try:
            connector = (
                connector_factory()
                if connector_factory is not None
                else AppleHealthConnector(platform=platform, backend=backend)
            )
            days = connector.read_summary(
                days=params.get("days", HEALTH_DEFAULT_DAYS))
        except Exception as exc:
            return _tool_failure("apple_health", exc)
        return tool_result({
            "source": "apple_health",
            "count": len(days),
            "days": [day.as_dict() for day in days],
        })

    return handler


def make_music_handler(
    *,
    platform: Optional[str] = None,
    backend: Any = None,
    connector_factory: Optional[Callable[[], AppleMusicConnector]] = None,
) -> Callable[[dict[str, Any]], str]:
    def handler(args: object) -> str:
        params = _args(args)
        try:
            connector = (
                connector_factory()
                if connector_factory is not None
                else AppleMusicConnector(platform=platform, backend=backend)
            )
            tracks = connector.list_tracks(
                limit=params.get("limit", MUSIC_DEFAULT_LIMIT))
        except Exception as exc:
            return _tool_failure("apple_music", exc)
        return tool_result({
            "source": "apple_music",
            "count": len(tracks),
            "tracks": [track.as_dict() for track in tracks],
        })

    return handler


def apple_tool_definitions() -> tuple[dict[str, Any], ...]:
    """Return definitions only; nothing is registered as a side effect."""
    return (
        {
            "name": "apple_calendar",
            "toolset": "apple_calendar",
            "schema": APPLE_CALENDAR_SCHEMA,
            "emoji": "\U0001F4C5",
            "description": "Read-only Apple Calendar events",
        },
        {
            "name": "apple_contacts",
            "toolset": "apple_contacts",
            "schema": APPLE_CONTACTS_SCHEMA,
            "emoji": "\U0001F465",
            "description": "Read-only Apple Contacts search and listing",
        },
        {
            "name": "apple_health",
            "toolset": "apple_health",
            "schema": APPLE_HEALTH_SCHEMA,
            "emoji": "\U0001FA7A",
            "description": "Read-only Apple Health daily summary",
        },
        {
            "name": "apple_music",
            "toolset": "apple_music",
            "schema": APPLE_MUSIC_SCHEMA,
            "emoji": "\U0001F3B5",
            "description": "Read-only Apple Music library listing",
        },
    )


APPLE_TOOL_REGISTRATIONS = (
    {
        "name": "apple_calendar",
        "toolset": "apple_calendar",
        "schema": APPLE_CALENDAR_SCHEMA,
        "handler": make_calendar_handler(),
        "check_fn": apple_calendar_configured,
        "emoji": "\U0001F4C5",
        "description": "Read-only Apple Calendar events",
    },
    {
        "name": "apple_contacts",
        "toolset": "apple_contacts",
        "schema": APPLE_CONTACTS_SCHEMA,
        "handler": make_contacts_handler(),
        "check_fn": apple_contacts_configured,
        "emoji": "\U0001F465",
        "description": "Read-only Apple Contacts search and listing",
    },
    {
        "name": "apple_health",
        "toolset": "apple_health",
        "schema": APPLE_HEALTH_SCHEMA,
        "handler": make_health_handler(),
        "check_fn": apple_health_configured,
        "emoji": "\U0001FA7A",
        "description": "Read-only Apple Health daily summary",
    },
    {
        "name": "apple_music",
        "toolset": "apple_music",
        "schema": APPLE_MUSIC_SCHEMA,
        "handler": make_music_handler(),
        "check_fn": apple_music_configured,
        "emoji": "\U0001F3B5",
        "description": "Read-only Apple Music library listing",
    },
)

for _registration in APPLE_TOOL_REGISTRATIONS:
    registry.register(max_result_size_chars=100_000, **_registration)
del _registration
