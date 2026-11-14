"""P4-05 — department and office MOCs are derived from authoritative memory.

Pins the plan contract (§8 P4-05): a department MOC covers rooms, agents,
tasks, projects, deliverables and decisions; the office MOC links every
department and shared special room; notes are stable and never duplicated as
prose.
"""
from __future__ import annotations

import json
from pathlib import Path

from voicebridge.business.memory_projection import (
    MemoryProjection,
    SPECIAL_ROOMS,
)


def _remember(service, principal, **overrides):
    values = {
        "scope": "department",
        "scope_id": "engineering",
        "content": "Engineering ships the release train on Fridays.",
        "source": "owner",
        "provenance": {"writer": "owner", "department": "engineering"},
        "visibility": "department",
    }
    values.update(overrides)
    return service.remember(principal, **values)


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    end = text.find("\n---\n", 4)
    assert end > 0, path
    data = {}
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = json.loads(value.strip())
    return data


def test_department_moc_has_every_category_and_stable_note_links(
    service, principal, tenant, tmp_path
):
    records = {
        "room": _remember(
            service, principal,
            content="The release room is on floor two.",
            labels=["room"],
            provenance={
                "writer": "owner", "department": "engineering",
                "room": "release-room",
            },
        ),
        "agent": _remember(
            service, principal,
            content="Seat seven owns the deploy checklist.",
            scope="agent", scope_id="seat-7",
            provenance={
                "writer": "seat-7", "department": "engineering",
                "agent": "seat-7",
            },
        ),
        "task": _remember(
            service, principal,
            content="Task T-14 prepares the release notes.",
            labels=["task"],
            provenance={
                "writer": "owner", "department": "engineering",
                "task": "T-14",
            },
        ),
        "project": _remember(
            service, principal,
            content="Project Atlas is the Q3 release.",
            labels=["project"],
            provenance={
                "writer": "owner", "department": "engineering",
                "project": "atlas",
            },
        ),
        "deliverable": _remember(
            service, principal,
            content="The signed build is the Friday deliverable.",
            labels=["deliverable"],
            provenance={
                "writer": "owner", "department": "engineering",
                "deliverable": "build-42",
                "artifact_ids": ["artifact-sha256-abc"],
            },
        ),
        "decision": _remember(
            service, principal,
            content="Decision: freeze at noon on Friday.",
            labels=["decision"],
            provenance={
                "writer": "owner", "department": "engineering",
                "decision": "freeze-noon",
            },
        ),
        "note": _remember(
            service, principal,
            content="A plain engineering note with no category.",
        ),
    }
    other = _remember(
        service, principal,
        scope="department", scope_id="finance",
        content="Finance closes the month on Tuesday.",
        provenance={"writer": "owner", "department": "finance"},
    )
    office = _remember(
        service, principal,
        scope="office", scope_id="office-1",
        content="Office hours start at 09:00.",
        source="owner",
        provenance={"writer": "owner"},
        visibility="office",
    )
    war_room = _remember(
        service, principal,
        content="War Room keeps the incident dissent.",
        scope="office", scope_id="office-1",
        provenance={
            "writer": "owner", "special_room": "war-room",
        },
        visibility="office",
    )

    projection = MemoryProjection(
        service,
        tmp_path / "projection",
        tenant_id=tenant.tenant_id,
        principal=principal,
        departments=["engineering", "finance"],
        special_rooms=[slug for slug, _ in SPECIAL_ROOMS],
    )
    written = projection.rebuild()
    assert written == 10

    root = projection.root
    dept_moc = root / "departments" / "engineering" / "_MOC.md"
    assert dept_moc.exists()
    text = dept_moc.read_text(encoding="utf-8")
    for heading in (
        "## Rooms", "## Agents", "## Tasks", "## Projects",
        "## Deliverables", "## Decisions", "## Notes",
    ):
        assert heading in text, heading
    for record in records.values():
        assert f"[[notes/{record.memory_id}" in text
    assert f"[[notes/{other.memory_id}" not in text
    assert f"[[notes/{office.memory_id}" not in text

    office_moc = (root / "_MOC.md").read_text(encoding="utf-8")
    assert "[[departments/engineering/_MOC|engineering]]" in office_moc
    assert "[[departments/finance/_MOC|finance]]" in office_moc
    assert "[[special/war-room/_MOC|War Room]]" in office_moc
    assert "[[special/memory-room/_MOC|Memory Room]]" in office_moc
    assert f"[[notes/{office.memory_id}" in office_moc
    assert f"[[notes/{records['room'].memory_id}" not in office_moc
    assert f"[[notes/{war_room.memory_id}" not in office_moc

    war_moc = (root / "special" / "war-room" / "_MOC.md").read_text(
        encoding="utf-8"
    )
    assert f"[[notes/{war_room.memory_id}" in war_moc

    note_text = (root / "notes" / f"{records['task'].memory_id}.md").read_text(
        encoding="utf-8"
    )
    assert f"# Memory {records['task'].memory_id}" in note_text
    assert "[[departments/engineering/_MOC|Department MOC]]" in note_text
    assert "[[_MOC|Office MOC]]" in note_text
    assert _frontmatter(root / "notes" / f"{records['task'].memory_id}.md")[
        "category"
    ] == "task"

    artifact_path = root / "artifacts" / "artifact-sha256-abc.md"
    assert artifact_path.exists()
    assert (
        f"[[notes/{records['deliverable'].memory_id}"
        in artifact_path.read_text(encoding="utf-8")
    )

    # No duplicated prose: each memory body appears exactly once in the vault.
    all_text = "\n".join(
        path.read_text(encoding="utf-8") for path in root.rglob("*.md")
    )
    for record in list(records.values()) + [other, office, war_room]:
        assert all_text.count(record.content) == 1, record.memory_id


def test_empty_declared_departments_and_special_rooms_still_have_mocs(
    service, principal, tenant, tmp_path
):
    _remember(service, principal)
    projection = MemoryProjection(
        service,
        tmp_path / "projection",
        tenant_id=tenant.tenant_id,
        principal=principal,
        departments=["engineering", "finance"],
        special_rooms=[slug for slug, _ in SPECIAL_ROOMS],
    )
    projection.rebuild()
    office_moc = (projection.root / "_MOC.md").read_text(encoding="utf-8")
    for name in ("engineering", "finance"):
        assert f"[[departments/{name}/_MOC|{name}]]" in office_moc
    for slug, title in SPECIAL_ROOMS:
        assert f"[[special/{slug}/_MOC|{title}]]" in office_moc
        assert (projection.root / "special" / slug / "_MOC.md").exists()
