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
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OK, DEGRADED, MISSING, PERMISSION_DENIED = (
    "OK", "DEGRADED", "MISSING", "PERMISSION_DENIED")
STATES = (OK, DEGRADED, MISSING, PERMISSION_DENIED)

MIC_NAME_KEY = ("mic", "device_name")
MIC_LEGACY_INDEX_KEY = ("mic", "device")
OUTPUT_NAME_KEY = ("audio", "output_device_name")
OUTPUT_LEGACY_INDEX_KEY = ("audio", "output_device")

# Raw ALSA path fragments that must NEVER survive as a stable identifier:
# "(hw:1,0)", "hw:1,0", or a bare transient numeric index.
_HW_PATH_RE = re.compile(r"\(\s*hw:\d+,\s*\d+\s*\)")
_HW_NAKED_RE = re.compile(r"(^|\s)hw:\d+,\s*\d+")
_TRAILING_SEP_RE = re.compile(r"[\s:]+$")
_NUMERIC_ONLY_RE = re.compile(r"^\d+$")


class AudioSettingsError(Exception):
    pass


class InvalidSelectionError(AudioSettingsError):
    pass


_UNSET = object()   # "leave this side untouched" sentinel (vs None = clear)


@dataclass
class DeviceInfo:
    """One enumerable audio device. ``stable_id`` is role+name+backend built
    ONLY from the NORMALIZED name — never a raw ALSA (hw:N,M) path and never a
    transient PortAudio index. ``raw_name``/``index`` are report-only fields
    for diagnostics and are never persisted or used for matching/selection."""

    name: str                      # normalized stable name (no hw path, no index)
    role: str                      # "input" | "output" | "duplex"
    backend: str                   # hostapi name, e.g. "MME", "ALSA", "Pulse"
    channels: int
    default_samplerate: int
    is_default: bool
    stable_id: str = ""
    raw_name: str = ""             # report-only; never a stable identifier
    index: Optional[int] = None    # report-only; never persisted or selected
    state: str = OK

    def __post_init__(self) -> None:
        if not self.stable_id:
            self.stable_id = f"{self.role}:{self.name}:{self.backend}"


def normalize_device_name(raw: Optional[str]) -> str:
    """Turn a raw sounddevice name into a stable, friendly identifier that
    NEVER contains a raw ALSA ``(hw:N,M)`` path, a naked ``hw:N,M`` token, or a
    transient numeric index. Approved aggregate identities (``pulse``,
    ``pipewire``, ``default``) pass through unchanged."""
    if not raw:
        return ""
    name = str(raw)
    name = _HW_PATH_RE.sub("", name)        # drop "(hw:1,0)" suffixes
    name = _HW_NAKED_RE.sub("", name)       # drop bare "hw:1,0" tokens
    name = _TRAILING_SEP_RE.sub("", name).strip()   # trailing " :" leftovers
    name = re.sub(r"\s{2,}", " ", name).strip()
    if _NUMERIC_ONLY_RE.match(name or ""):
        return ""                           # a bare index is not a name
    return name


def is_clean_identifier(name: Optional[str]) -> bool:
    """True when a name/selector is a stable identifier: no hw path, no
    numeric index."""
    if not name:
        return True
    s = str(name).strip()
    if _NUMERIC_ONLY_RE.match(s):
        return False
    if _HW_PATH_RE.search(s) or _HW_NAKED_RE.search(s):
        return False
    return True


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


def _pulse_input_source_names() -> List[str]:
    """Names of Pulse/PipeWire INPUT sources (alsa_input.*). A device that
    PortAudio lists with 0 input channels (e.g. the Scarlett 2i2, whose input
    is a separate ALSA PCM) is still a valid mic when its name tokens match a
    real Pulse input source — the bridge captures through that aggregate."""
    try:
        import subprocess
        out = subprocess.run(["pactl", "list", "sources", "short"],
                             capture_output=True, text=True, timeout=6)
        names = []
        for line in (out.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].startswith("alsa_input."):
                names.append(parts[1])
        return names
    except Exception:
        return []


def _name_tokens_match(a: str, b: str) -> bool:
    """Punctuation/space/underscore-insensitive token overlap: every
    significant token of `a` appears in `b` (used to match a PortAudio name to
    a pactl source name). ALSA hardware tokens (``hw`` and bare digits from
    ``(hw:1,0)``) are ignored — they are transient indices, never stable
    identifiers."""
    import re as _re
    _GENERIC = {"audio", "usb", "digital", "analog", "input", "output",
                "monitor", "capture", "device", "interface", "usbaudio",
                "multichannel", "alsa", "hw"}
    split = r"[\s_:.,()\-]+"

    def _sig(tok):
        return bool(tok) and tok not in _GENERIC and not tok.isdigit()

    at = [t for t in _re.split(split, a.lower()) if _sig(t)]
    bt = [t for t in _re.split(split, b.lower()) if _sig(t)]
    if not at:
        return False
    return all(t in bt for t in at)


