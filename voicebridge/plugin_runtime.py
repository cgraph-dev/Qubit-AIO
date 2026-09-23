"""Qubit in-process plugin host contract (Qubit audit #40).

Qubit ships a real plugin *registry* over four kinds (``tool``, ``channel``,
``provider``, ``hook``) with validated manifests and honest typed states, plus
a real *in-process runtime*:

* a manifest is metadata, never payload: ``register`` validates
  ``id``/``kind``/``entrypoint``/``digest`` with hard bounds, records it, and
  never imports, evaluates, downloads, or executes anything;
* ``bind_handler`` binds a host-owned Python callable for any kind, giving a
  real tool/channel/provider/hook execution runtime that is process-local and
  never persisted as code (after a restart ``invoke`` degrades to the typed
  ``PLUGIN_RUNTIME_UNAVAILABLE`` gate until the host rebinds);
* ``invoke`` is gated in order: emergency stop -> fresh confirmation -> the
  plugin exists -> an in-process host handler is installed. A manifest
  ``entrypoint`` does NOT grant execution; without a host-registered handler
  the call resolves to the typed ``PLUGIN_RUNTIME_UNAVAILABLE`` gate and
  nothing runs. A single safe built-in ``builtin.echo`` handler is installed
  so the in-process contract is exercisable end to end. Host-owned *adapters*
  over an existing canonical Qubit backend (``builtin.achievements`` over
  :mod:`voicebridge.qubit_plugins`) are registered in ``_HOST_HANDLERS`` and
  are invocable through the same gate; see :func:`host_capabilities`;
* this module also carries the MCP hosted-app loopback attach endpoint
  (``mcp_*``), policy conformance rules/attestation (``policy_*``), and local
  marketplace discovery (``marketplace_*``);
* every output (entrypoint, arguments, results, errors) passes through
  :func:`redaction.redact_text`/:func:`redaction.redact_obj`; the registry
  file is 0600 and bounded;
* mutating operations refuse while the emergency stop is latched via the same
  durable :class:`PowerControlStore` every other surface observes.

The registry lives in ``<state_dir>/plugin-runtime.json``. There is no outbound
network and no shell: the MCP endpoint binds ``127.0.0.1`` only, and this
module imports neither ``subprocess`` nor any outbound HTTP client.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SCHEMA = "qubit-plugin-runtime/1"
STORE_NAME = "plugin-runtime.json"
MAX_MANIFEST_BYTES = 65536
MAX_PLUGINS = 256
MAX_ARGS_BYTES = 16384
MAX_RESULT_CHARS = 4000
MAX_FILE_BYTES = 2_000_000

# MCP hosted-app loopback attach store (OC-D10). Grants never leave the host.
MCP_SCHEMA = "qubit-mcp-grants/1"
MCP_STORE_NAME = "mcp-grants.json"
MCP_HOST = "127.0.0.1"
# Wave 5: the MCP endpoint is an *accounted bounded loopback helper*, not a
# second product listener/authority. It may only ever bind a loopback literal;
# a non-loopback host is refused even if ``MCP_HOST`` is tampered with.
MCP_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
MCP_MAX_GRANTS = 64
MCP_MAX_BODY_BYTES = 65536
MCP_MAX_CAPABILITIES = 16
MCP_DEFAULT_CAPABILITIES = ("initialize", "tools/list", "tools/call")

# A12 disposition: the MCP loopback endpoint is NOT a second product listener
# or gateway. It is DISABLED BY DEFAULT — no socket is bound until an operator
# explicitly opts in by minting a named active attach grant through the
# human-confirmed ``mcp_attach`` flow (``_authorize_mutation`` over the shared
# ApprovalJournal) with the one durable e-stop clear. Without that operator
# opt-in ``mcp_serve`` raises the typed ``MCP_REFUSED`` refusal and binds
# nothing; there is no config, model, or plugin path that widens it, and no
# product CLI/voice/dashboard startup path calls it. See also
# ``docs/archive/2026-09-21/SECURITY_AUDIT_WAVE4.md`` §7.3.
MCP_SERVE_DISPOSITION = (
    "disabled-by-default; operator-opt-in-via-confirmed-attach-grant; "
    "loopback-helper-not-a-product-listener")

# Policy conformance store (OC-D02).
POLICY_SCHEMA = "qubit-policy-rules/1"
POLICY_STORE_NAME = "policy-rules.json"
POLICY_MAX_RULES = 128
POLICY_MAX_RULE_BYTES = 2048
POLICY_MAX_FILES = 200
POLICY_MAX_SCAN_BYTES = 2_000_000
POLICY_LEVELS = ("deny", "warn")

PLUGIN_KINDS = ("tool", "channel", "provider", "hook")

CONFIRM_CODE = "PLUGIN_CONFIRM_REQUIRED"
ESTOP_CODE = "PLUGIN_ESTOP"
NOT_FOUND_CODE = "PLUGIN_NOT_FOUND"
EXISTS_CODE = "PLUGIN_EXISTS"
LIMIT_CODE = "PLUGIN_LIMIT"
BAD_INPUT_CODE = "PLUGIN_BAD_INPUT"
BAD_ARGS_CODE = "PLUGIN_BAD_ARGS"
INVALID_MANIFEST_CODE = "PLUGIN_MANIFEST_INVALID"
TOO_LARGE_CODE = "PLUGIN_MANIFEST_TOO_LARGE"
CORRUPT_CODE = "PLUGIN_STORE_CORRUPT"
UNAVAILABLE_CODE = "PLUGIN_RUNTIME_UNAVAILABLE"
REFUSED_CODE = "PLUGIN_REFUSED"
RELOAD_CODE = "PLUGIN_RELOAD_LOCAL_REREAD"
HANDLER_FAILED_CODE = "PLUGIN_HANDLER_FAILED"
HANDLER_REQUIRED_CODE = "PLUGIN_HANDLER_REQUIRED"

# Plugin catalog (A27): installable rows the dashboard renders, plus the
# canonical install operation that records/validates a local reviewed manifest.
CATALOG_SCHEMA = "qubit-plugin-catalog/1"
CATALOG_DIR = "plugin-catalog"
INSTALL_CAPABILITY = "plugin.install"
MAX_CATALOG_ENTRIES = 256
MAX_CATALOG_BYTES = 65536
BRANDING_CODE = "PLUGIN_BRANDING"
INSTALL_SOURCE_CODE = "PLUGIN_INSTALL_SOURCE"

# Real directory-plugin lifecycle (source port of the vendored
# ``plugins/plugin_loader.py`` + ``qubit_cli/plugins.py`` loading path). A
# plugin's code is copied into the QUBIT_HOME-owned plugin store and loaded by
# importing its ``__init__.py`` and calling ``register(ctx)`` exactly as the
# vendored loader does; the captured registrations are invocable through
# :func:`invoke` (e-stop + shared journal + audit).
PLUGIN_HOME_DIR = "plugins"
SOURCE_DIRECTORY = "directory"
PLUGIN_MANIFEST_FILES = ("plugin.yaml", "plugin.yml", "plugin.json")
# Highest manifest-file schema and runtime API generation Qubit understands.
PLUGIN_MANIFEST_VERSION = 2
PLUGIN_API_VERSION = 1
MAX_PLUGIN_SOURCE_BYTES = 8_000_000
MAX_PLUGIN_FILES = 2000
MAX_PLUGIN_DEPTH = 12

LOAD_FAILED_CODE = "PLUGIN_LOAD_FAILED"
SOURCE_INVALID_CODE = "PLUGIN_SOURCE_INVALID"
DEPENDENCY_CODE = "PLUGIN_DEPENDENCY_MISSING"
COMPAT_CODE = "PLUGIN_COMPAT_INCOMPATIBLE"
TOOL_REQUIRED_CODE = "PLUGIN_TOOL_REQUIRED"
DISABLED_CODE = "PLUGIN_DISABLED"
NO_SURFACE_CODE = "PLUGIN_NO_SURFACE"


MCP_CONFIRM_CODE = "MCP_CONFIRM_REQUIRED"
MCP_ESTOP_CODE = "MCP_ESTOP"
MCP_NOT_FOUND_CODE = "MCP_GRANT_NOT_FOUND"
MCP_EXISTS_CODE = "MCP_GRANT_EXISTS"
MCP_LIMIT_CODE = "MCP_GRANT_LIMIT"
MCP_BAD_INPUT_CODE = "MCP_BAD_INPUT"
MCP_UNAUTHORIZED_CODE = "MCP_UNAUTHORIZED"
MCP_REFUSED_CODE = "MCP_REFUSED"
MCP_CORRUPT_CODE = "MCP_STORE_CORRUPT"

POLICY_CONFIRM_CODE = "POLICY_CONFIRM_REQUIRED"
POLICY_BAD_INPUT_CODE = "POLICY_BAD_INPUT"
POLICY_NOT_FOUND_CODE = "POLICY_RULE_NOT_FOUND"
POLICY_EXISTS_CODE = "POLICY_RULE_EXISTS"
POLICY_LIMIT_CODE = "POLICY_RULE_LIMIT"
POLICY_CORRUPT_CODE = "POLICY_STORE_CORRUPT"
POLICY_ESTOP_CODE = "POLICY_ESTOP"
POLICY_ATTEST_ACTOR_CODE = "POLICY_ATTEST_ACTOR"

MARKETPLACE_REMOTE_CODE = "MARKETPLACE_REMOTE_REFUSED"
MARKETPLACE_BAD_INPUT_CODE = "MARKETPLACE_BAD_INPUT"

_MCP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_POLICY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_ENTRY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_FIELDS = frozenset((
    "id", "kind", "entrypoint", "digest", "label", "provider", "version",
    "capabilities", "description"))


class PluginRuntimeError(Exception):
    """Typed plugin-runtime failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def _redact_obj(obj: Any) -> Any:
    from .redaction import redact_obj

    return redact_obj(obj)


def _now() -> float:
    return time.time()


def _store_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / STORE_NAME


def _blank() -> Dict[str, Any]:
    return {"schema": SCHEMA, "plugins": {}}


def _load(cfg: Any) -> Dict[str, Any]:
    path = _store_path(cfg)
    if not path.exists():
        return _blank()
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise PluginRuntimeError(CORRUPT_CODE, "plugin store too large")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except PluginRuntimeError:
        raise
    except (OSError, ValueError) as exc:
        raise PluginRuntimeError(
            CORRUPT_CODE, f"plugin store unreadable: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA \
            or not isinstance(raw.get("plugins"), dict):
        raise PluginRuntimeError(CORRUPT_CODE, "plugin store has an unknown "
                                               "schema")
    return raw


def _save(cfg: Any, state: Dict[str, Any]) -> None:
    path = _store_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{STORE_NAME}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        raise PluginRuntimeError(
            CORRUPT_CODE, f"plugin store write failed: {exc}") from exc


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"plugin.{op}", actor=actor, **detail)
    except Exception:
        pass


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": ESTOP_CODE, "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop "
                           "is latched; reset it before changing plugins.",
            "owner": "qubit-policy",
            "rollback": "registry untouched; nothing executed"}


# ---------------------------------------------------------------------------
# Shared confirmation authority (Wave 4, gap G5). Plugin/MCP/policy mutations
# used a boolean ``confirmed`` flag with no binding to the durable journal, so
# replay/expiry did not apply. They now consult the SAME durable
# ``ApprovalJournal`` the dashboard/voice/CLI approval surface uses.
# ---------------------------------------------------------------------------
def _approval_journal(cfg: Any):
    from pathlib import Path

    from .approvals import ApprovalJournal

    return ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                           default_ttl_s=120.0)


def _record_human_confirmation(cfg: Any, capability: str,
                               requested_by: str) -> None:
    """Durably record (and immediately consume) a human ``--confirm``."""
    try:
        journal = _approval_journal(cfg)
        actor = _redact(str(requested_by))[:64]
        item = journal.create(capability, f"{capability} via {actor}",
                              ttl_s=120.0, session="plugin", actor=actor)
        journal.respond(item.id, "approve")
        journal.finish(item.id)
    except Exception as exc:  # noqa: BLE001 - fail closed at the boundary
        raise PluginRuntimeError(
            CONFIRM_CODE,
            "shared confirmation unavailable: " + _redact(str(exc))[:120]
        ) from exc
    _audit("confirm", requested_by,
           {"capability": capability, "confirmation_id": item.id})


def _consume_journal_approval(cfg: Any, capability: str,
                              confirmation_id: str) -> bool:
    """Consume a durable human approval exactly once (expiry via the journal).

    A replayed/expired/wrong-capability id returns False; a fresh approved
    entry is settled to COMPLETE so a second call with the same id is refused.
    """
    from .approvals import (ABORTED, COMPLETE, DENIED, EXECUTING, IN_PROGRESS)

    try:
        journal = _approval_journal(cfg)
        item = journal.get(str(confirmation_id or "").strip())
    except Exception:
        return False
    if item is None or item.capability != capability:
        return False
    if item.decision not in (None, "approve"):
        return False
    if item.state == IN_PROGRESS:
        if item.expires_at <= _now():
            journal.abort(item.id)
            return False
        item = journal.respond(item.id, "approve")     # -> EXECUTING
    if item.state == EXECUTING:
        journal.finish(item.id)                        # -> COMPLETE (consumed)
        return True
    if item.state in (COMPLETE, ABORTED, DENIED):
        return False
    return False


def _authorize_mutation(cfg: Any, capability: str, *, confirmed: bool,
                        confirmation_id: str, requested_by: str,
                        confirm_code: str = CONFIRM_CODE,
                        estop_code: str = ESTOP_CODE) -> None:
    """One gate: e-stop -> durable journal confirmation, fail closed."""
    if estop_engaged(cfg):
        raise PluginRuntimeError(estop_code, "emergency stop is latched")
    if confirmation_id:
        if not _consume_journal_approval(cfg, capability, confirmation_id):
            raise PluginRuntimeError(
                confirm_code,
                "confirmation id is unknown, expired, already used, or for "
                "another capability")
        return
    if not confirmed:
        raise PluginRuntimeError(
            confirm_code,
            f"{capability} requires explicit --confirm or a human approval id")
    _record_human_confirmation(cfg, capability, requested_by)


