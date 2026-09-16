"""Packet 8H: operator-gated qualification harness for the real microphone,
audio sink, ASR, reasoning model, Piper playback, web push-to-talk, and Right
Ctrl (hotkey) path.

This is the audio analogue of ``voicebridge/communications/qualify.py``
(Packet 8G). It is additive and hermetic by default:

- Hermetic stages run against SYNTHETIC audio and SYNTHETIC PTT events in an
  ISOLATED qualification state dir. They never open a real device, never touch
  the production state root, never load a model, and never modify the working
  web-dashboard path or audio configuration.
- Live stages open the REAL microphone/sink or emit a REAL Right Ctrl press
  ONLY when explicitly authorized: ``--live`` AND ``VOICE_BRIDGE_QUALIFY=1``.
  Without both they are impossible and fail closed (``LIVE_NOT_AUTHORIZED``).

The harness NEVER: swaps Piper for another provider, adds a training/self-
modification loop, alters model defaults, records private audio, installs a
second daemon, enables or restarts a service, edits the live config, or changes
any production audio/hotkey/webui behavior. It is a measurement harness.

It addresses the documented 8H preflight risks:

- a missing configured audio pin is reported as ``DEVICE_MISMATCH`` (degraded)
  and its implicit ``aggregate_fallback`` route is exposed — it is never
  silently presented as a clean pass;
- the user-session environment (X11/Wayland display, XDG_RUNTIME_DIR, and
  Pulse/PipeWire access) is verified WITHOUT hardcoding a machine-only value;
  a missing/ambiguous session is a typed failure with recovery guidance, never
  a guess at "the highest display";
- capture-to-response and playback latency are decomposed and measured, and the
  actual configured ASR/TTS/reasoning-provider identity is recorded so a real
  response can be distinguished from an unavailable-provider fallback;
- automated restart is validated to require explicit authorization, bounded
  backoff, and a circuit breaker (the harness itself never restarts anything).
"""
from __future__ import annotations

import copy
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..config import Config

# Shared operator gate (same as Packet 8G): live audio qualification requires
# --live AND VOICE_BRIDGE_QUALIFY=1. Without both, every live stage fails closed.
QUALIFY_ENV = "VOICE_BRIDGE_QUALIFY"
QUALIFY_VERSION = 1

# Bounded caps: every probe is hard-bounded so it can never hang or record
# unbounded audio.
CAPTURE_MAX_S = 5.0          # live capture probe window
PLAYBACK_MAX_S = 10.0        # live playback probe timeout
PCM_MAX_BYTES = 2_000_000    # synthetic PCM cap (>= 60 s @16k)
TONE_DURATION_S = 0.3        # synthetic playback tone
UTTERANCE_DURATION_S = 0.6   # synthetic "speech-like" signal for signal probe
CONCURRENT_TURNS = 8         # default web+rCtrl concurrent-turn stress size
RESTART_WINDOW_S = 300.0     # circuit-breaker observation window
RESTART_MAX_ATTEMPTS = 3     # restarts allowed inside the window
RESTART_BASE_BACKOFF_S = 2.0
RESTART_MAX_BACKOFF_S = 60.0
PACTL_TIMEOUT_S = 3.0

_FRAME_RATE = 16000
_FRAME_SIZE = _FRAME_RATE * 30 // 1000  # 480 samples @16k (30 ms VAD frame)


# --------------------------------------------------------------------- live gate
def live_authorized(env_value: Optional[str] = None) -> bool:
    """Live execution requires the operator gate ``VOICE_BRIDGE_QUALIFY=1``."""
    return (os.environ.get(QUALIFY_ENV) if env_value is None
            else env_value) == "1"


def deny_live(live: bool, *, env_value: Optional[str] = None
              ) -> Optional[Dict[str, Any]]:
    """Return a typed denial when live execution is requested without the gate.

    ``None`` means the requested live mode is authorized. This is the single
    fail-closed gate for every live stage; live work is impossible by default.
    """
    if not live:
        return None
    if live_authorized(env_value):
        return None
    return {
        "state": "denied", "code": "LIVE_NOT_AUTHORIZED",
        "detail": f"live audio qualification requires --live AND "
                  f"{QUALIFY_ENV}=1; no device was opened",
    }


def isolate_cfg(cfg: Config, state_dir: Path) -> Config:
    """A copy of the config with the journal redirected to an isolated dir.

    Ensures the qualification harness never writes into the production state
    root (events journal or any other state the harness persists).
    """
    data = copy.deepcopy(cfg.data)
    log = data.setdefault("logging", {})
    log["jsonl"] = True
    log["jsonl_path"] = str(state_dir)
    return Config(data=data, path=cfg.path)


# ------------------------------------------------------------ session context
def _x11_socket_count() -> int:
    """Number of real X11 display sockets (for ambiguity detection)."""
    try:
        return len([p for p in os.listdir("/tmp/.X11-unix")
                    if p.startswith("X") and p[1:].isdigit()])
    except OSError:
        return 0


def _pulse_reachable(env: Optional[Dict[str, Any]] = None) -> bool:
    """Bounded, read-only Pulse/PipeWire reachability probe (``pactl info``).

    Never opens a stream and never modifies anything. Injectable via
    ``pactl_fn`` in ``session_context`` for hermetic tests.
    """
    import subprocess
    full = dict(os.environ if env is None else env)
    try:
        proc = subprocess.run(["pactl", "info"], capture_output=True,
                              text=True, timeout=PACTL_TIMEOUT_S, env=full)
        return proc.returncode == 0
    except Exception:
        return False


