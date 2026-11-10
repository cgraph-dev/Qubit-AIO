"""Guard the explicit Qubit route-ownership matrix (owner decision 2026-09-17).

The vendored Hermes runtime is the canonical implementation for
Hermes-compatible routes; Qubit owns the front door, policy boundaries, and the
Qubit/OpenClaw additions. This test fails when the served authority contradicts
the declared owner:

* a thin Qubit compat handler shadowing a working Hermes route, or
* a Qubit-policy/addition route the fork proxy would serve instead.

It also keeps the generated matrix current, so ownership cannot silently drift.

``docs/generated/`` is a build output (.gitignore:90), so the matrix is not in
the tree on a fresh checkout. Each test first runs the real generator when the
artifact is absent; it only skips — with the reason named — when generation
itself fails, never to paper over a real ownership contradiction.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMMITTED = REPO / "docs/generated" / "QUBIT_ROUTE_OWNERSHIP.json"
GENERATOR = REPO / "scripts" / "route_ownership_matrix.py"
SKIP_REASON = (
    "docs/generated/QUBIT_ROUTE_OWNERSHIP.json is a gitignored build output and "
    "`python scripts/route_ownership_matrix.py` failed to generate it in this tree"
)


def _build() -> dict:
    spec = importlib.util.spec_from_file_location(
        "route_ownership_matrix", GENERATOR)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["route_ownership_matrix"] = mod
    spec.loader.exec_module(mod)
    return mod.build()


def _generate() -> bool:
    """Run the real generator; True when it produced the matrix."""
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "--out", str(COMMITTED)],
        cwd=str(REPO), capture_output=True, text=True)
    return proc.returncode == 0 and COMMITTED.is_file()


def _committed() -> dict:
    if not COMMITTED.is_file():
        if not _generate():
            pytest.skip(SKIP_REASON)
    return json.loads(COMMITTED.read_text(encoding="utf-8"))


def test_committed_matrix_is_current():
    if not COMMITTED.is_file() and not _generate():
        pytest.skip(SKIP_REASON)
    assert COMMITTED.is_file(), COMMITTED
    assert _committed() == _build(), (
        "docs/generated/QUBIT_ROUTE_OWNERSHIP.json is stale; regenerate with "
        "`python scripts/route_ownership_matrix.py`")


def test_matrix_has_no_ownership_violations():
    doc = _committed()
    assert doc["violations"] == [], doc["violations"][:10]


def test_every_route_has_a_declared_owner():
    doc = _committed()
    owners = {"qubit_runtime", "qubit_policy", "qubit_addition"}
    assert set(doc["counts"]) <= owners
    for row in doc["rows"]:
        assert row["owner"] in owners, row
        assert row["reason"], row
        assert row["method"] and row["path"], row
    # All three owners are exercised by the real tree.
    assert doc["counts"]["qubit_runtime"] > 0
    assert doc["counts"]["qubit_policy"] > 0
    assert doc["counts"]["qubit_addition"] > 0


def test_non_authoritative_handlers_are_recorded_not_served():
    """Qubit compat handlers the fork owns are dead code, recorded explicitly.

    They must be classified ``qubit_runtime`` (never treated as the served
    implementation), and the count must match the rows so a future edit cannot
    quietly re-assume authority."""
    doc = _committed()
    derived = {
        f"{r['method']} {r['path']} -> {r['qubit_handler']}"
        for r in doc["rows"]
        if r["owner"] == "qubit_runtime" and r["qubit_handler"]
    }
    recorded = set(doc["non_authoritative_handlers"])
    assert recorded == derived
    assert doc["non_authoritative_handler_count"] == len(recorded)
