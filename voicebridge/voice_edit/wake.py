"""Hands-free wake command ("Hey Qubit, ...").

Uses the ONE existing STT authority: a cheap PulseAudio capture + a local
energy VAD detects a spoken segment, then that segment is transcribed through
the existing authenticated STT WebSocket (the same one Right-Ctrl uses). There
is no second speech backend, no second session store, and no always-on cloud
egress.

Privacy/egress: only detected speech segments are transcribed, locally; a
non-wake transcript is discarded immediately and never persisted. The monitor
pauses whenever a Right-Ctrl session is active so the two never both own the
microphone input.

Protocol:
* the wake phrase is stripped from the transcript; the remainder is the command
  ("Hey Qubit, type this for me: X" -> "type this for me: X");
* if the speaker paused after the wake phrase, the NEXT utterance is taken as
  the command (a short armed window);
* a wake with no command is ignored (no accidental typing).
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable, List, Optional

import numpy as np

from . import debug as _dbg
from .sound import beep


def _trace(stage: str, **fields: Any) -> None:
    try:
        from ..logging_setup import log_event
        log_event(f"voice_edit.wake.{stage}", **fields)
    except Exception:
        pass


DEFAULT_PHRASE = "hey qubit"
# Common ASR renderings of the wake word ("hey" is often dropped, "qubit" is
# heard as "cubit"/"qbit").
WAKE_ALIASES = (
    "hey qubit", "hi qubit", "ok qubit", "okay qubit", "hey cubit",
    "hey qbit", "hey qubits", "a qubit", "a cubit", "eh qubit",
    # Common ASR renderings of "Qubit" (faster-whisper heard "Hey Cupid"):
    "hey cupid", "hey cubid", "hey cue bit", "hey cube it", "hey quip it",
    "hi cupid", "ok cupid", "okay cupid", "a cupid", "hey cutie",
)
# Bare wake word at the very start (only when ASR dropped "hey"). Deliberately
# anchored to the first token so "the qubit is fast" never triggers it.
_BARE_WAKE_RE = re.compile(
    r"^\s*(?:qubit|cubit|qbit|cupid|cubid|cue bit|cube it)\b", re.IGNORECASE)
# Leading filler allowed before an alias ("ok, hey qubit ...").
_FILLER_RE = r"^\s*(?:(?:ok|okay|so|um|uh|eh|and|hi|hey)[\s,]+)*"


def strip_wake(text: str, aliases: Optional[List[str]] = None) -> Optional[str]:
    """Return the command text after a wake phrase, or None if not present.

    Matching is anchored to the START of the utterance (wake words are spoken
    first), so a sentence that merely mentions "qubit" never triggers it.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    low = cleaned.lower()
    for phrase in (aliases or list(WAKE_ALIASES)):
        m = re.match(_FILLER_RE + re.escape(phrase.lower()) + r"\b", low)
        if m:
            return cleaned[m.end():].lstrip(" \t,.:;!?—-")
    m = _BARE_WAKE_RE.match(low)
    if m:
        return cleaned[m.end():].lstrip(" \t,.:;!?—-")
    return None


class EnergyEndpointer:
    """Very cheap RMS endpointer (no model load; one pass per frame)."""

    def __init__(self, *, frame_ms: int = 300, speech_ms: int = 300,
                 silence_ms: int = 900, max_s: float = 25.0,
                 rms_gate: float = 0.0018, pre_roll_frames: int = 1) -> None:
        self._speech_frames = max(1, int(speech_ms / frame_ms))
        self._silence_frames = max(1, int(silence_ms / frame_ms))
        self._max_frames = max(1, int(max_s * 1000 / frame_ms))
        self._rms_gate = float(rms_gate)
        self.pre_roll_frames = max(0, int(pre_roll_frames))
        self.reset()

    def reset(self) -> None:
        self.state = "idle"
        self.voiced = 0
        self.silent = 0
        self.total = 0

    def add_frame(self, pcm: bytes) -> str:
        self.total += 1
        try:
            a = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
            rms = float(np.sqrt((a ** 2).mean()) / 32768.0) if a.size else 0.0
        except Exception:
            rms = 0.0
        is_voiced = rms >= self._rms_gate
        if self.state == "idle":
            self.voiced = self.voiced + 1 if is_voiced else 0
            if self.voiced >= self._speech_frames:
                self.state = "speech"
                self.silent = 0
            return self.state
        if self.state == "speech":
            self.silent = 0 if is_voiced else self.silent + 1
            if self.silent >= self._silence_frames or self.total >= self._max_frames:
                self.state = "done"
            return self.state
        return self.state


