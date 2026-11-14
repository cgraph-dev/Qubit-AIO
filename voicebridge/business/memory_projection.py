"""P4-05 — department and office Obsidian projections over authoritative memory.

The Business OS event log stays the only memory authority. This module reads
it through :class:`~voicebridge.business.memory.MemoryService` (fold + ACL) and
derives two human-facing graphs:

* **department MOCs** — rooms, agents, tasks, projects, deliverables, decisions
  and plain notes, each linking to one stable memory note;
* **the office MOC** — links every department MOC and every shared special room
  (War Room, Memory Room, Ops/Approvals, Comms, Server/Infra, Studio, Training,
  Lobby/Security) without copying their prose.

Notes are written once. MOCs only link to them, so the projection never
duplicates memory prose. An artifact index links memory notes to the artifact
ids recorded in their provenance, again with links only.

Every rebuild deletes the previous projection and rewrites it deterministically
from the events. A tombstoned, expired or unreadable record is not projected; a
superseded record is kept in a marked ``superseded`` section so it is never
mistaken for current truth. The injected ACL is consulted per record through
``MemoryService.export(principal=...)`` — a projection cannot expose a record
its viewer cannot read, and there is no second store or global memory pool.

OpenViking is an injected adapter. :class:`OpenVikingScopeGate` restricts an
adapter to ``office/global``, ``office/<department>`` and
``office/<department>/<agent>``; any other collection is a typed refusal
rather than a silent widening.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

from . import eventlog
from .errors import BAD_INPUT_CODE, BusinessError
from .memory import (
    MemoryService,
    OpenVikingAdapter,
)
from .util import redact, require_text

PROJECTION_SCHEMA = "qubit.office.memory_projection/1"

MEMORY_PROJECTION_SCOPE_DENIED_CODE = "BUSINESS_MEMORY_SCOPE_DENIED"
MEMORY_PROJECTION_INVALID_CODE = "BUSINESS_MEMORY_PROJECTION_INVALID"

#: The fixed OpenViking office scope. Department/agent scopes are built below.
OPENVIKING_GLOBAL_SCOPE = "office/global"

#: Shared service rooms that are not departments (docs/QUBIT-OFFICE-DEPARTMENTS-
#: AND-INTERACTIONS.md §1). The office MOC always links every one of them.
SPECIAL_ROOMS: tuple[tuple[str, str], ...] = (
    ("war-room", "War Room"),
    ("memory-room", "Memory Room"),
    ("ops-approvals", "Ops / Approvals"),
    ("comms", "Comms Hub"),
    ("server-infra", "Server / Infra"),
    ("studio", "Studio"),
    ("training", "Training / Auditorium"),
    ("lobby-security", "Lobby / Security"),
)
_SPECIAL_SLUGS = frozenset(slug for slug, _ in SPECIAL_ROOMS)
_SPECIAL_TITLES = dict(SPECIAL_ROOMS)

#: Department MOC sections, in stable order. ``note`` is the catch-all.
CATEGORY_SECTIONS: tuple[tuple[str, str], ...] = (
    ("room", "Rooms"),
    ("agent", "Agents"),
    ("task", "Tasks"),
    ("project", "Projects"),
    ("deliverable", "Deliverables"),
    ("decision", "Decisions"),
    ("note", "Notes"),
)
_CATEGORY_KEYS = frozenset(key for key, _ in CATEGORY_SECTIONS)

_SCAN_LIMIT = 100_000
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SECRET_SCAN = (
    re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]{6,}"),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(
        r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token"
        r"|refresh[_-]?token|client[_-]?secret)\b\s*[:=]\s*\S+"
    ),
)
PAID_ART_SCAN = (
    re.compile(r"(?i)data:image/"),
    re.compile(r"(?i)room_builder_floors"),
    re.compile(r"(?i)modern_interiors"),
    re.compile(r"(?i)assets/limezu"),
    re.compile(r"(?i)limezu\.itch\.io/assets"),
)

# -- small helpers ---------------------------------------------------------
def _text(value: Any, label: str, *, max_len: int = 400) -> str:
    try:
        return require_text(value, label, max_len=max_len)
    except ValueError as exc:
        raise BusinessError(BAD_INPUT_CODE, str(exc)) from exc


def _slug(value: str, *, fallback: str = "item") -> str:
    cleaned = _SLUG_RE.sub("-", str(value).strip().lower()).strip("-")
    cleaned = cleaned[:80].strip("-")
    return cleaned or fallback


def _unique_slugs(values: Iterable[str]) -> dict[str, str]:
    """Deterministic, collision-free directory names for stable record ids."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for value in sorted({str(item) for item in values}):
        base = _slug(value)
        slug = base
        if slug in used:
            slug = f"{base[:70]}-{hashlib.sha1(value.encode()).hexdigest()[:8]}"
        used.add(slug)
        mapping[value] = slug
    return mapping


