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


def _json_safe(value: Any, _depth: int = 0) -> Any:
    """Coerce a value to a JSON-serializable form WITHOUT hiding it.

    JSON-native scalars pass through; containers are walked with depth/item
    bounds; ANY other object (a mock, Path, bytes, enum, ...) becomes a visible
    typed marker so the journal stays valid and the offending value/type is
    never silently dropped or flattened behind a blanket ``default=str``.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if _depth > 6:
        return f"<depth:{_depth}>"
    if isinstance(value, dict):
        items = list(value.items())[:200]
        return {str(k): _json_safe(v, _depth + 1) for k, v in items}
    if isinstance(value, (list, tuple, set)):
        seq = list(value)[:200]
        out = [_json_safe(v, _depth + 1) for v in seq]
        return out if isinstance(value, (list, tuple)) else out
    try:
        marker = repr(value)
    except Exception:
        marker = ""
    return f"<{type(value).__name__}: {marker[:200]}>"


def log_event(event: str, **fields: Any) -> None:
    """Write a structured event to the JSONL journal (and a debug log line).

    Packet 1B: every field value passes through centralized redaction BEFORE
    touching the journal or the debug log — this is the single choke point for
    worker logs, orchestrator events, and tool output. Field values are then
    coerced to JSON-native forms so a non-serializable value (e.g. a mock in a
    test) can never crash the journal write; the coercion surfaces the type
    instead of hiding it.
    """
    from .redaction import redact_obj
    import os as _os
    record = {"ts": time.time(), "event": event,
              "pid": _os.getpid()}
    record.update(redact_obj(fields))
    record = _json_safe(record)
    if _EVENT_FILE:
        try:
            with _EVENT_FILE.open("a") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError:
            pass
    if _LOGGER:
        # Packet 1B repair: derive from the ALREADY-REDACTED record — the
        # original fields must never reach the debug sink.
        compact = {k: (str(v)[:200] if isinstance(v, str) else v)
                   for k, v in record.items() if k not in ("ts", "event")}
        _LOGGER.debug("%s %s", event, json.dumps(compact))


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
