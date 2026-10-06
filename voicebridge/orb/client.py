"""Orb HTTP client: read-only status polls + begin/cancel voice requests.

Same loopback convention the desktop helper uses
(``voicebridge/desktop_helper.py`` ``HelperClient`` /
``resolve_service_base`` / ``resolve_token``), re-implemented here so
the orb never imports the helper's capture/mic tree:

- base: ``voice_edit.service_url`` when set, else the ``webui``
  host/port (``0.0.0.0``/``::`` become ``127.0.0.1``), default
  ``http://127.0.0.1:8890``;
- auth: ``webui.token`` when set, else
  ``~/.config/qubit-voicebridge/webui-token.txt``; the header names
  (``X-Api-Token`` + ``X-Qubit-Session-Token``) and the ``/agent``
  prefix match the helper exactly;
- click path: ``POST /api/voice-edit/session`` (begin -- the exact
  call the helper's own voice flow starts with) and
  ``POST /api/voice-edit/<rid>/cancel`` (the existing cancel verb).
  The orb never captures audio itself.
- talk path (click-to-talk): ``trigger_helper_capture()`` signals the
  running desktop helper (SIGUSR1 to its heartbeat pid, cmdline
  validated so a recycled pid is never touched) and the HELPER runs
  its existing PTT capture + transcribe path. The orb still never
  opens the mic. When no helper is live, the click falls back to the
  stage-only begin (next click cancels it).

Errors surface as :class:`OrbError` (typed code, short detail). The
token is never included in any error, log line, or persisted file.
"""
from __future__ import annotations

import json
import os
import queue as _queue
import signal
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional

DEFAULT_BASE = "http://127.0.0.1:8890"
TOKEN_FILE = Path("~/.config/qubit-voicebridge/webui-token.txt").expanduser()

#: Talk-meter freshness: a published level older than this reads as 0.0
#: (silence). A payload without level keys reads as None (old helper /
#: offline -> the renderer keeps exactly today's loop). Units: ``mic_level``
#: is a 0.0..1.0 peak float, ``mic_level_ts`` is ``time.monotonic`` seconds
#: on the helper's clock (same machine, same clock as this reader).
MIC_LEVEL_MAX_AGE_S = 1.0


def mic_level_from_payload(payload: Any, now: Any = None) -> Optional[float]:
    """Freshest talk-meter level from a heartbeat/status payload (pure).

    Returns the clamped 0.0..1.0 level when ``mic_level``/``mic_level_ts``
    are present and fresher than :data:`MIC_LEVEL_MAX_AGE_S`; ``0.0`` when
    present but stale; ``None`` when absent/malformed (fail-soft: the
    caller renders today's loop). Never raises; performs no I/O (and in
    particular never opens the mic).
    """
    try:
        if not isinstance(payload, dict):
            return None
        if "mic_level" not in payload or "mic_level_ts" not in payload:
            return None
        level = float(payload["mic_level"])
        ts = float(payload["mic_level_ts"])
    except (TypeError, ValueError):
        return None
    except Exception:
        return None
    try:
        now_f = float(now) if now is not None else time.monotonic()
    except (TypeError, ValueError):
        return None
    except Exception:
        return None
    try:
        age = now_f - ts
    except Exception:
        return None
    if not (age >= 0.0):
        # NaN ts or a clock quirk: not a trustworthy fresh value.
        # NaN can never be fresh; a slightly future stamp is tolerated.
        try:
            import math as _math
            if _math.isnan(float(ts)):
                return 0.0
        except Exception:
            return 0.0
        age = 0.0
    try:
        if age > float(MIC_LEVEL_MAX_AGE_S):
            return 0.0
    except Exception:
        return 0.0
    try:
        return max(0.0, min(1.0, level))
    except Exception:
        return 0.0

#: Substring every live helper process carries in its cmdline (mirrors the
#: orb's own pid-validation pattern in ``__main__._pid_is_our_orb``).
_HELPER_CMDLINE_MARK = "voicebridge.desktop_helper"


