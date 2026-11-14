"""P0-04 — the approved dependency lock refuses unsafe reuse.

Hermetic: the real lock is read from the checkout and mutated copies are written
under ``tmp_path``. The validator is exercised as a subprocess so the test pins
the documented CLI contract (exit 0 pass / 1 violation / 2 usage or I/O), not
an internal helper.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LOCK = REPO / "docs" / "QUBIT-OFFICE-DEPENDENCY-LOCK.json"
SCRIPT = REPO / "scripts" / "validate_dependency_ledger.py"


def _load() -> dict:
    return json.loads(LOCK.read_text(encoding="utf-8"))


def _row(lock: dict, row_id: str) -> dict:
    for row in lock["dependencies"]:
        if row["id"] == row_id:
            return row
    raise AssertionError(f"missing fixture row {row_id!r}")


def _run(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--lock", str(path), "--json"],
        cwd=REPO,
        text=True,
        capture_output=True,
        timeout=60,
    )


def _mutated(tmp_path: Path, mutate) -> Path:
    lock = copy.deepcopy(_load())
    mutate(lock)
    path = tmp_path / "lock.json"
    path.write_text(json.dumps(lock), encoding="utf-8")
    return path


def _assert_violation(result: subprocess.CompletedProcess[str], code: str) -> None:
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is False
    codes = {item["code"] for item in report["violations"]}
    assert code in codes, report


# --- the checked-in lock is valid ---------------------------------------
def test_real_lock_passes() -> None:
    result = _run(LOCK)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["schema"] == "qubit-office-dependency-lock/1"
    assert report["checked"] == len(_load()["dependencies"])
    assert report["violations"] == []


# --- a concept-only license cannot become imported code ------------------
def test_concept_row_cannot_claim_imported(tmp_path: Path) -> None:
    def mutate(lock: dict) -> None:
        _row(lock, "agents-office")["reuse_status"] = "imported"

    _assert_violation(_run(_mutated(tmp_path, mutate)), "CONCEPT_ONLY_LICENSE")


# --- an absent license cannot become approved or imported ----------------
def test_absent_license_cannot_be_approved(tmp_path: Path) -> None:
    def mutate(lock: dict) -> None:
        _row(lock, "agent-monitor")["reuse_status"] = "approved"

    _assert_violation(_run(_mutated(tmp_path, mutate)), "UNAPPROVED_REUSE")


def test_plan_recorded_license_cannot_be_imported(tmp_path: Path) -> None:
    def mutate(lock: dict) -> None:
        _row(lock, "canopy")["reuse_status"] = "imported"

    _assert_violation(_run(_mutated(tmp_path, mutate)), "UNAPPROVED_REUSE")


# --- paid art must stay local -------------------------------------------
def test_paid_assets_cannot_allow_redistribution(tmp_path: Path) -> None:
    def mutate(lock: dict) -> None:
        lock["paid_assets_local_only"]["redistribution_allowed"] = True

    _assert_violation(_run(_mutated(tmp_path, mutate)), "PAID_REDISTRIBUTION")


# --- required review fields ---------------------------------------------
def test_missing_reviewer_is_refused(tmp_path: Path) -> None:
    def mutate(lock: dict) -> None:
        del _row(lock, "habblaud")["reviewer"]

    result = _run(_mutated(tmp_path, mutate))
    _assert_violation(result, "MISSING_FIELD")
    report = json.loads(result.stdout)
    assert any(
        "reviewer" in item["detail"] for item in report["violations"]
    ), report


def test_duplicate_ids_are_refused(tmp_path: Path) -> None:
    def mutate(lock: dict) -> None:
        lock["dependencies"].append(copy.deepcopy(_row(lock, "habblaud")))

    _assert_violation(_run(_mutated(tmp_path, mutate)), "DUPLICATE_ID")


def test_future_review_date_is_refused(tmp_path: Path) -> None:
    def mutate(lock: dict) -> None:
        _row(lock, "pixelfx")["reviewed_at"] = "2999-01-01"

    _assert_violation(_run(_mutated(tmp_path, mutate)), "FUTURE_REVIEW")


def test_missing_lock_is_a_usage_or_io_error(tmp_path: Path) -> None:
    result = _run(tmp_path / "absent.json")
    assert result.returncode == 2
    assert "not found" in result.stderr


@pytest.mark.parametrize("field", ["id", "name", "license_status", "reuse_status"])
def test_required_fields_are_not_optional(tmp_path: Path, field: str) -> None:
    def mutate(lock: dict) -> None:
        del _row(lock, "skyoffice")[field]

    _assert_violation(_run(_mutated(tmp_path, mutate)), "MISSING_FIELD")
