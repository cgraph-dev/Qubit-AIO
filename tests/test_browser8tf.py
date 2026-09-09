"""Packet 8T-F — Hermes browser parity via the scoped loopback probe.

Proves status is a read-only typed probe (never mutates a session) and
real-profile closure is refused with a typed error.
"""
from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stdout


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_status_typed_states(tmp_path, monkeypatch):
    from voicebridge import browser as BR

    out = BR.status(_cfg(tmp_path, monkeypatch))
    assert out["schema"] == "qubit-browser/1"
    assert out["state"] in ("OK", "BROWSER_RESTART_REQUIRED", "UNAVAILABLE")
    assert isinstance(out["cdp"], bool)
    assert "loopback" in out["authority"]
    assert "sk-" not in json.dumps(out)


def test_status_spawns_no_browser_control(tmp_path, monkeypatch):
    from voicebridge import browser as BR

    def _no_spawn(*a, **k):
        raise AssertionError("status must not drive the browser")

    monkeypatch.setattr(subprocess, "run", _no_spawn)
    monkeypatch.setattr(subprocess, "Popen", _no_spawn)
    out = BR.status(_cfg(tmp_path, monkeypatch))
    assert out["state"] in ("OK", "BROWSER_RESTART_REQUIRED", "UNAVAILABLE")


def test_close_profile_refused(tmp_path, monkeypatch):
    from voicebridge import browser as BR

    out = BR.refuse_close_profile("Default")
    assert out["state"] == "REFUSED"
    assert out["code"] == "BROWSER_CLOSE_REFUSED"
    assert out["owner"] == "qubit-policy"


def test_close_profile_redacts_name(tmp_path, monkeypatch):
    from voicebridge import browser as BR

    out = BR.refuse_close_profile("x token=abc123secret")
    assert "abc123secret" not in json.dumps(out)


def test_cli_wiring(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    cfg = _cfg(tmp_path, monkeypatch)

    class _A:
        def __init__(self, cmd, profile="default"):
            self.browser_command = cmd
            self.profile = profile

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_browser(cfg, _A("status")) == 0
    assert json.loads(buf.getvalue())["schema"] == "qubit-browser/1"
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_browser(cfg, _A("close-profile")) == 2
    assert json.loads(buf.getvalue())["code"] == "BROWSER_CLOSE_REFUSED"
    assert "browser" in CLI.build_parser().format_help()


def test_capability_catalog_reports_browser_implemented():
    from voicebridge.capability_catalog import check

    out = check("browser")
    assert out["state"] == "IMPLEMENTED", out
    assert out["code"] == "OK"
