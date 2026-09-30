"""Short local audio cues for the voice surface (wake heard / listening ready).

Generated from a sine wave at first use and cached, so there is no dependency
on a system sound theme. Playback is best-effort, non-blocking, and never raises.
The cues are short and quiet; they are never fed to ASR (the wake monitor feeds
the microphone, not the sink).
"""
from __future__ import annotations

import math
import struct
import subprocess
import wave
from pathlib import Path
from typing import Any, Optional

# Live player processes are kept referenced so a running Popen is never
# garbage-collected (which would warn/RuntimeError under test runners).
_PROCS: list = []


# kind -> (freq_hz, beeps, beep_ms, gap_ms)
_CUES = {
    "wake": (880.0, 2, 90, 60),
    "ready": (660.0, 1, 90, 0),
}


def _cache_dir() -> Path:
    try:
        from ..config import canonical_cache_dir
        return Path(canonical_cache_dir())
    except Exception:
        import os
        return Path(os.path.expanduser("~/.cache/qubit-voicebridge"))


def _wav_path(kind: str) -> Path:
    freq, beeps, ms, gap = _CUES.get(kind, _CUES["ready"])
    return _cache_dir() / f"qubit-voice-{kind}-{int(freq)}.wav"


def _generate(path: Path, kind: str) -> Path:
    freq, beeps, ms, gap = _CUES.get(kind, _CUES["ready"])
    sr = 16000
    samples = bytearray()
    for i in range(beeps):
        n = int(sr * ms / 1000)
        fade = max(1, int(sr * 0.006))
        for j in range(n):
            amp = 0.28
            if j < fade:
                amp *= j / fade
            elif j > n - fade:
                amp *= (n - j) / fade
            val = int(amp * 32767 * math.sin(2 * math.pi * freq * j / sr))
            samples += struct.pack("<h", val)
        samples += b"\x00\x00" * int(sr * gap / 1000)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(samples))
    tmp.replace(path)
    return path


def _player() -> Optional[str]:
    import shutil
    for name in ("paplay", "pw-play", "aplay"):
        if shutil.which(name):
            return name
    return None


def beep(cfg: Any = None, kind: str = "wake") -> bool:
    """Play a cue if enabled. Never raises; returns True when it was started."""
    try:
        if not _enabled(cfg):
            return False
        player = _player()
        if not player:
            return False
        path = _wav_path(kind)
        if not path.is_file():
            path = _generate(path, kind)
        proc = subprocess.Popen([player, str(path)],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                start_new_session=True)
        _PROCS.append(proc)
        _PROCS[:] = [p for p in _PROCS if p.poll() is None]
        return True
    except Exception:
        return False


def _enabled(cfg: Any) -> bool:
    if cfg is None:
        return True
    try:
        return bool(cfg.get("voice_edit", "beep", default=True))
    except Exception:
        return True


__all__ = ["beep"]
