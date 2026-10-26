"""Read-only Google Contacts tool backed by the Phase 5 connector.

Exposes one model-facing action surface (``search`` / ``list``) over
``connectors.google_contacts.GoogleContactsConnector``. Credential discovery,
the OAuth token refresh, scope checks and typed refusals live in the connector;
this module only validates arguments and renders bounded JSON.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from connectors.google_contacts import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    GoogleContactsConnector,
    GoogleContactsError,
    REASON_INVALID_REQUEST,
)
from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

_MAX_RESULT_CHARS = 100_000


def _google_contacts_configured() -> bool:
    """Reachability probe: the existing Google Workspace token must be present."""
    try:
        return GoogleContactsConnector().configured()
    except Exception:
        return False


def _error_payload(exc: GoogleContactsError) -> str:
    extra: dict[str, Any] = {"reason": exc.reason}
    if exc.scope:
        extra["scope"] = exc.scope
    if exc.status is not None:
        extra["status"] = exc.status
    return tool_error(exc.detail or exc.reason, **extra)


def _handle_google_contacts(
    args: dict[str, Any],
    *,
    connector_factory: Optional[Callable[[], GoogleContactsConnector]] = None,
) -> str:
    action = str(args.get("action") or "list").strip().lower()
    if action not in {"search", "list"}:
        return tool_error(
            "action must be 'search' or 'list'; Google Contacts is read-only",
            reason=REASON_INVALID_REQUEST)

    query = str(args.get("query") or "").strip()
    if action == "search" and not query:
        return tool_error(
            "query is required for action='search'", reason=REASON_INVALID_REQUEST)

    limit = args.get("limit", DEFAULT_LIMIT)
    try:
        connector = (connector_factory or GoogleContactsConnector)()
        contacts = (
            connector.search(query, limit=limit)
            if action == "search"
            else connector.list(limit=limit)
        )
    except GoogleContactsError as exc:
        return _error_payload(exc)
    except Exception as exc:  # never leak an exception body into model context
        logger.warning("google_contacts tool failed with %s", type(exc).__name__)
        return tool_error("google_contacts failed", reason="tool_error")

    payload: dict[str, Any] = {
        "source": "google_contacts",
        "action": action,
        "count": len(contacts),
        "contacts": [contact.as_dict() for contact in contacts],
    }
    if action == "search":
        payload["query"] = query[:256]
    return tool_result(payload)


GOOGLE_CONTACTS_SCHEMA = {
    "name": "google_contacts",
    "description": (
        "Read the user's Google Contacts (read-only). Set action='search' with a "
        "query to find contacts by name, email address or organization (the "
        "People API caps search at 30 results), or action='list' to page "
        "through the address book (up to 100). Returns bounded names, "
        "email addresses, phone numbers, organization and title fields. This tool "
        "cannot create, edit, delete or message contacts. It requires the existing "
        "Google Workspace token; a missing credential or missing contacts.readonly "
        "scope is returned as a typed error, never as a silent empty list."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "list"],
                "description": "search a query, or list contacts.",
            },
            "query": {
                "type": "string",
                "description": (
                    "Search text matched against contact names, email addresses and "
                    "organizations. Required when action='search'."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_LIMIT,
                "default": DEFAULT_LIMIT,
                "description": (
                    f"Maximum contacts to return (1-{MAX_LIMIT}). Search is "
                    "additionally capped at 30 by the People API."
                ),
            },
        },
        "required": ["action"],
    },
}


registry.register(
    name="google_contacts",
    toolset="google_contacts",
    schema=GOOGLE_CONTACTS_SCHEMA,
    handler=_handle_google_contacts,
    check_fn=_google_contacts_configured,
    emoji="👥",
    description="Read-only Google Contacts search and listing",
    max_result_size_chars=_MAX_RESULT_CHARS,
)
