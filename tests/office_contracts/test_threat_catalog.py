"""P0-05 — the threat-test catalog is complete, referenced and honest.

The catalog is only useful if every row names a real test (for implemented
status) or an owned, expiring gap. This test reads the checked-in JSON and
verifies those invariants against the working tree.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CATALOG = REPO / "docs" / "QUBIT-OFFICE-THREAT-TESTS.json"

ASI_IDS = {f"ASI{index:02d}" for index in range(1, 11)}
GATE_IDS = {
    "GATE-IDENTITY-TENANT",
    "GATE-SECRET-LEAKAGE",
    "GATE-APPROVAL-CONFUSION",
    "GATE-MCP-OAUTH",
    "GATE-MCP-SCHEMA",
    "GATE-MCP-SSRF",
    "GATE-SANDBOX-ESCAPE",
    "GATE-MEMORY-POISON",
    "GATE-A2A-SPOOF",
    "GATE-KILL-SWITCH",
    "GATE-OBSERVABILITY-PII",
    "GATE-BACKUP-RESTORE",
    "GATE-ACCESSIBILITY",
    "GATE-PAID-ART-LEAKAGE",
}
REQUIRED_FIELDS = (
    "id",
    "threat",
    "required_control",
    "test_id",
    "test_path",
    "expected_denial",
    "runbook",
    "owner",
    "review_expiry",
    "status",
)
STATUSES = {"implemented", "planned"}


def _catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def _rows() -> list[dict]:
    return _catalog()["rows"]


def _nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def test_schema_and_unique_ids() -> None:
    catalog = _catalog()
    assert catalog["schema"] == "qubit-office-threat-tests/1"
    rows = catalog["rows"]
    assert rows
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids)), "duplicate catalog id"


def test_every_asi_id_is_present() -> None:
    ids = {row["id"] for row in _rows()}
    missing = ASI_IDS - ids
    assert not missing, f"missing OWASP ASI rows: {sorted(missing)}"


def test_every_mandatory_gate_is_present() -> None:
    ids = {row["id"] for row in _rows()}
    missing = GATE_IDS - ids
    assert not missing, f"missing mandatory gates: {sorted(missing)}"


@pytest.mark.parametrize("row", _rows(), ids=lambda row: row["id"])
def test_rows_carry_the_required_contract(row: dict) -> None:
    for field in REQUIRED_FIELDS:
        assert field in row, f"{row.get('id')}: missing {field}"
    for field in (
        "threat",
        "required_control",
        "test_id",
        "test_path",
        "expected_denial",
        "runbook",
        "owner",
    ):
        assert _nonempty(row[field]), f"{row['id']}: {field} must be non-empty text"
    assert row["status"] in STATUSES, f"{row['id']}: unknown status"
    expiry = dt.date.fromisoformat(row["review_expiry"])
    assert expiry > dt.date.today(), f"{row['id']}: review_expiry is not in the future"


@pytest.mark.parametrize("row", _rows(), ids=lambda row: row["id"])
def test_implemented_rows_reference_real_tests(row: dict) -> None:
    if row["status"] != "implemented":
        return
    path = REPO / row["test_path"]
    assert path.is_file(), f"{row['id']}: missing test file {row['test_path']}"
    leaf = row["test_id"].split("::")[-1]
    body = path.read_text(encoding="utf-8")
    assert f"def {leaf}(" in body, (
        f"{row['id']}: {row['test_id']} not found in {row['test_path']}"
    )


@pytest.mark.parametrize("row", _rows(), ids=lambda row: row["id"])
def test_planned_rows_keep_an_owner_and_expiry(row: dict) -> None:
    path = REPO / row["test_path"]
    if row["status"] == "planned":
        # A planned gap is allowed to name a test that does not exist yet, but
        # it must keep an owner and a future review expiry so it cannot vanish.
        assert _nonempty(row["owner"])
        assert dt.date.fromisoformat(row["review_expiry"]) > dt.date.today()
    else:
        assert path.is_file(), f"{row['id']}: implemented test path missing"


def test_every_referenced_path_is_inside_the_repository() -> None:
    root = REPO.resolve()
    for row in _rows():
        path = (REPO / row["test_path"]).resolve()
        assert path == root or root in path.parents, (
            f"{row['id']}: test_path escapes the repository"
        )
