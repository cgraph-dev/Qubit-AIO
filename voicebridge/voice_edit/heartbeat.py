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


def _pid_is_helper(pid: int) -> bool:
    """True when ``pid``'s cmdline looks like a Qubit desktop helper.

    Guards against a recycled PID: the heartbeat records a bare pid, so a
    stale file could otherwise point at an unrelated process. Reads
    ``/proc/<pid>/cmdline``; when that is unavailable (non-Linux) it returns
    False — a takeover must never signal a process it cannot identify.
    Never raises.
    """
    try:
        raw = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
    except Exception:
        return False
    return "voicebridge.desktop_helper" in raw.replace(b"\0", b" ").decode(
        "utf-8", "replace")


def other_live_pid(exclude: int = 0) -> Optional[int]:
    """A live desktop-helper PID other than ``exclude``, else ``None``.

    Two helpers both open the mic and both run a wake loop, so one spoken
    phrase fires two turns. The service instance takes over from a leftover
    helper at startup using this. Requires a fresh heartbeat, a running pid,
    and a helper cmdline — a stale file, dead pid, or unrelated process is
    ignored. Never raises.

    On a host without ``/proc`` (macOS/Windows) cmdline validation is
    unavailable and this returns ``None`` (no takeover).
    """
    if not live():
        return None
    data = read() or {}
    try:
        pid = int(data.get("pid", 0) or 0)
    except (TypeError, ValueError):
        return None
    if pid <= 0 or pid == int(exclude or 0):
        return None
    if not _pid_is_helper(pid):
        return None
    return pid


__all__ = ["write", "clear", "read", "live", "other_live_pid", "path", "SCHEMA"]
