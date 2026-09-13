"""Invariants for the evidence-based OpenClaw gap-ledger regenerator.

The regenerator (``scripts/rebuild_ledger_openclaw.py``) must be deterministic
and must never claim ``IMPLEMENTED`` without a behavioural proof: every
``IMPLEMENTED`` row carries a non-empty ``proving_test`` that exists on disk
and references the cited backend, and the audited baseline row set is
preserved (no row is silently dropped).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rebuild = _load_module("rebuild_ledger_openclaw", "rebuild_ledger_openclaw.py")
gap_ledger = _load_module("gap_ledger", "gap_ledger.py")

SECTION_PATH = REPO_ROOT / "docs" / "QUBIT_GAP_LEDGER.openclaw.json"


def _section() -> dict:
    return json.loads(SECTION_PATH.read_text(encoding="utf-8"))


def test_schema_is_valid() -> None:
    data = _section()
    validated = gap_ledger.validate_section("openclaw", data)
    assert validated["schema"] == "qubit-gap-ledger-section/1"
    assert validated["source"] == "openclaw"
    assert validated["rows"]
    for row in data["rows"]:
        assert set(row) == set(gap_ledger.ROW_KEYS), sorted(
            set(row) ^ set(gap_ledger.ROW_KEYS))
    ids = [row["id"] for row in data["rows"]]
    assert len(ids) == len(set(ids)), "duplicate ledger row ids"


def test_output_is_deterministic() -> None:
    first = rebuild._render(rebuild.build_ledger())
    second = rebuild._render(rebuild.build_ledger())
    assert first == second


def test_committed_section_matches_regenerator() -> None:
    assert rebuild._render(rebuild.build_ledger()) == \
        SECTION_PATH.read_text(encoding="utf-8")


def test_baseline_rows_preserved() -> None:
    committed = {r["id"]: r for r in _section()["rows"]}
    baseline = {r["id"]: r for r in rebuild.baseline_rows()}
    assert set(committed) == set(baseline)
    assert len(committed) == 104


def test_implemented_rows_have_existing_proving_test() -> None:
    missing = []
    for row in _section()["rows"]:
        if row["status"] != "IMPLEMENTED":
            continue
        proving = row["proving_test"]
        if not proving:
            missing.append((row["id"], proving, "empty"))
            continue
        path = proving.split("::", 1)[0]
        if not (REPO_ROOT / path).is_file():
            missing.append((row["id"], proving, "not on disk"))
    assert missing == [], missing


def test_implemented_rows_link_backend_to_test() -> None:
    offenders = [(row["id"], row["qubit_backend"], row["proving_test"])
                 for row in _section()["rows"]
                 if row["status"] == "IMPLEMENTED"
                 and not rebuild._proves_caller(row)]
    assert offenders == [], offenders


def test_counts_match_rows() -> None:
    data = _section()
    rows = data["rows"]
    assert data["counts"]["total"] == len(rows)
    assert data["counts"]["by_status"] == {
        status: sum(1 for r in rows if r["status"] == status)
        for status in sorted({r["status"] for r in rows})
    }
    assert data["counts"]["by_area"] == {
        area: sum(1 for r in rows if r["area"] == area)
        for area in sorted({r["area"] for r in rows})
    }


def test_closed_automation_rows_are_evidenced() -> None:
    rows = {r["id"]: r for r in _section()["rows"]}
    assert rows["OC-27"]["status"] == "IMPLEMENTED"
    assert rows["OC-27"]["needs"] == "none"
    assert rows["OC-27"]["proving_test"].endswith(
        "test_scheduler_autofire8u.py")
    expected = {
        "OC-31": "tests/test_openclaw_e5_directives.py",
        "OC-D05": "tests/test_openclaw_e5_directives.py",
        "OC-D25": "tests/test_openclaw_e5_directives.py",
    }
    for rid, proving in expected.items():
        assert rows[rid]["status"] == "IMPLEMENTED", rows[rid]
        assert rows[rid]["proving_test"] == proving
        assert rows[rid]["needs"] == "none"
        assert rows[rid]["cli"] and rows[rid]["dashboard"] and rows[rid]["voice"]


def test_agent_wave_rows_are_derived() -> None:
    rows = {r["id"]: r for r in _section()["rows"]}
    # Genuinely implemented (needs=none) rows close through the one dispatcher.
    for rid in ("OC-29", "OC-D23", "OC-36", "OC-D31"):
        assert rows[rid]["status"] == "IMPLEMENTED", rid
        assert rows[rid]["proving_test"], rid
    # External dependency remains: the local part is proven; the code path is
    # complete and typed, so it is OPERATOR_ONLY (owner decision 2026-09-13).
    for rid, need in (("OC-48", "creds"), ("OC-51", "hardware"),
                      ("OC-D16", "creds")):
        assert rows[rid]["status"] == "OPERATOR_ONLY", rid
        assert rows[rid]["needs"] == need, rid
        assert rows[rid]["proving_test"], rid


def test_f2_rows_closed_or_honestly_reclassified() -> None:
    rows = {r["id"]: r for r in _section()["rows"]}
    expected = {
        "OC-33": "tests/test_openclaw_f2_projects.py",
        "OC-44": "tests/test_owner_session8tf.py",
        "OC-55": "tests/test_openclaw_e9_devices.py",
        "OC-71": "tests/test_openclaw_f2_panels.py",
        "OC-D03": "tests/test_openclaw_e9_transfers.py",
        "OC-D26": "tests/test_opencode_voice8u.py",
    }
    for rid, proving in expected.items():
        assert rows[rid]["status"] == "IMPLEMENTED", (rid, rows[rid])
        assert rows[rid]["needs"] == "none", rid
        assert rows[rid]["proving_test"] == proving, rid
    # Workboard dispatch cannot close without rewriting the frozen acceptance
    # test, so it is honestly owner-policy-gated (not a code-side gap).
    oc37 = rows["OC-37"]
    assert oc37["status"] == "SAFE_EQUIVALENT"
    assert oc37["needs"] == "owner"
    assert oc37["proving_test"] == "tests/test_kanban8tf.py"
    assert "frozen" in oc37["note"].lower()


def test_genuine_gates_stay_open() -> None:
    rows = {r["id"]: r for r in _section()["rows"]}
    # Credential / hardware / owner gates must not be flipped to IMPLEMENTED;
    # they are the owner-approved terminal statuses.
    assert rows["OC-13"]["status"] == "OPERATOR_ONLY" and \
        rows["OC-13"]["needs"] == "hardware"
    assert rows["OC-19"]["status"] == "OPERATOR_ONLY" and \
        rows["OC-19"]["needs"] == "creds"
    assert rows["OC-56"]["status"] == "OPERATOR_ONLY" and \
        rows["OC-56"]["needs"] == "hardware"
    # OC-01 closed its protocol surface but stays an owner-policy gate: Qubit
    # runs one event path and never starts a standalone gateway listener, so it
    # is a typed GATEWAY_NO_SERVER SAFE_EQUIVALENT, never IMPLEMENTED.
    assert rows["OC-01"]["status"] == "SAFE_EQUIVALENT" and \
        rows["OC-01"]["needs"] == "owner"
    assert "GATEWAY_NO_SERVER" in rows["OC-01"]["note"]


def test_all_rows_are_terminal() -> None:
    """Every committed row is a code-ready terminal status."""
    rows = _section()["rows"]
    non_terminal = [(r["id"], r["status"]) for r in rows
                    if r["status"] not in gap_ledger.TERMINAL_STATUSES]
    assert non_terminal == [], non_terminal
    for stale in ("GATED", "PARTIAL", "MISSING", "UNVERIFIED", "CLI_ONLY",
                  "DASHBOARD_ONLY", "VOICE_ONLY"):
        assert not [r["id"] for r in rows if r["status"] == stale], stale


def test_terminal_rows_have_proof_and_live_needs() -> None:
    """SAFE_EQUIVALENT / OPERATOR_ONLY rows keep a real proof and live need."""
    allowed = {"creds", "hardware", "owner"}
    for row in _section()["rows"]:
        if row["status"] not in ("SAFE_EQUIVALENT", "OPERATOR_ONLY"):
            continue
        assert row["needs"] in allowed, (row["id"], row["needs"])
        assert row["needs"] != "none", row["id"]
        proving = row["proving_test"]
        assert proving, (row["id"], "empty proving_test")
        assert (REPO_ROOT / proving.split("::", 1)[0]).is_file(), \
            (row["id"], proving)


def test_terminal_status_mapping_follows_owner_decision() -> None:
    """GATED(hardware) -> OPERATOR_ONLY; other GATED -> SAFE_EQUIVALENT;
    PARTIAL(creds/hardware) -> OPERATOR_ONLY; everything else is untouched."""
    rows = [
        {"status": "GATED", "needs": "hardware"},
        {"status": "GATED", "needs": "owner"},
        {"status": "GATED", "needs": "creds"},
        {"status": "PARTIAL", "needs": "creds"},
        {"status": "PARTIAL", "needs": "hardware"},
        {"status": "PARTIAL", "needs": "owner"},
        {"status": "MISSING", "needs": "owner"},
        {"status": "IMPLEMENTED", "needs": "none"},
    ]
    out = rebuild._finalize_terminal_statuses([dict(r) for r in rows])
    assert [r["status"] for r in out] == [
        "OPERATOR_ONLY", "SAFE_EQUIVALENT", "SAFE_EQUIVALENT",
        "OPERATOR_ONLY", "OPERATOR_ONLY", "PARTIAL", "MISSING", "IMPLEMENTED",
    ]


def test_upgrade_requires_production_dispatcher(tmp_path, monkeypatch) -> None:
    """A row upgraded from an open baseline must call a production dispatcher;
    an unlinked token mention is downgraded, never trusted."""
    monkeypatch.setattr(rebuild, "ROOT", tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "voicebridge").mkdir()
    (tmp_path / "voicebridge" / "automation.py").write_text("tick = 1\n")
    mention = tmp_path / "tests" / "test_mention.py"
    mention.write_text("def test_x():\n    print('automation')\n")
    dispatched = tmp_path / "tests" / "test_dispatched.py"
    dispatched.write_text(
        "from voicebridge import schedules as SC\n"
        "def test_x():\n    SC.autofire(None)\n")

    row = {"id": "OC-TEST", "qubit_backend": "voicebridge/automation.py tick",
           "needs": "none", "note": ""}
    # A mere mention links by token but is NOT a production-dispatcher proof.
    assert rebuild._proves_caller(
        dict(row, proving_test="tests/test_mention.py")) is True
    assert rebuild._producing_dispatcher(
        dict(row, proving_test="tests/test_mention.py")) is False
    # A real dispatcher call (schedules.autofire) is accepted.
    assert rebuild._producing_dispatcher(
        dict(row, proving_test="tests/test_dispatched.py")) is True
    # An upgraded row with only a mention is downgraded by the verifier.
    upgraded = dict(row, proving_test="tests/test_mention.py",
                    status="IMPLEMENTED")
    rebuild._verify_row(upgraded, baseline_status="PARTIAL")
    assert upgraded["status"] == "UNVERIFIED"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
