"""WebRTC noise suppression / echo cancellation capture via PulseAudio.

Firefox/Chrome capture through ``getUserMedia``, which applies AGC + noise
suppression + echo cancellation. This recreates that capture front-end for the
desktop helper using PulseAudio's ``module-echo-cancel`` with ``aec_method=
webrtc`` (the system ``libwebrtc_audio_processing`` library), producing a
virtual source ``qubit_aec`` that the helper records from. The SAME STT engine
and socket are used, so transcript quality matches the browser's "local STT".

Best-effort and reversible: if PulseAudio/``pactl`` is missing or the module
cannot load, capture falls back to the system default source.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Any, Optional

SOURCE_NAME = "qubit_aec"
SINK_NAME = "qubit_aec_out"
_LOADED_MODULE: Optional[str] = None


def enabled(cfg: Any) -> bool:
    try:
        return bool(cfg.get("voice_edit", "noise_suppression", default=False))
    except Exception:
        return False


def _run(args: list, *, timeout: float = 8.0) -> Optional[str]:
    if not shutil.which("pactl"):
        return None
    try:
        p = subprocess.run(["pactl", *args], capture_output=True, text=True,
                           timeout=timeout, check=False)
        return (p.stdout or "").strip()
    except Exception:
        return None


def _source_exists(name: str) -> bool:
    out = _run(["list", "short", "sources"])
    return bool(out) and any(line.split()[1] == name
                             for line in out.splitlines() if len(line.split()) > 1)


def ensure(cfg: Any = None) -> str:
    """Return the noise-suppressed source name, creating it if necessary."""
    global _LOADED_MODULE
    if _source_exists(SOURCE_NAME):
        return SOURCE_NAME
    out = _run(["load-module", "module-echo-cancel",
                f"source_name={SOURCE_NAME}", f"sink_name={SINK_NAME}",
                "aec_method=webrtc"])
    if out and out.isdigit():
        _LOADED_MODULE = out
        return SOURCE_NAME
    return ""


def release() -> None:
    """Unload the module only if this process loaded it."""
    global _LOADED_MODULE
    if _LOADED_MODULE:
        _run(["unload-module", _LOADED_MODULE])
        _LOADED_MODULE = None


def resolve_capture_device(cfg: Any) -> str:
    """The PulseAudio source the helper should record from.

    Precedence: an explicit ``voice_edit.asr_device`` wins; then
    ``voice_edit.capture_source`` (``auto`` | ``raw`` | ``aec``). The measured
    echo-cancel output is ~8x quieter than the raw interface and the plain
    device already carries AGC on top, so ``auto`` uses the raw source; the
    ``aec`` mode is available when echo cancellation is specifically wanted.
    """
    try:
        explicit = str(cfg.get("voice_edit", "asr_device", default="") or "")
    except Exception:
        explicit = ""
    if explicit:
        return explicit
    try:
        mode = str(cfg.get("voice_edit", "capture_source", default="auto")
                   or "auto").lower()
    except Exception:
        mode = "auto"
    if mode == "aec":
        return ensure(cfg)
    # "auto" and "raw" both use the plain device: its native capture already
    # reaches a usable level, while the echo-cancel source measured ~8x quieter.
    return ""


__all__ = ["ensure", "release", "resolve_capture_device", "enabled",
           "SOURCE_NAME"]
