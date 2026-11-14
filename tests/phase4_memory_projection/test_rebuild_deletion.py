"""P4-05 — deterministic rebuild, deletion, supersession and export.

The projection is disposable: ``rebuild`` deletes and rewrites it from events,
``remove`` deletes it, and a deleted/expired/superseded memory is never left
looking like current truth.
"""
from __future__ import annotations

import json
from pathlib import Path

from voicebridge.business.memory_projection import MemoryProjection


def _remember(service, principal, **overrides):
    values = {
        "scope": "department",
        "scope_id": "engineering",
        "content": "The release train leaves on Friday.",
        "source": "owner",
        "provenance": {"writer": "owner", "department": "engineering"},
        "visibility": "department",
    }
    values.update(overrides)
    return service.remember(principal, **values)


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_rebuild_is_byte_deterministic(service, principal, tenant, tmp_path):
    _remember(service, principal, content="Alpha fact.")
    _remember(
        service, principal, content="Beta fact.", labels=["task"],
        provenance={
            "writer": "owner", "department": "engineering", "task": "T-1",
        },
    )
    projection = MemoryProjection(
        service,
        tmp_path / "projection",
        tenant_id=tenant.tenant_id,
        principal=principal,
        departments=["engineering"],
    )
    projection.rebuild()
    first = _snapshot(projection.root)
    projection.rebuild()
    assert _snapshot(projection.root) == first


def test_forget_removes_the_note_and_export_reflects_it(
    service, principal, tenant, tmp_path
):
    keep = _remember(service, principal, content="Keep this.")
    drop = _remember(service, principal, content="Delete this.")
    projection = MemoryProjection(
        service,
        tmp_path / "projection",
        tenant_id=tenant.tenant_id,
        principal=principal,
        departments=["engineering"],
    )
    projection.rebuild()
    root = projection.root
    assert (root / "notes" / f"{drop.memory_id}.md").exists()

    service.forget(drop.memory_id, "owner", reason="no longer true")
    projection.rebuild()
    assert not (root / "notes" / f"{drop.memory_id}.md").exists()
    assert (root / "notes" / f"{keep.memory_id}.md").exists()
    exported = {row["memory_id"] for row in projection.export()}
    assert drop.memory_id not in exported
    assert keep.memory_id in exported
    # The event log still resolves the tombstone; projection is not authority.
    assert service.get(drop.memory_id).tombstoned is True


def test_superseded_note_is_marked_and_moved_out_of_current_sections(
    service, principal, tenant, tmp_path
):
    original = _remember(service, principal, content="The budget is 10k.")
    replacement = service.correct(
        original.memory_id, "owner", content="The budget is 12k.",
        provenance={
            "writer": "owner", "department": "engineering",
        },
    )
    projection = MemoryProjection(
        service,
        tmp_path / "projection",
        tenant_id=tenant.tenant_id,
        principal=principal,
        departments=["engineering"],
    )
    projection.rebuild()
    root = projection.root
    old_note = (root / "notes" / f"{original.memory_id}.md").read_text(
        encoding="utf-8"
    )
    assert "SUPERSEDED" in old_note
    assert f"[[notes/{replacement.memory_id}" in old_note

    dept_moc = (root / "departments" / "engineering" / "_MOC.md").read_text(
        encoding="utf-8"
    )
    assert "## Superseded" in dept_moc
    superseded_section = dept_moc.split("## Superseded", 1)[1]
    assert original.memory_id in superseded_section
    current_section = dept_moc.split("## Superseded", 1)[0]
    assert original.memory_id not in current_section
    assert replacement.memory_id in dept_moc

    manifest = json.loads(
        (root / "_projection.json").read_text(encoding="utf-8")
    )
    rows = {row["memory_id"]: row for row in manifest["notes"]}
    assert rows[original.memory_id]["superseded_by"] == replacement.memory_id


def test_expired_record_is_not_projected(service, principal, tenant, tmp_path):
    expired = service.remember(
        principal,
        scope="department",
        scope_id="engineering",
        content="This fact expired yesterday.",
        source="owner",
        provenance={"writer": "owner", "department": "engineering"},
        expires_at=1.0,
        now=1000.0,
    )
    projection = MemoryProjection(
        service,
        tmp_path / "projection",
        tenant_id=tenant.tenant_id,
        principal=principal,
        departments=["engineering"],
    )
    projection.rebuild()
    assert not (
        projection.root / "notes" / f"{expired.memory_id}.md"
    ).exists()


def test_remove_deletes_the_projection_not_the_events(
    service, principal, tenant, tmp_path
):
    record = _remember(service, principal)
    projection = MemoryProjection(
        service,
        tmp_path / "projection",
        tenant_id=tenant.tenant_id,
        principal=principal,
        departments=["engineering"],
    )
    projection.rebuild()
    assert projection.root.exists()
    projection.remove()
    assert not projection.root.exists()
    assert service.get(record.memory_id).content == record.content
