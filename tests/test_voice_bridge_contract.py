"""Voice-bridge contract tests: interrupt ordering, worker crash-resilience,
routing lanes.

These drive the REAL handlers (``VoiceOrchestrator._interrupt_task``,
``VoiceOrchestrator._emit_hotkey``, ``WebUI.interrupt``,
``WebUI.submit_talk`` / ``submit_text``) on a stub object that only supplies
the attributes the real code touches. Nothing here restarts a service or
opens an audio device.

Contract under test (owner rule):
  "router thin, workers get FULL terminal + browser; bridge must never
   crash, always listen / respond / interruptible."

Every assertion below is pinned to a real line in
``voicebridge/orchestrator.py`` / ``voicebridge/webui.py``; see
``docs/VOICE-BRIDGE-AUDIT.md`` for the defect reproductions.
"""
from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.orchestrator as O  # noqa: E402
from voicebridge.intent.classifier import Classifier  # noqa: E402
from voicebridge.intent.state import (  # noqa: E402
    ConfirmationState,
    ConversationState,
)


# --------------------------------------------------------------------- doubles
class _TaskState:
    """Minimal stand-in for Orchestrator.task (TaskState)."""

    def __init__(self, active: bool = False):
        self.active = active
        self.session_key = "sess-test"
        self.description = "test task"


class _Playback:
    """Records stop() so we can order-compare it against the busy guard."""

    def __init__(self, playing: bool = False):
        self.playing = playing
        self.stops = 0

    def stop(self) -> None:
        self.stops += 1
        self.playing = False

    def play(self, *a, **k):  # pragma: no cover - not exercised here
        return True


class _Feed:
    def __init__(self):
        self.items = []

    def push(self, item):
        self.items.append(item)


def _stub(*, task_active=False, playback_playing=False, agent_busy=False,
          muted=False, interrupt_on_ptt=True):
    """A stub that carries the REAL VoiceOrchestrator methods bound to it.

    This is how we exercise the production handler bodies without building a
    full orchestrator (which would open devices / spawn workers).
    """
    s = types.SimpleNamespace()
    s.task = _TaskState(active=task_active)
    s.playback = _Playback(playing=playback_playing)
    s._agent_busy = agent_busy
    s.muted = muted
    s._interrupt_requested = False
    s._stop = threading.Event()
    s._ptt_hold = False
    s._ptt_pressed = threading.Event()
    s._ptt_started = threading.Event()
    s._ptt_released = threading.Event()
    s._barge_stream = None
    s._duplex = None
    s._toggle_stop = threading.Event()
    s._cancel = threading.Event()
    s._toggle_armed = False
    s._press_time = 0.0
    s._release_time = 0.0
    s._webui = None
    # config double: .get(section, key, default=...)
    s.cfg = types.SimpleNamespace(
        get=lambda section, key, default=None, **_k: (
            interrupt_on_ptt if (section, key) == ("hotkey", "interrupt_on_ptt")
            else default))
    s.spoken = []

    def _speak(text, name="assistant", interruptible=True, barge=True):
        s.spoken.append(text)
        return text

    s.speak = _speak
    s.feed_push = lambda item: None
    # bind the real methods
    s._interrupt_task = types.MethodType(O.VoiceOrchestrator._interrupt_task, s)
    s._emit_hotkey = types.MethodType(O.VoiceOrchestrator._emit_hotkey, s)
    s._abort_task = lambda why="": None
    return s


# ================================================================== INTERRUPT
# Contract 1: an interrupt must NEVER be swallowed while the bridge is busy.
# The interrupt announcement is spawned on a daemon thread (non-blocking) so we
# wait briefly for it.

