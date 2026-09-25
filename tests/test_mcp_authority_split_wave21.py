"""MCP authority split — the canonical registry owns every MCP route.

The vendored MCP router (``qubit_runtime/qubit_cli/web_routers/mcp.py``) reads
and writes the fork's ``config.yaml mcp_servers`` store. Qubit's canonical MCP
authority is ``voicebridge.mcp_servers`` (one store, e-stop + confirmation +
audit). This test locks the invariant that every route the vendored router
declares is owned by the compat table (``voicebridge.qubit_compat``) — reads
through ``webui.QUBIT_READ_AUTHORITY`` so they can never be fork-proxied — and
that the vendored router is therefore unreachable for MCP.

Hermetic: route tables only, no live server.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_MCP_ROUTER = (REPO_ROOT / "qubit_runtime" / "qubit_cli" / "web_routers"
               / "mcp.py")

_DECORATOR = re.compile(
    r'@router\.(get|post|put|delete|patch)\(\s*"([^"]+)"')

# Concrete request samples for the vendored templates (``{name}`` etc.).
_SAMPLES = {
    "/api/mcp/servers/{name}": "/api/mcp/servers/cgraph-graft",
    "/api/mcp/servers/{name}/test": "/api/mcp/servers/cgraph-graft/test",
    "/api/mcp/servers/{name}/resources":
        "/api/mcp/servers/cgraph-graft/resources",
    "/api/mcp/servers/{name}/prompts":
        "/api/mcp/servers/cgraph-graft/prompts",
    "/api/mcp/servers/{name}/resource-templates":
        "/api/mcp/servers/cgraph-graft/resource-templates",
    "/api/mcp/servers/{name}/auth":
        "/api/mcp/servers/cgraph-graft/auth",
    "/api/mcp/servers/{name}/enabled":
        "/api/mcp/servers/cgraph-graft/enabled",
    "/api/mcp/oauth/flows/{flow_id}": "/api/mcp/oauth/flows/abc",
    "/api/mcp/oauth/callback/{server_name:path}":
        "/api/mcp/oauth/callback/foo",
}


def _vendored_routes():
    assert _MCP_ROUTER.is_file(), _MCP_ROUTER
    out = []
    for method, tmpl in _DECORATOR.findall(_MCP_ROUTER.read_text()):
        out.append((method.upper(), tmpl))
    return out


def _sample(tmpl: str) -> str:
    concrete = _SAMPLES.get(tmpl)
    if concrete is not None:
        return concrete
    # Fallback: substitute any remaining ``{param}``/``{param:type}`` segment.
    return re.sub(r"\{[^}]+\}", "x", tmpl)


def test_every_vendored_mcp_route_has_a_compat_handler():
    """No vendored MCP route can become the authority: each resolves in the
    compat table to a real handler (or a registered typed gate)."""
    from voicebridge import qubit_compat as HC

    routes = _vendored_routes()
    assert routes, "vendored MCP router routes were not discovered"
    for method, tmpl in routes:
        path = _sample(tmpl)
        route, params = HC.match(method, path)
        assert route is not None, (method, tmpl, path)
        assert route.get("handler"), (method, tmpl, route)


def test_vendored_mcp_reads_are_quarantined_from_the_fork_proxy():
    """Every GET the vendored MCP router declares is in
    ``webui.QUBIT_READ_AUTHORITY`` (or resolves to a compat gate), so the
    fork's ``config.yaml mcp_servers`` store can never serve a dashboard read."""
    from voicebridge import webui
    from voicebridge import qubit_compat as HC

    for method, tmpl in _vendored_routes():
        if method not in ("GET", "HEAD"):
            continue
        path = _sample(tmpl)
        route, _ = HC.match(method, path)
        quarantined = webui.qubit_read_authority(path) or route is not None
        assert quarantined, (method, tmpl, path)


def test_specific_mcp_reads_are_declared_read_authority():
    """The multi-segment capability reads are explicitly quarantined (their
    vendored variants exist but must never win)."""
    from voicebridge import webui

    for path in (
        "/api/mcp/servers",
        "/api/mcp/catalog",
        "/api/mcp/servers/cgraph-graft/resources",
        "/api/mcp/servers/cgraph-graft/prompts",
        "/api/mcp/servers/cgraph-graft/resource-templates",
    ):
        assert webui.qubit_read_authority(path), path


def test_canonical_registry_is_the_invocation_authority():
    """The one invocation authority is ``voicebridge.mcp_servers``; the compat
    invoke handler resolves through it (not the fork store)."""
    from voicebridge import qubit_compat as HC

    route, _ = HC.match("POST", "/api/mcp/servers/cgraph-graft/invoke")
    assert route is not None and route["handler"] == "mcp_invoke"
    assert callable(HC.HANDLERS.get("mcp_invoke"))
