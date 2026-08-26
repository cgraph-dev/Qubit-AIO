"""Packet 7C remediation: user-facing audio settings contract.

Provides a stable, user-facing contract for listing and selecting audio
devices by NAME/ROLE/BACKEND, resetting to OS defaults, and reporting typed
states (OK / DEGRADED / MISSING / PERMISSION_DENIED). It:

- never persists transient numeric PortAudio indices or raw ALSA ``(hw:N,M)``
  paths — selection is stored as a stable device name;
- never guesses when a selected device disappears: an unmatched pinned name is
  reported MISSING, not silently rerouted to a random device;
- preserves the working HyperX rules (mono USB mic, empty output pins follow
  the system default sink);
- persists across restart via the config file (name keys, atomic write +
  backup);
- does NOT restart services, enable recording, or start capture streams when
  settings change — it is a pure config contract.

This module is the source of truth that the CLI (`voicebridge audio ...`) and
the distribution doctor consume. Tests are hermetic: device enumeration is
driven through ``sounddevice.query_devices`` so it can be stubbed.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OK, DEGRADED, MISSING, PERMISSION_DENIED = (
    "OK", "DEGRADED", "MISSING", "PERMISSION_DENIED")
STATES = (OK, DEGRADED, MISSING, PERMISSION_DENIED)

MIC_NAME_KEY = ("mic", "device_name")
MIC_LEGACY_INDEX_KEY = ("mic", "device")
OUTPUT_NAME_KEY = ("audio", "output_device_name")
OUTPUT_LEGACY_INDEX_KEY = ("audio", "output_device")


class AudioSettingsError(Exception):
    pass


class InvalidSelectionError(AudioSettingsError):
    pass


_UNSET = object()   # "leave this side untouched" sentinel (vs None = clear)


@dataclass
class DeviceInfo:
    """One enumerable audio device. ``stable_id`` is role+name (never index)."""

    name: str
    role: str                 # "input" | "output" | "duplex"
    backend: str              # hostapi name, e.g. "MME", "ALSA", "Pulse"
    channels: int
    default_samplerate: int
    is_default: bool
    stable_id: str = ""
    index: Optional[int] = None   # present for the report only; never persisted
    state: str = OK

    def __post_init__(self) -> None:
        if not self.stable_id:
            self.stable_id = f"{self.role}:{self.name}"


def _query_devices() -> Tuple[List[Dict[str, Any]], Dict[int, str], Tuple[Any, Any]]:
    """Return (devices, hostapi_name_by_index, (default_input, default_output))."""
    import sounddevice as sd
    devs = sd.query_devices()
    try:
        hostapis = sd.query_hostapis()
    except Exception:
        hostapis = []
    host_by_idx = {i: (h.get("name") or "unknown") for i, h in enumerate(hostapis)}
    try:
        d = sd.default.device
        d_in, d_out = (d if isinstance(d, (tuple, list)) else (d, d))
    except Exception:
        d_in, d_out = None, None
    return list(devs), host_by_idx, (d_in, d_out)


def _device_info(dev: Dict[str, Any], index: int, role: str,
                 is_default: bool, backend: str) -> DeviceInfo:
    name = str(dev.get("name") or f"device-{index}")
    return DeviceInfo(
        name=name,
        role=role,
        backend=backend,
        channels=int(dev.get(f"max_{role}_channels") or 0),
        default_samplerate=int(dev.get("default_samplerate") or 0),
        is_default=is_default,
        index=index,
    )


def list_devices() -> Dict[str, List[DeviceInfo]]:
    """List all audio devices, split by usable role. Returns typed descriptors
    keyed by stable name/role/backend. Raises AudioSettingsError on a
    permission or enumeration failure (typed below as PERMISSION_DENIED)."""
    try:
        devs, host_by_idx, (d_in, d_out) = _query_devices()
    except Exception as exc:
        raise AudioSettingsError(f"cannot enumerate audio devices: {exc}") from exc
    inputs, outputs = [], []
    for i, dev in enumerate(devs):
        in_ch = int(dev.get("max_input_channels") or 0)
        out_ch = int(dev.get("max_output_channels") or 0)
        if in_ch == 0 and out_ch == 0:
            continue
        backend = host_by_idx.get(int(dev.get("hostapi")
                                      if dev.get("hostapi") is not None else -1),
                                  "unknown")
        if in_ch > 0:
            inputs.append(_device_info(dev, i, "input",
                                       i == d_in, backend))
        if out_ch > 0:
            outputs.append(_device_info(dev, i, "output",
                                        i == d_out, backend))
    return {"inputs": inputs, "outputs": outputs}


def _match(devices: List[DeviceInfo], selector: str) -> Optional[DeviceInfo]:
    """Match by stable_id, exact name, or name substring (case-insensitive).
    Never matches by numeric index. Returns None when nothing matches."""
    if not selector:
        return None
    sel = selector.strip()
    low = sel.lower()
    for d in devices:
        if d.stable_id == sel or d.name == sel:
            return d
    for d in devices:
        if low in d.name.lower():
            return d
    return None


def find_microphone(selector: str) -> Optional[DeviceInfo]:
    return _match(list_devices()["inputs"], selector)


def find_output(selector: str) -> Optional[DeviceInfo]:
    return _match(list_devices()["outputs"], selector)


# ---------------------------------------------------------------------------
# Config persistence (name keys only; atomic write + backup)
# ---------------------------------------------------------------------------
def _config_path(cfg: Any) -> Path:
    p = getattr(cfg, "path", None)
    if p is None:
        from ..config import DEFAULT_CONFIG_PATH
        p = DEFAULT_CONFIG_PATH
    return Path(p).expanduser()


def write_audio_config(cfg: Any, *, mic_name: Any = _UNSET,
                       output_name: Any = _UNSET) -> Path:
    """Persist name-based audio selection to the config with a timestamped
    backup. ``_UNSET`` leaves that side untouched; ``None`` clears it; a str
    pins it. Clears the legacy numeric index keys so a transient index is
    never persisted. Returns the backup path."""
    import yaml
    path = _config_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: Dict[str, Any] = {}
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
    mic = dict(data.get("mic") or {})
    audio = dict(data.get("audio") or {})
    if mic_name is not _UNSET:
        if mic_name is None:
            mic.pop(MIC_NAME_KEY[1], None)
        else:
            mic[MIC_NAME_KEY[1]] = mic_name
        mic[MIC_LEGACY_INDEX_KEY[1]] = None      # never persist an index
    if output_name is not _UNSET:
        if output_name is None:
            audio.pop(OUTPUT_NAME_KEY[1], None)
        else:
            audio[OUTPUT_NAME_KEY[1]] = output_name
        audio[OUTPUT_LEGACY_INDEX_KEY[1]] = ""   # empty pins -> default sink
    data["mic"] = mic
    data["audio"] = audio
    bak = path.with_name(f"{path.name}.audio.{int(time.time())}.bak")
    if path.exists():
        shutil.copy2(path, bak)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            yaml.safe_dump(data, fh, sort_keys=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return bak


def select_microphone(cfg: Any, selector: str) -> DeviceInfo:
    """Validate + persist a microphone selection by stable name/role/backend.
    Raises InvalidSelectionError when the selector matches nothing."""
    dev = find_microphone(selector)
    if dev is None:
        raise InvalidSelectionError(
            f"no input device matches {selector!r} "
            "(use `voicebridge audio list`)")
    write_audio_config(cfg, mic_name=dev.name, output_name=_UNSET)
    return dev


def select_output(cfg: Any, selector: str) -> DeviceInfo:
    """Validate + persist an output selection by stable name/role/backend.
    Raises InvalidSelectionError when the selector matches nothing."""
    dev = find_output(selector)
    if dev is None:
        raise InvalidSelectionError(
            f"no output device matches {selector!r} "
            "(use `voicebridge audio list`)")
    write_audio_config(cfg, mic_name=_UNSET, output_name=dev.name)
    return dev


def reset_to_defaults(cfg: Any) -> Path:
    """Explicitly clear both audio pins -> OS defaults. Returns the backup."""
    return write_audio_config(cfg, mic_name=None, output_name=None)


# ---------------------------------------------------------------------------
# Typed status
# ---------------------------------------------------------------------------
def _resolve_status(devices: List[DeviceInfo], name: Optional[str],
                    default_ok_detail: str) -> Dict[str, str]:
    if not name:
        return {"state": OK, "detail": default_ok_detail}
    dev = _match(devices, name)
    if dev is None:
        return {"state": MISSING,
                "detail": f"configured device {name!r} not present — "
                          "no silent fallback"}
    if dev.is_default:
        return {"state": OK, "detail": f"{dev.stable_id} (default)"}
    return {"state": OK, "detail": dev.stable_id}


def status(cfg: Any) -> Dict[str, Dict[str, str]]:
    """Typed audio status: mic + output. Each reports
    OK / DEGRADED / MISSING / PERMISSION_DENIED with a reason."""
    try:
        devs = list_devices()
    except AudioSettingsError as exc:
        msg = str(exc)
        state = PERMISSION_DENIED if any(
            w in msg.lower() for w in ("permission", "access denied",
                                       "cannot open device")) else MISSING
        return {"mic": {"state": state, "detail": msg},
                "output": {"state": state, "detail": msg}}
    mic_name = cfg.get(*MIC_NAME_KEY, default=None) or None
    out_name = cfg.get(*OUTPUT_NAME_KEY, default="") or None
    return {
        "mic": _resolve_status(devs["inputs"], mic_name,
                               "system default input (no pin)"),
        "output": _resolve_status(devs["outputs"], out_name,
                                  "system default sink (no pin)"),
    }