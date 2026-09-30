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
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .logging_setup import log_event
from .voice_edit import debug as _dbg
from .voice_edit.sound import beep
from .voice_edit.desktop import DesktopAdapter
from .voice_edit.overlay import make_overlay
from .voice_edit.schema import SelectionSnapshot
from .voice_edit.stt_client import ParecMic, SttClient, SttError, transcribe_once


def _trace(stage: str, **fields: Any) -> None:
    try:
        log_event(f"voice_edit.helper.{stage}", **fields)
    except Exception:
        pass


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

    def begin(self) -> Dict[str, Any]:
        return self._request("POST", "/api/voice-edit/session", {})

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


class DesktopHelper:
    """Hotkey -> STT -> transform/dictate -> preview -> apply state machine."""

    IDLE, LISTENING, PROCESSING, PREVIEW = "idle", "listening", "processing", "preview"

    def __init__(self, cfg: Any, *, client: Any, overlay: Any,
                 desktop: Optional[DesktopAdapter] = None,
                 mic_factory: Optional[Callable[[], Any]] = None,
                 stt_factory: Optional[Callable[[str], Any]] = None,
                 notify: Optional[Callable[[str, str], None]] = None) -> None:
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
        self._fs_active = False
        self._fs_checked = 0.0
        self._gen = 0
        self._repeat_restore: list = []
        self._listen_started_at = 0.0
        # Accurate-vs-streaming provenance (surfaced, never silent).
        self._stt_fallback_reason = ""
        self._final_engine = ""
        # A staged actionable task awaiting an explicit confirmation.
        self._pending_task: Dict[str, Any] = {}
        # True while Qubit is speaking its reply (voice conversation).
        self._speaking = False
        # Accurate-model prewarm readiness (explicit, not blind): the background
        # prewarm sets the event/state, and the first activation waits for it
        # with a bounded timeout so the user is never left waiting blind.
        # Tests may set these directly (no real model required).
        self._warm_event = threading.Event()
        self._warm_state = "skipped"

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
            # Busy = a session is actively capturing/processing, or a
            # full-screen app is focused. A PREVIEW left open must NOT block
            # wake forever: a new wake simply supersedes it.
            self._wake = WakeMonitor(
                self.cfg, on_command=self.on_wake_command,
                is_busy=lambda: (self.state in (self.LISTENING,
                                                self.PROCESSING)
                                 or getattr(self, "_speaking", False)
                                 or self._fullscreen_active()))
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
                  extra={"hotkey_mode": "toggle" if self.toggle else "hold",
                         "wake": bool(self._wake is not None
                                      and self._wake.enabled()),
                         "wake_engine": getattr(self._wake, "engine", ""),
                         "stt_warm": self._warm_state,
                         "state": self.state})
        self._hb_stop = threading.Event()
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True,
                                           name="voice-edit-heartbeat")
        self._hb_thread.start()
        self._ui_status("idle", "hold Right-Ctrl to speak")
        return True

    def _heartbeat_loop(self) -> None:
        while not self._hb_stop.wait(8.0):
            try:
                self._hb.write(
                    session=str(self.desktop.session().get("session_type", "x11")),
                    extra={"hotkey_mode": "toggle" if self.toggle else "hold",
                           "wake": bool(self._wake is not None
                                        and self._wake.enabled()),
                           "wake_engine": getattr(self._wake, "engine", ""),
                           "stt_warm": self._warm_state,
                           "state": self.state})
            except Exception:
                pass

    def shutdown(self) -> None:
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

    def _stop_listening(self) -> None:
        """Release of the hotkey: keep a short tail, then finalize.

        A browser keeps the socket open, so the last word is never clipped.
        Here we let the mic stream for ``release_tail_ms`` after the key is
        released so the ASR sees the end of the utterance.
        """
        _trace("stop_listening", active=self._active, state=self.state)
        self._active = False
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
                    return False
            except HelperError as exc:
                self._ui_status("error", f"{exc.code}")
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
                rid = str(self.client.begin().get("request_id", ""))
                self.request_id = rid
            except HelperError as exc:
                self._ui_status("error", f"{exc.code}: {exc.detail}")
                self._active = False
                return False
            self.state = self.LISTENING
            self._listen_started_at = time.monotonic()
            return True

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
            self._stop.set()

    def on_wake_command(self, command: str) -> None:
        """A hands-free wake utterance arrived (already transcribed).

        Runs the SAME begin/capture/transform path as Right-Ctrl; only the
        audio source differs (the wake monitor, not the hold-to-talk mic).
        """
        cmd = (command or "").strip()
        if not cmd:
            return
        if not self._open_request():
            return
        self._capture_selection()
        _trace("wake.command", chars=len(cmd), request_id=self.request_id,
               **(_dbg.text_fields(self.cfg, command=cmd)))
        self._transcript = cmd
        self._process_transcript(cmd)

    def _transcribe(self) -> None:
        _trace("transcribe.start", request_id=self.request_id)
        mode = str(_read_cfg(self.cfg, "voice_edit", "stt_mode",
                             default="accurate") or "accurate").lower()
        accurate = mode == "accurate"
        try:
            ticket = self.client.mint_stt_ticket()
        except HelperError as exc:
            _trace("transcribe.ticket_failed", code=exc.code)
            self._on_stt_error(exc.code, exc.detail)
            return
        _trace("transcribe.ticket_ok", ticket_len=len(ticket))
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
        # In accurate mode the streaming socket still drives live interim
        # partials; the wrapper records every frame so the same audio can be
        # decoded once by faster-whisper after release.
        from .voice_edit.stt_client import BufferingMic
        mic = BufferingMic(base_mic) if accurate else base_mic
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
        self._transcript = (final or "").strip()
        _trace("transcript", request_id=self.request_id,
               chars=len(self._transcript),
               **(_dbg.text_fields(self.cfg, text=self._transcript)))
        if not self._transcript:
            self._ui_status("error", self._stt_fallback_reason
                            or "no speech recognised")
            self._finish_error()
            return
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
        self._finish_error()

    def _finish_error(self) -> None:
        self._active = False
        self.state = self.IDLE

    def _process_transcript(self, transcript: str = "") -> None:
        if self.snapshot is None and self.request_id:
            # Ensure the selection snapshot exists even if capture lagged.
            self._capture_selection()
        text = (transcript or self._transcript or "").strip()
        # A staged task (mode "task") is confirm-gated: the next utterance is
        # only honored if it is an affirmative/negative confirmation.
        if self._pending_task:
            if self._affirmative(text):
                task = str(self._pending_task.get("text", "") or "")
                self._pending_task = {}
                self._run_task(task)
                return
            if self._negative(text):
                self._pending_task = {}
                self._ui_status("idle", "task cancelled")
                self._finish_error()
                return
            # Anything else supersedes the pending task ("no" by default).
            self._pending_task = {}
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
        low = (text or "").strip().lower().strip(" .,!?:;")
        return low in ("yes", "yeah", "yep", "confirm", "confirmed", "do it",
                       "go ahead", "run it", "ok", "okay", "sure", "proceed",
                       "yes please", "confirm task", "run the task")

    @staticmethod
    def _negative(text: str) -> bool:
        low = (text or "").strip().lower().strip(" .,!?:;")
        return low in ("no", "nope", "cancel", "stop", "don't", "dont",
                       "never mind", "nevermind", "abort", "no thanks")

    def _propose_task(self, out: Dict[str, Any]) -> None:
        """Stage a confirm-gated task and ask for confirmation (never runs it)."""
        task = str(out.get("task_text") or out.get("summary") or "").strip()
        if not task:
            self._ui_status("error", "nothing to run")
            self._finish_error()
            return
        self._pending_task = {"text": task, "request_id": self.request_id}
        _trace("task.proposed", chars=len(task), request_id=self.request_id,
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

    def _run_task(self, text: str) -> None:
        """Run a confirmed task through the one agent turn path (never typed)."""
        if not text:
            self._finish_error()
            return
        self.state = self.PROCESSING
        self._ui_status("processing", "running task…")
        try:
            out = self.client.task(text, confirm=True,
                                   request_id=self.request_id)
        except HelperError as exc:
            self._ui_error(exc.code, exc.detail)
            self._notify_status("Qubit", f"{exc.code}: {exc.detail}")
            self._finish_error()
            return
        reply = str(out.get("reply", "") or "")
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
            return
        self._ui_error(str(out.get("code", "VOICE_EDIT_TASK_EMPTY")),
                       str(out.get("detail", "")))
        self._notify_status("Qubit", str(out.get("detail", "") or "task failed"))
        self._finish_error()

    def _answer_question(self, question: str, *, mode: str = "answer") -> None:
        """Ask the Qubit brain, display the answer, and speak it aloud."""
        self._ui_status("processing", "answering…")
        spoken = False
        try:
            if hasattr(self.client, "ask_result"):
                out = self.client.ask_result(question, self.request_id)
                reply = str(out.get("answer", "") or "")
                # The service streams the speech itself; don't double-play.
                spoken = bool(out.get("spoken"))
            else:  # compatibility seam (older/fake clients)
                reply = self.client.ask(question, self.request_id)
        except HelperError as exc:
            self._ui_error(exc.code, exc.detail)
            return
        if not reply:
            self._ui_status("error", "no answer")
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

    def _speak(self, text: str) -> None:
        """Speak the gist of ``text`` through the service's TTS, locally.

        Best-effort and non-fatal: a TTS/playback failure never changes the
        answer that was already shown. Disable with ``voice_edit.speak_answers``.
        """
        if not text or not bool(_read_cfg(self.cfg, "voice_edit",
                                          "speak_answers", default=True)):
            return
        import os
        import shutil
        import subprocess
        import tempfile
        spoken = self._spoken_excerpt(text)
        if not spoken:
            return
        try:
            wav = self.client.speak(spoken[:400])
        except HelperError as exc:
            _trace("speak_error", code=exc.code)
            return
        if not wav:
            return
        player = "paplay" if shutil.which("paplay") else (
            "aplay" if shutil.which("aplay") else "")
        if not player:
            return
        path = ""
        self._speaking = True
        try:
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="qubit-answer-")
            with os.fdopen(fd, "wb") as fh:
                fh.write(wav)
            _trace("speak", chars=len(text))
            subprocess.run([player, path], timeout=90, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:  # never fatal
            _trace("speak_failed", detail=str(exc)[:120])
        finally:
            self._speaking = False
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def _insert_dictation(self, text: str) -> None:
        if not text:
            self._finish_error()
            return
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
