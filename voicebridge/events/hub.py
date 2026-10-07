"""In-process event hub with durable replay (Packet 2B).

Fan-out for the typed event stream: every published event is appended to the
Packet 2A journal (the single ordering authority), then offered to live
subscribers. Subscribing serializes against publishing under one lock and
seeds the subscriber from a journal scan past ``last_event_id`` — so a client
reconnect sees an exact continuation: no gaps, no duplicates.

Overflow policy: a slow subscriber's bounded queue drops its OLDEST entries so
the writer can keep streaming the NEWEST ones without interrupting the client.
``overflow`` is recorded only to power ``Subscriber.had_gap`` — a *genuine*
cursor gap (the dropped seqs fall below the cursor the client will resume from).
The client already tracks ``last_seq`` and replays from the journal, so a
non-gap overflow is healed by continuing the stream; only a real gap needs a
resync marker. The journal, not the queue, is the source of truth.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Any, Deque, Dict, List, Optional

from .core import to_wire
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
        # Highest seq a stalled read dropped. 0 = none dropped since the last
        # time the writer asked (``take_gap``), so a non-gap overflow stops
        # asking for a resync and the client keeps its cursor.
        self._gap_from: int = 0
        self.closed = False
        self.wake = threading.Event()

    def offer(self, record: Dict[str, Any]) -> None:
        if self.closed:
            return
        if self.run_filter and record.get("run_id") != self.run_filter:
            return
        while len(self.q) >= self.max:
            dropped = self.q.popleft()
            self.overflow = True
            seq = dropped.get("seq")
            if isinstance(seq, int):
                self._gap_from = max(self._gap_from, seq)
        self.q.append(record)
        self.wake.set()

    def take_gap(self, cursor: int) -> int:
        """Return the highest dropped seq at/below ``cursor`` and clear it.

        A client resumes from ``cursor`` (its ``last_seq``); if everything
        dropped is at or below that, the journal replay bridges the loss with
        no client action. Only a dropped seq *above* the cursor is a real gap
        the client cannot heal from its retained cursor — that is the one case
        that must signal resync. Mutating state lives on the subscriber, so
        this stays safe under the hub's publish lock.
        """
        gap_from = self._gap_from
        self._gap_from = 0
        return gap_from if gap_from > cursor else 0

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
                 max_queue: int = 4096) -> None:
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

    def close_all(self) -> None:
        """Wake and close every live subscriber (Packet 8K closure).

        Called on server shutdown so in-flight SSE handler threads exit their
        drain loop promptly instead of blocking ``server_close()`` (which joins
        handler threads when ``block_on_close`` is set). Without this, an open
        event-stream connection can stall the webui accept loop indefinitely."""
        with self._lock:
            subs = list(self._subs.values())
            self._subs.clear()
        for sub in subs:
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
    """Tell the client its cursor is unreachable; replays the whole journal.

    Emitted only on a *genuine* cursor gap (the client stalled long enough that
    the server dropped an event at a seq above the client's own cursor). The
    client answers by resuming from the retained ``last_seq`` — the journal
    replay fills the gap — not by reconnecting cold.
    """
    return ": resync\nevent: resync\ndata: {}\n\n"
