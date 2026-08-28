"""Short conversation, task, and confirmation state for the bridge."""
from __future__ import annotations

import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ConversationState:
    """Small rolling memory of recent exchanges (used for context, not a store)."""

    turns: List[Dict[str, str]] = field(default_factory=list)
    max_turns: int = 8

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
class PendingExecutorChoice:
    """The bridge asked 'OpenClaw or Hermes?' and is waiting for the answer."""
    task_text: str
    model: str = ""
    asked_at: float = field(default_factory=time.time)

    def age_s(self) -> float:
        return time.time() - self.asked_at


class ExecutorChoiceState:
    """One pending executor choice at a time; auto-expires."""

    def __init__(self, timeout_s: float = 60.0) -> None:
        self.timeout_s = timeout_s
        self._pending: Optional[PendingExecutorChoice] = None

    def request(self, task_text: str, model: str = "") -> None:
        self._pending = PendingExecutorChoice(task_text, model)

    def current(self) -> Optional[PendingExecutorChoice]:
        if self._pending is None:
            return None
        if self._pending.age_s() > self.timeout_s:
            self._pending = None
        return self._pending

    def take(self) -> Optional[PendingExecutorChoice]:
        p = self.current()
        self._pending = None
        return p

    def clear(self) -> None:
        self._pending = None

    @property
    def pending(self) -> bool:
        return self.current() is not None



@dataclass
class StickyRoute:
    """Remembers the user's preferred executor across turns.
    Set when the user says `route to hermes/openclaw/browser` or
    `use hermes` — persists until explicitly cleared or overridden.
    The local LLM brain tends to forget; this is the reliable memory.
    """
    target: str = ""  # hermes | openclaw | browser | "" (none)
    reason: str = ""  # why it was set (for logging)
    set_at: float = field(default_factory=time.time)
    turns: int = 0    # how many turns it has survived

    def is_set(self) -> bool:
        return bool(self.target)

    def age_s(self) -> float:
        return time.time() - self.set_at


class StickyRouteState:
    """One sticky route at a time; auto-expires after N turns or timeout.
    Updated by explicit user routing phrases (not inferred).
    Persists to <state_dir>/sticky_route.json so provider switches and
    bridge restarts don't blank the preference.
    """
    def __init__(self, timeout_s: float = 600.0, max_turns: int = 8,
                 state_dir: Optional[Path] = None) -> None:
        self.timeout_s = timeout_s
        self.max_turns = max_turns
        self._route: Optional[StickyRoute] = None
        self._path: Optional[Path] = (Path(state_dir).expanduser() / "sticky_route.json") if state_dir else None
        self._load()

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            return
        try:
            import json as _js
            d = _js.loads(self._path.read_text(errors="replace"))
            t = str(d.get("target","")).lower().strip()
            if t in ("hermes","openclaw","browser"):
                self._route = StickyRoute(target=t, reason=str(d.get("reason",""))[:120],
                                          set_at=float(d.get("set_at", time.time())),
                                          turns=int(d.get("turns",0)))
        except Exception:
            pass

    def _save(self) -> None:
        if not self._path:
            return
        try:
            import json as _js
            if self._route is None:
                try: self._path.unlink(missing_ok=True)
                except Exception: pass
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", errors="replace") as fh:
                _js.dump({"target": self._route.target,
                          "reason": self._route.reason,
                          "set_at": self._route.set_at,
                          "turns": self._route.turns}, fh)
        except Exception:
            pass

    def set(self, target: str, reason: str = "") -> None:
        self._route = StickyRoute(target=target.lower().strip(), reason=reason)
        self._save()

    def current(self) -> Optional[StickyRoute]:
        if self._route is None:
            return None
        if self._route.age_s() > self.timeout_s:
            self._route = None
            self._save()
            return None
        if self._route.turns >= self.max_turns:
            self._route = None
            self._save()
            return None
        return self._route

    def bump(self) -> None:
        """Call once per turn so max_turns expiry works."""
        if self._route is not None:
            self._route.turns += 1
            self._save()

    def clear(self) -> None:
        self._route = None
        self._save()

    @property
    def pending(self) -> bool:
        return self.current() is not None

    def target(self) -> str:
        c = self.current()
        return c.target if c else ""

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


class TaskQueue:
    """FIFO queue of tasks awaiting dispatch while another task is running.

    Lets the user keep dictating tasks while OpenClaw/Hermes works — nothing is
    lost and nothing hard-aborts a running task under load. Each entry carries
    the task text plus the model/provider it should run on.
    """

    def __init__(self, max_size: int = 20) -> None:
        self._q: "List[Dict[str, str]]" = []
        self._lock = None
        import threading as _th
        self._lock = _th.Lock()
        self.max_size = max_size

    def enqueue(self, task_text: str, model: str = "", provider: str = "") -> int:
        """Return the 1-based position in the queue, or 0 when full/ignored."""
        task_text = (task_text or "").strip()
        if not task_text:
            return 0
        with self._lock:
            if len(self._q) >= self.max_size:
                return 0
            self._q.append({"task": task_text, "model": model, "provider": provider})
            return len(self._q)

    def peek(self) -> Optional[Dict[str, str]]:
        with self._lock:
            return self._q[0] if self._q else None

    def dequeue(self) -> Optional[Dict[str, str]]:
        with self._lock:
            return self._q.pop(0) if self._q else None

    def clear(self) -> int:
        with self._lock:
            n = len(self._q)
            self._q = []
            return n

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._q)

    def items(self) -> List[Dict[str, str]]:
        with self._lock:
            return list(self._q)


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
