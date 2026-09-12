"""Qubit paired-node host: enrollment, capability inventory, dispatch gates.

Qubit opens no public listener and runs no second authority, so this module
is the code-side enrollment kernel for OpenClaw's paired node host
(``src/node-host/``, ``docs/nodes/*``): it records operator-approved node
endpoints, declares their capabilities honestly, and routes capability calls
through explicit gates.

* ``enroll`` — record a paired node (name, endpoint, declared capabilities).
  Endpoint scheme is validated (http/https only, no userinfo, no shell
  metacharacters), sizes are capped, and an explicit confirmation
  (``confirmed=True`` or a human approval id) is required before anything is
  persisted. Audit events are redacted.
* ``revoke`` — confirmation-gated removal.
* ``status``/``list_nodes``/``show`` — read-only inventory; never raises on
  missing state; secrets are redacted.
* ``capabilities`` — the node's declared capabilities with honest typed
  state: local-safe read capabilities are ``READY``; remote actuators
  (screen/audio/camera/media_playback/desktop) return typed ``UNAVAILABLE``.
  Availability is never conflated with authorization.
* ``dispatch`` — routes a capability. Only local-safe read capabilities
  execute (via the shared backend). Unknown nodes/capabilities and every
  remote/unsafe/process capability return typed refusals; a remote shell is
  never executed.

The emergency-stop latch is the same durable ``PowerControlStore`` the broker,
CLI, dashboard, voice, and runner observe (fail closed).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

SCHEMA = "qubit-node-host/1"
STORE_NAME = "nodes.json"
MAX_FILE_BYTES = 1_000_000
MAX_NODES = 128
MAX_NAME_LEN = 64
MAX_ENDPOINT_LEN = 512
MAX_CAPABILITIES = 32
MAX_CAPABILITY_LEN = 64
MAX_ARGS_BYTES = 16_000

NODE_HOST_UNAVAILABLE = "NODE_HOST_UNAVAILABLE"
NODE_ENROLL_CONFIRM_REQUIRED = "NODE_ENROLL_CONFIRM_REQUIRED"
NODE_REVOKE_CONFIRM_REQUIRED = "NODE_REVOKE_CONFIRM_REQUIRED"
NODE_UNKNOWN = "NODE_UNKNOWN"
NODE_EXISTS = "NODE_EXISTS"
NODE_LIMIT = "NODE_LIMIT"
NODE_BAD_INPUT = "NODE_BAD_INPUT"
NODE_CAPABILITY_UNAVAILABLE = "NODE_CAPABILITY_UNAVAILABLE"
NODE_DISPATCH_REFUSED = "NODE_DISPATCH_REFUSED"
NODE_ESTOP = "NODE_ESTOP"
NODE_STORE_CORRUPT = "NODE_STORE_CORRUPT"

CODES = frozenset((
    NODE_HOST_UNAVAILABLE, NODE_ENROLL_CONFIRM_REQUIRED,
    NODE_REVOKE_CONFIRM_REQUIRED, NODE_UNKNOWN, NODE_EXISTS, NODE_LIMIT,
    NODE_BAD_INPUT, NODE_CAPABILITY_UNAVAILABLE, NODE_DISPATCH_REFUSED,
    NODE_ESTOP, NODE_STORE_CORRUPT,
))

# Capability classes. Local-safe reads execute; remote actuators are not run
# by Qubit (no live second-device runtime); process/write capabilities are
# never executed by this surface.
LOCAL_READ_CAPABILITIES = (
    "device.inventory",
    "node.status",
    "presence.status",
)
REMOTE_CAPABILITIES = (
    "screen", "screen.snapshot",
    "audio", "audio.capture",
    "camera", "camera.capture",
    "media_playback", "media.playback",
    "desktop.control", "computer.act",
    "browser.cdp",
)
UNSAFE_CAPABILITIES = (
    "shell", "shell.run", "exec", "process.spawn", "file.write", "apply_patch",
)
KNOWN_CAPABILITIES = frozenset(
    LOCAL_READ_CAPABILITIES + REMOTE_CAPABILITIES + UNSAFE_CAPABILITIES)

# Only an authenticated human-facing source may enroll or revoke a node.
_OWNER_SOURCES = ("user", "cli", "dashboard", "voice")
NODE_ENROLL_CAPABILITY = "node.enroll"
NODE_REVOKE_CAPABILITY = "node.revoke"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][^\x00-\x1f\x7f]{0,63}$")
_CAP_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_ENDPOINT_SCHEMES = ("http", "https")
# Characters that only make sense when a value is handed to a shell; refused.
_SHELL_CHARS = frozenset(";|&`$<>\\\n\r\t\x00")


class NodeHostError(Exception):
    """Typed node-host failure (store integrity; never a CLI traceback)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _now() -> float:
    return time.time()


