"""Cross-process audio-device ownership.

PortAudio/ALSA is not safe for concurrent access: two Qubit processes opening
the same device can segfault in ``libasound``
(``snd_pcm_poll_descriptors_revents``). The running service is the audio owner;
CLI commands defer to it instead of opening the device a second time.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import Optional, TextIO

_OWNER_ENV = "QUBIT_AUDIO_OWNER"


def _lock_path() -> Path:
    try:
        from ..config import canonical_state_dir
        base = Path(canonical_state_dir())
    except Exception:
        base = Path(os.path.expanduser("~/.local/state/qubit-voicebridge"))
    p = base / "audio-owner.lock"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return p


def acquire_service_owner() -> Optional[TextIO]:
    """Service startup: hold the audio-owner lock for the process lifetime.

    Returns the open lock fd (keep a reference to hold it), or ``None`` if
    another process already owns audio.
    """
    try:
        f = open(_lock_path(), "a+")
    except OSError:
        return None
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    os.environ[_OWNER_ENV] = "1"
    return f


def is_service_owner() -> bool:
    """True when THIS process is the audio owner (the running service)."""
    return bool(os.environ.get(_OWNER_ENV))


def service_holds_audio() -> bool:
    """True when ANOTHER process currently owns the audio device."""
    if is_service_owner():
        return False
    try:
        f = open(_lock_path(), "a+")
    except OSError:
        return False
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return True
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()
    return False
