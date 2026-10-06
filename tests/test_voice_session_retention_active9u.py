"""Retention must never destroy the LIVE stable voice session.

Journal / live evidence (2026-10-06): the canonical ``voice-chat`` session holds
**588** messages while ``voice_edit.voice_session_max_messages`` defaults to
**200**. ``VoiceEditService.ask()`` / ``task()`` call
``voice_sessions.prune()`` on the hot path, so the very next voice turn classified
``voice-chat`` as over the per-session cap and DELETED the live conversation —
silently wiping the transcript and contradicting ``single`` mode's contract that
one stable session carries the spoken dialogue.

The fix: the stable single session (``SINGLE_SESSION_ID``) is exempt from
retention deletion entirely (TTL, message cap, and the max-sessions cap), and a
caller may additionally protect the currently-active session id. Old
``voice-<stamp>-<hash>`` sessions still prune normally.

Hermetic: a fake store; no disk, model, socket, or service.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import voice_sessions as VS


class FakeCfg:
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


class FakeStore:
    def __init__(self, sessions=None):
        self.sessions = [dict(s) for s in (sessions or [])]
        self.deleted = []

    def list(self, *, limit=50, offset=0, query="", include_archived=True):
        return {"state": "OK", "sessions": [dict(s) for s in self.sessions],
                "total": len(self.sessions)}

    def delete(self, sid, *, confirm=False):
        self.deleted.append(sid)
        self.sessions = [s for s in self.sessions if s.get("id") != sid]
        return {"state": "OK", "session": {"id": sid}}


def _bind_store(monkeypatch, store):
    """Bind ``VS._store`` to ``store`` (mirrors the module's injectable seam)."""
    store_ref = [store]
    monkeypatch.setattr(
        VS, "_store",
        lambda cfg, store=None: store_ref[0])


def _cfg(**ve):
    return FakeCfg({"voice_edit": dict(ve)})


def test_active_stable_session_over_message_cap_is_never_deleted(monkeypatch):
    """RED before the fix: the live 588-message voice-chat session was pruned."""
    now = 2_000_000.0
    store = FakeStore([
        # The live conversation: recent activity, over the 200-message cap.
        {"id": VS.SINGLE_SESSION_ID, "last_active": now - 5,
         "message_count": 588},
        # A genuine old pile-up that SHOULD still be reclaimed.
        {"id": "voice-20200101-000000-deadbeef", "last_active": now - 90_000,
         "message_count": 3},
    ])
    _bind_store(monkeypatch, store)
    result = VS.prune(_cfg(voice_session_max_messages=200), now=now)
    assert store.deleted == ["voice-20200101-000000-deadbeef"], store.deleted
    assert VS.SINGLE_SESSION_ID not in store.deleted
    assert result["deleted"] == 1
    assert any(s.get("id") == VS.SINGLE_SESSION_ID for s in store.sessions)


def test_stable_session_exempt_from_max_sessions_cap(monkeypatch):
    """The stable session must survive even when older than the cap."""
    now = 2_000_000.0
    sessions = [{"id": VS.SINGLE_SESSION_ID, "last_active": now - 5000,
                 "message_count": 1}]
    # 3 newer per-command sessions; max_sessions=2 keeps 2 of them, but the
    # stable session must not be sacrificed to make room.
    for i in range(3):
        sessions.append({"id": f"voice-2026010{i}-000000-aaaaaaaa",
                         "last_active": now - (100 - i), "message_count": 1})
    store = FakeStore(sessions)
    _bind_store(monkeypatch, store)
    result = VS.prune(_cfg(voice_session_max_sessions=2), now=now)
    assert VS.SINGLE_SESSION_ID not in store.deleted, store.deleted
    assert result["deleted"] == 1, result
    assert any(s.get("id") == VS.SINGLE_SESSION_ID for s in store.sessions)


def test_explicit_protect_keeps_the_currently_active_session(monkeypatch):
    """A caller may protect the active per-command session by id."""
    now = 2_000_000.0
    store = FakeStore([
        {"id": "voice-20260101-000000-active01", "last_active": now - 5,
         "message_count": 999},
        {"id": "voice-20200101-000000-deadbeef", "last_active": now - 90_000,
         "message_count": 1},
    ])
    _bind_store(monkeypatch, store)
    result = VS.prune(_cfg(), now=now,
                      protect=["voice-20260101-000000-active01"])
    assert "voice-20260101-000000-active01" not in store.deleted
    assert result["deleted"] == 1


def test_stable_session_exempt_from_ttl(monkeypatch):
    """The stable session is the live anchor; TTL must not wipe it either."""
    now = 2_000_000.0
    store = FakeStore([
        {"id": VS.SINGLE_SESSION_ID, "last_active": now - 10 * 24 * 3600,
         "message_count": 1},
    ])
    _bind_store(monkeypatch, store)
    result = VS.prune(_cfg(), now=now)
    assert store.deleted == []
    assert result["deleted"] == 0


def test_service_prune_protects_the_inflight_session(monkeypatch):
    """The service passes the active request's session as ``protect``."""
    from voicebridge.voice_edit.service import VoiceEditService

    now = 2_000_000.0
    cfg = _cfg(voice_session_mode="per_command",
               voice_session_max_messages=200)
    store = FakeStore([
        {"id": "voice-20260101-000000-active01", "last_active": now - 5,
         "message_count": 999},
        {"id": "voice-20200101-000000-deadbeef", "last_active": now - 90_000,
         "message_count": 1},
    ])
    svc = VoiceEditService.__new__(VoiceEditService)
    svc.cfg = cfg
    svc._session_store = store
    svc._voice_pruned_at = 0.0
    svc._request_session = lambda request_id: "voice-20260101-000000-active01"
    svc._maybe_prune_voice_sessions("r1")
    assert "voice-20260101-000000-active01" not in store.deleted, store.deleted
    assert "voice-20200101-000000-deadbeef" in store.deleted, store.deleted


def test_protect_accepts_a_bare_string(monkeypatch):
    """A single id string protects that id (never iterated char-by-char)."""
    now = 2_000_000.0
    store = FakeStore([
        {"id": "voice-20260101-000000-active01", "last_active": now - 5,
         "message_count": 999},
    ])
    _bind_store(monkeypatch, store)
    result = VS.prune(_cfg(), now=now,
                      protect="voice-20260101-000000-active01")
    assert store.deleted == []
    assert result["deleted"] == 0
