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
import random
import re
import threading
import time
from dataclasses import dataclass, field
import uuid
import wave
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import sounddevice as sd

from .audio.mic import MicCapture
from .audio.playback import Playback
from .audio.vad import BargeInDetector, Endpointer, FrameSplitter
from .config import Config
from .hermes import HermesBackend
from .hotkeys import HotkeyListener
from .intent.classifier import Classifier, Intent
from .intent.state import ConfirmationState, ConversationState, ExecutorChoiceState, StickyRouteState, TaskQueue, TaskState
from .local import actions as local_actions
from .logging_setup import get_logger, log_event
from .openclaw.cli_backend import CliBackend
from .openclaw.discovery import discover_gateway, discover_cuda_libs
from .task_templates import TaskTemplates
from .openclaw.gateway_ws import GatewaySync, GatewayError
from .safety.policy import SafetyPolicy, SafetyVerdict
from .shared_memory import SharedMemory
from .summarizer import summarize, trim_spoken

# Per-request context so Right Ctrl (mic loop, main thread) and dashboard (web
# worker threads) never share _last_web_* globals. Without this, two concurrent
# requests overwrite each other's summary before polling — the "wrong answer" bug.
# Legacy _last_web_* fields stay for backwards compat (other code reads them) but
# the hot path now uses WebCtx.

@dataclass
class _WebCtx:
    """One request's isolated web state. Created per call, never shared."""
    text: str = ""
    summary: str = ""
    route: str = ""
    spoken: List[str] = field(default_factory=list)
    web_mode: bool = True
    done: threading.Event = field(default_factory=threading.Event)
    conv_snapshot: str = ""  # snapshot of conversation.as_prompt() at request start

_thread_ctx: threading.local = threading.local()  # type: ignore[no-redef]
_conv_lock = threading.RLock()  # type: ignore[var-annotated]  # guards ConversationState.add

log = get_logger()


def _hermes_default_model() -> str:
    """Read the default model name from the Hermes config (was in the removed
    personaplex.realtime helper). Returns '' when unavailable."""
    try:
        import os
        import yaml
        p = os.path.expanduser("~/.hermes/config.yaml")
        if os.path.exists(p):
            with open(p) as fh:
                d = yaml.safe_load(fh) or {}
            return str(d.get("model", "") or "")
    except Exception:
        pass
    return ""

BEEP_START = np.array([], dtype=np.int16)   # placeholder; real beeps via piper
_SAMPLE_TASK = (
    "Open the current project, inspect the Stripe webhook flow, and "
    "patch the signature verification and add structured logs."
)

# Boss-talk + humming while thinking (characterful, not spammy)
_BOSS_PHRASES = [
    "I'm on it boss",
    "Starting the task now",
    "Processing your orders",
    "You got it boss",
    "On my way boss",
    "Working on it now",
    "Consider it done boss",
    "Right away boss",
    "Copy that boss",
    "At your service boss",
    "Got it boss, moving~",
    "On it, boss — let's go~",
]

_HUM_PHRASES = [
    "hmm~",
    "*soft hum* hmm hmm~",
    "*whistles softly*~",
    "*whistles a little tune*~",
    "*cute hum* la la~",
    "*little whistle* whew~",
    "still working boss~",
    "almost there boss, hang in there~",
    "still on it boss, I'm watching them for you~",
    "working away here, on it~",
    "hang tight boss, they're cooking~",
    "still cooking boss, smells good~",
    "boss, they're busy — I'm keeping an eye on them~",
    "they're grinding away, boss~",
    "shh, they're thinking hard~",
    "still at it, boss — got my eye on them~",
    "hmm hmm~ still going~",
    "*giggly hum* still here~",
    "yep, still running, boss~",
    "hang on, almost~",
    "working, working~",
    "all good boss, they're on it~",
]


def _boss_phrase() -> str:
    return random.choice(_BOSS_PHRASES)


def _hum_phrase() -> str:
    return random.choice(_HUM_PHRASES)



def _needs_web(text: str) -> bool:
    """Heuristic: does this question want current/web-grounded info (Perplexity)?

    STRICT by design (Aug 2026): casual speech contains "today"/"current"/
    "latest" constantly ("how are you today", "what's our current plan") — a
    loose substring match sends greetings to Perplexity (~2.3s) instead of the
    local LLM (~0.3s), which felt exactly like the old slow stack. Only clear
    web-intent constructions match.
    """
    t = text.lower()
    if any(m in t for m in (
            "search", "look up", "google", "perplexity", "on the web",
            "find out", "do you know about", "what's the latest",
            "what is the latest")):
        return True
    pats = (
        r"\bweather\b", r"\bprice of\b", r"\bstock", r"\bwho won\b",
        r"\bwhat happened\b", r"\bwhen did\b", r"\bhow many people\b",
        r"\belection", r"\bscore\b", r"\brelease date\b", r"\bannouncement",
        r"\bupdate on\b", r"\bnew version\b", r"\brecent\b", r"\bnews\b",
        r"\btoday['’]?s (weather|news|price|headlines|match|game|score|results)\b",
        r"\bcurrent (price|time|weather|events|news|status of)\b",
        r"\blatest (news|version|price|update|release|headlines|results|scores)\b",
    )
    return any(re.search(p, t) for p in pats)


def _interrupt_decision_prompt(old_task: str, new_request: str) -> str:
    """Build the prompt handed to the agent after an interrupt: it decides
    whether to continue the paused work or switch to the user's new request."""
    return (
        "You were working on this task:\n"
        f"{old_task}\n\n"
        "The user interrupted and gave a new request:\n"
        f"{new_request}\n\n"
        "Decide what to do: if the new request continues, refines, or corrects "
        "the current work, CONTINUE the current work and apply the new request "
        "as part of it. If it is a different direction or explicitly replaces "
        "the current work, STOP the current work and do the new request "
        "instead.\n"
        "Then act on your decision and report what you did. Be direct: no "
        "emojis, no pleasantries, only what was done and what's next."
    )


_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # misc symbols & pictographs, emoji, transport
    "\U00002600-\U000027BF"   # misc symbols, dingbats
    "\U0001F1E6-\U0001F1FF"   # flags
    "\U0000FE0F\U0000FE0E"    # variation selectors
    "\U00002702-\U000027B0"
    "]+"
)
_LEADING_FILLER_RE = re.compile(
    r"^\s*(?:(?:sure|absolutely|certainly|yes|yeah|yep|ok|okay|great|"
    r"perfect|no problem|here you go|you're welcome|happy to help|"
    r"glad to|sure thing)\s*[,.!]?\s*)+",
    re.IGNORECASE,
)
_TRAILING_FILLER_RE = re.compile(
    r"\s*(?:let me know (?:if|whether)[^.]*\.?|if (?:you|u) (?:need|want|have) "
    r"(?:anything|more|questions?)[^.]*\.?|hope (?:that )?helps[^.]*\.?|"
    r"have a great day[^.]*\.?|thanks for asking[^.]*\.?|"
    r"is there anything else[^.]*\??|feel free to ask[^.]*\.?)\s*$",
    re.IGNORECASE,
)


def speech_direct(text: str) -> str:
    """Trim spoken output: strip emojis, leading pleasantries, trailing filler.

    Keeps the substance — removes the smiley faces and extra words so TTS
    answers directly with only what's needed.
    """
    out = _EMOJI_RE.sub("", text)
    out = _LEADING_FILLER_RE.sub("", out, count=1)
    out = _TRAILING_FILLER_RE.sub("", out)
    return " ".join(out.split()).strip()


