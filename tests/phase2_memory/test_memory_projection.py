"""P2-04 — Obsidian projection: notes, MOC, backlinks, rebuild, no leakage.

The projection is derived from events. These tests prove it is complete,
deterministic, deleted with a tombstone, and rejected by the standalone
verifier when it drifts or leaks.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


from voicebridge.business.memory import MemoryService, SameTenantACL

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER = REPO_ROOT / "scripts" / "verify_memory_projection.py"


def _remember(service, principal, **overrides):
    values = {
        "scope": "department",
        "scope_id": "dept-1",
        "content": "The supplier quotes in EUR.",
        "source": "owner",
        "confidence": 0.8,
        "provenance": {"writer": "owner"},
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


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*.md"))
    }


def _run_verifier(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VERIFIER), "--root", str(root), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_rebuild_writes_one_note_per_memory_with_moc_and_backlinks(
    service, principal, tenant, tmp_path
):
    first = _remember(service, principal, content="Supplier A quotes in EUR.")
    second = _remember(service, principal, content="Supplier B quotes in RON.")
    agent = service.remember(
        principal,
        scope="agent",
        scope_id="seat-7",
        content="Seat seven owns supplier A.",
        source="agent",
        provenance={"writer": "seat-7"},
    )

    written = service.rebuild(tenant_id=tenant.tenant_id)
    assert written == 3

    root = service.projection_dir
    note = root / "department" / f"{first.memory_id}.md"
    assert note.exists()
    assert (root / "department" / "_MOC.md").exists()
    assert (root / "agent" / f"{agent.memory_id}.md").exists()

    frontmatter = _frontmatter(note)
    assert frontmatter["id"] == first.memory_id
    assert frontmatter["scope"] == "department"
    assert frontmatter["source"] == "owner"
    assert frontmatter["provenance"] == {"writer": "owner"}
    assert frontmatter["confidence"] == 0.8
    assert frontmatter["visibility"] == "department"
    assert frontmatter["expires"] is None
    assert frontmatter["supersedes"] is None

    body = note.read_text(encoding="utf-8")
    assert body.count(first.content) == 1
    assert "[[_MOC]]" in body

    moc = (root / "department" / "_MOC.md").read_text(encoding="utf-8")
    assert f"[[{first.memory_id}]]" in moc
    assert f"[[{second.memory_id}]]" in moc


def test_supersession_links_are_projected(service, principal, tenant):
    original = _remember(service, principal, content="The budget is 10k.")
    correction = service.correct(
        original.memory_id, "owner", content="The budget is 12k.", confidence=0.9
    )
    service.rebuild(tenant_id=tenant.tenant_id)
    root = service.projection_dir

    old_note = root / "department" / f"{original.memory_id}.md"
    new_note = root / "department" / f"{correction.memory_id}.md"
    assert _frontmatter(old_note)["superseded_by"] == correction.memory_id
    assert _frontmatter(new_note)["supersedes"] == original.memory_id
    assert f"superseded by [[{correction.memory_id}]]" in old_note.read_text(
        encoding="utf-8"
    )
    assert f"supersedes [[{original.memory_id}]]" in new_note.read_text(
        encoding="utf-8"
    )


def test_rebuild_is_deterministic_and_tombstones_remove_notes(
    service, principal, tenant
):
    keep = _remember(service, principal, content="Keep this fact.")
    drop = _remember(service, principal, content="Drop this fact.")

    service.rebuild(tenant_id=tenant.tenant_id)
    root = service.projection_dir
    first = _snapshot(root)
    service.rebuild(tenant_id=tenant.tenant_id)
    assert _snapshot(root) == first
    assert len(first) == 3  # two notes + MOC

    exported_before = service.export(
        "department", "dept-1", tenant_id=tenant.tenant_id
    )
    assert {item["memory_id"] for item in exported_before} == {
        keep.memory_id, drop.memory_id
    }

    service.forget(drop.memory_id, "owner", reason="superseded by reality")
    service.rebuild(tenant_id=tenant.tenant_id)
    assert not (root / "department" / f"{drop.memory_id}.md").exists()
    assert (root / "department" / f"{keep.memory_id}.md").exists()
    exported_after = service.export(
        "department", "dept-1", tenant_id=tenant.tenant_id
    )
    assert {item["memory_id"] for item in exported_after} == {keep.memory_id}
    # Deletion never rewrites history; the record still resolves as tombstoned.
    assert service.get(drop.memory_id).tombstoned is True


def test_verifier_accepts_a_clean_projection(service, principal, tenant):
    _remember(service, principal)
    service.rebuild(tenant_id=tenant.tenant_id)
    result = _run_verifier(service.projection_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK:" in result.stdout


def test_verifier_rejects_a_mismatched_note_id(service, principal, tenant):
    record = _remember(service, principal)
    service.rebuild(tenant_id=tenant.tenant_id)
    note = service.projection_dir / "department" / f"{record.memory_id}.md"
    text = note.read_text(encoding="utf-8")
    note.write_text(
        text.replace(
            f'id: "{record.memory_id}"', 'id: "mem_WRONGID"', 1
        ),
        encoding="utf-8",
    )
    result = _run_verifier(service.projection_dir)
    assert result.returncode == 1
    assert "does not match filename" in result.stdout


def test_verifier_rejects_moc_drift(service, principal, tenant):
    record = _remember(service, principal)
    service.rebuild(tenant_id=tenant.tenant_id)
    moc = service.projection_dir / "department" / "_MOC.md"
    text = moc.read_text(encoding="utf-8")
    # Remove the note link but keep the MOC syntactically valid.
    lines = [line for line in text.splitlines() if f"[[{record.memory_id}]]" not in line]
    moc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = _run_verifier(service.projection_dir)
    assert result.returncode == 1
    assert "is not listed" in result.stdout


def test_projection_does_not_leak_secrets(service, principal, tenant):
    record = _remember(
        service,
        principal,
        content=(
            "Wire credentials: api_key=super-secret-value and"
            " Bearer abcdefghijklmnop and sk-ABCDEFGHIJKLMNOP"
        ),
        source="owner",
    )
    service.rebuild(tenant_id=tenant.tenant_id)
    note = service.projection_dir / "department" / f"{record.memory_id}.md"
    text = note.read_text(encoding="utf-8")
    assert "super-secret-value" not in text
    assert "abcdefghijklmnop" not in text
    assert "sk-ABCDEFGHIJKLMNOP" not in text
    assert "***REDACTED***" in text
    assert _run_verifier(service.projection_dir).returncode == 0


def test_verifier_rejects_paid_art_references(service, principal, tenant):
    record = _remember(service, principal)
    service.rebuild(tenant_id=tenant.tenant_id)
    note = service.projection_dir / "department" / f"{record.memory_id}.md"
    with note.open("a", encoding="utf-8") as handle:
        handle.write("\n![floor](Room_Builder_Floors_16x16.png)\n")
    result = _run_verifier(service.projection_dir)
    assert result.returncode == 1
    assert "paid-art" in result.stdout


def test_verifier_reports_a_missing_root(tmp_path):
    result = subprocess.run(
        [sys.executable, str(VERIFIER), "--root", str(tmp_path / "absent"), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "does not exist" in result.stdout


def test_acceptance_projection_is_materialized(store, tmp_path, principal, tenant):
    """Materialize the fixed path the phase acceptance command verifies.

    The projection is derived, so building it from a throwaway store is safe;
    the verifier is then run exactly as the acceptance command runs it.
    """
    root = Path("/tmp/qoffice-memory-projection")
    service = MemoryService(store, root, SameTenantACL())
    service.remember(
        principal,
        scope="department",
        scope_id="dept-1",
        content="The supplier quotes in EUR.",
        source="owner",
        confidence=0.8,
        provenance={"writer": "owner"},
    )
    service.remember(
        principal,
        scope="office",
        scope_id="office-1",
        content="Office hours start at 09:00.",
        source="owner",
        provenance={"writer": "owner"},
    )
    service.rebuild(tenant_id=tenant.tenant_id)
    result = _run_verifier(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "department" / "_MOC.md").exists()


def test_superseding_a_tombstoned_record_leaves_no_dangling_link(
    service, principal, tenant
):
    original = _remember(service, principal, content="A fact that was deleted.")
    service.forget(original.memory_id, "owner")
    replacement = service.correct(
        original.memory_id, "owner", content="A corrected fact."
    )
    service.rebuild(tenant_id=tenant.tenant_id)
    root = service.projection_dir
    new_note = root / "department" / f"{replacement.memory_id}.md"
    assert new_note.exists()
    assert not (root / "department" / f"{original.memory_id}.md").exists()
    # The deleted target is not projected, so the live note must not link to it.
    assert "supersedes" in _frontmatter(new_note)
    assert _frontmatter(new_note)["supersedes"] is None
    assert _run_verifier(root).returncode == 0
