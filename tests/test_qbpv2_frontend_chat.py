"""Wave-2 W3 — Qubit-safe `/chat` transport.

The built-in dashboard chat must not drive the raw PTY WebSocket: Qubit policy
refuses `/api/pty` with `PTY_NO_SHELL`, which produced the 1006 reconnect loop.
The page now sends native agent turns through `POST /api/qubit/chat` (the same
`voicebridge.agent.turn` module as the CLI and voice PTT). These tests pin the
source contract and exercise the real backend turn.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

CHAT_PAGE = (REPO_ROOT / "frontend" / "qubit-web" / "src" / "pages"
             / "ChatPage.tsx")
# The one transcription authority: the page renders its typed `error` /
# `fallbackReason` verbatim; the error codes live in the controller/adapters.
VOICE_LIB = REPO_ROOT / "frontend" / "qubit-web" / "src" / "lib" / "voice"


def _cfg(tmp_path, monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        target = tmp_path / var.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(target))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home" / ".hermes"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    from voicebridge.config import Config

    return Config.load(p)


def test_chat_page_uses_the_qubit_safe_transport():
    src = CHAT_PAGE.read_text(encoding="utf-8")
    assert "/api/qubit/chat" in src
    # The raw PTY transport is gone: no buildWsUrl call, no /api/pty URL
    # construction, no WebSocket, no PTY reconnect bookkeeping. (The doc
    # comment may mention the refused endpoint; code literals must not.)
    assert '"/api/pty"' not in src
    assert "buildWsUrl(" not in src
    assert "new WebSocket" not in src
    assert "PTY_TICKET_TIMEOUT_MS" not in src
    assert "reconnectNonce" not in src


def test_chat_page_has_typed_states_and_persistence():
    src = CHAT_PAGE.read_text(encoding="utf-8")
    for token in ("No messages yet", "Qubit is thinking", "role=\"alert\"",
                  "Retry", "voiceError", "voiceFallback",
                  "loadTranscript", "saveTranscript"):
        assert token in src, token
    # aria-busy / role=log so the transcript is announced and the loading
    # state is machine-visible.
    assert "aria-busy" in src and 'role="log"' in src
    # The voice failure modes are typed, never silent. The page surfaces the
    # controller's `error` (rendered in the `role="alert"` block above) and
    # its `fallbackReason`; the typed codes live in the one transcription
    # authority (controller + adapters), not as disconnected literals here.
    voice_src = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted(VOICE_LIB.glob("*.ts"))
        if not p.name.endswith(".test.ts"))
    for code in ("VOICE_UNSUPPORTED", "VOICE_DENIED", "VOICE_NO_SPEECH",
                 "VOICE_STT_UNAVAILABLE", "VOICE_STT_TIMEOUT"):
        assert code in voice_src, code
    # A missing engine is reported as a typed fallback, never a silent switch.
    assert "fallbackReason" in voice_src


def test_backend_chat_turn_returns_a_real_brain4_reply(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    status, payload = HC.dispatch(
        cfg, "POST", "/api/qubit/chat", {},
        {"text": "Reply with the single token QUBIT_BRAIN4_OK",
         "session": "w3-chat-test"})
    assert status == 200, (status, payload)
    assert isinstance(payload, dict) and payload.get("ok") is True, payload
    assert payload.get("reply", "").strip(), payload
    assert payload.get("receipt", {}).get("model"), payload
    assert payload.get("receipt", {}).get("path") == "voicebridge.agent.turn", \
        payload


def test_chat_map_records_no_pty_transport():
    doc = json.loads((REPO_ROOT / "docs/archive/2026-09-21" / "HERMES_FRONTEND_MAP.json")
                     .read_text(encoding="utf-8"))
    route = doc["routes"]["/chat"]
    assert "api/qubit/chat" in route["note"]
    assert "PTY_NO_SHELL" in route["note"]
    workflow = next(w for w in doc["inventory"]["workflows"]
                    if w["workflow"] == "chat/streaming")
    assert "api/qubit/chat" in workflow["backend"]
    assert "empty" in workflow["states"] and "retry" in workflow["states"]
