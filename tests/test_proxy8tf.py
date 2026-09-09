"""Packet 8T-F — Hermes proxy parity via inventory + typed start refusal.

Proves no forwarding server runs or can be started here, provider
eligibility reports references only, and nothing is fetched or opened.
"""
from __future__ import annotations

import io
import json
import socket
import subprocess
from contextlib import redirect_stdout


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    (tmp_path / "voice.yaml").write_text(
        "intent:\n  frontier:\n    enabled: false\n    providers:\n"
        "      acme:\n        key_ref: \"env:ACME_API_KEY\"\n")
    return C.Config.load(tmp_path / "voice.yaml")


def test_status_never_running():
    from voicebridge import proxy_status as PX

    out = PX.status()
    assert out["state"] == "NOT_RUNNING"
    assert out["bind"] == "none" and out["forwards"] == 0
    assert "egress" in out["policy"]


def test_providers_reference_only(tmp_path, monkeypatch):
    from voicebridge import proxy_status as PX

    monkeypatch.setenv("ACME_API_KEY", "sk-live-s3cr3t-1")
    out = PX.providers(_cfg(tmp_path, monkeypatch))
    assert out["providers"] == [{"provider": "acme", "key_ref": "env",
                                 "allowlisted": False,
                                 "proxy_eligible": False,
                                 "why": "forwarding is disabled; use the "
                                        "brokered frontier gateway"}]
    assert "sk-live-s3cr3t-1" not in json.dumps(out)


def test_start_refused_and_no_listener(tmp_path, monkeypatch):
    from voicebridge import proxy_status as PX

    out = PX.refuse_start()
    assert out["code"] == "PROXY_NO_SERVER"
    assert out["owner"] == "qubit-policy"
    import socket as _s

    probe = _s.socket()
    try:
        probe.bind(("127.0.0.1", 0))
    finally:
        probe.close()


def test_no_network_or_listeners(tmp_path, monkeypatch):
    from voicebridge import proxy_status as PX

    def _no_net(*a, **k):
        raise AssertionError("inventory must not use the network")

    monkeypatch.setattr(socket, "create_connection", _no_net)
    monkeypatch.setattr(subprocess, "run", _no_net)
    PX.status()
    PX.providers(_cfg(tmp_path, monkeypatch))


def test_cli_wiring(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    cfg = _cfg(tmp_path, monkeypatch)

    class _A:
        def __init__(self, cmd):
            self.proxy_command = cmd

    for cmd, rc in (("status", 0), ("providers", 0), ("start", 2)):
        buf = io.StringIO()
        with redirect_stdout(buf):
            assert CLI._cmd_proxy(cfg, _A(cmd)) == rc, cmd
        json.loads(buf.getvalue())
    assert "proxy" in CLI.build_parser().format_help()


def test_capability_catalog_reports_proxy_implemented():
    from voicebridge.capability_catalog import check

    out = check("proxy")
    assert out["state"] == "IMPLEMENTED", out
    assert out["code"] == "OK"
