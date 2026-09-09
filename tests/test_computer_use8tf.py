"""Packet 8T-F — Hermes computer-use parity via capability probes.

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
