"""Agent 5 — OpenClaw row closures (gateway / runtime / pairing / RPC).

Covers the real capabilities that close these ledger rows through the
existing broker/permissions/audit path (no duplicate broker, scheduler,
listener, or authority):

* OC-08 setup-code / join-URL / QR encode + channel DM pairing.
* OC-04 gateway supervision policy path (approval/grant, real state
  transition, revert recovery, e-stop).
* OC-D09 new gateway RPC server methods dispatched from the
  transport-neutral ``FrameReader``.
* OC-D07 node runtime recovery + OC-D30 update rollback/repair/history.
* OC-D31 gateway discovery + runtime inventory/marketplace surface.

Every test drives a production path (CLI dispatcher, FrameReader request
path, or the shared runtime inventory) and includes negative/recovery
assertions.
"""
from __future__ import annotations

import asyncio
import io
import json
import stat
from contextlib import redirect_stdout
from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("QUBIT_OPENCODE_PIN_BIN", raising=False)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _acct(cfg, aid="team", provider="slack", ref="env:SLACK_TOKEN"):
    from voicebridge import messaging as MG

    return MG.account_add(cfg, aid, provider, aid, confirmed=True,
                          key_ref=ref)


class _FakeWS:
    def __init__(self, frames):
        self._frames = frames

    def __aiter__(self):
        async def _gen():
            for frame in self._frames:
                yield frame
        return _gen()


