"""Read-only view of the voice-typing event journal for the dashboard.

Surfaces the same ``events.jsonl`` records the audit already writes (char
counts, digests, engine/model/locality, error codes) so a user can see every
wake/command/transform/insert attempt and its outcome. It never contains the
spoken or selected text: those are never logged in the first place. No second
log store is created.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

# Event-name prefixes that belong to the voice surface.
VOICE_PREFIXES = (
    "voice_edit.",
    "qubit_compat.voice_edit.",
    "stt.",
)

# Fields worth showing; never include selected/transcribed text.
SHOWN_FIELDS = (
    "engine", "state", "chars", "chars_in", "chars_out", "explicit",
    "available", "captured_via", "app_class", "selected", "window_id",
    "forced", "n", "peak", "ok", "code", "detail", "action", "style",
    "router", "model", "provider", "locality", "privacy_mode", "request_id",
    "final_chars", "error", "method", "clipboard_restored", "focus_restored",
    "injection_suspected", "clarification_returned", "facts_dropped",
    "wake_engine", "blocked",
    # Only present when voice_edit.debug_log is on (owner opt-in).
    "text", "command", "dictated", "inserted", "transcript",
)


def _journal_path() -> Path:
    try:
        from ..config import canonical_state_dir
        return Path(canonical_state_dir()) / "events.jsonl"
    except Exception:
        import os
        return Path(os.path.expanduser(
            "~/.local/state/qubit-voicebridge/events.jsonl"))


def _is_voice_event(name: str) -> bool:
    return any(name.startswith(p) for p in VOICE_PREFIXES)


def _slim(rec: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "ts": rec.get("ts"),
        "event": rec.get("event"),
    }
    for key in SHOWN_FIELDS:
        if key in rec and rec[key] not in (None, ""):
            out[key] = rec[key]
    return out


def recent_events(limit: int = 200, *, max_bytes: int = 1_500_000) -> List[Dict[str, Any]]:
    """Return the newest voice events (oldest→newest), never any text."""
    path = _journal_path()
    if not path.is_file():
        return []
    limit = max(1, min(int(limit or 200), 2000))
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
            data = fh.read().decode("utf-8", "replace")
    except OSError:
        return []
    lines = data.splitlines()
    # Drop a possibly partial first line after the seek.
    if len(lines) > 1 and not lines[0].lstrip().startswith("{"):
        lines = lines[1:]
    out: List[Dict[str, Any]] = []
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if _is_voice_event(str(rec.get("event", ""))):
            out.append(_slim(rec))
        if len(out) >= limit:
            break
    out.reverse()
    return out


def summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    errors = [e for e in events
              if str(e.get("event", "")).endswith((".error", ".mic_error")) or
              e.get("ok") is False or e.get("code")]
    return {
        "count": len(events),
        "errors": len(errors),
        "last_error": errors[-1] if errors else None,
        "last_event": events[-1] if events else None,
    }


__all__ = ["recent_events", "summary", "VOICE_PREFIXES", "SHOWN_FIELDS"]
