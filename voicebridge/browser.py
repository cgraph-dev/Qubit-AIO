"""Packet 8T-F — Hermes browser parity via the scoped 8B loopback probe.

Hermes ``browser {close-profile}`` manages real-profile browsing sessions.
Qubit never touches personal browser profiles: the only browser authority
is the read-only loopback CDP path (Packet 8B). This module provides the
Qubit-native surface:

* ``status`` — the 8B CDP precondition as a typed state (``OK`` /
  ``BROWSER_RESTART_REQUIRED`` / ``UNAVAILABLE``); probing never kills,
  restarts, focuses, or mutates any browser session;
* ``close-profile`` — typed ``BROWSER_CLOSE_REFUSED`` (closing the user's
  real profile would mutate their session outside Qubit authority).
"""
from __future__ import annotations

from typing import Any, Dict

CLOSE_CODE = "BROWSER_CLOSE_REFUSED"


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def status(cfg: Any) -> Dict[str, Any]:
    """Loopback-CDP browser state (read-only probe, never mutates)."""
    from .communications.monitor import cdp_precondition

    probe = cdp_precondition(cfg)
    return {"state": probe.get("state", "UNAVAILABLE"),
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
