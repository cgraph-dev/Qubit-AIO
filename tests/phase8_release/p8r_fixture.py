"""Shared hermetic fixtures for the P8-05 release-evidence tests.

Every artefact is written under ``tmp_path``: no live service, network,
credential or real owner signature is involved. The "owner" here is an
explicitly labelled test fixture, never a claim about the real owner.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

FROZEN_COMMIT = "0123456789abcdef0123456789abcdef01234567"
FROZEN_TREE = "89abcdef0123456789abcdef0123456789abcdef"
NOW = "2026-11-14T00:00:00Z"
SCOPE = (
    "Qubit Office feature matrix (tests/phase*, tests/office_contracts, "
    "tests/test_business*.py, tests/test_office_business.py, "
    "tests/test_office_runner.py); not the repo-wide matrix"
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def load_script(name: str):
    """Import a standalone script module without installing it."""
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"p8r_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, document: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def write_phase_manifest(
    root: Path,
    phase: int,
    *,
    status: str = "PASS",
    generated_at: str = NOW,
    phase_field: int | None = None,
    write_evidence_file: bool = True,
) -> Path:
    evidence_dir = root / "evidence" / f"phase-{phase}"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_rel = f"evidence/phase-{phase}/P{phase}-evidence.json"
    evidence_path = root / evidence_rel
    if write_evidence_file:
        write_json(evidence_path, {"task_id": f"P{phase}-01", "status": "PASS"})
    hashes = ({evidence_rel: sha256_file(evidence_path)}
              if write_evidence_file else {})
    manifest = {
        "schema": "qubit-office-phase-manifest/1",
        "phase": phase if phase_field is None else phase_field,
        "status": status,
        "generated_at": generated_at,
        "git_head": "previous-phase-commit",
        "tasks": [{"task_id": f"P{phase}-01", "exit_code": 0}],
        "evidence_hashes": hashes,
        "evidence_verification": {"ok": True, "checked": len(hashes),
                                  "failures": []},
        "limitations": ["hermetic test fixture"],
    }
    return write_json(evidence_dir / "manifest.json", manifest)


def dependency_lock(dependencies: list[dict] | None = None) -> dict:
    if dependencies is None:
        dependencies = [
            {
                "id": "concept-only",
                "name": "Concept Only",
                "license_status": "absent",
                "reuse_status": "concept",
                "permitted_use": "Concept reference only.",
            },
            {
                "id": "imported-ok",
                "name": "Imported OK",
                "revision": "a" * 40,
                "license_status": "present",
                "license_spdx": "MIT",
                "reuse_status": "imported",
                "permitted_use": "Vendored with notices.",
                "notices": True,
            },
        ]
    return {
        "schema": "qubit-office-dependency-lock/1",
        "generated_at": "2026-11-14",
        "paid_assets_local_only": {"redistribution_allowed": False},
        "dependencies": dependencies,
    }


def write_dependency_lock(root: Path, lock: dict | None = None) -> Path:
    return write_json(
        root / "docs" / "QUBIT-OFFICE-DEPENDENCY-LOCK.json",
        lock if lock is not None else dependency_lock())


def final_matrix(
    *,
    commit: str = FROZEN_COMMIT,
    tree: str = FROZEN_TREE,
    status: str = "PASS",
    scope: str = SCOPE,
    passed: int = 420,
    failed: int = 0,
    generated_at: str = NOW,
) -> dict:
    return {
        "schema": "qubit-office-final-matrix/1",
        "task_id": "P8-05",
        "kind": "office-feature-matrix",
        "scope": scope,
        "generated_at": generated_at,
        "commit": commit,
        "tree": tree,
        "working_tree": "clean",
        "status": status,
        "command": "python -m pytest -q tests/phase8_release",
        "targets": ["tests/phase8_release"],
        "result": {"exit_code": 0, "passed": passed, "failed": failed,
                   "duration_s": 1.0},
        "limitations": ["office feature matrix only"],
    }


def write_final_matrix(root: Path, document: dict | None = None) -> Path:
    return write_json(
        root / "evidence" / "phase-8" / "final-matrix.json",
        document if document is not None else final_matrix())


def owner_decision(
    *,
    signed_by: str = "Owner Name (test fixture)",
    role: str = "owner",
    decision: str = "APPROVED",
    release_sha: str = FROZEN_COMMIT,
    release_tree: str = FROZEN_TREE,
    signed_at: str = NOW,
    waivers: list[dict] | None = None,
) -> dict:
    return {
        "schema": "qubit-office-release-decision/1",
        "signed_by": signed_by,
        "role": role,
        "signed_at": signed_at,
        "decision": decision,
        "release_sha": release_sha,
        "release_tree": release_tree,
        "waivers": waivers if waivers is not None else [],
    }


def write_owner_decision(root: Path, document: dict) -> Path:
    return write_json(
        root / "evidence" / "phase-8" / "owner-release-decision.json",
        document)


def valid_root(root: Path, *, phases=range(9), include_decision: bool = True,
               include_matrix: bool = True) -> Path:
    for phase in phases:
        write_phase_manifest(root, phase)
    write_dependency_lock(root)
    if include_matrix:
        write_final_matrix(root)
    if include_decision:
        write_owner_decision(root, owner_decision())
    return root