#: Equalizer spectrum: 12 log-spaced bands 80Hz-8kHz, 0.0..1.0 each
#: (spec design 2026-10-05). Published by the helper into heartbeat
#: extras as ``spec_bands`` + ``spec_ts`` (live mic) and ``spec_tts`` +
#: ``spec_tts_ts`` (reply band timeline); the orb only parses what the
#: already-fetched status/heartbeat payload carries — zero extra HTTP.
SPEC_BAND_COUNT = 12

#: A live band vector older than this reads as silence (fail-soft:
#: stale eases to flat, never freezes mid-air). Same clock discipline
#: as ``MIC_LEVEL_MAX_AGE_S`` (monotonic seconds on the helper clock).
SPEC_MAX_AGE_S = 1.0

#: One TTS timeline frame covers this many seconds of reply audio
#: (the publish side FFTs 100ms windows; playback position / this =
#: the frame index).
SPEC_TTS_FRAME_S = 0.1


class OrbError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _cfg_get(cfg: Any, *keys: str, default: Any = "") -> Any:
    """Read nested keys off a Config-like object or a plain mapping."""
    try:
        if callable(cfg) and not isinstance(cfg, dict) and not hasattr(cfg, "get"):
            try:
                return cfg(*keys, default=default)
            except TypeError:
                return default
        if isinstance(cfg, dict):
            node: Any = cfg
            for key in keys:
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
            return node
        get = getattr(cfg, "get", None)
        if callable(get):
            try:
                return get(*keys, default=default)
            except TypeError:
                pass
        node = cfg
        for key in keys:
            node = node[key] if isinstance(node, dict) else getattr(node, key)
        return node
    except Exception:
        return default


def service_base(cfg: Any) -> str:
    """Resolve the local service base (read-only; never raises)."""
    try:
        explicit = str(_cfg_get(cfg, "voice_edit", "service_url", default="") or "")
        if explicit:
            return explicit.rstrip("/")
        host = str(_cfg_get(cfg, "webui", "host", default="127.0.0.1") or "127.0.0.1")
        try:
            port = int(_cfg_get(cfg, "webui", "port", default=8890) or 8890)
        except (TypeError, ValueError):
            port = 8890
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        return f"http://{host}:{port}"
    except Exception:
        return DEFAULT_BASE


def service_token(cfg: Any) -> str:
    """Resolve the loopback token (read-only; never raises, never logs)."""
    try:
        tok = str(_cfg_get(cfg, "webui", "token", default="") or "")
        if tok:
            return tok
        if TOKEN_FILE.is_file():
            return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def load_cfg() -> Any:
    """Load the installed Qubit config (``None`` when unavailable)."""
    try:
        from voicebridge.config import Config
        return Config.load()
    except Exception:
        return None


