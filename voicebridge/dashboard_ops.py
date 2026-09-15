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

from dataclasses import dataclass, field
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
    target = pending.get(confirmation_id)
    if target is None:
        # May already be settled, expired, or belong to a session the
        # registry path filters out: surface the honest typed state.
        return {"state": "failed", "code": "NO_CONFIRMATION",
                "detail": "That confirmation is gone."}
    capability = str(getattr(target, "capability", "") or "")
    if capability.startswith("dashboard."):
        return confirm_extra_op(cfg, confirmation_id, actor=actor,
                                session=session, sensitive=sensitive)
    return confirm_registry_op(cfg, confirmation_id, actor=actor,
                               session=session)


def cancel_op(cfg: Any, confirmation_id: Optional[str] = None, *,
              actor: str = "dashboard:user",
              session: str = "dashboard") -> Dict[str, Any]:
    journal = _journal(cfg)
    try:
        pending = {a.id: a for a in journal.pending()}
    except Exception:
        pending = {}
    target = pending.get(confirmation_id) if confirmation_id else None
    if target is not None and str(
            getattr(target, "capability", "")).startswith("dashboard."):
        return cancel_extra_op(cfg, confirmation_id, actor=actor,
                               session=session)
    if target is not None:
        return cancel_registry_op(cfg, confirmation_id, actor=actor,
                                  session=session)
    # No id (or unknown id): cancel session pending on both channels and
    # report the honest aggregate.
    reg = cancel_registry_op(cfg, confirmation_id, actor=actor,
                             session=session)
    ext = cancel_extra_op(cfg, confirmation_id, actor=actor, session=session)
    if reg["state"] == "executed" or ext["state"] == "executed":
        total = int(reg.get("receipt", {}).get("cancelled", 0)) + \
            int(ext.get("receipt", {}).get("cancelled", 0))
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
                _choice("kind", ("local", "api", "realtime")),
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

