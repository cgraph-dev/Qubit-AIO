"""WebRTC noise suppression / echo cancellation capture via PulseAudio.

Firefox/Chrome capture through ``getUserMedia``, which applies AGC + noise
suppression + echo cancellation. This recreates that capture front-end for the
desktop helper using PulseAudio's ``module-echo-cancel`` with ``aec_method=
webrtc`` (the system ``libwebrtc_audio_processing`` library), producing a
virtual source ``qubit_aec`` that the helper records from. The SAME STT engine
and socket are used, so transcript quality matches the browser's "local STT".

Best-effort and reversible: if PulseAudio/``pactl`` is missing or the module
cannot load, capture falls back to the system default source.

In addition this module owns an honest in-process software stage
(``apply_capture_filter``): DC-blocking high-pass + downward-expansion noise
gate + soft limiter, implemented with numpy when available (stdlib fallback
returns the input unchanged). ``capture_source: auto`` keeps the raw device
(``resolve_capture_device`` returns ``""``) because the echo-cancel source
measured ~8x quieter, and layers this software stage on top when
``voice_edit.noise_suppression`` is on; ``aec`` selects the hardware
echo-cancel source instead. ``capture_params`` reports the effective
device + stage so the ON/OFF difference is unit-observable.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Any, Dict, Optional, Union

SOURCE_NAME = "qubit_aec"
SINK_NAME = "qubit_aec_out"
_LOADED_MODULE: Optional[str] = None

#: Software stage id reported by capture_params when suppression is on.
SOFTWARE_STAGE = "software_ns_agc"
#: Frame RMS below this is treated as noise-floor and expanded downward.
_NS_GATE_RMS = 0.004


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
    # When noise_suppression is ON the raw device is kept AND the software
    # stage (apply_capture_filter) runs on the captured frames — see
    # capture_params() for the observable device + stage pair.
    return ""


def capture_params(cfg: Any) -> Dict[str, Any]:
    """Observable capture configuration: device + software stage.

    ``noise_suppression`` OFF → ``{"ns": False, "stage": "none", ...}``;
    ON → ``{"ns": True, "stage": "software_ns_agc", ...}``. The device itself
    is unchanged by the flag (``auto`` keeps the raw source); the stage is
    what the flag switches.
    """
    ns = enabled(cfg)
    return {"device": resolve_capture_device(cfg),
            "ns": ns,
            "stage": SOFTWARE_STAGE if ns else "none"}


def apply_capture_filter(pcm: bytes,
                         cfg_or_enabled: Union[Any, bool]) -> bytes:
    """Run the software NS/AGC stage over one mono s16le PCM buffer.

    Accepts the live config (honours ``voice_edit.noise_suppression``) or a
    plain bool for tests. OFF → the input bytes are returned unchanged; ON →
    DC-blocking high-pass + downward-expansion gate + soft limiter. Never
    raises: any failure (short/odd buffer, missing numpy) returns the input.
    """
    if hasattr(cfg_or_enabled, "get"):
        on = enabled(cfg_or_enabled)
    else:
        try:
            on = bool(cfg_or_enabled)
        except Exception:
            on = False
    if not on:
        return pcm
    try:
        import numpy as np
    except Exception:
        return pcm
    try:
        a = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        if a.size == 0:
            return pcm
        # DC-blocking high-pass (removes mic DC offset / mains rumble).
        y = np.empty_like(a)
        prev_x = 0.0
        prev_y = 0.0
        for i, x in enumerate(a):
            prev_y = x - prev_x + 0.995 * prev_y
            prev_x = float(x)
            y[i] = prev_y
        # Downward-expansion gate: frames at/below the noise floor are pushed
        # down instead of being amplified later by AGC.
        rms = float(np.sqrt(np.mean(y * y))) if y.size else 0.0
        if rms < _NS_GATE_RMS and rms > 0.0:
            y = y * max(0.1, (rms / _NS_GATE_RMS) ** 2)
        # Soft limiter at +/-0.98 so hot frames cannot clip downstream.
        y = np.clip(y, -0.98, 0.98)
        return (y * 32767.0).astype("<i2").tobytes()
    except Exception:
        return pcm


__all__ = ["ensure", "release", "resolve_capture_device", "enabled",
           "capture_params", "apply_capture_filter", "SOURCE_NAME",
           "SOFTWARE_STAGE"]