class BufferMic:
    """Finite mic over an in-memory PCM buffer.

    ``on_exhausted`` fires as soon as the last frame is handed out so the STT
    sender finalizes immediately instead of idling.
    """

    def __init__(self, pcm: bytes, *, frame_bytes: int = 9600,
                 on_exhausted: Optional[Callable[[], None]] = None) -> None:
        self._frames: List[bytes] = [pcm[i:i + frame_bytes]
                                     for i in range(0, len(pcm), frame_bytes)]
        self._i = 0
        self._on_exhausted = on_exhausted

    def start(self) -> None:
        pass

    def read_frame(self, timeout: float = 0.2) -> Optional[bytes]:
        if self._i >= len(self._frames):
            if self._on_exhausted:
                self._on_exhausted()
            return None
        frame = self._frames[self._i]
        self._i += 1
        if self._i >= len(self._frames) and self._on_exhausted:
            self._on_exhausted()
        return frame

    def stop(self) -> None:
        pass


class WakeMonitor:
    """Always-listening wake detector feeding the existing STT authority."""

    def __init__(self, cfg: Any, *, on_command: Callable[[str], None],
                 is_busy: Callable[[], bool],
                 mic_factory: Optional[Callable[[], Any]] = None,
                 transcribe: Optional[Callable[[bytes], str]] = None,
                 kws: Optional[Any] = None,
                 phrase: str = "",
                 arm_window_s: float = 6.0) -> None:
        self.cfg = cfg
        self.on_command = on_command
        self.is_busy = is_busy
        self.phrase = phrase or str(_cfg(cfg, "voice_edit", "wake", "phrase",
                                         default=DEFAULT_PHRASE) or DEFAULT_PHRASE)
        self.arm_window_s = float(_cfg(cfg, "voice_edit", "wake", "arm_window_s",
                                       default=arm_window_s) or arm_window_s)
        self._mic_factory = mic_factory
        self._transcribe = transcribe
        # Wake engine: a dedicated keyword spotter when available (default),
        # else the ASR-phrase fallback. The spotter never transcribes; ASR runs
        # only for the utterance that triggered the keyword.
        self.engine = str(_cfg(cfg, "voice_edit", "wake", "engine",
                               default="auto") or "auto").lower()
        self._kws = kws
        if self._kws is None and self.engine in ("auto", "kws"):
            try:
                from .kws import KwsDetector, find_model_dir
                model_dir = find_model_dir(cfg)
                if model_dir is not None:
                    self._kws = KwsDetector(
                        model_dir,
                        threshold=float(_cfg(cfg, "voice_edit", "wake",
                                             "kws_threshold", default=0.25) or
                                        0.25),
                        score=float(_cfg(cfg, "voice_edit", "wake",
                                         "kws_score", default=1.0) or 1.0))
            except Exception as exc:
                self.last_error = f"kws_init: {exc}"[:160]
                self._kws = None
        if self._kws is not None:
            self.engine = "kws"
        elif self.engine == "auto":
            self.engine = "asr"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._armed_until = 0.0
        self._lock = threading.Lock()
        self.last_error = ""

    # -- lifecycle --------------------------------------------------------
    def enabled(self) -> bool:
        return bool(_cfg(self.cfg, "voice_edit", "wake", "enabled",
                         default=False))

    def start(self) -> bool:
        if not self.enabled():
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="voice-edit-wake")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _new_mic(self) -> Any:
        if self._mic_factory is not None:
            return self._mic_factory()
        from .stt_client import ParecMic
        from .noise import resolve_capture_device
        device = resolve_capture_device(self.cfg)
        gain = float(_cfg(self.cfg, "voice_edit", "mic_gain", default=12.0) or 12.0)
        return ParecMic(device=device, frame_ms=300, gain=gain,
                        target_peak=_mic_target_peak(self.cfg, default=0.5),
                        agc=bool(_cfg(self.cfg, "voice_edit", "agc",
                                      default=True)))

    def _do_transcribe(self, pcm: bytes) -> str:
        if self._transcribe is not None:
            return self._transcribe(pcm)
        # Accurate whole-utterance pass first (default); the streaming socket
        # remains the live path and the fallback when the accurate engine is
        # unavailable or yields nothing.
        mode = str(_cfg(self.cfg, "voice_edit", "stt_mode",
                        default="accurate") or "accurate").lower()
        if mode == "accurate":
            ready, reason = _accurate_ready(self.cfg)
            if ready:
                text, fail = "", ""
                try:
                    from .stt_client import transcribe_accurate
                    text, fail = transcribe_accurate(
                        self.cfg, _normalize_pcm(
                            pcm, target_peak=_mic_target_peak(self.cfg)))
                except Exception as exc:
                    fail = str(exc)[:160] or "accurate failed"
                if text.strip():
                    return text
                _trace("accurate_fallback", reason=fail or "empty transcript")
            else:
                _trace("accurate_fallback", reason=reason or "unavailable")
        from .stt_client import SttClient, transcribe_once
        from .stt_client import SttError
        device = str(_cfg(self.cfg, "voice_edit", "asr_device", default="") or "")
        ticket = _mint_ticket(self.cfg)
        if not ticket:
            return ""
        url = _ws_url(self.cfg)
        engine = str(_cfg(self.cfg, "voice_edit", "stt_engine", default="") or "")
        client = SttClient(url=url, ticket=ticket, engine=engine,
                           target_peak=_mic_target_peak(self.cfg, default=0.5))
        pcm = _normalize_pcm(pcm, target_peak=_mic_target_peak(self.cfg))
        ev = threading.Event()
        mic = BufferMic(pcm, on_exhausted=lambda: ev.set())
        try:
            return transcribe_once(client, mic, ev) or ""
        except Exception:
            return ""

    # -- loop -------------------------------------------------------------
    def _loop(self) -> None:
        mic = None
        ep = EnergyEndpointer(
            silence_ms=int(_cfg(self.cfg, "voice_edit", "wake", "silence_ms",
                                default=900) or 900),
            rms_gate=float(_cfg(self.cfg, "voice_edit", "wake", "rms_gate",
                                default=0.0018) or 0.0018))
        try:
            mic = self._new_mic()
            mic.start()
        except Exception as exc:
            self.last_error = str(exc)[:160]
            _trace("mic_error", detail=self.last_error)
            return
        _trace("started", phrase=self.phrase, engine=self.engine)
        buf: List[bytes] = []
        kws_hit = False
        try:
            while not self._stop.is_set():
                # Yield the microphone and STT to any active Right-Ctrl session.
                if self.is_busy():
                    ep.reset()
                    buf.clear()
                    kws_hit = False
                    now = time.monotonic()
                    if now - getattr(self, "_last_busy_log", 0.0) > 30.0:
                        self._last_busy_log = now
                        _trace("busy_skip")
                    if self._kws is not None:
                        try:
                            self._kws.reset()
                        except Exception:
                            pass
                    time.sleep(0.1)
                    continue
                frame = mic.read_frame(timeout=0.2)
                if frame is None:
                    continue
                state = ep.add_frame(frame)
                if self._kws is not None:
                    try:
                        samples = (np.frombuffer(frame, dtype="<i2")
                                   .astype(np.float32) / 32768.0)
                        n = getattr(self, "_dbg_frames", 0) + 1
                        self._dbg_frames = n
                        if n % 100 == 0:
                            _trace("kws.frames", n=n,
                                   peak=round(float(np.abs(samples).max()), 3))
                        if self._kws.feed(samples):
                            kws_hit = True
                            _trace("kws.hit")
                            beep(self.cfg, "wake")
                    except Exception as exc:
                        now = time.monotonic()
                        if now - getattr(self, "_last_kws_err", 0.0) > 5.0:
                            self._last_kws_err = now
                            _trace("kws.error", detail=str(exc)[:120])
                if state in ("speech", "done"):
                    buf.append(frame)
                if state != "done":
                    continue
                pcm = b"".join(buf)
                buf.clear()
                ep.reset()
                hit = kws_hit
                kws_hit = False
                if self._kws is not None:
                    try:
                        self._kws.reset()
                    except Exception:
                        pass
                    with self._lock:
                        armed = time.monotonic() < self._armed_until
                    # With a keyword spotter, ASR runs ONLY for the utterance
                    # that fired the keyword (or the armed follow-up).
                    if not hit and not armed:
                        continue
                self._handle_segment(pcm, forced=hit)
        finally:
            try:
                mic.stop()
            except Exception:
                pass

    def _handle_segment(self, pcm: bytes, *, forced: bool = False) -> None:
        if len(pcm) < 4800:
            return
        _trace("segment", bytes=len(pcm), forced=forced,
               engine=self.engine)
        try:
            text = self._do_transcribe(pcm).strip()
        except Exception as exc:
            self.last_error = str(exc)[:160]
            _trace("transcribe_error", detail=self.last_error)
            return
        _trace("transcript", chars=len(text), engine=self.engine,
               **(_dbg.text_fields(self.cfg, text=text)))
        if not text:
            return
        now = time.monotonic()
        with self._lock:
            armed = now < self._armed_until
        aliases = [self.phrase, *WAKE_ALIASES]
        if forced:
            # The keyword spotter fired: whatever ASR heard is the command.
            # Strip the wake phrase if ASR rendered it, else use the whole text.
            remainder = strip_wake(text, aliases=aliases)
            cmd = remainder if remainder is not None else text
            if cmd.strip():
                self._emit(cmd)
            else:
                with self._lock:
                    self._armed_until = time.monotonic() + self.arm_window_s
            return
        if armed:
            with self._lock:
                self._armed_until = 0.0
            remainder = strip_wake(text, aliases=aliases)
            self._emit(remainder if remainder is not None else text)
            return
        remainder = strip_wake(text, aliases=aliases)
        if remainder is None:
            _trace("discarded", chars=len(text),
                   **(_dbg.text_fields(self.cfg, text=text)))
            return  # not a wake phrase: discard, never type it
        beep(self.cfg, "wake")
        if remainder.strip():
            self._emit(remainder)
        else:
            with self._lock:
                self._armed_until = time.monotonic() + self.arm_window_s

    def _emit(self, command: str) -> None:
        cmd = (command or "").strip()
        if cmd:
            _trace("command", chars=len(cmd),
                   **(_dbg.text_fields(self.cfg, command=cmd)))
            try:
                self.on_command(cmd)
            except Exception as exc:
                self.last_error = str(exc)[:160]


