"""Canonical Qubit MCP server registry (client-side inventory + config).

Qubit' dashboard exposes CRUD/enable/test/auth for MCP servers the agent can
connect to. Qubit never mounts an unauthenticated external MCP server, but the
*configuration* of those servers is a real, reviewable capability: this module
records the requested server definitions in one canonical store
(``<state_dir>/mcp_servers.json``) with validation and reference-only
credentials.

Client invocation is real but strictly gated. ``test`` validates the recorded
config and then verifies the live server with a bounded ``tools/list`` probe;
``invoke`` calls an allowlisted tool and ``invoke_method`` brokers the
resources/prompts methods. Every one of them runs the shared e-stop + durable
confirmation gate first, and every connection goes through the ONE brokered
transport (:mod:`voicebridge.mcp_client`). A server that is not enabled and
runnable is inventory-only and is never connected to.

Reads are merged with the operator's ``mcp.servers`` config so the inventory
the CLI/dashboard already show stays authoritative; the store adds the rows the
dashboard created.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import mcp_client as MC

SCHEMA = "qubit-mcp-servers/1"
STORE_NAME = "mcp_servers.json"
MAX_SERVERS = 100
MAX_FIELD_CHARS = 512
MAX_CAPABILITIES = 64

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
_CAP_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,127}$")
_TRANSPORTS = ("stdio", "http")
_AUTHS = ("none", "header", "oauth")
_REF_RE = re.compile(r"^(vault|env|file):")

# Client-invocation disposition. Qubit's MCP authority is inventory/config plus
# a single brokered client transport (``voicebridge.mcp_client``). A server is
# invocable ONLY when it is explicitly enabled, records a runnable transport
# (stdio ``command`` / http ``url``), and declares a non-empty tool allowlist
# (``capabilities``). Anything else keeps the typed, fail-closed OPERATOR_ONLY
# refusal: no connection is opened and no process is spawned.
INVOKE_SCHEMA = "qubit-mcp-invocation/1"
INVOKE_OPERATOR_CODE = "MCP_CLIENT_INVOCATION_OPERATOR_REQUIRED"
INVOKE_READY_CODE = "MCP_INVOCATION_READY"
INVOKE_ESTOP_CODE = "MCP_ESTOP"
INVOKE_CONFIRM_CODE = "MCP_CONFIRM_REQUIRED"
INVOKE_NOT_FOUND_CODE = "MCP_NOT_FOUND"
INVOKE_REASON = ("MCP_NOT_CONNECTED_NO_CLIENT_INVOCATION: this server is not "
                 "explicitly enabled with a declared tool allowlist; Qubit "
                 "opens no MCP connection and exposes no client tool "
                 "invocation for it")
INVOKE_READY_REASON = ("ready: enabled, runnable transport, and a declared tool "
                       "allowlist; invocation runs through the brokered MCP "
                       "client (e-stop + confirmation gated)")
INVOKE_PREREQUISITE = ("enable the server, record a stdio command or http url, "
                       "and declare its allowed tools in `capabilities`")
INVOKE_COUNTERPART = ("voicebridge.plugin_runtime (in-process loopback MCP host "
                      "over host-bound handlers)")

# Discovery / resources / prompts. These are server-level operations through the
# same brokered client (``voicebridge.mcp_client``): e-stop + shared-journal
# confirmation, then the method gate (supported transport + enabled + runnable).
# ``tools/list`` is how the live server's tools are discovered; the result is
# only ever what the server returned, never a fabricated tool.
DISCOVER_SCHEMA = "qubit-mcp-discovery/1"
DISCOVER_PROBE_CODE = "MCP_DISCOVERY_OK"
#: Bounded default for a live ``tools/list`` probe (a hung server cannot wedge a
#: caller; the transport still hard-kills the process group on timeout).
PROBE_TIMEOUT_S = 8.0
#: Server-level methods the registry will broker. Mirrors the client's
#: sanctioned set so a caller cannot smuggle an arbitrary JSON-RPC method.
OPERATION_METHODS = MC.SUPPORTED_METHODS
#: Read-only dashboard capability reads mapped to their sanctioned client
#: method. These are list operations, so they carry the lighter method gate
#: (enabled + runnable), not the tool allowlist.
CAPABILITY_METHODS = {
    "resources": MC.RESOURCE_LIST_METHOD,
    "prompts": MC.PROMPT_LIST_METHOD,
    "resource-templates": MC.RESOURCE_TEMPLATES_METHOD,
}
DISCOVER_PREREQUISITE = ("enable the server and record a stdio command or http "
                         "url so Qubit can open a brokered client connection")


class McpServerError(Exception):
    """Typed MCP-registry failure (``code``/``detail``)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _store_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / STORE_NAME


