"""Auth status, status, background and commands — the UI's bootstrap reads.

UPSTREAM CONTRACT (read at UPSTREAM_PIN)
---------------------------------------------------------------------------
``GET /api/auth/status``        ``routes.py:17559-17595``
``GET /api/status``             (Qubit's own ``StatusResponse`` — see below)
``GET /api/background/status``  ``:18066-18072``
``GET /api/commands``           ``:18292-18294`` → ``api/commands.py:94-109``

THE AUTH ANSWER IS QUBIT'S ONE SCHEME, DESCRIBED HONESTLY
---------------------------------------------------------------------------
Qubit authenticates with ``X-Qubit-Session-Token`` on every ``/agent/api/*``
request, enforced by the listener before any handler runs
(``webui._qubit_api``). There are no passkeys, no OIDC and no login form in this
deployment, so :func:`auth_status` reports exactly that: auth on, session
valid (the request already passed the gate), every optional credential
mechanism ``false``. The ported settings block branches only on
``passkey_feature_flag`` (``panels.js:13094``) and therefore hides the passkey
affordance — which is true, not a workaround. No second scheme is introduced.

STATUS: A DELIBERATE 501, WITH A POINTER
---------------------------------------------------------------------------
Qubit already serves a ``StatusResponse`` at ``GET /api/status``
(``qubit_compat.h_status``), and the live dashboard reads it. Upstream's
``StatusResponse`` is a different wire type with different keys. Re-binding
Qubit's path would break the dashboard, and silently re-serving Qubit's payload
under upstream's name would make the ported client read ``undefined`` for the
fields it expects. So the shim refuses with a typed NOT_PORTED that names where
the real data lives; the porter maps the client's reads onto Qubit's keys.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from . import UPSTREAM_PIN, gap, str_param


def auth_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/wb/auth/status — Qubit's single token scheme, described."""
    return 200, {
        # The token gate is on: reaching this handler means it was satisfied.
        "auth_enabled": True,
        "logged_in": True,
        "oidc_enabled": False,
        "password_auth_enabled": False,
        "passwordless_enabled": False,
        "passkeys_enabled": False,
        "passkeys_count": 0,
        "passkey_feature_flag": False,
        "auth_disabled_acknowledged": False,
        # Upstream's trusted-session fields, which is what Qubit's token
        # session actually is: a bearer session established out of band.
        "trusted_auth_enabled": True,
        "auth_type": "session-token",
        "user": "operator",
        "bound_profile": "default",
        "scheme": "X-Qubit-Session-Token",
        "upstream": UPSTREAM_PIN,
    }


def status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
           p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/wb/status — GAP: Qubit's StatusResponse already owns /api/status."""
    return gap("STATUS_RESPONSE_DIVERGES",
               detail=("Read GET /api/status for the real runtime inventory and "
                       "map this client's fields onto it."),
               qubit_counterpart="GET /api/status")


def background_status(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
                      p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/wb/background/status — GAP: no background agent lane."""
    if not str_param(q, "session_id"):
        return 400, {"error": "session_id is required"}
    return gap("BACKGROUND_AGENT",
               detail=("A turn sent through /api/wb/chat/start answers with its "
                       "reply before returning; there are no detached results "
                       "to poll."),
               qubit_counterpart="POST /api/wb/chat/start")


def _command_rows(cfg: Any) -> List[Dict[str, Any]]:
    """Qubit's slash catalog, re-keyed to upstream's eight fields.

    The source is the SAME handler that serves ``GET /api/chat/slash-commands``
    (``qubit_compat.h_chat_slash_commands``), so the ported command menu and the
    Qubit chat overlay's palette can never disagree about what exists.
    """
    from .. import qubit_compat as HC

    fn = getattr(HC, "h_chat_slash_commands", None)
    if not callable(fn):
        raise AttributeError("qubit_compat exposes no h_chat_slash_commands")
    status_code, payload = fn(cfg, {}, {}, {})
    if status_code != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"the slash catalog answered {status_code}")
    rows: List[Dict[str, Any]] = []
    listed = payload.get("commands")
    for entry in listed if isinstance(listed, list) else []:
        if not isinstance(entry, dict):
            continue
        raw_name = str(entry.get("name") or "").strip()
        if not raw_name:
            continue
        name = raw_name[1:] if raw_name.startswith("/") else raw_name
        rows.append({
            "name": name,
            "description": str(entry.get("description") or ""),
            # Qubit's catalog tags each entry with its source; upstream's field
            # is a free category string, so the source IS the category.
            "category": str(entry.get("source") or "qubit"),
            "aliases": [],
            "args_hint": "",
            "subcommands": [],
            # Qubit answers every catalog command in-chat or via its own
            # registry; it does not classify them as CLI- or gateway-only here.
            "cli_only": False,
            "gateway_only": False,
        })
    return rows


def commands(cfg: Any, q: Dict[str, str], b: Dict[str, Any],
             p: Dict[str, str]) -> Tuple[int, Dict[str, Any]]:
    """GET /api/wb/commands — the slash catalog in upstream's shape."""
    try:
        rows = _command_rows(cfg)
    except Exception as exc:  # noqa: BLE001
        return 503, {
            "ok": False, "state": "UNAVAILABLE", "code": "COMMANDS_UNAVAILABLE",
            "detail": f"the slash catalog could not be built: "
                      f"{type(exc).__name__}: {exc}",
            "commands": [],
            "upstream": UPSTREAM_PIN,
        }
    return 200, {"commands": rows, "total": len(rows),
                 "upstream": UPSTREAM_PIN}


HANDLERS = {
    "h_wb_auth_status": auth_status,
    "h_wb_status": status,
    "h_wb_background_status": background_status,
    "h_wb_commands": commands,
}


def routes() -> list:
    return [
        ("GET", r"/api/wb/auth/status", "h_wb_auth_status"),
        ("GET", r"/api/wb/status", "h_wb_status"),
        ("GET", r"/api/wb/background/status", "h_wb_background_status"),
        ("GET", r"/api/wb/commands", "h_wb_commands"),
    ]