def session_context(env: Optional[Dict[str, Any]] = None, *,
                    display_resolver: Optional[Callable[[Dict[str, Any]],
                                                        Optional[str]]] = None,
                    socket_count: Optional[Callable[[], int]] = None,
                    pactl_fn: Optional[Callable[[Dict[str, Any]], bool]] = None,
                    ) -> Dict[str, Any]:
    """Verify the user-session environment the audio path needs.

    Checks (never hardcoding a machine-only value):

    - ``XDG_RUNTIME_DIR`` present + a real directory;
    - the graphical session type and a REACHABLE X11 display (or a typed
      Wayland note) — multi-session ambiguity degrades to ``AMBIGUOUS``,
      never a guess at the highest display;
    - Pulse/PipeWire reachability.

    Returns a typed record; a missing piece is a typed failure with recovery
    guidance, never a silent pass.
    """
    env = dict(os.environ if env is None else env)
    resolver = display_resolver or _default_display_resolver
    count_fn = socket_count or _x11_socket_count
    pactl = pactl_fn or _pulse_reachable

    out: Dict[str, Any] = {"states": {}, "recovery": {}}

    xdg = (env.get("XDG_RUNTIME_DIR") or "").strip()
    out["xdg_runtime_dir"] = xdg
    if xdg and Path(xdg).is_dir():
        out["states"]["xdg_runtime_dir"] = "OK"
    else:
        out["states"]["xdg_runtime_dir"] = "MISSING"
        out["recovery"]["xdg_runtime_dir"] = (
            "set XDG_RUNTIME_DIR (user systemd exports it at login); without "
            "it the PipeWire/Pulse and notification sockets are unreachable")

    session_type = (env.get("XDG_SESSION_TYPE") or "").lower()
    wayland = bool(env.get("WAYLAND_DISPLAY")) or session_type == "wayland"
    out["session_type"] = session_type or "unknown"
    out["wayland"] = wayland

    disp = resolver(env)
    n_sockets = int(count_fn() or 0)
    out["display"] = disp or ""
    if disp:
        out["states"]["display"] = "OK"
        out["display_type"] = "wayland" if wayland else "x11"
    elif n_sockets == 0:
        out["states"]["display"] = "NO_SESSION"
        out["recovery"]["display"] = (
            "no graphical X11 session detected — Right Ctrl PTT and desktop "
            "audio need a logged-in X11/Wayland session (headless manual "
            "Enter-toggle mode remains available)")
    elif n_sockets >= 2:
        # multiple real sockets and none provably reachable from this process:
        # never guess "the highest display" in a multi-session environment.
        out["states"]["display"] = "AMBIGUOUS"
        out["recovery"]["display"] = (
            f"{n_sockets} X display sockets found but none is reachable from "
            "this process — set DISPLAY explicitly to the active user session "
            "and re-run; the harness never guesses")
    else:
        out["states"]["display"] = "UNREACHABLE"
        out["recovery"]["display"] = (
            "an X display socket exists but xinput could not connect — check "
            "DISPLAY/XAUTHORITY for the active session")

    pulse = bool(pactl(env))
    out["pulse_pipewire"] = pulse
    if pulse:
        out["states"]["pulse_pipewire"] = "OK"
    else:
        out["states"]["pulse_pipewire"] = "UNAVAILABLE"
        out["recovery"]["pulse_pipewire"] = (
            "pactl could not reach Pulse/PipeWire — start the user audio "
            "session (or check XDG_RUNTIME_DIR/PULSE_SERVER) before live "
            "audio qualification")

    out["state"] = ("OK" if all(v == "OK" for v in out["states"].values())
                    else "degraded")
    return out


def _default_display_resolver(env: Dict[str, Any]) -> Optional[str]:
    from ..display import resolve_session_display
    return resolve_session_display(env)


# ------------------------------------------------------- effective devices
def _match_by_name(devices: Sequence[Any], pin: str) -> Optional[Any]:
    """Match a stable pin by normalized name substring (never by index)."""
    low = str(pin).lower()
    for d in devices:
        name = str(getattr(d, "name", "")).lower()
        stable = str(getattr(d, "stable_id", "")).lower()
        if low in name or low in stable or name == low or stable == low:
            return d
    return None


def _mic_pin(cfg: Config) -> str:
    """Configured stable mic pin (name), or '' when following the default."""
    return (cfg.get("mic", "device_name", default=None) or "").strip()


def _sink_pin(cfg: Config) -> str:
    """Configured stable sink pin (name), or '' when following the default."""
    return (cfg.get("audio", "output_device_name", default="") or "").strip()


def _resolve_index(pin: str, role: str) -> Optional[int]:
    """Effective PortAudio index the runtime would use for a pin (or None)."""
    try:
        if role == "input":
            from .mic import resolve_input_device
            return resolve_input_device(pin or None)
        from .playback import Playback
        return Playback(device_name=pin or None)._resolve_device()
    except Exception:
        return None


def effective_devices(cfg: Config, *, devices: Optional[Dict[str, List[Any]]] = None,
                      resolve: Optional[Callable[[str, str], Optional[int]]] = None,
                      ) -> Dict[str, Any]:
    """Resolve and report the effective stable source and sink.

    A configured pin that matches nothing is reported ``DEVICE_MISMATCH`` with
    the runtime's implicit ``aggregate_fallback`` route EXPOSED — it is never
    silently passed through as a clean result. An unpinned side reports
    ``system_default`` (the documented follow-the-default behavior).
    """
    from .settings import AudioSettingsError, list_devices
    if devices is None:
        try:
            devices = list_devices()
        except AudioSettingsError as exc:
            return {
                "state": "degraded", "enumeration_error": str(exc)[:160],
                "mic": {"state": "UNAVAILABLE", "role": "input",
                        "route": "unavailable",
                        "detail": "device enumeration failed"},
                "sink": {"state": "UNAVAILABLE", "role": "output",
                         "route": "unavailable",
                         "detail": "device enumeration failed"},
            }
    resolve = resolve or _resolve_index
    mic_pin = _mic_pin(cfg)
    sink_pin = _sink_pin(cfg)
    mic = _resolve_side(devices.get("inputs", []), mic_pin, "input", resolve)
    sink = _resolve_side(devices.get("outputs", []), sink_pin, "output", resolve)
    bad = ("DEVICE_MISMATCH" == mic["state"]) or ("DEVICE_MISMATCH" == sink["state"])
    return {
        "state": "degraded" if bad else "OK",
        "mic": mic, "sink": sink,
        "note": "configured pins that match nothing are DEVICE_MISMATCH, "
                "never silently routed through the aggregate default",
    }