def _device_info(dev: Dict[str, Any], index: int, role: str,
                 is_default: bool, backend: str) -> DeviceInfo:
    raw_name = str(dev.get("name") or f"device-{index}")
    name = normalize_device_name(raw_name) or backend or f"device-{index}"
    return DeviceInfo(
        name=name,
        role=role,
        backend=backend,
        channels=int(dev.get(f"max_{role}_channels") or 0),
        default_samplerate=int(dev.get("default_samplerate") or 0),
        is_default=is_default,
        raw_name=raw_name,
        index=index,
    )


def list_devices() -> Dict[str, List[DeviceInfo]]:
    """List all audio devices, split by usable role. Returns typed descriptors
    keyed by stable name/role/backend. Raises AudioSettingsError on a
    permission or enumeration failure (typed below as PERMISSION_DENIED).

    A device whose raw PortAudio input-channel count is 0 is STILL listed as an
    input when its name tokens match a real Pulse/PipeWire input source (the
    Scarlett 2i2 input is a separate ALSA PCM that PortAudio surfaces as 0
    channels but PipeWire exposes as analog-surround-40). This is NOT a silent
    fallback or a numeric index — it is the same capture route the bridge
    already uses (the pulse/default aggregate)."""
    try:
        devs, host_by_idx, (d_in, d_out) = _query_devices()
    except Exception as exc:
        raise AudioSettingsError(f"cannot enumerate audio devices: {exc}") from exc
    pulse_inputs = _pulse_input_source_names()
    inputs, outputs = [], []
    for i, dev in enumerate(devs):
        in_ch = int(dev.get("max_input_channels") or 0)
        out_ch = int(dev.get("max_output_channels") or 0)
        if in_ch == 0 and out_ch == 0:
            continue
        backend = host_by_idx.get(int(dev.get("hostapi")
                                      if dev.get("hostapi") is not None else -1),
                                  "unknown")
        raw_name = str(dev.get("name") or f"device-{i}")
        # Enrich: if PortAudio shows 0 input channels but the device's name
        # tokens match a real Pulse input source, treat it as an input (this is
        # how the Scarlett 2i2 / AT2020 is actually captured).
        has_pulse_input = bool(
            pulse_inputs and any(_name_tokens_match(raw_name, src)
                                 for src in pulse_inputs))
        if in_ch > 0 or has_pulse_input:
            inputs.append(_device_info(dev, i, "input",
                                       i == d_in, backend))
        if out_ch > 0:
            outputs.append(_device_info(dev, i, "output",
                                        i == d_out, backend))
    return {"inputs": inputs, "outputs": outputs}


def _match(devices: List[DeviceInfo], selector: str) -> Optional[DeviceInfo]:
    """Match by stable_id (exact), name (exact/substring), a role-prefixed
    name like ``input:pulse``, or a FULL stable id (role:name:backend). A bare
    backend name is only used as a LAST-RESORT match so it never shadows an
    explicit device name. Never matches by numeric index. Returns None when
    nothing matches."""
    if not selector:
        return None
    sel = selector.strip()
    low = sel.lower()
    for d in devices:
        if d.stable_id == sel or d.name == sel:
            return d
    # role-prefixed partial stable id: "input:pulse" -> match name "pulse"
    if ":" in sel:
        _role, _, _rest = sel.partition(":")
        if _role in ("input", "output", "duplex") and _rest:
            for d in devices:
                if d.role == _role and _rest.lower() in d.name.lower():
                    return d
    for d in devices:
        if low in d.name.lower():
            return d
    # last resort: an explicit backend identity (only if no name matched)
    for d in devices:
        if d.backend.lower() == low:
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
        from ..config import resolve_config_path
        p = resolve_config_path()
    return Path(p).expanduser()


def write_audio_config(cfg: Any, *, mic_name: Any = _UNSET,
                       output_name: Any = _UNSET) -> Path:
    """Persist name-based audio selection to the config with a timestamped
    backup. ``_UNSET`` leaves that side untouched; ``None`` clears it; a str
    pins it. Clears the legacy numeric index keys so a transient index is
    never persisted. Rejects any value that is not a clean stable identifier
    (raw ALSA hw path or numeric index). Returns the backup path."""
    import yaml
    for label, value in (("mic", mic_name), ("output", output_name)):
        if value is not _UNSET and value is not None and not is_clean_identifier(value):
            raise InvalidSelectionError(
                f"refusing to persist {label} device {value!r}: not a clean "
                "stable identifier (no hw path / numeric index allowed)")
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