# ---------------------------------------------------------------- OC-08
def test_setup_code_join_url_qr_and_cli_accept(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge import pairing as PA

    cfg = _cfg(tmp_path, monkeypatch)
    out = PA.encode_setup(cfg, "phone", ["messaging"], account="team",
                          confirmed=True)
    assert out["join_url"].startswith("qubit://pair")
    assert out["qr"]["format"] == "url"
    assert out["code"] in out["qr"]["payload"]
    # The code is shown once and never persisted (stored hashed only).
    raw = json.loads((Path(cfg.state_dir()) / "pairing.json").read_text())
    assert out["code"] not in json.dumps(raw)

    # Production CLI caller: `pairing approve` accepts the join URL.
    class _A:
        pairing_command = "approve"
        code = out["join_url"]
        confirm = True
        dry_run = False

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_pairing(cfg, _A())
    assert rc == 0
    approved = json.loads(buf.getvalue())
    assert approved["state"] == "OK" and approved["account"] == "team"


def test_setup_accept_binds_dm_and_negative_paths(tmp_path, monkeypatch):
    from voicebridge import pairing as PA

    cfg = _cfg(tmp_path, monkeypatch)
    out = PA.encode_setup(cfg, "phone", ["messaging"], account="team",
                          confirmed=True)
    accepted = PA.accept(cfg, out["join_url"], confirmed=True)
    assert accepted["setup_accepted"] is True
    assert accepted["dm_pairing"] is True
    rows = PA.channel_dm_pairings(cfg)["pairings"]
    assert rows and rows[0]["account"] == "team"

    # Negative: confirm required, malformed setup payload, single-use reuse.
    with pytest.raises(PA.PairingError) as nc:
        PA.accept(cfg, "qubit://pair?foo=1", confirmed=False)
    assert nc.value.code == PA.CONFIRM_CODE
    with pytest.raises(PA.PairingError) as bad:
        PA.accept(cfg, "qubit://pair?foo=1", confirmed=True)
    assert bad.value.code == "PAIRING_BAD_SETUP"
    with pytest.raises(PA.PairingError) as reuse:
        PA.accept(cfg, out["join_url"], confirmed=True)
    assert reuse.value.code == "PAIRING_BAD_CODE"


# ---------------------------------------------------------------- OC-04
def test_gateway_supervision_requires_policy_then_real_transition(
        tmp_path, monkeypatch):
    from voicebridge import gateway_peers as GW

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("SLACK_TOKEN", "x")
    _acct(cfg)
    GW.gateway_add(cfg, "gw1", "slack", "team", "webhook",
                   key_ref="env:SLACK_TOKEN", confirmed=True)

    # Negative: confirmation and action validation.
    with pytest.raises(GW.GatewayError) as nc:
        GW.supervision_request(cfg, "gw1", "start", confirmed=False)
    assert nc.value.code == GW.CONFIRM_CODE
    with pytest.raises(GW.GatewayError) as bad:
        GW.supervision_request(cfg, "gw1", "explode", confirmed=True)
    assert bad.value.code == GW.BAD_INPUT_CODE

    # No grant/approval -> a real broker approval is created; nothing moves.
    pending = GW.supervision_request(cfg, "gw1", "start", confirmed=True)
    assert pending["state"] == "APPROVAL_REQUIRED"
    assert pending["code"] == GW.SUPERVISION_PENDING_CODE
    assert pending["approval_id"]
    assert GW.gateway_show(cfg, "gw1")["gateway"]["process"] == "UNMANAGED"

    # Authorize through the SAME permission store the broker uses.
    from voicebridge.permissions import permissions_store

    permissions_store(cfg).grant(GW.SUPERVISION_CAPABILITY,
                                 requested_by="test", confirmed=True,
                                 scopes=("gateway",))
    started = GW.supervision_request(cfg, "gw1", "start", confirmed=True)
    assert started["state"] == "OK"
    assert started["process"] == GW.SUPERVISED_PROCESS
    # Visible through the production CLI link path.
    assert GW.gateway_link(cfg, "gw1")["supervision"]["process"] == \
        GW.SUPERVISED_PROCESS

    # Recovery: revert restores the pre-supervision state.
    reverted = GW.supervision_revert(cfg, "gw1", confirmed=True)
    assert reverted["process"] == "UNMANAGED"
    assert GW.gateway_show(cfg, "gw1")["gateway"]["process"] == "UNMANAGED"

    # E-stop fails closed through the shared latch.
    monkeypatch.setattr(GW, "estop_engaged", lambda _c: True)
    with pytest.raises(GW.GatewayError) as es:
        GW.supervision_request(cfg, "gw1", "stop", confirmed=True)
    assert es.value.code == GW.ESTOP_CODE


def test_gateway_supervision_durable_approval_authorizes(tmp_path, monkeypatch):
    from pathlib import Path as _P

    from voicebridge import gateway_peers as GW
    from voicebridge.approvals import ApprovalJournal

    cfg = _cfg(tmp_path, monkeypatch)
    _acct(cfg)
    GW.gateway_add(cfg, "gw1", "slack", "team", "webhook",
                   key_ref="env:SLACK_TOKEN", confirmed=True)
    pending = GW.supervision_request(cfg, "gw1", "restart", confirmed=True)
    journal = ApprovalJournal(_P(cfg.state_dir()) / "approvals.jsonl")
    journal.respond(pending["approval_id"], "approve")
    journal.finish(pending["approval_id"])
    authorized = GW.supervision_request(
        cfg, "gw1", "restart", approval_id=pending["approval_id"],
        confirmed=True)
    assert authorized["state"] == "OK"
    assert authorized["process"] == GW.SUPERVISED_PROCESS


# ------------------------------------------------------------ OC-D09
def test_gateway_rpc_methods_dispatch_through_frame_reader(
        tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import FrameReader
    from voicebridge.gateway.rpc import default_registry

    cfg = _cfg(tmp_path, monkeypatch)
    responses = []
    reader = FrameReader(methods=default_registry(), cfg=cfg,
                         respond=responses.append)
    frames = [
        json.dumps({"type": "req", "id": "a", "method": "environments.prepare",
                    "params": {}}),
        json.dumps({"type": "req", "id": "b", "method": "task.history",
                    "params": {"limit": 5}}),
        json.dumps({"type": "req", "id": "c", "method": "task.suggestions",
                    "params": {}}),
        json.dumps({"type": "req", "id": "d", "method": "update.report",
                    "params": {}}),
        json.dumps({"type": "req", "id": "e", "method": "workspace.files",
                    "params": {"action": "inspect"}}),
        json.dumps({"type": "req", "id": "f", "method": "plugin.mutations",
                    "params": {"action": "status"}}),
        json.dumps({"type": "req", "id": "g", "method": "migrations",
                    "params": {}}),
        json.dumps({"type": "req", "id": "h", "method": "does.not.exist",
                    "params": {}}),
        json.dumps({"type": "req", "id": "i"}),   # misshapen -> dropped
    ]
    loop = asyncio.new_event_loop()
    loop.run_until_complete(reader.drain(_FakeWS(frames)))
    loop.close()
    by_id = {r["id"]: r for r in responses}
    for rid in ("a", "b", "c", "d", "e", "f", "g"):
        assert by_id[rid]["ok"] is True, (rid, by_id.get(rid))
        assert by_id[rid]["type"] == "res"
    assert by_id["h"]["ok"] is False
    assert by_id["h"]["error"]["code"] == "GATEWAY_METHOD_NOT_FOUND"
    assert "i" not in by_id
    assert reader.frame_guard.dropped >= 1


def test_environments_prepare_unknown_and_plugin_mutation_gate(
        tmp_path, monkeypatch):
    from voicebridge.gateway.rpc import default_registry, handle_request

    cfg = _cfg(tmp_path, monkeypatch)
    reg = default_registry()
    res = handle_request(reg, cfg, {"type": "req", "id": "1",
                                    "method": "environments.prepare",
                                    "params": {"name": "ghost"}})
    assert res["ok"] is True and res["payload"]["state"] == "UNKNOWN"

    # Plugin mutation without confirmation routes through the existing
    # plugin-runtime gate and surfaces as a typed RPC error.
    res = handle_request(reg, cfg, {"type": "req", "id": "2",
                                    "method": "plugin.mutations",
                                    "params": {"action": "register",
                                               "manifest": {"id": "x",
                                                            "kind": "tool"}}})
    assert res["ok"] is False
    assert res["error"]["code"]


# ------------------------------------------------------- OC-D07 / OC-D30
def _stage_fake_opencode(tmp_path, monkeypatch):
    from voicebridge.opencode import policy as POL

    fake = tmp_path / "opencode-stage"
    fake.write_text("#!/bin/sh\necho '" + POL.PINNED_OPENCODE_VERSION
                    + " (test)'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(POL, "DEFAULT_BINARY", str(fake))
    monkeypatch.setattr("shutil.which", lambda _n: None)
    return fake


def test_runtime_history_repair_and_recover(tmp_path, monkeypatch):
    from voicebridge import runtime_images as RI

    cfg = _cfg(tmp_path, monkeypatch)
    fake = _stage_fake_opencode(tmp_path, monkeypatch)

    out = RI.install(cfg, "opencode-binary", requested_by="t",
                     confirmed=True, source=str(fake))
    assert out["state"] == "OK"
    RI.upgrade(cfg, "opencode-binary", requested_by="t", confirmed=True,
               source=str(fake))
    events = [e["event"] for e in RI.update_history(cfg)["entries"]]
    assert "install" in events and "upgrade" in events

    # Recovery is idempotent when the verified artifact already matches.
    healthy = RI.recover(cfg, "opencode-binary", requested_by="t",
                         confirmed=True)
    assert healthy["state"] == "OK"
    assert healthy["outcome"] == "ALREADY_HEALTHY"

    # Bounded repair rebuilds a corrupt state file and reconciles records.
    path = RI._state_path(cfg)
    path.write_text("{ not json")
    repaired = RI.repair(cfg, requested_by="t", confirmed=True)
    assert repaired["state"] == "OK"
    assert any(f["check"] == "state_json" and f["result"].startswith("CORRUPT")
               for f in repaired["findings"])
    assert any(a["action"] == "state_backup" for a in repaired["actions"])
    assert path.is_file() and json.loads(path.read_text())["schema"] == RI.SCHEMA
    assert RI.update_report(cfg)["recovery"]["modes"] == \
        ["recover", "repair", "rollback"]


def test_runtime_recover_unrecoverable_is_typed(tmp_path, monkeypatch):
    from voicebridge import runtime_images as RI
    from voicebridge.opencode import state as OCS

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(OCS, "verify_owned_binary", lambda *a, **k: {"ok": False})
    monkeypatch.setattr(RI, "_staged_opencode_binary", lambda *a, **k: None)
    with pytest.raises(RI.RuntimeImageError) as exc:
        RI.recover(cfg, "opencode-binary", requested_by="t", confirmed=True)
    assert exc.value.code == RI.UNRECOVERABLE_CODE
    with pytest.raises(RI.RuntimeImageError) as nc:
        RI.recover(cfg, "opencode-binary", requested_by="t", confirmed=False)
    assert nc.value.code == RI.CONFIRM_CODE


# ------------------------------------------------------------ OC-D31
def test_gateway_discovery_and_runtime_inventory_surface(tmp_path, monkeypatch):
    from voicebridge import gateway_peers as GW
    from voicebridge import runtime_inventory as RINV

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("SLACK_TOKEN", "x")
    _acct(cfg)
    GW.gateway_add(cfg, "gw1", "slack", "team", "webhook",
                   key_ref="env:SLACK_TOKEN", confirmed=True)
    GW.peer_add(cfg, "p1", "http://127.0.0.1:9/api", "team",
                key_ref="env:SLACK_TOKEN", confirmed=True)

    disc = GW.discovery(cfg)
    assert disc["state"] == "OK"
    assert [g["id"] for g in disc["gateways"]] == ["gw1"]
    assert [p["id"] for p in disc["peers"]] == ["p1"]
    assert any(p["provider"] == "slack" for p in disc["providers"])
    assert "refused" in disc["marketplace"]

    inv = RINV.runtime_inventory(cfg)
    assert inv["discovery"]["state"] == "OK"
    assert inv["discovery"]["gateways"] == 1
    assert inv["marketplace"]["state"] == "OK"
    assert "refused" in inv["marketplace"]["marketplace"]
