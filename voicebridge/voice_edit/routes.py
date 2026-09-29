"""Additive, authenticated HTTP handlers for the voice-typing / edit surface.

These handlers are reached only through the dashboard's authenticated
``/agent/api/...`` dispatch (``webui._qubit_api``) or the token-gated classic
``/api/...`` path. They add no listener and no second session/audit store.

Wire shape is a plain ``(status, payload)`` tuple so the caller can render it
without importing webui internals.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional, Tuple

from .schema import VoiceEditError
from .service import VoiceEditService
from .settings import (export_settings, import_settings, restore_all_defaults,
                       restore_default_prompt, settings_view, update_voice_edit)

_LOCK = threading.Lock()
_SERVICE: Optional[VoiceEditService] = None
_SERVICE_KEY: Any = None


def service_for(cfg: Any) -> VoiceEditService:
    """One service per config identity (keeps request state across HTTP calls)."""
    global _SERVICE, _SERVICE_KEY
    key = (id(cfg), str(getattr(cfg, "path", "") or ""))
    with _LOCK:
        if _SERVICE is None or _SERVICE_KEY != key:
            _SERVICE = VoiceEditService(cfg)
            _SERVICE_KEY = key
        return _SERVICE


def reset_service() -> None:
    global _SERVICE, _SERVICE_KEY
    with _LOCK:
        _SERVICE = None
        _SERVICE_KEY = None


def _ok(**fields: Any) -> Tuple[int, Dict[str, Any]]:
    out = {"ok": True}
    out.update(fields)
    return 200, out


def _err(exc: VoiceEditError) -> Tuple[int, Dict[str, Any]]:
    return exc.status, exc.public()


def _require_body_str(body: Any, key: str, default: str = "") -> str:
    if not isinstance(body, dict):
        return default
    val = body.get(key, default)
    return str(val if val is not None else default)


def handle(cfg: Any, method: str, api_path: str,
           body: Any = None) -> Optional[Tuple[int, Dict[str, Any]]]:
    """Dispatch a voice-edit route, or return None when it is not ours."""
    path = (api_path or "").rstrip("/")
    if not path.startswith("/api/voice-edit"):
        return None
    method = method.upper()
    body = body if isinstance(body, dict) else {}
    svc = service_for(cfg)
    try:
        if path == "/api/voice-edit/status" and method in ("GET", "HEAD"):
            return _ok(**svc.status())
        if path == "/api/voice-edit/log" and method in ("GET", "HEAD"):
            from . import logview
            limit = 200
            if isinstance(body, dict) and body.get("limit"):
                try:
                    limit = int(body["limit"])
                except (TypeError, ValueError):
                    limit = 200
            events = logview.recent_events(limit)
            return _ok(events=events, summary=logview.summary(events))
        if path == "/api/voice-edit/prompts" and method in ("GET", "HEAD"):
            return _ok(prompts=settings_view(cfg)["prompts"])
        if path == "/api/voice-edit/settings" and method in ("GET", "HEAD"):
            return _ok(settings=settings_view(cfg))
        if path == "/api/voice-edit/settings" and method == "PUT":
            update_voice_edit(cfg, body)
            reset_service()
            return _ok(settings=settings_view(cfg))
        if path == "/api/voice-edit/settings/restore" and method == "POST":
            action = _require_body_str(body, "action", "")
            if action:
                restore_default_prompt(cfg, action)
            else:
                restore_all_defaults(cfg)
            reset_service()
            return _ok(settings=settings_view(cfg))
        if path == "/api/voice-edit/export" and method in ("GET", "HEAD"):
            return _ok(export=export_settings(cfg))
        if path == "/api/voice-edit/import" and method == "POST":
            import_settings(cfg, body.get("import") if isinstance(
                body.get("import"), dict) else body)
            reset_service()
            return _ok(settings=settings_view(cfg))
        if path == "/api/voice-edit/ask" and method == "POST":
            out = svc.ask(_require_body_str(body, "text"),
                          request_id=_require_body_str(body, "request_id"),
                          mode=_require_body_str(body, "mode", "answer"))
            return (200 if out.get("ok") else 400), out
        if path == "/api/voice-edit/task" and method == "POST":
            out = svc.task(
                _require_body_str(body, "text"),
                confirm=bool(body.get("confirm", False)),
                request_id=_require_body_str(body, "request_id"))
            return (200 if out.get("ok") else 409), out
        if path == "/api/voice-edit/session" and method == "POST":
            out = svc.begin(session_id=_require_body_str(body, "session_id"),
                            request_id=_require_body_str(body, "request_id"),
                            target_app=_require_body_str(body, "target_app"))
            return _ok(**out)

        # /api/voice-edit/<request_id>/<verb>
        parts = path[len("/api/voice-edit/"):].split("/")
        if len(parts) == 2 and parts[0]:
            rid, verb = parts[0], parts[1]
            if verb == "capture" and method == "POST":
                return _ok(**svc.capture(rid))
            if verb == "transform" and method == "POST":
                return _ok(**svc.transform(
                    rid, _require_body_str(body, "transcript"),
                    privacy_override=_require_body_str(body, "privacy"),
                    action_override=_require_body_str(body, "action"),
                    style_override=_require_body_str(body, "style")))
            if verb == "route" and method == "POST":
                return _ok(**svc.route(
                    rid, _require_body_str(body, "transcript")))
            if verb == "edit" and method == "POST":
                return _ok(**svc.edit(rid, _require_body_str(body, "text")))
            if verb == "retry" and method == "POST":
                return _ok(**svc.retry(
                    rid, _require_body_str(body, "transcript"),
                    privacy_override=_require_body_str(body, "privacy")))
            if verb == "apply" and method == "POST":
                confirm = bool(body.get("confirm", False))
                return _ok(**svc.apply(rid, confirm=confirm,
                                       method=_require_body_str(body, "method")))
            if verb == "revert" and method == "POST":
                return _ok(**svc.revert(rid))
            if verb == "cancel" and method == "POST":
                return _ok(**svc.cancel(rid))
            if verb == "result" and method in ("GET", "HEAD"):
                return _ok(**svc.public_result(rid))
        return 404, {"ok": False, "code": "VOICE_EDIT_UNKNOWN_ROUTE",
                     "detail": f"no voice-edit route {method} {path}"}
    except VoiceEditError as exc:
        return _err(exc)
    except Exception as exc:  # never leak a stack trace or text
        return 500, {"ok": False, "state": "FAILED",
                     "code": "VOICE_EDIT_INTERNAL",
                     "detail": str(exc)[:160]}


__all__ = ["handle", "service_for", "reset_service"]