class VoiceOrchestrator:
    def __init__(self, config: Config, mic_only: bool = False,
                 no_hotkey: bool = False, dry_run: bool = False) -> None:
        self.cfg = config
        self.mic_only = mic_only
        self.no_hotkey = no_hotkey or not config.get("hotkey", "enabled", default=True)
        self.dry_run = dry_run  # global force: never dispatch, only speak plan

        # state
        self.conversation = ConversationState(max_turns=int(config.get("conversation", "max_turns", default=8)))
        self.confirmations = ConfirmationState()
        self.executor_choice = ExecutorChoiceState()
        self.sticky_route = StickyRouteState(state_dir=self.cfg.state_dir())
        self.shared = SharedMemory(self.cfg.state_dir())
        # restore recent history into conversation so switching provider / power loss doesn't blank context
        try:
            for _rec in self.shared.recent(limit=max(8, int(config.get("conversation", "max_turns", default=8)))):
                if _rec.get("role") in ("user","assistant","agent") and _rec.get("text"):
                    self.conversation.add(_rec["role"] if _rec["role"]!="agent" else "assistant", _rec["text"][:2000])
            # also show restore in journal so dashboard reflects it
            if self.conversation.turns:
                from .logging_setup import log_event as _le
                _le("shared_memory.restored", turns=len(self.conversation.turns))
        except Exception:
            pass
        self.task = TaskState()
        self.task_queue = TaskQueue()
        # pending "start the queued task?" confirmation (user answers yes/no)
        self._queued_confirm = None
        self.muted = False
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._ptt_started = threading.Event()
        self._ptt_pressed = threading.Event()  # sticky: a press during busy work is NOT lost
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
        self._webui = None
        self._t0 = time.time()
        # PTT state: tap-to-toggle + hold-to-talk
        self._press_time = 0.0
        self._release_time = 0.0      # set on ptt_end; 0 while the key is down
        self._toggle_armed = False       # a tap started a toggle capture
        self._toggle_stop = threading.Event()
        # interrupt/pause lane: Ctrl during a task pauses it; the agent only
        # continues on a spoken say-so (new instruction or "continue")
        self._interrupted_session: Optional[str] = None
        self._interrupted_task_text: str = ""
        self._interrupt_requested = False   # set from the hotkey thread while the
                                            # loop is blocked inside speak()
        self._hermes_busy = False           # a Hermes task is running in the bg
        self._duplex_ptt_pending = False    # duplex mode: Ctrl pressed; next tap
        self._duplex_ptt_until = 0.0        # utterance is the user's instruction
        self.playback = Playback()
        self.classifier: Optional[Classifier] = None
        self._templates: Optional[TaskTemplates] = None
        self.safety = SafetyPolicy(config.section("safety"))
        self.gateway_info = None
        self._route: str = ""   # 'gateway' | 'cli' | 'offline'
        self.blocking_tasks = True   # False in the interactive hotkey loop
        self._duplex: Optional[Any] = None  # PersonaPlexDuplex | RealtimeDuplex
        self._hermes: Optional[HermesBackend] = None
        self._memory_context: str = ""

    # ------------------------------------------------------------------ setup
    def _make_asr(self):
        from .asr.sherpa import SherpaASR
        from .asr.whisper import FasterWhisperASR
        asr_cfg = self.cfg.section("asr")
        provider = asr_cfg.get("provider", "sherpa")
        if provider == "personaplex":
            log_event("asr.provider", provider="personaplex", active=False,
                      detail="PersonaPlex ASR removed; falling back to sherpa")
            provider = "sherpa"
        model_dir = self.cfg.model_dir()
        if provider == "sherpa":
            return SherpaASR(engine=asr_cfg.get("engine", "moonshine"),
                             model_dir=model_dir,
                             provider=asr_cfg.get("provider_ep", "cpu"),
                             num_threads=4,
                             language=asr_cfg.get("language"))
        return FasterWhisperASR(model=asr_cfg.get("model", "base"),
                                device=asr_cfg.get("device", "auto"),
                                compute_type=asr_cfg.get("compute_type", "float16"),
                                language=asr_cfg.get("language"),
                                beam_size=asr_cfg.get("beam_size", 1),
                                model_dir=model_dir)

    def _make_tts(self):
        from .tts.edge import EdgeTTS
        from .tts.gateway_tts import GatewayTTS
        from .tts.kokoro import KokoroTTS
        from .tts.piper import PiperTTS
        provider = self.cfg.get("tts", "provider", default="piper")
        if provider == "edge":
            edge = EdgeTTS(voice=self.cfg.get("tts", "edge", "voice", default="en-US-JennyNeural"),
                           rate=self.cfg.get("tts", "edge", "rate", default="+0%"))
            if edge.available():
                return edge
        if provider == "gateway" and self._gateway is not None:
            return GatewayTTS(self._gateway, model=self.cfg.get("tts", "gateway", "model"))
        if provider == "hybrid":
            from .tts.hybrid import HybridTTS
            h = self.cfg.get("tts", "hybrid", default={}) if isinstance(self.cfg.get("tts", "hybrid", default={}), dict) else {}
            return HybridTTS(model_dir=self.cfg.model_dir(),
                             piper_voice=h.get("piper_voice", self.cfg.get("tts", "voice", default="en_US-amy-medium")),
                             openai_voice=h.get("openai_voice", "nova"),
                             openai_model=h.get("openai_model", "gpt-4o-mini-tts"),
                             key_env=self.cfg.get("tts", "openai", "key_env", default="~/.hermes/.env"),
                             instructions=self.cfg.get("tts", "openai", "instructions", default=""),
                             threshold_words=int(h.get("threshold_words", 18)))
        if provider == "openai":
            from .tts.openai_tts import OpenAITTS
            tts = OpenAITTS(voice=self.cfg.get("tts", "openai", "voice", default="nova"),
                            model=self.cfg.get("tts", "openai", "model", default="gpt-4o-mini-tts"),
                            key_env=self.cfg.get("tts", "openai", "key_env", default="~/.hermes/.env"),
                            instructions=self.cfg.get("tts", "openai", "instructions", default=""))
            if tts.available():
                return tts
            log_event("tts.openai.unavailable", fallback="piper")
        if provider == "kokoro":
            tts = KokoroTTS(model_dir=self.cfg.model_dir(),
                            voice_sid=int(self.cfg.get("tts", "kokoro", "voice_sid", default=0)))
            if tts.available():
                return tts
            log_event("tts.kokoro.unavailable", fallback="piper")
        return PiperTTS(model_dir=self.cfg.model_dir(),
                        voice=self.cfg.get("tts", "voice", default="en_US-amy-medium"))

    def _resolve_silero_model(self) -> Path:
        """Silero v6 preferred (Aug 2025, ~16% fewer errors than v5 on noisy
        real data); fall back to v5 if v6 isn't downloaded yet."""
        model_dir = self.cfg.model_dir()
        v6 = model_dir / "silero_vad_v6.onnx"
        if v6.exists():
            return v6
        v5 = model_dir / "silero_vad_v5.onnx"
        if v5.exists():
            return v5
        return v6  # let the loader report the missing-file error clearly

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

    def _retry_gateway(self) -> None:
        """Best-effort reconnect when the route is offline/stale.

        The bridge starts once and normally keeps its connection; if the
        gateway was down at startup (or its restart drops us), the route can
        stay 'offline'/'cli' forever even after the gateway is healthy again.
        This re-runs discovery so the bridge self-heals without a restart.
        """
        if self._route == "gateway" and self._gateway is not None:
            try:
                if self._gateway.connected:
                    return
            except Exception:
                pass
        log_event("openclaw.gateway.retry", route=self._route)
        try:
            old_cli = self._cli
            new_route = self._connect_gateway()
            if new_route == "gateway":
                log_event("openclaw.gateway.recovered")
            elif old_cli is not None:
                # _connect_cli_only() REPLACED self._cli with a fresh backend
                # that may have failed to connect — restore the working one
                # so we don't lose the CLI fallback on a failed gateway retry
                self._cli = old_cli
                self._route = "cli"
                log_event("openclaw.gateway.retry.cli_kept")
        except Exception as exc:
            log_event("openclaw.gateway.retry.failed", error=str(exc)[:160])

    def _gateway_watchdog(self) -> None:
        """Periodically re-try the gateway when the bridge isn't on it, and
        keep the connection alive (the gateway's keepalive ping timeout was
        killing the WS with 'no close frame received' — websockets ping fixes
        that, watchdog catches the rest)."""
        interval = float(self.cfg.get("openclaw", "reconnect_s", default=20))
        while not self._stop.is_set():
            time.sleep(interval)
            try:
                if self._route != "gateway" or (self._gateway is not None and not self._gateway.connected):
                    self._retry_gateway()
            except Exception:
                pass

    # -------------------------------------------------------------- lifecycle
    def start(self) -> None:
        # gateway first (may inform TTS choice)
        self._connect_gateway()
        # self-heal: if the gateway was down at startup, keep re-trying in the
        # background so the bridge recovers without a restart
        threading.Thread(target=self._gateway_watchdog, daemon=True,
                         name="gateway-watchdog").start()
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

        # daily-task templates (spoken phrase -> known task text; bypasses the
        # model entirely for recurring tasks — deterministic + 0 tokens)
        self._templates = TaskTemplates.load(
            path=self.cfg.get("intent", "templates_path",
                              default="~/.config/openclaw-voice-bridge/task-templates.yaml"),
            defaults_path=os.path.expanduser(
                "~/Projects/Repos/openclaw-voice-bridge/config/task-templates.yaml")
            if Path("~/Projects/Repos/openclaw-voice-bridge/config/task-templates.yaml").expanduser().exists()
            else None)
        if self._templates.items:
            log_event("task_templates.loaded", count=len(self._templates.items))

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

        # PersonaPlex full-duplex lane: REMOVED (user decision 2026-08-21).
        # Voice = local push-to-talk + DeepSeek. The adapter was deleted from
        # the repo; keep this block inert so configs referencing it degrade
        # gracefully instead of crashing.
        pp_cfg = self.cfg.section("personaplex")
        if pp_cfg.get("enabled", False):
            log_event("personaplex.lane", provider="removed",
                      started=False,
                      detail="PersonaPlex removed by user; using local voice")

        # mic
        mic_cfg = self.cfg.section("mic")
        vad_backend = self.cfg.get("vad", "backend", default="silero")
        silero_model = self.cfg.get("vad", "silero_model", default="") \
            or self._resolve_silero_model()
        self._mic = MicCapture(device=mic_cfg.get("device"),
                               sample_rate=mic_cfg.get("sample_rate", 48000),
                               channels=mic_cfg.get("channels", 2),
                               blocksize=mic_cfg.get("blocksize", 480),
                               endpointer=Endpointer(
                                   aggressiveness=self.cfg.get("vad", "aggressiveness", default=1),
                                   speech_start_ms=self.cfg.get("vad", "speech_start_ms", default=180),
                                   silence_timeout_s=self.cfg.get("vad", "silence_timeout_s", default=1.2),
                                   max_utterance_s=self.cfg.get("vad", "max_utterance_s", default=30.0),
                                   rms_gate=float(self.cfg.get("vad", "rms_gate", default=0.0018)),
                                   vad_backend=vad_backend,
                                   silero_model=silero_model),
                               gain=float(mic_cfg.get("gain", 4.0)),
                               min_rms=float(mic_cfg.get("min_rms", 0.0005)))
        self._beep_start = self._make_beep(880.0, 0.08)
        self._beep_end = self._make_beep(660.0, 0.06)
        self._barge = BargeInDetector(
            trigger_ms=int(self.cfg.get("vad", "barge_in", default=True)) and 150 or 999999,
            vad_backend=vad_backend, silero_model=silero_model)
        self._barge_stream = None
        if self.cfg.get("vad", "barge_in", default=True):
            self._start_barge_stream(mic_cfg)
        # speaker verification (voice-ID): gates barge-in on "is this ME?"
        self._speaker = None
        if self.cfg.get("speaker_verify", "enabled", default=True):
            try:
                from .voice_id import SpeakerVerifier
                sv_cfg = self.cfg.section("speaker_verify")
                self._speaker = SpeakerVerifier(
                    self.cfg.model_dir(), self.cfg.state_dir(),
                    name=sv_cfg.get("name", "user"),
                    threshold=float(sv_cfg.get("threshold", 0.75)))
                self._speaker.load()
                if not self._speaker.ready:
                    log_event("speaker.verify.no_enrollment",
                              hint="run `voicebridge enroll`")
            except Exception as exc:
                self._speaker = None
                log_event("speaker.verify.unavailable", error=str(exc)[:160])
        # hotkeys
        if not self.no_hotkey:
            hk = HotkeyListener(hold_key=self.cfg.get("hotkey", "hold_key", default="ctrl_r"),
                                tap_cancel=self.cfg.get("hotkey", "tap_cancel", default="esc"),
                                alt_hold_key=self.cfg.get("hotkey", "alt_hold_key", default=""))
            self._ptt_supported = hk.start(self._emit_hotkey)
            if self._ptt_supported:
                log_event("hotkey.active", hold=self.cfg.get("hotkey", "hold_key", default="ctrl_r"))
            self.hotkey = hk
        # voice dashboard (web UI): talk from the phone + live activity feed
        try:
            wu_cfg = self.cfg.section("webui")
            if wu_cfg.get("enabled", True):
                from .webui import WebUI
                self._webui = WebUI(
                    self,
                    host=wu_cfg.get("host", "0.0.0.0"),
                    port=int(wu_cfg.get("port", 8890)),
                    state_dir=self.cfg.state_dir())
                self._webui.start()
        except Exception as exc:
            self._webui = None
            log_event("webui.start.failed", error=str(exc)[:200])

    def _emit_hotkey(self, event: str, key: str) -> None:
        if event == "ptt_start":
            self._press_time = time.time()
            self._ptt_hold = True
            self._ptt_pressed.set()      # sticky: never lose a press to busy work
            self._ptt_started.set()
            self._ptt_released.clear()
            # duplex take-over: Ctrl = stop PersonaPlex + listen via the lane's
            # own mic tap (never opens a second mic stream -> no conflict)
            if (self._duplex is not None and self._duplex.live
                    and self.cfg.get("personaplex", "ptt_uses_tap", default=True)):
                self._duplex.interrupt_output()
                self._duplex_ptt_pending = True
                self._duplex_ptt_until = time.time() + 12.0
                log_event("personaplex.ptt_pending", until=int(self._duplex_ptt_until))
                return
            # interrupt from the LISTENER thread: the main loop is often
            # blocked inside speak(), so playback must be stopped here so the
            # response is cut off immediately (task pause is handled when the
            # loop picks up _interrupt_requested)
            if self.cfg.get("hotkey", "interrupt_on_ptt", default=True):
                if self.playback.playing:
                    self.playback.stop()
                    self._interrupt_requested = True
                elif self.task.active:
                    self._interrupt_requested = True
        elif event == "ptt_end":
            self._ptt_hold = False
            self._release_time = time.time()   # for accurate tap-vs-hold timing
            self._ptt_released.set()
            if self._toggle_armed:
                # second tap during a toggle capture -> stop listening AND
                # consume the latched press so it does NOT start a fresh
                # capture (that was the "closed too fast" double-beep bug:
                # the stop-tap both stopped the toggle and re-triggered it)
                self._toggle_stop.set()
                self._ptt_pressed.clear()
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

    def _start_barge_stream(self, mic_cfg=None) -> None:
        """Open the barge-in InputStream (one input stream at a time rule).

        The Scarlett tolerates ONE InputStream at a time: a second stream
        opened while this one polls corrupted ALSA's poll descriptors and
        segfaulted PortAudio (snd_pcm_poll_descriptors_revents, intermittent).
        Capture pauses this stream; speak() re-uses it.
        """
        mic_cfg = mic_cfg or self.cfg.section("mic")
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

    def _pause_barge_stream(self) -> bool:
        """Stop+close the barge stream; returns True if it was open (restart it)."""
        if self._barge_stream is None:
            return False
        try:
            self._barge_stream.stop()
            self._barge_stream.close()
        except Exception:
            pass
        self._barge_stream = None
        return True

    # ------------------------------------------------------------------ speak
    def speak(self, text: str, name: str = "assistant", interruptible: bool = True,
              barge: bool = True) -> bool:
        """Synthesize + play with optional barge-in monitoring. Returns True if finished."""
        if not text or self.muted:
            return True
        if self.cfg.get("tts", "direct", default=True):
            text = speech_direct(text)
        if not text:
            return True
        if self._web_mode:
            # Web/phone request: the browser plays the TTS audio (returned via
            # /api/result). DO NOT play through the PC speakers — that both
            # blocks the phone (audio playback takes ~10s for a summary) and
            # blasts the room. Record the text so the web result can use it.
            self._web_spoken.append(text)
            log_event("tts.speak", text=text[:300], provider="web")
            return True
        if self._tts is None:
            log.warning("TTS not available; would say: %s", text[:120])
            return False
        try:
            path = self._tts.synthesize(text, Path("/tmp/openclaw-voice-bridge") / f"tts-{uuid.uuid4().hex[:10]}.wav")
        except Exception as exc:
            # Hot-fallback: OpenAI network/key hiccup must never silence the bridge — fall back to local Piper instantly
            log_event("tts.synthesize.failed", provider=getattr(self._tts, "name", "unknown"), error=str(exc)[:200])
            if getattr(self._tts, "name", "") == "openai":
                try:
                    from .tts.piper import PiperTTS as _FallbackPiper
                    _fb = _FallbackPiper(model_dir=self.cfg.model_dir(), voice=self.cfg.get("tts", "voice", default="en_US-amy-medium"))
                    _fb.load()
                    path = _fb.synthesize(text, Path("/tmp/openclaw-voice-bridge") / f"tts-fb-{uuid.uuid4().hex[:10]}.wav")
                    log_event("tts.fallback", provider="piper", reason=str(exc)[:120])
                except Exception as exc2:
                    log.warning("TTS fallback also failed: %s", exc2)
                    return False
            else:
                raise
        log_event("tts.speak", text=text[:300], provider=self._tts.name)
        self._cancel.clear()
        self._barge.reset()
        if not barge or self._barge_stream is None or self._barge is None:
            return self.playback.play(path, name=name)
        stop = threading.Event()
        splitter = FrameSplitter()

        def _barge_allowed(buf: bytearray) -> bool:
            """True = honor the barge (stop talking). If voice-ID is armed,
            only the ENROLLED voice passes; without it, old behavior (any
            speech barges). Too-short buffers (<1s) default to allowed."""
            if self._speaker is None or not self._speaker.ready:
                return True
            import numpy as np
            samples = np.frombuffer(bytes(buf), dtype=np.int16).astype(np.float32) / 32768.0
            if len(samples) < 16000:  # <1s: can't embed reliably -> don't block
                return True
            try:
                accepted, score = self._speaker.verify(samples, 16000)
            except Exception as exc:
                log_event("barge.verify.error", error=str(exc)[:120])
                return True
            log_event("barge.verify", accepted=bool(accepted), score=round(score, 3))
            return bool(accepted)

        def _monitor():
            from .audio.vad import FRAME_RATE, resample_linear
            buf = bytearray()
            sv_cfg = self.cfg.section("speaker_verify") if self._speaker is not None else {}
            max_buf = int(float(sv_cfg.get("window_s", 2.0)) * FRAME_RATE) * 2
            while not stop.is_set():
                try:
                    data, _ = self._barge_stream.read(self._barge_stream.blocksize)
                    if self._barge_stream.channels > 1:
                        data = data.mean(axis=1, keepdims=True)
                    mono = data[:, 0]
                    mono16 = resample_linear(mono, self._barge_stream.samplerate, FRAME_RATE)
                    for raw in splitter.feed(mono16):
                        buf.extend(raw)
                        del buf[:max(0, len(buf) - max_buf)]
                        if self._barge.add_frame(raw):
                            if _barge_allowed(buf):
                                stop.set()
                                return
                            # impostor (or unverifiable but enrolled system):
                            # reset the detector and keep talking
                            self._barge.reset()
                except Exception:
                    return
        t = threading.Thread(target=_monitor, daemon=True)
        t.start()
        try:
            finished = self.playback.play(path, name=name)
        finally:
            stop.set()
        return finished

    def speak_stream(self, sentences, name: str = "assistant") -> str:
        """Speak a sequence of sentences with synthesis ahead of playback.

        Sentences are synthesized in the caller thread while a background
        player drains them sequentially — the first sentence starts playing as
        soon as it is synthesized instead of waiting for the whole reply.
        Returns the joined text that was actually spoken.
        """
        import queue as _queue
        joined = " ".join(sentences)
        if self._web_mode:
            # web/phone: record for the result, never play on PC speakers
            if joined.strip():
                self._web_spoken.append(joined.strip())
            return joined
        if self.muted or not sentences:
            return " ".join(sentences)
        if self._tts is None:
            log.warning("TTS not available; would say: %s",
                        " ".join(sentences)[:120])
            return ""
        q: "_queue.Queue[Optional[Path]]" = _queue.Queue()
        spoken: list[str] = []

        def _player() -> None:
            while True:
                try:
                    path = q.get(timeout=0.2)
                except _queue.Empty:
                    if self._interrupt_requested:
                        self.playback.stop()
                        break
                    continue
                if path is None:
                    break
                try:
                    if self._interrupt_requested:
                        self.playback.stop()
                        break
                    self.playback.play(path, name=name, interruptible=True)
                except Exception as exc:
                    log.warning("stream speak: %s", exc)

        t = threading.Thread(target=_player, daemon=True, name="stream-speak")
        t.start()
        try:
            for sentence in sentences:
                sentence = (sentence or "").strip()
                if not sentence:
                    continue
                spoken.append(sentence)
                if self._interrupt_requested:
                    break
                try:
                    path = self._tts.synthesize(
                        sentence,
                        Path("/tmp/openclaw-voice-bridge") / f"tts-{uuid.uuid4().hex[:10]}.wav")
                    q.put(path)
                except Exception as exc:
                    log.warning("stream synth: %s", exc)
        finally:
            q.put(None)
            t.join(timeout=15)
        return " ".join(spoken)

    # --------------------------------------------------------------- routing
    def _on_gateway_event(self, event: str, payload: Dict[str, Any]) -> None:
        # Used for future live progress events; keep minimal for now.
        if event in ("session.message", "agent", "chat"):
            log.debug("gw event %s: %s", event, str(payload)[:160])

    def _current_model(self) -> Optional[str]:
        """The model voice tasks run on: explicit config override if set,
        otherwise the agent's ACTUAL default (OpenClaw gateway default /
        tier, or Hermes default) so the voice follows whatever LLM the
        agent is set to."""
        override = self.cfg.get("openclaw", "model")
        if override:
            return override
        if self.gateway_info and self.gateway_info.default_model:
            return self.gateway_info.default_model
        return None

    def _dispatch_local_script(self, tmpl) -> str:
        """Summarize-first workflow: run a local tool, speak ONLY the summary.

        The small local model never sees the raw tool output for speaking —
        the script's stdout is collapsed into a ≤summary_words summary via
        the summarizer (local ollama model or extractive fallback), and that
        summary is what gets spoken. Raw output goes to the activity feed for
        the dashboard, not to the ears.
        """
        script = (tmpl.script or "").strip()
        if not script:
            self.speak("That template is missing its script.")
            return ""
        question = self._last_web_text or ""
        cmd = script.replace("{question}", question.replace("\n", " ")).replace("{text}", question.replace("\n", " "))
        log_event("local_script.run", name=tmpl.name, cmd=cmd[:300])
        try:
            import subprocess
            proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                  timeout=float(self.cfg.get("webui", "script_timeout_s", default=60)))
            raw = (proc.stdout or "").strip()
            if proc.returncode != 0:
                err = (proc.stderr or "").strip()[:300]
                log_event("local_script.failed", name=tmpl.name, rc=proc.returncode, error=err)
                self.speak(f"The search failed. {err}" if err else "The search failed.")
                self._last_web_summary = ""
                return ""
        except subprocess.TimeoutExpired:
            log_event("local_script.timeout", name=tmpl.name)
            self.speak("That took too long and was cancelled.")
            self._last_web_summary = ""
            return ""
        except Exception as exc:
            log_event("local_script.error", name=tmpl.name, error=str(exc)[:160])
            self.speak("Something went wrong running that.")
            self._last_web_summary = ""
            return ""
        if not raw:
            self.speak("I didn't get any result back.")
            self._last_web_summary = ""
            return ""
        # summarize-first: local model compresses to ≤N words, then speak it
        from .summarizer import summarize
        sum_cfg = self.cfg.section("summarizer")
        max_words = int(tmpl.summary_words or sum_cfg.get("max_words", 40))
        summary = summarize(raw, cfg={**sum_cfg, "max_words": max_words},
                            ollama_url=sum_cfg.get("ollama_url"),
                            ollama_model=sum_cfg.get("ollama_model"))
        if not summary:
            summary = clamp_words(raw, max_words)
        # Update both the legacy global and the per-request ctx (if any) so concurrent
        # web requests don't race on _last_web_summary (the wrong-answer bug).
        try:
            ctx = self._cur_ctx()
        except AttributeError:
            ctx = None
        if ctx is not None:
            ctx.summary = summary
            ctx.route = f"local-script:{tmpl.name}"
            ctx.done.set()
        self._last_web_summary = summary
        self._last_web_route = f"local-script:{tmpl.name}"
        log_event("local_script.summary", name=tmpl.name,
                  raw_len=len(raw), summary=summary[:300])
        self.feed_push({"kind": "summary", "text": f"{tmpl.name}", "summary": summary,
                        "route": self._last_web_route, "raw": raw[:50000]})
        self.speak(summary)
        return summary

    _last_web_text: str = ""
    _last_web_summary: str = ""
    _last_web_route: str = ""
    _web_mode: bool = False
    _web_spoken: List[str] = []
    # Lock serializing concurrent requests (Right Ctrl mic loop vs dashboard web threads).
    # Prevents shared-state races on _last_web_*, _web_spoken, conversation, executor_choice.
    _web_lock = threading.RLock()  # type: ignore
    # Active-request counter so the dashboard can show "busy" and the mic loop can wait politely.
    _web_active: int = 0

    # Thread-local per-request ctx helpers (added for non-blocking isolation)
    def _cur_ctx(self) -> "Optional[_WebCtx]":
        return getattr(_thread_ctx, "ctx", None)

    def _new_ctx(self, text: str = "") -> "_WebCtx":
        # Snapshot conversation so concurrent requests don't bleed Romanian/English history
        try:
            with _conv_lock:
                snap = self.conversation.as_prompt()
        except Exception:
            snap = ""
        ctx = _WebCtx(text=text, conv_snapshot=snap)
        _thread_ctx.ctx = ctx  # type: ignore[attr-defined]
        return ctx

    def _clear_ctx(self) -> None:
        try:
            delattr(_thread_ctx, "ctx")  # type: ignore[attr-defined]
        except Exception:
            pass

    def process_web_utterance(self, text: str) -> dict:
        """Phone/web roundtrip: route like the mic but return data, not audio.

        Mirrors _route_intent for the web UI: template first, then the
        classifier. Coding tasks still dispatch to OpenClaw/Hermes through
        the SAME gateway (shared session machinery); the reply is summarized
        by the local model before it's spoken/sent back.

        Concurrency: Right Ctrl (mic loop, main thread) and dashboard web workers
        both call into the same orchestrator. Shared state (_last_web_*, _web_spoken,
        conversation, executor_choice) is isolated per-request via thread-local _WebCtx
        so concurrent requests don't overwrite each other's summary (the "wrong answer"
        bug). Only the final summary handoff is briefly serialized; the heavy I/O
        (OpenClaw task 5-60s, script 30s, LLM 0.5s) runs fully concurrently — no
        Right Ctrl turn waits for a dashboard web search.
        """
        # Per-request isolation: each thread gets its own WebCtx (text/summary/route/spoken).
        # Legacy globals (_last_web_*) are still updated for backwards-compat readers,
        # but the hot path uses ctx.
        ctx = self._new_ctx(text)
        # Track web concurrency for status.busy (dashboard shows "busy" when mic has a task).
        with self._web_lock:
            self._web_active += 1
        try:
            # _web_mode + _web_spoken are kept for legacy speak() branching, but also mirrored to ctx.
            prev_mode = self._web_mode
            prev_spoken = self._web_spoken
            self._web_mode = True
            self._web_spoken = ctx.spoken  # share the list so speak() appends to ctx.spoken
            try:
                result = self._process_web_utterance_inner(text)
                # Mirror ctx state to legacy globals for any external pollers still on them.
                self._last_web_text = ctx.text or self._last_web_text
                if ctx.summary:
                    with self._web_lock:
                        self._last_web_summary = ctx.summary
                    self._last_web_route = ctx.route
                # Surface ctx summary if inner didn't (handles local-script + openclaw async summary)
                if not result.get("summary") and ctx.summary:
                    result["summary"] = ctx.summary
                    result["reply"] = ctx.summary
                return result
            finally:
                self._web_mode = prev_mode
                self._web_spoken = prev_spoken
        finally:
            with self._web_lock:
                self._web_active = max(0, self._web_active - 1)
            self._clear_ctx()

    def _process_web_utterance_inner(self, text: str) -> dict:
        self._last_web_text = text
        self._last_web_summary = ""
        self._last_web_route = ""
        ctx0 = self._cur_ctx()
        if ctx0 is not None:
            ctx0.text = text
            ctx0.summary = ""
            ctx0.route = ""
        log_event("webui.utterance", text=text[:200])
        try:
            self.shared.append_user(text, route="web")
        except Exception:
            pass
        # FAST filler bypass on web too (typed hello shouldn't hit LLM)
        _low_web = text.strip().lower()
        if _low_web in ("hello","hello.","hi","hi.","hey","hey.","yeah","yeah.","yep","yep.","ok","ok.","okay","okay."):
            log_event("routing.filler_fast", text=text[:60], source="web")
            self._handle_conversation(text)
            _cr_h = self._cur_ctx()
            _sum_h = _cr_h.summary if _cr_h and getattr(_cr_h,"summary",None) else ""
            return {"text": text, "route": _cr_h.route if _cr_h and _cr_h.route else "local", "summary": _sum_h or self._last_web_summary or "", "reply": _sum_h or "", "confidence": 1.0}

        # Hermes delegation outranks templates on web too — same fix as mic path.
        if self.classifier and (_hw := self.classifier._hermes_request(text)):
            log_event("routing.decision", text=text[:200], kind="hermes", confidence=0.9, reason="hermes delegation", source="web")
            if (ctx0 := self._cur_ctx()) is not None:
                ctx0.route = "hermes"
            self._handle_hermes(_hw.task_text or text)
            self._web_wait_for_summary()
            return {"text": text, "route": "hermes", "summary": (self._cur_ctx().summary if self._cur_ctx() else "") or self._last_web_summary or "", "reply": (self._cur_ctx().summary if self._cur_ctx() else "") or self._last_web_summary or "", "confidence": 0.9}
        tmpl = self._templates.match(text) if self._templates is not None else None
        if tmpl is not None:
            log_event("routing.template", name=tmpl.name, kind=tmpl.kind,
                      text=text[:160], source="web")
            if tmpl.kind == "local-script":
                self._last_web_route = f"local-script:{tmpl.name}"
                if (c0 := self._cur_ctx()) is not None:
                    c0.route = f"local-script:{tmpl.name}"
                self._dispatch_local_script(tmpl)
                c1 = self._cur_ctx()
                summ = (c1.summary if c1 and c1.summary else self._last_web_summary) if True else ""
                rout = (c1.route if c1 and c1.route else self._last_web_route) if True else ""
                return {"text": text, "route": rout,
                        "summary": summ or tmpl.ack,
                        "reply": summ,
                        "confidence": tmpl.confidence}
            if tmpl.ack:
                self.speak(tmpl.ack)
            if tmpl.kind == "hermes":
                self._handle_hermes(tmpl.task)
                self._web_wait_for_summary()
            elif tmpl.kind == "conversation":
                self._handle_conversation(tmpl.task)
            else:
                self._dispatch_coding(tmpl.task)
                self._web_wait_for_summary()
            return {"text": text, "route": tmpl.kind,
                    "summary": self._last_web_summary or "",
                    "reply": self._last_web_summary or "", "confidence": tmpl.confidence}
        intent = self.classifier.classify(text)
        log_event("routing.decision", text=text[:200], kind=intent.kind,
                  confidence=round(intent.confidence, 2), reason=intent.reason,
                  source="web")
        if intent.kind == "coding":
            self._dispatch_coding(intent.task_text or text)
            self._web_wait_for_summary()
        elif intent.kind == "local":
            self._handle_local(intent, text)
            # _handle_local already set ctx.summary; also mirror to legacy global for polling
            _cl = self._cur_ctx()
            if _cl and _cl.summary:
                with self._web_lock:
                    self._last_web_summary = _cl.summary
                    self._last_web_route = _cl.route or "local"
        elif intent.kind == "hermes":
            self._handle_hermes(intent.task_text or text)
            self._web_wait_for_summary()
        elif intent.kind == "system":
            self._handle_system(intent)
        elif intent.kind == "confirm":
            self._handle_confirm()
        elif intent.kind == "cancel":
            self._handle_cancel_utterance()
        elif intent.kind == "clarify":
            self.speak("Could you be more specific about what you'd like me to do?")
        else:
            self._handle_conversation(text)
        # Prefer isolated ctx.summary so concurrent EN/RO never swap on the shared global
        _cr = self._cur_ctx()
        _cr_summ = _cr.summary if _cr is not None and getattr(_cr, "summary", None) else ""
        _cr_route = _cr.route if _cr is not None and getattr(_cr, "route", None) else ""
        return {"text": text, "route": _cr_route or intent.kind,
                "summary": _cr_summ or self._last_web_summary or "",
                "reply": _cr_summ or self._last_web_summary or "",
                "confidence": round(intent.confidence, 2)}

    def _web_wait_for_summary(self, timeout_s: Optional[float] = None) -> None:
        """Block the web request until the dispatched task produces a summary
        (or the timeout expires). Long OpenClaw runs: the phone shows the ack
        immediately, and the summary arrives when the task completes — the
        dashboard feed updates live meanwhile.

        Checks both the per-request ctx (isolated, no race) and the legacy
        _last_web_summary global (set by the background waiter thread which has
        no ctx). Either path satisfies the wait.
        """
        if timeout_s is None:
            timeout_s = float(self.cfg.get("webui", "task_wait_s", default=900))
        deadline = time.time() + timeout_s
        ctx = self._cur_ctx()
        while time.time() < deadline and not self._stop.is_set():
            if ctx is not None and ctx.summary:
                return
            if self._last_web_summary:
                # Mirror global into ctx so the caller returns the right summary
                if ctx is not None:
                    ctx.summary = self._last_web_summary
                    ctx.route = self._last_web_route
                return
            time.sleep(0.2)

    def feed_push(self, item: dict) -> None:
        """Bridge -> webui activity feed (no-op if webui not started)."""
        if self._webui is not None:
            self._webui.feed.push(item)

    def _push_input(self, text: str, confidence: float = 0.0, source: str = "mic") -> None:
        """Surface a mic/tap/typed utterance in the dashboard feed (same shape
        as the web path pushes, so the activity page reflects EVERY source)."""
        self.feed_push({"kind": "user", "text": text, "route": source,
                        "conf": round(float(confidence or 0), 3)})

    def _dispatch_coding(self, task_text: str, model: Optional[str] = None,
                         force_confirm: bool = False) -> None:
        """Safety-check and dispatch a coding task to OpenClaw."""
        # If a task is already running, queue instead of hard-switching: the
        # running agent keeps working (no abort strain) and this task runs next.
        if self._busy_on_task():
            self._enqueue_task(task_text, model or self._current_model())
            return
        try:
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

            # executor ask-mode: until the user gets a bigger GPU / smarter model,
            # let THEM pick who runs the task (Hermes vs OpenClaw) instead of
            # always defaulting to OpenClaw. The next utterance is interpreted by
            # _handle_executor_answer(). Web/phone requests skip the question —
            # the website is the OpenClaw control surface; asking "who should run
            # this?" over the phone UI adds friction with no benefit.
            if (self.cfg.get("openclaw", "ask_executor", default=False)
                    and not self._web_mode):
                self.executor_choice.request(final_text, model or self._current_model() or "")
                self.speak("Who should handle this — OpenClaw or Hermes?")
                log_event("executor.ask", task=final_text[:160])
                return

            # Hermes-first: when hermes.prefer, route coding to Hermes by default.
            # OpenClaw is strictly for things Hermes can't do (browser pixel-verification, or when Hermes is down).
            # Heuristic: heavy browser / 1:1 copy / CAPTCHA tasks that benefit from full UI automation stay on OpenClaw when requested.
            try:
                _pref = bool(self.cfg.get("hermes", "prefer", default=True) and self._hermes is not None and self._hermes.available())
            except Exception:
                _pref = False
            _needs_openclaw = any(s in (final_text or "").lower() for s in ("1:1", "pixel-perfect", "copy a site 1:1", "screenshot.*verify", "captcha.*bypass"))
            # sticky route overrides prefer (user said route to openclaw explicitly)
            _sticky_now2 = getattr(self, "sticky_route", None)
            _sticky_target = _sticky_now2.target() if _sticky_now2 else ""
            if _pref and _sticky_target != "openclaw" and not _needs_openclaw:
                log_event("routing.prefer", target="hermes", reason="hermes.prefer: coding -> Hermes by default")
                self._handle_hermes(final_text)
                return
            self._launch_task(final_text, model or self._current_model())
        except Exception as exc:
            log_event("openclaw.error", task=task_text[:200], error=str(exc)[:300])
            log_event("hermes.error", task=task_text[:200], error=str(exc)[:300])
            try:
                self.speak("Something went wrong dispatching the task. Check the logs.")
            except Exception:
                pass

    # ------------------------------------------------------------- task queue
    def _busy_on_task(self) -> bool:
        """True when an agent task is already dispatched and still running."""
        return bool(self.task.active or self._hermes_busy)

    def _enqueue_task(self, task_text: str, model: Optional[str] = None,
                      provider: str = "openclaw") -> None:
        """Add a task to the FIFO queue; acknowledge how many are waiting."""
        pos = self.task_queue.enqueue(task_text, model or "", provider)
        if pos:
            log_event("task.queue.add", task=task_text[:200], position=pos,
                      model=model or "", provider=provider)
            n = "next" if pos == 1 else f"#{pos} in line"
            self.speak(f"Noted — queued as the {n} task. I'll start it when the current one wraps up.")
            self.feed_push({"kind": "status", "text": "task queued", "route": "queue",
                            "detail": f"#{pos}", "task": task_text[:200]})
        else:
            self.speak("The queue is full. Finish the current task first or cancel something.")

    def _run_next_queued(self) -> bool:
        """Launch the next queued task (if any) and tell the user it started."""
        nxt = self.task_queue.dequeue()
        if nxt is None:
            return False
        task_text = nxt.get("task", "")
        provider = nxt.get("provider", "openclaw")
        model = nxt.get("model") or None
        log_event("task.queue.start", task=task_text[:200], provider=provider,
                  remaining=self.task_queue.size)
        self.speak("Starting the next queued task.")
        self.feed_push({"kind": "status", "text": "queued task starting",
                        "route": "queue", "detail": task_text[:160]})
        if provider == "hermes":
            self._handle_hermes(task_text)
        else:
            self._launch_task(task_text, model)
        return True

    def _close_task(self) -> None:
        """Finish the current task cleanly, then ask whether to start the queue."""
        if self.task.active:
            self._abort_task("close task")
            log_event("task.close", session_key=self.task.session_key)
        elif self._hermes_busy:
            self._hermes_busy = False
            log_event("task.close", session_key="hermes")
        if self.task_queue.size:
            self._queued_confirm = True
            self.speak("Current task closed. There's a queued task — should I start it?")
            log_event("task.queue.ask", queued=self.task_queue.size)
        else:
            self.speak("Current task closed. The queue is empty.")

    def _answer_queued_confirm(self, text: str) -> None:
        """Interpret yes/no to 'start the queued task?'."""
        if not self._queued_confirm:
            return
        self._queued_confirm = None
        low = text.strip().lower()
        yes = any(w in low for w in ("yes", "yeah", "yep", "sure", "ok", "go", "do it"))
        if yes:
            self._run_next_queued()
        else:
            self.task_queue.clear()
            self.speak("Okay, dropping the queued task.")

    def _launch_task(self, task_text: str, model: Optional[str] = None) -> None:
        # skills nudge: remind the agent to use its full skill/tool set
        if self.cfg.get("openclaw", "skills_nudge", default=True):
            task_text = (task_text
                         + "\n\nYou are the WORKER (OpenClaw/Hermes). The router is ONLY a router — you own the task: use your full toolkit to deliver it end-to-end."
                           "\nExtensions + tools you MUST use freely (never ask the user for permission):"
                           "\n• terminal — full PC shell: run any command, install deps, launch apps, edit files, check repos, system control."
                           "\n• browser (tool: browser) — full Chromium automation: navigate, click, type, screenshot, copy a site 1:1, extract HTML/CSS, analyze forums/discussions, fill forms, handle logins. For pixel-perfect 1:1 copies, open the target URL, use `browser open/snapshot/screenshot`, copy the DOM + computed styles, and reconstruct faithfully."
                           "\n• vision — analyze any screenshot/image you captured, read text, verify layout matches the source."
                           "\n• web search (perplexity/ddg) — research questions, forum threads, docs, release notes; keep excerpts verbatim in the report."
                           "\n• files/repos/delegation/VS Code (`code` CLI) + all other extensions, skills and tools."
                           "\nFor personal web tasks (shopping, orders, logins, browsing sites the user uses) prefer browser profile=\"user\" so the user's real sessions/cookies/payment methods apply; use profile=\"openclaw\" only for isolated tasks."
                           "\nScreenshots/images are OPTIONAL — capture only when needed for verification (browser/vision tasks), not every time."
                           "\nWhen you use web search (perplexity/ddg) or browser, keep the relevant excerpts verbatim in your final report."
                           "\nProfessional report helper: run scripts/generate_report.py with --task and --summary to generate a perfect report without reasoning - the script is deterministic and idiot-proof. Use it whenever you need to produce a report.\nAlways produce two parts: 1) a FULL detailed report (all tool outputs, image descriptions, forum excerpts, browser screenshots refs, terminal outputs) 2) the last paragraph must be a tight 40-word max spoken summary — that summary is what gets spoken."
                           "\nIf a page needs a captcha, 2FA, or a manual confirmation the user must do, stop and tell the user exactly what to do on screen — do not loop or claim success."
                           "\nReply directly and concisely: no emojis, no pleasantries, no extra words — only what was done and what's next.")
        # capture VS Code workspace context (so switching agents doesn't blank it)
        try:
            import os as _os2
            _ws = _os2.environ.get("VSCODE_CWD", "").strip() or _os2.environ.get("VSCODE_GIT_IPC_HANDLE", "").strip()
            # also probe current working dir if VS Code launched the bridge
            if not _ws and _os2.environ.get("TERM_PROGRAM") == "vscode":
                _ws = str(Path.cwd())
            if _ws:
                self.shared.append_vscode(f"VS Code workspace: {_ws}", workspace=_ws)
        except Exception:
            pass
        # inject cross-provider shared history so switching agents doesn't blank context
        try:
            _hist = self.shared.as_prompt(max_chars=4000, max_turns=24)
            if _hist:
                task_text = f"{_hist}\n\n---\n\nCurrent task: {task_text}"
        except Exception:
            pass
        suffix = uuid.uuid4().hex[:8]
        if self._interrupted_session:
            # New instruction after an interrupt. NOTE: do NOT reuse the
            # aborted session key — the gateway's lane for that session was
            # left in a bad state after the abort and a follow-up run on it
            # returns status=error even when the agent completes (observed
            # 2026-08-21). Start a fresh session and let the agent decide
            # whether to continue the old work or switch, via the prompt.
            self._interrupted_session = None
            old_task = self._interrupted_task_text or ""
            self._interrupted_task_text = ""
            if old_task:
                task_text = _interrupt_decision_prompt(old_task, task_text)
                log_event("task.fresh_session_after_interrupt",
                          old_task=old_task[:120])
        session_key = self.cfg.session_key(f"task-{suffix}")
        log_event("openclaw.dispatch", session_key=session_key, task=task_text[:300],
                  model=model, route=self._route)

        # self-heal: if we're currently offline (gateway was down at startup or
        # dropped us), re-try discovery BEFORE giving up on this task
        if self._route != "gateway" and self._route != "cli":
            self._retry_gateway()

        if self._route == "gateway" and self._gateway:
            boss = _boss_phrase()
            try:
                self.speak(boss, name="ack", barge=False)
            except Exception:
                pass
            log_event("boss_talk", target="openclaw", phrase=boss, task=task_text[:160])
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
                if self._hermes is not None:
                    log_event("openclaw.failover.hermes", reason="gateway error")
                    self._handle_hermes(task_text,
                                        notice="OpenClaw is unreachable. Routing to Hermes.")
                    return
                self.speak("OpenClaw rejected the task. Check the logs.")
                return
            except Exception as exc:
                log_event("openclaw.request.exception", error=str(exc)[:200])
                if self._hermes is not None:
                    log_event("openclaw.failover.hermes", reason=str(exc)[:120])
                    self._handle_hermes(task_text,
                                        notice="OpenClaw is unreachable. Routing to Hermes.")
                    return
                self.speak("Something went wrong talking to OpenClaw.")
                return
        elif self._route == "cli" and self._cli:
            boss = _boss_phrase()
            try:
                self.speak(boss, name="ack", barge=False)
            except Exception:
                pass
            log_event("boss_talk", target="cli", phrase=boss, task=task_text[:160])
            try:
                result = self._cli.run_task(session_key, task_text, model)
            except Exception as exc:
                log_event("openclaw.error", session_key=session_key, error=str(exc)[:300])
                if self._hermes is not None:
                    log_event("openclaw.failover.hermes", reason=str(exc)[:120])
                    self._handle_hermes(task_text, notice="OpenClaw hit an error. Routing to Hermes.")
                    return
                self.speak("OpenClaw hit an error. Check the logs.")
                return
            self.task.set_active(session_key, "", task_text, model or "")
            self._handle_agent_result(session_key, result.reply, result.status, result.elapsed_s)
            self.task.clear()
        else:
            if self._hermes is not None:
                log_event("openclaw.failover.hermes", reason="no gateway route")
                self._handle_hermes(task_text,
                                    notice="OpenClaw is not reachable. Routing to Hermes.")
                return
            self.speak("OpenClaw is not reachable. Start the gateway and try again.")

    def _progress_loop(self) -> None:
        """Periodic talk-back while a big task runs — local Qubit checks what
        the worker (OpenClaw/Hermes) actually did so far and gives a real update.
        Falls back to 'Still working' when the history isn't readable yet."""
        interval = float(self.cfg.get("openclaw", "progress_min_interval_s", default=22))
        while self.task.active and not self._stop.is_set():
            time.sleep(interval)
            if self.task.active and not self.playback.playing and not self._interrupt_requested:
                elapsed = self.task.elapsed_s()
                log_event("openclaw.status", session_key=self.task.session_key, elapsed_s=round(elapsed, 1))
                # Try to give a REAL update: pull last turns from gateway history
                spoken = False
                if elapsed > 25:
                    try:
                        # _fetch_progress_snippet is best-effort; never crash the loop
                        snippet = getattr(self, "_fetch_progress_snippet", lambda: "")() or ""
                        if snippet:
                            now = time.time()
                            if now - float(getattr(self.task, "last_progress_spoken", 0)) >= 30:
                                # local small brain summarizes the snippet into a 1-sentence update
                                try:
                                    from .local_llm import chat_local as _chat_local
                                    _hist = ""
                                    try:
                                        _hist = self.shared.as_prompt(max_chars=800, max_turns=6)
                                    except Exception:
                                        pass
                                    upd = _chat_local(
                                        f"Summarize this worker progress in ONE short, playful and cute sentence for voice (max 18 words, warm and a little flirty, no filler):\n{snippet[:700]}",
                                        _hist, model=self.cfg.get("intent", "local_llm", "model", default="huihui_ai/llama3.2-abliterate:3b"),
                                        url=self.cfg.get("intent", "local_llm", "url", default="http://127.0.0.1:11434"), timeout_s=6.0, max_tokens=40,
                                        context="")
                                    if upd and len(upd.split()) <= 22:
                                        self.speak(upd, name="progress", barge=False)
                                        log_event("openclaw.progress.llm", elapsed_s=round(elapsed,1), update=upd[:160])
                                    else:
                                        self.speak(f"Still working — {snippet[:80]}", name="progress", barge=False)
                                        log_event("openclaw.progress.snippet", elapsed_s=round(elapsed,1), snippet=snippet[:120])
                                except Exception:
                                    self.speak(f"Still working — {snippet[:80]}", name="progress", barge=False)
                                    log_event("openclaw.progress.snippet", elapsed_s=round(elapsed,1), snippet=snippet[:120])
                                self.task.last_progress_spoken = time.time()
                                spoken = True
                    except Exception:
                        pass
                if not spoken and elapsed > 30:
                    if time.time() - float(getattr(self.task, "last_progress_spoken", 0)) >= 30:
                        self.speak("Still working. I'll update you when it's done.",
                                   name="progress", barge=False)
                        log_event("openclaw.progress", elapsed_s=round(elapsed, 1))
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
        # humming while the gateway task runs (non-blocking, stop when task clears)
        _hum_stop = threading.Event()
        def _hum_loop():
            import random as _rnd
            # first hum sooner so the user knows it's alive, then spaced out
            _hum_stop.wait(timeout=_rnd.uniform(6, 10))
            while not _hum_stop.is_set() and self.task.active and not self._stop.is_set():
                if self.playback.playing or self._interrupt_requested:
                    _hum_stop.wait(timeout=_rnd.uniform(12, 20))
                    continue
                phrase = _hum_phrase()
                log_event("humming", phrase=phrase, target="openclaw", elapsed_s=round(time.time() - t0, 1))
                try:
                    self.speak(phrase, name="hum", barge=False)
                except Exception:
                    pass
                _hum_stop.wait(timeout=_rnd.uniform(12, 20))
        threading.Thread(target=_hum_loop, daemon=True, name="openclaw-hum").start()
        if blocking:
            while self.task.active and not self._stop.is_set():
                time.sleep(0.5)
            _hum_stop.set()
        else:
            # non-blocking: stop hum when waiter clears the task (watch the task)
            def _watch_hum_stop():
                while self.task.active and not self._stop.is_set():
                    time.sleep(1.0)
                _hum_stop.set()
            threading.Thread(target=_watch_hum_stop, daemon=True, name="openclaw-hum-watch").start()

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
                # heartbeat so dashboard feed + events.jsonl show the task is alive (was invisible on crash)
                try:
                    log_event("openclaw.status", session_key=session_key, run_id=run_id, elapsed_s=round(time.time() - t0, 1), remaining_s=round(remaining, 1) if remaining > 0 else 0)
                except Exception:
                    pass
                try:
                    res = self._gateway.wait_run(run_id, timeout_ms=window_ms)
                except GatewayError as exc:
                    log_event("openclaw.error", session_key=session_key, run_id=run_id, error=exc.message, elapsed_s=round(time.time() - t0, 2))
                    # Try to salvage whatever the agent already wrote before declaring failure
                    salvaged = ""
                    try:
                        salvaged = self._fetch_final_reply(session_key, run_id) or ""
                    except Exception:
                        salvaged = ""
                    if salvaged.strip():
                        log_event("openclaw.result", session_key=session_key, run_id=run_id,
                                  status="partial", error=exc.message,
                                  elapsed_s=round(time.time() - t0, 2))
                        self.task.clear()
                        self._handle_agent_result(session_key, salvaged, "partial",
                                                  time.time() - t0)
                        return
                    log_event("openclaw.result", session_key=session_key, run_id=run_id,
                              status="failed", error=exc.message,
                              elapsed_s=round(time.time() - t0, 2))
                    self.task.clear()
                    self.speak("OpenClaw failed the task. Check the logs.")
                    return
                except Exception as exc:
                    log_event("openclaw.error", session_key=session_key, run_id=run_id, error=str(exc)[:200], elapsed_s=round(time.time() - t0, 2))
                    salvaged = ""
                    try:
                        salvaged = self._fetch_final_reply(session_key, run_id) or ""
                    except Exception:
                        salvaged = ""
                    if salvaged.strip():
                        log_event("openclaw.result", session_key=session_key, run_id=run_id,
                                  status="partial", error=str(exc)[:200],
                                  elapsed_s=round(time.time() - t0, 2))
                        self.task.clear()
                        self._handle_agent_result(session_key, salvaged, "partial",
                                                  time.time() - t0)
                        return
                    log_event("openclaw.result", session_key=session_key, run_id=run_id,
                              status="failed", error=str(exc)[:200],
                              elapsed_s=round(time.time() - t0, 2))
                    self.task.clear()
                    self.speak("OpenClaw failed the task. Check the logs.")
                    return
                if not self.task.active:
                    # interrupted (Ctrl) or cancelled while we were blocked in
                    # wait_run: the interrupt path already spoke ("Paused.") —
                    # exit silently instead of announcing a bogus error.
                    return
                st = res.get("status", "ok")
                if st == "aborted":
                    # gateway aborted the run for its own reasons; not an error
                    log_event("openclaw.result", session_key=session_key, run_id=run_id,
                              status="aborted", elapsed_s=round(time.time() - t0, 2))
                    self.task.clear()
                    return
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

    def _fetch_progress_snippet(self) -> str:
        """Best-effort peek at the worker's recent tool activity for voice updates."""
        try:
            if self._gateway is None or not getattr(self._gateway, "connected", False):
                return ""
            sk = getattr(self.task, "session_key", "")
            if not sk:
                return ""
            msgs = self._gateway.history(sk, limit=12, timeout=10.0)
            tools = []
            last_assistant = ""
            for m in (msgs or [])[-12:]:
                role = (m.get("role") or "").lower()
                if role == "tool" or m.get("tool"):
                    nm = m.get("tool") or m.get("name") or m.get("function") or ""
                    if nm and nm not in tools:
                        tools.append(str(nm)[:24])
                if role == "assistant":
                    c = m.get("text") or m.get("content") or ""
                    if isinstance(c, list):
                        c = " ".join(x.get("text","") if isinstance(x, dict) else str(x) for x in c)
                    c = str(c).strip()
                    if c and c.lower() not in ("no_reply","no reply") and not c.startswith("<tool"):
                        last_assistant = c[:120]
            parts = []
            if tools:
                parts.append(f"tools: {', '.join(tools[-4:])}")
            if last_assistant:
                parts.append(last_assistant)
            return " — ".join(parts)[:220]
        except Exception:
            return ""

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
        # Always journal + persist the FULL raw report first — summary/speech must never hide it.
        try:
            log_event("openclaw.result", session_key=session_key, status=status,
                      elapsed_s=round(elapsed_s, 2), reply_len=len(reply))
        except Exception:
            pass
        try:
            self.feed_push({"kind": "status", "text": f"task {status}",
                            "route": "openclaw",
                            "detail": f"elapsed {round(elapsed_s, 1)}s"})
        except Exception:
            pass
        if not reply.strip():
            try:
                self.speak("OpenClaw finished the task. I couldn't pull the final "
                           "text from the session — check the dashboard for details, "
                           "or ask me what it did.")
            except Exception:
                pass
            return
        try:
            log_event("openclaw.full_reply", session_key=session_key, reply_len=len(reply), reply=reply[:50000])
        except Exception:
            pass
        # Persist full report to <state_dir>/reports/ so the dashboard / "full report" survives restarts/crashes.
        try:
            self._persist_full_report(session_key, reply, status, elapsed_s)
        except Exception as exc:
            try:
                log_event("openclaw.report.persist_failed", error=str(exc)[:200])
            except Exception:
                pass
        # Lightweight verify: what did the agent actually do? (fs / git hints; never crashes the task)
        try:
            if self.cfg.get("reports", "verify", default=True):
                self._verify_task_result(session_key, reply)
        except Exception as exc:
            try:
                log_event("openclaw.verify.failed", error=str(exc)[:160])
            except Exception:
                pass
        # Summarize for voice — bulletproof: never let summarizer failure hide the full report.
        summary = ""
        try:
            summary = summarize(reply, self.cfg.section("summarizer"))
        except Exception as exc:
            try:
                log_event("openclaw.summarize.failed", error=str(exc)[:160])
            except Exception:
                pass
            from .summarizer import clamp_words as _cw, _strip_markdown as _sm
            try:
                summary = _cw(_sm(reply or ""), 40) or "Task finished — check the full report on the dashboard."
            except Exception:
                summary = "Task finished — check the full report on the dashboard."
        try:
            log_event("openclaw.summary", session_key=session_key, summary=summary[:400])
        except Exception:
            pass
        try:
            self.shared.append_agent_report(summary, raw=reply, provider="openclaw", session_key=session_key)
        except Exception:
            pass
        try:
            self.feed_push({"kind": "summary", "text": "OpenClaw task",
                            "summary": summary, "route": "openclaw", "raw": reply[:50000]})
        except Exception:
            pass
        # Only satisfy a waiting web poll if a web request is actually waiting;
        # otherwise a Right Ctrl task would overwrite a concurrent dashboard request's
        # _last_web_summary before it polls it (the "delay / wrong answer" bug).
        if self._web_active or self._web_mode:
            self._last_web_summary = summary
        self.speak(summary)
        # A task finished — start the next queued one (if any).
        try:
            if self.task_queue.size:
                self._run_next_queued()
        except Exception:
            pass

    
    def _persist_full_report(self, session_key: str, reply: str, status: str, elapsed_s: float) -> None:
        """Save full raw report to <state_dir>/reports/<session_key>.md for dashboard + crash recovery."""
        try:
            base = self.cfg.get("reports", "save_dir") or str(self.cfg.state_dir() / "reports")
            rdir = Path(base).expanduser()
            rdir.mkdir(parents=True, exist_ok=True)
            import re as _re2
            safe = _re2.sub(r"[^a-zA-Z0-9._:-]", "_", session_key)[:120] or "report"
            path = rdir / f"{safe}.md"
            import time as _tm2
            header = "# OpenClaw full report — {}\n\n- status: {}\n- elapsed: {}s\n- at: {}\n\n---\n\n".format(session_key, status, round(elapsed_s,1), _tm2.strftime("%Y-%m-%d %H:%M:%S"))
            path.write_text(header + (reply or ""), errors="replace")
            try: log_event("openclaw.report.saved", path=str(path), chars=len(reply or ""))
            except: pass
            try: self.feed_push({"kind": "raw_report", "text": f"full report: {safe}", "route": "openclaw", "detail": str(path), "raw": (reply or "")[:50000]})
            except: pass
        except Exception as exc:
            try: log_event("openclaw.report.save_failed", error=str(exc)[:200])
            except: pass

    def _verify_task_result(self, session_key: str, reply: str) -> None:
        """Lightweight verify: check mentioned paths + git hints, never crashes."""
        try:
            import re as _re2, subprocess as _sp2
            paths = _re2.findall(r"(?:^|\s)(/(?:[^\s`'\"]+/)*[^\s`'\"]+\.\w+|\./[^\s`'\"]+)", reply or "")
            checked=[]
            for q in paths[:4]:
                try: checked.append(f"{q}={'ok' if Path(q).exists() else 'missing'}")
                except: pass
            git_hint=""
            try:
                out=_sp2.run(["git","status","--porcelain"], capture_output=True, text=True, timeout=3)
                if out.returncode==0:
                    git_hint="; ".join([l.strip() for l in (out.stdout or "").splitlines()[:6]])[:300]
            except: pass
            try: log_event("openclaw.verify", session_key=session_key, reply_chars=len(reply or ""), paths="; ".join(checked)[:400], git=git_hint[:300])
            except: pass
            if checked and any("missing" in c for c in checked):
                try: self.feed_push({"kind": "status", "text": "verify: some mentioned files missing", "route": "verify", "detail": "; ".join(checked)[:300]})
                except: pass
        except Exception as exc:
            try: log_event("openclaw.verify.error", error=str(exc)[:160])
            except: pass