def _redact(text: Any) -> str:
    from .redaction import redact_text

    return redact_text(str(text if text is not None else ""))


def _path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / STORE_NAME


def _blank() -> Dict[str, Any]:
    return {"schema": SCHEMA, "nodes": {}}


def _load(cfg: Any) -> Dict[str, Any]:
    path = _path(cfg)
    if not path.exists():
        return _blank()
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise NodeHostError(NODE_STORE_CORRUPT, "node store too large")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except NodeHostError:
        raise
    except (OSError, ValueError) as exc:
        raise NodeHostError(NODE_STORE_CORRUPT,
                            f"node store unreadable: {exc}") from exc
    if (not isinstance(raw, dict) or raw.get("schema") != SCHEMA
            or not isinstance(raw.get("nodes"), dict)):
        raise NodeHostError(NODE_STORE_CORRUPT, "node store has an unknown schema")
    return raw


def _save(cfg: Any, state: Dict[str, Any]) -> None:
    path = _path(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{STORE_NAME}.tmp.{os.getpid()}")
        tmp.write_text(json.dumps({"schema": SCHEMA, "nodes": state["nodes"]},
                                  indent=2, sort_keys=True),
                       encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError as exc:
        raise NodeHostError(NODE_STORE_CORRUPT,
                            f"node store write failed: {exc}") from exc


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"node_host.{op}", actor=_redact(actor)[:64],
                  **{k: _redact(v)[:160] if isinstance(v, str) else v
                     for k, v in detail.items()})
    except Exception:
        pass


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": NODE_ESTOP, "operation": operation,
            "detail": "emergency stop is latched; reset it before node changes",
            "owner": "qubit-policy",
            "rollback": "node registry untouched; nothing dispatched"}


def _refusal(op: str, code: str, detail: str, *,
             state: str = "REFUSED") -> Dict[str, Any]:
    return {"state": state, "code": code, "operation": op,
            "detail": _redact(detail)[:300], "owner": "qubit-policy",
            "rollback": "nothing was persisted or dispatched; nothing to "
                        "roll back"}


def _not_found(node_id: Any) -> Dict[str, Any]:
    return {"state": "UNKNOWN", "code": NODE_UNKNOWN,
            "node_id": _redact(node_id)[:64],
            "detail": "no such enrolled node",
            "owner": "qubit-policy",
            "rollback": "node registry untouched; nothing dispatched"}


# ------------------------------------------------------------- validation
def _norm_id(node_id: Any) -> str:
    clean = str(node_id or "").strip().lower()
    return clean if _ID_RE.match(clean) else ""


def _check_name(name: Any) -> str:
    text = str(name or "").strip()
    if not text or len(text) > MAX_NAME_LEN or not _NAME_RE.match(text):
        raise NodeHostError(
            NODE_BAD_INPUT,
            f"name must be 1..{MAX_NAME_LEN} printable chars starting "
            "alphanumeric")
    return text


