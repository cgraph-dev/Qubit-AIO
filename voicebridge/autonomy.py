"""Owner-created scoped autonomy profiles (Workstream F).

An autonomy profile is an owner-authored, persisted, expiring pre-authorization
for a bounded set of capability scopes, targets, schedules, and resource
budgets. It replaces **per-action confirmation only** for operations that fall
wholly inside the profile; it never removes authentication, secret isolation,
scope enforcement, concurrency/budget ceilings, audit, cancellation, or the
global emergency stop.

Design contract (enforced here):

  * one canonical, atomic store in the Qubit state tree
    (``<state_dir>/autonomy.json``), never a second policy authority;
  * a profile binds owner + optional session/device;
  * :func:`authorize` is pure: it reads persisted state and returns a typed
    allow/deny decision without mutating anything;
  * a request carrying a capability/target outside the profile scope is denied
    (``CAPABILITY_NOT_IN_SCOPE`` / ``TARGET_NOT_ALLOWED``) — a caller can never
    widen its own authority from request data;
  * the emergency stop is checked fail-closed and is never bypassed.
"""
from __future__ import annotations

import enum
import fnmatch
import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .logging_setup import log_event

SCHEMA_VERSION = 1
STORE_NAME = "autonomy.json"
AUDIT_CAP = 500


def _now() -> float:
    return time.time()


