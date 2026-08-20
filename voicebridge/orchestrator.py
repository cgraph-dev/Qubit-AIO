"""Voice bridge orchestrator: mic -> ASR -> classify -> route -> OpenClaw -> speak.

Runs the whole loop with interruption support:
  * push-to-talk (hold key) or manual Enter-toggle
  * VAD endpointing + barge-in (speech while TTS -> cancel output)
  * intent routing (system / confirm / cancel / coding / conversation / clarify)
  * safety gate (destructive actions require spoken confirmation)
  * OpenClaw execution via gateway WS (CLI fallback if the gateway is down)
  * spoken status updates + summarized final results
  * structured events.jsonl observability with per-utterance timing

Threading model: the gateway client runs on its own asyncio loop thread; ASR
and TTS are synchronous (GPU/CPU) executed in worker threads; the main loop is
thread-based for simplicity, using events/queues for coordination.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import numpy as np
import sounddevice as sd

from .audio.mic import MicCapture
from .audio.playback import Playback
from .audio.vad import BargeInDetector, Endpointer, FrameSplitter
from .config import Config
from .hermes import HermesBackend
from .hotkeys import HotkeyListener
from .intent.classifier import Classifier, Intent
from .intent.state import ConfirmationState, ConversationState, TaskState
from .local import actions as local_actions
from .logging_setup import get_logger, log_event
from .openclaw.cli_backend import CliBackend
from .openclaw.discovery import discover_gateway, discover_cuda_libs
from .openclaw.gateway_ws import GatewaySync, GatewayError
from .personaplex import PersonaPlexDuplex
from .safety.policy import SafetyPolicy, SafetyVerdict
from .summarizer import summarize, trim_spoken

log = get_logger()

BEEP_START = np.array([], dtype=np.int16)   # placeholder; real beeps via piper
_SAMPLE_TASK = (
    "Open the current project, inspect the Stripe webhook flow, and "
    "patch the signature verification and add structured logs."
)


def _needs_web(text: str) -> bool:
    """Heuristic: does this question want current/web-grounded info (Perplexity)?"""
    t = text.lower()
    markers = (
        "search", "look up", "google", "latest", "current", "news", "today",
        "weather", "price of", "stock", "who won", "what happened", "when did",
        "how many people", "election", "score", "release date", "announcement",
        "update on", "perplexity", "on the web", "find out", "do you know about",
        "new version", "recent",
    )
    return any(m in t for m in markers)


class VoiceOrchestrator:
    def __init__(self, config: Config, mic_only: bool = False,
                 no_hotkey: bool = False, dry_run: bool = False) -> None:
        self.cfg = config
        self.mic_only = mic_only
        self.no_hotkey = no_hotkey or not config.get("hotkey", "enabled", default=True)
        self.dry_run = dry_run  # global force: never dispatch, only speak plan

        # state
        self.conversation = ConversationState(max_turns=int(config.get("conversation", "max_turns", default=4)))
        self.confirmations = ConfirmationState()
        self.task = TaskState()
        self.muted = False
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._ptt_started = threading.Event()
        self._ptt_released = threading.Event()
        self._ptt_hold = False
        self._ptt_supported = False
        self._events: queue.Queue = queue.Queue()  # hotkey/manual events

        # layers (lazy)
        self._asr = None
        self._tts = None
        self._gateway: Optional[GatewaySync] = None
        self._cli: Optional[CliBackend] = None
        self._mic: Optional[MicCapture] = None
        self._barge: Optional[BargeInDetector] = None
        self._barge_stream = None
        self._beep_start = None
        self._beep_end = None
        # PTT state: tap-to-toggle + hold-to-talk
        self._press_time = 0.0
        self._toggle_armed = False       # a tap started a toggle capture
        self._toggle_stop = threading.Event()
        self.playback = Playback()
        self.classifier: Optional[Classifier] = None
        self.safety = SafetyPolicy(config.section("safety"))
        self.gateway_info = None
        self._route: str = ""   # 'gateway' | 'cli' | 'offline'
        self.blocking_tasks = True   # False in the interactive hotkey loop
        self._duplex: Optional[PersonaPlexDuplex] = None
        self._hermes: Optional[HermesBackend] = None
        self._memory_context: str = ""

    # ------------------------------------------------------------------ setup
    def _make_asr(self):
        from .asr.personaplex import PersonaPlexASR
        from .asr.whisper import FasterWhisperASR
        asr_cfg = self.cfg.section("asr")
        provider = asr_cfg.get("provider", "whisper")
        if provider == "personaplex":
            pp = PersonaPlexASR(url=asr_cfg.get("personaplex", {}).get("url", "ws://127.0.0.1:8765"))
            if pp.available():
                log_event("asr.provider", provider="personaplex", active=True)
                return pp
            log_event("asr.provider", provider="personaplex", active=False,
                      note="PersonaPlex server unreachable; falling back to whisper")
        model_dir = self.cfg.model_dir()
        return FasterWhisperASR(model=asr_cfg.get("model", "base"),
                                device=asr_cfg.get("device", "auto"),
                                compute_type=asr_cfg.get("compute_type", "float16"),
                                language=asr_cfg.get("language"),
                                beam_size=asr_cfg.get("beam_size", 1),
                                model_dir=model_dir)

    def _make_tts(self):
        from .tts.edge import EdgeTTS
        from .tts.gateway_tts import GatewayTTS
        from .tts.piper import PiperTTS
        provider = self.cfg.get("tts", "provider", default="piper")
        if provider == "edge":
            edge = EdgeTTS(voice=self.cfg.get("tts", "edge", "voice", default="en-US-JennyNeural"),
                           rate=self.cfg.get("tts", "edge", "rate", default="+0%"))
            if edge.available():
                return edge
        if provider == "gateway" and self._gateway is not None:
            return GatewayTTS(self._gateway, model=self.cfg.get("tts", "gateway", "model"))
        return PiperTTS(model_dir=self.cfg.model_dir(),
                        voice=self.cfg.get("tts", "voice", default="en_US-amy-medium"))

    def _connect_gateway(self) -> str:
        """Returns route: 'gateway' | 'cli' | 'offline'."""
        self.gateway_info = discover_gateway(self.cfg)
        if not self.gateway_info.token:
            log_event("openclaw.gateway.no_token")
            return self._connect_cli_only()

        from .openclaw.discovery import current_vram_mb
        log_event("openclaw.gateway.info", url=self.gateway_info.url,
                  agent=self.gateway_info.agent,
                  default_model=self.gateway_info.default_model,
                  free_vram_mb=current_vram_mb())

        self._gateway = GatewaySync(
            self.gateway_info.url, self.gateway_info.token,
            agent=self.gateway_info.agent,
            event_cb=self._on_gateway_event)
        try:
            if self._gateway.connect(timeout=30.0):
                self._route = "gateway"
                log_event("openclaw.route", route="gateway")
                return "gateway"
        except Exception as exc:
            log_event("openclaw.gateway.connect.failed", error=str(exc)[:200])
        return self._connect_cli_only()

    def _connect_cli_only(self) -> str:
        self._cli = CliBackend(agent=self.gateway_info.agent if self.gateway_info else "main",
                               timeout_s=int(self.cfg.get("openclaw", "task_timeout_s", default=900)))
        if self._cli.connect():
            self._route = "cli"
            log_event("openclaw.route", route="cli")
            return "cli"
        self._route = "offline"
        log_event("openclaw.route", route="offline")
        return "offline"

    # -------------------------------------------------------------- lifecycle
    def start(self) -> None:
        # gateway first (may inform TTS choice)
        self._connect_gateway()
        # ASR
        self._asr = self._make_asr()
        try:
            self._asr.load()
        except Exception as exc:
            log_event("asr.load.failed", error=str(exc)[:200])
        # TTS
        self._tts = self._make_tts()
        try:
            self._tts.load()
        except Exception as exc:
            log_event("tts.load.failed", error=str(exc)[:200])
        # classifier
        self.classifier = Classifier(self.cfg.section("intent"),
                                     self.confirmations, self.conversation)

        # memory context (loaded from the OpenClaw workspace on THIS PC — the
        # Vast.ai box is disposable and never owns state)
        self._memory_context = self._load_memory_context()

        # Hermes backend (optional second executor, same machine)
        hermes_cfg = self.cfg.section("hermes")
        if hermes_cfg.get("enabled", False):
            self._hermes = HermesBackend(
                cmd=hermes_cfg.get("cmd", "hermes"),
                resume_session=hermes_cfg.get("resume_session", "voice"),
                timeout_s=float(hermes_cfg.get("timeout_s", 600)),
                model=hermes_cfg.get("model"))
            if self._hermes.available():
                log_event("hermes.active")
            else:
                log_event("hermes.unavailable")
                self._hermes = None

        # PersonaPlex full-duplex lane (remote GPU over Tailscale). Persona
        # prompt = configured persona + memory context loaded above.
        pp_cfg = self.cfg.section("personaplex")
        if pp_cfg.get("enabled", False):
            if self._memory_context:
                self.cfg.data.setdefault("personaplex", {})["text_prompt"] = (
                    f"{pp_cfg.get('text_prompt', '')}\n\n"
                    f"Things you remember about the user (from their notes):\n"
                    f"{self._memory_context}")
            self._duplex = PersonaPlexDuplex(self.cfg, on_transcript=self._on_duplex_transcript)
            ok = self._duplex.start()
            log_event("personaplex.lane", started=ok)

        # mic
        mic_cfg = self.cfg.section("mic")
        self._mic = MicCapture(device=mic_cfg.get("device"),
                               sample_rate=mic_cfg.get("sample_rate", 48000),
                               channels=mic_cfg.get("channels", 2),
                               blocksize=mic_cfg.get("blocksize", 480),
                               endpointer=Endpointer(
                                   aggressiveness=self.cfg.get("vad", "aggressiveness", default=1),
                                   speech_start_ms=self.cfg.get("vad", "speech_start_ms", default=180),
                                   silence_timeout_s=self.cfg.get("vad", "silence_timeout_s", default=1.2),
                                   max_utterance_s=self.cfg.get("vad", "max_utterance_s", default=30.0)),
                               gain=float(mic_cfg.get("gain", 4.0)),
                               min_rms=float(mic_cfg.get("min_rms", 0.0008)))
        self._beep_start = self._make_beep(880.0, 0.08)
        self._beep_end = self._make_beep(660.0, 0.06)
        self._barge = BargeInDetector(trigger_ms=int(self.cfg.get("vad", "barge_in", default=True)) and 150 or 999999)
        self._barge_stream = None
        if self.cfg.get("vad", "barge_in", default=True):
            try:
                self._barge_stream = sd.InputStream(
                    samplerate=mic_cfg.get("sample_rate", 48000),
                    channels=mic_cfg.get("channels", 1),
                    device=mic_cfg.get("device"),
                    blocksize=mic_cfg.get("blocksize", 480),
                    dtype="float32")
                self._barge_stream.start()
                log_event("barge_in.active")
            except Exception as exc:
                self._barge_stream = None
                log_event("barge_in.unavailable", error=str(exc)[:160])
        # hotkeys
        if not self.no_hotkey:
            hk = HotkeyListener(hold_key=self.cfg.get("hotkey", "hold_key", default="ctrl_r"),
                                tap_cancel=self.cfg.get("hotkey", "tap_cancel", default="esc"),
                                alt_hold_key=self.cfg.get("hotkey", "alt_hold_key", default=""))
            self._ptt_supported = hk.start(self._emit_hotkey)
            if self._ptt_supported:
                log_event("hotkey.active", hold=self.cfg.get("hotkey", "hold_key", default="ctrl_r"))
            self.hotkey = hk
    def _emit_hotkey(self, event: str, key: str) -> None:
        if event == "ptt_start":
            self._press_time = time.time()
            self._ptt_hold = True
            self._ptt_started.set()
            self._ptt_released.clear()
        elif event == "ptt_end":
            self._ptt_hold = False
            self._ptt_released.set()
            if self._toggle_armed:
                # second tap during a toggle capture -> stop listening
                self._toggle_stop.set()
        elif event == "cancel":
            self._cancel.set()
            self._toggle_stop.set()
            try:
                self.playback.stop()
            except Exception:
                pass
            if self.task.active:
                self._abort_task("cancelled by hotkey")
    def stop(self) -> None:
        self._stop.set()
        if self._mic:
            self._mic.close()
        if self._barge_stream is not None:
            try:
                self._barge_stream.stop()
                self._barge_stream.close()
            except Exception:
                pass
            self._barge_stream = None
        if self._duplex:
            self._duplex.stop()
        if self.cfg.get("memory", "save_notes", default=True):
            self._save_session_note("voice bridge session ended")
        if self._gateway:
            self._gateway.disconnect()
        if hasattr(self, "hotkey"):
            self.hotkey.stop()
        self.playback.stop()

    # ------------------------------------------------------------------ speak
    def speak(self, text: str, name: str = "assistant", interruptible: bool = True,
              barge: bool = True) -> bool:
        """Synthesize + play with optional barge-in monitoring. Returns True if finished."""
        if not text or self.muted:
            return True
        if self._tts is None:
            log.warning("TTS not available; would say: %s", text[:120])
            return False
        path = self._tts.synthesize(text, Path("/tmp/openclaw-voice-bridge") / f"tts-{uuid.uuid4().hex[:10]}.wav")
        log_event("tts.speak", text=text[:300], provider=self._tts.name)
        self._cancel.clear()
        self._barge.reset()
        if not barge or self._barge_stream is None or self._barge is None:
            return self.playback.play(path, name=name)
        # monitor the always-open barge-in stream while playing
        stop = threading.Event()
        splitter = FrameSplitter()

        def _monitor():
            while not stop.is_set():
                try:
                    data, _ = self._barge_stream.read(self._barge_stream.blocksize)
                    if self._barge_stream.channels > 1:
                        data = data.mean(axis=1, keepdims=True)
                    mono = data[:, 0]
                    from .audio.vad import FRAME_RATE, resample_linear
                    mono16 = resample_linear(mono, self._barge_stream.samplerate, FRAME_RATE)
                    for raw in splitter.feed(mono16):
                        if self._barge.add_frame(raw):
                            stop.set()
                            return
                except Exception:
                    return
        t = threading.Thread(target=_monitor, daemon=True)
        t.start()
        try:
            finished = self.playback.play(path, name=name)
        finally:
            stop.set()
        return finished

    # --------------------------------------------------------------- routing
    def _on_gateway_event(self, event: str, payload: Dict[str, Any]) -> None:
        # Used for future live progress events; keep minimal for now.
        if event in ("session.message", "agent", "chat"):
            log.debug("gw event %s: %s", event, str(payload)[:160])

    def _current_model(self) -> Optional[str]:
        return self.cfg.get("openclaw", "model")

    def _dispatch_coding(self, task_text: str, model: Optional[str] = None,
                         force_confirm: bool = False) -> None:
        """Safety-check and dispatch a coding task to OpenClaw."""
        verdict = self.safety.check(task_text)
        log_event("safety.verdict", text=task_text[:200], allowed=verdict.allowed,
                  deny=verdict.deny, require_confirm=verdict.require_confirm,
                  dry_run=verdict.dry_run, reason=verdict.confirm_reason or verdict.deny_reason)

        if verdict.blocked:
            self.speak(f"I won't do that. {verdict.deny_reason}.")
            return

        final_text = task_text
        if verdict.dry_run:
            final_text = verdict.dry_run_instruction + "\n\n" + task_text

        if self.dry_run:
            self.speak(f"Dry run mode: I would send this task to OpenClaw: {trim_spoken(final_text, 300)}")
            return

        if verdict.require_confirm or force_confirm:
            self.confirmations.request(
                action_desc=trim_spoken(final_text, 260),
                payload={"task": final_text, "model": model or self._current_model(),
                         "dry_run": verdict.dry_run},
                reason=verdict.confirm_reason or "user-confirmed dispatch")
            self.speak(self.safety.spoken_confirmation(trim_spoken(task_text, 200), verdict))
            return

        self._launch_task(final_text, model or self._current_model())

    def _launch_task(self, task_text: str, model: Optional[str] = None) -> None:
        # skills nudge: remind the agent to use its full skill/tool set
        if self.cfg.get("openclaw", "skills_nudge", default=True):
            task_text = (task_text
                         + "\n\nUse your available skills and tools as needed "
                           "(files, terminal, browser, repos, delegation).")
        suffix = uuid.uuid4().hex[:8]
        session_key = self.cfg.session_key(f"task-{suffix}")
        log_event("openclaw.dispatch", session_key=session_key, task=task_text[:300],
                  model=model, route=self._route)

        if self._route == "gateway" and self._gateway:
            try:
                self.speak("On it.", name="ack", barge=False)
            except Exception:
                pass
            # create session + send in one RPC (message nested)
            try:
                res = self._gateway.create_session(
                    key=session_key, label=f"voice task {suffix}", model=model,
                    message=task_text, timeout=45.0)
                run_id = res.get("runId") or ""
                if not run_id:
                    # session exists but run didn't start -> send explicitly
                    res2 = self._gateway.send_message(session_key, task_text, timeout=45.0)
                    run_id = res2.get("runId") or ""
                log_event("openclaw.request", session_key=session_key, task=task_text[:300],
                          model=model, route=self._route, run_id=run_id)
                self.task.set_active(session_key, run_id, task_text, model or "")
                if self.cfg.get("openclaw", "progress_updates", default=True):
                    self._progress_thread = threading.Thread(target=self._progress_loop,
                                                             daemon=True)
                    self._progress_thread.start()
                # wait for completion
                self._await_run(session_key, run_id, task_text)
            except GatewayError as exc:
                log_event("openclaw.request.failed", error=exc.message[:200])
                self.speak("OpenClaw rejected the task. Check the logs.")
                return
            except Exception as exc:
                log_event("openclaw.request.exception", error=str(exc)[:200])
                self.speak("Something went wrong talking to OpenClaw.")
                return
        elif self._route == "cli" and self._cli:
            try:
                self.speak("On it.", name="ack", barge=False)
            except Exception:
                pass
            result = self._cli.run_task(session_key, task_text, model)
            self.task.set_active(session_key, "", task_text, model or "")
            self._handle_agent_result(session_key, result.reply, result.status, result.elapsed_s)
            self.task.clear()
        else:
            self.speak("OpenClaw is not reachable. Start the gateway and try again.")

    def _progress_loop(self) -> None:
        """Periodic spoken status while a task runs (rate-limited)."""
        interval = float(self.cfg.get("openclaw", "progress_min_interval_s", default=15))
        while self.task.active and not self._stop.is_set():
            time.sleep(interval)
            if self.task.active and not self.playback.playing:
                elapsed = self.task.elapsed_s()
                if elapsed > 30:
                    self.speak("Still working. I'll update you when it's done.",
                               name="progress", barge=False)
                    self.task.last_progress_spoken = time.time()

    def _await_run(self, session_key: str, run_id: str, task_text: str,
                   blocking: Optional[bool] = None) -> None:
        """Track a gateway run; speak the summary when it completes.

        Non-blocking by default in the interactive loop (the PTT loop must stay
        responsive). `blocking=True` (CLI task/once/roundtrip) waits for the
        terminal state before returning.
        """
        if blocking is None:
            blocking = self.blocking_tasks
        t0 = time.time()
        self._spawn_waiter(session_key, run_id, t0)
        if blocking:
            while self.task.active and not self._stop.is_set():
                time.sleep(0.5)

    def _spawn_waiter(self, session_key: str, run_id: str, t0: float) -> None:
        """Background poller (waiter + reaper in one).

        Polls agent.wait with 60 s windows up to task_timeout_s, then switches
        to 5-minute reaper windows indefinitely while the task is still active.
        On completion it fetches the reply (layered lookup), speaks the summary,
        and clears the task. Checks the voice-cancel flag between windows.
        """
        import threading

        def _waiter():
            task_timeout = int(self.cfg.get("openclaw", "task_timeout_s", default=900))
            timeout_spoken = False
            while self.task.active and not self._stop.is_set():
                if self._cancel.is_set():
                    self._cancel.clear()
                    if self._gateway is not None:
                        try:
                            self._gateway.abort(session_key, timeout=20.0)
                        except Exception:
                            pass
                    log_event("openclaw.abort", session_key=session_key, run_id=run_id,
                              why="voice cancel")
                    self.task.clear()
                    self.speak("Stopped.")
                    return
                remaining = task_timeout - (time.time() - t0)
                window_ms = 60_000 if remaining > 0 else 300_000
                try:
                    res = self._gateway.wait_run(run_id, timeout_ms=window_ms)
                except GatewayError as exc:
                    log_event("openclaw.result", session_key=session_key, run_id=run_id,
                              status="failed", error=exc.message,
                              elapsed_s=round(time.time() - t0, 2))
                    self.task.clear()
                    self.speak("OpenClaw failed the task. Check the logs.")
                    return
                except Exception as exc:
                    log_event("openclaw.result", session_key=session_key, run_id=run_id,
                              status="failed", error=str(exc)[:200],
                              elapsed_s=round(time.time() - t0, 2))
                    self.task.clear()
                    self.speak("OpenClaw failed the task. Check the logs.")
                    return
                st = res.get("status", "ok")
                if st == "timeout":
                    if remaining <= 0 and not timeout_spoken:
                        timeout_spoken = True
                        log_event("openclaw.result", session_key=session_key, run_id=run_id,
                                  status="timeout", elapsed_s=round(time.time() - t0, 2))
                        self.speak("The task is still running in OpenClaw. "
                                   "I'll keep tracking it and let you know when it's done.")
                        self.task.last_progress_spoken = time.time()
                    continue  # still going; poll again
                if st == "ok":
                    reply = self._fetch_final_reply(session_key, run_id)
                    self.task.clear()
                    self._handle_agent_result(session_key, reply, "ok",
                                              time.time() - t0)
                else:
                    log_event("openclaw.result", session_key=session_key, run_id=run_id,
                              status=st, elapsed_s=round(time.time() - t0, 2))
                    self.task.clear()
                    self.speak(f"OpenClaw finished the task with status {st}. Check the logs.")
                return
            self.task.clear()

        threading.Thread(target=_waiter, daemon=True, name="task-waiter").start()
        log_event("openclaw.waiter.started", session_key=session_key, run_id=run_id)

    def _extract_assistant_reply(self, messages: list) -> str:
        """Pull the last meaningful assistant text from a transcript."""
        parts = []
        for m in messages:
            if m.get("role") == "assistant":
                c = m.get("text") or m.get("content") or ""
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in c)
                c = str(c).strip()
                # skip pure tool-call noise / silent markers
                if c and c.lower() not in ("no_reply", "no reply") and not c.startswith("<tool"):
                    parts.append(c)
        # prefer the last substantial message, but include the previous one as context
        return "\n".join(parts[-2:])

    def _fetch_final_reply(self, session_key: str, run_id: str = "") -> str:
        """Layered reply lookup: parent history -> child sessions -> task ledger.

        The gateway sometimes runs tool-heavy work in subagent/embedded sessions
        whose transcript never lands on the parent session (observed in tests:
        run status=ok, parent history contains only the user message). This walks
        the known sources before giving up.
        """
        # 1. parent history
        try:
            msgs = self._gateway.history(session_key, limit=30, timeout=45.0)
            reply = self._extract_assistant_reply(msgs)
            if reply.strip():
                return reply
        except Exception as exc:
            log_event("openclaw.reply.parent_failed", error=str(exc)[:160])

        # 2. child sessions (rows whose parentSessionKey points at our session)
        try:
            rows = self._gateway.list_sessions(limit=200, timeout=45.0)
            children = [r for r in rows if (r.get("parentSessionKey") or "") == session_key
                        or (run_id and r.get("spawnedBy") == run_id)]
            for child in children[:5]:
                ckey = child.get("key") or ""
                if not ckey:
                    continue
                try:
                    msgs = self._gateway.history(ckey, limit=20, timeout=30.0)
                    reply = self._extract_assistant_reply(msgs)
                    if reply.strip():
                        log_event("openclaw.reply.child", child_key=ckey)
                        return reply
                except Exception:
                    continue
        except Exception as exc:
            log_event("openclaw.reply.children_failed", error=str(exc)[:160])

        # 3. task ledger (owner/child session keys)
        try:
            tasks = self._gateway.list_tasks(timeout=45.0)
            for t in tasks:
                blob = json.dumps(t)
                if session_key not in blob and (not run_id or run_id not in blob):
                    continue
                ckey = t.get("childSessionKey") or t.get("ownerKey") or t.get("sessionKey")
                if not ckey or ckey == session_key:
                    continue
                try:
                    msgs = self._gateway.history(ckey, limit=20, timeout=30.0)
                    reply = self._extract_assistant_reply(msgs)
                    if reply.strip():
                        log_event("openclaw.reply.task_ledger", child_key=ckey)
                        return reply
                except Exception:
                    continue
        except Exception as exc:
            log_event("openclaw.reply.ledger_failed", error=str(exc)[:160])

        return ""

    def _handle_agent_result(self, session_key: str, reply: str, status: str,
                             elapsed_s: float) -> None:
        log_event("openclaw.result", session_key=session_key, status=status,
                  elapsed_s=round(elapsed_s, 2), reply_len=len(reply))
        if not reply.strip():
            self.speak("OpenClaw finished the task. I couldn't pull the final "
                       "text from the session — check the dashboard for details, "
                       "or ask me what it did.")
            return
        summary = summarize(reply, self.cfg.section("summarizer"))
        log_event("openclaw.summary", session_key=session_key, summary=summary[:400])
        self.speak(summary)

    # ----------------------------------------------------------- system cmds
    def _handle_system(self, intent: Intent) -> None:
        cmd = intent.params.get("command")
        if cmd == "status":
            route = self._route
            model = self.cfg.get("openclaw", "model") or (self.gateway_info.default_model if self.gateway_info else "unknown")
            duplex = self._duplex.state if self._duplex else "off"
            self.speak(f"Voice bridge is running. Route: {route}. "
                       f"PersonaPlex: {duplex}. Agent model: {model}.")
        elif cmd == "mute":
            self.muted = True
            self.playback.stop()
            log_event("system.mute")
        elif cmd == "unmute":
            self.muted = False
            self.speak("Unmuted.")
        elif cmd == "help":
            self.speak("Hold the talk key, say a task for OpenClaw, like: "
                       "inspect the webhook flow and patch the signature check. "
                       "Say stop to cancel. Say status for health.")
        elif cmd == "stop":
            self.playback.stop()
            self._abort_task("voice stop")
            self.speak("Stopped.")
        elif cmd == "cancel":
            self.confirmations.clear()
            self._abort_task("voice cancel")
            self.speak("Cancelled.")
        elif cmd == "model":
            tier = intent.params.get("tier")
            model = self.classifier.resolve_model_tier(tier)
            self.cfg.data.setdefault("openclaw", {})["model"] = model
            log_event("system.model", tier=tier, model=model)
            if model:
                self.speak(f"Using model {model.split('/')[-1]} for future tasks.")
            else:
                self.speak("Using the agent's default model for future tasks.")
        elif cmd == "quit":
            self.speak("Goodbye.")
            self._stop.set()

    def _abort_task(self, why: str) -> None:
        if self.task.active:
            key = self.task.session_key
            run_id = self.task.run_id
            if self._route == "gateway" and self._gateway:
                try:
                    self._gateway.abort(key, timeout=20.0)
                except Exception:
                    pass
            self.task.clear()
            log_event("openclaw.abort", why=why, session_key=key, run_id=run_id)

    # ------------------------------------------------------------- confirm
    def _handle_confirm(self) -> None:
        pending = self.confirmations.take()
        if not pending:
            self.speak("There's nothing to confirm.")
            return
        payload = pending.payload
        self.speak("Okay, going ahead.")
        self._launch_task(payload.get("task", ""), payload.get("model"))

    def _handle_cancel_utterance(self) -> None:
        if self.confirmations.pending:
            self.confirmations.clear()
            self.speak("Cancelled.")
            return
        if self.task.active:
            self._abort_task("voice cancel")
            self.speak("Stopping the task.")
        else:
            self.playback.stop()
            self.speak("Cancelled.")

    # ------------------------------------------------------------- conversation
    def _handle_conversation(self, text: str) -> None:
        backend = self.cfg.get("intent", "conversation_backend", default="ollama")
        if backend == "agent" and self._route in ("gateway", "cli"):
            # chat through OpenClaw agent (slower but full memory/tools)
            chat_key = self.cfg.session_key("chat")
            if self._route == "gateway" and self._gateway:
                try:
                    res = self._gateway.create_session(key=chat_key, label="voice chat",
                                                       model=self._current_model())
                    res2 = self._gateway.send_message(chat_key, text, timeout=45.0)
                    run_id = res2.get("runId") or ""
                    self._await_run(chat_key, run_id, text)
                    return
                except Exception as exc:
                    log_event("conversation.agent.failed", error=str(exc)[:160])
            elif self._cli:
                result = self._cli.run_task(chat_key, text, self._current_model())
                self._handle_agent_result(chat_key, result.reply, result.status, result.elapsed_s)
                return
        # fast chat: direct DeepSeek completion (default), Perplexity for
        # web/current-info questions ("if needed"), local ollama as fallback
        reply = ""
        if backend == "deepseek":
            if self.cfg.get("intent", "perplexity_enabled", default=True) \
                    and (_needs_web(text) or "perplexity" in text.lower()):
                reply = self._chat_perplexity(text)
            if not reply:
                reply = self._chat_deepseek(text)
        if not reply:
            reply = self._chat_ollama(text)
        if reply:
            self.conversation.add("user", text)
            self.conversation.add("assistant", reply)
            self.speak(trim_spoken(reply, 500))
        else:
            self.speak("I'm sorry, I couldn't reach the chat model.")

    def _chat_deepseek(self, text: str) -> str:
        """Fast plain chat completion via the DeepSeek API (no agent bootstrap)."""
        import urllib.request
        key = self._deepseek_key()
        if not key:
            log_event("conversation.deepseek.nokey",
                      hint="set DEEPSEEK_API_KEY in ~/.hermes/.env")
            return ""
        url = self.cfg.get("intent", "deepseek_url",
                           default="https://api.deepseek.com/chat/completions")
        model = self.cfg.get("intent", "deepseek_model", default="deepseek-v4-flash")
        ctx = self.conversation.as_prompt()
        system = ("You are a concise, warm voice assistant. Answer briefly in "
                  "1-3 short sentences unless asked for detail.")
        payload = {
            "model": model, "stream": False, "temperature": 0.4,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",
                 "content": f"Recent conversation:\n{ctx}\n\nUser: {text}"},
            ],
        }
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode())
            content = body["choices"][0]["message"]["content"]
            return (content or "").strip()
        except Exception as exc:
            log_event("conversation.deepseek.failed", error=str(exc)[:160])
            return ""

    def _deepseek_key(self) -> str:
        return self._env_key("DEEPSEEK_API_KEY")

    def _perplexity_key(self) -> str:
        return self._env_key("PERPLEXITY_API_KEY")

    def _env_key(self, name: str) -> str:
        """Read a KEY from ~/.hermes/.env (or environment), never logging it."""
        env_path = Path(self.cfg.get("intent", "deepseek_env",
                                     default="~/.hermes/.env")).expanduser()
        try:
            for line in env_path.read_text(errors="replace").splitlines():
                line = line.strip()
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
        return os.environ.get(name, "")

    def _chat_perplexity(self, text: str) -> str:
        """Web-grounded answer via the Perplexity API (for 'if needed' queries)."""
        import urllib.request
        key = self._perplexity_key()
        if not key:
            log_event("conversation.perplexity.nokey",
                      hint="set PERPLEXITY_API_KEY in ~/.hermes/.env")
            return ""
        url = self.cfg.get("intent", "perplexity_url",
                           default="https://api.perplexity.ai/chat/completions")
        model = self.cfg.get("intent", "perplexity_model", default="sonar-pro")
        system = ("You are a concise voice assistant answering from current web "
                  "search. Give a short, accurate, up-to-date answer in 1-4 "
                  "sentences. Prefer facts over hedging.")
        payload = {
            "model": model, "stream": False, "temperature": 0.3,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
        }
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                body = json.loads(resp.read().decode())
            content = body["choices"][0]["message"]["content"]
            log_event("conversation.perplexity", model=model)
            return (content or "").strip()
        except Exception as exc:
            log_event("conversation.perplexity.failed", error=str(exc)[:160])
            return ""

    def _chat_ollama(self, text: str) -> str:
        import urllib.request
        url = self.cfg.get("intent", "ollama_url", default="http://127.0.0.1:11434")
        model = self.cfg.get("intent", "ollama_model", default="qwen3-vl:8b")
        ctx = self.conversation.as_prompt()
        prompt = f"Recent conversation:\n{ctx}\n\nUser: {text}\n\nAssistant:"
        payload = {"model": model, "prompt": prompt, "stream": False,
                   "options": {"think": False, "temperature": 0.4}}
        try:
            req = urllib.request.Request(f"{url}/api/generate",
                                         data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode())
            return (body.get("response") or "").strip()
        except Exception as exc:
            log_event("conversation.ollama.failed", error=str(exc)[:160])
            return ""

    # ------------------------------------------- extra backends (duplex/local/hermes)
    def _duck_pp(self) -> None:
        if self._duplex:
            self._duplex.duck()

    def _unduck_pp(self) -> None:
        if self._duplex:
            self._duplex.unduck()

    def _handle_local(self, intent: Intent, text: str) -> None:
        """Whitelisted direct PC action (volume, apps, screenshot, ...)."""
        if not self.cfg.get("local", "enabled", default=True):
            self.speak("Local actions are disabled; routing to OpenClaw.")
            self._dispatch_coding(text)
            return
        name = intent.params.get("action")
        params = intent.params.get("action_params") or {}
        if not name:
            # LLM said "local" but no action matched — ask for the direct phrasing
            self.speak("That sounds like a PC action. Try a direct command, "
                       "like: mute the microphone, or open the terminal.")
            return
        m = local_actions.ActionMatch(name=name, params=params, text=text)
        msg = local_actions.execute(m)
        self.speak(msg)

    def _handle_hermes(self, task: str) -> None:
        """Explicit Hermes delegation (ask Hermes to ...)."""
        if self._hermes is None:
            self.speak("Hermes is not enabled; routing to OpenClaw instead.")
            self._dispatch_coding(task)
            return
        self.speak("Asking Hermes.", name="ack", barge=False)
        try:
            reply = self._hermes.run_task(task)
        except TimeoutError:
            self.speak("Hermes timed out. Try again, or ask me instead.")
            return
        except Exception as exc:
            self.speak(f"Hermes failed: {str(exc)[:120]}")
            return
        summary = summarize(reply, self.cfg.section("summarizer"))
        self.speak(summary)

    def _route_intent(self, intent: Intent, text: str) -> None:
        """Shared dispatcher used by mic, typed input, and the duplex tap."""
        if intent.kind == "system":
            self._handle_system(intent)
        elif intent.kind == "confirm":
            self._handle_confirm()
        elif intent.kind == "cancel":
            self._handle_cancel_utterance()
        elif intent.kind == "coding":
            self._dispatch_coding(intent.task_text or text)
        elif intent.kind == "local":
            self._handle_local(intent, text)
        elif intent.kind == "hermes":
            self._handle_hermes(intent.task_text or text)
        elif intent.kind == "clarify":
            self.speak("Could you be more specific about what you'd like me to do?")
        else:
            self._handle_conversation(text)

    def _on_duplex_transcript(self, text: str, confidence: float) -> None:
        """A tapped utterance while the PersonaPlex lane is live."""
        if self._stop.is_set():
            return
        log_event("routing.input", text=text, confidence=round(confidence, 3),
                  source="duplex-tap")
        intent = self.classifier.classify(text)
        log_event("routing.decision", text=text[:200], kind=intent.kind,
                  confidence=round(intent.confidence, 2), reason=intent.reason,
                  source=intent.source)
        self.conversation.add("user", text)
        if intent.kind == "conversation":
            # PersonaPlex is already answering in character — stay out of the way.
            return
        # Everything else: duck PersonaPlex so it can't answer for work it
        # cannot do, route the task, then resume the voice lane.
        self._duck_pp()
        try:
            self._route_intent(intent, text)
        finally:
            self._unduck_pp()

    # ----------------------------------------------------------------- memory
    def _load_memory_context(self) -> str:
        """Read MEMORY.md + recent daily notes from the OpenClaw workspace."""
        try:
            ws = Path(self.cfg.get("memory", "workspace",
                                   default="~/.openclaw/workspace")).expanduser()
            budget = int(self.cfg.get("memory", "context_chars", default=1500))
            parts: list[str] = []
            mem = ws / "MEMORY.md"
            if mem.exists():
                parts.append(f"<MEMORY.md>\n{mem.read_text(errors='replace')[:budget]}")
            daily_dir = ws / "memory"
            if daily_dir.exists():
                recent = sorted(daily_dir.glob("*.md"))[-2:]
                for p in recent:
                    parts.append(f"<{p.name}>\n{p.read_text(errors='replace')[:budget]}")
            ctx = "\n\n".join(parts)
            if ctx:
                log_event("memory.loaded", chars=len(ctx))
            return ctx
        except Exception as exc:
            log_event("memory.load.error", error=str(exc)[:160])
            return ""

    def _save_session_note(self, note: str) -> None:
        try:
            ws = Path(self.cfg.get("memory", "workspace",
                                   default="~/.openclaw/workspace")).expanduser()
            day = time.strftime("%Y-%m-%d")
            d = ws / "memory"
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{day}.md"
            stamp = time.strftime("%H:%M")
            with open(path, "a") as fh:
                fh.write(f"\n- {stamp} [voice-bridge] {note}\n")
            log_event("memory.note.saved", path=str(path))
        except Exception as exc:
            log_event("memory.note.error", error=str(exc)[:160])

    # ------------------------------------------------------------------ loop
    def run(self) -> None:
        self.start()
        print("=" * 60)
        print("OpenClaw Voice Bridge — push-to-talk")
        if self._duplex is not None:
            print("  PersonaPlex full-duplex lane: talk naturally; tasks are routed.")
        if self._ptt_supported:
            hold = self.cfg.get("hotkey", "hold_key", default="ctrl_r")
            print(f"  Hold {hold} and speak. Esc cancels. Ctrl+C to quit.")
        else:
            print("  Hotkeys unavailable (no X). Press Enter to start/stop recording.")
        print(f"  ASR: {self._asr.name}  |  TTS: {self._tts.name}  |  OpenClaw route: {self._route}")
        print("=" * 60)

        if self._duplex is not None:
            self._run_duplex_loop()
            return

        if self.mic_only:
            self._record_once_interactive()
            return

        # hotkey mode
        if self._ptt_supported:
            self._run_hotkey_loop()
        else:
            self._run_manual_loop()

    def _run_duplex_loop(self) -> None:
        """Passive loop while the PersonaPlex lane owns conversation.

        State-aware: when the lane is live the bridge stays passive (PersonaPlex
        answers chat; the whisper tap routes tasks). When the lane is down or
        reconnecting, the bridge falls back to the local push-to-talk pipeline
        so the mic keeps working even with the Vast.ai box powered off.
        """
        hold = self.cfg.get("hotkey", "hold_key", default="ctrl_r")
        print("Full-duplex PersonaPlex active — say tasks naturally, e.g.")
        print('  "open the current project and inspect the Stripe webhook validation"')
        print('  "what do you think about this marketplace idea?"')
        print("Type 'status' for health, 'quit' to exit.")
        stdin_q: "queue.Queue[str]" = queue.Queue()

        def _stdin_reader():
            while not self._stop.is_set():
                try:
                    line = input().strip().lower()
                except (EOFError, KeyboardInterrupt):
                    return
                stdin_q.put(line)

        threading.Thread(target=_stdin_reader, daemon=True, name="pp-stdin").start()
        fallback = False
        while not self._stop.is_set():
            lane_live = self._duplex is not None and self._duplex.live
            if lane_live and fallback:
                fallback = False
                print("\n[PersonaPlex is back — full-duplex mode resumed]")
                self.speak("PersonaPlex is back.")
            if not lane_live and not fallback:
                fallback = True
                print(f"\n[PersonaPlex offline — local push-to-talk fallback active. "
                      f"Hold {hold} (or press Enter) to talk.]")
                self.speak("PersonaPlex is offline. Using the local voice model.")
            if lane_live:
                try:
                    line = stdin_q.get(timeout=1.0)
                except queue.Empty:
                    continue
                self._duplex_typed_command(line)
            else:
                self._duplex_fallback_tick(stdin_q)

    def _duplex_typed_command(self, line: str) -> None:
        if line in ("q", "quit", "exit"):
            self._stop.set()
        elif line in ("status", "s", "health"):
            self._handle_system(Intent("system", params={"command": "status"}))
        elif line == "duck":
            self._duck_pp()
            print("PersonaPlex ducked")
        elif line == "unduck":
            self._unduck_pp()
            print("PersonaPlex resumed")
        elif line.startswith("task "):
            text = line[5:].strip()
            if text:
                self._duck_pp()
                try:
                    self._route_intent(self.classifier.classify(text), text)
                finally:
                    self._unduck_pp()

    def _duplex_fallback_tick(self, stdin_q: "queue.Queue[str]") -> None:
        """One local-PTT utterance (or typed command), watching for lane recovery."""
        while not self._stop.is_set() and (self._duplex is None or not self._duplex.live):
            if self._ptt_supported and self._ptt_started.is_set():
                self._ptt_started.clear()
                self._ptt_released.clear()
                res = self._capture_utterance()
                if res is not None:
                    self._process_utterance(res)
                return
            try:
                line = stdin_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if line == "":  # Enter = manual PTT toggle (no-hotkey mode)
                self._ptt_released.clear()
                res = self._capture_utterance()
                if res is not None:
                    self._process_utterance(res)
                return
            if line in ("q", "quit", "exit"):
                self._stop.set()
                return
            if line in ("status", "s"):
                self._handle_system(Intent("system", params={"command": "status"}))
                return
            if line.startswith("task "):
                text = line[5:].strip()
                if text:
                    self._duck_pp()
                    try:
                        self._route_intent(self.classifier.classify(text), text)
                    finally:
                        self._unduck_pp()
                return

    def _run_hotkey_loop(self) -> None:
        """Hold-to-talk loop. LEVEL-based on _ptt_hold (not one-shot flags):
        a press that arrives while the loop is busy is dropped cleanly instead
        of racing with flag clears and starting a 30s silent capture."""
        while not self._stop.is_set():
            # wait for a genuine hold
            while not self._stop.is_set() and not self._ptt_hold:
                time.sleep(0.02)
            if self._stop.is_set():
                break
            res = self._capture_utterance()
            if res is not None:
                self._process_utterance(res)
            # drain: don't instantly re-trigger if the key is still held
            while not self._stop.is_set() and self._ptt_hold:
                time.sleep(0.02)

    def _run_manual_loop(self) -> None:
        print("Manual mode: press Enter to start recording, Enter again to stop.")
        while not self._stop.is_set():
            try:
                line = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                break
            if line in ("q", "quit", "exit"):
                break
            self._ptt_released.clear()
            res = self._capture_utterance()
            if res is not None:
                self._process_utterance(res)

    def _record_once_interactive(self) -> None:
        res = self._capture_utterance()
        if res is not None:
            self._process_utterance(res)

    @staticmethod
    def _make_beep(freq: float = 880.0, dur_s: float = 0.08) -> Path:
        """Generate a short sine beep WAV (cached) for capture start/end feedback."""
        path = Path("/tmp/openclaw-voice-bridge") / f"beep-{int(freq)}-{int(dur_s*1000)}.wav"
        if path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        sr = 22050
        n = int(sr * dur_s)
        fade = int(sr * 0.01)
        t = np.arange(n) / sr
        sig = np.sin(2 * np.pi * freq * t) * 0.25
        env = np.ones(n)
        env[:fade] = np.linspace(0, 1, fade)
        env[-fade:] = np.linspace(1, 0, fade)
        pcm = (sig * env * 32767).astype(np.int16)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm.tobytes())
        return path

    def _play_beep(self, which: str) -> None:
        if not self.cfg.get("feedback", "beeps", default=True):
            return
        path = self._beep_start if which == "start" else self._beep_end
        try:
            self.playback.play(path, name=f"beep-{which}", interruptible=False)
        except Exception:
            pass

    def _capture_utterance(self) -> Optional[str]:
        """Record one utterance (PTT start..release or manual). Returns wav path or None."""
        if self._mic is None:
            log.error("mic not initialized")
            return None
        mic = self._mic
        stop_req = threading.Event()
        if self._ptt_supported:
            held = self._ptt_hold  # snapshot at capture start
            press_t = self._press_time
            tap_ms = int(self.cfg.get("hotkey", "tap_ms", default=300))
            toggle_no_speech_s = float(self.cfg.get("hotkey", "toggle_no_speech_s", default=5.0))
            # reset per-capture toggle state
            self._toggle_armed = False
            self._toggle_stop.clear()

            def _release_watcher():
                # wait until the key is actually released (level, not one-shot)
                while held and self._ptt_hold and not self._stop.is_set():
                    time.sleep(0.02)
                held_s = time.time() - press_t
                if held_s < tap_ms / 1000.0:
                    # TAP -> toggle mode: keep listening until a second tap,
                    # the silence endpoint, or a no-speech timeout.
                    self._toggle_armed = True
                    log_event("hotkey.toggle", on=True)
                    no_speech_t0 = time.time()
                    while not self._stop.is_set():
                        if self._toggle_stop.is_set():
                            log_event("hotkey.toggle", on=False, why="second tap")
                            break
                        if mic.endpointer.state == "done":
                            break
                        if mic.endpointer.state == "idle" and \
                                time.time() - no_speech_t0 > toggle_no_speech_s:
                            log_event("hotkey.toggle", on=False, why="no speech")
                            break
                        time.sleep(0.05)
                    stop_req.set()
                    return
                # HOLD -> stop shortly after release (catch the last word)
                time.sleep(0.6)
                stop_req.set()
            threading.Thread(target=_release_watcher, daemon=True).start()
        self._play_beep("start")
        try:
            with self._mic.open():
                res = self._mic.record_utterance(stop_req, barge_in=None)
        except Exception as exc:
            log_event("mic.capture.error", error=str(exc)[:160])
            self.speak("I couldn't hear you. Check the microphone.")
            return None
        self._play_beep("end")
        if res.wav_path is None:
            log_event("mic.utterance.empty", ended_by=res.ended_by,
                      peak_rms=round(res.peak_rms, 5),
                      hint="no audible speech (level below min_rms)")
            if not self.muted:
                self.speak("I didn't hear anything. Speak up, or check the "
                           "microphone gain — then try again.")
            return None
        log_event("mic.utterance", wav=str(res.wav_path),
                  duration_s=round(res.duration_s, 2),
                  ended_by=res.ended_by, peak_rms=round(res.peak_rms, 5))
        return str(res.wav_path)

    def _process_utterance(self, wav_path: str) -> None:
        t0 = time.time()
        if self._asr is None:
            return
        transcript = self._asr.transcribe(wav_path)
        elapsed_asr = time.time() - t0
        log_event("orchestrator.timing", asr_ms=round(elapsed_asr * 1000, 1), text=transcript.text[:200])
        if transcript.empty:
            self.speak("I didn't catch that. Try again.")
            return
        if transcript.confidence and transcript.confidence < self.cfg.get("asr", "min_confidence", default=0.35):
            self.speak("Sorry, I didn't catch that. Could you repeat it?")
            return
        log_event("routing.input", text=transcript.text, confidence=round(transcript.confidence, 3))
        if transcript.text.strip().lower() in ("uh", "um", "hmm", "ah", "mm", "aah", "okay", "ok"):
            self.speak("I didn't catch that. Try again.")
            return
        intent = self.classifier.classify(transcript.text)
        log_event("routing.decision", text=transcript.text[:200], kind=intent.kind,
                  confidence=round(intent.confidence, 2), reason=intent.reason,
                  source=intent.source)
        self.conversation.add("user", transcript.text)
        self._route_intent(intent, transcript.text)
        log_event("orchestrator.turn_done", total_ms=round((time.time() - t0) * 1000, 1))

    # -------------------------------------------------------------- external
    def process_text(self, text: str) -> None:
        """Process a typed task (used by `voicebridge task` and tests)."""
        self.start()
        if not text.strip():
            self.speak("Empty task.")
            return
        log_event("routing.input", text=text, source="text")
        intent = self.classifier.classify(text)
        log_event("routing.decision", text=text[:200], kind=intent.kind,
                  confidence=round(intent.confidence, 2), reason=intent.reason,
                  source=intent.source)
        self._route_intent(intent, text)