def check_endpoint(endpoint: Any) -> str:
    """Endpoint policy: http(s) with a host, no userinfo, no shell chars."""
    raw = str(endpoint or "").strip()
    if not raw or len(raw) > MAX_ENDPOINT_LEN:
        raise NodeHostError(NODE_BAD_INPUT,
                            f"endpoint is required (<= {MAX_ENDPOINT_LEN} chars)")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in raw):
        raise NodeHostError(NODE_BAD_INPUT,
                            "endpoint contains control characters")
    if any(ch in _SHELL_CHARS for ch in raw):
        raise NodeHostError(NODE_BAD_INPUT,
                            "endpoint contains shell metacharacters")
    try:
        parts = urlparse(raw)
    except ValueError as exc:
        raise NodeHostError(NODE_BAD_INPUT,
                            f"endpoint is unparseable: {exc}") from exc
    if parts.scheme not in _ENDPOINT_SCHEMES or not parts.hostname:
        raise NodeHostError(NODE_BAD_INPUT,
                            "endpoint must be an http(s) URL with a host")
    if parts.username or parts.password:
        raise NodeHostError(NODE_BAD_INPUT,
                            "credentials in endpoints are never accepted")
    return raw


def _check_capabilities(caps: Any) -> List[str]:
    if caps is None:
        return []
    if isinstance(caps, str):
        caps = [c for c in caps.split(",") if c.strip()]
    if not isinstance(caps, (list, tuple, set, frozenset)):
        raise NodeHostError(NODE_BAD_INPUT, "capabilities must be a list")
    out: List[str] = []
    for cap in caps:
        text = str(cap or "").strip().lower()
        if not text or len(text) > MAX_CAPABILITY_LEN or not _CAP_RE.match(text):
            raise NodeHostError(NODE_BAD_INPUT, f"bad capability {cap!r}")
        if text not in out:
            out.append(text)
    if len(out) > MAX_CAPABILITIES:
        raise NodeHostError(NODE_BAD_INPUT,
                            f"too many capabilities (max {MAX_CAPABILITIES})")
    return sorted(out)


def _check_args(args: Any) -> Dict[str, Any]:
    if args is None:
        return {}
    if not isinstance(args, dict):
        raise NodeHostError(NODE_BAD_INPUT, "args must be a mapping")
    try:
        raw = json.dumps(args, default=repr)
    except (TypeError, ValueError) as exc:
        raise NodeHostError(NODE_BAD_INPUT, f"args not serializable: {exc}") from exc
    if len(raw.encode("utf-8")) > MAX_ARGS_BYTES:
        raise NodeHostError(NODE_BAD_INPUT, "args exceed the size cap")
    return args


def _valid_approval(cfg: Any, confirmation_id: str, capability: str) -> bool:
    """A durable, human-approved journal entry can stand in for --confirm."""
    cid = str(confirmation_id or "").strip()
    if not cid:
        return False
    try:
        from .approvals import ApprovalJournal, COMPLETE, EXECUTING

        journal = ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl")
        item = journal.get(cid)
        if item is None or item.decision != "approve":
            return False
        if item.state not in (EXECUTING, COMPLETE):
            return False
        return item.capability in (capability, "node.host")
    except Exception:
        return False


def _confirm_gate(cfg: Any, *, confirmed: bool, confirmation_id: str,
                  capability: str, code: str, op: str) -> Optional[Dict[str, Any]]:
    if confirmed or _valid_approval(cfg, confirmation_id, capability):
        return None
    return _refusal(op, code,
                    f"{op} requires an explicit human confirmation; nothing "
                    "was persisted")


def _public(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": rec.get("id", ""),
            "name": _redact(rec.get("name", ""))[:MAX_NAME_LEN],
            "endpoint": _redact(rec.get("endpoint", ""))[:MAX_ENDPOINT_LEN],
            "capabilities": [_redact(c)[:MAX_CAPABILITY_LEN]
                             for c in rec.get("capabilities", [])],
            "state": rec.get("state", "ENROLLED"),
            "source": _redact(rec.get("source", ""))[:32],
            "created_at": rec.get("created_at"),
            "updated_by": _redact(rec.get("updated_by", ""))[:64],
            "last_outcome": rec.get("last_outcome", "NEVER_USED")}


