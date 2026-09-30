"""9U — OpenClaw agents / agents.workspace family.

Static behavior proof (the manifest's own checks) plus runtime proof against the
canonical authorities: the scoped read-only file authority
(``voicebridge.files``) and the shared task ledger (``voicebridge.task_ledger``).

Qubit is one unified agent, so the roster mutations are typed operator gates and
the gateway workspace write is a typed safe-equivalent; every read delegates to
the same module the CLI/dashboard/voice use.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import qubit_upgrade_manifest as OUM  # noqa: E402

FAMILY_MODULE = "voicebridge/gateway/qubit_handlers/agents.py"

SAFE_EQUIVALENT = {
    "agents.files.set": "AGENTS_FILES_SET_BROKERED_WRITE",
    "agent.wait": "AGENT_WAIT_LOCAL_TASK_STATE",
}
OPERATOR_ONLY = {
    "agents.create": "AGENTS_MULTI_AGENT_OPERATOR_REQUIRED",
    "agents.update": "AGENTS_MULTI_AGENT_OPERATOR_REQUIRED",
    "agents.delete": "AGENTS_MULTI_AGENT_OPERATOR_REQUIRED",
}
IMPLEMENTED = (
    "agents.workspace.list",
    "agents.workspace.get",
    "agents.files.list",
    "agents.files.get",
)


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _workspace(tmp_path, monkeypatch):
    from voicebridge import projects as PJ

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "ws"
    root.mkdir()
    (root / "notes.md").write_text("hello qubit");
    PJ.create(cfg, "ws", [str(root)], confirmed=True, use=True)
    return cfg, root


def _registry():
    from voicebridge.gateway import rpc as R
    return R.default_registry()


# ---------------------------------------------------------------- static proof
def test_registered_implementations_pass_manifest_behavior_proof():
    sources = OUM.registry_sources(REPO_ROOT / "voicebridge" / "gateway"
                                   / "rpc.py")
    methods, funcs, consts, origins = OUM._parse_rpc_handlers_multi(sources)
    registry = set(_registry().names())
    ours = {m for m, origin in origins.items()
            if origin.endswith("qubit_handlers/agents.py")}
    assert ours == set(IMPLEMENTED) | set(SAFE_EQUIVALENT) | set(OPERATOR_ONLY)
    assert ours <= registry, f"unregistered: {ours - registry}"
    for method in sorted(ours):
        node = methods[method]
        code = SAFE_EQUIVALENT.get(method) or OPERATOR_ONLY.get(method)
        if code:
            assert OUM._emits_code(node, funcs, code, consts), (
                f"{method} never emits {code!r}")
        else:
            assert OUM._is_non_trivial(node, funcs), (
                f"{method} does no backend work")


def test_manifest_dispositions_match_family():
    import json

    manifest = json.loads((REPO_ROOT / "docs" / "archive" / "2026-09-21"
                           / "QUBIT_OPENCLAW_UPGRADE_MANIFEST.json"
                           ).read_text())
    rows = manifest["pinned_surface_disposition"]["methods"]
    for method in IMPLEMENTED:
        assert rows[method]["status"] == "IMPLEMENTED", rows[method]
    for method, code in SAFE_EQUIVALENT.items():
        assert rows[method]["status"] == "SAFE_EQUIVALENT", rows[method]
        assert rows[method]["code"] == code, rows[method]
    for method, code in OPERATOR_ONLY.items():
        assert rows[method]["status"] == "OPERATOR_ONLY", rows[method]
        assert rows[method]["code"] == code, rows[method]


# --------------------------------------------------------------- runtime proof
def test_workspace_list_and_get_read_the_canonical_files(tmp_path, monkeypatch):
    cfg, _root = _workspace(tmp_path, monkeypatch)
    reg = _registry()

    listing = reg.handle(cfg, "agents.workspace.list",
                         {"agentId": "qubit-worker"})
    assert listing["state"] == "OK", listing
    assert listing["agentId"] == "qubit-worker"
    names = {e["name"] for e in listing["entries"]}
    assert "notes.md" in names, listing

    read = reg.handle(cfg, "agents.workspace.get",
                      {"agentId": "qubit-worker", "path": "notes.md"})
    assert read["state"] == "OK", read
    assert "hello qubit" in read["file"]["content"], read


def test_files_list_and_get_delegate_to_the_same_authority(tmp_path, monkeypatch):
    cfg, _root = _workspace(tmp_path, monkeypatch)
    reg = _registry()

    listing = reg.handle(cfg, "agents.files.list", {})
    assert listing["state"] == "OK", listing
    assert "notes.md" in {e["name"] for e in listing["entries"]}, listing

    read = reg.handle(cfg, "agents.files.get", {"path": "notes.md"})
    assert read["state"] == "OK", read
    assert "hello qubit" in read["file"]["content"], read


def test_unknown_agent_is_a_typed_error(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg, _root = _workspace(tmp_path, monkeypatch)
    reg = _registry()
    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "agents.workspace.list", {"agentId": "other-agent"})
    assert exc.value.code == "AGENT_NOT_FOUND", exc.value


def test_missing_path_and_escape_are_typed_errors(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg, _root = _workspace(tmp_path, monkeypatch)
    reg = _registry()

    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "agents.workspace.get", {"path": ""})
    assert exc.value.code == "GATEWAY_BAD_PARAMS", exc.value

    with pytest.raises(GatewayError) as exc2:
        reg.handle(cfg, "agents.workspace.get", {"path": "/etc/passwd"})
    assert exc2.value.code == "FILES_OUTSIDE_ROOT", exc2.value


def test_agent_wait_reports_canonical_task_state(tmp_path, monkeypatch):
    from voicebridge import kanban as K

    cfg, _root = _workspace(tmp_path, monkeypatch)
    reg = _registry()

    missing = reg.handle(cfg, "agent.wait", {"runId": "kanban:999999"})
    assert missing["state"] == "NOT_FOUND", missing

    task = K.create(cfg, "run the report", confirmed=True)["task"]
    out = reg.handle(cfg, "agent.wait", {"runId": f"kanban:{task['id']}"})
    assert out["state"] == "OK", out
    assert out["code"] == "AGENT_WAIT_LOCAL_TASK_STATE", out
    assert out["task"]["id"] == f"kanban:{task['id']}", out

    from voicebridge.gateway.gateway_ws import GatewayError

    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "agent.wait", {})
    assert exc.value.code == "GATEWAY_BAD_PARAMS", exc.value


def test_typed_refusals(tmp_path, monkeypatch):
    cfg, _root = _workspace(tmp_path, monkeypatch)
    reg = _registry()

    setout = reg.handle(cfg, "agents.files.set",
                        {"name": "AGENTS.md", "content": "x"})
    assert setout["state"] == "UNAVAILABLE", setout
    assert setout["code"] == "AGENTS_FILES_SET_BROKERED_WRITE", setout
    assert setout["qubit_counterpart"], setout

    for method in ("agents.create", "agents.update", "agents.delete"):
        out = reg.handle(cfg, method, {"name": "x"})
        assert out["state"] == "UNAVAILABLE", (method, out)
        assert out["code"] == "AGENTS_MULTI_AGENT_OPERATOR_REQUIRED", (method,
                                                                       out)
        assert out["operator_prerequisite"], (method, out)
