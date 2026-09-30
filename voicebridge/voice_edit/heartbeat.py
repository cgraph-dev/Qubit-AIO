"""Liveness heartbeat for the desktop helper.

The browser `/qubit-chat` page has its own in-page Right-Ctrl push-to-talk.
When the system-wide desktop helper is actually running it MUST be the single
authority for the global hotkey, or the two would both react. The helper writes
a small heartbeat file; the dashboard reads it via
``/api/voice-edit/status`` and the page yields Right-Ctrl while the helper is
live.

The heartbeat carries no user data: pid, timestamp, session type, and a version.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

SCHEMA = "qubit-voice-edit-helper/1"
FRESH_S = 20.0


def _state_dir() -> Path:
    try:
        from ..config import canonical_state_dir
        return Path(canonical_state_dir())
    except Exception:
        return Path(os.path.expanduser("~/.local/state/qubit-voicebridge"))


def path() -> Path:
    return _state_dir() / "voice_edit_helper.json"


def write(*, session: str = "x11", extra: Optional[Dict[str, Any]] = None) -> None:
    payload: Dict[str, Any] = {
        "schema": SCHEMA, "pid": os.getpid(), "ts": time.time(),
        "session": session,
    }
    if extra:
        payload.update(extra)
    p = path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        pass


def clear() -> None:
    try:
        path().unlink()
    except OSError:
        pass


def read() -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    return data


def live(*, fresh_s: float = FRESH_S) -> bool:
    data = read()
    if not data:
        return False
    if time.time() - float(data.get("ts", 0) or 0) > fresh_s:
        return False
    pid = int(data.get("pid", 0) or 0)
    if pid > 0:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
    return True


__all__ = ["write", "clear", "read", "live", "path", "SCHEMA"]
