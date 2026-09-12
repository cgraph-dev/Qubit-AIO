"""Agent 8 — ACP harness adapters + LSP lifecycle + named refusals.

OC-58 no-credential parts: a real per-harness adapter contract and a
transport-injected ACP session loop (Qubit never spawns a harness). Also proves
the ledger-named refusals (``acp_serve_refusal`` / ``acp_accept_hooks_refusal``)
and the LSP lifecycle surface. Hermetic: synthetic in-process transport.
"""
from __future__ import annotations

import subprocess

import pytest


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
        raise AssertionError("ACP adapter must never spawn a harness")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)


def test_adapter_descriptors_and_shaping():
    from voicebridge import acp_client as AC

    out = AC.adapters()
    assert {a["harness"] for a in out["adapters"]} == set(AC.HARNESSES)
    desc = AC.adapter_descriptor("claude-code")["adapter"]
    assert desc["protocol"] == "acp-jsonrpc/1"
    assert desc["methods"] == list(AC.ADAPTER_METHODS)
    assert "never spawns" in desc["transport"]
    assert AC.shape_method("codex", "initialize")["request"]["method"] == \
        "initialize"
    prompt = AC.shape_method("codex", "session/prompt",
                             {"prompt": "hi"})["request"]
    assert prompt["method"] == "session/prompt"
    with pytest.raises(AC.AcpError) as e:
        AC.shape_method("codex", "shell/exec")
    assert e.value.code == AC.BAD_INPUT_CODE
    with pytest.raises(AC.AcpError) as e:
        AC.adapter_descriptor("ghost")
    assert e.value.code == AC.UNKNOWN_HARNESS_CODE


def test_adapter_session_injected_transport(tmp_path, monkeypatch):
    from voicebridge import acp_client as AC

    cfg = _cfg(tmp_path, monkeypatch)
    _no_spawn(monkeypatch)
    seen = []

    def transport(request):
        seen.append(request["method"])
        if request["method"] == "initialize":
            return {"jsonrpc": "2.0", "id": 1,
                    "result": {"protocolVersion": 1}}
        return {"jsonrpc": "2.0", "id": 2,
                "result": {"content": [{"type": "text", "text": "ok"}]}}

    with pytest.raises(AC.AcpError) as e:
        AC.adapter_session(cfg, "claude-code", prompt="hi", transport=transport)
    assert e.value.code == AC.CONFIRM_CODE
    out = AC.adapter_session(cfg, "claude-code", prompt="hi",
                             transport=transport, confirmed=True)
    assert out["state"] == "OK"
    assert seen == ["initialize", "session/prompt"]
    assert out["executed"] is True and out["spawned"] is False
    assert out["responses"][1]["result"]["content"][0]["text"] == "ok"


def test_adapter_session_without_transport_is_typed(tmp_path, monkeypatch):
    from voicebridge import acp_client as AC

    cfg = _cfg(tmp_path, monkeypatch)
    _no_spawn(monkeypatch)
    with pytest.raises(AC.AcpError) as e:
        AC.adapter_session(cfg, "gemini", prompt="hi", confirmed=True)
    assert e.value.code == AC.TRANSPORT_CODE


def test_adapter_session_validates_prompt(tmp_path, monkeypatch):
    from voicebridge import acp_client as AC

    cfg = _cfg(tmp_path, monkeypatch)
    transport = lambda r: {"jsonrpc": "2.0"}
    with pytest.raises(AC.AcpError) as e:
        AC.adapter_session(cfg, "gemini", prompt="   ", transport=transport,
                           confirmed=True)
    assert e.value.code == AC.BAD_INPUT_CODE
    with pytest.raises(AC.AcpError) as e:
        AC.adapter_session(cfg, "gemini", prompt="x" * (AC.MAX_PROMPT_CHARS + 1),
                           transport=transport, confirmed=True)
    assert e.value.code == AC.BAD_INPUT_CODE


def test_adapter_session_estop(tmp_path, monkeypatch):
    from voicebridge import acp_client as AC
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        with pytest.raises(AC.AcpError) as e:
            AC.adapter_session(cfg, "codex", prompt="hi",
                               transport=lambda r: {}, confirmed=True)
        assert e.value.code == AC.ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")


def test_named_refusals_match_ledger():
    from voicebridge import acp_lsp as AL

    serve = AL.acp_serve_refusal()
    assert serve["state"] == "REFUSED" and serve["code"] == AL.NO_SERVER_CODE
    hooks = AL.acp_accept_hooks_refusal()
    assert hooks["state"] == "REJECTED"
    assert hooks["code"] == AL.ACCEPT_HOOKS_CODE
    assert AL.acp_refuse_serve() == serve
    assert AL.acp_refuse_accept_hooks() == hooks


def test_lsp_lifecycle(monkeypatch):
    from voicebridge import acp_lsp as AL
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    life = AL.lsp_lifecycle()
    assert life["active_clients"] == "none"
    assert {s["lifecycle"] for s in life["servers"]} == {AL.LSP_ACTIVE_STATE}
    assert AL.lsp_lifecycle("gopls")["servers"][0]["server"] == "gopls"
    unknown = AL.lsp_lifecycle("cobol-ls-9000")
    assert unknown["state"] == "UNKNOWN"
    assert unknown["code"] == AL.UNKNOWN_SERVER_CODE
    assert AL.lsp_start("gopls")["code"] == AL.NO_CLIENTS_CODE
    assert AL.lsp_stop()["code"] == AL.NO_CLIENTS_CODE
    assert AL.lsp_status()["lifecycle"] == AL.LSP_ACTIVE_STATE
