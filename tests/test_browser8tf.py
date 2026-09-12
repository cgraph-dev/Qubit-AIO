"""Qubit browser surface via the scoped loopback probe.

Proves status is a read-only typed probe (never mutates a session) and
real-profile closure is refused with a typed error.
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


def test_status_typed_states(tmp_path, monkeypatch):
    from voicebridge import browser as BR

    for probe, state in (({"state": "OK", "cdp": True}, "OK"),
                         ({"state": "BROWSER_RESTART_REQUIRED", "cdp": False,
                           "detail": "restart it"}, "BROWSER_RESTART_REQUIRED"),
                         ({}, "UNAVAILABLE"),
                         ({"state": "BOGUS"}, "UNAVAILABLE")):
        out = BR.status(probe)
        assert out["schema"] == "qubit-browser/1"
        assert out["state"] == state, (probe, out)
        assert isinstance(out["cdp"], bool)
        assert "loopback" in out["authority"]
    assert "sk-" not in json.dumps(BR.status({"state": "OK"}))


def test_status_matches_live_precondition_shape(tmp_path, monkeypatch):
    from voicebridge.communications.monitor import cdp_precondition
    from voicebridge import browser as BR

    cfg = _cfg(tmp_path, monkeypatch)
    out = BR.status(cdp_precondition(cfg))
    assert out["state"] in ("OK", "BROWSER_RESTART_REQUIRED", "UNAVAILABLE")


def test_browser_module_respects_8a_import_boundary():
    text = (Path(__file__).resolve().parents[1] / "voicebridge" / "browser.py"
            ).read_text()
    assert "voicebridge.communications" not in text
    assert "from .communications" not in text


def test_status_spawns_no_browser_control(tmp_path, monkeypatch):
    from voicebridge import browser as BR

    def _no_spawn(*a, **k):
        raise AssertionError("rendering must not drive the browser")

    monkeypatch.setattr(subprocess, "run", _no_spawn)
    monkeypatch.setattr(subprocess, "Popen", _no_spawn)
    out = BR.status({"state": "OK", "cdp": True})
    assert out["state"] == "OK"


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


# --------------------------------------------- OC-10: profile lifecycle
def test_managed_profile_lifecycle(tmp_path, monkeypatch):
    from voicebridge import browser as BR

    cfg = _cfg(tmp_path, monkeypatch)
    assert BR.profiles(cfg)["profiles"] == []
    assert not BR._registry_path(cfg).exists()

    # Confirmation is mandatory; a missing confirm never creates state.
    denied = BR.create_profile(cfg, "research")
    assert denied["state"] == "denied" and denied["code"] == BR.CONFIRM_CODE
    assert not BR._registry_path(cfg).exists()

    # Reserved user-profile names and malformed names are refused.
    assert BR.create_profile(cfg, "default",
                             confirmed=True)["code"] == BR.PROFILE_BAD_INPUT_CODE
    assert BR.create_profile(cfg, "Bad Name",
                             confirmed=True)["code"] == BR.PROFILE_BAD_INPUT_CODE

    created = BR.create_profile(cfg, "research", cdp_port=9310, confirmed=True)
    assert created["state"] == "OK"
    assert created["profile"]["managed"] is True
    assert created["profile"]["state"] == "stopped"
    assert created["profile"]["cdp_port"] == 9310
    assert (BR._profiles_root(cfg) / "research").is_dir()
    # Duplicate is typed, never a silent second record.
    assert BR.create_profile(cfg, "research",
                             confirmed=True)["code"] == BR.PROFILE_EXISTS_CODE
    # Port must stay in the dedicated loopback range.
    assert BR.create_profile(cfg, "work", cdp_port=80,
                             confirmed=True)["code"] == BR.PROFILE_BAD_INPUT_CODE

    listed = BR.profiles(cfg)
    assert [p["name"] for p in listed["profiles"]] == ["research"]
    assert "personal profiles are never" in listed["authority"]

    # Launch requires an injected approved launcher (no browser is spawned).
    assert BR.open_profile(cfg, "research",
                           confirmed=True)["code"] == BR.PROFILE_NO_LAUNCHER_CODE
    seen = {}

    def _fake_launcher(directory, port):
        seen["dir"] = directory
        seen["port"] = port
        return {"pid": 4242}

    opened = BR.open_profile(cfg, "research", launcher=_fake_launcher,
                             confirmed=True)
    assert opened["state"] == "OK"
    assert opened["profile"]["state"] == "running"
    assert opened["profile"]["pid"] == 4242
    assert seen["port"] == 9310
    assert str(BR._profiles_root(cfg)) in seen["dir"]

    # Closing a QUBIT-managed profile is a real transition (not a refusal).
    stopped = []
    closed = BR.close_profile(cfg, "research",
                              stopper=lambda entry: stopped.append(entry),
                              confirmed=True)
    assert closed["state"] == "OK" and closed["closed"] is True
    assert closed["profile"]["state"] == "stopped"
    assert stopped and stopped[0]["pid"] == 4242

    # Removing the managed profile deletes only its Qubit-owned directory.
    removed = BR.remove_profile(cfg, "research", confirmed=True)
    assert removed["removed"] is True and removed["dir_removed"] is True
    assert not (BR._profiles_root(cfg) / "research").exists()
    assert BR._load_registry(cfg)["profiles"] == {}


def test_close_profile_refuses_real_user_profiles(tmp_path, monkeypatch):
    from voicebridge import browser as BR

    cfg = _cfg(tmp_path, monkeypatch)
    for name in ("Default", "default", "user", "personal", "work",
                 "chromium"):
        out = BR.close_profile(cfg, name, confirmed=True)
        assert out["state"] == "REFUSED", name
        assert out["code"] == BR.CLOSE_CODE, name
        assert out["owner"] == "qubit-policy"
    # A refusal never creates a registry or a profile directory.
    assert not BR._registry_path(cfg).exists()


def test_profile_lifecycle_confirmation_and_estop(tmp_path, monkeypatch):
    from voicebridge import browser as BR

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(BR, "_estop", lambda _cfg: True)
    assert BR.create_profile(cfg, "research",
                             confirmed=True)["code"] == BR.ESTOP_CODE
    monkeypatch.setattr(BR, "_estop", lambda _cfg: False)
    BR.create_profile(cfg, "research", confirmed=True)
    # Confirmation gates open/close/remove (no launcher/state change yet).
    assert BR.open_profile(cfg, "research")["code"] == BR.CONFIRM_CODE
    assert BR.close_profile(cfg, "research")["code"] == BR.CONFIRM_CODE
    assert BR.remove_profile(cfg, "research")["code"] == BR.CONFIRM_CODE
    monkeypatch.setattr(BR, "_estop", lambda _cfg: True)
    assert BR.open_profile(cfg, "research",
                           confirmed=True)["code"] == BR.ESTOP_CODE
    assert BR.close_profile(cfg, "research",
                            confirmed=True)["code"] == BR.ESTOP_CODE
    assert BR.remove_profile(cfg, "research",
                             confirmed=True)["code"] == BR.ESTOP_CODE
