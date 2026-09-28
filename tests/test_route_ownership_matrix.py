"""Guard the explicit Qubit route-ownership matrix (owner decision 2026-09-17).

The vendored Hermes runtime is the canonical implementation for
Hermes-compatible routes; Qubit owns the front door, policy boundaries, and the
Qubit/OpenClaw additions. This test fails when the served authority contradicts
the declared owner:

* a thin Qubit compat handler shadowing a working Hermes route, or
* a Qubit-policy/addition route the fork proxy would serve instead.

It also keeps the committed matrix current, so ownership cannot silently drift.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMITTED = REPO / "docs/generated" / "QUBIT_ROUTE_OWNERSHIP.json"
GENERATOR = REPO / "scripts" / "route_ownership_matrix.py"


def _build() -> dict:
    spec = importlib.util.spec_from_file_location(
        "route_ownership_matrix", GENERATOR)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["route_ownership_matrix"] = mod
    spec.loader.exec_module(mod)
    return mod.build()


def _committed() -> dict:
    return json.loads(COMMITTED.read_text(encoding="utf-8"))


def test_committed_matrix_is_current():
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