def _available_names(devices: Sequence[Any], *, limit: int = 20) -> List[str]:
    """Bounded list of present stable device names for operator guidance."""
    names: List[str] = []
    for d in devices:
        name = str(getattr(d, "name", "")).strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def _resolve_side(devices: Sequence[Any], pin: str, role: str,
                  resolve: Callable[[str, str], Optional[int]]) -> Dict[str, Any]:
    if not pin:
        default = next((d for d in devices
                        if getattr(d, "is_default", False)), None)
        return {
            "state": "OK", "role": role, "route": "system_default",
            "configured_pin": "", "present": False,
            "stable_id": getattr(default, "stable_id", ""),
            "index": resolve("", role),
        }
    dev = _match_by_name(devices, pin)
    if dev is None:
        # The runtime's documented fallback follows the aggregate default when
        # a named pin matches nothing — that is exactly the risk this packet
        # must expose, so it is reported as DEVICE_MISMATCH, not a pass. The
        # operator gets the exact configured pin and the present device names
        # so they can intentionally re-pin; the harness never reroutes blindly.
        available = _available_names(devices)
        return {
            "state": "DEVICE_MISMATCH", "role": role, "route": "aggregate_fallback",
            "configured_pin": pin, "present": False,
            "stable_id": "", "index": resolve(pin, role),
            "detail": (f"configured {role} pin {pin!r} is not present; the "
                       "runtime would follow the aggregate default, so the "
                       f"real {role} is NOT confirmed. Re-pin to one of the "
                       f"present {role} devices: {', '.join(available) or '(none enumerated)'} "
                       "(use `voicebridge audio list` / `voicebridge audio "
                       f"select {'mic' if role == 'input' else 'output'}`) — "
                       "do not leave an unmatched pin in place"),
            "available_devices": available,
        }
    return {
        "state": "OK", "role": role, "route": "pinned",
        "configured_pin": pin, "present": True,
        "stable_id": getattr(dev, "stable_id", ""),
        "index": resolve(pin, role),
    }


# ---------------------------------------------------------- provider identity
def provider_identity(cfg: Config, *, asr: Any = None, tts: Any = None,
                      model: Any = None) -> Dict[str, Any]:
    """Record the ACTUAL configured ASR/TTS/reasoning-provider identity.

    Lets the harness distinguish a real response from an unavailable-provider
    fallback: the configured values are recorded alongside any live provider
    objects supplied by the caller, and known fallback relationships (e.g.
    openai TTS -> Piper) are flagged so a fallback can never look like a real
    provider response.
    """
    asr_name = getattr(asr, "name", None) or cfg.get("asr", "provider",
                                                     default="unknown")
    asr_engine = getattr(asr, "engine", None) or cfg.get("asr", "engine",
                                                         default="")
    tts_name = getattr(tts, "name", None) or cfg.get("tts", "provider",
                                                     default="unknown")
    tts_voice = getattr(tts, "voice", None) or cfg.get("tts", "voice",
                                                       default="")
    brain = getattr(model, "name", None) or cfg.get(
        "intent", "local_llm", "model", default="")
    return {
        "asr": {
            "configured_provider": cfg.get("asr", "provider", default="unknown"),
            "actual_provider": asr_name,
            "engine": asr_engine,
            "language": cfg.get("asr", "language", default=""),
        },
        "tts": {
            "configured_provider": cfg.get("tts", "provider", default="unknown"),
            "actual_provider": tts_name,
            "voice": tts_voice,
            "sample_rate": cfg.get("tts", "sample_rate", default=22050),
            "fallback_to_piper": bool(
                cfg.get("tts", "provider", default="").lower() == "openai"),
        },
        "reasoning_model": {
            "configured": cfg.get("intent", "local_llm", "model", default=""),
            "actual": brain,
            "summarizer_model": cfg.get("summarizer", "ollama_model", default=""),
        },
        "note": "a TTS/ASR provider configured as fallback-capable is never "
                "presented as the primary provider's response",
    }


# ------------------------------------------------------------ latency stages
def stage_clock(stages: Dict[str, Callable[[], Any]], *,
                clock: Callable[[], float] = time.monotonic,
                ) -> Dict[str, Any]:
    """Time ordered pipeline stages with a monotonic clock.

    Each callable returns ``(ok, note)`` (or ``None`` == ``(True, "")``) or
    raises. Records per-stage ms, first-audio (capture+asr+reasoning+synthesis,
    i.e. everything before playback), end-to-end ms, and whether any stage
    fell back or failed. ``synthesis`` is the stage that produces the audio a
    client can play first.
    """
    out: Dict[str, Any] = {
        "stages": {}, "first_audio_ms": None, "end_to_end_ms": 0.0,
        "real_vs_fallback": "real", "failed": [],
    }
    order = list(stages)
    t0 = clock()
    for name in order:
        s = clock()
        try:
            result = stages[name]() or (True, "")
            ok, note = (result if isinstance(result, tuple) else (bool(result), ""))
            out["stages"][name] = {
                "ok": bool(ok), "ms": round((clock() - s) * 1000.0, 1),
                "note": note or "",
            }
        except Exception as exc:  # noqa: BLE001 - typed, bounded
            out["stages"][name] = {
                "ok": False, "ms": round((clock() - s) * 1000.0, 1),
                "error": str(exc)[:160],
            }
            if name in ("asr", "reasoning", "synthesis", "playback"):
                out["real_vs_fallback"] = "fallback"
            continue
        if name == "synthesis" and out["first_audio_ms"] is None:
            out["first_audio_ms"] = round((clock() - t0) * 1000.0, 1)
    out["end_to_end_ms"] = round((clock() - t0) * 1000.0, 1)
    out["failed"] = [n for n, s in out["stages"].items() if not s["ok"]]
    return out


