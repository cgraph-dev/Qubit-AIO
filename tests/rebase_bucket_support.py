# tests/rebase_bucket_support.py
"""Shared helpers for the Task 6 per-bucket rebase gates.

Each ``tests/test_rebase_<bucket>.py`` imports this module (pytest prepends the
``tests`` directory to ``sys.path``) and asserts three things:

1. the v0.21.5-only files the merge must land are present;
2. the bucket still imports on the merged tree (import + registry smoke);
3. the bucket appears in the canonical merge audit with its resolution tally.

Before Task 6 runs these fail (the new files and the audit are absent); after
the bucket merge they pass.  The module never writes a product tree.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "qubit_runtime"
AUDIT = REPO / "docs" / "generated" / "audit" / "V0_21_5_MERGE.json"


@pytest.fixture
def runtime_on_path(tmp_path, monkeypatch):
    """Import the merged runtime from its own tree under an isolated home."""
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.syspath_prepend(str(RUNTIME))
    yield


def assert_new_files(bucket: str, rels: list[str]) -> None:
    missing = [r for r in rels if not (RUNTIME / r).is_file()]
    assert not missing, f"{bucket}: v0.21.5 file(s) not merged: {missing}"


def assert_imports(modules: list[str]) -> None:
    for mod in modules:
        importlib.import_module(mod)


def assert_bucket_audit(bucket: str, min_conflicts: int = 0) -> dict:
    assert AUDIT.is_file(), f"merge audit missing: {AUDIT}"
    data = json.loads(AUDIT.read_text(encoding="utf-8"))
    assert data["schema"] == "qubit-v0215-task6-merge/1"
    rows = [r for r in data["files"] if r["bucket"] == bucket]
    assert rows, f"bucket {bucket!r} has no audit records"
    conflicts = [r for r in rows if r["resolution"] == "KEEP_THEIRS_CONFLICT"]
    assert len(conflicts) >= min_conflicts, (
        f"{bucket}: expected >= {min_conflicts} kept conflicts, got {len(conflicts)}"
    )
    return data
