"""Regression guards for the re-runnable forensic-audit harness.

These tests never write product files: they exercise the pure schema/lifecycle
logic, the deterministic fast checks, and the report renderer.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

from forensic_audit import checks as checks_mod  # noqa: E402
from forensic_audit import report as report_mod  # noqa: E402
from forensic_audit import schema  # noqa: E402


def test_fingerprint_is_line_independent():
    a = schema.finding("home_fallback", "f", "t", path="p", line=10)
    b = schema.finding("home_fallback", "f", "t", path="p", line=999)
    assert a["fingerprint"] == b["fingerprint"]
    c = schema.finding("home_fallback", "g", "t", path="p", line=10)
    assert a["fingerprint"] != c["fingerprint"]


def test_lifecycle_new_open_fixed_regressed():
    row = schema.finding("c", "s", "t", path="p", severity="HIGH")
    # absent -> present => NEW
    out = schema.diff_against_ledger([dict(row)], schema.empty_ledger())
    assert out[0]["status"] == schema.STATUS_NEW
    # present twice => OPEN
    led = schema.ledger_payload(out, 1)
    out2 = schema.diff_against_ledger([dict(row)], led)
    assert out2[0]["status"] == schema.STATUS_OPEN
    # absent now => FIXED
    out3 = schema.diff_against_ledger([], led)
    assert out3[0]["status"] == schema.STATUS_FIXED
    # present again after FIXED => REGRESSED
    fixed_led = schema.ledger_payload(out3, 2)
    out4 = schema.diff_against_ledger([dict(row)], fixed_led)
    assert out4[0]["status"] == schema.STATUS_REGRESSED
    assert schema.summarize(out4)["gating"]


def test_triage_preserves_status():
    row = schema.finding("c", "s", "t", path="p", severity="HIGH")
    led = schema.ledger_payload(schema.diff_against_ledger([dict(row)],
                                                           schema.empty_ledger()), 1)
    triage = {row["fingerprint"]: {"status": "WONTFIX", "note": "third-party"}}
    rows = schema.apply_triage(
        schema.diff_against_ledger([dict(row)], led), triage)
    assert rows[0]["status"] == "WONTFIX"
    assert not schema.summarize(rows)["gating"]


def test_fast_checks_are_deterministic_and_wellformed():
    a = checks_mod.run_checks(REPO, fast=True)
    b = checks_mod.run_checks(REPO, fast=True)
    assert schema.dumps(schema.sort_findings(a)) == schema.dumps(schema.sort_findings(b))
    for row in a:
        assert row["check"]
        assert row["severity"] in schema.SEVERITY_ORDER
        assert row["classification"] in schema.CLASSIFICATIONS
        assert len(row["fingerprint"]) == 16


def test_check_ids_unique():
    ids = [c["id"] for c in checks_mod.CHECKS]
    assert len(ids) == len(set(ids))
    assert ids


def test_report_render_has_sections():
    row = schema.finding("home_fallback", "s", "t", path="p", severity="HIGH")
    rows = schema.diff_against_ledger([row], schema.empty_ledger())
    text = report_mod.render(rows, 1, checks_mod.CHECKS)
    assert "## Open findings" in text
    assert "Coverage map" in text
    assert "Tier 1" in text
    assert "READY_FOR_REPAIR" in text or "FAIL_GATE" in text


def test_subset_run_does_not_close_unrun_checks():
    a = schema.finding("a", "s", "t", path="p", severity="HIGH")
    b = schema.finding("b", "s", "t", path="p", severity="HIGH")
    led = schema.ledger_payload(
        schema.diff_against_ledger([dict(a), dict(b)], schema.empty_ledger()), 1)
    rows = schema.diff_against_ledger([dict(a)], led, run_check_ids=["a"])
    kept = [r for r in rows if r["fingerprint"] == b["fingerprint"]]
    assert kept and kept[0]["status"] != schema.STATUS_FIXED


def test_home_fallback_check_flags_isolated_home(tmp_path):
    # A synthetic overlay file with a fallback candidate must be flagged.
    root = tmp_path
    (root / "voicebridge").mkdir()
    (root / "voicebridge" / "x.py").write_text(
        "from pathlib import Path\nimport os\n"
        "D = os.getenv('QUBIT_HOME', str(Path.home() / '.hermes'))\n")
    rows = checks_mod.check_home_fallback(root, {})
    assert any(r["check"] == "home_fallback" for r in rows)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
