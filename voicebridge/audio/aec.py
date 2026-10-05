"""Slice-5 PipeWire AEC recipe (WebRTC echo cancellation).

Loads ``module-echo-cancel`` (``aec_method=webrtc``) with EXPLICIT masters —
the Scarlett mic as the capture master, the Scarlett output as the playback
reference — so the echo-cancelled source ``qubit_aec`` subtracts exactly what
the speakers play (own TTS replies, browser audio). ASR/KWS consume it via
the existing sanctioned flag ``voice_edit.capture_source: aec`` (see
``voicebridge/voice_edit/noise.py::resolve_capture_device``); ``auto``
(default) keeps the raw mic until the owner-eared test passes.

Source/sink names are identical to ``noise.SOURCE_NAME``/``noise.SINK_NAME``
(pinned by test) so both layers address the same source; this module never
imports ``voice_edit`` (no layer cycle — pure pactl + config read).

The 5 s journal-skip TTS guard (``desktop_helper.recent_tts_speak``) stays as
belt-and-braces until AEC is proven live by the owner-eared test.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Any, Dict, Optional

#: Canonical echo-cancelled source/sink (must equal noise.SOURCE_NAME/_SINK).
SOURCE_NAME = "qubit_aec"
SINK_NAME = "qubit_aec_out"

#: Explicit masters measured on this host (PipeWire 1.0.5).
DEFAULT_SOURCE_MASTER = (
    "alsa_input.usb-Focusrite_Scarlett_2i2_4th_Gen_S2F35P9350962D-00"
    ".analog-surround-40"
)
DEFAULT_SINK_MASTER = (
    "alsa_output.usb-Focusrite_Scarlett_2i2_4th_Gen_S2F35P9350962D-00"
    ".iec958-stereo"
)

#: Sanctioned config flag enabling the AEC consumer switch.
AEC_CONFIG_KEY = "capture_source"
AEC_MODE_VALUE = "aec"


def _run(args: list, *, timeout: float = 8.0) -> Optional[str]:
    if not shutil.which("pactl"):
        return None
    try:
        p = subprocess.run(["pactl", *args], capture_output=True, text=True,
                           timeout=timeout, check=False)
        if p.returncode != 0:
            return None
        return (p.stdout or "").strip()
    except Exception:
        return None


def _source_exists(name: str) -> bool:
    out = _run(["list", "short", "sources"])
    return bool(out) and any(
        len(line.split()) > 1 and line.split()[1] == name
        for line in out.splitlines())


def _sink_exists(name: str) -> bool:
    out = _run(["list", "short", "sinks"])
    return bool(out) and any(
        len(line.split()) > 1 and line.split()[1] == name
        for line in out.splitlines())


def load(source_master: str = DEFAULT_SOURCE_MASTER,
         sink_master: str = DEFAULT_SINK_MASTER) -> str:
    """Ensure the echo-cancelled source exists; return module id or "".

    Idempotent: when ``qubit_aec`` already exists nothing is loaded and the
    source name is returned. Otherwise loads ``module-echo-cancel`` with the
    explicit masters and returns the numeric module id ("" on failure).
    """
    if _source_exists(SOURCE_NAME):
        return SOURCE_NAME
    out = _run(["load-module", "module-echo-cancel",
                f"source_master={source_master}",
                f"sink_master={sink_master}",
                f"source_name={SOURCE_NAME}",
                f"sink_name={SINK_NAME}",
                "aec_method=webrtc"])
    return out if out and out.isdigit() else ""


def unload(module_id: str) -> bool:
    """Unload one echo-cancel module by id. Never unloads anything else."""
    if not module_id or not str(module_id).isdigit():
        return False
    return _run(["unload-module", str(module_id)]) is not None


def status() -> Dict[str, Any]:
    """Observable AEC state (never raises; missing pactl → all False)."""
    pactl = shutil.which("pactl") is not None
    src = _source_exists(SOURCE_NAME)
    sink = _sink_exists(SINK_NAME)
    info = _run(["info"]) or ""
    default_src = ""
    for line in info.splitlines():
        if line.startswith("Default Source:"):
            default_src = line.split(":", 1)[1].strip()
    spec = ""
    sources = _run(["list", "short", "sources"]) or ""
    for line in sources.splitlines():
        parts = line.split()
        if len(parts) > 1 and parts[1] == SOURCE_NAME:
            spec = " ".join(parts[2:])
    return {
        "loaded": bool(src),
        "source_present": bool(src),
        "sink_present": bool(sink),
        "is_default_source": bool(default_src) and default_src == SOURCE_NAME,
        "default_source": default_src,
        "source_spec": spec,
        "source_master": DEFAULT_SOURCE_MASTER,
        "sink_master": DEFAULT_SINK_MASTER,
        "pactl_available": pactl,
    }


def set_default_source(name: str = SOURCE_NAME) -> Optional[str]:
    """Pin the Pulse default source; return the previous default (or None)."""
    prev = str((status().get("default_source") or ""))
    if _run(["set-default-source", name]) is None:
        return None
    return prev or None


def is_aec_enabled(cfg: Any) -> bool:
    """True when the sanctioned flag selects the AEC source."""
    try:
        return str(cfg.get("voice_edit", AEC_CONFIG_KEY,
                           default="auto") or "").lower() == AEC_MODE_VALUE
    except Exception:
        return False


def recipe_text() -> str:
    """Persistence recipe: re-apply the AEC source after login/reboot."""
    return (
        "# Qubit Slice-5 AEC recipe — PipeWire module-echo-cancel (WebRTC).\n"
        "# One-shot (explicit masters so the reference is the Scarlett out):\n"
        f"pactl load-module module-echo-cancel "
        f"source_master={DEFAULT_SOURCE_MASTER} "
        f"sink_master={DEFAULT_SINK_MASTER} "
        f"source_name={SOURCE_NAME} sink_name={SINK_NAME} "
        "aec_method=webrtc\n"
        "# Persistent (per-user PipeWire — runs after pipewire-pulse starts):\n"
        "#   mkdir -p ~/.config/pipewire/pipewire-pulse.conf.d/\n"
        "#   add to ~/.config/pipewire/pipewire-pulse.conf.d/qubit-aec.conf:\n"
        "#     context.exec = [\n"
        "#       { path = \"pactl\" args = \"load-module module-echo-cancel "
        f"source_master={DEFAULT_SOURCE_MASTER} "
        f"sink_master={DEFAULT_SINK_MASTER} "
        f"source_name={SOURCE_NAME} sink_name={SINK_NAME} "
        "aec_method=webrtc\" }\n"
        "#     ]\n"
        "# Consumer switch (default OFF until the owner-eared test passes):\n"
        "#   voice_edit.capture_source: aec   # auto|raw keep the raw mic\n"
    )


__all__ = ["SOURCE_NAME", "SINK_NAME", "DEFAULT_SOURCE_MASTER",
           "DEFAULT_SINK_MASTER", "AEC_CONFIG_KEY", "AEC_MODE_VALUE",
           "load", "unload", "status", "set_default_source",
           "is_aec_enabled", "recipe_text"]
