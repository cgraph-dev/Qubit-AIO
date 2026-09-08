"""Shared runtime plumbing: scrubbed environments and redacted receipts."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .worker import HERMES_PIN, RUNTIME_NAME


def scrubbed_env(extra: Optional[Mapping[str, str]] = None,
                 keep: Sequence[str] = ("PATH", "HOME", "LANG", "LC_ALL",
                                        "TZ", "PYTHONHASHSEED",
                                        "XDG_CONFIG_HOME", "XDG_DATA_HOME",
                                        "XDG_STATE_HOME", "XDG_CACHE_HOME"),
                 xdg_from_home: Optional[Path] = None) -> Dict[str, str]:
    """Build a minimal child environment: allowlisted keys only.

    Secret-shaped variables (*KEY*, *TOKEN*, *SECRET*, *PASSWORD*,
    *CREDENTIAL*) are NEVER passed through even if allowlisted by name.
    ``xdg_from_home`` redirects XDG_*_HOME under an isolated home so the
    child cannot touch the operator's real config/state.
    """
    env: Dict[str, str] = {}
    for key in keep:
        val = os.environ.get(key, "")
        if not val:
            continue
        upper = key.upper()
        if any(mark in upper for mark in ("KEY", "TOKEN", "SECRET",
                                          "PASSWORD", "CREDENTIAL")):
            continue
        env[key] = val
    if xdg_from_home is not None:
        home = str(xdg_from_home)
        env["HOME"] = home
        env["XDG_CONFIG_HOME"] = str(xdg_from_home / ".config")
        env["XDG_DATA_HOME"] = str(xdg_from_home / ".local/share")
        env["XDG_STATE_HOME"] = str(xdg_from_home / ".local/state")
        env["XDG_CACHE_HOME"] = str(xdg_from_home / ".cache")
    if extra:
        env.update({k: v for k, v in extra.items() if v is not None})
    return env


def redact(value: str) -> str:
    try:
        from voicebridge.redaction import redact_text
        return redact_text(value)
    except Exception:
        return value


def audit(event: str, **fields: Any) -> None:
    try:
        from voicebridge.logging_setup import log_event

        safe = {k: (redact(v) if isinstance(v, str) else v)
                for k, v in fields.items()}
        log_event(f"runtime.{event}", runtime=RUNTIME_NAME, pin=HERMES_PIN,
                  **safe)
    except Exception:
        pass
