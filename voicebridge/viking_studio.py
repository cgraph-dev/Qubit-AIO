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

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

# The OpenViking server the Studio is served from. Matches the plugin's
# ``_DEFAULT_ENDPOINT`` (qubit_runtime/plugins/memory/openviking/__init__.py:53).
_DEFAULT_ENDPOINT = "http://127.0.0.1:1933"
# Bounded server-side probe budget: the dashboard request must answer quickly
# even when the OpenViking listener is wedged (never an open-ended hang).
_PROBE_TIMEOUT_S = 5.0

_ROUTE_HEALTH = "/api/qubit/viking/studio-health"
_ROUTE_WIRING = "/api/qubit/viking/studio-wiring"


def _health_payload(endpoint: str) -> Dict[str, Any]:
    """``GET <endpoint>/health`` using the same live auth as the memory client.

    The old probe constructed an anonymous client unconditionally, so a
    protected OpenViking returned a false ``unreachable`` result even while
    Qubit's memory provider was working.  Resolve the profile-scoped key via
    the plugin's secret accessor and let its anonymous-first/authenticated
    fallback perform the real probe.
    """
    try:
        from voicebridge.qubit_runtime import ensure_path
        ensure_path()
        from plugins.memory.openviking import _VikingClient
        from agent.secret_scope import get_secret
        api_key = str(get_secret("OPENVIKING_API_KEY", "") or "")
        account = str(get_secret("OPENVIKING_ACCOUNT", "") or "")
        user = str(get_secret("OPENVIKING_USER", "") or "")
        agent = str(get_secret("OPENVIKING_AGENT", "") or "")
        payload = _VikingClient(
            endpoint, api_key=api_key, account=account or None,
            user=user or None, agent=agent or None,
        ).health_payload()
        if isinstance(payload, dict) and payload:
            return payload
    except Exception:  # noqa: BLE001 - fall back to the stdlib probe below
        pass

    req = urllib.request.Request(
        endpoint.rstrip("/") + "/health",
        headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT_S) as resp:
        raw = resp.read(64 * 1024)
    data = json.loads(raw.decode("utf-8", "replace"))
    return data if isinstance(data, dict) else {}


def _truthy(value: Any) -> bool:
    """OpenViking reports ``healthy`` as a bool, but a string 'true' is truth."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "ok")


def studio_health(endpoint: str = _DEFAULT_ENDPOINT) -> Tuple[int, Dict[str, Any]]:
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
    """Which memory provider Qubit is wired to (``memory.provider``).

    Reads ``load_user_config_effective()`` — the served-dashboard reader —
    rather than the CLI's ``load_config()``: the CLI loader merges
    ``DEFAULT_CONFIG`` first, so it can never distinguish "the operator set
    memory.provider=openviking" from a default; the effective reader replays
    the exact user file + managed overlay the running dashboard obeys.
    """
    provider = ""
    try:
        from qubit_cli.config_effective import load_user_config_effective
        effective = load_user_config_effective() or {}
        memory = effective.get("memory") if isinstance(effective, dict) else None
        if isinstance(memory, dict):
            provider = str(memory.get("provider") or "")
    except Exception:  # noqa: BLE001 - absent/unreadable config is "unset"
        provider = ""
    return 200, {"result": {"memory": {"provider": provider}}}


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
