"""Qubit browser surface via the scoped loopback probe.

Qubit never touches personal browser profiles: the only browser authority
is the read-only loopback CDP path. This module provides the
Qubit-native surface:

* ``status`` — renders the 8B CDP precondition probe as a typed state
  (``OK`` / ``BROWSER_RESTART_REQUIRED`` / ``UNAVAILABLE``). The probe
  itself is acquired by the caller (``voicebridge/cli.py``, which owns the
  Packet 8A import boundary); this module never imports the
  communications package, spawns nothing, and mutates no session;
* ``close-profile`` — typed ``BROWSER_CLOSE_REFUSED`` (closing the user's
  real profile would mutate their session outside Qubit authority).
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

CLOSE_CODE = "BROWSER_CLOSE_REFUSED"


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def status(probe: Mapping[str, Any]) -> Dict[str, Any]:
    """Render a CDP precondition probe as typed browser state (pure)."""
    probe = dict(probe or {})
    state = str(probe.get("state", "UNAVAILABLE"))
    if state not in ("OK", "BROWSER_RESTART_REQUIRED", "UNAVAILABLE"):
        state = "UNAVAILABLE"
    return {"state": state,
            "schema": "qubit-browser/1",
            "cdp": bool(probe.get("cdp", False)),
            "detail": _redact(str(probe.get("detail", "") or ""))[:300],
            "authority": "loopback CDP read-only (Packet 8B); personal "
                         "profiles are never touched"}


def refuse_close_profile(profile: str = "") -> Dict[str, Any]:
    """Typed refusal for real-profile session mutation."""
    return {"state": "REFUSED", "code": CLOSE_CODE,
            "profile": _redact((profile or "default")[:80]),
            "explanation": "Qubit Agent cannot do this: closing a real "
                           "browser profile would mutate your session "
                           "outside Qubit authority; the read-only loopback "
                           "CDP path is the only browser authority.",
            "owner": "qubit-policy",
            "rollback": "no session touched; nothing to roll back"}
