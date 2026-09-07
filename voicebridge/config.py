"""Configuration loading for the voice bridge.

Load order:
  1. defaults (this module)
  2. YAML file at ~/.config/qubit-voicebridge/voice.yaml (or $VOICE_BRIDGE_CONFIG)
  3. environment overrides: VOICE_BRIDGE__SECTION__KEY (double underscores nest;
     single "_" inside a segment stays part of the key, e.g.
     VOICE_BRIDGE__INTENT__LOCAL_LLM__MODEL -> intent.local_llm.model).
     Legacy VOICE_BRIDGE_SECTION_KEY form still works but cannot reach keys
     that contain underscores.

This module is the single centralized home of the product identity and the
XDG-aware path resolver (Packet 8S): every caller (Config, CLI, install /
migrate flows) resolves canonical config/state/data/cache paths from here.
Legacy ``openclaw-voice-bridge`` / ``qubit-voice-agent`` locations are only
ever READ through explicit compatibility detection
(:func:`detect_legacy_configs`); nothing writes to a legacy path.

All sections map 1:1 to the example config in config/voice.example.yaml.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# ---------------------------------------------------------------------------
# Canonical product identity (Packet 8S). The internal import package stays
# ``voicebridge``; the two legacy names are compatibility-only.
# ---------------------------------------------------------------------------
PRODUCT_NAME = "qubit-voicebridge"
PRODUCT_TITLE = "Qubit VoiceBridge"
LEGACY_NAME = "openclaw-voice-bridge"
LEGACY_NAME_7D = "qubit-voice-agent"
LEGACY_NAMES = (LEGACY_NAME, LEGACY_NAME_7D)
_LEGACY_CONFIG_FILES = ("voice.yaml",)


def _xdg_base(env: str, default: str) -> Path:
    """XDG-aware base directory: honored env var wins (whitespace ignored),
    otherwise ``~/<default>``. Never returns ``.``."""
    raw = os.environ.get(env, "")
    if raw is not None and str(raw).strip():
        return Path(str(raw).strip()).expanduser()
    return Path("~").expanduser() / default


def canonical_config_dir() -> Path:
    return _xdg_base("XDG_CONFIG_HOME", ".config") / PRODUCT_NAME


def canonical_state_dir() -> Path:
    return _xdg_base("XDG_STATE_HOME", ".local/state") / PRODUCT_NAME


def canonical_data_dir() -> Path:
    return _xdg_base("XDG_DATA_HOME", ".local/share") / PRODUCT_NAME


def canonical_cache_dir() -> Path:
    return _xdg_base("XDG_CACHE_HOME", ".cache") / PRODUCT_NAME


def canonical_config_path() -> Path:
    return canonical_config_dir() / "voice.yaml"


DEFAULT_CONFIG_PATH = canonical_config_path()
DEFAULT_STATE_DIR = canonical_state_dir()
DEFAULT_DATA_DIR = canonical_data_dir()


def resolve_config_path(path: Optional[Path | str] = None) -> Path:
    """Resolve the effective config file path without touching the fs.

    Precedence: explicit ``path`` > non-empty ``VOICE_BRIDGE_CONFIG`` >
    canonical default. An empty/whitespace ``VOICE_BRIDGE_CONFIG`` safely
    falls back to the canonical default (never ``.``). A path that exists
    as a directory fails closed with ``ValueError`` (never a bare
    ``IsADirectoryError`` from a later ``open``)."""
    if path is not None:
        text = str(path).strip()
        if not text:
            return canonical_config_path()
        candidate = Path(text).expanduser()
    else:
        raw = os.environ.get("VOICE_BRIDGE_CONFIG", "")
        if raw is None or not str(raw).strip():
            return canonical_config_path()
        candidate = Path(str(raw).strip()).expanduser()
    if candidate.exists() and candidate.is_dir():
        raise ValueError(
            f"config path is a directory, refusing to read it as a file: "
            f"{candidate}")
    return candidate


def detect_legacy_configs() -> List[Dict[str, Any]]:
    """Explicit compatibility detection for legacy config locations.

    Read-only: only reports which legacy ``voice.yaml`` files exist and
    whether each legacy directory is a real directory, a healthy compat
    alias, or a mismatched alias. Never creates, moves, or writes anything.
    The only sanctioned READ of a legacy file goes through this record
    (:meth:`Config.load`); writes always target the canonical path."""
    out: List[Dict[str, Any]] = []
    for family in LEGACY_NAMES:
        legacy_dir = _xdg_base("XDG_CONFIG_HOME", ".config") / family
        rec: Dict[str, Any] = {"family": family, "dir": str(legacy_dir)}
        if not legacy_dir.exists() and not legacy_dir.is_symlink():
            rec["state"] = "ABSENT"
        elif legacy_dir.is_symlink():
            target = os.path.realpath(legacy_dir)
            rec["resolves"] = target
            rec["state"] = ("ALIAS_OK" if target == str(canonical_config_dir())
                            else "ALIAS_MISMATCH")
        else:
            rec["state"] = "REAL_DIR"
        for name in _LEGACY_CONFIG_FILES:
            cand = legacy_dir / name
            try:
                if cand.is_file():
                    rec.setdefault("files", []).append(str(cand))
            except OSError:
                continue
        out.append(rec)
    return out


def resolve_model_file(relative: Path | str) -> Path:
    """Canonical-first, legacy-fallback resolution for model artifact FILES.

    Returns the canonical ``<data>/models/<relative>`` path when it exists;
    otherwise the first existing legacy copy (read-only compatibility read —
    the caller must never write through the returned legacy path; downloads
    and bootstrap always target the canonical tree); otherwise the canonical
    path (so missing-file errors and acquisition flows name the canonical
    location). Never touches the filesystem beyond existence checks."""
    rel = Path(relative)
    canon = canonical_data_dir() / "models" / rel
    if canon.exists():
        return canon
    for family in LEGACY_NAMES:
        cand = _xdg_base("XDG_DATA_HOME", ".local/share") / family / "models" / rel
        try:
            if cand.exists():
                return cand
        except OSError:
            continue
    return canon

DEFAULTS: Dict[str, Any] = {
    "audio": {
        "output_device": "",        # portaudio device index for TTS (pin to headphones); "" = system default
        "output_device_name": "",   # more robust: match by name substring (e.g. "ALC892 Analog")
    },
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
        "device_name": "",     # stable name pin (Packet 7C audio contract); "" = default
        "sample_rate": 48000,  # device rate; resampled to 16k for ASR
        "channels": 2,         # capture N channels, use the loudest
        "blocksize": 480,      # ~10ms at 48k
        "gain": 3.0,           # pre-amplify quiet mics (too high (8x+64x) amplifies hiss and distorts ASR -> empty transcripts)
        "min_rms": 0.0005,          # below this the capture is treated as silence
        "normalize_target": 0.35,   # peak-normalize the clip to this fraction before ASR (stable level for quiet mics)
        "max_capture_s": 30.0,      # watchdog: force-close a wedged mic stream
    },
    "vad": {
        "enabled": True,
        "backend": "silero",         # silero (neural, sherpa-onnx) | webrtc
        "silero_model": "",          # empty = <model_dir>/silero_vad_v6.onnx (v6, Aug 2025: ~16% fewer errors than v5; falls back to v5 if v6 missing)
        "aggressiveness": 1,        # webrtcvad 0-3 (1 = lenient for quiet mics); unused for silero
        "speech_start_ms": 180,     # frames of speech to consider "started"
        "silence_timeout_s": 1.2,   # trailing silence ends the utterance
        "max_utterance_s": 30.0,
        "rms_gate": 0.0018,          # frame RMS floor for "voiced" (after gain); 0.004 was too strict — quiet Focusrite speech at peak 0.006 was dropped as silence
        "barge_in": True,           # speech while TTS is speaking -> cancel output
    },
    "feedback": {
        "beeps": True,              # short beep when recording starts/stops
    },
    "speaker_verify": {
        "enabled": True,            # gate barge-in on voice-ID when enrolled;
                                    # no enrollment = old behavior (any speech barges)
        "name": "user",
        "threshold": 0.65,          # cosine similarity; eres2net: self 0.7-0.96 / cross ~0.3
        "window_s": 2.0,            # analysis window for barge verification
        "enroll_seconds": 5,        # default enrollment recording length
    },
    "asr": {
        "provider": "sherpa",         # sherpa (Moonshine/Parakeet, 0 VRAM CPU) | whisper | personaplex
        "engine": "parakeet",        # sherpa engine: parakeet v3 (max accuracy, 25 langs) | moonshine (fastest)
        "provider_ep": "cpu",         # sherpa onnxruntime EP: cpu (0 VRAM) | cuda
        "num_threads": 4,            # sherpa/parakeet CPU threads (honoured by mic + fallback path)
        "model": "base",            # tiny|base|small|medium (whisper only)
        "device": "auto",           # auto|cuda|cpu (whisper only)
        "compute_type": "float16",
        "language": "en",
        "beam_size": 4,          # 4 = much better accent/broken-English, still <0.3s
        "fallback_engine": "moonshine",  # when primary returns 0 chars on a real wav, retry once with this (low resources, fastest)
        "min_confidence": 0.02,     # ~off: whisper avg_logprob for REAL mic speech is 0.05-0.2;
                                    # a higher gate rejects perfect transcripts ("didn't catch that" bug)
                                    # empty-text + filler-word checks handle real silence
    },
    "realtime": {
        # OpenAI Realtime duplex lane (same orchestration, no GPU needed).
        # Active only when personaplex.enabled=true and personaplex.provider=realtime.
        "model": "gpt-4o-mini-realtime-preview",  # cheap lane; gpt-4o-realtime-preview for premium
        "key_env": "~/.hermes/.env",    # OPENAI_API_KEY lives here
        "reconnect_s": 5.0,
    },
    "hermes": {
        "enabled": True,                # route "ask Hermes ..." tasks to `hermes chat -q`
                                    # (only fires on explicit "ask Hermes" phrasing)
        "cmd": "hermes",
        "resume_session": "voice",      # continuity under a fixed session name
        "timeout_s": 600,
        "model": None,                  # None = hermes default (deepseek-v4-flash)
        "prefer": True,                 # True = Hermes is default for all coding tasks; OpenClaw only for things Hermes can't do
    },
    "local": {
        "enabled": True,                # whitelisted direct PC actions (volume, apps, ...)
    },
    "memory": {
        "enabled": True,                # load OpenClaw memory into the persona prompt;
        "workspace": "~/.openclaw/workspace",  # save session notes here
        "context_chars": 1500,          # max memory chars injected into text_prompt
        "save_notes": True,             # append session summary to memory/YYYY-MM-DD.md
        # Packet 8M: optional GENOME-compatible semantic memory adapter.
        # DISABLED BY DEFAULT. provider none|hash|ollama. Ingest is always
        # LLM-free; embeddings are only contacted for recall and only when a
        # provider is enabled and configured. Bounds enforced by the adapter:
        # timeout_s in (0, 60], top_k integer in (0, 50], valid model id,
        # plain-http loopback-only ollama_url; malformed values are a typed
        # DEGRADED state, never silently coerced.
        "semantic": {
            "enabled": False,
            "provider": "none",         # none | hash (offline approx) | ollama
            "embed_model": "nomic-embed-text",
            "ollama_url": "http://127.0.0.1:11434",
            "timeout_s": 10.0,
            "top_k": 5,
        },
    },
    "tts": {
        "provider": "piper",        # piper (fast, cheap, local) | hybrid (piper short, openai nova long) | kokoro | edge | gateway | openai
        "voice": "en_US-amy-medium",
        "sample_rate": 22050,
        "kokoro": {"voice_sid": 0},  # Kokoro v0.19 EN: 11 voices, sid 0..10
        "interrupt": True,          # allow barge-in / stop speaking
        "edge": {"voice": "en-US-JennyNeural", "rate": "+0%"},
        "gateway": {"model": None},  # None = gateway default
        "openai": {"voice": "nova", "model": "gpt-4o-mini-tts", "key_env": "~/.hermes/.env", "instructions": "Speak with a warm, soft and sexy tone, with a subtle, elegant Japanese accent. Gentle, breathy and intimate, slightly playful — like a caring Japanese girlfriend. Speak naturally, at a relaxed pace, clear and affectionate."},
        "hybrid": {"threshold_words": 18, "piper_voice": "en_US-amy-medium", "openai_voice": "nova", "openai_model": "gpt-4o-mini-tts"},
        "direct": True,          # strip emojis/pleasantries/filler from spoken output
    },
    "intent": {
        "llm_classifier": True,     # enabled: +~0.3-0.6s but fixes broken-English routing (user approved 20-30% cost) — heuristic only (fast);
                                    # ambiguous taps default to conversation
        # Packet 4C routing cascade (feature-flagged; default OFF = pre-4C).
        "router": {
            "enabled": False,       # true = L0 deterministic rules run first
            "l1_enabled": False,    # true = also try CPU semantic matching
            "embed_model": "nomic-embed-text",
        },
        # Packet 5A opt-in Q5 canary (reversible; default OFF).
        "canary": {
            "q5_enabled": False,
        },
        # Packet 5C live integration (default ON; rollback flag).
        "compiler": {
            "mode": "5c",           # 5c = bounded FTS5 compiler; 4b = old
            "evidence_sources": [],
            "observe": True,
        },
        "ollama_url": "http://127.0.0.1:11434",
        "ollama_model": "qubit-brain4",
        "classify_timeout_s": 8.0,
        "conversation_backend": "local",
        "templates_path": "~/.config/qubit-voicebridge/task-templates.yaml",  # daily-task templates (spoken phrase -> known task)    # bridge IS the voice: local model answers ALL chat instantly
                                    # (chitchat + questions). Only TASKS go through OpenClaw/Hermes.
                                    # deepseek/perplexity = last-resort fallbacks
        # Hybrid local LLM: tiny 3-4B answers SHORT conversational turns locally
        # (~0.3s, private), DeepSeek handles long/complex/web asks. Flip enabled
        # to false to go cloud-only (DeepSeek every turn, GPU idle).
        "local_llm": {
            "enabled": True,           # bridge responds to ALL chat itself (<0.5s);
                                        # only tasks dispatch to OpenClaw/Hermes
            "model": "qubit-brain4",  # baked EN-only brain, no-think template (3.2GB resident).
                                    # phi-4-mini-abliterated is smarter in EN but BROKEN at RO (echoes/
                                    # wrong dates/hallucinates) — kept as optional EN-only smart mode.
            "url": "http://127.0.0.1:11434",
            "timeout_s": 8.0,           # local model chat timeout (was 4s — real 4s timeouts in the journal)
            "max_tokens": 100,         # local reply cap (short answers only)
        },
        # Packet 8R: supervised OpenCode coding adapter. DISABLED BY DEFAULT.
        # Master switch (packaged default False) ANDs with the state-file user
        # enablement (`voicebridge opencode enable`) and the emergency-stop
        # latch. No upstream code vendored; the local pinned binary is invoked
        # one-shot at arm's length. The child inherits only the strict
        # allowlist (PATH/LANG/LC_ALL/LC_MESSAGES/TZ) — there is no
        # environment pass-through knob (see voicebridge/opencode.py).
        "opencode": {
            "enabled": False,
            "binary": "~/.opencode/bin/opencode",
            "pinned_version": "1.18.29",
            "timeout_s": 300.0,
            "max_timeout_s": 600.0,
        },
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
        # Packet 8Q: optional frontier-provider gateway. DISABLED BY DEFAULT.
        # Enable only with an explicit allowlist, privacy tier, budgets, and
        # credential references (env:/file:). Never reads OpenCode's private
        # auth store. Availability/price subject to provider account.
        "frontier": {
            "enabled": False,
            "route": "local",  # local | frontier | auto (auto = opt-in escalation)
            "privacy_tier": "standard",
            "allowlist": [],
            "providers": {},
            "max_tokens": 512,
            "max_context_chars": 4000,
            "max_response_chars": 4000,
            "timeout_s": 20.0,
            "max_retries": 1,
            "rpm": 10,
            "max_concurrency": 2,
            "daily_usd": 1.0,
            "monthly_usd": 10.0,
        },
    },
    "openclaw": {
        "gateway_url": None,     # auto-discovered: ws://127.0.0.1:18789
        "token_file": "~/.openclaw/.env",
        "agent": "main",
        "model": None,           # None = FOLLOW the agent's selected default model
                                 # (gateway default; e.g. deepseek flash today) — the
                                 # voice uses whatever LLM OpenClaw/Hermes is set to
        "session_prefix": "voice",
        "ask_executor": False,          # True = ask "OpenClaw or Hermes?" before each coding task
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
        # Packet 3A: route local actions + local-script templates through the
        # capability broker (argv-only). false selects the LEGACY DIRECT PATH:
        # an UNSECURED ROLLBACK mode with no policy decisions — never describe
        # it as secured.
        "tool_broker": True,
    },
    "summarizer": {
        "provider": "ollama",   # ollama | extractive
        "ollama_model": "qubit-brain4",
        "max_chars": 900,       # extractive/summarizer input budget
        "max_words": 40,         # HARD cap on every spoken response (save time)
    },
    "reports": {
        "save_dir": None,       # None = <state_dir>/reports/<session_key>.md (full raw report)
        "verify": True,         # lightweight verify after each task (fs / git / reply sanity)
    },
    "logging": {
        "level": "INFO",
        "jsonl": True,          # structured event log (observability)
        "jsonl_path": None,     # default: $STATE_DIR/events.jsonl
    },
    "webui": {
        "host": "127.0.0.1",    # loopback default; LAN requires host override + token
        "token": "",            # REQUIRED when host is non-loopback (Packet 1B)
        "allowed_origins": [],  # exact Origin list for CORS; empty = no cross-origin
        # Dashboard frontend directory. Empty = repository-relative frontend/.
        # Set to an absolute path to serve an external dashboard checkout
        # (machine-specific value; keep it in the user config, not here).
        "frontend_dir": "",
    },
    "permissions": {
        # Packet 7E: restricted (default) / unrestricted (opt-in) permission
        # profiles, scoped grants, and skill manifests. state_path default =
        # <state_dir>/permissions.json (private, atomic, audited).
        "state_path": "",
        "default_mode": "restricted",
    },
    "actions": {
        # Packet 8F: typed, brokered, confirmation-gated browser/desktop/
        # editor actions. Contracts, previews, approvals, receipts, and audit
        # work with `enabled: false`; live adapter EXECUTION is a separate
        # approved rollout step (fixture simulations are explicit operator
        # paths and do not require this flag).
        "state_path": "",
        "enabled": False,
        "confirmation_ttl_s": 300.0,
        "max_preview_chars": 400,
    },
    "communications": {
        # Packet 8D: authorized hourly inbox monitor. The timer stays DISABLED
        # until the user explicitly installs+enables the repository template;
        # every mutate command is dry-run by default and audited.
        "monitor": {
            "enabled": False,
            "platforms": ["gmail"],
            "accounts": ["default"],
            # Packet 8I: the overlap lease MUST exceed the oneshot's
            # TimeoutStartSec (300 s) by a safety margin (180 s). A configured
            # value below the floor is clamped up at runtime; the effective
            # value is reported truthfully.
            "lease_ttl_s": 480.0,
            "retention_days": 90,
            "screenshot_days": 30,
            "desktop_notify": True,
            "voice_notify": False,
            # Packet 8I: bounded notification retry with a dead-letter bound.
            "notification_max_retries": 3,
            "voice": {
                "quiet_hours": "22:00-07:00",
                "timezone": "Europe/Bucharest",
                "max_words": 12,
            },
            "cdp_launch": False,      # optional approved Chromium launch hook
        },
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
    """Read VOICE_BRIDGE_* env vars into a nested dict.

    Double underscores separate nesting levels so keys containing underscores
    stay addressable: VOICE_BRIDGE__INTENT__LOCAL_LLM__MODEL ->
    intent.local_llm.model. The legacy single-underscore form still works but
    cannot reach underscored keys (plan P0-B).
    """
    out: Dict[str, Any] = {}
    for key, val in os.environ.items():
        if not key.startswith("VOICE_BRIDGE_"):
            continue
        if "__" in key:
            path = [p.lower() for p in key[len("VOICE_BRIDGE"):].split("__") if p]
        else:
            path = key[len("VOICE_BRIDGE_"):].lower().split("_")
        if not path:
            continue
        node = out
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = val
    return out


@dataclass
class Config:
    data: Dict[str, Any]
    path: Optional[Path] = None
    legacy_source: Optional[str] = None

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
        cfg_path = resolve_config_path(path)
        data = deep_merge(DEFAULTS, {})
        legacy_source: Optional[str] = None
        if cfg_path.exists() and cfg_path.is_file():
            with open(cfg_path) as fh:
                loaded = yaml.safe_load(fh) or {}
            data = deep_merge(data, loaded)
        elif path is None and not cfg_path.exists():
            # Explicit compatibility read: canonical absent, so consult the
            # detected legacy locations (read-only). Writes still target the
            # canonical path — legacy paths are never written after migration.
            for rec in detect_legacy_configs():
                for cand in rec.get("files", []):
                    try:
                        with open(cand) as fh:
                            loaded = yaml.safe_load(fh) or {}
                    except OSError:
                        continue
                    data = deep_merge(data, loaded)
                    legacy_source = cand
                    break
                if legacy_source is not None:
                    break
        data = deep_merge(data, _env_overrides())
        return cls(data=data, path=cfg_path if cfg_path.exists() else None,
                   legacy_source=legacy_source)

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