def _artifact_slug(artifact_id: str) -> str:
    return _slug(artifact_id, fallback="artifact")


def _frontmatter(values: Mapping[str, Any], order: Iterable[str]) -> list[str]:
    lines = ["---"]
    for key in order:
        lines.append(f"{key}: {json.dumps(values.get(key), sort_keys=True)}")
    lines.append("---")
    return lines


def department_scope(department: str) -> str:
    """The fixed OpenViking collection for one department."""
    return f"office/{_text(department, 'department', max_len=200)}"


def agent_scope(department: str, agent: str) -> str:
    """The fixed OpenViking collection for one agent inside a department."""
    clean_agent = _text(agent, "agent", max_len=200)
    if "/" in clean_agent:
        raise BusinessError(
            MEMORY_PROJECTION_SCOPE_DENIED_CODE,
            "agent scope must be a bare agent id, not a path",
        )
    return f"office/{_text(department, 'department', max_len=200)}/{clean_agent}"


def scope_collections(
    *, department: str | None = None, agent: str | None = None,
    allow_global: bool = False,
) -> tuple[str, ...]:
    """The exact collections a scoped viewer may touch, in stable order."""
    values: list[str] = []
    if allow_global:
        values.append(OPENVIKING_GLOBAL_SCOPE)
    if department is not None:
        values.append(department_scope(department))
        if agent is not None:
            values.append(agent_scope(department, agent))
    elif agent is not None:
        raise BusinessError(
            MEMORY_PROJECTION_SCOPE_DENIED_CODE,
            "an agent scope requires its department",
        )
    return tuple(values)


# -- scoped OpenViking adapter --------------------------------------------
@runtime_checkable
class ReadableOpenVikingAdapter(OpenVikingAdapter, Protocol):
    """An adapter that can also be read back for scoped retrieval."""

    def records(
        self, namespace: str, collection: str
    ) -> dict[str, dict[str, Any]]: ...

    def get(
        self, namespace: str, collection: str, memory_id: str
    ) -> dict[str, Any] | None: ...


class OpenVikingScopeGate:
    """A deny-by-default view over an injected OpenViking adapter.

    It satisfies :class:`OpenVikingAdapter` so it can be passed straight to
    ``MemoryService``; every ``write``/``delete``/``records``/``get`` names a
    collection that must be one of :func:`scope_collections`. A read or write
    outside that set is a typed refusal — it never falls back to
    ``office/global`` or another department.
    """

    def __init__(
        self,
        adapter: OpenVikingAdapter,
        *,
        department: str | None = None,
        agent: str | None = None,
        allow_global: bool = False,
    ) -> None:
        self.adapter = adapter
        self._allowed = scope_collections(
            department=department, agent=agent, allow_global=allow_global
        )

    @property
    def allowed_collections(self) -> tuple[str, ...]:
        return self._allowed

    def allows(self, collection: str) -> bool:
        return str(collection) in self._allowed

    def _require(self, collection: str, action: str) -> str:
        clean = _text(collection, "collection", max_len=300)
        if clean not in self._allowed:
            raise BusinessError(
                MEMORY_PROJECTION_SCOPE_DENIED_CODE,
                f"{action} denied for {clean!r}; this view is limited to"
                f" {', '.join(self._allowed) or 'no collections'}",
            )
        return clean

    def write(
        self,
        namespace: str,
        collection: str,
        memory_id: str,
        content: str,
        metadata: Mapping[str, Any],
    ) -> None:
        self.adapter.write(
            namespace,
            self._require(collection, "write"),
            memory_id,
            content,
            metadata,
        )

    def delete(self, namespace: str, collection: str, memory_id: str) -> None:
        self.adapter.delete(
            namespace, self._require(collection, "delete"), memory_id
        )

    def _readable(self) -> ReadableOpenVikingAdapter:
        if not isinstance(self.adapter, ReadableOpenVikingAdapter):
            raise BusinessError(
                MEMORY_PROJECTION_INVALID_CODE,
                "the injected OpenViking adapter does not support scoped reads",
            )
        return self.adapter

    def records(
        self, namespace: str, collection: str
    ) -> dict[str, dict[str, Any]]:
        return self._readable().records(
            namespace, self._require(collection, "read")
        )

    def get(
        self, namespace: str, collection: str, memory_id: str
    ) -> dict[str, Any] | None:
        return self._readable().get(
            namespace, self._require(collection, "read"), memory_id
        )


