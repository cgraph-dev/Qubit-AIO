"""Pure per-session voice turn/confirmation FSM (``voicebridge.voice_edit.session_fsm``).

Hermetic: pure logic, no I/O, no threads, an injected clock. Pins the contract
the redesign depends on:

- a proposal stages a session-scoped pending action and enters
  ``awaiting_confirm``;
- ``resolve`` runs on affirm, cancels on deny, expires on TTL, and -- CRITICAL
  -- leaves the pending action (and state) untouched on anything else, so new
  input can never silently overwrite a staged confirmation;
- sessions keep independent pending actions;
- the classifier matches the live ``_affirmative``/``_negative`` semantics;
- nothing raises on junk.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit.session_fsm import (
    DEFAULT_SESSION,
    STATES,
    PendingAction,
    SessionFSM,
    SessionRegistry,
    classify_confirmation,
)


class _Clock:
    """Deterministic monotonic-seconds clock for the injectable ``now`` seam."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> float:
        self.t += seconds
        return self.t


def test_states_vocabulary_is_exact():
    assert STATES == ("idle", "listening", "transcribing", "routing",
                      "awaiting_confirm", "executing", "speaking", "error")


def test_propose_stages_awaits_confirm_and_returns_the_action():
    fsm = SessionFSM("s1")
    assert fsm.state == "idle"
    action = fsm.propose("create a text document", "req-1")
    assert isinstance(action, PendingAction)
    assert action.text == "create a text document"
    assert action.request_id == "req-1"
    assert action.session == "s1"
    assert action.kind == "task"
    assert fsm.state == "awaiting_confirm"
    assert fsm.pending() == action
    assert fsm.is_awaiting_confirm() is True


def test_affirm_runs_clears_and_moves_to_executing():
    fsm = SessionFSM("s1")
    action = fsm.propose("delete the file", "req-2")
    outcome, resolved = fsm.resolve("Yes, please.")
    assert outcome == "run"
    assert resolved == action
    assert fsm.pending() is None
    assert fsm.is_awaiting_confirm() is False
    assert fsm.state == "executing"


def test_deny_cancels_clears_and_returns_to_idle():
    fsm = SessionFSM("s1")
    action = fsm.propose("delete the file", "req-3")
    outcome, resolved = fsm.resolve("no thanks")
    assert outcome == "cancel"
    assert resolved == action
    assert fsm.pending() is None
    assert fsm.state == "idle"


def test_other_preserves_pending_and_state_the_core_fix():
    clock = _Clock()
    fsm = SessionFSM("s1", now=clock)
    action = fsm.propose("send the report", "req-4")
    # An unrelated/media utterance must NOT discard the staged task.
    outcome, resolved = fsm.resolve("what is the weather in Bucharest")
    assert outcome == "other"
    assert resolved == action
    assert fsm.pending() == action
    assert fsm.state == "awaiting_confirm"
    assert fsm.is_awaiting_confirm() is True
    # ...and the real confirmation still runs the ORIGINAL action afterwards.
    outcome2, resolved2 = fsm.resolve("yes")
    assert (outcome2, resolved2) == ("run", action)
    assert fsm.state == "executing"


def test_expired_clears_via_injectable_clock():
    clock = _Clock()
    fsm = SessionFSM("s1", now=clock, ttl_s=120.0)
    action = fsm.propose("run the task", "req-5")
    clock.advance(120.0)
    assert fsm.expired() is False          # exactly at the TTL is still fresh
    clock.advance(0.001)
    assert fsm.expired() is True
    outcome, resolved = fsm.resolve("yes")  # a stale "yes" must not run it
    assert outcome == "expired"
    assert resolved == action
    assert fsm.pending() is None
    assert fsm.state == "idle"


def test_expiry_wins_over_an_affirmative():
    clock = _Clock()
    fsm = SessionFSM("s1", now=clock, ttl_s=10.0)
    action = fsm.propose("destructive thing", "req-6")
    clock.advance(11.0)
    assert fsm.resolve("yes") == ("expired", action)


