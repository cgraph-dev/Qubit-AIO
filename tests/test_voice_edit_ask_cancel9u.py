"""Orb STOP cancels the in-flight ``/ask`` turn end-to-end (9u).

The orb STOP (SIGUSR2 -> helper ``on_orb_stop``) used to stop only the
helper's own playback/flag: the service's blocking ``/api/voice-edit/ask``
call (the expensive brain/agent turn) kept running to completion, and any
speech the service streams for the turn was out of reach.

This pins the end-to-end path:

- SERVER: a cancel keyed to the turn's ``request_id`` (the helper's
  ``/<rid>/cancel`` POST) sets a per-turn cancel event. ``ask`` skips the
  brain call if the stop arrived first, aborts the brain call through the
  real gateway ``cancel``/``should_stop`` hooks if it is in flight, and
  never triggers the full-turn fallback or returns a reply once stopped.
  ``_full_turn_answer`` hands the same event to the existing
  ``chat_transport.turn`` cancel transport.
- HELPER: ``stop_speaking`` asks the service to cancel the active
  ``self.request_id`` (best-effort, at most once per turn, on a daemon
  thread so the SIGUSR2 handler never blocks), and a typed cancelled
  response closes the orb FSM with the canonical stop triple, never an
  error flash.

Hermetic: fakes for the gateway/brain, the chat transport, the client and
the clock; no network, model, mic, audio or X.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.chat_transport as CT  # noqa: E402
import voicebridge.desktop_helper as dh  # noqa: E402
from voicebridge.providers import gateway as GW  # noqa: E402
from voicebridge.voice_edit import service as svc_mod  # noqa: E402
from voicebridge.voice_edit.service import VoiceEditService  # noqa: E402


# ---------------------------------------------------------------------------
# shared fakes
# ---------------------------------------------------------------------------
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


def _cfg(**ve):
    base = {"enabled": True, "routing": "local",
            "confirm_before_replace": False, "speak_answers": True}
    base.update(ve)
    return FakeCfg({"voice_edit": base})


class FakeDesktop:
    def session(self):
        return {"session_type": "x11", "x11": True}


class R:
    """A ``ProviderResult``-shaped stub."""

    def __init__(self, *, ok=True, text="", code="", message="",
                 provider="local", model="qubit-brain4"):
        self.ok = ok
        self.text = text
        self.code = code
        self.message = message
        self.provider = provider
        self.model = model


def _svc(monkeypatch):
    """A hermetic service whose heavy seams are inert (no store, no I/O)."""
    monkeypatch.setattr(svc_mod, "_signal_helper_stop", lambda: False)
    svc = VoiceEditService(_cfg(), desktop=FakeDesktop(),
                           audit=lambda *a: None, estop=lambda cfg: False)
    svc._maybe_prune_voice_sessions = lambda *a, **k: None
    svc._remember_ask_exchange = lambda *a, **k: None
    svc._conversation_context = lambda *a, **k: ""
    svc._personal_context = lambda *a, **k: ""
    svc._live_facts = lambda *a, **k: ""
    svc._select_model = lambda configured: ""
    svc._known_model_profile = lambda pid: True
    return svc


# ---------------------------------------------------------------------------
# SERVER: cancel prevents the brain call if it has not started
# ---------------------------------------------------------------------------
def test_cancel_before_brain_prevents_the_call(monkeypatch):
    called = []
    monkeypatch.setattr(GW, "run_role", lambda *a, **k: called.append(1))
    svc = _svc(monkeypatch)
    rid = "rid-early"
    # The stop arrives before ask registers: cancel() leaves a set tombstone.
    svc.cancel(rid)
    out = svc.ask("tell me a joke about routers", request_id=rid)
    assert out["ok"] is False
    assert out["code"] == "VOICE_EDIT_ASK_CANCELLED"
    assert called == [], "a cancelled turn must never reach the brain"


def test_cancel_during_brain_uses_the_real_abort_hooks(monkeypatch):
    entered = threading.Event()
    seen = {}

    def fake_run_role(prompt, cfg, **kw):
        seen["cancel"] = kw.get("cancel")
        seen["should_stop"] = kw.get("should_stop")
        entered.set()
        deadline = time.time() + 3.0
        while time.time() < deadline and not kw["should_stop"]():
            time.sleep(0.005)
        return R(ok=False, code="CANCELLED", message="cancelled mid-flight")

    monkeypatch.setattr(GW, "run_role", fake_run_role)
    svc = _svc(monkeypatch)
    rid = "rid-mid"
    result = {}
    t = threading.Thread(
        target=lambda: result.update(
            out=svc.ask("tell me a joke", request_id=rid)))
    t.start()
    assert entered.wait(timeout=3.0), "brain call never started"
    # The orb STOP lands while the model call is in flight.
    svc.cancel(rid)
    t.join(timeout=3.0)
    assert not t.is_alive()
    assert result["out"]["code"] == "VOICE_EDIT_ASK_CANCELLED"
    # Both real abort hooks exist and read the turn's event.
    assert callable(seen["cancel"]) and seen["cancel"]() is True
    assert callable(seen["should_stop"]) and seen["should_stop"]() is True


def test_cancel_returns_promptly_even_when_the_call_ignores_the_hooks(
        monkeypatch):
    """A genuinely uninterruptible call must not hold the /ask handler.

    The blocking local ``chat_local`` HTTP read cannot be aborted from another
    thread, so the service watches the turn's event on a worker and returns the
    typed cancelled result at once; the late reply is then discarded (never
    persisted, never spoken) and the worker cleans itself up.
    """
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = []
    remembered = []
    fallback = []

    def stubborn_run_role(prompt, cfg, **kw):
        calls.append(1)
        entered.set()
        # Deliberately ignores both cancel hooks: this is the layer that
        # cannot be interrupted mid-request.
        release.wait(timeout=5.0)
        finished.set()
        return R(text="I can't help with that.")

    monkeypatch.setattr(GW, "run_role", stubborn_run_role)
    monkeypatch.setattr(CT, "turn", lambda *a, **k: fallback.append(1))
    svc = _svc(monkeypatch)
    svc._remember_ask_exchange = lambda *a, **k: remembered.append(a)
    rid = "rid-stubborn"
    result = {}
    t = threading.Thread(
        target=lambda: result.update(
            out=svc.ask("tell me a joke", request_id=rid)))
    t.start()
    assert entered.wait(timeout=3.0), "brain call never started"
    t0 = time.monotonic()
    svc.cancel(rid)
    t.join(timeout=1.0)
    elapsed = time.monotonic() - t0
    assert not t.is_alive(), "the /ask handler waited for the blocked call"
    assert elapsed < 0.3, f"cancel took {elapsed:.3f}s to return"
    assert result["out"]["ok"] is False
    assert result["out"]["code"] == "VOICE_EDIT_ASK_CANCELLED"
    assert calls == [1], "the turn must run exactly once (no retry/double-run)"
    # The tombstone outlives the handler return, so a late reply's speech is
    # still refused by the /api/audio/speak gate.
    assert svc.ask_turn_is_stopped(rid) is True
    # Let the abandoned worker finish: it must observe the event, discard the
    # reply, skip the fallback, and leave no thread behind.
    release.set()
    assert finished.wait(timeout=2.0)
    deadline = time.time() + 2.0
    while svc._ask_workers and time.time() < deadline:
        time.sleep(0.01)
    assert svc._ask_workers == set(), "the cancelled worker leaked a thread"
    assert remembered == [], "a stopped turn persisted a late reply"
    assert fallback == [], "no fallback after a stop"


def test_cancelled_turn_tombstone_is_pruned_by_the_request_ttl(monkeypatch):
    monkeypatch.setattr(GW, "run_role", lambda *a, **k: R(text="ok"))
    svc = _svc(monkeypatch)
    rid = "rid-tombstone"
    svc.cancel(rid)
    assert svc.ask_turn_is_stopped(rid) is True
    svc._ask_cancel_at[rid] = time.monotonic() - svc.ttl() - 1.0
    svc._prune()
    assert svc.ask_turn_is_stopped(rid) is False


def test_normal_keyed_turn_leaves_no_worker_behind(monkeypatch):
    monkeypatch.setattr(GW, "run_role", lambda *a, **k: R(text="the answer"))
    svc = _svc(monkeypatch)
    out = svc.ask("tell me a joke", request_id="rid-noworker")
    assert out["ok"] is True and out["answer"] == "the answer"
    deadline = time.time() + 2.0
    while svc._ask_workers and time.time() < deadline:
        time.sleep(0.01)
    assert svc._ask_workers == set()
    assert "rid-noworker" not in svc._ask_cancels


def test_stop_during_brain_skips_the_full_turn_fallback(monkeypatch):
    called = []
    rid = "rid-skip"

    def fake_run_role(prompt, cfg, **kw):
        # The stop arrives and the brain then refuses: the second-chance
        # full turn must NOT run for a cancelled turn.
        svc.cancel(rid)
        return R(text="I can't help with that.")

    monkeypatch.setattr(GW, "run_role", fake_run_role)
    monkeypatch.setattr(CT, "turn", lambda *a, **k: called.append(1))
    svc = _svc(monkeypatch)
    out = svc.ask("tell me a joke", request_id=rid)
    assert out["code"] == "VOICE_EDIT_ASK_CANCELLED"
    assert called == [], "no fallback agent turn after a stop"


# ---------------------------------------------------------------------------
# SERVER: full-turn agent branch is cancellable through chat_transport
# ---------------------------------------------------------------------------
def test_full_turn_agent_branch_is_cancellable(monkeypatch):
    # Brain refuses -> ask falls back to the full agent turn.
    monkeypatch.setattr(GW, "run_role",
                        lambda *a, **k: R(text="I can't help with that."))
    svc = _svc(monkeypatch)
    rid = "rid-full"
    seen = {}

    def fake_turn(cfg, text, **kw):
        seen["cancel"] = kw.get("cancel")
        seen["text"] = text
        svc.cancel(rid)  # orb STOP lands mid agent turn
        if kw.get("cancel") and kw["cancel"]():
            return {"ok": False, "state": "FAILED", "code": "CANCELLED"}
        return {"ok": True, "reply": "should never be returned"}

    monkeypatch.setattr(CT, "turn", fake_turn)
    out = svc.ask("tell me a joke", request_id=rid)
    assert out["code"] == "VOICE_EDIT_ASK_CANCELLED"
    assert callable(seen["cancel"])
    assert seen["cancel"]() is True


def test_full_turn_cancel_returns_promptly_when_the_agent_ignores_cancel(
        monkeypatch):
    """Same prompt return for the full agent turn (the 120 s branch)."""
    monkeypatch.setattr(GW, "run_role",
                        lambda *a, **k: R(text="I can't help with that."))
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    turns = []

    def stubborn_turn(cfg, text, **kw):
        turns.append(text)
        entered.set()
        release.wait(timeout=5.0)  # ignores kw["cancel"] on purpose
        finished.set()
        return {"ok": True, "reply": "a late agent reply"}

    monkeypatch.setattr(CT, "turn", stubborn_turn)
    svc = _svc(monkeypatch)
    rid = "rid-stubborn-full"
    result = {}
    t = threading.Thread(
        target=lambda: result.update(
            out=svc.ask("do the thing", request_id=rid)))
    t.start()
    assert entered.wait(timeout=3.0), "agent turn never started"
    t0 = time.monotonic()
    svc.cancel(rid)
    t.join(timeout=1.0)
    elapsed = time.monotonic() - t0
    assert not t.is_alive(), "the /ask handler waited for the agent turn"
    assert elapsed < 0.3, f"cancel took {elapsed:.3f}s to return"
    assert result["out"]["code"] == "VOICE_EDIT_ASK_CANCELLED"
    assert turns == ["do the thing"], "the agent turn ran exactly once"
    release.set()
    assert finished.wait(timeout=2.0)
    deadline = time.time() + 2.0
    while svc._ask_workers and time.time() < deadline:
        time.sleep(0.01)
    assert svc._ask_workers == set()


def test_full_turn_agent_branch_passes_cancel_to_transport(monkeypatch):
    monkeypatch.setattr(GW, "run_role",
                        lambda *a, **k: R(text="I can't help with that."))
    svc = _svc(monkeypatch)
    seen = {}

    def fake_turn(cfg, text, **kw):
        seen.update(kw)
        return {"ok": False, "code": "CANCELLED"}

    monkeypatch.setattr(CT, "turn", fake_turn)
    out = svc.ask("tell me a joke", request_id="rid-pass")
    assert out["code"] == "VOICE_EDIT_ASK_CANCELLED"
    assert callable(seen.get("cancel")), "transport must receive a cancel hook"


# ---------------------------------------------------------------------------
# SERVER: a normal turn is unaffected (byte-compatible behavior)
# ---------------------------------------------------------------------------
def test_normal_turn_unaffected(monkeypatch):
    calls = []

    def fake_run_role(prompt, cfg, **kw):
        calls.append(kw)
        return R(text="the answer")

    monkeypatch.setattr(GW, "run_role", fake_run_role)
    svc = _svc(monkeypatch)
    out = svc.ask("tell me a joke", request_id="rid-ok")
    assert out["ok"] is True and out["answer"] == "the answer"
    # Hooks are wired for every turn but never fire when nothing cancelled.
    assert calls[0]["cancel"]() is False
    assert calls[0]["should_stop"]() is False


def test_turn_cancel_event_is_released_after_the_turn(monkeypatch):
    monkeypatch.setattr(GW, "run_role", lambda *a, **k: R(text="ok"))
    svc = _svc(monkeypatch)
    svc.ask("tell me a joke", request_id="rid-clean")
    assert "rid-clean" not in svc._ask_cancels
    assert "rid-clean" not in svc._ask_cancel_at


def test_empty_request_id_stays_cancellable_noop(monkeypatch):
    monkeypatch.setattr(GW, "run_role", lambda *a, **k: R(text="ok"))
    svc = _svc(monkeypatch)
    out = svc.ask("tell me a joke")  # no request_id
    assert out["ok"] is True
    assert svc._ask_cancels == {}


# ---------------------------------------------------------------------------
# HELPER: stop asks the service to cancel the right request id
# ---------------------------------------------------------------------------
class FakeOverlay:
    def set_status(self, state, detail=""):
        pass

    def show_preview(self, payload):
        pass

    def show_error(self, code, detail):
        pass

    def append_history(self, line):
        pass

    def show(self):
        pass


class CancelClient:
    """Fake helper client that records service-cancel calls."""

    def __init__(self, *, raises=False):
        self.calls = []
        self.done = threading.Event()
        self._raises = raises

    def cancel(self, rid):
        if self._raises:
            raise OSError("service gone")
        self.calls.append(rid)
        self.done.set()
        return {"ok": True}


def _helper(client):
    h = dh.DesktopHelper(_cfg(), client=client, overlay=FakeOverlay(),
                         desktop=FakeDesktop(),
                         mic_factory=lambda: None)
    h._warm_event.set()
    h._warm_state = "skipped"
    return h


def test_stop_requests_service_cancel_for_request_id():
    client = CancelClient()
    h = _helper(client)
    h.request_id = "rid-1"
    h._turn_seq = 3
    h.state = h.PROCESSING
    assert h.stop_speaking() is True
    assert client.done.wait(timeout=2.0), "service cancel never dispatched"
    assert client.calls == ["rid-1"]


def test_idle_stop_does_not_ask_service_to_cancel():
    client = CancelClient()
    h = _helper(client)
    h.request_id = "rid-idle"
    h._turn_seq = 1
    h.state = h.IDLE
    assert h.stop_speaking() is False
    time.sleep(0.1)
    assert client.calls == []


def test_service_cancel_requested_at_most_once_per_turn():
    client = CancelClient()
    h = _helper(client)
    h.request_id = "rid-once"
    h._turn_seq = 7
    h.state = h.PROCESSING
    h._request_service_cancel()
    h._request_service_cancel()
    assert client.done.wait(timeout=2.0)
    time.sleep(0.1)
    assert client.calls == ["rid-once"], "one stop per turn, no ping-pong"


def test_service_cancel_new_turn_after_stop_still_asks():
    client = CancelClient()
    h = _helper(client)
    h.request_id = "rid-a"
    h._turn_seq = 1
    h.state = h.PROCESSING
    h._request_service_cancel()
    assert client.done.wait(timeout=2.0)
    # A fresh turn is a new epoch: the guard must not swallow its stop.
    h.request_id = "rid-b"
    h._turn_seq = 2
    h._request_service_cancel()
    deadline = time.time() + 2.0
    while time.time() < deadline and client.calls != ["rid-a", "rid-b"]:
        time.sleep(0.005)
    assert client.calls == ["rid-a", "rid-b"]


def test_service_cancel_error_never_raises():
    client = CancelClient(raises=True)
    h = _helper(client)
    h.request_id = "rid-boom"
    h._turn_seq = 1
    h.state = h.PROCESSING
    # Must not raise even though the client cancel blows up (daemon thread).
    assert h.stop_speaking() is True
    # Direct dispatch path is fail-soft too.
    dh.DesktopHelper._dispatch_service_cancel(
        lambda rid: (_ for _ in ()).throw(OSError("x")), "rid-boom")


def test_on_orb_stop_is_signal_handler_safe():
    client = CancelClient()
    h = _helper(client)
    h.request_id = "rid-sig"
    h._turn_seq = 4
    h.state = h.PROCESSING
    h.on_orb_stop()  # must never raise from the (main-thread) handler
    assert client.done.wait(timeout=2.0)
    assert client.calls == ["rid-sig"]


# ---------------------------------------------------------------------------
# HELPER: a typed cancelled response closes the FSM cleanly (no error)
# ---------------------------------------------------------------------------
class CancelledTurnClient:
    """Routes to an ``answer`` turn whose ``ask_result`` is cancelled."""

    def __init__(self):
        self.cancel_calls = []

    def status(self):
        return {"enabled": True, "estop": False}

    def begin(self):
        return {"ok": True, "request_id": "req-1"}

    def route(self, rid, text):
        return {"ok": True, "mode": "answer", "question": text}

    def ask_result(self, question, request_id):
        raise dh.HelperError("VOICE_EDIT_ASK_CANCELLED",
                             "the turn was cancelled")

    def cancel(self, rid):
        self.cancel_calls.append(rid)
        return {"ok": True}


def test_cancelled_ask_closes_fsm_with_stop_triple_not_error():
    client = CancelledTurnClient()
    h = _helper(client)
    h._process_transcript("what is the time")
    types = [e.get("type") for e in h.talk_events()]
    assert "interrupt" in types
    assert "playback.stopped" in types
    assert "response.done" in types
    assert "error" not in types, "a stop is not an error"
    assert types.count("response.done") == 1, "the stop triple emits once"