# -- normalized projection record -----------------------------------------
@dataclass(frozen=True, slots=True)
class ProjectionRecord:
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
    created_at: str
    department: str | None
    special_room: str | None
    category: str
    entity_ids: Mapping[str, str] = field(default_factory=dict)
    artifact_ids: tuple[str, ...] = ()

    def to_export_row(self) -> dict[str, Any]:
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
            "created_at": self.created_at,
            "department": self.department,
            "special_room": self.special_room,
            "category": self.category,
            "entity_ids": dict(self.entity_ids),
            "artifacts": list(self.artifact_ids),
            "path": f"notes/{self.memory_id}.md",
        }

    def to_row(self, path: str) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "path": path,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "visibility": self.visibility,
            "department": self.department,
            "special_room": self.special_room,
            "category": self.category,
            "artifacts": list(self.artifact_ids),
            "superseded_by": self.superseded_by,
            "readable": True,
        }


def _department_for(
    scope: str, scope_id: str, provenance: Mapping[str, Any]
) -> str | None:
    raw = provenance.get("department")
    if raw:
        return str(raw)
    if scope == "department":
        return scope_id
    if scope == "agent" and "/" in scope_id:
        return scope_id.split("/", 1)[0]
    return None


def _special_for(provenance: Mapping[str, Any]) -> str | None:
    raw = provenance.get("special_room")
    if raw and str(raw) in _SPECIAL_SLUGS:
        return str(raw)
    room = provenance.get("room")
    if room and str(room) in _SPECIAL_SLUGS:
        return str(room)
    return None


def _category_for(
    labels: Iterable[str], provenance: Mapping[str, Any]
) -> str:
    clean_labels = {str(label).lower() for label in labels}
    for key, _ in CATEGORY_SECTIONS:
        if key != "note" and key in clean_labels:
            return key
    for key, _ in CATEGORY_SECTIONS:
        if key != "note" and provenance.get(key):
            return key
    return "note"


