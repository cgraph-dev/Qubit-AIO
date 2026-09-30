"""Connections toolset — local MCP-backed connector operations plus a truthful
operator-only boundary for externally-gated managed connectors.

The local half reads the canonical Qubit MCP registry
(``qubit_cli.mcp_config._get_mcp_servers`` and ``qubit_cli.mcp_catalog``) — the
same config the dashboard ``/api/mcp/*`` routes, the ``qubit mcp`` CLI, and the
desktop ``setup_mcp`` tool read, so all surfaces share one registry.

Managed connector accounts (Gmail, Notion, ...) are served by an external tool
gateway that Qubit deliberately does not run; those actions return a typed
``operator_only`` result naming the prerequisite. This module never fabricates a
connection, a link, or a credential reference.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from tools.registry import registry

# Actions the local MCP registry can drive through its canonical surfaces.
_LOCAL_MCP_ACTIONS = ("install", "enable", "authorize", "reconnect")
# Actions that require the external connector gateway.
_MANAGED_ACTIONS = ("connect",)
_GATEWAY_PREREQUISITE = (
    "external Qubit connector tool gateway (gateway.sock + portal "
    "account authorization); Qubit runs no managed-connector gateway"
)
_LOCAL_SURFACE = "`qubit mcp` in the terminal, or the dashboard Connections page"


def _configured_mcp_servers() -> Dict[str, dict]:
    """Configured local MCP servers from the canonical config registry."""
    try:
        from qubit_cli.mcp_config import _get_mcp_servers

        servers = _get_mcp_servers()
        return servers if isinstance(servers, dict) else {}
    except Exception:
        return {}


def _catalog_names() -> List[str]:
    """Local MCP catalog entry names, or [] when the catalog is unavailable."""
    try:
        from qubit_cli.mcp_catalog import list_catalog

        return sorted(str(entry.name) for entry in list_catalog())
    except Exception:
        return []


def manage_connections(action: str = "status", connectors: Any = None) -> str:
    """One entry point for the ``connections`` toolset.

    ``status`` reports the real local MCP inventory and the truthful managed
    boundary. Mutating local actions name the canonical surface; managed
    actions return a typed operator-only result.
    """
    action = str(action or "status").strip().lower() or "status"
    servers = _configured_mcp_servers()
    enabled = sorted(name for name, cfg in servers.items() if isinstance(cfg, dict) and cfg.get("enabled"))

    if action == "status":
        return json.dumps({
            "state": "ok",
            "local_mcp_servers": sorted(servers.keys()),
            "local_mcp_enabled": enabled,
            "local_mcp_catalog": _catalog_names(),
            "managed_connectors": {
                "state": "setup_required",
                "prerequisite": _GATEWAY_PREREQUISITE,
            },
        }, ensure_ascii=False)

    if action in _LOCAL_MCP_ACTIONS:
        # The consent-card / config-write path is owned by the canonical MCP
        # surface; the model tool points there rather than re-implementing it.
        return json.dumps({
            "state": "setup_required",
            "action": action,
            "surface": _LOCAL_SURFACE,
            "detail": "Manage local MCP servers through the canonical Qubit MCP surface.",
        }, ensure_ascii=False)

    if action in _MANAGED_ACTIONS:
        return json.dumps({
            "state": "operator_only",
            "action": action,
            "prerequisite": _GATEWAY_PREREQUISITE,
            "detail": "Managed connector accounts require the external tool gateway.",
        }, ensure_ascii=False)

    return json.dumps({
        "state": "error",
        "detail": f"unknown action {action!r}; expected status, one of "
                  f"{', '.join(_LOCAL_MCP_ACTIONS)}, or one of {', '.join(_MANAGED_ACTIONS)}",
    }, ensure_ascii=False)


MANAGE_CONNECTIONS_SCHEMA: Dict[str, Any] = {
    "name": "manage_connections",
    "description": (
        "Inspect and manage the user's connections. 'status' lists configured "
        "local MCP servers and catalog entries. Mutating local MCP actions "
        "(install, enable, authorize, reconnect) are performed through the "
        "canonical Qubit MCP surface. Managed connector accounts (Gmail, Notion, "
        "...) require an external tool gateway and return an operator-only "
        "result naming that prerequisite."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "install", "enable", "authorize", "reconnect", "connect"],
                "description": "Defaults to status.",
            },
            "connectors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Local MCP server names (install/enable/authorize/reconnect) or managed connector slugs (connect).",
            },
        },
        "required": [],
    },
}


def _check_available() -> bool:
    # The toolset is visible even without a gateway so the catalog can show the
    # truthful state instead of hiding the capability.
    return True


registry.register(
    name="manage_connections",
    toolset="connections",
    schema=MANAGE_CONNECTIONS_SCHEMA,
    handler=lambda args, **kw: manage_connections(
        action=args.get("action", "status"),
        connectors=args.get("connectors"),
    ),
    check_fn=_check_available,
    emoji="🔌",
)
