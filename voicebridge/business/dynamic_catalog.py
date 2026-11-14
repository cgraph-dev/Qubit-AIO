"""Dynamic capability catalog composed from injected providers (P2-03).

The Office capability catalog is *declarative*: it lists what exists, where it
came from and how healthy/pinned it is. Authority is **not** decided here.
``snapshot_for`` reuses :func:`voicebridge.business.policy.compile_snapshot`, so
there is exactly one policy engine and deny-by-default/deny-wins semantics are
unchanged.

Rules enforced by this module:

* **Injected sources, no hardcoded provider.** The catalog knows nothing about
  a research vendor, a model vendor or a live MCP server. Callers inject
  :class:`CatalogSource` implementations; tests inject pure functions. An empty
  catalog exposes an empty capability set.
* **Skills are requirements.** A skill can *require* capabilities; it can never
  be a capability grant. Skill requirements are stored separately from
  capability records, and a grant that names a skill id is refused by the policy
  catalog as unknown.
* **MCP tools carry a complete schema hash.** The hash covers both the input and
  output schema (and optional annotations), and a per-server allowlist decides
  which tools are admitted to the catalog at all.
* **Availability is typed.** Every record is ``available``, ``degraded``,
  ``unsupported`` or ``unconfigured``. A non-available record must carry a
  non-empty reason code.
* **Deterministic digest.** The digest is canonical JSON over the pinned
  catalog version and the sorted records, so re-composition with the same
  inputs yields the same digest and a health/version/schema change changes it.

No store handle, no network, no clock other than the caller's ``issued_at``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol, runtime_checkable

from . import policy as policy_module
from .errors import BAD_INPUT_CODE, BusinessError
from .ids import fingerprint

# Stable refusal codes.
CATALOG_INVALID_CODE = "BUSINESS_CATALOG_INVALID"
CATALOG_DUPLICATE_CODE = "BUSINESS_CATALOG_DUPLICATE"
CATALOG_UNKNOWN_CODE = policy_module.CAPABILITY_UNKNOWN_CODE
CATALOG_UNAVAILABLE_CODE = "BUSINESS_CATALOG_CAPABILITY_UNAVAILABLE"
CATALOG_UNSUPPORTED_CODE = "BUSINESS_CATALOG_CAPABILITY_UNSUPPORTED"
MCP_NOT_ALLOWLISTED_CODE = "BUSINESS_CATALOG_MCP_NOT_ALLOWLISTED"

AVAILABILITY_STATES = ("available", "degraded", "unsupported", "unconfigured")
HEALTH_STATES = ("healthy", "degraded", "unavailable")
SOURCE_KINDS = (
    "tools",
    "toolsets",
    "mcp",
    "plugins",
    "skills",
    "connectors",
    "model_providers",
)

_WILDCARDS = frozenset({"*", "all", "any", "ALL", "ANY", "All", "Any"})


def _text(value: Any, label: str, *, max_len: int = 300) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(
            CATALOG_INVALID_CODE, f"{label} must be a non-empty string"
        )
    clean = value.strip()
    if len(clean) > max_len:
        raise BusinessError(
            CATALOG_INVALID_CODE, f"{label} is longer than {max_len} characters"
        )
    return clean


def _explicit(value: Any, label: str, *, max_len: int = 300) -> str:
    clean = _text(value, label, max_len=max_len)
    if clean in _WILDCARDS or "*" in clean:
        raise BusinessError(
            CATALOG_INVALID_CODE,
            f"{label} must be an exact value; wildcards are not allowed",
        )
    return clean


def _approval(value: Any) -> str:
    if value is True:
        return "required"
    if value is False or value is None:
        return "none"
    clean = str(value).strip().lower()
    if clean not in policy_module.APPROVAL_POLICIES:
        raise BusinessError(
            CATALOG_INVALID_CODE,
            "approval_policy must be one of"
            f" {', '.join(sorted(policy_module.APPROVAL_POLICIES))}",
        )
    return clean


def _optional_non_negative(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise BusinessError(
            CATALOG_INVALID_CODE,
            f"{label} must be a non-negative integer or None",
        )
    return value


def schema_fingerprint(
    input_schema: Mapping[str, Any] | None,
    output_schema: Mapping[str, Any] | None = None,
    *,
    annotations: Mapping[str, Any] | None = None,
) -> str:
    """SHA-256 over a tool's **complete** input/output schema (+ annotations).

    Hashing only the description (or only the input schema) lets a server flip
    its output shape or annotations without invalidating the pin. This covers
    all of it.
    """
    for label, value in (
        ("input_schema", input_schema),
        ("output_schema", output_schema),
        ("annotations", annotations),
    ):
        if value is not None and not isinstance(value, Mapping):
            raise BusinessError(
                CATALOG_INVALID_CODE, f"{label} must be a mapping or None"
            )
    return fingerprint(
        {
            "input": dict(input_schema or {}),
            "output": dict(output_schema or {}),
            "annotations": dict(annotations or {}),
        }
    )


@dataclass(frozen=True, slots=True)
class CapabilityRecord:
    """One catalogued capability with provenance, pin and availability."""

    capability_id: str
    category: str
    source: str
    provider: str
    resource: str
    action: str
    risk: str = "L0"
    required_scopes: tuple[str, ...] = ()
    approval_policy: str = "none"
    budget_minor: int | None = None
    rate_limit: int | None = None
    health: str = "healthy"
    version: str = "1"
    availability: str = "available"
    availability_reason: str = ""
    input_schema_hash: str = ""
    output_schema_hash: str = ""
    schema_hash: str = ""
    server: str = ""
    allowlist: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "capability_id", _explicit(self.capability_id, "capability_id")
        )
        object.__setattr__(
            self, "category", _explicit(self.category, "category")
        )
        source = _text(self.source, "source", max_len=60)
        if source not in SOURCE_KINDS:
            raise BusinessError(
                CATALOG_INVALID_CODE,
                f"source must be one of {', '.join(SOURCE_KINDS)}",
            )
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "provider", _text(self.provider, "provider"))
        object.__setattr__(self, "resource", _explicit(self.resource, "resource"))
        object.__setattr__(self, "action", _explicit(self.action, "action"))
        if self.risk not in policy_module.AUTONOMY_LEVELS:
            raise BusinessError(
                CATALOG_INVALID_CODE,
                f"risk must be one of"
                f" {', '.join(policy_module.AUTONOMY_LEVELS)}",
            )
        scopes = self.required_scopes
        if isinstance(scopes, str) or not isinstance(scopes, (tuple, list)):
            raise BusinessError(
                CATALOG_INVALID_CODE,
                "required_scopes must be a sequence of strings",
            )
        object.__setattr__(
            self,
            "required_scopes",
            tuple(_explicit(scope, "required scope") for scope in scopes),
        )
        object.__setattr__(
            self, "approval_policy", _approval(self.approval_policy)
        )
        object.__setattr__(
            self,
            "budget_minor",
            _optional_non_negative(self.budget_minor, "budget_minor"),
        )
        object.__setattr__(
            self,
            "rate_limit",
            _optional_non_negative(self.rate_limit, "rate_limit"),
        )
        health = _text(self.health, "health", max_len=60).lower()
        if health not in HEALTH_STATES:
            raise BusinessError(
                CATALOG_INVALID_CODE,
                f"health must be one of {', '.join(HEALTH_STATES)}",
            )
        object.__setattr__(self, "health", health)
        object.__setattr__(self, "version", _text(self.version, "version"))
        availability = _text(
            self.availability, "availability", max_len=60
        ).lower()
        if availability not in AVAILABILITY_STATES:
            raise BusinessError(
                CATALOG_INVALID_CODE,
                f"availability must be one of {', '.join(AVAILABILITY_STATES)}",
            )
        object.__setattr__(self, "availability", availability)
        reason = str(self.availability_reason or "").strip()
        if availability != "available" and not reason:
            raise BusinessError(
                CATALOG_INVALID_CODE,
                f"availability {availability!r} requires a reason code",
            )
        object.__setattr__(self, "availability_reason", reason)
        object.__setattr__(
            self,
            "input_schema_hash",
            str(self.input_schema_hash or "").strip(),
        )
        object.__setattr__(
            self,
            "output_schema_hash",
            str(self.output_schema_hash or "").strip(),
        )
        object.__setattr__(
            self, "schema_hash", str(self.schema_hash or "").strip()
        )
        object.__setattr__(self, "server", str(self.server or "").strip())
        allowlist = self.allowlist
        if isinstance(allowlist, str) or not isinstance(allowlist, (tuple, list)):
            raise BusinessError(
                CATALOG_INVALID_CODE,
                "allowlist must be a sequence of strings",
            )
        object.__setattr__(
            self,
            "allowlist",
            tuple(_explicit(item, "allowlist entry") for item in allowlist),
        )
        if not isinstance(self.metadata, Mapping):
            raise BusinessError(
                CATALOG_INVALID_CODE, "metadata must be a mapping"
            )
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_policy_capability(self) -> policy_module.Capability:
        """Project onto the one policy engine's capability record."""
        return policy_module.Capability(
            capability_id=self.capability_id,
            category=self.category,
            resource=self.resource,
            action=self.action,
            risk=self.risk,
            required_scopes=self.required_scopes,
            approval_policy=self.approval_policy,
            budget_minor=self.budget_minor,
            rate_limit=self.rate_limit,
            health=self.health,
            version=self.version,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "category": self.category,
            "source": self.source,
            "provider": self.provider,
            "resource": self.resource,
            "action": self.action,
            "risk": self.risk,
            "required_scopes": list(self.required_scopes),
            "approval_policy": self.approval_policy,
            "budget_minor": self.budget_minor,
            "rate_limit": self.rate_limit,
            "health": self.health,
            "version": self.version,
            "availability": self.availability,
            "availability_reason": self.availability_reason,
            "input_schema_hash": self.input_schema_hash,
            "output_schema_hash": self.output_schema_hash,
            "schema_hash": self.schema_hash,
            "server": self.server,
            "allowlist": list(self.allowlist),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class SkillRequirement:
    """A skill *requires* capabilities; it never grants one."""

    skill_id: str
    version: str = "1"
    required_capabilities: tuple[str, ...] = ()
    binding: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "skill_id", _explicit(self.skill_id, "skill_id")
        )
        object.__setattr__(self, "version", _text(self.version, "version"))
        caps = self.required_capabilities
        if isinstance(caps, str) or not isinstance(caps, (tuple, list)):
            raise BusinessError(
                CATALOG_INVALID_CODE,
                "required_capabilities must be a sequence of strings",
            )
        object.__setattr__(
            self,
            "required_capabilities",
            tuple(_explicit(cap, "required capability") for cap in caps),
        )
        object.__setattr__(
            self, "binding", str(self.binding or "").strip()
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "required_capabilities": list(self.required_capabilities),
            "binding": self.binding,
        }


