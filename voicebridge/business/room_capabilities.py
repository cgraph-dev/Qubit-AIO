"""Room capability manifests: no room is decorative (P3-02).

Every room in the Office is a projection of real business work. A room with
furniture and desks but no workflow is not a finished room; it is a decorative
prop. This module makes that impossible to express:

* A :class:`RoomCapabilityManifest` must name a real workflow, at least one
  role from the shipped :class:`~voicebridge.business.roles.RoleRegistry`, at
  least one capability from a capability catalog, a queue/task kind, artifact
  kinds, KPIs, a memory scope, a privacy class, an approval policy, a minimum
  agent count and the room modes it supports. Construction refuses empty or
  wildcard values.
* A :class:`RoomCapabilityRegistry` indexes manifests by room kind, archetype
  and department, resolves the manifest for one concrete room, and
  :meth:`validate_coverage` refuses any room in the registry that has no
  manifest — decorating a room does not make it covered.
* Composition, not duplication: role ids are validated against the existing
  ``RoleRegistry``; capability ids are validated against a supplied catalog
  (``policy.CapabilityCatalog`` or ``dynamic_catalog.DynamicCapabilityCatalog``).
  The module owns no second role list and no second policy engine.

The Trading Department is the one place where separation is itself a
correctness rule: an execution workflow must require approval, must name an
independent risk role and approval role, and must never contain the
risk-limit-mutation capability. That is checked at construction and again when
a registry is built.

Pure data + validation: no store handle, no network, no clock.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from . import policy as policy_module
from .errors import BAD_INPUT_CODE, BusinessError
from .ids import fingerprint
from .roles import RoleRegistry

# Typed refusal codes. A caller branches on these; prose is not a contract.
ROOM_CAPABILITY_INVALID_CODE = "ROOM_CAPABILITY_INVALID"
ROOM_CAPABILITY_DUPLICATE_CODE = "ROOM_CAPABILITY_DUPLICATE"
ROOM_CAPABILITY_UNKNOWN_ROOM_CODE = "ROOM_CAPABILITY_UNKNOWN_ROOM"
ROOM_CAPABILITY_UNKNOWN_CAPABILITY_CODE = "ROOM_CAPABILITY_UNKNOWN_CAPABILITY"
ROOM_CAPABILITY_UNKNOWN_ROLE_CODE = "ROOM_CAPABILITY_UNKNOWN_ROLE"
ROOM_CAPABILITY_UNCOVERED_CODE = "ROOM_CAPABILITY_UNCOVERED"
ROOM_CAPABILITY_WILDCARD_CODE = "ROOM_CAPABILITY_WILDCARD"
ROOM_CAPABILITY_SEPARATION_CODE = "ROOM_CAPABILITY_SEPARATION"
ROOM_CAPABILITY_AMBIGUOUS_CODE = "ROOM_CAPABILITY_AMBIGUOUS"

PRIVACY_CLASSES = frozenset({"public", "department", "private", "restricted"})
ROOM_MODES = frozenset(
    {"work", "meeting", "review", "focus", "incident", "replay", "simulated"}
)
TRADING_DEPARTMENT = "trading"

_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
_WILDCARDS = frozenset({"*", "all", "any", "ALL", "ANY", "All", "Any"})


class RoomCapabilityError(BusinessError):
    """A typed room-capability refusal."""


def _fail(code: str, detail: str) -> None:
    raise RoomCapabilityError(code, detail)


def _token(value: Any, label: str, *, max_len: int = 160) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(ROOM_CAPABILITY_INVALID_CODE, f"{label} must be a non-empty string")
    clean = value.strip()
    if len(clean) > max_len:
        _fail(
            ROOM_CAPABILITY_INVALID_CODE,
            f"{label} is longer than {max_len} characters",
        )
    if not _SAFE_TOKEN_RE.match(clean):
        _fail(
            ROOM_CAPABILITY_INVALID_CODE,
            f"{label} must be a stable token (letters, digits, . _ : / -)",
        )
    return clean


def _explicit(value: Any, label: str, *, max_len: int = 160) -> str:
    if isinstance(value, str):
        probe = value.strip()
        if probe in _WILDCARDS or "*" in probe:
            _fail(
                ROOM_CAPABILITY_WILDCARD_CODE,
                f"{label} must be an exact value; wildcards are not allowed",
            )
    return _token(value, label, max_len=max_len)


def _tokens(value: Any, label: str, *, required: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _fail(ROOM_CAPABILITY_INVALID_CODE, f"{label} must be a sequence")
    clean = tuple(_explicit(item, label) for item in value)
    if required and not clean:
        _fail(ROOM_CAPABILITY_INVALID_CODE, f"{label} must not be empty")
    if len(set(clean)) != len(clean):
        _fail(
            ROOM_CAPABILITY_INVALID_CODE, f"{label} must not repeat values"
        )
    return clean


def _texts(value: Any, label: str, *, required: bool = False) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _fail(ROOM_CAPABILITY_INVALID_CODE, f"{label} must be a sequence")
    clean = tuple(_token(item, label) for item in value)
    if required and not clean:
        _fail(ROOM_CAPABILITY_INVALID_CODE, f"{label} must not be empty")
    if len(set(clean)) != len(clean):
        _fail(
            ROOM_CAPABILITY_INVALID_CODE, f"{label} must not repeat values"
        )
    return clean


def _approval(value: Any) -> str:
    if value is True:
        return "required"
    if value is False or value is None:
        return "none"
    clean = str(value).strip().lower()
    if clean not in policy_module.APPROVAL_POLICIES:
        _fail(
            ROOM_CAPABILITY_INVALID_CODE,
            "approval_policy must be one of"
            f" {', '.join(sorted(policy_module.APPROVAL_POLICIES))}",
        )
    return clean


@dataclass(frozen=True, slots=True)
class RoomCapabilityManifest:
    """One real workflow a room can host. Validation is part of construction."""

    manifest_id: str
    room_kinds: tuple[str, ...]
    archetypes: tuple[str, ...]
    departments: tuple[str, ...]
    workflow_id: str
    workflow_name: str
    required_role_ids: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    artifact_kinds: tuple[str, ...]
    queue_kind: str
    task_kind: str
    kpi_ids: tuple[str, ...]
    memory_scope: str
    privacy_class: str
    approval_policy: str = "none"
    required_agent_count: int = 1
    room_modes: tuple[str, ...] = ("work",)
    execution_capabilities: tuple[str, ...] = ()
    risk_capabilities: tuple[str, ...] = ()
    approval_capabilities: tuple[str, ...] = ()
    risk_limit_capabilities: tuple[str, ...] = ()
    execution_role_ids: tuple[str, ...] = ()
    risk_role_ids: tuple[str, ...] = ()
    approval_role_ids: tuple[str, ...] = ()
    rationale: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "manifest_id", _explicit(self.manifest_id, "manifest_id")
        )
        object.__setattr__(
            self, "room_kinds", _tokens(self.room_kinds, "room_kinds")
        )
        object.__setattr__(
            self, "archetypes", _tokens(self.archetypes, "archetypes")
        )
        if not self.room_kinds and not self.archetypes:
            _fail(
                ROOM_CAPABILITY_INVALID_CODE,
                "a manifest must name at least one room kind or archetype",
            )
        object.__setattr__(
            self, "departments", _tokens(self.departments, "departments")
        )
        object.__setattr__(
            self, "workflow_id", _explicit(self.workflow_id, "workflow_id")
        )
        object.__setattr__(
            self,
            "workflow_name",
            _text_value(self.workflow_name, "workflow_name"),
        )
        object.__setattr__(
            self,
            "required_role_ids",
            _tokens(self.required_role_ids, "required_role_ids", required=True),
        )
        object.__setattr__(
            self,
            "required_capabilities",
            _tokens(
                self.required_capabilities,
                "required_capabilities",
                required=True,
            ),
        )
        object.__setattr__(
            self, "allowed_tools", _tokens(self.allowed_tools, "allowed_tools")
        )
        object.__setattr__(
            self,
            "artifact_kinds",
            _tokens(self.artifact_kinds, "artifact_kinds", required=True),
        )
        object.__setattr__(
            self, "queue_kind", _explicit(self.queue_kind, "queue_kind")
        )
        object.__setattr__(self, "task_kind", _explicit(self.task_kind, "task_kind"))
        object.__setattr__(
            self, "kpi_ids", _tokens(self.kpi_ids, "kpi_ids", required=True)
        )
        object.__setattr__(
            self, "memory_scope", _explicit(self.memory_scope, "memory_scope")
        )
        object.__setattr__(
            self, "privacy_class", _explicit(self.privacy_class, "privacy_class")
        )
        if self.privacy_class not in PRIVACY_CLASSES:
            _fail(
                ROOM_CAPABILITY_INVALID_CODE,
                "privacy_class must be one of"
                f" {', '.join(sorted(PRIVACY_CLASSES))}",
            )
        object.__setattr__(
            self, "approval_policy", _approval(self.approval_policy)
        )
        if (
            not isinstance(self.required_agent_count, int)
            or isinstance(self.required_agent_count, bool)
            or self.required_agent_count < 1
        ):
            _fail(
                ROOM_CAPABILITY_INVALID_CODE,
                "required_agent_count must be a positive integer",
            )
        modes = _tokens(self.room_modes, "room_modes", required=True)
        unknown = sorted(set(modes) - ROOM_MODES)
        if unknown:
            _fail(
                ROOM_CAPABILITY_INVALID_CODE,
                f"unknown room mode(s): {', '.join(unknown)}",
            )
        object.__setattr__(self, "room_modes", modes)
        for label in (
            "execution_capabilities",
            "risk_capabilities",
            "approval_capabilities",
            "risk_limit_capabilities",
            "execution_role_ids",
            "risk_role_ids",
            "approval_role_ids",
        ):
            object.__setattr__(
                self, label, _tokens(getattr(self, label), label)
            )
        object.__setattr__(
            self, "rationale", _text_value(self.rationale, "rationale", allow_empty=True)
        )
        self._validate_separation()

    # -- separation ------------------------------------------------------
    def _validate_separation(self) -> None:
        required_caps = set(self.required_capabilities)
        required_roles = set(self.required_role_ids)
        # ``risk_limit_capabilities`` names the boundary execution must never
        # cross; it does not have to be required by this workflow (an execution
        # room names the limit it cannot mutate, but does not hold it).
        declared_caps = (
            set(self.execution_capabilities)
            | set(self.risk_capabilities)
            | set(self.approval_capabilities)
        )
        missing_caps = sorted(declared_caps - required_caps)
        if missing_caps:
            _fail(
                ROOM_CAPABILITY_INVALID_CODE,
                "separation declares capabilities that are not required by the"
                f" workflow: {', '.join(missing_caps)}",
            )
        declared_roles = (
            set(self.execution_role_ids)
            | set(self.risk_role_ids)
            | set(self.approval_role_ids)
        )
        missing_roles = sorted(declared_roles - required_roles)
        if missing_roles:
            _fail(
                ROOM_CAPABILITY_INVALID_CODE,
                "separation declares roles that are not required by the"
                f" workflow: {', '.join(missing_roles)}",
            )

        if not self.execution_capabilities:
            # A non-executing workflow may still name guard roles/capabilities,
            # but there is no execution to separate them from.
            return
        if self.approval_policy != "required":
            _fail(
                ROOM_CAPABILITY_SEPARATION_CODE,
                "an execution workflow must require approval",
            )
        if not self.risk_capabilities or not self.approval_capabilities:
            _fail(
                ROOM_CAPABILITY_SEPARATION_CODE,
                "an execution workflow must name independent risk and approval"
                " capabilities",
            )
        if not self.risk_role_ids or not self.approval_role_ids:
            _fail(
                ROOM_CAPABILITY_SEPARATION_CODE,
                "an execution workflow must name independent risk and approval"
                " roles",
            )
        execution_caps = set(self.execution_capabilities)
        overlap = sorted(
            execution_caps
            & (
                set(self.risk_capabilities)
                | set(self.approval_capabilities)
                | set(self.risk_limit_capabilities)
            )
        )
        if overlap:
            _fail(
                ROOM_CAPABILITY_SEPARATION_CODE,
                "execution cannot share risk/approval/limit capabilities:"
                f" {', '.join(overlap)}",
            )
        execution_roles = set(self.execution_role_ids)
        role_overlap = sorted(
            execution_roles & (set(self.risk_role_ids) | set(self.approval_role_ids))
        )
        if role_overlap:
            _fail(
                ROOM_CAPABILITY_SEPARATION_CODE,
                "execution cannot share risk/approval roles:"
                f" {', '.join(role_overlap)}",
            )

    # -- serialization ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "room_kinds": list(self.room_kinds),
            "archetypes": list(self.archetypes),
            "departments": list(self.departments),
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "required_role_ids": list(self.required_role_ids),
            "required_capabilities": list(self.required_capabilities),
            "allowed_tools": list(self.allowed_tools),
            "artifact_kinds": list(self.artifact_kinds),
            "queue_kind": self.queue_kind,
            "task_kind": self.task_kind,
            "kpi_ids": list(self.kpi_ids),
            "memory_scope": self.memory_scope,
            "privacy_class": self.privacy_class,
            "approval_policy": self.approval_policy,
            "required_agent_count": self.required_agent_count,
            "room_modes": list(self.room_modes),
            "execution_capabilities": list(self.execution_capabilities),
            "risk_capabilities": list(self.risk_capabilities),
            "approval_capabilities": list(self.approval_capabilities),
            "risk_limit_capabilities": list(self.risk_limit_capabilities),
            "execution_role_ids": list(self.execution_role_ids),
            "risk_role_ids": list(self.risk_role_ids),
            "approval_role_ids": list(self.approval_role_ids),
            "rationale": self.rationale,
        }

    @property
    def digest(self) -> str:
        return fingerprint(self.to_dict())

    @property
    def is_executing(self) -> bool:
        return bool(self.execution_capabilities)

    def matches(
        self,
        *,
        room_kind: str | None = None,
        archetype: str | None = None,
        department_id: str | None = None,
    ) -> bool:
        """Whether this manifest describes the requested room axis."""
        kind_match = room_kind is not None and room_kind in self.room_kinds
        archetype_match = archetype is not None and archetype in self.archetypes
        if not kind_match and not archetype_match:
            return False
        if department_id is None:
            return True
        return not self.departments or department_id in self.departments


def _text_value(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        _fail(ROOM_CAPABILITY_INVALID_CODE, f"{label} must be a string")
    clean = value.strip()
    if not allow_empty and not clean:
        _fail(ROOM_CAPABILITY_INVALID_CODE, f"{label} must be a non-empty string")
    if len(clean) > 500:
        _fail(
            ROOM_CAPABILITY_INVALID_CODE,
            f"{label} is longer than 500 characters",
        )
    return clean


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """The result of checking every room in a role registry."""

    total_rooms: int
    covered_rooms: int
    by_department: Mapping[str, int]
    manifests_used: tuple[str, ...]

    @property
    def uncovered_rooms(self) -> int:
        return self.total_rooms - self.covered_rooms

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_rooms": self.total_rooms,
            "covered_rooms": self.covered_rooms,
            "uncovered_rooms": self.uncovered_rooms,
            "by_department": dict(sorted(self.by_department.items())),
            "manifests_used": list(self.manifests_used),
        }


class RoomCapabilityRegistry:
    """Validated manifests, indexed by room kind, archetype and department."""

    def __init__(
        self,
        manifests: Iterable[RoomCapabilityManifest] = (),
        *,
        roles: RoleRegistry | None = None,
        capabilities: Any | None = None,
    ) -> None:
        self._roles = roles
        self._capabilities = capabilities
        self._by_id: dict[str, RoomCapabilityManifest] = {}
        for manifest in manifests:
            self.register(manifest)

    # -- registration ----------------------------------------------------
    def register(self, manifest: RoomCapabilityManifest) -> RoomCapabilityManifest:
        if not isinstance(manifest, RoomCapabilityManifest):
            _fail(
                ROOM_CAPABILITY_INVALID_CODE,
                "manifest must be a RoomCapabilityManifest",
            )
        if manifest.manifest_id in self._by_id:
            _fail(
                ROOM_CAPABILITY_DUPLICATE_CODE,
                f"duplicate manifest_id {manifest.manifest_id!r}",
            )
        if self._roles is not None:
            for role_id in manifest.required_role_ids:
                try:
                    self._roles.get_role(role_id)
                except BusinessError:
                    _fail(
                        ROOM_CAPABILITY_UNKNOWN_ROLE_CODE,
                        f"manifest {manifest.manifest_id!r} names unknown role"
                        f" {role_id!r}",
                    )
        if self._capabilities is not None:
            has = getattr(self._capabilities, "has", None)
            for capability_id in manifest.required_capabilities:
                if callable(has):
                    known = bool(has(capability_id))
                else:
                    known = capability_id in self._capabilities
                if not known:
                    _fail(
                        ROOM_CAPABILITY_UNKNOWN_CAPABILITY_CODE,
                        f"manifest {manifest.manifest_id!r} names capability"
                        f" {capability_id!r}, which is not in the catalog",
                    )
        self._by_id[manifest.manifest_id] = manifest
        return manifest

    # -- reads -----------------------------------------------------------
    @property
    def manifests(self) -> tuple[RoomCapabilityManifest, ...]:
        return tuple(self._by_id[key] for key in sorted(self._by_id))

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._by_id)

    def get(self, manifest_id: str) -> RoomCapabilityManifest:
        clean = _token(manifest_id, "manifest_id")
        manifest = self._by_id.get(clean)
        if manifest is None:
            _fail(
                ROOM_CAPABILITY_UNKNOWN_ROOM_CODE,
                f"unknown manifest {clean!r}",
            )
        return manifest

    def manifests_for_kind(
        self, room_kind: str
    ) -> tuple[RoomCapabilityManifest, ...]:
        clean = _token(room_kind, "room_kind")
        rows = tuple(
            manifest
            for manifest in self.manifests
            if clean in manifest.room_kinds
        )
        if not rows:
            _fail(
                ROOM_CAPABILITY_UNKNOWN_ROOM_CODE,
                f"no manifest covers room kind {clean!r}",
            )
        return rows

    def manifests_for_archetype(
        self, archetype: str
    ) -> tuple[RoomCapabilityManifest, ...]:
        clean = _token(archetype, "archetype")
        rows = tuple(
            manifest
            for manifest in self.manifests
            if clean in manifest.archetypes
        )
        if not rows:
            _fail(
                ROOM_CAPABILITY_UNKNOWN_ROOM_CODE,
                f"no manifest covers archetype {clean!r}",
            )
        return rows

    def manifests_for_department(
        self, department_id: str
    ) -> tuple[RoomCapabilityManifest, ...]:
        clean = _token(department_id, "department_id")
        return tuple(
            manifest
            for manifest in self.manifests
            if clean in manifest.departments
        )

    def manifest_for(
        self,
        room: Any,
        *,
        department_id: str | None = None,
    ) -> RoomCapabilityManifest:
        """Resolve the one manifest a concrete room hosts.

        Kind wins over archetype when both are present; the department-named
        manifest wins over a shared (department-less) one. Two equally specific
        matches is a typed ambiguity, never a silent pick.
        """
        room_kind = getattr(room, "kind", None)
        archetype = getattr(room, "archetype", None)
        if department_id is None:
            department_id = getattr(room, "department_id", None)
        if isinstance(department_id, str):
            department_id = _token(department_id, "department_id")
        elif department_id is not None:
            _fail(
                ROOM_CAPABILITY_INVALID_CODE,
                "department_id must be a string or None",
            )

        candidates = self._candidates(kind=room_kind, archetype=archetype)
        if not candidates:
            label = room_kind or archetype or getattr(room, "room_id", "room")
            _fail(
                ROOM_CAPABILITY_UNKNOWN_ROOM_CODE,
                f"no manifest covers room {label!r}",
            )
        specific = tuple(
            manifest
            for manifest in candidates
            if department_id is not None
            and department_id in manifest.departments
        )
        if specific:
            candidates = specific
        else:
            candidates = tuple(
                manifest for manifest in candidates if not manifest.departments
            )
            if not candidates:
                _fail(
                    ROOM_CAPABILITY_UNKNOWN_ROOM_CODE,
                    f"no manifest covers room {room_kind or archetype!r} in"
                    f" department {department_id!r}",
                )
        if len(candidates) > 1:
            names = ", ".join(sorted(item.manifest_id for item in candidates))
            _fail(
                ROOM_CAPABILITY_AMBIGUOUS_CODE,
                f"room {room_kind or archetype!r} matches more than one"
                f" manifest: {names}",
            )
        return candidates[0]

    def _candidates(
        self, *, kind: str | None, archetype: str | None
    ) -> tuple[RoomCapabilityManifest, ...]:
        if isinstance(kind, str):
            kind = kind.strip()
            rows = tuple(
                manifest for manifest in self.manifests
                if kind in manifest.room_kinds
            )
            if rows:
                return rows
        if isinstance(archetype, str):
            archetype = archetype.strip()
            return tuple(
                manifest for manifest in self.manifests
                if archetype in manifest.archetypes
            )
        return ()

    def counts_by_department(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for manifest in self.manifests:
            for department in manifest.departments or ("(shared)",):
                counts[department] = counts.get(department, 0) + 1
        return dict(sorted(counts.items()))

    def counts_by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for manifest in self.manifests:
            for kind in manifest.room_kinds or ("(archetype-only)",):
                counts[kind] = counts.get(kind, 0) + 1
        return dict(sorted(counts.items()))

    # -- coverage --------------------------------------------------------
    def validate_coverage(
        self, roles: RoleRegistry | None = None
    ) -> CoverageReport:
        """Refuse every room in the role registry that has no manifest."""
        registry = roles if roles is not None else self._roles
        if registry is None:
            _fail(
                BAD_INPUT_CODE,
                "validate_coverage needs a RoleRegistry (pass one or build the"
                " registry with roles=...)",
            )
        total = 0
        covered = 0
        by_department: dict[str, int] = {}
        used: list[str] = []
        uncovered: list[str] = []
        for floor in registry.floors:
            for department_id in floor.department_ids:
                by_department.setdefault(department_id, 0)
                for room in floor.rooms:
                    total += 1
                    try:
                        manifest = self.manifest_for(
                            room, department_id=department_id
                        )
                    except RoomCapabilityError as exc:
                        if exc.code not in (
                            ROOM_CAPABILITY_UNKNOWN_ROOM_CODE,
                            ROOM_CAPABILITY_AMBIGUOUS_CODE,
                        ):
                            raise
                        uncovered.append(
                            f"{floor.floor_id}/{room.room_id} ({room.kind})"
                        )
                        continue
                    covered += 1
                    by_department[department_id] += 1
                    if manifest.manifest_id not in used:
                        used.append(manifest.manifest_id)
        if uncovered:
            _fail(
                ROOM_CAPABILITY_UNCOVERED_CODE,
                "rooms without a capability manifest (a decorative room is not"
                f" a finished room): {', '.join(uncovered)}",
            )
        return CoverageReport(
            total_rooms=total,
            covered_rooms=covered,
            by_department=by_department,
            manifests_used=tuple(sorted(used)),
        )

    # -- serialization ---------------------------------------------------
    @property
    def digest(self) -> str:
        return fingerprint(
            {
                "manifests": [
                    manifest.to_dict() for manifest in self.manifests
                ]
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "qubit.office.room_capabilities/1",
            "digest": self.digest,
            "manifests": [manifest.to_dict() for manifest in self.manifests],
            "counts_by_department": self.counts_by_department(),
            "counts_by_kind": self.counts_by_kind(),
        }


# ---------------------------------------------------------------- catalog
def _capability(
    capability_id: str,
    category: str,
    resource: str,
    action: str,
    *,
    risk: str = "L1",
    approval: str = "none",
) -> policy_module.Capability:
    return policy_module.Capability(
        capability_id=capability_id,
        category=category,
        resource=resource,
        action=action,
        risk=risk,
        approval_policy=approval,
    )


_CAPABILITY_ROWS = (
    _capability("office.work.execute", "office", "room.work", "execute"),
    _capability("office.work.assign", "office", "task", "assign"),
    _capability("office.artifact.publish", "office", "artifact", "publish"),
    _capability("office.war_room.convene", "office", "room.war_room", "convene"),
    _capability("office.decision.record", "office", "decision", "record"),
    _capability("office.focus.session", "office", "room.focus", "session"),
    _capability("office.task.checkpoint", "office", "task", "checkpoint"),
    _capability(
        "trading.market_data.read", "trading", "market_data", "read"
    ),
    _capability("trading.strategy.backtest", "trading", "strategy", "backtest"),
    _capability("trading.risk.limits.read", "trading", "risk.limits", "read"),
    _capability(
        "trading.risk.limits.write",
        "trading",
        "risk.limits",
        "write",
        risk="L4",
        approval="required",
    ),
    _capability("trading.order.propose", "trading", "order", "propose"),
    _capability(
        "trading.order.approve",
        "trading",
        "order",
        "approve",
        risk="L4",
        approval="required",
    ),
    _capability(
        "trading.order.execute",
        "trading",
        "order",
        "execute",
        risk="L4",
        approval="required",
    ),
    _capability(
        "trading.order.cancel",
        "trading",
        "order",
        "cancel",
        risk="L3",
    ),
    _capability("trading.paper_broker.run", "trading", "paper_broker", "run"),
    _capability("trading.ledger.post", "trading", "portfolio.ledger", "post"),
    _capability(
        "trading.portfolio.reconcile", "trading", "portfolio", "reconcile"
    ),
    _capability(
        "trading.incident.respond",
        "trading",
        "incident",
        "respond",
        risk="L3",
    ),
)


def shipped_capability_catalog() -> policy_module.CapabilityCatalog:
    """The minimal capability catalog the shipped room workflows require."""
    return policy_module.CapabilityCatalog(_CAPABILITY_ROWS)


# ---------------------------------------------------------------- shipped set
_KIND_PROFILES: Mapping[str, Mapping[str, Any]] = {
    "work": {
        "workflow": "work",
        "name": "Department work execution",
        "capabilities": (
            "office.work.execute",
            "office.work.assign",
            "office.artifact.publish",
        ),
        "tools": ("task.board", "artifact.store", "workspace.write"),
        "artifacts": ("document", "note"),
        "queue": "department_work",
        "task": "department_task",
        "kpis": ("throughput", "cycle_time"),
        "memory": "department",
        "privacy": "department",
        "approval": "none",
        "agents": 1,
        "modes": ("work", "review"),
    },
    "war_room": {
        "workflow": "war_room",
        "name": "War Room decision",
        "capabilities": ("office.war_room.convene", "office.decision.record"),
        "tools": ("warroom.session", "artifact.store"),
        "artifacts": ("decision_record", "brief"),
        "queue": "war_room_queue",
        "task": "war_room_task",
        "kpis": ("decision_turnaround", "decision_count"),
        "memory": "room",
        "privacy": "restricted",
        "approval": "conditional",
        "agents": 2,
        "modes": ("meeting", "review"),
    },
    "focus": {
        "workflow": "focus",
        "name": "Focus session",
        "capabilities": ("office.focus.session", "office.task.checkpoint"),
        "tools": ("workspace.write", "checkpoint.store"),
        "artifacts": ("checkpoint", "note"),
        "queue": "focus_queue",
        "task": "focus_task",
        "kpis": ("focus_completion",),
        "memory": "session",
        "privacy": "department",
        "approval": "none",
        "agents": 1,
        "modes": ("focus",),
    },
}

_TRADING_PROFILES: Mapping[str, Mapping[str, Any]] = {
    "trading_floor": {
        "workflow": "trading_execution",
        "name": "Trading order execution",
        "roles": (
            "finance-payments",
            "finance-controller",
            "compliance-officer",
            "analytics-engineer",
        ),
        "capabilities": (
            "trading.market_data.read",
            "trading.order.propose",
            "trading.order.approve",
            "trading.risk.limits.read",
            "trading.order.execute",
            "trading.paper_broker.run",
        ),
        "tools": ("market_data.feed", "paper_broker", "order.oms", "risk.engine"),
        "artifacts": ("order_receipt", "trade_record"),
        "queue": "trading_orders",
        "task": "trading_order",
        "kpis": ("fill_rate", "slippage", "realized_pnl"),
        "memory": "department",
        "privacy": "restricted",
        "approval": "required",
        "agents": 3,
        "modes": ("work", "replay"),
        "execution": ("trading.order.execute",),
        "risk": ("trading.risk.limits.read", "trading.order.approve"),
        "approval_caps": ("trading.order.approve",),
        "risk_limits": ("trading.risk.limits.write",),
        "execution_roles": ("finance-payments",),
        "risk_roles": ("compliance-officer",),
        "approval_roles": ("finance-controller",),
    },
    "risk_room": {
        "workflow": "risk_governance",
        "name": "Independent risk review and limits",
        "roles": ("compliance-officer", "finance-controller", "security-officer"),
        "capabilities": (
            "trading.risk.limits.read",
            "trading.risk.limits.write",
            "trading.order.approve",
            "trading.incident.respond",
        ),
        "tools": ("risk.engine", "order.cancel"),
        "artifacts": ("risk_report", "limit_change"),
        "queue": "risk_reviews",
        "task": "risk_review",
        "kpis": ("drawdown", "exposure_limit_breaches"),
        "memory": "room",
        "privacy": "restricted",
        "approval": "required",
        "agents": 2,
        "modes": ("review", "incident"),
        "risk": (
            "trading.risk.limits.read",
            "trading.risk.limits.write",
            "trading.order.approve",
        ),
        "risk_limits": ("trading.risk.limits.write",),
        "risk_roles": ("compliance-officer", "finance-controller"),
    },
    "strategy_lab": {
        "workflow": "strategy_research",
        "name": "Strategy research and backtest",
        "roles": (
            "research-market",
            "research-validation",
            "data-analyst",
            "analytics-engineer",
        ),
        "capabilities": (
            "trading.market_data.read",
            "trading.strategy.backtest",
            "trading.risk.limits.read",
            "trading.paper_broker.run",
        ),
        "tools": ("market_data.feed", "backtester", "research.search"),
        "artifacts": ("backtest_report", "strategy_spec"),
        "queue": "strategy_research",
        "task": "strategy_research_task",
        "kpis": ("backtest_sharpe", "out_of_sample_return"),
        "memory": "department",
        "privacy": "department",
        "approval": "none",
        "agents": 2,
        "modes": ("work", "replay"),
    },
    "data_room": {
        "workflow": "market_data_health",
        "name": "Market data quality",
        "roles": ("analytics-engineer", "data-analyst", "security-officer"),
        "capabilities": ("trading.market_data.read", "trading.risk.limits.read"),
        "tools": ("market_data.feed", "data.health"),
        "artifacts": ("data_snapshot", "health_report"),
        "queue": "data_health",
        "task": "data_health_task",
        "kpis": ("feed_uptime", "stale_data_incidents"),
        "memory": "department",
        "privacy": "department",
        "approval": "none",
        "agents": 1,
        "modes": ("work", "incident"),
    },
    "treasury": {
        "workflow": "portfolio_accounting",
        "name": "Portfolio accounting and reconciliation",
        "roles": (
            "finance-bookkeeper",
            "finance-reconciliation",
            "finance-reporting",
        ),
        "capabilities": (
            "trading.ledger.post",
            "trading.portfolio.reconcile",
        ),
        "tools": ("portfolio.ledger", "reconciliation"),
        "artifacts": ("ledger_entry", "reconciliation_report"),
        "queue": "portfolio_reconciliation",
        "task": "reconciliation_task",
        "kpis": ("reconciliation_delta", "pnl_accuracy"),
        "memory": "department",
        "privacy": "restricted",
        "approval": "required",
        "agents": 2,
        "modes": ("work", "review"),
    },
    "compliance_archive": {
        "workflow": "trading_compliance",
        "name": "Trading compliance and audit",
        "roles": (
            "compliance-officer",
            "legal-counsel",
            "legal-contracts",
        ),
        "capabilities": (
            "trading.order.approve",
            "trading.risk.limits.read",
        ),
        "tools": ("audit.log", "approval.journal"),
        "artifacts": ("audit_record", "approval_record"),
        "queue": "compliance_reviews",
        "task": "compliance_review",
        "kpis": ("approval_completeness", "audit_coverage"),
        "memory": "room",
        "privacy": "restricted",
        "approval": "required",
        "agents": 1,
        "modes": ("review",),
        "risk": (
            "trading.order.approve",
            "trading.risk.limits.read",
        ),
        "risk_roles": ("compliance-officer",),
    },
    "incident_room": {
        "workflow": "trading_incident",
        "name": "Trading incident response",
        "roles": (
            "incident-responder",
            "security-officer",
            "finance-controller",
        ),
        "capabilities": (
            "trading.incident.respond",
            "trading.order.cancel",
            "trading.risk.limits.read",
        ),
        "tools": ("order.cancel", "kill_switch", "incident.log"),
        "artifacts": ("incident_report", "cancel_receipt"),
        "queue": "trading_incidents",
        "task": "incident_task",
        "kpis": ("time_to_cancel", "incident_recovery_time"),
        "memory": "room",
        "privacy": "restricted",
        "approval": "required",
        "agents": 2,
        "modes": ("incident", "review"),
        "risk": (
            "trading.order.cancel",
            "trading.risk.limits.read",
        ),
        "risk_roles": ("incident-responder",),
    },
}


def _manifest_for_profile(
    *,
    manifest_id: str,
    workflow_id: str,
    workflow_name: str,
    room_kinds: tuple[str, ...],
    archetypes: tuple[str, ...],
    departments: tuple[str, ...],
    role_ids: tuple[str, ...],
    capabilities: tuple[str, ...],
    tools: tuple[str, ...],
    artifacts: tuple[str, ...],
    queue: str,
    task: str,
    kpis: tuple[str, ...],
    memory: str,
    privacy: str,
    approval: str,
    agents: int,
    modes: tuple[str, ...],
    rationale: str,
    execution: tuple[str, ...] = (),
    risk: tuple[str, ...] = (),
    approval_caps: tuple[str, ...] = (),
    risk_limits: tuple[str, ...] = (),
    execution_roles: tuple[str, ...] = (),
    risk_roles: tuple[str, ...] = (),
    approval_roles: tuple[str, ...] = (),
) -> RoomCapabilityManifest:
    required_caps = tuple(
        dict.fromkeys(capabilities + execution + risk + approval_caps)
    )
    required_roles = tuple(
        dict.fromkeys(
            role_ids
            + execution_roles
            + risk_roles
            + approval_roles
        )
    )
    return RoomCapabilityManifest(
        manifest_id=manifest_id,
        room_kinds=room_kinds,
        archetypes=archetypes,
        departments=departments,
        workflow_id=workflow_id,
        workflow_name=workflow_name,
        required_role_ids=required_roles,
        required_capabilities=required_caps,
        allowed_tools=tools,
        artifact_kinds=artifacts,
        queue_kind=queue,
        task_kind=task,
        kpi_ids=kpis,
        memory_scope=memory,
        privacy_class=privacy,
        approval_policy=approval,
        required_agent_count=agents,
        room_modes=modes,
        execution_capabilities=execution,
        risk_capabilities=risk,
        approval_capabilities=approval_caps,
        risk_limit_capabilities=risk_limits,
        execution_role_ids=execution_roles,
        risk_role_ids=risk_roles,
        approval_role_ids=approval_roles,
        rationale=rationale,
    )


def build_shipped_manifests(
    roles: RoleRegistry,
) -> tuple[RoomCapabilityManifest, ...]:
    """One manifest per ``(department, room kind)`` the shipped floors use."""
    grouped: dict[tuple[str, str], set[str]] = {}
    for floor in roles.floors:
        for department_id in floor.department_ids:
            for room in floor.rooms:
                grouped.setdefault((department_id, room.kind), set()).update(
                    room.role_ids
                )
    manifests: list[RoomCapabilityManifest] = []
    for department_id, room_kind in sorted(grouped):
        profile = _KIND_PROFILES.get(room_kind)
        if profile is None:
            _fail(
                ROOM_CAPABILITY_UNKNOWN_ROOM_CODE,
                f"shipped room kind {room_kind!r} has no capability profile",
            )
        role_ids = tuple(sorted(grouped[(department_id, room_kind)]))
        manifests.append(
            _manifest_for_profile(
                manifest_id=f"roomcap:{department_id}:{room_kind}",
                workflow_id=f"{department_id}.{profile['workflow']}",
                workflow_name=f"{department_id} · {profile['name']}",
                room_kinds=(room_kind,),
                archetypes=(),
                departments=(department_id,),
                role_ids=role_ids,
                capabilities=tuple(profile["capabilities"]),
                tools=tuple(profile["tools"]),
                artifacts=tuple(profile["artifacts"]),
                queue=str(profile["queue"]),
                task=str(profile["task"]),
                kpis=tuple(profile["kpis"]),
                memory=str(profile["memory"]),
                privacy=str(profile["privacy"]),
                approval=str(profile["approval"]),
                agents=int(profile["agents"]),
                modes=tuple(profile["modes"]),
                rationale=(
                    f"{department_id} rooms of kind {room_kind!r} run the"
                    f" {profile['workflow']!r} workflow"
                ),
            )
        )
    return tuple(manifests)


def build_trading_manifests() -> tuple[RoomCapabilityManifest, ...]:
    """The Trading Department rooms, with hard risk/approval/execution separation."""
    manifests: list[RoomCapabilityManifest] = []
    for room_kind, profile in sorted(_TRADING_PROFILES.items()):
        manifests.append(
            _manifest_for_profile(
                manifest_id=f"roomcap:{TRADING_DEPARTMENT}:{room_kind}",
                workflow_id=f"{TRADING_DEPARTMENT}.{profile['workflow']}",
                workflow_name=f"Trading · {profile['name']}",
                room_kinds=(room_kind,),
                archetypes=(room_kind,),
                departments=(TRADING_DEPARTMENT,),
                role_ids=tuple(profile["roles"]),
                capabilities=tuple(profile["capabilities"]),
                tools=tuple(profile["tools"]),
                artifacts=tuple(profile["artifacts"]),
                queue=str(profile["queue"]),
                task=str(profile["task"]),
                kpis=tuple(profile["kpis"]),
                memory=str(profile["memory"]),
                privacy=str(profile["privacy"]),
                approval=str(profile["approval"]),
                agents=int(profile["agents"]),
                modes=tuple(profile["modes"]),
                rationale=(
                    f"Trading {room_kind!r} runs {profile['workflow']!r};"
                    " execution, risk and approval authority stay separated"
                ),
                execution=tuple(profile.get("execution", ())),
                risk=tuple(profile.get("risk", ())),
                approval_caps=tuple(profile.get("approval_caps", ())),
                risk_limits=tuple(profile.get("risk_limits", ())),
                execution_roles=tuple(profile.get("execution_roles", ())),
                risk_roles=tuple(profile.get("risk_roles", ())),
                approval_roles=tuple(profile.get("approval_roles", ())),
            )
        )
    return tuple(manifests)


def build_shipped_registry(
    *,
    roles: RoleRegistry | None = None,
    capabilities: Any | None = None,
) -> RoomCapabilityRegistry:
    """Compose the full manifest set for the shipped role/floors registry."""
    role_registry = roles if roles is not None else RoleRegistry.default()
    catalog = (
        capabilities
        if capabilities is not None
        else shipped_capability_catalog()
    )
    manifests = build_shipped_manifests(role_registry) + build_trading_manifests()
    return RoomCapabilityRegistry(
        manifests, roles=role_registry, capabilities=catalog
    )


__all__ = [
    "CoverageReport",
    "PRIVACY_CLASSES",
    "ROOM_MODES",
    "ROOM_CAPABILITY_AMBIGUOUS_CODE",
    "ROOM_CAPABILITY_DUPLICATE_CODE",
    "ROOM_CAPABILITY_INVALID_CODE",
    "ROOM_CAPABILITY_SEPARATION_CODE",
    "ROOM_CAPABILITY_UNCOVERED_CODE",
    "ROOM_CAPABILITY_UNKNOWN_CAPABILITY_CODE",
    "ROOM_CAPABILITY_UNKNOWN_ROLE_CODE",
    "ROOM_CAPABILITY_UNKNOWN_ROOM_CODE",
    "ROOM_CAPABILITY_WILDCARD_CODE",
    "RoomCapabilityError",
    "RoomCapabilityManifest",
    "RoomCapabilityRegistry",
    "TRADING_DEPARTMENT",
    "build_shipped_manifests",
    "build_shipped_registry",
    "build_trading_manifests",
    "shipped_capability_catalog",
]
