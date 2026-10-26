"""Read-only Google Calendar tool definition for the Phase 5 connector.

Exposes one model-facing action surface (``list`` / ``search``) over
``connectors.google_calendar.GoogleCalendarConnector``. Credential discovery,
the OAuth token refresh, scope checks and typed refusals live in the connector;
this module only validates arguments and renders bounded JSON.

This module deliberately does not register itself in the global tool registry:
wiring the toolset is a separate, explicit decision. Call
``build_google_calendar_tool()`` to obtain the definition.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from connectors.google_calendar import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    GoogleCalendarConnector,
    GoogleCalendarError,
    REASON_INVALID_REQUEST,
)
from tools.registry import tool_error, tool_result

logger = logging.getLogger(__name__)

TOOL_NAME = "google_calendar"
TOOLSET = "google_calendar"
_MAX_RESULT_CHARS = 100_000


def _google_calendar_configured() -> bool:
    """Reachability probe: the existing Google Workspace token must be present."""
    try:
        return GoogleCalendarConnector().configured()
    except Exception:
        return False


def _error_payload(exc: GoogleCalendarError) -> str:
    extra: dict[str, Any] = {"reason": exc.reason}
    if exc.scope:
        extra["scope"] = exc.scope
    if exc.status is not None:
        extra["status"] = exc.status
    return tool_error(exc.detail or exc.reason, **extra)


def handle_google_calendar(
    args: dict[str, Any],
    *,
    connector_factory: Optional[Callable[[], GoogleCalendarConnector]] = None,
) -> str:
    """Dispatch one read-only ``google_calendar`` action to the connector."""
    action = str(args.get("action") or "list").strip().lower()
    if action not in {"list", "search"}:
        return tool_error(
            "action must be 'list' or 'search'; Google Calendar is read-only",
            reason=REASON_INVALID_REQUEST)

    time_min = str(args.get("time_min") or "").strip()
    if not time_min:
        return tool_error(
            "time_min is required (RFC3339 timestamp)", reason=REASON_INVALID_REQUEST)
    time_max = str(args.get("time_max") or "").strip()

    query = str(args.get("query") or "").strip()
    if action == "search" and not query:
        return tool_error(
            "query is required for action='search'", reason=REASON_INVALID_REQUEST)

    calendar_id = str(args.get("calendar_id") or "").strip() or None
    limit = args.get("limit", DEFAULT_LIMIT)
    try:
        connector = (connector_factory or GoogleCalendarConnector)()
        if action == "search":
            events = connector.search(
                query, time_min, time_max, limit=limit,
                **({"calendar_id": calendar_id} if calendar_id else {}))
        else:
            events = connector.list_events(
                time_min, time_max, limit=limit,
                **({"calendar_id": calendar_id} if calendar_id else {}))
    except GoogleCalendarError as exc:
        return _error_payload(exc)
    except Exception as exc:  # never leak an exception body into model context
        logger.warning("google_calendar tool failed with %s", type(exc).__name__)
        return tool_error("google_calendar failed", reason="tool_error")

    payload: dict[str, Any] = {
        "source": TOOL_NAME,
        "action": action,
        "count": len(events),
        "events": [event.as_dict() for event in events],
    }
    if action == "search":
        payload["query"] = query[:256]
    return tool_result(payload)


GOOGLE_CALENDAR_SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Read the user's Google Calendar events (read-only). Set action='list' "
        "to read events between time_min and time_max, or action='search' with "
        "a query for the Calendar API's free-text event search. Times are "
        "RFC3339 timestamps. Returns bounded event ids, titles, start/end "
        "times, an all-day flag and attendee counts. This tool cannot create, "
        "update, delete or invite anyone to an event. It requires the existing "
        "Google Workspace token; a missing credential or missing "
        "calendar.readonly scope is returned as a typed error, never as a "
        "silent empty list."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "search"],
                "description": "list events in a time window, or search by text.",
            },
            "time_min": {
                "type": "string",
                "description": "Inclusive start of the window, RFC3339 (required).",
            },
            "time_max": {
                "type": "string",
                "description": "Exclusive end of the window, RFC3339 (optional).",
            },
            "query": {
                "type": "string",
                "description": (
                    "Free-text search text matched by the Calendar API. "
                    "Required when action='search'."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_LIMIT,
                "default": DEFAULT_LIMIT,
                "description": f"Maximum events to return (1-{MAX_LIMIT}).",
            },
            "calendar_id": {
                "type": "string",
                "description": "Calendar id; defaults to 'primary'.",
            },
        },
        "required": ["action", "time_min"],
    },
}


def build_google_calendar_tool() -> dict[str, Any]:
    """Return the tool definition without registering it globally."""
    return {
        "name": TOOL_NAME,
        "toolset": TOOLSET,
        "schema": GOOGLE_CALENDAR_SCHEMA,
        "handler": handle_google_calendar,
        "check_fn": _google_calendar_configured,
        "emoji": "📅",
        "description": "Read-only Google Calendar listing and search",
        "max_result_size_chars": _MAX_RESULT_CHARS,
    }


__all__ = [
    "GOOGLE_CALENDAR_SCHEMA",
    "TOOL_NAME",
    "TOOLSET",
    "build_google_calendar_tool",
    "handle_google_calendar",
]