def tool_capability(
    tool_id: str,
    *,
    provider: str = "qubit",
    category: str = "tools",
    source: str = "tools",
    resource: str | None = None,
    action: str = "invoke",
    **overrides: Any,
) -> CapabilityRecord:
    """Build a Qubit tool/toolset capability record."""
    return CapabilityRecord(
        capability_id=f"tool:{tool_id}",
        category=category,
        source=source,
        provider=provider,
        resource=resource or tool_id,
        action=action,
        **overrides,
    )


def mcp_capability(
    server: str,
    tool: str,
    *,
    input_schema: Mapping[str, Any] | None = None,
    output_schema: Mapping[str, Any] | None = None,
    annotations: Mapping[str, Any] | None = None,
    allowlist: Iterable[str] = (),
    **overrides: Any,
) -> CapabilityRecord:
    """Build one MCP tool capability with a complete schema hash.

    ``allowlist`` is enforced here: a tool that is not on the server's
    allowlist is a typed refusal, not a silently admitted capability.
    """
    server_name = _explicit(server, "server")
    tool_name = _explicit(tool, "tool")
    allow = tuple(allowlist)
    if allow and tool_name not in allow:
        raise BusinessError(
            MCP_NOT_ALLOWLISTED_CODE,
            f"MCP tool {tool_name!r} is not on server {server_name!r}'s"
            " allowlist",
        )
    provider = overrides.pop("provider", server_name)
    input_hash = fingerprint(
        {
            "input": dict(input_schema or {}),
            "annotations": dict(annotations or {}),
        }
    )
    output_hash = fingerprint({"output": dict(output_schema or {})})
    full_hash = schema_fingerprint(
        input_schema, output_schema, annotations=annotations
    )
    return CapabilityRecord(
        capability_id=f"mcp:{server_name}:{tool_name}",
        category="mcp",
        source="mcp",
        provider=provider,
        resource=f"{server_name}/{tool_name}",
        action="invoke",
        server=server_name,
        allowlist=allow,
        input_schema_hash=input_hash,
        output_schema_hash=output_hash,
        schema_hash=full_hash,
        **overrides,
    )


