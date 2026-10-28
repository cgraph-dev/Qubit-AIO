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

# Snap-confinement redirects are LOUD, never silent: every decision is
# recorded here and surfaced via xdg_report() (product paths diagnostic,
# status output, repair/doctor flows).
_XDG_REDIRECTS: List[Dict[str, Any]] = []
_XDG_VARS = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
             "XDG_CACHE_HOME")
_XDG_DEFAULTS = {"XDG_CONFIG_HOME": ".config", "XDG_DATA_HOME": ".local/share",
                 "XDG_STATE_HOME": ".local/state", "XDG_CACHE_HOME": ".cache"}


def _is_snap_confinement(p: Path) -> bool:
    """True when a path lives inside a snap sandbox (e.g. VS Code's
    ``~/snap/code/<rev>/...``). Snap-confined processes inherit sandbox
    XDG_*_HOME values that must not fragment the voice bridge's data tree."""
    return "snap" in p.parts


def _xdg_base(env: str, default: str) -> Path:
    """XDG-aware base directory: honored env var wins (whitespace ignored),
    otherwise ``~/<default>``. Never returns ``.``.

    A value inside snap confinement is NOT honored: the sandbox path would
    fragment installs per snap revision. It redirects to the real-home
    default LOUDLY (recorded in _XDG_REDIRECTS, reported by xdg_report())."""
    raw = os.environ.get(env, "")
    if raw is not None and str(raw).strip():
        candidate = Path(str(raw).strip()).expanduser()
        if _is_snap_confinement(candidate):
            effective = Path("~").expanduser() / default
            if not any(r["var"] == env and r["raw"] == str(raw).strip()
                       for r in _XDG_REDIRECTS):
                _XDG_REDIRECTS.append({
                    "var": env, "raw": str(raw).strip(),
                    "effective": str(effective),
                    "reason": "snap-confinement-redirect"})
            return effective
        return candidate
    return Path("~").expanduser() / default


def xdg_report() -> List[Dict[str, Any]]:
    """Effective XDG resolution for every directory variable: raw value,
    effective base, and source (``env`` | ``default`` |
    ``snap-confinement-redirect``). Read-only; the redirect is never silent."""
    out: List[Dict[str, Any]] = []
    for env in _XDG_VARS:
        raw = os.environ.get(env, "")
        raw_text = str(raw).strip() if raw is not None else ""
        effective = str(_xdg_base(env, _XDG_DEFAULTS[env]))
        redir = next((r for r in _XDG_REDIRECTS
                      if r["var"] == env and r["raw"] == raw_text), None)
        if redir is not None:
            source = "snap-confinement-redirect"
        elif raw_text:
            source = "env"
        else:
            source = "default"
        out.append({"var": env, "raw": raw_text, "effective": effective,
                    "source": source})
    return out


def canonical_config_dir() -> Path:
    return _xdg_base("XDG_CONFIG_HOME", ".config") / PRODUCT_NAME


def canonical_state_dir() -> Path:
    return _xdg_base("XDG_STATE_HOME", ".local/state") / PRODUCT_NAME


def canonical_data_dir() -> Path:
    return _xdg_base("XDG_DATA_HOME", ".local/share") / PRODUCT_NAME


def canonical_cache_dir() -> Path:
    return _xdg_base("XDG_CACHE_HOME", ".cache") / PRODUCT_NAME


def canonical_runtime_home() -> Path:
    """Canonical Qubit runtime (fork) home: ``<state_dir>/runtime``.

    Matches ``voicebridge.qubit_runtime.default_home()`` for the default
    profile — the one directory the vendored runtime, the session store, and
    the PTY/TUI share. Never the operator's external ``~/.hermes``.
    """
    return canonical_state_dir() / "runtime"


def canonical_runtime_env_path() -> Path:
    """The canonical runtime ``.env`` holding provider keys (never committed).

    Credential references of the form ``env:NAME`` fall back to this file when
    the process environment does not carry ``NAME``. It is read server-side
    only; the values are never logged, returned in diagnostics, printed, or
    echoed to any surface.
    """
    return canonical_runtime_home() / ".env"


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

#: Allowed values for ``voice.input_engine`` (Qubit speech-INPUT selection).
#: ``browser`` = supported Chrome SpeechRecognition, when available and
#: permitted; ``local`` = Qubit's streaming local STT (``voicebridge/asr``,
#: live partials over the authenticated STT WebSocket); ``terminal`` = the
#: local desktop/terminal capture path through the same local STT backend;
#: ``auto`` = prefer browser and fall back to local only with an explicit
#: visible reason. Any other value is out of contract and normalizes to
#: ``auto`` (the frontend ``normalizeVoiceEngine`` applies the same rule).
VOICE_INPUT_ENGINES: tuple = ("browser", "local", "terminal", "auto")


