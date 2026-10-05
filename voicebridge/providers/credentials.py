"""Packet 8Q — credential references.

Keys live ONLY in environment variables, the canonical Qubit runtime ``.env``
(``<state_dir>/runtime/.env``), or an OS-secret reference (a file path outside
git whose *contents* are never logged). Qubit never reads OpenCode's private
auth database and never stores keys in config, frontend, prompts, logs,
reports, or telemetry.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class SecretRef:
    """Qubit server-side credential reference (resolved only through
    Qubit policy).

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


def _canonical_runtime_env_path() -> Optional[Path]:
    """The canonical runtime ``.env`` path, or ``None`` when unresolvable.

    Imported lazily so this leaf module stays dependency-light and cannot
    create an import cycle through :mod:`voicebridge.config`.
    """
    try:
        from .. import config as _config
        return Path(_config.canonical_runtime_env_path())
    except Exception:  # noqa: BLE001 - credential resolution must fail closed
        return None


def _env_file_value(name: str) -> str:
    """Look ``name`` up in the canonical runtime ``.env`` (dotenv style).

    Returns ``""`` when the file or the key is absent, or on any read error.
    Only the requested variable is ever returned; sibling values are never
    logged, cached, or surfaced. The fixed canonical path is re-checked against
    the browser-secret / OpenCode-private-db refusals so a misdirected state
    tree cannot turn this into a credential-harvesting read.
    """
    if not name:
        return ""
    path = _canonical_runtime_env_path()
    if path is None:
        return ""
    low = str(path).lower()
    if looks_like_browser_secret(low) or looks_like_opencode_private_db(low):
        return ""
    try:
        if not path.is_file():
            return ""
        text = path.read_text(errors="replace")
    except Exception:  # noqa: BLE001 - unreadable .env -> typed missing
        return ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        if not sep or key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value
    return ""


def resolve_key(provider: str, ref: str) -> tuple[str, str]:
    """Resolve a credential reference.

    ``ref`` forms:
      - "env:NAME" -> ``NAME`` from the canonical runtime ``.env``
        (``<state_dir>/runtime/.env``), else ``os.environ["NAME"]``. The
        canonical .env is the operator's authoritative credential store and
        is read fresh on every call, so a deliberate edit/rotation wins over
        a stale value inherited from the parent shell — exact parity with the
        reference (`hermes_cli.config.get_env_value_prefer_dotenv`).
      - "file:/path" -> first line of file (path must exist, never logged)
      - "" -> missing
    Returns (key, code) where code is "" on success else "AUTH_MISSING".
    The key itself must never be logged by callers; no diagnostic, redaction,
    or typed public field ever carries the value.
    """
    ref = (ref or "").strip()
    if not ref:
        return "", "AUTH_MISSING"
    if ref.startswith("env:"):
        name = ref[len("env:"):].strip()
        if not name:
            return "", "AUTH_MISSING"
        # File first, environment second (Hermes parity). Reversing this was
        # the rebrand divergence that broke key rotation: the vendored runtime
        # dotenv-loads .env into os.environ at import, so preferring os.environ
        # shadowed every rotated key until a full service restart (live
        # 2026-10-05: the owner replaced a rate-limited OpenCode Go key and
        # every provider call kept 402ing). _env_file_value re-reads the file
        # each call, so a direct .env edit takes effect immediately.
        val = _env_file_value(name) or os.environ.get(name, "")
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