class OrbClient:
    """Thin authenticated client for the local voice-edit surface."""

    def __init__(self, base_url: str, token: str = "",
                 opener: Optional[Callable[..., Any]] = None) -> None:
        self.base = (base_url or DEFAULT_BASE).rstrip("/")
        self.token = token or ""
        self._opener = opener or urllib.request.urlopen

    @classmethod
    def from_config(cls, cfg: Any,
                    opener: Optional[Callable[..., Any]] = None) -> "OrbClient":
        return cls(service_base(cfg), service_token(cfg), opener=opener)

    def _request(self, method: str, path: str,
                 payload: Optional[dict] = None,
                 timeout: float = 10.0) -> Dict[str, Any]:
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
                parsed = json.loads(body) if body.strip() else {}
                return parsed if isinstance(parsed, dict) else {}
        except urllib.error.HTTPError as exc:
            raise OrbError(f"HTTP_{exc.code}", "service refused the request")
        except (urllib.error.URLError, OSError, ValueError):
            raise OrbError("ORB_SERVICE_UNREACHABLE", "local service not reachable")
        except Exception:
            raise OrbError("ORB_REQUEST_FAILED", "request failed")

    def get_status(self) -> Dict[str, Any]:
        """Read-only voice-edit status (same source as the dashboard)."""
        return self._request("GET", "/api/voice-edit/status", timeout=2)

    def get_log(self, limit: int = 60) -> Dict[str, Any]:
        """Read-only recent voice journal (char counts only, never text)."""
        try:
            lim = max(1, min(int(limit), 200))
        except (TypeError, ValueError):
            lim = 60
        return self._request("GET", f"/api/voice-edit/log?limit={lim}", timeout=2)

    def get_settings(self) -> Dict[str, Any]:
        """Read-only voice settings (same auth, no new auth).

        Returns the ``settings`` mapping (``{}`` when the body carries
        none). Transport failures raise :class:`OrbError` like every other
        call — the poll tick keeps its last override then. Never logs the
        token.
        """
        out = self._request("GET", "/api/voice-edit/settings", timeout=2)
        try:
            settings = out.get("settings", {})
            return dict(settings) if isinstance(settings, dict) else {}
        except Exception:
            return {}

    def mic_level(self, status: Any = None,
                  now: Any = None) -> Optional[float]:
        """Freshest talk-meter level with no extra HTTP call (never raises).

        Reads ``mic_level``/``mic_level_ts`` from the given status payload
        first (today's service does not pass them through, so this is the
        future-proof path), else from the local helper heartbeat file the
        orb already reads for click-to-talk. Returns 0.0..1.0, ``0.0`` when
        stale, ``None`` when unavailable (old helper/offline). Performs no
        audio capture — the helper owns the mic.
        """
        try:
            if isinstance(status, dict) and "mic_level" in status \
                    and "mic_level_ts" in status:
                return mic_level_from_payload(status, now=now)
            return mic_level_from_payload(_helper_heartbeat(), now=now)
        except Exception:
            return None

    def spectrum(self, status: Any = None,
                 now: Any = None) -> Optional[list]:
        """Freshest live spectrum with no extra HTTP call (never raises).

        Parses ``spec_bands``/``spec_ts`` from the given status payload
        first, else from the local helper heartbeat file. Returns the
        12-float list, or ``None`` when stale/absent (old helper /
        offline -> the renderer keeps today's loop). No audio capture.
        """
        try:
            fresh = spec_bands_from_payload(status, now=now)
            if fresh is not None:
                return fresh
            return spec_bands_from_payload(_helper_heartbeat(), now=now)
        except Exception:
            return None

    def tts_frame(self, status: Any = None,
                 now_mono: Any = None) -> Optional[list]:
        """TTS-timeline frame under the play head (never raises).

        Same source discipline as :meth:`spectrum`: the already-fetched
        status payload first, the helper heartbeat file second. Past
        the reply end (or absent) -> ``None``. No extra HTTP, no audio
        capture.
        """
        try:
            frame = spec_tts_frame_from_payload(status, now_mono=now_mono)
            if frame is not None:
                return frame
            return spec_tts_frame_from_payload(_helper_heartbeat(),
                                               now_mono=now_mono)
        except Exception:
            return None

    def begin_session(self) -> Dict[str, Any]:
        """Stage a voice request (the helper's own flow starts here)."""
        return self._request("POST", "/api/voice-edit/session", {}, timeout=2)

    def cancel_request(self, request_id: str) -> Dict[str, Any]:
        """Cancel a staged voice request (existing cancel verb)."""
        rid = "".join(c for c in str(request_id or "")
                      if c.isalnum() or c in ("-", "_"))[:64]
        if not rid:
            raise OrbError("ORB_BAD_REQUEST_ID", "no staged request to cancel")
        return self._request("POST", f"/api/voice-edit/{rid}/cancel", {},
                             timeout=2)

    def trigger_helper_capture(self) -> Dict[str, Any]:
        """Ask the running desktop helper to start/finish one PTT capture.

        Sends SIGUSR1 to the helper's heartbeat pid (cmdline-validated, so
        a recycled pid belonging to another process is never signalled).
        The helper — the single mic owner — then runs its existing PTT
        path (begin + mic + STT + turn); the orb captures nothing itself.
        Raises ``OrbError("ORB_HELPER_NOT_RUNNING")`` when no live helper
        is found (caller falls back to stage-only begin). Never raises
        anything else; carries no secret.
        """
        try:
            hb = _helper_heartbeat()
        except Exception:
            hb = None
        pid = 0
        try:
            pid = int((hb or {}).get("pid", 0) or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0:
            raise OrbError("ORB_HELPER_NOT_RUNNING", "desktop helper not live")
        try:
            cmdline = _helper_cmdline(pid)
        except Exception:
            cmdline = ""
        if _HELPER_CMDLINE_MARK not in (cmdline or ""):
            raise OrbError("ORB_HELPER_NOT_RUNNING",
                           "desktop helper pid not validated")
        try:
            os.kill(pid, signal.SIGUSR1)
        except (OSError, ValueError):
            raise OrbError("ORB_HELPER_NOT_RUNNING",
                           "desktop helper not reachable")
        return {"ok": True, "helper_pid": pid}


def fetch_poll_payload(cli: Any, tick: int) -> Dict[str, Any]:
    """One non-UI fetch round: status + log every round, settings every 5th.

    Runs on the poll worker thread (or synchronously on worker-less
    paths such as tests); touches no widget — HTTP only. The every-5th-
    tick settings rule and the never-while-offline rule live here, so
    both views share one cadence. ``settings`` is ``None`` when not
    fetched (not this tick, offline, or failed) so the caller keeps its
    last override; a fetched mapping (possibly ``{}``) is applied.
    Never raises.
    """
    try:
        status = cli.get_status()
    except Exception:
        status = None
    events: Any = []
    if status is not None:
        try:
            log = cli.get_log(60)
            raw = log.get("events", []) if isinstance(log, dict) else []
            events = raw if isinstance(raw, list) else []
        except Exception:
            events = []
    settings: Optional[Dict[str, Any]] = None
    try:
        nth = int(tick)
    except (TypeError, ValueError):
        nth = 0
    if status is not None and nth % 5 == 1:
        try:
            got = cli.get_settings()
            settings = dict(got) if isinstance(got, dict) else {}
        except Exception:
            settings = None
    return {"status": status, "events": events, "settings": settings,
            "heartbeat": _helper_heartbeat()}


def offer_latest(q: Any, payload: Dict[str, Any]) -> None:
    """Hand one payload to the mainloop: newest wins, stale dropped.

    The queue is bounded (maxsize 1); when full the stale entry is
    discarded before offering the fresh one, so the mainloop never
    applies out-of-order state. Never raises, never blocks.
    """
    try:
        q.put_nowait(payload)
        return
    except _queue.Full:
        pass
    except Exception:
        return
    try:
        q.get_nowait()
    except (_queue.Empty, Exception):
        pass
    try:
        q.put_nowait(payload)
    except Exception:
        pass


def drain_latest(q: Any) -> Optional[Dict[str, Any]]:
    """Take the newest queued payload (None when empty). Never raises."""
    try:
        latest: Optional[Dict[str, Any]] = None
        while True:
            try:
                latest = q.get_nowait()
            except _queue.Empty:
                break
        return latest
    except Exception:
        return None


def _helper_heartbeat() -> Optional[Dict[str, Any]]:
    """Read the desktop helper liveness heartbeat (None when not live)."""
    try:
        from voicebridge.voice_edit.heartbeat import live as _live, read as _read
        if not _live():
            return None
        return _read()
    except Exception:
        return None


def _helper_cmdline(pid: int) -> str:
    """One process's cmdline as text ("" when unreadable)."""
    try:
        raw = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
        return raw.replace(b"\0", b" ").decode("utf-8", "replace")
    except Exception:
        return ""


def _spec_extras(payload: Any) -> Dict[str, Any]:
    """Heartbeat extras mapping out of a status/heartbeat payload.

    Reads the payload itself, else its ``"extras"`` sub-mapping (the
    publish side may nest the spectrum beside other extras). Anything
    else -> ``{}``. Never raises, never opens the mic.
    """
    try:
        if isinstance(payload, dict):
            if "spec_bands" in payload or "spec_tts" in payload:
                return payload
            nested = payload.get("extras", None)
            if isinstance(nested, dict):
                return nested
    except Exception:
        pass
    return {}


def _clamp_spec_vector(raw: Any) -> Optional[list]:
    """12 finite floats clamped 0..1, else None. Never raises."""
    try:
        vals = list(raw)
    except (TypeError, ValueError):
        return None
    except Exception:
        return None
    if len(vals) != SPEC_BAND_COUNT:
        return None
    out: list = []
    try:
        for v in vals:
            f = float(v)
            if f != f:  # NaN is never a band level
                return None
            out.append(max(0.0, min(1.0, f)))
    except (TypeError, ValueError):
        return None
    except Exception:
        return None
    return out


def spec_bands_from_payload(payload: Any, now: Any = None) -> Optional[list]:
    """Fresh live spectrum from a heartbeat/status payload (pure).

    Returns the fresh 12-float list when ``spec_bands``/``spec_ts``
    are present and fresher than :data:`SPEC_MAX_AGE_S`; ``None`` when
    absent, malformed, wrong length, or stale (fail-soft: the caller
    renders today's sine loop, or eases a live smoother to flat).
    Never raises; performs no I/O (and never opens the mic).
    """
    try:
        extras = _spec_extras(payload)
        if "spec_bands" not in extras or "spec_ts" not in extras:
            return None
        bands = _clamp_spec_vector(extras["spec_bands"])
        if bands is None:
            return None
        ts = float(extras["spec_ts"])
    except (TypeError, ValueError):
        return None
    except Exception:
        return None
    try:
        now_f = float(now) if now is not None else time.monotonic()
    except (TypeError, ValueError):
        return None
    except Exception:
        return None
    try:
        age = now_f - ts
    except Exception:
        return None
    if not (age >= 0.0):
        age = 0.0  # NaN/future stamp: not a trustworthy fresh value
    try:
        if age > float(SPEC_MAX_AGE_S):
            return None
    except Exception:
        return None
    return bands


def spec_tts_frame_from_payload(payload: Any,
                                now_mono: Any = None) -> Optional[list]:
    """The TTS-timeline frame for the current playback position (pure).

    ``spec_tts`` is the reply's band timeline (one 12-float frame per
    :data:`SPEC_TTS_FRAME_S`); ``spec_tts_ts`` is the monotonic second
    playback started. Returns the frame under the play head, or
    ``None`` when absent/malformed, before the start (clamped to the
    first frame instead), or past the reply end (stale = silence).
    Never raises; performs no I/O (and never opens the mic).
    """
    try:
        extras = _spec_extras(payload)
        if "spec_tts" not in extras or "spec_tts_ts" not in extras:
            return None
        frames = list(extras["spec_tts"])
        start = float(extras["spec_tts_ts"])
    except (TypeError, ValueError):
        return None
    except Exception:
        return None
    if not frames:
        return None
    try:
        now_f = float(now_mono) if now_mono is not None else time.monotonic()
    except (TypeError, ValueError):
        return None
    except Exception:
        return None
    try:
        idx = int((now_f - start) / float(SPEC_TTS_FRAME_S))
    except (TypeError, ValueError):
        return None
    except Exception:
        return None
    if idx < 0:
        idx = 0
    if idx >= len(frames):
        return None  # past reply end: silence, never a frozen mid-air bar
    return _clamp_spec_vector(frames[idx])


def frame_spectrum_for_state(state: Any, payload: Any,
                             now: Any = None,
                             now_mono: Any = None) -> Optional[list]:
    """Which spectrum (if any) a state renders (pure, never raises).

    ``listening`` reads the live bands, ``speaking`` reads the TTS
    timeline frame at the current playback position; every other state
    returns ``None`` (thinking/idle/error have no audio to analyse and
    render byte-identical with or without a payload). No I/O.
    """
    try:
        key = str(state or "").strip().lower()
    except Exception:
        return None
    try:
        if key == "listening":
            return spec_bands_from_payload(payload, now=now)
        if key == "speaking":
            mono = now_mono if now_mono is not None else now
            return spec_tts_frame_from_payload(payload, now_mono=mono)
    except Exception:
        return None
    return None


__all__ = [
    "DEFAULT_BASE",
    "MIC_LEVEL_MAX_AGE_S",
    "mic_level_from_payload",
    "SPEC_BAND_COUNT",
    "SPEC_MAX_AGE_S",
    "SPEC_TTS_FRAME_S",
    "spec_bands_from_payload",
    "spec_tts_frame_from_payload",
    "frame_spectrum_for_state",
    "OrbError",
    "service_base",
    "service_token",
    "load_cfg",
    "OrbClient",
    "fetch_poll_payload",
    "offer_latest",
    "drain_latest",
]
