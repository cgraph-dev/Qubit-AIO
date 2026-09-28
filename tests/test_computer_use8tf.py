"""Qubit desktop-control capability probes.

Proves control stays DISABLED with typed states, driver installs are
refused (pinned-source policy), and probes spawn nothing and touch no
session.
"""
from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stdout


def test_status_shape_and_policy():
    from voicebridge import computer_use as CU

    out = CU.status()
    assert out["schema"] == "qubit-computer-use/1"
    assert out["control"] == "DISABLED"
    assert isinstance(out["display_present"], bool)
    assert isinstance(out["atspi_reachable"], bool)
    assert set(out["driver"]) == {"present", "path"}
    assert "8V" in out["control_policy"]
    assert "sk-" not in json.dumps(out)


def test_doctor_checklist_typed():
    from voicebridge import computer_use as CU

    out = CU.doctor()
    assert out["schema"] == "qubit-computer-use-doctor/1"
    assert len(out["checks"]) == 4
    for check in out["checks"]:
        assert check["state"] in ("OK", "DEGRADED", "MISSING"), check
        assert check["guidance"], check
    assert out["state"] in ("OK", "DEGRADED")


def test_permissions_record():
    from voicebridge import computer_use as CU

    out = CU.permissions()
    assert out["control"] == "DISABLED" and out["owner"] == "packet-8V"
    assert any("never downloaded" in r for r in out["rules"])


def test_install_refused():
    from voicebridge import computer_use as CU

    out = CU.refuse_install()
    assert out["state"] == "REJECTED"
    assert out["code"] == "COMPUTER_USE_NO_DRIVER"
    assert out["owner"] == "packet-8V"


def test_probes_spawn_nothing_and_touch_nothing(tmp_path, monkeypatch):
    from voicebridge import computer_use as CU

    def _no_spawn(*a, **k):
        raise AssertionError("probes must not spawn processes")

    monkeypatch.setattr(subprocess, "run", _no_spawn)
    monkeypatch.setattr(subprocess, "Popen", _no_spawn)
    before = {p.name for p in tmp_path.iterdir()} if tmp_path.is_dir() else set()
    CU.status()
    CU.doctor()
    assert {p.name for p in tmp_path.iterdir()} == before


def test_cli_wiring():
    from voicebridge import cli as CLI

    class _A:
        def __init__(self, cmd):
            self.computer_use_command = cmd

    for cmd, rc in (("status", 0), ("doctor", 0), ("permissions", 0),
                    ("install", 2)):
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert CLI._cmd_computer_use(None, _A(cmd)) == rc, cmd
        json.loads(buf.getvalue())
    assert "computer-use" in CLI.build_parser().format_help()


def test_capability_catalog_reports_computer_use_implemented():
    from voicebridge.capability_catalog import check

    out = check("computer-use")
    assert out["state"] == "IMPLEMENTED", out
    assert out["code"] == "OK"


# --------------------------------------------- OC-15: `screen` arrange tool
def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_screen_panes_catalog(tmp_path, monkeypatch):
    from voicebridge import computer_use as CU

    cfg = _cfg(tmp_path, monkeypatch)
    out = CU.screen_panes()
    assert out["schema"] == CU.SCREEN_SCHEMA
    assert set(out["panes"]) == set(CU.SCREEN_PANES)
    assert set(out["layouts"]) == set(CU.SCREEN_LAYOUTS)
    assert "never a window manager" in out["detail"]
    assert CU.screen_status(cfg)["arranged"] is False


def test_screen_arrange_persists_and_validates(tmp_path, monkeypatch):
    from voicebridge import computer_use as CU

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CU, "_estop", lambda _cfg: False)

    # Confirmation is mandatory.
    assert CU.screen(cfg, ["chat"])["code"] == CU.SCREEN_CONFIRM_CODE
    # Unknown pane, duplicates, empty, and over-budget arrangements fail closed.
    assert CU.screen(cfg, ["chat", "nope"],
                     confirmed=True)["code"] == CU.SCREEN_BAD_INPUT_CODE
    assert CU.screen(cfg, ["chat", "chat"],
                     confirmed=True)["code"] == CU.SCREEN_BAD_INPUT_CODE
    assert CU.screen(cfg, [],
                     confirmed=True)["code"] == CU.SCREEN_BAD_INPUT_CODE
    too_many = list(CU.SCREEN_PANES[:CU.MAX_SCREEN_PANES + 1])
    assert CU.screen(cfg, too_many,
                     confirmed=True)["code"] == CU.SCREEN_BAD_INPUT_CODE
    assert CU.screen(cfg, ["chat"], layout="spiral",
                     confirmed=True)["code"] == CU.SCREEN_BAD_INPUT_CODE

    out = CU.screen(cfg, "chat,files", layout="columns", confirmed=True)
    assert out["state"] == "OK" and out["panes"] == ["chat", "files"]
    assert "no desktop" in out["authority"]
    status = CU.screen_status(cfg)
    assert status["arranged"] is True
    assert status["panes"] == ["chat", "files"] and status["layout"] == "columns"


def test_screen_estop_latches(tmp_path, monkeypatch):
    from voicebridge import computer_use as CU

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CU, "_estop", lambda _cfg: True)
    out = CU.screen(cfg, ["chat"], confirmed=True)
    assert out["state"] == "ESTOP" and out["code"] == CU.SCREEN_ESTOP_CODE
    assert not CU.screen_status(cfg)["arranged"]
