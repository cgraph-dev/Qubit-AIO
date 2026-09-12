"""Qubit in-process plugin host contract (OpenClaw audit #40).

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
  so the in-process contract is exercisable end to end;
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
import os
import re
import secrets
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
MCP_MAX_GRANTS = 64
MCP_MAX_BODY_BYTES = 65536
MCP_MAX_CAPABILITIES = 16
MCP_DEFAULT_CAPABILITIES = ("initialize", "tools/list", "tools/call")

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
HANDLER_FAILED_CODE = "PLUGIN_HANDLER_FAILED"
HANDLER_REQUIRED_CODE = "PLUGIN_HANDLER_REQUIRED"

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
    "capabilities"))


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
    return {"id": pid, "kind": kind, "entrypoint": entry.strip(),
            "digest": digest,
            "label": _bounded(manifest.get("label", ""), 64),
            "provider": _bounded(manifest.get("provider", ""), 64),
            "version": _bounded(manifest.get("version", ""), 32),
            "capabilities": sorted(set(clean_caps))}


def _echo_handler(args: Dict[str, Any]) -> Dict[str, Any]:
    """Safe built-in test handler: echoes bounded text, touches nothing."""
    return {"echo": str(args.get("text", ""))[:200],
            "handler": "builtin.echo"}


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


def _public(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": rec["id"], "kind": rec.get("kind", ""),
            "entrypoint": _redact(rec.get("entrypoint", ""))[:160],
            "digest": rec.get("digest", ""),
            "label": _redact(rec.get("label", ""))[:64],
            "provider": _redact(rec.get("provider", ""))[:64],
            "version": rec.get("version", ""),
            "capabilities": list(rec.get("capabilities", [])),
            "builtin": bool(rec.get("builtin", False)),
            "handler": rec["id"] in _HANDLERS,
            "registered_at": rec.get("registered_at"),
            "updated_by": rec.get("updated_by", "")}


def _find(cfg: Any, plugin_id: str) -> Optional[Dict[str, Any]]:
    clean = _check_id(plugin_id)
    if clean in _BUILTIN:
        return dict(_BUILTIN[clean])
    return _load(cfg)["plugins"].get(clean)


def _handler_kinds(cfg: Any) -> Dict[str, str]:
    """Map every bound in-process handler to its plugin kind (honest view)."""
    kinds: Dict[str, str] = {}
    for pid, rec in _BUILTIN.items():
        if pid in _HANDLERS:
            kinds[pid] = rec.get("kind", "")
    try:
        for pid, rec in _load(cfg)["plugins"].items():
            if pid in _HANDLERS:
                kinds[pid] = rec.get("kind", "")
    except PluginRuntimeError:
        pass
    return kinds


def status(cfg: Any) -> Dict[str, Any]:
    """Typed runtime state: the in-process host is real, provider runtime is
    not. An unconfigured/absent execution runtime is reported honestly."""
    latched = estop_engaged(cfg)
    try:
        count = len(_load(cfg)["plugins"])
    except PluginRuntimeError:
        count = -1
    return {"state": "ESTOP" if latched else "READY",
            "code": ESTOP_CODE if latched else "",
            "schema": SCHEMA, "kinds": list(PLUGIN_KINDS),
            "plugins": count, "builtin": sorted(_BUILTIN),
            "handlers": sorted(_HANDLERS),
            "handler_kinds": _handler_kinds(cfg),
            "in_process": "AVAILABLE",
            "external_runtime": "UNAVAILABLE",
            "external_code": UNAVAILABLE_CODE,
            "detail": "in-process host handlers are real for every kind "
                      "(tool/channel/provider/hook); manifest entrypoints are "
                      "recorded, never imported or executed",
            "estop": latched}


def list_plugins(cfg: Any) -> Dict[str, Any]:
    """List registered plugins (built-ins first), redacted."""
    if estop_engaged(cfg):
        return refuse_estop("plugin list")
    rows = [dict(v) for v in _BUILTIN.values()]
    rows.extend(_load(cfg)["plugins"].values())
    rows.sort(key=lambda r: r.get("id", ""))
    return {"state": "OK", "schema": SCHEMA,
            "plugins": [_public(r) for r in rows]}


def get_plugin(cfg: Any, plugin_id: str) -> Dict[str, Any]:
    """Show one plugin (redacted)."""
    rec = _find(cfg, plugin_id)
    if rec is None:
        raise PluginRuntimeError(NOT_FOUND_CODE, f"no plugin {plugin_id!r}")
    return {"state": "OK", "plugin": _public(rec)}


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
    if clean["id"] in _BUILTIN:
        raise PluginRuntimeError(REFUSED_CODE,
                                 f"{clean['id']!r} is a built-in plugin")
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
    if clean["id"] in _BUILTIN:
        raise PluginRuntimeError(REFUSED_CODE,
                                 f"{clean['id']!r} is a built-in plugin")
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
    if clean in _BUILTIN:
        raise PluginRuntimeError(REFUSED_CODE,
                                 f"{clean!r} is a built-in plugin")
    state = _load(cfg)
    if clean not in state["plugins"]:
        raise PluginRuntimeError(NOT_FOUND_CODE, f"no plugin {plugin_id!r}")
    del state["plugins"][clean]
    _save(cfg, state)
    _HANDLERS.pop(clean, None)
    _audit("unregister", requested_by, {"id": clean})
    return {"state": "OK", "removed": clean}


def invoke(cfg: Any, plugin_id: str, *, args: Optional[Dict[str, Any]] = None,
           confirmed: bool = False, confirmation_id: str = "",
           requested_by: str = "cli:user") -> Dict[str, Any]:
    """Invoke an in-process handler, or gate with a typed UNAVAILABLE.

    Order: emergency stop -> shared-journal confirmation -> plugin exists ->
    an in-process host handler is bound. A manifest entrypoint alone never
    executes; absent a handler the result is the typed
    ``PLUGIN_RUNTIME_UNAVAILABLE`` gate and nothing runs.
    """
    _authorize_mutation(cfg, "plugin.invoke", confirmed=confirmed,
                        confirmation_id=confirmation_id,
                        requested_by=requested_by)
    clean = _check_id(plugin_id)
    rec = _find(cfg, clean)
    if rec is None:
        raise PluginRuntimeError(NOT_FOUND_CODE, f"no plugin {plugin_id!r}")
    handler = _HANDLERS.get(clean)
    if handler is None:
        raise PluginRuntimeError(
            UNAVAILABLE_CODE,
            f"plugin {clean!r} has no in-process handler: provider-backed "
            "execution is unavailable and manifest entrypoints are never "
            "executed")
    payload = {} if args is None else args
    if not isinstance(payload, dict):
        raise PluginRuntimeError(BAD_ARGS_CODE, "args must be an object")
    try:
        blob = json.dumps(payload, default=repr)
    except (TypeError, ValueError):
        raise PluginRuntimeError(BAD_ARGS_CODE,
                                 "args are not serializable") from None
    if len(blob.encode("utf-8")) > MAX_ARGS_BYTES:
        raise PluginRuntimeError(BAD_ARGS_CODE,
                                 f"args exceed {MAX_ARGS_BYTES} bytes")
    try:
        result = handler(dict(payload))
    except Exception as exc:
        raise PluginRuntimeError(
            HANDLER_FAILED_CODE, f"handler failed: {_redact(str(exc))[:200]}")
    safe = _redact_obj(result)
    if isinstance(safe, str):
        safe = safe[:MAX_RESULT_CHARS]
    _audit("invoke", requested_by,
           {"id": clean, "args_sha256": hashlib.sha256(
               blob.encode()).hexdigest()})
    return {"state": "OK", "plugin": clean, "result": safe,
            "note": "in-process handler only; nothing external executed"}


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
            "handlers": sorted(_HANDLERS),
            "handler_kinds": _handler_kinds(cfg),
            "estop": estop_engaged(cfg),
            "external_runtime": UNAVAILABLE_CODE,
            "warnings": warnings}


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
    for pid in sorted(_HANDLERS):
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
        if name not in _HANDLERS:
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
            result = _HANDLERS[name](dict(args))
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
                "bound": self.bound}

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

    Binds ``127.0.0.1`` on an ephemeral port by default, refuses when no active
    attach grant exists, and can only dispatch host-bound handlers. Raises a
    typed :class:`PluginRuntimeError` on policy failure; otherwise returns a
    running :class:`McpLoopbackHost` that the caller must ``stop()``.
    """
    if estop_engaged(cfg):
        raise PluginRuntimeError(MCP_ESTOP_CODE, "emergency stop is latched")
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
