"""MCP capability list routes — resources / prompts / resource templates.

The upstream 0.21.4 ``mcp.py`` router (``hermes_cli/web_routers/mcp.py``) exposes
no resources/prompts/templates list route. The vendored transport does: the
agent's utility handlers drain ``session.list_resources``/``list_prompts``
(``qubit_runtime/tools/mcp_tool_handlers.py:534-542``) and the SDK session also
exposes ``list_resource_templates``. Qubit adds read-only dashboard list routes
on top of that same transport (``qubit_runtime/qubit_cli/web_routers/mcp.py``),
never a second MCP client.

Behavioral: the route table is the real mounted surface, the guards run without a
connection, and the template renderer is exercised with a real-shaped item.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "qubit_runtime") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "qubit_runtime"))


def _routes():
    from qubit_cli.web_routers import mcp as mcp_routes

    return mcp_routes


def test_capability_routes_are_registered():
    paths = {
        (route.path, tuple(sorted(route.methods)))
        for route in _routes().router.routes
    }
    assert ("/api/mcp/servers/{name}/resources", ("GET",)) in paths
    assert ("/api/mcp/servers/{name}/prompts", ("GET",)) in paths
    assert ("/api/mcp/servers/{name}/resource-templates", ("GET",)) in paths


def test_unknown_server_is_a_typed_404_without_connecting():
    mcp_routes = _routes()
    for handler in (
        mcp_routes.list_mcp_server_resources,
        mcp_routes.list_mcp_server_prompts,
        mcp_routes.list_mcp_server_resource_templates,
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(handler("does-not-exist", None))
        assert exc.value.status_code == 404
        assert "not found" in str(exc.value.detail).lower()


class _Template:
    uri_template = "file:///{path}"
    name = "readme"
    description = "Read a file"
    mime_type = "text/markdown"


def test_render_resource_templates_matches_resource_wire_shape():
    out = _routes()._render_resource_templates([_Template()], "srv")
    assert out == {
        "resource_templates": [
            {
                "uriTemplate": "file:///{path}",
                "name": "readme",
                "description": "Read a file",
                "mimeType": "text/markdown",
            }
        ]
    }