def normalize_input_engine(value: Any) -> str:
    """Canonicalize ``voice.input_engine``; unknown/missing fails to ``auto``.

    This setting changes speech input only — never the model, session, tools,
    policy, audit, confirmation, or e-stop path.
    """
    return value if value in VOICE_INPUT_ENGINES else "auto"


def _coerce_bool(value: Any) -> Optional[bool]:
    """Strict bool coercion mirroring the frontier policy parser.

    Returns ``None`` for anything ambiguous so a malformed override is
    dropped instead of silently treated as truthy.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
    return None


def model_profile_overrides(cfg: Any, profile_id: str) -> Dict[str, Any]:
    """Validated per-profile selection overrides (fail-closed: ``{}``).

    Reads ``intent.frontier.profile_overrides.<profile_id>`` from the
    canonical config. Only the recognized, non-secret keys are returned and
    only when well-formed:

    * ``policy_enabled`` (bool) — overrides ``intent.frontier.enabled``;
    * ``allowlisted`` (bool) — overrides the global ``provider/model`` check;
    * ``context_tokens`` (int > 0) — overrides the profile's declared window.

    Unknown keys and malformed values are dropped, so a typo can never widen
    access. This never holds a credential value.
    """
    pid = str(profile_id or "").strip()
    if not pid:
        return {}
    try:
        node = cfg.get("intent", "frontier", "profile_overrides", default=None)
    except Exception:
        return {}
    if not isinstance(node, dict):
        return {}
    entry = node.get(pid)
    if not isinstance(entry, dict):
        return {}
    out: Dict[str, Any] = {}
    if "policy_enabled" in entry:
        flag = _coerce_bool(entry.get("policy_enabled"))
        if flag is not None:
            out["policy_enabled"] = flag
    if "allowlisted" in entry:
        flag = _coerce_bool(entry.get("allowlisted"))
        if flag is not None:
            out["allowlisted"] = flag
    if "context_tokens" in entry:
        try:
            tokens = int(entry.get("context_tokens"))
        except (TypeError, ValueError):
            tokens = 0
        if tokens > 0:
            out["context_tokens"] = tokens
    return out


DEFAULT_MAX_PROMPT_CHARS = 8000
"""Ceiling for the FULL assembled local user turn (see local_llm
``_max_prompt_chars`` / ``intent.local_llm.max_prompt_chars``). The import-time
default; the config key is the authority at runtime."""

DEFAULTS: Dict[str, Any] = {
    # Speech input selection. Read-only setting for the /qubit-chat voice UI;
    # the frontend reads ``voice.input_engine`` to choose Chrome
    # SpeechRecognition (browser) vs Qubit's STT profile (local) vs auto
    # (browser with a visible local fallback reason). It never changes the
    # model, session, tools, policy, audit, confirmation, or e-stop path.
    "voice": {
        "input_engine": "auto",   # browser | local | terminal | auto
        # Streaming STT socket (live interim text). Tunables only affect the
        # local/terminal engine; the browser engine is the browser's own.
        "stt_streaming": {
            "enabled": True,           # serve /agent/api/audio/stt/ws
            "partial_interval_ms": 300,  # hint; the session also rate-limits decodes
            "max_seconds": 120,        # per-request audio cap (server enforces)
            # Browser-side capture conditioning (getUserMedia constraints).
            "noise_suppression": True,
            "echo_cancellation": True,
            "auto_gain_control": True,
        },
    },
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
        # Streaming STT engine for live /qubit-chat transcription (the STT
        # WebSocket). `auto` prefers an installed true-streaming pack and
        # otherwise decodes incrementally with the offline `engine` above --
        # no second backend. True-streaming packs (int8, CPU real-time):
        #   zipformer-streaming        -> sherpa-onnx-streaming-zipformer-en-20M-2023-02-17 (low-spec)
        #   zipformer-streaming-small  -> sherpa-onnx-streaming-zipformer-small-bilingual-zh-en-2023-02-16
        #   nemotron-streaming         -> sherpa-onnx-nemotron-speech-streaming-en-0.6b-80ms-int8-2026-04-25 (low latency)
        "streaming_engine": "auto",
        # Offline fallback model for live partials: the fast one (Moonshine)
        # even when the accurate `engine` above is Parakeet. Whole-file
        # transcription still uses `engine`.
        "streaming_window_engine": "moonshine",
        # English-only: the local streaming candidates are English models; pin
        # the language and never route non-English audio to them.
        "english_only": True,
        # 0 = use the model pack's built-in chunk size; a nonzero value is a
        # request hint (only the window engine's frame size honours it).
        "streaming_chunk_ms": 0,
        # Prewarm policy at service start: auto|offline-window|<engine>|false.
        "prewarm": "auto",
        "streaming_partial_min_interval_s": 0.25,  # min gap between window decodes (bounds CPU)
        "streaming_silence_rms": 0.004,            # frames below this are not fed to the recognizer
        "provider_ep": "cpu",         # sherpa onnxruntime EP: cpu (0 VRAM) | cuda
        # Execution provider for LIVE streaming only (empty = inherit
        # provider_ep). cuda requires the GPU-enabled sherpa-onnx wheel plus the
        # nvidia-*-cu12 pip libraries; it falls back to cpu automatically.
        "streaming_provider_ep": "",
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
        "key_env": "~/.config/qubit-voicebridge/.env",    # OPENAI_API_KEY lives here
        "reconnect_s": 5.0,
    },
    "agent": {
        "enabled": True,                # Qubit-native agent turns + worker sessions
        "model": None,                  # None = Qubit default (qubit-brain)
        "session_prefix": "voice",
        "skills_nudge": True,     # append "use your skills/tools" to every task
        "task_timeout_s": 900,
        "max_history_chars": 12000,   # truncate history fed to summarizer
        "progress_updates": True,     # speak short status updates during long tasks
        "progress_min_interval_s": 45,
        # Dynamic context policy (voicebridge/context_policy.py). Context size
        # is a latency/resource setting, never an intelligence limit: a simple
        # voice turn uses the fast window, a long/tool-heavy conversation
        # expands, and a request the local window cannot hold escalates to
        # ``escalation_model`` (an empty value fails closed with
        # CONTEXT_ESCALATION_UNAVAILABLE rather than silently truncating).
        "context": {
            "min_tokens": 2048,       # fast window for simple turns
            "default_tokens": 4096,   # window when history/complexity needs more
            "max_tokens": 16384,      # largest window before escalation
            "fast_messages": 6,       # rolling window for the fast tier
            "default_messages": 16,   # rolling window for the expanded tier
            "max_messages": 48,       # hard cap on messages fed to the model
            "escalation_model": "",   # registered profile id; "" = fail closed
            "summary_head_chars": 280,  # chars kept per older message in the summary
        },
    },
    "local": {
        "enabled": True,                # whitelisted direct PC actions (volume, apps, ...)
    },
    "memory": {
        "enabled": True,                # load local memory into the persona prompt;
        "workspace": "~/.config/qubit-voicebridge/workspace",  # save session notes here
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
        # Piper voice renderer: sherpa (sherpa-onnx OfflineTts — shares the ASR
        # ONNX Runtime, no load-order coupling) | piper (third-party piper-tts).
        # Empty = auto (sherpa when the vits-piper pack is present).
        "piper_backend": "sherpa",
        "kokoro": {"voice_sid": 0},  # Kokoro v0.19 EN: 11 voices, sid 0..10
        "interrupt": True,          # allow barge-in / stop speaking
        "edge": {"voice": "en-US-JennyNeural", "rate": "+0%"},
        "gateway": {"model": None},  # None = gateway default
        "openai": {"voice": "nova", "model": "gpt-4o-mini-tts", "key_env": "~/.config/qubit-voicebridge/.env", "instructions": "Speak with a warm, soft and sexy tone, with a subtle, elegant Japanese accent. Gentle, breathy and intimate, slightly playful — like a caring Japanese girlfriend. Speak naturally, at a relaxed pace, clear and affectionate."},
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
            # L1.5 EmbeddingGemma-2 registry augmenter (default OFF). Only
            # consulted after L0 declined AND resolve() returned UNKNOWN; it
            # can never decide a confirmation-gated/high-risk entry.
            "l1_5_enabled": False,
            "l1_5_threshold": 0.87,
            "l1_5_margin": 0.002,
            "l1_5_model_dir": "~/.local/share/qubit-voicebridge/models/embeddinggemma-2-onnx",
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
        "ollama_model": "qubit-brain",
        "classify_timeout_s": 8.0,
        "conversation_backend": "local",
        "templates_path": "~/.config/qubit-voicebridge/task-templates.yaml",  # daily-task templates (spoken phrase -> known task)    # bridge IS the voice: local model answers ALL chat instantly
                                    # (chitchat + questions). Only TASKS go through Qubit/Qubit.
                                    # deepseek/perplexity = last-resort fallbacks
        # Hybrid local LLM: tiny 3-4B answers SHORT conversational turns locally
        # (~0.3s, private), DeepSeek handles long/complex/web asks. Flip enabled
        # to false to go cloud-only (DeepSeek every turn, GPU idle).
        "local_llm": {
            "enabled": True,           # bridge responds to ALL chat itself (<0.5s);
                                        # only tasks dispatch to Qubit/Qubit
            "model": "qubit-brain",  # baked EN-only brain, no-think template (3.2GB resident).
                                    # phi-4-mini-abliterated is smarter in EN but BROKEN at RO (echoes/
                                    # wrong dates/hallucinates) — kept as optional EN-only smart mode.
            "url": "http://127.0.0.1:11434",
            "timeout_s": 8.0,           # local model chat timeout (was 4s — real 4s timeouts in the journal)
            "max_tokens": 100,         # local reply cap (short answers only)
            # Hard ceiling on the FULL assembled local user turn (compiled
            # context + instructions + the user's own text). The raw user text
            # has its own bound (chat_transport MAX_TEXT_CHARS); this bounds
            # what the assembly can grow to, so an oversized prompt is trimmed
            # (context first, question kept) instead of reaching a provider or
            # runner that rejects it. See local_llm._max_prompt_chars.
            "max_prompt_chars": 8000,
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
        "deepseek_env": "~/.config/qubit-voicebridge/.env",    # DEEPSEEK_API_KEY lives here
        # Perplexity: used for web/current-info questions ("if needed")
        "perplexity_enabled": True,
        "perplexity_url": "https://api.perplexity.ai/chat/completions",
        "perplexity_model": "sonar-pro",
        "perplexity_env": "~/.config/qubit-voicebridge/.env",  # PERPLEXITY_API_KEY lives here
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
            # Per-profile selection overrides, keyed by profile id (for
            # example the migrated ``qubit-model``). Fail-closed: an absent
            # entry leaves the global policy/allowlist AND the profile's own
            # declared context window unchanged. Only these three recognized,
            # non-secret keys are honored:
            #   policy_enabled: bool   -> overrides intent.frontier.enabled
            #   allowlisted:    bool   -> overrides the global allowlist check
            #   context_tokens: int>0  -> overrides the profile's declared window
            # An unknown key or malformed value is ignored, so a typo can never
            # silently widen access. The global gateway still enforces its own
            # policy/allowlist at execution time.
            "profile_overrides": {},
        },
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
        # Route local actions + local-script templates through the capability
        # broker (argv-only). Setting this false NO LONGER selects a direct
        # path: the orchestrator fails closed with a typed BROKER_DISABLED
        # denial (Packet 8O — see tests/test_packet8o.py no-bypass regression),
        # so a configured script or local action can never execute around
        # grants, scopes, confirmation, audit, resource limits, or emergency
        # stop. Power/autonomous mode pre-authorizes scopes THROUGH the broker;
        # it never turns the broker off.
        "tool_broker": True,
    },
    "summarizer": {
        "provider": "ollama",   # ollama | extractive
        "ollama_model": "qubit-brain",
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
            data = _migrate_legacy_sections(data)
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
        data = _migrate_legacy_sections(data)
        return cls(data=data, path=cfg_path if cfg_path.exists() else None,
                   legacy_source=legacy_source)


    # --- resolved paths (runtime XDG resolution — never the import-time
    # constants, so CLI, service, installer, and status always agree) ---
    def state_dir(self) -> Path:
        p = Path(self.get("logging", "jsonl_path") or canonical_state_dir())
        if not self.get("logging", "jsonl"):
            p = canonical_state_dir()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def data_dir(self) -> Path:
        p = canonical_data_dir()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def model_dir(self) -> Path:
        p = self.data_dir() / "models"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def session_key(self, suffix: str = "") -> str:
        prefix = self.get("agent", "session_prefix", default="voice")
        key = f"qubit:{prefix}"
        if suffix:
            key += f":{suffix}"
        return key

    def as_yaml(self) -> str:
        return yaml.safe_dump(self.data, sort_keys=False)


# Map legacy assistant sections onto the unified `agent` section.
# Documented migration compat (docs/archive/2026-09-21/QUBIT_MIGRATION.md); the legacy names
# survive only here and in that doc, never in product behavior.
_LEGACY_SECTION_MAP = {
    "openclaw": ("model", "session_prefix", "skills_nudge",
                 "task_timeout_s", "max_history_chars",
                 "progress_updates", "progress_min_interval_s"),
    "hermes": ("enabled", "model"),
}


def _migrate_legacy_sections(data: Dict[str, Any]) -> Dict[str, Any]:
    """Fold retired `qubit`/`qubit` user keys into `agent`.

    Explicit `agent` keys always win; legacy sections are dropped after
    folding so exactly one configuration tree remains at runtime.
    """
    if not isinstance(data, dict):
        return data
    agent = data.get("agent")
    if not isinstance(agent, dict):
        agent = {}
        data["agent"] = agent
    for section, keys in _LEGACY_SECTION_MAP.items():
        legacy = data.get(section)
        if not isinstance(legacy, dict):
            continue
        for key in keys:
            if key in legacy and key not in agent:
                agent[key] = legacy[key]
        data.pop(section, None)
    return data
