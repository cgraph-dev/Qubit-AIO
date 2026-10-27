"""Read-only Dropbox tool definition and registry wiring.

This module exposes a model-facing tool surface over
``connectors.dropbox.DropboxConnector`` and registers it in the ``dropbox``
toolset at import. Its ``check_fn`` keeps the schema out of every session until
the documented Dropbox token file is present.

The three actions are ``list_folder``, ``metadata`` and ``search``. There is no
upload, delete, create, move, copy or share action. Errors keep the connector's
typed reason (``missing_credential``, ``unauthorized``, ``rate_limited``, ...)
and never carry a token or a raw API body.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from connectors.dropbox import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    DropboxConnector,
    DropboxError,
    REASON_INVALID_REQUEST,
)
from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

TOOL_NAME = "dropbox"
TOOLSET = "dropbox"
_MAX_RESULT_CHARS = 100_000
_READ_ACTIONS = ("list_folder", "metadata", "search")


def dropbox_tool_configured() -> bool:
    """Reachability probe: the documented Dropbox token file must be usable."""
    try:
        return DropboxConnector().configured()
    except Exception:
        return False


def _error_payload(exc: DropboxError) -> str:
    extra: dict[str, Any] = {"reason": exc.reason}
    if exc.status is not None:
        extra["status"] = exc.status
    return tool_error(exc.detail or exc.reason, **extra)


def dropbox_tool_handler(
    args: dict[str, Any],
    *,
    connector_factory: Optional[Callable[[], DropboxConnector]] = None,
) -> str:
    """Run one read-only Dropbox action and return bounded JSON."""
    if not isinstance(args, dict):
        return tool_error("arguments must be an object", reason=REASON_INVALID_REQUEST)

    action = str(args.get("action") or "").strip().lower()
    if action not in _READ_ACTIONS:
        return tool_error(
            "action must be 'list_folder', 'metadata' or 'search'; Dropbox is read-only",
            reason=REASON_INVALID_REQUEST,
        )

    path = str(args.get("path") or "").strip()
    query = str(args.get("query") or "").strip()
    if action == "search" and not query:
        return tool_error(
            "query is required for action='search'", reason=REASON_INVALID_REQUEST)
    if action == "metadata" and not path:
        return tool_error(
            "path is required for action='metadata'", reason=REASON_INVALID_REQUEST)

    limit = args.get("limit", DEFAULT_LIMIT)
    recursive = args.get("recursive") is True
    try:
        connector = (connector_factory or DropboxConnector)()
        if action == "search":
            entries = connector.search(query, path=path, limit=limit)
        elif action == "metadata":
            entries = [connector.get_metadata(path)]
        else:
            entries = connector.list_folder(
                path, limit=limit, recursive=recursive)
    except DropboxError as exc:
        return _error_payload(exc)
    except Exception as exc:  # never leak an exception body into model context
        logger.warning("dropbox tool failed with %s", type(exc).__name__)
        return tool_error("dropbox failed", reason="tool_error")

    payload: dict[str, Any] = {
        "source": "dropbox",
        "action": action,
        "count": len(entries),
        "entries": [entry.as_dict() for entry in entries],
    }
    if action == "list_folder":
        payload["path"] = path[:1024]
        payload["recursive"] = recursive
    elif action == "search":
        payload["query"] = query[:256]
        if path:
            payload["path"] = path[:1024]
    return tool_result(payload)


DROPBOX_TOOL_SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": (
        "Read the user's Dropbox (read-only). action='list_folder' lists one "
        "folder (path '' is the root; optional recursive=true), action='metadata' "
        "returns one file or folder row by path, and action='search' searches "
        "names and content with an optional path scope. Returns bounded name, "
        "path, id, size, modified timestamps, revision and content-hash fields. "
        "This tool cannot upload, download, create, move, copy, delete or share "
        "anything. It requires a Dropbox access token in "
        "$QUBIT_HOME/dropbox_token.json; a missing credential or an API refusal "
        "is returned as a typed error, never as a silent empty list."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(_READ_ACTIONS),
                "description": "list_folder, metadata, or search.",
            },
            "path": {
                "type": "string",
                "description": (
                    "Dropbox path ('' for the root when listing). Required for "
                    "action='metadata'; optional path scope for action='search'."
                ),
            },
            "query": {
                "type": "string",
                "description": "Search text. Required when action='search'.",
            },
            "recursive": {
                "type": "boolean",
                "default": False,
                "description": "List subfolders recursively (action='list_folder').",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_LIMIT,
                "default": DEFAULT_LIMIT,
                "description": f"Maximum entries to return (1-{MAX_LIMIT}).",
            },
        },
        "required": ["action"],
    },
}


@dataclass(frozen=True)
class DropboxToolDefinition:
    """An unwired tool definition a caller can register or inspect."""

    name: str
    toolset: str
    schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]
    check_fn: Callable[[], bool]
    max_result_size_chars: int = _MAX_RESULT_CHARS

    def as_registry_kwargs(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "toolset": self.toolset,
            "schema": copy.deepcopy(self.schema),
            "handler": self.handler,
            "check_fn": self.check_fn,
            "emoji": "🗂️",
            "description": "Read-only Dropbox listing, metadata and search",
            "max_result_size_chars": self.max_result_size_chars,
        }


def build_dropbox_tool(
    *,
    connector_factory: Optional[Callable[[], DropboxConnector]] = None,
) -> DropboxToolDefinition:
    """Build an inspectable Dropbox definition without replacing the registered tool."""
    schema = copy.deepcopy(DROPBOX_TOOL_SCHEMA)
    if connector_factory is None:
        handler = dropbox_tool_handler
    else:
        def handler(args: dict[str, Any]) -> str:
            return dropbox_tool_handler(
                args, connector_factory=connector_factory)
    return DropboxToolDefinition(
        name=TOOL_NAME,
        toolset=TOOLSET,
        schema=schema,
        handler=handler,
        check_fn=dropbox_tool_configured,
    )


registry.register(
    name=TOOL_NAME,
    toolset=TOOLSET,
    schema=DROPBOX_TOOL_SCHEMA,
    handler=dropbox_tool_handler,
    check_fn=dropbox_tool_configured,
    emoji="🗂️",
    description="Read-only Dropbox listing, metadata and search",
    max_result_size_chars=_MAX_RESULT_CHARS,
)


__all__ = [
    "DROPBOX_TOOL_SCHEMA",
    "DropboxToolDefinition",
    "TOOL_NAME",
    "TOOLSET",
    "build_dropbox_tool",
    "dropbox_tool_configured",
    "dropbox_tool_handler",
]
