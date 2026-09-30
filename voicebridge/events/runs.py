"""Run coordination and snapshot/delta state tracking (Packet 2B).

RunCoordinator: emits RUN_STARTED / terminal events through the hub with a
settle-exactly-once guarantee per run. Terminal idempotency is durable: after
a process restart the coordinator re-checks the journal, so cancelling an
already-cancelled run never emits a second terminal event.

StateTracker: keeps the STATE_SNAPSHOT baseline per run and derives ordered
STATE_DELTA patches (RFC 6902 subset from Packet 2A state.py). A missing or
stale baseline — including a patch that fails to apply — falls back to a
fresh snapshot instead of guessing (plan Phase 2 task 8/10).
"""
from __future__ import annotations

import copy
import logging
import threading
from typing import Any, Callable, Dict, Optional

from .core import (TERMINAL_EVENT_TYPES, EventValidationError, EventType,
                   make_event)
from .hub import EventHub
from .state import PatchError, apply_patch, diff_state


class RunCoordinator:
    def __init__(self, hub: EventHub,
                 abort_hook: Optional[Callable[[str], Any]] = None) -> None:
        self._hub = hub
        self._abort_hook = abort_hook
        self._lock = threading.Lock()
        self._runs: Dict[str, Dict[str, Any]] = {}

    def _terminal_recorded(self, run_id: str) -> bool:
        for rec in self._hub._journal.read_run(run_id):
            if rec.get("type") in {t.value for t in TERMINAL_EVENT_TYPES}:
                return True
        return False

    def start(self, run_id: str, thread_id: str,
              parent_run_id: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            if run_id in self._runs:
                raise EventValidationError(f"run already started: {run_id}")
            self._runs[run_id] = {"thread_id": thread_id}
        ev = make_event(EventType.RUN_STARTED, thread_id=thread_id,
                        run_id=run_id, parent_run_id=parent_run_id)
        return self._hub.publish(ev)

    def cancel(self, run_id: str, reason: Optional[str] = None) -> bool:
        """Request cancellation; True iff THIS call emitted the terminal event.

        Idempotent in-memory AND across restarts: if the journal already holds
        a terminal event for the run, nothing is emitted. The abort hook runs
        only on the winning transition.
        """
        info = self._runs.get(run_id) or {}
        thread_id = info.get("thread_id", "unknown")
        with self._lock:
            prev_terminal = self._terminal_recorded(run_id)
            self._runs[run_id] = info
            if not prev_terminal:
                ev = make_event(EventType.RUN_CANCELLED, thread_id=thread_id,
                                run_id=run_id, reason=reason)
                record = self._hub.publish(ev)
                won = True
            else:
                record = None
                won = False
        if won and self._abort_hook is not None:
            try:
                self._abort_hook(run_id)
            except Exception as exc:  # noqa: BLE001 - hook is best-effort,
                # but the failure is observable, never silent.
                logging.getLogger(__name__).warning(
                    "abort hook failed for %s: %s", run_id, exc)
        return won

    def finish(self, run_id: str, result: Any = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            info = self._runs.get(run_id) or {}
            if self._terminal_recorded(run_id):
                return None
            ev = make_event(EventType.RUN_FINISHED,
                            thread_id=info.get("thread_id", "unknown"),
                            run_id=run_id, result=result)
            return self._hub.publish(ev)

    def fail(self, run_id: str, message: str,
             code: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            if self._terminal_recorded(run_id):
                return None
            ev = make_event(EventType.RUN_ERROR, message=message[:300],
                            code=code, envelope_run_id=run_id)
            return self._hub.publish(ev)


class StateTracker:
    """Per-run snapshot baseline + ordered RFC 6902 deltas."""

    def __init__(self, hub: EventHub) -> None:
        self._hub = hub
        # RLock: update() degrades to snapshot() while holding the lock.
        self._lock = threading.RLock()
        self._base: Dict[str, Dict[str, Any]] = {}

    def snapshot(self, run_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._base[run_id] = copy.deepcopy(state)
        return self._hub.publish(make_event(
            EventType.STATE_SNAPSHOT, envelope_run_id=run_id,
            snapshot=copy.deepcopy(state)))

    def update(self, run_id: str, new_state: Dict[str, Any]) -> Dict[str, Any]:
        """Emit STATE_DELTA against the baseline; stale/missing/broken base
        degrades to a fresh STATE_SNAPSHOT (never a wrong delta)."""
        with self._lock:
            base = self._base.get(run_id)
            ops: Optional[list] = None
            if isinstance(base, dict):
                try:
                    candidate = diff_state(base, new_state)
                    apply_patch(base, candidate)  # validate before publish
                    ops = candidate
                    self._base[run_id] = copy.deepcopy(new_state)
                except PatchError:
                    ops = None
            if ops is None:
                self._base[run_id] = copy.deepcopy(new_state)
                return self.snapshot(run_id, new_state)
            return self._hub.publish(make_event(
                EventType.STATE_DELTA, envelope_run_id=run_id,
                delta=ops))

    def baseline_seq(self, run_id: str) -> Optional[int]:
        """Journal seq of the latest snapshot event, for staleness checks."""
        seq = None
        for rec in self._hub._journal.read_run(run_id):
            if rec.get("type") == EventType.STATE_SNAPSHOT.value:
                seq = rec.get("seq")
        return seq

    @staticmethod
    def client_is_stale(client_last_seq: Optional[int],
                        baseline_seq: Optional[int]) -> bool:
        """A client whose cursor predates the baseline must resnapshot."""
        if client_last_seq is None:
            return True
        if baseline_seq is None:
            return False  # no snapshot yet; deltas alone may still be valid
        return int(client_last_seq) < int(baseline_seq)

    @staticmethod
    def reconstruct(snap_state: Dict[str, Any],
                    ordered_delta_ops: list) -> Dict[str, Any]:
        out = copy.deepcopy(snap_state)
        for ops in ordered_delta_ops:
            out = apply_patch(out, ops)
        return out
