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
                 "default_action", "enabled", "asr_device", "stt_engine",
                 "stt_mode", "stt_model", "router",
                 "hotkey_mode", "service_url", "stt_ws_url", "request_ttl_s",
                 "brain_router", "brain_timeout_s", "wake", "block_fullscreen",
                 "beep", "debug_log", "mic_gain", "mic_target_peak",
                 "release_tail_ms", "noise_suppression", "disable_key_repeat",
                 "answer_questions", "text_commands", "capture_source")


def _kws_available(cfg: Any) -> bool:
    try:
        from .kws import find_model_dir
        return find_model_dir(cfg) is not None
    except Exception:
        return False


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


def _sync_live_cfg(cfg: Any, clean: Dict[str, Any], *,
                   remove: tuple = ()) -> None:
    """Reflect a persisted change on the live Config so a running service sees
    the new value without a restart (the file remains the authority)."""
    data = getattr(cfg, "data", None)
    if not isinstance(data, dict):
        return
    ve = data.setdefault("voice_edit", {})
    if not isinstance(ve, dict):
        ve = {}
        data["voice_edit"] = ve
    for key in remove:
        ve.pop(key, None)
    for k, v in clean.items():
        if isinstance(v, dict) and isinstance(ve.get(k), dict):
            ve[k] = {**ve[k], **v}
        else:
            ve[k] = v


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
        "answer_questions": bool(_read_cfg(cfg, "voice_edit",
                                           "answer_questions", default=True)),
        "text_commands": bool(_read_cfg(cfg, "voice_edit", "text_commands",
                                        default=False)),
        "capture_source": str(_read_cfg(cfg, "voice_edit", "capture_source",
                                        default="auto") or "auto"),
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
            "kws_threshold": float(_read_cfg(cfg, "voice_edit", "wake",
                                             "kws_threshold", default=0.25) or
                                   0.25),
            "kws_available": _kws_available(cfg),
        },
        "prompts": defaults,
        "per_app": per_app,
        "prompt_versions": {a: s.version for a, s in DEFAULT_PROMPTS.items()},
    }


def _validate_action(action: str) -> str:
    a = str(action or "").strip()
    if a not in ACTIONS:
        from .schema import VoiceEditError
        raise VoiceEditError("VOICE_EDIT_UNKNOWN_ACTION",
                             f"unknown action {a!r}")
    return a


def update_voice_edit(cfg: Any, update: Dict[str, Any]) -> Path:
    """Atomic deep-merge of a whitelisted ``voice_edit`` update, with backup."""
    if not isinstance(update, dict):
        from .schema import VoiceEditError
        raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS", "settings must be an object")
    clean: Dict[str, Any] = {}
    for key in SETTINGS_KEYS:
        if key in update:
            clean[key] = update[key]
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
        for k in ("silence_ms", "rms_gate", "arm_window_s", "max_s"):
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
        clean["wake"] = clean_wake
    if "per_app" in update:
        per_app = update["per_app"]
        if not isinstance(per_app, dict):
            from .schema import VoiceEditError
            raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                 "per_app must be an object")
        clean["per_app"] = {str(k).lower(): (_validate_action(v) if v else "")
                            for k, v in per_app.items()}
    if not clean:
        from .schema import VoiceEditError
        raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                             "no recognised voice_edit settings in request")
    for key in ("beep", "debug_log", "block_fullscreen", "noise_suppression",
                "disable_key_repeat", "answer_questions", "text_commands"):
        if key in clean:
            clean[key] = bool(clean[key])
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
    if "default_action" in clean:
        clean["default_action"] = _validate_action(clean["default_action"])
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
    if "request_ttl_s" in clean:
        try:
            clean["request_ttl_s"] = max(30.0, min(float(clean["request_ttl_s"]),
                                                   3600.0))
        except (TypeError, ValueError):
            from .schema import VoiceEditError
            raise VoiceEditError("VOICE_EDIT_BAD_SETTINGS",
                                 "request_ttl_s must be a number")
    path = _config_path(cfg)
    data = _load_yaml(path)
    merged = _deep_merge(data, {"voice_edit": clean})
    bak = _write_yaml(path, merged)
    _sync_live_cfg(cfg, clean)
    return bak


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
                      "default_action", "asr_device", "stt_engine", "stt_mode",
                      "stt_model", "router"):
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
