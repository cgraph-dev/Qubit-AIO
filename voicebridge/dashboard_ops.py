"""Packet 8T-F — dashboard operations console backend.

One authenticated dashboard surface that executes the SAME backend
operations as the CLI and the voice registry, with the same typed results,
permissions, confirmations, receipts, audit, redaction, resource limits,
cancellation, emergency stop, and rollback behavior.

Two tiers:

* Tier 1 (automatic tri-parity): every ``voice_commands.REGISTRY`` entry is
  exposed as a dashboard op. The dashboard builds a ``Resolution`` and calls
  ``voice_commands.execute()`` with ``source="dashboard"`` (an explicitly
  allowed source). Confirm-gated entries stage into the SHARED
  ``ApprovalJournal`` the dashboard approvals page and the CLI
  ``voice confirm`` consume — one confirmation pipeline for all three
  surfaces. Emergency stop, audit, redaction, and settle-once semantics are
  therefore identical by construction, not by reimplementation.

* Tier 2 (explicit extras): CLI-mutating operations that have no voice
  registry entry. Each extra names the exact backend function the CLI calls
  (same module, same function), stages confirmations in the same journal
  file under a ``dashboard.<id>`` capability, enforces the same global +
  module emergency-stop probes the CLI enforces, audits the same way, and
  redacts the same way. Extras declare honest ``interfaces``: entries whose
  arguments cannot be spoken safely (passphrases, raw byte payloads, nested
  JSON answers) are ``["cli", "dashboard"]`` with a machine-readable reason;
  everything else is ``["cli", "dashboard", "voice"]`` once its voice entry
  lands (Tier 1 then covers it automatically and the extra is removed).

Sensitive arguments (passphrases) are NEVER persisted: they are stripped
before the confirmation is journaled and must be re-supplied with the
confirm call, which lives only in request memory. Audit and error paths
carry redacted values only.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

SENSITIVE_ARGS = ("passphrase", "password", "secret", "token", "api_key",
                  "key_ref")

INTERFACES_ALL = ("cli", "dashboard", "voice")
# Real CLI + dashboard surfaces; the spoken surface is a dedicated canonical
# registry entry (declared per-op via ``voice_guard``), not this Tier-2 id.
INTERFACES_NO_VOICE = ("cli", "dashboard")
INTERFACES_NO_VOICE_PASSPHRASE = INTERFACES_NO_VOICE
# Ops that are honestly dashboard-only: a rendered form is the only surface
# that can carry the structured/sensitive argument, and the CLI/voice path for
# the underlying capability is a different (already-covered) entry point.
INTERFACES_DASHBOARD = ("dashboard",)
REASON_DASHBOARD_REFUSAL = (
    "dashboard-only: this is a typed refusal surface (no CLI/voice equivalent "
    "is meaningful); the dashboard console shows the honest typed state"
)
REASON_PASSPHRASE = (
    "voice unsupported: spoken passphrases would leak into transcripts, "
    "journal records, and audio history; use the dashboard password form "
    "or the CLI terminal instead"
)
REASON_BYTES = (
    "voice unsupported: raw byte payloads cannot be spoken; "
    "use the dashboard form or the CLI instead"
)
REASON_NESTED = (
    "voice unsupported: nested structured answers cannot be spoken "
    "reliably; use the dashboard onboarding form or the CLI wizard instead"
)
REASON_VOICE_CANONICAL = (
    "no separate voice surface: the spoken form of this capability is the "
    "canonical voice registry entry named by voice_guard (same backend); "
    "this Tier-2 entry is the CLI/dashboard form only"
)

# Tier-1 registry ops whose `cli` binding is a known cross-boundary handoff
# (cli.py does not own a subparser yet). The catalog must never advertise a
# surface that does not exist: these report dashboard+voice only, with the
# machine-readable reason, until the CLI binding lands — at which point the
# id is removed here and the automatic all-three interface returns. Every
# entry must still resolve to the same shared broker backend on the surfaces
# it does declare.
#
# A14: this map is intentionally EMPTY because every registry op that the
# catalog advertises as `cli` has a real `qubit <family> [<sub>]` production
# path — either declared in `voice_commands.CLI_COVERAGE` or a native parser
# subcommand (e.g. `vision_describe` -> `qubit vision describe`). The
# `tests/test_qubit_command_parity8u.py` closure and
# `tests/test_authority_unification8u.py::test_catalog_cli_claims_are_truthful`
# re-derive that for every op, so a genuine orphan fails the gate instead of
# being hidden here. Populate an id here only when a real CLI binding is
# missing.
CLI_PENDING: Dict[str, str] = {}


def _redact(text: str) -> str:
    from .redaction import redact_text

    try:
        return redact_text(text)
    except Exception:
        return str(text)


def _audit(op: str, actor: str, detail: Dict[str, Any]) -> None:
    from .logging_setup import log_event

    scrubbed = {}
    for key, value in detail.items():
        if key in SENSITIVE_ARGS or key == "sensitive":
            scrubbed[key] = "<redacted>"
        else:
            scrubbed[key] = value
    try:
        log_event(f"dashboard.ops.{op}", actor=actor, **scrubbed)
    except Exception:
        pass


def _estop(cfg: Any) -> bool:
    try:
        from .plugins import estop_engaged

        return bool(estop_engaged(cfg))
    except Exception:
        return True


def _journal(cfg: Any):
    from .approvals import ApprovalJournal
    from pathlib import Path

    return ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                           default_ttl_s=120.0)


def _mark_executed(cfg: Any, confirmation_id: str) -> bool:
    from . import voice_commands as VC

    return VC._mark_executed(cfg, confirmation_id)


# ---------------------------------------------------------------- Tier 1
def _coerce_arg(spec: Any, value: Any) -> Any:
    """Coerce one dashboard-supplied arg per VoiceArg type; raises ValueError."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if getattr(spec, "required", False):
            raise ValueError(f"missing required argument: {spec.name}")
        return None
    kind = getattr(spec, "type", "str")
    if kind == "int":
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            raise ValueError(f"argument {spec.name!r} must be an integer")
    if kind == "choice":
        text = str(value).strip()
        choices = tuple(getattr(spec, "choices", ()) or ())
        if choices and text not in choices:
            raise ValueError(
                f"argument {spec.name!r} must be one of {sorted(choices)}")
        return text
    return str(value)


def catalog() -> List[Dict[str, Any]]:
    """Machine-readable op catalog: Tier 1 (voice registry) + Tier 2 extras."""
    from . import voice_commands as VC

    items: List[Dict[str, Any]] = []
    for entry in sorted(VC.REGISTRY.values(), key=lambda e: e.id):
        interfaces = list(INTERFACES_ALL)
        interface_reason = ""
        if entry.id in CLI_PENDING:
            interfaces = ["dashboard", "voice"]
            interface_reason = CLI_PENDING[entry.id]
        items.append({
            "id": entry.id,
            "tier": "registry",
            "group": entry.backend or "general",
            "title": entry.title,
            "help": entry.help,
            "aliases": list(entry.aliases)[:6],
            "examples": list(entry.examples)[:3],
            "args": [{"name": a.name, "type": a.type,
                      "required": bool(a.required),
                      "choices": list(a.choices or ()),
                      "clarifier": a.clarifier or ""}
                     for a in entry.args],
            "confirm": bool(entry.confirm),
            "scope": list(entry.scope),
            "interfaces": interfaces,
            "interface_reason": interface_reason,
            "parity": list(entry.parity or ()),
        })
    for op_id in sorted(EXTRA_OPS):
        items.append(_catalog_extra_item(op_id, EXTRA_OPS[op_id]))
    for op_id in sorted(MODEL_PROFILE_OPS):
        items.append(_catalog_extra_item(op_id, MODEL_PROFILE_OPS[op_id]))
    return items


def _catalog_extra_item(op_id: str, spec: ExtraOp) -> Dict[str, Any]:
    from . import voice_commands as VC

    return {
        "id": op_id,
        "tier": "extra",
        "group": spec.group,
        "title": spec.title,
        "help": spec.help,
        "aliases": [],
        "examples": [],
        "args": [{"name": a["name"], "type": a.get("type", "str"),
                  "required": bool(a.get("required", False)),
                  "choices": list(a.get("choices", ()) or ()),
                  "clarifier": a.get("clarifier", ""),
                  "secret": bool(a.get("secret", False))}
                 for a in spec.args],
        "confirm": bool(spec.confirm),
        "scope": ["dashboard"],
        "interfaces": list(spec.interfaces),
        "interface_reason": spec.interface_reason,
        "voice_guard": (spec.voice_guard
                        or (f"guard_{op_id}"
                            if op_id in getattr(VC, "VOICE_GUARDS", {})
                            else "")),
        "parity": list(spec.parity or ()),
    }


def _dashboard_ctx(cfg: Any, actor: str, session: str) -> SimpleNamespace:
    from . import voice_commands as VC

    return VC.make_ctx(cfg, actor=actor, session=session, source="dashboard")


def record_session(cfg: Any, confirmation_id: str) -> str:
    """The session a staged confirmation belongs to (for channel-agnostic
    settle paths like the approvals page and the approvals CLI)."""
    try:
        journal = _journal(cfg)
        target = journal.get(confirmation_id)
        if target is None:
            return "dashboard"
        meta = getattr(target, "meta", {}) or {}
        capability = str(getattr(target, "capability", "") or "")
        default = "dashboard" if capability.startswith("dashboard.") \
            else "voice"
        return str(meta.get("session", default))
    except Exception:
        return "dashboard"


def run_registry_op(cfg: Any, op_id: str, args: Dict[str, Any], *,
                    actor: str = "dashboard:user",
                    session: str = "dashboard") -> Dict[str, Any]:
    """Execute a Tier-1 op through the shared voice pipeline."""
    from . import voice_commands as VC

    entry = VC.REGISTRY.get(op_id)
    if entry is None:
        return {"state": "failed", "code": "UNKNOWN_OP",
                "detail": f"unknown operation {op_id!r}"}
    coerced: Dict[str, Any] = {}
    try:
        allowed = {a.name for a in entry.args}
        for key in args:
            if key not in allowed:
                raise ValueError(f"unknown argument: {key!r}")
        for spec in entry.args:
            if spec.name in args:
                value = _coerce_arg(spec, args[spec.name])
                if value is not None:
                    coerced[spec.name] = value
            elif spec.required and spec.name not in dict(entry.fixed or ()):
                raise ValueError(f"missing required argument: {spec.name}")
    except ValueError as exc:
        _audit("rejected", actor, {"op": op_id, "reason": str(exc)[:120]})
        return {"state": "failed", "code": "BAD_ARGS",
                "detail": _redact(str(exc))[:200]}
    from .voice_commands import Resolution

    resolution = Resolution(VC.MATCH, entry=entry, args=coerced)
    try:
        outcome = VC.execute(_dashboard_ctx(cfg, actor, session), resolution)
    except Exception as exc:
        return {"state": "failed", "code": "EXECUTOR_ERROR",
                "detail": _redact(str(exc))[:200]}
    out: Dict[str, Any] = {"state": outcome.kind, "speech": outcome.speech}
    if outcome.kind == "refused":
        # Refusals leave no receipt by design (voice-tested invariant); the
        # dashboard surfaces the same typed refusal as a code.
        lowered = (outcome.speech or "").lower()
        if "latched" in lowered:
            out["code"] = "ESTOP_LATCHED"
        elif "did not come from you" in lowered:
            out["code"] = "SOURCE_REFUSED"
        else:
            out["code"] = "REFUSED"
    if outcome.confirmation_id:
        out["confirmation_id"] = outcome.confirmation_id
    if outcome.receipt:
        out["receipt"] = outcome.receipt
        if outcome.kind == "failed" and outcome.receipt.get("code"):
            out["code"] = outcome.receipt["code"]
    return out