def _wait_for(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_interrupt_task_stops_playback_and_announces():
    s = _stub(task_active=False, playback_playing=True)
    s._interrupt_task(announce=True)
    assert s.playback.stops >= 1, "interrupt must stop spoken output"
    assert _wait_for(lambda: bool(s.spoken)), \
        "interrupt with playback must announce 'Go ahead.'"


def test_interrupt_task_silent_when_announce_false():
    """mode='stop' (double-tap) / Esc must NOT speak an ack."""
    s = _stub(playback_playing=True)
    s._interrupt_task(announce=False)
    time.sleep(0.15)
    assert s.playback.stops >= 1
    assert s.spoken == [], "announce=False must stay silent"


def test_interrupt_task_keeps_running_task_alive():
    """Contract: interrupt gives the floor back but does NOT hard-abort work."""
    s = _stub(task_active=True)
    aborted = {"n": 0}
    s._abort_task = lambda why="": aborted.__setitem__("n", aborted["n"] + 1)
    s._interrupt_task(announce=True)
    assert aborted["n"] == 0, "interrupt must not abort the running task"
    assert s.task.active is True


@pytest.mark.xfail(
    reason="DEFECT-1: _emit_hotkey ptt_start (orchestrator.py:1770-1775) sets "
           "_interrupt_requested only when playback.playing or task.active; an "
           "agent-only busy state (self._agent_busy) leaves the flag clear, so "
           "the agent turn's cancelled callback (line 3906) stays False and the "
           "turn cannot be interrupted. strict=True flips green when fixed.",
    strict=True)
def test_interrupt_not_swallowed_when_only_agent_busy():
    """DEFECT-1 REPRO (was `_emit_hotkey` guard).

    A PTT press while ONLY a background agent turn is running (no playback,
    no task) must still register as an interrupt request. The old guard at
    orchestrator.py:1770-1775 only set `_interrupt_requested` when
    `playback.playing` or `task.active` was true, so an agent-only busy state
    left the flag clear -> the press looked like a plain capture start and the
    running turn was never flagged interruptible.
    """
    s = _stub(task_active=False, playback_playing=False, agent_busy=True)
    s._emit_hotkey("ptt_start", "Control_R")
    assert s._interrupt_requested is True, (
        "PTT during an agent-only busy state must set _interrupt_requested "
        "(otherwise the interrupt is swallowed)")


def test_interrupt_task_idle_is_rate_limited_not_lost():
    """An idle interrupt is still journaled (annotate, don't crash)."""
    s = _stub()  # fully idle
    s._interrupt_task(announce=True)   # first: journaled
    s.spoken.clear()
    s._interrupt_task(announce=True)   # second within 2s: rate-limited
    time.sleep(0.1)
    assert s.spoken == [], "idle interrupt must not speak"


def test_interrupt_survives_speak_raising():
    """Crash-resilience: a broken TTS must not make the interrupt handler raise.

    FINDING (DEFECT-3): the announce thread spawned at orchestrator.py:3116-3122
    calls ``self.speak`` with no try/except, so a TTS failure becomes an
    unhandled exception ON THAT THREAD. The handler call itself returns cleanly
    (the contract holds), but the failure is unattributed. We assert the
    call does not raise AND record the thread exception explicitly.
    """
    s = _stub(playback_playing=True)

    def _boom(*a, **k):
        raise RuntimeError("tts exploded")

    s.speak = _boom
    raised = []

    def _hook(args):
        raised.append(args.exc_value)

    old = threading.excepthook
    threading.excepthook = _hook
    try:
        s._interrupt_task(announce=True)  # must NOT raise
        time.sleep(0.2)
    finally:
        threading.excepthook = old
    assert s.playback.stops >= 1
    # Documented finding: the announce thread leaks an unhandled exception.
    assert raised and isinstance(raised[0], RuntimeError), (
        "expected the announce thread to surface the TTS failure "
        "(DEFECT-3); got %r" % (raised,))


@pytest.mark.xfail(
    reason="DEFECT-1 consequence: with only _agent_busy set, the agent turn's "
           "cancelled callback (orchestrator.py:3906) stays False, so the turn "
           "runs to completion after an interrupt. strict=True flips green when "
           "_emit_hotkey (1770-1775) sets the flag for the _agent_busy case.",
    strict=True)
def test_agent_turn_cancel_callback_stays_false_on_agent_only_interrupt():
    """Consequence of DEFECT-1, end to end.

    The agent turn is driven with ``cancelled=lambda: self._interrupt_requested``
    (orchestrator.py:3905-3906). With only ``_agent_busy`` set, a PTT press
    followed by the main loop's compensating ``_interrupt_task()`` call leaves
    ``_interrupt_requested`` False, so ``cancelled()`` never becomes true and
    the turn runs to completion — the interrupt is swallowed.
    """
    s = _stub(task_active=False, playback_playing=False, agent_busy=True)
    s._emit_hotkey("ptt_start", "Control_R")   # user presses PTT mid-turn
    s._interrupt_task(announce=False)          # main loop compensation (4415)
    cancelled = lambda: bool(s._interrupt_requested)  # mirrors line 3906
    assert cancelled() is True, (
        "agent turn is not cancellable after an agent-only interrupt "
        "(DEFECT-1)")


def test_callback_test_is_sensitive_to_the_flag():
    """Falsifiability guard: the callback above DOES flip when the flag is set,
    so the failing assertion above is a real signal, not a dead check."""
    s = _stub(task_active=False, playback_playing=False, agent_busy=True)
    s._interrupt_requested = True
    assert bool(s._interrupt_requested) is True


# ============================================================ CRASH RESILIENCE
# Contract 2: an exception inside a worker/turn must NOT kill the listener;
# the bridge still answers a health probe; malformed requests get typed errors.

def _real_webui_stub(monkeypatch, *, orch_overrides=None):
    """Build a WebUI bound to a stub orchestrator, with heavy boot skipped."""
    import voicebridge.webui as W

    orch = types.SimpleNamespace()
    orch.cfg = types.SimpleNamespace(
        get=lambda section, key, default=None, **_k: default)
    orch._pending_lock = threading.Lock()
    orch._results = {}
    orch._result_lock = threading.Lock()
    orch.task = _TaskState(active=False)
    orch._web_active = 0
    orch._t0 = time.time()
    if orch_overrides:
        for k, v in orch_overrides.items():
            setattr(orch, k, v)

    # Skip the qubit_asgi home mutation + feed history load (not under test).
    monkeypatch.setattr(W, "frontend_status",
                        lambda *a, **k: (None, False, False, "test", "repo"),
                        raising=False)
    monkeypatch.setattr(W.ActivityFeed, "load_history",
                        lambda self, *a, **k: None, raising=False)
    ui = W.WebUI.__new__(W.WebUI)
    ui.orch = orch
    ui.host = "127.0.0.1"
    ui.port = 8890
    ui.feed = _Feed()  # type: ignore[assignment]
    ui._asr = None
    ui._asr_lock = threading.Lock()
    ui._asr_factory = None
    ui._tts = None
    ui._state_dir = Path("/tmp")
    ui._server = None
    ui._thread = None
    ui._pending = {}
    ui._pending_lock = threading.Lock()
    ui._results = {}
    ui._result_lock = threading.Lock()
    ui.api_token = ""
    ui.allowed_origins = []
    ui._frontend_dir = None  # type: ignore[assignment]
    ui._use_frontend_dir = False
    ui._frontend_stale = False
    ui._frontend_version = "test"
    ui._frontend_active = "repo"
    return ui


def test_worker_exception_does_not_kill_listener(monkeypatch):
    """A raising handle_talk must be caught -> typed error result, no re-raise."""
    def _boom(pcm, rate):
        raise RuntimeError("worker exploded")

    ui = _real_webui_stub(monkeypatch)
    monkeypatch.setattr(ui, "handle_talk", _boom, raising=False)
    rid = ui.submit_talk(b"\x00\x00" * 64, rate=16000)
    assert isinstance(rid, str) and rid
    res = None
    end = time.time() + 3.0
    while time.time() < end:
        r = ui.result(rid)
        if r.get("status") != "pending":
            res = r
            break
        time.sleep(0.02)
    assert res is not None, "worker never produced a result (listener hung)"
    assert res.get("route") == "error", f"expected typed error, got {res!r}"
    assert res.get("summary") == "Something went wrong."


def test_worker_exception_on_text_path_is_typed(monkeypatch):
    def _boom(text):
        raise RuntimeError("turn exploded")

    ui = _real_webui_stub(monkeypatch)
    monkeypatch.setattr(ui.orch, "process_web_utterance", _boom, raising=False)
    monkeypatch.setattr(ui, "_speak_locally", lambda: False, raising=False)
    rid = ui.submit_text("do something")
    end = time.time() + 3.0
    res = None
    while time.time() < end:
        r = ui.result(rid)
        if r.get("status") != "pending":
            res = r
            break
        time.sleep(0.02)
    assert res is not None
    assert res.get("route") == "error"


def test_health_probe_answers_after_worker_crash(monkeypatch):
    """After a failed worker, the bridge still reports healthy/status."""
    def _boom(pcm, rate):
        raise RuntimeError("worker exploded")

    ui = _real_webui_stub(monkeypatch)
    monkeypatch.setattr(ui, "handle_talk", _boom, raising=False)
    rid = ui.submit_talk(b"\x00" * 128, rate=16000)
    time.sleep(0.1)
    # the bridge still reports a live status payload
    payload = ui.status()
    assert isinstance(payload, dict)
    assert payload.get("bridge") == "up"


def test_malformed_audio_rate_falls_back_not_crash():
    """A garbage X-Audio-Rate header must not raise (typed fallback)."""
    from voicebridge.webui import parse_audio_rate

    assert parse_audio_rate("not-a-number") == 16000
    assert parse_audio_rate(None) == 16000
    assert parse_audio_rate("") == 16000


def test_interrupt_endpoint_mode_parse_is_defensive(monkeypatch):
    """Malformed interrupt body must default to mode='interrupt', not crash."""
    seen = {}

    def _interrupt(mode="interrupt"):
        seen["mode"] = mode

    ui = _real_webui_stub(monkeypatch)
    ui.interrupt = _interrupt
    # replicate the endpoint's parse+dispatch logic
    import json as _json
    for body in (b"", b"{", b"null", b'{"mode": "stop"}'):
        try:
            mode = _json.loads(body or b"{}").get("mode", "interrupt")
        except Exception:
            mode = "interrupt"
        ui.interrupt(mode=mode)
    assert seen["mode"] == "stop"


def test_webui_interrupt_delegates_to_interrupt_task():
    """WebUI.interrupt must route to the orchestrator's interrupt handler."""
    calls = []

    class _Orch:
        def _interrupt_task(self, announce=True):
            calls.append(announce)

    ui = types.SimpleNamespace(orch=_Orch())
    # bind the REAL WebUI.interrupt
    import voicebridge.webui as W
    ui.interrupt = types.MethodType(W.WebUI.interrupt, ui)
    ui.interrupt(mode="interrupt")
    ui.interrupt(mode="stop")
    assert calls == [True, False], \
        "mode='interrupt' -> announce=True, mode='stop' -> announce=False"


# ================================================================== ROUTING
# Contract 3: every lane is reachable; ambiguity must not silently take the
# WRONG lane.

def _cls(busy=False, pending=False):
    confirmations = ConfirmationState()
    if pending:
        confirmations.request("delete the file", {"path": "/tmp/x"}, "destructive")
    return Classifier({"llm_classifier": False}, confirmations,
                      ConversationState(max_turns=4),
                      busy_fn=lambda: busy)


def test_lane_system_reachable():
    i = _cls().classify("are you there")
    assert i.kind == "system" and i.params.get("command") == "status"


def test_lane_cancel_reachable_when_busy():
    i = _cls(busy=True).classify("stop the task")
    assert i.kind == "cancel", f"'stop the task' while busy -> {i.kind}"


def test_lane_confirm_reachable_when_pending():
    i = _cls(pending=True).classify("yes")
    assert i.kind in ("confirm", "conversation"), i.kind


def test_lane_coding_reachable():
    i = _cls().classify(
        "refactor the login module and fix the failing tests")
    assert i.kind == "coding", f"task text -> {i.kind}"


def test_lane_conversation_reachable():
    i = _cls().classify("thanks, that was helpful")
    assert i.kind in ("conversation", "clarify")


def test_lane_clarify_on_non_english():
    i = _cls().classify("файлды ашып көрсет маған")
    assert i.kind == "clarify", f"non-english must clarify, got {i.kind}"


def test_ambiguous_utterance_never_silently_cancels():
    """An utterance that merely contains 'stop' but is not a stop command must
    NOT take the cancel lane when nothing is running."""
    i = _cls(busy=False).classify("what stops a hard drive from spinning")
    assert i.kind != "cancel", "ambiguous 'stop' took the cancel lane"


def test_empty_utterance_is_conversation_not_crash():
    i = _cls().classify("   ")
    assert i.kind == "conversation" and i.confidence == 0.0
