"""Packet 8Q — credential references.

Keys live ONLY in environment variables or an OS-secret reference (a file path
outside git whose *contents* are never logged). Qubit never reads OpenCode's
private auth database and never stores keys in config, frontend, prompts,
logs, reports, or telemetry.
"""
from __future__ import annotations

import os
from pathlib import Path


def resolve_key(provider: str, ref: str) -> tuple[str, str]:
    """Resolve a credential reference.

    ``ref`` forms:
      - "env:NAME" -> os.environ["NAME"]
      - "file:/path" -> first line of file (path must exist, never logged)
      - "" -> missing
    Returns (key, code) where code is "" on success else "AUTH_MISSING".
    The key itself must never be logged by callers.
    """
    ref = (ref or "").strip()
    if not ref:
        return "", "AUTH_MISSING"
    if ref.startswith("env:"):
        name = ref[len("env:"):].strip()
        if not name:
            return "", "AUTH_MISSING"
        val = os.environ.get(name, "")
        return (val, "") if val else ("", "AUTH_MISSING")
    if ref.startswith("file:"):
        p = Path(ref[len("file:"):].strip()).expanduser()
        try:
            if not p.is_file():
                return "", "AUTH_MISSING"
            val = p.read_text(errors="replace").splitlines()
            val = val[0].strip() if val else ""
            return (val, "") if val else ("", "AUTH_MISSING")
        except Exception:
            return "", "AUTH_MISSING"
    return "", "AUTH_MISSING"


def redact_ref(ref: str) -> str:
    """Safe descriptor for logs: kind only, never the value or full path."""
    ref = (ref or "").strip()
    if ref.startswith("env:"):
        return f"env:{ref[len('env:'):].strip()[:64] or '?'}"
    if ref.startswith("file:"):
        return "file:<redacted-path>"
    return "missing"


def looks_like_opencode_private_db(path: str) -> bool:
    p = (path or "").lower()
    return ("opencode" in p and ("auth" in p or "credentials" in p
                                or "keyring" in p or ".json" in p))