# ----------------------------------------------------------- system cmds
    def _handle_system(self, intent: Intent) -> None:
        cmd = intent.params.get("command")
        # sync Right Ctrl system commands to dashboard activity feed
        log_event("system.command", command=cmd or "unknown", text=str(getattr(intent, "task_text", ""))[:120])
        self.feed_push({"kind": "status", "text": f"system:{cmd or 'unknown'}", "route": "system", "detail": cmd or ""})
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
            self._interrupted_session = None
            self.speak("Cancelled.")
        elif cmd == "continue":
            key = self._interrupted_session
            self._interrupted_session = None
            if key and self._route == "gateway" and self._gateway:
                self.speak("Continuing.")
                try:
                    res = self._gateway.send_message(
                        key, "Please continue where you left off.", timeout=45.0)
                    run_id = res.get("runId") or ""
                    log_event("openclaw.continue", session_key=key, run_id=run_id)
                    self.task.set_active(key, run_id, "continue",
                                         self._current_model() or "")
                    self._spawn_waiter(key, run_id, time.time())
                except Exception as exc:
                    log_event("openclaw.continue.failed", error=str(exc)[:160])
                    self.speak("I couldn't resume the task. Say it again.")
            else:
                self.speak("Nothing to continue. Give me a new task instead.")
        elif cmd == "closetask":
            self._close_task()
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

    def _interrupt_task(self, announce: bool = True) -> None:
        """Give the floor back to the user (Ctrl during speech or a task).

        Stops spoken output but KEEPS a running agent task alive — a task is
        not hard-aborted, so the PC isn't thrashed and nothing is lost. The new
        utterance is then interpreted: a new task is QUEUED (runs after the
        current one), "cancel" aborts, "close task" finishes + asks about the
        queue, "continue" resumes. The running agent decides what to do next.

        announce=False — silent stop (like Esc/cancel); skips the ack.
        """
        interrupted_anything = False
        task_running = False
        if self.task.active:
            # Keep the task running; remember it so interrupt bookkeeping + logs
            # stay accurate. No _abort_task() — that killed work under load.
            task_running = True
            log_event("task.interrupted", session_key=self.task.session_key,
                      old_task=self.task.description[:200], keep_running=True)
            interrupted_anything = True
        if self.playback.playing:
            self.playback.stop()
            interrupted_anything = True
        if self._hermes_busy:
            # Hermes task keeps running in the background (no abort).
            log_event("hermes.interrupted", keep_running=True)
            interrupted_anything = True
        # Rate-limit idle interrupts so a stuck dashboard poll loop doesn't spam the journal.
        if not interrupted_anything:
            now = time.time()
            if not hasattr(self, "_last_idle_interrupt_ts"):
                self._last_idle_interrupt_ts = 0.0  # type: ignore[attr-defined]
            if now - float(getattr(self, "_last_idle_interrupt_ts", 0.0)) < 2.0:  # type: ignore[attr-defined]
                return
            self._last_idle_interrupt_ts = now  # type: ignore[attr-defined]
        # Always journal the interrupt so /api/logs + dashboard Logs + qubit-dev /logs
        # can show it, even when idle (so "interrupt doesn't work" is debuggable).
        log_event("interrupt.request", announce=announce, had_task=task_running,
                  had_playback=bool(self.playback.playing), was_idle=not interrupted_anything,
                  had_hermes=bool(self._hermes_busy))
        # Publish to the dashboard Activity feed too (so clicking shows it)
        try:
            self.feed_push({"kind": "status", "text": "interrupt", "route": "interrupt",
                            "detail": f"task={task_running} playback={self.playback.playing} hermes={bool(self._hermes_busy)} idle={not interrupted_anything}",
                            "raw": f"announce={announce} interrupted_anything={interrupted_anything} task_running={task_running}"})
        except Exception:
            pass
        # Non-blocking announce: never hold the HTTP /api/interrupt handler on TTS.
        if interrupted_anything and not self.muted and announce:
            import threading as _th
            if task_running:
                _th.Thread(target=lambda: self.speak(
                    "Go ahead — I'm listening. Name a task and I'll queue it; say cancel or close task to stop the current one."),
                    daemon=True, name="interrupt-ack").start()
            else:
                _th.Thread(target=lambda: self.speak("Go ahead."), daemon=True, name="interrupt-ack").start()

    # ------------------------------------------------------------- executor
    def _handle_executor_answer(self, text: str) -> None:
        """Interpret the answer to 'OpenClaw or Hermes?' and dispatch."""
        pending = self.executor_choice.take()
        if not pending:
            return
        low = text.strip().lower()
        log_event("executor.answer", text=low[:120], task=pending.task_text[:160])
        hermes_re = re.compile(
            r"\b(hermes|use hermes|hermes agent|ask hermes)\b", re.I)
        openclaw_re = re.compile(
            r"\b(openclaw|open claw|claw|qubit|use (the )?agent)\b", re.I)
        if hermes_re.search(low) and not openclaw_re.search(low):
            self.speak("Using Hermes.")
            self._handle_hermes(pending.task_text)
        elif openclaw_re.search(low):
            self.speak("Using OpenClaw.")
            self._launch_task(pending.task_text, pending.model or None)
        else:
            # "you pick" / "whatever" / "either" / unparseable: default to
            # OpenClaw (the established agent path)
            self.speak("I'll use OpenClaw for that.")
            self._launch_task(pending.task_text, pending.model or None)

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
        """The bridge's voice: the LOCAL model answers ALL chat (chitchat AND
        questions) — fast (<0.5s), local, uncensored. Only TASKS go through
        OpenClaw/Hermes (see _route_intent); cloud APIs are last-resort
        fallbacks when the local model or gateway is down.
        """
        # === Fast path: short filler / ack without semantic content ===
        # "yeah / okay / ok / hello / hi / hey / ...": don't burn the 3B LLM (0.7s) for a canned line.
        # Keep them local, no history pollution, and free the pipeline for real work.
        _filler_re = getattr(self, "_filler_re", None)
        if _filler_re is None:
            import re as _re
            # matches bare filler / greeting with optional trailing period
            self._filler_re = _re.compile(r"^(?:yeah|yep|okay|ok|hello|hi|hey|bruski|sup|howdy|yo|hmm|ah|oh|alright|fine|good|thanks|thank you)\b[.!?]*$", flags=_re.I)
            _filler_re = self._filler_re
        low = text.strip().lower()
        # Explicit hello/hi must never burn the 3B — instant and keeps the bridge responsive under load
        if low in ("hello","hello.","hi","hi.","hey","hey."):
            low = "hello"  # normalize for filler check
        if _filler_re.fullmatch(low):
            # Route by language but keep it instant — also mirror to per-request ctx so /api/result returns it
            if low.startswith(("salut","buna","bruski")) or "salut" in low or "bruski" in low:
                # Romanian-ish greeting -> short RO ack
                reply_f = "Sunt bine. Cu ce te pot ajuta?"
            else:
                reply_f = "I'm functioning well. What do you need?"
            _cf = self._cur_ctx()
            if _cf is not None:
                _cf.summary = reply_f
                _cf.route = "local"
                _cf.done.set()
            with self._web_lock:
                self._last_web_summary = reply_f
                self._last_web_route = "local"
            self.speak(reply_f)
            self.feed_push({"kind": "summary", "text": text[:300], "summary": reply_f, "route": "local", "detail": "filler"})
            log_event("conversation.filler", text=text[:80])
            return
        # Hotword for identity: robust to ASR clipping ("My name." / "my name"), accent and missing "what's"
        # Covers: "whats my name", "what is my name", "my name", "who am i", "who i am" — bare "my name" is the RCtrl clipped case
        _low_identity = low
        if re.search(r"\b(my name|who am i|who i am)\b", _low_identity):
            # Deterministic answer from memory — never ask back
            reply = "Your name is Tricky."
            # Keep per-request ctx + conversation history consistent for next turns
            _ctx_id = self._cur_ctx()
            if _ctx_id is not None:
                _ctx_id.summary = reply
                _ctx_id.route = "local"
                _ctx_id.done.set()
            with _conv_lock:
                self.conversation.add("user", text)
                self.conversation.add("assistant", reply)
            with self._web_lock:
                self._last_web_summary = reply
                self._last_web_route = "local"
            self.feed_push({"kind": "summary", "text": text[:300], "summary": reply, "route": "local", "detail": "identity"})
            log_event("conversation.identity", text=text[:80], summary=reply)
            self.speak(reply)
            return
        # inject sticky route into local LLM context so it doesn't hallucinate
        _sticky_hint = ""
        try:
            _sr = self.sticky_route.target()
            if _sr:
                _sticky_hint = f"Active routing preference: route to {_sr.title()} (user asked to route to {_sr}). Honor it."
        except Exception:
            pass
        llm_cfg = self.cfg.section("intent").get("local_llm", {}) or {}
        # Explicit "use perplexity to search X" -> straight to Perplexity,
        # never the local model (it answers web queries generically without
        # actually searching — "I was trained on a large corpus…").
        explicit_web = ("perplexity" in text.lower()
                        or (self.cfg.get("intent", "perplexity_enabled", default=True)
                            and _needs_web(text)))
        if not explicit_web and llm_cfg.get("enabled", True):
            from .local_llm import chat_local
            # Use per-request snapshot so concurrent EN/RO requests don't bleed
            _ctx_for_llm = self._cur_ctx()
            _conv_prompt = _ctx_for_llm.conv_snapshot if _ctx_for_llm is not None and _ctx_for_llm.conv_snapshot is not None else self.conversation.as_prompt()
            # Ensure EVERY local chat turn sees the identity file (Qubit/Tricky) — even per-request isolated ctx
            _mem_ctx = (self._memory_context or self.conversation.as_prompt())  # both include personal-knowledge
            if _sticky_hint:
                _mem_ctx = f"{_sticky_hint}\n\n{_mem_ctx}" if _mem_ctx else _sticky_hint
            # also show recent reports hint so local chat can answer "what did you do?" accurately
            try:
                reply = chat_local(
                    text, _conv_prompt,
                    model=llm_cfg.get("model", "huihui_ai/llama3.2-abliterate:3b"),
                    url=llm_cfg.get("url", "http://127.0.0.1:11434"),
                    timeout_s=float(llm_cfg.get("timeout_s", 4.0)),
                    max_tokens=int(llm_cfg.get("max_tokens", 100)),
                    context=_mem_ctx)
            except Exception as exc:
                reply = ""
                log_event("conversation.local.skip", error=str(exc)[:120])
            if reply:
                # Activity feed: show what the user SAID (transcription) alongside the reply
                # so Tricky can verify ASR quality: "You: How are you? → Bot: Sunt bine..."
                # text=sanitized user utterance, summary=bot reply, detail=routing reason
                trimmed = trim_spoken(reply, 300)
                ctx2 = self._cur_ctx()
                if ctx2 is not None:
                    ctx2.summary = trimmed
                    ctx2.route = "local"
                    ctx2.done.set()
                with _conv_lock:
                    self.conversation.add("user", text)
                    self.conversation.add("assistant", reply)
                # Mirror so legacy pollers (_web_wait_for_summary waiting on global) also see it
                with self._web_lock:
                    self._last_web_summary = trimmed
                    self._last_web_route = "local"
                self.feed_push({"kind": "summary", "text": text[:300], "summary": trimmed,
                                "route": "local", "detail": "conversation", "raw": reply[:50000]})
                log_event("conversation.local.summary", text=text[:160], summary=reply[:160],
                          model=llm_cfg.get("model", "huihui_ai/llama3.2-abliterate:3b"))
                try:
                    self.shared.append_assistant(reply, route="local", provider="local")
                except Exception:
                    pass
                log_event("conversation.local.full", text=text[:300], reply_len=len(reply), reply=reply[:2000],
                          model=llm_cfg.get("model", "huihui_ai/llama3.2-abliterate:3b"))
                self.speak(trim_spoken(reply, 500))
                return

        # last-resort fallbacks when the local model is unavailable
        reply = ""
        spoke = False
        web_ask = self.cfg.get("intent", "perplexity_enabled", default=True) \
            and (_needs_web(text) or "perplexity" in text.lower())
        if web_ask:
            reply = self._chat_perplexity(text)
        if not reply:
            if self.cfg.get("intent", "stream_speech", default=True):
                sentences: List[str] = []
                reply = self._chat_deepseek_stream(text, sentences.append)
                if reply:
                    self.speak_stream(sentences or [reply])
                    spoke = True
            if not reply:
                reply = self._chat_deepseek(text)
        if not reply:
            reply = self._chat_ollama(text)
        if reply:
            with _conv_lock:
                self.conversation.add("user", text)
                self.conversation.add("assistant", reply)
            if not spoke:
                self.speak(trim_spoken(reply, 500))
            # sync fallback replies to dashboard too (Right Ctrl + web alike) — show what was HEARD
            trimmed2 = trim_spoken(reply, 300)
            ctx3 = self._cur_ctx()
            if ctx3 is not None:
                ctx3.summary = trimmed2
                ctx3.route = "fallback"
                ctx3.done.set()
            self.feed_push({"kind": "summary", "text": text[:300], "summary": trimmed2,
                            "route": "fallback", "detail": "conversation", "raw": reply[:50000]})
            log_event("conversation.fallback.summary", text=text[:160], summary=reply[:160])
            try:
                self.shared.append_assistant(reply, route="fallback", provider="fallback")
            except Exception:
                pass
            log_event("conversation.fallback.full", text=text[:300], reply_len=len(reply), reply=reply[:2000])
        else:
            # Last resort: if even fallbacks 402/timeout, still answer greetings/identity deterministically so UX never blocks
            if re.search(r"\b(hello|hi|hey)\b", low):
                self.speak("Hey Tricky, I\'m Qubit — I\'m here. Try again?")
            elif re.search(r"\b(my name|who am i)\b", low):
                self.speak("Your name is Tricky.")
            else:
                self.speak("I'm sorry, I couldn't reach the chat model.")

    def _chat_deepseek_stream(self, text: str, on_sentence) -> str:
        """Stream a DeepSeek completion; emit completed sentences via
        on_sentence(str) as they arrive and return the full reply."""
        import urllib.request
        import urllib.error
        key = self._deepseek_key()
        if not key:
            return ""
        url = self.cfg.get("intent", "deepseek_url",
                           default="https://api.deepseek.com/chat/completions")
        model = (self.cfg.get("intent", "deepseek_model", default="")
                 or _hermes_default_model()
                 or "deepseek-v4-flash")
        _c = self._cur_ctx()
        ctx = _c.conv_snapshot if _c is not None and _c.conv_snapshot else self.conversation.as_prompt()
        system = ("You are a concise, warm voice assistant. Answer briefly in "
                  "1-3 short sentences unless asked for detail. Be direct: "
                  "no emojis, no pleasantries, no filler words — say only "
                  "what's needed. "
                  "Reply in the SAME language as the current User message — if it is English, reply in English; if Romanian, reply in Romanian. Ignore previous turns' language.")
        payload = {
            "model": model, "stream": True, "temperature": 0.4,
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
            with urllib.request.urlopen(req, timeout=60) as resp:
                buf = ""
                full = ""
                for raw in resp:
                    if self._interrupt_requested:
                        resp.close()
                        break
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = (json.loads(data)["choices"][0]
                                 .get("delta", {}).get("content") or "")
                    except Exception:
                        continue
                    buf += delta
                    full += delta
                    # emit completed sentences as they form
                    while True:
                        m = re.search(r"[.!?…]\s*", buf)
                        if not m:
                            break
                        sentence = buf[:m.end()].strip()
                        buf = buf[m.end():]
                        if sentence:
                            on_sentence(sentence)
                tail = buf.strip()
                if tail:
                    full += ""
                    on_sentence(tail)
                return full.strip()
        except Exception as exc:
            log_event("conversation.deepseek.failed", error=str(exc)[:160])
            return ""

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
        model = (self.cfg.get("intent", "deepseek_model", default="")
                 or _hermes_default_model()
                 or "deepseek-v4-flash")
        ctx = self.conversation.as_prompt()
        system = ("You are a concise, warm voice assistant. Answer briefly in "
                  "1-3 short sentences unless asked for detail. Be direct: "
                  "no emojis, no pleasantries, no filler words — say only "
                  "what's needed. "
                  "Reply in the SAME language as the current User message — if it is English, reply in English; if Romanian, reply in Romanian. Ignore previous turns' language.")
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
                  "sentences. Prefer facts over hedging. Be direct: no emojis, "
                  "no pleasantries, no filler words. "
                  "Reply in the SAME language as the current User message.")
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
        # Fallback uses the same resident model as local_llm (was hardcoded qwen3-vl:8b which is not resident)
        fallback = self.cfg.get("intent", "local_llm", "model", default="huihui_ai/llama3.2-abliterate:3b")
        model = self.cfg.get("intent", "ollama_model", default=fallback)
        _c = self._cur_ctx()
        ctx = _c.conv_snapshot if _c is not None and _c.conv_snapshot else self.conversation.as_prompt()
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
        log_event("local.action.exec", action=name, text=text[:160], params=params)
        msg = local_actions.execute(m)
        log_event("local.action.result", action=name, result_len=len(msg), result=msg[:2000])
        # Mirror to per-request ctx so /api/result returns the summary (dashboard poll), not just activity feed
        c_local = self._cur_ctx()
        if c_local is not None:
            c_local.summary = msg[:300]
            c_local.route = "local"
            c_local.done.set()
        # sync Right Ctrl local actions to dashboard activity feed + event journal (persists after restart)
        log_event("local.action.summary", action=name, text=text[:160], summary=msg[:300])
        self.feed_push({"kind": "summary", "text": f"local:{name}", "summary": msg[:300], "route": "local", "raw": msg[:50000]})
        self.speak(msg)

    def _handle_hermes(self, task: str, notice: str = "") -> None:
        """Explicit Hermes delegation (ask Hermes to ...) — background + interruptible.

        `notice` overrides the default ack (used by the OpenClaw→Hermes
        failover path so the user hears WHY it routed to Hermes).
        """
        # Queue a new Hermes task while one is already running (don't interrupt).
        if self._busy_on_task():
            self._enqueue_task(task, self._current_model(), provider="hermes")
            return
        if self._hermes is None:
            self.speak("Hermes is not enabled; routing to OpenClaw instead.")
            self._dispatch_coding(task)
            return
        hermes = self._hermes
        log_event("hermes.task", task=task[:300], notice=notice[:120] if notice else "")
        boss = _boss_phrase()
        self.speak(notice or boss, name="ack", barge=False)
        log_event("boss_talk", target="hermes", phrase=boss, task=task[:160])
        try:
            _hist_h = self.shared.as_prompt(max_chars=4000, max_turns=24)
            if _hist_h:
                task = f"{_hist_h}\n\n---\n\nCurrent task: {task}"
        except Exception:
            pass
        task = (task + "\n\nReply directly and concisely: no emojis, no "
                     "pleasantries, no extra words — only what was needed.")
        self._hermes_busy = True
        _humming_stop = threading.Event()

        def _hum_loop():
            import random as _rnd
            while not _humming_stop.is_set() and self._hermes_busy and not self._stop.is_set():
                _humming_stop.wait(timeout=_rnd.uniform(6, 10))
                if _humming_stop.is_set() or not self._hermes_busy or self._stop.is_set():
                    break
                if self.playback.playing or self._interrupt_requested:
                    continue
                phrase = _hum_phrase()
                log_event("humming", phrase=phrase, target="hermes")
                try:
                    self.speak(phrase, name="hum", barge=False)
                except Exception:
                    pass

        threading.Thread(target=_hum_loop, daemon=True, name="hermes-hum").start()

        def _run():
            try:
                reply = hermes.run_task(task)
            except TimeoutError as exc:
                _humming_stop.set()
                self._hermes_busy = False
                log_event("hermes.error", task=task[:200], error="timeout")
                self.speak("Hermes timed out. Try again, or ask me instead.")
                return
            except Exception as exc:
                _humming_stop.set()
                self._hermes_busy = False
                log_event("hermes.error", task=task[:200], error=str(exc)[:300])
                self.speak(f"Hermes failed: {str(exc)[:120]}")
                return
            _humming_stop.set()
            self._hermes_busy = False
            if self._interrupt_requested:
                # user redirected mid-run; don't speak the stale reply
                log_event("hermes.result.discarded", why="interrupted")
                return
            # Persist full hermes raw report (images + perplexity/ddg excerpts kept verbatim) — never lose it on crash/summarize failure.
            try:
                log_event("hermes.full_reply", reply_len=len(reply), reply=reply[:50000])
            except Exception:
                pass
            try:
                self._persist_full_report(f"hermes:{self.task.session_key or 'hermes'}", reply, "ok", 0.0)
            except Exception:
                pass
            try:
                if self.cfg.get("reports", "verify", default=True):
                    self._verify_task_result(f"hermes:{self.task.session_key or 'hermes'}", reply)
            except Exception:
                pass
            # Bulletproof summarize: always <=40w even if ollama down
            summary = ""
            try:
                summary = summarize(reply, self.cfg.section("summarizer"))
            except Exception:
                from .summarizer import clamp_words as _cw, _strip_markdown as _sm
                try:
                    summary = _cw(_sm(reply or ""), 40)
                except Exception:
                    summary = "Task finished — check the full report on the dashboard."
            try:
                # hermes hyperlink enrich
                _hl=[]
                try:
                    import re as _re3
                    for mm in _re3.finditer(r"https?://[^\s)\]\"']+", reply or ""):
                        uu=mm.group(0).rstrip('.,;)')
                        if uu not in _hl: _hl.append(uu)
                    if _hl:
                        reply = reply + "\n\n---\n\n### References\n" + "\n".join(f"- [{u}]({u})" for u in _hl[:20])
                except Exception:
                    pass
                try:
                    self.shared.append_agent_report(summary, raw=reply, provider="hermes", session_key=self.task.session_key or "hermes")
                except Exception:
                    pass
                self.feed_push({"kind": "summary", "text": "Hermes task", "summary": summary, "route": "hermes", "raw": reply[:50000], "links": _hl[:20]})
            except Exception:
                pass
            try:
                log_event("hermes.summary", summary=summary[:300])
            except Exception:
                pass
            # Mirror to per-request ctx so /api/result prefers isolated summary (no swap with concurrent)
            if (_ctx_h := self._cur_ctx()) is not None:
                _ctx_h.summary = summary
                _ctx_h.route = "hermes"
                _ctx_h.done.set()
            with self._web_lock:
                self._last_web_summary = summary
                self._last_web_route = "hermes"
            self.speak(summary)
            # A Hermes task finished — start the next queued one (if any).
            try:
                if self.task_queue.size:
                    self._run_next_queued()
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True, name="hermes-task").start()

    def _route_intent(self, intent: Intent, text: str) -> None:
        """Shared dispatcher used by mic, typed input, and the duplex tap."""
        # push the routing decision for mic/tap/typed sources (the web path
        # pushes its own routing item in webui.handle_talk)
        self.feed_push({"kind": "routing", "text": text, "route": intent.kind,
                        "conf": round(float(getattr(intent, "confidence", 0) or 0), 2),
                        "detail": getattr(intent, "reason", "") or ""})
        log_event("routing.decision", text=text[:200], kind=intent.kind,
                  confidence=round(float(getattr(intent, "confidence", 0) or 0), 2),
                  reason=getattr(intent, "reason", "") or "", source="mic")
        try:
            self.shared.append_user(text, route=intent.kind)
        except Exception:
            pass
        # FAST filler bypass: "yeah / hello / hi / hey" must never hit the 4.6s LLM — answer instantly
        _low_fast = text.strip().lower()
        if _low_fast in ("hello","hello.","hi","hi.","hey","hey.","yeah","yeah.","yep","yep.","ok","ok.","okay","okay."):
            if _low_fast in ("hello","hello.","hi","hi.","hey","hey."):
                _low_fast = "hello"
            _reply_f = "Sunt bine. Cu ce te pot ajuta?" if any(w in _low_fast for w in ("salut","buna")) else "I'm functioning well. What do you need?"
            # For hello/yeah the intent is filler — bypass LLM and go straight to _handle_conversation's fast path
            # but we can short-circuit here to avoid the 4.6s LLM entirely
            log_event("routing.filler_fast", text=text[:60])
            self.feed_push({"kind": "routing", "text": text, "route": "filler", "conf": 1.0, "detail": "fast filler bypass (no LLM)"})
            self._handle_conversation(text)
            return

        # sticky routing: explicit "route to X / use X" sets persistent preference
        # so the local brain doesn't forget on next turn. Also handle "clear/reset route".
        if self.classifier:
            _low_sticky = text.lower().strip()
            if __import__("re").search(r"\b(clear|reset|forget) (route|routing|preference)\b", _low_sticky):
                self.sticky_route.clear()
                self.speak("Routing preference cleared.")
                log_event("sticky_route.cleared")
                return
            _sticky = self.classifier._sticky_route(text)
            if _sticky:
                # pure routing preference (no task attached) -> just set and ack
                _task_re = __import__("re").sub(r"route (this|it|everything|all)?\s*(to|through)\s+(hermes|openclaw|browser)\b|use (the )?(hermes|openclaw|browser)\b.*|always (use|route through) (hermes|openclaw|browser)\b", "", _low_sticky, flags=__import__("re").I).strip(" .,;:")
                self.sticky_route.set(_sticky, reason=text[:120])
                log_event("sticky_route.set", target=_sticky, text=text[:160])
                if not _task_re or len(_task_re) < 4:
                    self.speak(f"Got it — routing to {_sticky.title()} from now on.")
                    self.feed_push({"kind": "status", "text": f"sticky route → {_sticky}", "route": "sticky"})
                    return
                # routing + task in one utterance: "route to hermes and do X" -> strip prefix and continue
                text = _task_re
                intent.task_text = _task_re
                log_event("sticky_route.with_task", target=_sticky, task=text[:160])
            # if we already have a sticky route and this utterance is a real task/conversation NOT a system cmd,
            # apply it — but explicit hermes delegation still wins (checked next), and local actions win too.
            # We bump the sticky counter here so it naturally expires.
            _sticky_now = self.sticky_route.target()
            if _sticky_now and intent.kind not in ("system", "confirm", "cancel"):
                # only apply sticky to tasks / open-ended requests; not to pure conversation filler
                if intent.kind in ("coding", "hermes") or (intent.kind == "conversation" and len(text.split()) > 3):
                    _h = self.classifier._hermes_request(text)
                    if not _h:  # no explicit hermes override in this utterance
                        if _sticky_now == "hermes" and intent.kind != "hermes":
                            log_event("sticky_route.applied", target=_sticky_now, from_kind=intent.kind)
                            self._handle_hermes(intent.task_text or text)
                            self.sticky_route.bump()
                            return
                        if _sticky_now == "browser" and intent.kind == "coding":
                            # browser tasks are still coding but with browser hint — prepend it
                            intent.task_text = f"[use browser] {intent.task_text or text}"
                            log_event("sticky_route.applied", target=_sticky_now, from_kind=intent.kind)
                            self.sticky_route.bump()
                            # fall through to normal coding dispatch with browser hint

        # executor ask-mode answer: the user just said who should run the
        # pending task ("OpenClaw", "Hermes", "you pick", "whatever") — handle
        # it BEFORE templates/classification so the answer isn't misrouted
        if self.executor_choice.pending:
            self._handle_executor_answer(text)
            return
        # queued-task confirmation: answer to "should I start the queued task?"
        if self._queued_confirm:
            self._answer_queued_confirm(text)
            return
        # Explicit hermes delegation wins over everything (templates/classifier) —
        # "ask hermes to check my inbox" must route to Hermes, not to the generic
        # coding "check-email" template that also matches "my inbox".
        if self.classifier and (_h := self.classifier._hermes_request(text)):
            self._handle_hermes(_h.task_text or text)
            return
        # daily-task templates win over the model-based classifier: a matched
        # template dispatches the known task text directly (0 tokens, instant)
        tmpl = self._templates.match(text) if self._templates is not None else None
        if tmpl is not None:
            log_event("routing.template", name=tmpl.name, kind=tmpl.kind,
                      text=text[:160])
            if tmpl.ack:
                self.speak(tmpl.ack)
            if tmpl.kind == "hermes":
                self._handle_hermes(tmpl.task)
            elif tmpl.kind == "conversation":
                self._handle_conversation(tmpl.task)
            elif tmpl.kind == "local-script":
                self._last_web_text = text
                self._dispatch_local_script(tmpl)
            else:
                self._dispatch_coding(tmpl.task)
            return
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
            # _handle_local already set ctx.summary; also mirror to legacy global for polling
            _cl = self._cur_ctx()
            if _cl and _cl.summary:
                with self._web_lock:
                    self._last_web_summary = _cl.summary
                    self._last_web_route = _cl.route or "local"
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
        pending = self._duplex_ptt_pending
        self._duplex_ptt_pending = False
        if self._duplex is not None:
            self._duplex.resume_output()
        # feedback suppression: the mic hears the lane's own voice (or the
        # bridge's piper replies) — ignore unless the user took over with PTT
        if not pending:
            if self.playback.playing:
                return
            if self._duplex is not None and self._duplex.speaking:
                return
        log_event("routing.input", text=text, confidence=round(confidence, 3),
                  source="duplex-tap", ptt=pending)
        self._push_input(text, confidence, "duplex-tap")
        intent = self.classifier.classify(text)
        log_event("routing.decision", text=text[:200], kind=intent.kind,
                  confidence=round(intent.confidence, 2), reason=intent.reason,
                  source=intent.source)
        self.conversation.add("user", text)
        if intent.kind == "conversation" and not pending:
            # PersonaPlex is already answering in character — stay out of the way.
            return
        # Everything else (or a PTT take-over): duck PersonaPlex so it can't
        # answer for work it cannot do, route the task, then resume the lane.
        self._duck_pp()
        try:
            self._route_intent(intent, text)
        finally:
            self._unduck_pp()

    # ----------------------------------------------------------------- memory
    def _load_memory_context(self) -> str:
        """Read personal-knowledge.md + MEMORY.md + recent daily notes.

        personal-knowledge.md lives next to the voice config
        (~/.config/openclaw-voice-bridge/personal-knowledge.md, shipped in the
        repo as config/personal-knowledge.md) — edit it freely; it is injected
        into every local-model chat turn so the bridge knows the user's world.
        """
        try:
            ws = Path(self.cfg.get("memory", "workspace",
                                   default="~/.openclaw/workspace")).expanduser()
            budget = int(self.cfg.get("memory", "context_chars", default=1500))
            parts: list[str] = []
            # personal knowledge (user-editable, highest priority)
            pk = self._personal_knowledge_path()
            if pk.exists():
                parts.append(f"<personal-knowledge>\n{pk.read_text(errors='replace')[:2500]}")
            mem = ws / "MEMORY.md"
            if mem.exists():
                parts.append(f"<MEMORY.md>\n{mem.read_text(errors='replace')[:budget]}")
            daily_dir = ws / "memory"
            if daily_dir.exists():
                # only the MOST RECENT note — daily logs are low-signal for
                # chat replies and cost tokens on EVERY local turn
                recent = sorted(daily_dir.glob("*.md"))[-1:]
                for p in recent:
                    parts.append(f"<{p.name}>\n{p.read_text(errors='replace')[:min(budget, 800)]}")
            ctx = "\n\n".join(parts)
            if ctx:
                log_event("memory.loaded", chars=len(ctx), pk=str(pk))
            return ctx
        except Exception as exc:
            log_event("memory.load.error", error=str(exc)[:160])
            return ""

    def _personal_knowledge_path(self) -> Path:
        """<config dir>/personal-knowledge.md (alongside voice.yaml)."""
        cfg_file = self.cfg.path
        if cfg_file:
            return Path(cfg_file).parent / "personal-knowledge.md"
        return Path(os.path.expanduser("~/.config/openclaw-voice-bridge")) / "personal-knowledge.md"

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
        down_since: Optional[float] = None
        while not self._stop.is_set():
            lane_live = self._duplex is not None and self._duplex.live
            if lane_live:
                down_since = None
                if fallback:
                    fallback = False
                    print("\n[PersonaPlex is back — full-duplex mode resumed]")
                    self.speak("PersonaPlex is back.")
            else:
                if down_since is None:
                    down_since = time.time()
                # only announce an outage once it has lasted >60 s: the lane
                # reconnects every few seconds, so instant announcements make
                # it sound like it "keeps restarting"
                if (not fallback and time.time() - down_since > 60.0):
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
        of racing with flag clears and starting a 30s silent capture.

        Sticky-press rule: `_ptt_pressed` latches ANY press (even one fully
        contained inside a busy window, e.g. a 100ms tap while speak() holds
        the loop). The loop consumes it as a fresh PTT press afterwards — the
        user never has to tap twice to "wake up" the bridge.
        """
        while not self._stop.is_set():
            # keep xinput alive: if the XI2 loop died, restart it (never leave hotkey dead)
            try:
                hk = getattr(self, "hotkey", None)
                if hk is not None and hasattr(hk, "ensure_alive"):
                    hk.ensure_alive()
            except Exception:
                pass
            # wait for a genuine hold OR a latched press
            while not self._stop.is_set() and not self._ptt_hold \
                    and not self._ptt_pressed.is_set():
                time.sleep(0.02)
            if self._stop.is_set():
                break
            # consume the latched press: if the key is still held this becomes
            # a normal hold; if it was a quick tap, _capture_utterance detects
            # that from _press_time and enters toggle mode
            self._ptt_pressed.clear()
            # duplex PTT take-over: the lane's mic tap delivers the utterance —
            # do NOT open a second mic stream (device conflict). Wait for the
            # tap to fire or the 12 s window to lapse.
            if self._duplex_ptt_pending and self._duplex is not None:
                if time.time() > self._duplex_ptt_until:
                    self._duplex_ptt_pending = False
                    self._duplex.resume_output()
                    log_event("personaplex.ptt_expired")
                else:
                    time.sleep(0.05)
                    continue
            busy = (self.task.active or self.playback.playing
                    or self._interrupt_requested or self._hermes_busy)
            if busy and self.cfg.get("hotkey", "interrupt_on_ptt", default=True):
                # pause whatever the bridge is doing (speech OR agent task),
                # then listen hands-free for the new instruction; the agent
                # only continues on an explicit say-so
                self._interrupt_requested = False
                self._interrupt_task()
                res = self._capture_utterance(toggle=True)
            else:
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

    def _capture_utterance(self, toggle: bool = False) -> Optional[str]:
        """Record one utterance (PTT start..release or manual). Returns wav path or None.

        toggle=True forces hands-free listening (interrupt flow): ends on
        silence, a second tap, or the no-speech timeout — independent of the key.
        """
        if self._mic is None:
            log.error("mic not initialized")
            return None
        mic = self._mic
        stop_req = threading.Event()
        tap_ms = int(self.cfg.get("hotkey", "tap_ms", default=300))
        toggle_no_speech_s = float(self.cfg.get("hotkey", "toggle_no_speech_s", default=5.0))
        # End-of-turn silence (research-validated 0.7-0.9s; was 0.4s which cut
        # mid-thought pauses). Hold-to-talk still ends FAST on release via
        # stop_req + tail_s — this only lengthens the VAD 'done' backstop that
        # ends hands-free/toggle captures and tolerates breathing pauses.
        end_of_turn_s = float(self.cfg.get("vad", "silence_timeout_s", default=0.8))
        self._toggle_armed = False
        self._toggle_stop.clear()
        if self._ptt_supported and not toggle:
            held = self._ptt_hold  # snapshot at capture start
            press_t = self._press_time

            def _toggle_wait(why: str) -> None:
                """Hands-free listen until second tap / endpointer done /
                no-speech timeout. Shared by the tap path and the
                release-before-speech hold path."""
                self._toggle_armed = True
                log_event("hotkey.toggle", on=True, why=why)
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
                # toggle over: disarm so the NEXT interaction starts clean
                # (a stale _toggle_armed made the next hold's release act
                # like a second tap and kill the capture instantly)
                self._toggle_armed = False
                self._toggle_stop.clear()
                stop_req.set()

            def _release_watcher():
                # wait until the key is actually released (level, not one-shot)
                while held and self._ptt_hold and not self._stop.is_set():
                    time.sleep(0.02)
                # hold duration = release_time - press_time when the key is
                # already up (accurate even if the loop was busy when the tap
                # happened — time.time() - press_t would overcount the busy
                # window and turn a quick tap into a "long hold" -> instant stop)
                if not self._ptt_hold and self._release_time > 0:
                    held_s = self._release_time - press_t
                else:
                    held_s = time.time() - press_t
                if held_s < tap_ms / 1000.0:
                    # TAP -> toggle mode: keep listening until a second tap,
                    # the silence endpoint, or a no-speech timeout.
                    _toggle_wait("tap")
                    return
                # HOLD: normally stop shortly after release (catch the last
                # word) — BUT if the user released BEFORE any speech was
                # detected, they were still preparing to talk ("press, beep,
                # ...start talking"). Slamming the capture shut here was the
                # "stops after 1 sec, no time to talk" bug. Keep listening
                # hands-free until they speak + pause, tap again, or time out.
                if mic.endpointer.state == "idle":
                    _toggle_wait("release before speech")
                    return
                time.sleep(float(self.cfg.get("hotkey", "tail_s", default=0.2)))
                stop_req.set()
            threading.Thread(target=_release_watcher, daemon=True).start()
        elif self._ptt_supported:
            # forced toggle (interrupt flow): listen regardless of key state
            self._toggle_armed = True
            log_event("hotkey.toggle", on=True, why="interrupt")

            def _toggle_watcher():
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
                # forced toggle over: disarm (see tap path — stale _toggle_armed
                # made the next hold's release kill the capture instantly)
                self._toggle_armed = False
                self._toggle_stop.clear()
                stop_req.set()
            threading.Thread(target=_toggle_watcher, daemon=True).start()
        self._play_beep("start")
        # ONE input stream at a time on the Scarlett: pause the barge stream
        # while capturing (two concurrent InputStreams segfaulted PortAudio).
        resume_barge = self._pause_barge_stream()
        res = None
        max_capture_s = float(self.cfg.get("mic", "max_capture_s", default=30.0))
        try:
            # Zero-read retry: after rapid start/stop cycles (or an interrupt
            # mid-playback) the Scarlett can open a stream that delivers pure
            # zeros (peak_rms ~0.0) until it settles. Reopen ONCE before
            # telling the user nothing was heard — this was the "I need to
            # press Ctrl until the voice recovers" loop.
            for attempt in range(2):
                # Watchdog: sd.InputStream.read() can BLOCK FOREVER on a
                # wedged device (no timeout in sounddevice). Run the capture
                # in a thread and force-close the stream past the deadline so
                # the read() unblocks and the PTT loop never freezes.
                capture_result: list = []
                capture_done = threading.Event()

                def _do_capture():
                    try:
                        # `mic` is the enclosing non-None reference (checked
                        # at the top of _capture_utterance)
                        with mic.open():
                            capture_result.append(
                                mic.record_utterance(stop_req, barge_in=None,
                                                     silence_timeout_s=end_of_turn_s))
                    except Exception as exc:  # pragma: no cover
                        log_event("mic.capture.error", error=str(exc)[:160])
                        capture_result.append(None)
                    finally:
                        capture_done.set()

                t = threading.Thread(target=_do_capture, daemon=True,
                                     name=f"capture-{attempt}")
                t.start()
                if not capture_done.wait(timeout=max_capture_s + 3.0):
                    # stuck: force the stream closed to break the blocked read
                    log_event("mic.capture.timeout", attempt=attempt + 1)
                    try:
                        self._mic.close()
                    except Exception:
                        pass
                    t.join(timeout=3.0)
                    capture_result.append(None)
                res = capture_result[-1] if capture_result else None
                if res is None or res.wav_path is not None \
                        or res.peak_rms >= 0.0015:
                    break
                log_event("mic.zero_read.retry", attempt=attempt + 1,
                          ended_by=res.ended_by, peak_rms=round(res.peak_rms, 5))
                time.sleep(0.15)  # let the device settle before reopening
        except Exception as exc:
            log_event("mic.capture.error", error=str(exc)[:160])
            res = None
        finally:
            if resume_barge:
                self._start_barge_stream()
        self._play_beep("end")
        if res is None:
            self.speak("I couldn't hear you. Check the microphone.")
            return None
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

    def _transcribe_with_fallback(self, wav_path: str):
        """Primary ASR (parakeet/moonshine) + one low-cost fallback when it returns empty on a real wav.
        Moonshine is fastest fallback (~0.05s) so retries stay snappy even on empty captures.
        Returns the transcript that has text, or the original empty one if fallback also empty.
        """
        tr = self._asr.transcribe(wav_path)
        if not tr.empty:
            return tr
        # Only fallback when the wav had energy (peak_rms already logged as >=0.055 in the capture path) —
        # i.e. mic heard something but parakeet/moonshine blanked. Don't retry pure silence.
        try:
            kind = getattr(tr, "text", "") or ""
            # wavs with chars==0 but duration>0.6s deserve a retry
            cur_engine = getattr(self._asr, "engine", "") or self.cfg.get("asr","engine","parakeet")
            alt_engine = "moonshine" if cur_engine != "moonshine" else "parakeet"
            from .asr.sherpa import SherpaASR as _FallbackASR
            fb = _FallbackASR(engine=alt_engine, model_dir=self.cfg.model_dir(), provider="cpu", num_threads=2, language=self.cfg.get("asr","language","en"))
            fb.load()
            tr2 = fb.transcribe(Path(wav_path))
            if not getattr(tr2, "empty", True) and (tr2.text or "").strip():
                log_event("asr.fallback.success", primary=cur_engine, fallback=alt_engine, text=tr2.text[:120])
                return tr2
            log_event("asr.fallback.empty", primary=cur_engine, fallback=alt_engine)
        except Exception as exc:
            log_event("asr.fallback.failed", error=str(exc)[:160])
        return tr

    def _process_utterance(self, wav_path: str) -> None:
        t0 = time.time()
        if self._asr is None:
            return
        transcript = self._transcribe_with_fallback(wav_path)
        elapsed_asr = time.time() - t0
        log_event("orchestrator.timing", asr_ms=round(elapsed_asr * 1000, 1), text=transcript.text[:200])
        if transcript.empty:
            self.speak("I didn't catch that. Try again.")
            return
        if transcript.confidence and transcript.confidence < self.cfg.get("asr", "min_confidence", default=0.35):
            self.speak("Sorry, I didn't catch that. Could you repeat it?")
            return
        log_event("routing.input", text=transcript.text, confidence=round(transcript.confidence, 3))
        self._push_input(transcript.text, transcript.confidence, "mic")
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
        self._push_input(text, 0.0, "typed")
        intent = self.classifier.classify(text)
        log_event("routing.decision", text=text[:200], kind=intent.kind,
                  confidence=round(intent.confidence, 2), reason=intent.reason,
                  source=intent.source)
        self._route_intent(intent, text)
