"""P4-05 — materialize the fixed acceptance projection and verify it.

Mirrors ``tests/phase2_memory/test_memory_projection.py``: the projection is
derived, so building it under ``/tmp`` from a throwaway store is safe and the
acceptance verifier can then be run exactly as the phase gate runs it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from voicebridge.business import BusinessStore, identity
from voicebridge.business.memory import MemoryService, SameTenantACL
from voicebridge.business.memory_projection import MemoryProjection

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER = REPO_ROOT / "scripts" / "verify_memory_projections.py"
BASE = Path("/tmp/qoffice-memory-projection-p4")


def _run_verifier() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(VERIFIER), "--json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_acceptance_projection_is_materialized_and_verifies():
    if BASE.exists():
        shutil.rmtree(BASE)
    BASE.mkdir(parents=True)
    store = BusinessStore(BASE / "business.db")
    tenant = store.create_tenant("Qubit Office Acceptance")
    store.create_business(tenant.tenant_id, "Qubit Office", slug="office")
    principal = identity.create_principal(store, tenant.tenant_id, kind="owner")
    service = MemoryService(store, BASE / "unused", SameTenantACL())

    service.remember(
        principal,
        scope="department",
        scope_id="engineering",
        content="Engineering runs the Friday release train.",
        source="owner",
        provenance={
            "writer": "owner",
            "department": "engineering",
            "room": "release-room",
            "artifact_ids": ["build-42-sha256"],
        },
        visibility="department",
        labels=["deliverable"],
    )
    service.remember(
        principal,
        scope="office",
        scope_id="office-1",
        content="Office hours start at 09:00.",
        source="owner",
        provenance={"writer": "owner"},
        visibility="office",
    )
    projection = MemoryProjection(
        service,
        BASE / "projection",
        tenant_id=tenant.tenant_id,
        principal=principal,
        departments=["engineering"],
    )
    projection.rebuild()

    result = _run_verifier()
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["stats"]["rebuild_checked"] is True
    assert (BASE / "projection" / "_MOC.md").exists()
