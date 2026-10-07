"""Wave-5 TASK 2: voice/wake/PTT CREATES a ``/qubit-chat`` dashboard session.

Owner directive: Right-Ctrl, wake commands and voice commands must create
sessions in ``/qubit-chat``. The single seam is ``VoiceEditService.begin()``
(both the Right-Ctrl PTT path and the wake path reach it via
``desktop_helper._open_request`` -> ``helper.client.begin``). ``begin`` now
ensures the canonical dashboard-source session exists on the ONE store
(``chat_transport.store`` -> ``QubitSessionStore``), so the ``/qubit-chat``
picker (which lists dashboard-source rows through ``h_chat_sessions_list``)
shows it.

Hermetic: a fake store; no disk, model, socket, or service. The fake store
mirrors the real ``QubitSessionStore`` contract (``show`` / ``ensure`` /
``create`` with the ``dashboard`` source) so the assertions exercise the same
code path the live store does.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import voice_sessions as VS
from voicebridge.voice_edit.service import VoiceEditService


class FakeCfg:
    def __init__(self, data=None, path=None):
        self._data = data or {}
        self.path = path

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


class FakeStore:
    """Minimal ``QubitSessionStore`` double, faithful to the real contract.

    ``create`` stamps ``source="dashboard"`` (as the real store does) and only
    applies a default model for a NEW row; ``ensure`` is a no-op when the row
    exists, so an existing transcript's stored model is never re-defaulted.
    """

    SOURCE = "dashboard"

    def __init__(self, rows=None):
        #: id -> {"id", "source", "model", "message_count", "title"}
        self.rows = {r["id"]: dict(r) for r in (rows or [])}
        self.ensure_calls = []
        self.create_calls = []

    def show(self, sid):
        row = self.rows.get(str(sid))
        if row is None:
            return {"state": "NOT_FOUND", "code": "SESSION_NOT_FOUND"}
        return {"state": "OK", "session": dict(row)}

    def ensure(self, session_id, *, model=None, identity="dashboard:user"):
        sid = str(session_id or "").strip()[:80] or "dashboard-default"
        self.ensure_calls.append((sid, model, identity))
        if sid in self.rows:
            return {"state": "OK", "session": dict(self.rows[sid])}
        return self.create("", session_id=sid, model=model, identity=identity)

    def create(self, name="", *, session_id=None, model=None,
               identity="dashboard:user", created_at=None, updated_at=None):
        sid = str(session_id or name or "").strip()
        self.create_calls.append((sid, model, identity))
        if sid in self.rows:
            return {"state": "EXISTS", "code": "SESSION_EXISTS"}
        self.rows[sid] = {"id": sid, "source": self.SOURCE, "model": model,
                          "message_count": 0, "title": name or None}
        return {"state": "OK", "session": dict(self.rows[sid]), "messages": []}


def _cfg(**voice_edit):
    return FakeCfg({"voice_edit": dict(voice_edit)})


# ---------------------------------------------------------------------------
# ensure_voice_session — identity resolution, source, default model
# ---------------------------------------------------------------------------
def test_ensure_creates_dashboard_session_at_local_brain_default(monkeypatch):
    """A fresh voice interaction creates a dashboard-source row the picker lists."""
    store = FakeStore()
    monkeypatch.setattr(VS, "_store", lambda cfg, store=None: store)
    out = VS.ensure_voice_session(_cfg(), None, store=store, model="qubit-brain")
    assert out["ok"] is True
    assert out["state"] == "OK"
    assert out["session_id"] == VS.SINGLE_SESSION_ID == "voice-chat"
    assert out["created"] is True
    row = store.rows["voice-chat"]
    assert row["source"] == "dashboard", row
    assert row["model"] == "qubit-brain", row
    # The picker lists dashboard-source rows -> this row is visible.
    assert store.show("voice-chat")["session"]["source"] == "dashboard"


def test_ensure_reuses_existing_session_without_reddefaulting_model(monkeypatch):
    """An existing voice transcript is never overwritten or re-defaulted."""
    store = FakeStore([{"id": "voice-chat", "source": "dashboard",
                        "model": "qubit-brain4", "message_count": 592}])
    monkeypatch.setattr(VS, "_store", lambda cfg, store=None: store)
    out = VS.ensure_voice_session(_cfg(), None, store=store, model="qubit-brain")
    assert out["ok"] is True and out["created"] is False
    # The stored model is untouched (ensure is a no-op for an existing row).
    assert store.rows["voice-chat"]["model"] == "qubit-brain4"
    assert store.create_calls == []


def test_ensure_explicit_routed_target_wins_over_voice_default(monkeypatch):
    """A routed wake target keeps its id (the wake route binding)."""
    store = FakeStore()
    monkeypatch.setattr(VS, "_store", lambda cfg, store=None: store)
    out = VS.ensure_voice_session(_cfg(), "agent:r:main", store=store,
                                  model="qubit-brain")
    assert out["session_id"] == "agent:r:main"
    assert "agent:r:main" in store.rows
    assert VS.SINGLE_SESSION_ID not in store.rows


def test_ensure_per_command_mode_uses_deterministic_id(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(VS, "_store", lambda cfg, store=None: store)
    cfg = _cfg(voice_session_mode="per_command")
    out = VS.ensure_voice_session(cfg, None, store=store, model="qubit-brain")
    assert out["session_id"].startswith("voice-")
    assert out["session_id"] != VS.SINGLE_SESSION_ID


def test_ensure_store_unavailable_is_typed_and_never_raises(monkeypatch):
    monkeypatch.setattr(VS, "_store", lambda cfg, store=None: None)
    out = VS.ensure_voice_session(_cfg(), None, store=None, model="qubit-brain")
    assert out["ok"] is False and out["state"] == "STORE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# VoiceEditService.begin — the single PTT/wake seam
# ---------------------------------------------------------------------------
class _FakeCap:
    def session(self):
        return {}


class _Service(VoiceEditService):
    """VoiceEditService with desktop/estop stubbed so begin() is hermetic."""

    def __init__(self, cfg, session_store):
        super().__init__(cfg, session_store=session_store,
                         desktop=_FakeCap(), estop=lambda _cfg: False,
                         audit=lambda *a, **k: None)

    def _require_enabled(self):
        pass

    def _require_estop_clear(self):
        pass


def test_begin_creates_dashboard_session_on_the_injected_store():
    """begin() ensures the canonical dashboard session (PTT default path)."""
    store = FakeStore()
    svc = _Service(_cfg(), store)
    out = svc.begin()
    assert out["ok"] is True
    assert store.ensure_calls, store.ensure_calls
    sid, model, _identity = store.ensure_calls[-1]
    assert sid == VS.SINGLE_SESSION_ID
    # model is None here because the hermetic cfg has no intent.local_llm; the
    # store then applies its own default. The row is still dashboard-source.
    assert store.rows[sid]["source"] == "dashboard"


def test_begin_with_routed_session_creates_that_session():
    """The wake-routed path passes session_id=route_session; begin ensures it."""
    store = FakeStore()
    svc = _Service(_cfg(), store)
    svc.begin(session_id="agent:r:main")
    assert "agent:r:main" in store.rows
    assert store.rows["agent:r:main"]["source"] == "dashboard"


def test_begin_is_never_blocked_by_a_store_fault():
    """A broken store must not break the turn (additive, best-effort)."""
    class BoomStore:
        def show(self, sid):
            raise RuntimeError("store down")

        def ensure(self, *a, **k):
            raise RuntimeError("store down")

    svc = _Service(_cfg(), BoomStore())
    out = svc.begin()
    assert out["ok"] is True and out["request_id"]


def test_begin_uses_canonical_store_when_none_injected(monkeypatch):
    """With no injected store, begin resolves the ONE canonical store."""
    store = FakeStore()
    from voicebridge import chat_transport as CT
    monkeypatch.setattr(CT, "store", lambda cfg: store)
    svc = _Service(_cfg(), None)
    svc.begin()
    assert store.ensure_calls, store.ensure_calls
    assert store.ensure_calls[-1][0] == VS.SINGLE_SESSION_ID
