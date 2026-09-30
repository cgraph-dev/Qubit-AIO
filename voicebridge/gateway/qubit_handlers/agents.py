"""OpenClaw contractual-pin agents / agents.workspace family.

Every handler delegates to the one canonical Qubit authority:

* :mod:`voicebridge.files` — the scoped, read-only project-folder authority the
  CLI ``qubit files``, the dashboard, and voice all read (approved roots,
  typed refusals, redaction, audit). It backs ``agents.workspace.list`` /
  ``agents.workspace.get`` and ``agents.files.list`` / ``agents.files.get``.
* :mod:`voicebridge.task_ledger` — the shared task/run ledger behind
  ``agent.wait``.

Qubit is intentionally **one unified agent** (owner decision: one runtime, one
source tree, no second agent registry). The upstream multi-agent roster
mutations therefore carry typed operator prerequisites instead of a fabricated
roster:

* ``agents.create`` / ``agents.update`` / ``agents.delete`` — OPERATOR_ONLY: a
  multi-agent runtime Qubit does not run. The canonical single agent is
  ``qubit-worker``; its model/profile is managed through the shared config and
  provider registry, not by adding agents.
* ``agents.files.set`` — SAFE_EQUIVALENT: Qubit exposes no gateway workspace
  write; writes go through the brokered ``qubit_runtime.tools.file_operations``
  write path under the shared confirmation journal.

Mechanism differences carry their own typed codes so the manifest's behavior
proof can assert the exact refusal.
"""
from __future__ import annotations

from typing import Any, Dict

from ..gateway_ws import GatewayError

AGT_BAD_PARAMS = "GATEWAY_BAD_PARAMS"

#: The single canonical Qubit agent id (matches ``agents.list`` / the worker
#: lane). There is no second agent registry.
CANONICAL_AGENT_ID = "qubit-worker"

#: SAFE_EQUIVALENT typed codes (real local counterpart; upstream mechanism
#: differs). The manifest asserts each is emitted by its registered handler.
AGENTS_FILES_SET_BROKERED_WRITE = "AGENTS_FILES_SET_BROKERED_WRITE"
AGENT_WAIT_LOCAL_TASK_STATE = "AGENT_WAIT_LOCAL_TASK_STATE"
#: OPERATOR_ONLY typed code (missing live prerequisite / service).
AGENTS_MULTI_AGENT_OPERATOR_REQUIRED = "AGENTS_MULTI_AGENT_OPERATOR_REQUIRED"


def _agt_str(params: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = params.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value).strip()
    return ""


def _agt_agent_id(params: Dict[str, Any]) -> str:
    """Resolve the requested agent id, defaulting to the canonical single one.

    An explicit id must name the canonical Qubit agent; any other id is a typed
    not-found (Qubit runs no multi-agent roster).
    """
    requested = _agt_str(params, "agentId", "agent_id", "agent")
    if requested and requested != CANONICAL_AGENT_ID:
        raise GatewayError("AGENT_NOT_FOUND",
                           f"no agent {requested!r}; Qubit runs the single "
                           f"canonical agent {CANONICAL_AGENT_ID!r}")
    return CANONICAL_AGENT_ID