def _cap_row(capability: str, *, declared: bool = True) -> Dict[str, Any]:
    if capability in LOCAL_READ_CAPABILITIES:
        return {"capability": capability, "state": "READY", "code": "",
                "declared": declared, "confirmed_required": False,
                "detail": "local-safe read; executes via the shared backend"}
    if capability in REMOTE_CAPABILITIES:
        return {"capability": capability, "state": "UNAVAILABLE",
                "code": NODE_CAPABILITY_UNAVAILABLE, "declared": declared,
                "detail": f"remote {capability} needs a live node-host "
                          "actuator that Qubit does not run",
                "owner": "qubit-policy"}
    if capability in UNSAFE_CAPABILITIES:
        return {"capability": capability, "state": "UNAVAILABLE",
                "code": NODE_CAPABILITY_UNAVAILABLE, "declared": declared,
                "detail": "process/write capabilities are never executed by "
                          "the node host",
                "owner": "qubit-policy"}
    return {"capability": capability, "state": "UNAVAILABLE",
            "code": NODE_CAPABILITY_UNAVAILABLE, "declared": declared,
            "detail": "unknown capability; nothing is fabricated",
            "owner": "qubit-policy"}


# ---------------------------------------------------------------- reads
def status(cfg: Any) -> Dict[str, Any]:
    """Read-only inventory summary; never raises on missing state."""
    try:
        state = _load(cfg)
        recs = list(state["nodes"].values())
        estop = estop_engaged(cfg)
    except Exception as exc:
        return {"state": "DEGRADED",
                "code": getattr(exc, "code", NODE_STORE_CORRUPT),
                "detail": _redact(exc)[:200], "nodes": 0, "node_ids": [],
                "estop": False, "host_state": "DEGRADED",
                "owner": "qubit-policy",
                "rollback": "read-only; nothing to roll back"}
    return {"state": "OK", "schema": SCHEMA, "nodes": len(recs),
            "enrolled": len(recs),
            "node_ids": sorted(str(r.get("id", "")) for r in recs),
            "estop": bool(estop),
            "host_state": "ESTOP" if estop else "READY",
            "supported_capabilities": list(LOCAL_READ_CAPABILITIES),
            "remote_capabilities": list(REMOTE_CAPABILITIES),
            "authority": "read-only inventory; availability is not authorization"}


def list_nodes(cfg: Any) -> Dict[str, Any]:
    """List enrolled node records (secrets redacted)."""
    try:
        state = _load(cfg)
    except NodeHostError as exc:
        return {"state": "DEGRADED", "code": exc.code,
                "detail": exc.detail[:200], "nodes": []}
    recs = sorted(state["nodes"].values(),
                  key=lambda r: r.get("created_at", 0), reverse=True)
    return {"state": "OK", "schema": SCHEMA, "nodes": [_public(r) for r in recs],
            "authority": "read-only inventory; secrets redacted"}


def show(cfg: Any, node_id: str) -> Dict[str, Any]:
    """One enrolled node record or a typed not-found."""
    try:
        state = _load(cfg)
    except NodeHostError as exc:
        return {"state": "DEGRADED", "code": exc.code,
                "detail": exc.detail[:200]}
    rec = state["nodes"].get(_norm_id(node_id))
    if rec is None:
        return _not_found(node_id)
    return {"state": "OK", "node": _public(rec)}


