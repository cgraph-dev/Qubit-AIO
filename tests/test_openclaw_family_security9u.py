"""9U — OpenClaw approvals/devices/attach security family.

Static behavior proof + runtime proof against the canonical authorities:
the shared ``ApprovalJournal`` and the device-scoped token store.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import openclaw_upgrade_manifest as OUM  # noqa: E402

FAMILY_MODULE = "voicebridge/gateway/openclaw_handlers/security.py"

SAFE_EQUIVALENT = {
    "exec.approvals.set": "EXEC_APPROVALS_LOCAL_JOURNAL",
    "exec.approvals.node.get": "EXEC_APPROVALS_NODE_LOCAL_POLICY",
}
OPERATOR_ONLY = {
    "exec.approvals.node.set": "EXEC_APPROVALS_NODE_OPERATOR_REQUIRED",
    "attach.grant": "ATTACH_MCP_LOOPBACK_OPERATOR_REQUIRED",
    "attach.revoke": "ATTACH_MCP_LOOPBACK_OPERATOR_REQUIRED",
}
IMPLEMENTED = (
    "approval.history",
    "device.pair.rename",
    "device.scopes.requestUpgrade",
    "device.scopes.waitUpgrade",
)


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


# ---------------------------------------------------------------- static proof
def test_registered_implementations_pass_manifest_behavior_proof():
    sources = OUM.registry_sources(REPO_ROOT / "voicebridge" / "gateway"
                                   / "rpc.py")
    methods, funcs, consts, origins = OUM._parse_rpc_handlers_multi(sources)
    registry = set(_registry().names())
    ours = {m for m, origin in origins.items()
            if origin.endswith("openclaw_handlers/security.py")}
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
def test_approval_history_lists_terminal_only(tmp_path, monkeypatch):
    from pathlib import Path

    from voicebridge.approvals import ApprovalJournal

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    journal = ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                              default_ttl_s=120.0)
    pending = journal.create("shell.exec", "run ls")
    approved = journal.create("shell.exec", "run pwd")
    journal.respond(approved.id, "approve")
    journal.finish(approved.id)
    denied = journal.create("file.write", "write x")
    journal.respond(denied.id, "deny")

    out = reg.handle(cfg, "approval.history", {"limit": 50})
    assert out["state"] == "OK", out
    ids = {i["id"] for i in out["items"]}
    assert pending.id not in ids, "pending approvals must not appear in history"
    assert approved.id in ids and denied.id in ids
    statuses = {i["id"]: i["status"] for i in out["items"]}
    assert statuses[approved.id] == "complete"
    assert statuses[denied.id] == "denied"

    filtered = reg.handle(cfg, "approval.history", {"kind": "shell.exec"})
    assert {i["id"] for i in filtered["items"]} == {approved.id}, filtered


def test_device_pair_rename(tmp_path, monkeypatch):
    from voicebridge import device as DEV

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    issued = DEV.token_issue(cfg, device_id="dev-1", scopes=["read"],
                             label="old", requested_by="t", confirmed=True)
    assert issued["state"] == "OK", issued
    tid = issued["token_id"]

    staged = reg.handle(cfg, "device.pair.rename",
                        {"deviceId": "dev-1", "label": "new"})
    assert staged["state"] == "denied", staged

    out = reg.handle(cfg, "device.pair.rename",
                     {"deviceId": "dev-1", "label": "new", "confirm": True})
    assert out["state"] == "OK", out
    assert out["token"]["label"] == "new"
    assert DEV.token_show(cfg, tid)["token"]["label"] == "new"

    unknown = reg.handle(cfg, "device.pair.rename",
                         {"deviceId": "nope", "label": "x", "confirm": True})
    assert unknown["state"] == "NOT_FOUND", unknown


def test_device_scope_upgrade_request_and_wait(tmp_path, monkeypatch):
    from voicebridge import device as DEV

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    staged = reg.handle(cfg, "device.scopes.requestUpgrade",
                        {"deviceId": "dev-2", "scopes": ["read", "write"]})
    assert staged["state"] == "denied", staged

    req = reg.handle(cfg, "device.scopes.requestUpgrade",
                     {"deviceId": "dev-2", "scopes": ["read", "write"],
                      "confirm": True})
    assert req["state"] == "OK" and req["status"] == "pending", req
    rid = req["requestId"]

    wait = reg.handle(cfg, "device.scopes.waitUpgrade", {"requestId": rid})
    assert wait["state"] == "OK" and wait["status"] == "pending", wait

    DEV.token_approve(cfg, rid, requested_by="t", confirmed=True)
    wait2 = reg.handle(cfg, "device.scopes.waitUpgrade", {"requestId": rid})
    assert wait2["status"] == "active", wait2
    assert set(wait2["scopes"]) == {"read", "write"}


def test_typed_refusal_family(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    setout = reg.handle(cfg, "exec.approvals.set", {})
    assert setout["code"] == "EXEC_APPROVALS_LOCAL_JOURNAL", setout

    nodeget = reg.handle(cfg, "exec.approvals.node.get", {})
    assert nodeget["code"] == "EXEC_APPROVALS_NODE_LOCAL_POLICY", nodeget
    assert nodeget["state"] == "OK"

    nodeset = reg.handle(cfg, "exec.approvals.node.set", {})
    assert nodeset["code"] == "EXEC_APPROVALS_NODE_OPERATOR_REQUIRED"

    grant = reg.handle(cfg, "attach.grant", {})
    assert grant["code"] == "ATTACH_MCP_LOOPBACK_OPERATOR_REQUIRED"
    revoke = reg.handle(cfg, "attach.revoke", {})
    assert revoke["code"] == "ATTACH_MCP_LOOPBACK_OPERATOR_REQUIRED"


def test_estop_and_invalid_input(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    with pytest.raises(GatewayError):
        reg.handle(cfg, "device.pair.rename", {"confirm": True})

    monkeypatch.setattr("voicebridge.broker.estop_engaged",
                        lambda cfg=None: True)
    out = reg.handle(cfg, "device.pair.rename",
                     {"deviceId": "d", "label": "x", "confirm": True})
    assert out["state"] == "UNAVAILABLE"
    assert out["code"] == "EMERGENCY_STOP"
