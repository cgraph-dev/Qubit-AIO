"""P4-05 — ACL-preserving projection and artifact backlinks.

A projection is built for one viewer. The injected ACL is consulted per record
through ``MemoryService.export``; another department's private record never
reaches the note set, the MOCs or the artifact graph.
"""
from __future__ import annotations

from voicebridge.business.memory import MemoryService
from voicebridge.business.memory_projection import MemoryProjection


def _remember(service, principal, **overrides):
    values = {
        "scope": "department",
        "scope_id": "dept-a",
        "content": "Dept A owns the supplier contract.",
        "source": "owner",
        "provenance": {"writer": "owner", "department": "dept-a"},
        "visibility": "department",
    }
    values.update(overrides)
    return service.remember(principal, **values)


def test_another_departments_private_record_is_not_projected(
    store, tmp_path, tenant, principal, department_acl
):
    service = MemoryService(
        store, tmp_path / "unused", department_acl("dept-a")
    )
    visible = _remember(service, principal)
    hidden = _remember(
        service, principal,
        scope="department", scope_id="dept-b",
        content="Dept B keeps a private payroll record.",
        provenance={
            "writer": "owner", "department": "dept-b",
            "artifact_ids": ["payroll-artifact"],
        },
    )
    shared = _remember(
        service, principal,
        scope="department", scope_id="dept-b",
        content="Dept B published the office holiday calendar.",
        provenance={"writer": "owner", "department": "dept-b"},
        visibility="office",
    )

    projection = MemoryProjection(
        service,
        tmp_path / "projection",
        tenant_id=tenant.tenant_id,
        principal=principal,
        departments=["dept-a", "dept-b"],
    )
    projection.rebuild()
    root = projection.root

    assert (root / "notes" / f"{visible.memory_id}.md").exists()
    assert not (root / "notes" / f"{hidden.memory_id}.md").exists()
    # An office-visible record from the same other department is readable.
    assert (root / "notes" / f"{shared.memory_id}.md").exists()

    all_text = "\n".join(
        path.read_text(encoding="utf-8") for path in root.rglob("*.md")
    )
    assert hidden.memory_id not in all_text
    assert "private payroll record" not in all_text
    assert not (root / "artifacts" / "payroll-artifact.md").exists()

    dept_a = (root / "departments" / "dept-a" / "_MOC.md").read_text(
        encoding="utf-8"
    )
    dept_b = (root / "departments" / "dept-b" / "_MOC.md").read_text(
        encoding="utf-8"
    )
    assert visible.memory_id in dept_a
    assert hidden.memory_id not in dept_b
    assert shared.memory_id not in dept_a


def test_manifest_marks_every_projected_note_readable(
    service, principal, tenant, tmp_path
):
    import json

    _remember(service, principal)
    projection = MemoryProjection(
        service,
        tmp_path / "projection",
        tenant_id=tenant.tenant_id,
        principal=principal,
        departments=["dept-a"],
    )
    projection.rebuild()
    manifest = json.loads(
        (projection.root / "_projection.json").read_text(encoding="utf-8")
    )
    assert manifest["viewer"]
    assert manifest["notes"]
    assert all(row["readable"] is True for row in manifest["notes"])


def test_artifact_backlinks_are_bidirectional(
    service, principal, tenant, tmp_path
):
    with_artifact = _remember(
        service, principal,
        content="The signed contract is stored as a PDF.",
        labels=["deliverable"],
        provenance={
            "writer": "owner", "department": "dept-a",
            "deliverable": "contract-2026",
            "artifact_ids": ["artifact-abc", "artifact-def"],
        },
    )
    projection = MemoryProjection(
        service,
        tmp_path / "projection",
        tenant_id=tenant.tenant_id,
        principal=principal,
        departments=["dept-a"],
    )
    projection.rebuild()
    note = (
        projection.root / "notes" / f"{with_artifact.memory_id}.md"
    ).read_text(encoding="utf-8")
    for artifact in ("artifact-abc", "artifact-def"):
        index = projection.root / "artifacts" / f"{artifact}.md"
        assert index.exists()
        assert f"[[notes/{with_artifact.memory_id}" in index.read_text(
            encoding="utf-8"
        )
        assert f"[[artifacts/{artifact}" in note


def test_projection_redacts_secret_shaped_memory_content(
    service, principal, tenant, tmp_path
):
    record = _remember(
        service, principal,
        content="Wire credentials: api_key=super-secret-value and"
        " Bearer abcdefghijklmnop",
    )
    projection = MemoryProjection(
        service,
        tmp_path / "projection",
        tenant_id=tenant.tenant_id,
        principal=principal,
        departments=["dept-a"],
    )
    projection.rebuild()
    text = (
        projection.root / "notes" / f"{record.memory_id}.md"
    ).read_text(encoding="utf-8")
    assert "super-secret-value" not in text
    assert "abcdefghijklmnop" not in text
    assert "***REDACTED***" in text