def _mic_target_peak(cfg: Any, default: float = 0.6) -> float:
    """Configured ``voice_edit.mic_target_peak``, clamped to 0.05..0.95.

    Mirrors the ``update_voice_edit`` clamp in settings.py (not imported to
    avoid a cycle). Absent/unparseable → ``default`` (0.6 preserves the
    historical ``_normalize_pcm`` default; capture clients pass 0.5 to match
    the settings default and the ``ParecMic`` constructor default).
    """
    try:
        raw = _cfg(cfg, "voice_edit", "mic_target_peak", default=None)
        if raw is None:
            return float(default)
        return max(0.05, min(float(raw), 0.95))
    except Exception:
        try:
            return float(default)
        except Exception:
            return 0.6


def _normalize_pcm(pcm: bytes, target_peak: float = 0.6,
                   max_gain: float = 24.0) -> bytes:
    """Peak-normalize one utterance so ASR sees a healthy level."""
    try:
        a = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        if a.size == 0:
            return pcm
        peak = float(np.max(np.abs(a))) / 32768.0
        if peak <= 0.001:
            return pcm
        factor = min(max_gain, target_peak / peak)
        if factor <= 1.01:
            return pcm
        return np.clip(a * factor, -32768, 32767).astype("<i2").tobytes()
    except Exception:
        return pcm