def _entity_ids(provenance: Mapping[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, _ in CATEGORY_SECTIONS:
        if key == "note":
            continue
        for name in (key, f"{key}_id"):
            value = provenance.get(name)
            if value not in (None, ""):
                found[key] = str(value)
                break
    return found


def _artifact_ids(provenance: Mapping[str, Any]) -> tuple[str, ...]:
    raw = provenance.get("artifact_ids", provenance.get("artifacts"))
    values: list[str] = []
    if isinstance(raw, str) and raw.strip():
        values.append(raw.strip())
    elif isinstance(raw, (list, tuple, set)):
        values.extend(str(item) for item in raw if str(item).strip())
    single = provenance.get("artifact_id")
    if single not in (None, ""):
        values.append(str(single))
    return tuple(sorted({value for value in values if value}))


def _note_link(memory_id: str, label: str | None = None) -> str:
    return f"[[notes/{memory_id}|{label or memory_id}]]"


class MemoryProjection:
    """Derive the department/office Obsidian graph from authoritative memory.

    ``service`` is the only source. ``tenant_id`` binds the projection to one
    tenant (a shared root across tenants would be a leak); ``principal`` is the
    viewer whose ACL is consulted for every record.
    """

    def __init__(
        self,
        service: MemoryService,
        root: str | Path,
        *,
        tenant_id: str | None = None,
        principal: Any = None,
        departments: Iterable[str] = (),
        special_rooms: Iterable[str] | None = None,
    ) -> None:
        if not isinstance(service, MemoryService):
            raise BusinessError(
                BAD_INPUT_CODE, "memory projection requires a MemoryService"
            )
        self.service = service
        self.root = Path(root)
        self.tenant_id = self._resolve_tenant(tenant_id)
        self.principal = principal
        self.departments = tuple(
            sorted({_text(item, "department", max_len=200) for item in departments})
        )
        rooms = SPECIAL_ROOMS if special_rooms is None else tuple(
            (slug, _SPECIAL_TITLES.get(slug, slug))
            for slug in special_rooms
        )
        unknown = [slug for slug, _ in rooms if slug not in _SPECIAL_SLUGS]
        if unknown:
            raise BusinessError(
                BAD_INPUT_CODE,
                f"unknown special room(s): {', '.join(sorted(unknown))}",
            )
        self.special_rooms = tuple(rooms)

    # -- public surface -------------------------------------------------
    def rebuild(self) -> int:
        """Delete and deterministically rebuild the whole projection."""
        records = self.collect()
        dept_names = sorted(
            {record.department for record in records if record.department}
            | set(self.departments)
        )
        special_names = {slug for slug, _ in self.special_rooms}
        special_names |= {
            record.special_room for record in records if record.special_room
        }
        dept_slugs = _unique_slugs(dept_names)
        special_slugs = _unique_slugs(special_names)
        artifacts = sorted(
            {artifact for record in records for artifact in record.artifact_ids}
        )
        artifact_slugs = _unique_slugs(artifacts)

        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)

        note_rows: list[dict[str, Any]] = []
        notes_dir = self.root / "notes"
        notes_dir.mkdir()
        for record in records:
            path = notes_dir / f"{record.memory_id}.md"
            path.write_text(
                self._render_note(record, dept_slugs, special_slugs,
                                  artifact_slugs),
                encoding="utf-8",
            )
            note_rows.append(record.to_row(str(path.relative_to(self.root))))

        by_department: dict[str, list[ProjectionRecord]] = {}
        by_special: dict[str, list[ProjectionRecord]] = {}
        office_records: list[ProjectionRecord] = []
        for record in records:
            if record.department:
                by_department.setdefault(record.department, []).append(record)
            if record.special_room:
                by_special.setdefault(record.special_room, []).append(record)
            if (
                not record.department
                and not record.special_room
                and record.scope in ("office", "tenant")
            ):
                office_records.append(record)

        department_rows: list[dict[str, Any]] = []
        for name in dept_names:
            slug = dept_slugs[name]
            directory = self.root / "departments" / slug
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "_MOC.md"
            path.write_text(
                self._render_department_moc(
                    name, slug, by_department.get(name, []), dept_slugs,
                    special_slugs, artifact_slugs,
                ),
                encoding="utf-8",
            )
            department_rows.append(
                {
                    "id": name,
                    "slug": slug,
                    "moc": str(path.relative_to(self.root)),
                }
            )

        special_rows: list[dict[str, Any]] = []
        for slug in sorted(special_names):
            slug_dir = special_slugs[slug]
            directory = self.root / "special" / slug_dir
            directory.mkdir(parents=True, exist_ok=True)
            title = _SPECIAL_TITLES.get(slug, slug)
            path = directory / "_MOC.md"
            path.write_text(
                self._render_special_moc(
                    slug, title, slug_dir, by_special.get(slug, []),
                    dept_slugs, special_slugs, artifact_slugs,
                ),
                encoding="utf-8",
            )
            special_rows.append(
                {
                    "id": slug,
                    "title": title,
                    "slug": slug_dir,
                    "moc": str(path.relative_to(self.root)),
                }
            )

        artifact_rows: list[dict[str, Any]] = []
        if artifacts:
            artifacts_dir = self.root / "artifacts"
            artifacts_dir.mkdir()
            for artifact in artifacts:
                slug = artifact_slugs[artifact]
                path = artifacts_dir / f"{slug}.md"
                backlinks = [
                    record for record in records
                    if artifact in record.artifact_ids
                ]
                path.write_text(
                    self._render_artifact(artifact, slug, backlinks),
                    encoding="utf-8",
                )
                artifact_rows.append(
                    {
                        "id": artifact,
                        "slug": slug,
                        "path": str(path.relative_to(self.root)),
                    }
                )

        (self.root / "_MOC.md").write_text(
            self._render_office_moc(
                department_rows, special_rows, office_records, dept_slugs,
                special_slugs,
            ),
            encoding="utf-8",
        )

        digest = self._build_digest()
        manifest = {
            "schema": PROJECTION_SCHEMA,
            "tenant_id": self.tenant_id,
            "viewer": self._viewer_id(),
            "acl_mode": "injected",
            "departments": department_rows,
            "special_rooms": special_rows,
            "artifacts": artifact_rows,
            "notes": sorted(note_rows, key=lambda row: row["memory_id"]),
            "counts": {
                "notes": len(note_rows),
                "departments": len(department_rows),
                "special_rooms": len(special_rows),
                "artifacts": len(artifact_rows),
            },
            "build_digest": digest,
        }
        (self.root / "_projection.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return len(note_rows)

    def collect(self) -> list[ProjectionRecord]:
        """Read only this viewer's live records from the event log."""
        events = eventlog.list_events(
            self.service.store, self.tenant_id, limit=_SCAN_LIMIT
        )
        aggregates = sorted(
            {
                event.aggregate_id
                for event in events
                if event.type.startswith("memory.")
                and event.aggregate_id.startswith("memory:")
            }
        )
        records: dict[str, ProjectionRecord] = {}
        for aggregate in aggregates:
            parts = aggregate.split(":", 2)
            if len(parts) != 3:
                continue
            scope, scope_id = parts[1], parts[2]
            exported = self.service.export(
                scope,
                scope_id,
                tenant_id=self.tenant_id,
                principal=self.principal,
                include_quarantined=False,
            )
            for payload in exported:
                record = self._normalize(payload)
                records[record.memory_id] = record
        return sorted(
            records.values(),
            key=lambda record: (record.created_at, record.memory_id),
        )

    def export(self) -> list[dict[str, Any]]:
        """The authoritative, viewer-filtered records this projection derives."""
        return [record.to_export_row() for record in self.collect()]

    def remove(self) -> None:
        """Delete the projection. The event log and memory records are untouched."""
        if self.root.exists():
            shutil.rmtree(self.root)

    # -- internals ------------------------------------------------------
    def _resolve_tenant(self, tenant_id: str | None) -> str:
        if tenant_id is not None:
            clean = _text(tenant_id, "tenant_id", max_len=80)
            self.service.store.require_tenant(clean)
            return clean
        tenants = [tenant.tenant_id for tenant in self.service.store.list_tenants()]
        if len(tenants) != 1:
            raise BusinessError(
                BAD_INPUT_CODE,
                "memory projection needs tenant_id when the store has"
                f" {len(tenants)} tenants",
            )
        return tenants[0]

    def _viewer_id(self) -> str | None:
        if self.principal is None:
            return None
        if isinstance(self.principal, str):
            return self.principal
        for name in ("principal_id", "subject_id", "id"):
            value = getattr(self.principal, name, None)
            if value is not None:
                return str(value)
        if isinstance(self.principal, Mapping):
            for name in ("principal_id", "subject_id", "id"):
                if name in self.principal:
                    return str(self.principal[name])
        return None

    @staticmethod
    def _normalize(payload: Mapping[str, Any]) -> ProjectionRecord:
        labels = tuple(str(label) for label in payload.get("labels") or ())
        provenance = dict(payload.get("provenance") or {})
        safe_provenance = redact(provenance)
        scope = str(payload["scope"])
        scope_id = str(payload["scope_id"])
        department = _department_for(scope, scope_id, provenance)
        return ProjectionRecord(
            memory_id=str(payload["memory_id"]),
            tenant_id=str(payload["tenant_id"]),
            scope=scope,
            scope_id=scope_id,
            content=str(redact(str(payload.get("content", "")))),
            source=str(payload.get("source", "unknown")),
            provenance=safe_provenance,
            confidence=float(payload.get("confidence", 0.0)),
            visibility=str(payload.get("visibility", "internal")),
            retention=str(payload.get("retention", "standard")),
            expires_at=payload.get("expires_at"),
            supersedes=payload.get("supersedes"),
            superseded_by=payload.get("superseded_by"),
            labels=labels,
            created_at=str(payload.get("created_at") or ""),
            department=department,
            special_room=_special_for(provenance),
            category=_category_for(labels, provenance),
            entity_ids=_entity_ids(provenance),
            artifact_ids=_artifact_ids(provenance),
        )

    def _build_digest(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(self.root.rglob("*.md")):
            relative = path.relative_to(self.root).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def _render_note(
        self,
        record: ProjectionRecord,
        dept_slugs: Mapping[str, str],
        special_slugs: Mapping[str, str],
        artifact_slugs: Mapping[str, str],
    ) -> str:
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
            "supersedes": record.supersedes,
            "superseded_by": record.superseded_by,
            "labels": list(record.labels),
            "created": record.created_at,
            "department": record.department,
            "special_room": record.special_room,
            "category": record.category,
            "artifacts": list(record.artifact_ids),
        }
        order = (
            "id", "scope", "scope_id", "tenant", "source", "provenance",
            "confidence", "visibility", "retention", "expires", "supersedes",
            "superseded_by", "labels", "created", "department",
            "special_room", "category", "artifacts",
        )
        lines = _frontmatter(values, order)
        lines.extend(["", f"# Memory {record.memory_id}", ""])
        if record.superseded_by:
            target = _note_link(record.superseded_by)
            lines.extend(
                [f"> SUPERSEDED by {target}; not current truth.", ""]
            )
        lines.extend([record.content, "", "## Backlinks", ""])
        lines.append("- [[_MOC|Office MOC]]")
        if record.department:
            slug = dept_slugs.get(record.department)
            if slug:
                lines.append(
                    f"- [[departments/{slug}/_MOC|Department MOC]]"
                )
        if record.special_room:
            slug = special_slugs.get(record.special_room)
            if slug:
                lines.append(f"- [[special/{slug}/_MOC|Special Room MOC]]")
        for artifact in record.artifact_ids:
            slug = artifact_slugs.get(artifact)
            if slug:
                lines.append(f"- [[artifacts/{slug}|Artifact {artifact}]]")
        if record.supersedes:
            lines.append(
                f"- supersedes {_note_link(record.supersedes)}"
            )
        if record.superseded_by:
            lines.append(
                f"- superseded by {_note_link(record.superseded_by)}"
            )
        lines.append("")
        return "\n".join(lines)

    def _render_department_moc(
        self,
        name: str,
        slug: str,
        records: list[ProjectionRecord],
        dept_slugs: Mapping[str, str],
        special_slugs: Mapping[str, str],
        artifact_slugs: Mapping[str, str],
    ) -> str:
        values = {
            "id": f"dept_{slug}_MOC",
            "kind": "moc",
            "scope": "department",
            "department": name,
            "tenant": self.tenant_id,
        }
        lines = _frontmatter(
            values, ("id", "kind", "scope", "department", "tenant")
        )
        lines.extend(["", f"# {name} — memory map", ""])
        lines.extend(["[[_MOC|Office MOC]]", ""])
        current = [item for item in records if not item.superseded_by]
        superseded = [item for item in records if item.superseded_by]
        lines.extend(
            self._render_sections(
                current, dept_slugs, special_slugs, artifact_slugs
            )
        )
        if superseded:
            lines.extend(["## Superseded", ""])
            for record in superseded:
                lines.append(
                    f"- {_note_link(record.memory_id)} — superseded by"
                    f" {_note_link(record.superseded_by)}"
                )
            lines.append("")
        lines.append("")
        return "\n".join(lines)

    def _render_special_moc(
        self,
        slug: str,
        title: str,
        slug_dir: str,
        records: list[ProjectionRecord],
        dept_slugs: Mapping[str, str],
        special_slugs: Mapping[str, str],
        artifact_slugs: Mapping[str, str],
    ) -> str:
        values = {
            "id": f"special_{slug_dir}_MOC",
            "kind": "moc",
            "scope": "special-room",
            "special_room": slug,
            "tenant": self.tenant_id,
        }
        lines = _frontmatter(
            values, ("id", "kind", "scope", "special_room", "tenant")
        )
        lines.extend(["", f"# {title} — memory map", ""])
        lines.extend(["[[_MOC|Office MOC]]", ""])
        current = [item for item in records if not item.superseded_by]
        superseded = [item for item in records if item.superseded_by]
        lines.extend(
            self._render_sections(
                current, dept_slugs, special_slugs, artifact_slugs
            )
        )
        if superseded:
            lines.extend(["## Superseded", ""])
            for record in superseded:
                lines.append(
                    f"- {_note_link(record.memory_id)} — superseded by"
                    f" {_note_link(record.superseded_by)}"
                )
            lines.append("")
        lines.append("")
        return "\n".join(lines)

    def _render_sections(
        self,
        records: list[ProjectionRecord],
        dept_slugs: Mapping[str, str],
        special_slugs: Mapping[str, str],
        artifact_slugs: Mapping[str, str],
    ) -> list[str]:
        lines: list[str] = []
        for key, title in CATEGORY_SECTIONS:
            subset = [record for record in records if record.category == key]
            if not subset:
                continue
            lines.extend([f"## {title}", ""])
            for record in subset:
                lines.append(self._moc_row(record, artifact_slugs))
            lines.append("")
        return lines

    @staticmethod
    def _moc_row(
        record: ProjectionRecord, artifact_slugs: Mapping[str, str]
    ) -> str:
        details = [
            f"{key} `{value}`"
            for key, value in sorted(record.entity_ids.items())
            if value
        ]
        if record.artifact_ids:
            links = [
                f"[[artifacts/{artifact_slugs[artifact]}|{artifact}]]"
                for artifact in record.artifact_ids
                if artifact in artifact_slugs
            ]
            if links:
                details.append("artifacts " + ", ".join(links))
        suffix = f" — {'; '.join(details)}" if details else ""
        return f"- {_note_link(record.memory_id)}{suffix}"

    def _render_office_moc(
        self,
        departments: list[dict[str, Any]],
        special_rooms: list[dict[str, Any]],
        office_records: list[ProjectionRecord],
        dept_slugs: Mapping[str, str],
        special_slugs: Mapping[str, str],
    ) -> str:
        values = {
            "id": "office_MOC",
            "kind": "moc",
            "scope": "office",
            "tenant": self.tenant_id,
        }
        lines = _frontmatter(
            values, ("id", "kind", "scope", "tenant")
        )
        lines.extend(["", "# Qubit Office — memory map", ""])
        lines.extend(["## Departments", ""])
        if departments:
            for row in departments:
                lines.append(
                    f"- [[departments/{row['slug']}/_MOC|{row['id']}]]"
                )
        else:
            lines.append("- (no departments projected)")
        lines.extend(["", "## Shared service rooms", ""])
        for row in special_rooms:
            lines.append(
                f"- [[special/{row['slug']}/_MOC|{row['title']}]]"
            )
        lines.extend(["", "## Office memory", ""])
        current = [
            record for record in office_records
            if not record.superseded_by and record.visibility != "private"
        ]
        if current:
            for record in current:
                lines.append(self._moc_row(record, {}))
        else:
            lines.append("- (no office-scope memories)")
        superseded = [
            record for record in office_records
            if record.superseded_by and record.visibility != "private"
        ]
        if superseded:
            lines.extend(["", "## Superseded", ""])
            for record in superseded:
                lines.append(
                    f"- {_note_link(record.memory_id)} — superseded by"
                    f" {_note_link(record.superseded_by)}"
                )
        lines.append("")
        return "\n".join(lines)

    def _render_artifact(
        self, artifact: str, slug: str, records: list[ProjectionRecord]
    ) -> str:
        values = {
            "id": f"artifact_{slug}",
            "kind": "artifact",
            "artifact_id": artifact,
            "tenant": self.tenant_id,
        }
        lines = _frontmatter(
            values, ("id", "kind", "artifact_id", "tenant")
        )
        lines.extend(["", f"# Artifact {artifact}", "", "## Backlinks", ""])
        for record in records:
            lines.append(f"- {_note_link(record.memory_id)}")
        lines.append("")
        return "\n".join(lines)


def scan_text(text: str) -> list[str]:
    """Return stable names of secret/paid-art patterns found in projection text."""
    findings: list[str] = []
    if any(pattern.search(text) for pattern in _SECRET_SCAN):
        findings.append("secret-shaped-text")
    if any(pattern.search(text) for pattern in PAID_ART_SCAN):
        findings.append("paid-art-reference")
    return findings


__all__ = [
    "CATEGORY_SECTIONS",
    "MEMORY_PROJECTION_INVALID_CODE",
    "MEMORY_PROJECTION_SCOPE_DENIED_CODE",
    "MemoryProjection",
    "OPENVIKING_GLOBAL_SCOPE",
    "OpenVikingScopeGate",
    "PROJECTION_SCHEMA",
    "ProjectionRecord",
    "ReadableOpenVikingAdapter",
    "SPECIAL_ROOMS",
    "agent_scope",
    "department_scope",
    "scan_text",
    "scope_collections",
]
