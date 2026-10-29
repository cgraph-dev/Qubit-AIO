"""Pure per-session voice turn / confirmation state machine.

The desktop helper used to hold a **single** ``_pending_task`` dict: any new
input superseded it, so a media/own-voice utterance that tripped the wake
spotter executed as a command and silently replaced the real staged task -- the
later "yes" then ran the wrong action. The fix is to scope a staged
confirmation to its **session** and to make "anything else" a *non-consuming*
transition: unrecognised input leaves the pending action staged so the caller,
not the FSM, decides.

This module is the state half of that redesign. It is **pure**: no I/O, no
threads, no global state, an injectable clock (``now``), and it **never raises**
-- malformed input degrades to the documented safe default rather than taking
the voice turn down.

Vocabulary mirrors the talk/orb FSMs
(:mod:`voicebridge.voice_edit.talk_fsm`, :mod:`voicebridge.orb.state`): the
per-turn states are :data:`STATES`, and the confirmation classifier ports the
exact ``_affirmative`` / ``_negative`` word lists from
``voicebridge.desktop_helper`` (same normalization: lower-case, strip
punctuation, drop everything but ``[a-z0-9 ]``) so a politely-phrased "Yes,
please." confirms just as it does live.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

#: Per-turn states. ``awaiting_confirm`` is the one that owns a pending action;
#: ``executing`` is entered on affirm, ``idle`` on deny/expire.
STATES = (
    "idle",
    "listening",
    "transcribing",
    "routing",
    "awaiting_confirm",
    "executing",
    "speaking",
    "error",
)

#: The confirming word list, ported verbatim from ``desktop_helper._affirmative``
#: (already normalized: lower-case, punctuation stripped, ``'`` removed).
_AFFIRMATIVE = frozenset({
    "yes", "yeah", "yep", "yup", "confirm", "confirmed", "do it",
    "go ahead", "run it", "ok", "okay", "sure", "proceed", "yes please",
    "confirm task", "run the task",
})

#: The denying word list, ported verbatim from ``desktop_helper._negative``.
_NEGATIVE = frozenset({
    "no", "nope", "nah", "cancel", "stop", "dont", "never mind",
    "nevermind", "abort", "no thanks",
})

# ASR often repeats a confirmation ("Yeah, yes.", "Yes, yes.") or pads it
# ("yes please do it"). Accept a short all-affirmative token sequence, while
# still refusing an unrelated sentence that merely starts with "yes".
_AFFIRMATIVE_CORE = frozenset({
    "yes", "yeah", "yep", "yup", "confirm", "confirmed", "ok", "okay",
    "sure", "proceed",
})
_AFFIRMATIVE_FILLER = frozenset({
    "yes", "yeah", "yep", "yup", "confirm", "confirmed", "ok", "okay",
    "sure", "proceed", "please", "do", "it", "go", "ahead", "run", "the",
    "task", "now", "and",
})
_NEGATIVE_CORE = frozenset({
    "no", "nope", "nah", "cancel", "stop", "dont", "nevermind", "abort",
})
_NEGATIVE_FILLER = frozenset({
    "no", "nope", "nah", "cancel", "stop", "dont", "nevermind", "abort",
    "thanks", "thank", "you", "please",
})

#: Fallback session name for a missing/blank session id (fail-soft, deterministic).
DEFAULT_SESSION = "default"


def _core(text: Any) -> str:
    """Normalize an utterance to the classifier's alphanumeric core.

    Mirrors ``desktop_helper._affirmative``/``_negative``: strip surrounding
    punctuation, lower-case, then keep only ``[a-z0-9 ]`` (so "Yes, please."
    and "don't" normalize to "yes please" / "dont"). Never raises.
    """
    try:
        low = str(text or "").strip().lower().strip(" .,!?:;")
    except Exception:
        return ""
    try:
        return re.sub(r"[^a-z0-9 ]", "", low)
    except Exception:
        return ""


def classify_confirmation(text: Any) -> str:
    """Classify an utterance as ``"affirm"`` / ``"deny"`` / ``"other"``.

    Uses exactly the ``_affirmative``/``_negative`` semantics and word lists
    (affirm checked first, as live). Junk/empty input -> ``"other"``. Never
    raises.
    """
    low = _core(text)
    if low in _AFFIRMATIVE:
        return "affirm"
    if low in _NEGATIVE:
        return "deny"
    tokens = low.split()
    if tokens:
        if all(t in _AFFIRMATIVE_FILLER for t in tokens) and any(
                t in _AFFIRMATIVE_CORE for t in tokens):
            return "affirm"
        if all(t in _NEGATIVE_FILLER for t in tokens) and any(
                t in _NEGATIVE_CORE for t in tokens):
            return "deny"
    return "other"


@dataclass(frozen=True)
class PendingAction:
    """An actionable turn staged and awaiting an explicit confirmation.

    Frozen so a staged action cannot be mutated out from under a session; the
    TTL is captured at staging time so a later default change cannot retro-
    actively expire (or resurrect) an already-staged action.
    """

    text: str
    request_id: str
    session: str
    staged_at: float
    ttl_s: float
    kind: str = "task"


def _session_key(session: Any) -> str:
    """Normalize a session id; missing/blank -> ``"default"``. Never raises."""
    try:
        key = str(session or "").strip()
    except Exception:
        key = ""
    return key or DEFAULT_SESSION


class SessionFSM:
    """One session's turn + confirmation state.

    Pure and deterministic: the only time source is the injected ``now``
    callable (defaults to :func:`time.monotonic`). Every method is total -- no
    input can raise out of it.
    """

    def __init__(self, session: Any = DEFAULT_SESSION, *,
                 now: Optional[Callable[[], float]] = None,
                 ttl_s: float = 120.0) -> None:
        self.session = _session_key(session)
        self._now: Callable[[], float] = now or time.monotonic
        try:
            self.ttl_s = float(ttl_s)
        except (TypeError, ValueError):
            self.ttl_s = 120.0
        self.state: str = "idle"
        self._pending: Optional[PendingAction] = None

    # -- clock -------------------------------------------------------------
    def _read_now(self) -> float:
        """Read the injected clock, tolerating a broken seam. Never raises."""
        try:
            return float(self._now())
        except Exception:
            return 0.0

    # -- state -------------------------------------------------------------
    def set_state(self, state: Any) -> str:
        """Set the turn state. An unknown value leaves the state unchanged."""
        try:
            key = str(state or "").strip().lower()
        except Exception:
            key = ""
        if key in STATES:
            self.state = key
        return self.state

    def begin_listen(self) -> str:
        """Enter ``listening`` (mic capture opened for this session)."""
        return self.set_state("listening")

    # -- confirmation lifecycle -------------------------------------------
    def propose(self, text: Any, request_id: Any = "",
                kind: str = "task") -> PendingAction:
        """Stage ``text`` for confirmation and enter ``awaiting_confirm``.

        An explicit new proposal supersedes any previous pending action (the
        caller asked to stage this one); it is arbitrary *input* that must
        never silently discard a staged action -- see :meth:`resolve`.
        """
        try:
            body = str(text or "")
        except Exception:
            body = ""
        try:
            rid = str(request_id or "")
        except Exception:
            rid = ""
        self._pending = PendingAction(
            text=body,
            request_id=rid,
            session=self.session,
            staged_at=self._read_now(),
            ttl_s=self.ttl_s,
            kind=str(kind or "task"),
        )
        self.state = "awaiting_confirm"
        return self._pending

    def pending(self) -> Optional[PendingAction]:
        """The staged action (even if expired -- see :meth:`expired`), else None."""
        return self._pending

    def is_awaiting_confirm(self) -> bool:
        """True while a staged action is genuinely awaiting confirmation."""
        return self._pending is not None and self.state == "awaiting_confirm"

    def expired(self) -> bool:
        """True only when a staged action exists and its age exceeds its TTL."""
        action = self._pending
        if action is None:
            return False
        age = self._read_now() - action.staged_at
        if age < 0:
            # Clock stepped back: treat as fresh rather than expired.
            return False
        try:
            return age > float(action.ttl_s)
        except (TypeError, ValueError):
            return False

    def clear(self) -> None:
        """Drop any staged action without changing the turn state."""
        self._pending = None

    def resolve(self, text: Any) -> Tuple[str, Optional[PendingAction]]:
        """Resolve the next utterance against the staged action.

        Returns ``(outcome, action)`` where ``outcome`` is one of:

        - ``"run"``      -- affirmed; pending cleared, state -> ``executing``.
        - ``"cancel"``   -- denied; pending cleared, state -> ``idle``.
        - ``"expired"``  -- TTL elapsed; pending cleared, state -> ``idle``.
        - ``"other"``    -- anything else; **pending and state are preserved**
          (the core fix: new/unrelated input never silently discards a staged
          confirmation -- the caller decides what to do).

        With nothing staged, returns ``("other", None)``. Expiry is checked
        first so a stale "yes" can never run a task: recency binds intent.
        Never raises.
        """
        action = self._pending
        if action is None:
            return ("other", None)
        if self.expired():
            self._pending = None
            self.state = "idle"
            return ("expired", action)
        verdict = classify_confirmation(text)
        if verdict == "affirm":
            self._pending = None
            self.state = "executing"
            return ("run", action)
        if verdict == "deny":
            self._pending = None
            self.state = "idle"
            return ("cancel", action)
        # Unrecognised input: preserve the staged action AND the state.
        return ("other", action)


class SessionRegistry:
    """Lazy per-session :class:`SessionFSM` factory.

    ``fsm(session)`` returns the same instance for the same key; a missing or
    blank session folds to :data:`DEFAULT_SESSION`. Deterministic and total --
    a bad ``session`` value can never raise. The injected clock / TTL are
    propagated to every FSM it creates.
    """

    def __init__(self, *, now: Optional[Callable[[], float]] = None,
                 ttl_s: float = 120.0) -> None:
        self._now = now or time.monotonic
        self._ttl_s = ttl_s
        self._sessions: Dict[str, SessionFSM] = {}

    def fsm(self, session: Any) -> SessionFSM:
        """Return (creating on first use) the FSM for ``session``."""
        key = _session_key(session)
        fsm = self._sessions.get(key)
        if fsm is None:
            fsm = SessionFSM(key, now=self._now, ttl_s=self._ttl_s)
            self._sessions[key] = fsm
        return fsm


__all__ = [
    "STATES",
    "DEFAULT_SESSION",
    "PendingAction",
    "classify_confirmation",
    "SessionFSM",
    "SessionRegistry",
]
