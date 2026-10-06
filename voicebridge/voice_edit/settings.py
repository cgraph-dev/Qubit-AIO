"""Prompt / routing settings for the voice editing surface.

Every write is an atomic read-modify-write of the canonical config with a
timestamped backup. User customizations live under ``voice_edit.prompts`` and
are never touched by a default-prompt update; restoring a default simply deletes
the user copy for that action.
"""
from __future__ import annotations

import copy
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .prompts import DEFAULT_PROMPTS, default_prompts_public
from .schema import ACTIONS

SETTINGS_KEYS = ("routing", "local_profile", "api_profile", "allow_api",
                 "confirm_before_replace", "auto_send", "insertion_method",
                 "default_action", "default_prompt", "enabled", "asr_device",
                 "stt_engine", "stt_mode", "stt_model", "router",
                 "hotkey_mode", "service_url", "stt_ws_url", "request_ttl_s",
                 "brain_router", "brain_timeout_s", "wake", "block_fullscreen",
                 "beep", "debug_log", "mic_gain", "mic_target_peak",
                 "release_tail_ms", "noise_suppression", "disable_key_repeat",
                 "text_commands", "capture_source", "agc",
                   "speak_answers", "history_panel", "chat_model",
                   "detail_model", "detail_cues", "voice_recent_turns",
                    "verbatim_transcript", "custom_commands",
                    "sleep_mode", "waveform_visible", "waveform_ingame",
                    "web_paid_first", "ollama_model", "runner_alias",
                    # Wake words + wake routing are Qubit-owned JSON stores
                    # (never persisted into voice_edit YAML); accepted here so
                    # the one settings surface can read and mutate them.
                    # ``wake_words_default`` is view-only (the built-in list)
                    # and deliberately not an accepted key.
                    "wake_words", "wake_words_add", "wake_words_remove",
                    "wake_routing", "wake_route_add", "wake_route_remove")
# NOTE: "answer_questions" was removed from persistence+view (2026-09-30): it
# persisted with zero readers repo-wide, and wiring a service consumer would
# contradict the honest-UI invariant (no control may act without a rendered
# checkbox). A stored ``voice_edit.answer_questions`` key is still tolerated
# on read (never rejected, never deleted by a merge); it is simply no longer
# written, validated, or surfaced.


def _kws_available(cfg: Any) -> bool:
    try:
        from .kws import find_model_dir
        return find_model_dir(cfg) is not None
    except Exception:
        return False


def _wake_words_list() -> list:
    """Effective wake-word list (persisted, else defaults). Never raises."""
    try:
        from . import wake_words
        return list(wake_words.active_triggers())
    except Exception:
        return []


def _wake_words_defaults() -> list:
    """The built-in default wake-word list. Never raises."""
    try:
        from . import wake_words
        return list(wake_words.DEFAULT_TRIGGERS)
    except Exception:
        return ["hey qubit"]


def _wake_routing_view() -> Dict[str, Any]:
    """The persisted wake-routing config as a JSON-safe dict. Never raises."""
    try:
        from . import wake_routing
        return wake_routing.load_config().to_dict()
    except Exception:
        return {"version": 1, "defaultTarget": {"mode": "current"},
                "routes": [], "updatedAtMs": 0}


def _read_cfg(cfg: Any, *path: str, default: Any = None) -> Any:
    try:
        return cfg.get(*path, default=default)
    except Exception:
        return default


