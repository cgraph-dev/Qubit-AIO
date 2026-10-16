"""Additive, authenticated HTTP handlers for the embedded OpenViking Studio.

The Qubit dashboard's ``/open-viking`` page embeds the upstream OpenViking
Studio (a Vite SPA at ``http://127.0.0.1:1933/studio/*``) in an iframe and
asks the backend two questions over the SPA's ``/agent/api/...`` prefix:

* ``GET /api/qubit/viking/studio-health`` — is the OpenViking server up, and
  what version / auth mode is it running? Probed SERVER-SIDE (browser CORS
  and reachability are not the truth here).
* ``GET /api/qubit/viking/studio-wiring`` — is this Qubit wired to OpenViking
  as its memory provider? Read from the effective user config
  (``memory.provider``).

These handlers are reached only through the dashboard's authenticated
``/agent/api/...`` dispatch (``webui._qubit_api``), alongside the
``/api/voice-edit`` and ``/api/device`` families. They add no listener and no
second session/audit store.

Wire shape is a plain ``(status, payload)`` tuple so the caller (webui) can
render it without importing webui internals — the same contract
``voicebridge.voice_edit.routes.handle`` uses.
"""
from __future__ import annotations

import urllib.error
from typing import Any, Dict, Optional, Tuple

# The OpenViking server the Studio is served from. Matches the plugin's
# ``_DEFAULT_ENDPOINT`` (qubit_runtime/plugins/memory/openviking/__init__.py:53).
_DEFAULT_ENDPOINT = "http://127.0.0.1:1933"
# Bounded server-side probe budget: the dashboard request must answer quickly
# even when the OpenViking listener is wedged (never an open-ended hang).
_PROBE_TIMEOUT_S = 5.0

_ROUTE_HEALTH = "/api/qubit/viking/studio-health"
_ROUTE_WIRING = "/api/qubit/viking/studio-wiring"


def _health_payload(endpoint: Optional[str] = None) -> Dict[str, Any]:
    """Probe with the provider's canonical, profile-scoped connection settings.

    Never retry anonymously after a client/configuration failure: that would
    hide auth and secret-scope errors behind a healthy public endpoint.
    """
    from voicebridge.qubit_runtime import ensure_path
    ensure_path()
    from plugins.memory.openviking import (
        _VikingClient, _load_qubit_openviking_config, _resolve_connection_settings,
    )
    settings = _resolve_connection_settings(_load_qubit_openviking_config())
    if endpoint is not None:
        settings["endpoint"] = endpoint
    payload = _VikingClient(**settings).health_payload()
    if not isinstance(payload, dict) or not payload:
        raise ValueError("OpenViking /health returned an empty or non-object payload")
    return payload


def _truthy(value: Any) -> bool:
    """OpenViking reports ``healthy`` as a bool, but a string 'true' is truth."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "ok")


def studio_health(endpoint: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
    """Probe OpenViking server-side; return the page's ``result`` envelope.

    Success -> ``{ok:True, version, auth_mode, error:""}``.
    Failure -> ``ok:False`` with the REAL error string and HTTP 200, because
    the SPA renders the ``result.ok`` flag rather than treating a down
    OpenViking as a failed dashboard call.
    """
    try:
        payload = _health_payload(endpoint)
        healthy = _truthy(payload.get("healthy")) or \
            str(payload.get("status", "")).strip().lower() == "ok"
        if not healthy:
            return 200, {"result": {
                "ok": False,
                "version": str(payload.get("version") or ""),
                "auth_mode": str(payload.get("auth_mode") or ""),
                "error": "OpenViking /health reported " +
                         str(payload.get("status") or payload or "no status")}}
        return 200, {"result": {
            "ok": True,
            "version": str(payload.get("version") or ""),
            "auth_mode": str(payload.get("auth_mode") or ""),
            "error": ""}}
    except urllib.error.HTTPError as exc:
        return 200, {"result": {"ok": False, "version": "", "auth_mode": "",
                                "error": f"OpenViking /health HTTP {exc.code}"}}
    except Exception as exc:  # noqa: BLE001 - surface the real string
        return 200, {"result": {"ok": False, "version": "", "auth_mode": "",
                                "error": f"{type(exc).__name__}: {exc}"}}


def studio_wiring(cfg: Any = None) -> Tuple[int, Dict[str, Any]]:
    """Report configured external provider, not session activation or recall.

    agent_init reads the top-level memory.provider from load_config(); nested
    memory.semantic.provider belongs to the bridge semantic adapter, not this
    external provider. Use the same default/managed configuration reader.
    """
    try:
        from voicebridge.qubit_runtime import ensure_path
        ensure_path()
        from qubit_cli.config import load_config_readonly
        from tools.memory_tool import get_builtin_memory_config
        memory = get_builtin_memory_config(load_config_readonly())
        provider = memory.get("provider", "")
        if not isinstance(provider, str):
            raise ValueError("memory.provider must be a string")
        return 200, {"result": {"memory": {"provider": provider},
                                "scope": "configured", "error": ""}}
    except Exception as exc:
        return 200, {"result": {"memory": {"provider": ""},
                                "scope": "configured", "error": f"{type(exc).__name__}: {exc}"}}


def handle(cfg: Any, method: str, api_path: str,
           body: Any = None) -> Optional[Tuple[int, Dict[str, Any]]]:
    """Dispatch a viking-studio route, or return None when it is not ours."""
    path = (api_path or "").rstrip("/")
    method = method.upper()
    if path == _ROUTE_HEALTH:
        if method not in ("GET", "HEAD"):
            return 405, {"ok": False, "code": "METHOD_NOT_ALLOWED",
                         "detail": "GET only"}
        return studio_health()
    if path == _ROUTE_WIRING:
        if method not in ("GET", "HEAD"):
            return 405, {"ok": False, "code": "METHOD_NOT_ALLOWED",
                         "detail": "GET only"}
        return studio_wiring(cfg)
    return None
