"""Logging + structured observability.

Every interesting event (transcript, routing decision, gateway request, status,
result, failure, reconnect, timing) is written both as a human-readable log line
and as a JSON object to the event journal (events.jsonl) for later analysis.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

_EVENT_LOCK = threading.Lock()
_EVENT_FILE: Optional[Path] = None
_LOGGER: Optional[logging.Logger] = None

#: Journal growth bound. The event journal is append-only and, before this
#: bound, reached 361 MB on a real install (every turn, ASR probe and TTS
#: boundary is a line). It is rotated at this size; the keeper keeps at most
#: ``_EVENT_BACKUPS`` older files. Both are configurable through
#: ``logging.jsonl_max_bytes`` / ``logging.jsonl_backups``; 0 disables rotation.
DEFAULT_EVENT_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_EVENT_BACKUPS = 3
_EVENT_MAX_BYTES = DEFAULT_EVENT_MAX_BYTES
_EVENT_BACKUPS = DEFAULT_EVENT_BACKUPS
#: Rotation is checked at most this often, not on every append (a syscall per
#: event would defeat the point of a cheap journal).
_ROTATE_CHECK_S = 2.0
_last_rotate_check = 0.0
_rotate_lock = threading.Lock()

#: Free-text transcripts (spoken/ambient speech) are persisted only when the
#: owner explicitly opts into verbose logging. Set by ``setup`` from
#: ``voice_edit.debug_log`` (or ``QUBIT_LOG_TRANSCRIPTS=1``). ASR providers and
#: any other ambient-audio writer consult :func:`transcript_fields`.
_LOG_TRANSCRIPTS = False


def _env_flag(name: str, default: bool = False) -> bool:
    try:
        raw = os.environ.get(name)
    except Exception:
        return default
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def transcripts_enabled() -> bool:
    """True only when the owner enabled verbose transcript logging."""
    return bool(_LOG_TRANSCRIPTS)


def set_transcripts_enabled(enabled: bool) -> None:
    global _LOG_TRANSCRIPTS
    _LOG_TRANSCRIPTS = bool(enabled)


def transcript_fields(**texts: Any) -> Dict[str, Any]:
    """``{name: value}`` only when transcript logging is explicitly enabled.

    The counterpart to :func:`voice_edit.debug.text_fields` for writers (ASR
    providers) that do not carry a config object. Values are bounded exactly
    like the wake-loop fields so the journal shape stays consistent.
    """
    if not _LOG_TRANSCRIPTS:
        return {}
    out: Dict[str, Any] = {}
    for name, value in texts.items():
        if value is None:
            continue
        out[name] = str(value)[:2000]
    return out


def _coerce_bytes(value: Any, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, n)


def _rotate_locked(path: Path) -> None:
    """Rotate ``events.jsonl`` -> ``.1`` -> ``.2`` ... (best effort).

    Writers open the journal per append (no long-lived fd), so a reader that
    misses the rename can lose at most the one line racing the replace. The
    lock file serialises concurrent rotators across processes; a failure here
    is never allowed to break event logging.
    """
    try:
        if _EVENT_MAX_BYTES <= 0 or not path.exists():
            return
        if path.stat().st_size <= _EVENT_MAX_BYTES:
            return
    except OSError:
        return
    lock_fh = None
    try:
        try:
            import fcntl
        except Exception:  # pragma: no cover - non-POSIX
            fcntl = None
        if fcntl is not None:
            lock_fh = path.with_name(path.name + ".rotate.lock").open("a+")
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        # Re-check under the lock: another process may have rotated already.
        if not path.exists() or path.stat().st_size <= _EVENT_MAX_BYTES:
            return
        if _EVENT_BACKUPS <= 0:
            path.open("w").close()
            return
        for idx in range(_EVENT_BACKUPS - 1, 0, -1):
            src = path.with_name(f"{path.name}.{idx}")
            dst = path.with_name(f"{path.name}.{idx + 1}")
            if src.exists():
                os.replace(src, dst)
        os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass
    finally:
        if lock_fh is not None:
            try:
                import fcntl
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                lock_fh.close()
            except Exception:
                pass


def _maybe_rotate(path: Path) -> None:
    global _last_rotate_check
    now = time.monotonic()
    if now - _last_rotate_check < _ROTATE_CHECK_S:
        return
    with _rotate_lock:
        now = time.monotonic()
        if now - _last_rotate_check < _ROTATE_CHECK_S:
            return
        _last_rotate_check = now
        _rotate_locked(path)


def setup(level: str = "INFO", jsonl: bool = True, jsonl_path: Optional[Path] = None,
          state_dir: Optional[Path] = None, *,
          max_bytes: Optional[int] = None,
          backups: Optional[int] = None,
          log_transcripts: Optional[bool] = None) -> logging.Logger:
    global _EVENT_FILE, _LOGGER, _EVENT_MAX_BYTES, _EVENT_BACKUPS
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
    if max_bytes is not None:
        _EVENT_MAX_BYTES = _coerce_bytes(max_bytes, DEFAULT_EVENT_MAX_BYTES)
    if backups is not None:
        _EVENT_BACKUPS = _coerce_bytes(backups, DEFAULT_EVENT_BACKUPS)
    if log_transcripts is not None:
        set_transcripts_enabled(bool(log_transcripts))
    elif _env_flag("QUBIT_LOG_TRANSCRIPTS", False):
        set_transcripts_enabled(True)
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
            _maybe_rotate(_EVENT_FILE)
        except Exception:
            pass
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