def confirm_registry_op(cfg: Any, confirmation_id: str, *,
                        actor: str = "dashboard:user",
                        session: str = "dashboard") -> Dict[str, Any]:
    from . import voice_commands as VC

    try:
        outcome = VC.confirm_pending(
            _dashboard_ctx(cfg, actor, session), confirmation_id)
    except Exception as exc:
        return {"state": "failed", "code": "CONFIRM_ERROR",
                "detail": _redact(str(exc))[:200]}
    out: Dict[str, Any] = {"state": outcome.kind, "speech": outcome.speech}
    if outcome.receipt:
        out["receipt"] = outcome.receipt
        if outcome.kind == "failed" and outcome.receipt.get("code"):
            out["code"] = outcome.receipt["code"]
    return out


def cancel_registry_op(cfg: Any, confirmation_id: Optional[str] = None, *,
                       actor: str = "dashboard:user",
                       session: str = "dashboard") -> Dict[str, Any]:
    from . import voice_commands as VC

    try:
        outcome = VC.cancel_pending(
            _dashboard_ctx(cfg, actor, session), confirmation_id)
    except Exception as exc:
        return {"state": "failed", "code": "CANCEL_ERROR",
                "detail": _redact(str(exc))[:200]}
    return {"state": outcome.kind, "speech": outcome.speech,
            "receipt": outcome.receipt}


# ---------------------------------------------------------------- Tier 2
@dataclass
class ExtraOp:
    id: str
    group: str
    title: str
    help: str
    args: Tuple[Dict[str, Any], ...] = ()
    confirm: bool = True
    interfaces: Tuple[str, ...] = INTERFACES_ALL
    interface_reason: str = ""
    # Canonical voice registry entry that speaks for this capability when the
    # Tier-2 entry itself is CLI/dashboard only (same backend, different id).
    voice_guard: str = ""
    parity: Tuple[str, ...] = ()
    run: Optional[Callable[[Any, Dict[str, Any], str], Dict[str, Any]]] = None
    estop: Optional[Callable[[Any], bool]] = None


EXTRA_OPS: Dict[str, ExtraOp] = {}
# Resolver-backed model/provider profile operations. Reads are unconfirmed,
# mutations confirm-gated. They live in a dedicated registry (mirroring the
# voice layer's MODEL_PROFILE_REGISTRY) so the pinned read-only ``EXTRA_OPS``
# allowlist stays exact while every entry still flows through the SAME
# run/confirm/audit/journal/e-stop pipeline.
MODEL_PROFILE_OPS: Dict[str, ExtraOp] = {}


def extra(op_id: str, group: str, title: str, help: str, *,
          args: Tuple[Dict[str, Any], ...] = (),
          confirm: bool = True,
          interfaces: Tuple[str, ...] = INTERFACES_ALL,
          interface_reason: str = "",
          voice_guard: str = "",
          parity: Tuple[str, ...] = (),
          estop: Optional[Callable[[Any], bool]] = None) -> Callable:
    """Decorator registering a Tier-2 extra op (backend = same fn CLI calls)."""

    def _wrap(fn: Callable[[Any, Dict[str, Any], str], Dict[str, Any]]):
        EXTRA_OPS[op_id] = ExtraOp(
            id=op_id, group=group, title=title, help=help, args=args,
            confirm=confirm, interfaces=interfaces,
            interface_reason=interface_reason, voice_guard=voice_guard,
            parity=parity, run=fn, estop=estop)
        return fn

    return _wrap


def model_op(op_id: str, group: str, title: str, help: str, *,
             args: Tuple[Dict[str, Any], ...] = (),
             confirm: bool = True,
             interfaces: Tuple[str, ...] = INTERFACES_ALL,
             interface_reason: str = "",
             voice_guard: str = "",
             parity: Tuple[str, ...] = (),
             estop: Optional[Callable[[Any], bool]] = None) -> Callable:
    """Register a resolver-backed model/provider profile op (same pipeline)."""

    def _wrap(fn: Callable[[Any, Dict[str, Any], str], Dict[str, Any]]):
        MODEL_PROFILE_OPS[op_id] = ExtraOp(
            id=op_id, group=group, title=title, help=help, args=args,
            confirm=confirm, interfaces=interfaces,
            interface_reason=interface_reason, voice_guard=voice_guard,
            parity=parity, run=fn, estop=estop)
        return fn

    return _wrap


def _lookup_extra(op_id: str) -> Optional[ExtraOp]:
    return EXTRA_OPS.get(op_id) or MODEL_PROFILE_OPS.get(op_id)


def _sensitive_names(spec: ExtraOp) -> set:
    """Argument names that must never be journaled or audited.

    The global ``SENSITIVE_ARGS`` list plus any per-argument ``secret`` flag
    declared on the extra. A ``secret`` arg is therefore never persisted in
    the shared approval journal and must be re-supplied at confirm time —
    exactly like a passphrase, but usable for nested structured payloads
    (onboarding answers, snapshots) that carry private data.
    """
    declared = {a["name"] for a in spec.args if a.get("secret")}
    return set(SENSITIVE_ARGS) | declared


def _redacted_args(spec: ExtraOp, args: Dict[str, Any]) -> Dict[str, Any]:
    sensitive = _sensitive_names(spec)
    return {k: ("<redacted>" if k in sensitive else v)
            for k, v in args.items()}


def _coerce_extra(spec: ExtraOp, args: Dict[str, Any]) -> Dict[str, Any]:
    coerced: Dict[str, Any] = {}
    allowed = {a["name"]: a for a in spec.args}
    for key in args:
        if key not in allowed:
            raise ValueError(f"unknown argument: {key!r}")
    for name, field in allowed.items():
        if name in args:
            value = args[name]
            if value is None or (isinstance(value, str) and not value.strip()):
                if field.get("required"):
                    raise ValueError(f"missing required argument: {name}")
                continue
            if field.get("type") == "int":
                try:
                    coerced[name] = int(str(value).strip())
                except (TypeError, ValueError):
                    raise ValueError(f"argument {name!r} must be an integer")
            elif field.get("type") == "choice":
                text = str(value).strip()
                choices = list(field.get("choices", ()) or ())
                if choices and text not in choices:
                    raise ValueError(
                        f"argument {name!r} must be one of {sorted(choices)}")
                coerced[name] = text
            else:
                coerced[name] = str(value)
        elif field.get("required"):
            raise ValueError(f"missing required argument: {name}")
    return coerced


# Backend result states meaning the requested work did NOT happen. A typed
# refusal/denial is a successful *outcome* for the dedicated ``confirm=False``
# refusal operations and for ops that deliver a gate denial as their honest
# result (``actions_retry``), so those tokens only count as failures on the
# confirmed mutation path, where a refusal means nothing changed.
_EXTRA_FAILURE_STATES = frozenset({
    "failed", "missing", "error", "unavailable", "invalid",
    "estop", "refused", "rejected", "blocked",
})
_EXTRA_REFUSAL_STATES = frozenset({"refused", "rejected", "blocked", "delegated"})


def _extra_result_failure(result: Any, *, refusals_are_failure: bool
                          ) -> Optional[Dict[str, str]]:
    """Typed failure for an op result whose top-level ``state`` is not success.

    Extra ops return a structured ``{"state": ...}`` result; the envelope used
    to hard-code ``executed`` regardless, so a failed/refused mutation (model
    profile rollback, create without a key_ref, ...) looked like success. This
    surfaces the true state. Results without a top-level ``state`` (nested
    report shapes) and dedicated typed refusals on the unconfirmed path are
    left to the envelope's existing behavior.
    """
    if not isinstance(result, dict):
        return None
    state = str(result.get("state") or "").strip().lower()
    if not state or state not in _EXTRA_FAILURE_STATES:
        return None
    refusal = state in _EXTRA_REFUSAL_STATES
    if refusal and not refusals_are_failure:
        return None
    return {
        "state": "refused" if refusal else "failed",
        "code": str(result.get("code") or "OPERATION_FAILED")[:80],
        "detail": _redact(str(result.get("detail") or result.get("message")
                              or state))[:300],
    }


def run_extra_op(cfg: Any, op_id: str, args: Dict[str, Any], *,
                 actor: str = "dashboard:user",
                 session: str = "dashboard") -> Dict[str, Any]:
    spec = _lookup_extra(op_id)
    if spec is None or spec.run is None:
        return {"state": "failed", "code": "UNKNOWN_OP",
                "detail": f"unknown operation {op_id!r}"}
    try:
        coerced = _coerce_extra(spec, args)
    except ValueError as exc:
        _audit("rejected", actor, {"op": op_id, "reason": str(exc)[:120]})
        return {"state": "failed", "code": "BAD_ARGS",
                "detail": _redact(str(exc))[:200]}
    if _estop(cfg) or (spec.estop is not None and spec.estop(cfg)):
        _audit("estop", actor, {"op": op_id, "session": session})
        return {"state": "refused", "code": "ESTOP_LATCHED",
                "detail": "Emergency stop is latched. Reset it first."}
    if not spec.confirm:
        try:
            result = spec.run(cfg, coerced, actor)
        except Exception as exc:
            code = getattr(exc, "code", None) or type(exc).__name__
            _audit("failed", actor, {"op": op_id, "code": str(code)[:60]})
            return {"state": "failed", "code": str(code)[:60],
                    "detail": _redact(str(exc))[:300]}
        failure = _extra_result_failure(result, refusals_are_failure=False)
        if failure is not None:
            _audit("failed", actor,
                   {"op": op_id, "code": failure["code"]})
            return {"state": failure["state"], "code": failure["code"],
                    "detail": failure["detail"],
                    "receipt": {"entry": op_id, "op": op_id, "result": result}}
        _audit("executed", actor, {"op": op_id, "session": session})
        return {"state": "executed", "speech": f"{spec.title}: done.",
                "receipt": {"entry": op_id, "op": op_id, "result": result}}
    journal = _journal(cfg)
    secret_names = _sensitive_names(spec)
    sensitive = sorted(k for k in coerced if k in secret_names)
    staged = {k: v for k, v in coerced.items() if k not in secret_names}
    shown = ", ".join(f"{k} {_redact(str(v))[:40]}" for k, v in
                      sorted(staged.items()))[:200]
    wording = f"{spec.title}: confirm to proceed. {shown}".strip()
    try:
        approval = journal.create(
            f"dashboard.{op_id}", wording, ttl_s=120.0, session=session,
            args=_redacted_args(spec, staged), sensitive=sensitive, op=op_id,
            actor=actor)
    except Exception as exc:
        return {"state": "failed", "code": "CONFIRM_STAGE_ERROR",
                "detail": _redact(str(exc))[:200]}
    _audit("needs_confirm", actor,
           {"op": op_id, "confirmation_id": approval.id, "session": session})
    out: Dict[str, Any] = {"state": "needs-confirm", "speech": wording,
                           "confirmation_id": approval.id}
    if sensitive:
        out["sensitive_required"] = sensitive
    return out


