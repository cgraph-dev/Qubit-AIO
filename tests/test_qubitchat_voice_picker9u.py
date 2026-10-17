"""Live-voice follow picker visibility (item 3).

``ChatPage`` finds the newest ``voice-*`` session through
``GET /api/qubit/chat/sessions`` (``pickNewestVoiceSession``) and follows it
via the ``qubit.chat.session`` storage key + ``?resume=``. The picker route
hides probe noise (``_is_chat_picker_noise``) — but a fresh live voice
session sits at 0-1 messages with source ``dashboard``, exactly the "tiny AND
machine-sourced" noise shape, so the follow jump was blind at the moment a
voice turn starts. ``voice-*`` ids are minted only by the voice path and are
exempt from the noise rule.

Hermetic: isolated XDG dirs, real store + real route dispatch, no network.
Covers the full scratch lifecycle (create -> list -> confirm-gated delete).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _dispatch(cfg, method, path, query=None, body=None):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, method, path, query or {}, body or {})


def _ids(body):
    return [s.get("id") for s in (body.get("sessions") or [])]


def test_fresh_voice_sessions_are_picker_visible(tmp_path, monkeypatch):
    """0-message and 1-message voice-* rows survive the noise filter."""
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    st = CT.store(cfg)
    assert st.create("", session_id="voice-fresh-zero").get("state") == "OK"
    assert st.create("", session_id="voice-fresh-one").get("state") == "OK"
    st.append("voice-fresh-one", "user", "hello voice")
    code, body = _dispatch(cfg, "GET", "/api/qubit/chat/sessions")
    assert code == 200, body
    listed = _ids(body)
    assert "voice-fresh-zero" in listed, listed
    assert "voice-fresh-one" in listed, listed


def test_probe_shaped_rows_stay_hidden(tmp_path, monkeypatch):
    """The exemption hides nothing else: tiny dashboard rows still filter."""
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    st = CT.store(cfg)
    assert st.create("", session_id="dashboard-fresh-zero").get(
        "state") == "OK"
    code, body = _dispatch(cfg, "GET", "/api/qubit/chat/sessions")
    assert code == 200, body
    assert "dashboard-fresh-zero" not in _ids(body)


def test_scratch_lifecycle_deletes_clean(tmp_path, monkeypatch):
    """Confirm-gated delete removes scratch rows (no residue)."""
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    st = CT.store(cfg)
    assert st.create("", session_id="voice-scratch-del").get("state") == "OK"
    code, _ = _dispatch(cfg, "DELETE",
                        "/api/qubit/chat/sessions/voice-scratch-del")
    assert code == 409, code  # confirm gate holds without confirm
    code, _ = _dispatch(cfg, "DELETE",
                        "/api/qubit/chat/sessions/voice-scratch-del",
                        {"confirm": "true"})
    assert code == 200, code
    code, body = _dispatch(cfg, "GET", "/api/qubit/chat/sessions")
    assert code == 200, body
    assert "voice-scratch-del" not in _ids(body)
