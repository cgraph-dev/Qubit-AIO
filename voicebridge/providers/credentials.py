"""Packet 8Q — credential references.

Keys live ONLY in environment variables or an OS-secret reference (a file path
outside git whose *contents* are never logged). Qubit never reads OpenCode's
private auth database and never stores keys in config, frontend, prompts,
logs, reports, or telemetry.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SecretRef:
    """Packet 8T-F — server-side credential reference (OpenClaw SecretRef shape,
    resolved only through Qubit policy).

    ``source`` is "env" (``name`` = variable) or "file" (``name`` = path).
    Values are resolved server-side via :func:`resolve_key` and the browser /
    private-DB refusals below; the value must never be logged, and the file
    path is redacted to kind-only in output. Anything else fails closed.
    """
    source: str
    name: str


def resolve_secret_ref(ref: SecretRef | Mapping[str, str] | str,
                       provider: str = "secret-ref") -> tuple[str, str]:
    """Resolve a SecretRef to (value, code); code is "" on success.

    Accepts a SecretRef, a {"source","name"} mapping, or a legacy
    "env:NAME"/"file:PATH" string. Refuses unknown sources, browser-secret
    paths, and OpenCode private databases with AUTH_MISSING (never raises
    for policy reasons — callers treat missing as typed degraded).
    """
    source: str
    name: str
    if isinstance(ref, SecretRef):
        source, name = ref.source, ref.name
    elif isinstance(ref, Mapping):
        source, name = str(ref.get("source", "")), str(ref.get("name", ""))
    elif isinstance(ref, str):
        text = ref.strip()
        if text.startswith("env:") or text.startswith("file:"):
            source, name = text.split(":", 1)
        else:
            return "", "AUTH_MISSING"
    else:
        return "", "AUTH_MISSING"
    source, name = source.strip(), name.strip()
    if source not in ("env", "file") or not name:
        return "", "AUTH_MISSING"
    if source == "file" and (looks_like_browser_secret(name)
                             or looks_like_opencode_private_db(name)):
        return "", "AUTH_MISSING"
    return resolve_key(provider, f"{source}:{name}")


def redact_secret_ref(ref: SecretRef | Mapping[str, str] | str) -> str:
    """Kind-only descriptor for logs: never the value or full path."""
    if isinstance(ref, SecretRef):
        source, name = ref.source, ref.name
    elif isinstance(ref, Mapping):
        source, name = str(ref.get("source", "")), str(ref.get("name", ""))
    elif isinstance(ref, str):
        return redact_ref(ref)
    else:
        return "missing"
    source = source.strip()
    if source == "env" and name.strip():
        return f"env:{name.strip()[:64]}"
    if source == "file" and name.strip():
        return "file:<redacted-path>"
    return "missing"


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
