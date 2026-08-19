"""Logging + structured observability.

Every interesting event (transcript, routing decision, gateway request, status,
result, failure, reconnect, timing) is written both as a human-readable log line
and as a JSON object to the event journal (events.jsonl) for later analysis.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

_EVENT_LOCK = threading.Lock()
_EVENT_FILE: Optional[Path] = None
_LOGGER: Optional[logging.Logger] = None


def setup(level: str = "INFO", jsonl: bool = True, jsonl_path: Optional[Path] = None,
          state_dir: Optional[Path] = None) -> logging.Logger:
    global _EVENT_FILE, _LOGGER
    logger = logging.getLogger("voicebridge")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s | %(message)s")
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    logger.propagate = False

    if jsonl:
        if jsonl_path:
            _EVENT_FILE = Path(jsonl_path)
        elif state_dir:
            _EVENT_FILE = Path(state_dir) / "events.jsonl"
        if _EVENT_FILE:
            _EVENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LOGGER = logger
    return logger


def get_logger() -> logging.Logger:
    if _LOGGER is None:
        return setup()
    return _LOGGER


def log_event(event: str, **fields: Any) -> None:
    """Write a structured event to the JSONL journal (and a debug log line)."""
    record = {"ts": time.time(), "event": event}
    record.update(fields)
    if _EVENT_FILE:
        try:
            with _EVENT_FILE.open("a") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError:
            pass
    if _LOGGER:
        compact = {k: (str(v)[:200] if isinstance(v, str) else v) for k, v in fields.items()}
        _LOGGER.debug("%s %s", event, json.dumps(compact, default=str))


class Timing:
    """Simple named timing accumulator for metrics."""

    def __init__(self) -> None:
        self._marks: Dict[str, float] = {}

    def mark(self, name: str) -> None:
        self._marks[name] = time.time()

    def span(self, start: str, end: str) -> float:
        return self._marks.get(end, time.time()) - self._marks.get(start, time.time())

    def summary(self) -> Dict[str, float]:
        return dict(self._marks)