def _accurate_ready(cfg: Any) -> tuple[bool, str]:
    """Whether the accurate transcriber is importable and reports available."""
    try:
        from . import asr_accurate
    except Exception as exc:
        return False, f"accurate unavailable: {exc}"[:160]
    try:
        if asr_accurate.is_available(cfg):
            return True, ""
    except Exception as exc:
        return False, f"accurate unavailable: {exc}"[:160]
    return False, "accurate unavailable"


def _cfg(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default)
    except Exception:
        return default


def _mint_ticket(cfg: Any) -> str:
    try:
        from ..desktop_helper import HelperClient, resolve_service_base, resolve_token
        client = HelperClient(resolve_service_base(cfg), resolve_token(cfg))
        return client.mint_stt_ticket()
    except Exception:
        return ""


def _ws_url(cfg: Any) -> str:
    try:
        from ..desktop_helper import resolve_service_base
        base = resolve_service_base(cfg)
        return base.replace("http://", "ws://").replace("https://", "wss://") \
            + "/agent/api/audio/stt/ws"
    except Exception:
        return "ws://127.0.0.1:8890/agent/api/audio/stt/ws"


__all__ = ["WakeMonitor", "strip_wake", "EnergyEndpointer", "BufferMic",
           "DEFAULT_PHRASE", "WAKE_ALIASES"]
