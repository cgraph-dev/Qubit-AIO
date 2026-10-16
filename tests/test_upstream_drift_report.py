"""Focused tests for scripts/upstream_drift_report.py's pure helpers.

These do NOT hit the network and do NOT need the upstream clone: they exercise
the classification predicates and the pin resolution, which are where a wrong
answer would silently mislabel "real work" as "upstream merely newer". The
numbers themselves are verified by running the script; these guard the parsing.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "upstream_drift_report.py"


def _load():
    spec = importlib.util.spec_from_file_location("_drift_report", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def m():
    return _load()


def test_pin_matches_the_burndown_instrument(m):
    """The report's pin must be the SAME constant the burndown declares.

    A second hardcoded copy is exactly how this repo ended up with mutually
    inconsistent 'how far behind' numbers; this pins the two together.
    """
    src = (REPO / "scripts" / "frozen_target_progress.py").read_text()
    declared = None
    for line in src.splitlines():
        if line.startswith("FROZEN_REV"):
            declared = line.split("=", 1)[1].strip().strip('"').strip("'")
    assert declared is not None
    assert m.frozen_pin() == declared


def test_is_test_path(m):
    assert m.is_test_path("tests/foo.py")
    assert m.is_test_path("plugins/x/tests/bar.py")
    assert not m.is_test_path("agent/foo.py")
    # a production file whose path merely contains 'test' is not a test
    assert not m.is_test_path("agent/latest_runner.py")


def test_is_production_py(m):
    assert m.is_production_py("agent/foo.py")
    assert not m.is_production_py("agent/foo.pyc")
    assert not m.is_production_py("tests/foo.py")
    assert not m.is_production_py("agent/foo.md")
    assert not m.is_production_py("gateway/tests/x.py")


def test_router_is_imported_not_reimplemented(m):
    """The existence test must use the audits' route(), not a local copy."""
    route = m._load_route()
    # hermes_cli -> qubit_cli, and the underscore-module rule both apply.
    assert route("hermes_cli/commands.py") == "qubit_cli/commands.py"
    assert route("hermes_state_keys.py") == "qubit_state_keys.py"
    assert route("plugins/hermes-achievements/api.py") == \
        "plugins/qubit-achievements/api.py"
    # Segment-level rule: a bare last segment starting with `hermes` is renamed
    # (hermes_flow.json -> qubit_flow.json). The filter that protects frozen wire
    # names lives in the audit's symbol compare (PRESERVED_WIRE), not in route().
    assert route("hermes_flow.json") == "qubit_flow.json"


def _frozen_rev_declared() -> str:
    src = (REPO / "scripts" / "frozen_target_progress.py").read_text()
    for line in src.splitlines():
        if line.startswith("FROZEN_REV"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise AssertionError("frozen_target_progress.py declares no FROZEN_REV")


def test_audit_short_port_baseline_is_the_frozen_pin():
    """audit_sync_completeness' short-port BASELINE must be the frozen pin.

    Its `short_port` branch answers "we never reached our own baseline" -- and
    "our own baseline" is the revision we should have synced from, i.e.
    FROZEN_REV, not an arbitrary waypoint. A hardcoded stale base credits us
    every upstream symbol added since it, masking genuine port debt as
    "upstream merely newer". This is the third drift-measuring site; the other
    two (audit_capability_gaps, audit_missing_symbols) already read FROZEN_REV,
    and this asserts the instrument they both shell out to agrees.
    """
    src = (REPO / "scripts" / "audit_sync_completeness.py").read_text()
    # The literal must not remain as a measuring base (it may appear only in a
    # comment explaining the old value).
    code_lines = [ln for ln in src.splitlines()
                  if not ln.lstrip().startswith("#")]
    assert not any(ln.startswith("BASELINE") and "a036b13793" in ln
                   for ln in code_lines), (
        "audit_sync_completeness still hardcodes the stale a036b13793 baseline")

    import sys as _sys
    _sys.path.insert(0, str(REPO / "scripts"))
    try:
        import audit_sync_completeness as audit  # noqa: E402
    finally:
        _sys.path.pop(0)
    assert audit.BASELINE == _frozen_rev_declared(), (
        f"BASELINE={audit.BASELINE!r} != frozen pin {_frozen_rev_declared()!r}")
