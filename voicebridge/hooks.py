"""Qubit hook subscriptions via a declared-never-executed registry.

Qubit ``hooks {list,ls,test,revoke,remove,rm,doctor}`` inspects
shell-script hooks from config and never auto-executes them. Automatic shell
execution can never pass the Qubit broker (no-direct-shell invariant), so
the registry keeps the shell permanently disarmed:

* ``declare`` — record a subscription (name, event pattern, shell text
  for REVIEW ONLY) with explicit ``--confirm``; declarations are stored
  DISABLED with a content hash and hostile-marker findings up front;
* ``list``/``show``/``validate`` — read-only inspection and static
  screening (schema, hash, hostile markers; redacted echoes);
* ``test`` — dry-run delivery plan (what WOULD match, findings) without
  executing anything and without writing audit events;
* ``emit`` — the production core lifecycle entry point (OC-29/D23): session,
  task, tool, plugin, and skill paths emit documented event types into the
  registry; matching DISABLED subscriptions get a redacted ``hook.emitted``
  broker audit event and a bounded emission receipt, and the shell never runs;
* ``fire`` — manual, confirmed delivery through the broker audit path:
  appends a redacted ``hook.fired`` event for matching subscriptions and
  reports the shell as NOT executed (disarmed delivery); hash mismatch
  fails closed (``HOOK_TAMPERED``);
* ``revoke``/``remove`` — disable matching or delete the declaration
  (confirmed, audited);
* ``doctor`` — registry health (schema, hash re-verification, findings).

No code path in this module — or reachable from it — spawns a process.
Shell text is data for review, never an instruction.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List

SCHEMA = "qubit-hooks/1"
STORE_NAME = "hooks.json"
MAX_HOOKS = 128
MAX_NAME_LEN = 64
MAX_EVENT_LEN = 128
MAX_SHELL_LEN = 4000
MAX_FILE_BYTES = 1_000_000
MAX_EMISSIONS = 200
MAX_PAYLOAD_KEYS = 32

# Documented core lifecycle events a Qubit caller may emit into the registry
# (Qubit hooks event-types parity, execution stays disarmed).
EVENT_TYPES = (
    "session.started", "session.completed",
    "task.started", "task.completed", "task.failed",
    "tool.invoked", "tool.failed",
    "plugin.invoked", "plugin.bound",
    "skill.submitted", "skill.verified",
    "agent.started", "agent.stopped",
    "hook.error",
)

# Review-only authoring templates for the hooks Qubit ships "bundled"
# (Qubit bundled-hooks parity, OC-D23). Metadata only: nothing is
# auto-declared and the registry never executes a shell. ``bundled`` surfaces
# them as starting points and ``doctor`` reports their names; a caller still
# runs ``declare --confirm`` to create a review-only subscription.
BUNDLED_HOOKS = (
    {"name": "command-logger",
     "description": "Record emitted lifecycle events to the broker audit log.",
     "events": ("tool.invoked", "plugin.invoked"), "kind": "bundled"},
    {"name": "session-notifier",
     "description": "Surface session start/complete lifecycle notices.",
     "events": ("session.started", "session.completed"), "kind": "bundled"},
    {"name": "task-notifier",
     "description": "Surface task completion/failure lifecycle notices.",
     "events": ("task.completed", "task.failed"), "kind": "bundled"},
)

CONFIRM_CODE = "HOOK_CONFIRM_REQUIRED"
ESTOP_CODE = "HOOK_ESTOP"
NOT_FOUND_CODE = "HOOK_NOT_FOUND"
EXISTS_CODE = "HOOK_EXISTS"
BAD_INPUT_CODE = "HOOK_BAD_INPUT"
TAMPERED_CODE = "HOOK_TAMPERED"
CORRUPT_CODE = "HOOK_STORE_CORRUPT"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_EVENT_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_EVENT_GLOB_RE = re.compile(r"^[a-z0-9*][a-z0-9_.:*-]{0,127}$")


class HookError(Exception):
    """Typed hook-registry failure (never a traceback on the CLI)."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _redact(text: str) -> str:
    from .redaction import redact_text

    return redact_text(text)


def _hash_shell(shell: str) -> str:
    return hashlib.sha256(shell.encode("utf-8")).hexdigest()