def _new_id(prefix: str = "aut") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class AutonomyError(Exception):
    """Typed failure for autonomy profile administration."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class AutonomyState(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    REVOKED = "revoked"
    EXPIRED = "expired"


class AutonomyDenial(str, enum.Enum):
    """Typed deny/allow reason returned by :func:`authorize`."""

    ALLOWED = "ALLOWED"
    NO_PROFILE = "NO_ACTIVE_PROFILE"
    OWNER_MISMATCH = "OWNER_MISMATCH"
    BINDING_MISMATCH = "BINDING_MISMATCH"
    REVOKED = "REVOKED"
    PAUSED = "PAUSED"
    EXPIRED = "EXPIRED"
    OUT_OF_WINDOW = "OUT_OF_WINDOW"
    ESTOP_ENGAGED = "ESTOP_ENGAGED"
    CAPABILITY_NOT_IN_SCOPE = "CAPABILITY_NOT_IN_SCOPE"
    TARGET_NOT_ALLOWED = "TARGET_NOT_ALLOWED"
    CONCURRENCY_EXCEEDED = "CONCURRENCY_EXCEEDED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


_TERMINAL_STATES = {AutonomyState.REVOKED.value, AutonomyState.EXPIRED.value}


@dataclass(frozen=True)
class AutonomyBudget:
    """Hard ceilings for one profile. ``0``/empty means "unbounded"."""

    max_operations: int = 0
    max_cost: float = 0.0
    max_network_calls: int = 0
    max_provider_calls: int = 0
    providers: Tuple[str, ...] = ()
    max_duration_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_operations": int(self.max_operations),
            "max_cost": float(self.max_cost),
            "max_network_calls": int(self.max_network_calls),
            "max_provider_calls": int(self.max_provider_calls),
            "providers": list(self.providers),
            "max_duration_s": float(self.max_duration_s),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "AutonomyBudget":
        if not isinstance(raw, dict):
            return cls()
        try:
            return cls(
                max_operations=int(raw.get("max_operations", 0) or 0),
                max_cost=float(raw.get("max_cost", 0.0) or 0.0),
                max_network_calls=int(raw.get("max_network_calls", 0) or 0),
                max_provider_calls=int(raw.get("max_provider_calls", 0) or 0),
                providers=tuple(str(p) for p in (raw.get("providers") or ())),
                max_duration_s=float(raw.get("max_duration_s", 0.0) or 0.0),
            )
        except (TypeError, ValueError):
            return cls()


@dataclass
class AutonomyUsage:
    """Recorded consumption for one profile (audit + budget accounting)."""

    operations: int = 0
    cost: float = 0.0
    network_calls: int = 0
    provider_calls: int = 0
    active: int = 0
    last_used_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operations": int(self.operations),
            "cost": float(self.cost),
            "network_calls": int(self.network_calls),
            "provider_calls": int(self.provider_calls),
            "active": int(self.active),
            "last_used_at": self.last_used_at,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "AutonomyUsage":
        if not isinstance(raw, dict):
            return cls()
        try:
            return cls(
                operations=int(raw.get("operations", 0) or 0),
                cost=float(raw.get("cost", 0.0) or 0.0),
                network_calls=int(raw.get("network_calls", 0) or 0),
                provider_calls=int(raw.get("provider_calls", 0) or 0),
                active=int(raw.get("active", 0) or 0),
                last_used_at=(None if raw.get("last_used_at") is None
                              else float(raw["last_used_at"])),
            )
        except (TypeError, ValueError):
            return cls()


@dataclass
class AutonomyProfile:
    """Owner-created scoped autonomy grant."""

    profile_id: str
    owner: str
    state: str = AutonomyState.ACTIVE.value
    name: str = ""
    session: str = ""
    device: str = ""
    capabilities: Tuple[str, ...] = ()
    targets: Tuple[str, ...] = ()
    not_before: Optional[float] = None
    expires_at: Optional[float] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    days: Tuple[int, ...] = ()
    max_concurrency: int = 1
    budget: AutonomyBudget = field(default_factory=AutonomyBudget)
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)
    revoked_at: Optional[float] = None
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "owner": self.owner,
            "state": self.state,
            "name": self.name,
            "session": self.session,
            "device": self.device,
            "capabilities": list(self.capabilities),
            "targets": list(self.targets),
            "not_before": self.not_before,
            "expires_at": self.expires_at,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "days": list(self.days),
            "max_concurrency": int(self.max_concurrency),
            "budget": self.budget.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "revoked_at": self.revoked_at,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Optional["AutonomyProfile"]:
        if not isinstance(raw, dict):
            return None
        pid = str(raw.get("profile_id") or "").strip()
        owner = str(raw.get("owner") or "").strip()
        state = raw.get("state")
        if not pid or not owner or state not in {s.value for s in AutonomyState}:
            return None

        def _fnum(key: str) -> Optional[float]:
            val = raw.get(key)
            if val is None:
                return None
            try:
                return float(val)
            except (TypeError, ValueError):
                return None

        caps = tuple(str(c) for c in (raw.get("capabilities") or ()) if str(c))
        targets = tuple(str(t) for t in (raw.get("targets") or ()) if str(t))
        try:
            days = tuple(sorted({int(d) for d in (raw.get("days") or ())}))
        except (TypeError, ValueError):
            days = ()
        try:
            max_conc = int(raw.get("max_concurrency", 1))
        except (TypeError, ValueError):
            max_conc = 1
        return cls(
            profile_id=pid,
            owner=owner,
            state=str(state),
            name=str(raw.get("name") or ""),
            session=str(raw.get("session") or ""),
            device=str(raw.get("device") or ""),
            capabilities=caps,
            targets=targets,
            not_before=_fnum("not_before"),
            expires_at=_fnum("expires_at"),
            window_start=(raw.get("window_start") or None),
            window_end=(raw.get("window_end") or None),
            days=days,
            max_concurrency=max_conc,
            budget=AutonomyBudget.from_dict(raw.get("budget")),
            created_at=_fnum("created_at") or _now(),
            updated_at=_fnum("updated_at") or _now(),
            revoked_at=_fnum("revoked_at"),
            note=str(raw.get("note") or ""),
        )


@dataclass(frozen=True)
class AutonomyDecision:
    """Pure authorization result. ``code`` is an :class:`AutonomyDenial` value."""

    allowed: bool
    code: str
    reason: str
    profile_id: str
    capability: str
    target: str
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "reason": self.reason,
            "profile_id": self.profile_id,
            "capability": self.capability,
            "target": self.target,
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# Schedule/window helpers
# ---------------------------------------------------------------------------
def _parse_hm(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        hh, mm = str(value).split(":")
        hour, minute = int(hh), int(mm)
    except (ValueError, AttributeError) as exc:
        raise AutonomyError("INVALID_WINDOW", f"bad HH:MM: {value!r}") from exc
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise AutonomyError("INVALID_WINDOW", f"bad HH:MM: {value!r}")
    return hour * 60 + minute


def _in_window(profile: AutonomyProfile, now: float) -> bool:
    """True when ``now`` falls inside the profile's weekly/time window."""
    dt = datetime.fromtimestamp(now)
    if profile.days and dt.weekday() not in profile.days:
        return False
    start = _parse_hm(profile.window_start)
    end = _parse_hm(profile.window_end)
    if start is None or end is None:
        return True
    cur = dt.hour * 60 + dt.minute
    if start <= end:
        return start <= cur <= end
    # overnight window (e.g. 22:00 -> 06:00)
    return cur >= start or cur <= end


