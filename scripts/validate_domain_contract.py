#!/usr/bin/env python3
"""Validate the frozen v1 Office domain/projection contract.

The JSON schemas in ``voicebridge/business/contracts/`` are the frozen v1
wire/projection contract. They are a projection of the real Business OS
vocabulary in ``voicebridge/business/{schema.py,models.py,agents.py,ids.py}``;
the SQLite schema remains authoritative for storage. This tool does not carry a
second domain authority: every fixture uses the same identifiers, statuses and
money rules the product already enforces.

Usage:
    python scripts/validate_domain_contract.py [--json] [--schema-dir DIR]

Exit codes:
    0  all schemas valid and every positive/negative case behaved as required
    1  a schema, positive fixture or named negative case failed
    2  usage error or unreadable/invalid schema files
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError
except Exception as exc:  # pragma: no cover - environment failure, typed exit
    print(f"validate_domain_contract: jsonschema unavailable: {exc}", file=sys.stderr)
    raise SystemExit(2)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA_DIR = REPO_ROOT / "voicebridge" / "business" / "contracts"
SCHEMA_FILES = (
    "domain.schema.json",
    "event.schema.json",
    "agent.schema.json",
    "session.schema.json",
    "run.schema.json",
)

# -- real-vocabulary fixtures ------------------------------------------------
# 26 Crockford base32 characters, matching voicebridge.business.ids.new_id.
TEN = "ten_00000000000000000000000000"
OWN = "own_00000000000000000000000000"
ENT = "ent_00000000000000000000000000"
BIZ = "biz_00000000000000000000000000"
DEP = "dep_00000000000000000000000000"
ROOM = "rom_00000000000000000000000000"
CELL = "cel_00000000000000000000000000"
DESK = "dsk_00000000000000000000000000"
SEAT = f"{ROOM}:{CELL}:1"
ART = "art_00000000000000000000000000"
APV = "apv_00000000000000000000000000"
LED = "led_00000000000000000000000000"
CAP = "cap_00000000000000000000000000"
GRT = "grt_00000000000000000000000000"
MEMORY = "mem_00000000000000000000000000"
AUD = "aud_00000000000000000000000000"
SES = "ses_00000000000000000000000000"
RUN = "run_00000000000000000000000000"
TSK = "tsk_00000000000000000000000000"
WSP = "wsp_00000000000000000000000000"
CHK = "chk_00000000000000000000000000"
LEA = "lea_00000000000000000000000000"
AGT = "agt_00000000000000000000000000"
ROLE = "rol_00000000000000000000000000"
PRN = "prn_00000000000000000000000000"
EVT = "evt_00000000000000000000000000"
MAN = "man_00000000000000000000000000"
SHA = "0" * 64
TS = "2026-01-01T00:00:00Z"

OWNER = {
    "owner_id": OWN,
    "auth_subject": "local:owner",
    "display_name": "Owner",
    "status": "active",
    "created_at": TS,
    "retired_at": None,
}
TENANT = {
    "tenant_id": TEN,
    "owner_id": OWN,
    "name": "Personal",
    "status": "active",
    "data_residency": "local",
    "retention_policy": "standard",
    "created_at": TS,
}
LEGAL_ENTITY = {
    "legal_entity_id": ENT,
    "tenant_id": TEN,
    "name": "Owner Studio Ltd",
    "jurisdiction": "RO",
    "registration_id": None,
    "tax_id": None,
    "currency": "USD",
    "status": "active",
}
BUSINESS = {
    "business_id": BIZ,
    "tenant_id": TEN,
    "legal_entity_id": ENT,
    "name": "Qubit Studio",
    "slug": "qubit-studio",
    "status": "active",
    "currency": "USD",
    "created_at": TS,
    "updated_at": TS,
    "metadata": {},
}
DEPARTMENT = {
    "department_id": DEP,
    "tenant_id": TEN,
    "business_ids": [BIZ],
    "name": "Trading",
    "slug": "trading",
    "wing": "north",
    "floor": 1,
    "status": "active",
}
ROOM_REC = {
    "room_id": ROOM,
    "tenant_id": TEN,
    "department_id": DEP,
    "name": "Trading Floor",
    "archetype": "open-plan",
    "capacity": 24,
    "privacy_class": "internal",
    "status": "active",
    "capability_manifest_id": MAN,
    "memory_scope_id": MEMORY,
    "map_cell_ids": [CELL],
}
CELL_REC = {
    "cell_id": CELL,
    "room_id": ROOM,
    "origin": {"x": 0, "y": 0},
    "cols": 16,
    "rows": 12,
    "desk_ids": [DESK],
}
DESK_REC = {
    "desk_id": DESK,
    "room_id": ROOM,
    "cell_id": CELL,
    "seat_id": SEAT,
    "local_seat_id": 1,
}
SEAT_REC = {
    "seat_id": SEAT,
    "room_id": ROOM,
    "cell_id": CELL,
    "desk_id": DESK,
    "local_seat_id": 1,
    "agent_id": AGT,
}
ARTIFACT = {
    "artifact_id": ART,
    "tenant_id": TEN,
    "business_id": BIZ,
    "job_id": None,
    "task_id": TSK,
    "run_id": RUN,
    "kind": "report",
    "name": "weekly-report.md",
    "media_type": "text/markdown",
    "sha256": SHA,
    "size_bytes": 123,
    "storage_ref": "file:///tmp/weekly-report.md",
    "provenance": {"producer": AGT},
    "access_policy": "tenant",
    "immutable": True,
    "created_at": TS,
}
APPROVAL = {
    "approval_id": APV,
    "tenant_id": TEN,
    "business_id": BIZ,
    "job_id": None,
    "run_id": RUN,
    "action": "publish",
    "status": "pending",
    "requested_by": AGT,
    "decided_by": None,
    "reason": "",
    "idempotency_key": "idem-00000001",
    "request_hash": SHA,
    "canonical_payload_hash": SHA,
    "policy_version": "v1.0",
    "payload": {"target": "site"},
    "created_at": TS,
    "decided_at": None,
    "expires_at": 1767225600,
}
LEDGER = {
    "entry_id": LED,
    "tenant_id": TEN,
    "business_id": BIZ,
    "job_id": None,
    "account": "cash",
    "direction": "debit",
    "amount_minor": 1500,
    "currency": "USD",
    "idempotency_key": "idem-00000002",
    "request_hash": SHA,
    "memo": "test entry",
    "status": "committed",
    "provider_request_id": None,
    "artifact_id": None,
    "created_at": TS,
}
CAPABILITY = {
    "capability_id": CAP,
    "category": "read",
    "resource": "crm.contacts",
    "action": "read",
    "risk": "low",
    "required_scopes": ["crm.read"],
    "approval_policy": "none",
    "budget_minor": None,
    "rate_limit": "60/min",
    "health": "healthy",
    "version": "1.0.0",
}
GRANT = {
    "grant_id": GRT,
    "tenant_id": TEN,
    "subject_id": AGT,
    "capability_id": CAP,
    "resource": "crm.contacts",
    "action": "read",
    "data_class": "internal",
    "environment": "production",
    "expires_at": None,
    "approval_id": None,
}
MEMORY_REC = {
    "memory_id": MEMORY,
    "tenant_id": TEN,
    "scope": "agent",
    "source": "session",
    "provenance": {"agent_id": AGT},
    "confidence": 0.9,
    "visibility": "private",
    "retention": "standard",
    "created_at": TS,
    "updated_at": TS,
    "supersedes": None,
    "content_hash": SHA,
    "deleted": False,
}
AUDIT = {
    "audit_id": AUD,
    "tenant_id": TEN,
    "business_id": BIZ,
    "actor": OWN,
    "agent_id": AGT,
    "run_id": None,
    "policy_revision": 1,
    "action": "approval.requested",
    "target": {"type": "approval", "id": APV},
    "decision": "allow",
    "before_hash": None,
    "after_hash": SHA,
    "chain_hash": SHA,
    "created_at": TS,
}
EVENT = {
    "event_id": EVT,
    "aggregate_id": APV,
    "sequence": 1,
    "type": "approval.requested",
    "correlation_id": EVT,
    "causation_id": None,
    "idempotency_key": "idem-00000003",
    "tenant_id": TEN,
    "actor": AGT,
    "occurred_at": TS,
    "payload_hash": SHA,
    "payload_ref": "events/0001.json",
    "policy_version": "v1.0",
    "retention": {"class": "standard", "until": None},
    "tombstone": False,
}
AGENT_PROFILE = {
    "agent_id": AGT,
    "owner_id": OWN,
    "tenant_id": TEN,
    "business_ids": [BIZ],
    "department_id": DEP,
    "room_id": ROOM,
    "desk_id": DESK,
    "role_id": ROLE,
    "security_principal_id": PRN,
    "display_name": "Atlas",
    "status": "active",
    "policy_revision": 1,
    "autonomy_ceiling": "L3",
    "model_policy": {
        "provider": "opencode-go",
        "model": "deepseek-v4.1-flash",
        "reasoning_effort": "medium",
        "fallback_provider": None,
        "fallback_model": None,
        "temperature": 0.2,
        "max_tokens": 4096,
    },
    "capability_grants": [GRT],
    "skills": ["research"],
    "mcp_servers": [
        {"name": "graft", "version": "0.12.0", "schema_hash": SHA, "enabled": True}
    ],
    "memory_scopes": ["agent", "session"],
    "secret_refs": ["env:OPENCODE_GO_API_KEY"],
    "budgets": {
        "currency": "USD",
        "period": "monthly",
        "period_limit_minor": 5000,
        "per_turn_limit_minor": 100,
    },
    "created_at": TS,
    "updated_at": TS,
}
SESSION = {
    "session_id": SES,
    "agent_id": AGT,
    "owner_id": OWN,
    "participants": [
        {"principal_type": "owner", "principal_id": OWN, "role": "owner"},
        {"principal_type": "agent", "principal_id": AGT, "role": "executor"},
    ],
    "visibility": "participants",
    "default_memory_scopes": ["agent", "session"],
    "default_workspace_id": WSP,
    "status": "active",
    "last_checkpoint_id": CHK,
    "current_policy_revision": 1,
    "created_at": TS,
    "updated_at": TS,
}
RUN_REC = {
    "run_id": RUN,
    "session_id": SES,
    "task_id": TSK,
    "initiator": {"type": "agent", "id": AGT},
    "parent_run_id": None,
    "model": "deepseek-v4.1-flash",
    "provider": "opencode-go",
    "prompt_revision": 1,
    "capability_snapshot_id": CAP,
    "budget_snapshot": {"currency": "USD", "limit_minor": 100, "spent_minor": 0},
    "lease_id": LEA,
    "status": "executing",
    "created_at": TS,
    "updated_at": TS,
}
TASK_REC = {
    "task_id": TSK,
    "objective": "Draft the weekly report",
    "acceptance_criteria": ["report artifact exists"],
    "priority": "normal",
    "owner": OWN,
    "assignee": AGT,
    "dependencies": [],
    "required_capabilities": [CAP],
    "data_classification": "internal",
    "budget": {"currency": "USD", "limit_minor": 100},
    "deadline": None,
    "status": "open",
    "artifact_ids": [ART],
}
WORKSPACE = {
    "workspace_id": WSP,
    "owner_scope": OWN,
    "task_id": TSK,
    "session_id": SES,
    "repository_ref": "git@local:qubit",
    "worktree_ref": "/tmp/ws",
    "storage_class": "persistent",
    "access_policy": "tenant",
    "current_revision": "rev-1",
    "artifact_manifest": [{"artifact_id": ART, "sha256": SHA}],
    "retention_policy": "standard",
}
CHECKPOINT = {
    "checkpoint_id": CHK,
    "run_id": RUN,
    "sequence": 1,
    "state_summary": "run started",
    "pending_actions": [],
    "tool_receipts": [],
    "artifact_ids": [],
    "memory_writes": [],
    "approvals": [],
    "workspace_revision": "rev-1",
    "policy_revision": 1,
    "safe_resume_point": True,
}
LEASE = {
    "lease_id": LEA,
    "resource": f"workspace:{WSP}",
    "holder": AGT,
    "expires_at": TS,
    "heartbeat_at": TS,
    "fencing_token": "1",
}

# (case id, schema file, def or None, fixture)
POSITIVE: list[tuple[str, str, str | None, dict[str, Any]]] = [
    ("owner", "domain.schema.json", "Owner", OWNER),
    ("tenant", "domain.schema.json", "Tenant", TENANT),
    ("legal_entity", "domain.schema.json", "LegalEntity", LEGAL_ENTITY),
    ("business", "domain.schema.json", "Business", BUSINESS),
    ("department", "domain.schema.json", "Department", DEPARTMENT),
    ("room", "domain.schema.json", "Room", ROOM_REC),
    ("cell", "domain.schema.json", "Cell", CELL_REC),
    ("desk", "domain.schema.json", "Desk", DESK_REC),
    ("seat", "domain.schema.json", "Seat", SEAT_REC),
    ("artifact", "domain.schema.json", "Artifact", ARTIFACT),
    ("approval", "domain.schema.json", "Approval", APPROVAL),
    ("ledger_entry", "domain.schema.json", "LedgerEntry", LEDGER),
    ("capability", "domain.schema.json", "Capability", CAPABILITY),
    ("grant", "domain.schema.json", "Grant", GRANT),
    ("memory_record", "domain.schema.json", "MemoryRecord", MEMORY_REC),
    ("audit_event", "domain.schema.json", "AuditEvent", AUDIT),
    ("event_envelope", "event.schema.json", None, EVENT),
    ("agent_profile", "agent.schema.json", None, AGENT_PROFILE),
    ("session", "session.schema.json", None, SESSION),
    ("run", "run.schema.json", "Run", RUN_REC),
    ("task", "run.schema.json", "Task", TASK_REC),
    ("workspace", "run.schema.json", "Workspace", WORKSPACE),
    ("checkpoint", "run.schema.json", "Checkpoint", CHECKPOINT),
    ("lease", "run.schema.json", "Lease", LEASE),
]


def _without(fixture: dict[str, Any], *keys: str) -> dict[str, Any]:
    out = dict(fixture)
    for key in keys:
        out.pop(key, None)
    return out


def _negative_cases() -> list[dict[str, Any]]:
    missing_tenant = _without(BUSINESS, "tenant_id")
    cell_bad_cols = {**CELL_REC, "cols": 8}
    cell_bad_rows = {**CELL_REC, "rows": 10}
    run_bad_status = {**RUN_REC, "status": "running"}
    approval_missing_hash = _without(APPROVAL, "canonical_payload_hash")
    event_missing_idem = _without(EVENT, "idempotency_key")
    agent_bad_status = {**AGENT_PROFILE, "status": "running"}
    seat_bad_id = {**SEAT_REC, "seat_id": SEAT_REC["room_id"]}
    return [
        {
            "id": "missing_tenant_id",
            "file": "domain.schema.json",
            "def": "Business",
            "fixture": missing_tenant,
            "expected_property": "tenant_id",
            "reason": "every business record must carry tenant_id",
        },
        {
            "id": "cell_cols_not_16",
            "file": "domain.schema.json",
            "def": "Cell",
            "fixture": cell_bad_cols,
            "expected_path": ["cols"],
            "reason": "a cell is exactly 16 columns",
        },
        {
            "id": "cell_rows_not_12",
            "file": "domain.schema.json",
            "def": "Cell",
            "fixture": cell_bad_rows,
            "expected_path": ["rows"],
            "reason": "a cell is exactly 12 rows",
        },
        {
            "id": "run_status_invalid",
            "file": "run.schema.json",
            "def": "Run",
            "fixture": run_bad_status,
            "expected_path": ["status"],
            "reason": "run status must be one of the canonical ten states",
        },
        {
            "id": "approval_missing_canonical_payload_hash",
            "file": "domain.schema.json",
            "def": "Approval",
            "fixture": approval_missing_hash,
            "expected_property": "canonical_payload_hash",
            "reason": "an approval binds an exact payload hash",
        },
        {
            "id": "event_missing_idempotency_key",
            "file": "event.schema.json",
            "def": None,
            "fixture": event_missing_idem,
            "expected_property": "idempotency_key",
            "reason": "an event envelope requires an idempotency key",
        },
        {
            "id": "agent_status_invalid",
            "file": "agent.schema.json",
            "def": None,
            "fixture": agent_bad_status,
            "expected_path": ["status"],
            "reason": "agent status must match AGENT_STATUSES",
        },
        {
            "id": "seat_id_bad_format",
            "file": "domain.schema.json",
            "def": "Seat",
            "fixture": seat_bad_id,
            "expected_path": ["seat_id"],
            "reason": "a seat id must be room:cell:local, not a duplicated bare id",
        },
    ]


# -- validation core ---------------------------------------------------------
def load_schemas(schema_dir: Path) -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for name in SCHEMA_FILES:
        path = schema_dir / name
        with path.open("r", encoding="utf-8") as handle:
            schemas[name] = json.load(handle)
    return schemas


def _schema_for(
    schemas: dict[str, dict[str, Any]], file: str, def_name: str | None
) -> dict[str, Any]:
    doc = schemas[file]
    if def_name is None:
        return doc
    wrapper = dict(doc)
    wrapper["$ref"] = f"#/$defs/{def_name}"
    return wrapper


def _errors(
    schemas: dict[str, dict[str, Any]], file: str, def_name: str | None, fixture: Any
) -> list[Any]:
    validator = Draft202012Validator(_schema_for(schemas, file, def_name))
    return sorted(validator.iter_errors(fixture), key=lambda e: list(e.absolute_path))


def _matches(error: Any, case: dict[str, Any]) -> bool:
    expected_path = case.get("expected_path")
    if expected_path is not None:
        actual = list(error.absolute_path)
        if len(actual) < len(expected_path) or actual[-len(expected_path):] != expected_path:
            return False
    expected_property = case.get("expected_property")
    if expected_property is not None:
        if error.validator != "required" or expected_property not in error.message:
            return False
    return True


def run(schema_dir: Path, self_test_negative: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(case_id: str, status: str, detail: str) -> None:
        checks.append({"id": case_id, "status": status, "detail": detail})

    try:
        schemas = load_schemas(schema_dir)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"validate_domain_contract: cannot load schemas: {exc}") from exc

    for name in SCHEMA_FILES:
        add(f"schema_present:{name}", "pass", str(schema_dir / name))
    for name in SCHEMA_FILES:
        try:
            Draft202012Validator.check_schema(schemas[name])
        except SchemaError as exc:
            add(f"schema_valid:{name}", "fail", exc.message)
        else:
            add(f"schema_valid:{name}", "pass", "Draft 2020-12 schema is valid")

    positives = list(POSITIVE)
    if self_test_negative:
        # Deliberately inject a fixture that is invalid but is required to
        # validate. This exercises the exit-1 path without touching real files.
        positives.append(
            (
                "self_test_negative",
                "domain.schema.json",
                "Tenant",
                {"tenant_id": TEN},
            )
        )

    for case_id, file, def_name, fixture in positives:
        errs = _errors(schemas, file, def_name, fixture)
        if errs:
            first = errs[0]
            add(
                f"positive:{case_id}",
                "fail",
                f"{list(first.absolute_path)}: {first.message}",
            )
        else:
            add(f"positive:{case_id}", "pass", "fixture validates")

    for case in _negative_cases():
        errs = _errors(schemas, case["file"], case["def"], case["fixture"])
        if not errs:
            add(f"negative:{case['id']}", "fail", "invalid fixture was accepted")
            continue
        matched = next((err for err in errs if _matches(err, case)), None)
        if matched is None:
            add(
                f"negative:{case['id']}",
                "fail",
                "rejected, but not for the named reason: "
                + "; ".join(f"{list(e.absolute_path)}: {e.message}" for e in errs[:3]),
            )
        else:
            add(
                f"negative:{case['id']}",
                "pass",
                f"rejected at {list(matched.absolute_path)}: {matched.message}",
            )

    failed = [c for c in checks if c["status"] != "pass"]
    return {
        "ok": not failed,
        "schema_dir": str(schema_dir),
        "checks": checks,
        "summary": {
            "total": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
        },
    }


def _emit(report: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    for check in report["checks"]:
        mark = "PASS" if check["status"] == "pass" else "FAIL"
        print(f"{mark} {check['id']}: {check['detail']}")
    summary = report["summary"]
    print(
        f"{summary['passed']}/{summary['total']} checks passed, "
        f"{summary['failed']} failed"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the frozen v1 Office domain/projection contract."
    )
    parser.add_argument(
        "--json", action="store_true", help="emit a machine-readable report"
    )
    parser.add_argument(
        "--schema-dir",
        default=str(DEFAULT_SCHEMA_DIR),
        help="directory holding the five contract schemas",
    )
    parser.add_argument(
        "--self-test-negative",
        action="store_true",
        help="inject a knowingly invalid fixture that must fail (proves exit 1)",
    )
    args = parser.parse_args(argv)

    schema_dir = Path(args.schema_dir)
    if not schema_dir.is_dir():
        print(
            f"validate_domain_contract: schema directory not found: {schema_dir}",
            file=sys.stderr,
        )
        return 2
    try:
        report = run(schema_dir, self_test_negative=args.self_test_negative)
    except SystemExit as exc:
        # run() raises SystemExit with a message for unreadable/corrupt schemas.
        print(str(exc), file=sys.stderr)
        return 2
    _emit(report, args.json)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
