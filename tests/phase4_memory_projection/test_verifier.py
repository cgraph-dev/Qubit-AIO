"""P4-05 — the standalone verifier catches drift, leaks and rebuild mismatch."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from voicebridge.business.memory import MemoryService, SameTenantACL
from voicebridge.business.memory_projection import MemoryProjection

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER = REPO_ROOT / "scripts" / "verify_memory_projections.py"


def _remember(service, principal, **overrides):
    values = {
        "scope": "department",
        "scope_id": "engineering",
        "content": "Engineering owns the release train.",
        "source": "owner",
        "provenance": {
            "writer": "owner",
            "department": "engineering",
            "artifact_ids": ["artifact-1"],
        },
        "visibility": "department",
        "labels": ["deliverable"],
    }
    values.update(overrides)
    return service.remember(principal, **values)


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VERIFIER), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _build(store, tmp_path, tenant, principal):
    service = MemoryService(
        store, tmp_path / "unused", SameTenantACL()
    )
    _remember(service, principal)
    _remember(
        service, principal,
        scope="office", scope_id="office-1",
        content="Office hours start at 09:00.",
        source="owner",
        provenance={"writer": "owner"},
        visibility="office",
        labels=[],
    )
    projection = MemoryProjection(
        service,
        tmp_path / "projection",
        tenant_id=tenant.tenant_id,
        principal=principal,
        departments=["engineering"],
    )
    projection.rebuild()
    return service, projection


def test_verifier_accepts_a_clean_projection(
    store, tmp_path, tenant, principal
):
    _, projection = _build(store, tmp_path, tenant, principal)
    result = _run("--root", str(projection.root), "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["stats"]["notes"] == 2
    assert report["stats"]["departments"] == 1
    assert report["stats"]["special_rooms"] == 8
    assert report["stats"]["artifacts"] == 1


def test_verifier_checks_rebuild_equality_against_the_store(
    store, tmp_path, tenant, principal
):
    _, projection = _build(store, tmp_path, tenant, principal)
    result = _run(
        "--root", str(projection.root),
        "--db", str(store.path if hasattr(store, "path") else tmp_path / "business.db"),
        "--json",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["stats"]["rebuild_checked"] is True


def test_verifier_rejects_a_planted_note_id(
    store, tmp_path, tenant, principal
):
    _, projection = _build(store, tmp_path, tenant, principal)
    note = next((projection.root / "notes").glob("*.md"))
    text = note.read_text(encoding="utf-8")
    note.write_text(
        text.replace('id: "', 'id: "mem_WRONG_', 1),
        encoding="utf-8",
    )
    result = _run("--root", str(projection.root))
    assert result.returncode == 1
    assert "does not match" in result.stdout
    assert "digest" in result.stdout


def test_verifier_rejects_moc_drift(
    store, tmp_path, tenant, principal
):
    _, projection = _build(store, tmp_path, tenant, principal)
    moc = projection.root / "departments" / "engineering" / "_MOC.md"
    text = moc.read_text(encoding="utf-8")
    lines = [
        line for line in text.splitlines()
        if "[[notes/" not in line
    ]
    moc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = _run("--root", str(projection.root))
    assert result.returncode == 1
    assert "is not listed" in result.stdout


def test_verifier_rejects_a_secret_and_paid_art(
    store, tmp_path, tenant, principal
):
    _, projection = _build(store, tmp_path, tenant, principal)
    note = next((projection.root / "notes").glob("*.md"))
    with note.open("a", encoding="utf-8") as handle:
        handle.write("\napi_key=super-secret-value\n")
    result = _run("--root", str(projection.root))
    assert result.returncode == 1
    assert "secret-shaped text" in result.stdout

    text = note.read_text(encoding="utf-8").replace(
        "api_key=super-secret-value", "![floor](Room_Builder_Floors_16x16.png)"
    )
    note.write_text(text, encoding="utf-8")
    result = _run("--root", str(projection.root))
    assert result.returncode == 1
    assert "paid-art" in result.stdout


def test_verifier_returns_two_for_a_missing_root(tmp_path):
    result = _run("--root", str(tmp_path / "absent"), "--json")
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert "does not exist" in report["errors"][0]
