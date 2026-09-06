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


_BROWSER_SECRET_BASENAMES = frozenset({
    "cookies", "cookies.sqlite", "cookies.sqlite-wal", "login data",
    "login data.sqlite", "logins.json", "key4.db", "key3.db", "cert9.db",
    "local state", "secure preferences", "preferences", "history",
    "history.sqlite", "web data", "web data.sqlite", "formhistory.sqlite",
    "permissions.sqlite", "content-prefs.sqlite",
})

_BROWSER_PROFILE_MARKERS = ("chrome", "chromium", "firefox", "brave",
                            "edge", "opera", "vivaldi", "comet")


def looks_like_browser_secret(path: str) -> bool:
    """True when ``path`` points at browser cookie/session/credential stores.

    A ``file:`` credential reference must never resolve browser secrets as a
    provider key: refuse first lines of Cookies/Login Data/Local State-style
    stores and files inside browser profile trees.
    """
    low = (path or "").lower().replace("\\", "/")
    base = low.rsplit("/", 1)[-1].strip()
    if base in _BROWSER_SECRET_BASENAMES:
        return True
    if any(m in low for m in _BROWSER_PROFILE_MARKERS) and base not in (
            "", "bookmarks", "bookmarks.bak"):
        return "profile" in low or "default" in low or ".mozilla" in low
    return False


def looks_like_opencode_private_db(path: str) -> bool:
    p = (path or "").lower()
    return ("opencode" in p and ("auth" in p or "credentials" in p
                                or "keyring" in p or ".json" in p))