def _config_path(cfg: Any) -> Path:
    path = getattr(cfg, "path", None)
    if path:
        return Path(path)
    from ..config import resolve_config_path
    return resolve_config_path()


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _write_yaml(path: Path, data: Dict[str, Any]) -> Path:
    bak = path.with_name(f"{path.name}.voice-edit.{int(time.time())}.bak")
    try:
        if path.exists():
            bak.write_bytes(path.read_bytes())
    except OSError:
        pass
    tmp = path.with_name(f".{path.name}.voice-edit.tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)
    return bak


def _deep_merge(base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in update.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _sync_live_cfg(cfg: Any, patch: Dict[str, Any]) -> None:
    """Reflect persisted section changes on the live Config so a running
    service sees the new value without a restart (the file remains the
    authority). ``patch`` is ``{section: {key: value}}`` (e.g. voice_edit /
    tts / intent)."""
    data = getattr(cfg, "data", None)
    if not isinstance(data, dict):
        return
    for section, values in patch.items():
        if not isinstance(values, dict):
            continue
        node = data.setdefault(section, {})
        if not isinstance(node, dict):
            node = {}
            data[section] = node
        for k, v in values.items():
            if section == "voice_edit" and k == "per_app" and isinstance(
                    v, dict) and not v:
                # PUT per_app:{} clears stored mappings (a merge would keep
                # them); anything else still deep-merges per key.
                node.pop("per_app", None)
                continue
            if isinstance(v, dict) and isinstance(node.get(k), dict):
                node[k] = {**node[k], **v}
            else:
                node[k] = v
    _invalidate_llm_caches(patch)


def _invalidate_llm_caches(patch: Dict[str, Any]) -> None:
    """Drop lazy local-runner caches after an intent.local_llm.* change.

    Clears in-memory snapshots only (the local style snapshot and the
    runner-contract digest cache); in-flight requests and the file authority
    are untouched, so new turns pick up fresh temperature/think/model values
    with no restart.
    """
    try:
        intent = patch.get("intent")
    except Exception:
        return
    if not isinstance(intent, dict):
        return
    llm = intent.get("local_llm")
    runner = intent.get("runner")
    if not (isinstance(llm, dict) and llm) and not (
            isinstance(runner, dict) and runner):
        return
    try:
        from ..local_llm import reset_style_cache
        reset_style_cache()
    except Exception:
        pass
    try:
        from ..runner_contract import reset_cache
        reset_cache()
    except Exception:
        pass


def list_local_models(cfg: Any) -> list:
    """Installed local Ollama models (name + size) for the settings UI.

    So a user on any machine sees what THEY can pick (a weak PC's small models,
    a strong PC's bigger ones) instead of a hardcoded list. Best-effort: an
    unreachable runner returns []."""
    try:
        import json
        import urllib.request
        from ..runner_contract import resolve as _rc
        base = (_rc(cfg).url or "http://127.0.0.1:11434").rstrip("/")
        with urllib.request.urlopen(base + "/api/tags", timeout=3) as r:
            data = json.loads(r.read().decode())
        out = []
        for m in data.get("models") or []:
            name = str(m.get("name", "") or "")
            if name:
                out.append({"name": name, "size_bytes": int(m.get("size") or 0)})
        return sorted(out, key=lambda x: x["size_bytes"])
    except Exception:  # noqa: BLE001 - UI convenience, never fatal
        return []


def settings_view(cfg: Any) -> Dict[str, Any]:
    user_prompts = _read_cfg(cfg, "voice_edit", "prompts", default={}) or {}
    if not isinstance(user_prompts, dict):
        user_prompts = {}
    defaults = default_prompts_public()
    for row in defaults:
        row["customized"] = bool(str(user_prompts.get(row["action"], "")).strip())
        row["effective_text"] = (str(user_prompts[row["action"]])
                                 if row["customized"] else row["text"])
    per_app = _read_cfg(cfg, "voice_edit", "per_app", default={}) or {}
    return {
        "enabled": bool(_read_cfg(cfg, "voice_edit", "enabled", default=False)),
        "routing": str(_read_cfg(cfg, "voice_edit", "routing",
                                 default="auto") or "auto"),
        "local_profile": str(_read_cfg(cfg, "voice_edit", "local_profile",
                                       default="qubit-brain4") or
                             "qubit-brain4"),
        "api_profile": str(_read_cfg(cfg, "voice_edit", "api_profile",
                                     default="") or ""),
        "allow_api": bool(_read_cfg(cfg, "voice_edit", "allow_api",
                                    default=True)),
        "confirm_before_replace": bool(_read_cfg(
            cfg, "voice_edit", "confirm_before_replace", default=True)),
        "auto_send": bool(_read_cfg(cfg, "voice_edit", "auto_send",
                                    default=False)),
        "insertion_method": str(_read_cfg(
            cfg, "voice_edit", "insertion_method",
            default="clipboard_paste") or "clipboard_paste"),
        "default_prompt": str(_read_cfg(cfg, "voice_edit", "default_action",
                                        default="clarify") or "clarify"),
        "asr_device": str(_read_cfg(cfg, "voice_edit", "asr_device",
                                    default="") or ""),
        "stt_engine": str(_read_cfg(cfg, "voice_edit", "stt_engine",
                                    default="") or ""),
        # STT accuracy/speed mode for the local engine. "streaming" favours
        # latency; "accurate" favours quality. Default is intentionally
        # non-destructive: reading a config without these keys yields the
        # defaults below, but they are only persisted when a user PUTs them.
        "stt_mode": str(_read_cfg(cfg, "voice_edit", "stt_mode",
                                  default="accurate") or "accurate"),
        # Local STT model size. RAM/VRAM note: "small" ~= 0.5 GB; "medium" and
        # "large-v3" are heavier and opt-in.
        "stt_model": str(_read_cfg(cfg, "voice_edit", "stt_model",
                                   default="small") or "small"),
        # The one voice router: "deterministic" (parser only) or "brain"
        # (provider-assisted action mapping, still preview-confirmed).
        "router": str(_read_cfg(cfg, "voice_edit", "router",
                                default="deterministic") or "deterministic"),
        "hotkey_mode": str(_read_cfg(cfg, "voice_edit", "hotkey_mode",
                                     default="hold") or "hold"),
        "request_ttl_s": _read_cfg(cfg, "voice_edit", "request_ttl_s",
                                   default=300.0),
        "brain_router": bool(_read_cfg(cfg, "voice_edit", "brain_router",
                                       default=True)),
        "brain_timeout_s": float(_read_cfg(cfg, "voice_edit",
                                           "brain_timeout_s", default=4.0) or
                                 4.0),
        "block_fullscreen": bool(_read_cfg(cfg, "voice_edit",
                                           "block_fullscreen", default=True)),
        "beep": bool(_read_cfg(cfg, "voice_edit", "beep", default=True)),
        "debug_log": bool(_read_cfg(cfg, "voice_edit", "debug_log",
                                    default=False)),
        "mic_gain": float(_read_cfg(cfg, "voice_edit", "mic_gain",
                                    default=6.0) or 6.0),
        "mic_target_peak": float(_read_cfg(cfg, "voice_edit",
                                           "mic_target_peak", default=0.5) or
                                 0.5),
        "release_tail_ms": int(_read_cfg(cfg, "voice_edit",
                                         "release_tail_ms", default=500) or
                               500),
        "noise_suppression": bool(_read_cfg(cfg, "voice_edit",
                                            "noise_suppression", default=False)),
        "disable_key_repeat": bool(_read_cfg(cfg, "voice_edit",
                                             "disable_key_repeat",
                                             default=True)),
        "text_commands": bool(_read_cfg(cfg, "voice_edit", "text_commands",
                                        default=False)),
        "capture_source": str(_read_cfg(cfg, "voice_edit", "capture_source",
                                        default="auto") or "auto"),
        "agc": bool(_read_cfg(cfg, "voice_edit", "agc", default=True)),
        "speak_answers": bool(_read_cfg(cfg, "voice_edit", "speak_answers",
                                        default=True)),
        "verbatim_transcript": bool(_read_cfg(cfg, "voice_edit",
                                              "verbatim_transcript",
                                              default=False)),
        # Manual sleep mode (owner decision 2026-10-03 §5 #1): set by saying
        # "going to sleep" / "I'm going to sleep"; gates UNPROMPTED speech
        # only (completions/briefings), never on-request answers. Cleared by
        # the next directed "hey qubit".
        "sleep_mode": bool(_read_cfg(cfg, "voice_edit", "sleep_mode",
                                     default=False)),
        # Dashboard waveform widget (voice-final lane): set by saying
        # "show waveform" / "hide waveform" or the Voice settings switch.
        # Default False (owner 2026-10-04): the OS orb is the only
        # always-on waveform; False unmounts the widget entirely
        # (no polling/rAF while hidden). "show waveform" re-mounts.
        "waveform_visible": bool(_read_cfg(cfg, "voice_edit",
                                           "waveform_visible",
                                           default=False)),
        # In-game orb override (owner 2026-10-04): set by saying
        # "show waveform ingame" / "hide waveform ingame" or the Voice
        # settings switch. Default False = today's auto-hide (the orb
        # withdraws while a fullscreen game is focused and returns on
        # alt-tab). True keeps the orb mapped over fullscreen too. This is
        # a SEPARATE flag from waveform_visible (dashboard widget) — bare
        # "show/hide waveform" never touches it.
        "waveform_ingame": bool(_read_cfg(cfg, "voice_edit",
                                          "waveform_ingame",
                                          default=False)),
        "history_panel": bool(_read_cfg(cfg, "voice_edit", "history_panel",
                                        default=True)),
        "chat_model": str(_read_cfg(cfg, "voice_edit", "chat_model",
                                    default="") or ""),
        "detail_model": str(_read_cfg(cfg, "voice_edit", "detail_model",
                                      default="") or ""),
        "detail_cues": list(_read_cfg(cfg, "voice_edit", "detail_cues",
                                      default=[]) or []),
        "voice_recent_turns": int(_read_cfg(cfg, "voice_edit",
                                            "voice_recent_turns",
                                            default=4) or 4),
        # Cross-section knobs exposed on this form (stored under tts.* /
        # intent.local_llm.* so they are portable config, not code).
        "tts_provider": str(_read_cfg(cfg, "tts", "provider",
                                      default="piper") or "piper"),
        "tts_voice": str(_read_cfg(cfg, "tts", "voice",
                                   default="en_US-amy-medium") or ""),
        "tts_kokoro_voice_sid": int(_read_cfg(
            cfg, "tts", "kokoro", "voice_sid", default=1) or 1),
        "local_model": str(_read_cfg(cfg, "intent", "local_llm", "model",
                                     default="") or ""),
        "local_temperature": float(_read_cfg(
            cfg, "intent", "local_llm", "temperature", default=0.7) or 0.7),
        "local_think": bool(_read_cfg(cfg, "intent", "local_llm", "think",
                                      default=False)),
        # Slice-2 web/model knobs (authority: intent.*; voice_edit.* kept as
        # a UI round-trip mirror). Readers: chat_transport._web_paid_first
        # (intent.web_paid_first), runner_contract (intent.runner.alias /
        # intent.ollama_model).
        "web_paid_first": bool(_read_cfg(cfg, "intent", "web_paid_first",
                                         default=None)
                               if _read_cfg(cfg, "intent", "web_paid_first",
                                            default=None) is not None
                               else _read_cfg(cfg, "voice_edit",
                                              "web_paid_first",
                                              default=False)),
        "ollama_model": str(_read_cfg(cfg, "intent", "ollama_model",
                                      default=None)
                            or _read_cfg(cfg, "voice_edit", "ollama_model",
                                         default="") or ""),
        "runner_alias": str(_read_cfg(cfg, "intent", "runner", "alias",
                                      default=None)
                            or _read_cfg(cfg, "voice_edit", "runner_alias",
                                         default="") or ""),
        "wake": {
            "enabled": bool(_read_cfg(cfg, "voice_edit", "wake", "enabled",
                                      default=False)),
            "phrase": str(_read_cfg(cfg, "voice_edit", "wake", "phrase",
                                    default="hey qubit") or "hey qubit"),
            "silence_ms": int(_read_cfg(cfg, "voice_edit", "wake", "silence_ms",
                                        default=900) or 900),
            "rms_gate": float(_read_cfg(cfg, "voice_edit", "wake", "rms_gate",
                                        default=0.0018) or 0.0018),
            "engine": str(_read_cfg(cfg, "voice_edit", "wake", "engine",
                                    default="auto") or "auto"),
            "vad": str(_read_cfg(cfg, "voice_edit", "wake", "vad",
                                 default="auto") or "auto"),
            "kws_threshold": float(_read_cfg(cfg, "voice_edit", "wake",
                                             "kws_threshold", default=0.25) or
                                   0.25),
            "asr_fallback": _read_cfg(cfg, "voice_edit", "wake",
                                        "asr_fallback", default=True) is not False,
            "asr_fallback_max_s": float(_read_cfg(
                cfg, "voice_edit", "wake", "asr_fallback_max_s",
                default=15.0) or 15.0),
            "max_s": float(_read_cfg(
                cfg, "voice_edit", "wake", "max_s",
                default=15.0) or 15.0),
            "arm_window_s": float(_read_cfg(
                cfg, "voice_edit", "wake", "arm_window_s",
                default=6.0) or 6.0),
            "kws_available": _kws_available(cfg),
        },
        # Wake words + wake routing stores (read-only view). Mutations go
        # through ``update_voice_edit`` onto the same two JSON stores.
        "wake_words": _wake_words_list(),
        "wake_words_default": _wake_words_defaults(),
        "wake_routing": _wake_routing_view(),
        "prompts": defaults,
        "per_app": per_app,
        "custom_commands": list(
            _read_cfg(cfg, "voice_edit", "custom_commands", default=[]) or []),
        "prompt_versions": {a: s.version for a, s in DEFAULT_PROMPTS.items()},
    }


def _validate_action(action: str) -> str:
    a = str(action or "").strip()
    if a not in ACTIONS:
        from .schema import VoiceEditError
        raise VoiceEditError("VOICE_EDIT_UNKNOWN_ACTION",
                             f"unknown action {a!r}")
    return a


def _validate_setting_action(action: Any) -> str:
    """Validate a default-action *setting*.

    Reuses the shared :func:`_validate_action`, but reports a malformed setting
    as ``VOICE_EDIT_BAD_SETTINGS`` (the code every other bad settings value in
    ``update_voice_edit`` uses).  ``_validate_action`` keeps raising
    ``VOICE_EDIT_UNKNOWN_ACTION`` where it names a prompt key.
    """
    from .schema import VoiceEditError
    try:
        return _validate_action(action)
    except VoiceEditError as exc:
        raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS", exc.detail) from exc


def update_voice_edit(cfg: Any, update: Dict[str, Any]) -> Path:
    """Atomic deep-merge of a whitelisted ``voice_edit`` update, with backup."""
    if not isinstance(update, dict):
        from .schema import VoiceEditError
        raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS", "settings must be an object")
    clean: Dict[str, Any] = {}
    for key in SETTINGS_KEYS:
        if key in update:
            clean[key] = update[key]
    # --- Wake words / wake routing (separate Qubit-owned JSON stores) --------
    # These keys mutate dedicated stores, never voice_edit YAML. Every value is
    # validated and normalized BEFORE any write so a bad field in a multi-key
    # request can never leave partial state. The store helpers themselves never
    # raise; only a structurally bad request is rejected, with the existing
    # ``VOICE_EDIT_BAD_SETTINGS`` shape.
    from .schema import VoiceEditError
    for _wk in ("wake_words", "wake_words_add", "wake_words_remove",
                "wake_routing", "wake_route_add", "wake_route_remove"):
        clean.pop(_wk, None)
    wake_words_save: Optional[list] = None
    wake_routing_save: Any = None
    if any(k in update for k in ("wake_words", "wake_words_add",
                                 "wake_words_remove")):
        from . import wake_words as _ww
        if "wake_words" in update:
            raw_words = update["wake_words"]
            if not isinstance(raw_words, (list, tuple)):
                raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                     "wake_words must be a list")
            words = list(raw_words)
        else:
            words = _ww.load_triggers()
        if "wake_words_add" in update:
            add_word = update["wake_words_add"]
            if not isinstance(add_word, str):
                raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                     "wake_words_add must be a string")
            words = _ww.add_trigger(words, add_word)
        if "wake_words_remove" in update:
            rem_word = update["wake_words_remove"]
            if not isinstance(rem_word, str):
                raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                     "wake_words_remove must be a string")
            words = _ww.remove_trigger(words, rem_word)
        wake_words_save = words
    if any(k in update for k in ("wake_routing", "wake_route_add",
                                 "wake_route_remove")):
        from . import wake_routing as _wr
        if "wake_routing" in update:
            raw_routing = update["wake_routing"]
            if not isinstance(raw_routing, dict):
                raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                     "wake_routing must be an object")
            routing = _wr.parse_config(raw_routing)
        else:
            routing = _wr.load_config()
        if "wake_route_add" in update:
            spec = update["wake_route_add"]
            if not isinstance(spec, dict):
                raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                     "wake_route_add must be an object")
            trig = spec.get("trigger")
            target = _wr.normalize_route_target(spec.get("target"))
            if (not isinstance(trig, str) or not trig.strip()
                    or len(trig.strip()) > _wr.MAX_TRIGGER_LENGTH
                    or not _wr.normalize_trigger(trig) or target is None):
                raise VoiceEditError(
                    "VOICE_EDIT_BAD_SETTINGS",
                    "wake_route_add needs a trigger and exactly one target "
                    "(mode|agentId|sessionKey)")
            routing = _wr.add_route(routing, trig, target)
        if "wake_route_remove" in update:
            rem_trig = update["wake_route_remove"]
            if not isinstance(rem_trig, str):
                raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                     "wake_route_remove must be a string")
            routing = _wr.remove_route(routing, rem_trig)
        wake_routing_save = routing
    if "prompts" in update:
        prompts = update["prompts"]
        if not isinstance(prompts, dict):
            from .schema import VoiceEditError
            raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                 "prompts must be an action -> text object")
        prompts = {_validate_action(k): str(v)
                   for k, v in prompts.items() if v is not None}
        if prompts:
            clean["prompts"] = prompts
    if "wake" in update:
        wake = update["wake"]
        if not isinstance(wake, dict):
            from .schema import VoiceEditError
            raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                 "wake must be an object")
        clean_wake: Dict[str, Any] = {}
        if "enabled" in wake:
            clean_wake["enabled"] = bool(wake["enabled"])
        if "phrase" in wake:
            clean_wake["phrase"] = str(wake["phrase"]).strip()[:60]
        if "engine" in wake:
            eng = str(wake["engine"]).strip().lower()
            if eng not in ("auto", "kws", "asr"):
                from .schema import VoiceEditError
                raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                     "wake.engine must be auto|kws|asr")
            clean_wake["engine"] = eng
        if "vad" in wake:
            vad = str(wake["vad"]).strip().lower()
            if vad not in ("auto", "silero", "webrtc", "energy"):
                from .schema import VoiceEditError
                raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                     "wake.vad must be auto|silero|webrtc|energy")
            clean_wake["vad"] = vad
        for k in ("kws_threshold", "kws_score"):
            if k in wake:
                try:
                    clean_wake[k] = float(wake[k])
                except (TypeError, ValueError):
                    from .schema import VoiceEditError
                    raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                         f"wake.{k} must be a number")
        if "kws_threshold" in clean_wake:
            clean_wake["kws_threshold"] = max(0.05, min(clean_wake["kws_threshold"], 0.9))
        if "asr_fallback" in wake:
            clean_wake["asr_fallback"] = bool(wake["asr_fallback"])
        if "asr_fallback_max_s" in wake:
            try:
                clean_wake["asr_fallback_max_s"] = float(
                    wake["asr_fallback_max_s"])
            except (TypeError, ValueError):
                from .schema import VoiceEditError
                raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                     "wake.asr_fallback_max_s must be a number")
            clean_wake["asr_fallback_max_s"] = max(
                2.0, min(clean_wake["asr_fallback_max_s"], 25.0))
        if "max_s" in wake:
            try:
                clean_wake["max_s"] = float(wake["max_s"])
            except (TypeError, ValueError):
                from .schema import VoiceEditError
                raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                     "wake.max_s must be a number")
            clean_wake["max_s"] = max(2.0, min(clean_wake["max_s"], 25.0))
        for k in ("silence_ms", "rms_gate", "arm_window_s"):
            if k in wake:
                try:
                    clean_wake[k] = float(wake[k])
                except (TypeError, ValueError):
                    from .schema import VoiceEditError
                    raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                         f"wake.{k} must be a number")
        if "silence_ms" in clean_wake:
            clean_wake["silence_ms"] = int(
                max(300.0, min(clean_wake["silence_ms"], 5000.0)))
        if "rms_gate" in clean_wake:
            clean_wake["rms_gate"] = max(0.0005, min(clean_wake["rms_gate"], 0.2))
        if "arm_window_s" in clean_wake:
            # The conversational-confirm window: below ~2 s a spoken "yes"
            # can never land in time; above 60 s a stale "yes" executes a
            # long-gone proposal (the pending-task TTL is 120 s).
            clean_wake["arm_window_s"] = max(
                2.0, min(clean_wake["arm_window_s"], 60.0))
        clean["wake"] = clean_wake
    if "per_app" in update:
        per_app = update["per_app"]
        if not isinstance(per_app, dict):
            from .schema import VoiceEditError
            raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                 "per_app must be an object")
        clean["per_app"] = {str(k).lower(): (_validate_action(v) if v else "")
                            for k, v in per_app.items()}
    if "custom_commands" in update:
        from .custom_commands import validate_aliases
        clean["custom_commands"] = validate_aliases(update["custom_commands"])
    from .schema import VoiceEditError
    # Cross-section knobs (models + TTS + local wording). These live under
    # tts.* / intent.local_llm.* in the config (portable, not Qubit-specific),
    # but are edited here so the dashboard can present one Voice settings form.
    tts_patch: Dict[str, Any] = {}
    if "tts_provider" in update:
        prov = str(update["tts_provider"] or "").strip().lower()
        if prov not in ("piper", "kokoro", "edge", "hybrid", "openai"):
            raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                 "tts_provider must be piper|kokoro|edge|hybrid|openai")
        tts_patch["provider"] = prov
    if "tts_voice" in update:
        tts_patch["voice"] = str(update["tts_voice"] or "").strip()[:80]
    if "tts_kokoro_voice_sid" in update:
        try:
            tts_patch["kokoro"] = {
                "voice_sid": int(max(0, min(int(update["tts_kokoro_voice_sid"]), 10)))}
        except (TypeError, ValueError):
            raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                 "tts_kokoro_voice_sid must be an integer 0..10")
    llm_patch: Dict[str, Any] = {}
    if "local_model" in update:
        llm_patch["model"] = str(update["local_model"] or "").strip()[:120]
    if "local_temperature" in update:
        try:
            llm_patch["temperature"] = max(
                0.0, min(float(update["local_temperature"]), 2.0))
        except (TypeError, ValueError):
            raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                 "local_temperature must be a number")
    if "local_think" in update:
        llm_patch["think"] = bool(update["local_think"])
    if (not clean and not tts_patch and not llm_patch
            and wake_words_save is None and wake_routing_save is None):
        raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                             "no recognised settings in request")
    for key in ("beep", "debug_log", "block_fullscreen", "noise_suppression",
                "disable_key_repeat", "text_commands",
                "speak_answers", "history_panel", "agc",
                 "verbatim_transcript", "sleep_mode", "waveform_visible",
                 "waveform_ingame",
                "enabled", "allow_api", "confirm_before_replace",
                "auto_send", "brain_router"):
        if key in clean:
            clean[key] = bool(clean[key])
    # Helper-connection endpoints + device/engine overrides: strip whitespace
    # (a trailing space in a URL breaks the helper connection) and cap length
    # like the model-name siblings (unbounded strings can never resolve).
    for key in ("service_url", "stt_ws_url"):
        if key in clean:
            clean[key] = str(clean[key] or "").strip()[:512]
    for key in ("asr_device", "stt_engine"):
        if key in clean:
            clean[key] = str(clean[key] or "").strip()[:120]
    for key in ("mic_gain", "mic_target_peak"):
        if key in clean:
            try:
                clean[key] = float(clean[key])
            except (TypeError, ValueError):
                from .schema import VoiceEditError
                raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                     f"{key} must be a number")
        if key == "mic_gain" and key in clean:
            clean[key] = max(1.0, min(clean[key], 32.0))
        if key == "mic_target_peak" and key in clean:
            clean[key] = max(0.05, min(clean[key], 0.95))
    if "release_tail_ms" in clean:
        try:
            clean["release_tail_ms"] = int(max(0, min(float(clean["release_tail_ms"]), 2000)))
        except (TypeError, ValueError):
            from .schema import VoiceEditError
            raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                 "release_tail_ms must be a number")
    # The default action has one public wire key (``default_prompt``, matching
    # ``settings_view``/``export_settings``) and one persisted key
    # (``default_action``).  Accept either and persist under ``default_action``;
    # whichever name is supplied is validated by the shared action check, and
    # ``default_prompt`` wins if both are present.
    if "default_action" in clean:
        clean["default_action"] = _validate_setting_action(clean["default_action"])
    if "default_prompt" in clean:
        clean["default_action"] = _validate_setting_action(clean["default_prompt"])
        clean.pop("default_prompt", None)
    if "routing" in clean and str(clean["routing"]) not in ("local", "api", "auto"):
        from .schema import VoiceEditError
        raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                             "routing must be local|api|auto")
    if "insertion_method" in clean and str(clean["insertion_method"]) not in (
            "clipboard_paste", "typed", "app_native"):
        from .schema import VoiceEditError
        raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                             "insertion_method must be clipboard_paste|typed|app_native")
    if "hotkey_mode" in clean and str(clean["hotkey_mode"]) not in ("hold", "toggle"):
        from .schema import VoiceEditError
        raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                             "hotkey_mode must be hold|toggle")
    if "stt_mode" in clean and str(clean["stt_mode"]) not in ("streaming", "accurate"):
        from .schema import VoiceEditError
        raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                             "stt_mode must be streaming|accurate")
    if "stt_model" in clean and str(clean["stt_model"]) not in (
            "tiny", "base", "small", "medium", "large-v3"):
        from .schema import VoiceEditError
        raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                             "stt_model must be tiny|base|small|medium|large-v3")
    if "router" in clean and str(clean["router"]) not in ("deterministic", "brain"):
        from .schema import VoiceEditError
        raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                             "router must be deterministic|brain")
    # Slice-5 AEC consumer switch: auto|raw keep the raw mic, aec selects the
    # echo-cancelled source (default auto = raw until the owner-eared test).
    if "capture_source" in clean and str(clean["capture_source"]).lower() not in (
            "auto", "raw", "aec"):
        from .schema import VoiceEditError
        raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                             "capture_source must be auto|raw|aec")
    if "capture_source" in clean:
        clean["capture_source"] = str(clean["capture_source"]).lower()
    if "request_ttl_s" in clean:
        try:
            clean["request_ttl_s"] = max(30.0, min(float(clean["request_ttl_s"]),
                                                   3600.0))
        except (TypeError, ValueError):
            from .schema import VoiceEditError
            raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                 "request_ttl_s must be a number")
    if "brain_timeout_s" in clean:
        # Brain-router classify wait: negative breaks the wait, absurd
        # values wedge the turn past the request TTL.
        try:
            clean["brain_timeout_s"] = max(
                0.5, min(float(clean["brain_timeout_s"]), 30.0))
        except (TypeError, ValueError):
            from .schema import VoiceEditError
            raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                 "brain_timeout_s must be a number")
    for key in ("chat_model", "detail_model"):
        if key in clean:
            clean[key] = str(clean[key] or "").strip()[:120]
    for key in ("local_profile", "api_profile"):
        # Profile selectors: bound the persisted string like the other
        # model-name knobs (an unbounded name can never resolve).
        if key in clean:
            clean[key] = str(clean[key] or "").strip()[:120]
    if "detail_cues" in clean:
        cues = clean["detail_cues"]
        if not isinstance(cues, (list, tuple)):
            from .schema import VoiceEditError
            raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                 "detail_cues must be a list of words")
        clean["detail_cues"] = [str(c).strip() for c in cues
                                if str(c).strip()][:40]
    if "voice_recent_turns" in clean:
        try:
            clean["voice_recent_turns"] = int(
                max(2, min(int(clean["voice_recent_turns"]), 40)))
        except (TypeError, ValueError):
            from .schema import VoiceEditError
            raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                 "voice_recent_turns must be a number")
    # Slice-2 web/model knobs: validate/coerce here, persist under voice_edit
    # (via clean) AND mirror to the intent.* authority the readers use.
    intent_patch: Dict[str, Any] = {}
    if "web_paid_first" in clean:
        clean["web_paid_first"] = bool(clean["web_paid_first"])
        intent_patch["web_paid_first"] = clean["web_paid_first"]
    for _mkey in ("ollama_model", "runner_alias"):
        if _mkey in clean:
            _mname = str(clean[_mkey] or "").strip()[:120]
            if not _mname:
                raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                     f"{_mkey} must be a non-empty model name")
            clean[_mkey] = _mname
    if "ollama_model" in clean:
        intent_patch["ollama_model"] = clean["ollama_model"]
    if "runner_alias" in clean:
        intent_patch.setdefault("runner", {})["alias"] = clean["runner_alias"]
    path = _config_path(cfg)
    data = _load_yaml(path)
    patch: Dict[str, Any] = {}
    if clean:
        patch["voice_edit"] = clean
    if tts_patch:
        patch["tts"] = tts_patch
    if llm_patch or intent_patch:
        patch["intent"] = dict(intent_patch)
        if llm_patch:
            patch["intent"]["local_llm"] = llm_patch
    if patch:
        merged = _deep_merge(data, patch)
        if "per_app" in clean and clean["per_app"] == {}:
            # PUT per_app:{} clears stored per-app mappings (a deep merge would
            # silently keep them); the live-cfg sync deletes the node too.
            ve = merged.get("voice_edit")
            if isinstance(ve, dict):
                ve.pop("per_app", None)
        bak = _write_yaml(path, merged)
        _sync_live_cfg(cfg, patch)
        result: Path = bak
    else:
        # Wake-store-only PUT: nothing to persist into voice_edit YAML.
        result = path
    if wake_words_save is not None:
        from . import wake_words as _ww
        _ww.save_triggers(wake_words_save)
    if wake_routing_save is not None:
        from . import wake_routing as _wr
        _wr.save_config(wake_routing_save)
    return result