def _check_id(text: str) -> str:
    clean = (text or "").strip().lower()
    if not _ID_RE.match(clean):
        raise PluginRuntimeError(BAD_INPUT_CODE,
                                 "id must match ^[a-z0-9][a-z0-9_.-]{0,63}$")
    return clean


def _bounded(text: Any, limit: int) -> str:
    return str(text or "").strip()[:limit]


def _validate_manifest(manifest: Any) -> Dict[str, Any]:
    """Validate and normalize a plugin manifest (metadata only, bounded)."""
    if isinstance(manifest, str):
        raw = manifest
        if len(raw.encode("utf-8")) > MAX_MANIFEST_BYTES:
            raise PluginRuntimeError(
                TOO_LARGE_CODE,
                f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
        try:
            manifest = json.loads(raw)
        except ValueError as exc:
            raise PluginRuntimeError(
                INVALID_MANIFEST_CODE, f"manifest is not JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise PluginRuntimeError(INVALID_MANIFEST_CODE,
                                 "manifest must be an object")
    try:
        blob = json.dumps(manifest, default=repr)
    except (TypeError, ValueError):
        raise PluginRuntimeError(INVALID_MANIFEST_CODE,
                                 "manifest is not serializable") from None
    if len(blob.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise PluginRuntimeError(
            TOO_LARGE_CODE, f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
    unknown = set(manifest) - _MANIFEST_FIELDS
    if unknown:
        raise PluginRuntimeError(
            INVALID_MANIFEST_CODE,
            "manifest has unknown fields: " + ", ".join(sorted(unknown)))
    pid = str(manifest.get("id", "")).strip().lower()
    if not _ID_RE.match(pid):
        raise PluginRuntimeError(
            INVALID_MANIFEST_CODE,
            "id must match ^[a-z0-9][a-z0-9_.-]{0,63}$")
    kind = str(manifest.get("kind", "")).strip().lower()
    if kind not in PLUGIN_KINDS:
        raise PluginRuntimeError(
            INVALID_MANIFEST_CODE, f"kind must be one of {PLUGIN_KINDS}")
    entry = manifest.get("entrypoint", "")
    if not isinstance(entry, str) or not _ENTRY_RE.match(entry.strip()):
        raise PluginRuntimeError(
            INVALID_MANIFEST_CODE,
            "entrypoint must be a dotted/short identifier (no paths, URLs, "
            "shell metacharacters, or whitespace)")
    digest = str(manifest.get("digest", "")).strip().lower()
    if not _DIGEST_RE.match(digest):
        raise PluginRuntimeError(
            INVALID_MANIFEST_CODE,
            "digest must be a lowercase sha256 hex digest (64 chars)")
    caps = manifest.get("capabilities", []) or []
    if not isinstance(caps, (list, tuple)):
        raise PluginRuntimeError(INVALID_MANIFEST_CODE,
                                 "capabilities must be a list")
    clean_caps = [_bounded(c, 64) for c in caps][:32]
    if any(not _ID_RE.match(c) for c in clean_caps):
        raise PluginRuntimeError(INVALID_MANIFEST_CODE,
                                 "capability ids must match the plugin id rule")
    clean_label = _bounded(manifest.get("label", ""), 64)
    clean_description = _bounded(manifest.get("description", ""), 240)
    _check_branding(clean_label, clean_description)
    return {"id": pid, "kind": kind, "entrypoint": entry.strip(),
            "digest": digest,
            "label": clean_label,
            "description": clean_description,
            "provider": _bounded(manifest.get("provider", ""), 64),
            "version": _bounded(manifest.get("version", ""), 32),
            "capabilities": sorted(set(clean_caps))}


def _echo_handler(args: Dict[str, Any]) -> Dict[str, Any]:
    """Safe built-in test handler: echoes bounded text, touches nothing."""
    return {"echo": str(args.get("text", ""))[:200],
            "handler": "builtin.echo"}


def _achievements_adapter(cfg: Any, args: Dict[str, Any]) -> Any:
    """Host adapter over the ONE canonical achievements backend.

    ``hermes-achievements`` ships as a *dashboard* plugin (a FastAPI router +
    a frontend bundle), not as a native ``tool``/``channel``/``provider``/
    ``hook`` plugin. Qubit already owns its real local execution path in
    :mod:`voicebridge.qubit_plugins` (the vendored engine over the real
    ``qubit_state.SessionDB``). This adapter exposes that same backend through
    the canonical in-process plugin broker so the capability is invocable with
    e-stop + shared confirmation + audit -- it never loads a second engine and
    never opens the network or a shell.

    ``action`` selects ``snapshot`` (default), ``status`` or ``rescan``.
    """
    action = str((args or {}).get("action", "snapshot") or "snapshot").strip()
    action = action.lower()
    if action not in ("snapshot", "status", "rescan"):
        raise PluginRuntimeError(
            BAD_ARGS_CODE, "action must be one of snapshot, status, rescan")
    from . import qubit_plugins as _HP

    if action == "snapshot":
        code, payload = _HP.h_plugin_achievements(cfg, {}, {}, {})
    elif action == "status":
        code, payload = _HP.h_plugin_achievements_scan_status(cfg, {}, {}, {})
    else:
        code, payload = _HP.h_plugin_achievements_rescan(cfg, {}, {}, {})
    if code != 200 or not isinstance(payload, dict):
        detail = ""
        if isinstance(payload, dict):
            detail = str(payload.get("detail") or payload.get("code") or "")
        raise RuntimeError(_redact(detail)[:200] or
                           f"achievements backend returned {code}")
    return payload


# Built-in in-process plugins: the manifest is implicit and cannot be
# installed over or removed (the host owns the handler).
_BUILTIN: Dict[str, Dict[str, Any]] = {
    "builtin.echo": {
        "id": "builtin.echo", "kind": "tool", "entrypoint": "builtin:echo",
        "label": "Echo (built-in)", "provider": "", "version": "1.0.0",
        "capabilities": ["echo"], "builtin": True},
}
_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Any]] = {
    "builtin.echo": _echo_handler,
}

# Host-owned adapter capabilities (see :func:`_achievements_adapter`). These
# are deliberately NOT ``_BUILTIN`` catalog rows: they are Qubit's own thin
# adapters over an existing canonical backend, resolved from the live config at
# invoke time, so their handler signature takes ``(cfg, args)``. They are
# invocable through :func:`invoke`, visible in :func:`status`/`host_capabilities`
# and on the loopback MCP tool list, but they add no installable manifest and
# cannot be registered over or removed.
_HOST_MANIFESTS: Dict[str, Dict[str, Any]] = {
    "builtin.achievements": {
        "id": "builtin.achievements", "kind": "tool",
        "entrypoint": "builtin:achievements", "label": "Achievements",
        "provider": "", "version": "0.4.0",
        "description": "Steam-style achievements computed from real local "
                       "Qubit session history (adapter over the canonical "
                       "dashboard plugin engine).",
        "capabilities": ["achievements.snapshot", "achievements.status",
                         "achievements.rescan"],
        "actions": ["snapshot", "status", "rescan"],
        "builtin": True, "host_adapter": True},
}
_HOST_HANDLERS: Dict[str, Callable[[Any, Dict[str, Any]], Any]] = {
    "builtin.achievements": _achievements_adapter,
}


def _public(rec: Dict[str, Any]) -> Dict[str, Any]:
    pid = rec["id"]
    provides = rec.get("provides") or {}
    enabled = bool(rec.get("enabled", True))
    loaded = _LOADED.get(pid)
    has_tool = bool(provides.get("tools")) or bool(
        loaded and loaded.get("tools"))
    is_dir = rec.get("source_kind") == SOURCE_DIRECTORY
    return {"id": pid, "kind": rec.get("kind", ""),
            "entrypoint": _redact(rec.get("entrypoint", ""))[:160],
            "digest": rec.get("digest", ""),
            "label": _redact(rec.get("label", ""))[:64],
            "description": _redact(rec.get("description", ""))[:240],
            "provider": _redact(rec.get("provider", ""))[:64],
            "version": rec.get("version", ""),
            "capabilities": list(rec.get("capabilities", [])),
            "builtin": bool(rec.get("builtin", False)),
            "enabled": enabled,
            "source_kind": rec.get("source_kind", ""),
            "plugin_class": rec.get("plugin_class", ""),
            "install_dir": str(rec.get("install_dir", "")) if is_dir else "",
            "load_state": rec.get("load_state", ""),
            "setup_required": bool(rec.get("setup_required", False)),
            "provides": {
                "tools": list(provides.get("tools", [])),
                "hooks": list(provides.get("hooks", [])),
                "skills": list(provides.get("skills", [])),
                "commands": list(provides.get("commands", [])),
                "cli_commands": list(provides.get("cli_commands", [])),
                "memory_providers": list(provides.get("memory_providers", [])),
                "context_engines": list(provides.get("context_engines", [])),
                "unsupported": list(provides.get("unsupported", [])),
                "dashboard": provides.get("dashboard"),
            },
            "warnings": list(rec.get("warnings", [])),
            "handler": (pid in _HANDLERS or pid in _HOST_HANDLERS
                        or (is_dir and enabled and has_tool)),
            "host_adapter": pid in _HOST_HANDLERS,
            "registered_at": rec.get("registered_at"),
            "updated_by": rec.get("updated_by", "")}


def _find(cfg: Any, plugin_id: str) -> Optional[Dict[str, Any]]:
    clean = _check_id(plugin_id)
    if clean in _BUILTIN:
        return dict(_BUILTIN[clean])
    if clean in _HOST_MANIFESTS:
        return dict(_HOST_MANIFESTS[clean])
    return _load(cfg)["plugins"].get(clean)


def _host_handler_ids() -> List[str]:
    return sorted(pid for pid in _HOST_MANIFESTS if pid in _HOST_HANDLERS)


def _handler_kinds(cfg: Any) -> Dict[str, str]:
    """Map every bound in-process handler to its plugin kind (honest view)."""
    kinds: Dict[str, str] = {}
    for pid, rec in _BUILTIN.items():
        if pid in _HANDLERS:
            kinds[pid] = rec.get("kind", "")
    for pid, rec in _HOST_MANIFESTS.items():
        if pid in _HOST_HANDLERS:
            kinds[pid] = rec.get("kind", "")
    try:
        for pid, rec in _load(cfg)["plugins"].items():
            if pid in _HANDLERS:
                kinds[pid] = rec.get("kind", "")
    except PluginRuntimeError:
        pass
    return kinds


# ---------------------------------------------------------------------------
# Plugin catalog (A27) — installable rows for the dashboard /plugins surface.
#
# A manifest is metadata only. The catalog exposes every plugin the dashboard
# can render (built-in, already-registered, and reviewed local manifests) with
# an explicit ``installed`` flag and the canonical ``plugin.install``
# capability. Reviewed local manifests live in a bounded directory
# ``<state_dir>/plugin-catalog/*.json``; they are validated exactly like a
# ``register`` manifest and are never fetched from the network.
# ---------------------------------------------------------------------------
# Upstream product words are assembled from fragments so the de-brand engine's
# prose pass cannot rewrite the guard into matching "Qubit" itself.
_UPSTREAM_BRAND_RE = re.compile(
    r"\b(?:" + "Her" + "mes" + "|" + "No" + "us" + "|" + "Open" + "Claw"
    + r")\b", re.IGNORECASE)


def _check_branding(label: str, description: str = "") -> None:
    """Refuse upstream product names on the Qubit plugin product surface."""
    for field, value in (("label", label), ("description", description)):
        if value and _UPSTREAM_BRAND_RE.search(value):
            raise PluginRuntimeError(
                BRANDING_CODE,
                f"plugin {field} exposes an upstream product name on the "
                "Qubit surface; keep attribution in provenance records")


def _catalog_dir(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / CATALOG_DIR


def _catalog_source(cfg: Any) -> Dict[str, Dict[str, Any]]:
    """Reviewed local manifests available to install, keyed by identifier."""
    out: Dict[str, Dict[str, Any]] = {}
    root = _catalog_dir(cfg)
    if not root.is_dir():
        return out
    entries = sorted(root.glob("*.json"))[:MAX_CATALOG_ENTRIES]
    for path in entries:
        try:
            if path.stat().st_size > MAX_CATALOG_BYTES:
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            clean = _validate_manifest(raw)
        except (OSError, ValueError, PluginRuntimeError):
            continue
        ident = path.stem
        out[ident] = clean
    return out


def _catalog_row(rec: Dict[str, Any], *, installed: bool,
                 source: str, identifier: str = "") -> Dict[str, Any]:
    label = rec.get("label", "") or rec.get("id", "")
    return {
        "id": rec.get("id", ""),
        "identifier": identifier or rec.get("id", ""),
        "name": label,
        "label": label,
        "description": _redact(rec.get("description", ""))[:240],
        "kind": rec.get("kind", ""),
        "provider": _redact(rec.get("provider", ""))[:64],
        "version": rec.get("version", ""),
        "capabilities": list(rec.get("capabilities", [])),
        "installed": bool(installed),
        "builtin": bool(rec.get("builtin", False)),
        "source": source,
        "can_install": not installed,
        "install_capability": INSTALL_CAPABILITY,
        "enabled": bool(rec.get("enabled", True)),
        "source_kind": rec.get("source_kind", ""),
        "load_state": rec.get("load_state", ""),
        "setup_required": bool(rec.get("setup_required", False)),
    }


def plugin_catalog(cfg: Any) -> Dict[str, Any]:
    """Installable plugin rows for the dashboard catalog (read-only).

    Returns built-in plugins, already-registered plugins, and every validated
    reviewed local manifest. Remote catalog entries are refused by pinned-source
    policy and are reported as a typed ``remote`` refusal marker, never fetched.
    """
    if estop_engaged(cfg):
        return refuse_estop("plugin catalog")
    rows: List[Dict[str, Any]] = []
    for pid in sorted(_BUILTIN):
        rows.append(_catalog_row(_BUILTIN[pid], installed=True,
                                 source="qubit-builtin"))
    registered = _load(cfg)["plugins"]
    for pid in sorted(registered):
        rows.append(_catalog_row(registered[pid], installed=True,
                                 source="qubit-local"))
    reviewed = _catalog_source(cfg)
    for ident in sorted(reviewed):  # reviewed, not yet installed
        if ident in registered:
            continue
        rows.append(_catalog_row(reviewed[ident], installed=False,
                                 source="qubit-reviewed", identifier=ident))
    return {"state": "OK", "schema": CATALOG_SCHEMA,
            "install_capability": INSTALL_CAPABILITY,
            "remote": {"state": "REFUSED", "code": MARKETPLACE_REMOTE_CODE,
                       "detail": "remote plugin catalog entries are refused by "
                                 "pinned-source policy; reviewed local "
                                 "manifests only"},
            "plugins": rows}


_RELOAD_LOCK = threading.Lock()
#: Last content digest per plugin store, keyed by the resolved store path so a
#: reload of one store never reports another store's history (the flag is
#: per-store, not process-global, and stays deterministic under test isolation).
_RELOAD_DIGEST: Dict[str, str] = {}


def _reload_key(cfg: Any) -> str:
    try:
        return str(_store_path(cfg))
    except (OSError, ValueError, TypeError):
        return "default"


def reload(cfg: Any) -> Dict[str, Any]:
    """Re-read the plugin store/catalog and re-load enabled directory plugins.

    The real local counterpart of Qubit's ``plugins.reload`` lifecycle
    mutation. The store and reviewed catalog are re-read from disk, and every
    enabled directory plugin is evicted from ``sys.modules`` and re-imported
    so a changed on-disk plugin takes effect without a process restart. A
    per-store content digest tells the caller whether anything changed since
    the previous reload in this process. Failures are reported truthfully per
    plugin; a reload never fabricates success.
    """
    if estop_engaged(cfg):
        return refuse_estop("plugin reload")
    state = _load(cfg)
    reloaded: List[str] = []
    errors: List[Dict[str, Any]] = []
    for pid, rec in list((state.get("plugins") or {}).items()):
        if rec.get("source_kind") != SOURCE_DIRECTORY:
            continue
        if not rec.get("enabled", True):
            continue
        try:
            loaded = _load_plugin_record(cfg, rec)
            _apply_load_to_record(rec, loaded)
            reloaded.append(pid)
        except PluginRuntimeError as exc:
            rec["load_state"] = "ERROR"
            rec["load_error"] = exc.detail[:240]
            errors.append({"id": pid, "code": exc.code, "detail": exc.detail[:200]})
        state["plugins"][pid] = rec
    if reloaded or errors:
        _save(cfg, state)
    catalog = plugin_catalog(cfg)
    digest = hashlib.sha256(json.dumps(
        {"plugins": state.get("plugins", {}),
         "catalog": catalog.get("plugins", [])},
        sort_keys=True, default=str).encode("utf-8")).hexdigest()
    key = _reload_key(cfg)
    with _RELOAD_LOCK:
        previous = _RELOAD_DIGEST.get(key)
        _RELOAD_DIGEST[key] = digest
    _audit("reload", "cli:user", {"reloaded": reloaded, "errors": len(errors)})
    return {"state": "OK", "code": RELOAD_CODE,
            "reloaded": True,
            "changed": previous is not None and previous != digest,
            "first_reload": previous is None,
            "plugin_count": len(state.get("plugins", {}) or {}),
            "catalog_count": len(catalog.get("plugins", []) or []),
            "reloaded_plugins": reloaded,
            "errors": errors,
            "digest": digest,
            "authority": "re-reads the persisted plugin store/catalog and "
                         "re-imports enabled directory plugins; no process "
                         "restart"}


def status(cfg: Any) -> Dict[str, Any]:
    """Typed runtime state: the in-process host is real, provider runtime is
    not. An unconfigured/absent execution runtime is reported honestly."""
    latched = estop_engaged(cfg)
    try:
        count = len(_load(cfg)["plugins"])
    except PluginRuntimeError:
        count = -1
    try:
        directory = sum(1 for r in _load(cfg)["plugins"].values()
                        if r.get("source_kind") == SOURCE_DIRECTORY)
    except PluginRuntimeError:
        directory = 0
    return {"state": "ESTOP" if latched else "READY",
            "code": ESTOP_CODE if latched else "",
            "schema": SCHEMA, "kinds": list(PLUGIN_KINDS),
            "plugins": count, "builtin": sorted(_BUILTIN),
            "directory_plugins": directory,
            "loaded_plugins": sorted(_LOADED),
            "handlers": sorted(set(_HANDLERS) | set(_HOST_HANDLERS)
                               | set(_LOADED)),
            "host_capabilities": _host_handler_ids(),
            "handler_kinds": _handler_kinds(cfg),
            "in_process": "AVAILABLE",
            "external_runtime": "UNAVAILABLE",
            "external_code": UNAVAILABLE_CODE,
            "detail": "in-process host handlers and loaded local directory "
                      "plugins are real for every kind (tool/channel/provider/"
                      "hook); a bare manifest entrypoint is recorded, never "
                      "imported or executed",
            "estop": latched}


def list_plugins(cfg: Any) -> Dict[str, Any]:
    """List registered plugins (built-ins first), redacted.

    Also carries the installable ``catalog`` rows and the canonical
    ``plugin.install`` capability so the dashboard can render INSTALL controls
    over the same registry.
    """
    if estop_engaged(cfg):
        return refuse_estop("plugin list")
    rows = [dict(v) for v in _BUILTIN.values()]
    rows.extend(_load(cfg)["plugins"].values())
    rows.sort(key=lambda r: r.get("id", ""))
    return {"state": "OK", "schema": SCHEMA,
            "install_capability": INSTALL_CAPABILITY,
            "plugins": [_public(r) for r in rows],
            "catalog": plugin_catalog(cfg).get("plugins", [])}


def get_plugin(cfg: Any, plugin_id: str) -> Dict[str, Any]:
    """Show one plugin (redacted)."""
    rec = _find(cfg, plugin_id)
    if rec is None:
        raise PluginRuntimeError(NOT_FOUND_CODE, f"no plugin {plugin_id!r}")
    return {"state": "OK", "plugin": _public(rec)}


def host_capabilities(cfg: Any) -> Dict[str, Any]:
    """Read-only host-owned adapter capabilities invocable via :func:`invoke`.

    These are Qubit's own adapters over an existing canonical backend (e.g. the
    vendored achievements dashboard plugin); they are not installable manifests
    and never execute imported code. Listed separately from the registered
    plugin catalog so a caller can discover the real in-process capabilities.
    """
    rows: List[Dict[str, Any]] = []
    for pid in _host_handler_ids():
        rec = _HOST_MANIFESTS[pid]
        rows.append({
            "id": pid, "kind": rec.get("kind", ""),
            "label": _redact(rec.get("label", ""))[:64],
            "description": _redact(rec.get("description", ""))[:240],
            "capabilities": list(rec.get("capabilities", [])),
            "actions": list(rec.get("actions", [])),
            "invocation": f"plugin_runtime.invoke:{pid}",
            "handler": True,
        })
    return {"state": "OK", "schema": "qubit-plugin-host-capabilities/1",
            "capabilities": rows,
            "detail": "host in-process adapters over an existing canonical "
                      "backend; e-stop + shared confirmation + audit apply",
            "estop": estop_engaged(cfg)}


# ===========================================================================
# Real directory-plugin lifecycle (source port of the vendored plugin system)
#
# The vendored loader (``qubit_runtime/plugins/plugin_loader.py``) imports a
# plugin directory's ``__init__.py`` and calls ``register(ctx)``. This section
# ports that same loading/invocation behavior into the ONE Qubit registry:
# ``install`` copies a local reviewed plugin into the QUBIT_HOME-owned plugin
# store and loads it, ``enable``/``disable``/``reload``/``uninstall`` own the
# lifecycle, dependency/compat checks fail truthfully, and a plugin's tools are
# invoked through the existing e-stop + shared-journal + audit gate in
# :func:`invoke`. The plugin code itself is never rewritten; only the context
# it registers into is Qubit-owned.
# ===========================================================================
_LOADED: Dict[str, Dict[str, Any]] = {}


def _prepare_plugin_runtime(cfg: Any) -> None:
    """Put the vendored fork on ``sys.path`` and point it at QUBIT_HOME."""
    from . import qubit_plugins as _QP

    _QP._prepare_runtime(cfg)


def _qubit_home(cfg: Any) -> Path:
    from . import qubit_plugins as _QP

    return _QP._qubit_home(cfg)


def _plugin_home(cfg: Any) -> Path:
    return _qubit_home(cfg) / PLUGIN_HOME_DIR


def _plugin_dir(cfg: Any, plugin_id: str) -> Path:
    return _plugin_home(cfg) / plugin_id


def _module_slug(plugin_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", plugin_id)


def _evict_module(name: str) -> None:
    prefix = f"{name}."
    for mod in [m for m in list(sys.modules) if m == name or m.startswith(prefix)]:
        sys.modules.pop(mod, None)


def _normalize_plugin_id(raw: str) -> str:
    candidate = re.sub(r"[^a-z0-9_.-]+", "-", str(raw or "").strip().lower())
    candidate = candidate.strip(".-")
    if not candidate or not re.match(r"^[a-z0-9]", candidate):
        raise PluginRuntimeError(SOURCE_INVALID_CODE, "plugin has no usable id")
    return candidate[:64]


def _iter_source_files(root: Path) -> List[Path]:
    """Every regular file under *root*, rejecting escapes and unbounded trees."""
    root = root.resolve()
    files: List[Path] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if len(rel.parts) > MAX_PLUGIN_DEPTH:
            raise PluginRuntimeError(SOURCE_INVALID_CODE,
                                     "plugin source is nested too deeply")
        if path.is_symlink():
            target = path.resolve()
            if target != root and root not in target.parents:
                raise PluginRuntimeError(
                    SOURCE_INVALID_CODE,
                    "plugin source has a symlink that escapes the source dir")
        if path.is_file():
            if "__pycache__" in rel.parts:
                continue
            files.append(path)
            if len(files) > MAX_PLUGIN_FILES:
                raise PluginRuntimeError(
                    SOURCE_INVALID_CODE,
                    f"plugin source has more than {MAX_PLUGIN_FILES} files")
    return files


def _tree_digest(root: Path) -> str:
    """Deterministic sha256 over file paths + contents (provenance digest)."""
    digest = hashlib.sha256()
    base = root.resolve()
    for path in _iter_source_files(root):
        digest.update(path.relative_to(base).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _copy_plugin_tree(cfg: Any, root: Path, dest: Path) -> None:
    total = 0
    base = root.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    for path in _iter_source_files(root):
        data = path.read_bytes()
        total += len(data)
        if total > MAX_PLUGIN_SOURCE_BYTES:
            raise PluginRuntimeError(
                TOO_LARGE_CODE,
                f"plugin source exceeds {MAX_PLUGIN_SOURCE_BYTES} bytes")
        target = dest / path.relative_to(base)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        os.chmod(target, 0o600)
    os.chmod(dest, 0o700)


def _remove_install_dir(cfg: Any, rec: Dict[str, Any]) -> str:
    """Delete a directory plugin's code, bounded to the QUBIT_HOME store."""
    if rec.get("source_kind") != SOURCE_DIRECTORY:
        return ""
    dest = Path(str(rec.get("install_dir", ""))).expanduser()
    try:
        home = _plugin_home(cfg).resolve()
        target = dest.resolve()
    except OSError:
        return ""
    if target == home or home not in target.parents:
        return ""
    shutil.rmtree(target, ignore_errors=True)
    return str(target)


def _read_source_manifest(root: Path) -> Dict[str, Any]:
    """Parse ``plugin.yaml``/``plugin.yml``/``plugin.json`` from a source dir.

    Reuses the vendored ``parse_manifest_file`` (read-only) so manifest v1/v2
    fields, kind auto-detection, and dependency declarations match the runtime
    one-for-one. Directory-only plugins (no manifest) load from ``__init__.py``.
    """
    yaml_file = next((root / n for n in PLUGIN_MANIFEST_FILES
                      if n.endswith((".yaml", ".yml"))
                      and (root / n).is_file()), None)
    if yaml_file is not None:
        try:
            from qubit_cli.plugins_manifest import parse_manifest_file
        except Exception as exc:  # pragma: no cover - fork unavailable
            raise PluginRuntimeError(
                SOURCE_INVALID_CODE,
                "plugin manifest parser unavailable: "
                + _redact(str(exc))[:160]) from exc
        try:
            parsed = parse_manifest_file(yaml_file, root, "user", "")
        except Exception as exc:
            raise PluginRuntimeError(
                SOURCE_INVALID_CODE,
                "plugin manifest could not be parsed: "
                + _redact(str(exc))[:160]) from exc
        if parsed is None:
            raise PluginRuntimeError(SOURCE_INVALID_CODE,
                                     "plugin manifest could not be parsed")
        return {"name": parsed.name, "version": parsed.version,
                "description": parsed.description, "kind": parsed.kind,
                "provides_tools": list(parsed.provides_tools),
                "provides_hooks": list(parsed.provides_hooks),
                "python_dependencies": list(parsed.python_dependencies),
                "requires_plugins": list(parsed.requires_plugins),
                "config_schema": dict(parsed.config_schema),
                "api_version": parsed.api_version,
                "manifest_version": parsed.manifest_version}
    json_file = root / "plugin.json"
    if json_file.is_file():
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PluginRuntimeError(
                SOURCE_INVALID_CODE,
                "plugin.json unreadable: " + _redact(str(exc))[:160]) from exc
        if not isinstance(data, dict):
            raise PluginRuntimeError(SOURCE_INVALID_CODE,
                                     "plugin.json must be an object")
        raw_mv = data.get("manifest_version", 1)
        try:
            manifest_version = int(raw_mv)
        except (TypeError, ValueError):
            manifest_version = 1
        return {"name": str(data.get("name") or root.name),
                "version": str(data.get("version") or ""),
                "description": _bounded(data.get("description", ""), 240),
                "kind": str(data.get("kind") or "standalone"),
                "provides_tools": list(data.get("provides_tools") or []),
                "provides_hooks": list(data.get("provides_hooks") or []),
                "python_dependencies": list(data.get("python_dependencies") or []),
                "requires_plugins": list(data.get("requires_plugins") or []),
                "config_schema": dict(data.get("config_schema") or {}),
                "api_version": data.get("api_version"),
                "manifest_version": manifest_version}
    if (root / "__init__.py").is_file():
        return {"name": root.name, "version": "", "description": "",
                "kind": "standalone", "provides_tools": [],
                "provides_hooks": [], "python_dependencies": [],
                "requires_plugins": [], "config_schema": {},
                "api_version": None, "manifest_version": 1}
    raise PluginRuntimeError(
        SOURCE_INVALID_CODE,
        "plugin source has no plugin.yaml, plugin.json, or __init__.py")


def check_plugin_requirements(cfg: Any, manifest: Any,
                              *, enabled_ids: Any = ()) -> Dict[str, Any]:
    """Dependency/compat verdict for a plugin manifest (never executes it).

    Mirrors the vendored checks: declared ``python_dependencies`` are probed
    with importlib.metadata and NEVER auto-installed; ``manifest_version`` and
    ``api_version`` beyond what Qubit supports are incompatible; advisory
    ``requires_plugins`` entries that are not enabled are reported (not
    hard-failed, matching the vendored advisory contract).
    """
    data = manifest if isinstance(manifest, dict) else {}
    missing_dependencies: List[str] = []
    missing_plugins: List[str] = []
    incompatible: List[str] = []
    warnings: List[str] = []
    try:
        _prepare_plugin_runtime(cfg)
    except Exception:  # pragma: no cover - fork unavailable
        pass
    dist_installed = None
    try:
        from qubit_cli.plugins_loader import _dist_installed as dist_installed
    except Exception:  # pragma: no cover - fork unavailable
        dist_installed = None
    if dist_installed is not None:
        for req in data.get("python_dependencies") or []:
            try:
                if dist_installed(str(req)) is False:
                    missing_dependencies.append(str(req))
            except Exception:
                pass
    try:
        manifest_version = int(data.get("manifest_version") or 1)
    except (TypeError, ValueError):
        manifest_version = 1
    if manifest_version > PLUGIN_MANIFEST_VERSION:
        incompatible.append(
            f"manifest_version {manifest_version} > supported "
            f"{PLUGIN_MANIFEST_VERSION}")
    api_version = data.get("api_version")
    if api_version is not None:
        try:
            if int(api_version) > PLUGIN_API_VERSION:
                incompatible.append(
                    f"api_version {api_version} > supported {PLUGIN_API_VERSION}")
        except (TypeError, ValueError):
            warnings.append(f"api_version {api_version!r} is not an integer")
    installed = set(enabled_ids or ())
    for dep in data.get("requires_plugins") or []:
        dep_id = dep.get("id") if isinstance(dep, dict) else str(dep or "")
        dep_id = str(dep_id or "").strip()
        if dep_id and dep_id not in installed:
            missing_plugins.append(dep_id)
    hint = ("pip install " + " ".join(f"'{m}'" for m in missing_dependencies)) \
        if missing_dependencies else ""
    return {"ok": not missing_dependencies and not incompatible,
            "missing_dependencies": missing_dependencies,
            "missing_plugins": missing_plugins,
            "incompatible": incompatible,
            "warnings": warnings,
            "install_hint": hint}


def _classify_kind(manifest: Dict[str, Any]) -> str:
    """Map a manifest to one of the registry's ``PLUGIN_KINDS``.

    The vendored manifest kinds (standalone/backend/exclusive/platform/
    model-provider) are a different axis; the registry kind describes what the
    plugin actually provides so the dashboard can render it truthfully.
    """
    if manifest.get("provides_tools"):
        return "tool"
    if manifest.get("provides_hooks"):
        return "hook"
    if str(manifest.get("kind", "")) in ("exclusive", "model-provider"):
        return "provider"
    if str(manifest.get("kind", "")) == "platform":
        return "channel"
    return "tool"


def _bounded_list(values: Any, limit: int = 64) -> List[str]:
    out: List[str] = []
    for value in values or []:
        clean = _bounded(value, 64)
        if clean and clean not in out:
            out.append(clean)
    return sorted(out[:limit])


class _ManifestView:
    """Read-only manifest facade handed to plugin code as ``ctx.manifest``."""

    def __init__(self, rec: Dict[str, Any]) -> None:
        self.name = rec.get("label") or rec.get("id", "")
        self.key = rec.get("id", "")
        self.version = rec.get("version", "")
        self.description = rec.get("description", "")
        self.source = "user"
        self.path = rec.get("install_dir", "")
        self.skill_namespace = rec.get("id", "")
        self.kind = rec.get("plugin_class", "standalone")
        self.provides_tools = list((rec.get("provides") or {}).get("tools", []))
        self.provides_hooks: List[str] = []
        self.requires_env: List[Any] = []

    @property
    def plugin_id(self) -> str:
        return self.key or self.name


class _DictState:
    """Minimal durable per-plugin state (``ctx.state``) under the install dir."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: Dict[str, Any] = {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = raw
        except (OSError, ValueError):
            self._data = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[str(key)] = value
        self.save()

    def delete(self, key: str) -> bool:
        existed = str(key) in self._data
        self._data.pop(str(key), None)
        self.save()
        return existed

    def all(self) -> Dict[str, Any]:
        return dict(self._data)

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + f".tmp.{os.getpid()}")
            tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True),
                           encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except OSError:
            pass


class _QubitPluginContext:
    """Host-owned plugin registration surface (the ported ``PluginContext``).

    Mirrors the vendored registrar names so an unmodified plugin directory
    loads into the ONE Qubit registry. Registrations are captured in-process
    and persisted as metadata; the tool handlers are executed only through
    :func:`invoke`, which applies e-stop + shared journal + audit. Unknown
    registrars are recorded as ``unsupported`` (truthful) instead of silently
    pretending to wire a capability Qubit does not own.
    """

    def __init__(self, cfg: Any, plugin_id: str,
                 record: Dict[str, Any]) -> None:
        self._cfg = cfg
        self.plugin_id = plugin_id
        self.manifest = _ManifestView(record)
        self.tools: Dict[str, Dict[str, Any]] = {}
        self.hooks: Dict[str, List[Any]] = {}
        self.skills: Dict[str, Dict[str, Any]] = {}
        self.commands: Dict[str, Dict[str, Any]] = {}
        self.cli_commands: Dict[str, Dict[str, Any]] = {}
        self.memory_providers: List[Dict[str, Any]] = []
        self.context_engines: List[Dict[str, Any]] = []
        self.unsupported: List[Dict[str, Any]] = []
        self.warnings: List[str] = []
        self.config: Dict[str, Any] = dict(record.get("config") or {})
        install_dir = str(record.get("install_dir", "") or "")
        self.state = _DictState(
            Path(install_dir) / "state.json") if install_dir else _DictState(
            Path(os.devnull))

    # -- registrar surface -------------------------------------------------
    def register_tool(self, name: str, toolset: str = "", schema: Any = None,
                      handler: Any = None, check_fn: Any = None,
                      requires_env: Any = None, is_async: bool = False,
                      description: str = "", emoji: str = "",
                      override: bool = False) -> Dict[str, Any]:
        clean = str(name or "").strip()
        if not clean or not callable(handler):
            raise ValueError("register_tool requires a name and a handler")
        if override:
            self.warnings.append(
                f"tool {clean}: override requested; Qubit never overrides "
                "host tools, the plugin tool is registered under its own name")
        self.tools[clean] = {
            "name": clean, "toolset": str(toolset or ""),
            "schema": dict(schema) if isinstance(schema, dict) else {},
            "handler": handler,
            "check_fn": check_fn if callable(check_fn) else None,
            "is_async": bool(is_async),
            "description": _bounded(description, 240),
            "emoji": str(emoji or "")[:16], "override": False}
        return {"kind": "tool", "key": clean}

    def register_hook(self, hook_name: str, callback: Any) -> Dict[str, Any]:
        if not callable(callback):
            raise ValueError("register_hook requires a callable")
        self.hooks.setdefault(str(hook_name), []).append(callback)
        return {"kind": "hook", "key": str(hook_name)}

    def register_skill(self, name: str, path: Any, description: str = "",
                       frontmatter: Any = None) -> Dict[str, Any]:
        clean = str(name or "").strip()
        if not clean:
            raise ValueError("register_skill requires a name")
        skill_path = Path(path)
        if not skill_path.is_file():
            raise FileNotFoundError(f"SKILL.md not found at {skill_path}")
        self.skills[clean] = {"name": clean, "path": str(skill_path),
                              "description": _bounded(description, 240),
                              "frontmatter": dict(frontmatter or {})}
        return {"kind": "skill", "key": clean}

    def register_command(self, name: str, handler: Any, description: str = "",
                         args_hint: str = "",
                         argument_mode: Any = None) -> Dict[str, Any]:
        clean = str(name or "").strip().lstrip("/")
        if not clean or not callable(handler):
            raise ValueError("register_command requires a name and a handler")
        self.commands[clean] = {"handler": handler,
                                "description": _bounded(description, 240),
                                "args_hint": str(args_hint or "")}
        return {"kind": "command", "key": clean}

    def register_cli_command(self, name: str, help: str = "",  # noqa: A002
                             setup_fn: Any = None, handler_fn: Any = None,
                             description: str = "") -> Dict[str, Any]:
        clean = str(name or "").strip()
        if not clean:
            raise ValueError("register_cli_command requires a name")
        self.cli_commands[clean] = {"name": clean,
                                    "help": _bounded(help, 120),
                                    "setup_fn": setup_fn,
                                    "handler_fn": handler_fn,
                                    "description": _bounded(description, 240)}
        return {"kind": "cli_command", "key": clean}

    def register_memory_provider(self, provider: Any) -> Dict[str, Any]:
        name = str(getattr(provider, "name", "") or type(provider).__name__)
        self.memory_providers.append({"name": name, "provider": provider})
        return {"kind": "memory_provider", "key": name}

    def register_context_engine(self, engine: Any) -> Dict[str, Any]:
        name = str(getattr(engine, "name", "") or type(engine).__name__)
        self.context_engines.append({"name": name, "engine": engine})
        return {"kind": "context_engine", "key": name}

    # -- read-only / host-owned surface ------------------------------------
    def has_capability(self, capability: str) -> bool:
        return False  # registration is not a grant; fail closed

    def has_plugin(self, plugin_id: str) -> bool:
        clean = str(plugin_id or "").strip()
        try:
            state = _load(self._cfg)["plugins"]
        except PluginRuntimeError:
            return False
        return any(pid == clean or rec.get("label") == clean
                   for pid, rec in state.items() if rec.get("enabled", True))

    def get_config(self, key: str, default: Any = None) -> Any:
        node: Any = self.config
        for part in str(key or "").split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return default
        return node

    def set_config(self, key: str, value: Any) -> None:
        raise PermissionError(
            "plugin settings are host-owned; write them through the Qubit "
            "config surface")

    def dispatch_tool(self, tool_name: str, args: Any, **kwargs: Any) -> Any:
        tool = self.tools.get(str(tool_name))
        if tool is None:
            raise KeyError(f"unknown plugin tool {tool_name!r}")
        return _call_tool(tool, dict(args or {}))

    def on_unload(self, callback: Any) -> Dict[str, Any]:
        if not callable(callback):
            raise TypeError("on_unload callback must be callable")
        return {"kind": "on_unload"}

    def emit(self, event: str, payload: Any = None) -> int:
        self.unsupported.append({"method": "emit", "args": _bounded(event, 120)})
        return 0

    def subscribe(self, event: str, callback: Any) -> None:
        self.unsupported.append(
            {"method": "subscribe", "args": _bounded(event, 120)})

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        def _record(*args: Any, **kwargs: Any) -> None:
            self.unsupported.append(
                {"method": name, "args": _bounded(_redact(repr(args)), 200)})
            return None

        return _record


def _call_tool(tool: Dict[str, Any], args: Dict[str, Any]) -> Any:
    fn = tool.get("handler")
    if not callable(fn):
        raise PluginRuntimeError(HANDLER_FAILED_CODE,
                                 "plugin tool has no callable handler")
    if tool.get("is_async"):
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(fn(args))
        raise PluginRuntimeError(
            HANDLER_FAILED_CODE,
            "async plugin tool cannot run inside a running event loop")
    return fn(args)


def _capture_provides(ctx: _QubitPluginContext) -> Dict[str, Any]:
    return {"tools": sorted(ctx.tools),
            "hooks": sorted(ctx.hooks),
            "skills": sorted(ctx.skills),
            "commands": sorted(ctx.commands),
            "cli_commands": sorted(ctx.cli_commands),
            "memory_providers": [p["name"] for p in ctx.memory_providers],
            "context_engines": [e["name"] for e in ctx.context_engines],
            "unsupported": sorted({u["method"] for u in ctx.unsupported})}


def _provides_any(provides: Dict[str, Any]) -> bool:
    return any(provides.get(key) for key in
               ("tools", "hooks", "skills", "commands", "cli_commands",
                "memory_providers", "context_engines", "dashboard"))


def _read_dashboard_manifest(install_dir: Path) -> Optional[Dict[str, Any]]:
    """Read a plugin's ``dashboard/manifest.json`` (real dashboard-tab row)."""
    manifest = install_dir / "dashboard" / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        if manifest.stat().st_size > MAX_CATALOG_BYTES:
            return None
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        from .qubit_plugins import _rebrand_product, _rebrand_tab
    except Exception:  # pragma: no cover
        _rebrand_product = _rebrand_tab = lambda value: value  # noqa: E731
    return {"name": str(data.get("name") or install_dir.name),
            "label": _rebrand_product(data.get("label", "")),
            "description": _rebrand_product(data.get("description", "")),
            "icon": str(data.get("icon") or "Puzzle"),
            "version": str(data.get("version") or ""),
            "tab": _rebrand_tab(data.get("tab", {})),
            "entry": str(data.get("entry") or "dist/index.js"),
            "has_api": bool(data.get("api")),
            "manifest": str(manifest)}


def _load_plugin_record(cfg: Any, rec: Dict[str, Any]) -> Dict[str, Any]:
    """Import a plugin directory and call its ``register(ctx)`` (real load)."""
    pid = rec["id"]
    install_dir = Path(str(rec.get("install_dir", ""))).expanduser()
    if not install_dir.is_dir():
        raise PluginRuntimeError(
            LOAD_FAILED_CODE, f"install dir is missing for plugin {pid!r}")
    _prepare_plugin_runtime(cfg)
    try:
        from plugins.plugin_loader import load_plugin_module
    except Exception as exc:
        raise PluginRuntimeError(
            LOAD_FAILED_CODE,
            "vendored plugin loader unavailable: "
            + _redact(str(exc))[:160]) from exc
    module_name = f"qubit_plugins.{_module_slug(pid)}"
    _evict_module(module_name)
    ctx = _QubitPluginContext(cfg, pid, rec)
    logger = logging.getLogger("voicebridge.plugin_runtime")
    try:
        module = load_plugin_module(module_name, install_dir,
                                    parents=("qubit_plugins",), logger=logger)
        if module is None:
            raise RuntimeError("loader returned no module")
        register = getattr(module, "register", None)
        if register is None:
            raise RuntimeError("plugin has no register(ctx) entrypoint")
        register(ctx)
    except Exception as exc:
        _evict_module(module_name)
        raise PluginRuntimeError(
            LOAD_FAILED_CODE,
            f"plugin {pid!r} failed to load: "
            + _redact(str(exc))[:240]) from exc
    loaded = {"module": module, "ctx": ctx, "tools": ctx.tools,
              "hooks": ctx.hooks, "skills": ctx.skills,
              "commands": ctx.commands, "cli_commands": ctx.cli_commands,
              "memory_providers": ctx.memory_providers,
              "context_engines": ctx.context_engines,
              "unsupported": ctx.unsupported}
    _LOADED[pid] = loaded
    return loaded


def _ensure_loaded(cfg: Any, rec: Dict[str, Any]) -> Dict[str, Any]:
    pid = rec["id"]
    if pid in _LOADED:
        return _LOADED[pid]
    return _load_plugin_record(cfg, rec)


def _apply_load_to_record(rec: Dict[str, Any],
                          loaded: Dict[str, Any]) -> None:
    ctx: _QubitPluginContext = loaded["ctx"]
    provides = _capture_provides(ctx)
    provides["dashboard"] = _read_dashboard_manifest(
        Path(str(rec.get("install_dir", ""))))
    rec["provides"] = provides
    rec["load_state"] = "LOADED" if _provides_any(provides) else "NO_SURFACE"
    rec["load_error"] = ""
    if ctx.warnings:
        rec["warnings"] = list(rec.get("warnings") or []) + list(ctx.warnings)


def _enabled_ids(state: Dict[str, Any]) -> set:
    return {pid for pid, rec in (state.get("plugins") or {}).items()
            if rec.get("enabled", True)}


def _install_from_directory(cfg: Any, source: str, *, confirmed: bool,
                            confirmation_id: str, requested_by: str
                            ) -> Dict[str, Any]:
    """Copy a local reviewed plugin into QUBIT_HOME storage and load it."""
    _authorize_mutation(cfg, INSTALL_CAPABILITY, confirmed=confirmed,
                        confirmation_id=confirmation_id,
                        requested_by=requested_by)
    _prepare_plugin_runtime(cfg)
    root = Path(source).expanduser()
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise PluginRuntimeError(
            SOURCE_INVALID_CODE,
            "plugin source not found: " + _redact(str(source))[:160]) from exc
    if not root.is_dir():
        raise PluginRuntimeError(SOURCE_INVALID_CODE,
                                 "plugin source must be a directory")
    home = _plugin_home(cfg).resolve()
    if root == home or home in root.parents:
        raise PluginRuntimeError(
            SOURCE_INVALID_CODE,
            "plugin source is already inside the Qubit plugin store")
    manifest = _read_source_manifest(root)
    pid = _normalize_plugin_id(manifest.get("name") or root.name)
    if pid in _BUILTIN or pid in _HOST_MANIFESTS:
        raise PluginRuntimeError(REFUSED_CODE,
                                 f"{pid!r} is a host-owned capability")
    state = _load(cfg)
    if pid in state["plugins"]:
        raise PluginRuntimeError(EXISTS_CODE, f"plugin {pid!r} exists")
    if len(state["plugins"]) >= MAX_PLUGINS:
        raise PluginRuntimeError(LIMIT_CODE,
                                 f"plugin limit ({MAX_PLUGINS}) reached")
    _check_branding(str(manifest.get("name") or pid),
                    str(manifest.get("description") or ""))
    req = check_plugin_requirements(cfg, manifest,
                                    enabled_ids=_enabled_ids(state))
    if req["incompatible"]:
        raise PluginRuntimeError(COMPAT_CODE, "; ".join(req["incompatible"]))
    digest = _tree_digest(root)
    dest = _plugin_dir(cfg, pid)
    if dest.exists():
        raise PluginRuntimeError(EXISTS_CODE,
                                 f"plugin directory {pid!r} already exists")
    try:
        _copy_plugin_tree(cfg, root, dest)
    except PluginRuntimeError:
        _remove_install_dir(cfg, {"source_kind": SOURCE_DIRECTORY,
                                 "install_dir": str(dest)})
        raise
    rec: Dict[str, Any] = {
        "id": pid, "kind": _classify_kind(manifest),
        "entrypoint": f"{_module_slug(pid)}:register",
        "digest": digest,
        "label": _bounded(manifest.get("name") or pid, 64),
        "description": _bounded(manifest.get("description", ""), 240),
        "provider": "", "version": _bounded(manifest.get("version", ""), 32),
        "capabilities": _bounded_list(manifest.get("provides_tools")),
        "registered_at": _now(), "updated_by": requested_by,
        "source_kind": SOURCE_DIRECTORY, "source": root.name,
        "install_dir": str(dest), "enabled": True, "setup_required": False,
        "python_dependencies": list(manifest.get("python_dependencies") or []),
        "requires_plugins": list(manifest.get("requires_plugins") or []),
        "manifest_version": int(manifest.get("manifest_version") or 1),
        "api_version": manifest.get("api_version"),
        "plugin_class": str(manifest.get("kind", "standalone")),
        "warnings": list(req.get("warnings") or []),
        "provides": {}, "load_state": "", "load_error": ""}
    if req["missing_dependencies"]:
        rec["enabled"] = False
        rec["setup_required"] = True
        rec["load_state"] = "SETUP_REQUIRED"
        rec["warnings"] = list(rec["warnings"]) + [
            f"missing Python dependency: {d}"
            for d in req["missing_dependencies"]]
    else:
        try:
            loaded = _load_plugin_record(cfg, rec)
        except PluginRuntimeError:
            _remove_install_dir(cfg, rec)
            raise
        _apply_load_to_record(rec, loaded)
    state["plugins"][pid] = rec
    _save(cfg, state)
    _audit("install", requested_by,
           {"id": pid, "source": root.name, "digest": digest[:12],
            "enabled": rec["enabled"],
            "provides": rec.get("provides", {}).get("tools", [])})
    out: Dict[str, Any] = {
        "state": "OK", "installed": True, "plugin": _public(rec),
        "source": root.name, "source_kind": SOURCE_DIRECTORY,
        "install_dir": str(dest),
        "requirements": {"missing_dependencies": req["missing_dependencies"],
                         "missing_plugins": req["missing_plugins"],
                         "warnings": req["warnings"]},
        "install_capability": INSTALL_CAPABILITY,
        "note": ("copied into QUBIT_HOME plugin storage and loaded "
                 "in-process" if rec["enabled"] else
                 "copied into QUBIT_HOME plugin storage; disabled until its "
                 "declared dependencies are installed")}
    if req["missing_plugins"]:
        out["warnings"] = [f"requires plugin {d!r} which is not enabled"
                           for d in req["missing_plugins"]]
    return out


def enable_plugin(cfg: Any, plugin_id: str, *, confirmed: bool = False,
                  confirmation_id: str = "",
                  requested_by: str = "cli:user") -> Dict[str, Any]:
    """Load an installed directory plugin and persist ``enabled=True``."""
    _authorize_mutation(cfg, "plugin.enable", confirmed=confirmed,
                        confirmation_id=confirmation_id,
                        requested_by=requested_by)
    clean = _check_id(plugin_id)
    state = _load(cfg)
    rec = state["plugins"].get(clean)
    if rec is None:
        if clean in _BUILTIN or clean in _HOST_MANIFESTS:
            raise PluginRuntimeError(REFUSED_CODE,
                                     f"{clean!r} is a host-owned capability")
        raise PluginRuntimeError(NOT_FOUND_CODE, f"no plugin {plugin_id!r}")
    if rec.get("source_kind") != SOURCE_DIRECTORY:
        raise PluginRuntimeError(
            REFUSED_CODE,
            f"plugin {clean!r} has no loadable source directory")
    req = check_plugin_requirements(cfg, rec,
                                    enabled_ids=_enabled_ids(state))
    if req["incompatible"]:
        raise PluginRuntimeError(COMPAT_CODE, "; ".join(req["incompatible"]))
    if req["missing_dependencies"]:
        raise PluginRuntimeError(
            DEPENDENCY_CODE,
            "missing declared Python dependencies: "
            + ", ".join(req["missing_dependencies"])
            + (f" ({req['install_hint']})" if req["install_hint"] else ""))
    rec["enabled"] = True
    rec["setup_required"] = False
    try:
        loaded = _load_plugin_record(cfg, rec)
    except PluginRuntimeError:
        rec["enabled"] = False
        state["plugins"][clean] = rec
        _save(cfg, state)
        raise
    _apply_load_to_record(rec, loaded)
    state["plugins"][clean] = rec
    _save(cfg, state)
    _audit("enable", requested_by, {"id": clean})
    return {"state": "OK", "plugin": _public(rec)}


def disable_plugin(cfg: Any, plugin_id: str, *, confirmed: bool = False,
                   confirmation_id: str = "",
                   requested_by: str = "cli:user") -> Dict[str, Any]:
    """Unload an installed directory plugin and persist ``enabled=False``."""
    _authorize_mutation(cfg, "plugin.disable", confirmed=confirmed,
                        confirmation_id=confirmation_id,
                        requested_by=requested_by)
    clean = _check_id(plugin_id)
    state = _load(cfg)
    rec = state["plugins"].get(clean)
    if rec is None:
        if clean in _BUILTIN or clean in _HOST_MANIFESTS:
            raise PluginRuntimeError(REFUSED_CODE,
                                     f"{clean!r} is a host-owned capability")
        raise PluginRuntimeError(NOT_FOUND_CODE, f"no plugin {plugin_id!r}")
    if rec.get("source_kind") != SOURCE_DIRECTORY:
        raise PluginRuntimeError(REFUSED_CODE,
                                 f"plugin {clean!r} is not a directory plugin")
    rec["enabled"] = False
    rec["load_state"] = "DISABLED"
    state["plugins"][clean] = rec
    _save(cfg, state)
    _LOADED.pop(clean, None)
    _evict_module(f"qubit_plugins.{_module_slug(clean)}")
    _audit("disable", requested_by, {"id": clean})
    return {"state": "OK", "plugin": _public(rec)}


def dashboard_tabs(cfg: Any) -> Dict[str, Any]:
    """Dashboard-tab rows contributed by enabled directory plugins (read-only)."""
    if estop_engaged(cfg):
        return refuse_estop("plugin dashboard tabs")
    tabs: List[Dict[str, Any]] = []
    for pid, rec in (_load(cfg).get("plugins") or {}).items():
        if rec.get("source_kind") != SOURCE_DIRECTORY \
                or not rec.get("enabled", True):
            continue
        tab = (rec.get("provides") or {}).get("dashboard")
        if tab:
            tabs.append({"plugin": pid, **tab})
    return {"state": "OK", "schema": "qubit-plugin-dashboard-tabs/1",
            "tabs": sorted(tabs, key=lambda t: str(t.get("plugin", "")))}


def register(cfg: Any, *, manifest: Any, confirmed: bool = False,
             confirmation_id: str = "",
             requested_by: str = "cli:user") -> Dict[str, Any]:
    """Record a validated plugin manifest (metadata only; no execution).

    Confirmation-gated through the shared durable journal, estop-aware,
    bounded, and redacted. Nothing is fetched, imported, or run: the entrypoint
    string is stored verbatim so the host can decide later whether to bind an
    in-process handler.
    """
    _authorize_mutation(cfg, "plugin.register", confirmed=confirmed,
                        confirmation_id=confirmation_id,
                        requested_by=requested_by)
    clean = _validate_manifest(manifest)
    state = _load(cfg)
    if clean["id"] in _BUILTIN or clean["id"] in _HOST_MANIFESTS:
        raise PluginRuntimeError(REFUSED_CODE,
                                 f"{clean['id']!r} is a host-owned capability")
    if clean["id"] in state["plugins"]:
        raise PluginRuntimeError(EXISTS_CODE, f"plugin {clean['id']!r} exists")
    if len(state["plugins"]) >= MAX_PLUGINS:
        raise PluginRuntimeError(LIMIT_CODE,
                                 f"plugin limit ({MAX_PLUGINS}) reached")
    rec = dict(clean)
    rec["registered_at"] = _now()
    rec["updated_by"] = requested_by
    state["plugins"][clean["id"]] = rec
    _save(cfg, state)
    _audit("register", requested_by,
           {"id": clean["id"], "kind": clean["kind"]})
    return {"state": "OK", "plugin": _public(rec),
            "note": "manifest recorded; no code was imported or executed"}


def bind_handler(cfg: Any, *, manifest: Any,
                 handler: Optional[Callable[[Dict[str, Any]], Any]] = None,
                 confirmed: bool = False, confirmation_id: str = "",
                 requested_by: str = "cli:user") -> Dict[str, Any]:
    """Bind a real in-process handler for any plugin kind (OC-40).

    This is the Qubit plugin *runtime*: after validation and a shared-journal
    confirmation a host-owned Python callable is bound in-process for
    ``tool``/``channel``/``provider``/``hook`` plugins. The manifest is still
    metadata only — no entrypoint is imported, no file is fetched, no process
    is spawned. The binding is process-local and is never persisted as code:
    after a restart the manifest is listed but the handler is absent, so
    ``invoke`` degrades to the typed ``PLUGIN_RUNTIME_UNAVAILABLE`` gate until
    the host rebinds it.
    """
    _authorize_mutation(cfg, "plugin.bind", confirmed=confirmed,
                        confirmation_id=confirmation_id,
                        requested_by=requested_by)
    if not callable(handler):
        raise PluginRuntimeError(
            HANDLER_REQUIRED_CODE,
            "handler must be a callable owned by the Qubit host")
    clean = _validate_manifest(manifest)
    state = _load(cfg)
    if clean["id"] in _BUILTIN or clean["id"] in _HOST_MANIFESTS:
        raise PluginRuntimeError(REFUSED_CODE,
                                 f"{clean['id']!r} is a host-owned capability")
    if clean["id"] in state["plugins"]:
        raise PluginRuntimeError(EXISTS_CODE, f"plugin {clean['id']!r} exists")
    if len(state["plugins"]) >= MAX_PLUGINS:
        raise PluginRuntimeError(LIMIT_CODE,
                                 f"plugin limit ({MAX_PLUGINS}) reached")
    rec = dict(clean)
    rec["registered_at"] = _now()
    rec["updated_by"] = requested_by
    rec["handler_bound"] = True
    state["plugins"][clean["id"]] = rec
    _save(cfg, state)
    _HANDLERS[clean["id"]] = handler
    _audit("bind", requested_by,
           {"id": clean["id"], "kind": clean["kind"]})
    return {"state": "OK", "plugin": _public(rec),
            "note": "in-process handler bound by the Qubit host; nothing "
                    "imported, fetched, or spawned"}


def unregister(cfg: Any, plugin_id: str, *, confirmed: bool = False,
               confirmation_id: str = "",
               requested_by: str = "cli:user") -> Dict[str, Any]:
    """Remove a registered plugin (built-ins cannot be removed)."""
    _authorize_mutation(cfg, "plugin.unregister", confirmed=confirmed,
                        confirmation_id=confirmation_id,
                        requested_by=requested_by)
    clean = _check_id(plugin_id)
    if clean in _BUILTIN or clean in _HOST_MANIFESTS:
        raise PluginRuntimeError(REFUSED_CODE,
                                 f"{clean!r} is a host-owned capability")
    state = _load(cfg)
    rec = state["plugins"].get(clean)
    if rec is None:
        raise PluginRuntimeError(NOT_FOUND_CODE, f"no plugin {plugin_id!r}")
    del state["plugins"][clean]
    _save(cfg, state)
    _HANDLERS.pop(clean, None)
    _LOADED.pop(clean, None)
    _evict_module(f"qubit_plugins.{_module_slug(clean)}")
    removed_dir = _remove_install_dir(cfg, rec)
    _audit("unregister", requested_by, {"id": clean})
    out: Dict[str, Any] = {"state": "OK", "removed": clean}
    if removed_dir:
        out["removed_dir"] = removed_dir
    return out


def install(cfg: Any, *, manifest: Any = None, identifier: str = "",
            source: str = "",
            handler: Optional[Callable[[Dict[str, Any]], Any]] = None,
            confirmed: bool = False, confirmation_id: str = "",
            requested_by: str = "cli:user") -> Dict[str, Any]:
    """Canonical plugin install: reviewed local source into the registry.

    The dashboard INSTALL control / CLI / voice resolves here. Four local
    sources are accepted and all end at the same canonical operation, so
    e-stop and the shared confirmation journal apply unchanged:

    * a local plugin **directory** (``source=`` or a directory ``identifier=``)
      — copied into the QUBIT_HOME-owned plugin store and loaded by calling its
      ``register(ctx)`` (real code execution; the vendored loader behavior);
    * an explicit manifest object/JSON string (``manifest=``) — metadata only;
    * a reviewed catalog identifier in ``<state_dir>/plugin-catalog/``;
    * a local reviewed ``*.json`` manifest path (``identifier=``).

    A remote identifier is a typed refusal from :mod:`voicebridge.plugins` —
    nothing is fetched, cloned, or executed.
    """
    if estop_engaged(cfg):
        raise PluginRuntimeError(ESTOP_CODE, "emergency stop is latched")
    src = str(source or "").strip()
    if src:
        return _install_from_directory(cfg, src, confirmed=confirmed,
                                       confirmation_id=confirmation_id,
                                       requested_by=requested_by)
    resolved: Any = None
    source_kind = "manifest"
    if manifest is not None:
        resolved = manifest
    else:
        ident = str(identifier or "").strip()
        if not ident:
            raise PluginRuntimeError(
                BAD_INPUT_CODE,
                "install needs a manifest object, a catalog identifier, a "
                "local manifest path, or a plugin source directory")
        candidate = Path(ident).expanduser()
        if candidate.is_dir():
            return _install_from_directory(
                cfg, ident, confirmed=confirmed,
                confirmation_id=confirmation_id, requested_by=requested_by)
        if candidate.suffix == ".json" and candidate.is_file():
            try:
                if candidate.stat().st_size > MAX_MANIFEST_BYTES:
                    raise PluginRuntimeError(
                        TOO_LARGE_CODE,
                        f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
                resolved = json.loads(candidate.read_text(encoding="utf-8"))
            except PluginRuntimeError:
                raise
            except (OSError, ValueError) as exc:
                raise PluginRuntimeError(
                    INVALID_MANIFEST_CODE,
                    f"manifest unreadable: {exc}") from exc
            source_kind = "path"
        else:
            reviewed = _catalog_source(cfg)
            if ident in reviewed:
                resolved = reviewed[ident]
                source_kind = "catalog"
            else:
                from . import plugins as _PLUG

                verdict = _PLUG.classify_install_source(ident)
                if verdict.get("kind") == "remote":
                    return _PLUG.refuse_remote(
                        str(verdict.get("identifier", ident)))
                raise PluginRuntimeError(
                    INSTALL_SOURCE_CODE,
                    f"no reviewed local plugin manifest for {ident!r}")
    clean = _validate_manifest(resolved)
    if clean["id"] in _BUILTIN:
        raise PluginRuntimeError(REFUSED_CODE,
                                 f"{clean['id']!r} is a built-in plugin")
    if clean["id"] in _load(cfg)["plugins"]:
        raise PluginRuntimeError(EXISTS_CODE,
                                 f"plugin {clean['id']!r} is already installed")
    if handler is not None:
        out = bind_handler(cfg, manifest=clean, handler=handler,
                           confirmed=confirmed, confirmation_id=confirmation_id,
                           requested_by=requested_by)
    else:
        out = register(cfg, manifest=clean, confirmed=confirmed,
                       confirmation_id=confirmation_id,
                       requested_by=requested_by)
    out["installed"] = True
    out["source"] = source_kind
    out["install_capability"] = INSTALL_CAPABILITY
    return out


def invoke(cfg: Any, plugin_id: str, *, args: Optional[Dict[str, Any]] = None,
           tool: str = "",
           confirmed: bool = False, confirmation_id: str = "",
           requested_by: str = "cli:user") -> Dict[str, Any]:
    """Invoke an in-process handler, or gate with a typed UNAVAILABLE.

    Order: emergency stop -> shared-journal confirmation -> plugin exists ->
    an in-process handler is available. A manifest entrypoint alone never
    executes; absent a handler the result is the typed
    ``PLUGIN_RUNTIME_UNAVAILABLE`` gate and nothing runs. Directory plugins
    installed from a local source are (re)loaded here and one of their
    registered tool handlers runs through the same gate.
    """
    _authorize_mutation(cfg, "plugin.invoke", confirmed=confirmed,
                        confirmation_id=confirmation_id,
                        requested_by=requested_by)
    clean = _check_id(plugin_id)
    rec = _find(cfg, clean)
    if rec is None:
        raise PluginRuntimeError(NOT_FOUND_CODE, f"no plugin {plugin_id!r}")
    payload = {} if args is None else args
    if not isinstance(payload, dict):
        raise PluginRuntimeError(BAD_ARGS_CODE, "args must be an object")
    payload = dict(payload)
    host = _HOST_HANDLERS.get(clean)
    handler = _HANDLERS.get(clean)
    tool_used = ""
    if host is None and handler is None:
        if rec.get("source_kind") != SOURCE_DIRECTORY:
            raise PluginRuntimeError(
                UNAVAILABLE_CODE,
                f"plugin {clean!r} has no in-process handler: provider-backed "
                "execution is unavailable and manifest entrypoints are never "
                "executed")
        if not rec.get("enabled", True):
            raise PluginRuntimeError(
                DISABLED_CODE,
                f"plugin {clean!r} is disabled; enable it before invoking")
        loaded = _ensure_loaded(cfg, rec)
        tools = loaded.get("tools") or {}
        if not tools:
            raise PluginRuntimeError(
                NO_SURFACE_CODE,
                f"plugin {clean!r} registers no invocable tool")
        selected = str(tool or payload.pop("tool", "") or "").strip()
        if not selected:
            if len(tools) == 1:
                selected = next(iter(tools))
            else:
                raise PluginRuntimeError(
                    TOOL_REQUIRED_CODE,
                    "plugin registers multiple tools; pass tool=<name>: "
                    + ", ".join(sorted(tools)))
        entry = tools.get(selected)
        if entry is None:
            raise PluginRuntimeError(
                NOT_FOUND_CODE,
                f"plugin {clean!r} has no tool {selected!r}")
        tool_used = selected
        handler = (lambda p, _entry=entry: _call_tool(_entry, p))
    try:
        blob = json.dumps(payload, default=repr)
    except (TypeError, ValueError):
        raise PluginRuntimeError(BAD_ARGS_CODE,
                                 "args are not serializable") from None
    if len(blob.encode("utf-8")) > MAX_ARGS_BYTES:
        raise PluginRuntimeError(BAD_ARGS_CODE,
                                 f"args exceed {MAX_ARGS_BYTES} bytes")
    try:
        result = host(cfg, dict(payload)) if host is not None \
            else handler(dict(payload))
    except PluginRuntimeError:
        raise
    except Exception as exc:
        raise PluginRuntimeError(
            HANDLER_FAILED_CODE, f"handler failed: {_redact(str(exc))[:200]}")
    safe = _redact_obj(result)
    if isinstance(safe, str):
        safe = safe[:MAX_RESULT_CHARS]
    _audit("invoke", requested_by,
           {"id": clean, "tool": tool_used,
            "args_sha256": hashlib.sha256(blob.encode()).hexdigest()})
    out: Dict[str, Any] = {"state": "OK", "plugin": clean, "result": safe,
                           "note": "in-process handler only; nothing "
                                   "external executed"}
    if tool_used:
        out["tool"] = tool_used
    return out


def doctor(cfg: Any) -> Dict[str, Any]:
    """Diagnostics: store health, kind coverage, handlers, and the latch."""
    warnings: List[str] = []
    store_ok = True
    try:
        state = _load(cfg)
        plugins = state["plugins"]
    except PluginRuntimeError as exc:
        store_ok = False
        plugins = {}
        warnings.append(f"{exc.code}: {exc.detail[:200]}")
    kinds = {r.get("kind", "") for r in plugins.values()}
    return {"state": "OK" if store_ok else "DEGRADED",
            "schema": SCHEMA,
            "store_ok": store_ok,
            "plugins": len(plugins) + len(_BUILTIN),
            "kinds_present": sorted(k for k in kinds if k),
            "kinds_known": list(PLUGIN_KINDS),
            "handlers": sorted(set(_HANDLERS) | set(_HOST_HANDLERS)),
            "host_capabilities": _host_handler_ids(),
            "handler_kinds": _handler_kinds(cfg),
            "estop": estop_engaged(cfg),
            "external_runtime": UNAVAILABLE_CODE,
            "warnings": warnings}


# ===========================================================================
# Toolset enable/disable — persisted state (dashboard sweep)
#
# The SPA toolset toggle used to grant a permission but never persist
# ``enabled``, so the read path always reported True. The canonical persisted
# store lives in :mod:`voicebridge.toolsets`; these re-exports expose it on the
# plugin-runtime public API the route layer already imports. All toolset
# mutations stage/consume on the SAME durable ApprovalJournal as every other
# confirmation, so the pending-approvals banner can settle them.
# ===========================================================================
def toolset_states(cfg: Any) -> Dict[str, bool]:
    from . import toolsets as _TS

    return _TS.toolset_states(cfg)


def toolset_enabled(cfg: Any, name: str, default: bool = True) -> bool:
    from . import toolsets as _TS

    return _TS.toolset_enabled(cfg, name, default)


def toolset_rows(cfg: Any, names: List[str]) -> List[Dict[str, Any]]:
    from . import toolsets as _TS

    return _TS.toolset_rows(cfg, names)


def stage_toolset_mutation(cfg: Any, name: str, enabled: bool, **kwargs: Any
                           ) -> Dict[str, Any]:
    from . import toolsets as _TS

    return _TS.stage_toolset_mutation(cfg, name, enabled, **kwargs)


def settle_toolset_mutation(cfg: Any, confirmation_id: str, **kwargs: Any
                            ) -> Dict[str, Any]:
    from . import toolsets as _TS

    return _TS.settle_toolset_mutation(cfg, confirmation_id, **kwargs)


def settle_toolset_mutation_if_owned(cfg: Any, confirmation_id: str, **kwargs: Any
                                     ) -> Optional[Dict[str, Any]]:
    from . import toolsets as _TS

    return _TS.settle_toolset_mutation_if_owned(cfg, confirmation_id, **kwargs)


def handle_toolset_mutation(cfg: Any, name: str, enabled: bool, **kwargs: Any
                            ) -> Dict[str, Any]:
    from . import toolsets as _TS

    return _TS.handle_toolset_mutation(cfg, name, enabled, **kwargs)


def toolset_enable(cfg: Any, name: str, **kwargs: Any) -> Dict[str, Any]:
    from . import toolsets as _TS

    return _TS.toolset_enable(cfg, name, **kwargs)


def toolset_disable(cfg: Any, name: str, **kwargs: Any) -> Dict[str, Any]:
    from . import toolsets as _TS

    return _TS.toolset_disable(cfg, name, **kwargs)


def toolset_doctor(cfg: Any) -> Dict[str, Any]:
    from . import toolsets as _TS

    return _TS.toolset_doctor(cfg)


# ===========================================================================
# MCP hosted apps — loopback attach grants (OC-D10)
#
# The host exposes *already-registered in-process handlers* over a loopback
# (127.0.0.1) MCP-style JSON-RPC endpoint. There is no outbound network, no
# external process, and no transport that can reach a remote host: the server
# binds an ephemeral port on loopback, authenticates a hashed bearer token, and
# can only dispatch a plugin id that the Qubit host already bound in-process.
# ===========================================================================
def _mcp_store_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / MCP_STORE_NAME


def _mcp_blank() -> Dict[str, Any]:
    return {"schema": MCP_SCHEMA, "grants": {}}


def _mcp_load(cfg: Any) -> Dict[str, Any]:
    path = _mcp_store_path(cfg)
    if not path.exists():
        return _mcp_blank()
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise PluginRuntimeError(MCP_CORRUPT_CODE, "MCP grant store too big")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except PluginRuntimeError:
        raise
    except (OSError, ValueError) as exc:
        raise PluginRuntimeError(
            MCP_CORRUPT_CODE, f"MCP grant store unreadable: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != MCP_SCHEMA \
            or not isinstance(raw.get("grants"), dict):
        raise PluginRuntimeError(MCP_CORRUPT_CODE,
                                 "MCP grant store has an unknown schema")
    return raw


def _mcp_save(cfg: Any, state: Dict[str, Any]) -> None:
    path = _mcp_store_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{MCP_STORE_NAME}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        raise PluginRuntimeError(
            MCP_CORRUPT_CODE, f"MCP grant store write failed: {exc}") from exc


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _mcp_public(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {"app_id": rec["app_id"], "host": rec.get("host", MCP_HOST),
            "capabilities": list(rec.get("capabilities", [])),
            "token_sha256": rec.get("token_sha256", "")[:16],
            "created_at": rec.get("created_at"),
            "created_by": rec.get("created_by", ""),
            "revoked": bool(rec.get("revoked", False))}


def mcp_grants(cfg: Any) -> Dict[str, Any]:
    """List attach grants (read-only; raw tokens are never stored or shown)."""
    before = estop_engaged(cfg)
    state = _mcp_load(cfg)
    rows = [_mcp_public(r) for r in state["grants"].values()
            if not r.get("revoked")]
    rows.sort(key=lambda r: r["app_id"])
    return {"state": "ESTOP" if before else "OK", "schema": MCP_SCHEMA,
            "host": MCP_HOST, "grants": rows,
            "tokens": "hashed-only; shown once at attach"}


def mcp_attach(cfg: Any, app_id: str, *, capabilities: Optional[List[str]] = None,
               confirmed: bool = False, confirmation_id: str = "",
               requested_by: str = "cli:user") -> Dict[str, Any]:
    """Mint a loopback attach grant with a one-time bearer token (hashed at rest).

    The raw token is returned exactly once and only its sha256 digest is
    persisted, so a later disk read cannot recover it. Capabilities default to
    the minimal ``initialize``/``tools/list``/``tools/call`` set and may only
    narrow; the endpoint still dispatches only host-bound in-process handlers.
    """
    _authorize_mutation(cfg, "mcp.attach", confirmed=confirmed,
                        confirmation_id=confirmation_id,
                        requested_by=requested_by,
                        confirm_code=MCP_CONFIRM_CODE,
                        estop_code=MCP_ESTOP_CODE)
    clean = (app_id or "").strip().lower()
    if not _MCP_ID_RE.match(clean):
        raise PluginRuntimeError(
            MCP_BAD_INPUT_CODE,
            "app_id must match ^[a-z0-9][a-z0-9_.-]{0,63}$")
    caps = list(capabilities) if capabilities is not None \
        else list(MCP_DEFAULT_CAPABILITIES)
    if not isinstance(caps, list) or not caps \
            or len(caps) > MCP_MAX_CAPABILITIES:
        raise PluginRuntimeError(MCP_BAD_INPUT_CODE,
                                 "capabilities must be a 1..16 item list")
    clean_caps = []
    for cap in caps:
        text = str(cap).strip()
        if text not in MCP_DEFAULT_CAPABILITIES:
            raise PluginRuntimeError(
                MCP_BAD_INPUT_CODE,
                "unknown capability; allowed: " +
                ", ".join(MCP_DEFAULT_CAPABILITIES))
        if text not in clean_caps:
            clean_caps.append(text)
    state = _mcp_load(cfg)
    active = [r for r in state["grants"].values() if not r.get("revoked")]
    if clean in state["grants"] and not state["grants"][clean].get("revoked"):
        raise PluginRuntimeError(MCP_EXISTS_CODE,
                                 f"grant {clean!r} already exists")
    if len(active) >= MCP_MAX_GRANTS:
        raise PluginRuntimeError(MCP_LIMIT_CODE,
                                 f"MCP grant limit ({MCP_MAX_GRANTS}) reached")
    token = secrets.token_urlsafe(32)
    rec = {"app_id": clean, "token_sha256": _hash_token(token),
           "host": MCP_HOST, "capabilities": sorted(clean_caps),
           "created_at": _now(), "created_by": requested_by, "revoked": False}
    state["grants"][clean] = rec
    _mcp_save(cfg, state)
    _audit("mcp_attach", requested_by, {"app_id": clean})
    return {"state": "OK", "grant": _mcp_public(rec),
            "bearer_token": token, "shown_once": True,
            "host": MCP_HOST, "url": f"http://{MCP_HOST}/mcp",
            "note": "loopback-only attach grant; token hashed at rest; only "
                    "already-registered in-process handlers are reachable"}


def mcp_revoke(cfg: Any, app_id: str, *, confirmed: bool = False,
               confirmation_id: str = "",
               requested_by: str = "cli:user") -> Dict[str, Any]:
    """Revoke an attach grant (confirmed via the shared journal, audited)."""
    _authorize_mutation(cfg, "mcp.revoke", confirmed=confirmed,
                        confirmation_id=confirmation_id,
                        requested_by=requested_by,
                        confirm_code=MCP_CONFIRM_CODE,
                        estop_code=MCP_ESTOP_CODE)
    clean = (app_id or "").strip().lower()
    state = _mcp_load(cfg)
    rec = state["grants"].get(clean)
    if rec is None or rec.get("revoked"):
        raise PluginRuntimeError(NOT_FOUND_CODE, f"no active grant {app_id!r}")
    rec["revoked"] = True
    rec["revoked_at"] = _now()
    _mcp_save(cfg, state)
    _audit("mcp_revoke", requested_by, {"app_id": clean})
    return {"state": "OK", "revoked": clean}


def mcp_authenticate(cfg: Any, token: str) -> Optional[Dict[str, Any]]:
    """Constant-time bearer check against active grants (loopback callers)."""
    if not token:
        return None
    digest = _hash_token(token)
    try:
        state = _mcp_load(cfg)
    except PluginRuntimeError:
        return None
    for rec in state["grants"].values():
        if rec.get("revoked"):
            continue
        if hmac.compare_digest(rec.get("token_sha256", ""), digest):
            return dict(rec)
    return None


def _mcp_tools() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for pid in sorted(set(_HANDLERS) | set(_HOST_HANDLERS)):
        rows.append({"name": pid,
                     "description": "Qubit in-process handler (loopback only)"})
    return rows


def _mcp_dispatch(cfg: Any, method: str, params: Dict[str, Any],
                  grant: Dict[str, Any]) -> Dict[str, Any]:
    caps = set(grant.get("capabilities", []))
    if method not in caps:
        return {"error": {"code": -32601, "message": "capability not granted"}}
    if method == "initialize":
        return {"result": {"protocolVersion": 1,
                           "serverInfo": {"name": "qubit-mcp", "version":
                                          "1.0.0"},
                           "capabilities": {"tools": {"listChanged": False}}}}
    if method == "tools/list":
        return {"result": {"tools": _mcp_tools()}}
    if method == "tools/call":
        name = str((params or {}).get("name", ""))
        args = (params or {}).get("arguments", {}) or {}
        host = _HOST_HANDLERS.get(name)
        plain = _HANDLERS.get(name)
        if host is None and plain is None:
            return {"error": {"code": -32602,
                              "message": "unknown in-process tool"}}
        if not isinstance(args, dict):
            return {"error": {"code": -32602,
                              "message": "arguments must be an object"}}
        try:
            blob = json.dumps(args, default=repr)
        except (TypeError, ValueError):
            return {"error": {"code": -32602, "message": "arguments invalid"}}
        if len(blob.encode("utf-8")) > MAX_ARGS_BYTES:
            return {"error": {"code": -32602, "message": "arguments too large"}}
        if estop_engaged(cfg):
            return {"error": {"code": -32003, "message": "emergency stop"}}
        try:
            result = host(cfg, dict(args)) if host is not None \
                else plain(dict(args))
        except Exception as exc:
            return {"error": {"code": -32603,
                              "message": _redact(str(exc))[:200]}}
        return {"result": {"content": _redact_obj(result),
                           "tool": name}}
    return {"error": {"code": -32601, "message": "method not found"}}


class _LoopbackServer(ThreadingHTTPServer):
    """Threaded loopback server that joins request threads on close."""

    daemon_threads = False
    allow_reuse_address = True
    block_on_close = True


class McpLoopbackHost:
    """A running loopback MCP endpoint; call :meth:`stop` to release it."""

    def __init__(self, server: ThreadingHTTPServer, thread: threading.Thread,
                 grants: List[str]) -> None:
        self._server = server
        self._thread = thread
        self.grants = grants
        self.host = MCP_HOST
        self.port = int(server.server_address[1])
        self.url = f"http://{MCP_HOST}:{self.port}/mcp"
        self.bound = "127.0.0.1 loopback only; no outbound network"

    def stop(self) -> None:
        try:
            self._server.shutdown()
        except Exception:
            pass
        try:
            self._server.server_close()
        except Exception:
            pass
        try:
            self._thread.join(timeout=2.0)
        except Exception:
            pass

    def as_dict(self) -> Dict[str, Any]:
        """JSON-safe view of the running endpoint (the handle stays live)."""
        return {"state": "OK", "schema": "qubit-mcp-host/1", "host": self.host,
                "port": self.port, "url": self.url, "grants": list(self.grants),
                "bound": self.bound, "listener_scope": "loopback-helper",
                "second_listener": False,
                "serve_disposition": MCP_SERVE_DISPOSITION}

    def __enter__(self) -> "McpLoopbackHost":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()


def _mcp_handler_class(cfg: Any, only_app: str = "") -> type:
    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "qubit-mcp/1.0"

        def log_message(self, *args: Any) -> None:  # no stderr noise
            return

        def _send(self, code: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, default=repr).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(body)

        def do_GET(self) -> None:  # no resources are served
            self._send(405, {"error": {"code": -32600,
                                       "message": "POST only"}})

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                length = 0
            if length <= 0 or length > MCP_MAX_BODY_BYTES:
                self._send(413, {"error": {"code": -32600,
                                           "message": "body too large"}})
                return
            auth = self.headers.get("Authorization", "") or ""
            token = auth[len("Bearer "):].strip() if auth.startswith(
                "Bearer ") else ""
            grant = mcp_authenticate(cfg, token)
            if grant is None:
                self._send(401, {"error": {"code": -32001,
                                           "message": "unauthorized"}})
                return
            if only_app and grant.get("app_id") != only_app:
                self._send(403, {"error": {"code": -32002,
                                           "message": "grant mismatch"}})
                return
            try:
                raw = self.rfile.read(length)
                req = json.loads(raw.decode("utf-8"))
            except (OSError, ValueError):
                self._send(400, {"error": {"code": -32700,
                                           "message": "parse error"}})
                return
            if not isinstance(req, dict):
                self._send(400, {"error": {"code": -32600,
                                           "message": "invalid request"}})
                return
            result = _mcp_dispatch(cfg, str(req.get("method", "")),
                                   req.get("params", {}) or {}, grant)
            out = {"jsonrpc": "2.0", "id": req.get("id")}
            out.update(result)
            _audit("mcp_call", "mcp:loopback",
                   {"app_id": grant.get("app_id"),
                    "method": str(req.get("method", ""))[:64]})
            self._send(200, out)

    return _Handler


def mcp_serve(cfg: Any, app_id: str = "", *, port: int = 0) -> McpLoopbackHost:
    """Start a loopback-only MCP endpoint over bound in-process handlers.

    A12: this is DISABLED BY DEFAULT and not a second product authority. No
    socket is bound unless an operator explicitly opts in by minting a named
    active attach grant with the human-confirmed ``mcp_attach --confirm`` flow
    (which itself requires the shared ApprovalJournal) and the one durable
    e-stop is clear. Without an active grant this raises the typed
    :data:`MCP_REFUSED_CODE` refusal before any bind. It binds ``127.0.0.1`` on
    an ephemeral port by default, refuses any non-loopback host, and can only
    dispatch handlers the host already bound in-process — it never accepts
    product traffic and has no CLI/voice/dashboard startup caller.

    Raises a typed :class:`PluginRuntimeError` on policy failure; otherwise
    returns a running :class:`McpLoopbackHost` that the caller must ``stop()``.
    """
    if estop_engaged(cfg):
        raise PluginRuntimeError(MCP_ESTOP_CODE, "emergency stop is latched")
    if MCP_HOST not in MCP_LOOPBACK_HOSTS:
        raise PluginRuntimeError(
            MCP_REFUSED_CODE,
            f"refusing non-loopback MCP bind host {MCP_HOST!r}")
    try:
        port_num = int(port)
    except (TypeError, ValueError):
        raise PluginRuntimeError(MCP_BAD_INPUT_CODE, "port must be an integer")
    if port_num < 0 or port_num > 65535:
        raise PluginRuntimeError(MCP_BAD_INPUT_CODE, "port out of range")
    clean = (app_id or "").strip().lower()
    state = _mcp_load(cfg)
    active = sorted(r["app_id"] for r in state["grants"].values()
                    if not r.get("revoked"))
    if clean:
        rec = state["grants"].get(clean)
        if rec is None or rec.get("revoked"):
            raise PluginRuntimeError(NOT_FOUND_CODE, f"no active grant {app_id!r}")
    elif not active:
        raise PluginRuntimeError(
            MCP_REFUSED_CODE,
            "no active MCP attach grant; mint one with --confirm")
    try:
        server = _LoopbackServer(
            (MCP_HOST, port_num), _mcp_handler_class(cfg, clean))
    except OSError as exc:
        raise PluginRuntimeError(
            MCP_REFUSED_CODE, f"loopback bind failed: {exc}") from exc
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _audit("mcp_serve", "mcp:host",
           {"port": server.server_address[1], "grants": len(active)})
    return McpLoopbackHost(server, thread, active)


def mcp_doctor(cfg: Any) -> Dict[str, Any]:
    """MCP host diagnostics: grant-store health, bound handlers, the latch."""
    warnings: List[str] = []
    store_ok = True
    try:
        grants = _mcp_load(cfg)["grants"]
    except PluginRuntimeError as exc:
        store_ok = False
        grants = {}
        warnings.append(f"{exc.code}: {exc.detail[:200]}")
    active = [r["app_id"] for r in grants.values() if not r.get("revoked")]
    kinds = _handler_kinds(cfg)
    return {"state": "OK" if store_ok else "DEGRADED", "schema": MCP_SCHEMA,
            "store_ok": store_ok, "host": MCP_HOST,
            "active_grants": sorted(active),
            "tools": sorted(_HANDLERS), "tool_kinds": kinds,
            "outbound_network": "NONE",
            "listener": "loopback-helper",
            "one_listener_rule": "accounted-exception",
            "serve_disposition": MCP_SERVE_DISPOSITION,
            "serve_enabled": bool(active),
            "second_listener": False,
            "bind": "127.0.0.1:ephemeral",
            "estop": estop_engaged(cfg), "warnings": warnings}


# ===========================================================================
# Policy conformance plugin — user-authored rules + attestation (OC-D02)
#
# Extends the existing Qubit safety policy with operator-authored,
# workspace-conformance rules, a bounded doctor that reports findings, and a
# digest attestation over the exact ruleset and findings. Rules are data:
# storing or matching one never imports or executes anything.
# ===========================================================================
def _policy_store_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / POLICY_STORE_NAME


def _policy_blank() -> Dict[str, Any]:
    return {"schema": POLICY_SCHEMA, "rules": {}}


def _policy_load(cfg: Any) -> Dict[str, Any]:
    path = _policy_store_path(cfg)
    if not path.exists():
        return _policy_blank()
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise PluginRuntimeError(POLICY_CORRUPT_CODE, "policy store too big")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except PluginRuntimeError:
        raise
    except (OSError, ValueError) as exc:
        raise PluginRuntimeError(
            POLICY_CORRUPT_CODE, f"policy store unreadable: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != POLICY_SCHEMA \
            or not isinstance(raw.get("rules"), dict):
        raise PluginRuntimeError(POLICY_CORRUPT_CODE,
                                 "policy store has an unknown schema")
    return raw


def _policy_save(cfg: Any, state: Dict[str, Any]) -> None:
    path = _policy_store_path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{POLICY_STORE_NAME}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        raise PluginRuntimeError(
            POLICY_CORRUPT_CODE, f"policy store write failed: {exc}") from exc


def _policy_public(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": rec["id"], "level": rec["level"],
            "pattern": _redact(rec.get("pattern", ""))[:200],
            "glob": rec.get("glob", ""), "message": rec.get("message", ""),
            "updated_by": rec.get("updated_by", ""),
            "updated_at": rec.get("updated_at")}


def _validate_rule(rule: Any) -> Dict[str, Any]:
    if not isinstance(rule, dict):
        raise PluginRuntimeError(POLICY_BAD_INPUT_CODE, "rule must be an object")
    unknown = set(rule) - {"id", "pattern", "level", "glob", "message"}
    if unknown:
        raise PluginRuntimeError(
            POLICY_BAD_INPUT_CODE,
            "rule has unknown fields: " + ", ".join(sorted(unknown)))
    rid = str(rule.get("id", "")).strip().lower()
    if not _POLICY_ID_RE.match(rid):
        raise PluginRuntimeError(POLICY_BAD_INPUT_CODE,
                                 "rule id must match ^[a-z0-9][a-z0-9_.-]{0,63}$")
    pattern = str(rule.get("pattern", ""))
    if not pattern or len(pattern) > 256:
        raise PluginRuntimeError(POLICY_BAD_INPUT_CODE,
                                 "pattern must be 1..256 chars")
    try:
        re.compile(pattern)
    except re.error as exc:
        raise PluginRuntimeError(POLICY_BAD_INPUT_CODE,
                                 f"pattern is not a valid regex: {exc}") from exc
    level = str(rule.get("level", "warn")).strip().lower()
    if level not in POLICY_LEVELS:
        raise PluginRuntimeError(POLICY_BAD_INPUT_CODE,
                                 f"level must be one of {POLICY_LEVELS}")
    glob = str(rule.get("glob", "") or "").strip()[:128]
    message = str(rule.get("message", "") or "").strip()[:200]
    return {"id": rid, "pattern": pattern, "level": level, "glob": glob,
            "message": message}


def policy_rules(cfg: Any) -> Dict[str, Any]:
    """List user-authored conformance rules (read-only, redacted)."""
    state = _policy_load(cfg)
    rows = sorted(state["rules"].values(), key=lambda r: r.get("id", ""))
    return {"state": "OK", "schema": POLICY_SCHEMA,
            "rules": [_policy_public(r) for r in rows]}


def policy_add_rule(cfg: Any, *, rule: Any, confirmed: bool = False,
                    confirmation_id: str = "",
                    requested_by: str = "cli:user") -> Dict[str, Any]:
    """Author/replace a conformance rule (shared-journal confirmed, bounded)."""
    _authorize_mutation(cfg, "policy.rule.add", confirmed=confirmed,
                        confirmation_id=confirmation_id,
                        requested_by=requested_by,
                        confirm_code=POLICY_CONFIRM_CODE,
                        estop_code=POLICY_ESTOP_CODE)
    clean = _validate_rule(rule)
    state = _policy_load(cfg)
    if clean["id"] not in state["rules"] \
            and len(state["rules"]) >= POLICY_MAX_RULES:
        raise PluginRuntimeError(POLICY_LIMIT_CODE,
                                 f"policy rule limit ({POLICY_MAX_RULES})")
    clean["updated_at"] = _now()
    clean["updated_by"] = requested_by
    state["rules"][clean["id"]] = clean
    _policy_save(cfg, state)
    _audit("policy_rule_add", requested_by, {"rule": clean["id"]})
    return {"state": "OK", "rule": _policy_public(clean)}


def policy_remove_rule(cfg: Any, rule_id: str, *, confirmed: bool = False,
                       confirmation_id: str = "",
                       requested_by: str = "cli:user") -> Dict[str, Any]:
    """Remove a conformance rule (shared-journal confirmed, audited)."""
    _authorize_mutation(cfg, "policy.rule.remove", confirmed=confirmed,
                        confirmation_id=confirmation_id,
                        requested_by=requested_by,
                        confirm_code=POLICY_CONFIRM_CODE,
                        estop_code=POLICY_ESTOP_CODE)
    clean = str(rule_id or "").strip().lower()
    state = _policy_load(cfg)
    if clean not in state["rules"]:
        raise PluginRuntimeError(POLICY_NOT_FOUND_CODE,
                                 f"no rule {rule_id!r}")
    del state["rules"][clean]
    _policy_save(cfg, state)
    _audit("policy_rule_remove", requested_by, {"rule": clean})
    return {"state": "OK", "removed": clean}


def _policy_workspace(workspace: str) -> Path:
    text = str(workspace or "").strip()
    if not text or "\x00" in text:
        raise PluginRuntimeError(POLICY_BAD_INPUT_CODE,
                                 "workspace is required")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise PluginRuntimeError(POLICY_BAD_INPUT_CODE,
                                 "workspace must be an absolute path")
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise PluginRuntimeError(POLICY_BAD_INPUT_CODE,
                                 f"bad workspace ({exc})") from exc
    if not resolved.is_dir():
        raise PluginRuntimeError(POLICY_NOT_FOUND_CODE,
                                 "workspace folder does not exist")
    return resolved


def _policy_scan(workspace: Path) -> List[Dict[str, Any]]:
    """Bounded, read-only text scan (no symlinks, hard file/byte caps)."""
    out: List[Dict[str, Any]] = []
    scanned = 0
    for path in sorted(workspace.rglob("*")):
        if scanned >= POLICY_MAX_FILES:
            break
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if path.stat().st_size > POLICY_MAX_SCAN_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        scanned += 1
        out.append({"path": str(path.relative_to(workspace)), "text": text})
    return out


def policy_doctor(cfg: Any, workspace: str) -> Dict[str, Any]:
    """Workspace-conformance doctor: builtin markers + user-authored rules."""
    from .opencode.policy import flag_untrusted

    root = _policy_workspace(workspace)
    try:
        rules = list(_policy_load(cfg)["rules"].values())
    except PluginRuntimeError as exc:
        return {"state": "DEGRADED", "code": exc.code,
                "detail": exc.detail[:200], "findings": []}
    compiled = []
    for rec in rules:
        try:
            compiled.append((rec, re.compile(rec["pattern"])))
        except re.error:
            continue
    findings: List[Dict[str, Any]] = []
    files = _policy_scan(root)
    for item in files:
        rel = item["path"]
        text = item["text"]
        for marker in flag_untrusted(text):
            findings.append({"kind": "builtin", "level": "warn", "rule": marker,
                             "file": rel,
                             "message": f"hostile marker {marker!r}"})
        for rec, rx in compiled:
            if rec.get("glob") and not Path(rel).match(rec["glob"]):
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    findings.append({"kind": "rule", "level": rec["level"],
                                     "rule": rec["id"], "file": rel,
                                     "line": line_no,
                                     "message": rec.get("message", "")})
    denies = sum(1 for f in findings if f["level"] == "deny")
    return {"state": "FAIL" if denies else ("WARN" if findings else "OK"),
            "schema": POLICY_SCHEMA, "workspace": str(root),
            "files_scanned": len(files), "rules": len(compiled),
            "findings": findings,
            "deny": denies,
            "warn": sum(1 for f in findings if f["level"] == "warn")}


def policy_attest(cfg: Any, workspace: str, *, requested_by: str,
                  note: str = "") -> Dict[str, Any]:
    """Human-signed attestation digest over the ruleset + doctor findings."""
    if requested_by != "user":
        raise PluginRuntimeError(
            POLICY_ATTEST_ACTOR_CODE,
            "only a human owner may emit a policy attestation")
    report = policy_doctor(cfg, workspace)
    state = _policy_load(cfg)
    rules_digest = hashlib.sha256(json.dumps(
        state["rules"], sort_keys=True, default=repr).encode("utf-8")
    ).hexdigest()
    payload = {"workspace": report.get("workspace", ""),
               "state": report.get("state", ""),
               "findings": report.get("findings", []),
               "rules_digest": rules_digest}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True,
                                       default=repr).encode("utf-8")
                            ).hexdigest()
    rec = {"schema": "qubit-policy-attestation/1",
           "attested_by": requested_by, "note": _redact(note)[:200],
           "digest": digest, "rules_digest": rules_digest,
           "workspace": report.get("workspace", ""),
           "state": report.get("state", ""),
           "findings_count": len(report.get("findings", [])),
           "deny": report.get("deny", 0), "warn": report.get("warn", 0),
           "generated_at": _now()}
    _audit("policy_attest", requested_by,
           {"workspace": rec["workspace"][:120], "digest": digest})
    return {"state": "OK", "attestation": rec,
            "report": {k: v for k, v in report.items() if k != "findings"}}


# ===========================================================================
# Plugin marketplace / discovery — local index first, remote typed-refused
# (OC-D31)
# ===========================================================================
def marketplace_source_kind(spec: str) -> str:
    """Classify a marketplace source without ever touching the network."""
    raw = (spec or "").strip()
    if not raw:
        return "missing"
    lowered = raw.lower()
    if "://" in raw or lowered.startswith(("git@", "ssh://", "git://")):
        return "remote"
    candidate = Path(raw).expanduser()
    if candidate.is_dir():
        return "local-dir"
    if candidate.is_file():
        return "local-file"
    if raw.count("/") == 1 and not raw.startswith((".", "/", "~")):
        return "remote"  # owner/repo shorthand
    return "missing"


def marketplace_refuse_remote(spec: str) -> Dict[str, Any]:
    """Typed refusal for a remote marketplace source (nothing is fetched)."""
    return {"state": "BLOCKED", "code": MARKETPLACE_REMOTE_CODE,
            "source": _redact((spec or "")[:200]),
            "explanation": "Qubit Agent cannot do this: remote marketplaces "
                           "are not fetched. Add reviewed manifests to a local "
                           "index directory and discover them offline.",
            "owner": "qubit-policy",
            "rollback": "nothing fetched or installed; nothing to roll back"}


def marketplace_discover(cfg: Any, source: str, *,
                         query: str = "") -> Dict[str, Any]:
    """Discover reviewed plugin manifests from a local directory/file index.

    Reads and validates manifests as metadata only: nothing is imported,
    fetched, or executed. Remote sources are typed-refused.
    """
    kind = marketplace_source_kind(source)
    if kind == "remote":
        return marketplace_refuse_remote(source)
    if kind == "missing":
        raise PluginRuntimeError(MARKETPLACE_BAD_INPUT_CODE,
                                 "marketplace source does not exist locally")
    root = Path(source).expanduser()
    paths = sorted(root.glob("*.json")) if root.is_dir() else [root]
    entries: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for path in paths[:MAX_PLUGINS]:
        try:
            if path.stat().st_size > MAX_MANIFEST_BYTES:
                errors.append({"path": path.name, "error": "too large"})
                continue
            manifest = json.loads(path.read_text(encoding="utf-8"))
            clean = _validate_manifest(manifest)
            entries.append(_public(clean))
        except (OSError, ValueError, PluginRuntimeError) as exc:
            code = getattr(exc, "code", "PLUGIN_MANIFEST_INVALID")
            errors.append({"path": path.name, "error": str(code)[:64]})
    q = (query or "").strip().lower()
    if q:
        entries = [e for e in entries
                   if q in e["id"].lower() or q in e["label"].lower()
                   or any(q in c.lower() for c in e.get("capabilities", []))]
    entries.sort(key=lambda e: e["id"])
    return {"state": "OK", "code": "", "schema": "qubit-marketplace/1",
            "source": str(root), "source_kind": kind,
            "plugins": entries, "count": len(entries), "errors": errors,
            "remote": "refused", "network": "NONE"}


def marketplace_doctor(cfg: Any) -> Dict[str, Any]:
    """Marketplace/discovery surface state (local-first, no remote fetch)."""
    return {"state": "OK", "schema": "qubit-marketplace/1",
            "remote": "REFUSED", "remote_code": MARKETPLACE_REMOTE_CODE,
            "local_index": "SUPPORTED",
            "detail": "discover reviewed manifests from a local directory; "
                      "remote marketplaces are never fetched",
            "estop": estop_engaged(cfg)}