# --------------------------------------------------------------- mutations
def enroll(cfg: Any, *, name: str, endpoint: str, capabilities: Any = None,
           confirmation_id: str = "", confirmed: bool = False,
           source: str = "user", requested_by: str = "user") -> Dict[str, Any]:
    """Record a paired node (explicit confirmation required to persist)."""
    if estop_engaged(cfg):
        return refuse_estop("node enroll")
    if source not in _OWNER_SOURCES:
        return _refusal("enroll", NODE_DISPATCH_REFUSED,
                        "only an authenticated human source may enroll a node")
    gate = _confirm_gate(cfg, confirmed=confirmed,
                         confirmation_id=confirmation_id,
                         capability=NODE_ENROLL_CAPABILITY,
                         code=NODE_ENROLL_CONFIRM_REQUIRED, op="node enroll")
    if gate is not None:
        return gate
    try:
        clean_name = _check_name(name)
        clean_endpoint = check_endpoint(endpoint)
        caps = _check_capabilities(capabilities)
        state = _load(cfg)
    except NodeHostError as exc:
        return _refusal("enroll", exc.code, exc.detail)
    if len(state["nodes"]) >= MAX_NODES:
        return _refusal("enroll", NODE_LIMIT,
                        f"node limit reached ({MAX_NODES})")
    node_id = "node_" + secrets.token_hex(6)
    rec = {"id": node_id,
           "name": _redact(clean_name)[:MAX_NAME_LEN],
           "endpoint": _redact(clean_endpoint)[:MAX_ENDPOINT_LEN],
           "capabilities": caps,
           "state": "ENROLLED",
           "source": _redact(source)[:32],
           "created_at": _now(),
           "updated_by": _redact(requested_by)[:64],
           "last_outcome": "NEVER_USED"}
    state["nodes"][node_id] = rec
    try:
        _save(cfg, state)
    except NodeHostError as exc:
        return _refusal("enroll", exc.code, exc.detail)
    _audit("enroll", requested_by,
           {"node": node_id, "source": source, "caps": caps,
            "endpoint": clean_endpoint[:120]})
    return {"state": "OK", "node": _public(rec),
            "detail": "node enrolled; no listener opened"}


def revoke(cfg: Any, node_id: str, *, confirmation_id: str = "",
           confirmed: bool = False, source: str = "user",
           requested_by: str = "user") -> Dict[str, Any]:
    """Confirmation-gated removal of an enrolled node."""
    if estop_engaged(cfg):
        return refuse_estop("node revoke")
    if source not in _OWNER_SOURCES:
        return _refusal("revoke", NODE_DISPATCH_REFUSED,
                        "only an authenticated human source may revoke a node")
    gate = _confirm_gate(cfg, confirmed=confirmed,
                         confirmation_id=confirmation_id,
                         capability=NODE_REVOKE_CAPABILITY,
                         code=NODE_REVOKE_CONFIRM_REQUIRED, op="node revoke")
    if gate is not None:
        return gate
    try:
        state = _load(cfg)
    except NodeHostError as exc:
        return _refusal("revoke", exc.code, exc.detail)
    rec = state["nodes"].get(_norm_id(node_id))
    if rec is None:
        return _not_found(node_id)
    del state["nodes"][rec["id"]]
    try:
        _save(cfg, state)
    except NodeHostError as exc:
        return _refusal("revoke", exc.code, exc.detail)
    _audit("revoke", requested_by, {"node": rec["id"]})
    return {"state": "OK", "removed": rec["id"]}


# ------------------------------------------------------------- capabilities
def capabilities(cfg: Any, node_id: str) -> Dict[str, Any]:
    """Declared capabilities with honest typed availability states."""
    try:
        state = _load(cfg)
    except NodeHostError as exc:
        return {"state": "DEGRADED", "code": exc.code,
                "detail": exc.detail[:200], "capabilities": []}
    rec = state["nodes"].get(_norm_id(node_id))
    if rec is None:
        return _not_found(node_id)
    declared = list(rec.get("capabilities", []))
    rows: List[Dict[str, Any]] = []
    seen = set()
    for cap in declared:
        rows.append(_cap_row(cap, declared=True))
        seen.add(cap)
    for cap in LOCAL_READ_CAPABILITIES:
        if cap not in seen:
            rows.append(_cap_row(cap, declared=False))
    return {"state": "OK", "node": rec["id"], "capabilities": rows,
            "declared": declared,
            "authority": "declared capabilities only; availability is never "
                         "authorization"}


