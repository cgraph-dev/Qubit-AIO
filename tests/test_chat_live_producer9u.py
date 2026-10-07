"""Chat live-update producer — session.* / model.changed on the SSE feed.

The client consumer (``frontend/qubit-web/src/lib/chatLiveEvents.ts``) can only
ever react to a frame the server actually publishes. Before this producer, a
session create / model pick / rename / delete and a main-model set mutated the
store but broadcast nothing, so an open SPA only updated on a full page reload
(the reported defect).

This is a real reproduction through the compat handlers: a hermetic
``EventHub`` + ``EventJournal`` is attached to ``cfg._qubit_chat_hub`` — exactly
how ``webui.py`` wires the running service — and the handlers are driven
directly. The assertions are on the AG-UI wire frames the SSE loop emits
(``EventHub.wire_frame``), i.e. what the browser would actually parse.

Hermetic: the session store and model resolver are doubles; the hub is a real
in-memory-ordered hub over a ``tmp_path`` journal. No model, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class _FakeStore:
    """chat_transport store double covering the patched routes."""

    def __init__(self):
        self.model = {}

    def create(self, name="", session_id=None, model=None):
        sid = session_id or "dashboard-probe-1"
        self.model[sid] = model
        return {"state": "OK",
                "session": {"id": sid, "name": name, "model": model},
                "messages": []}

    def rename(self, sid, name):
        if sid not in self.model:
            return {"state": "MISSING", "code": "MISSING", "detail": "no row"}
        return {"state": "OK", "session": {"id": sid, "name": name,
                                           "model": self.model[sid]}}

    def set_model(self, sid, model):
        if sid not in self.model:
            return {"state": "MISSING", "code": "MISSING", "detail": "no row"}
        self.model[sid] = model
        return {"state": "OK", "session": {"id": sid, "model": model}}

    def archive(self, sid, archived):
        return {"state": "OK", "session": {"id": sid, "archived": archived}}

    def meta_patch(self, sid, meta):
        return {"state": "OK", "session": {"id": sid, "meta": meta}}

    def delete(self, sid, *, confirm=False):
        self.model.pop(sid, None)
        return {"state": "OK", "session": {"id": sid}}

    def show(self, sid):
        return {"state": "OK", "session": {"id": sid, "model": None},
                "messages": [], "message_count": 0}

    def meta_get(self, sid):
        return {"state": "OK", "meta": {}}


class _Cfg:
    """Minimal cfg surface the handlers touch, plus the live-hub attachment."""

    def __init__(self, hub, state_dir: Path):
        self._hub = hub
        self._state_dir = state_dir
        self._qubit_chat_hub = hub

    def state_dir(self):
        return self._state_dir

    def get(self, *a, **k):
        return None

    def section(self, name):
        return {}


@pytest.fixture()
def live_cfg(tmp_path, monkeypatch):
    from voicebridge.events import EventHub, EventJournal, make_event
    from voicebridge import chat_transport as CT
    from voicebridge import qubit_compat as QC

    hub = EventHub(EventJournal(tmp_path / "typed-events.jsonl"))
    cfg = _Cfg(hub, tmp_path)
    cfg._qubit_chat_make_event = make_event

    store = _FakeStore()
    monkeypatch.setattr(CT, "store", lambda _cfg: store)
    # A found model validates; an explicit pin echoes as the applied model.
    monkeypatch.setattr(CT, "resolve_model",
                        lambda _cfg, requested=None: (
                            {"ok": True, "model": requested}
                            if requested else
                            {"ok": True, "model": "qubit-brain"}))
    return cfg, store, hub


def _wire_frames(hub) -> list[dict]:
    """Every journal record, as the browser parses it (wire shape)."""
    from voicebridge.events.core import to_wire
    return [to_wire(rec) for rec in hub._journal.read_all()]


def _chat_frames(hub) -> list[dict]:
    return [f for f in _wire_frames(hub)
            if f.get("type") == "CUSTOM"
            and str(f.get("name", "")).startswith(("session.", "model."))]


def test_create_publishes_session_created(live_cfg):
    from voicebridge.qubit_compat import h_chat_session_create

    cfg, _store, hub = live_cfg
    status, body = h_chat_session_create(
        cfg, {}, {"name": "live-probe-create", "id": "probe-live-1"}, {})

    assert status == 200 and body["ok"] is True
    frames = _chat_frames(hub)
    assert frames, "no chat frame published"
    created = [f for f in frames if f["name"] == "session.created"]
    assert created, [f.get("name") for f in frames]
    value = created[0]["value"]
    assert value["session_id"] == "probe-live-1"
    assert value["session"]["name"] == "live-probe-create"


def test_model_pick_publishes_session_updated_with_model(live_cfg):
    from voicebridge.qubit_compat import h_chat_session_create, h_chat_session_patch

    cfg, _store, hub = live_cfg
    h_chat_session_create(cfg, {}, {"id": "probe-live-1"}, {})
    status, body = h_chat_session_patch(
        cfg, {"id": "probe-live-1"}, {"model": "deepseek-v4.1-flash"},
        {"id": "probe-live-1"})

    assert status == 200 and body["ok"] is True
    updated = [f for f in _chat_frames(hub) if f["name"] == "session.updated"]
    assert updated, "model pick published no session.updated"
    assert updated[-1]["value"]["session"]["model"] == "deepseek-v4.1-flash"
    assert updated[-1]["value"]["session_id"] == "probe-live-1"


def test_rename_publishes_session_updated(live_cfg):
    from voicebridge.qubit_compat import h_chat_session_create, h_chat_session_patch

    cfg, _store, hub = live_cfg
    h_chat_session_create(cfg, {}, {"id": "probe-live-1"}, {})
    status, _ = h_chat_session_patch(
        cfg, {"id": "probe-live-1"}, {"name": "probe-renamed"},
        {"id": "probe-live-1"})
    assert status == 200
    updated = [f for f in _chat_frames(hub) if f["name"] == "session.updated"]
    assert updated[-1]["value"]["session"]["name"] == "probe-renamed"


def test_delete_publishes_session_deleted(live_cfg):
    from voicebridge.qubit_compat import h_chat_session_create, h_chat_session_delete

    cfg, _store, hub = live_cfg
    h_chat_session_create(cfg, {}, {"id": "probe-live-1"}, {})
    status, body = h_chat_session_delete(
        cfg, {"confirm": "true"}, {}, {"id": "probe-live-1"})
    assert status == 200 and body["ok"] is True
    deleted = [f for f in _chat_frames(hub) if f["name"] == "session.deleted"]
    assert deleted, "delete published no session.deleted"
    assert deleted[-1]["value"]["session_id"] == "probe-live-1"


def test_missing_hub_is_fail_soft(live_cfg):
    """A cfg without the hub attachment must not fail the session write."""
    from voicebridge.qubit_compat import h_chat_session_create

    cfg, _store, _hub = live_cfg
    del cfg._qubit_chat_hub  # simulate a headless / attribute-less cfg
    status, body = h_chat_session_create(cfg, {}, {"id": "no-hub-1"}, {})
    assert status == 200 and body["ok"] is True


def test_frames_are_json_parseable_wire(live_cfg):
    """The SSE payload the consumer parses must round-trip through JSON."""
    from voicebridge.qubit_compat import h_chat_session_create

    cfg, _store, hub = live_cfg
    h_chat_session_create(cfg, {}, {"id": "probe-live-1"}, {})
    frame = hub.wire_frame(hub._journal.read_all()[0])
    assert frame.startswith("id: 1\ndata: ")
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    assert payload["type"] == "CUSTOM"
    assert payload["name"] == "session.created"


def test_global_model_set_publishes_model_changed(live_cfg, monkeypatch):
    """A main-model pick must announce model.changed on the same feed."""
    from voicebridge.providers import endpoints as EP
    from voicebridge import qubit_compat as QC
    from voicebridge.qubit_compat import h_model_set

    cfg, _store, hub = live_cfg
    monkeypatch.setattr(
        EP, "select_model",
        lambda _cfg, provider, model, **k: {
            "ok": True, "provider": provider, "model": model,
            "profile_id": "opencode-go-deepseek-v4.1-flash"})
    monkeypatch.setattr(
        QC, "_terminal_follow_main_model",
        lambda _cfg, _pid, **k: {
            "terminal_follows": False,
            "terminal_profile_id": "deepseek-v4-flash",
            "terminal_detail": "/chat keeps its current model"})

    status, body = h_model_set(
        cfg, {},
        {"scope": "main", "provider": "opencode-go",
         "model": "deepseek-v4.1-flash", "confirm": True},
        {})

    assert status == 200 and body["ok"] is True
    changed = [f for f in _chat_frames(hub) if f["name"] == "model.changed"]
    assert changed, "global model set published no model.changed"
    value = changed[-1]["value"]
    assert value["model"] == "deepseek-v4.1-flash"
    assert value["scope"] == "main"
    # The honest /chat signal rides along so the UI can explain the keep.
    assert value["terminal_follows"] is False
    assert "keeps" in value["terminal_detail"]