def mcp_server_capabilities(
    server: Mapping[str, Any],
) -> tuple[CapabilityRecord, ...]:
    """Compose every allowlisted tool of one injected MCP server row.

    The row shape is intentionally small and provider-agnostic::

        {"name": "docs", "version": "1.2", "health": "healthy",
         "availability": "available", "allowlist": ["search"],
         "tools": [{"name": "search", "inputSchema": {...},
                    "outputSchema": {...}}]}
    """
    if not isinstance(server, Mapping):
        raise BusinessError(BAD_INPUT_CODE, "MCP server row must be a mapping")
    name = _explicit(server.get("name"), "server name")
    allow = tuple(server.get("allowlist") or ())
    common = {
        "version": str(server.get("version", "1")),
        "health": str(server.get("health", "healthy")),
        "availability": str(server.get("availability", "available")),
        "availability_reason": str(server.get("availability_reason", "")),
    }
    records: list[CapabilityRecord] = []
    for tool in server.get("tools", ()) or ():
        if not isinstance(tool, Mapping):
            raise BusinessError(BAD_INPUT_CODE, "MCP tool row must be a mapping")
        tool_name = _explicit(tool.get("name"), "tool name")
        if allow and tool_name not in allow:
            # The allowlist is the server boundary: unlisted tools are absent.
            continue
        records.append(
            mcp_capability(
                name,
                tool_name,
                input_schema=tool.get("inputSchema"),
                output_schema=tool.get("outputSchema"),
                annotations=tool.get("annotations"),
                allowlist=allow,
                **common,
            )
        )
    return tuple(records)


