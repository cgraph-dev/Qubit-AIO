"""Live voice-journal tail: the orb's authoritative event source.

The orb must react to the *physical* voice boundaries (mic open, TTS
playback start/end, transcript final) the instant they happen. Polling an
HTTP status snapshot cannot do that: it samples a changing process, so it
misses short transitions and shows stale/idle state between streamed TTS
chunks (live 2026-10-06: a 95-char reply went orange mid-sentence because
a fixed 4 s window expired while audio was still playing).

The desktop helper and the voice service already append every lifecycle
event, in order, to the one Qubit journal (``<state>/events.jsonl``).
This module tails that file — a real event stream — so the orb consumes
events at append time (sub-100 ms) with no HTTP, no polling, and no
inference. The heartbeat file remains the source for the live mic
spectrum and the exact TTS timeline; the journal supplies the events.

Pure parsing + a bounded, ordered ring; the tail thread only reads a file
and never touches a widget. Never raises.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional


def journal_path() -> Optional[Path]:
    """Canonical voice journal path, or None when unresolvable."""
    try:
        from voicebridge.config import canonical_state_dir
        return Path(canonical_state_dir()) / "events.jsonl"
    except Exception:
        return None


def parse_event_line(line: Any) -> Optional[Dict[str, Any]]:
    """One journal line -> event dict, or None (blank/partial/malformed)."""
    try:
        text = line.decode("utf-8", "replace") if isinstance(line, bytes) else str(line)
    except Exception:
        return None
    text = text.strip()
    if not text or not text.startswith("{"):
        return None
    try:
        obj = json.loads(text)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    if not obj.get("event"):
        return None
    return obj


class JournalTail:
    """Tail the voice journal and keep a bounded, ordered recent window.

    ``on_event(event)`` (optional) is called from the tail thread for every
    NEW event — it must not touch a UI. ``recent()`` is thread-safe and
    returns the buffered events oldest->newest. ``start()`` is idempotent;
    ``stop()`` joins the thread. Never raises.
    """

    def __init__(self, path: Any = None, *, max_events: int = 256,
                 on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
                 poll_s: float = 0.05) -> None:
        try:
            self._path = Path(path) if path is not None else journal_path()
        except Exception:
            self._path = None
        try:
            self._max = max(1, int(max_events))
        except (TypeError, ValueError):
            self._max = 256
        try:
            self._poll_s = max(0.01, min(1.0, float(poll_s)))
        except (TypeError, ValueError):
            self._poll_s = 0.05
        self._on_event = on_event if callable(on_event) else None
        self._events: Deque[Dict[str, Any]] = deque(maxlen=self._max)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._pos = 0
        self._inode: Any = None

    # -- public API ----------------------------------------------------
    def recent(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Buffered recent events oldest->newest (thread-safe, never raises)."""
        try:
            with self._lock:
                data = list(self._events)
        except Exception:
            return []
        if limit is None:
            return data
        try:
            n = max(0, int(limit))
        except (TypeError, ValueError):
            return data
        return data[-n:] if n else []

    def start(self) -> None:
        """Start the daemon tail thread (idempotent, never raises)."""
        try:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._path is None:
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="orb-journal-tail", daemon=True)
            self._thread.start()
        except Exception:
            self._thread = None

    def stop(self) -> None:
        """Signal the tail thread and join briefly (never raises)."""
        try:
            self._stop.set()
        except Exception:
            pass
        try:
            t = self._thread
            if t is not None:
                t.join(timeout=1.0)
        except Exception:
            pass
        self._thread = None

    def _append(self, event: Dict[str, Any]) -> None:
        with self._lock:
            self._events.append(event)
        cb = self._on_event
        if cb is not None:
            try:
                cb(event)
            except Exception:
                pass

    # -- tail loop -----------------------------------------------------
    def _run(self) -> None:
        path = self._path
        while not self._stop.is_set():
            try:
                if not path.exists():
                    self._pos = 0
                    self._inode = None
                    self._sleep()
                    continue
                st = path.stat()
                inode = getattr(st, "st_ino", None)
                # First open (or a rotation/truncation): start at the END so
                # the orb shows "now", not a replay of history.
                if self._inode != inode or st.st_size < self._pos:
                    self._pos = st.st_size
                    self._inode = inode
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(self._pos)
                    got = False
                    while not self._stop.is_set():
                        line = fh.readline()
                        if not line:
                            break
                        got = True
                        ev = parse_event_line(line)
                        if ev is not None:
                            self._append(ev)
                    self._pos = fh.tell()
                    # The file may have rotated while we held it open.
                    try:
                        if path.stat().st_ino != inode:
                            self._pos = 0
                            self._inode = None
                    except Exception:
                        pass
                if not got:
                    self._sleep()
            except Exception:
                self._sleep()

    def _sleep(self) -> None:
        try:
            self._stop.wait(self._poll_s)
        except Exception:
            time.sleep(self._poll_s)


__all__ = ["JournalTail", "journal_path", "parse_event_line"]
