"""Short conversation, task, and confirmation state for the bridge."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ConversationState:
    """Small rolling memory of recent exchanges (used for context, not a store)."""

    turns: List[Dict[str, str]] = field(default_factory=list)
    max_turns: int = 4

    def add(self, role: str, text: str) -> None:
        if text.strip():
            self.turns.append({"role": role, "text": text.strip()})
            if len(self.turns) > self.max_turns:
                self.turns = self.turns[-self.max_turns:]

    def as_prompt(self) -> str:
        return "\n".join(f"{t['role']}: {t['text']}" for t in self.turns)

    def clear(self) -> None:
        self.turns = []


@dataclass
class TaskState:
    """State of the currently dispatched OpenClaw task (if any)."""

    active: bool = False
    session_key: str = ""
    run_id: str = ""
    description: str = ""
    started_at: float = 0.0
    last_progress_spoken: float = 0.0
    model: str = ""

    def set_active(self, session_key: str, run_id: str, description: str, model: str = "") -> None:
        self.active = True
        self.session_key = session_key
        self.run_id = run_id
        self.description = description
        self.model = model
        self.started_at = time.time()
        self.last_progress_spoken = time.time()

    def clear(self) -> None:
        self.active = False
        self.session_key = ""
        self.run_id = ""
        self.description = ""
        self.started_at = 0.0

    def elapsed_s(self) -> float:
        return time.time() - self.started_at if self.started_at else 0.0


@dataclass
class PendingConfirmation:
    """A destructive/risky action awaiting spoken yes/no."""

    action_desc: str
    payload: Dict[str, Any]      # e.g. {"task": "...", "session_key": "...", "dry_run": bool}
    reason: str
    created_at: float = field(default_factory=time.time)

    def age_s(self) -> float:
        return time.time() - self.created_at


class ConfirmationState:
    """One pending confirmation at a time; auto-expires after timeout."""

    def __init__(self, timeout_s: float = 120.0) -> None:
        self.timeout_s = timeout_s
        self._pending: Optional[PendingConfirmation] = None

    def request(self, action_desc: str, payload: Dict[str, Any], reason: str) -> None:
        self._pending = PendingConfirmation(action_desc, payload, reason)

    def current(self) -> Optional[PendingConfirmation]:
        if self._pending is None:
            return None
        if self._pending.age_s() > self.timeout_s:
            self._pending = None
        return self._pending

    def take(self) -> Optional[PendingConfirmation]:
        p = self.current()
        self._pending = None
        return p

    def clear(self) -> None:
        self._pending = None

    @property
    def pending(self) -> bool:
        return self.current() is not None
