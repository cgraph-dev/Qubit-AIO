"""Qubit in-process plugin host contract (OpenClaw audit #40).

Qubit ships a real plugin *registry* over four kinds (``tool``, ``channel``,
``provider``, ``hook``) with validated manifests and honest typed states. It
deliberately ships NO provider-backed execution runtime:

* a manifest is metadata, never payload: ``register`` validates
  ``id``/``kind``/``entrypoint``/``digest`` with hard bounds, records it, and
  never imports, evaluates, downloads, or executes anything;
* ``invoke`` is gated in order: emergency stop -> fresh confirmation -> the
  plugin exists -> an in-process host handler is installed. A manifest
  ``entrypoint`` does NOT grant execution; without a host-registered handler
  the call resolves to the typed ``PLUGIN_RUNTIME_UNAVAILABLE`` gate and
  nothing runs. A single safe built-in ``builtin.echo`` handler is installed
  so the in-process contract is exercisable end to end;
* every output (entrypoint, arguments, results, errors) passes through
  :func:`redaction.redact_text`/:func:`redaction.redact_obj`; the registry
  file is 0600 and bounded;
* mutating operations refuse while the emergency stop is latched via the same
  durable :class:`PowerControlStore` every other surface observes.

The registry lives in ``<state_dir>/plugin-runtime.json``. There is no
network and no shell: this module imports neither ``subprocess`` nor any
socket/HTTP client.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

SCHEMA = "qubit-plugin-runtime/1"
STORE_NAME = "plugin-runtime.json"
MAX_MANIFEST_BYTES = 65536
MAX_PLUGINS = 256
MAX_ARGS_BYTES = 16384
MAX_RESULT_CHARS = 4000
MAX_FILE_BYTES = 2_000_000

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
            "in_process": "AVAILABLE",
            "external_runtime": "UNAVAILABLE",
            "external_code": UNAVAILABLE_CODE,
            "detail": "in-process handlers only; provider-backed execution is "
                      "unavailable and manifest entrypoints are recorded, "
                      "never executed",
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
             requested_by: str = "cli:user") -> Dict[str, Any]:
    """Record a validated plugin manifest (metadata only; no execution).

    Confirmation-gated, estop-aware, bounded, and redacted. Nothing is
    fetched, imported, or run: the entrypoint string is stored verbatim so
    the host can decide later whether to bind an in-process handler.
    """
    if estop_engaged(cfg):
        raise PluginRuntimeError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise PluginRuntimeError(CONFIRM_CODE,
                                 "plugin register requires explicit --confirm")
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


def unregister(cfg: Any, plugin_id: str, *, confirmed: bool = False,
               requested_by: str = "cli:user") -> Dict[str, Any]:
    """Remove a registered plugin (built-ins cannot be removed)."""
    if estop_engaged(cfg):
        raise PluginRuntimeError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise PluginRuntimeError(
            CONFIRM_CODE, "plugin unregister requires explicit --confirm")
    clean = _check_id(plugin_id)
    if clean in _BUILTIN:
        raise PluginRuntimeError(REFUSED_CODE,
                                 f"{clean!r} is a built-in plugin")
    state = _load(cfg)
    if clean not in state["plugins"]:
        raise PluginRuntimeError(NOT_FOUND_CODE, f"no plugin {plugin_id!r}")
    del state["plugins"][clean]
    _save(cfg, state)
    _audit("unregister", requested_by, {"id": clean})
    return {"state": "OK", "removed": clean}


def invoke(cfg: Any, plugin_id: str, *, args: Optional[Dict[str, Any]] = None,
           confirmed: bool = False,
           requested_by: str = "cli:user") -> Dict[str, Any]:
    """Invoke an in-process handler, or gate with a typed UNAVAILABLE.

    Order: emergency stop -> fresh confirmation -> plugin exists -> an
    in-process host handler is bound. A manifest entrypoint alone never
    executes; absent a handler the result is the typed
    ``PLUGIN_RUNTIME_UNAVAILABLE`` gate and nothing runs.
    """
    if estop_engaged(cfg):
        raise PluginRuntimeError(ESTOP_CODE, "emergency stop is latched")
    if not confirmed:
        raise PluginRuntimeError(CONFIRM_CODE,
                                 "plugin invoke requires explicit --confirm")
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
            "estop": estop_engaged(cfg),
            "external_runtime": UNAVAILABLE_CODE,
            "warnings": warnings}
