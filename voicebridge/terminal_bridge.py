"""Terminal bridge: MCP registry + one-conversation wiring for ``/chat``.

``/chat`` renders the real vendored terminal (``qubit --tui``) behind a PTY
(:mod:`voicebridge.pty_ws`). Two things must line up with every other Qubit
surface before that terminal is spawned:

* **MCP servers** — :func:`voicebridge.mcp_servers.list_servers` is the ONE MCP
  read authority. The TUI must see the same configured servers, so
  :func:`merge_mcp_servers` merges those definitions into the runtime home
  ``config.yaml`` ``mcp_servers:`` block. Only the registry's non-secret
  reference values (``env:``/``vault:``/``file:``) ever leave the registry — a
  raw value is refused rather than written. The merge is additive (an existing
  operator entry wins by name), atomic (temp file + ``os.replace``), backed up
  (``config.yaml.qubit-bak``), and idempotent (a no-op when nothing changed).
* **One conversation** — an explicit ``?resume=<id>`` terminal session is
  ensured in the canonical store (:func:`voicebridge.agent.resolve_session_id`
  verbatim path) so the vendored TUI resumes the transcript the other
   surfaces write. An empty resume means FRESH (reference parity): no row is
  ensured and no resume env is emitted, so the TUI forges a new session.

No PTY is created, no server is connected, and no credential is resolved here.
Everything is a filesystem/store operation, so it is hermetically testable.
"""
from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, Optional

CONFIG_NAME = "config.yaml"
MCP_SERVERS_KEY = "mcp_servers"
BACKUP_SUFFIX = ".qubit-bak"
TMP_SUFFIX = ".qubit-tmp"

#: One writer at a time for the runtime home config (the PTY server may serve
#: several terminal opens concurrently). Process-local; the vendored runtime
#: reads the file, it does not hold this lock.
_CONFIG_WRITE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Runtime-home config I/O (atomic + backup, existing keys always preserved)
# ---------------------------------------------------------------------------
def _read_config(path: Path) -> Optional[Dict[str, Any]]:
    """Load a YAML mapping; ``{}`` when absent/empty, ``None`` when unreadable.

    ``None`` is a fail-closed signal: a malformed or non-mapping file must never
    be overwritten by a merge.
    """
    if not path.is_file():
        return {}
    try:
        import yaml

        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if loaded is None:
        return {}
    return loaded if isinstance(loaded, dict) else None


def _backup(path: Path) -> str:
    """Copy the current config to ``config.yaml.qubit-bak`` atomically."""
    if not path.is_file():
        return ""
    target = path.with_name(path.name + BACKUP_SUFFIX)
    tmp = target.with_name(target.name + TMP_SUFFIX)
    shutil.copy2(path, tmp)
    os.replace(tmp, target)
    return str(target)


def _atomic_write(path: Path, data: Dict[str, Any]) -> None:
    """Serialize *data* and replace *path* atomically."""
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + TMP_SUFFIX)
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Registry -> vendored ``mcp_servers`` entry mapping (reference-only)
# ---------------------------------------------------------------------------
def _reference(value: Any) -> str:
    """Map a registry reference to its vendored form; ``""`` refuses a raw value.

    ``env:NAME`` becomes the vendored ``${env:NAME}`` placeholder so the runtime
    resolves it from the profile secret scope without Qubit ever holding the
    value. ``vault:``/``file:`` references are carried verbatim — they name
    where a secret lives, never the secret. Anything that is not one of those
    three reference forms is refused (returns ``""``) so a raw secret can never
    be written.
    """
    ref = str(value or "").strip()
    if ref.startswith("env:"):
        return "${" + ref + "}"
    if ref.startswith(("vault:", "file:")):
        return ref
    return ""


