"""Durable human-in-the-loop approval journal (Packet 1B).

Adapts the pinned CopilotKit state machine (InProgress -> Executing -> Complete,
respond exposed only while executing, settle exactly once, abort settles
pending work as an explicit outcome) to a restart-safe Python store backed by
an append-only JSONL journal. Divergence from upstream (documented in
docs/UPSTREAM_REUSE.md R2): records survive bridge restarts; CopilotKit's hook
is in-memory only.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

IN_PROGRESS = "in_progress"      # awaiting user response (respond available)
EXECUTING = "executing"          # approved, target action running
COMPLETE = "complete"
ABORTED = "aborted"
DENIED = "denied"
TERMINAL = {COMPLETE, ABORTED, DENIED}


@dataclass
class Approval:
    id: str
    capability: str
    summary: str
    created_at: float
    expires_at: float
    state: str = IN_PROGRESS
    decision: Optional[str] = None     # approve | deny | abort
    settled_at: Optional[float] = None
    meta: Dict[str, Any] = field(default_factory=dict)


class ApprovalJournal:
    """Settle-once approvals durable across restarts.

    Packet 2B: ``event_sink`` is an optional callback ``(name, approval)``
    invoked after each durable state transition (required / settled /
    finished / aborted) so the typed event stream mirrors approvals.
    Durability never depends on the sink: sink exceptions are logged and
    swallowed AFTER the journal append, never before it.
    """

    def __init__(self, path: Path, clock: Any = None,
                 default_ttl_s: float = 600.0,
                 event_sink: Optional[Any] = None):
        self.path = Path(path)
        self._clock = clock or time.time
        self._ttl = float(default_ttl_s)
        self.event_sink = event_sink
        self._lock = threading.Lock()
        self._items: Dict[str, Approval] = {}
        self._load()

    def _emit(self, name: str, item: Approval) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(name, item)
        except Exception as exc:  # noqa: BLE001 - observability boundary
            import logging
            logging.getLogger(__name__).warning(
                "approval event sink failed (%s/%s): %s", name, item.id, exc)

    # ------------------------------------------------------------ persistence
    def _load(self) -> None:
        if not self.path.exists():
            return
        with self._lock:
            for line in self.path.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    prev = self._items.get(rec["id"])
                    # null settled_at counts as 0 so a same-timestamp state
                    # transition (e.g. approve) still supersedes the create.
                    if prev is None or (rec.get("settled_at") or 0) >= \
                            ((prev.settled_at or 0)):
                        self._items[rec["id"]] = Approval(**rec)
                except Exception:
                    continue                      # torn tail line: skip

    def _append(self, item: Approval) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (json.dumps(asdict(item), separators=(",", ":")) + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)

    # ------------------------------------------------------------- operations
    def create(self, capability: str, summary: str, *,
               ttl_s: Optional[float] = None, **meta: Any) -> Approval:
        now = self._clock()
        ttl = float(ttl_s) if ttl_s is not None else self._ttl
        item = Approval(id=uuid.uuid4().hex, capability=capability,
                        summary=summary[:300], created_at=now,
                        expires_at=now + max(1.0, min(ttl, 86400.0)), meta=meta)
        with self._lock:
            self._items[item.id] = item
        self._append(item)
        self._emit("approval.required", item)
        return item

    def get(self, approval_id: str) -> Optional[Approval]:
        with self._lock:
            return self._items.get(approval_id)

    def pending(self) -> List[Approval]:
        now = self._clock()
        with self._lock:
            live = [a for a in self._items.values() if a.state == IN_PROGRESS]
        return [a for a in live if a.expires_at > now]

    def respond(self, approval_id: str, decision: str) -> Approval:
        """Settle exactly once; second responses are no-ops returning the
        already-settled record (CopilotKit settle-once semantics)."""
        if decision not in ("approve", "deny"):
            raise ValueError("decision must be 'approve' or 'deny'")
        now = self._clock()
        with self._lock:
            item = self._items.get(approval_id)
            if item is None:
                raise KeyError(approval_id)
            if item.state in TERMINAL:
                return item                       # idempotent no-op
            if item.state != EXECUTING and item.expires_at <= now:
                item.state, item.decision = ABORTED, "abort"
                item.settled_at = now
                self._append(item)
                self._emit("approval.aborted", item)
                return item
            item.state = EXECUTING if decision == "approve" else DENIED
            item.decision = decision
            item.settled_at = now if decision == "deny" else None
            self._append(item)                    # durable BEFORE returning
            self._emit("approval.settled", item)
            return item

    def finish(self, approval_id: str) -> Approval:
        """Move an approved/EXECUTING record to COMPLETE (single terminal)."""
        with self._lock:
            item = self._items[approval_id]
            if item.state == COMPLETE:
                return item
            if item.state != EXECUTING:
                # Explicit (not ``assert``) so ``python -O`` cannot strip the
                # guard and let an unapproved record reach COMPLETE.
                raise AssertionError(f"finish on {item.state}")
            item.state, item.settled_at = COMPLETE, self._clock()
        self._append(item)
        self._emit("approval.finished", item)
        return item

    def abort(self, approval_id: str) -> Approval:
        """Abort path: pending -> ABORTED; executing -> ABORTED (explicit)."""
        with self._lock:
            item = self._items[approval_id]
            if item.state in TERMINAL:
                return item
            item.state, item.decision, item.settled_at = ABORTED, "abort", self._clock()
        self._append(item)
        self._emit("approval.aborted", item)
        return item

    def sweep_expired(self) -> int:
        now = self._clock()
        n = 0
        swept: List[Approval] = []
        with self._lock:
            for item in self._items.values():
                if item.state == IN_PROGRESS and item.expires_at <= now:
                    item.state, item.decision, item.settled_at = ABORTED, "abort", now
                    self._append(item)
                    swept.append(item)
                    n += 1
        for item in swept:
            self._emit("approval.aborted", item)
        return n
