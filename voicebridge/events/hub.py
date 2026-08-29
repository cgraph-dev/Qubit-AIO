"""In-process event hub with durable replay (Packet 2B).

Fan-out for the typed event stream: every published event is appended to the
Packet 2A journal (the single ordering authority), then offered to live
subscribers. Subscribing serializes against publishing under one lock and
seeds the subscriber from a journal scan past ``last_event_id`` — so a client
reconnect sees an exact continuation: no gaps, no duplicates.

Overflow policy: a slow subscriber's bounded queue drops its OLDEST entries
and raises ``overflow``; the SSE writer then tells the client to resync, and
replay comes back from the journal — nothing is lost because the journal, not
the queue, is the source of truth.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from .core import EventType, to_wire
from .journal import EventJournal

HEARTBEAT_S = 15.0


class Subscriber:
    def __init__(self, sub_id: int, run_filter: Optional[str],
                 max_queue: int) -> None:
        self.id = sub_id
        self.run_filter = run_filter
        self.max = max(1, int(max_queue))
        self.q: Deque[Dict[str, Any]] = deque()
        self.overflow = False
        self.closed = False
        self.wake = threading.Event()

    def offer(self, record: Dict[str, Any]) -> None:
        if self.closed:
            return
        if self.run_filter and record.get("run_id") != self.run_filter:
            return
        if len(self.q) >= self.max:
            self.q.popleft()
            self.overflow = True
        self.q.append(record)
        self.wake.set()

    def drain(self, timeout: Optional[float]) -> List[Dict[str, Any]]:
        if timeout is not None and not self.q:
            self.wake.wait(timeout)
            self.wake.clear()
        items = list(self.q)
        self.q.clear()
        return items

    def close(self) -> None:
        self.closed = True
        self.wake.set()


class EventHub:
    def __init__(self, journal: EventJournal,
                 max_queue: int = 512) -> None:
        self._journal = journal
        self._max_queue = max_queue
        self._lock = threading.Lock()
        self._subs: Dict[int, Subscriber] = {}
        self._next_id = 1

    # -- publish -----------------------------------------------------------

    def publish(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Append to the journal and fan out; returns the stored record."""
        with self._lock:  # one global order: append+fanout vs subscribe-replay
            record = self._journal.append_validated(event)
            for sub in list(self._subs.values()):
                sub.offer(record)
            return record

    # -- subscribe ---------------------------------------------------------

    def subscribe(self, last_event_id: Optional[int] = None,
                  run_id: Optional[str] = None) -> Subscriber:
        """Register a subscriber; seed it from the journal past the cursor."""
        sub = Subscriber(self._next_id, run_filter=run_id,
                         max_queue=self._max_queue)
        with self._lock:
            self._next_id += 1
            self._subs[sub.id] = sub
            cursor = int(last_event_id) if last_event_id else 0
            for rec in self._journal.read_all():
                if isinstance(rec.get("seq"), int) and rec["seq"] > cursor:
                    sub.offer(rec)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        with self._lock:
            self._subs.pop(sub.id, None)
        sub.close()

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    @property
    def last_seq(self) -> int:
        return self._journal.last_seq

    @staticmethod
    def wire_frame(record: Dict[str, Any]) -> str:
        """SSE frame: ``id: <seq>`` + AG-UI wire json (R1 framing)."""
        body = to_wire(record)
        line = f"id: {record.get('seq', 0)}\ndata: " + \
               __import__("json").dumps(body, separators=(",", ":"),
                                        ensure_ascii=False) + "\n\n"
        return line


def resync_frame() -> str:
    """Tell the client its queue overflowed; reconnect replays from journal."""
    return ": resync\nevent: resync\ndata: {}\n\n"