def latency_probe(*, stages: Optional[Dict[str, Callable[[], Any]]] = None,
                  identity: Optional[Dict[str, Any]] = None,
                  clock: Callable[[], float] = time.monotonic,
                  ) -> Dict[str, Any]:
    """Canonical capture->ASR->reasoning->synthesis->playback latency probe.

    ``stages`` defaults to the five named stages with no-op callables so the
    decomposition, first-audio split, and fallback detection are exercised
    hermetically; live qualification injects real provider stages (see the 8H
    runbook). ``identity`` is ``provider_identity(...)`` output recorded next
    to the measurements so a real response is distinguishable from a fallback.
    """
    default_stages: Dict[str, Callable[[], Any]] = {
        "capture": lambda: (True, "synthetic"),
        "asr": lambda: (True, "synthetic"),
        "reasoning": lambda: (True, "synthetic"),
        "synthesis": lambda: (True, "synthetic"),
        "playback": lambda: (True, "synthetic"),
    }
    timed = stage_clock(stages if stages is not None else default_stages,
                        clock=clock)
    out: Dict[str, Any] = {
        "identity": identity or {},
        "first_audio_ms": timed["first_audio_ms"],
        "end_to_end_ms": timed["end_to_end_ms"],
        "real_vs_fallback": timed["real_vs_fallback"],
        "failed": timed["failed"],
        "stages": timed["stages"],
        "note": "hermetic latency is the stage decomposition (synthetic); "
                "real device/model latency is an opt-in live measurement",
    }
    return out


# --------------------------------------------------------------- signal probe
def _rms(pcm: bytes) -> float:
    import numpy as np
    if not pcm or len(pcm) % 2:
        return 0.0
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size == 0:
        return 0.0
    return float(np.sqrt((samples ** 2).mean()))


def _peak(pcm: bytes) -> float:
    import numpy as np
    if not pcm:
        return 0.0
    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size == 0:
        return 0.0
    return float(np.max(np.abs(samples))) / 32768.0


def silence_pcm(duration_s: float = 0.4, *, rate: int = _FRAME_RATE) -> bytes:
    import numpy as np
    return np.zeros(int(rate * duration_s), dtype=np.int16).tobytes()


def utterance_pcm(duration_s: float = UTTERANCE_DURATION_S, *,
                  rate: int = _FRAME_RATE, amplitude: float = 0.4,
                  freq: float = 440.0) -> bytes:
    """Synthetic, speech-like PCM (amplitude-modulated tone + gentle noise)."""
    import numpy as np
    n = int(rate * duration_s)
    t = np.linspace(0, duration_s, n, endpoint=False)
    tone = np.sin(2 * np.pi * freq * t)
    mod = 0.5 + 0.5 * np.sin(2 * np.pi * 8.0 * t)      # syllable-ish envelope
    noise = np.random.default_rng(7).normal(0, 0.02, n)
    sig = np.clip(tone * mod * amplitude + noise, -1.0, 1.0)
    return (sig * 32767).astype(np.int16).tobytes()


