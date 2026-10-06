"""Voice-session namespace + retention (voice_sessions) and service wiring.

Hermetic: every store is a fake; no disk, no model, no socket. Covers the two
modes, the deterministic per-command id, the retention policy defaults, and the
prune rules (TTL / message cap / max_sessions / non-voice isolation / a broken
store), plus the service delegation and the routed-session precedence.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import voice_sessions as VS
from voicebridge.voice_edit.service import VoiceEditService
from voicebridge.voice_edit.transform import TransformEngine


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
    """Minimal canonical-store double recording list/delete calls."""

    def __init__(self, sessions=None):
        self.sessions = [dict(s) for s in (sessions or [])]
        self.deleted = []
        self.list_calls = 0

    def list(self, *, limit=50, offset=0, query="", include_archived=True):
        self.list_calls += 1
        return {"state": "OK", "sessions": [dict(s) for s in self.sessions],
                "total": len(self.sessions)}

    def delete(self, sid, *, confirm=False):
        self.deleted.append(sid)
        self.sessions = [s for s in self.sessions if s.get("id") != sid]
        return {"state": "OK", "session": {"id": sid}}

    def messages(self, sid, *, limit=500, offset=0):
        return {"state": "OK", "messages": []}


class BrokenStore:
    def list(self, **kwargs):
        raise RuntimeError("store is broken")

    def delete(self, *args, **kwargs):
        raise RuntimeError("store is broken")


def _cfg(**ve):
    return FakeCfg({"voice_edit": dict(ve)})


# ---------------------------------------------------------------------------
# mode + ids
# ---------------------------------------------------------------------------
def test_session_mode_defaults_to_single():
    assert VS.session_mode(_cfg()) == "single"
    assert VS.session_mode(FakeCfg()) == "single"


def test_session_mode_per_command_aliases():
    for raw in ("per_command", "per-command", "percommand", "PER COMMAND"):
        assert VS.session_mode(_cfg(voice_session_mode=raw)) == "per_command"


def test_voice_session_id_single_is_stable():
    cfg = _cfg()
    first = VS.voice_session_id(cfg)
    second = VS.voice_session_id(cfg, request_id="whatever", now=123.0)
    assert first == VS.SINGLE_SESSION_ID == "voice-chat"
    assert second == "voice-chat"
    assert VS.is_voice_session(first)


def test_voice_session_id_per_command_is_deterministic():
    cfg = _cfg(voice_session_mode="per_command")
    a = VS.voice_session_id(cfg, request_id="req-42", now=1_700_000_000)
    b = VS.voice_session_id(cfg, request_id="req-42", now=1_700_000_000)
    assert a == b
    assert a.startswith(VS.VOICE_SESSION_PREFIX)
    import re
    assert re.fullmatch(r"voice-\d{8}-\d{6}-[0-9a-f]{8}", a), a


def test_voice_session_id_per_command_differs_by_request():
    cfg = _cfg(voice_session_mode="per_command")
    a = VS.voice_session_id(cfg, request_id="one", now=1_700_000_000)
    b = VS.voice_session_id(cfg, request_id="two", now=1_700_000_000)
    assert a != b


def test_is_voice_session():
    assert VS.is_voice_session("voice-chat") is True
    assert VS.is_voice_session("voice-20240101-000000-deadbeef") is True
    assert VS.is_voice_session("voice-chat") is True
    assert VS.is_voice_session("dashboard-chat") is False
    assert VS.is_voice_session("") is False
    assert VS.is_voice_session(None) is False


# ---------------------------------------------------------------------------
# retention policy
# ---------------------------------------------------------------------------
def test_retention_policy_defaults():
    policy = VS.retention_policy(_cfg())
    assert policy["ttl_s"] == VS.DEFAULT_TTL_S == 24 * 60 * 60
    assert policy["max_messages_per_session"] == \
        VS.DEFAULT_MAX_MESSAGES_PER_SESSION
    assert policy["max_sessions"] == VS.DEFAULT_MAX_SESSIONS


def test_retention_policy_overrides_and_bad_values():
    policy = VS.retention_policy(_cfg(
        voice_session_ttl_s=60, voice_session_max_messages=5,
        voice_session_max_sessions=3))
    assert policy == {"ttl_s": 60.0, "max_messages_per_session": 5,
                      "max_sessions": 3}
    # Invalid values fall back to the defaults rather than disabling retention.
    policy = VS.retention_policy(_cfg(
        voice_session_ttl_s="nope", voice_session_max_messages=0,
        voice_session_max_sessions=-1))
    assert policy["ttl_s"] == VS.DEFAULT_TTL_S
    assert policy["max_messages_per_session"] == \
        VS.DEFAULT_MAX_MESSAGES_PER_SESSION
    assert policy["max_sessions"] == VS.DEFAULT_MAX_SESSIONS


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------
def _bind_store(monkeypatch, store):
    store_ref = [store]
    monkeypatch.setattr(
        VS, "_store",
        lambda cfg, store=None: store if store is not None else store_ref[0])
    return store


def test_prune_deletes_by_ttl(monkeypatch):
    now = 2_000_000.0
    store = _bind_store(monkeypatch, FakeStore([
        {"id": "voice-old", "last_active": now - 90_000, "message_count": 1},
        {"id": "voice-new", "last_active": now - 10, "message_count": 1},
    ]))
    result = VS.prune(_cfg(), now=now)
    assert result["deleted"] == 1 and result["deleted_ttl"] == 1
    assert store.deleted == ["voice-old"]
    assert result["kept"] == 1


def test_prune_deletes_by_message_cap(monkeypatch):
    now = 2_000_000.0
    store = _bind_store(monkeypatch, FakeStore([
        {"id": "voice-big", "last_active": now - 10, "message_count": 500},
        {"id": "voice-ok", "last_active": now - 20, "message_count": 10},
    ]))
    result = VS.prune(_cfg(voice_session_max_messages=200), now=now)
    assert result["deleted"] == 1 and result["deleted_messages"] == 1
    assert store.deleted == ["voice-big"]


def test_prune_keeps_newest_max_sessions(monkeypatch):
    now = 2_000_000.0
    sessions = [
        {"id": f"voice-{i}", "last_active": now - (100 - i * 10),
         "message_count": 1}
        for i in range(4)
    ]
    store = _bind_store(monkeypatch, FakeStore(sessions))
    result = VS.prune(_cfg(voice_session_max_sessions=2), now=now)
    assert result["deleted"] == 2 and result["deleted_cap"] == 2
    # Oldest two pruned, newest two kept.
    assert sorted(store.deleted) == ["voice-0", "voice-1"]
    assert result["kept"] == 2


def test_prune_never_touches_non_voice_sessions(monkeypatch):
    now = 2_000_000.0
    store = _bind_store(monkeypatch, FakeStore([
        {"id": "dashboard-chat", "last_active": now - 999_999,
         "message_count": 10_000},
        {"id": "agent-run", "last_active": now - 999_999,
         "message_count": 10_000},
        {"id": "voice-fresh", "last_active": now - 5, "message_count": 1},
    ]))
    result = VS.prune(_cfg(voice_session_max_sessions=1), now=now)
    assert store.deleted == []
    assert result["deleted"] == 0
    assert result["scanned"] == 1


def test_prune_never_raises_on_broken_store(monkeypatch):
    _bind_store(monkeypatch, BrokenStore())
    result = VS.prune(_cfg())
    assert result["ok"] is True
    assert result["deleted"] == 0


def test_prune_store_unavailable_is_safe(monkeypatch):
    def _boom(cfg, store=None):
        raise RuntimeError("no store")

    monkeypatch.setattr(VS, "_store", _boom)
    result = VS.prune(_cfg())
    assert result["ok"] is True and result["deleted"] == 0


# ---------------------------------------------------------------------------
# service wiring
# ---------------------------------------------------------------------------
class FakeDesktop:
    def session(self):
        return {"session_type": "x11"}

    def _tool(self, name):
        return False

    def is_fullscreen(self, window_id=""):
        return False

    def capture_selection(self, cfg=None):
        from voicebridge.voice_edit.schema import SelectionSnapshot
        return SelectionSnapshot(available=False, reason="none")

    def insert_text(self, text, snapshot, cfg=None, *, method=""):
        from voicebridge.voice_edit.schema import InsertionResult
        return InsertionResult(ok=True, method="clipboard_paste")


def _service(cfg=None, store=None):
    audits = []
    svc = VoiceEditService(
        cfg or _cfg(),
        desktop=FakeDesktop(),
        engine=TransformEngine(runner=lambda *a, **k: None),
        audit=lambda op, actor, detail: audits.append((op, detail)),
        estop=lambda cfg: False,
        session_store=store or FakeStore())
    return svc, audits


def test_service_voice_session_delegates_to_module():
    cfg = _cfg()
    svc, _ = _service(cfg)
    assert svc._voice_session("r1") == VS.voice_session_id(cfg, request_id="r1")
    assert svc._voice_session() == "voice-chat"


def test_service_full_turn_uses_voice_session(monkeypatch):
    import voicebridge.chat_transport as CT

    cfg = _cfg()
    svc, _ = _service(cfg)
    seen = {}

    def fake_turn(cfg, text, **kw):
        seen.update(kw)
        return {"ok": True, "reply": "hello back", "route": "answer"}

    monkeypatch.setattr(CT, "turn", fake_turn)
    out = svc._full_turn_answer("hello", request_id="ft-1")
    assert out and out["ok"] is True
    assert seen.get("session") == VS.voice_session_id(cfg, request_id="ft-1")
    assert seen.get("session") == "voice-chat"


def test_service_task_uses_voice_session(monkeypatch):
    import voicebridge.voice_edit.tool_task as TT

    cfg = _cfg()
    svc, _ = _service(cfg)
    calls = {}

    def fake_run(cfg, text, **kw):
        calls["session"] = kw.get("session")
        return {"state": "OK", "reply": "done", "tool_used": "local.x"}

    monkeypatch.setattr(TT, "run_tool_task", fake_run)
    out = svc.task("do the thing", confirm=True, request_id="r1")
    assert out["ok"] is True
    assert calls["session"] == "voice-chat"


def test_service_task_routed_session_wins(monkeypatch):
    import voicebridge.voice_edit.tool_task as TT

    cfg = _cfg(enabled=True)
    svc, _ = _service(cfg)
    calls = {}

    def fake_run(cfg, text, **kw):
        calls["session"] = kw.get("session")
        return {"state": "OK", "reply": "done", "tool_used": "local.x"}

    monkeypatch.setattr(TT, "run_tool_task", fake_run)
    rid = svc.begin(session_id="routed-sess")["request_id"]
    out = svc.task("do the thing", confirm=True, request_id=rid)
    assert out["ok"] is True
    assert calls["session"] == "routed-sess"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