def _findings(shell: str) -> List[str]:
    from .opencode.policy import flag_untrusted

    return flag_untrusted(shell or "")


def _store_path(cfg: Any) -> Path:
    return Path(cfg.state_dir()) / STORE_NAME


def _blank() -> Dict[str, Any]:
    return {"schema": SCHEMA, "hooks": {}, "emissions": []}


def _load(cfg: Any) -> Dict[str, Any]:
    path = _store_path(cfg)
    if not path.exists():
        return _blank()
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise HookError(CORRUPT_CODE, "hook store too large; restore from backup")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except HookError:
        raise
    except (OSError, ValueError) as exc:
        raise HookError(CORRUPT_CODE,
                        f"hook store unreadable: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA \
            or not isinstance(raw.get("hooks"), dict):
        raise HookError(CORRUPT_CODE, "hook store has an unknown schema")
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
        raise HookError(CORRUPT_CODE,
                        f"hook store write failed: {exc}") from exc


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    try:
        log_event(f"hook.{op}", actor=actor, **detail)
    except Exception:
        pass


def _now() -> float:
    return time.time()


def _check_name(name: str) -> str:
    text = (name or "").strip().lower()
    if not _NAME_RE.match(text):
        raise HookError(BAD_INPUT_CODE,
                        "hook name must match ^[a-z0-9][a-z0-9_.-]{0,63}$")
    return text


def _check_event(event: str) -> str:
    text = (event or "").strip().lower()
    if text == "*":
        return text
    pattern = _EVENT_GLOB_RE if "*" in text else _EVENT_RE
    if not pattern.match(text):
        raise HookError(BAD_INPUT_CODE,
                        "event pattern must match ^[a-z0-9][a-z0-9_.:-]{0,127}$ "
                        "or a '*'-glob of the same shape")
    return text


def _event_matches(pattern: str, event: str) -> bool:
    """Exact match, ``fnmatch`` glob (``task.*``), or the ``*`` wildcard."""
    if pattern == "*":
        return True
    return fnmatch.fnmatchcase(event, pattern)


def _public(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {"name": rec["name"], "event": rec["event"],
            "status": rec["status"],
            "shell_preview": _redact(rec.get("shell", ""))[:120],
            "findings": list(rec.get("findings", [])),
            "content_hash": rec.get("content_hash", "")[:16],
            "created_at": rec.get("created_at"),
            "updated_at": rec.get("updated_at"),
            "updated_by": rec.get("updated_by")}


def _get(state: Dict[str, Any], name: str) -> Dict[str, Any]:
    rec = state["hooks"].get(name)
    if rec is None:
        raise HookError(NOT_FOUND_CODE, f"no hook {name!r}")
    return rec


# ---------------------------------------------------------------- operations
def declare(cfg: Any, name: str, event: str, shell: str, *,
            requested_by: str = "cli:user",
            confirmed: bool = False) -> Dict[str, Any]:
    """Record a subscription DISABLED (shell stored for review, never run)."""
    if not confirmed:
        raise HookError(CONFIRM_CODE,
                        "hooks declare requires explicit --confirm")
    clean_name = _check_name(name)
    clean_event = _check_event(event)
    text = shell or ""
    if len(text) > MAX_SHELL_LEN:
        raise HookError(BAD_INPUT_CODE,
                        f"shell text must be <={MAX_SHELL_LEN} chars")
    state = _load(cfg)
    if len(state["hooks"]) >= MAX_HOOKS:
        raise HookError(EXISTS_CODE, f"hook limit reached ({MAX_HOOKS})")
    if clean_name in state["hooks"]:
        raise HookError(EXISTS_CODE, f"hook {clean_name!r} exists")
    rec = {"name": clean_name, "event": clean_event, "shell": text,
           "status": "disabled", "findings": _findings(text),
           "content_hash": _hash_shell(text),
           "created_at": _now(), "updated_at": _now(),
           "updated_by": requested_by}
    state["hooks"][clean_name] = rec
    _save(cfg, state)
    _audit("declare", requested_by,
           {"hook": clean_name, "match_event": clean_event,
            "findings": len(rec["findings"])})
    return {"state": "OK", "hook": _public(rec),
            "execution": "NEVER: shell text is review data, not an instruction"}


def list_hooks(cfg: Any) -> Dict[str, Any]:
    """List declarations (read-only)."""
    state = _load(cfg)
    recs = sorted(state["hooks"].values(), key=lambda r: r.get("name", ""))
    return {"state": "OK", "hooks": [_public(r) for r in recs]}


def bundled() -> Dict[str, Any]:
    """Qubit-bundled hook authoring templates (metadata only).

    Mirrors Qubit's bundled hooks as review-only starting points: no
    subscription is created and no shell is ever executed. Callers still use
    ``declare(..., confirmed=True)`` to subscribe.
    """
    return {"state": "OK", "schema": SCHEMA,
            "bundled": [dict(h) for h in BUNDLED_HOOKS],
            "events_known": list(EVENT_TYPES),
            "note": "templates only; `declare --confirm` to subscribe"}


def show(cfg: Any, name: str) -> Dict[str, Any]:
    """Show one declaration (read-only, redacted preview)."""
    state = _load(cfg)
    return {"state": "OK", "hook": _public(_get(state, _check_name(name)))}


def validate(cfg: Any, name: str) -> Dict[str, Any]:
    """Static screening of one declaration (read-only, never executes)."""
    state = _load(cfg)
    rec = _get(state, _check_name(name))
    fresh = _findings(rec.get("shell", ""))
    intact = _hash_shell(rec.get("shell", "")) == rec.get("content_hash")
    return {"state": "OK", "hook": _public(rec),
            "hash_intact": intact,
            "findings": fresh,
            "verdict": "DISABLED-REVIEW-ONLY" if intact else "TAMPERED"}


def test_plan(cfg: Any, name: str) -> Dict[str, Any]:
    """Dry-run delivery plan (no execution, no audit writes)."""
    state = _load(cfg)
    rec = _get(state, _check_name(name))
    return {"state": "OK", "hook": _public(rec),
            "would_match_event": rec["event"],
            "would_execute_shell": False,
            "would_append_audit": "hook.fired (redacted, shell NOT executed)",
            "note": "dry-run: nothing executed, nothing written"}


def fire(cfg: Any, event: str, *, requested_by: str = "cli:user",
         confirmed: bool = False) -> Dict[str, Any]:
    """Manual broker-audited delivery for matching subscriptions.

    Appends one redacted ``hook.fired`` audit event per matching DISABLED
    declaration and reports every shell as NOT executed. Hash mismatch
    fails closed. Requires explicit confirmation (delivery is observable).
    """
    if not confirmed:
        raise HookError(CONFIRM_CODE,
                        "hooks fire requires explicit --confirm")
    clean_event = _check_event(event)
    state = _load(cfg)
    matched = [r for r in state["hooks"].values()
               if _event_matches(r.get("event", ""), clean_event)
               and r.get("status") == "disabled"]
    delivered = []
    for rec in matched:
        if _hash_shell(rec.get("shell", "")) != rec.get("content_hash"):
            raise HookError(TAMPERED_CODE,
                            f"hook {rec['name']!r} changed since review; "
                            f"re-declare it before firing")
        from .logging_setup import log_event

        log_event("hook.fired", actor=requested_by, hook=rec["name"],
                  match_event=clean_event, shell_executed=False)
        delivered.append(rec["name"])
    return {"state": "OK", "event": clean_event, "matched": sorted(delivered),
            "shell_executed": False,
            "delivery": "broker audit event only; shell never executes"}


def emit(cfg: Any, event: str, *, payload: Dict[str, Any] | None = None,
         requested_by: str = "core:lifecycle") -> Dict[str, Any]:
    """Emit a real core lifecycle event into the hook registry (OC-29).

    Production callers (session/task/plugin/skill paths) emit their lifecycle
    events here. Matching DISABLED subscriptions receive a redacted
    ``hook.emitted`` broker audit event and a bounded emission receipt is
    persisted; the shell is *never* executed. Tampered subscriptions are
    reported and skipped rather than raising into the caller. While the
    emergency stop is latched emission is refused with a typed result.
    """
    clean_event = _check_event(event)
    if estop_engaged(cfg):
        return refuse_estop(f"hook emit {clean_event}")
    raw_payload = payload if isinstance(payload, dict) else {}
    keys = [str(k)[:64] for k in list(raw_payload)[:MAX_PAYLOAD_KEYS]]
    state = _load(cfg)
    matched = [r for r in state["hooks"].values()
               if _event_matches(r.get("event", ""), clean_event)
               and r.get("status") == "disabled"]
    delivered: List[str] = []
    tampered: List[str] = []
    for rec in matched:
        if _hash_shell(rec.get("shell", "")) != rec.get("content_hash"):
            tampered.append(rec["name"])
            from .logging_setup import log_event

            try:
                log_event("hook.error", actor=requested_by, hook=rec["name"],
                          code=TAMPERED_CODE, match_event=clean_event)
            except Exception:
                pass
            continue
        from .logging_setup import log_event

        try:
            log_event("hook.emitted", actor=requested_by, hook=rec["name"],
                      match_event=clean_event, shell_executed=False)
        except Exception:
            pass
        delivered.append(rec["name"])
    emissions = state.setdefault("emissions", [])
    if not isinstance(emissions, list):
        emissions = state["emissions"] = []
    emissions.append({"event": clean_event, "matched": sorted(delivered),
                      "tampered": sorted(tampered), "at": _now(),
                      "by": requested_by, "payload_keys": sorted(keys)})
    state["emissions"] = emissions[-MAX_EMISSIONS:]
    _save(cfg, state)
    return {"state": "OK", "event": clean_event,
            "matched": sorted(delivered), "tampered": sorted(tampered),
            "shell_executed": False, "payload_keys": sorted(keys),
            "delivery": "core lifecycle event; broker audit only; shell never "
                        "executes"}


def revoke(cfg: Any, name: str, *, requested_by: str = "cli:user",
           confirmed: bool = False) -> Dict[str, Any]:
    """Revoke a declaration (audited; shell was never executable)."""
    return remove(cfg, name, requested_by=requested_by, confirmed=confirmed,
                  op="revoke")


def remove(cfg: Any, name: str, *, requested_by: str = "cli:user",
           confirmed: bool = False, op: str = "remove") -> Dict[str, Any]:
    """Delete a declaration (confirmed, audited)."""
    if not confirmed:
        raise HookError(CONFIRM_CODE,
                        f"hooks {op} requires explicit --confirm")
    state = _load(cfg)
    rec = _get(state, _check_name(name))
    del state["hooks"][rec["name"]]
    _save(cfg, state)
    _audit(op, requested_by, {"hook": rec["name"]})
    return {"state": "OK", op + "d": rec["name"]}


def doctor(cfg: Any) -> Dict[str, Any]:
    """Registry health: schema, hash re-verification, findings (read-only)."""
    try:
        state = _load(cfg)
    except HookError as exc:
        return {"state": "FAILED", "code": exc.code,
                "detail": exc.detail[:200]}
    report = []
    for rec in sorted(state["hooks"].values(), key=lambda r: r.get("name", "")):
        intact = _hash_shell(rec.get("shell", "")) == rec.get("content_hash")
        report.append({"name": rec["name"],
                       "state": "OK" if intact else "TAMPERED",
                       "findings": _findings(rec.get("shell", ""))})
    overall = "OK" if all(r["state"] == "OK" for r in report) else "DEGRADED"
    emissions = state.get("emissions", [])
    return {"state": overall, "hooks": report,
            "events_known": list(EVENT_TYPES),
            "bundled_known": [h["name"] for h in BUNDLED_HOOKS],
            "emissions": len(emissions) if isinstance(emissions, list) else 0}


def estop_engaged(cfg: Any) -> bool:
    """Same durable latch probe as the plugins surface (fail closed)."""
    from .plugins import estop_engaged as _engaged

    return _engaged(cfg)


def refuse_estop(operation: str) -> Dict[str, Any]:
    """Typed refusal while the emergency stop is latched."""
    return {"state": "ESTOP", "code": ESTOP_CODE, "operation": operation,
            "explanation": "Qubit Agent cannot do this: the emergency stop "
                           "is latched; reset it before changing hooks.",
            "owner": "qubit-policy",
            "rollback": "registry untouched; nothing to roll back"}