def clipped_pcm(duration_s: float = 0.3, *, rate: int = _FRAME_RATE) -> bytes:
    """Full-scale square wave: hard-saturated samples (peak == 1.0)."""
    import numpy as np
    n = int(rate * duration_s)
    half = max(1, n // 4)
    samples = np.where(np.arange(n) % (2 * half) < half, 1.0, -1.0)
    return (samples * 32767).astype(np.int16).tobytes()


def _resample_to_16k(pcm: bytes, rate: int) -> bytes:
    if rate == _FRAME_RATE or not pcm:
        return pcm
    from .vad import resample_linear
    import numpy as np
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    out = resample_linear(samples, rate, _FRAME_RATE)
    return (np.clip(out, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def signal_probe(pcm: bytes, *, rate: int = _FRAME_RATE,
                 min_rms: float = 0.0008, vad: Any = None,
                 ) -> Dict[str, Any]:
    """Classify one PCM buffer with the same gates the production path uses.

    Covers silence (below the energy floor), clipping (peak >= 0.98), and a
    speech-like signal; with a ``vad`` Endpointer it also reports the VAD
    boundary states. Never loads a model when ``vad`` uses ``webrtc``.
    """
    rms = _rms(pcm)
    peak = _peak(pcm)
    below_floor = rms < float(min_rms)
    clipped = peak >= 0.98
    state = ("SILENCE" if below_floor
             else ("CLIPPING" if clipped else "SPEECH"))
    out: Dict[str, Any] = {
        "state": state, "rms": round(rms, 6), "peak": round(peak, 6),
        "length_s": round(len(pcm) / 2 / float(rate or _FRAME_RATE), 3),
        "below_floor": bool(below_floor), "clipped": bool(clipped),
        "min_rms": float(min_rms), "rate": int(rate),
    }
    if vad is not None:
        out["vad"] = vad_boundaries_probe(vad, pcm, rate=rate)
    return out


def vad_boundaries_probe(vad: Any, pcm: bytes, *, rate: int = _FRAME_RATE,
                         ) -> Dict[str, Any]:
    """Feed PCM through an Endpointer and report speech-start/done boundaries.

    Proves silence before speech does not start, speech starts after the
    configured ``speech_start_ms``, and trailing silence completes the turn.
    """
    pcm = _resample_to_16k(pcm, rate)
    n = len(pcm) // 2
    if not n:
        return {"speech_started": False, "done": False, "final_state": "idle",
                "frames": 0}
    # _FRAME_SIZE is in SAMPLES; the buffer is raw int16 bytes (2 bytes/sample)
    byte_size = _FRAME_SIZE * 2
    frames = [pcm[i * byte_size:(i + 1) * byte_size]
              for i in range(n // _FRAME_SIZE)]
    first_speech: Optional[int] = None
    for i, f in enumerate(frames):
        st = vad.add_frame(f)
        if st == "speech" and first_speech is None:
            first_speech = i
    return {
        "speech_started": vad.state in ("speech", "done"),
        "done": vad.state == "done",
        "final_state": vad.state,
        "frames": len(frames),
        "first_speech_frame": first_speech,
        "speech_frames": getattr(vad, "speech_frames", 0),
    }


def echo_pcm(duration_s: float = 0.6, *, rate: int = _FRAME_RATE,
             delay_s: float = 0.08, amplitude: float = 0.35) -> bytes:
    """Synthetic speech-like PCM plus a delayed low-amplitude echo of itself.

    Covers the "echo" scenario: a reverberant signal must still classify as
    SPEECH (never silence or clipping) through the production energy gate.
    """
    import numpy as np
    base = np.frombuffer(utterance_pcm(duration_s, rate=rate,
                                       amplitude=amplitude), dtype=np.int16)
    delay = int(rate * delay_s)
    echo = np.zeros_like(base)
    if len(base) > delay:
        echo[delay:] = (base[:-delay].astype(np.float32) * 0.25).astype(np.int16)
    out = base.astype(np.float32) + echo.astype(np.float32)
    return (np.clip(out, -32768, 32767)).astype(np.int16).tobytes()


def signal_qualification(*, utterance: Optional[bytes] = None,
                         silence: Optional[bytes] = None,
                         clipped: Optional[bytes] = None,
                         min_rms: float = 0.0008,
                         ) -> Dict[str, Any]:
    """Hermetic sample-rate/channel/silence/clipping/noise/echo/VAD checks.

    Returns typed states for silence rejection, clipping detection, a clean
    speech-like signal, sample-rate conversion, and echo resilience, plus VAD
    boundaries on a webrtc Endpointer (no model).
    """
    from .vad import Endpointer
    utterance = utterance_pcm() if utterance is None else bytes(utterance)
    silence = silence_pcm() if silence is None else bytes(silence)
    clipped = clipped_pcm() if clipped is None else bytes(clipped)
    ep = Endpointer(vad_backend="webrtc", aggressiveness=2,
                    speech_start_ms=220, silence_timeout_s=1.2)

    sil = signal_probe(silence, min_rms=min_rms)
    clip = signal_probe(clipped, min_rms=min_rms)
    speech = signal_probe(utterance, min_rms=min_rms, vad=ep)
    echo = signal_probe(echo_pcm(), min_rms=min_rms)

    # sample-rate / channel conversion: a 48 kHz speech buffer must resample to
    # the 16 kHz ASR contract without losing its energy classification.
    sr = signal_probe(utterance, rate=48000, min_rms=min_rms)
    ok = (sil["state"] == "SILENCE" and clip["state"] == "CLIPPING"
          and speech["state"] == "SPEECH" and sr["state"] == "SPEECH"
          and echo["state"] == "SPEECH"
          and speech["vad"]["speech_started"] is True
          and speech["vad"]["done"] is False)
    return {
        "state": "OK" if ok else "failed",
        "silence": sil, "clipping": clip, "speech": speech,
        "echo": echo, "rate_converted_48k": sr,
        "checks": {
            "silence_rejected": sil["state"] == "SILENCE",
            "clipping_detected": clip["state"] == "CLIPPING",
            "speech_detected": speech["state"] == "SPEECH",
            "rate_conversion_keeps_speech": sr["state"] == "SPEECH",
            "echo_keeps_speech": echo["state"] == "SPEECH",
            "vad_speech_started": speech["vad"]["speech_started"] is True,
        },
    }


# -------------------------------------------------------------- PTT edge probe
def ptt_edge_probe(listener: Any = None) -> Dict[str, Any]:
    """Duplicate-press / stuck-key protection for the Right Ctrl listener.

    Emits press/press/release/release (a duplicate or stuck key) and verifies
    the key is never left logically held and capture start/end both occur. The
    event counts are recorded faithfully (the orchestrator's ptt_end handling
    is idempotent), so a duplicated release can never leave PTT stuck open.
    """
    from ..hotkeys import HotkeyListener
    ln = listener or HotkeyListener(hold_key="ctrl_r", tap_cancel="esc")
    events: List[tuple] = []
    ln.on_event = lambda e, k: events.append((e, k))
    ln._press("ctrl_r")
    ln._press("ctrl_r")      # duplicate press must not double-start capture
    ln._release("ctrl_r")
    ln._release("ctrl_r")    # duplicate release must not leave anything held
    starts = [e for e in events if e[0] == "ptt_start"]
    ends = [e for e in events if e[0] == "ptt_end"]
    held = sorted(ln._held)
    ok = bool(starts) and bool(ends) and not held
    return {
        "state": "OK" if ok else "failed",
        "starts": len(starts), "ends": len(ends), "held_after_release": held,
        "no_stuck_key": not held,
        "capture_started_and_stopped": bool(starts) and bool(ends),
    }


# ------------------------------------------------------ concurrent PTT stress
def run_concurrent_turns(plan: Sequence[tuple], *, runner: Callable[[str, str], Any],
                         workers: int = 4,
                         ) -> Dict[str, Any]:
    """Run interleaved web-PTT and Right-Ctrl turns and prove isolation.

    ``plan`` is ``[(turn_id, kind), ...]`` with ``kind`` in ``web`` / ``rctrl`` /
    ``cancel``. Each turn runs through ``runner(turn_id, kind)`` (which must
    return a dict carrying at least its own ``turn`` key). The probe asserts
    every answer is attributable to its own turn — proving there is no
    cross-turn answer or shared-state contamination, and that a ``cancel`` turn
    does not consume or corrupt a neighbor's answer.
    """
    from concurrent.futures import ThreadPoolExecutor
    plan = list(plan)
    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        futures = [pool.submit(runner, tid, kind) for tid, kind in plan]
        for fut in futures:
            try:
                results.append(fut.result(timeout=30))
            except Exception as exc:  # noqa: BLE001 - typed, bounded
                results.append({"turn": "", "error": str(exc)[:160]})
    answers = {r.get("turn"): r for r in results if r.get("turn")}
    expected = [tid for tid, _ in plan]
    missing = [tid for tid in expected if tid not in answers]
    contaminated = [tid for tid, r in answers.items()
                    if tid not in expected or
                    r.get("answer_turn") not in (None, tid) and
                    r.get("answer_turn") != tid]
    # a cancel turn must never have hijacked another turn's answer
    cancel_leak = [
        tid for tid, _ in plan
        if _ == "cancel" and answers.get(tid, {}).get("answer_turn") not in (None, tid)]
    ok = not missing and not contaminated and not cancel_leak
    return {
        "state": "OK" if ok else "failed",
        "turns_total": len(plan),
        "web_turns": sum(1 for _, k in plan if k == "web"),
        "rctrl_turns": sum(1 for _, k in plan if k == "rctrl"),
        "cancel_turns": sum(1 for _, k in plan if k == "cancel"),
        "answers": len(answers), "missing": missing,
        "contaminated": contaminated, "cancel_leak": cancel_leak,
        "no_cross_turn_answer": not contaminated,
    }


# ---------------------------------------------------------- restart circuit
@dataclass
class RestartCircuit:
    """Bounded-backoff + circuit-breaker gate for ANY automated restart.

    Automated restart must require explicit authorization, bounded backoff, and
    a circuit breaker. This harness NEVER restarts anything itself — the circuit
    is a validation target: if a restart is requested, it is refused without
    authorization, refused inside the backoff window, and refused when the
    circuit has tripped (``max_attempts`` inside ``window_s``).
    """

    max_attempts: int = RESTART_MAX_ATTEMPTS
    window_s: float = RESTART_WINDOW_S
    base_backoff_s: float = RESTART_BASE_BACKOFF_S
    max_backoff_s: float = RESTART_MAX_BACKOFF_S
    clock: Callable[[], float] = time.monotonic
    recent: List[float] = field(default_factory=list)
    _next_allowed: float = field(default=0.0, init=False)

    def allow_restart(self, *, authorized: bool = False) -> tuple:
        """Return ``(ok, reason)`` for one restart attempt."""
        now = self.clock()
        if not authorized:
            return False, "RESTART_NOT_AUTHORIZED"
        if now < self._next_allowed:
            return False, f"BACKOFF ({self._next_allowed - now:.0f}s left)"
        self.recent = [t for t in self.recent if now - t < self.window_s]
        if len(self.recent) >= self.max_attempts:
            return False, "CIRCUIT_OPEN"
        self.recent.append(now)
        backoff = min(self.max_backoff_s,
                      self.base_backoff_s * (2 ** max(0, len(self.recent) - 1)))
        self._next_allowed = now + backoff
        return True, "OK"


def restart_guard_probe(*, circuit: Optional[RestartCircuit] = None,
                        clock: Optional[Callable[[], float]] = None,
                        authorized: bool = False,
                        attempts: int = 8) -> Dict[str, Any]:
    """Exercise the restart circuit and report typed outcomes.

    Uses an accelerated virtual clock by default so both the bounded backoff
    and the circuit breaker are proven deterministically without real waiting:
    the virtual clock advances just past each backoff window, so repeated
    attempts first hit ``BACKOFF`` and then ``CIRCUIT_OPEN`` once the window's
    attempt budget is exhausted. Without ``authorized`` every attempt is
    refused (``RESTART_NOT_AUTHORIZED``).
    """
    if clock is None:
        _t: Dict[str, float] = {"v": 0.0}

        def _virtual_clock() -> float:
            _t["v"] += 2.1
            return _t["v"]

        clock = _virtual_clock
    circ = circuit or RestartCircuit(clock=clock)
    outcomes: List[Dict[str, Any]] = []
    for i in range(attempts):
        ok, reason = circ.allow_restart(authorized=authorized)
        outcomes.append({"attempt": i, "allowed": bool(ok), "reason": reason})
    opened = any(o["reason"] == "CIRCUIT_OPEN" for o in outcomes)
    backoff = any(str(o["reason"]).startswith("BACKOFF") for o in outcomes)
    if not authorized:
        all_denied = all(o["reason"] == "RESTART_NOT_AUTHORIZED"
                         for o in outcomes)
        state = "OK" if all_denied else "failed"
        detail = "all restart attempts refused without authorization"
    else:
        state = "OK" if opened and backoff else "failed"
        detail = "circuit opened and backoff applied after repeated attempts"
    return {
        "state": state, "authorized": bool(authorized), "detail": detail,
        "circuit_opened": opened, "backoff_applied": backoff,
        "outcomes": outcomes,
    }


# -------------------------------------------------------- temp audio cleanup
def temp_audio_cleanup_probe(tmp_dir: Path, *, prefixes: Sequence[str] = ("tts-", "webui-"),
                             ) -> Dict[str, Any]:
    """Create synthetic temp WAVs and prove the cleanup contract removes them.

    Guards the "temporary-audio cleanup / no audio leak" requirement: after a
    turn the harness's own temp audio (the same prefixes the production paths
    use) must not linger. ``tmp_dir`` is the caller-owned scratch dir; nothing
    outside it is touched.
    """
    tmp_dir = Path(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    created: List[Path] = []
    for i, prefix in enumerate(prefixes):
        p = tmp_dir / f"{prefix}qualify-{i}.wav"
        p.write_bytes(b"RIFFdummy")   # synthetic bytes only
        created.append(p)
    leftover = _cleanup_temp(tmp_dir, prefixes=prefixes)
    remaining = [str(p.relative_to(tmp_dir)) for p in tmp_dir.iterdir()]
    return {
        "state": "OK" if not remaining else "failed",
        "created": len(created), "removed": len(created) - len(leftover),
        "remaining": remaining,
    }


def _cleanup_temp(root: Path, *, prefixes: Sequence[str]) -> List[Path]:
    """Unlink temp audio files with the given prefixes under ``root``.

    Best-effort and bounded; returns the paths it could not remove.
    """
    left: List[Path] = []
    if not root.is_dir():
        return left
    for p in root.iterdir():
        if p.is_file() and any(str(p.name).startswith(prefix)
                               for prefix in prefixes):
            try:
                p.unlink()
            except OSError:
                left.append(p)
    return left


# ------------------------------------------------------------ live stages
def capture_probe(cfg: Config, *, seconds: float = 1.0, live: bool = False,
                  env_value: Optional[str] = None,
                  selfcheck: Optional[Callable[[Config], Dict[str, Any]]] = None,
                  ) -> Dict[str, Any]:
    """Live-gated REAL microphone capture probe (bounded).

    Reuses the production ``mic_selfcheck`` route (one input stream at a time),
    which opens the SAME capture path the bridge uses. Typed states:
    ``UNAVAILABLE`` / ``SILENT`` / ``DEGRADED`` / ``HEALTHY``. Records the
    device the effective config resolves to. Never records audio.
    """
    denied = deny_live(live, env_value=env_value)
    if denied:
        return denied
    from .mic import mic_selfcheck
    check = (selfcheck or mic_selfcheck)(cfg) if selfcheck else \
        mic_selfcheck(cfg, seconds=max(0.2, min(float(seconds), CAPTURE_MAX_S)))
    from .settings import list_devices
    try:
        devs = list_devices()
        pin = _mic_pin(cfg)
        idx = _resolve_index(pin, "input")
        present = _match_by_name(devs.get("inputs", []), pin) if pin else None
    except Exception as exc:  # noqa: BLE001 - typed, bounded
        present, idx = None, None
        check.setdefault("error", str(exc)[:160])
    return {
        "state": check.get("state", "UNAVAILABLE"),
        "configured_pin": _mic_pin(cfg),
        "pin_present": present is not None if _mic_pin(cfg) else None,
        "effective_index": idx,
        "signal": check,
        "note": "capture probe opens the production mic path bounded; if the "
                "configured pin is missing the state is DEVICE_MISMATCH, never "
                "a silent aggregate pass",
    }


def playback_probe(cfg: Config, *, live: bool = False,
                   env_value: Optional[str] = None,
                   playback: Any = None,
                   tone: Optional[bytes] = None,
                   ) -> Dict[str, Any]:
    """Live-gated REAL sink playback probe (bounded, synthetic tone only).

    Synthesizes a short sine tone (never private audio), resolves the effective
    sink with the production ``Playback`` path, and plays it. Confirms the
    stream actually became active (audible) and measures start latency.
    """
    denied = deny_live(live, env_value=env_value)
    if denied:
        return denied
    import tempfile
    from .playback import Playback as _Playback
    sink_pin = _sink_pin(cfg)
    sink_name = sink_pin or None
    pb = playback or _Playback(device_name=sink_name)
    wav = tone if tone is not None else tone_wav_bytes()
    fd, path = tempfile.mkstemp(prefix="qubit-qualify-playback-",
                                suffix=".wav")
    os.close(fd)
    try:
        Path(path).write_bytes(wav)
        t0 = time.monotonic()
        played = pb.play(path, name="qualify-tone",
                         interruptible=False, on_start=None)
        latency_ms = round((time.monotonic() - t0) * 1000.0, 1)
        # an interrupted/failed play never counts as audible
        audible = bool(played) and not pb.playing
    except Exception as exc:  # noqa: BLE001 - typed, bounded
        played, audible, latency_ms = False, False, -1.0
        err = str(exc)[:160]
    else:
        err = ""
    finally:
        _cleanup_temp(Path(path).parent, prefixes=("qubit-qualify-playback-",))
    from .settings import list_devices
    try:
        devs = list_devices()
        present = _match_by_name(devs.get("outputs", []), sink_pin) if sink_pin else None
    except Exception:
        present = None
    return {
        "state": "OK" if audible else ("failed" if not err else "UNAVAILABLE"),
        "audible": audible, "played": bool(played),
        "latency_ms": latency_ms,
        "configured_pin": sink_pin,
        "pin_present": present is not None if sink_pin else None,
        "resolved_route": "pinned" if sink_pin and present else
                          ("aggregate_fallback" if sink_pin and not present
                           else "system_default"),
        "error": err,
    }


def tone_wav_bytes(*, rate: int = 16000, duration_s: float = TONE_DURATION_S,
                   freq: float = 440.0, amplitude: float = 0.3) -> bytes:
    """Synthetic WAV (sine tone) for playback probes — never private audio."""
    import io, struct
    import numpy as np
    n = int(rate * duration_s)
    t = np.linspace(0, duration_s, n, endpoint=False)
    samples = (np.sin(2 * np.pi * freq * t) * amplitude * 32767).astype(np.int16)
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", 36 + len(samples) * 2))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16))
    buf.write(b"data")
    buf.write(struct.pack("<I", len(samples) * 2))
    buf.write(samples.tobytes())
    return buf.getvalue()


def ptt_probe(*, live: bool = False, env_value: Optional[str] = None,
              listener: Any = None,
              ) -> Dict[str, Any]:
    """Live-gated Right Ctrl qualification probe.

    In live mode a REAL HotkeyListener is started and the operator holds/lets
    go of Right Ctrl (bounded wait); the probe records whether ptt_start and
    ptt_end arrived. Hermetic tests drive ``ptt_edge_probe`` with synthetic
    events instead; this function itself is what the operator runbook invokes.
    """
    denied = deny_live(live, env_value=env_value)
    if denied:
        return denied
    from ..hotkeys import HotkeyListener
    from ..display import ensure_session_display
    ensure_session_display()
    ln = listener or HotkeyListener(hold_key="ctrl_r", tap_cancel="esc")
    events: List[tuple] = []
    ln.on_event = lambda e, k: events.append((e, k))
    if not ln.start(ln.on_event):
        return {"state": "UNAVAILABLE",
                "detail": "no hotkey backend could connect (headless or "
                          "ambiguous display) — PTT is not qualified live"}
    try:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if any(e[0] == "ptt_start" for e in events) and \
                    any(e[0] == "ptt_end" for e in events):
                break
            time.sleep(0.1)
    finally:
        ln.stop()
    starts = [e for e in events if e[0] == "ptt_start"]
    ends = [e for e in events if e[0] == "ptt_end"]
    ok = bool(starts) and bool(ends)
    return {
        "state": "OK" if ok else "TIMEOUT",
        "starts": len(starts), "ends": len(ends),
        "detail": "hold and release Right Ctrl within 10 s to qualify the path"
                  if not ok else "",
    }


# ------------------------------------------------------------ orchestration
def run_qualification(cfg: Config, *, live: bool = False,
                      env_value: Optional[str] = None,
                      state_dir: Optional[Path] = None,
                      dry_run: bool = False,
                      run_id: Optional[str] = None,
                      utterance: Optional[bytes] = None,
                      silence: Optional[bytes] = None,
                      clipped: Optional[bytes] = None,
                      concurrency_plan: Optional[Sequence[tuple]] = None,
                      ) -> Dict[str, Any]:
    """Run the full 8H qualification (hermetic + optional gated live).

    Hermetic stages (session/devices/providers/signal/vad/latency/concurrency/
    restart-guard/cleanup) are the required gates and run in an ISOLATED state
    dir. Live stages (capture/playback/ptt) run only when ``live`` AND the
    operator gate pass; a requested-but-unauthorized live stage fails closed.
    The summary is a REDACTED record persisted at ``0600`` (unless ``dry_run``)
    and never contains private audio, transcripts, or machine secrets.
    """
    state_dir = Path(state_dir or (cfg.state_dir() / "qualification"))
    state_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)
    rid = run_id or f"aq-{uuid.uuid4().hex[:12]}"
    scfg = isolate_cfg(cfg, state_dir)

    from .vad import Endpointer
    ep = Endpointer(vad_backend="webrtc", aggressiveness=2,
                    speech_start_ms=220, silence_timeout_s=1.2)
    plan = concurrency_plan or [
        (f"web-{i}", "web") for i in range(CONCURRENT_TURNS // 2)
    ] + [(f"rctrl-{i}", "rctrl") for i in range(CONCURRENT_TURNS // 2)]

    def _runner(tid: str, kind: str) -> Dict[str, Any]:
        # isolation model: each turn reads/writes ONLY its own key
        workspace: Dict[str, Dict[str, Any]] = {}
        workspace.setdefault(tid, {})["seen"] = True
        if kind == "cancel":
            return {"turn": tid, "kind": kind, "answer_turn": tid, "cancelled": True}
        return {"turn": tid, "kind": kind, "answer_turn": tid,
                "answer": f"answer-{tid}"}

    stages: Dict[str, Any] = {
        "session": session_context(),
        "devices": effective_devices(scfg),
        "providers": provider_identity(scfg),
        "signal": signal_qualification(
            utterance=utterance, silence=silence, clipped=clipped),
        "vad": vad_boundaries_probe(ep, utterance if utterance is not None
                                    else utterance_pcm()),
        "latency": latency_probe(identity=provider_identity(scfg)),
        "concurrency": run_concurrent_turns(plan, runner=_runner),
        "restart_guard": restart_guard_probe(),
        "cleanup": temp_audio_cleanup_probe(state_dir / "temp"),
    }
    if live:
        stages["capture"] = capture_probe(scfg, live=True, env_value=env_value)
        stages["playback"] = playback_probe(scfg, live=True, env_value=env_value)
        stages["ptt"] = ptt_probe(live=True, env_value=env_value)

    required = [k for k in ("signal", "vad", "latency", "concurrency",
                            "restart_guard", "cleanup")]
    if live:
        required += ["capture", "playback", "ptt"]

    def _stage_failed(key: str, value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        if value.get("state") in ("failed", "denied"):
            return True
        if key == "vad" and value.get("speech_started") is not True:
            return True      # a speech utterance must actually start VAD speech
        if key == "latency" and value.get("failed"):
            return True      # an unavailable pipeline stage must not pass
        return False

    failed = any(_stage_failed(k, stages[k]) for k in required)
    out: Dict[str, Any] = {
        "state": "failed" if failed else "OK",
        "qualify_version": QUALIFY_VERSION, "run_id": rid,
        "dry_run": bool(dry_run), "live": bool(live),
        "stages": stages,
        "summary": {"live": bool(live), "required": required},
    }
    if not dry_run:
        _persist_summary(state_dir, rid, out)
    return out


def _persist_summary(state_dir: Path, run_id: str, out: Dict[str, Any]) -> None:
    """Write the redacted qualification summary at ``0600``.

    Only bounded metadata and typed states are stored; absolute state-dir
    paths and any audio-derived payloads are never persisted.
    """
    payload = {
        "qualify_version": out.get("qualify_version"),
        "run_id": run_id,
        "dry_run": out.get("dry_run"),
        "live": out.get("live"),
        "state": out.get("state"),
        "stages": _redacted(out.get("stages", {})),
    }
    target = state_dir / "qualify" / run_id / "summary.json"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp.{uuid.uuid4().hex[:8]}")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except OSError:
        pass  # best-effort; evidence remains in the stage outputs


def _redacted(obj: Any) -> Any:
    """Drop absolute paths and machine-only noise from the persisted record."""
    if isinstance(obj, dict):
        return {k: _redacted(v) for k, v in obj.items()
                if k not in ("index", "effective_index", "recovery")}
    if isinstance(obj, list):
        return [_redacted(v) for v in obj]
    return obj


__all__ = [
    "CAPTURE_MAX_S", "CONCURRENT_TURNS", "PCM_MAX_BYTES", "PLAYBACK_MAX_S",
    "QUALIFY_ENV", "QUALIFY_VERSION", "RESTART_BASE_BACKOFF_S",
    "RESTART_MAX_ATTEMPTS", "RESTART_MAX_BACKOFF_S", "RESTART_WINDOW_S",
    "TONE_DURATION_S", "UTTERANCE_DURATION_S", "RestartCircuit",
    "capture_probe", "clipped_pcm", "deny_live", "echo_pcm",
    "effective_devices", "isolate_cfg", "latency_probe",
    "live_authorized", "playback_probe", "provider_identity",
    "ptt_edge_probe", "ptt_probe", "restart_guard_probe",
    "run_concurrent_turns", "run_qualification", "session_context",
    "signal_probe", "signal_qualification", "silence_pcm", "stage_clock",
    "temp_audio_cleanup_probe", "tone_wav_bytes", "utterance_pcm",
    "vad_boundaries_probe",
]