def restore_default_prompt(cfg: Any, action: str) -> Path:
    action = _validate_action(action)
    path = _config_path(cfg)
    data = _load_yaml(path)
    ve = data.get("voice_edit") or {}
    prompts = ve.get("prompts") or {}
    if isinstance(prompts, dict) and action in prompts:
        prompts.pop(action, None)
        ve["prompts"] = prompts
        data["voice_edit"] = ve
        live = getattr(cfg, "data", None)
        if isinstance(live, dict) and isinstance(live.get("voice_edit"), dict) \
                and isinstance(live["voice_edit"].get("prompts"), dict):
            live["voice_edit"]["prompts"].pop(action, None)
        return _write_yaml(path, data)
    return path


def restore_all_defaults(cfg: Any) -> Path:
    path = _config_path(cfg)
    data = _load_yaml(path)
    ve = data.get("voice_edit") or {}
    ve.pop("prompts", None)
    data["voice_edit"] = ve
    live = getattr(cfg, "data", None)
    if isinstance(live, dict) and isinstance(live.get("voice_edit"), dict):
        live["voice_edit"].pop("prompts", None)
    return _write_yaml(path, data)


def export_settings(cfg: Any) -> Dict[str, Any]:
    view = settings_view(cfg)
    user = _read_cfg(cfg, "voice_edit", "prompts", default={}) or {}
    per_app = _read_cfg(cfg, "voice_edit", "per_app", default={}) or {}
    return {
        "schema": "qubit-voice-edit/1",
        "exported_at": int(time.time()),
        "settings": {k: view[k] for k in SETTINGS_KEYS if k in view},
        "routing": {k: view[k] for k in
                    ("routing", "local_profile", "api_profile", "allow_api",
                     "confirm_before_replace", "auto_send", "insertion_method")},
        "user_prompts": {k: str(v) for k, v in (user or {}).items()},
        "per_app": dict(per_app or {}),
    }


