"""P2-04 — memory authority, ACL-scoped recall and rebuildable projections.

The Business OS event log is the **only** memory authority. This module never
creates a second database, table or file: every memory is a ``memory.recorded``
event on the aggregate ``memory:<scope>:<scope_id>``, every correction is a
new recorded event that names the record it supersedes, and every deletion is
a tombstone event. History is append-only; the current state is a deterministic
fold over the events.

Projections are derived, never authoritative:

* **Obsidian** — one markdown note per live memory under
  ``projection_dir/<scope>/<memory_id>.md`` with JSON-compatible YAML
  frontmatter, a stable note id, a scope MOC and backlinks. ``rebuild()``
  deletes and rewrites it from events, so the same events always produce the
  same bytes.
* **OpenViking** — an injected adapter over authenticated, scoped collections
  (``office/global``, ``office/<department>``, ``office/<department>/<agent>``).
  There is no network adapter here and no fallback to a wider collection: a
  failure is a typed refusal, never a silent global write.

Reads are deny-by-default. The injected ACL is consulted **per record**, a
record from another tenant is never returned, and ``recall`` can only read the
one aggregate it was asked about — there is no global pool to leak across
departments or tenants.

Retrieved/tool/web content is untrusted (poisoning): a low-confidence or
instruction-like record is quarantined at admission unless the caller
explicitly opts in, and a record marked as a decision/policy cannot be
superseded without an explicit protected-write opt-in.
"""
from __future__ import annotations

import json
import math
import re
import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

from . import eventlog
from .errors import BAD_INPUT_CODE, BusinessError
from .ids import new_id
from .util import now_iso, redact, require_text

# -- stable refusal codes --------------------------------------------------
MEMORY_NOT_FOUND_CODE = "BUSINESS_MEMORY_NOT_FOUND"
MEMORY_READ_DENIED_CODE = "BUSINESS_MEMORY_READ_DENIED"
MEMORY_WRITE_DENIED_CODE = "BUSINESS_MEMORY_WRITE_DENIED"
MEMORY_SCOPE_INVALID_CODE = "BUSINESS_MEMORY_SCOPE_INVALID"
MEMORY_PRINCIPAL_INVALID_CODE = "BUSINESS_MEMORY_PRINCIPAL_INVALID"
MEMORY_PROTECTED_CODE = "BUSINESS_MEMORY_PROTECTED"
MEMORY_OPENVIKING_FAILED_CODE = "BUSINESS_MEMORY_OPENVIKING_FAILED"

SCOPES = ("agent", "session", "task", "department", "office", "tenant")
VISIBILITIES = ("private", "internal", "department", "office", "public")
RETENTION_CLASSES = eventlog.RETENTION_CLASSES
DEFAULT_VISIBILITY = "internal"
DEFAULT_RETENTION = eventlog.DEFAULT_RETENTION_CLASS

#: Content is bounded after redaction; a larger body is truncated, never
#: stored unbounded.
MAX_CONTENT_CHARS = 8000
MAX_PROVENANCE_KEYS = 32
MAX_LABELS = 32
MAX_LABEL_CHARS = 64

#: Sources whose content was produced outside Qubit's own trust boundary.
UNTRUSTED_SOURCES = frozenset(
    {
        "web",
        "search",
        "browser",
        "tool",
        "mcp",
        "retrieved",
        "external",
        "email",
        "file",
        "attachment",
    }
)

#: Below this confidence an untrusted record is quarantined at admission.
MIN_TRUSTED_CONFIDENCE = 0.35

#: Deterministic ranking weights: relevance, importance (confidence), recency.
W_RELEVANCE = 0.5
W_IMPORTANCE = 0.3
W_RECENCY = 0.2
RECENCY_HALF_LIFE_S = 3 * 86400.0

_EVENT_RECORDED = "memory.recorded"
_EVENT_FORGOTTEN = "memory.forgotten"
_EVENT_QUARANTINED = "memory.quarantined"
_EVENT_PROMOTED = "memory.promoted"
_MEMORY_EVENT_TYPES = frozenset(
    {_EVENT_RECORDED, _EVENT_FORGOTTEN, _EVENT_QUARANTINED, _EVENT_PROMOTED}
)

#: Bounded scan for cross-aggregate lookup/rebuild. ``list_events`` is the
#: public reader; memory events are small and this is a control-plane path.
_SCAN_LIMIT = 100_000

_WORD_RE = re.compile(r"[a-z0-9_]{2,}")
_INSTRUCTION_RE = re.compile(
    r"(?i)(?:"
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions"
    r"|disregard\s+(?:the\s+)?(?:previous|prior|above|system)"
    r"|you\s+are\s+now\s+(?:a|an|the)\b"
    r"|system\s*prompt"
    r"|reveal\s+(?:your|the)\s+(?:system|prompt|instructions)"
    r"|execute\s+(?:the\s+following|this)\s+command"
    r"|<\|?system\|?>"
    r"|```\s*system"
    r"|\boverride\s+(?:the\s+)?(?:instructions|policy|system)"
    r"|\bjailbreak\b"
    r")"
)
_EXTRA_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token"
    r"|refresh[_-]?token|client[_-]?secret)\b(\s*[:=]\s*)([^\s,;]+)"
)
_AWS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_REDACTED = "***REDACTED***"

