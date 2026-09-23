"""9U — OpenClaw mcp.app and board families.

Static behavior proof (the manifest's own checks) plus runtime proof against the
canonical authorities: the in-process MCP/plugin host
(``voicebridge.plugin_runtime``) and the slug-scoped kanban board
(``voicebridge.kanban``).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import openclaw_upgrade_manifest as OUM  # noqa: E402

MCP_MODULE = "voicebridge/gateway/openclaw_handlers/mcp_app.py"
BOARD_MODULE = "voicebridge/gateway/openclaw_handlers/board.py"

MCP_SAFE = {
    "mcp.app.listResources": "MCP_RESOURCES_LOCAL_TOOL",
    "mcp.app.readResource": "MCP_READ_RESOURCE_LOCAL_TOOL",
}
MCP_OPS = {
    "mcp.app.listResourceTemplates": "MCP_RESOURCE_TEMPLATES_OPERATOR_REQUIRED",
    "mcp.app.updateModelContext": "MCP_APP_SANDBOX_OPERATOR_REQUIRED",
}
MCP_IMPL = ("mcp.app.callTool",)

BOARD_SAFE = {"board.widget.grant": "BOARD_WIDGET_GRANT_LOCAL_ROLES"}
BOARD_OPS = {
    "board.event": "BOARD_EVENT_OPERATOR_REQUIRED",
    "board.prompt.authorize": "BOARD_VIEW_TICKET_OPERATOR_REQUIRED",
    "board.action": "BOARD_ACTION_OPERATOR_REQUIRED",
}
BOARD_IMPL = ("board.update",)


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _registry():
    from voicebridge.gateway import rpc as R
    return R.default_registry()


def _family(module, implemented, safe, ops):
    sources = OUM.registry_sources(REPO_ROOT / "voicebridge" / "gateway"
                                   / "rpc.py")
    methods, funcs, consts, origins = OUM._parse_rpc_handlers_multi(sources)
    registry = set(_registry().names())
    ours = {m for m, origin in origins.items() if origin.endswith(module)}
    assert ours == set(implemented) | set(safe) | set(ops), ours
    assert ours <= registry, f"unregistered: {ours - registry}"
    for method in sorted(ours):
        node = methods[method]
        code = safe.get(method) or ops.get(method)
        if code:
            assert OUM._emits_code(node, funcs, code, consts), (
                f"{method} never emits {code!r}")
        else:
            assert OUM._is_non_trivial(node, funcs), f"{method} does no work"


def test_mcp_family_static_proof():
    _family(MCP_MODULE, MCP_IMPL, MCP_SAFE, MCP_OPS)


def test_board_family_static_proof():
    _family(BOARD_MODULE, BOARD_IMPL, BOARD_SAFE, BOARD_OPS)


def test_manifest_dispositions_match_families():
    import json

    rows = json.loads((REPO_ROOT / "docs" / "archive" / "2026-09-21"
                       / "QUBIT_OPENCLAW_UPGRADE_MANIFEST.json"
                       ).read_text())["pinned_surface_disposition"]["methods"]
    for m in MCP_IMPL + BOARD_IMPL:
        assert rows[m]["status"] == "IMPLEMENTED", rows[m]
    for m, c in {**MCP_SAFE, **BOARD_SAFE}.items():
        assert rows[m]["status"] == "SAFE_EQUIVALENT", rows[m]
        assert rows[m]["code"] == c, rows[m]
    for m, c in {**MCP_OPS, **BOARD_OPS}.items():
        assert rows[m]["status"] == "OPERATOR_ONLY", rows[m]
        assert rows[m]["code"] == c, rows[m]


# --------------------------------------------------------------- mcp runtime
def test_mcp_call_tool_invokes_the_in_process_host(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    # With confirmation the canonical in-process handler runs.
    out = reg.handle(cfg, "mcp.app.callTool",
                     {"toolName": "builtin.echo", "arguments": {"text": "hi"},
                      "confirm": True})
    assert out["state"] == "OK", out
    assert out["result"]["echo"] == "hi", out


def test_mcp_typed_refusals(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    res = reg.handle(cfg, "mcp.app.listResources", {})
    assert res["code"] == "MCP_RESOURCES_LOCAL_TOOL", res
    read = reg.handle(cfg, "mcp.app.readResource", {"uri": "file:///x"})
    assert read["code"] == "MCP_READ_RESOURCE_LOCAL_TOOL", read
    tmpl = reg.handle(cfg, "mcp.app.listResourceTemplates", {})
    assert tmpl["code"] == "MCP_RESOURCE_TEMPLATES_OPERATOR_REQUIRED", tmpl
    ctx = reg.handle(cfg, "mcp.app.updateModelContext", {})
    assert ctx["code"] == "MCP_APP_SANDBOX_OPERATOR_REQUIRED", ctx

    from voicebridge.gateway.gateway_ws import GatewayError
    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "mcp.app.callTool", {})
    assert exc.value.code == "GATEWAY_BAD_PARAMS", exc.value


def test_mcp_call_tool_without_confirmation_is_refused(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "mcp.app.callTool",
                   {"toolName": "builtin.echo", "arguments": {}})
    assert exc.value.code == "PLUGIN_CONFIRM_REQUIRED", exc.value


# ------------------------------------------------------------- board runtime
def _board(tmp_path, monkeypatch):
    from voicebridge import kanban as K

    cfg = _cfg(tmp_path, monkeypatch)
    K.boards_create(cfg, "main", confirmed=True)
    return cfg


def test_board_update_applies_real_tab_ops(tmp_path, monkeypatch):
    cfg = _board(tmp_path, monkeypatch)
    reg = _registry()

    out = reg.handle(cfg, "board.update",
                     {"board": "main",
                      "ops": [{"kind": "tab_create", "tabId": "work",
                               "title": "Work"}]})
    assert out["state"] == "OK", out
    assert out["applied"] == [{"kind": "tab_create", "tabId": "work"}], out
    assert "work" in {t["slug"] for t in out["tabs"]}, out


def test_board_update_rejects_name_addressed_widget_ops(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _board(tmp_path, monkeypatch)
    reg = _registry()
    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "board.update",
                   {"ops": [{"kind": "widget_move", "name": "w1"}]})
    assert exc.value.code == "BOARD_UPDATE_UNSUPPORTED_OP", exc.value
    with pytest.raises(GatewayError) as exc2:
        reg.handle(cfg, "board.update", {"ops": []})
    assert exc2.value.code == "GATEWAY_BAD_PARAMS", exc2.value


def test_board_typed_refusals(tmp_path, monkeypatch):
    cfg = _board(tmp_path, monkeypatch)
    reg = _registry()

    grant = reg.handle(cfg, "board.widget.grant", {"name": "w1"})
    assert grant["code"] == "BOARD_WIDGET_GRANT_LOCAL_ROLES", grant
    ev = reg.handle(cfg, "board.event", {})
    assert ev["code"] == "BOARD_EVENT_OPERATOR_REQUIRED", ev
    pa = reg.handle(cfg, "board.prompt.authorize", {})
    assert pa["code"] == "BOARD_VIEW_TICKET_OPERATOR_REQUIRED", pa
    act = reg.handle(cfg, "board.action", {})
    assert act["code"] == "BOARD_ACTION_OPERATOR_REQUIRED", act