def test_pending_returns_the_action_even_when_expired():
    clock = _Clock()
    fsm = SessionFSM("s1", now=clock, ttl_s=5.0)
    action = fsm.propose("x", "req-7")
    clock.advance(6.0)
    # pending() is raw; expired() is the separate TTL signal.
    assert fsm.pending() == action
    assert fsm.expired() is True


def test_resolve_with_nothing_staged_is_other_none():
    fsm = SessionFSM("s1")
    assert fsm.resolve("yes") == ("other", None)
    assert fsm.pending() is None
    assert fsm.state == "idle"


def test_two_sessions_keep_independent_pending_actions():
    reg = SessionRegistry()
    clock = _Clock()
    a = SessionFSM("a", now=clock)
    b = SessionFSM("b", now=clock)
    action_a = a.propose("task for A", "req-a")
    action_b = b.propose("task for B", "req-b")
    assert a.pending() == action_a
    assert b.pending() == action_b
    # Resolving A's slot never touches B's.
    assert a.resolve("no") == ("cancel", action_a)
    assert a.pending() is None
    assert b.pending() == action_b
    assert b.state == "awaiting_confirm"


def test_registry_is_deterministic_and_defaults_blank_sessions():
    reg = SessionRegistry()
    assert reg.fsm("s1") is reg.fsm("s1")
    assert reg.fsm("s1") is not reg.fsm("s2")
    # Missing/blank/None -> one shared "default" session.
    assert reg.fsm("").session == DEFAULT_SESSION
    assert reg.fsm(None) is reg.fsm("   ")
    assert reg.fsm("default") is reg.fsm(None)


def test_registry_propagates_clock_and_ttl():
    clock = _Clock()
    reg = SessionRegistry(now=clock, ttl_s=7.0)
    fsm = reg.fsm("s")
    assert fsm.propose("t", "r").ttl_s == 7.0
    clock.advance(8.0)
    assert fsm.expired() is True


def test_set_state_and_begin_listen():
    fsm = SessionFSM("s1")
    assert fsm.begin_listen() == "listening"
    assert fsm.state == "listening"
    assert fsm.set_state("routing") == "routing"
    # Unknown states leave the machine unchanged (fail-soft).
    assert fsm.set_state("bogus") == "routing"
    assert fsm.set_state(None) == "routing"


def test_classify_confirmation_affirmatives():
    for text in ("yes", "Yes.", "YES!", "yeah", "yep", "yup", "confirm",
                 "confirmed", "do it", "go ahead", "run it", "ok", "okay",
                 "sure", "proceed", "yes please", "Yes, please.",
                 "confirm task", "run the task"):
        assert classify_confirmation(text) == "affirm", text


def test_classify_confirmation_negatives():
    for text in ("no", "No.", "nope", "nah", "cancel", "stop", "dont",
                 "don't", "never mind", "nevermind", "abort", "no thanks",
                 "NO, THANKS."):
        assert classify_confirmation(text) == "deny", text


def test_classify_confirmation_other():
    for text in ("", None, "what is the weather", "yes please do it now",
                 "cancel the subscription please", "maybe later", 42, object()):
        assert classify_confirmation(text) == "other", repr(text)


def test_never_raises_on_junk():
    junk = [None, "", 0, 3.14, b"bytes", [], {}, object(), ("a",)]
    for value in junk:
        classify_confirmation(value)
        fsm = SessionFSM(value)
        fsm.set_state(value)
        fsm.begin_listen()
        fsm.propose(value, value, kind=value)
        fsm.resolve(value)
        fsm.expired()
        fsm.pending()
        fsm.is_awaiting_confirm()
        SessionRegistry().fsm(value)


def test_a_broken_clock_seam_never_raises():
    def boom() -> float:
        raise RuntimeError("clock down")

    fsm = SessionFSM("s1", now=boom, ttl_s=1.0)
    action = fsm.propose("x", "r")
    assert action.staged_at == 0.0
    assert fsm.expired() is False
    assert fsm.resolve("yes") == ("run", action)
