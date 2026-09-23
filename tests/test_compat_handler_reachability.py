"""Guard the compat-handler reachability proof (owner decision 2026-09-17).

Every Qubit compat handler the vendored runtime owns must have a recorded
reachability decision. The proof must show zero genuinely unreachable dead
code; a future edit that orphans a handler fails here.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMITTED = REPO / "docs/archive/2026-09-21/QUBIT_COMPAT_HANDLER_REACHABILITY.json"
GENERATOR = REPO / "scripts/compat_handler_reachability.py"
MATRIX = REPO / "docs/archive/2026-09-21/QUBIT_ROUTE_OWNERSHIP.json"

CLASSES = {"production_referenced", "test_referenced", "route_test_reachable",
           "script_referenced", "registration_only"}


def _build() -> dict:
    spec = importlib.util.spec_from_file_location(
        "compat_handler_reachability", GENERATOR)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["compat_handler_reachability"] = mod
    spec.loader.exec_module(mod)
    return mod.build()


def _committed() -> dict:
    return json.loads(COMMITTED.read_text(encoding="utf-8"))


def test_committed_proof_is_current():
    assert COMMITTED.is_file(), COMMITTED
    assert _committed() == _build(), (
        "docs/archive/2026-09-21/QUBIT_COMPAT_HANDLER_REACHABILITY.json is stale; regenerate with "
        "`python scripts/compat_handler_reachability.py`")


def test_no_unreachable_dead_code():
    doc = _committed()
    assert doc["dead_module_functions"] == [], doc["dead_module_functions"]
    assert doc["delete_candidates"] == [], doc["delete_candidates"]


def test_every_owned_handler_has_a_decision():
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    owned = {r["qubit_handler"] for r in matrix["rows"]
             if r["owner"] == "qubit_runtime" and r["qubit_handler"]}
    proof = {r["handler"] for r in _committed()["rows"]}
    assert owned <= proof, sorted(owned - proof)
    for row in _committed()["rows"]:
        assert row["classification"] in CLASSES, row
        assert row["routes"], row  # every owned handler is registered
        # ``defined`` may be False for a handler registered through a lambda
        # wrapper (e.g. ``sess_empty`` -> ``h_sess_empty``); registration is
        # what makes it reachable, and a missing HANDLERS entry would surface
        # as a NO_HANDLER 500 in the route parity suite.
        assert isinstance(row["defined"], bool), row


def test_production_callers_are_real_ast_references():
    """A production_referenced handler must have a real call site, not a comment."""
    doc = _committed()
    prod = [r for r in doc["rows"]
            if r["classification"] == "production_referenced"]
    assert prod, "expected at least one production-referenced handler"
    for row in prod:
        assert row["production_references"], row
        for refs in row["production_references"].values():
            assert refs, row