# ------------------------------------------------------------------ dispatch
def _shared_backend(cfg: Any, capability: str,
                    args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a local-safe read capability (no shell, no process spawn)."""
    if capability == "node.status":
        return status(cfg)
    if capability == "presence.status":
        return {"present": True, "detail": "local node host is reachable",
                "heartbeat": _now()}
    if capability == "device.inventory":
        from .device import machine_id

        try:
            node_count = len(_load(cfg)["nodes"])
        except NodeHostError:
            node_count = 0
        return {"machine_id": _redact(machine_id())[:128],
                "nodes": node_count,
                "estop": estop_engaged(cfg),
                "authority": "local read-only; no shell, no process spawn"}
    raise NodeHostError(NODE_CAPABILITY_UNAVAILABLE,
                        f"capability {capability!r} has no local backend")


def dispatch(cfg: Any, node_id: str, capability: str, args: Any = None, *,
             confirmed: bool = False, transport: Any = None,
             requested_by: str = "user") -> Dict[str, Any]:
    """Route a capability; only local-safe reads execute, everything else is
    a typed refusal. A remote shell is never executed.

    ``transport`` is an optional operator-injected node boundary
    ``(request) -> result`` used for remote actuators (screen/audio/camera/
    media_playback/...). Without it those capabilities stay typed UNAVAILABLE;
    with it, dispatch requires explicit confirmation (availability is never
    authorization). Qubit itself never spawns a node process.
    """
    if estop_engaged(cfg):
        return refuse_estop("node dispatch")
    try:
        state = _load(cfg)
    except NodeHostError as exc:
        return _refusal("dispatch", exc.code, exc.detail)
    rec = state["nodes"].get(_norm_id(node_id))
    if rec is None:
        return _not_found(node_id)
    cap = str(capability or "").strip().lower()
    if cap not in KNOWN_CAPABILITIES:
        return _refusal("dispatch", NODE_CAPABILITY_UNAVAILABLE,
                        f"node {rec['id']!r} declares no capability {cap!r}")
    if cap in UNSAFE_CAPABILITIES:
        return _refusal("dispatch", NODE_DISPATCH_REFUSED,
                        "process/write capabilities are never executed by the "
                        "node host (no shell)")
    try:
        clean_args = _check_args(args)
    except NodeHostError as exc:
        return _refusal("dispatch", exc.code, exc.detail)
    if cap in REMOTE_CAPABILITIES:
        if transport is None:
            return _refusal("dispatch", NODE_DISPATCH_REFUSED,
                            f"{cap} requires a live remote node-host actuator; "
                            "none is running")
        if not confirmed:
            return _refusal("dispatch", NODE_DISPATCH_REFUSED,
                            f"{cap} requires explicit confirmation before the "
                            "injected node transport is invoked")
        if not callable(transport):
            return _refusal("dispatch", NODE_DISPATCH_REFUSED,
                            "transport must be callable")
        try:
            result = transport({"type": "node.invoke", "node": rec["id"],
                                "capability": cap, "args": clean_args})
        except Exception as exc:
            return _refusal("dispatch", NODE_HOST_UNAVAILABLE,
                            "node transport failed: "
                            f"{_redact(str(exc))[:160]}")
        if not isinstance(result, dict):
            return _refusal("dispatch", NODE_HOST_UNAVAILABLE,
                            "node transport returned a non-mapping")
        try:
            rec["last_outcome"] = "DISPATCH_OK"
            _save(cfg, state)
        except NodeHostError:
            pass
        _audit("dispatch", requested_by,
               {"node": rec["id"], "cap": cap, "transport": "injected"})
        return {"state": "OK", "node": rec["id"], "capability": cap,
                "result": result,
                "authority": "operator-injected node transport; availability "
                             "is not authorization"}
    try:
        result = _shared_backend(cfg, cap, clean_args)
    except NodeHostError as exc:
        return _refusal("dispatch", exc.code, exc.detail)
    try:
        rec["last_outcome"] = "DISPATCH_OK"
        _save(cfg, state)
    except NodeHostError:
        pass
    _audit("dispatch", requested_by, {"node": rec["id"], "cap": cap})
    return {"state": "OK", "node": rec["id"], "capability": cap,
            "result": result,
            "authority": "local read-only via the shared backend"}


def refuse_unavailable(op: str, detail: str = "") -> Dict[str, Any]:
    """Typed refusal for a node-host action with no live backend."""
    return _refusal(op, NODE_HOST_UNAVAILABLE,
                    detail or "no live node-host backend is running")
