"""Evidence invariants for every OPERATOR_ONLY gap-ledger row.

Owner decision 2026-09-13
(`docs/QUBIT_OWNER_AMENDMENT_2026-09-13_TERMINAL_STATUSES.md`): an
``OPERATOR_ONLY`` row is code-complete and hermetically tested, but execution
requires a genuine live prerequisite (credentials/hardware/owner). Each such
row must therefore carry, on disk:

- a proving test that exists (and, when given, the exact ``::node``);
- a valid live-prerequisite ``needs`` (``creds``/``hardware``/``owner``, never
  ``none``);
- a typed unavailable/refusal state in its ledger note, with the canonical
  typed code recorded in the operator runbook; and
- an entry in ``docs/OPERATOR_QUALIFICATION.md`` (and the terminal statuses in
  the 2026-09-13 amendment).

The row set is pinned: a new ``OPERATOR_ONLY`` row with no evidence fails, and
a negative check proves the qualification helper rejects fabricated rows.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

DOCS = REPO_ROOT / "docs"
RUNBOOK = DOCS / "OPERATOR_QUALIFICATION.md"
AMENDMENT = DOCS / "QUBIT_OWNER_AMENDMENT_2026-09-13_TERMINAL_STATUSES.md"
LEDGERS = (
    DOCS / "QUBIT_GAP_LEDGER.qubit.json",
    DOCS / "QUBIT_GAP_LEDGER.openclaw.json",
    DOCS / "QUBIT_GAP_LEDGER.hermes.json",
)

LIVE_NEEDS = {"creds", "hardware", "owner"}

_TYPED_CODE_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
_TYPED_STATE_RE = re.compile(
    r"typed|refus|unavail|disabled|no real|probes|gate|credential|fixture|"
    r"actuator",
    re.IGNORECASE,
)

# Canonical evidence for the 13 audited OPERATOR_ONLY rows. ``typed`` lists the
# production typed unavailable/refusal codes proven by the code path and its
# test; the runbook must record them.
EXPECTED = {
    "OC-13": {
        "source": "openclaw",
        "needs": "hardware",
        "proving_test": "tests/test_computer_use8tf.py",
        "typed": ("COMPUTER_USE_NO_DRIVER",),
    },
    "OC-14": {
        "source": "openclaw",
        "needs": "hardware",
        "proving_test": "tests/test_desktop_parity8u.py",
        "typed": ("ADAPTER_DISABLED",),
    },
    "OC-19": {
        "source": "openclaw",
        "needs": "creds",
        "proving_test": "tests/test_openclaw_closure_wave2_messaging.py",
        "typed": ("MSG_TRANSPORT_UNAVAILABLE", "MSG_NO_CREDENTIAL"),
    },
    "OC-48": {
        "source": "openclaw",
        "needs": "creds",
        "proving_test": "tests/test_openclaw_closure_wave2_webmedia.py",
        "typed": ("MEMORY_PROVIDER_UNAVAILABLE",),
    },
    "OC-51": {
        "source": "openclaw",
        "needs": "hardware",
        "proving_test": "tests/test_openclaw_closure_wave2_webmedia.py",
        "typed": ("LOGBOOK_CAPTURE_UNAVAILABLE",),
    },
    "OC-56": {
        "source": "openclaw",
        "needs": "hardware",
        "proving_test": "tests/test_openclaw_closure_wave2_runtime.py",
        "typed": ("NODE_CAPABILITY_UNAVAILABLE", "NODE_DISPATCH_REFUSED"),
    },
    "OC-D15": {
        "source": "openclaw",
        "needs": "creds",
        "proving_test": "tests/test_openclaw_closure_wave3.py",
        "typed": ("MSG_TRANSPORT_UNAVAILABLE",),
    },
    "OC-D16": {
        "source": "openclaw",
        "needs": "creds",
        "proving_test": "tests/test_openclaw_closure_wave3.py",
        "typed": ("MSG_NO_CREDENTIAL", "MSG_TRANSPORT_UNAVAILABLE"),
    },
    "computer_doctor": {
        "source": "qubit",
        "needs": "hardware",
        "proving_test":
            "tests/test_computer_use8tf.py::test_doctor_checklist_typed",
        "typed": ("DEGRADED", "MISSING"),
    },
    "computer_install_refusal": {
        "source": "qubit",
        "needs": "hardware",
        "proving_test":
            "tests/test_computer_use8tf.py::test_doctor_checklist_typed",
        "typed": ("COMPUTER_USE_NO_DRIVER",),
    },
    "computer_permissions": {
        "source": "qubit",
        "needs": "hardware",
        "proving_test":
            "tests/test_computer_use8tf.py::test_permissions_record",
        "typed": ("DISABLED",),
    },
}


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gap_ledger = _load_module("gap_ledger", "gap_ledger.py")


def _operator_rows() -> list:
    rows = []
    for path in LEDGERS:
        data = json.loads(path.read_text(encoding="utf-8"))
        for row in data["rows"]:
            if row.get("status") == "OPERATOR_ONLY":
                rows.append((path.name.split(".")[-2], row))
    return rows


def _proving_test_exists(proving: str) -> bool:
    if not proving:
        return False
    path, _, node = proving.partition("::")
    file = REPO_ROOT / path
    if not file.is_file():
        return False
    if node:
        text = file.read_text(encoding="utf-8")
        if not re.search(rf"^def {re.escape(node)}\b", text, re.M):
            return False
    return True


def _qualification_failures(row: dict, runbook: str, amendment: str,
                            canonical: dict | None = None) -> list:
    """Return the list of owner-criteria failures for one row.

    Used for real rows and for the negative check; a real row must return an
    empty list, a fabricated row must not.
    """
    failures = []
    if row.get("status") != "OPERATOR_ONLY":
        return ["status"]
    rid = row.get("id", "")
    if row.get("needs") not in LIVE_NEEDS:
        failures.append("needs")
    if not _proving_test_exists(row.get("proving_test", "")):
        failures.append("proving_test")
    note = row.get("note", "")
    codes = (canonical or {}).get(rid, {}).get("typed", ())
    note_typed = bool(_TYPED_CODE_RE.search(note)
                      or _TYPED_STATE_RE.search(note))
    runbook_has_code = bool(codes) and all(code in runbook for code in codes)
    if not (note_typed or runbook_has_code):
        failures.append("typed_code")
    if rid and rid not in runbook:
        failures.append("runbook")
    if "OPERATOR_ONLY" not in amendment:
        failures.append("amendment")
    return failures


def _runbook_text() -> str:
    assert RUNBOOK.is_file(), RUNBOOK
    return RUNBOOK.read_text(encoding="utf-8")


def _amendment_text() -> str:
    assert AMENDMENT.is_file(), AMENDMENT
    return AMENDMENT.read_text(encoding="utf-8")


def test_operator_row_set_is_the_audited_eleven() -> None:
    rows = _operator_rows()
    ids = sorted(row["id"] for _, row in rows)
    assert ids == sorted(EXPECTED), (ids, sorted(EXPECTED))
    assert len(rows) == 11


def test_operator_row_provenance_matches_ledger() -> None:
    for source, row in _operator_rows():
        expected = EXPECTED[row["id"]]
        assert source == expected["source"], (row["id"], source)
        assert row["needs"] == expected["needs"], (row["id"], row["needs"])
        assert row["proving_test"] == expected["proving_test"], row["id"]


def test_operator_rows_have_valid_live_prerequisite() -> None:
    for _, row in _operator_rows():
        assert row["needs"] in LIVE_NEEDS, (row["id"], row["needs"])
        assert row["needs"] != "none", row["id"]


def test_operator_rows_have_proving_test_on_disk() -> None:
    missing = []
    for _, row in _operator_rows():
        if not _proving_test_exists(row["proving_test"]):
            missing.append((row["id"], row["proving_test"]))
    assert missing == [], missing


def test_operator_rows_are_typed_and_documented() -> None:
    runbook = _runbook_text()
    amendment = _amendment_text()
    failures = {
        row["id"]: _qualification_failures(row, runbook, amendment, EXPECTED)
        for _, row in _operator_rows()
    }
    offenders = {rid: fails for rid, fails in failures.items() if fails}
    assert offenders == {}, offenders


def test_every_canonical_typed_code_is_in_the_runbook() -> None:
    runbook = _runbook_text()
    missing = {
        rid: [code for code in spec["typed"] if code not in runbook]
        for rid, spec in EXPECTED.items()
    }
    missing = {rid: codes for rid, codes in missing.items() if codes}
    assert missing == {}, missing


def test_every_operator_row_has_a_runbook_entry() -> None:
    runbook = _runbook_text()
    missing = [row["id"] for _, row in _operator_rows()
               if row["id"] not in runbook]
    assert missing == [], missing
    for rid in EXPECTED:
        assert f"`{rid}`" in runbook, rid


def test_runbook_summary_count_matches_ledger() -> None:
    runbook = _runbook_text()
    assert "| OPERATOR_ONLY rows (all three ledgers) | **11** |" in runbook


def test_amendment_records_terminal_status_decision() -> None:
    amendment = _amendment_text()
    for marker in (
        "SAFE_EQUIVALENT",
        "OPERATOR_ONLY",
        "QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md",
        "QUBIT_OWNER_AMENDMENT_2026-09-11_HERMES_RUNTIME.md",
        "Do not weaken safety",
        "not 100% complete",
        "independently verified",
    ):
        assert marker in amendment, marker


def test_ledger_reports_code_ready_but_not_complete() -> None:
    sections, missing, _ = gap_ledger.load_sections(DOCS)
    summary = gap_ledger.build_summary(sections, missing)
    assert summary["sections_missing"] == [], summary["sections_missing"]
    assert summary["open_rows"] == 0
    assert summary["code_ready"] is True
    assert summary["operator_qualification_rows"] == 11
    assert summary["complete"] is False


def test_negative_check_rejects_unevidenced_rows() -> None:
    runbook = _runbook_text()
    amendment = _amendment_text()
    good = {
        "id": "computer_doctor",
        "status": "OPERATOR_ONLY",
        "needs": "hardware",
        "proving_test":
            "tests/test_computer_use8tf.py::test_doctor_checklist_typed",
        "note": "Typed refusal: COMPUTER_USE_NO_DRIVER / desktop health probe.",
    }
    assert _qualification_failures(good, runbook, amendment, EXPECTED) == []

    bad_proving = dict(good, id="fake_proving",
                       proving_test="tests/does_not_exist.py")
    assert "proving_test" in _qualification_failures(
        bad_proving, runbook, amendment, EXPECTED)

    bad_needs = dict(good, id="fake_needs", needs="none")
    assert "needs" in _qualification_failures(
        bad_needs, runbook, amendment, EXPECTED)

    bad_typed = {
        "id": "fake_typed",
        "status": "OPERATOR_ONLY",
        "needs": "hardware",
        "proving_test":
            "tests/test_browser8tf.py::test_close_profile_redacts_name",
        "note": "the capability works and is available",
    }
    assert "typed_code" in _qualification_failures(
        bad_typed, runbook, amendment, EXPECTED)

    bad_runbook = dict(good, id="not_in_runbook_row")
    assert "runbook" in _qualification_failures(
        bad_runbook, runbook, amendment, EXPECTED)

    not_operator = dict(good, status="IMPLEMENTED")
    assert _qualification_failures(
        not_operator, runbook, amendment, EXPECTED) == ["status"]


def test_operator_row_node_reference_resolves() -> None:
    for _, row in _operator_rows():
        proving = row["proving_test"]
        if "::" not in proving:
            continue
        path, node = proving.split("::", 1)
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert re.search(rf"^def {re.escape(node)}\b", text, re.M), proving


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