def plugin_capability(
    plugin_id: str, *, provider: str = "qubit", **overrides: Any
) -> CapabilityRecord:
    return CapabilityRecord(
        capability_id=f"plugin:{plugin_id}",
        category="plugins",
        source="plugins",
        provider=provider,
        resource=plugin_id,
        action="invoke",
        **overrides,
    )


def connector_capability(
    connector_id: str, *, provider: str = "qubit", **overrides: Any
) -> CapabilityRecord:
    return CapabilityRecord(
        capability_id=f"connector:{connector_id}",
        category="connectors",
        source="connectors",
        provider=provider,
        resource=connector_id,
        action="invoke",
        **overrides,
    )


def model_provider_capability(
    provider: str, *, model: str = "", **overrides: Any
) -> CapabilityRecord:
    """A model/provider route is a capability like any other; no vendor is
    assumed and no research provider is hardcoded."""
    provider_name = _explicit(provider, "provider")
    suffix = f":{model}" if model else ""
    return CapabilityRecord(
        capability_id=f"model_provider:{provider_name}{suffix}",
        category="model_providers",
        source="model_providers",
        provider=provider_name,
        resource=model or provider_name,
        action="invoke",
        **overrides,
    )


def skill_requirement(
    skill_id: str,
    *,
    version: str = "1",
    required_capabilities: Iterable[str] = (),
    binding: str = "",
) -> SkillRequirement:
    return SkillRequirement(
        skill_id=skill_id,
        version=version,
        required_capabilities=tuple(required_capabilities),
        binding=binding,
    )


