"""Thin typed operations for owner-created scoped autonomy profiles.

This module is the composition entry point between the autonomy store and the
existing authority modules. It reads — never mutates — the broker e-stop probe
and the permission hard-deny classifier, so an autonomy profile can never
bypass the emergency stop, audit, or a hard-deny capability class.

Authorization is two-layered:

  1. a hard-deny capability pattern (permissions, read-only) or a latched
     e-stop (broker, read-only) denies unconditionally; then
  2. :func:`voicebridge.autonomy.authorize` decides within the profile.

Only when both layers allow is the operation recorded and an audit receipt
emitted. Nothing here creates a second broker, policy store, or execution path.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import autonomy
from .autonomy import (
    AutonomyBudget,
    AutonomyDecision,
    AutonomyDenial,
    AutonomyError,
    AutonomyProfile,
)
from .broker import estop_engaged
from .logging_setup import log_event
from .permissions import is_hard_deny_auto

DENY_HARD_DENY = "HARD_DENY"

# Codes that mean "this profile does not govern the request at all" (no such
# profile id, or the owner/session/device binding already excludes it). They
# are not refusals: the caller falls back to its existing confirmation flow.
# Everything else (expired/paused/revoked/out-of-window/scope/target/budget/
# concurrency/e-stop) is a typed refusal when an applicable profile exists.
_BINDING_SKIP_CODES = frozenset({
    AutonomyDenial.NO_PROFILE.value,
    AutonomyDenial.OWNER_MISMATCH.value,
    AutonomyDenial.BINDING_MISMATCH.value,
})


@dataclass(frozen=True)
class OpDecision:
    """Typed result of :func:`authorize_operation`."""

    allowed: bool
    code: str
    reason: str
    profile_id: str
    capability: str
    target: str
    detail: str = ""
    audit_id: str = ""
    autonomy: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "reason": self.reason,
            "profile_id": self.profile_id,
            "capability": self.capability,
            "target": self.target,
            "detail": self.detail,
            "audit_id": self.audit_id,
            "autonomy": self.autonomy,
        }


def _op_decision(d: Optional[AutonomyDecision], *, allowed: bool, code: str,
                 reason: str, profile_id: str, capability: str, target: str,
                 detail: str = "", audit_id: str = "") -> OpDecision:
    return OpDecision(
        allowed=allowed, code=code, reason=reason, profile_id=profile_id,
        capability=capability, target=target, detail=detail,
        audit_id=audit_id, autonomy=(d.to_dict() if d is not None else None))


def authorize_operation(cfg: Any, profile_id: str, capability: str,
                        target: str = "", *, now: Optional[float] = None,
                        cost: Optional[float] = None,
                        owner: Optional[str] = None,
                        session: Optional[str] = None,
                        device: Optional[str] = None,
                        source: str = "autonomy") -> OpDecision:
    """Authorize one pre-authorized operation under an autonomy profile.

    Composes the read-only hard-deny classifier and the fail-closed e-stop
    probe with :func:`autonomy.authorize`. Records usage and emits one audit
    event for every allowed operation. Never widens scope from request data.
    """
    # Layer 1a: hard-deny capability classes are never auto-authorized.
    hard = is_hard_deny_auto(capability)
    if hard:
        rec = _op_decision(None, allowed=False, code=DENY_HARD_DENY,
                           reason="capability is a hard-deny class",
                           profile_id=profile_id, capability=capability,
                           target=target, detail=hard)
        _receipt("denied", rec, source)
        return rec

    # Layer 1b: a latched emergency stop denies unconditionally.
    try:
        stopped = bool(estop_engaged(cfg))
    except Exception:
        stopped = True  # fail closed
    if stopped:
        rec = _op_decision(None, allowed=False, code=AutonomyDenial.ESTOP_ENGAGED.value,
                           reason="emergency stop is latched",
                           profile_id=profile_id, capability=capability,
                           target=target)
        _receipt("denied", rec, source)
        return rec

    # Layer 2: the owner-authored profile scope/budget decision (pure).
    decision = autonomy.authorize(cfg, profile_id, capability, target,
                                  now=now, cost=cost, owner=owner,
                                  session=session, device=device)
    if not decision.allowed:
        rec = _op_decision(decision, allowed=False, code=decision.code,
                           reason=decision.reason, profile_id=profile_id,
                           capability=capability, target=target,
                           detail=decision.detail)
        _receipt("denied", rec, source)
        return rec

    # Allowed: record consumption and emit the audit receipt.
    audit_id = f"aud_{uuid.uuid4().hex[:16]}"
    try:
        autonomy.autonomy_store(cfg).record_operation(
            profile_id, cost=float(cost or 0.0), now=now)
    except AutonomyError:
        # A profile that vanished between decision and record fails closed.
        rec = _op_decision(decision, allowed=False,
                           code=AutonomyDenial.NO_PROFILE.value,
                           reason="profile disappeared before recording",
                           profile_id=profile_id, capability=capability,
                           target=target)
        _receipt("denied", rec, source)
        return rec
    rec = _op_decision(decision, allowed=True, code=decision.code,
                       reason=decision.reason, profile_id=profile_id,
                       capability=capability, target=target,
                       detail=decision.detail, audit_id=audit_id)
    _receipt("operation", rec, source)
    return rec


def resolve_operation(cfg: Any, capability: str, target: str = "", *,
                      now: Optional[float] = None, cost: Optional[float] = None,
                      owner: Optional[str] = None,
                      session: Optional[str] = None,
                      device: Optional[str] = None,
                      source: str = "broker") -> Optional[OpDecision]:
    """Connect one real capability execution to the autonomy authority.

    This is the single composition point the broker calls before it runs a
    capability. It answers the product question "is a scoped autonomy profile
    currently governing this request?":

    * ``None`` when autonomy is not engaged — no profile exists, or every
      profile's owner/session/device binding excludes this request. The caller
      then preserves its existing per-action confirmation/grant behavior.
    * otherwise exactly one audited :class:`OpDecision`, chosen from the
      binding-matching profiles. An in-scope profile allows (pre-authorized);
      an ACTIVE profile that does not cover the capability/target, or whose
      budget/concurrency/window is exhausted, or a latched e-stop, returns a
      typed refusal carrying the autonomy code.

    Capability/target from the request are only ever containment-checked
    against the owner-authored profile; they can never widen it.
    """
    profiles = autonomy.list_profiles(cfg)
    if not profiles:
        return None
    candidates: List[AutonomyDecision] = []
    for prof in profiles:
        d = autonomy.authorize(cfg, prof.profile_id, capability, target,
                               now=now, cost=cost, owner=owner,
                               session=session, device=device)
        if d.code in _BINDING_SKIP_CODES:
            continue
        candidates.append(d)
    if not candidates:
        return None
    # Prefer a profile that covers the request; otherwise the first applicable
    # refusal (any one is a truthful "not pre-authorized by this owner").
    chosen = next((d for d in candidates if d.allowed), candidates[0])
    return authorize_operation(
        cfg, chosen.profile_id, capability, target, now=now, cost=cost,
        owner=owner, session=session, device=device, source=source)


def _receipt(kind: str, rec: OpDecision, source: str) -> None:
    try:
        log_event(f"autonomy.{kind}", profile_id=rec.profile_id,
                  capability=rec.capability, target=rec.target,
                  allowed=rec.allowed, code=rec.code, source=source,
                  audit_id=rec.audit_id)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Admin wrappers (thin delegations to voicebridge.autonomy)
# ---------------------------------------------------------------------------
def create(cfg: Any, *, owner: str, capabilities: Tuple[str, ...],
           targets: Tuple[str, ...] = (),
           budget: Optional[AutonomyBudget] = None,
           **kwargs: Any) -> AutonomyProfile:
    return autonomy.autonomy_store(cfg).create(
        owner=owner, capabilities=capabilities, targets=targets,
        budget=budget, **kwargs)


def pause(cfg: Any, profile_id: str, *, owner: str,
          now: Optional[float] = None) -> AutonomyProfile:
    return autonomy.pause(cfg, profile_id, owner=owner, now=now)


def resume(cfg: Any, profile_id: str, *, owner: str,
           now: Optional[float] = None) -> AutonomyProfile:
    return autonomy.resume(cfg, profile_id, owner=owner, now=now)


def revoke(cfg: Any, profile_id: str, *, owner: str,
           now: Optional[float] = None) -> AutonomyProfile:
    return autonomy.revoke(cfg, profile_id, owner=owner, now=now)


def expire(cfg: Any, now: Optional[float] = None) -> List[str]:
    return autonomy.expire(cfg, now=now)


def list_profiles(cfg: Any,
                  owner: Optional[str] = None) -> List[AutonomyProfile]:
    return autonomy.list_profiles(cfg, owner=owner)


def show(cfg: Any, profile_id: str) -> Optional[AutonomyProfile]:
    return autonomy.show(cfg, profile_id)


def status(cfg: Any, now: Optional[float] = None) -> Dict[str, Any]:
    out = autonomy.status(cfg, now=now)
    out["estop"] = bool(estop_engaged(cfg))
    return out


__all__ = [
    "DENY_HARD_DENY",
    "OpDecision",
    "authorize_operation",
    "resolve_operation",
    "create",
    "expire",
    "list_profiles",
    "pause",
    "resume",
    "revoke",
    "show",
    "status",
]
