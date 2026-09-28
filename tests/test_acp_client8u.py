"""Packet 8U — ACP external harness client (OpenClaw audit #58).

Proves a real harness registry/contract: PATH-only presence detection, typed
ACP_HARNESS_UNAVAILABLE/ACP_REFUSED gates, real request shaping, receipts,
redaction, and estop fail-closed. Hermetic: tmp XDG state, no execution.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import urllib.request
from pathlib import Path

import pytest

HARNESSES = ("claude-code", "cursor", "gemini", "codex", "droid", "opencode")


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _absent(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)


def _present(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")


def _no_exec(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("ACP client must never execute a harness")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def test_catalog_and_status_honest(tmp_path, monkeypatch):
    from voicebridge import acp_client as AC

    cfg = _cfg(tmp_path, monkeypatch)
    _absent(monkeypatch)
    cat = AC.catalog(cfg)
    assert {r["harness"] for r in cat["harnesses"]} == set(HARNESSES)
    assert cat["available"] == [] and cat["connected"] == []
    assert cat["live_sessions"] == 0
    for row in cat["harnesses"]:
        assert row["present"] is False
        assert row["state"] == "UNAVAILABLE"
        assert row["code"] == AC.HARNESS_UNAVAILABLE_CODE
    st = AC.status(cfg)
    assert st["state"] == "OK" and st["live_sessions"] == 0
    assert st["configured"] == 0


def test_present_but_unconfigured_stays_unavailable(tmp_path, monkeypatch):
    from voicebridge import acp_client as AC

    cfg = _cfg(tmp_path, monkeypatch)
    _present(monkeypatch)
    row = next(r for r in AC.catalog(cfg)["harnesses"]
               if r["harness"] == "claude-code")
    assert row["present"] is True
    assert row["state"] == "UNAVAILABLE"
    assert row["code"] == AC.HARNESS_UNAVAILABLE_CODE


def test_configure_reference_only_and_redaction(tmp_path, monkeypatch):
    from voicebridge import acp_client as AC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("ACP_API_KEY8U", "sk-acp-secret-value-8u")
    with pytest.raises(AC.AcpError) as excinfo:
        AC.configure(cfg, "claude-code",
                     credential_ref="env:ACP_API_KEY8U")
    assert excinfo.value.code == AC.CONFIRM_CODE
    assert not (Path(cfg.state_dir()) / AC.STORE_NAME).exists()
    out = AC.configure(cfg, "claude-code",
                       credential_ref="env:ACP_API_KEY8U", confirmed=True)
    blob = json.dumps(out) + json.dumps(AC.doctor(cfg))
    assert "sk-acp-secret-value-8u" not in blob
    assert "ACP_API_KEY8U" not in blob
    with pytest.raises(AC.AcpError) as excinfo:
        AC.configure(cfg, "gemini", credential_ref="raw-secret",
                     confirmed=True)
    assert excinfo.value.code == AC.BAD_INPUT_CODE
    with pytest.raises(AC.AcpError) as excinfo:
        AC.configure(cfg, "not-a-harness", confirmed=True)
    assert excinfo.value.code == AC.UNKNOWN_HARNESS_CODE


def test_handshake_gates_absent_and_unconfigured(tmp_path, monkeypatch):
    from voicebridge import acp_client as AC

    cfg = _cfg(tmp_path, monkeypatch)
    _no_exec(monkeypatch)
    _absent(monkeypatch)
    with pytest.raises(AC.AcpError) as excinfo:
        AC.handshake(cfg, "claude-code")
    assert excinfo.value.code == AC.CONFIRM_CODE
    with pytest.raises(AC.AcpError) as excinfo:
        AC.handshake(cfg, "ghost", confirmed=True)
    assert excinfo.value.code == AC.UNKNOWN_HARNESS_CODE
    with pytest.raises(AC.AcpError) as excinfo:
        AC.handshake(cfg, "claude-code", confirmed=True)
    assert excinfo.value.code == AC.HARNESS_UNAVAILABLE_CODE
    _present(monkeypatch)
    with pytest.raises(AC.AcpError) as excinfo:
        AC.handshake(cfg, "claude-code", confirmed=True)
    assert excinfo.value.code == AC.HARNESS_UNAVAILABLE_CODE


def test_handshake_shapes_real_request_no_exec(tmp_path, monkeypatch):
    from voicebridge import acp_client as AC

    cfg = _cfg(tmp_path, monkeypatch)
    _no_exec(monkeypatch)
    _present(monkeypatch)
    monkeypatch.setenv("ACP_API_KEY8U", "sk-acp-secret-value-8u")
    AC.configure(cfg, "claude-code", credential_ref="env:ACP_API_KEY8U",
                 confirmed=True)
    out = AC.handshake(cfg, "claude-code", confirmed=True)
    assert out["state"] == "READY"
    assert out["executed"] is False
    assert out["binary"] == "/usr/bin/claude"
    req = out["request"]
    assert req["method"] == "initialize"
    assert req["jsonrpc"] == "2.0"
    assert req["params"]["clientInfo"]["name"] == "qubit"
    receipts = AC.receipts(cfg)["receipts"]
    assert any(r["verdict"] == "HANDSHAKE_SHAPED" for r in receipts)
    assert "sk-acp-secret-value-8u" not in json.dumps(receipts)


def test_send_always_refused(tmp_path, monkeypatch):
    from voicebridge import acp_client as AC

    cfg = _cfg(tmp_path, monkeypatch)
    _no_exec(monkeypatch)
    _present(monkeypatch)
    with pytest.raises(AC.AcpError) as excinfo:
        AC.send(cfg, "gemini", prompt="hello")
    assert excinfo.value.code == AC.CONFIRM_CODE
    with pytest.raises(AC.AcpError) as excinfo:
        AC.send(cfg, "ghost", prompt="hello", confirmed=True)
    assert excinfo.value.code == AC.UNKNOWN_HARNESS_CODE
    with pytest.raises(AC.AcpError) as excinfo:
        AC.send(cfg, "gemini", prompt="hello", confirmed=True)
    assert excinfo.value.code == AC.REFUSED_CODE
    with pytest.raises(AC.AcpError) as excinfo:
        AC.send(cfg, "gemini", prompt="   ", confirmed=True)
    assert excinfo.value.code == AC.BAD_INPUT_CODE
    with pytest.raises(AC.AcpError) as excinfo:
        AC.send(cfg, "gemini", prompt="x" * (AC.MAX_PROMPT_CHARS + 1),
                confirmed=True)
    assert excinfo.value.code == AC.BAD_INPUT_CODE
    monkeypatch.setenv("ACP_API_KEY8U", "sk-acp-secret-value-8u")
    AC.configure(cfg, "gemini", credential_ref="env:ACP_API_KEY8U",
                 confirmed=True)
    with pytest.raises(AC.AcpError) as excinfo:
        AC.send(cfg, "gemini", prompt="hello", confirmed=True)
    assert excinfo.value.code == AC.REFUSED_CODE


def test_send_refuses_even_unconfigured_and_keeps_no_store(tmp_path,
                                                           monkeypatch):
    from voicebridge import acp_client as AC

    cfg = _cfg(tmp_path, monkeypatch)
    _absent(monkeypatch)
    with pytest.raises(AC.AcpError) as excinfo:
        AC.send(cfg, "codex", prompt="hello", confirmed=True)
    assert excinfo.value.code == AC.REFUSED_CODE
    assert not (Path(cfg.state_dir()) / AC.STORE_NAME).exists()


def test_redaction_no_secret_in_receipts(tmp_path, monkeypatch):
    from voicebridge import acp_client as AC
    from voicebridge.logging_setup import setup

    cfg = _cfg(tmp_path, monkeypatch)
    _present(monkeypatch)
    secret = "sk-acp-prompt-secret-1234567890"
    setup(level="INFO", jsonl=True, state_dir=cfg.state_dir())
    try:
        AC.configure(cfg, "codex", credential_ref="env:ACP_API_KEY8U",
                     confirmed=True)
        with pytest.raises(AC.AcpError):
            AC.send(cfg, "codex", prompt=f"token={secret}", confirmed=True)
        blob = (Path(cfg.state_dir()) / "events.jsonl").read_text()
        assert secret not in blob
        assert secret not in json.dumps(AC.receipts(cfg))
    finally:
        from voicebridge import logging_setup as LS

        LS._EVENT_FILE = None


def test_estop_fail_closed(tmp_path, monkeypatch):
    from voicebridge import acp_client as AC
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    _present(monkeypatch)
    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        assert AC.status(cfg)["estop"] is True
        assert AC.refuse_estop("handshake")["code"] == AC.ESTOP_CODE
        with pytest.raises(AC.AcpError) as excinfo:
            AC.configure(cfg, "codex", confirmed=True)
        assert excinfo.value.code == AC.ESTOP_CODE
        with pytest.raises(AC.AcpError) as excinfo:
            AC.handshake(cfg, "codex", confirmed=True)
        assert excinfo.value.code == AC.ESTOP_CODE
        with pytest.raises(AC.AcpError) as excinfo:
            AC.send(cfg, "codex", prompt="hi", confirmed=True)
        assert excinfo.value.code == AC.ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")


def test_unconfigure_and_not_found(tmp_path, monkeypatch):
    from voicebridge import acp_client as AC

    cfg = _cfg(tmp_path, monkeypatch)
    _present(monkeypatch)
    AC.configure(cfg, "codex", confirmed=True)
    with pytest.raises(AC.AcpError) as excinfo:
        AC.configure(cfg, "codex", confirmed=True)
    assert excinfo.value.code == AC.EXISTS_CODE
    assert AC.unconfigure(cfg, "codex", confirmed=True)["removed"] == "codex"
    with pytest.raises(AC.AcpError) as excinfo:
        AC.unconfigure(cfg, "codex", confirmed=True)
    assert excinfo.value.code == AC.NOT_FOUND_CODE


def test_doctor_reports_health(tmp_path, monkeypatch):
    from voicebridge import acp_client as AC

    cfg = _cfg(tmp_path, monkeypatch)
    _absent(monkeypatch)
    out = AC.doctor(cfg)
    assert out["state"] == "OK" and out["store_ok"] is True
    assert out["external_runtime"] == AC.HARNESS_UNAVAILABLE_CODE
    assert out["live_sessions"] == 0
