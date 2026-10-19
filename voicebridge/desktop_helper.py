"""Global desktop voice-typing / selected-text-editing helper.

Run with ``python -m voicebridge.desktop_helper`` (or ``--check`` for a
read-only preflight). It is **disabled by default**: nothing happens until
``voice_edit.enabled`` is true.

Behaviour
---------
* Hold Right-Ctrl anywhere (toggle mode optional):
  * with a selection -> capture it, stream speech to Qubit's existing STT
    WebSocket, parse the spoken command, transform via the existing model
    gateway, show a preview, and replace the selection only on Apply;
  * with no selection -> ordinary dictation: insert the final transcript at the
    cursor (interim text is shown while speaking).
* Escape cancels.

It talks to Qubit through the authenticated local HTTP API and mints a
single-use STT ticket; it does not add a listener, a second ASR authority, a
model router, a session store, or an audit system. Audio is captured with a
PulseAudio/PipeWire client (``parec``) so the raw PortAudio device owned by the
running service is never opened a second time.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from .logging_setup import log_event
from .chat_transport import parse_route_word
from .voice_edit import debug as _dbg
from .voice_edit.sound import beep
from .voice_edit.desktop import DesktopAdapter
from .voice_edit.overlay import make_overlay
from .voice_edit.schema import SelectionSnapshot
from .voice_edit.session_fsm import (
    PendingAction,
    SessionFSM,
    SessionRegistry,
    classify_confirmation,
)
from .voice_edit.stt_client import ParecMic, SttClient, SttError, transcribe_once
from .voice_edit.talk_events import TalkEventSequencer
from .voice_edit.talk_fsm import TalkStateMachine, reduce as reduce_talk


def _trace(stage: str, **fields: Any) -> None:
    try:
        log_event(f"voice_edit.helper.{stage}", **fields)
    except Exception:
        pass


def _route_target_fields(target: Any) -> "tuple[str, str, str]":
    """(kind, agent_id, session_key) for a wake route target (never raises).

    Accepts the :class:`~voicebridge.voice_edit.wake_routing.RouteTarget`
    dataclass or its plain-dict wire shape. Junk → current.
    """
    try:
        if isinstance(target, dict):
            if target.get("mode") == "current":
                return "current", "", ""
            agent = str(target.get("agentId") or "")
            session = str(target.get("sessionKey") or "")
            if agent and not session:
                return "agent", agent, ""
            if session and not agent:
                return "session", "", session
            return "current", "", ""
        return (str(getattr(target, "kind", "") or ""),
                str(getattr(target, "agent_id", None) or ""),
                str(getattr(target, "session_key", None) or ""))
    except Exception:
        return "current", "", ""


def _route_session_key(target: Any) -> str:
    """The target's ``sessionKey`` when it routes to a session, else "".

    Only an explicit session target yields a key: an ``agentId`` has no safe
    session derivation on this surface (no agent→session registry is in
    scope), so it falls back to the current session and is recorded/audited
    instead. Never raises.
    """
    kind, _agent, session = _route_target_fields(target)
    return session if kind == "session" else ""


#: How long a staged "say yes" confirmation stays valid. Voice confirmations
#: bind intent to recency: the arm window gates the reply to seconds, and this
#: TTL is the backstop for re-woken or hotkey confirmations.
_PENDING_TASK_TTL_S = 120.0

#: Stable session key for the helper's own voice session — the default when a
#: turn carries no explicit route target (``mode:current`` / Right-Ctrl). A
#: routed wake target's ``sessionKey`` scopes that turn instead, so a staged
#: confirmation belongs to the session that staged it (voice_edit/session_fsm).
_DEFAULT_VOICE_SESSION = "voice-chat"

#: Backstop for one wake turn (journal 2026-10-04 10:17-10:18: turns that
#: reach ``helper.wake.listening`` and never answer feel dead). The worker
#: blocks in HTTP (route 120 s / task 180 s / ask 90 s / speak 60 s) with
#: state PROCESSING, and every following turn refuses via ``_open_request``
#: with only a trace. Past this bound the watchdog degrades LOUDLY (typed
#: ``wake.turn_timeout`` + error status) and frees the loop to IDLE so the
#: next turn processes; a late-stale worker landing afterwards is harmless
#: (same as a slow turn today). Generous over the slowest legit path
#: (180 s task + ~155 s speak ≈ 335 s). ``<= 0`` disables the watchdog.
_WAKE_TURN_TIMEOUT_S = 360.0


class HelperError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


def _read_cfg(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default)
    except Exception:
        return default


#: Continuous ("keep listening") mode: after a turn completes, re-arm the
#: wake listener for a bounded run of follow-up commands so the user does
#: not repeat the wake phrase for each command. OFF (the default) keeps
#: today's single-turn behavior byte-identical.
_CONTINUOUS_DEFAULT_WINDOW_S = 15.0
_CONTINUOUS_MIN_WINDOW_S = 3.0
_CONTINUOUS_MAX_WINDOW_S = 120.0
_CONTINUOUS_DEFAULT_MAX_FOLLOWUPS = 5
_CONTINUOUS_MIN_FOLLOWUPS = 1
_CONTINUOUS_MAX_FOLLOWUPS = 20


def continuous_settings(cfg: Any) -> "tuple[bool, float, int]":
    """``(enabled, window_s, max_followups)`` for continuous mode.

    Read from ``voice_edit.continuous_mode`` (default OFF),
    ``voice_edit.continuous_window_s`` (default 15.0, clamped 3..120) and
    ``voice_edit.continuous_max_followups`` (default 5, clamped 1..20). The
    clamps are authoritative here so a hand-edited or out-of-range stored
    value can never open an unbounded listening window. Never raises.
    """
    try:
        on = _read_cfg(cfg, "voice_edit", "continuous_mode", default=False)
        on = False if on is None else bool(on)
    except Exception:
        on = False
    try:
        raw_window = _read_cfg(cfg, "voice_edit", "continuous_window_s",
                               default=None)
        window = (float(_CONTINUOUS_DEFAULT_WINDOW_S) if raw_window is None
                  else float(raw_window))
    except Exception:
        window = _CONTINUOUS_DEFAULT_WINDOW_S
    if window != window:  # NaN — never arm with an unusable window
        window = _CONTINUOUS_DEFAULT_WINDOW_S
    window = max(_CONTINUOUS_MIN_WINDOW_S,
                 min(window, _CONTINUOUS_MAX_WINDOW_S))
    try:
        raw_maxf = _read_cfg(cfg, "voice_edit", "continuous_max_followups",
                             default=None)
        maxf = (int(_CONTINUOUS_DEFAULT_MAX_FOLLOWUPS) if raw_maxf is None
                else int(raw_maxf))
    except Exception:
        maxf = _CONTINUOUS_DEFAULT_MAX_FOLLOWUPS
    maxf = max(_CONTINUOUS_MIN_FOLLOWUPS,
               min(maxf, _CONTINUOUS_MAX_FOLLOWUPS))
    return on, window, maxf


def verbatim_transcript(text: Any, cfg_or_enabled: Any = False) -> str:
    """Chrome-style transcript: exactly what was heard.

    Lowercase, no punctuation, words/numbers/diacritics kept
    (hyphens split into spaces, intra-word apostrophes kept). Routing is
    punctuation-independent (the parser strips separators itself), so the
    normalized form is safe to route, display, and dictate. Honours
    ``voice_edit.verbatim_transcript`` (default OFF — transcripts keep
    the engine's punctuation and casing); OFF returns the input
    unchanged. Never raises.
    """
    raw = str(text or "")
    try:
        if hasattr(cfg_or_enabled, "get"):
            on = _read_cfg(cfg_or_enabled, "voice_edit",
                           "verbatim_transcript", default=False)
            on = False if on is None else bool(on)
        else:
            on = bool(cfg_or_enabled)
    except Exception:
        on = False
    if not on:
        return raw
    try:
        t = raw.lower()
        t = re.sub(r"[-–—]", " ", t)
        t = re.sub(r"[^\w\s']", "", t, flags=re.UNICODE)
        t = " ".join(t.split()).strip(" '")
        return t
    except Exception:
        return raw


#: Speak-time legacy-brand substitution (lane 4, 2026-10-03): the brain
#: stitches legacy self-reference tokens from memory context / skill prose
#: (e.g. today's journal: helper.answer spoke "I'll use Hermes agent
#: instead of OpenClaw."). Spoken voice IS a product surface (DECISIONS #7),
#: so the OUTGOING spoken excerpt is substituted inside _speak() only.
#: Panel preview / History rows keep the VERBATIM brain text for audit
#: honesty — never route them through this. Small explicit map,
#: case-insensitive, longest-first; no fuzzy matching. Tradeoff (stated):
#: code/URLs naming those strings are also substituted in the ephemeral
#: speech; the verbatim stays in History/panel for copy, so no data is lost.
_SPEAK_BRAND_MAP = (
    ("Hermes agent", "Qubit"),
    ("Nous Research", "Qubit"),
    ("NousResearch", "Qubit"),
    ("OpenClaw", "Qubit"),
    ("Hermes", "Qubit"),
)


def speak_brand_text(text: Any) -> str:
    """Substitute legacy self-reference tokens in OUTGOING speech (pure).

    Never raises; non-string/empty input returns "" (falsy, like before).
    Sentences without legacy tokens are returned byte-identical.
    """
    raw = str(text or "")
    if not raw:
        return ""
    try:
        for src, dst in _SPEAK_BRAND_MAP:
            raw = re.sub(re.escape(src), dst, raw, flags=re.IGNORECASE)
        return raw
    except Exception:
        return str(text or "")


def _mic_target_peak(cfg: Any, default: float = 0.5) -> float:
    """Configured ``voice_edit.mic_target_peak`` (clamped 0.05..0.95).

    Mirrors ``voice_edit.wake._mic_target_peak`` for the Right-Ctrl path so
    both capture paths share one value. Unreadable cfg → ``default``.
    """
    try:
        raw = _read_cfg(cfg, "voice_edit", "mic_target_peak", default=None)
        if raw is None:
            return default
        return max(0.05, min(float(raw), 0.95))
    except Exception:
        return default


class _NsFilterMic:
    """Wrap a mic; run the software NS/AGC stage on every frame read.

    Only installed when ``voice_edit.noise_suppression`` is on (see
    ``DesktopHelper._transcribe``). OFF → never constructed. Never raises out
    of ``read_frame``: a filter fault returns the raw frame. Everything except
    ``read_frame`` delegates to the wrapped mic.
    """

    def __init__(self, mic: Any) -> None:
        self._mic = mic

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["_mic"], name)

    def read_frame(self, timeout: float = 0.5) -> Optional[bytes]:
        frame = self._mic.read_frame(timeout)
        if not frame:
            return frame
        try:
            from .voice_edit.noise import apply_capture_filter
            return apply_capture_filter(frame, True)
        except Exception:
            return frame


class HelperClient:
    """Thin authenticated HTTP client for the voice-edit service."""

    def __init__(self, base_url: str, token: str = "",
                 opener: Optional[Callable[..., Any]] = None) -> None:
        self.base = base_url.rstrip("/")
        self.token = token
        self._opener = opener or urllib.request.urlopen

    def _request(self, method: str, path: str, payload: Optional[dict] = None,
                 timeout: float = 90.0) -> Dict[str, Any]:
        url = f"{self.base}/agent{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("X-Api-Token", self.token)
            req.add_header("X-Qubit-Session-Token", self.token)
        try:
            with self._opener(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
                return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", "replace")
                parsed = json.loads(body) if body.strip() else {}
            except Exception:
                parsed = {}
            code = str(parsed.get("code") or f"HTTP_{exc.code}")
            raise HelperError(code, str(parsed.get("detail", "") or "")[:200])
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise HelperError("VOICE_EDIT_SERVICE_UNREACHABLE",
                              str(exc)[:160])

    def status(self) -> Dict[str, Any]:
        return self._request("GET", "/api/voice-edit/status", timeout=10)

    def begin(self, session_id: str = "") -> Dict[str, Any]:
        # Optional session binding (wake routing / OpenClaw ``voicewake.routing``
        # port): the service ``begin``/``route`` pair already carries the
        # request's session id. Empty keeps the payload byte-identical to the
        # pre-routing call.
        payload = {"session_id": session_id} if session_id else {}
        return self._request("POST", "/api/voice-edit/session", payload)

    def capture(self, rid: str) -> Dict[str, Any]:
        return self._request("POST", f"/api/voice-edit/{rid}/capture", {})

    def transform(self, rid: str, transcript: str) -> Dict[str, Any]:
        return self._request("POST", f"/api/voice-edit/{rid}/transform",
                             {"transcript": transcript})

    def route(self, rid: str, transcript: str) -> Dict[str, Any]:
        """The ONE voice decision point + dispatch for one utterance."""
        return self._request("POST", f"/api/voice-edit/{rid}/route",
                             {"transcript": transcript}, timeout=120.0)

    def task(self, text: str, *, confirm: bool = False,
             request_id: str = "") -> Dict[str, Any]:
        """Confirm-gated task: confirm=False proposes, confirm=True runs."""
        return self._request("POST", "/api/voice-edit/task",
                             {"text": text, "confirm": confirm,
                              "request_id": request_id}, timeout=180.0)

    def edit(self, rid: str, text: str) -> Dict[str, Any]:
        return self._request("POST", f"/api/voice-edit/{rid}/edit", {"text": text})

    def retry(self, rid: str, transcript: str = "") -> Dict[str, Any]:
        return self._request("POST", f"/api/voice-edit/{rid}/retry",
                             {"transcript": transcript})

    def apply(self, rid: str, *, confirm: bool = True,
              method: str = "") -> Dict[str, Any]:
        return self._request("POST", f"/api/voice-edit/{rid}/apply",
                             {"confirm": confirm, "method": method})

    def revert(self, rid: str) -> Dict[str, Any]:
        return self._request("POST", f"/api/voice-edit/{rid}/revert", {})

    def cancel(self, rid: str) -> Dict[str, Any]:
        return self._request("POST", f"/api/voice-edit/{rid}/cancel", {})

    def ask(self, text: str, request_id: str = "") -> str:
        """Ask the Qubit brain a question and return its answer (no tools)."""
        return str(self.ask_result(text, request_id).get("answer", "") or "")

    def ask_result(self, text: str, request_id: str = "") -> Dict[str, Any]:
        """Ask and return the full result (answer + whether it was spoken)."""
        return self._request("POST", "/api/voice-edit/ask",
                             {"text": text, "request_id": request_id},
                             timeout=90.0)

    def speak(self, text: str) -> bytes:
        """Render ``text`` with the ONE local TTS authority (returns WAV bytes)."""
        import base64
        out = self._request("POST", "/api/audio/speak", {"text": text},
                            timeout=60.0)
        url = str(out.get("data_url", "") or "")
        if not url.startswith("data:audio/"):
            return b""
        return base64.b64decode(url.split(",", 1)[1])

    def mint_stt_ticket(self) -> str:
        out = self._request("POST", "/api/auth/ws-ticket", {"scope": "stt"},
                            timeout=10)
        ticket = str(out.get("ticket", "") or "")
        if not ticket:
            raise HelperError("STT_TICKET_MISSING", "no ticket in response")
        return ticket


def resolve_service_base(cfg: Any) -> str:
    explicit = str(_read_cfg(cfg, "voice_edit", "service_url", default="") or "")
    if explicit:
        return explicit.rstrip("/")
    host = str(_read_cfg(cfg, "webui", "host", default="127.0.0.1") or "127.0.0.1")
    port = int(_read_cfg(cfg, "webui", "port", default=8890) or 8890)
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{port}"


def resolve_token(cfg: Any) -> str:
    tok = str(_read_cfg(cfg, "webui", "token", default="") or "")
    if tok:
        return tok
    legacy = Path("~/.config/qubit-voicebridge/webui-token.txt").expanduser()
    try:
        if legacy.is_file():
            return legacy.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


#: Journal events proving the room audio is our own reply: service TTS
#: (orchestrator) or the helper's own speak trace. Hearing one recently
#: means a wake segment is echo, not direction (false-fire fix 2026-10-02:
#: the journal showed our own "say yes to run it ..." prompt transcribed
#: back and executed as a command).
_TTS_ECHO_EVENTS = frozenset(
    {"tts.speak", "tts.synthesize", "voice_edit.helper.speak"})


def recent_tts_speak(events_path, window_s=5.0) -> bool:
    """True when a TTS speak event landed in the journal within ``window_s``.

    Read-only tail scan (last 64 KiB, event-name prefilter); a missing or
    unreadable journal returns False (fail open: never wedge wake on a
    logging problem). Event ``ts`` is epoch seconds, same clock as
    :func:`time.time`.
    """
    try:
        window_s = float(window_s)
    except Exception:
        window_s = 5.0
    try:
        with open(str(events_path), "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 65536))
            tail = fh.read().decode("utf-8", "replace")
    except OSError:
        return False
    now = time.time()
    for line in tail.splitlines():
        line = line.strip()
        if not line or ("tts" not in line and "helper.speak" not in line):
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if not isinstance(event, dict) \
                or event.get("event") not in _TTS_ECHO_EVENTS:
            continue
        try:
            spoken_at = float(event.get("ts", 0) or 0)
        except Exception:
            continue
        if spoken_at and now - spoken_at < window_s:
            return True
    return False


def frame_rms(frame: Any) -> float:
    """RMS level of one mono-s16 mic frame as 0.0..1.0 (never raises).

    Pure-per-sample math (no ``audioop`` — deprecated and an error under
    the repo's ``filterwarnings = error``).
    """
    try:
        import array
        import math
        import sys
        data = bytes(frame)
        if len(data) < 2:
            return 0.0
        samples = array.array("h", data[:len(data) // 2 * 2])
        if not samples:
            return 0.0
        if sys.byteorder != "little":
            samples.byteswap()
        mean_sq = math.fsum(float(s) * float(s) for s in samples) / len(samples)
        return max(0.0, min(1.0, math.sqrt(mean_sq) / 32768.0))
    except Exception:
        return 0.0


#: Live mic-level talk meter (2026-10-05): the helper — the single mic
#: owner — publishes a cheap per-frame peak (0.0..1.0 + monotonic ts) that
#: the voice orb renders as a recording-like talk meter. Transport is the
#: existing heartbeat file extras (``mic_level``/``mic_level_ts``); never
#: audio bytes, never text. The orb NEVER opens the mic; it only reads
#: these two numbers.
_MIC_LEVEL_LOCK = threading.Lock()
_MIC_LEVEL = {"level": 0.0, "ts": 0.0}

#: Minimum seconds between heartbeat-file refreshes while capturing (the
#: per-frame in-memory publish is free; the file rewrite is throttled so a
#: 300 ms frame cadence does not churn the state dir).
_MIC_LEVEL_HB_MIN_S = 0.5


def _mic_peak(frame: Any) -> float:
    """Peak abs level of one mono-s16 mic frame as 0.0..1.0 (never raises).

    Same sample reading as :func:`frame_rms` (plain ``array``, no
    ``audioop``); peak (not RMS) so conversational speech visibly pumps
    the meter instead of hovering near the noise floor.
    """
    try:
        import array
        import sys
        data = bytes(frame)
        if len(data) < 2:
            return 0.0
        samples = array.array("h", data[:len(data) // 2 * 2])
        if not samples:
            return 0.0
        if sys.byteorder != "little":
            samples.byteswap()
        peak = 0
        for sample in samples:
            mag = abs(int(sample))
            if mag > peak:
                peak = mag
        return max(0.0, min(1.0, peak / 32768.0))
    except Exception:
        return 0.0


def publish_mic_level(frame: Any) -> float:
    """Record one captured frame's peak; return the clamped level.

    Called on the helper-owned capture path only (PTT mic via
    :class:`LevelTapMic`). Never raises; garbage input publishes 0.0.
    """
    level = _mic_peak(frame)
    try:
        with _MIC_LEVEL_LOCK:
            _MIC_LEVEL["level"] = level
            _MIC_LEVEL["ts"] = time.monotonic()
    except Exception:
        pass
    return level


def read_mic_level() -> "tuple[float, float]":
    """Freshest published ``(level, monotonic_ts)``. Never raises."""
    try:
        with _MIC_LEVEL_LOCK:
            return (float(_MIC_LEVEL.get("level", 0.0) or 0.0),
                    float(_MIC_LEVEL.get("ts", 0.0) or 0.0))
    except Exception:
        return (0.0, 0.0)


def mic_level_for_heartbeat() -> Dict[str, float]:
    """Additive heartbeat extras carrying the talk meter. Never raises.

    Keys: ``mic_level`` (0.0..1.0 peak float) + ``mic_level_ts``
    (``time.monotonic`` seconds of the frame). The consumer treats a
    value older than ~1 s as 0.0 (silence).
    """
    try:
        level, ts = read_mic_level()
        return {"mic_level": max(0.0, min(1.0, float(level))),
                "mic_level_ts": float(ts)}
    except Exception:
        return {"mic_level": 0.0, "mic_level_ts": 0.0}


#: Real-equalizer spectrum publish (orb-equalizer spec 2026-10-05, PUBLISH
#: side only). The helper — the single mic owner — FFTs its own capture
#: frames plus the TTS reply wav and publishes band levels through the
#: existing heartbeat extras. Never audio bytes, never text. The orb only
#: reads these numbers (consume/render live in the orb lane).
_SPEC_N_BANDS = 12
_SPEC_MIN_HZ = 80.0
_SPEC_MAX_HZ = 8000.0
#: dB map: _SPEC_FLOOR_DB (and below) -> 0.0, _SPEC_TOP_DB (and above) -> 1.0.
#: The floor is the noise gate: digital silence reads exactly 0.0, never
#: flicker; room noise sits near the floor, speech/music ride mid-scale.
_SPEC_FLOOR_DB = -60.0
_SPEC_TOP_DB = -6.0
#: Live analysis window (s); TTS timeline slice (s). 100 ms both directions.
_SPEC_WINDOW_S = 0.1
#: TTS timeline cap, in 100 ms frames: 600 = 60 s = 7200 floats worst case.
#: Typical replies are seconds; longer replies truncate (documented, the tail
#: is dropped, never the head). Bounds the heartbeat payload.
_SPEC_TTS_MAX_FRAMES = 600
#: Consumer grace past reply-end before the timeline reads as silence.
_SPEC_TTS_GRACE_S = 1.0

_SPEC_LOCK = threading.Lock()
_SPEC = {"bands": [0.0] * _SPEC_N_BANDS, "ts": 0.0}
_SPEC_BUF = bytearray()
_SPEC_TTS = {"frames": [], "ts": 0.0, "dur": 0.0}


def reset_spec_state() -> None:
    """Clear published spectrum state (test seam). Never raises."""
    try:
        with _SPEC_LOCK:
            _SPEC["bands"] = [0.0] * _SPEC_N_BANDS
            _SPEC["ts"] = 0.0
            del _SPEC_BUF[:]
            _SPEC_TTS["frames"] = []
            _SPEC_TTS["ts"] = 0.0
            _SPEC_TTS["dur"] = 0.0
    except Exception:
        pass


def spec_bands_for_pcm(pcm: Any, *, sample_rate: int = 16000) -> list:
    """12 log-spaced band levels for one 100 ms mono-s16 window (pure).

    ``sample_rate`` sets the window length (``rate // 10`` samples) and the
    rFFT bin frequencies; bands span ~80 Hz-8 kHz (bands above Nyquist on
    low-rate audio read 0.0). Returns 12 floats in 0.0..1.0 (rounded to 4
    decimals); short (< one window) / empty / malformed input returns 12
    zeros. Never raises. numpy only (no scipy).
    """
    zeros = [0.0] * _SPEC_N_BANDS
    try:
        rate = int(sample_rate or 16000)
        if rate <= 0:
            rate = 16000
        need = rate // 10
        data = bytes(pcm)
        import array as _array
        import sys as _sys
        raw = _array.array("h", data[:(len(data) // 2) * 2])
        if not raw or len(raw) < need:
            return list(zeros)
        if _sys.byteorder != "little":
            raw.byteswap()
        import math as _math
        import numpy as _np
        x = _np.asarray(raw[:need], dtype=_np.float64) / 32768.0
        windowed = x * _np.hanning(need)
        mag = _np.abs(_np.fft.rfft(windowed)) / need
        freqs = _np.fft.rfftfreq(need, 1.0 / rate)
        edges = _np.logspace(_math.log10(_SPEC_MIN_HZ),
                             _math.log10(_SPEC_MAX_HZ), _SPEC_N_BANDS + 1)
        out = []
        for i in range(_SPEC_N_BANDS):
            lo, hi = float(edges[i]), float(edges[i + 1])
            sel = mag[(freqs >= lo) & (freqs < hi)]
            m = float(sel.mean()) if sel.size else 0.0
            db = 20.0 * _math.log10(m + 1e-9)
            v = (db - _SPEC_FLOOR_DB) / (_SPEC_TOP_DB - _SPEC_FLOOR_DB)
            out.append(round(max(0.0, min(1.0, v)), 4))
        return out
    except Exception:
        return list(zeros)


def publish_spec_frame(frame: Any, *, sample_rate: int = 16000) -> list:
    """Accumulate one captured frame into 100 ms spectrum windows.

    Called on the helper-owned capture path (via :class:`LevelTapMic`, same
    site as :func:`publish_mic_level`). Frames arrive at mic cadence (~300 ms
    = 3 windows); partial windows buffer until complete. Stores the freshest
    12 bands + ``time.monotonic`` ts; returns the latest bands. Garbage input
    keeps the last publish. Never raises. At most 10 windows FFT'd per call
    (flood guard; surplus oldest bytes dropped).
    """
    try:
        data = bytes(frame)
    except Exception:
        data = b""
    if not data:
        try:
            with _SPEC_LOCK:
                return list(_SPEC["bands"])
        except Exception:
            return [0.0] * _SPEC_N_BANDS
    try:
        rate = int(sample_rate or 16000)
        if rate <= 0:
            rate = 16000
        win_bytes = (rate // 10) * 2
        with _SPEC_LOCK:
            _SPEC_BUF.extend(data)
            # Flood guard: never hold more than 2 windows + this call's work.
            max_keep = win_bytes * 12
            if len(_SPEC_BUF) > max_keep:
                del _SPEC_BUF[:len(_SPEC_BUF) - max_keep]
            done = 0
            while len(_SPEC_BUF) >= win_bytes and done < 10:
                window = bytes(_SPEC_BUF[:win_bytes])
                del _SPEC_BUF[:win_bytes]
                done += 1
                # FFT outside the next lock acquisition is fine (pure), but
                # the window bytes are already detached — compute inline.
                bands = spec_bands_for_pcm(window, sample_rate=rate)
                _SPEC["bands"] = bands
                _SPEC["ts"] = time.monotonic()
            return list(_SPEC["bands"])
    except Exception:
        pass
    try:
        with _SPEC_LOCK:
            return list(_SPEC["bands"])
    except Exception:
        return [0.0] * _SPEC_N_BANDS


def read_spec_bands() -> "tuple[list, float]":
    """Freshest published ``(12 bands, monotonic_ts)``. Never raises."""
    try:
        with _SPEC_LOCK:
            return (list(_SPEC.get("bands", [0.0] * _SPEC_N_BANDS)),
                    float(_SPEC.get("ts", 0.0) or 0.0))
    except Exception:
        return ([0.0] * _SPEC_N_BANDS, 0.0)


def spec_for_heartbeat() -> Dict[str, Any]:
    """Additive heartbeat extras carrying the live spectrum. Never raises.

    Keys: ``spec_bands`` (12 floats 0.0..1.0, freshest 100 ms window) +
    ``spec_ts`` (``time.monotonic`` seconds of the window). The consumer
    treats a value older than ~1 s as silence (flat baseline).
    """
    try:
        bands, ts = read_spec_bands()
        clean = [max(0.0, min(1.0, float(v))) for v in (bands or [])]
        while len(clean) < _SPEC_N_BANDS:
            clean.append(0.0)
        return {"spec_bands": clean[:_SPEC_N_BANDS], "spec_ts": float(ts)}
    except Exception:
        return {"spec_bands": [0.0] * _SPEC_N_BANDS, "spec_ts": 0.0}


def wav_to_spec_timeline(wav: Any) -> "tuple[list, int, float]":
    """Whole-wav band timeline: ``(frames, sample_rate, duration_s)`` (pure).

    Parses the RIFF/WAVE header (16-bit PCM; mono used as-is, multi-channel
    averaged to mono) and FFTs each 100 ms slice via
    :func:`spec_bands_for_pcm`; a short tail slice is zero-padded to a full
    window so the final word is kept. Truncates to ``_SPEC_TTS_MAX_FRAMES``
    (head kept, tail dropped); ``duration_s`` covers the KEPT timeline.
    Malformed input returns ``([], 16000, 0.0)``. Never raises; never logs
    audio or text (only band levels leave this function).
    """
    try:
        data = bytes(wav)
    except Exception:
        return ([], 16000, 0.0)
    try:
        import array as _array
        import struct as _struct
        import sys as _sys
        if len(data) < 44 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
            return ([], 16000, 0.0)
        pos = 12
        rate = 0
        channels = 0
        pcm_data = b""
        size = len(data)
        while pos + 8 <= size:
            tag = data[pos:pos + 4]
            (chunk_len,) = _struct.unpack_from("<I", data, pos + 4)
            body = pos + 8
            if tag == b"fmt " and chunk_len >= 16 and body + 16 <= size:
                (_fmt, channels, rate, _br, _ba, bits) = _struct.unpack_from(
                    "<HHIIHH", data, body)
                if _fmt != 1 or bits != 16 or channels <= 0 or rate <= 0:
                    return ([], 16000, 0.0)
            elif tag == b"data" and body + chunk_len <= size + 1:
                end = min(body + chunk_len, size)
                pcm_data = data[body:end]
            pos = body + chunk_len + (chunk_len & 1)
            if rate and pcm_data:
                break
        if not rate or not pcm_data:
            return ([], 16000, 0.0)
        raw = _array.array("h", pcm_data[:(len(pcm_data) // 2) * 2])
        if not raw:
            return ([], rate, 0.0)
        if _sys.byteorder != "little":
            raw.byteswap()
        if channels > 1:
            n_frames = len(raw) // channels
            mono = _array.array("h", [0]) * n_frames
            for i in range(n_frames):
                base = i * channels
                mono[i] = int(sum(raw[base:base + channels]) / channels)
            raw = mono
        win = rate // 10
        frames = []
        total = len(raw)
        idx = 0
        while idx < total and len(frames) < _SPEC_TTS_MAX_FRAMES:
            sl = raw[idx:idx + win]
            if len(sl) < win:
                pad = _array.array("h", [0]) * (win - len(sl))
                sl = sl + pad
            frames.append(spec_bands_for_pcm(sl.tobytes(),
                                             sample_rate=rate))
            idx += win
        return (frames, rate, len(frames) * _SPEC_WINDOW_S)
    except Exception:
        return ([], 16000, 0.0)


def publish_spec_tts(wav: Any, *, started_at: float = 0.0) -> Dict[str, Any]:
    """Publish the reply's band timeline at TTS synth time. Never raises.

    FFTs the whole wav ONCE (via :func:`wav_to_spec_timeline`) and stores
    the frames with the playback-start monotonic ts (``started_at``; defaults
    to now — synth-complete is ms before playback starts, immaterial for
    100 ms bars). Returns the stored payload dict. Garbage wav stores an
    empty (immediately stale) timeline.
    """
    try:
        frames, _rate, dur = wav_to_spec_timeline(wav)
        try:
            ts = float(started_at) or time.monotonic()
        except Exception:
            ts = time.monotonic()
        if ts <= 0.0:
            ts = time.monotonic()
        with _SPEC_LOCK:
            _SPEC_TTS["frames"] = frames
            _SPEC_TTS["ts"] = ts
            _SPEC_TTS["dur"] = float(dur)
            return {"spec_tts": [list(f) for f in frames],
                    "spec_tts_ts": ts, "spec_tts_dur": float(dur)}
    except Exception:
        pass
    try:
        with _SPEC_LOCK:
            return {"spec_tts": [list(f) for f in _SPEC_TTS.get("frames", [])],
                    "spec_tts_ts": float(_SPEC_TTS.get("ts", 0.0) or 0.0),
                    "spec_tts_dur": float(_SPEC_TTS.get("dur", 0.0) or 0.0)}
    except Exception:
        return {"spec_tts": [], "spec_tts_ts": 0.0, "spec_tts_dur": 0.0}


def read_spec_tts() -> "tuple[list, float, float]":
    """Stored ``(frames, playback-start monotonic ts, duration_s)`` copies."""
    try:
        with _SPEC_LOCK:
            return ([list(f) for f in _SPEC_TTS.get("frames", [])],
                    float(_SPEC_TTS.get("ts", 0.0) or 0.0),
                    float(_SPEC_TTS.get("dur", 0.0) or 0.0))
    except Exception:
        return ([], 0.0, 0.0)


def spec_tts_for_heartbeat() -> Dict[str, Any]:
    """Additive heartbeat extras carrying the TTS timeline. Never raises.

    Keys: ``spec_tts`` (list of 12-float frames, 10/s, head-truncated at
    ``_SPEC_TTS_MAX_FRAMES``) + ``spec_tts_ts`` (playback-start monotonic
    seconds) + ``spec_tts_dur`` (kept-timeline seconds). The consumer derives
    position as ``now - spec_tts_ts`` and reads past reply-end (+grace) as
    silence. While no reply is in flight the frames list is EMPTY (payload
    stays tiny); keys are always present so the schema is stable.
    """
    try:
        frames, ts, dur = read_spec_tts()
        now = time.monotonic()
        if frames and ts > 0.0 and dur > 0.0 \
                and now <= ts + dur + _SPEC_TTS_GRACE_S:
            return {"spec_tts": frames, "spec_tts_ts": ts,
                    "spec_tts_dur": dur}
    except Exception:
        pass
    return {"spec_tts": [], "spec_tts_ts": 0.0, "spec_tts_dur": 0.0}


class LevelTapMic:
    """Wrap a helper-owned mic; publish per-frame peak, pass audio through.

    Every frame read through here keeps its exact bytes (the STT path is
    untouched) while :func:`publish_mic_level` records the peak. Installed
    on the helper's PTT capture mics only — the orb never constructs one
    (it never opens the mic). Optional ``on_frame(level)`` callback lets
    the helper refresh the heartbeat file throttled; faults in the
    callback never break the read. Everything except ``read_frame``
    delegates to the wrapped mic.
    """

    def __init__(self, mic: Any,
                 on_frame: Optional[Callable[[float], None]] = None) -> None:
        self._mic = mic
        self._on_frame = on_frame

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["_mic"], name)

    def read_frame(self, timeout: float = 0.5) -> Optional[bytes]:
        frame = self._mic.read_frame(timeout)
        if frame:
            try:
                level = publish_mic_level(frame)
            except Exception:
                level = 0.0
            # Real-equalizer live spectrum: same helper-owned frames the
            # level tap sees (audio bytes pass through unchanged).
            try:
                publish_spec_frame(frame)
            except Exception:
                pass
            cb = self.__dict__.get("_on_frame")
            if cb is not None:
                try:
                    cb(level)
                except Exception:
                    pass
        return frame


class BargeInWatcher:
    """Time-based confident-speech trigger for TTS barge-in (pure logic).

    Feed one RMS level per mic frame; returns True the moment confident
    speech (``rms >= threshold``) has been continuous for ``min_ms``.
    Any below-threshold frame resets the run, so echo-level audio and
    short transients never fire. ``min_ms`` is clamped to the
    research-backed 150-300 ms band.
    """

    def __init__(self, *, threshold: float = 0.10, min_ms: int = 200,
                 frame_ms: int = 300) -> None:
        self.threshold = float(threshold)
        self.min_ms = max(150, min(300, int(min_ms)))
        self.frame_ms = int(frame_ms)
        self._hot_ms = 0.0

    def feed(self, rms: float, dt_ms: Any = None) -> bool:
        """One mic frame's RMS level. ``dt_ms`` defaults to the mic's nominal
        frame duration (frames arrive at mic cadence, so frame count maps to
        real time); an explicit value overrides for tests/callers with a
        known different cadence."""
        try:
            dt = float(dt_ms) if dt_ms is not None else float(self.frame_ms)
        except Exception:
            dt = float(self.frame_ms)
        try:
            level = float(rms)
        except Exception:
            level = 0.0
        if level >= self.threshold:
            self._hot_ms += max(0.0, dt)
            return self._hot_ms >= self.min_ms
        self._hot_ms = 0.0
        return False


class DesktopHelper:
    """Hotkey -> STT -> transform/dictate -> preview -> apply state machine."""

    IDLE, LISTENING, PROCESSING, PREVIEW = "idle", "listening", "processing", "preview"

    def __init__(self, cfg: Any, *, client: Any, overlay: Any,
                 desktop: Optional[DesktopAdapter] = None,
                 mic_factory: Optional[Callable[[], Any]] = None,
                 stt_factory: Optional[Callable[[str], Any]] = None,
                 notify: Optional[Callable[[str, str], None]] = None,
                 talk: Optional[Any] = None) -> None:
        self.cfg = cfg
        self.client = client
        self.overlay = overlay
        self.desktop = desktop or DesktopAdapter()
        self._mic_factory = mic_factory or self._default_mic_factory
        self._stt_factory = stt_factory or (lambda ticket: SttClient(
            url=str(_read_cfg(cfg, "voice_edit", "stt_ws_url", default="") or
                    "") or self._default_ws_url(),
            ticket=ticket,
            target_peak=_mic_target_peak(cfg),
            engine=str(_read_cfg(cfg, "voice_edit", "stt_engine", default="") or "")))
        self._notify = notify
        self.state = self.IDLE
        self.request_id: str = ""
        self.snapshot: Optional[SelectionSnapshot] = None
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._transcript = ""
        self._interim = ""
        self._preview: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self.toggle = str(_read_cfg(cfg, "voice_edit", "hotkey_mode",
                                    default="hold") or "hold") == "toggle"
        self._active = False
        self._ui_q: "queue.Queue" = queue.Queue()
        self._hk_q: "queue.Queue" = queue.Queue()
        self._hk_stop = threading.Event()
        self._hk_worker: Optional[threading.Thread] = None
        self._hb: Any = None
        self._hb_stop = threading.Event()
        self._hb_thread: Optional[threading.Thread] = None
        self._wake: Any = None
        # Resolved wake route target for the current turn (set by
        # on_wake_command_routed, consumed by _open_request). ``None`` for
        # Right-Ctrl turns and ``mode:current`` — the default session.
        self._wake_route_target: Any = None
        # Session-scoped confirmation state (voice_edit/session_fsm.py): a
        # staged "say yes" belongs to the session that staged it, so unrelated
        # input in another session — or the wake path — can never silently
        # overwrite it. ``_turn_session`` is captured per request in
        # ``_open_request`` from the routed target (or the helper's own voice
        # session), and ``_wake_turn_active`` marks the wake worker so the
        # pending guard is traced as ``wake.pending_guard``.
        self._sessions = SessionRegistry(ttl_s=_PENDING_TASK_TTL_S)
        self._turn_session: str = _DEFAULT_VOICE_SESSION
        self._wake_turn_active: bool = False
        self._fs_active = False
        self._fs_checked = 0.0
        self._gen = 0
        self._repeat_restore: list = []
        self._listen_started_at = 0.0
        # Key-DOWN pre-roll handoff (2026-10-04 onset fix): the pump mic +
        # ring owned by _open_request until _transcribe claims them.
        self._pre_mic = None
        self._pre_roll: list = []
        self._pre_pump = None
        self._pre_pump_stop = None
        # Accurate-vs-streaming provenance (surfaced, never silent).
        self._stt_fallback_reason = ""
        self._final_engine = ""
        # A staged actionable task awaiting an explicit confirmation.
        self._pending_task: Dict[str, Any] = {}
        # True while Qubit is speaking its reply (voice conversation).
        self._speaking = False
        # True when the last _speak() was stopped by barge-in (reset each
        # speak; read by callers/tests, never a second audio authority).
        self._barge_interrupted = False
        # Live player subprocess for the current spoken reply (set in
        # _speak, cleared in its finally). ``_speak_stop`` is the explicit
        # stop flag: SIGUSR2 from the orb -> stop_speaking() sets it and
        # terminates this process, so an orb STOP ends the reply even when
        # barge-in is disabled. Both are junk-safe / never fatal.
        self._playback_proc: Any = None
        self._speak_stop = threading.Event()
        # Orb STOP before TTS starts (thinking/working): ``_turn_seq`` is the
        # current reply turn's epoch, bumped at the START of every turn in
        # ``_process_transcript``. A stop that lands while a turn is
        # PROCESSING but nothing is playing records that epoch in
        # ``_turn_stopped_seq``, and the reply ``_speak`` for THAT turn then
        # aborts instead of speaking. Scoping by epoch means an idle stop
        # never silences a later reply (a new turn bumps the epoch).
        # ``_turn_stop_announced`` emits the canonical stop triple once.
        self._turn_seq = 0
        self._turn_stopped_seq = -1
        self._turn_stop_announced = False
        # Which turn (epoch) already asked the service to cancel its in-flight
        # /ask turn. A STOP asks once per turn: the service's own cancel
        # signals the helper back with SIGUSR2, so an unguarded request would
        # ping-pong forever. -1 means "no turn has asked yet".
        self._turn_cancel_sent = -1
        # Accurate-model prewarm readiness (explicit, not blind): the background
        # prewarm sets the event/state, and the first activation waits for it
        # with a bounded timeout so the user is never left waiting blind.
        # Tests may set these directly (no real model required).
        self._warm_event = threading.Event()
        self._warm_state = "skipped"
        # Talk-meter heartbeat-file refresh throttle (monotonic seconds;
        # see _note_capture_frame). The per-frame in-memory publish is
        # free; the file rewrite is throttled.
        self._level_hb_at = 0.0
        # One ordered typed-talk stream for this helper (OpenClaw
        # ``talk/talk-events`` port). Every boundary below emits through it so
        # observers/FSM reconstruct a turn exactly; ``talk=`` is the test seam.
        self._talk: Any = talk if talk is not None else TalkEventSequencer()

    def _tame_key_repeat(self) -> None:
        """Disable X auto-repeat for Right-Ctrl (and Left-Ctrl).

        With auto-repeat on, holding Right-Ctrl fires a phantom *release* ~0.5 s
        in, which the hotkey listener reports as PTT release -- the session
        ended by itself after a couple of seconds. Restored on shutdown.
        """
        if not bool(_read_cfg(self.cfg, "voice_edit", "disable_key_repeat",
                              default=True)):
            return
        import shutil
        import subprocess
        if not shutil.which("xset") or not self.desktop.session().get("x11"):
            return
        for code in ("105", "37"):   # Control_R, Control_L
            try:
                subprocess.run(["xset", "-r", code], timeout=3, check=False,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            except Exception:
                pass
        self._repeat_tamed = True

    def _restore_key_repeat(self) -> None:
        if not getattr(self, "_repeat_tamed", False):
            return
        import subprocess
        for code in ("105", "37"):
            try:
                subprocess.run(["xset", "r", code], timeout=3, check=False,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            except Exception:
                pass
        self._repeat_tamed = False

    def _wake_busy(self) -> bool:
        """Whether the hands-free wake loop must yield the microphone now.

        True only while a helper session is actively capturing/processing or
        our own reply is still audible (echo guard). Fullscreen is
        DELIBERATELY not part of this gate: including it disabled the whole
        wake loop for as long as any fullscreen game/video was focused
        (journal 2026-10-06: 20+ minutes of ``busy_skip`` with the helper
        otherwise idle and zero TTS events — the owner's "hey qubit worked
        once then stopped" while Right-Ctrl, which has no such gate, kept
        working). Typing into a fullscreen app is still refused at the
        insertion layer (``voice_edit.block_fullscreen``) — that is the
        correct place for the rule. Never raises: a broken probe reads as
        not-busy so wake can never wedge on it.
        """
        try:
            if self.state in (self.LISTENING, self.PROCESSING):
                return True
        except Exception:
            pass
        try:
            if getattr(self, "_speaking", False):
                return True
        except Exception:
            pass
        try:
            if self._tts_echo_busy():
                return True
        except Exception:
            pass
        return False

    def _fullscreen_active(self) -> bool:
        """Cached (2s) check whether the focused window is fullscreen."""
        now = time.monotonic()
        if now - self._fs_checked < 2.0:
            return self._fs_active
        self._fs_checked = now
        try:
            self._fs_active = bool(self.desktop.is_fullscreen(""))
        except Exception:
            self._fs_active = False
        return self._fs_active

    def _tts_echo_busy(self, window_s: float = 5.0) -> bool:
        """True while our own reply is still in the room (echo guard).

        Service TTS (orchestrator) plays through the room speakers, which
        the AT2020 hears; without this the wake loop transcribes our own
        answers back into commands (observed live 2026-10-02). Cached 1s —
        this runs on the wake loop's hot path. Fail open (False) when the
        journal is unreadable, so wake never wedges on a logging problem.
        """
        now = time.monotonic()
        if now - getattr(self, "_tts_echo_checked", 0.0) < 1.0:
            return bool(getattr(self, "_tts_echo_cached", False))
        busy = False
        try:
            from .config import canonical_state_dir
            busy = recent_tts_speak(canonical_state_dir() / "events.jsonl",
                                    window_s=window_s)
        except Exception:
            busy = False
        self._tts_echo_checked = now
        self._tts_echo_cached = busy
        return busy

    def _default_mic_factory(self) -> Any:
        # ``voice_edit.asr_device`` names a PulseAudio/PipeWire source; empty
        # means the system default source (the user's microphone).
        from .voice_edit import noise as _noise
        device = _noise.resolve_capture_device(self.cfg)
        gain = float(_read_cfg(self.cfg, "voice_edit", "mic_gain",
                               default=12.0) or 12.0)
        return ParecMic(device=device, gain=gain,
                        target_peak=_mic_target_peak(self.cfg),
                        agc=bool(_read_cfg(self.cfg, "voice_edit", "agc",
                                           default=True)))

    def _default_ws_url(self) -> str:
        base = resolve_service_base(self.cfg)
        return base.replace("http://", "ws://").replace("https://", "wss://") \
            + "/agent/api/audio/stt/ws"

    # -- lifecycle -------------------------------------------------------
    def enabled(self) -> bool:
        return bool(_read_cfg(self.cfg, "voice_edit", "enabled", default=False))

    def start(self) -> bool:
        if not self.enabled():
            self._log_status("disabled", "voice_edit.enabled is false")
            return False
        # Thread-safe UI marshalling: workers enqueue; the Tk mainloop drains.
        self._ui_q = queue.Queue()
        self._drain_ui()
        self._hk_q = queue.Queue()
        self._hk_stop = threading.Event()
        self._hk_worker = threading.Thread(target=self._hotkey_loop, daemon=True,
                                           name="voice-edit-hotkey")
        self._hk_worker.start()
        self._tame_key_repeat()
        # Match the browser capture chain: WebRTC noise suppression + AEC.
        try:
            from .voice_edit import noise as _noise
            self._noise_source = _noise.resolve_capture_device(self.cfg)
        except Exception:
            self._noise_source = ""
        # Hands-free wake ("Hey Qubit") uses the same STT authority and the same
        # command path; it pauses whenever a Right-Ctrl session is active.
        try:
            from .voice_edit.wake import WakeMonitor
            # Busy = a session is actively capturing/processing, or our own
            # reply is still playing through the room (echo guard). See
            # ``_wake_busy`` for why fullscreen is deliberately NOT part of
            # this gate. A PREVIEW left open must NOT block wake forever: a
            # new wake simply supersedes it.
            self._wake = WakeMonitor(
                self.cfg, on_command=self.on_wake_command,
                is_busy=self._wake_busy,
                # Lane 1 (2026-10-03): let the wake loop's bare-confirm
                # precheck see this helper's staged confirmation (e.g. the
                # timer). Read-only TTL mirror — never stages, settles, or
                # clears; a broken read degrades to no-pending (False).
                pending_probe=self._wake_has_live_pending,
                # 2026-10-03 (silent-discard fix): route command-shaped
                # unarmed discards to the heard-preview status line.
                # Status line only — the hook never beeps/speaks/emits.
                on_hint=self._on_wake_hint,
                # OpenClaw ``voicewake.routing`` port: resolve the spoken
                # command to a target session/agent and carry it to the turn
                # (see on_wake_command_routed). Absent/malformed route falls
                # back to the plain single-arg path.
                on_command_route=self.on_wake_command_routed)
            if self._wake.enabled():
                self._wake.start()
        except Exception:
            self._wake = None
        # Warm the accurate decoder in the background: the cold model load is
        # ~10 s, the decode itself ~150 ms. Best-effort and local-only. The
        # readiness is explicit (``_warm_event``/``_warm_state``) so the first
        # activation can show a cue and wait, bounded, instead of going blind.
        self._warm_event = threading.Event()
        if str(_read_cfg(self.cfg, "voice_edit", "stt_mode",
                         default="accurate") or "").lower() == "accurate":
            self._warm_state = "pending"

            def _warm_accurate() -> None:
                self._warm_state = "warming"
                try:
                    from .voice_edit import asr_accurate
                    if asr_accurate.is_available(self.cfg):
                        if asr_accurate.prewarm(self.cfg):
                            self._warm_state = "warm"
                        else:
                            self._warm_state = "unavailable"
                    else:
                        self._warm_state = "unavailable"
                except Exception:
                    # Never let the background prewarm thread raise.
                    self._warm_state = "unavailable"
                finally:
                    self._warm_event.set()

            threading.Thread(target=_warm_accurate, daemon=True,
                             name="voice-edit-stt-warm").start()
        else:
            self._warm_state = "skipped"
            self._warm_event.set()
        # Liveness heartbeat: the dashboard yields Right-Ctrl while this is live
        # so only one authority owns the global hotkey.
        from .voice_edit import heartbeat as _hb
        self._hb = _hb
        _hb.write(session=str(self.desktop.session().get("session_type", "x11")),
                  extra=self._heartbeat_extra())
        self._hb_stop = threading.Event()
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True,
                                           name="voice-edit-heartbeat")
        self._hb_thread.start()
        # Orb click-to-talk trigger (owner opt-in 2026-10-04): SIGUSR1 from
        # the voice orb toggles capture via on_orb_toggle. The handler only
        # enqueues (signal-safe); the hotkey worker does the real work on
        # the existing PTT path. Installed best-effort (main thread only);
        # never raises out of start().
        self._orb_prev_sigusr1: Any = None
        self._orb_prev_sigusr2: Any = None
        self._install_orb_signal_handlers()
        self._ui_status("idle", "hold Right-Ctrl to speak")
        return True

    def _heartbeat_extra(self) -> Dict[str, Any]:
        """Heartbeat extras: liveness keys + the talk-meter level (additive).

        The ``mic_level``/``mic_level_ts`` keys are the orb's recording-like
        meter (0.0..1.0 peak + monotonic ts; consumer reads >1 s old as
        silence). Never raises; a broken read degrades to level 0.0.
        """
        try:
            wake_on = bool(self._wake is not None
                           and self._wake.enabled())
        except Exception:
            wake_on = False
        try:
            wake_engine = str(getattr(self._wake, "engine", "") or "")
        except Exception:
            wake_engine = ""
        extra: Dict[str, Any] = {
            "hotkey_mode": "toggle" if self.toggle else "hold",
            "wake": wake_on,
            "wake_engine": wake_engine,
            "stt_warm": self._warm_state,
            "state": self.state,
        }
        try:
            extra.update(mic_level_for_heartbeat())
        except Exception:
            pass
        try:
            extra.update(spec_for_heartbeat())
        except Exception:
            pass
        try:
            extra.update(spec_tts_for_heartbeat())
        except Exception:
            pass
        return extra

    def _note_capture_frame(self, level: float) -> None:
        """Throttled heartbeat-file refresh carrying the live talk meter.

        Called per captured frame (via :class:`LevelTapMic`) on the
        helper-owned PTT path only. The in-memory level is already fresh;
        this rewrites the heartbeat file at most every
        ``_MIC_LEVEL_HB_MIN_S`` so the orb's 2 s poll sees a live value
        instead of an 8 s-loop-stale one. Never raises; never runs when
        the helper has no heartbeat writer (tests/embedded use).
        """
        try:
            now = time.monotonic()
            if now - float(getattr(self, "_level_hb_at", 0.0) or 0.0) \
                    < _MIC_LEVEL_HB_MIN_S:
                return
            self._level_hb_at = now
            hb = getattr(self, "_hb", None)
            if hb is None:
                return
            hb.write(
                session=str(self.desktop.session().get("session_type",
                                                        "x11")),
                extra=self._heartbeat_extra())
        except Exception:
            pass

    def _heartbeat_loop(self) -> None:
        while not self._hb_stop.wait(8.0):
            try:
                self._hb.write(
                    session=str(self.desktop.session().get("session_type", "x11")),
                    extra=self._heartbeat_extra())
            except Exception:
                pass

    def shutdown(self) -> None:
        try:
            import signal as _sig
            prev = getattr(self, "_orb_prev_sigusr1", None)
            if prev is not None:
                try:
                    _sig.signal(_sig.SIGUSR1, prev)
                except Exception:
                    pass
            prev2 = getattr(self, "_orb_prev_sigusr2", None)
            if prev2 is not None:
                try:
                    _sig.signal(_sig.SIGUSR2, prev2)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if self._wake is not None:
                self._wake.stop()
        except Exception:
            pass
        try:
            self._hk_stop.set()
        except Exception:
            pass
        try:
            self._hb_stop.set()
        except Exception:
            pass
        try:
            self._hb.clear()
        except Exception:
            pass
        try:
            self._restore_key_repeat()
        except Exception:
            pass
        try:
            from .voice_edit import noise as _noise
            if getattr(self, "_noise_source", ""):
                _noise.release()
        except Exception:
            pass
        self._stop.set()

    def on_hotkey(self, event: str, key: str = "") -> None:
        """Hotkey callback (from HotkeyListener's thread): never blocks it.

        Queues to the worker when the helper is running; falls back to a direct
        synchronous handle when it is not (unit tests / embedded use).
        """
        worker = getattr(self, "_hk_worker", None)
        if worker is not None and worker.is_alive():
            self._hk_q.put((event, key))
            return
        self._handle_hotkey(event, key)

    def _install_orb_signal_handlers(self) -> None:
        """Install the orb's SIGUSR1 (toggle) + SIGUSR2 (stop) handlers.

        SIGUSR1 toggles click-to-talk capture; SIGUSR2 stops the live
        spoken reply. Two distinct signals so a stop can never start a
        capture. The previous handlers are saved for :meth:`shutdown`;
        handlers only set flags / enqueue, so they are signal-safe.
        Best-effort (main thread only): a failure never breaks start().
        """
        try:
            import signal as _sig
            self._orb_prev_sigusr1 = _sig.getsignal(_sig.SIGUSR1)
            _sig.signal(_sig.SIGUSR1, lambda _s, _f: self.on_orb_toggle())
        except Exception:
            pass
        # Orb STOP while Qubit is speaking (2026-10-19): SIGUSR2 from the
        # orb terminates the live player subprocess.
        try:
            import signal as _sig
            self._orb_prev_sigusr2 = _sig.getsignal(_sig.SIGUSR2)
            _sig.signal(_sig.SIGUSR2, lambda _s, _f: self.on_orb_stop())
        except Exception:
            pass

    def on_orb_toggle(self) -> None:
        """Orb click-to-talk trigger (SIGUSR1 from the voice orb).

        The orb never opens the mic itself (single mic owner stays this
        helper, no contention): this only enqueues the same toggle the
        click would mean on a key — start capture when idle, finish
        (stop -> transcribe -> turn) while capturing. Queues to the
        hotkey worker when running; direct synchronous handle otherwise
        (unit tests / embedded use). Never raises.
        """
        try:
            worker = getattr(self, "_hk_worker", None)
            if worker is not None and worker.is_alive():
                self._hk_q.put(("orb_toggle", "orb"))
                return
            self._handle_hotkey("orb_toggle", "orb")
        except Exception:
            pass

    def on_orb_stop(self) -> None:
        """Orb STOP trigger (SIGUSR2 from the voice orb): end the reply.

        Unlike :meth:`on_orb_toggle` this does not queue to the hotkey
        worker: stopping playback only sets a flag and terminates the
        player subprocess, both non-blocking and safe from the
        (main-thread) signal handler. It never starts or stops a capture.
        The canonical ``interrupt`` / ``playback.stopped`` /
        ``response.done`` talk events are emitted by the ``_speak`` that
        owns the player as it unblocks. Never raises.
        """
        try:
            self.stop_speaking()
        except Exception:
            pass

    def stop_speaking(self) -> bool:
        """Stop the current spoken reply now (explicit orb STOP path).

        Returns True when a reply was live (``_speaking`` or a player
        subprocess) OR a turn was THINKING/WORKING (the reply has not
        reached ``_speak`` yet). Sets ``_speak_stop`` and terminates
        ``_playback_proc``; the owning ``_speak`` then emits the same
        ``interrupt`` / ``playback.stopped`` / ``response.done`` events the
        barge-in path does, so the orb's state machine ends immediately.

        A stop before any player exists (thinking/working) marks THIS turn's
        epoch stopped, so the pending reply ``_speak`` aborts instead of
        speaking; the marker is scoped to the turn, so an idle stop never
        silences a later reply. Safe to call from any thread (including a
        signal handler), idempotent, and never fatal: a failed terminate
        leaves the normal playback path intact. Does not touch the mic or
        the answer path.

        When a turn is actually live (playing or PROCESSING) this also asks
        the service to cancel the in-flight ``/ask`` turn by its
        ``request_id`` (see :meth:`_request_service_cancel`), so the model or
        agent call and any service-side speech for the turn stop too — not
        just this helper's playback.
        """
        try:
            proc = getattr(self, "_playback_proc", None)
            was_live = bool(getattr(self, "_speaking", False)) or proc is not None
            if not was_live:
                # Nothing playing yet: suppress a reply still in flight
                # (thinking/working) by marking this turn stopped. Idle
                # (no turn) stays a no-op so a later reply is never cut.
                try:
                    if self.state == self.PROCESSING:
                        self._turn_stopped_seq = self._turn_seq
                        _trace("speak.stop_pending", turn=self._turn_seq)
                        try:
                            self._request_service_cancel()
                        except Exception:
                            pass
                        return True
                except Exception:
                    pass
                return False
            try:
                self._speak_stop.set()
            except Exception:
                pass
            self._barge_interrupted = True
            if proc is not None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            _trace("speak.stop")
            try:
                self._request_service_cancel()
            except Exception:
                pass
            return True
        except Exception:
            return False

    def _request_service_cancel(self) -> None:
        """Ask the service to cancel this turn's in-flight ``/ask`` call.

        The orb STOP reaches this helper first (SIGUSR2); the service owns the
        expensive model/agent turn, so the helper tells it to cancel the
        active ``self.request_id``. Best-effort, idempotent PER TURN (the
        service's cancel signals the helper back with SIGUSR2, so an unguarded
        request would ping-pong), and never fatal. The POST runs on a
        short-lived daemon thread: this is reached from the SIGUSR2 handler
        via :meth:`stop_speaking`, which must never block on the network. A
        client without ``cancel`` (older/fake) is a quiet no-op.
        """
        rid = str(getattr(self, "request_id", "") or "")
        if not rid:
            return
        try:
            if getattr(self, "_turn_cancel_sent", -1) == getattr(
                    self, "_turn_seq", 0):
                return
            self._turn_cancel_sent = getattr(self, "_turn_seq", 0)
        except Exception:
            return
        cancel = getattr(getattr(self, "client", None), "cancel", None)
        if not callable(cancel):
            return
        try:
            threading.Thread(target=self._dispatch_service_cancel,
                             args=(cancel, rid), daemon=True,
                             name=f"orb-svc-cancel-{rid[:16]}").start()
        except Exception:
            pass

    @staticmethod
    def _dispatch_service_cancel(cancel: Any, rid: str) -> None:
        """Run one best-effort service cancel; never raises (daemon thread)."""
        try:
            cancel(rid)
        except Exception:
            pass

    def _hotkey_loop(self) -> None:
        while not self._hk_stop.is_set():
            try:
                event, key = self._hk_q.get(timeout=0.5)
            except queue.Empty:
                continue
            self._handle_hotkey(event, key)

    def _handle_hotkey(self, event: str, key: str) -> None:
        try:
            if event == "cancel":
                self.cancel()
                return
            if event == "orb_toggle":
                # Orb click-to-talk: always toggle semantics (the orb has no
                # key-up edge, independent of voice_edit.hotkey_mode).
                self._handle_orb_toggle()
                return
            if event == "ptt_start":
                if self.toggle:
                    if self.state == self.PREVIEW:
                        self.cancel()
                    elif self.state in (self.LISTENING, self.PROCESSING):
                        self._stop_listening()
                    else:
                        self._start_listening()
                else:
                    self._start_listening()
            elif event == "ptt_end" and not self.toggle:
                # Ignore a release that cannot be a real key-up (a synthetic
                # ghost/auto-repeat release), which would end the session early.
                if time.monotonic() - self._listen_started_at < 0.15:
                    _trace("hotkey.ghost_release")
                    return
                self._stop_listening()
        except Exception as exc:  # never kill the hotkey worker
            self._ui_error("VOICE_EDIT_HELPER_ERROR", str(exc)[:160])

    def _handle_orb_toggle(self) -> None:
        """One orb click: start capture / finish capture / cancel preview.

        Toggle semantics always (no key-up exists on the orb path):
        PREVIEW -> cancel; LISTENING/PROCESSING -> finish via the normal
        release tail (transcribe -> turn); IDLE -> the existing
        ``_start_listening`` (begin + mic + STT, the Right-Ctrl path).
        A second click can never overlap a first: ``_open_request``
        refuses while LISTENING/PROCESSING. Never raises.
        """
        try:
            if self.state == self.PREVIEW:
                self.cancel()
            elif self.state in (self.LISTENING, self.PROCESSING):
                self._stop_listening()
            else:
                self._start_listening()
        except Exception as exc:  # never kill the hotkey worker
            self._ui_error("VOICE_EDIT_HELPER_ERROR", str(exc)[:160])

    def _stop_listening(self) -> None:
        """Release of the hotkey: keep a short tail, then finalize.

        A browser keeps the socket open, so the last word is never clipped.
        Here we let the mic stream for ``release_tail_ms`` after the key is
        released so the ASR sees the end of the utterance.
        """
        _trace("stop_listening", active=self._active, state=self.state)
        self._active = False
        self._talk_emit("input.speech_stopped")
        tail = float(_read_cfg(self.cfg, "voice_edit", "release_tail_ms",
                               default=500) or 500) / 1000.0
        if tail <= 0:
            self._stop.set()
            return
        gen = self._gen

        def _fire() -> None:
            if self._gen == gen:   # a new session must not be cut by this tail
                self._stop.set()

        t = threading.Timer(tail, _fire)
        t.daemon = True
        t.start()

    # -- thread-safe UI marshalling --------------------------------------
    def _ui(self, fn: Callable[..., Any], *args: Any) -> None:
        """Queue a UI callback for the Tk main thread (safe from any thread)."""
        q = getattr(self, "_ui_q", None)
        if q is not None and getattr(self.overlay, "root", None) is not None:
            q.put((fn, args))
            return
        try:
            fn(*args)
        except Exception:
            pass

    def _drain_ui(self) -> None:
        q = getattr(self, "_ui_q", None)
        if q is not None:
            while True:
                try:
                    fn, args = q.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn(*args)
                except Exception:
                    pass
        root = getattr(self.overlay, "root", None)
        if root is not None:
            try:
                root.after(50, self._drain_ui)
            except Exception:
                pass

    def _ui_status(self, state: str, detail: str = "") -> None:
        self._ui(self.overlay.set_status, state, detail)

    def _ui_preview(self, payload: Dict[str, Any]) -> None:
        self._ui(self.overlay.show_preview, payload)

    def _ui_error(self, code: str, detail: str) -> None:
        self._ui(self.overlay.show_error, code, detail)

    # -- typed talk events (fail-soft; never break a turn) ---------------
    def _talk_emit(self, event_type: str, **fields: Any) -> None:
        """Emit one typed talk boundary (journaled as ``voice_edit.talk.*``).

        Wrapped fail-soft: a broken/raising/absent sequencer can NEVER break a
        voice turn (it is observability, not control flow).
        """
        try:
            self._talk.emit(event_type, journal=True, **fields)
        except Exception:
            pass

    def _talk_begin(self) -> None:
        """Mint a new talk turn id (one per Right-Ctrl/wake turn). Fail-soft."""
        try:
            self._talk.begin_turn()
        except Exception:
            pass

    def _talk_end(self) -> None:
        """Clear the active talk turn. Fail-soft."""
        try:
            self._talk.end_turn()
        except Exception:
            pass

    def talk_events(self) -> Tuple[Dict[str, Any], ...]:
        """Read-only snapshot of the recent typed talk events (for observers)."""
        try:
            return tuple(self._talk.snapshot().get("events", ()))
        except Exception:
            return ()

    def talk_state(self) -> str:
        """Orb state reduced (pure FSM) from this helper's talk events in order.

        Sequential last-wins (the stream is already ordered by ``seq``); use
        :meth:`voice_edit.talk_fsm.TalkStateMachine.apply_many` when a batch
        needs the orb's overlap priority (listening > speaking > working >
        thinking > idle). Never raises.
        """
        try:
            state = "idle"
            for event in self.talk_events():
                state = reduce_talk(state, event)
            return state
        except Exception:
            return "idle"

    def _history(self, kind: str, heard: str, result: str) -> None:
        """Append one interaction to the panel's persistent history.

        Shows the panel (without stealing keyboard focus) when
        ``voice_edit.history_panel`` is on, so the user can see what was heard
        and what happened after the text has been inserted/sent.
        """
        try:
            import time as _t
            ts = _t.strftime("%H:%M:%S")
            h = " ".join(str(heard or "").split())[:160]
            r = " ".join(str(result or "").split())[:240]
            line = f"{ts}  {kind}: heard {h!r} -> {r!r}"
            self._ui(self.overlay.append_history, line)
            if bool(_read_cfg(self.cfg, "voice_edit", "history_panel",
                              default=True)):
                self._ui(self.overlay.show)
        except Exception:
            pass

    #: Max fired-timer reminders announced on one voice turn (Lane 2,
    #: 2026-10-03): the timer ledger is never pruned, so a long downtime
    #: could leave dozens of `fired` rows — announce at most this many per
    #: turn (oldest first) and drain the rest on following turns instead
    #: of one unbounded speech.
    _TIMER_DELIVERY_PER_TURN = 3

    @staticmethod
    def _timer_reminder_text(mins: int) -> str:
        """Spoken reminder for a fired timer (deterministic, no secrets)."""
        try:
            mins = int(mins)
        except (TypeError, ValueError):
            mins = 0
        if mins <= 0:
            return "Your timer is up."
        plural = "" if mins == 1 else "s"
        return f"Your {mins}-minute timer is up."

    def _deliver_fired_timers(self) -> list:
        """Announce ledger-fired timers on the voice-turn cadence (no push).

        Lane 2 (2026-10-03): the timer firer writes only the ledger (never
        chat/session state), so a fired timer reaches the user here — the
        helper answer path, where unprompted delivery actually speaks.
        Pulls ``poll_fired_timers()`` (reused as-is), skips ids already
        announced this lifetime, honors sleep (panel/History only, never
        speech — the same contract as completions), audits each delivery.
        Never raises and never disturbs the current turn.
        """
        try:
            from .local.actions import poll_fired_timers as _poll
            fired = _poll() or []
        except Exception:
            return []
        announced = getattr(self, "_timers_announced", None)
        if not isinstance(announced, set):
            announced = self._timers_announced = set()
        try:
            sleeping = bool(_read_cfg(self.cfg, "voice_edit", "sleep_mode",
                                      default=False))
        except Exception:
            sleeping = False
        try:
            ordered = sorted(
                [r for r in fired if isinstance(r, dict)],
                key=lambda r: float(r.get("fired_at") or 0.0))
        except Exception:
            ordered = []
        fresh = []
        for rec in ordered:
            try:
                tid = str(rec.get("id") or "")
            except Exception:
                continue
            if not tid or tid in announced:
                continue
            fresh.append(rec)
            if len(fresh) >= self._TIMER_DELIVERY_PER_TURN:
                break
        delivered = []
        for rec in fresh:
            try:
                tid = str(rec.get("id") or "")
                mins = int(rec.get("mins") or 0)
            except (TypeError, ValueError, AttributeError):
                continue
            if not tid:
                continue
            text = self._timer_reminder_text(mins)
            announced.add(tid)
            _trace("timer.reminded", timer_id=tid[:16], mins=mins,
                   slept=sleeping)
            self._history("timer", f"{mins}-minute timer elapsed", text)
            self._notify_status("Qubit", text)
            if not sleeping:
                self._speak(text)
            delivered.append({"id": tid, "mins": mins, "text": text,
                              "slept": sleeping})
        return delivered

    # -- capture / listening --------------------------------------------
    def _warm_timeout_s(self) -> float:
        """Bounded wait for the accurate prewarm, clamped to 0-60 seconds."""
        try:
            val = float(_read_cfg(self.cfg, "voice_edit", "warm_timeout_s",
                                  default=12.0) or 12.0)
        except (TypeError, ValueError):
            val = 12.0
        return max(0.0, min(60.0, val))

    def _wait_for_warm(self) -> None:
        """Show a cue and wait, bounded, for the accurate-model prewarm.

        The cold accurate load is ~10 s while the warm decode is ~150 ms. We
        surface "warming speech model…" so the user is not waiting blind, but we
        always proceed on timeout (and never wait when the model is unavailable
        or was skipped) so the hotkey never feels dead. Best-effort only.
        """
        mode = str(_read_cfg(self.cfg, "voice_edit", "stt_mode",
                             default="accurate") or "").lower()
        if mode != "accurate":
            return
        if self._warm_state in ("warm", "unavailable", "skipped"):
            return
        event = getattr(self, "_warm_event", None)
        if event is None or event.is_set():
            return
        timeout = self._warm_timeout_s()
        if timeout <= 0:
            return
        _trace("warm.wait", timeout_s=timeout, state=self._warm_state)
        self._ui_status("warming", "warming speech model…")
        deadline = time.monotonic() + timeout
        last_refresh = time.monotonic()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if event.wait(min(1.0, remaining)):
                break
            # Refresh the visible cue at most ~every second.
            now = time.monotonic()
            if now - last_refresh >= 1.0:
                last_refresh = now
                if self._warm_state in ("unavailable", "skipped"):
                    break
                self._ui_status("warming", "warming speech model…")
        _trace("warm.done", state=self._warm_state, event_set=event.is_set())

    def _open_request(self) -> bool:
        """Authorize one request (status/e-stop/audit) without capturing yet.

        The hotkey flow opens the request, starts the mic + STT immediately, and
        only then captures the selection, so the first words spoken on keydown
        are never lost to the capture round-trip.
        """
        # Consume any wake route target for THIS turn (set by
        # on_wake_command_routed). Cleared unconditionally so a refused turn
        # can never leak the target into a later Right-Ctrl request. A
        # ``mode:current``/absent target yields "" → today's default session.
        route_session = _route_session_key(self._wake_route_target)
        self._wake_route_target = None
        # Scope this turn's confirmation state to the routed session (or the
        # helper's voice session). Captured before the route target is cleared
        # so _process_transcript sees the same session the request bound.
        self._turn_session = route_session or _DEFAULT_VOICE_SESSION
        with self._lock:
            if not self.enabled() or self.state in (self.LISTENING,
                                                    self.PROCESSING):
                return False
            try:
                st = self.client.status()
                if not st.get("enabled"):
                    self._ui_status("disabled", "feature disabled")
                    return False
                if st.get("estop"):
                    self._ui_status("error", "emergency stop engaged")
                    self._talk_emit("error", code="VOICE_EDIT_ESTOP")
                    return False
            except HelperError as exc:
                self._ui_status("error", f"{exc.code}")
                self._talk_emit("error", code=exc.code)
                return False
            # Accurate mode: cue the wait for the background prewarm, bounded,
            # so the user is not left waiting blind on the first activation.
            self._wait_for_warm()
            self._active = True
            self._stop.clear()
            self._transcript = ""
            self._interim = ""
            self.snapshot = None
            self._gen += 1
            try:
                begin_fn = self.client.begin
                if route_session:
                    try:
                        out = begin_fn(session_id=route_session)
                    except TypeError:
                        # compatibility seam (older/fake clients): no session
                        # kwarg — fall back to the plain call, current session.
                        out = begin_fn()
                else:
                    out = begin_fn()
                rid = str(out.get("request_id", ""))
                self.request_id = rid
            except HelperError as exc:
                self._ui_status("error", f"{exc.code}: {exc.detail}")
                self._talk_emit("error", code=exc.code)
                self._active = False
                return False
            # Key-DOWN edge (2026-10-04 onset fix): open the mic NOW, before
            # the ticket/WS round-trips in _transcribe, and pump onset frames
            # into a bounded pre-roll the transcribe path prepends. Without
            # this, everything spoken during begin + ticket + mic-spawn +
            # WS-connect (~0.3 s warm, far more on a cold accurate load) was
            # never recorded — measured live as "Hello" lost unrecoverably.
            # A mic that will not open here degrades to today's behavior
            # (transcribe-time start); buffered audio is dropped unsent on
            # any failure below, never retained.
            self._pre_mic = None
            self._pre_roll = []
            self._pre_pump = None
            self._pre_pump_stop = None
            try:
                base = self._mic_factory()
                try:
                    from .voice_edit.noise import enabled as _ns_on
                    if _ns_on(self.cfg):
                        base = _NsFilterMic(base)
                except Exception:
                    pass
                # Talk meter: tap the helper-owned mic so every captured
                # frame publishes its peak (audio bytes pass through
                # unchanged; the orb only ever reads the two numbers).
                try:
                    base = LevelTapMic(base,
                                       on_frame=self._note_capture_frame)
                except Exception:
                    pass
                base.start()
            except Exception:
                base = None
            if base is not None:
                self._pre_mic = base
                self._pre_pump_stop = threading.Event()
                pump = threading.Thread(target=self._pump_pre_roll,
                                        daemon=True, name="voice-edit-preroll")
                self._pre_pump = pump
                pump.start()
            self.state = self.LISTENING
            self._listen_started_at = time.monotonic()
            self._talk_begin()
            self._talk_emit("input.speech_started",
                            request_id=self.request_id)
            return True

    def _pump_pre_roll(self) -> None:
        """Fill the onset ring while ticket/WS proceed (key-DOWN pump).

        Reads up to 4 frames (~1.2 s at 300 ms) from the already-started mic
        the request opened; the transcribe path prepends them, so speech at
        the key edge survives setup. Exits on stop/pump-stop/generation turn/
        full ring. Never raises; frames already pass the NS/AGC stage, so a
        quiet onset also meets settled gain (the transient measured
        identical either way needs no separate fix).
        """
        try:
            mic = self._pre_mic
            gen = self._gen
            stop = self._pre_pump_stop
            while (len(self._pre_roll) < 4 and not self._stop.is_set()
                    and (stop is None or not stop.is_set())
                    and self._gen == gen and mic is not None):
                try:
                    frame = mic.read_frame(timeout=0.2)
                except Exception:
                    break
                if frame:
                    self._pre_roll.append(frame)
                    if len(self._pre_roll) > 4:
                        del self._pre_roll[:len(self._pre_roll) - 4]
                else:
                    # Idle mic (or a test double answering None instantly):
                    # never tight-spin; the ring simply stays short.
                    time.sleep(0.05)
        except Exception:
            pass

    def _claim_pre_roll(self) -> "tuple[Any, list]":
        """Take the key-DOWN mic + buffered onset for the transcribe path.

        Stops the pump (bounded join), drains the ring, and transfers mic
        ownership: the caller must NOT start it again and must stop it on
        failure. Returns ``(mic_or_None, frames)``; a missing mic degrades
        to the transcribe-time start. Never raises.
        """
        mic = getattr(self, "_pre_mic", None)
        self._pre_mic = None
        stop = getattr(self, "_pre_pump_stop", None)
        try:
            if stop is not None:
                stop.set()
        except Exception:
            pass
        try:
            pump = getattr(self, "_pre_pump", None)
            if pump is not None:
                pump.join(timeout=0.5)
        except Exception:
            pass
        self._pre_pump = None
        self._pre_pump_stop = None
        try:
            frames = [bytes(f) for f in (getattr(self, "_pre_roll", None)
                                         or []) if f]
        except Exception:
            frames = []
        self._pre_roll = []
        return mic, frames

    def _drop_pre_roll(self) -> None:
        """Stop a key-DOWN pump mic whose turn never started (never sent)."""
        mic, _frames = self._claim_pre_roll()
        if mic is not None:
            try:
                mic.stop()
            except Exception:
                pass

    def _capture_selection(self) -> bool:
        """Capture the focused window's selection (one service round-trip)."""
        try:
            cap = self.client.capture(self.request_id)
            snap = cap.get("selection", {})
            live = self.desktop.focused_window()
            self.snapshot = SelectionSnapshot(
                available=bool(snap.get("available")),
                captured_via=str(snap.get("captured_via", "")),
                app_class=(live.get("app_class", "") or
                           str(snap.get("app_class", ""))),
                window_id=live.get("window_id", ""))
        except HelperError as exc:
            self._ui_status("error", f"{exc.code}: {exc.detail}")
            self._active = False
            self.state = self.IDLE
            return False
        _trace("listening", selected=bool(self.snapshot.available),
               request_id=self.request_id,
               window_id=self.snapshot.window_id or "")
        self._ui_status("listening",
                        "selected text" if self.snapshot.available
                        else "no selection: dictation")
        return True

    def _start_listening(self) -> None:
        if not self._open_request():
            return
        # Start the microphone + STT FIRST (browser-like: audio flows the
        # moment you press), then capture the selection concurrently.
        self._worker = threading.Thread(target=self._transcribe, daemon=True,
                                         name="voice-edit-stt")
        self._worker.start()
        if not self._capture_selection():
            self._drop_pre_roll()
            self._stop.set()

    def _on_wake_hint(self, detail: str) -> None:
        """Visible hint for an unarmed command-shaped discard (2026-10-03).

        Same status-line surface the §67 heard-preview uses (``_safe_show``
        + ``_ui_status("heard", …)``) — status line only: never beeps,
        never speaks, never emits a command. Never raises.
        """
        try:
            self._safe_show()
            self._ui_status("heard", str(detail or "")[-160:])
        except Exception:
            pass

    def on_wake_command_routed(self, command: str, target: Any) -> None:
        """A wake utterance WITH a resolved route target (OpenClaw port).

        Records the target for the turn (consumed by :meth:`_open_request`,
        which binds the request's session to the target's ``sessionKey``) and
        audits it, then delegates to the existing :meth:`on_wake_command`
        path — behaviour for ``{"mode": "current"}`` / an unresolvable target
        is byte-identical to today (the current session). Never raises.
        """
        try:
            self._wake_route_target = target
            kind, agent_id, session_key = _route_target_fields(target)
            _trace("wake_route", kind=kind, agent_id=agent_id,
                   session_key=session_key)
        except Exception:
            self._wake_route_target = None
        self.on_wake_command(command)

    def on_wake_command(self, command: str) -> None:
        """A hands-free wake utterance arrived (already transcribed).

        Runs the begin/transform path with a key-synthesis-free local focus
        snapshot (never the service selection capture: that emits Ctrl+C
        into the focused window, which is SIGINT in a terminal); only the
        audio source differs from Right-Ctrl (the wake monitor, not the
        hold-to-talk mic).
        The turn itself runs on a worker thread: the whole wake turn (HTTP
        + model + blocking TTS + agent) used to park inside the monitor
        loop's thread, blinding the detector for the entire turn — exactly
        when conversational confirm asks the user to answer.
        """
        cmd = (command or "").strip()
        if not cmd:
            return
        if not self._open_request():
            _trace("wake.turn_refused", state=self.state)
            return
        # The wake utterance is already transcribed by the monitor — the
        # key-DOWN pump mic has no consumer on this path. Drop it (unsent)
        # rather than leaking an open capture.
        self._drop_pre_roll()
        # Claim the turn so a second wake command refuses (via
        # _open_request) instead of overlapping agent turns and TTS.
        self.state = self.PROCESSING
        cmd = verbatim_transcript(cmd, self.cfg)
        if not cmd:
            self._finish_error()
            return
        # The wake path never streamed interim partials, so the heard words
        # jumped straight to the answer with no text preview. Show them now,
        # on the same status surface Right-Ctrl partials use.
        self._safe_show()
        self._ui_status("heard", cmd[-160:])
        worker = threading.Thread(target=self._wake_turn, args=(cmd,),
                                  daemon=True, name="voice-wake-turn")
        self._wake_thread = worker
        worker.start()
        self._arm_wake_turn_watchdog(worker)

    def _wake_turn_timeout_s(self) -> float:
        """Bound for one wake turn (0/disabled → no watchdog). Never raises."""
        try:
            val = float(_read_cfg(self.cfg, "voice_edit", "wake_turn_timeout_s",
                                  default=_WAKE_TURN_TIMEOUT_S)
                        or _WAKE_TURN_TIMEOUT_S)
        except Exception:
            return _WAKE_TURN_TIMEOUT_S
        return val

    def _arm_wake_turn_watchdog(self, worker: Any) -> None:
        """Arm the stuck-turn watchdog for this generation (never raises).

        Fires only while THIS turn still owns the loop (generation match)
        with the worker alive in PROCESSING — a newer turn re-arms its own
        timer and the stale one no-ops on the generation check.
        """
        try:
            timeout = self._wake_turn_timeout_s()
            if timeout <= 0:
                return
            gen = self._gen
            timer = threading.Timer(timeout, self._wake_turn_watchdog,
                                    args=(gen, worker))
            timer.daemon = True
            try:
                worker._watchdog = timer  # owned by this turn only
            except Exception:
                pass
            timer.start()
        except Exception:
            pass

    def _wake_turn_watchdog(self, gen: Any, worker: Any) -> None:
        """Free a wedged wake turn loudly (never raises; generation-guarded).

        A hung HTTP dependency (route/task/ask/speak) parks the worker with
        state PROCESSING and every following turn refuses silently. Past the
        bound this degrades to a typed error + IDLE instead — mirroring the
        P0 ``_finish_error``-on-every-path guards. The stale worker landing
        late afterwards is harmless (same as a slow turn today).
        """
        try:
            alive = bool(getattr(worker, "is_alive", lambda: False)())
        except Exception:
            alive = False
        try:
            if not alive or self._gen != gen or self.state != self.PROCESSING:
                return
            _trace("wake.turn_timeout", state=self.state,
                   request_id=self.request_id)
            self._ui_error("VOICE_EDIT_TURN_TIMEOUT",
                           "voice turn timed out — say it again")
            self._finish_error()
        except Exception:
            pass

    def _capture_selection_local(self) -> bool:
        """Snapshot focus identity WITHOUT touching selection/clipboard/keys.

        Hands-free (wake) path only. The service capture synthesizes Ctrl+C
        into the focused window to read the selection; in any terminal whose
        WM_CLASS is not on the adapter's allowlist (VS Code's integrated
        terminal, gnome-console, JetBrains, ..., or an empty class when
        detection fails) that bare Ctrl+C is SIGINT and kills the foreground
        process (P0 2026-10-03: "hey qubit" killed `sleep` in the owner's
        terminal). A wake turn must therefore NEVER invoke the service
        capture: this builds an explicitly selection-less snapshot from a
        single read-only ``focused_window()`` query — no HTTP, no clipboard
        save/clear/restore, no key synthesis. Push-to-talk keeps the real
        ``_capture_selection`` (explicit key-hold over a selection).
        """
        try:
            live = self.desktop.focused_window()
        except Exception:
            live = {}
        try:
            self.snapshot = SelectionSnapshot(
                available=False,
                captured_via="wake_no_capture",
                app_class=str(live.get("app_class", "") or ""),
                window_id=str(live.get("window_id", "") or ""))
        except Exception:
            self.snapshot = SelectionSnapshot(window_id="")
        _trace("wake.listening", selected=False,
               request_id=self.request_id,
               window_id=self.snapshot.window_id or "")
        return True

    def _wake_turn(self, cmd: str) -> None:
        """Body of one wake turn (worker thread, never the monitor loop).

        Uses the key-synthesis-free local snapshot (see
        ``_capture_selection_local``): the service capture would emit Ctrl+C
        into the focused window. Selection-edit via wake degrades to the
        clarify path; dictate/answer/task/chat (which need no selection)
        are unaffected.
        """
        self._wake_turn_active = True
        try:
            self._capture_selection_local()
            _trace("wake.command", chars=len(cmd),
                    request_id=self.request_id,
                    **(_dbg.text_fields(self.cfg, command=cmd)))
            self._transcript = cmd
            self._talk_emit("transcript.final", chars=len(cmd),
                            source="wake")
            self._process_transcript(cmd)
        except Exception as exc:
            _trace("wake.turn_error", detail=str(exc)[:160])
            self._talk_emit("error", code="VOICE_EDIT_WAKE_TURN_ERROR")
            try:
                self._finish_error()
            except Exception:
                pass
        finally:
            self._wake_turn_active = False
            try:
                timer = getattr(threading.current_thread(), "_watchdog", None)
                if timer is not None:
                    timer.cancel()
            except Exception:
                pass

    def _transcribe(self) -> None:
        _trace("transcribe.start", request_id=self.request_id)
        mode = str(_read_cfg(self.cfg, "voice_edit", "stt_mode",
                             default="accurate") or "accurate").lower()
        accurate = mode == "accurate"
        # Onset handoff (2026-10-04): the key-DOWN pump mic is already
        # running with onset frames buffered; otherwise open the mic here —
        # in both cases BEFORE the ticket mint, so the onset is recorded
        # while the ticket/WS round-trips proceed (measured: ticket-first
        # order lost "Hello" unrecoverably).
        claimed, pre_roll = self._claim_pre_roll()
        if claimed is not None:
            base_mic = claimed  # already NS-wrapped + started; never restart
        else:
            base_mic = self._mic_factory()
            # Honest consumer of ``voice_edit.noise_suppression`` on the
            # Right-Ctrl path: when ON, every captured frame runs through the
            # software NS/AGC stage before either STT consumer (the streaming
            # socket reads filtered frames live; the accurate pass decodes the
            # filtered buffer). OFF → mic untouched. Mirrors the wake path
            # (``voice_edit.wake._maybe_ns_filter``) at frame granularity.
            try:
                from .voice_edit.noise import enabled as _ns_on
                if _ns_on(self.cfg):
                    base_mic = _NsFilterMic(base_mic)
            except Exception:
                pass
            # Talk meter (same tap as the key-DOWN path above): the fallback
            # mic opened here is helper-owned too.
            try:
                base_mic = LevelTapMic(base_mic,
                                       on_frame=self._note_capture_frame)
            except Exception:
                pass
        # In accurate mode the streaming socket still drives live interim
        # partials; the wrapper records every frame so the same audio can be
        # decoded once by faster-whisper after release. PreRoll sits inside
        # Buffering so the accurate rescore also gets the onset.
        from .voice_edit.stt_client import BufferingMic, PreRollMic
        inner = PreRollMic(base_mic, pre_roll=pre_roll) if pre_roll \
            else base_mic
        mic = BufferingMic(inner) if accurate else inner
        if claimed is None:
            # One start, via chain delegation — and BEFORE the ticket mint,
            # so the onset is recorded while the ticket/WS round-trips run.
            try:
                mic.start()
            except SttError as exc:
                _trace("transcribe.mic_failed", code=exc.code)
                self._on_stt_error(exc.code, exc.detail)
                return
            except Exception as exc:  # any capture-tool failure is a mic fault
                _trace("transcribe.mic_failed", code="STT_MIC_UNAVAILABLE")
                self._on_stt_error("STT_MIC_UNAVAILABLE", str(exc)[:160])
                return
        try:
            ticket = self.client.mint_stt_ticket()
        except HelperError as exc:
            if claimed is not None:
                try:
                    base_mic.stop()  # claimed ownership: never leak parec
                except Exception:
                    pass
            _trace("transcribe.ticket_failed", code=exc.code)
            self._on_stt_error(exc.code, exc.detail)
            return
        _trace("transcribe.ticket_ok", ticket_len=len(ticket))
        _trace("transcribe.mic_started", stop_set=self._stop.is_set(),
               stt_mode="accurate" if accurate else "streaming")
        client = self._stt_factory(ticket)
        try:
            final = transcribe_once(
                client, mic, self._stop, on_partial=self._on_partial,
                on_ready=self._ready_cue,
                on_status=lambda s: _trace("transcribe.status", state=s,
                                           stop_set=self._stop.is_set()))
        except Exception as exc:  # never crash the helper on an STT fault
            _trace("transcribe.client_error", detail=str(exc)[:120])
            final = ""
        finally:
            try:
                mic.stop()
            except Exception:
                pass
        stream_error = getattr(client, "error", None)
        # Accurate mode: decode the buffered utterance once with the local
        # faster-whisper provider. The streaming final is only the fallback,
        # and the fallback reason is always surfaced (never silent).
        if accurate:
            pcm = getattr(mic, "pcm", b"") if hasattr(mic, "pcm") else b""
            if pcm:
                from .voice_edit.stt_client import transcribe_accurate
                acc_text, reason = transcribe_accurate(self.cfg, pcm)
                if acc_text:
                    final = acc_text
                    self._final_engine = "accurate"
                    _trace("transcribe.accurate", chars=len(acc_text))
                else:
                    self._final_engine = "streaming"
                    self._stt_fallback_reason = reason or "accurate empty"
                    _trace("transcribe.accurate_fallback",
                           reason=self._stt_fallback_reason)
            else:
                self._final_engine = "streaming"
                self._stt_fallback_reason = "no buffered audio"
                _trace("transcribe.accurate_fallback",
                       reason=self._stt_fallback_reason)
        else:
            self._final_engine = "streaming"
        _trace("transcribe.done", final_chars=len((final or "").strip()),
               engine=getattr(client, "engine_reported", ""),
               final_engine=self._final_engine,
               fallback=self._stt_fallback_reason,
               error=bool(stream_error))
        if stream_error and not (final or "").strip():
            self._on_stt_error(stream_error.code, stream_error.detail)
            return
        self._transcript = verbatim_transcript((final or "").strip(),
                                                   self.cfg)
        _trace("transcript", request_id=self.request_id,
               chars=len(self._transcript),
               **(_dbg.text_fields(self.cfg, text=self._transcript)))
        if not self._transcript:
            self._ui_status("error", self._stt_fallback_reason
                            or "no speech recognised")
            self._talk_emit("error", code="VOICE_EDIT_NO_TRANSCRIPT")
            self._finish_error()
            return
        self._talk_emit("transcript.final", chars=len(self._transcript))
        self._process_transcript()

    def _ready_cue(self) -> None:
        """The ASR session is open: cue the user that they can speak now."""
        _trace("stt.ready", request_id=self.request_id)
        beep(self.cfg, "ready")
        self._ui_status("listening", "speak now")
        self._notify_status("Qubit", "Listening — speak now")

    def _on_partial(self, text: str) -> None:
        self._interim = text
        self._ui_status("listening", text[-160:])

    def _on_stt_error(self, code: str, detail: str) -> None:
        self._ui_error(code, detail)
        self._talk_emit("error", code=code)
        self._finish_error()

    def _finish_error(self) -> None:
        self._talk_emit("response.done")
        self._talk_end()
        self._active = False
        self.state = self.IDLE
        # Continuous ("keep listening") mode: a completed turn may keep the
        # wake listener open for a bounded run of follow-up commands. A
        # no-op unless ``voice_edit.continuous_mode`` is on.
        self._maybe_arm_continuous()

    def _maybe_arm_continuous(self) -> None:
        """Keep listening for a bounded run of follow-up commands.

        Called when a turn completes. Only when
        ``voice_edit.continuous_mode`` is on. Deliberately:
        * never arms over a live confirmation — the confirm prompt's own
          ``_await_reply`` arm plus the wake loop's pending guard own the
          next utterance, so a pending still only accepts yes/no;
        * keeps a run's remaining budget (a fresh turn opens a new run, a
          follow-up extends the current one, and an exhausted run stays
          locked until a fresh wake);
        * is gated by the wake loop's ``is_busy`` probe (which is
          ``DesktopHelper._wake_busy``: capturing/processing/speaking/echo),
          so a follow-up can never overlap Qubit working or talking.
        Never raises; a missing/dead wake listener is simply a no-op.
        """
        try:
            on, window, maxf = continuous_settings(self.cfg)
        except Exception:
            return
        if not on:
            return
        wake = getattr(self, "_wake", None)
        if wake is None:
            return
        try:
            if self._wake_has_live_pending():
                return
        except Exception:
            pass
        try:
            if bool(getattr(wake, "continuous_locked",
                            lambda: False)()):
                return
        except Exception:
            return
        try:
            if bool(getattr(wake, "continuous_active", lambda: False)()):
                rearm = getattr(wake, "rearm_continuous", None)
                if callable(rearm):
                    rearm()
                    _trace("continuous.rearmed", window_s=window)
                return
        except Exception:
            pass
        fn = getattr(wake, "expect_continuous", None)
        if callable(fn):
            try:
                fn(window, maxf)
                _trace("continuous.armed", window_s=window, max=maxf)
            except Exception:
                pass

    # -- session-scoped confirmation state ---------------------------------
    def _current_session_key(self) -> str:
        """The session a turn belongs to: the routed session, else the voice
        session. Never raises (junk degrades to the stable default key).
        """
        try:
            key = str(getattr(self, "_turn_session", "") or "")
        except Exception:
            key = ""
        if key:
            return key
        try:
            routed = _route_session_key(getattr(self, "_wake_route_target",
                                                None))
        except Exception:
            routed = ""
        return routed or _DEFAULT_VOICE_SESSION

    def _session_fsm(self) -> Any:
        """The :class:`SessionFSM` for the current session (lazily created).

        Lazy on first use so a helper built via ``__new__`` (test doubles /
        restored instances) still has a working registry. Never raises out:
        an unusable registry yields a throwaway FSM, so callers degrade to
        the no-pending path instead of taking the turn down.
        """
        reg = getattr(self, "_sessions", None)
        if not isinstance(reg, SessionRegistry):
            reg = SessionRegistry(ttl_s=_PENDING_TASK_TTL_S)
            try:
                self._sessions = reg
            except Exception:
                pass
        try:
            return reg.fsm(self._current_session_key())
        except Exception:
            return SessionFSM(_DEFAULT_VOICE_SESSION, ttl_s=_PENDING_TASK_TTL_S)

    @property
    def _pending_task(self) -> Dict[str, Any]:
        """Compat view of the current session's staged action (legacy tests).

        The authority is the session FSM; this dict-shaped view exists only
        so existing readers/tests keep working. Write through it to stage or
        clear the current session's pending (empty dict clears).
        """
        try:
            action = self._session_fsm().pending()
        except Exception:
            return {}
        if action is None:
            return {}
        return {"text": action.text, "request_id": action.request_id,
                "staged_at": action.staged_at, "ttl_s": action.ttl_s,
                "kind": action.kind, "session": action.session}

    @_pending_task.setter
    def _pending_task(self, value: Any) -> None:
        try:
            fsm = self._session_fsm()
        except Exception:
            return
        if not value:
            try:
                fsm.clear()
                fsm.state = "idle"
            except Exception:
                pass
            return
        try:
            text = str(value.get("text", "") or "")
            request_id = str(value.get("request_id", "") or "")
            raw_ts = value.get("staged_at", None)
            kind = str(value.get("kind", "task") or "task")
        except Exception:
            return
        # A missing/invalid stage time reads as already expired (matches the
        # legacy malformed-probe contract) rather than a fresh confirmation.
        if raw_ts is None:
            staged_at = float("-inf")
        else:
            try:
                staged_at = float(raw_ts)
            except (TypeError, ValueError):
                staged_at = float("-inf")
        try:
            ttl_s = float(value.get("ttl_s", fsm.ttl_s))
        except (TypeError, ValueError):
            ttl_s = fsm.ttl_s
        try:
            fsm._pending = PendingAction(
                text=text, request_id=request_id, session=fsm.session,
                staged_at=staged_at, ttl_s=ttl_s, kind=kind)
            fsm.state = "awaiting_confirm"
        except Exception:
            pass

    def _wake_has_live_pending(self) -> bool:
        """Read-only liveness of the staged confirm-gated task (Lane 1).

        Delegates to the session FSM without consuming anything: True only
        while an action is staged for the current session and unexpired
        (``_PENDING_TASK_TTL_S`` on the FSM's monotonic clock). Never raises —
        an unreadable state degrades to False (no settle).
        """
        try:
            fsm = self._session_fsm()
            if fsm.pending() is None:
                return False
            return not fsm.expired()
        except Exception:
            return False

    def _process_transcript(self, transcript: str = "") -> None:
        # New reply turn: bump the epoch. A stop marker from an earlier turn
        # is scoped to that epoch, so an idle stop can never silence this
        # turn's reply; the per-turn stop announcement also resets.
        self._turn_seq += 1
        self._turn_stopped_seq = -1
        self._turn_stop_announced = False
        if self.snapshot is None and self.request_id:
            # Ensure the selection snapshot exists even if capture lagged.
            self._capture_selection()
        text = (transcript or self._transcript or "").strip()
        # Fired-timer pull (Lane 2, 2026-10-03): the ledger firer never
        # touches chat/session state, so timers surface here — every voice
        # turn announces what fired while away (sleep: panel/History only).
        self._deliver_fired_timers()
        # A staged task (mode "task") is confirm-gated, and the confirmation
        # is scoped to its session: unrelated input — including a command on
        # the wake path — must NOT silently discard or overwrite it (the
        # historical single-dict bug that ran the wrong action on a later
        # "yes"). The session FSM resolves the utterance; only affirm / deny /
        # expiry consume the staged action.
        fsm = self._session_fsm()
        if fsm.pending() is not None:
            outcome, action = fsm.resolve(text)
            if outcome == "run":
                self._run_task(str(getattr(action, "text", "") or ""))
                return
            if outcome == "cancel":
                self._ui_status("idle", "task cancelled")
                self._finish_error()
                return
            if outcome == "expired":
                # P0-3: confirmations bind intent to recency — a "yes" hours
                # later in an unrelated context must never execute a stale
                # destructive-tier task.
                _trace("task.expired", request_id=self.request_id,
                       session=fsm.session)
                self._ui_status("idle", "confirmation expired — say it again")
                self._finish_error()
                return
            # Unrecognised input: keep the staged action and do NOT route or
            # overwrite it (the core fix). The wake path traces the guard so
            # the journal shows why a spoken command was held back.
            if getattr(self, "_wake_turn_active", False):
                _trace("wake.pending_guard", request_id=self.request_id,
                       chars=len(text), session=fsm.session)
            self._ui_status(
                "clarify",
                "you have a pending confirmation — say yes, no, or cancel")
            self._notify_status(
                "Qubit", "Pending confirmation — say yes, no, or cancel")
            self._speak("You have a pending confirmation. "
                        "Say yes to run it, no to cancel.")
            self._await_reply("say yes, no, or cancel")
            self._finish_error()
            return
        self.state = self.PROCESSING
        self._ui_status("processing", text[-160:])
        route_fn = getattr(self.client, "route", None)
        try:
            if callable(route_fn):
                out = route_fn(self.request_id, text)
            else:  # compatibility seam (older/fake clients)
                out = self.client.transform(self.request_id, text)
        except HelperError as exc:
            self._ui_error(exc.code, exc.detail)
            self._talk_emit("error", code=exc.code)
            self._finish_error()
            return
        mode = out.get("mode")
        if mode == "dictate":
            # Explicit "type this ..." command: type the payload verbatim.
            self._insert_dictation(str(out.get("transcript", "")))
        elif mode in ("answer", "chat"):
            # A question or plain conversation: answer it via the brain and
            # show the reply -- never type the utterance into the field.
            self._answer_question(str(out.get("question", "")), mode=str(mode))
            self.state = self.PREVIEW
            self._preview = out
        elif mode == "task":
            self._propose_task(out)
        elif mode == "clarify":
            self._ui_status("clarify", str(out.get("question", "")))
            self._notify_status("Qubit", str(out.get("question", "")))
            self._speak(str(out.get("question", "")))
            self._await_reply(str(out.get("question", "")) or
                              "answer the question aloud")
            # A clarification asks the user to speak again: return to IDLE so
            # the next activation starts a fresh session (staying in PROCESSING
            # swallowed every subsequent PTT).
            self._finish_error()
        elif mode == "ignore":
            # e.g. the speaker only said the wake phrase: type nothing.
            self._ui_status("idle", "no command heard")
            self._finish_error()
        elif mode == "preview":
            self.state = self.PREVIEW
            self._preview = out
            self._ui_preview(out)
            self._history("edit", text,
                          str((out.get("detected") or {}).get("action", "")))
        else:
            self._ui_error(str(out.get("code", "VOICE_EDIT_ERROR")),
                           str(out.get("detail", "")))
            self._finish_error()

    @staticmethod
    def _affirmative(text: str) -> bool:
        # Whisper emits conversational punctuation ("Yes, please.") while
        # verbatim mode strips it — match on the alphanumeric core either
        # way so a politely-phrased confirmation never silently discards a
        # staged task. Delegates to the session FSM's classifier (the same
        # word lists) so there is one confirmation vocabulary.
        return classify_confirmation(text) == "affirm"

    @staticmethod
    def _negative(text: str) -> bool:
        return classify_confirmation(text) == "deny"

    def _await_reply(self, what: str = "reply") -> None:
        """Keep the mic waiting for the user's spoken answer.

        Called whenever the helper asks something aloud (a "say yes"
        confirmation, a clarification question): re-arms the wake listener
        (whose window started at the original wake and expired during
        brain/TTS latency) so a bare answer routes without needing
        "hey qubit" again. Never raises; a missing/dead wake listener
        simply leaves Right-Ctrl as the answer path.
        """
        try:
            wake = getattr(self, "_wake", None)
            if wake is not None and hasattr(wake, "expect_reply"):
                wake.expect_reply()
        except Exception:
            pass
        try:
            self._ui_status("listening", what)
        except Exception:
            pass

    def _propose_task(self, out: Dict[str, Any]) -> None:
        """Stage a confirm-gated task and ask for confirmation (never runs it)."""
        task = str(out.get("task_text") or out.get("summary") or "").strip()
        if not task:
            self._ui_status("error", "nothing to run")
            self._finish_error()
            return
        fsm = self._session_fsm()
        fsm.propose(task, self.request_id)
        _trace("task.proposed", chars=len(task), request_id=self.request_id,
               session=fsm.session,
               **(_dbg.text_fields(self.cfg, task=task)))
        self.state = self.PREVIEW
        self._preview = {
            "ok": True, "mode": "task", "original": task, "proposed": task,
            "detected": {"action": "task", "style": "", "language": "",
                         "audience": ""},
            "model": "", "provider": "", "privacy_mode": "local",
            "injection_suspected": False, "facts_dropped": [], "elapsed_ms": 0,
            "target_app": ""}
        self._ui_preview(self._preview)
        self._ui_status("clarify", "confirm to run the task")
        self._notify_status("Qubit", f"Confirm task: {task[:200]}")
        self._speak(f"{task[:200]}. Say yes to run it, or no to cancel.")
        self._await_reply("say yes to run, no to cancel")

    def _run_task(self, text: str) -> None:
        """Run a confirmed task through the one agent turn path (never typed)."""
        if not text:
            self._finish_error()
            return
        self.state = self.PROCESSING
        self._ui_status("processing", "running task…")
        self._talk_emit("response.started", mode="task")
        self._talk_emit("tool.call", name="task")
        try:
            out = self.client.task(text, confirm=True,
                                   request_id=self.request_id)
        except HelperError as exc:
            self._ui_error(exc.code, exc.detail)
            self._notify_status("Qubit", f"{exc.code}: {exc.detail}")
            self._talk_emit("error", code=exc.code)
            self._finish_error()
            return
        reply = str(out.get("reply", "") or "")
        self._talk_emit("tool.result", ok=bool(out.get("ok")))
        _trace("task.result", ok=bool(out.get("ok")),
               reply_chars=len(reply),
               **(_dbg.text_fields(self.cfg, task=text, reply=reply)))
        if out.get("ok") and reply:
            self.state = self.PREVIEW
            self._preview = {
                "ok": True, "mode": "task_result", "original": text,
                "proposed": reply,
                "detected": {"action": "task", "style": "", "language": "",
                             "audience": ""},
                "model": str(out.get("model", "") or ""),
                "provider": str(out.get("provider", "") or ""),
                "privacy_mode": "local", "injection_suspected": False,
                "facts_dropped": [], "elapsed_ms": 0, "target_app": ""}
            self._ui_preview(self._preview)
            self._notify_status("Qubit", reply[:200])
            self._history("task", text, reply)
            self._speak(reply)
            # The task ran to completion: keep listening for a follow-up.
            self._maybe_arm_continuous()
            return
        self._ui_error(str(out.get("code", "VOICE_EDIT_TASK_EMPTY")),
                       str(out.get("detail", "")))
        self._notify_status("Qubit", str(out.get("detail", "") or "task failed"))
        self._finish_error()

    def _answer_question(self, question: str, *, mode: str = "answer") -> None:
        """Ask the Qubit brain, display the answer, and speak it aloud."""
        # Slice 1 talk-back: an "in chat:" turn runs the headless worker and
        # can take many seconds, so acknowledge immediately (status + voice)
        # instead of silent waiting. The full reply is spoken at the end
        # (gist) with the complete text in History.
        try:
            _rw = parse_route_word(question)
        except Exception:
            _rw = None
        if _rw is not None and _rw["route"] == "in-chat":
            self._ui_status("working", "working in /chat…")
            self._speak("On it — working on that in chat.")
        self._ui_status("processing", "answering…")
        self._talk_emit("response.started", mode=mode)
        spoken = False
        # Owner ask (2026-10-04 voice-final lane): a redo/rewrite answer
        # carries clipboard fields (service copied the output write-only —
        # safe on the selection-less wake path, no capture/keys/replace).
        # The short receipt is spoken AFTER the reply so a long rewrite
        # never swallows it in the 2-sentence excerpt. Absent fields (every
        # non-transform answer) mean silence — no behavior change there.
        clip_ok: Optional[bool] = None
        try:
            if hasattr(self.client, "ask_result"):
                out = self.client.ask_result(question, self.request_id)
                # A STOP that cancelled the turn at the service returns this
                # typed code (route 400): close the orb FSM with the canonical
                # stop triple instead of an error — the turn was stopped, not
                # broken. The service already aborted the model/agent call and
                # returned no reply to speak.
                if str(out.get("code") or "") == "VOICE_EDIT_ASK_CANCELLED":
                    self._announce_turn_stop()
                    self.state = self.PREVIEW
                    return
                reply = str(out.get("answer", "") or "")
                # Lane 3 (2026-10-03): the service stages tool-shaped misses
                # that reached ask() as conversation (timer-class) instead of
                # letting the brain narrate an unsettleable "Confirm?". A
                # task-shaped ask result stages through the existing confirm
                # path — spoken prompt + wake re-arm — so voice "yes" settles
                # it like any routed task.
                if (not reply and out.get("mode") == "task"
                        and out.get("requires_confirmation")
                        and str(out.get("task_text") or "").strip()):
                    self._propose_task(out)
                    return
                # The service streams the speech itself; don't double-play.
                spoken = bool(out.get("spoken"))
                if "clipboard_ok" in out:
                    clip_ok = bool(out.get("clipboard_ok"))
            else:  # compatibility seam (older/fake clients)
                reply = self.client.ask(question, self.request_id)
        except HelperError as exc:
            if exc.code == "VOICE_EDIT_ASK_CANCELLED":
                # Orb STOP cancelled this turn at the service (the POST came
                # back 400): the stop marker is already recorded, so emit the
                # canonical stop triple once and end cleanly.
                self._announce_turn_stop()
                self.state = self.PREVIEW
                return
            self._ui_error(exc.code, exc.detail)
            self._talk_emit("error", code=exc.code)
            self._finish_error()
            return
        if not reply:
            self._ui_status("error", "no answer")
            self._finish_error()
            return
        _trace("answer", mode=mode, chars=len(reply),
               **(_dbg.text_fields(self.cfg, answer=reply)))
        self._ui_preview({
            "ok": True, "original": question, "proposed": reply,
            "mode": mode,
            "detected": {"action": mode, "style": "", "language": "",
                         "audience": ""},
            "model": "qubit-brain4", "provider": "local",
            "privacy_mode": "local", "injection_suspected": False,
            "facts_dropped": [], "elapsed_ms": 0,
            "target_app": (self.snapshot.app_class if self.snapshot else "")})
        # Voice conversation: the service streams the speech; only fall back
        # to helper-side playback when it did not.
        self._history(mode, question, reply)
        if not spoken:
            self._speak(reply)
        if clip_ok is True:
            self._speak("Copied to clipboard.")
        elif clip_ok is False:
            self._speak("Clipboard copy failed.")
        # The answer was delivered: keep listening for a follow-up.
        self._maybe_arm_continuous()

    @staticmethod
    def _spoken_excerpt(text: str, sentences: int = 2) -> str:
        """First ``sentences`` sentences only — TTS time scales with length, so
        speak the gist and keep the full text in the panel/history."""
        import re
        t = " ".join(str(text or "").split())
        if not t:
            return ""
        parts = re.split(r"(?<=[.!?])\s+", t)
        return " ".join(parts[:max(1, sentences)]).strip()

    def _barge_in_settings(self) -> "tuple[bool, float, int]":
        """Barge-in tuning from config (never raises; echo-safe defaults).

        ``voice_edit.barge_in`` (default True) enables stopping TTS on
        confident room speech; ``barge_in_threshold`` is the RMS gate
        (default 0.10 — calibrated 2026-10-03: 60 s drone baseline max
        0.073, speaker echo max 0.097, owner speech peaks 0.5 max-abs
        i.e. ~0.13+ RMS even straddled across 300 ms frames; the old
        0.20 sat above every owner word at seating distance so the
        first "stop" died silently); ``barge_in_ms`` is the sustain
        window, clamped to 150-300 ms. Direct YAML keys only (no
        settings-view change — the other lane owns that surface).
        """
        try:
            enabled = _read_cfg(self.cfg, "voice_edit", "barge_in",
                                default=True)
            enabled = True if enabled is None else bool(enabled)
        except Exception:
            enabled = True
        try:
            threshold = float(_read_cfg(self.cfg, "voice_edit",
                                        "barge_in_threshold",
                                        default=0.10) or 0.10)
        except Exception:
            threshold = 0.10
        try:
            min_ms = int(_read_cfg(self.cfg, "voice_edit", "barge_in_ms",
                                   default=200) or 200)
        except Exception:
            min_ms = 200
        return enabled, threshold, max(150, min(300, min_ms))

    def _wait_playback(self, proc: Any) -> bool:
        """Wait out ``proc``; True when the explicit stop flag ended it.

        Waits in short slices so an orb STOP (``stop_speaking`` / SIGUSR2)
        terminates playback promptly even when barge-in is disabled or no
        mic could be opened. Never raises; a dead process simply reads as
        finished.
        """
        stop = getattr(self, "_speak_stop", None)
        if stop is None:
            try:
                proc.wait(timeout=90)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            return False
        while True:
            try:
                if stop.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    return True
            except Exception:
                pass
            try:
                # Both waits and returns when the process ends; a running
                # process raises TimeoutExpired, so we poll the stop flag.
                proc.wait(timeout=0.05)
                return False
            except Exception:
                try:
                    if proc.poll() is not None:
                        return False
                except Exception:
                    return False

    def _play_with_barge_in(self, proc: Any) -> bool:
        """Block until ``proc`` ends; stop it on confident speech or STOP.

        Returns True when playback was interrupted (confident room speech
        or an explicit orb STOP). Disabled (or an unopenable mic) degrades
        to a plain wait — speech never breaks because detection cannot run.
        The mic stream is short-lived (this playback only) and never
        touches the wake monitor's mic.
        """
        stop = getattr(self, "_speak_stop", None)
        if stop is not None:
            try:
                if stop.is_set():
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    return True
            except Exception:
                pass
        enabled, threshold, min_ms = self._barge_in_settings()
        if not enabled or not threshold > 0:
            return self._wait_playback(proc)
        try:
            mic = self._mic_factory()
        except Exception:
            mic = None
        if mic is None:
            return self._wait_playback(proc)
        watcher = BargeInWatcher(threshold=threshold, min_ms=min_ms)
        try:
            mic.start()
        except Exception:
            return self._wait_playback(proc)
        interrupted = False
        try:
            frame_ms = int(getattr(mic, "frame_ms", 300) or 300)
        except Exception:
            frame_ms = 300
        try:
            while proc.poll() is None:
                if stop is not None:
                    try:
                        if stop.is_set():
                            try:
                                proc.terminate()
                            except Exception:
                                pass
                            interrupted = True
                            break
                    except Exception:
                        pass
                try:
                    frame = mic.read_frame(timeout=0.5)
                except Exception:
                    frame = None
                if not frame:
                    continue
                # One real frame = one nominal frame duration of audio: the
                # mic delivers at its own cadence, so counting frames maps to
                # real confident-speech time without wall-clock flakiness.
                if watcher.feed(frame_rms(frame), frame_ms):
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    interrupted = True
                    _trace("speak.barge_in", threshold=threshold,
                           min_ms=min_ms)
                    break
        finally:
            try:
                mic.stop()
            except Exception:
                pass
        if stop is not None:
            try:
                if stop.is_set():
                    interrupted = True
            except Exception:
                pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return interrupted

    def _turn_reply_stopped(self) -> bool:
        """True when an orb STOP marked the CURRENT turn before playback.

        Scoped by epoch: a marker only counts while the turn that recorded
        it is still current, so a new turn (new epoch) always speaks.
        Never raises.
        """
        try:
            return (self._turn_seq > 0
                    and self._turn_stopped_seq == self._turn_seq)
        except Exception:
            return False

    def _announce_turn_stop(self) -> None:
        """Emit the canonical stop triple once for a turn stopped pre-TTS.

        Mirrors what the player-owning ``_speak`` emits on an explicit stop,
        so the orb FSM ends from the same typed boundaries. Idempotent per
        turn; never raises.
        """
        if getattr(self, "_turn_stop_announced", False):
            return
        self._turn_stop_announced = True
        self._barge_interrupted = True
        _trace("speak.stop_pending_honored",
               turn=getattr(self, "_turn_seq", 0))
        try:
            self._talk_emit("interrupt")
            self._talk_emit("playback.stopped")
            self._talk_emit("response.done")
        except Exception:
            pass

    def _speak(self, text: str) -> None:
        """Speak the gist of ``text`` through the service's TTS, locally.

        Best-effort and non-fatal: a TTS/playback failure never changes the
        answer that was already shown. Disable with ``voice_edit.speak_answers``.
        Confident room speech stops playback mid-reply (barge-in); silence
        and echo-level audio play through. Yields to idle speech afterwards.
        The spoken excerpt runs through ``speak_brand_text`` (legacy
        self-reference tokens -> Qubit-neutral wording); preview/History
        keep the verbatim text for audit honesty.
        """
        if not text or not bool(_read_cfg(self.cfg, "voice_edit",
                                          "speak_answers", default=True)):
            return
        import os
        import shutil
        import subprocess
        import tempfile
        # A STOP that landed while this turn was THINKING/WORKING (before any
        # player existed) is honored here: never synthesize or speak the
        # pending reply, and close the orb FSM with the canonical stop triple.
        if self._turn_reply_stopped():
            self._announce_turn_stop()
            return
        # Fresh reply: a STOP flag from a previous turn must never cut this
        # one. Cleared before synth so an orb STOP arriving mid-synthesis is
        # still honored when playback starts.
        try:
            self._speak_stop.clear()
        except Exception:
            pass
        spoken = self._spoken_excerpt(text)
        if not spoken:
            return
        spoken = speak_brand_text(spoken)
        if not spoken:
            return
        try:
            wav = self.client.speak(spoken[:400])
        except HelperError as exc:
            _trace("speak_error", code=exc.code)
            return
        if not wav:
            return
        # A STOP that arrived DURING synthesis (still no player) must not
        # start playback either; emit the stop triple and drop the wav.
        if self._turn_reply_stopped() or self._speak_stop.is_set():
            self._announce_turn_stop()
            return
        # Real-equalizer TTS timeline: FFT the reply wav ONCE at synth time
        # (ts = synth-complete ≈ playback start, ms apart). Published even
        # when no local player exists — the consumer reads it against the
        # journaled speak window, not against this process.
        try:
            publish_spec_tts(wav)
        except Exception:
            pass
        player = "paplay" if shutil.which("paplay") else (
            "aplay" if shutil.which("aplay") else "")
        if not player:
            return
        path = ""
        self._speaking = True
        self._barge_interrupted = False
        self._talk_emit("response.audio.delta", chars=len(spoken))
        self._talk_emit("playback.started", chars=len(spoken))
        try:
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="qubit-answer-")
            with os.fdopen(fd, "wb") as fh:
                fh.write(wav)
            _trace("speak", chars=len(text))
            proc = subprocess.Popen(
                [player, path], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            self._playback_proc = proc
            if self._play_with_barge_in(proc):
                self._barge_interrupted = True
                self._talk_emit("interrupt")
                self._ui_status("interrupted", "stopped — speak again")
        except Exception as exc:  # never fatal
            _trace("speak_failed", detail=str(exc)[:120])
        finally:
            self._playback_proc = None
            self._speaking = False
            # Playback finished: a done marker so the orb's green ends the
            # moment Qubit stops talking instead of lingering for the whole
            # recency window (live 2026-10-05).
            _trace("speak_done", chars=len(text))
            self._talk_emit("playback.stopped", chars=len(text))
            self._talk_emit("response.done")
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    #: Leading cue separators stripped at the insert boundary (journal
    #: 2026-10-04: "Type this for me. 1 plus 1 equals 99." arrived as
    #: ". 1 plus 1 equals 99." — the exact-cue strip in the parser leaves the
    #: sentence period, the loose-cue strip removes it; the boundary strips
    #: it either way so both PTT and wake dictate type clean text). The set
    #: mirrors the loose-cue lstrip (whitespace + sentence punctuation
    #: only): parens/quotes/letters/digits are never touched.
    _DICTATE_LEAD_STRIP = " \t,.:;!?-\u2013\u2014"

    def _insert_dictation(self, text: str) -> None:
        cleaned = str(text or "").lstrip(self._DICTATE_LEAD_STRIP)
        if not cleaned.strip():
            self._finish_error()
            return
        text = cleaned
        snap = self.snapshot or SelectionSnapshot(window_id="")
        # Refuse to inject into a fullscreen app (a game/video): text there is
        # almost always unintended. Offer the clipboard instead.
        if bool(_read_cfg(self.cfg, "voice_edit", "block_fullscreen",
                          default=True)) and \
                self.desktop.is_fullscreen(snap.window_id):
            self.desktop.set_clipboard(text)
            _trace("insert.blocked_fullscreen", chars=len(text))
            self._ui_status("error",
                            "fullscreen window: text copied to clipboard")
            self._history("dictate", self._transcript,
                          "(blocked: fullscreen — copied to clipboard)")
            self._finish_error()
            return
        if self.snapshot is not None and self.snapshot.window_id:
            # The overlay may hold focus (it shows status cues); hide it and
            # refocus the target first (parity with the apply path), so the
            # focus gate below sees the real app and not our own window.
            try:
                self.overlay.hide()
            except Exception:
                pass
            self.desktop.activate_window(self.snapshot.window_id)
        res = self.desktop.insert_text(text, snap, self.cfg)
        _trace("insert", ok=res.ok, method=res.method,
               app_class=res.app_class, chars=len(text),
               fullscreen=self.desktop.is_fullscreen(snap.window_id),
               **(_dbg.text_fields(self.cfg, inserted=text)))
        if res.ok:
            self._ui_status("done", "dictation inserted")
            self._history("dictate", self._transcript, text)
        else:
            # Truthful fallback: put the text on the clipboard.
            self.desktop.set_clipboard(text)
            self._ui_status(
                "error", f"{res.code}: text copied to clipboard instead")
            self._history("dictate", self._transcript,
                          f"(failed {res.code}: copied to clipboard)")
        self._finish_error()

    # -- preview actions --------------------------------------------------
    def on_overlay_action(self, action: str, payload: Dict[str, Any]) -> None:
        rid = self.request_id
        if action == "cancel":
            self.cancel()
            return
        if action == "copy":
            try:
                out = self.client._request("GET", f"/api/voice-edit/{rid}/result")
            except HelperError as exc:
                self.overlay.show_error(exc.code, exc.detail)
                return
            self.desktop.set_clipboard(str(out.get("proposed", "")))
            self.overlay.set_status("done", "result copied")
            return
        if action == "edit":
            entry = getattr(self.overlay, "_edit_entry", None)
            if entry is not None:
                entry.pack(fill="x", pady=(8, 0))
                entry.delete("1.0", "end")
                entry.insert("1.0", self._preview.get("proposed", ""))
            return
        if action == "edit_submit":
            try:
                out = self.client.edit(rid, str(payload.get("text", "")))
                merged = dict(self._preview)
                merged.update(out)
                self._preview = merged
                self.overlay.show_preview(merged)
            except HelperError as exc:
                self.overlay.show_error(exc.code, exc.detail)
            return
        if action == "retry":
            try:
                out = self.client.retry(rid, self._transcript)
                if out.get("mode") == "preview":
                    self.state = self.PREVIEW
                    self._preview = out
                    self.overlay.show_preview(out)
                else:
                    self.overlay.show_error(str(out.get("code", "")),
                                            str(out.get("detail", "")))
            except HelperError as exc:
                self.overlay.show_error(exc.code, exc.detail)
            return
        if action == "apply":
            # A staged task is confirmed by the same button: run it through the
            # one agent turn path (never typed), instead of the selection apply.
            if self._preview.get("mode") == "task":
                task = str(self._pending_task.get("text", "")
                           or self._preview.get("original", "") or "")
                self._pending_task = {}
                self._run_task(task)
                return
            # Hide the overlay and refocus the target so the service's
            # stale-selection re-check and paste hit the real app, not us.
            try:
                self.overlay.hide()
            except Exception:
                pass
            if self.snapshot is not None and self.snapshot.window_id:
                self.desktop.activate_window(self.snapshot.window_id)
            try:
                out = self.client.apply(rid, confirm=True)
            except HelperError as exc:
                self._safe_show()
                self.overlay.show_error(exc.code, exc.detail)
                return
            self._safe_show()
            if out.get("ok"):
                self.overlay.set_status("done", "applied")
            else:
                self.overlay.show_error(str(out.get("code", "")),
                                        str(out.get("detail", "")))
            return
        if action == "revert":
            # Undo happens in the target app; refocus it so the focus check
            # passes (parity with apply).
            try:
                self.overlay.hide()
            except Exception:
                pass
            if self.snapshot is not None and self.snapshot.window_id:
                self.desktop.activate_window(self.snapshot.window_id)
            try:
                out = self.client.revert(rid)
            except HelperError as exc:
                self._safe_show()
                self.overlay.show_error(exc.code, exc.detail)
                return
            self._safe_show()
            if out.get("ok"):
                self.overlay.set_status("done", "reverted")
            else:
                self.overlay.show_error(str(out.get("code", "")),
                                        str(out.get("detail", "")))
            return

    def _safe_show(self) -> None:
        try:
            self.overlay.show()
        except Exception:
            pass

    def cancel(self) -> None:
        self._stop.set()
        self._drop_pre_roll()
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=3.0)
        if self.request_id:
            try:
                self.client.cancel(self.request_id)
            except Exception:
                pass
        self._pending_task = {}
        self._active = False
        self.state = self.IDLE
        self._talk_emit("response.done")
        self._talk_end()
        self._ui_status("idle", "cancelled")

    # -- helpers ----------------------------------------------------------
    def _log_status(self, state: str, detail: str) -> None:
        self.overlay.set_status(state, detail)

    def _notify_status(self, title: str, body: str) -> None:
        if self._notify:
            try:
                self._notify(title, body)
            except Exception:
                pass


def _notify_send(title: str, body: str) -> None:
    import shutil
    import subprocess
    if not shutil.which("notify-send"):
        return
    try:
        subprocess.run(["notify-send", "-a", "Qubit", title, body],
                       timeout=3, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def check(cfg: Any) -> Dict[str, Any]:
    base = resolve_service_base(cfg)
    desktop = DesktopAdapter()
    client = HelperClient(base, resolve_token(cfg))
    out: Dict[str, Any] = {
        "enabled": bool(_read_cfg(cfg, "voice_edit", "enabled", default=False)),
        "service_url": base,
        "session": desktop.session(),
        "tools": {"xdotool": bool(desktop._tool("xdotool")),
                  "xclip": bool(desktop._tool("xclip")),
                  "parec": bool(ParecMic().available())},
        "hotkey_mode": str(_read_cfg(cfg, "voice_edit", "hotkey_mode",
                                     default="hold") or "hold"),
    }
    try:
        out["service"] = client.status()
    except HelperError as exc:
        out["service"] = {"ok": False, "code": exc.code, "detail": exc.detail}
    # Warm readiness: prefer the live helper's own heartbeat; otherwise infer
    # cheaply from the mode + whether the accurate decoder is importable.
    out["stt_warm"] = _infer_stt_warm(cfg)
    return out


def _infer_stt_warm(cfg: Any) -> str:
    try:
        from .voice_edit import heartbeat as _hb
        live = _hb.read() or {}
        warm = str(live.get("stt_warm", "") or "")
        if warm:
            return warm
    except Exception:
        pass
    mode = str(_read_cfg(cfg, "voice_edit", "stt_mode",
                         default="accurate") or "accurate").lower()
    if mode != "accurate":
        return "skipped"
    try:
        from .voice_edit import asr_accurate
        return "pending" if asr_accurate.is_available(cfg) else "unavailable"
    except Exception:
        return "unavailable"


def build_cfg() -> Any:
    from .config import Config
    return Config.load()


def take_over_previous_helper(*, kill: Any = None, sleep: Any = None,
                              monotonic: Any = None,
                              exclude: Optional[int] = None) -> Optional[int]:
    """End a leftover helper so exactly one owns the mic. Returns its PID or None.

    Two helpers each open the mic and run their own wake loop, so one spoken
    phrase fires two turns (owner 2026-10-05: "voice trigger twice"). A
    systemd-managed helper takes over from a stale manual/leftover one: send
    SIGTERM, then wait (bounded) for it to release the mic. Nothing is
    signalled unless the heartbeat names a fresh, cmdline-validated helper pid
    (``heartbeat.other_live_pid``). Never raises; never blocking beyond the
    wait bound. ``kill``/``sleep``/``monotonic`` are injected for tests.
    """
    kill = kill or os.kill
    sleep = sleep or time.sleep
    monotonic = monotonic or time.monotonic
    try:
        from .voice_edit import heartbeat as _hb
        pid = _hb.other_live_pid(
            exclude=os.getpid() if exclude is None else int(exclude))
    except Exception:
        return None
    if not pid:
        return None
    _trace("singleton.takeover", pid=pid)
    try:
        kill(pid, signal.SIGTERM)
    except OSError:
        return pid
    deadline = monotonic() + 3.0
    while monotonic() < deadline:
        try:
            kill(pid, 0)
        except OSError:
            return pid
        sleep(0.1)
    return pid


def _install_term_handler(helper: "DesktopHelper") -> None:
    """Release the mic on SIGTERM so a takeover/stop never orphans ``parec``.

    Without this, ``kill``/systemd-stop terminates the process without running
    ``shutdown()``, leaving the capture child alive and the mic busy — the
    leftover that produced double triggers. Never raises.
    """
    def _term(_signum: int, _frame: Any) -> None:
        try:
            helper.shutdown()
        except Exception:
            pass
        raise SystemExit(0)

    try:
        signal.signal(signal.SIGTERM, _term)
    except (ValueError, OSError):
        pass


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="qubit-voice-typing",
                                     description="Qubit global voice typing/editing")
    parser.add_argument("--check", action="store_true",
                        help="preflight and exit (read-only)")
    parser.add_argument("--headless", action="store_true",
                        help="no preview window; status via notifications")
    args = parser.parse_args(argv)
    try:
        # Share the one Qubit event journal so helper diagnostics are auditable.
        from .config import canonical_state_dir
        from .logging_setup import setup as _setup_logging
        _setup_logging(state_dir=canonical_state_dir())
    except Exception:
        pass
    cfg = build_cfg()
    if args.check:
        print(json.dumps(check(cfg), indent=2, sort_keys=True))
        return 0
    if not bool(_read_cfg(cfg, "voice_edit", "enabled", default=False)):
        print("Qubit voice typing is disabled (voice_edit.enabled=false). "
              "Enable it in dashboard Settings first.", file=sys.stderr)
        return 3
    desktop = DesktopAdapter()
    overlay = make_overlay(lambda action, payload: helper.on_overlay_action(
        action, payload), headless=args.headless)
    client = HelperClient(resolve_service_base(cfg), resolve_token(cfg))
    helper = DesktopHelper(cfg, client=client, overlay=overlay, desktop=desktop,
                           notify=_notify_send)
    # Single mic authority: end a leftover helper before we open the mic, and
    # make sure OUR shutdown runs on SIGTERM so we never orphan the capture
    # child ourselves (double helpers = double voice triggers, owner 2026-10-05).
    take_over_previous_helper()
    _install_term_handler(helper)
    if not helper.start():
        return 3
    # Overlay action callbacks run on the Tk main thread already when the window
    # is alive; schedule the loop and the hotkey listener.
    from .hotkeys import HotkeyListener
    listener = HotkeyListener(hold_key="ctrl_r", tap_cancel="esc")
    if not listener.start(lambda event, key: helper.on_hotkey(event, key)):
        overlay.set_status("error", "no global hotkey backend available")
        print("hotkey backend unavailable; run under an X11 session",
              file=sys.stderr)
        return 4
    try:
        if args.headless or not hasattr(overlay, "root"):
            while True:
                time.sleep(0.5)
        else:
            overlay.root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()
        helper.shutdown()
        overlay.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