_PROTECTED_SOURCES = frozenset({"decision", "policy", "owner-decision"})
_PROTECTED_LABELS = frozenset({"decision", "policy", "authority"})

_FRONTMATTER_ORDER = (
    "id",
    "scope",
    "scope_id",
    "tenant",
    "source",
    "provenance",
    "confidence",
    "visibility",
    "retention",
    "expires",
    "supersedes",
    "superseded_by",
    "labels",
    "quarantined",
    "created",
)


# -- small helpers ---------------------------------------------------------
def _text(value: Any, label: str, *, max_len: int = 4096) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise BusinessError(BAD_INPUT_CODE, str(exc)) from exc


def _optional_text(value: Any, label: str, *, max_len: int = 4096) -> str | None:
    if value is None:
        return None
    return _text(value, label, max_len=max_len)


def _iso(moment: float | str | None) -> str | None:
    if moment is None:
        return None
    if isinstance(moment, str):
        return _text(moment, "expires_at", max_len=40)
    if isinstance(moment, bool) or not isinstance(moment, (int, float)):
        raise BusinessError(BAD_INPUT_CODE, "expires_at must be an epoch or ISO text")
    return now_iso(float(moment))


def _epoch(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _now(moment: float | None) -> float:
    if moment is None:
        return datetime.now(timezone.utc).timestamp()
    if isinstance(moment, bool) or not isinstance(moment, (int, float)):
        raise BusinessError(BAD_INPUT_CODE, "now must be an epoch number")
    return float(moment)


def _scrub(value: str) -> str:
    """Apply the shared redactor plus memory-specific secret shapes."""
    clean = redact(value)
    clean = _EXTRA_SECRET_RE.sub(rf"\1\2{_REDACTED}", clean)
    clean = _AWS_KEY_RE.sub(_REDACTED, clean)
    return clean


def _bounded_content(value: Any) -> str:
    clean = _scrub(_text(value, "memory content", max_len=MAX_CONTENT_CHARS * 4))
    if len(clean) > MAX_CONTENT_CHARS:
        clean = clean[:MAX_CONTENT_CHARS].rstrip() + "…"
    return clean


def _provenance(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise BusinessError(BAD_INPUT_CODE, "provenance must be a mapping")
    if len(value) > MAX_PROVENANCE_KEYS:
        raise BusinessError(
            BAD_INPUT_CODE,
            f"provenance has more than {MAX_PROVENANCE_KEYS} keys",
        )
    return redact({str(key): item for key, item in value.items()})


def _labels(value: Iterable[str] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, Mapping)):
        raise BusinessError(BAD_INPUT_CODE, "labels must be a sequence of strings")
    clean: list[str] = []
    for item in value:
        label = _text(item, "label", max_len=MAX_LABEL_CHARS).lower()
        if label not in clean:
            clean.append(label)
        if len(clean) >= MAX_LABELS:
            break
    return tuple(clean)


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BusinessError(BAD_INPUT_CODE, "confidence must be a number in [0, 1]")
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        raise BusinessError(BAD_INPUT_CODE, "confidence must be a number in [0, 1]")
    return number


def _scope_pair(scope: Any, scope_id: Any) -> tuple[str, str]:
    clean_scope = _text(scope, "scope", max_len=20).lower()
    if clean_scope not in SCOPES:
        raise BusinessError(
            MEMORY_SCOPE_INVALID_CODE,
            f"unknown scope {clean_scope!r}; expected one of {', '.join(SCOPES)}",
        )
    clean_id = _text(scope_id, "scope_id", max_len=200)
    if ":" in clean_id:
        raise BusinessError(
            MEMORY_SCOPE_INVALID_CODE, "scope_id may not contain ':'"
        )
    return clean_scope, clean_id


def _aggregate(scope: str, scope_id: str) -> str:
    return f"memory:{scope}:{scope_id}"


def _instructions_like(text: str) -> bool:
    return bool(_INSTRUCTION_RE.search(text))


def _protected(record: "MemoryRecord") -> bool:
    return record.source.lower() in _PROTECTED_SOURCES or any(
        label in _PROTECTED_LABELS for label in record.labels
    )


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _relevance(record: "MemoryRecord", query: str) -> float:
    wanted = _tokens(query or "")
    if not wanted:
        return 0.0
    haystack = _tokens(record.content + " " + " ".join(record.labels))
    return len(wanted & haystack) / len(wanted)


def _score(record: "MemoryRecord", query: str, now: float) -> float:
    created = _epoch(record.created_at)
    age = max(0.0, now - created) if created is not None else 0.0
    recency = 0.5 ** (age / RECENCY_HALF_LIFE_S)
    return (
        W_RELEVANCE * _relevance(record, query)
        + W_IMPORTANCE * record.confidence
        + W_RECENCY * recency
    )


# -- principal / ACL contract ---------------------------------------------
@dataclass(frozen=True, slots=True)
class MemoryPrincipal:
    """A minimal principal for callers that do not use the identity module."""

    principal_id: str
    tenant_id: str
    kind: str = "agent"


@runtime_checkable
class MemoryACL(Protocol):
    """Per-record authorization. Both methods must be deny-by-default."""

    def can_read(self, principal: Any, record: "MemoryRecord") -> bool: ...

    def can_write(self, principal: Any, scope: str, record: "MemoryRecord") -> bool: ...


def _principal_field(principal: Any, *names: str) -> Any:
    if isinstance(principal, Mapping):
        for name in names:
            if name in principal:
                return principal[name]
        return None
    for name in names:
        if hasattr(principal, name):
            return getattr(principal, name)
    return None


def _principal_id(principal: Any) -> str:
    if isinstance(principal, str) and principal.strip():
        return principal.strip()
    value = _principal_field(principal, "principal_id", "subject_id", "id")
    if value is None:
        raise BusinessError(
            MEMORY_PRINCIPAL_INVALID_CODE,
            "principal must expose principal_id/subject_id",
        )
    return str(value)


def _principal_tenant(principal: Any) -> str | None:
    value = _principal_field(principal, "tenant_id")
    return None if value is None else str(value)


def _effective_principal(principal: Any, tenant_id: str) -> Any:
    """Bind a tenant onto a principal that did not carry one explicitly."""
    if _principal_tenant(principal) is not None:
        return principal
    return MemoryPrincipal(
        principal_id=_principal_id(principal),
        tenant_id=tenant_id,
        kind=str(_principal_field(principal, "kind") or "agent"),
    )


class SameTenantACL:
    """Reference ACL: same-tenant reads/writes, nothing cross-tenant.

    It is deliberately permissive *inside* a tenant — scope separation is
    enforced by the aggregate a call names, not by this object. Production
    callers inject their own ACL for department/agent separation.
    """

    def can_read(self, principal: Any, record: "MemoryRecord") -> bool:
        return _principal_tenant(principal) == record.tenant_id

    def can_write(self, principal: Any, scope: str, record: "MemoryRecord") -> bool:
        return _principal_tenant(principal) == record.tenant_id


# -- OpenViking adapter contract ------------------------------------------
@runtime_checkable
class OpenVikingAdapter(Protocol):
    """An authenticated, scoped memory mirror. Implementations do no widening."""

    def write(
        self,
        namespace: str,
        collection: str,
        memory_id: str,
        content: str,
        metadata: Mapping[str, Any],
    ) -> None: ...

    def delete(self, namespace: str, collection: str, memory_id: str) -> None: ...


def viking_collection(
    scope: str, scope_id: str, *, department: str | None = None
) -> str:
    """Map a memory scope to its fixed OpenViking collection.

    ``office/global`` is reserved for office/tenant scope. Department and agent
    memories are scoped beneath their department; an agent without a department
    goes to ``office/agents/<agent>`` rather than the global collection.
    """
    clean_scope, clean_id = _scope_pair(scope, scope_id)
    if clean_scope in ("office", "tenant"):
        return "office/global"
    if clean_scope == "department":
        return f"office/{clean_id}"
    if clean_scope == "agent":
        if department:
            return f"office/{_text(department, 'department', max_len=200)}/{clean_id}"
        if "/" in clean_id:
            return f"office/{clean_id}"
        return f"office/agents/{clean_id}"
    return f"office/{clean_scope}/{clean_id}"


class InMemoryOpenViking:
    """Hermetic test double: authenticated namespaces, no network, no widening."""

    def __init__(self, authorized_namespaces: Iterable[str] | None = None) -> None:
        # ``None`` authorises every namespace (a convenience for a single
        # tenant); an explicit iterable restricts writes to those namespaces.
        self._authorized: set[str] | None = (
            None
            if authorized_namespaces is None
            else {str(item) for item in authorized_namespaces}
        )
        self._data: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self.writes: list[tuple[str, str, str]] = []

    def authorize(self, namespace: str) -> None:
        if self._authorized is None:
            self._authorized = set()
        self._authorized.add(str(namespace))

    def _require(self, namespace: str, collection: str) -> None:
        if self._authorized is not None and namespace not in self._authorized:
            raise BusinessError(
                MEMORY_OPENVIKING_FAILED_CODE,
                f"namespace {namespace!r} is not authenticated",
            )
        if not collection.startswith("office/"):
            raise BusinessError(
                MEMORY_OPENVIKING_FAILED_CODE,
                f"collection {collection!r} is outside the office namespace",
            )

    def write(
        self,
        namespace: str,
        collection: str,
        memory_id: str,
        content: str,
        metadata: Mapping[str, Any],
    ) -> None:
        self._require(namespace, collection)
        bucket = self._data.setdefault((namespace, collection), {})
        bucket[str(memory_id)] = {
            "content": content,
            "metadata": dict(metadata),
        }
        self.writes.append((namespace, collection, str(memory_id)))

    def delete(self, namespace: str, collection: str, memory_id: str) -> None:
        self._require(namespace, collection)
        self._data.get((namespace, collection), {}).pop(str(memory_id), None)

    def records(
        self, namespace: str, collection: str
    ) -> dict[str, dict[str, Any]]:
        return dict(self._data.get((namespace, collection), {}))

    def collections(self, namespace: str) -> list[str]:
        return sorted(
            collection
            for (ns, collection) in self._data
            if ns == namespace
        )

    def get(self, namespace: str, collection: str, memory_id: str) -> dict[str, Any] | None:
        return self._data.get((namespace, collection), {}).get(str(memory_id))

    @property
    def data(self) -> dict[tuple[str, str], dict[str, dict[str, Any]]]:
        return {
            key: {mid: dict(item) for mid, item in bucket.items()}
            for key, bucket in self._data.items()
        }


# -- the record ------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class MemoryRecord:
    memory_id: str
    tenant_id: str
    scope: str
    scope_id: str
    content: str
    source: str
    provenance: Mapping[str, Any]
    confidence: float
    visibility: str
    retention: str
    expires_at: str | None
    supersedes: str | None
    superseded_by: str | None
    labels: tuple[str, ...]
    quarantined: bool
    created_at: str
    tombstoned: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "tenant_id": self.tenant_id,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "content": self.content,
            "source": self.source,
            "provenance": dict(self.provenance),
            "confidence": self.confidence,
            "visibility": self.visibility,
            "retention": self.retention,
            "expires_at": self.expires_at,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "labels": list(self.labels),
            "quarantined": self.quarantined,
            "created_at": self.created_at,
        }

    def to_export(self) -> dict[str, Any]:
        body = self.to_payload()
        body["tombstoned"] = self.tombstoned
        return body

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, tenant_id: str) -> "MemoryRecord":
        return cls(
            memory_id=str(payload["memory_id"]),
            tenant_id=str(payload.get("tenant_id") or tenant_id),
            scope=str(payload["scope"]),
            scope_id=str(payload["scope_id"]),
            content=str(payload.get("content", "")),
            source=str(payload.get("source", "unknown")),
            provenance=dict(payload.get("provenance") or {}),
            confidence=float(payload.get("confidence", 1.0)),
            visibility=str(payload.get("visibility", DEFAULT_VISIBILITY)),
            retention=str(payload.get("retention", DEFAULT_RETENTION)),
            expires_at=payload.get("expires_at"),
            supersedes=payload.get("supersedes"),
            superseded_by=payload.get("superseded_by"),
            labels=tuple(payload.get("labels") or ()),
            quarantined=bool(payload.get("quarantined", False)),
            created_at=str(payload.get("created_at") or now_iso()),
            tombstoned=bool(payload.get("tombstoned", False)),
        )