@runtime_checkable
class CatalogSource(Protocol):
    """An injected provider of capability records.

    ``capabilities()`` is the only required method. A skill source may also
    expose ``requirements()`` returning :class:`SkillRequirement` rows; those
    are collected separately and are never capability grants.
    """

    source_id: str

    def capabilities(self) -> Iterable[CapabilityRecord]:
        ...


@dataclass(frozen=True, slots=True)
class FunctionCatalogSource:
    """Adapt one injected callable into a :class:`CatalogSource`."""

    source_id: str
    provider: Callable[[], Iterable[CapabilityRecord]]
    requirement_provider: (
        Callable[[], Iterable[SkillRequirement]] | None
    ) = None

    def capabilities(self) -> Iterable[CapabilityRecord]:
        return tuple(self.provider())

    def requirements(self) -> Iterable[SkillRequirement]:
        if self.requirement_provider is None:
            return ()
        return tuple(self.requirement_provider())


class DynamicCapabilityCatalog:
    """Compose, pin and freeze an Office capability view."""

    def __init__(
        self,
        sources: Iterable[CatalogSource] = (),
        *,
        catalog_version: str = "1",
        policy_revision: str = "catalog-1",
    ) -> None:
        self._sources = tuple(sources)
        self.catalog_version = _text(catalog_version, "catalog_version")
        self.policy_revision = _text(policy_revision, "policy_revision")
        self._records: dict[str, CapabilityRecord] = {}
        self._requirements: dict[str, SkillRequirement] = {}
        self._digest = ""
        self._built = False

    # -- composition -----------------------------------------------------
    def rebuild(self) -> "DynamicCapabilityCatalog":
        records: dict[str, CapabilityRecord] = {}
        requirements: dict[str, SkillRequirement] = {}
        for source in self._sources:
            source_id = getattr(source, "source_id", None)
            if not isinstance(source_id, str) or not source_id.strip():
                raise BusinessError(
                    CATALOG_INVALID_CODE,
                    "each CatalogSource must declare a non-empty source_id",
                )
            for record in source.capabilities():
                if not isinstance(record, CapabilityRecord):
                    raise BusinessError(
                        CATALOG_INVALID_CODE,
                        f"source {source_id!r} yielded a non-CapabilityRecord",
                    )
                if record.capability_id in records:
                    raise BusinessError(
                        CATALOG_DUPLICATE_CODE,
                        f"capability {record.capability_id!r} was declared by"
                        " more than one source",
                    )
                records[record.capability_id] = record
            requirement_fn = getattr(source, "requirements", None)
            if callable(requirement_fn):
                for requirement in requirement_fn():
                    if not isinstance(requirement, SkillRequirement):
                        raise BusinessError(
                            CATALOG_INVALID_CODE,
                            f"source {source_id!r} yielded a non-"
                            "SkillRequirement",
                        )
                    if requirement.skill_id in requirements:
                        raise BusinessError(
                            CATALOG_DUPLICATE_CODE,
                            f"skill requirement {requirement.skill_id!r} was"
                            " declared more than once",
                        )
                    requirements[requirement.skill_id] = requirement
        self._records = records
        self._requirements = requirements
        self._digest = fingerprint(
            {
                "catalog_version": self.catalog_version,
                "records": [
                    record.as_dict()
                    for record in sorted(
                        records.values(),
                        key=lambda row: row.capability_id,
                    )
                ],
                "requirements": [
                    requirement.as_dict()
                    for requirement in sorted(
                        requirements.values(),
                        key=lambda row: row.skill_id,
                    )
                ],
            }
        )
        self._built = True
        return self

    def _ensure(self) -> None:
        if not self._built:
            self.rebuild()

    @property
    def digest(self) -> str:
        self._ensure()
        return self._digest

    @property
    def version(self) -> str:
        return self.catalog_version

    # -- reads -----------------------------------------------------------
    def get(self, capability_id: str) -> CapabilityRecord:
        self._ensure()
        clean = _text(capability_id, "capability_id", max_len=120)
        record = self._records.get(clean)
        if record is None:
            raise BusinessError(
                CATALOG_UNKNOWN_CODE,
                f"capability {clean!r} is not in the catalog",
            )
        return record

    def has(self, capability_id: str) -> bool:
        self._ensure()
        return capability_id in self._records

    def list(self) -> tuple[CapabilityRecord, ...]:
        self._ensure()
        return tuple(
            self._records[key] for key in sorted(self._records)
        )

    def requirements(self) -> tuple[SkillRequirement, ...]:
        self._ensure()
        return tuple(
            self._requirements[key] for key in sorted(self._requirements)
        )

    def required_capabilities_for(
        self, skill_ids: Iterable[str]
    ) -> tuple[str, ...]:
        """Union of the capabilities those skills require, still not grants."""
        self._ensure()
        needed: list[str] = []
        for skill_id in skill_ids:
            requirement = self._requirements.get(
                _text(skill_id, "skill_id")
            )
            if requirement is None:
                raise BusinessError(
                    CATALOG_UNKNOWN_CODE,
                    f"skill {skill_id!r} is not in the catalog",
                )
            for capability in requirement.required_capabilities:
                if capability not in needed:
                    needed.append(capability)
        return tuple(needed)

    def versions(self) -> dict[str, str]:
        self._ensure()
        return {
            record.capability_id: record.version
            for record in self.list()
        }

    def policy_catalog(self) -> policy_module.CapabilityCatalog:
        """Project onto the one policy engine's catalog."""
        self._ensure()
        return policy_module.CapabilityCatalog(
            record.to_policy_capability() for record in self.list()
        )

    def resolve(self, capability_id: str) -> CapabilityRecord:
        """Return a record usable now, or a typed availability refusal.

        ``degraded`` is returned (the caller must surface the reason);
        ``unsupported``/``unconfigured`` are refusals, never a silent pass.
        """
        record = self.get(capability_id)
        if record.availability == "unsupported":
            raise BusinessError(
                CATALOG_UNSUPPORTED_CODE,
                f"capability {record.capability_id!r} is unsupported"
                f" [{record.availability_reason}]",
            )
        if record.availability == "unconfigured":
            raise BusinessError(
                CATALOG_UNAVAILABLE_CODE,
                f"capability {record.capability_id!r} is unconfigured"
                f" [{record.availability_reason}]",
            )
        return record

    def health(self, capability_id: str) -> str:
        return self.get(capability_id).health

    # -- snapshot --------------------------------------------------------
    def snapshot_for(
        self,
        subject: str,
        grants: Iterable[policy_module.Grant] = (),
        *,
        tenant_id: str,
        policy_revision: str | int | None = None,
        denials: Iterable[policy_module.Denial] = (),
        tool_versions: Mapping[str, str] | None = None,
        model_constraints: Mapping[str, Any] | None = None,
        issued_at: float | None = None,
        expires_at: float | None = None,
    ) -> policy_module.CapabilitySnapshot:
        """Compile a per-run snapshot through the existing policy engine."""
        self._ensure()
        pinned_versions = (
            self.versions() if tool_versions is None else tool_versions
        )
        revision = (
            self.policy_revision
            if policy_revision is None
            else policy_revision
        )
        return policy_module.compile_snapshot(
            tenant_id=tenant_id,
            subject_id=subject,
            policy_revision=revision,
            grants=grants,
            denials=denials,
            tool_versions=pinned_versions,
            model_constraints=model_constraints,
            issued_at=time.time() if issued_at is None else issued_at,
            expires_at=expires_at,
            catalog=self.policy_catalog(),
        )

    def __len__(self) -> int:  # pragma: no cover - trivial
        self._ensure()
        return len(self._records)