def confirm_extra_op(cfg: Any, confirmation_id: str, *,
                     actor: str = "dashboard:user",
                     session: str = "dashboard",
                     sensitive: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    journal = _journal(cfg)
    try:
        pending = {a.id: a for a in journal.pending()}
    except Exception as exc:
        return {"state": "failed", "code": "JOURNAL_ERROR",
                "detail": _redact(str(exc))[:200]}
    target = pending.get(confirmation_id)
    if target is None:
        return {"state": "failed", "code": "NO_CONFIRMATION",
                "detail": "That confirmation is gone."}
    if not str(getattr(target, "capability", "")).startswith("dashboard."):
        return {"state": "failed", "code": "WRONG_CONFIRM_CHANNEL",
                "detail": "That confirmation belongs to another channel."}
    meta = getattr(target, "meta", {}) or {}
    if meta.get("session", "dashboard") != session:
        return {"state": "failed", "code": "SESSION_MISMATCH",
                "detail": "That confirmation belongs to another session."}
    op_id = str(meta.get("op", ""))
    spec = _lookup_extra(op_id)
    if spec is None or spec.run is None:
        return {"state": "failed", "code": "STALE_OP",
                "detail": "That confirmation is stale."}
    if _estop(cfg) or (spec.estop is not None and spec.estop(cfg)):
        try:
            journal.respond(target.id, "deny")
        except Exception:
            pass
        return {"state": "refused", "code": "ESTOP_LATCHED",
                "detail": "Emergency stop is latched."}
    needed = list(meta.get("sensitive", []) or [])
    supplied = dict(sensitive or {})
    merged = dict(meta.get("args", {}) or {})
    for name in needed:
        if name not in supplied or not str(supplied[name]):
            return {"state": "failed", "code": "SENSITIVE_REQUIRED",
                    "detail": f"Re-enter {name} to confirm.",
                    "sensitive_required": needed}
        merged[name] = str(supplied[name])
    try:
        journal.respond(target.id, "approve")
    except Exception as exc:
        return {"state": "failed", "code": "SETTLE_ERROR",
                "detail": _redact(str(exc))[:200]}
    if not _mark_executed(cfg, target.id):
        return {"state": "failed", "code": "ALREADY_DONE",
                "detail": "Already done."}
    try:
        result = spec.run(cfg, merged, actor)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        _audit("confirmed_failed", actor,
               {"op": op_id, "confirmation_id": target.id})
        return {"state": "failed", "code": str(code)[:60],
                "detail": _redact(str(exc))[:300]}
    failure = _extra_result_failure(result, refusals_are_failure=True)
    if failure is not None:
        _audit("confirmed_failed", actor,
               {"op": op_id, "confirmation_id": target.id,
                "code": failure["code"]})
        return {"state": failure["state"], "code": failure["code"],
                "detail": failure["detail"],
                "receipt": {"op": op_id, "confirmation_id": target.id,
                            "result": result}}
    _audit("confirmed_executed", actor,
           {"op": op_id, "confirmation_id": target.id})
    return {"state": "executed",
            "speech": f"{spec.title}: done.",
            "receipt": {"op": op_id, "confirmation_id": target.id,
                        "result": result}}


def cancel_extra_op(cfg: Any, confirmation_id: Optional[str] = None, *,
                    actor: str = "dashboard:user",
                    session: str = "dashboard") -> Dict[str, Any]:
    journal = _journal(cfg)
    try:
        items = [a for a in journal.pending()
                 if str(getattr(a, "capability", "")).startswith("dashboard.")
                 and (getattr(a, "meta", {}) or {}).get("session", "dashboard")
                 == session]
    except Exception as exc:
        return {"state": "failed", "code": "JOURNAL_ERROR",
                "detail": _redact(str(exc))[:200]}
    if confirmation_id:
        items = [a for a in items if a.id == confirmation_id]
    count = 0
    for item in items:
        try:
            journal.respond(item.id, "deny")
            count += 1
        except Exception:
            pass
    _audit("cancelled", actor, {"session": session, "count": count})
    if count == 0:
        return {"state": "failed", "code": "NOTHING_PENDING",
                "detail": "There's nothing pending."}
    return {"state": "executed",
            "speech": f"Cancelled {count} pending confirmation(s).",
            "receipt": {"cancelled": count}}


def cancel_compat_op(cfg: Any, confirmation_id: Optional[str] = None, *,
                     actor: str = "dashboard:user",
                     session: str = "dashboard") -> Dict[str, Any]:
    """Deny staged ``qubit.*`` compat confirmations on the shared journal.

    ``qubit_compat._stage_compat_confirm`` stages the confirm-gated compat
    mutations (config write, toolset config, cron create/update/delete, MCP
    server/auth) under a ``qubit.<action>`` capability with the dashboard
    session. They have no dashboard-ops registry row, so before this the
    pending-approvals banner's Cancel answered ``NOTHING_PENDING`` and a staged
    config write could only expire. Deny them here with the same journal and
    audit semantics ``confirm_op`` uses to settle them.
    """
    journal = _journal(cfg)
    try:
        items = [a for a in journal.pending()
                 if str(getattr(a, "capability", "")).startswith("qubit.")
                 and (getattr(a, "meta", {}) or {}).get("session", "dashboard")
                 == session]
    except Exception as exc:
        return {"state": "failed", "code": "JOURNAL_ERROR",
                "detail": _redact(str(exc))[:200]}
    if confirmation_id:
        items = [a for a in items if a.id == confirmation_id]
    count = 0
    for item in items:
        try:
            journal.respond(item.id, "deny")
            count += 1
        except Exception:
            pass
    if count:
        _audit("cancelled", actor,
               {"session": session, "count": count, "channel": "compat"})
    if count == 0:
        return {"state": "failed", "code": "NOTHING_PENDING",
                "detail": "There's nothing pending."}
    return {"state": "executed",
            "speech": f"Cancelled {count} pending confirmation(s).",
            "receipt": {"cancelled": count}}


def cancel_skill_op(cfg: Any, confirmation_id: Optional[str] = None, *,
                    actor: str = "dashboard:user",
                    session: str = "dashboard") -> Dict[str, Any]:
    """Deny staged ``skill.mutation`` confirmations on the shared journal.

    The verified-skills channel stages create/edit/install/remove confirmations
    under ``skill.mutation`` with the dashboard session. Without a cancel
    branch (mirroring ``cancel_compat_op``/toolset cancel) the pending-
    approvals banner's Cancel answered ``NOTHING_PENDING`` and a staged skill
    mutation stayed ``in_progress`` until its TTL. Deny here with the same
    journal + audit semantics the settle path uses.
    """
    from .verified_skills.mutations import SKILL_MUTATION_CAPABILITY

    journal = _journal(cfg)
    try:
        items = [a for a in journal.pending()
                 if str(getattr(a, "capability", "")) == SKILL_MUTATION_CAPABILITY
                 and (getattr(a, "meta", {}) or {}).get("session", "dashboard")
                 == session]
    except Exception as exc:
        return {"state": "failed", "code": "JOURNAL_ERROR",
                "detail": _redact(str(exc))[:200]}
    if confirmation_id:
        items = [a for a in items if a.id == confirmation_id]
    count = 0
    for item in items:
        try:
            journal.respond(item.id, "deny")
            count += 1
        except Exception:
            pass
    if count:
        _audit("cancelled", actor,
               {"session": session, "count": count, "channel": "skill"})
    if count == 0:
        return {"state": "failed", "code": "NOTHING_PENDING",
                "detail": "There's nothing pending."}
    return {"state": "executed",
            "speech": f"Cancelled {count} pending confirmation(s).",
            "receipt": {"cancelled": count}}


def _compat_runner(action: str) -> Optional[Callable[[Any, Dict[str, Any]], Any]]:
    """The real canonical backend for a staged ``qubit.*`` action.

    Returns ``None`` for a compat capability with no executable Qubit backend
    (so a settle can never report fabricated success). ``config.write``
    re-enters the SAME ``config_save`` handler the Settings PUT uses, with the
    secret-filtered scalar payload recorded at stage time and ``confirm`` set
    so the write applies exactly once. ``memory.reset``, the hooks
    ``declare``/``remove``/``update`` actions, and ``messaging.platform_update``
    re-enter the same compat handlers with the staged payload, so the
    pending-approvals banner's id-only Confirm executes the real backend exactly
    once.
    """
    if action == "config.write":
        def _run(cfg: Any, payload: Dict[str, Any]) -> Any:
            from . import qubit_compat as HC

            body: Dict[str, Any] = {"confirm": True}
            if payload:
                body["config"] = dict(payload)
            return HC.config_save(cfg, {}, body, {})
        return _run
    if action == "memory.reset":
        def _run(cfg: Any, payload: Dict[str, Any]) -> Any:
            from . import qubit_compat as HC

            target = str((payload or {}).get("target", "all") or "all")
            return HC.h_mem_reset(cfg, {}, {"confirm": True, "target": target},
                                  {})
        return _run
    if action == "hooks.declare":
        def _run(cfg: Any, payload: Dict[str, Any]) -> Any:
            from . import qubit_compat as HC

            return HC.h_hooks_create(cfg, {}, {**dict(payload or {}),
                                               "confirm": True}, {})
        return _run
    if action == "hooks.remove":
        def _run(cfg: Any, payload: Dict[str, Any]) -> Any:
            from . import qubit_compat as HC

            return HC.hooks_delete(cfg, {}, {**dict(payload or {}),
                                             "confirm": True}, {})
        return _run
    if action == "hooks.update":
        def _run(cfg: Any, payload: Dict[str, Any]) -> Any:
            from . import qubit_compat as HC

            return HC.h_hooks_update(cfg, {}, {**dict(payload or {}),
                                               "confirm": True}, {})
        return _run
    if action == "messaging.platform_update":
        def _run(cfg: Any, payload: Dict[str, Any]) -> Any:
            from . import qubit_compat as HC

            staged = dict(payload or {})
            body: Dict[str, Any] = {"confirm": True}
            if isinstance(staged.get("enabled"), bool):
                body["enabled"] = staged["enabled"]
            if isinstance(staged.get("env"), dict):
                body["env"] = dict(staged["env"])
            if isinstance(staged.get("clear_env"), list):
                body["clear_env"] = list(staged["clear_env"])
            return HC.msg_update(cfg, {}, body,
                                 {"id": str(staged.get("id", "") or "")})
        return _run
    if action == "skills_create":
        def _run(cfg: Any, payload: Dict[str, Any]) -> Any:
            from . import qubit_compat as HC

            staged = dict(payload or {})
            body: Dict[str, Any] = {"confirm": True,
                                    "skill_id": str(staged.get("skill_id", "") or ""),
                                    "instructions": staged.get("instructions", ""),
                                    "category": staged.get("category", "")}
            return HC.skill_create(cfg, {}, body, {})
        return _run
    if action in ("skills_update", "skills_delete"):
        # The skills editor's rewrite/delete stage through ``_compat_confirm``
        # with the validated scalar set (``skills_update`` carries the new
        # content; ``skills_delete`` the name). The pending-approvals banner's
        # id-only Confirm must re-enter the SAME handler so the edit applies
        # exactly once instead of failing ``CONFIRM_UNSUPPORTED``.
        def _run(cfg: Any, payload: Dict[str, Any]) -> Any:
            from . import qubit_compat as HC

            staged = dict(payload or {})
            sid = str(staged.get("skill_id") or staged.get("name") or "")
            if action == "skills_update":
                return HC.skill_update(
                    cfg, {}, {"confirm": True, "skill_id": sid,
                              "content": str(staged.get("content", ""))},
                    {"id": sid})
            return HC.skill_delete(
                cfg, {}, {"confirm": True, "skill_id": sid, "name": sid}, {})
        return _run
    if action in ("mcp_server_create", "mcp_server_delete",
                  "mcp_server_enabled", "mcp_server_auth"):
        # MCP registry mutations staged by ``_compat_confirm`` (the create
        # payload is the validated, reference-only server fields). The banner's
        # id-only Confirm re-enters the canonical ``voicebridge.mcp_servers``
        # handler so the pending MCP change applies exactly once.
        def _run(cfg: Any, payload: Dict[str, Any]) -> Any:
            from . import qubit_compat as HC

            staged = dict(payload or {})
            name = str(staged.get("name", "") or "")
            if action == "mcp_server_create":
                fields = staged.get("fields")
                body: Dict[str, Any] = {"confirm": True, "name": name}
                if isinstance(fields, dict):
                    body.update(fields)
                return HC.mcp_create(cfg, {}, body, {})
            if action == "mcp_server_delete":
                return HC.mcp_delete(cfg, {}, {"confirm": True, "name": name},
                                     {})
            if action == "mcp_server_enabled":
                return HC.mcp_set_enabled(
                    cfg, {}, {"confirm": True, "name": name,
                              "enabled": bool(staged.get("enabled"))}, {})
            return HC.mcp_auth(
                cfg, {}, {"confirm": True, "name": name,
                          "reference": str(staged.get("reference", ""))}, {})
        return _run
    if action in ("cron.pause", "cron.resume", "cron.run_due"):
        # The cron row actions (pause/resume/trigger) stage via
        # ``_cron_mutate`` with ``payload={"id": job_id}``. Re-enter the same
        # mutation with the staged id so the pending-approvals banner's id-only
        # Confirm executes the real ``schedules`` backend exactly once, rather
        # than failing ``CONFIRM_UNSUPPORTED``.
        fn_name = {"cron.pause": "pause", "cron.resume": "resume",
                   "cron.run_due": "run_due"}[action]

        def _run(cfg: Any, payload: Dict[str, Any]) -> Any:
            from . import qubit_compat as HC

            staged = dict(payload or {})
            return HC._cron_mutate(fn_name, cfg, {}, {"confirm": True},
                                   {"id": str(staged.get("id", "") or "")})
        return _run
    if action in ("cron.create", "cron.update", "cron.delete"):
        # Cron create/edit/delete stage through ``_compat_confirm`` with the
        # validated scalar payload recorded at stage time. The pending-approvals
        # banner's id-only Confirm re-enters the SAME compat handler so the
        # schedule write applies exactly once (previously only pause/resume/
        # trigger had a runner and create answered ``CONFIRM_UNSUPPORTED``).
        def _run(cfg: Any, payload: Dict[str, Any]) -> Any:
            from . import qubit_compat as HC

            staged = dict(payload or {})
            if action == "cron.create":
                if not staged.get("name") or not staged.get("when"):
                    return 400, {
                        "ok": False, "code": "CONFIRM_UNSUPPORTED",
                        "detail": "cron.create confirmation is missing its "
                                  "staged payload; cancel and retry."}
                body = {"confirm": True,
                        **{k: v for k, v in staged.items() if k != "id"}}
                return HC.cron_create(cfg, {}, body, {})
            if action == "cron.update":
                body = {"confirm": True,
                        **{k: v for k, v in staged.items() if k != "id"}}
                return HC.cron_update(
                    cfg, {}, body,
                    {"id": str(staged.get("id", "") or "")})
            return HC.cron_delete(cfg, {}, {"confirm": True},
                                  {"id": str(staged.get("id", "") or "")})
        return _run
    if action.startswith("webhook_"):
        # Webhook create/enable/delete/rotate/test/retry are staged by
        # ``_compat_confirm`` as ``webhook_*``; the pending-approvals banner's
        # id-only Confirm must execute the real handler with the staged payload
        # (create carries name/url/events/secret_ref), exactly once.
        def _run(cfg: Any, payload: Dict[str, Any]) -> Any:
            from . import qubit_compat as HC

            staged = dict(payload or {})
            sid = str(staged.get("id", "") or "")
            if action == "webhook_create":
                body = {"confirm": True,
                        "id": sid,
                        "name": str(staged.get("name", "") or ""),
                        "url": str(staged.get("url", "") or ""),
                        "events": list(staged.get("events", []) or []),
                        "secret_ref": str(staged.get("secret_ref", "") or "")}
                return HC.h_webhooks_mutate("add", cfg, {}, body, {})
            if action == "webhook_enable":
                return HC.webhooks_enable(cfg, {}, {"confirm": True}, {})
            if action == "webhook_enabled":
                return HC.webhooks_enabled(cfg, {}, {"confirm": True, "id": sid},
                                           {"id": sid})
            if action == "webhook_delete":
                return HC.webhooks_delete(cfg, {}, {"confirm": True, "id": sid},
                                          {"id": sid})
            handler = {"webhook_rotate": HC.webhooks_rotate,
                       "webhook_test": HC.webhooks_test,
                       "webhook_retry": HC.webhooks_retry}.get(action)
            if handler is not None:
                return handler(cfg, {}, {"confirm": True, "id": sid},
                               {"id": sid})
            return None
        return _run
    if action in ("kanban.create", "kanban.boards_create", "kanban.update"):
        def _run(cfg, payload):
            from . import qubit_compat as HC

            staged = dict(payload or {})
            if action == "kanban.boards_create":
                return HC.h_plugin_kanban_boards_create(
                    cfg, {}, {**staged, "confirm": True}, {})
            if action == "kanban.create":
                return HC.h_plugin_kanban_tasks_create(
                    cfg, {}, {**staged, "confirm": True}, {})
            tid = str(staged.get("task_id", "") or "")
            return HC.h_plugin_kanban_tasks_patch(
                cfg, {}, {**staged, "confirm": True}, {"task_id": tid})
        return _run
    return None


def _confirm_compat_one(cfg: Any, item: Any, *, actor: str, session: str,
                        supplied: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Settle one ``qubit.*`` journal entry once and run its real backend."""
    from .approvals import ABORTED, COMPLETE, DENIED

    capability = str(getattr(item, "capability", "") or "")
    meta = getattr(item, "meta", {}) or {}
    action = str(meta.get("action") or capability[len("qubit."):] or "")
    runner = _compat_runner(action)
    if runner is None:
        return {"state": "failed", "code": "CONFIRM_UNSUPPORTED",
                "detail": f"No executable Qubit backend for {action!r}; "
                          "cancel it instead."}
    payload = dict(meta.get("payload") or meta.get("args") or {})
    if isinstance(supplied, dict):
        payload.update(supplied)
    if _estop(cfg):
        try:
            _journal(cfg).respond(item.id, "deny")
        except Exception:
            pass
        return {"state": "refused", "code": "ESTOP_LATCHED",
                "detail": "Emergency stop is latched."}
    journal = _journal(cfg)
    try:
        settled = journal.respond(item.id, "approve")
    except Exception as exc:
        return {"state": "failed", "code": "SETTLE_ERROR",
                "detail": _redact(str(exc))[:200]}
    if settled.state in (ABORTED, COMPLETE, DENIED):
        return {"state": "failed", "code": "NO_CONFIRMATION",
                "detail": "That confirmation is gone."}
    if not _mark_executed(cfg, item.id):
        return {"state": "failed", "code": "ALREADY_DONE",
                "detail": "Already done."}
    try:
        outcome = runner(cfg, payload)
    except Exception as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        _audit("confirmed_failed", actor,
               {"action": action, "confirmation_id": item.id,
                "code": str(code)[:60]})
        return {"state": "failed", "code": str(code)[:60],
                "detail": _redact(str(exc))[:300]}
    http, body = outcome if isinstance(outcome, tuple) else (200, outcome)
    ok = bool(isinstance(body, dict) and body.get("ok"))
    if not ok:
        code = str((body or {}).get("code") or "BACKEND_REFUSED")[:60]
        _audit("confirmed_failed", actor,
               {"action": action, "confirmation_id": item.id, "code": code})
        return {"state": "failed", "code": code,
                "detail": _redact(str((body or {}).get("detail", "")))[:300]}
    _audit("confirmed_executed", actor,
           {"action": action, "confirmation_id": item.id})
    return {"state": "executed",
            "speech": f"{action}: done.",
            "receipt": {"action": action, "confirmation_id": item.id,
                        "result": body}}


def confirm_compat_op(cfg: Any, confirmation_id: Optional[str] = None, *,
                      actor: str = "dashboard:user",
                      session: str = "dashboard",
                      args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Approve and execute staged ``qubit.*`` compat confirmations once.

    Symmetric to ``cancel_compat_op``/``confirm_extra_op``: it settles on the
    one shared approval journal, routes the recorded action to the real
    canonical backend, and audits the outcome. Missing, expired, already
    settled, or wrong-session confirmations fail closed with a typed code; a
    successful settle consumes the journal entry exactly once, so a replay can
    never double-apply.
    """
    journal = _journal(cfg)
    try:
        items = [a for a in journal.pending()
                 if str(getattr(a, "capability", "")).startswith("qubit.")
                 and (getattr(a, "meta", {}) or {}).get("session", "dashboard")
                 == session]
    except Exception as exc:
        return {"state": "failed", "code": "JOURNAL_ERROR",
                "detail": _redact(str(exc))[:200]}
    if confirmation_id:
        items = [a for a in items if a.id == confirmation_id]
        if not items:
            return {"state": "failed", "code": "NO_CONFIRMATION",
                    "detail": "That confirmation is gone."}
    if not items:
        return {"state": "failed", "code": "NOTHING_PENDING",
                "detail": "There's nothing pending."}
    executed: list = []
    last_failure: Optional[Dict[str, Any]] = None
    for item in items:
        out = _confirm_compat_one(cfg, item, actor=actor, session=session,
                                  supplied=args)
        if out["state"] == "executed":
            executed.append(out)
        else:
            last_failure = out
    if executed:
        return {"state": "executed",
                "speech": f"Confirmed {len(executed)} pending confirmation(s).",
                "receipt": {"confirmed": len(executed),
                            "results": [o.get("receipt") for o in executed]}}
    return last_failure or {"state": "failed", "code": "NOTHING_PENDING",
                            "detail": "There's nothing pending."}


# ---------------------------------------------------------------- dispatch
def run_op(cfg: Any, op_id: str, args: Dict[str, Any], *,
           actor: str = "dashboard:user",
           session: str = "dashboard") -> Dict[str, Any]:
    from . import voice_commands as VC

    if op_id in VC.REGISTRY:
        return run_registry_op(cfg, op_id, args, actor=actor, session=session)
    if op_id in EXTRA_OPS or op_id in MODEL_PROFILE_OPS:
        return run_extra_op(cfg, op_id, args, actor=actor, session=session)
    return {"state": "failed", "code": "UNKNOWN_OP",
            "detail": f"unknown operation {op_id!r}"}


def confirm_op(cfg: Any, confirmation_id: str, *,
               actor: str = "dashboard:user",
               session: str = "dashboard",
               sensitive: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    journal = _journal(cfg)
    try:
        pending = {a.id: a for a in journal.pending()}
    except Exception as exc:
        return {"state": "failed", "code": "JOURNAL_ERROR",
                "detail": _redact(str(exc))[:200]}
    target = pending.get(confirmation_id) if confirmation_id else None
    if target is not None:
        capability = str(getattr(target, "capability", "") or "")
        if capability.startswith("dashboard."):
            return confirm_extra_op(cfg, confirmation_id, actor=actor,
                                    session=session, sensitive=sensitive)
        if capability.startswith("qubit."):
            return confirm_compat_op(cfg, confirmation_id, actor=actor,
                                     session=session, args=sensitive)
        return confirm_registry_op(cfg, confirmation_id, actor=actor,
                                   session=session)
    if confirmation_id:
        # May already be settled, expired, or belong to a session the
        # registry path filters out: surface the honest typed state.
        return {"state": "failed", "code": "NO_CONFIRMATION",
                "detail": "That confirmation is gone."}
    # No id: confirm the session's pending compat and voice confirmations and
    # report the honest aggregate (mirrors cancel_op's no-id path).
    compat = confirm_compat_op(cfg, None, actor=actor, session=session,
                               args=sensitive)
    if compat["state"] == "executed":
        return compat
    return confirm_registry_op(cfg, "", actor=actor, session=session)


def cancel_op(cfg: Any, confirmation_id: Optional[str] = None, *,
              actor: str = "dashboard:user",
              session: str = "dashboard") -> Dict[str, Any]:
    journal = _journal(cfg)
    try:
        pending = {a.id: a for a in journal.pending()}
    except Exception:
        pending = {}
    target = pending.get(confirmation_id) if confirmation_id else None
    if target is not None:
        capability = str(getattr(target, "capability", "") or "")
        if capability.startswith("dashboard."):
            return cancel_extra_op(cfg, confirmation_id, actor=actor,
                                   session=session)
        if capability.startswith("qubit."):
            return cancel_compat_op(cfg, confirmation_id, actor=actor,
                                    session=session)
        from . import toolsets as TS
        from .verified_skills.mutations import SKILL_MUTATION_CAPABILITY

        if capability == SKILL_MUTATION_CAPABILITY:
            # The verified-skills module owns its journal ids (settle-if-owned
            # exists; cancel lives here as the symmetric deny path).
            return cancel_skill_op(cfg, confirmation_id, actor=actor,
                                   session=session)
        if capability == TS.TOOLSET_CAPABILITY:
            # The toolset mutation module owns its journal ids; try its
            # cancel-if-owned contract before the registry fallback (mirrors
            # h_ops_confirm's settle-if-owned branch).
            owned = TS.cancel_toolset_mutation_if_owned(
                cfg, confirmation_id, actor=actor, session=session)
            if owned is not None:
                return owned
        return cancel_registry_op(cfg, confirmation_id, actor=actor,
                                  session=session)
    # No id (or unknown id): cancel session pending on all four channels and
    # report the honest aggregate.
    from . import toolsets as TS

    reg = cancel_registry_op(cfg, confirmation_id, actor=actor,
                             session=session)
    ext = cancel_extra_op(cfg, confirmation_id, actor=actor, session=session)
    compat = cancel_compat_op(cfg, confirmation_id, actor=actor,
                              session=session)
    skill = cancel_skill_op(cfg, confirmation_id, actor=actor,
                            session=session)
    tool = TS.cancel_toolset_mutations(cfg, confirmation_id, actor=actor,
                                       session=session)
    if reg["state"] == "executed" or ext["state"] == "executed" \
            or compat["state"] == "executed" or skill["state"] == "executed" \
            or tool["state"] == "executed":
        total = int(reg.get("receipt", {}).get("cancelled", 0)) + \
            int(ext.get("receipt", {}).get("cancelled", 0)) + \
            int(compat.get("receipt", {}).get("cancelled", 0)) + \
            int(skill.get("receipt", {}).get("cancelled", 0)) + \
            int(tool.get("receipt", {}).get("cancelled", 0))
        return {"state": "executed",
                "speech": f"Cancelled {total} pending confirmation(s).",
                "receipt": {"cancelled": total}}
    return {"state": "failed", "code": "NOTHING_PENDING",
            "detail": "There's nothing pending."}


# ============================================================ Tier-2 extras
# Backend for each extra is the SAME module function the CLI calls, with
# requested_by="dashboard:user" and confirmed=True (confirmation was staged
# above). Module-level emergency-stop probes mirror the CLI dispatch.

def _req(args: str) -> Dict[str, Any]:
    return {"name": args, "type": "str", "required": True}


def _opt(args: str) -> Dict[str, Any]:
    return {"name": args, "type": "str", "required": False}


# ---- Qubit Brain mutations (deintegratecuritate.md: CLI + dashboard real
# flows; voice entries land in the registry, Tier 1 covers them there).
@extra("brain_sweep", group="brain", title="Brain expiry sweep",
       help="Delete expired Brain profile fields (audited, reversible via export).",
       args=(), interfaces=INTERFACES_NO_VOICE,
       interface_reason=REASON_VOICE_CANONICAL,
       voice_guard="brain_profile_sweep", parity=("memory",))
def _extra_brain_sweep(cfg: Any, args: Dict[str, Any],
                       actor: str) -> Dict[str, Any]:
    from . import brain as BR

    return BR.profile_sweep(cfg, requested_by=actor, confirmed=True)


@extra("brain_export", group="brain", title="Brain profile export",
       help="Export scoped Brain profile data (secrets stay as references).",
       args=(_req("principal"), _opt("identity")), confirm=False,
       interfaces=INTERFACES_NO_VOICE,
       interface_reason=REASON_VOICE_CANONICAL,
       voice_guard="brain_profile_export", parity=("memory",))
def _extra_brain_export(cfg: Any, args: Dict[str, Any],
                        actor: str) -> Dict[str, Any]:
    from . import brain as BR

    return BR.profile_export(cfg, args["principal"],
                             identity=args.get("identity", "") or "default")


@extra("brain_onboard_apply", group="brain", title="Brain onboarding apply",
       help="Apply first-run onboarding answers (JSON map). Voice offers the "
            "plan; nested answers need the dashboard form or CLI wizard.",
       args=({"name": "answers", "type": "str", "required": True,
               "secret": True,
               "clarifier": "Paste the answers JSON from the plan preview."},),
       interfaces=INTERFACES_NO_VOICE_PASSPHRASE, interface_reason=REASON_NESTED,
       parity=("memory",))
def _extra_brain_onboard_apply(cfg: Any, args: Dict[str, Any],
                               actor: str) -> Dict[str, Any]:
    import json as _json

    from . import brain as BR

    try:
        answers = _json.loads(args["answers"])
    except ValueError as exc:
        raise BR.BrainError("BRAIN_BAD_ANSWERS",
                            f"answers must be JSON: {exc}") from exc
    if not isinstance(answers, dict):
        raise BR.BrainError("BRAIN_BAD_ANSWERS", "answers must be a JSON object")
    return BR.onboard_apply(cfg, answers, requested_by=actor, confirmed=True)


# ---- Encrypted sync handoff (passphrase-bearing: never journaled at rest).
@extra("sync_offer", group="sync", title="Sync offer",
       help="Create an opt-in encrypted device-bound handoff offer. The "
            "passphrase is never stored; re-enter it to confirm.",
       args=({"name": "passphrase", "type": "str", "required": True,
               "secret": True}, _opt("for_device")),
       interfaces=INTERFACES_NO_VOICE_PASSPHRASE,
       interface_reason=REASON_PASSPHRASE, parity=("sync",))
def _extra_sync_offer(cfg: Any, args: Dict[str, Any],
                      actor: str) -> Dict[str, Any]:
    from . import sync as SY

    return SY.offer(cfg, args["passphrase"],
                    for_device=args.get("for_device", "") or "",
                    requested_by=actor, confirmed=True)


@extra("sync_accept", group="sync", title="Sync accept",
       help="Accept a handoff bundle with its passphrase (enrollment-gated "
            "apply; pre-restore backup kept).",
       args=(_req("bundle"),
             {"name": "passphrase", "type": "str", "required": True,
              "secret": True},
             _opt("offer_id"),
             {"name": "apply", "type": "choice", "required": False,
              "choices": ("true", "false")}),
       interfaces=INTERFACES_NO_VOICE_PASSPHRASE,
       interface_reason=REASON_PASSPHRASE, parity=("sync",))
def _extra_sync_accept(cfg: Any, args: Dict[str, Any],
                       actor: str) -> Dict[str, Any]:
    from . import sync as SY

    return SY.accept(cfg, args["bundle"], args["passphrase"],
                     offer_id=args.get("offer_id", "") or "",
                     apply=(args.get("apply", "false") == "true"),
                     requested_by=actor, confirmed=True)


@extra("sync_revoke", group="sync", title="Sync revoke",
       help="Revoke (tombstone) a sync offer.",
       args=(_req("offer_id"),),
       interfaces=INTERFACES_NO_VOICE,
       interface_reason=REASON_VOICE_CANONICAL,
       voice_guard="sync_revoke_voice", parity=("sync",))
def _extra_sync_revoke(cfg: Any, args: Dict[str, Any],
                       actor: str) -> Dict[str, Any]:
    from . import sync as SY

    return SY.revoke(cfg, args["offer_id"], requested_by=actor, confirmed=True)


# ---- Webhook verify (raw byte payload: dashboard form / CLI only).
@extra("webhook_verify", group="webhooks", title="Webhook verify",
       help="Verify an HMAC webhook delivery (payload hex, signature, "
            "unix timestamp).",
       args=(_req("sub_id"), _req("payload_hex"), _req("signature"),
             {"name": "timestamp", "type": "str", "required": True},
             _opt("sender")),
       interfaces=INTERFACES_NO_VOICE_PASSPHRASE, interface_reason=REASON_BYTES,
       parity=("pairing/webhook/peer",))
def _extra_webhook_verify(cfg: Any, args: Dict[str, Any],
                          actor: str) -> Dict[str, Any]:
    from . import webhooks as WH

    try:
        payload = bytes.fromhex(args["payload_hex"])
    except ValueError as exc:
        raise WH.WebhookError("WEBHOOK_BAD_PAYLOAD",
                              "payload_hex must be hex") from exc
    try:
        timestamp = float(args["timestamp"])
    except ValueError as exc:
        raise WH.WebhookError("WEBHOOK_BAD_TIME",
                              "timestamp must be numeric") from exc
    return WH.verify(cfg, args["sub_id"], payload, args["signature"],
                     timestamp, sender=args.get("sender", "") or "")


# ---- Kanban snapshot import (nested JSON: dashboard form / CLI only).
@extra("kanban_import", group="kanban", title="Kanban import",
       help="Restore a kanban export snapshot (JSON). Counts auto-reconciled.",
       args=({"name": "snapshot", "type": "str", "required": True,
               "clarifier": "Paste the export JSON."},),
       interfaces=INTERFACES_NO_VOICE_PASSPHRASE, interface_reason=REASON_NESTED,
       parity=("kanban",))
def _extra_kanban_import(cfg: Any, args: Dict[str, Any],
                         actor: str) -> Dict[str, Any]:
    import json as _json

    from . import kanban as KB

    try:
        snap = _json.loads(args["snapshot"])
    except ValueError as exc:
        raise KB.KanbanError("KANBAN_BAD_SNAPSHOT",
                             f"snapshot must be JSON: {exc}") from exc
    if not isinstance(snap, dict):
        raise KB.KanbanError("KANBAN_BAD_SNAPSHOT",
                             "snapshot must be a JSON object")
    return KB.import_snapshot(cfg, snap, requested_by=actor, confirmed=True)


# ---- Portable backup create/restore need passphrases (same resupply flow).
# Mirrors `voicebridge export-state` / `import-state`: encrypted bundle bytes
# are written to a 0o600 state-dir file (never echoed back); restore is a
# dry-run plan by default and enrollment-gated on apply.
@extra("backup_create", group="backup", title="Backup create",
       help="Create a passphrase-encrypted portable backup (7C primitives). "
            "Writes a 0o600 .vbp file; returns path and size only.",
       args=({"name": "passphrase", "type": "str", "required": True,
               "secret": True}, _opt("label")),
       interfaces=INTERFACES_NO_VOICE_PASSPHRASE,
       interface_reason=REASON_PASSPHRASE, parity=("backup/import",))
def _extra_backup_create(cfg: Any, args: Dict[str, Any],
                         actor: str) -> Dict[str, Any]:
    import os as _os
    import time as _time

    from . import portable as PT

    bundle = PT.export_bundle(cfg, args["passphrase"])
    label = (args.get("label", "") or "").strip() or "dashboard"
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in label)[:24]
    out = cfg.state_dir() / f"portable-{int(_time.time())}-{safe}.vbp"
    out.write_bytes(bundle)
    try:
        _os.chmod(out, 0o600)
    except OSError:
        pass
    return {"path": str(out), "bytes": len(bundle)}


@extra("backup_restore", group="backup", title="Backup restore",
       help="Preview/restore a portable backup (dry-run plan by default; "
            "enrollment-gated apply with pre-restore backup). Bundle is "
            "base64 content or a state-dir .vbp path.",
       args=(_req("bundle"),
             {"name": "passphrase", "type": "str", "required": True,
              "secret": True},
             {"name": "apply", "type": "choice", "required": False,
              "choices": ("true", "false")}),
       interfaces=INTERFACES_NO_VOICE_PASSPHRASE,
       interface_reason=REASON_PASSPHRASE, parity=("backup/import",))
def _extra_backup_restore(cfg: Any, args: Dict[str, Any],
                          actor: str) -> Dict[str, Any]:
    import base64 as _b64
    from pathlib import Path as _Path

    from . import device as _Dev
    from . import portable as PT

    raw = args["bundle"]
    blob: Optional[bytes] = None
    if raw.strip().startswith("QVZP") or len(raw) > 512:
        try:
            blob = _b64.b64decode(raw)
        except Exception:
            blob = None
    if blob is None:
        candidate = _Path(raw)
        if not candidate.is_absolute():
            candidate = cfg.state_dir() / candidate.name
        if candidate.suffix != ".vbp" or not candidate.exists():
            raise PT.PortableError("bundle must be base64 content or a "
                                   "state-dir .vbp path")
        blob = candidate.read_bytes()
    info = PT.bundle_info(blob)
    payload = PT.read_bundle(blob, args["passphrase"])
    apply = (args.get("apply", "false") == "true")
    plan = PT.plan_restore(cfg, payload, dry_run=not apply)
    if not apply:
        try:
            _Dev.require_enrolled(cfg)
            enrolled = True
        except Exception:
            enrolled = False
        return {"dry_run": True, "enrolled": enrolled,
                "schema_version": info.schema_version,
                "plan": [str(a) for a in plan.actions]}
    _Dev.require_enrolled(cfg)
    result = PT.restore_state(cfg, payload, dry_run=False)
    return {"restored": True, "backup": str(result.get("backup", "")),
            "plan": [str(a) for a in plan.actions]}


# ---- Typed refusal parity for dashboard surfaces that must stay refused.
@extra("browser_close", group="browser", title="Browser close-profile",
       help="Typed refusal: personal browser profiles are never touched.",
       args=(_opt("profile"),), confirm=False, parity=("browser",))
def _extra_browser_close(cfg: Any, args: Dict[str, Any],
                         actor: str) -> Dict[str, Any]:
    from . import browser as BW

    return BW.refuse_close_profile(args.get("profile", "") or "")


@extra("proxy_start", group="proxy", title="Proxy start",
       help="Typed refusal: no forwarding server runs (would bypass egress "
            "policy and audit).",
       args=(), confirm=False, interfaces=INTERFACES_NO_VOICE,
       interface_reason=REASON_VOICE_CANONICAL,
       voice_guard="proxy_start_refusal", parity=("proxy",))
def _extra_proxy_start(cfg: Any, args: Dict[str, Any],
                       actor: str) -> Dict[str, Any]:
    from . import proxy_status as PX

    return PX.refuse_start()


@extra("acp_serve", group="editor", title="ACP serve",
       help="Typed refusal: no ACP server is shipped.",
       args=(), confirm=False, interfaces=INTERFACES_DASHBOARD,
       interface_reason=REASON_DASHBOARD_REFUSAL,
       voice_guard="acp_serve_refusal", parity=("acp/lsp",))
def _extra_acp_serve(cfg: Any, args: Dict[str, Any],
                     actor: str) -> Dict[str, Any]:
    from . import acp_lsp as AL

    return AL.acp_refuse_serve()


@extra("acp_accept_hooks", group="editor", title="ACP accept-hooks",
       help="Typed refusal: upstream hook bypass flags are never honored.",
       args=(), confirm=False, interfaces=INTERFACES_DASHBOARD,
       interface_reason=REASON_DASHBOARD_REFUSAL,
       voice_guard="acp_accept_hooks_refusal", parity=("acp/lsp",))
def _extra_acp_accept_hooks(cfg: Any, args: Dict[str, Any],
                            actor: str) -> Dict[str, Any]:
    from . import acp_lsp as AL

    return AL.acp_refuse_accept_hooks()


@extra("lsp_install", group="editor", title="LSP install",
       help="Typed refusal: language-server binaries are never downloaded.",
       args=(_opt("server"),), confirm=False, interfaces=INTERFACES_DASHBOARD,
       interface_reason=REASON_DASHBOARD_REFUSAL,
       voice_guard="lsp_install_refusal", parity=("acp/lsp",))
def _extra_lsp_install(cfg: Any, args: Dict[str, Any],
                       actor: str) -> Dict[str, Any]:
    from . import acp_lsp as AL

    return AL.lsp_refuse_install(args.get("server", "") or "")


@extra("computer_install", group="computer", title="Computer-use install",
       help="Typed refusal: control drivers are never auto-installed on any "
            "surface; the operator installs them deliberately outside Qubit.",
       args=(), confirm=False, interfaces=INTERFACES_DASHBOARD,
       interface_reason=REASON_DASHBOARD_REFUSAL, parity=("computer-use",))
def _extra_computer_install(cfg: Any, args: Dict[str, Any],
                            actor: str) -> Dict[str, Any]:
    from . import computer_use as CU

    return CU.refuse_install()


@extra("import_agent_import", group="agent", title="Import-agent import",
       help="Typed refusal: foreign agent setups need human review; reviewed "
            "artifacts install via skills install.",
       args=(), confirm=False, interfaces=INTERFACES_NO_VOICE,
       interface_reason=REASON_VOICE_CANONICAL,
       voice_guard="import_refusal", parity=("import-agent",))
def _extra_import_agent_import(cfg: Any, args: Dict[str, Any],
                               actor: str) -> Dict[str, Any]:
    from . import import_agent as IA

    fn = getattr(IA, "refuse_import", None)
    if fn is not None:
        return fn()
    return {"state": "REFUSED", "code": "IMPORT_AGENT_REVIEW_REQUIRED",
            "detail": "reviewed artifacts install via skills install; "
                      "credentials are never imported"}


@extra("gateway_supervision", group="gateway", title="Gateway supervision",
       help="Typed refusal: the dashboard never supervises gateway processes "
            "(single event path; the operator supervises processes).",
       args=({"name": "action", "type": "choice", "required": True,
               "choices": ("run", "start", "stop", "restart", "install")},),
       confirm=False, interfaces=INTERFACES_DASHBOARD,
       interface_reason=REASON_DASHBOARD_REFUSAL,
       # Canonical spoken counterpart: the gateway supervision refusal is
       # already a real voice entry, so the dashboard control links to it
       # instead of being dashboard-only (declared, never a duplicate entry).
       voice_guard="gateway_supervision_refusal",
       parity=("gateway", "gateway-manage"))
def _extra_gateway_supervision(cfg: Any, args: Dict[str, Any],
                               actor: str) -> Dict[str, Any]:
    from . import gateway_peers as GW

    return GW.refuse_supervision(args["action"])


@extra("kanban_widget_move", group="kanban", title="Kanban widget move",
       help="Reposition/resize a canvas widget (same backend as "
            "`qubit kanban widget-move`).",
       args=({"name": "widget_id", "type": "int", "required": True},
             _opt("x"), _opt("y"), _opt("w"), _opt("h")),
       interfaces=INTERFACES_NO_VOICE,
       interface_reason=REASON_VOICE_CANONICAL,
       voice_guard="kanban_widget_move_voice", parity=("kanban",))
def _extra_kanban_widget_move(cfg: Any, args: Dict[str, Any],
                              actor: str) -> Dict[str, Any]:
    from . import kanban as KB

    box = {k: int(args[k]) for k in ("x", "y", "w", "h") if args.get(k)}
    return KB.widget_move(cfg, int(args["widget_id"]), requested_by=actor,
                          confirmed=True, **box)


@extra("kanban_role_grant", group="kanban", title="Kanban role grant",
       help="Grant a board role to a delegate principal (same backend as "
            "`qubit kanban role-grant`).",
       args=(_req("board"), _req("principal"),
             {"name": "role", "type": "choice", "required": True,
              "choices": ("owner", "editor", "viewer")}),
       interfaces=INTERFACES_NO_VOICE,
       interface_reason=REASON_VOICE_CANONICAL,
       voice_guard="kanban_role_grant_voice", parity=("kanban",))
def _extra_kanban_role_grant(cfg: Any, args: Dict[str, Any],
                             actor: str) -> Dict[str, Any]:
    from . import kanban as KB

    return KB.role_grant(cfg, args["board"], args["principal"],
                         args["role"], requested_by=actor, confirmed=True)


@extra("kanban_role_revoke", group="kanban", title="Kanban role revoke",
       help="Revoke a board role (same backend as "
            "`qubit kanban role-revoke`).",
       args=(_req("board"), _req("principal")),
       interfaces=INTERFACES_NO_VOICE,
       interface_reason=REASON_VOICE_CANONICAL,
       voice_guard="kanban_role_revoke_voice", parity=("kanban",))
def _extra_kanban_role_revoke(cfg: Any, args: Dict[str, Any],
                              actor: str) -> Dict[str, Any]:
    from . import kanban as KB

    return KB.role_revoke(cfg, args["board"], args["principal"],
                          requested_by=actor, confirmed=True)


@extra("kanban_snapshot_restore", group="kanban",
       title="Kanban snapshot restore",
       help="Restore a canvas snapshot: widgets, layout, roles, and task "
            "positions (same backend as `qubit kanban snapshot-restore`).",
       args=({"name": "snapshot_id", "type": "int", "required": True},),
       interfaces=INTERFACES_NO_VOICE,
       interface_reason=REASON_VOICE_CANONICAL,
       voice_guard="kanban_snapshot_restore_voice", parity=("kanban",))
def _extra_kanban_snapshot_restore(cfg: Any, args: Dict[str, Any],
                                   actor: str) -> Dict[str, Any]:
    from . import kanban as KB

    return KB.snapshot_restore(cfg, int(args["snapshot_id"]),
                               requested_by=actor, confirmed=True)


@extra("opencode_prepare", group="opencode", title="OpenCode prepare",
       help="Issue a fresh hash-bound confirmation for an exact coding task"
            " (same runner the CLI uses; in-project apply binds the hash).",
       args=(_req("task"), _opt("project"),
             {"name": "in_project", "type": "choice", "required": False,
              "choices": ("true", "false")}),
       interfaces=INTERFACES_NO_VOICE_PASSPHRASE,
       interface_reason="preview-hash binding cannot be spoken reliably; "
                        "voice runs use the disposable-worktree entries, "
                        "in-project apply uses this form or the CLI",
       parity=("opencode",))
def _extra_opencode_prepare(cfg: Any, args: Dict[str, Any],
                            actor: str) -> Dict[str, Any]:
    from . import opencode as OC
    from .actions import build_envelope

    OC.register_opencode_specs()
    from .voice_commands import _runner_power

    runner = OC.opencode_runner(cfg, executors={},
                                power=_runner_power(cfg))
    task_args = {"task": args["task"], "disposable": True}
    project = args.get("project", "") or "."
    if str(args.get("in_project", "") or "").lower() == "true":
        task_args["disposable"] = False
    task_args["project"] = project
    spec = OC.opencode_spec(OC.ACT_RUN)
    env = build_envelope(spec, args=task_args, target=project,
                         scope="code")
    out = runner.prepare(OC.ACT_RUN, env, issued_by="dashboard")
    out["action_hash"] = env["action_hash"]
    return out


@extra("opencode_run_confirmed", group="opencode",
       title="OpenCode execute (hash-bound)",
       help="Execute a prepared coding task with its preview hash "
            "(same runner the CLI uses).",
       args=(_req("task"), _opt("project"),
             {"name": "in_project", "type": "choice", "required": False,
              "choices": ("true", "false")},
             _req("expected_hash")),
       interfaces=INTERFACES_NO_VOICE_PASSPHRASE,
       interface_reason="preview-hash binding cannot be spoken reliably; "
                        "voice runs use the disposable-worktree entries, "
                        "in-project apply uses this form or the CLI",
       parity=("opencode",))
def _extra_opencode_run_confirmed(cfg: Any, args: Dict[str, Any],
                                  actor: str) -> Dict[str, Any]:
    from . import opencode as OC
    from .voice_commands import _runner_power

    OC.register_opencode_specs()
    runner = OC.opencode_runner(cfg, executors={},
                                power=_runner_power(cfg))
    task_args = {"task": args["task"], "disposable": True}
    project = args.get("project", "") or "."
    if str(args.get("in_project", "") or "").lower() == "true":
        task_args["disposable"] = False
    task_args["project"] = project
    return OC.request_coding_run(
        runner, project=project, task_args=task_args,
        target=project, scope="code", source="user", confirmed=True,
        expected_hash=args["expected_hash"])


@extra("kanban_delegated", group="kanban", title="Kanban delegated op",
       help="Typed delegation notice: swarm/dispatch/daemon/watch/notify "
            "execution ops delegate to the brokered worker path.",
       args=({"name": "subcommand", "type": "str", "required": True},),
       confirm=False, interfaces=INTERFACES_NO_VOICE,
       interface_reason=REASON_VOICE_CANONICAL,
       voice_guard="kanban_swarm", parity=("kanban",))
def _extra_kanban_delegated(cfg: Any, args: Dict[str, Any],
                            actor: str) -> Dict[str, Any]:
    from . import kanban as KB

    return KB.refuse_delegated(args["subcommand"])


@extra("schedule_dispatch", group="schedules", title="Scheduled dispatch",
       help="Run a due scheduled job through the canonical agent/automation "
            "dispatcher. The job was pre-authorized when the user confirmed "
            "the schedule; the monitor tick settles this op instead of "
            "calling a private executor.",
       args=(_req("task_text"), _opt("session"), _opt("now"),
             {"name": "directive", "type": "choice", "required": False,
              "choices": ("true", "false")}),
       confirm=True, interfaces=INTERFACES_DASHBOARD,
       interface_reason=REASON_VOICE_CANONICAL,
       voice_guard="schedules_run_due", parity=("schedules",))
def _extra_schedule_dispatch(cfg: Any, args: Dict[str, Any],
                             actor: str) -> Dict[str, Any]:
    """One canonical executor for scheduled jobs (free text or directive).

    The free-text path resolves the selected model/provider profile through
    the same ``chat_transport`` resolver+gateway CLI/dashboard use; the
    directive path runs the real ``automation`` dispatcher. Both return the
    typed per-job outcome the scheduler records (never a fabricated OK).
    """
    text = str(args.get("task_text", "") or "")
    moment = None
    if str(args.get("now", "") or "").strip():
        try:
            moment = float(str(args["now"]).strip())
        except (TypeError, ValueError):
            moment = None
    if str(args.get("directive", "") or "").lower() == "true":
        from .automation import run_directive

        return run_directive(cfg, text, now=moment, requested_by=actor)
    session = str(args.get("session", "") or "schedule")
    from . import chat_transport as CT

    res = CT.turn(cfg, text, session=session, role="coding")
    if not isinstance(res, dict) or not res.get("ok"):
        return {"state": "UNAVAILABLE",
                "code": str((res or {}).get("code", "AGENT_ERROR")),
                "detail": str((res or {}).get("detail")
                              or (res or {}).get("error", ""))[:200]}
    return {"state": "OK", "code": "OK",
            "detail": str(res.get("reply", ""))[:300]}


@extra("pets_downloads", group="themes", title="Pet gallery downloads",
       help="Typed refusal: gallery pet downloads are never fetched; "
            "built-in Qubit presets apply locally.",
       args=(), confirm=False, interfaces=INTERFACES_DASHBOARD,
       interface_reason=REASON_VOICE_CANONICAL,
       voice_guard="pets_downloads_refusal", parity=("pets/skin",))
def _extra_pets_downloads(cfg: Any, args: Dict[str, Any],
                          actor: str) -> Dict[str, Any]:
    from . import themes as TH

    return TH.refuse_pet_downloads()


@extra("plugins_remote", group="skills", title="Remote plugin install",
       help="Typed refusal: remote Git/index plugin identifiers are never "
            "fetched; local reviewed manifests install via skills install.",
       args=(_req("identifier"),), confirm=False,
       interfaces=INTERFACES_NO_VOICE,
       interface_reason=REASON_VOICE_CANONICAL,
       voice_guard="plugins_remote_refusal", parity=("plugins-install",))
def _extra_plugins_remote(cfg: Any, args: Dict[str, Any],
                          actor: str) -> Dict[str, Any]:
    return {"state": "BLOCKED", "code": "BLOCKED_REMOTE_PLUGIN",
            "detail": "remote plugin identifiers are never fetched; place a "
                      "reviewed local manifest and use skills install"}


# ---- Passphrase-sealed portable state (voice-impossible by REASON_PASSPHRASE).
@extra("export_state", group="state", title="Export portable state",
       help="Write an encrypted portable bundle (600 perms). The passphrase "
            "is never stored; it lives only in request memory.",
       args=({"name": "passphrase", "type": "str", "required": True,
               "secret": True}, _opt("label")),
       interfaces=INTERFACES_NO_VOICE_PASSPHRASE,
       interface_reason=REASON_PASSPHRASE, parity=("backup",))
def _extra_export_state(cfg: Any, args: Dict[str, Any],
                        actor: str) -> Dict[str, Any]:
    import time as _time

    from . import portable as P

    out = Path(cfg.state_dir()) / f"portable-{int(_time.time())}.vbp"
    bundle = P.export_bundle(cfg, args["passphrase"])
    out.write_bytes(bundle)
    try:
        import os as _os

        _os.chmod(out, 0o600)
    except OSError:
        pass
    return {"bundle": out.name, "bytes": len(bundle)}


@extra("import_state", group="state", title="Import portable state",
       help="Preview a restore plan from a server-side bundle (dry-run "
            "only; the applied restore stays CLI `import-state --apply`). "
            "The passphrase is never stored.",
       args=(_req("bundle_path"),
             {"name": "passphrase", "type": "str", "required": True,
               "secret": True}),
       interfaces=INTERFACES_NO_VOICE_PASSPHRASE,
       interface_reason=REASON_PASSPHRASE, parity=("backup",))
def _extra_import_state(cfg: Any, args: Dict[str, Any],
                        actor: str) -> Dict[str, Any]:
    from . import portable as P

    bundle_path = Path(str(args["bundle_path"])).expanduser()
    if not bundle_path.is_file():
        raise P.PortableError(f"bundle not found: {bundle_path.name}")
    payload = P.read_bundle(bundle_path.read_bytes(), args["passphrase"])
    plan = P.plan_restore(cfg, payload, dry_run=True)
    return {"plan": [str(a) for a in plan.actions],
            "note": "preview only — apply via `qubit import-state --apply`"}


# ---- Structured action envelopes (voice-impossible by REASON_NESTED).
def _action_runner(cfg: Any):
    from . import actions as A
    from .voice_commands import _runner_power

    return A.action_runner(cfg, executors={},
                           power=_runner_power(cfg))


def _args_json(raw: str) -> Dict[str, Any]:
    import json as _json

    from . import actions as A

    try:
        data = _json.loads(raw or "{}")
    except ValueError as exc:
        raise A.ActionValidationError(f"args JSON invalid: {exc}") from None
    if not isinstance(data, dict):
        raise A.ActionValidationError("args must be a JSON object")
    return data


@extra("actions_preview", group="actions", title="Preview action",
       help="Render the real preview for a JSON args envelope (writes "
            "nothing).",
       args=(_req("action_id"), _req("args_json"), _opt("target"),
             _opt("scope")),
       interfaces=INTERFACES_NO_VOICE_PASSPHRASE,
       interface_reason=REASON_NESTED, parity=("actions",))
def _extra_actions_preview(cfg: Any, args: Dict[str, Any],
                           actor: str) -> Dict[str, Any]:
    return _action_runner(cfg).preview(
        args["action_id"], _args_json(args["args_json"]),
        target=args.get("target", "") or "",
        account_scope="", scope=args.get("scope", "") or "")


@extra("actions_prepare", group="actions", title="Prepare action",
       help="Mint a hash-bound action confirmation (JSON envelope).",
       args=(_req("action_id"), _req("envelope_json")),
       interfaces=INTERFACES_NO_VOICE_PASSPHRASE,
       interface_reason=REASON_NESTED, parity=("actions",))
def _extra_actions_prepare(cfg: Any, args: Dict[str, Any],
                           actor: str) -> Dict[str, Any]:
    return _action_runner(cfg).prepare(args["action_id"],
                                       _args_json(args["envelope_json"]),
                                       issued_by="dashboard")


@extra("actions_retry", group="actions", title="Retry action",
       help="Retry a failed receipt with a JSON args envelope.",
       args=(_req("receipt_id"), _req("args_json"), _opt("target"),
             _opt("scope")),
       interfaces=INTERFACES_NO_VOICE_PASSPHRASE,
       interface_reason=REASON_NESTED, parity=("actions",))
def _extra_actions_retry(cfg: Any, args: Dict[str, Any],
                         actor: str) -> Dict[str, Any]:
    return _action_runner(cfg).retry(
        args["receipt_id"], args=_args_json(args["args_json"]),
        target=args.get("target", "") or "",
        scope=args.get("scope", "") or "", account_scope="",
        source="user", confirmed=True)


@extra("actions_recover", group="actions", title="Recover action",
       help="Run the recovery flow for a receipt.",
       args=(_req("receipt_id"),),
       interfaces=INTERFACES_NO_VOICE_PASSPHRASE,
       interface_reason=REASON_NESTED, parity=("actions",))
def _extra_actions_recover(cfg: Any, args: Dict[str, Any],
                           actor: str) -> Dict[str, Any]:
    return _action_runner(cfg).recover(args["receipt_id"], confirmed=True)


@extra("ghost_preview", group="ghost", title="Preview Ghost action",
       help="Render the real Ghost preview for a JSON args envelope "
            "(writes nothing).",
       args=(_req("action_id"), _req("args_json"), _opt("target"),
             _opt("scope")),
       interfaces=INTERFACES_NO_VOICE_PASSPHRASE,
       interface_reason=REASON_NESTED, parity=("ghost",))
def _extra_ghost_preview(cfg: Any, args: Dict[str, Any],
                         actor: str) -> Dict[str, Any]:
    from . import ghost as G

    G.register_ghost_specs()
    from .voice_commands import _runner_power

    runner = G.ghost_runner(cfg, executors={},
                            power=_runner_power(cfg))
    return runner.preview(args["action_id"], _args_json(args["args_json"]),
                          target=args.get("target", "") or "desktop",
                          scope=args.get("scope", "") or "", role="")


# ===================================================== model/provider profiles
# Resolver-backed model/profile operations: the SAME functions the CLI
# (``qubit profile model ...``) and the voice registry call. Reads execute
# immediately; mutations stage on the shared confirmation journal. A raw
# credential value is refused before it can be staged or written (secret
# references only). These live in MODEL_PROFILE_OPS, not EXTRA_OPS, to keep
# the pinned read-only allowlist exact; they share the one run/confirm/audit
# pipeline by construction.
_MODEL_ARGS = dict(
    interfaces=INTERFACES_NO_VOICE,
    interface_reason=REASON_VOICE_CANONICAL,
    voice_guard="config_profiles",
    parity=("config", "profile"),
)


def _choice(name: str, choices: Tuple[str, ...],
            required: bool = False) -> Dict[str, Any]:
    return {"name": name, "type": "choice", "required": required,
            "choices": list(choices)}


def _int(name: str, required: bool = False) -> Dict[str, Any]:
    return {"name": name, "type": "int", "required": required}


@model_op("list_model_profiles", group="profile",
          title="List model profiles",
          help="List the canonical model/provider profiles and their gates.",
          confirm=False, args=(), **_MODEL_ARGS)
def _op_list_model_profiles(cfg: Any, args: Dict[str, Any],
                            actor: str) -> Dict[str, Any]:
    from . import model_profiles_ops as MP

    return MP.list_model_profiles(cfg)


@model_op("show_model_profile", group="profile",
          title="Show model profile",
          help="Show one model profile: capabilities, locality, egress, "
               "budgets, health and effective selection.",
          confirm=False, args=(_req("profile_id"),), **_MODEL_ARGS)
def _op_show_model_profile(cfg: Any, args: Dict[str, Any],
                           actor: str) -> Dict[str, Any]:
    from . import model_profiles_ops as MP

    return MP.show_model_profile(cfg, args["profile_id"])


@model_op("test_model_profile", group="profile",
          title="Test model profile",
          help="Preflight a model profile (read-only; optional local probe).",
          confirm=False, args=(_opt("profile_id"), _opt("probe")),
          **_MODEL_ARGS)
def _op_test_model_profile(cfg: Any, args: Dict[str, Any],
                           actor: str) -> Dict[str, Any]:
    from . import model_profiles_ops as MP

    return MP.test_model_profile(cfg, args.get("profile_id", ""),
                                 probe=args.get("probe", False))


@model_op("create_model_profile", group="profile",
          title="Create model profile",
          help="Register a model profile (non-secret fields only; key_ref is "
               "an env:/file: reference, never a raw key).",
          args=(_req("profile_id"),
                _choice("kind", ("local", "api", "realtime", "plugin")),
                _opt("provider"), _opt("model"), _opt("base_url"),
                {"name": "key_ref", "type": "str", "required": False,
                 "secret": True},
                _choice("locality", ("local", "external")),
                _choice("egress_consent", ("deny", "allow")),
                _int("context_tokens"), _opt("supports_tools"),
                _opt("supports_vision"), _opt("roles"), _opt("fallback_to")),
          **_MODEL_ARGS)
def _op_create_model_profile(cfg: Any, args: Dict[str, Any],
                             actor: str) -> Dict[str, Any]:
    from . import model_profiles_ops as MP

    return MP.create_model_profile(cfg, confirm=True, **args)


@model_op("clone_model_profile", group="profile",
          title="Clone model profile",
          help="Clone a model profile under a new id (records the source as "
               "the rollback target).",
          args=(_req("source_id"), _req("new_id")), **_MODEL_ARGS)
def _op_clone_model_profile(cfg: Any, args: Dict[str, Any],
                            actor: str) -> Dict[str, Any]:
    from . import model_profiles_ops as MP

    return MP.clone_model_profile(cfg, args["source_id"], args["new_id"],
                                  confirm=True)


@model_op("remove_model_profile", group="profile",
          title="Remove model profile",
          help="Remove a non-default model profile (refused while a session "
               "selects it).",
          args=(_req("profile_id"),), **_MODEL_ARGS)
def _op_remove_model_profile(cfg: Any, args: Dict[str, Any],
                             actor: str) -> Dict[str, Any]:
    from . import model_profiles_ops as MP

    return MP.remove_model_profile(cfg, args["profile_id"], confirm=True)


@model_op("select_session_model", group="profile",
          title="Select session model",
          help="Select a model profile for one session/role (confirmed).",
          args=(_req("session"), _req("profile_id"), _opt("role")),
          **_MODEL_ARGS)
def _op_select_session_model(cfg: Any, args: Dict[str, Any],
                             actor: str) -> Dict[str, Any]:
    from . import model_profiles_ops as MP

    return MP.select_session_model(
        cfg, args["session"], args["profile_id"],
        role=args.get("role", "") or "chat", confirm=True)


@model_op("clear_session_model", group="profile",
          title="Clear session model",
          help="Clear this session's model override (confirmed).",
          args=(_req("session"), _opt("role")), **_MODEL_ARGS)
def _op_clear_session_model(cfg: Any, args: Dict[str, Any],
                            actor: str) -> Dict[str, Any]:
    from . import model_profiles_ops as MP

    return MP.clear_session_model(cfg, args["session"],
                                  role=args.get("role", "") or "*",
                                  confirm=True)


@model_op("rollback_model_profile", group="profile",
          title="Rollback model profile",
          help="Select a profile's declared rollback target for a session "
               "(confirmed; typed NO_ROLLBACK when none is declared).",
          args=(_req("profile_id"), _opt("session"), _opt("role")),
          **_MODEL_ARGS)
def _op_rollback_model_profile(cfg: Any, args: Dict[str, Any],
                               actor: str) -> Dict[str, Any]:
    from . import model_profiles_ops as MP

    return MP.rollback_model_profile(
        cfg, args["profile_id"],
        session=args.get("session", "") or "dashboard",
        role=args.get("role", "") or "chat", confirm=True)


# ===================================================== speech profiles
# Canonical STT/TTS/realtime profile lifecycle. The SAME
# ``voicebridge.providers.speech`` registry the orchestrator's ASR/TTS seams
# resolve through, the CLI (``qubit profile speech ...``), the Qubit RPC
# surface, and the voice registry all share. Reads execute immediately;
# mutations stage on the one confirmation journal; ``key_ref`` is an
# ``env:``/``file:`` reference only (a raw key is refused). These live in
# MODEL_PROFILE_OPS so they flow through the identical run/confirm/audit/
# journal/e-stop pipeline and never become a second mount of the provider.
_SPEECH_ARGS = dict(
    interfaces=INTERFACES_NO_VOICE,
    interface_reason=REASON_VOICE_CANONICAL,
    voice_guard="speech_profile_test",
    parity=("config", "profile"),
)


def _bool(name: str, required: bool = False) -> Dict[str, Any]:
    return {"name": name, "type": "bool", "required": required}


def _speech_profile_from_args(args: Dict[str, Any]) -> Any:
    from .providers.speech import SpeechProfile

    fields = {
        "id": str(args.get("profile_id") or "").strip(),
        "kind": str(args.get("kind") or "speech_in"),
        "provider": str(args.get("provider") or "").strip(),
        "model": str(args.get("model") or "").strip(),
        "voice": str(args.get("voice") or "").strip(),
        "device": str(args.get("device") or "").strip(),
        "language": str(args.get("language") or "").strip(),
        "locality": str(args.get("locality") or "local"),
        "egress_consent": str(args.get("egress_consent") or "deny"),
        "key_ref": str(args.get("key_ref") or "").strip(),
        "base_url": str(args.get("base_url") or "").strip(),
        "engine": str(args.get("engine") or "").strip(),
        "fallback_to": str(args.get("fallback_to") or "").strip(),
    }
    return SpeechProfile.from_record(fields)


@model_op("list_speech_profiles", group="profile",
          title="List speech profiles",
          help="List the canonical STT/TTS/realtime speech profiles and gates.",
          confirm=False, args=(_opt("kind"),), **_SPEECH_ARGS)
def _op_list_speech_profiles(cfg: Any, args: Dict[str, Any],
                             actor: str) -> Dict[str, Any]:
    from .providers import resolver as R

    return R.list_speech_profiles(cfg, args.get("kind") or None)


@model_op("show_speech_profile", group="profile",
          title="Show speech profile",
          help="Show one speech profile (STT/TTS/realtime) with its effective "
               "selection and gate.",
          confirm=False, args=(_req("profile_id"),), **_SPEECH_ARGS)
def _op_show_speech_profile(cfg: Any, args: Dict[str, Any],
                            actor: str) -> Dict[str, Any]:
    from .providers import resolver as R

    return R.show_speech_profile(cfg, args["profile_id"])


@model_op("test_speech_profile", group="profile",
          title="Test speech profile",
          help="Preflight the active (or named) speech profile (read-only).",
          confirm=False, args=(_opt("profile_id"), _opt("kind")), **_SPEECH_ARGS)
def _op_test_speech_profile(cfg: Any, args: Dict[str, Any],
                            actor: str) -> Dict[str, Any]:
    from .providers import resolver as R
    from .providers import speech as SP

    pid = str(args.get("profile_id") or "").strip()
    kind = str(args.get("kind") or "").strip() or SP.SPEECH_IN
    if pid:
        return SP.show_speech_profile(cfg, pid)
    eff = R.resolve_speech_in(cfg) if kind == SP.SPEECH_IN else (
        R.resolve_speech_out(cfg) if kind == SP.SPEECH_OUT
        else R.resolve_realtime(cfg))
    return {"state": "OK", "kind": kind, "effective": eff.to_public_dict()}


@model_op("create_speech_profile", group="profile",
          title="Create speech profile",
          help="Register a speech profile (non-secret fields only; key_ref is "
               "an env:/file: reference, never a raw key).",
          args=(_req("profile_id"),
                _choice("kind", ("speech_in", "speech_out", "realtime")),
                _opt("provider"), _opt("model"), _opt("voice"),
                _opt("device"), _opt("language"),
                _choice("locality", ("local", "external")),
                _choice("egress_consent", ("deny", "allow")),
                {"name": "key_ref", "type": "str", "required": False,
                 "secret": True},
                _opt("base_url"), _opt("engine"), _opt("fallback_to")),
          **_SPEECH_ARGS)
def _op_create_speech_profile(cfg: Any, args: Dict[str, Any],
                              actor: str) -> Dict[str, Any]:
    from .providers import resolver as R

    return R.upsert_speech_profile(cfg, _speech_profile_from_args(args),
                                   confirm=True)


@model_op("clone_speech_profile", group="profile",
          title="Clone speech profile",
          help="Clone a speech profile under a new id (records the source as "
               "the rollback target).",
          args=(_req("source_id"), _req("new_id")), **_SPEECH_ARGS)
def _op_clone_speech_profile(cfg: Any, args: Dict[str, Any],
                             actor: str) -> Dict[str, Any]:
    from .providers import resolver as R

    return R.clone_speech_profile(cfg, args["source_id"], args["new_id"],
                                  confirm=True)


@model_op("remove_speech_profile", group="profile",
          title="Remove speech profile",
          help="Remove a non-default speech profile (refused while a session "
               "selects it).",
          args=(_req("profile_id"),), **_SPEECH_ARGS)
def _op_remove_speech_profile(cfg: Any, args: Dict[str, Any],
                              actor: str) -> Dict[str, Any]:
    from .providers import resolver as R

    return R.remove_speech_profile(cfg, args["profile_id"], confirm=True)


@model_op("select_session_speech", group="profile",
          title="Select session speech profile",
          help="Select a speech profile for one session/kind (confirmed).",
          args=(_req("session"), _req("profile_id"), _req("kind")),
          **_SPEECH_ARGS)
def _op_select_session_speech(cfg: Any, args: Dict[str, Any],
                              actor: str) -> Dict[str, Any]:
    from .providers import speech as SP

    return SP.set_session_speech_profile(
        cfg, args["session"], args["kind"], args["profile_id"], confirm=True)


@model_op("clear_session_speech", group="profile",
          title="Clear session speech profile",
          help="Clear a session's speech profile override (confirmed).",
          args=(_req("session"), _opt("kind")), **_SPEECH_ARGS)
def _op_clear_session_speech(cfg: Any, args: Dict[str, Any],
                             actor: str) -> Dict[str, Any]:
    from .providers import speech as SP

    return SP.clear_session_speech_profile(
        cfg, args["session"], kind=(args.get("kind") or None), confirm=True)