# -- service ---------------------------------------------------------------
class MemoryService:
    """Event-log-backed memory with ACL-scoped recall and derived projections."""

    def __init__(
        self,
        store: Any,
        projection_dir: str | Path,
        acl: MemoryACL,
        openviking: OpenVikingAdapter | None = None,
    ) -> None:
        if acl is None:
            raise BusinessError(
                BAD_INPUT_CODE, "memory service requires an injected ACL"
            )
        self.store = store
        self.projection_dir = Path(projection_dir)
        self.acl = acl
        self.openviking = openviking

    # -- write ---------------------------------------------------------
    def remember(
        self,
        principal: Any,
        scope: str | None = None,
        scope_id: str | None = None,
        content: str | None = None,
        *,
        tenant_id: str | None = None,
        source: str = "agent",
        provenance: Mapping[str, Any] | None = None,
        confidence: float = 1.0,
        visibility: str = DEFAULT_VISIBILITY,
        retention: str = DEFAULT_RETENTION,
        expires_at: str | float | None = None,
        supersedes: str | None = None,
        labels: Iterable[str] | None = None,
        department: str | None = None,
        allow_untrusted: bool = False,
        policy_allows: bool = False,
        allow_protected: bool = False,
        memory_id: str | None = None,
        now: float | None = None,
    ) -> MemoryRecord:
        scope, scope_id = _scope_pair(scope, scope_id)
        tenant = tenant_id or _principal_tenant(principal)
        if not tenant:
            raise BusinessError(
                MEMORY_PRINCIPAL_INVALID_CODE,
                "principal has no tenant_id and no tenant_id was supplied",
            )
        principal_tenant = _principal_tenant(principal)
        if principal_tenant is not None and principal_tenant != tenant:
            raise BusinessError(
                MEMORY_WRITE_DENIED_CODE,
                f"principal tenant {principal_tenant!r} cannot write tenant"
                f" {tenant!r} memory",
            )
        self.store.require_tenant(tenant)
        principal = _effective_principal(principal, tenant)

        source_clean = _text(source, "source", max_len=80).lower()
        visibility_clean = _text(visibility, "visibility", max_len=20).lower()
        if visibility_clean not in VISIBILITIES:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"unknown visibility {visibility_clean!r}; expected one of"
                f" {', '.join(VISIBILITIES)}",
            )
        retention_clean = _text(retention, "retention", max_len=20).lower()
        if retention_clean not in RETENTION_CLASSES:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"unknown retention {retention_clean!r}; expected one of"
                f" {', '.join(RETENTION_CLASSES)}",
            )
        conf = _confidence(confidence)
        body = _bounded_content(content)
        labels_clean = list(_labels(labels))
        prov = _provenance(provenance)
        dept_clean = _optional_text(department, "department", max_len=200)
        if dept_clean is not None:
            prov = {**prov, "department": dept_clean}
        expires = _iso(expires_at)
        moment = _now(now)
        created = now_iso(moment)

        if supersedes is not None:
            target = self.get(supersedes, tenant_id=tenant)
            if target.scope != scope or target.scope_id != scope_id:
                raise BusinessError(
                    MEMORY_SCOPE_INVALID_CODE,
                    "a superseding record must stay in the same scope",
                )
            if _protected(target) and not allow_protected:
                raise BusinessError(
                    MEMORY_PROTECTED_CODE,
                    f"memory {supersedes!r} is a decision/policy record;"
                    " superseding it needs allow_protected=True",
                )

        explicit_untrusted = (
            allow_untrusted
            or policy_allows
            or "trusted" in labels_clean
            or "policy:allow-untrusted" in labels_clean
            or str(prov.get("policy") or "").lower()
            in ("allow-untrusted", "allow_untrusted")
        )
        quarantined = False
        reason: str | None = None
        if source_clean in UNTRUSTED_SOURCES:
            if "untrusted" not in labels_clean:
                labels_clean.append("untrusted")
            if not explicit_untrusted:
                if conf < MIN_TRUSTED_CONFIDENCE:
                    quarantined = True
                    reason = f"untrusted source {source_clean!r} below confidence floor"
                elif _instructions_like(body):
                    quarantined = True
                    reason = "instruction-like content from an untrusted source"
        if quarantined:
            if "quarantined" not in labels_clean:
                labels_clean.append("quarantined")
            prov = {**prov, "quarantine_reason": reason}

        record = MemoryRecord(
            memory_id=_text(memory_id or new_id("mem"), "memory_id", max_len=80),
            tenant_id=tenant,
            scope=scope,
            scope_id=scope_id,
            content=body,
            source=source_clean,
            provenance=prov,
            confidence=conf,
            visibility=visibility_clean,
            retention=retention_clean,
            expires_at=expires,
            supersedes=supersedes,
            superseded_by=None,
            labels=tuple(labels_clean),
            quarantined=quarantined,
            created_at=created,
        )

        if not self.acl.can_write(principal, scope, record):
            raise BusinessError(
                MEMORY_WRITE_DENIED_CODE,
                f"principal {_principal_id(principal)!r} cannot write"
                f" {scope}:{scope_id}",
            )

        aggregate = _aggregate(scope, scope_id)
        eventlog.append_event(
            self.store,
            tenant,
            aggregate,
            _EVENT_RECORDED,
            record.to_payload(),
            actor=_principal_id(principal),
            idempotency_key=f"mem:{tenant}:{record.memory_id}:recorded",
            retention_class=retention_clean,
            now=moment,
        )
        if not quarantined:
            self._mirror_write(record, department=department)
        return record

    # -- read ----------------------------------------------------------
    def recall(
        self,
        principal: Any,
        scope: str | tuple[str, str],
        query: str,
        limit: int = 10,
        *,
        scope_id: str | None = None,
        tenant_id: str | None = None,
        now: float | None = None,
        include_quarantined: bool = False,
    ) -> list[MemoryRecord]:
        scope, scope_id = self._resolve_scope(scope, scope_id)
        tenant = tenant_id or _principal_tenant(principal)
        if not tenant:
            raise BusinessError(
                MEMORY_PRINCIPAL_INVALID_CODE,
                "principal has no tenant_id and no tenant_id was supplied",
            )
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise BusinessError(BAD_INPUT_CODE, "limit must be an integer >= 0")
        moment = _now(now)
        principal = _effective_principal(principal, tenant)
        records = self._fold(tenant, scope, scope_id)
        scored: list[tuple[float, MemoryRecord]] = []
        for record in records.values():
            if record.tombstoned or record.superseded_by is not None:
                continue
            if record.quarantined and not include_quarantined:
                continue
            if record.tenant_id != tenant:
                continue
            expires = _epoch(record.expires_at)
            if expires is not None and expires <= moment:
                continue
            if not self.acl.can_read(principal, record):
                continue
            scored.append((_score(record, query, moment), record))
        scored.sort(key=lambda item: (-item[0], item[1].memory_id))
        return [record for _, record in scored[:limit]]

    def get(self, memory_id: str, *, tenant_id: str | None = None) -> MemoryRecord:
        located = self._locate(memory_id, tenant_id=tenant_id)
        if located is None:
            raise BusinessError(
                MEMORY_NOT_FOUND_CODE, f"no memory {memory_id!r}"
            )
        tenant, scope, scope_id = located
        record = self._fold(tenant, scope, scope_id).get(memory_id)
        if record is None:
            raise BusinessError(
                MEMORY_NOT_FOUND_CODE, f"no memory {memory_id!r}"
            )
        return record

    def export(
        self,
        scope: str | tuple[str, str],
        scope_id: str | None = None,
        *,
        tenant_id: str | None = None,
        principal: Any = None,
        include_quarantined: bool = True,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        scope, scope_id = self._resolve_scope(scope, scope_id)
        if tenant_id is None and principal is not None:
            tenant_id = _principal_tenant(principal)
        if not tenant_id:
            tenants = self.store.list_tenants()
            if len(tenants) == 1:
                tenant_id = tenants[0].tenant_id
        if not tenant_id:
            raise BusinessError(
                BAD_INPUT_CODE,
                "export needs tenant_id, a principal, or a single-tenant store",
            )
        moment = _now(now)
        records = self._fold(tenant_id, scope, scope_id)
        output: list[dict[str, Any]] = []
        for record in sorted(
            records.values(), key=lambda item: (item.created_at, item.memory_id)
        ):
            if record.tombstoned:
                continue
            expires = _epoch(record.expires_at)
            if expires is not None and expires <= moment:
                continue
            if record.quarantined and not include_quarantined:
                continue
            if principal is not None:
                effective = _effective_principal(principal, tenant_id)
                if not self.acl.can_read(effective, record):
                    continue
            output.append(record.to_export())
        return output

    # -- deletion / correction / quarantine ----------------------------
    def forget(
        self,
        memory_id: str,
        actor: str,
        *,
        reason: str | None = None,
        tenant_id: str | None = None,
        now: float | None = None,
    ) -> MemoryRecord:
        located = self._require_located(memory_id, tenant_id=tenant_id)
        tenant, scope, scope_id = located
        current = self._fold(tenant, scope, scope_id)[memory_id]
        if current.tombstoned:
            return current
        payload = {"memory_id": memory_id}
        if reason is not None:
            payload["reason"] = _text(reason, "reason", max_len=500)
        eventlog.append_event(
            self.store,
            tenant,
            _aggregate(scope, scope_id),
            _EVENT_FORGOTTEN,
            payload,
            actor=_text(actor, "actor", max_len=200),
            idempotency_key=f"mem:{tenant}:{memory_id}:forgotten",
            retention_class=current.retention,
            now=_now(now),
        )
        self._mirror_delete(current)
        return replace(current, tombstoned=True)

    def quarantine(
        self,
        memory_id: str,
        reason: str,
        *,
        actor: str = "system",
        tenant_id: str | None = None,
        now: float | None = None,
    ) -> MemoryRecord:
        tenant, scope, scope_id = self._require_located(
            memory_id, tenant_id=tenant_id
        )
        current = self._fold(tenant, scope, scope_id)[memory_id]
        eventlog.append_event(
            self.store,
            tenant,
            _aggregate(scope, scope_id),
            _EVENT_QUARANTINED,
            {
                "memory_id": memory_id,
                "reason": _text(reason, "reason", max_len=500),
            },
            actor=_text(actor, "actor", max_len=200),
            idempotency_key=f"mem:{tenant}:{memory_id}:quarantined",
            retention_class=current.retention,
            now=_now(now),
        )
        labels = tuple(
            label for label in current.labels if label != "promoted"
        )
        if "quarantined" not in labels:
            labels = labels + ("quarantined",)
        quarantined = replace(current, quarantined=True, labels=labels)
        self._mirror_delete(current)
        return quarantined

    def promote(
        self,
        memory_id: str,
        reason: str | None = None,
        *,
        actor: str = "system",
        tenant_id: str | None = None,
        now: float | None = None,
    ) -> MemoryRecord:
        tenant, scope, scope_id = self._require_located(
            memory_id, tenant_id=tenant_id
        )
        current = self._fold(tenant, scope, scope_id)[memory_id]
        payload: dict[str, Any] = {"memory_id": memory_id}
        if reason is not None:
            payload["reason"] = _text(reason, "reason", max_len=500)
        eventlog.append_event(
            self.store,
            tenant,
            _aggregate(scope, scope_id),
            _EVENT_PROMOTED,
            payload,
            actor=_text(actor, "actor", max_len=200),
            idempotency_key=f"mem:{tenant}:{memory_id}:promoted",
            retention_class=current.retention,
            now=_now(now),
        )
        labels = tuple(
            label for label in current.labels
            if label not in ("quarantined", "untrusted")
        )
        promoted = replace(current, quarantined=False, labels=labels)
        self._mirror_write(promoted)
        return promoted

    def correct(
        self,
        memory_id: str,
        actor: str | None = None,
        content: str | None = None,
        *,
        principal: Any = None,
        source: str | None = None,
        provenance: Mapping[str, Any] | None = None,
        confidence: float | None = None,
        labels: Iterable[str] | None = None,
        visibility: str | None = None,
        retention: str | None = None,
        expires_at: str | float | None = None,
        department: str | None = None,
        allow_protected: bool = False,
        allow_untrusted: bool = False,
        tenant_id: str | None = None,
        now: float | None = None,
    ) -> MemoryRecord:
        if content is None:
            raise BusinessError(
                BAD_INPUT_CODE, "correct needs replacement content"
            )
        target = self.get(memory_id, tenant_id=tenant_id)
        if _protected(target) and not allow_protected:
            raise BusinessError(
                MEMORY_PROTECTED_CODE,
                f"memory {memory_id!r} is a decision/policy record;"
                " superseding it needs allow_protected=True",
            )
        if principal is None:
            principal = MemoryPrincipal(
                principal_id=_text(actor or "system", "actor", max_len=200),
                tenant_id=target.tenant_id,
                kind="owner",
            )
        return self.remember(
            principal,
            scope=target.scope,
            scope_id=target.scope_id,
            content=content,
            tenant_id=target.tenant_id,
            source=target.source if source is None else source,
            provenance=target.provenance if provenance is None else provenance,
            confidence=target.confidence if confidence is None else confidence,
            visibility=target.visibility if visibility is None else visibility,
            retention=target.retention if retention is None else retention,
            expires_at=target.expires_at if expires_at is None else expires_at,
            supersedes=memory_id,
            labels=(
                labels
                if labels is not None
                else tuple(
                    label
                    for label in target.labels
                    if label != "quarantined"
                )
            ),
            department=department,
            allow_untrusted=allow_untrusted,
            allow_protected=allow_protected,
            now=now,
        )

    # -- projection -----------------------------------------------------
    def rebuild(self, tenant_id: str | None = None) -> int:
        """Delete and rebuild the Obsidian projection from events alone."""
        tenants = (
            [tenant_id]
            if tenant_id is not None
            else [tenant.tenant_id for tenant in self.store.list_tenants()]
        )
        records: list[MemoryRecord] = []
        for tenant in tenants:
            events = eventlog.list_events(self.store, tenant, limit=_SCAN_LIMIT)
            grouped: dict[str, list[eventlog.EventEnvelope]] = {}
            for event in events:
                if (
                    event.type in _MEMORY_EVENT_TYPES
                    and event.aggregate_id.startswith("memory:")
                ):
                    grouped.setdefault(event.aggregate_id, []).append(event)
            for aggregate, aggregate_events in grouped.items():
                parts = aggregate.split(":", 2)
                if len(parts) != 3:
                    continue
                # ``list_events`` orders by occurred_at; the authoritative
                # order inside an aggregate is its per-aggregate sequence.
                aggregate_events.sort(key=lambda item: item.sequence)
                records.extend(
                    self._fold_events(aggregate_events, tenant).values()
                )

        if self.projection_dir.exists():
            shutil.rmtree(self.projection_dir)
        self.projection_dir.mkdir(parents=True, exist_ok=True)

        by_scope: dict[str, list[MemoryRecord]] = {}
        for record in records:
            if record.tombstoned:
                continue
            by_scope.setdefault(record.scope, []).append(record)

        written = 0
        for scope, scope_records in by_scope.items():
            scope_records.sort(key=lambda item: (item.created_at, item.memory_id))
            live_ids = {record.memory_id for record in scope_records}
            for record in scope_records:
                path = self.projection_dir / record.scope / f"{record.memory_id}.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    self._render_note(record, live_ids), encoding="utf-8"
                )
                written += 1
            moc = self.projection_dir / scope / "_MOC.md"
            moc.write_text(self._render_moc(scope, scope_records), encoding="utf-8")
        return written

    # -- internals ------------------------------------------------------
    def _resolve_scope(
        self, scope: str | tuple[str, str], scope_id: str | None
    ) -> tuple[str, str]:
        if isinstance(scope, (tuple, list)):
            if len(scope) != 2:
                raise BusinessError(
                    MEMORY_SCOPE_INVALID_CODE, "scope tuple must be (scope, scope_id)"
                )
            return _scope_pair(scope[0], scope[1])
        if scope_id is None:
            if isinstance(scope, str) and ":" in scope:
                head, tail = scope.split(":", 1)
                return _scope_pair(head, tail)
            raise BusinessError(
                MEMORY_SCOPE_INVALID_CODE,
                "scope_id is required when scope is a string",
            )
        return _scope_pair(scope, scope_id)

    def _locate(
        self, memory_id: str, *, tenant_id: str | None = None
    ) -> tuple[str, str, str] | None:
        key = _text(memory_id, "memory_id", max_len=80)
        tenants = (
            [tenant_id]
            if tenant_id is not None
            else [tenant.tenant_id for tenant in self.store.list_tenants()]
        )
        for tenant in tenants:
            events = eventlog.list_events(self.store, tenant, limit=_SCAN_LIMIT)
            for event in events:
                if event.type != _EVENT_RECORDED:
                    continue
                payload = event.payload or {}
                if payload.get("memory_id") != key:
                    continue
                return (
                    tenant,
                    str(payload.get("scope")),
                    str(payload.get("scope_id")),
                )
        return None

    def _require_located(
        self, memory_id: str, *, tenant_id: str | None = None
    ) -> tuple[str, str, str]:
        located = self._locate(memory_id, tenant_id=tenant_id)
        if located is None:
            raise BusinessError(
                MEMORY_NOT_FOUND_CODE, f"no memory {memory_id!r}"
            )
        return located

    def _fold(
        self, tenant_id: str, scope: str, scope_id: str
    ) -> dict[str, MemoryRecord]:
        events = eventlog.read_events(
            self.store, tenant_id, _aggregate(scope, scope_id)
        )
        return self._fold_events(events, tenant_id)

    def _fold_events(
        self, events: Iterable[eventlog.EventEnvelope], tenant_id: str
    ) -> dict[str, MemoryRecord]:
        records: dict[str, MemoryRecord] = {}
        for event in events:
            payload = event.payload or {}
            identifier = payload.get("memory_id")
            if event.type == _EVENT_RECORDED:
                record = MemoryRecord.from_payload(payload, tenant_id=tenant_id)
                records[record.memory_id] = record
            elif identifier in records and event.type == _EVENT_FORGOTTEN:
                records[identifier] = replace(
                    records[identifier], tombstoned=True
                )
            elif identifier in records and event.type == _EVENT_QUARANTINED:
                labels = records[identifier].labels
                if "quarantined" not in labels:
                    labels = labels + ("quarantined",)
                records[identifier] = replace(
                    records[identifier], quarantined=True, labels=labels
                )
            elif identifier in records and event.type == _EVENT_PROMOTED:
                labels = tuple(
                    label
                    for label in records[identifier].labels
                    if label not in ("quarantined", "untrusted")
                )
                records[identifier] = replace(
                    records[identifier], quarantined=False, labels=labels
                )
        for record in list(records.values()):
            if (
                record.supersedes
                and record.supersedes in records
                and not record.tombstoned
                and not record.quarantined
            ):
                target = records[record.supersedes]
                records[record.supersedes] = replace(
                    target, superseded_by=record.memory_id
                )
        return records

    def _mirror_write(
        self, record: MemoryRecord, *, department: str | None = None
    ) -> None:
        if self.openviking is None:
            return
        dept = department or record.provenance.get("department")
        collection = viking_collection(
            record.scope, record.scope_id, department=dept
        )
        namespace = f"tenant/{record.tenant_id}"
        try:
            self.openviking.write(
                namespace,
                collection,
                record.memory_id,
                record.content,
                {
                    "scope": record.scope,
                    "scope_id": record.scope_id,
                    "source": record.source,
                    "provenance": dict(record.provenance),
                    "confidence": record.confidence,
                    "visibility": record.visibility,
                    "created_at": record.created_at,
                    "supersedes": record.supersedes,
                },
            )
        except BusinessError:
            raise
        except Exception as exc:  # pragma: no cover - adapter-specific
            raise BusinessError(
                MEMORY_OPENVIKING_FAILED_CODE,
                f"OpenViking write to {collection!r} failed: {exc}",
            ) from exc

    def _mirror_delete(self, record: MemoryRecord) -> None:
        if self.openviking is None:
            return
        collection = viking_collection(
            record.scope,
            record.scope_id,
            department=record.provenance.get("department"),
        )
        namespace = f"tenant/{record.tenant_id}"
        try:
            self.openviking.delete(namespace, collection, record.memory_id)
        except BusinessError:
            raise
        except Exception as exc:  # pragma: no cover - adapter-specific
            raise BusinessError(
                MEMORY_OPENVIKING_FAILED_CODE,
                f"OpenViking delete from {collection!r} failed: {exc}",
            ) from exc

    @staticmethod
    def _render_note(
        record: MemoryRecord, live_ids: set[str] | None = None
    ) -> str:
        supersedes = record.supersedes
        superseded_by = record.superseded_by
        if live_ids is not None:
            if supersedes not in live_ids:
                supersedes = None
            if superseded_by not in live_ids:
                superseded_by = None
        values = {
            "id": record.memory_id,
            "scope": record.scope,
            "scope_id": record.scope_id,
            "tenant": record.tenant_id,
            "source": record.source,
            "provenance": dict(record.provenance),
            "confidence": record.confidence,
            "visibility": record.visibility,
            "retention": record.retention,
            "expires": record.expires_at,
            "supersedes": supersedes,
            "superseded_by": superseded_by,
            "labels": list(record.labels),
            "quarantined": record.quarantined,
            "created": record.created_at,
        }
        lines = ["---"]
        for key in _FRONTMATTER_ORDER:
            lines.append(f"{key}: {json.dumps(values[key], sort_keys=True)}")
        lines.extend(["---", "", f"# Memory {record.memory_id}", ""])
        if record.quarantined:
            lines.extend(
                [
                    "> QUARANTINED: untrusted content; excluded from recall.",
                    "",
                ]
            )
        lines.extend([record.content, "", "## Links", "", "- [[_MOC]]"])
        if supersedes:
            lines.append(f"- supersedes [[{supersedes}]]")
        if superseded_by:
            lines.append(f"- superseded by [[{superseded_by}]]")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_moc(scope: str, records: list[MemoryRecord]) -> str:
        lines = [
            "---",
            f"id: {json.dumps('_MOC_' + scope)}",
            f"scope: {json.dumps(scope)}",
            "kind: \"moc\"",
            "---",
            "",
            f"# {scope} memory",
            "",
        ]
        for record in records:
            lines.append(
                f"- [[{record.memory_id}]] — {record.source} — {record.created_at}"
            )
        lines.append("")
        return "\n".join(lines)


__all__ = [
    "DEFAULT_RETENTION",
    "DEFAULT_VISIBILITY",
    "InMemoryOpenViking",
    "MAX_CONTENT_CHARS",
    "MEMORY_NOT_FOUND_CODE",
    "MEMORY_OPENVIKING_FAILED_CODE",
    "MEMORY_PRINCIPAL_INVALID_CODE",
    "MEMORY_PROTECTED_CODE",
    "MEMORY_READ_DENIED_CODE",
    "MEMORY_SCOPE_INVALID_CODE",
    "MEMORY_WRITE_DENIED_CODE",
    "MIN_TRUSTED_CONFIDENCE",
    "MemoryACL",
    "MemoryPrincipal",
    "MemoryRecord",
    "MemoryService",
    "OpenVikingAdapter",
    "RETENTION_CLASSES",
    "SCOPES",
    "SameTenantACL",
    "UNTRUSTED_SOURCES",
    "VISIBILITIES",
    "viking_collection",
]