def _load(cfg: Any) -> Dict[str, Any]:
    path = _store_path(cfg)
    if not path.is_file():
        return {"schema": SCHEMA, "servers": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise McpServerError("MCP_STORE_CORRUPT",
                             f"mcp server store unreadable: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA \
            or not isinstance(raw.get("servers"), dict):
        raise McpServerError("MCP_STORE_CORRUPT", "unknown mcp server schema")
    return raw


def _save(cfg: Any, data: Dict[str, Any]) -> None:
    path = _store_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True),
                   encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def _bounded(value: Any, limit: int = MAX_FIELD_CHARS) -> str:
    return str(value or "").strip()[:limit]


def _cfg_timeout(cfg: Any, key: str, fallback: float,
                 maximum: float = MC.MAX_TIMEOUT_S) -> float:
    """Resolve a bounded timeout from ``mcp.<key>`` config, else ``fallback``.

    Optional plumbing only: an absent/invalid/non-positive value keeps the
    existing hardcoded default, and every value is clamped to the client's hard
    cap so config can never remove the timeout bound.
    """
    raw = None
    get = getattr(cfg, "get", None)
    if callable(get):
        try:
            raw = get("mcp", key, default=None)
        except Exception:  # noqa: BLE001 - config is never a gate
            raw = None
    try:
        value = float(raw) if raw is not None else float(fallback)
    except (TypeError, ValueError):
        value = float(fallback)
    if value <= 0:
        value = float(fallback)
    return max(0.1, min(value, float(maximum)))


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    """Redacted audit event; credential values are never passed in."""
    from .logging_setup import log_event

    try:
        log_event(f"mcp.{op}", actor=actor,
                  **{k: _bounded(v, 160) for k, v in detail.items()})
    except Exception:
        pass


def _estop(cfg: Any) -> bool:
    """Same durable latch probe every other surface observes (fail closed)."""
    try:
        from .plugins import estop_engaged
        return bool(estop_engaged(cfg))
    except Exception:
        return True


def _check_name(name: str) -> str:
    clean = _bounded(name, 64)
    if not _NAME_RE.match(clean):
        raise McpServerError("MCP_BAD_INPUT",
                             "name must match ^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
    return clean


def _validate(record: Dict[str, Any]) -> Dict[str, Any]:
    transport = _bounded(record.get("transport", "stdio"), 16).lower()
    if transport not in _TRANSPORTS:
        raise McpServerError("MCP_BAD_INPUT",
                             f"transport must be one of {_TRANSPORTS}")
    auth = _bounded(record.get("auth", record.get("auth_type", "none")), 16).lower()
    if auth not in _AUTHS:
        raise McpServerError("MCP_BAD_INPUT", f"auth must be one of {_AUTHS}")
    out: Dict[str, Any] = {"transport": transport, "auth": auth}
    if transport == "stdio":
        command = _bounded(record.get("command", ""), 256)
        if not command:
            raise McpServerError("MCP_BAD_INPUT",
                                 "a stdio server needs a command")
        out["command"] = command
        args = record.get("args") or []
        if not isinstance(args, (list, tuple)):
            raise McpServerError("MCP_BAD_INPUT", "args must be a list")
        out["args"] = [_bounded(a, 256) for a in args][:64]
    else:
        url = _bounded(record.get("url", ""), 512)
        if not re.match(r"^https?://", url):
            raise McpServerError("MCP_BAD_INPUT",
                                 "an http server needs an http(s) url")
        out["url"] = url
    env = record.get("env") or {}
    if isinstance(env, dict):
        refs: Dict[str, str] = {}
        for key, value in list(env.items())[:64]:
            ref = _bounded(value, 256)
            if not _REF_RE.match(ref):
                raise McpServerError(
                    "SECRET_WRITE_REFUSED",
                    f"env {key!r} must be a vault:/env:/file: reference, never "
                    "a raw secret value")
            refs[_bounded(key, 128)] = ref
        if refs:
            out["env"] = refs
    caps = record.get("capabilities", record.get("allowed_tools"))
    if caps is not None:
        if isinstance(caps, str):
            caps = [caps]
        if not isinstance(caps, (list, tuple)):
            raise McpServerError("MCP_BAD_INPUT",
                                 "capabilities must be a list of tool names")
        clean_caps: List[str] = []
        for item in list(caps)[:MAX_CAPABILITIES]:
            name = _bounded(item, 128)
            if not name or not _CAP_RE.match(name):
                raise McpServerError(
                    "MCP_BAD_INPUT",
                    "capabilities entries must be tool names matching "
                    "^[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,127}$")
            clean_caps.append(name)
        out["capabilities"] = clean_caps
    # ``enabled`` is part of the persisted record, not a transient request
    # field: carry it through validation so a create-with-enabled persists and
    # an edit never silently disables an enabled server (D-2).
    out["enabled"] = bool(record.get("enabled", False))
    return out


def _invocable(rec: Dict[str, Any]) -> bool:
    """True when the record passes the shared capability/allowlist gate."""
    return MC.is_invocable(rec)


def _public(rec: Dict[str, Any]) -> Dict[str, Any]:
    ready = _invocable(rec)
    return {
        "name": rec.get("name"),
        "transport": rec.get("transport", "stdio"),
        "auth": rec.get("auth", "none"),
        "command": rec.get("command"),
        "args": list(rec.get("args") or []),
        "url": rec.get("url"),
        "env": dict(rec.get("env") or {}),
        "capabilities": list(rec.get("capabilities") or []),
        "enabled": bool(rec.get("enabled", False)),
        "connected": False,
        "invocable": ready,
        "invocation_code": INVOKE_READY_CODE if ready else INVOKE_OPERATOR_CODE,
        "status": "configured-not-connected",
        "source": "qubit-registry",
    }


def config_server_names(cfg: Any) -> List[str]:
    """Operator-declared MCP server names from the Qubit config (read-only).

    This is the compatibility inventory (the pre-registry ``mcp.servers``
    config key). PRB-01: it is a READ-ONLY source merged into the canonical
    registry view; it is never written and never a second authority.
    """
    get = (lambda *p, default=None: cfg.get(*p, default=default)) \
        if hasattr(cfg, "get") else (lambda *p, default=None: default)
    raw = get("mcp", "servers", default=[])
    names: List[str] = []
    if isinstance(raw, dict):
        names = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        names = [str(x if isinstance(x, str) else x.get("name", ""))
                 for x in raw if isinstance(x, (str, dict))]
    return sorted(n for n in names if n)


def list_servers(cfg: Any) -> Dict[str, Any]:
    """Configured MCP servers (canonical registry + read-only config names).

    PRB-01: this is the ONE MCP read authority. Dashboard GET, CLI
    ``qubit mcp status``, voice ``mcp``, and ``integration_status.mcp_status``
    all resolve here; ``config_server_names`` is a read-only inventory that is
    merged so a pre-existing operator declaration stays visible.
    """
    data = _load(cfg)
    rows: List[Dict[str, Any]] = []
    seen = set()
    for name, rec in sorted(data["servers"].items()):
        if not isinstance(rec, dict):
            continue
        rows.append(_public(rec))
        seen.add(name)
    for name in config_server_names(cfg):
        if name not in seen:
            rows.append({"name": name, "transport": "stdio",
                         "auth": "none", "command": None, "args": [],
                         "url": None, "env": {}, "enabled": False,
                         "connected": False, "invocable": False,
                         "invocation_code": INVOKE_OPERATOR_CODE,
                         "status": "configured-not-connected",
                         "source": "qubit-config"})
            seen.add(name)
    return {"schema": SCHEMA, "servers": rows, "total": len(rows)}


def sanitize_catalog_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """De-brand one MCP catalog entry at the served boundary (overlay).

    The catalog is authored by the vendored ``qubit_runtime/optional-mcps/
    **/manifest.yaml`` files, which are the provenance record and stay
    byte-faithful. This normalizes only the *visible* prose fields
    (``name`` / ``description`` / ``post_install``) for the dashboard, so the
    MCP page never renders a legacy vendor name as product copy.

    The real third-party install source (``source`` / ``install_url``) is
    retained verbatim — it is genuine provenance and must never be fabricated
    into a Qubit URL. The dashboard presents those links as third-party with
    neutral link text (``frontend/qubit-web/src/pages/McpPage.tsx``).

    NOTE (rebase debt): the served catalog comes from
    ``qubit_compat.h_mcp_catalog_live`` (not owned here); that handler should
    route its entries through this normalizer. Until then the MCP page applies
    the equivalent de-brand at the React render boundary.
    """
    from .qubit_plugins import _rebrand_product

    out = dict(entry)
    for key in ("name", "description", "post_install"):
        value = out.get(key)
        if isinstance(value, str):
            out[key] = _rebrand_product(value)
    return out


def upsert(cfg: Any, name: str, fields: Dict[str, Any],
           confirmed: bool = False) -> Dict[str, Any]:
    clean = _check_name(name)
    data = _load(cfg)
    servers = data["servers"]
    if clean not in servers and len(servers) >= MAX_SERVERS:
        raise McpServerError("MCP_LIMIT", f"server limit ({MAX_SERVERS}) reached")
    existing = servers.get(clean) if isinstance(servers.get(clean), dict) else {}
    merged = {**existing, **fields, "name": clean}
    merged["enabled"] = bool(fields.get("enabled", existing.get("enabled",
                                                               False)))
    merged.pop("confirm", None)
    clean_record = _validate(merged)
    clean_record["name"] = clean
    clean_record["updated_at"] = time.time()
    servers[clean] = clean_record
    _save(cfg, data)
    return {"state": "OK", "server": _public(clean_record)}


def delete(cfg: Any, name: str) -> Dict[str, Any]:
    clean = _check_name(name)
    data = _load(cfg)
    existed = clean in data["servers"]
    data["servers"].pop(clean, None)
    if existed:
        _save(cfg, data)
    return {"state": "OK", "name": clean, "removed": existed}


def set_enabled(cfg: Any, name: str, enabled: bool) -> Dict[str, Any]:
    clean = _check_name(name)
    data = _load(cfg)
    rec = data["servers"].get(clean)
    if not isinstance(rec, dict):
        raise McpServerError("MCP_NOT_FOUND", f"no registered server {clean!r}")
    rec["enabled"] = bool(enabled)
    rec["updated_at"] = time.time()
    _save(cfg, data)
    return {"state": "OK", "server": _public(rec)}


def _redact_probe(text: object) -> str:
    """Redact a probe/error string before it can reach a served response.

    B4-05: the dashboard MCP test body echoes probe failures verbatim, and a
    connection error can embed an ``Authorization`` header value. Delegate to
    the vendored ``redact_mcp_probe_text`` (the reference's own redactor, which
    masks recognized credential header fields + bare auth schemes and then runs
    the generic secret redactor); fall back to the overlay redactor if the fork
    is unavailable. Never raises — an unexpected failure fails closed.
    """
    raw = "" if text is None else str(text)
    if not raw:
        return raw
    try:
        from . import qubit_runtime as QR

        QR.ensure_path()
        from qubit_cli.mcp_config import redact_mcp_probe_text  # type: ignore

        return str(redact_mcp_probe_text(raw))
    except Exception:  # noqa: BLE001 - fail closed, never raise
        try:
            from .redaction import redact_text

            return str(redact_text(raw))
        except Exception:  # noqa: BLE001
            return "«redacted»"


def _probe(cfg: Any, rec: Dict[str, Any], *, timeout_s: Optional[float] = None,
           requested_by: str = "cli:user") -> Dict[str, Any]:
    """Real ``tools/list`` probe through the brokered client.

    Returns a normalized result and never raises a client error: an unreachable
    server is a typed failure (:data:`MC.McpClientError` code), never a
    fabricated tool list. The record must already pass :func:`mcp_client.check_ready`
    (the caller enforces it); this is a read-only discovery.
    """
    if timeout_s is None:
        timeout_s = _cfg_timeout(cfg, "probe_timeout_s", PROBE_TIMEOUT_S)
    try:
        out = MC.discover_tools(cfg, rec, timeout_s=timeout_s,
                                requested_by=requested_by)
    except MC.McpClientError as exc:
        return {"connected": False, "tools": [], "code": exc.code,
                "state": "ERROR",
                "errors": [_redact_probe(f"{exc.code}: {exc.detail}")],
                "detail": _redact_probe(exc.detail)}
    tools = list(out.get("names") or [])
    return {"connected": True, "tools": tools, "code": DISCOVER_PROBE_CODE,
            "state": "OK", "errors": [],
            "detail": f"live tools/list returned {len(tools)} tool(s)"}


def _probeable(rec: Dict[str, Any]) -> bool:
    """True when the record is enabled + runnable enough for a real probe."""
    if not bool(rec.get("enabled")) or not MC.server_ready(rec):
        return False
    try:
        MC.check_ready(rec)
        return True
    except MC.McpClientError:
        return False


def _client_method(cfg: Any, rec: Dict[str, Any], op: str, params: Any, *,
                   timeout_s: Optional[float],
                   requested_by: str) -> Dict[str, Any]:
    """Dispatch one sanctioned method to its explicit brokered-client wrapper."""
    payload = params if isinstance(params, dict) else {}
    if op == MC.DISCOVERY_METHOD:
        return MC.discover_tools(cfg, rec, timeout_s=timeout_s,
                                 requested_by=requested_by)
    if op == MC.RESOURCE_LIST_METHOD:
        return MC.list_resources(cfg, rec, timeout_s=timeout_s,
                                 requested_by=requested_by)
    if op == MC.RESOURCE_READ_METHOD:
        return MC.read_resource(cfg, rec, str(payload.get("uri") or ""),
                                timeout_s=timeout_s,
                                requested_by=requested_by)
    if op == MC.PROMPT_LIST_METHOD:
        return MC.list_prompts(cfg, rec, timeout_s=timeout_s,
                               requested_by=requested_by)
    if op == MC.RESOURCE_TEMPLATES_METHOD:
        return MC.list_resource_templates(cfg, rec, timeout_s=timeout_s,
                                          requested_by=requested_by)
    if op == MC.PROMPT_GET_METHOD:
        args = payload.get("arguments")
        return MC.get_prompt(cfg, rec, str(payload.get("name") or ""),
                             arguments=args if isinstance(args, dict) else None,
                             timeout_s=timeout_s, requested_by=requested_by)
    # Unreachable: invoke_method validates op against OPERATION_METHODS first.
    raise McpServerError("MCP_BAD_INPUT",
                         f"method {op!r} is not a sanctioned client method")


def test(cfg: Any, name: str, *, probe: bool = True,
         timeout_s: Optional[float] = None) -> Dict[str, Any]:
    """Validate a recorded server, then verify it live with a real ``tools/list``.

    The local config is validated first (a typed ``MCP_BAD_INPUT`` record is not
    a success). When the server is enabled and runnable, ``probe`` (default
    True) opens the brokered stdio/http connection and asks the live server for
    its ``tools/list``; the returned ``tools`` are exactly what the server
    advertised, and an unreachable server is the client's typed error
    (``connected`` stays False, ``tools`` stays empty — never a fabricated
    tool). A disabled or unrunnable server is inventory-only and is not probed
    (``MCP_NOT_CONNECTED``).
    """
    clean = _check_name(name)
    data = _load(cfg)
    rec = data["servers"].get(clean)
    if not isinstance(rec, dict):
        # A server declared only in the operator's config is still real
        # inventory: validate it locally rather than 404ing.
        configured = set(config_server_names(cfg))
        if clean not in configured:
            raise McpServerError("MCP_NOT_FOUND",
                                 f"no registered server {clean!r}")
        return {"state": "OK", "code": "MCP_NOT_CONNECTED", "name": clean,
                "valid": True, "ok": False, "connected": False, "tools": [],
                "errors": [],
                "probe": "local-validation", "network_probe": False,
                "detail": "config-declared server; inventory is locally valid; "
                          "Qubit does not open an unauthenticated connection"}
    errors: List[str] = []
    try:
        _validate(rec)
    except McpServerError as exc:
        errors.append(_redact_probe(exc.detail))
    out: Dict[str, Any] = {
        "state": "OK" if not errors else "INVALID",
        "code": "MCP_NOT_CONNECTED",
        "name": clean,
        "valid": not errors,
        # Honest success flag: only a probe that actually connected is a
        # success. The dashboard keys its success toast/panel on ``ok``; a
        # locally-valid but unconnected server must not read as connected.
        "ok": False,
        "connected": False,
        "tools": [],
        "errors": errors,
        "probe": "local-validation",
        "network_probe": False,
        "detail": "config is recorded and locally valid; an unauthenticated MCP "
                  "connection is not opened",
    }
    if errors or not probe or not _probeable(rec):
        return out
    if _estop(cfg):
        out.update({
            "state": "BLOCKED",
            "code": INVOKE_ESTOP_CODE,
            "detail": "emergency stop is latched; the live tools/list probe was "
                      "not attempted",
        })
        return out
    result = _probe(cfg, rec, timeout_s=timeout_s)
    out.update({
        "ok": bool(result["connected"]),
        "connected": bool(result["connected"]),
        "tools": list(result["tools"]),
        "code": str(result["code"]),
        "state": "OK" if result["connected"] else "UNREACHABLE",
        "probe": "tools/list (live)",
        "network_probe": True,
        "detail": str(result["detail"]),
    })
    if result["errors"]:
        out["errors"] = errors + [str(e) for e in result["errors"]]
    return out


def discover(cfg: Any, name: str, *, persist: bool = False,
             confirmed: bool = False, confirmation_id: str = "",
             requested_by: str = "cli:user",
             timeout_s: Optional[float] = None) -> Dict[str, Any]:
    """Real ``tools/list`` discovery through the brokered MCP client.

    Runs the same e-stop + shared-journal confirmation gate as a tool call. The
    server must be enabled and runnable. ``persist=True`` records the live
    server's tool names as the explicit ``capabilities`` allowlist (an
    operator-confirmed, audited act); the default only reports them. An
    unreachable server raises the client's typed :class:`McpServerError` code —
    no tool is ever invented.
    """
    clean = _check_name(name)
    _authorize(cfg, "mcp.discover", confirmed=confirmed,
               confirmation_id=confirmation_id, requested_by=requested_by)
    rec = _stored_record(cfg, clean)
    if not isinstance(rec, dict):
        raise McpServerError(INVOKE_NOT_FOUND_CODE,
                             f"no registered server {clean!r}")
    try:
        MC.check_ready(rec)
    except MC.McpClientError as exc:
        _audit("discover.denied", requested_by,
               {"name": clean, "code": exc.code})
        raise McpServerError(exc.code, exc.detail) from exc
    result = _probe(cfg, rec, timeout_s=timeout_s, requested_by=requested_by)
    if not result["connected"]:
        _audit("discover.denied", requested_by,
               {"name": clean, "code": str(result["code"])})
        raise McpServerError(str(result["code"]), str(result["detail"]))
    tools = [t for t in result["tools"] if _CAP_RE.match(str(t))]
    tools = tools[:MAX_CAPABILITIES]
    if persist:
        data = _load(cfg)
        stored = data["servers"].get(clean)
        if isinstance(stored, dict):
            stored["capabilities"] = tools
            stored["updated_at"] = time.time()
            _save(cfg, data)
    _audit("discover", requested_by,
           {"name": clean, "tools": str(len(tools)), "persisted": str(persist)})
    row = next((s for s in list_servers(cfg).get("servers", [])
                if s.get("name") == clean), {})
    return {
        "state": "OK",
        "schema": DISCOVER_SCHEMA,
        "name": clean,
        "transport": str(rec.get("transport") or ""),
        "connected": True,
        "invocable": bool(row.get("invocable")),
        "tools": tools,
        "tool_count": len(tools),
        "persisted": bool(persist),
        "code": DISCOVER_PROBE_CODE,
        "requested_by": _bounded(requested_by, 64),
        "authority": ("voicebridge.mcp_client.discover_tools (brokered MCP "
                      "client; e-stop + shared confirmation)"),
        "detail": f"live tools/list returned {len(tools)} tool(s)",
    }


def set_auth_ref(cfg: Any, name: str, ref: str) -> Dict[str, Any]:
    """Record a credential REFERENCE for a server (never a raw secret).

    The reference is stored under ``env.MCP_AUTH`` and surfaces verbatim
    (reference only) through :func:`list_servers`, so the write and the read
    round-trip. The canonical client resolves ``env:``/``file:``/``oauth:``
    references; ``vault:`` is carried honestly but has no Qubit-side resolver
    (``voicebridge.mcp_client._resolve_ref``), so the connect fails closed
    rather than spoofing a credential. ``oauth:`` is never accepted here —
    it is written only by the approved-flow bridge (``qubit_compat``).
    """
    clean = _check_name(name)
    data = _load(cfg)
    rec = data["servers"].get(clean)
    if not isinstance(rec, dict):
        raise McpServerError("MCP_NOT_FOUND", f"no registered server {clean!r}")
    value = _bounded(ref, 256)
    if not _REF_RE.match(value):
        raise McpServerError("SECRET_WRITE_REFUSED",
                             "auth accepts a vault:/env:/file: reference only, "
                             "never a raw token")
    env = dict(rec.get("env") or {})
    env["MCP_AUTH"] = value
    rec["env"] = env
    rec["auth"] = "header"
    rec["updated_at"] = time.time()
    _save(cfg, data)
    return {"state": "OK", "name": clean, "reference": value,
            "reference_set": True, "auth": "header"}


def auth_status(cfg: Any, name: str) -> Dict[str, Any]:
    """Read-only credential-reference status for one server (never a value).

    Resolves through the canonical registry so the read matches the write. The
    ``resolvable`` flag is honest: ``env:``/``file:`` resolve at connect time;
    ``vault:``/``ref:`` are carried but have no Qubit-side resolver.
    """
    clean = _check_name(name)
    row = next((s for s in list_servers(cfg).get("servers", [])
                if s.get("name") == clean), None)
    if row is None:
        raise McpServerError(INVOKE_NOT_FOUND_CODE,
                             f"no registered server {clean!r}")
    ref = str((row.get("env") or {}).get("MCP_AUTH", "") or "")
    resolvable = False
    if ref:
        try:
            from . import env_store as ES

            resolvable = ES.is_resolvable_reference(ref)
        except Exception:  # noqa: BLE001 - status read never fails hard
            resolvable = False
    return {"state": "OK", "name": clean, "auth": row.get("auth", "none"),
            "reference_set": bool(ref), "reference": ref or None,
            "resolvable": resolvable}


# ---------------------------------------------------------------------------
# Client invocation (fail-closed)
#
# ``invoke`` is the ONE entry point for a tool call and ``invoke_method`` for the
# sanctioned server-level methods (``tools/list`` discovery, resources/prompts).
# Both run the shared durable confirmation + audit gate first; a server that is
# not enabled/runnable (or, for a tool call, does not allowlist the tool)
# returns the typed ``MCP_CLIENT_INVOCATION_OPERATOR_REQUIRED`` refusal naming
# the exact prerequisite, and nothing is connected or spawned. Only when the
# gate passes is the brokered client transport opened (bounded timeout; stdio
# process group killed on timeout; output redacted). No credential value is read,
# returned, or logged (only ``env:``/``file:``/``vault:`` references are ever
# present in the registry).
# ---------------------------------------------------------------------------
def _approval_journal(cfg: Any):
    from .approvals import ApprovalJournal

    return ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                           default_ttl_s=120.0)


def _consume_journal_approval(cfg: Any, capability: str,
                              confirmation_id: str) -> bool:
    from .approvals import ABORTED, COMPLETE, DENIED, EXECUTING, IN_PROGRESS

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
        if item.expires_at <= time.time():
            journal.abort(item.id)
            return False
        item = journal.respond(item.id, "approve")
    if item.state == EXECUTING:
        journal.finish(item.id)
        return True
    if item.state in (COMPLETE, ABORTED, DENIED):
        return False
    return False


def _authorize(cfg: Any, capability: str, *, confirmed: bool,
               confirmation_id: str, requested_by: str) -> None:
    """One gate: e-stop -> shared durable journal confirmation, fail closed."""
    if _estop(cfg):
        raise McpServerError(INVOKE_ESTOP_CODE, "emergency stop is latched")
    if confirmation_id:
        if not _consume_journal_approval(cfg, capability, confirmation_id):
            raise McpServerError(
                INVOKE_CONFIRM_CODE,
                "confirmation id is unknown, expired, already used, or for "
                "another capability")
        return
    if not confirmed:
        raise McpServerError(
            INVOKE_CONFIRM_CODE,
            f"{capability} requires explicit --confirm or a human approval id")
    try:
        journal = _approval_journal(cfg)
        actor = _bounded(requested_by, 64)
        item = journal.create(capability, f"{capability} via {actor}",
                              ttl_s=120.0, session="mcp", actor=actor)
        journal.respond(item.id, "approve")
        journal.finish(item.id)
    except Exception as exc:  # noqa: BLE001 - fail closed at the boundary
        raise McpServerError(
            INVOKE_CONFIRM_CODE,
            "shared confirmation unavailable: " + _bounded(exc, 120)) from exc
    _audit("confirm", requested_by,
           {"capability": capability, "confirmation_id": item.id})


def invocation_status(cfg: Any, name: str) -> Dict[str, Any]:
    """Read-only invocation disposition for one configured server.

    Resolves the server through the ONE authority (:func:`list_servers`) and
    reports the typed disposition. No connection is opened and no process is
    spawned. A server that is explicitly enabled, runnable, and carries a
    non-empty tool allowlist is reported ``INVOCABLE``; every other server
    keeps the typed, fail-closed ``OPERATOR_ONLY`` refusal.
    """
    clean = _check_name(name)
    row = next((s for s in list_servers(cfg).get("servers", [])
                if s.get("name") == clean), None)
    if row is None:
        raise McpServerError(INVOKE_NOT_FOUND_CODE,
                             f"no configured server {clean!r}")
    if bool(row.get("invocable")):
        return {
            "state": "INVOCABLE",
            "schema": INVOKE_SCHEMA,
            "name": clean,
            "transport": row.get("transport"),
            "enabled": bool(row.get("enabled")),
            "connected": False,
            "invocable": True,
            "code": INVOKE_READY_CODE,
            "reason": INVOKE_READY_REASON,
            "operator_prerequisite": "",
            "qubit_counterpart": INVOKE_COUNTERPART,
            "authority": "voicebridge.mcp_servers.list_servers",
        }
    blocked_by = ""
    try:
        MC.check_invocable(row, "")
    except MC.McpClientError as exc:
        blocked_by = exc.code
    return {
        "state": "OPERATOR_ONLY",
        "schema": INVOKE_SCHEMA,
        "name": clean,
        "transport": row.get("transport"),
        "enabled": bool(row.get("enabled")),
        "connected": False,
        "invocable": False,
        "code": INVOKE_OPERATOR_CODE,
        "reason": INVOKE_REASON,
        "blocked_by": blocked_by,
        "operator_prerequisite": INVOKE_PREREQUISITE,
        "qubit_counterpart": INVOKE_COUNTERPART,
        "authority": "voicebridge.mcp_servers.list_servers",
    }


def _stored_record(cfg: Any, name: str) -> Optional[Dict[str, Any]]:
    rec = _load(cfg).get("servers", {}).get(name)
    return rec if isinstance(rec, dict) else None


def _capability_disposition(name: str, method: str, code: str,
                            detail: str) -> Dict[str, Any]:
    """Honest typed disposition for a capability read that cannot connect."""
    return {
        "state": "OPERATOR_ONLY",
        "schema": INVOKE_SCHEMA,
        "name": name,
        "operation": method,
        "connected": False,
        "invocable": False,
        "code": code,
        "result": None,
        "names": [],
        "count": 0,
        "operator_prerequisite": DISCOVER_PREREQUISITE,
        "qubit_counterpart": INVOKE_COUNTERPART,
        "authority": "voicebridge.mcp_servers.list_servers",
        "detail": detail,
    }


def list_capability(cfg: Any, name: str, capability: str, *,
                    timeout_s: Optional[float] = None,
                    requested_by: str = "dashboard:user") -> Dict[str, Any]:
    """Read-only drain of one server-level list method (resources/prompts/templates).

    Resolves the server through the ONE registry authority, then drains the
    real list through the brokered :mod:`voicebridge.mcp_client` transport.
    This is a read, so it needs no confirmation; the shared e-stop and the
    method gate (supported transport + enabled + runnable) still apply. A
    server that is inventory-only returns the honest typed OPERATOR_ONLY
    disposition naming the prerequisite — never a fabricated empty list.
    Unknown servers raise ``MCP_NOT_FOUND``; a transport failure raises the
    client's typed code.
    """
    clean = _check_name(name)
    method = CAPABILITY_METHODS.get(str(capability))
    if method is None:
        raise McpServerError("MCP_BAD_INPUT",
                             f"capability {capability!r} is not a supported read")
    rec = _stored_record(cfg, clean)
    if not isinstance(rec, dict):
        # A server declared only in the operator config is real inventory but
        # records no runnable transport, so it cannot be drained.
        if clean not in set(config_server_names(cfg)):
            raise McpServerError(INVOKE_NOT_FOUND_CODE,
                                 f"no registered server {clean!r}")
        return _capability_disposition(
            clean, method, "MCP_NOT_CONNECTED",
            "config-declared server records no runnable transport; Qubit opens "
            "no MCP connection for it")
    if _estop(cfg):
        return _capability_disposition(
            clean, method, INVOKE_ESTOP_CODE, "emergency stop is latched")
    try:
        MC.check_method(rec, method)
    except MC.McpClientError as exc:
        return _capability_disposition(clean, method, exc.code, exc.detail)
    if timeout_s is None:
        timeout_s = _cfg_timeout(cfg, "timeout_s", MC.DEFAULT_TIMEOUT_S)
    try:
        out = _client_method(cfg, rec, method, {}, timeout_s=timeout_s,
                             requested_by=requested_by)
    except MC.McpClientError as exc:
        _audit("list.denied", requested_by,
               {"name": clean, "method": method, "code": exc.code})
        raise McpServerError(exc.code, exc.detail) from exc
    result: Dict[str, Any] = {
        "state": "OK",
        "schema": INVOKE_SCHEMA,
        "name": clean,
        "operation": method,
        "transport": str(rec.get("transport") or ""),
        "connected": True,
        "invocable": True,
        "result": out.get("result"),
        "text": out.get("text"),
        "names": out.get("names"),
        "count": out.get("count"),
        "duration_ms": out.get("duration_ms"),
        "requested_by": _bounded(requested_by, 64),
        "redacted": True,
        "authority": ("voicebridge.mcp_client.call_method (brokered MCP client; "
                      "read-only list)"),
        "detail": "real MCP client list; output redacted",
    }
    _audit("list", requested_by,
           {"name": clean, "method": method,
            "transport": result["transport"], "state": "OK"})
    return result


def invoke(cfg: Any, name: str, *, tool: str = "",
           args: Optional[Dict[str, Any]] = None, confirmed: bool = False,
           confirmation_id: str = "",
           requested_by: str = "cli:user",
           timeout_s: Optional[float] = None) -> Dict[str, Any]:
    """Canonical MCP client-invocation entry point (CLI, voice, gateway).

    Order: emergency stop -> shared-journal confirmation -> the server exists
    in the canonical registry -> the shared capability/allowlist gate. A server
    that is explicitly enabled, runnable, and allowlists the requested tool runs
    through the brokered :mod:`voicebridge.mcp_client` transport (bounded
    timeout; stdio process group killed on timeout; output redacted). Any other
    server returns the typed, fail-closed ``OPERATOR_ONLY`` refusal — nothing is
    connected, spawned, or sent, and no credential value is read or returned.

    Transport failures raise :class:`McpServerError` carrying the client's typed
    code (``MCP_CONNECT_FAILED`` / ``MCP_TIMEOUT`` / ``MCP_PROTOCOL_ERROR`` /
    ``MCP_TOOL_NOT_ALLOWED`` / ``MCP_SECRET_UNRESOLVED``).
    """
    _authorize(cfg, "mcp.invoke", confirmed=confirmed,
               confirmation_id=confirmation_id, requested_by=requested_by)
    clean = _check_name(name)
    rec = _stored_record(cfg, clean)
    if isinstance(rec, dict) and _invocable(rec):
        if timeout_s is None:
            timeout_s = _cfg_timeout(cfg, "timeout_s", MC.DEFAULT_TIMEOUT_S)
        try:
            out = MC.call_server(cfg, rec, tool, args=args, timeout_s=timeout_s,
                                 requested_by=requested_by)
        except MC.McpClientError as exc:
            _audit("invoke.denied", requested_by,
                   {"name": clean, "tool": _bounded(tool, 128),
                    "code": exc.code})
            raise McpServerError(exc.code, exc.detail) from exc
        state = str(out.get("state", "OK"))
        result = {
            "state": state,
            "schema": INVOKE_SCHEMA,
            "name": clean,
            "tool": _bounded(tool, 128),
            "transport": str(rec.get("transport") or ""),
            "connected": True,
            "invocable": True,
            "is_error": bool(out.get("is_error")),
            "result": out.get("result"),
            "text": out.get("text"),
            "code": "" if state == "OK" else MC.TOOL_ERROR_CODE,
            "duration_ms": out.get("duration_ms"),
            "requested_by": _bounded(requested_by, 64),
            "redacted": True,
            "authority": ("voicebridge.mcp_client.call_server (brokered MCP "
                          "client transport; e-stop + shared confirmation)"),
            "detail": "real MCP client invocation; output redacted",
        }
        _audit("invoke", requested_by,
               {"name": clean, "tool": _bounded(tool, 128),
                "transport": result["transport"], "state": state})
        return result
    out = invocation_status(cfg, clean)
    _audit("invoke", requested_by,
           {"name": out["name"], "tool": _bounded(tool, 128),
            "transport": str(out.get("transport") or "")})
    out = dict(out)
    out.update({"tool": _bounded(tool, 128),
                "args_provided": bool(args),
                "requested_by": _bounded(requested_by, 64),
                "detail": "no MCP connection was opened and no process was "
                          "spawned; this is a policy refusal, not a tool call"})
    return out


def invoke_cli(cfg: Any, name: str, *, tool: str = "",
               args: Optional[Dict[str, Any]] = None, confirmed: bool = False,
               confirmation_id: str = "",
               timeout_s: Optional[float] = None) -> Dict[str, Any]:
    """CLI entry point: :func:`invoke` attributed to ``cli:user``."""
    return invoke(cfg, name, tool=tool, args=args, confirmed=confirmed,
                  confirmation_id=confirmation_id, requested_by="cli:user",
                  timeout_s=timeout_s)


def invoke_method(cfg: Any, name: str, method: str, *,
                  params: Optional[Dict[str, Any]] = None,
                  confirmed: bool = False, confirmation_id: str = "",
                  requested_by: str = "cli:user",
                  timeout_s: Optional[float] = None) -> Dict[str, Any]:
    """Broker one sanctioned server-level MCP method (resources/prompts).

    Runs the shared e-stop + confirmation gate, then the method gate (supported
    transport + enabled + runnable). ``tools/list`` discovery, the
    ``resources`` reads, and the ``prompts`` reads go through here. A server
    that is not registered is ``MCP_NOT_FOUND``; a disabled/unrunnable server
    returns the typed OPERATOR_ONLY disposition naming the prerequisite.
    Transport failures raise :class:`McpServerError` with the client's typed
    code.
    """
    clean = _check_name(name)
    op = str(method or "").strip()
    if op not in OPERATION_METHODS:
        raise McpServerError("MCP_BAD_INPUT",
                             f"method {op!r} is not a sanctioned client method")
    _authorize(cfg, f"mcp.{op}", confirmed=confirmed,
               confirmation_id=confirmation_id, requested_by=requested_by)
    rec = _stored_record(cfg, clean)
    if not isinstance(rec, dict):
        raise McpServerError(INVOKE_NOT_FOUND_CODE,
                             f"no registered server {clean!r}")
    try:
        MC.check_method(rec, op)
    except MC.McpClientError as exc:
        out = invocation_status(cfg, clean)
        out = dict(out)
        out.update({"operation": op, "operator_prerequisite":
                    f"{DISCOVER_PREREQUISITE} (blocked by {exc.code})"})
        _audit("method.denied", requested_by,
               {"name": clean, "method": op, "code": exc.code})
        return out
    if timeout_s is None:
        timeout_s = _cfg_timeout(cfg, "timeout_s", MC.DEFAULT_TIMEOUT_S)
    try:
        out = _client_method(cfg, rec, op, params, timeout_s=timeout_s,
                             requested_by=requested_by)
    except MC.McpClientError as exc:
        _audit("method.denied", requested_by,
               {"name": clean, "method": op, "code": exc.code})
        raise McpServerError(exc.code, exc.detail) from exc
    state = str(out.get("state", "OK"))
    result: Dict[str, Any] = {
        "state": state,
        "schema": INVOKE_SCHEMA,
        "name": clean,
        "operation": op,
        "transport": str(rec.get("transport") or ""),
        "connected": True,
        "invocable": True,
        "is_error": bool(out.get("is_error")),
        "result": out.get("result"),
        "text": out.get("text"),
        "names": out.get("names"),
        "count": out.get("count"),
        "code": "" if state == "OK" else MC.PROTOCOL_CODE,
        "duration_ms": out.get("duration_ms"),
        "requested_by": _bounded(requested_by, 64),
        "redacted": True,
        "authority": ("voicebridge.mcp_client.call_method (brokered MCP client; "
                      "e-stop + shared confirmation)"),
        "detail": "real MCP client method invocation; output redacted",
    }
    _audit("method", requested_by,
           {"name": clean, "method": op,
            "transport": result["transport"], "state": state})
    return result


def invoke_voice(cfg: Any, name: str, *, tool: str = "",
                 args: Optional[Dict[str, Any]] = None, confirmed: bool = False,
                 confirmation_id: str = "",
                 timeout_s: Optional[float] = None) -> Dict[str, Any]:
    """Voice entry point: :func:`invoke` attributed to ``voice:user``."""
    return invoke(cfg, name, tool=tool, args=args, confirmed=confirmed,
                  confirmation_id=confirmation_id, requested_by="voice:user",
                  timeout_s=timeout_s)
