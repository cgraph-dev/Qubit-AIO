"""Append-only ordered event journal (Packet 2A).

JSONL storage for the typed event stream: one compact JSON record per line,
monotonic ``seq`` assigned under a lock, redaction applied at the write
boundary (Packet 1B ``voicebridge.redaction``), torn-tail tolerant recovery
(a partially written last line is skipped, never rewritten — the file is
append-only by contract).

Records are AG-UI wire shape plus the local envelope (``seq``, ``schema``,
``redacted``; ``run_id``/``parent_id`` correlation where the pinned wire shape
lacks them). Use ``voicebridge.events.core.to_wire``/``encode_sse`` to strip
the envelope for AG-UI consumers.

Single-owner-per-file: one EventJournal instance per path per process.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from ..redaction import redact_obj
from .core import SCHEMA_VERSION, EventType, make_event


class EventJournal:
    def __init__(self, path: "str | Path", fsync: bool = True) -> None:
        self.path = Path(path)
        self.fsync = fsync
        self._lock = threading.Lock()
        self._last_seq = 0
        if str(self.path.parent):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            for rec in self._iter_valid():
                seq = rec.get("seq")
                if isinstance(seq, int):
                    self._last_seq = max(self._last_seq, seq)

    # -- write side -------------------------------------------------------

    def append(self, type_: "EventType | str", /, *, timestamp: Optional[int] = None,
               correlation_run_id: Optional[str] = None,
               correlation_parent_id: Optional[str] = None,
               **fields: Any) -> Dict[str, Any]:
        """Redact, sequence, and append one event; returns the stored record.

        ``correlation_*`` become journal-only ``run_id``/``parent_id`` envelope
        fields for event types whose pinned wire shape lacks them; for RUN_*
        types pass the spec'd ``run_id``/``thread_id`` payload fields instead.
        """
        with self._lock:
            event = make_event(type_, timestamp=timestamp,
                               envelope_run_id=correlation_run_id,
                               envelope_parent_id=correlation_parent_id,
                               redacted=True, **fields)
            record: Dict[str, Any] = {"seq": self._last_seq + 1}
            record.update(redact_obj(event))
            line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
            needs_newline = False
            if self.path.exists():
                try:
                    with self.path.open("rb") as fh:
                        fh.seek(-1, os.SEEK_END)
                        needs_newline = fh.read(1) != b"\n"
                except OSError:
                    needs_newline = False
            with self.path.open("a", encoding="utf-8") as fh:
                if needs_newline:
                    fh.write("\n")
                fh.write(line + "\n")
                fh.flush()
                if self.fsync:
                    os.fsync(fh.fileno())
            self._last_seq += 1
            return record

    def append_validated(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Persist an already-built ``make_event`` dict; returns stored record.

        Packet 2B path: the hub publishes constructed events; envelope keys are
        re-derived here so ``seq`` stays solely journal-assigned. For RUN_*
        types ``run_id`` is a required payload field and stays in the payload;
        for every other type it becomes correlation-only envelope data.
        """
        payload = {k: v for k, v in event.items()
                   if k not in ("type", "timestamp", "seq", "schema", "redacted")}
        etype = EventType(str(event["type"]))
        corr_run = None
        corr_parent = None
        if etype not in (EventType.RUN_STARTED, EventType.RUN_FINISHED,
                         EventType.RUN_CANCELLED):
            corr_run = payload.pop("run_id", None)
            corr_parent = payload.pop("parent_id", None)
        return self.append(etype, timestamp=event.get("timestamp"),
                           correlation_run_id=corr_run,
                           correlation_parent_id=corr_parent,
                           **payload)

    @property
    def last_seq(self) -> int:
        return self._last_seq

    # -- read side --------------------------------------------------------

    def _iter_valid(self) -> Iterator[Dict[str, Any]]:
        try:
            fh = self.path.open("r", encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue  # torn or corrupt tail: skipped, never repaired
                if isinstance(rec, dict) and isinstance(rec.get("seq"), int) \
                        and "type" in rec:
                    yield rec

    def read_all(self) -> List[Dict[str, Any]]:
        return list(self._iter_valid())

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return self._iter_valid()

    def read_run(self, run_id: str) -> List[Dict[str, Any]]:
        """All records correlated to a run (envelope or payload ``run_id``)."""
        return [rec for rec in self._iter_valid() if rec.get("run_id") == run_id]