def _entry_from_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build one vendored ``mcp_servers`` entry from a canonical registry row.

    Returns ``None`` for a row with no usable transport (a read-only
    config-declared name carries no command/url, so there is nothing to merge).
    """
    transport = str(row.get("transport") or "stdio").strip().lower()
    entry: Dict[str, Any] = {}
    if transport == "http":
        url = str(row.get("url") or "").strip()
        if not url:
            return None
        entry["url"] = url
        if str(row.get("auth") or "").strip().lower() == "oauth":
            entry["auth"] = "oauth"
    else:
        command = str(row.get("command") or "").strip()
        if not command:
            return None
        entry["command"] = command
        args = row.get("args")
        if isinstance(args, (list, tuple)) and args:
            entry["args"] = [str(a) for a in args][:64]
    env = row.get("env")
    refs: Dict[str, str] = {}
    if isinstance(env, dict):
        for key, value in list(env.items())[:64]:
            ref = _reference(value)
            if ref:
                refs[str(key)[:128]] = ref
    if refs:
        entry["env"] = refs
        auth_ref = refs.get("MCP_AUTH")
        if auth_ref and auth_ref.startswith("${env:"):
            entry.setdefault("headers", {"Authorization": f"Bearer {auth_ref}"})
    entry["enabled"] = bool(row.get("enabled", False))
    return entry


def registry_mcp_servers(cfg: Any) -> Dict[str, Dict[str, Any]]:
    """Vendored ``mcp_servers`` entries derived from the ONE registry read.

    Reads :func:`voicebridge.mcp_servers.list_servers` (the canonical authority)
    and never the store file directly. Secret values are impossible by
    construction: the registry only stores references, and :func:`_reference`
    drops anything that is not one.
    """
    from . import mcp_servers as MS

    doc = MS.list_servers(cfg)
    out: Dict[str, Dict[str, Any]] = {}
    for row in (doc.get("servers") if isinstance(doc, dict) else None) or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        entry = _entry_from_row(row)
        if entry is not None:
            out[name] = entry
    return out


def merge_mcp_servers(qubit_home: Any, cfg: Any) -> Dict[str, Any]:
    """Merge the Qubit MCP registry into ``<qubit_home>/config.yaml``.

    Additive and preserving: an existing ``mcp_servers`` entry wins by name, and
    every other top-level config key is left untouched. Atomic, backed up, and
    idempotent. A broken registry or unreadable config degrades to a typed
    report; it never raises into the caller and never writes a secret.
    """
    path = Path(qubit_home) / CONFIG_NAME
    try:
        incoming = registry_mcp_servers(cfg)
    except Exception as exc:  # noqa: BLE001 - a broken registry must not write
        return {"state": "DEGRADED", "code": "MCP_REGISTRY_UNREADABLE",
                "detail": str(exc)[:200], "changed": False, "path": str(path),
                "added": [], "preserved": [], "total": 0}
    with _CONFIG_WRITE_LOCK:
        data = _read_config(path)
        if data is None:
            return {"state": "DEGRADED", "code": "TERMINAL_HOME_CONFIG_UNREADABLE",
                    "detail": "config.yaml is not a YAML mapping; refusing to "
                              "overwrite it", "changed": False, "path": str(path),
                    "added": [], "preserved": [], "total": 0}
        existing = data.get(MCP_SERVERS_KEY)
        if not isinstance(existing, dict):
            existing = {}
        merged: Dict[str, Any] = {**incoming, **existing}
        added = sorted(set(merged) - set(existing))
        preserved = sorted(existing)
        if merged == existing:
            return {"state": "OK", "changed": False, "path": str(path),
                    "backup": "", "added": [], "preserved": preserved,
                    "total": len(existing)}
        data[MCP_SERVERS_KEY] = merged
        try:
            backup = _backup(path)
            _atomic_write(path, data)
        except OSError as exc:
            return {"state": "DEGRADED", "code": "TERMINAL_HOME_CONFIG_WRITE_FAILED",
                    "detail": str(exc)[:200], "changed": False, "path": str(path),
                    "added": [], "preserved": preserved, "total": len(existing)}
        return {"state": "OK", "changed": True, "path": str(path),
                "backup": backup, "added": added, "preserved": preserved,
                "total": len(merged)}


# ---------------------------------------------------------------------------
# One canonical conversation
# ---------------------------------------------------------------------------
def canonical_terminal_session(cfg: Any, requested: Optional[str] = None
                               ) -> Dict[str, Any]:
    """Resolve the ``/chat`` session to a resume target, or fresh when empty.

    Reference parity (``hermes_cli/web_routers/chat_ws.py`` + ``web_server_chat``):
    an empty ``resume`` means a FRESH chat — no ``HERMES_TUI_RESUME`` env, so
    the vendored TUI forges a brand-new session via ``session.create``. Only an
    explicit non-empty id is kept verbatim (and ensured in the canonical store
    so the TUI resumes a real transcript). This matches the reference: opening
    ``/chat`` with no ``?resume=`` never reopens the old ``dashboard-chat``
    transcript.
    """
    sid = str(requested or "").strip()[:80]
    if not sid:
        return {"session_id": "", "ensured": False, "detail": "fresh",
                "authority": "voicebridge.agent.canonical_session_id"}
    ensured = False
    detail = ""
    try:
        from . import chat_transport

        res = chat_transport.store(cfg).ensure(sid, identity="terminal:chat")
        if res.get("state") == "OK":
            ensured = True
            sid = str((res.get("session") or {}).get("id") or sid)
        else:
            detail = str(res.get("detail") or res.get("code") or "")
    except Exception as exc:  # noqa: BLE001 - a store fault must not block /chat
        detail = str(exc)[:200]
    return {"session_id": sid, "ensured": ensured, "detail": detail,
            "authority": "voicebridge.agent.canonical_session_id"}


def terminal_resume_env(session_id: Any) -> Dict[str, str]:
    """Env seeds that make the vendored TUI resume *session_id*.

    Both names are set: the launched Ink TUI reads ``QUBIT_TUI_RESUME`` (the
    rebranded name in ``ui-tui/src/config/env.ts``), while the server-side PTY
    registry reads ``HERMES_TUI_RESUME``. The vendored Python launch paths only
    write the latter, so the former is the missing half of the hand-off.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return {}
    return {"QUBIT_TUI_RESUME": sid, "HERMES_TUI_RESUME": sid}


__all__ = [
    "CONFIG_NAME", "MCP_SERVERS_KEY", "registry_mcp_servers",
    "merge_mcp_servers", "canonical_terminal_session", "terminal_resume_env",
]
