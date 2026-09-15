"""F5 final-wave branding: D4 served-boundary rebrand of persisted chat.

The chat-session store is durable history and may contain legacy upstream-
authored text (tracked defect D4: a stored ``/chat`` message that says
``"developed as OpenClaw"``). The transcript bytes are never rewritten; the
upstream product name is rendered as Qubit only at the served response
boundary, through the shared ``hermes_plugins._rebrand_product`` convention.

These tests seed a legacy transcript on disk, dispatch the real compat routes,
and assert (a) the served payload is Qubit-branded with no upstream product
token, and (b) the on-disk transcript still holds the original bytes.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

UPSTREAM = re.compile(r"\b(?:Hermes|OpenClaw)\b", re.IGNORECASE)

LEGACY_TEXT = "This build was developed as OpenClaw by the Hermes team."


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seed_legacy_store(cfg, session_id="legacy-chat"):
    """Write a legacy chat session + transcript straight to the store file."""
    from voicebridge import chat_transport as CT

    state = Path(cfg.state_dir())
    state.mkdir(parents=True, exist_ok=True)
    now = 1_700_000_000.0
    store = {
        "schema": CT.SESSION_SCHEMA,
        "sessions": {
            session_id: {
                "id": session_id,
                "name": "OpenClaw handoff",
                "model": None,
                "created_at": now,
                "updated_at": now,
                "archived": False,
                "message_count": 2,
                "identity": "dashboard:user",
            },
        },
        "messages": {
            session_id: [
                {"role": "user", "content": "Who made this?",
                 "timestamp": now},
                {"role": "assistant", "content": LEGACY_TEXT,
                 "timestamp": now + 1,
                 "meta": {"model": "OpenClaw-local"}},
            ],
        },
        "tombstones": {},
    }
    path = state / CT.STORE_FILE
    path.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    return path


def _dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def test_d4_legacy_message_served_as_qubit_but_store_unchanged(
        tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    store_path = _seed_legacy_store(cfg)
    before = store_path.read_text(encoding="utf-8")
    assert "OpenClaw" in before, "fixture must actually contain the legacy text"

    code, out = HC.dispatch(
        cfg, "GET", "/api/qubit/chat/sessions/legacy-chat/messages", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    served = _dump(out["messages"])
    assert "developed as Qubit" in served, served
    assert not UPSTREAM.search(served), served

    code, out = HC.dispatch(
        cfg, "GET", "/api/qubit/chat/sessions/legacy-chat", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    served_show = _dump(out)
    assert not UPSTREAM.search(served_show), served_show
    assert out["session"]["name"] == "Qubit handoff", out["session"]

    after = store_path.read_text(encoding="utf-8")
    assert after == before, "the stored transcript bytes must be untouched"
    assert "OpenClaw" in after


def test_d4_export_and_sessions_list_are_rebranded(tmp_path, monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_legacy_store(cfg)

    code, out = HC.dispatch(
        cfg, "GET", "/api/qubit/chat/sessions/legacy-chat/export", {}, {})
    assert code == 200, (code, out)
    assert not UPSTREAM.search(_dump(out)), _dump(out)

    code, out = HC.dispatch(
        cfg, "GET", "/api/qubit/chat/sessions", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    served = _dump(out)
    assert not UPSTREAM.search(served), served
    assert out["sessions"], out


def test_chat_compat_boundary_has_no_upstream_product_token(
        tmp_path, monkeypatch):
    """Normal-surface scan: every persisted-chat serve path is Qubit-branded."""
    from voicebridge import hermes_compat as HC
    from voicebridge.hermes_plugins import _rebrand_product

    # the shared convention renders both upstream product names as Qubit
    assert _rebrand_product(LEGACY_TEXT) == \
        "This build was developed as Qubit by the Qubit team."

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_legacy_store(cfg)
    for path in (
        "/api/qubit/chat/sessions",
        "/api/qubit/chat/sessions/legacy-chat",
        "/api/qubit/chat/sessions/legacy-chat/messages",
        "/api/qubit/chat/sessions/legacy-chat/export",
    ):
        code, out = HC.dispatch(cfg, "GET", path, {}, {})
        assert code == 200, (path, code, out)
        assert not UPSTREAM.search(_dump(out)), (path, _dump(out))


if __name__ == "__main__":  # pragma: no cover
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
