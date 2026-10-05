"""Orb position persistence (Qubit state dir, never the repo).

The file holds exactly ``{"x", "y", "collapsed"}`` -- geometry only,
no secrets, no transcripts. All reads are fail-closed (missing /
corrupt / wrong-shape -> ``None``, never raises); all writes are
atomic (tmp + replace) and never raise.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from .geometry import OrbPosition

ORB_STATE_FILENAME = "voice_orb.json"
ORB_PID_FILENAME = "voice_orb.pid"


def state_dir() -> Path:
    """Qubit state dir (same authority as the helper heartbeat file)."""
    try:
        from voicebridge.config import canonical_state_dir
        return Path(canonical_state_dir())
    except Exception:
        return Path(os.path.expanduser("~/.local/state/qubit-voicebridge"))


def default_path() -> Path:
    return state_dir() / ORB_STATE_FILENAME


def pid_path() -> Path:
    return state_dir() / ORB_PID_FILENAME


def load_position(path: Any = None) -> Optional[OrbPosition]:
    try:
        raw = Path(path) if path is not None else default_path()
        data = json.loads(raw.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        x, y = data.get("x"), data.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return None
        if not (abs(x) != float("inf") and abs(y) != float("inf")):
            return None
        return OrbPosition(x=int(x), y=int(y),
                           collapsed=data.get("collapsed") is True)
    except Exception:
        return None


def save_position(path: Any, pos: OrbPosition) -> None:
    try:
        target = Path(path) if path is not None else default_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"x": int(pos.x), "y": int(pos.y),
                              "collapsed": bool(pos.collapsed)})
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".orb-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except Exception:
        pass


__all__ = [
    "ORB_STATE_FILENAME",
    "ORB_PID_FILENAME",
    "state_dir",
    "default_path",
    "pid_path",
    "load_position",
    "save_position",
]