def import_settings(cfg: Any, payload: Dict[str, Any]) -> Path:
    if not isinstance(payload, dict):
        from .schema import VoiceEditError
        raise VoiceEditError("VOICE_EDIT_BAD_IMPORT", "import must be an object")
    update: Dict[str, Any] = {}
    for section in ("routing", "settings"):
        src = payload.get(section) or {}
        if isinstance(src, dict):
            for k in ("routing", "local_profile", "api_profile", "allow_api",
                      "confirm_before_replace", "auto_send", "insertion_method",
                      "default_action", "default_prompt", "asr_device",
                      "stt_engine", "stt_mode", "stt_model", "router",
                      "custom_commands"):
                if k in src:
                    update[k] = src[k]
    user_prompts = payload.get("user_prompts") or {}
    if isinstance(user_prompts, dict):
        kept = {k: v for k, v in user_prompts.items() if k in ACTIONS}
        if kept:
            update["prompts"] = kept
    per_app = payload.get("per_app") or {}
    if isinstance(per_app, dict):
        update["per_app"] = per_app
    if not update:
        from .schema import VoiceEditError
        raise VoiceEditError("VOICE_EDIT_BAD_IMPORT",
                             "no recognised settings in import")
    return update_voice_edit(cfg, update)


__all__ = [
    "settings_view", "update_voice_edit", "restore_default_prompt",
    "restore_all_defaults", "export_settings", "import_settings",
]
