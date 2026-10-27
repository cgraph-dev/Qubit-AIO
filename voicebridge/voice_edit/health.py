"""Voice-edit display capability health.

Selection capture needs a real X11 session *inside the service process*. A
bridge started by systemd inherits the user manager's environment, which does
not include ``DISPLAY``/``XAUTHORITY`` unless they were imported. In that case
capture degrades to ``unsupported_session`` and a selection turn silently
becomes dictation. ``display_health`` makes that state visible without leaking
the session's display values.

The hint names the two owner actions (import the variables into the user
manager, restart the bridge) and is static on purpose: values never appear in
the payload, logs, or errors.
"""
from __future__ import annotations

import os
from typing import Any, Dict

from .desktop import DesktopAdapter

_IMPORT_HINT = (
    "selection capture needs X11 in the voice service. In your desktop "
    "session run `systemctl --user import-environment DISPLAY XAUTHORITY`, "
    "then restart the service with `systemctl --user restart "
    "qubit-voicebridge`."
)


def _env_present(env: Dict[str, str], key: str) -> bool:
    return bool(str(env.get(key, "") or "").strip())


def display_health(cfg: Any = None) -> Dict[str, Any]:
    """Report whether selection capture has a usable display session.

    ``cfg`` is accepted for call-site symmetry (routes hold the config); the
    answer is process/session state, not configuration.
    """
    try:
        env = dict(os.environ)
    except Exception:
        env = {}
    try:
        session = DesktopAdapter(env=env).session()
    except Exception:
        session = {}
    x11 = bool(session.get("x11"))
    return {
        "x11": x11,
        "session_type": str(session.get("session_type", "unknown") or "unknown"),
        "display_present": _env_present(env, "DISPLAY"),
        "wayland_present": _env_present(env, "WAYLAND_DISPLAY"),
        "reason": "" if x11 else "unsupported_session",
        "hint": "" if x11 else _IMPORT_HINT,
    }


__all__ = ["display_health"]
