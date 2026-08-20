"""Configuration loading for the voice bridge.

Load order:
  1. defaults (this module)
  2. YAML file at ~/.config/openclaw-voice-bridge/voice.yaml (or $VOICE_BRIDGE_CONFIG)
  3. environment overrides: VOICE_BRIDGE_<SECTION>_<KEY> (uppercase, dotted paths)

All sections map 1:1 to the example config in config/voice.example.yaml.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

DEFAULT_CONFIG_PATH = Path("~/.config/openclaw-voice-bridge/voice.yaml").expanduser()
DEFAULT_STATE_DIR = Path("~/.local/state/openclaw-voice-bridge").expanduser()
DEFAULT_DATA_DIR = Path("~/.local/share/openclaw-voice-bridge").expanduser()

DEFAULTS: Dict[str, Any] = {
    "hotkey": {
        "enabled": True,
        "hold_key": "ctrl_r",  # hold-to-talk
        "alt_hold_key": "",    # optional second key
        "tap_cancel": "esc",   # tap while speaking -> stop speech
        "tap_toggle": True,    # quick tap = start/stop listening; hold = classic PTT
        "tap_ms": 300,         # press shorter than this counts as a tap
        "toggle_no_speech_s": 5.0,  # toggle stops itself if nothing spoken
        "interrupt_on_ptt": True,   # Ctrl during a task = pause + listen for new instruction
    },
    "mic": {
        "device": None,        # None = system default input
        "sample_rate": 48000,  # device rate; resampled to 16k for ASR
        "channels": 2,         # capture N channels, use the loudest
        "blocksize": 480,      # ~10ms at 48k
        "gain": 4.0,           # pre-amplify quiet interfaces (Focusrite needs this)
        "min_rms": 0.0008,     # below this RMS the capture is treated as silence
    },
    "vad": {
        "enabled": True,
        "aggressiveness": 1,        # webrtcvad 0-3 (1 = lenient for quiet mics)
        "speech_start_ms": 180,     # frames of speech to consider "started"
        "silence_timeout_s": 1.2,   # trailing silence ends the utterance
        "max_utterance_s": 30.0,
        "rms_gate": 0.004,          # frame RMS floor for "voiced" (after gain); lowers false VAD on hiss
        "barge_in": True,           # speech while TTS is speaking -> cancel output
    },
    "feedback": {
        "beeps": True,              # short beep when recording starts/stops
    },
    "asr": {
        "provider": "whisper",      # whisper | personaplex
        "model": "base",            # tiny|base|small|medium (whisper)
        "device": "auto",           # auto|cuda|cpu
        "compute_type": "float16",
        "language": "en",
        "beam_size": 1,
        "min_confidence": 0.02,     # ~off: whisper avg_logprob for REAL mic speech is 0.05-0.2;
                                    # a higher gate rejects perfect transcripts ("didn't catch that" bug)
                                    # empty-text + filler-word checks handle real silence
    },
    "personaplex": {
        # Full-duplex lane: when enabled and reachable, PersonaPlex is the
        # front-facing voice (mic -> remote GPU -> speakers); the bridge keeps
        # a whisper tap for intent routing and speaks task results via TTS.
        "enabled": False,               # set True once a Vast.ai instance is up
        "provider": "personaplex",      # personaplex (needs 24 GB GPU) | realtime (OpenAI API, works today)
        "url": "wss://personaplex:8998/api/chat",  # Tailscale MagicDNS name or IP
        "ssl_insecure": True,           # self-signed cert on the Vast box
        "voice_prompt": "NATF2.pt",    # persona voice (NATF*/NATM*/VARF*/VARM*)
        "text_prompt": "You are a friendly, sharp personal assistant named Sage. "
                        "You enjoy good conversation and are curious about the "
                        "user's projects. Keep answers concise and natural.",
        "sample_rate": 24000,           # Mimi codec rate (matches the server)
        "whisper_tap": True,            # local ASR tap for intent routing
        "ptt_uses_tap": True,           # duplex mode: Ctrl routes the next tapped
                                        # utterance (no second mic stream — avoids
                                        # device conflict with the lane's mic)
        "duck_on_task_s": 8.0,          # mute PP + stop feeding it while a task runs
        "reconnect_s": 5.0,             # reconnect/backoff interval
        # legacy (pre-duplex) adapter knobs kept for compatibility
        "timeout_s": 5,
        "auto_start": False,
    },
    "realtime": {
        # OpenAI Realtime duplex lane (same orchestration, no GPU needed).
        # Active only when personaplex.enabled=true and personaplex.provider=realtime.
        "model": "gpt-4o-mini-realtime-preview",  # cheap lane; gpt-4o-realtime-preview for premium
        "key_env": "~/.hermes/.env",    # OPENAI_API_KEY lives here
        "reconnect_s": 5.0,
    },
    "hermes": {
        "enabled": False,               # route "ask Hermes ..." tasks to `hermes chat -q`
        "cmd": "hermes",
        "resume_session": "voice",      # continuity under a fixed session name
        "timeout_s": 600,
        "model": None,                  # None = hermes default (deepseek-v4-flash)
    },
    "local": {
        "enabled": True,                # whitelisted direct PC actions (volume, apps, ...)
    },
    "memory": {
        "enabled": True,                # load OpenClaw memory into the persona prompt;
        "workspace": "~/.openclaw/workspace",  # save session notes here
        "context_chars": 1500,          # max memory chars injected into text_prompt
        "save_notes": True,             # append session summary to memory/YYYY-MM-DD.md
    },
    "tts": {
        "provider": "piper",        # piper | edge | gateway
        "voice": "en_US-amy-medium",
        "sample_rate": 22050,
        "interrupt": True,          # allow barge-in / stop speaking
        "edge": {"voice": "en-US-JennyNeural", "rate": "+0%"},
        "gateway": {"model": None},  # None = gateway default
        "direct": True,          # strip emojis/pleasantries/filler from spoken output
    },
    "intent": {
        "llm_classifier": True,     # use ollama qwen3-vl:8b when heuristic is unsure
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "qwen3-vl:8b",
        "classify_timeout_s": 8.0,
        "conversation_backend": "deepseek",  # deepseek (fast API) | ollama (local) | agent (OpenClaw memory)
        "deepseek_url": "https://api.deepseek.com/chat/completions",
        "deepseek_model": "deepseek-v4-flash",
        "deepseek_env": "~/.hermes/.env",    # DEEPSEEK_API_KEY lives here
        # Perplexity: used for web/current-info questions ("if needed")
        "perplexity_enabled": True,
        "perplexity_url": "https://api.perplexity.ai/chat/completions",
        "perplexity_model": "sonar-pro",
        "perplexity_env": "~/.hermes/.env",  # PERPLEXITY_API_KEY lives here
        # In duplex mode PersonaPlex answers conversation directly; this backend
        # is only used when the PersonaPlex lane is down.
        "duplex_conversation_fallback": True,
    },
    "openclaw": {
        "gateway_url": None,     # auto-discovered: ws://127.0.0.1:18789
        "token_file": "~/.openclaw/.env",
        "agent": "main",
        "model": None,           # None = FOLLOW the agent's selected default model
                                 # (gateway default; e.g. deepseek flash today) — the
                                 # voice uses whatever LLM OpenClaw/Hermes is set to
        "session_prefix": "voice",
        "skills_nudge": True,     # append "use your skills/tools" to every task
        "task_timeout_s": 900,
        "max_history_chars": 12000,   # truncate history fed to summarizer
        "progress_updates": True,     # speak short status updates during long tasks
        "progress_min_interval_s": 45,
    },
    "safety": {
        "enabled": True,
        "confirm_destructive": True,
        "denylist": [   # phrases that are refused outright
            "ignore all previous instructions",
            "disable safety",
        ],
        "always_confirm": [   # extra patterns beyond the built-in destructive set
        ],
        "never_confirm": [    # patterns that skip confirmation even if destructive-looking
        ],
        "dry_run_first": False,   # prepend "dry run first" to agent coding tasks
        "spoken_confirmation": True,
    },
    "summarizer": {
        "provider": "ollama",   # ollama | extractive
        "ollama_model": "qwen3-vl:8b",
        "max_chars": 900,       # target spoken summary length
    },
    "logging": {
        "level": "INFO",
        "jsonl": True,          # structured event log (observability)
        "jsonl_path": None,     # default: $STATE_DIR/events.jsonl
    },
}


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _env_overrides() -> Dict[str, Any]:
    """Read VOICE_BRIDGE_* env vars into a nested dict."""
    out: Dict[str, Any] = {}
    for key, val in os.environ.items():
        if not key.startswith("VOICE_BRIDGE_"):
            continue
        path = key[len("VOICE_BRIDGE_"):].lower().split("_")
        node = out
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = val
    return out


@dataclass
class Config:
    data: Dict[str, Any]
    path: Optional[Path] = None

    # --- helpers ---
    def section(self, name: str) -> Dict[str, Any]:
        return self.data.get(name, {})

    def get(self, *path: str, default: Any = None) -> Any:
        node: Any = self.data
        for p in path:
            if not isinstance(node, dict) or p not in node:
                return default
            node = node[p]
        return node

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Config":
        cfg_path = Path(path) if path else Path(
            os.environ.get("VOICE_BRIDGE_CONFIG", DEFAULT_CONFIG_PATH))
        data = deep_merge(DEFAULTS, {})
        if cfg_path.exists():
            with open(cfg_path) as fh:
                loaded = yaml.safe_load(fh) or {}
            data = deep_merge(data, loaded)
        data = deep_merge(data, _env_overrides())
        return cls(data=data, path=cfg_path if cfg_path.exists() else None)

    # --- resolved paths ---
    def state_dir(self) -> Path:
        p = Path(self.get("logging", "jsonl_path") or DEFAULT_STATE_DIR)
        if not self.get("logging", "jsonl"):
            p = DEFAULT_STATE_DIR
        p.mkdir(parents=True, exist_ok=True)
        return p

    def data_dir(self) -> Path:
        p = DEFAULT_DATA_DIR
        p.mkdir(parents=True, exist_ok=True)
        return p

    def model_dir(self) -> Path:
        p = self.data_dir() / "models"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def session_key(self, suffix: str = "") -> str:
        prefix = self.get("openclaw", "session_prefix", default="voice")
        agent = self.get("openclaw", "agent", default="main")
        key = f"agent:{agent}:{prefix}"
        if suffix:
            key += f":{suffix}"
        return key

    def as_yaml(self) -> str:
        return yaml.safe_dump(self.data, sort_keys=False)