def _agt_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Map a ``voicebridge.files`` listing row to the workspace entry shape."""
    is_dir = bool(entry.get("dir"))
    rel = str(entry.get("rel", "") or "")
    row: Dict[str, Any] = {"path": rel, "name": str(entry.get("name", "") or ""),
                           "kind": "directory" if is_dir else "file"}
    if not is_dir:
        row["size"] = int(entry.get("size", 0) or 0)
    return row


def _agt_workspace_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import files as F

    _agt_agent_id(params)
    path = _agt_str(params, "path")
    out = F.inspect(cfg, path) if not path else F.list_dir(cfg, path)
    if str(out.get("state")) not in ("OK",):
        return {"state": str(out.get("state", "EMPTY")),
                "agentId": CANONICAL_AGENT_ID,
                "code": out.get("code"), "detail": out.get("detail"),
                "entries": []}
    entries = [_agt_entry(e) for e in out.get("entries", [])]
    return {"state": "OK", "agentId": CANONICAL_AGENT_ID, "path": path,
            "entries": entries, "totalEntries": len(entries), "offset": 0,
            "authority": "voicebridge.files.inspect/list_dir"}


def _agt_workspace_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import files as F

    _agt_agent_id(params)
    path = _agt_str(params, "path")
    if not path:
        raise GatewayError(AGT_BAD_PARAMS, "path is required")
    out = F.read_file(cfg, path)
    return {"state": "OK", "agentId": CANONICAL_AGENT_ID,
            "file": {"path": path,
                     "size": int(out.get("size", 0) or 0),
                     "encoding": "utf8",
                     "content": out.get("text", "")},
            "authority": "voicebridge.files.read_file"}


def _agt_files_list(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    out = _agt_workspace_list(cfg, params)
    out["authority"] = "voicebridge.files.inspect/list_dir"
    return out


def _agt_files_get(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _agt_workspace_get(cfg, params)


def _agt_files_set(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    _agt_agent_id(params)
    name = _agt_str(params, "name", "path", "file")
    if not name:
        raise GatewayError(AGT_BAD_PARAMS, "name is required")
    return {"state": "UNAVAILABLE", "code": AGENTS_FILES_SET_BROKERED_WRITE,
            "detail": ("Qubit exposes no gateway workspace write; agent files "
                       "are written through the brokered file-write tool under "
                       "the shared confirmation journal"),
            "qubit_counterpart": "brokered `qubit_runtime.tools.file_operations` "
                                 "write + the shared confirmation journal",
            "agentId": CANONICAL_AGENT_ID, "name": name}


def _agt_agent_wait(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    from ... import task_ledger as TL

    run_id = _agt_str(params, "runId", "run_id", "id")
    if not run_id:
        raise GatewayError(AGT_BAD_PARAMS, "runId is required")
    out = TL.show(cfg, run_id)
    state = str(out.get("state", "OK"))
    if state != "OK":
        return {"state": state, "runId": run_id,
                "code": out.get("code", "TASK_NOT_FOUND"),
                "detail": out.get("detail"),
                "authority": "voicebridge.task_ledger.show"}
    return {"state": "OK", "runId": run_id, "code": AGENT_WAIT_LOCAL_TASK_STATE,
            "task": out.get("task"), "source": out.get("source"),
            "qubit_counterpart": "CLI `qubit tasks show` + the shared task ledger",
            "detail": ("Qubit reports the canonical task/run state; it does not "
                       "block-wait on an agent turn"),
            "authority": "voicebridge.task_ledger.show"}


def _agt_agent_create(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _agt_multi_agent_gate("agents.create")


def _agt_agent_update(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _agt_multi_agent_gate("agents.update")


def _agt_agent_delete(cfg: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    return _agt_multi_agent_gate("agents.delete")


def _agt_multi_agent_gate(operation: str) -> Dict[str, Any]:
    return {"state": "UNAVAILABLE",
            "code": AGENTS_MULTI_AGENT_OPERATOR_REQUIRED,
            "detail": (f"{operation} needs a multi-agent runtime/roster; Qubit "
                       "is one unified agent and runs no second agent registry"),
            "operator_prerequisite": ("an upstream multi-agent runtime; Qubit "
                                      "is intentionally a single unified agent"),
            "qubit_counterpart": ("the single canonical agent `qubit-worker` "
                                  "managed via config + the provider registry")}


def register(registry: Any) -> None:
    """Register the agents / agents.workspace handlers on *registry*."""
    registry.register("agents.workspace.list", _agt_workspace_list)
    registry.register("agents.workspace.get", _agt_workspace_get)
    registry.register("agents.files.list", _agt_files_list)
    registry.register("agents.files.get", _agt_files_get)
    registry.register("agents.files.set", _agt_files_set)
    registry.register("agent.wait", _agt_agent_wait)
    registry.register("agents.create", _agt_agent_create)
    registry.register("agents.update", _agt_agent_update)
    registry.register("agents.delete", _agt_agent_delete)