def _target_allowed(profile: AutonomyProfile, target: str) -> bool:
    if not profile.targets:
        return True
    if not target:
        return False
    for pat in profile.targets:
        if pat == target or fnmatch.fnmatchcase(target, pat):
            return True
    return False


def _effective_state(profile: AutonomyProfile, now: float) -> str:
    if profile.state in _TERMINAL_STATES:
        return profile.state
    if profile.state == AutonomyState.PAUSED.value:
        return profile.state
    if profile.expires_at is not None and now >= profile.expires_at:
        return AutonomyState.EXPIRED.value
    return profile.state


# ---------------------------------------------------------------------------
# Persisted store (canonical state tree)
# ---------------------------------------------------------------------------
class AutonomyStore:
    """Atomic, cross-instance-consistent autonomy profile + usage store.

    Reads never create the parent directory (so a pure ``authorize`` call has no
    filesystem side effect). Writes are atomic (unique temp + fsync + rename)
    and ``0600``. A corrupt/unreadable file fails closed to "no profiles", so
    every authorization denies rather than silently un-granting scope.
    """

    def __init__(self, path: Path | str, *,
                 clock=time.time) -> None:
        self._path = Path(path)
        self._clock = clock
        self._lock = threading.RLock()
        self._state: Dict[str, Any] = {
            "schema": SCHEMA_VERSION,
            "profiles": {},
            "usage": {},
            "audit": [],
        }
        self._load()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, ValueError, TypeError):
            return  # fail closed to empty
        if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), dict):
            return
        profiles: Dict[str, AutonomyProfile] = {}
        for pid, rec in raw["profiles"].items():
            prof = AutonomyProfile.from_dict(rec)
            if prof is not None:
                profiles[str(pid)] = prof
        usage: Dict[str, AutonomyUsage] = {}
        raw_usage = raw.get("usage")
        if isinstance(raw_usage, dict):
            for pid, urec in raw_usage.items():
                usage[str(pid)] = AutonomyUsage.from_dict(urec)
        audit = raw.get("audit")
        self._state["profiles"] = profiles
        self._state["usage"] = usage
        self._state["audit"] = audit[-AUDIT_CAP:] if isinstance(audit, list) else []

    def _serialize(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "profiles": {pid: p.to_dict()
                         for pid, p in self._state["profiles"].items()},
            "usage": {pid: u.to_dict()
                      for pid, u in self._state["usage"].items()},
            "audit": list(self._state["audit"]),
        }

    def _save_locked(self) -> Path:
        parent = self._path.parent
        parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass
        fd, tmp = tempfile.mkstemp(dir=str(parent),
                                   prefix=f".{self._path.name}.",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(self._serialize(), fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass
        return self._path

    def _audit(self, action: str, actor: str, detail: Dict[str, Any]) -> None:
        audit = self._state.setdefault("audit", [])
        audit.append({"ts": self._clock(), "action": action, "actor": actor,
                      "detail": detail})
        if len(audit) > AUDIT_CAP:
            del audit[:len(audit) - AUDIT_CAP]
        try:
            log_event(f"autonomy.{action}", actor=actor, detail=detail)
        except Exception:
            pass

    # ------------------------------------------------------------ accessors
    def get(self, profile_id: str) -> Optional[AutonomyProfile]:
        with self._lock:
            return self._state["profiles"].get(profile_id)

    def list(self, owner: Optional[str] = None) -> List[AutonomyProfile]:
        with self._lock:
            profs = list(self._state["profiles"].values())
        if owner is not None:
            profs = [p for p in profs if p.owner == owner]
        return sorted(profs, key=lambda p: p.created_at)

    def audit(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._state.get("audit", []))[-int(limit):]

    def usage(self, profile_id: str) -> AutonomyUsage:
        with self._lock:
            rec = self._state["usage"].get(profile_id)
        if isinstance(rec, AutonomyUsage):
            return AutonomyUsage.from_dict(rec.to_dict())
        return AutonomyUsage.from_dict(rec)

    # ------------------------------------------------------------ mutations
    def create(self, *, owner: str, capabilities: Tuple[str, ...],
               targets: Tuple[str, ...] = (), name: str = "",
               session: str = "", device: str = "",
               not_before: Optional[float] = None,
               expires_at: Optional[float] = None,
               window_start: Optional[str] = None,
               window_end: Optional[str] = None,
               days: Tuple[int, ...] = (), max_concurrency: int = 1,
               budget: Optional[AutonomyBudget] = None, note: str = "",
               profile_id: Optional[str] = None,
               requested_by: Optional[str] = None,
               now: Optional[float] = None) -> AutonomyProfile:
        owner = (owner or "").strip()
        if not owner:
            raise AutonomyError("BAD_REQUEST", "owner is required")
        if requested_by is not None and requested_by.strip() != owner:
            raise AutonomyError(
                "OWNER_MISMATCH",
                "only the named owner may create the profile")
        caps = tuple(dict.fromkeys(
            c.strip() for c in capabilities if c and c.strip()))
        if not caps:
            raise AutonomyError("BAD_REQUEST",
                                "at least one capability scope is required")
        tgts = tuple(dict.fromkeys(
            t.strip() for t in targets if t and t.strip()))
        _parse_hm(window_start)
        _parse_hm(window_end)
        if max_concurrency < 0:
            raise AutonomyError("BAD_REQUEST", "max_concurrency must be >= 0")
        if not_before is not None and expires_at is not None \
                and not_before >= expires_at:
            raise AutonomyError("BAD_REQUEST",
                                "not_before must precede expires_at")
        stamp = _now() if now is None else float(now)
        prof = AutonomyProfile(
            profile_id=profile_id or _new_id(),
            owner=owner,
            state=AutonomyState.ACTIVE.value,
            name=name or "autonomy",
            session=session or "",
            device=device or "",
            capabilities=caps,
            targets=tgts,
            not_before=not_before,
            expires_at=expires_at,
            window_start=window_start or None,
            window_end=window_end or None,
            days=tuple(sorted(set(days))),
            max_concurrency=int(max_concurrency),
            budget=budget or AutonomyBudget(),
            created_at=stamp,
            updated_at=stamp,
            note=note or "",
        )
        with self._lock:
            if prof.profile_id in self._state["profiles"]:
                raise AutonomyError("DUPLICATE_PROFILE",
                                    f"{prof.profile_id} already exists")
            self._state["profiles"][prof.profile_id] = prof
            self._state["usage"].setdefault(prof.profile_id, AutonomyUsage())
            self._audit("create", owner, {
                "profile_id": prof.profile_id,
                "capabilities": list(caps),
                "targets": list(tgts),
                "expires_at": expires_at,
            })
            self._save_locked()
        return prof

    def set_state(self, profile_id: str, state: str, *, owner: str,
                  now: Optional[float] = None,
                  reason: str = "") -> AutonomyProfile:
        if state not in {s.value for s in AutonomyState}:
            raise AutonomyError("INVALID_STATE", f"unknown state {state!r}")
        stamp = _now() if now is None else float(now)
        with self._lock:
            prof = self._state["profiles"].get(profile_id)
            if prof is None:
                raise AutonomyError("UNKNOWN_PROFILE", profile_id)
            if (owner or "").strip() != prof.owner:
                raise AutonomyError(
                    "OWNER_MISMATCH",
                    "only the profile owner may change its state")
            prof.state = state
            prof.updated_at = stamp
            if state == AutonomyState.REVOKED.value:
                prof.revoked_at = stamp
            self._audit(state, prof.owner, {
                "profile_id": profile_id,
                "reason": reason or "",
            })
            self._save_locked()
            return prof

    def sweep_expired(self, now: Optional[float] = None) -> List[str]:
        stamp = _now() if now is None else float(now)
        expired: List[str] = []
        with self._lock:
            for pid, prof in self._state["profiles"].items():
                if prof.state == AutonomyState.ACTIVE.value \
                        and prof.expires_at is not None \
                        and stamp >= prof.expires_at:
                    prof.state = AutonomyState.EXPIRED.value
                    prof.updated_at = stamp
                    expired.append(pid)
            if expired:
                self._audit("expire", "system",
                            {"profiles": list(expired)})
                self._save_locked()
        return expired

    def record_operation(self, profile_id: str, *, cost: float = 0.0,
                         network_calls: int = 0, provider_calls: int = 0,
                         active_delta: int = 0,
                         now: Optional[float] = None) -> AutonomyUsage:
        stamp = _now() if now is None else float(now)
        with self._lock:
            if profile_id not in self._state["profiles"]:
                raise AutonomyError("UNKNOWN_PROFILE", profile_id)
            rec = self._state["usage"].get(profile_id)
            usage = rec if isinstance(rec, AutonomyUsage) else AutonomyUsage()
            usage.operations += 1
            usage.cost += float(cost or 0.0)
            usage.network_calls += int(network_calls or 0)
            usage.provider_calls += int(provider_calls or 0)
            usage.active = max(0, usage.active + int(active_delta))
            usage.last_used_at = stamp
            self._state["usage"][profile_id] = usage
            self._save_locked()
            return AutonomyUsage.from_dict(usage.to_dict())


# ---------------------------------------------------------------------------
# Config-bound helpers
# ---------------------------------------------------------------------------
def autonomy_store(cfg: Any) -> AutonomyStore:
    """Open the one autonomy store in the canonical Qubit state tree."""
    path = ""
    try:
        path = cfg.get("autonomy", "state_path", default="") or ""
    except Exception:
        path = ""
    if not path:
        path = cfg.state_dir() / STORE_NAME
    return AutonomyStore(Path(path).expanduser())


def _estop_engaged(cfg: Any) -> bool:
    """Fail-closed e-stop probe (read-only; single shared power latch)."""
    try:
        from .broker import estop_engaged as _probe
        return bool(_probe(cfg))
    except Exception:
        return True


def _decision(allowed: bool, code: AutonomyDenial, reason: str,
              profile_id: str, capability: str, target: str,
              detail: str = "") -> AutonomyDecision:
    return AutonomyDecision(allowed=allowed, code=code.value, reason=reason,
                            profile_id=profile_id, capability=capability,
                            target=target, detail=detail)


def authorize(cfg: Any, profile_id: str, capability: str, target: str = "",
              now: Optional[float] = None, cost: Optional[float] = None, *,
              owner: Optional[str] = None, session: Optional[str] = None,
              device: Optional[str] = None) -> AutonomyDecision:
    """Pure allow/deny decision for one capability/target under a profile.

    Never mutates persisted state and never widens the profile from request
    data: the request's ``capability``/``target`` are only ever checked for
    containment in the owner-authored scope.
    """
    stamp = _now() if now is None else float(now)
    store = autonomy_store(cfg)
    prof = store.get(profile_id)
    if prof is None:
        return _decision(False, AutonomyDenial.NO_PROFILE,
                         "no autonomy profile with that id",
                         profile_id, capability, target)
    if owner is not None and (owner or "").strip() != prof.owner:
        return _decision(False, AutonomyDenial.OWNER_MISMATCH,
                         "caller is not the profile owner",
                         profile_id, capability, target)
    if session is not None and prof.session and session != prof.session:
        return _decision(False, AutonomyDenial.BINDING_MISMATCH,
                         "session does not match the profile binding",
                         profile_id, capability, target)
    if device is not None and prof.device and device != prof.device:
        return _decision(False, AutonomyDenial.BINDING_MISMATCH,
                         "device does not match the profile binding",
                         profile_id, capability, target)

    state = _effective_state(prof, stamp)
    if state == AutonomyState.REVOKED.value:
        return _decision(False, AutonomyDenial.REVOKED,
                         "profile revoked", profile_id, capability, target)
    if state == AutonomyState.PAUSED.value:
        return _decision(False, AutonomyDenial.PAUSED,
                         "profile paused", profile_id, capability, target)
    if state == AutonomyState.EXPIRED.value:
        return _decision(False, AutonomyDenial.EXPIRED,
                         "profile expired", profile_id, capability, target)
    if state != AutonomyState.ACTIVE.value:
        return _decision(False, AutonomyDenial.NO_PROFILE,
                         "profile is not active",
                         profile_id, capability, target)

    if prof.not_before is not None and stamp < prof.not_before:
        return _decision(False, AutonomyDenial.OUT_OF_WINDOW,
                         "profile schedule has not started",
                         profile_id, capability, target)
    if not _in_window(prof, stamp):
        return _decision(False, AutonomyDenial.OUT_OF_WINDOW,
                         "outside the allowed schedule window",
                         profile_id, capability, target)

    if _estop_engaged(cfg):
        return _decision(False, AutonomyDenial.ESTOP_ENGAGED,
                         "emergency stop is latched",
                         profile_id, capability, target)

    if capability not in prof.capabilities:
        return _decision(False, AutonomyDenial.CAPABILITY_NOT_IN_SCOPE,
                         "capability is not in the profile scope",
                         profile_id, capability, target)
    if not _target_allowed(prof, target):
        return _decision(False, AutonomyDenial.TARGET_NOT_ALLOWED,
                         "target is not allowed by the profile",
                         profile_id, capability, target)

    usage = store.usage(profile_id)
    if prof.max_concurrency > 0 and usage.active >= prof.max_concurrency:
        return _decision(False, AutonomyDenial.CONCURRENCY_EXCEEDED,
                         "profile concurrency ceiling reached",
                         profile_id, capability, target)
    budget = prof.budget
    if budget.max_operations > 0 \
            and usage.operations + 1 > budget.max_operations:
        return _decision(False, AutonomyDenial.BUDGET_EXCEEDED,
                         "profile operation budget exhausted",
                         profile_id, capability, target)
    if budget.max_cost > 0 and cost is not None \
            and usage.cost + float(cost) > budget.max_cost:
        return _decision(False, AutonomyDenial.BUDGET_EXCEEDED,
                         "profile cost budget would be exceeded",
                         profile_id, capability, target)
    if budget.max_network_calls > 0 \
            and usage.network_calls >= budget.max_network_calls:
        return _decision(False, AutonomyDenial.BUDGET_EXCEEDED,
                         "profile network budget exhausted",
                         profile_id, capability, target)
    if budget.max_provider_calls > 0 \
            and usage.provider_calls >= budget.max_provider_calls:
        return _decision(False, AutonomyDenial.BUDGET_EXCEEDED,
                         "profile provider budget exhausted",
                         profile_id, capability, target)

    return _decision(True, AutonomyDenial.ALLOWED,
                     "authorized by active autonomy profile",
                     profile_id, capability, target,
                     detail=f"owner={prof.owner}")


# --------------------------------------------------------------- admin verbs
def create(cfg: Any, *, owner: str, capabilities: Tuple[str, ...],
           targets: Tuple[str, ...] = (), **kwargs: Any) -> AutonomyProfile:
    return autonomy_store(cfg).create(owner=owner, capabilities=capabilities,
                                      targets=targets, **kwargs)


def pause(cfg: Any, profile_id: str, *, owner: str,
          now: Optional[float] = None) -> AutonomyProfile:
    return autonomy_store(cfg).set_state(
        profile_id, AutonomyState.PAUSED.value, owner=owner, now=now)


def resume(cfg: Any, profile_id: str, *, owner: str,
           now: Optional[float] = None) -> AutonomyProfile:
    return autonomy_store(cfg).set_state(
        profile_id, AutonomyState.ACTIVE.value, owner=owner, now=now)


def revoke(cfg: Any, profile_id: str, *, owner: str,
           now: Optional[float] = None) -> AutonomyProfile:
    return autonomy_store(cfg).set_state(
        profile_id, AutonomyState.REVOKED.value, owner=owner, now=now,
        reason="owner revoked")


def expire(cfg: Any, now: Optional[float] = None) -> List[str]:
    return autonomy_store(cfg).sweep_expired(now)


def list_profiles(cfg: Any,
                  owner: Optional[str] = None) -> List[AutonomyProfile]:
    return autonomy_store(cfg).list(owner=owner)


def show(cfg: Any, profile_id: str) -> Optional[AutonomyProfile]:
    return autonomy_store(cfg).get(profile_id)


def status(cfg: Any, now: Optional[float] = None) -> Dict[str, Any]:
    stamp = _now() if now is None else float(now)
    store = autonomy_store(cfg)
    profs = store.list()
    counts: Dict[str, int] = {s.value: 0 for s in AutonomyState}
    for p in profs:
        effective = _effective_state(p, stamp)
        counts[effective] = counts.get(effective, 0) + 1
    return {
        "now": stamp,
        "estop": _estop_engaged(cfg),
        "profile_count": len(profs),
        "counts": counts,
        "profiles": [p.to_dict() for p in profs],
        "store_path": str(store.path),
    }


__all__ = [
    "AutonomyBudget",
    "AutonomyDecision",
    "AutonomyDenial",
    "AutonomyError",
    "AutonomyProfile",
    "AutonomyState",
    "AutonomyStore",
    "AutonomyUsage",
    "authorize",
    "autonomy_store",
    "create",
    "expire",
    "list_profiles",
    "pause",
    "resume",
    "revoke",
    "show",
    "status",
]
