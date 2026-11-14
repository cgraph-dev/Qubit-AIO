"""P0-02: the frozen v1 Office domain/projection contract.

These tests pin the five JSON schemas against the real Business OS vocabulary
(``voicebridge/business/``) and prove that the validator rejects each required
negative case for the named reason. They are hermetic: no network, no live
service, no live Qubit home.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "voicebridge" / "business" / "contracts"
SCRIPT = ROOT / "scripts" / "validate_domain_contract.py"
SCHEMA_FILES = (
    "domain.schema.json",
    "event.schema.json",
    "agent.schema.json",
    "session.schema.json",
    "run.schema.json",
)
RUN_STATUSES = {
    "queued",
    "leased",
    "executing",
    "waiting",
    "approval",
    "checkpointed",
    "completed",
    "failed",
    "cancelled",
    "unknown",
}


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_domain_contract", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VDC = _load_validator()


def _schema(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_contract_directory_holds_exactly_the_five_schemas():
    files = {p.name for p in CONTRACTS.glob("*.json")}
    assert files == set(SCHEMA_FILES)


def test_every_schema_is_valid_draft_2020_12():
    for name in SCHEMA_FILES:
        doc = _schema(name)
        assert doc["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(doc)


def test_domain_schema_defines_every_required_record():
    defs = set(_schema("domain.schema.json")["$defs"])
    assert {
        "Owner",
        "Tenant",
        "LegalEntity",
        "Business",
        "Department",
        "Room",
        "Cell",
        "Desk",
        "Seat",
        "Artifact",
        "Approval",
        "LedgerEntry",
        "Capability",
        "Grant",
        "MemoryRecord",
        "AuditEvent",
    } <= defs


def test_run_schema_defines_every_required_record():
    defs = set(_schema("run.schema.json")["$defs"])
    assert {"Run", "Task", "Workspace", "Checkpoint", "Lease"} <= defs


def test_run_status_enum_is_exactly_the_canonical_ten():
    run = _schema("run.schema.json")["$defs"]["Run"]
    assert set(run["properties"]["status"]["enum"]) == RUN_STATUSES


def test_agent_vocabulary_matches_the_real_registry():
    from voicebridge.business.agents import AGENT_STATUSES, AUTONOMY_LEVELS

    agent = _schema("agent.schema.json")
    assert tuple(agent["properties"]["status"]["enum"]) == AGENT_STATUSES
    assert tuple(agent["properties"]["autonomy_ceiling"]["enum"]) == AUTONOMY_LEVELS


def test_cell_is_fixed_16_by_12():
    cell = _schema("domain.schema.json")["$defs"]["Cell"]["properties"]
    assert cell["cols"]["const"] == 16
    assert cell["rows"]["const"] == 12


def test_money_is_integer_minor_units_and_hashes_are_sha256():
    defs = _schema("domain.schema.json")["$defs"]
    assert defs["LedgerEntry"]["properties"]["amount_minor"]["type"] == "integer"
    assert defs["LedgerEntry"]["properties"]["amount_minor"]["minimum"] == 0
    sha = defs["Sha256"]
    assert sha["pattern"] == "^[0-9a-f]{64}$"
    assert defs["Approval"]["properties"]["canonical_payload_hash"]["$ref"].endswith("/Sha256")


def test_agent_desk_and_room_are_documented_as_presentation_only():
    agent = _schema("agent.schema.json")
    comment = agent.get("$comment", "")
    assert "presentation" in comment.lower()
    assert "never authorization" in comment.lower()
    for field in ("room_id", "desk_id"):
        note = agent["properties"][field].get("$comment", "")
        assert "presentation" in note.lower()
        assert "never an authorization input" in note.lower()


def test_positive_fixtures_all_validate():
    schemas = VDC.load_schemas(CONTRACTS)
    failures = []
    for case_id, file, def_name, fixture in VDC.POSITIVE:
        errors = VDC._errors(schemas, file, def_name, fixture)
        if errors:
            failures.append((case_id, list(errors[0].absolute_path), errors[0].message))
    assert failures == []


def test_each_negative_fixture_is_rejected_for_its_named_reason():
    schemas = VDC.load_schemas(CONTRACTS)
    cases = VDC._negative_cases()
    assert {case["id"] for case in cases} >= {
        "missing_tenant_id",
        "cell_cols_not_16",
        "cell_rows_not_12",
        "run_status_invalid",
        "approval_missing_canonical_payload_hash",
        "event_missing_idempotency_key",
        "agent_status_invalid",
        "seat_id_bad_format",
    }
    for case in cases:
        errors = VDC._errors(schemas, case["file"], case["def"], case["fixture"])
        assert errors, f"{case['id']} was accepted"
        assert any(VDC._matches(err, case) for err in errors), (
            f"{case['id']} rejected for the wrong reason: "
            f"{[(list(e.absolute_path), e.message) for e in errors]}"
        )


def test_cli_reports_pass_with_exit_zero():
    result = _run_cli("--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["summary"]["failed"] == 0
    assert report["summary"]["total"] >= 40


def test_cli_self_test_negative_exits_one():
    result = _run_cli("--self-test-negative", "--json")
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is False
    failed_ids = {c["id"] for c in report["checks"] if c["status"] != "pass"}
    assert failed_ids == {"positive:self_test_negative"}


def test_cli_bad_argument_exits_two():
    result = _run_cli("--definitely-not-a-flag")
    assert result.returncode == 2


def test_cli_missing_schema_directory_exits_two(tmp_path: Path):
    result = _run_cli("--schema-dir", str(tmp_path / "absent"))
    assert result.returncode == 2


def test_cli_corrupt_schema_exits_two(tmp_path: Path):
    for name in SCHEMA_FILES:
        (tmp_path / name).write_text("{ not json", encoding="utf-8")
    result = _run_cli("--schema-dir", str(tmp_path), "--json")
    assert result.returncode == 2
    assert "cannot load schemas" in result.stderr
