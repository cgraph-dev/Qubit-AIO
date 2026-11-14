#!/usr/bin/env python3
"""Independent verifier for P5-06 legal and P5-07 people/analytics.

WHY THIS EXISTS
---------------
The legal, people and analytics modules are not their own witnesses. This
script builds a throwaway Business OS database with the real services, then
re-reads SQLite and recomputes the claims:

* contract version hashes and clause diffs from stored clause JSON;
* signing is refused without an approved exact-payload approval, and a tampered
  binding is refused while the contract stays unsigned;
* obligation/renewal due rows and jurisdiction effective-date selection;
* tenant/entity separation;
* leave/revoke immediately revokes the principal, grants and linked agent;
* procurement separation of duties, spend cap and the double-entry receipt;
* KPI formulas are declarative, values match a raw SQL recount, every source
  row drills through, and stale/unknown are reported honestly.

It also proves the verifier can fail: a second fixture plants a wrong signed
version and a fabricated snapshot value, and the same checks must reject them.

Exit codes: ``0`` all checks pass, ``1`` a check failed, ``2`` usage/internal.

Hermetic: no network, no credentials, no live service, no real signature.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voicebridge.business import (  # noqa: E402
    AgentControlService,
    BusinessStore,
    identity,
)
from voicebridge.business import analytics, legal, people  # noqa: E402
from voicebridge.business import approvals as APPROVALS  # noqa: E402
from voicebridge.business.errors import BusinessError  # noqa: E402
from voicebridge.business.ids import canonical_json, fingerprint  # noqa: E402
from voicebridge.business.util import now_iso  # noqa: E402

SCHEMA = "qubit-office-legal-people-analytics-verification/1"


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _rows(path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _scalar(path: Path, sql: str, params: tuple = ()) -> Any:
    rows = _rows(path, sql, params)
    return rows[0][0] if rows else None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _raise_code(fn, *args, **kwargs) -> str | None:
    try:
        fn(*args, **kwargs)
    except BusinessError as exc:
        return exc.code
    return None


# -- fixture ---------------------------------------------------------------
class Fixture:
    def __init__(self, root: Path, label: str) -> None:
        self.root = root
        self.label = label
        self.store = BusinessStore(root / f"{label}.db")
        self.tenant = self.store.create_tenant(f"{label} Tenant")
        self.business = self.store.create_business(
            self.tenant.tenant_id, f"{label} Business", slug=label
        )
        self.other = self.store.create_tenant(f"{label} Other")
        self.personal = legal.create_entity(
            self.store,
            self.tenant.tenant_id,
            name="Owner Personal",
            jurisdiction="RO",
            kind="personal",
            currency="RON",
        )
        self.company = legal.create_entity(
            self.store,
            self.tenant.tenant_id,
            name="Acme SRL",
            jurisdiction="RO",
            kind="company",
            currency="RON",
        )
        legal.register_rule(
            self.store,
            self.tenant.tenant_id,
            jurisdiction="RO",
            rule_key="vat_rate",
            version=1,
            effective_from="2020-01-01T00:00:00Z",
            payload={"rate_percent": 19},
        )
        legal.register_rule(
            self.store,
            self.tenant.tenant_id,
            jurisdiction="RO",
            rule_key="vat_rate",
            version=2,
            effective_from="2030-01-01T00:00:00Z",
            payload={"rate_percent": 21},
        )
        self.contract = self._signed_contract()
        self.pending = self._pending_contract()
        self.obligation_due = legal.add_obligation(
            self.store,
            self.tenant.tenant_id,
            self.contract["contract_id"],
            kind="renewal",
            description="renew the lease",
            due_at="2020-01-01T00:00:00Z",
        )
        self.obligation_future = legal.add_obligation(
            self.store,
            self.tenant.tenant_id,
            self.contract["contract_id"],
            kind="payment",
            description="future rent",
            due_at="2999-01-01T00:00:00Z",
        )
        self.owner_principal = identity.create_principal(
            self.store, self.tenant.tenant_id, kind="owner", external_ref="human-1"
        )
        identity.grant(
            self.store,
            self.tenant.tenant_id,
            subject_id=self.owner_principal.principal_id,
            capability="office",
            resource="room:ops",
            action="enter",
        )
        self.agent_principal = identity.create_principal(
            self.store, self.tenant.tenant_id, kind="agent", external_ref="agent-1"
        )
        self.agent = AgentControlService(self.store).create(
            self.tenant.tenant_id,
            self.business.business_id,
            role_id="role-ops",
            department="Ops",
            display_name="Ops Agent",
            human_owner="alice",
            provider="openai",
            model="gpt-test",
            idempotency_key="agent-1",
            security_principal_id=self.agent_principal.principal_id,
        )[0]
        self.person = people.join(
            self.store,
            self.tenant.tenant_id,
            self.business.business_id,
            display_name="Alice",
            department="Ops",
            principal_id=self.owner_principal.principal_id,
            agent_id=self.agent.agent_id,
            idempotency_key="join-alice",
        )["person"]
        people.record_access(
            self.store,
            self.tenant.tenant_id,
            self.person["person_id"],
            principal_id=self.owner_principal.principal_id,
            scope="office",
        )
        people.record_access(
            self.store,
            self.tenant.tenant_id,
            self.person["person_id"],
            principal_id=self.agent_principal.principal_id,
            scope="agent",
        )
        self.self_approval_code = None
        self.cap_code = None
        self.tamper_code = None
        self._procurement()
        self.tamper_code = self._tamper_pending_signing()
        self._analytics()
        self._leave()

    # -- legal ---------------------------------------------------------
    def _signed_contract(self) -> dict[str, Any]:
        contract = legal.create_contract(
            self.store,
            self.tenant.tenant_id,
            business_id=self.business.business_id,
            entity_id=self.personal["entity_id"],
            title="Lease",
            counterparty="Landlord SRL",
        )
        legal.add_version(
            self.store,
            self.tenant.tenant_id,
            contract["contract_id"],
            clauses=[
                {"clause_id": "term", "title": "Term", "body": "12 months"},
                {"clause_id": "rent", "title": "Rent", "body": "1000 RON"},
            ],
        )
        version = legal.add_version(
            self.store,
            self.tenant.tenant_id,
            contract["contract_id"],
            clauses=[
                {"clause_id": "term", "title": "Term", "body": "24 months"},
                {"clause_id": "notice", "title": "Notice", "body": "30 days"},
            ],
        )
        request = legal.request_signing(
            self.store,
            self.tenant.tenant_id,
            contract["contract_id"],
            version["version_id"],
            requested_by="alice",
            idempotency_key="sign-a",
        )
        APPROVALS.decide(
            self.store,
            tenant_id=self.tenant.tenant_id,
            approval_id=request["approval_id"],
            decision="approved",
            decided_by="boss",
        )
        legal.record_signing(
            self.store,
            self.tenant.tenant_id,
            contract["contract_id"],
            request["approval_id"],
            signed_by="clerk",
        )
        return legal.get_contract(
            self.store, self.tenant.tenant_id, contract["contract_id"]
        )

    def _pending_contract(self) -> dict[str, Any]:
        contract = legal.create_contract(
            self.store,
            self.tenant.tenant_id,
            business_id=self.business.business_id,
            entity_id=self.company["entity_id"],
            title="Supply",
            counterparty="Supplier SRL",
        )
        version = legal.add_version(
            self.store,
            self.tenant.tenant_id,
            contract["contract_id"],
            clauses=[{"clause_id": "price", "title": "Price", "body": "100 RON"}],
        )
        request = legal.request_signing(
            self.store,
            self.tenant.tenant_id,
            contract["contract_id"],
            version["version_id"],
            requested_by="alice",
            idempotency_key="sign-b",
        )
        APPROVALS.decide(
            self.store,
            tenant_id=self.tenant.tenant_id,
            approval_id=request["approval_id"],
            decision="approved",
            decided_by="boss",
        )
        return {
            "contract": legal.get_contract(
                self.store, self.tenant.tenant_id, contract["contract_id"]
            ),
            "version": version,
            "approval_id": request["approval_id"],
            "payload_hash": request["payload_hash"],
        }

    def _tamper_pending_signing(self) -> str | None:
        conn = sqlite3.connect(self.store.path)
        try:
            conn.execute(
                "UPDATE bos_legal_signing SET payload_hash='deadbeef'"
                " WHERE tenant_id=? AND approval_id=?",
                (self.tenant.tenant_id, self.pending["approval_id"]),
            )
            conn.commit()
        finally:
            conn.close()
        return _raise_code(
            legal.record_signing,
            self.store,
            self.tenant.tenant_id,
            self.pending["contract"]["contract_id"],
            self.pending["approval_id"],
            signed_by="clerk",
        )

    # -- people --------------------------------------------------------
    def _procurement(self) -> None:
        self.store.set_budget(
            self.tenant.tenant_id,
            self.business.business_id,
            currency="USD",
            period=now_iso()[:7],
            limit_minor=10_000,
        )
        self.purchase = people.request_purchase(
            self.store,
            self.tenant.tenant_id,
            self.business.business_id,
            vendor_name="Paper Co",
            purpose="paper",
            amount_minor=5_000,
            requester="alice",
            idempotency_key="buy-1",
        )
        self.self_approval_code = _raise_code(
            people.approve_purchase,
            self.store,
            self.tenant.tenant_id,
            self.purchase["request_id"],
            decided_by="alice",
        )
        people.approve_purchase(
            self.store,
            self.tenant.tenant_id,
            self.purchase["request_id"],
            decided_by="boss",
        )
        self.receipt = people.record_receipt(
            self.store,
            self.tenant.tenant_id,
            self.purchase["request_id"],
            receipt_name="paper.txt",
            receipt_body="PAID",
        )
        over = people.request_purchase(
            self.store,
            self.tenant.tenant_id,
            self.business.business_id,
            vendor_name="Paper Co",
            purpose="over cap",
            amount_minor=6_000,
            requester="alice",
            idempotency_key="buy-cap",
        )
        self.cap_code = _raise_code(
            people.approve_purchase,
            self.store,
            self.tenant.tenant_id,
            over["request_id"],
            decided_by="boss",
        )
        self.over_request = over

    def _leave(self) -> None:
        people.move(
            self.store,
            self.tenant.tenant_id,
            self.person["person_id"],
            department="Finance",
            title="Analyst",
        )
        people.move(
            self.store,
            self.tenant.tenant_id,
            self.person["person_id"],
            department="Legal",
        )
        self.left = people.leave(
            self.store, self.tenant.tenant_id, self.person["person_id"], reason="resigned"
        )

    # -- analytics -----------------------------------------------------
    def _analytics(self) -> None:
        for title in ("done-1", "done-2", "open-1"):
            task = self.store.create_task(
                self.tenant.tenant_id, self.business.business_id, title
            )
            conn = sqlite3.connect(self.store.path)
            try:
                conn.execute(
                    "UPDATE bos_task SET status=? WHERE tenant_id=? AND task_id=?",
                    (
                        "done" if title.startswith("done") else "open",
                        self.tenant.tenant_id,
                        task.task_id,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        old = _iso(datetime.now(timezone.utc) - timedelta(hours=2))
        old_task = self.store.create_task(
            self.tenant.tenant_id, self.business.business_id, "old-done"
        )
        conn = sqlite3.connect(self.store.path)
        try:
            conn.execute(
                "UPDATE bos_task SET status='done', created_at=?, updated_at=?"
                " WHERE tenant_id=? AND task_id=?",
                (old, old, self.tenant.tenant_id, old_task.task_id),
            )
            conn.commit()
        finally:
            conn.close()
        self.metric = analytics.create_contract(
            self.store,
            self.tenant.tenant_id,
            name="Done tasks",
            formula={
                "op": "count",
                "table": "bos_task",
                "filter": {"status": "done"},
            },
            owner="alice",
            business_id=self.business.business_id,
            attribution_window_seconds=7200,
            refresh_cadence_seconds=60,
            acceptable_staleness_seconds=3600,
            target=2.0,
            anomaly_threshold=1.0,
            idempotency_key="kpi-1",
        )
        self.metric_result = analytics.compute(
            self.store, self.tenant.tenant_id, self.metric["metric_id"]
        )
        self.snapshot = analytics.record_snapshot(
            self.store,
            self.tenant.tenant_id,
            self.metric["metric_id"],
            result=self.metric_result,
        )
        self.stale_metric = analytics.create_contract(
            self.store,
            self.tenant.tenant_id,
            name="Old work",
            formula={
                "op": "count",
                "table": "bos_task",
                "filter": {"status": "done", "title": "old-done"},
            },
            owner="alice",
            business_id=self.business.business_id,
            attribution_window_seconds=10800,
            refresh_cadence_seconds=60,
            acceptable_staleness_seconds=60,
            idempotency_key="kpi-stale",
        )
        self.stale_result = analytics.compute(
            self.store, self.tenant.tenant_id, self.stale_metric["metric_id"]
        )
        self.empty_metric = analytics.create_contract(
            self.store,
            self.tenant.tenant_id,
            name="Nothing",
            formula={
                "op": "count",
                "table": "bos_task",
                "filter": {"status": "missing"},
            },
            owner="alice",
            business_id=self.business.business_id,
            attribution_window_seconds=7200,
            refresh_cadence_seconds=60,
            acceptable_staleness_seconds=3600,
            idempotency_key="kpi-empty",
        )
        self.empty_result = analytics.compute(
            self.store, self.tenant.tenant_id, self.empty_metric["metric_id"]
        )
        self.bad_formula_code = _raise_code(
            analytics.validate_formula, {"op": "drop", "table": "bos_task"}
        )


# -- checks ----------------------------------------------------------------
def _legal_checks(fx: Fixture) -> list[dict[str, Any]]:
    path = fx.store.path
    tenant_id = fx.tenant.tenant_id
    checks: list[dict[str, Any]] = []

    entities = {
        row["entity_id"]: row["kind"]
        for row in _rows(path, "SELECT * FROM bos_legal_entity WHERE tenant_id=?", (tenant_id,))
    }
    contracts = _rows(
        path, "SELECT * FROM bos_legal_contract WHERE tenant_id=?", (tenant_id,)
    )
    separated = all(
        entities.get(row["entity_id"]) == ("personal" if row["contract_id"] == fx.contract["contract_id"] else "company")
        or row["contract_id"] == fx.pending["contract"]["contract_id"]
        for row in contracts
    )
    personal_ids = {
        row["contract_id"]
        for row in contracts
        if entities.get(row["entity_id"]) == "personal"
    }
    company_ids = {
        row["contract_id"]
        for row in contracts
        if entities.get(row["entity_id"]) == "company"
    }
    checks.append(
        _check(
            "entity_books_separated",
            separated and fx.contract["contract_id"] in personal_ids
            and fx.pending["contract"]["contract_id"] in company_ids,
            f"personal={len(personal_ids)} company={len(company_ids)}",
        )
    )

    versions = _rows(
        path,
        "SELECT * FROM bos_legal_contract_version WHERE tenant_id=?",
        (tenant_id,),
    )
    hash_ok = True
    for row in versions:
        clauses = json.loads(row["clauses_json"])
        if _sha(canonical_json(clauses)) != row["content_sha256"]:
            hash_ok = False
    checks.append(
        _check("contract_version_hashes", hash_ok, f"{len(versions)} versions")
    )

    by_contract: dict[str, dict[int, list[dict[str, Any]]]] = {}
    for row in versions:
        by_contract.setdefault(row["contract_id"], {})[int(row["version"])] = json.loads(
            row["clauses_json"]
        )
    diffs = by_contract.get(fx.contract["contract_id"], {})
    first = {item["clause_id"]: item for item in diffs.get(1, [])}
    second = {item["clause_id"]: item for item in diffs.get(2, [])}
    expected_added = sorted(second.keys() - first.keys())
    expected_removed = sorted(first.keys() - second.keys())
    expected_changed = sorted(
        key for key in first.keys() & second.keys() if first[key] != second[key]
    )
    actual = legal.diff_versions(
        fx.store, tenant_id, fx.contract["contract_id"], 1, 2
    )
    diff_ok = (
        [item["clause_id"] for item in actual["added"]] == expected_added
        and [item["clause_id"] for item in actual["removed"]] == expected_removed
        and [item["clause_id"] for item in actual["changed"]] == expected_changed
    )
    checks.append(
        _check(
            "clause_diff_matches_raw",
            diff_ok,
            f"added={expected_added} removed={expected_removed} changed={expected_changed}",
        )
    )

    other_contract = legal.create_contract(
        fx.store,
        tenant_id,
        business_id=fx.business.business_id,
        entity_id=fx.personal["entity_id"],
        title="Unsigned",
        counterparty="X",
    )
    legal.add_version(
        fx.store,
        tenant_id,
        other_contract["contract_id"],
        clauses=[{"clause_id": "a", "title": "A", "body": "a"}],
    )
    unsigned_code = _raise_code(
        legal.record_signing,
        fx.store,
        tenant_id,
        other_contract["contract_id"],
        "apr_none",
        signed_by="clerk",
    )
    checks.append(
        _check(
            "signing_without_approval_refused",
            unsigned_code == legal.LEGAL_SIGNING_APPROVAL_REQUIRED_CODE,
            f"code={unsigned_code}",
        )
    )

    signed_rows = _rows(
        path,
        "SELECT * FROM bos_legal_signing WHERE tenant_id=? AND status='signed'",
        (tenant_id,),
    )
    signed_ok = len(signed_rows) == 1
    payload_ok = True
    for row in signed_rows:
        approval = _rows(
            path,
            "SELECT * FROM bos_approval WHERE tenant_id=? AND approval_id=?",
            (tenant_id, row["approval_id"]),
        )[0]
        expected = legal.signing_payload(
            fx.store, tenant_id, row["contract_id"], row["version_id"]
        )
        expected_hash = fingerprint(expected)
        if not (
            row["payload_hash"] == expected_hash
            and approval["request_hash"] == expected_hash
            and json.loads(row["payload_json"]) == expected
        ):
            payload_ok = False
    checks.append(
        _check(
            "signing_payload_hash_exact",
            signed_ok and payload_ok,
            f"signed={len(signed_rows)} exact={payload_ok}",
        )
    )

    pending = legal.get_contract(
        fx.store, tenant_id, fx.pending["contract"]["contract_id"]
    )
    checks.append(
        _check(
            "tampered_signing_refused",
            fx.tamper_code == legal.LEGAL_SIGNING_APPROVAL_MISMATCH_CODE
            and pending["status"] == "pending_signature",
            f"code={fx.tamper_code} status={pending['status']}",
        )
    )

    signed_contract = legal.get_contract(
        fx.store, tenant_id, fx.contract["contract_id"]
    )
    checks.append(
        _check(
            "signed_contract_matches_version",
            signed_contract["status"] == "signed"
            and signed_contract["signed_version_id"] is not None,
            f"status={signed_contract['status']}",
        )
    )

    raw_due = [
        row["obligation_id"]
        for row in _rows(
            path,
            "SELECT * FROM bos_legal_obligation WHERE tenant_id=? AND status='open'"
            " ORDER BY due_at, obligation_id",
            (tenant_id,),
        )
        if row["due_at"] <= now_iso()
    ]
    module_due = [
        row["obligation_id"]
        for row in legal.due_obligations(fx.store, tenant_id)
    ]
    checks.append(
        _check(
            "obligation_due_matches_raw",
            raw_due == module_due == [fx.obligation_due["obligation_id"]],
            f"raw={raw_due} module={module_due}",
        )
    )

    now_version = legal.evaluate_rule(
        fx.store, tenant_id, jurisdiction="RO", rule_key="vat_rate"
    )
    future_version = legal.evaluate_rule(
        fx.store,
        tenant_id,
        jurisdiction="RO",
        rule_key="vat_rate",
        as_of=datetime(2031, 1, 1, tzinfo=timezone.utc).timestamp(),
    )
    checks.append(
        _check(
            "jurisdiction_effective_date",
            now_version["version"] == 1
            and now_version["payload"]["rate_percent"] == 19
            and future_version["version"] == 2
            and future_version["payload"]["rate_percent"] == 21,
            f"now=v{now_version['version']} future=v{future_version['version']}",
        )
    )
    unknown_code = _raise_code(
        legal.evaluate_rule,
        fx.store,
        tenant_id,
        jurisdiction="XX",
        rule_key="vat_rate",
    )
    checks.append(
        _check(
            "unknown_jurisdiction_refused",
            unknown_code == legal.LEGAL_JURISDICTION_UNKNOWN_CODE,
            f"code={unknown_code}",
        )
    )

    other_visible = False
    try:
        legal.get_contract(fx.store, fx.other.tenant_id, fx.contract["contract_id"])
        other_visible = True
    except BusinessError:
        other_visible = False
    checks.append(
        _check(
            "legal_tenant_isolation",
            not other_visible
            and legal.list_contracts(fx.store, fx.other.tenant_id) == [],
            f"other_can_read={other_visible}",
        )
    )
    return checks


def _people_checks(fx: Fixture) -> list[dict[str, Any]]:
    path = fx.store.path
    tenant_id = fx.tenant.tenant_id
    checks: list[dict[str, Any]] = []

    principals = {
        row["principal_id"]: row["status"]
        for row in _rows(
            path,
            "SELECT * FROM bos_principal WHERE tenant_id=?",
            (tenant_id,),
        )
    }
    granted_ok = identity.evaluate(
        fx.store, tenant_id, fx.owner_principal.principal_id, "office", "room:ops", "enter"
    ).allowed
    checks.append(
        _check(
            "leave_revokes_principal_immediately",
            principals.get(fx.owner_principal.principal_id) == "revoked"
            and principals.get(fx.agent_principal.principal_id) == "revoked"
            and granted_ok is False,
            f"owner={principals.get(fx.owner_principal.principal_id)}"
            f" agent={principals.get(fx.agent_principal.principal_id)}"
            f" allowed={granted_ok}",
        )
    )

    agent_row = _rows(
        path,
        "SELECT * FROM bos_agent WHERE tenant_id=? AND agent_id=?",
        (tenant_id, fx.agent.agent_id),
    )[0]
    checks.append(
        _check(
            "leave_revokes_linked_agent",
            agent_row["status"] == "revoked" and fx.left["agent_status"] == "revoked",
            f"raw={agent_row['status']} reported={fx.left['agent_status']}",
        )
    )

    assignments = _rows(
        path,
        "SELECT * FROM bos_people_assignment WHERE tenant_id=? ORDER BY created_at,"
        " assignment_id",
        (tenant_id,),
    )
    active = [row for row in assignments if row["status"] == "active"]
    checks.append(
        _check(
            "assignment_history_preserved",
            [row["department"] for row in assignments] == ["Ops", "Finance", "Legal"]
            and not active
            and all(row["effective_to"] is not None for row in assignments),
            f"departments={[row['department'] for row in assignments]} active={len(active)}",
        )
    )

    approval = _rows(
        path,
        "SELECT * FROM bos_approval WHERE tenant_id=? AND action='procurement.purchase'"
        " ORDER BY created_at, approval_id",
        (tenant_id,),
    )[0]
    checks.append(
        _check(
            "procurement_separation_of_duties",
            fx.self_approval_code == people.PEOPLE_SELF_APPROVAL_CODE
            and approval["decided_by"] == "boss"
            and approval["requested_by"] == "alice",
            f"code={fx.self_approval_code} decided_by={approval['decided_by']}",
        )
    )

    ledger_rows = _rows(
        path,
        "SELECT * FROM bos_ledger_entry WHERE tenant_id=? AND account IN"
        " ('expense:procurement','cash') ORDER BY created_at, entry_id",
        (tenant_id,),
    )
    ledger_ok = len(ledger_rows) == 2 and {
        (row["direction"], int(row["amount_minor"]), row["account"])
        for row in ledger_rows
    } == {("debit", 5_000, "expense:procurement"), ("credit", 5_000, "cash")}
    artifact = _rows(
        path,
        "SELECT * FROM bos_artifact WHERE tenant_id=? AND artifact_id=?",
        (tenant_id, fx.receipt["receipt_artifact_id"]),
    )
    checks.append(
        _check(
            "procurement_receipt_double_entry",
            ledger_ok
            and bool(artifact)
            and artifact[0]["kind"] == "procurement_receipt"
            and fx.receipt["request"]["status"] == "completed",
            f"ledger={len(ledger_rows)} artifact={bool(artifact)}"
            f" status={fx.receipt['request']['status']}",
        )
    )

    over_status = people.get_purchase(
        fx.store, tenant_id, fx.over_request["request_id"]
    )["status"]
    checks.append(
        _check(
            "spend_cap_refused",
            fx.cap_code == people.PEOPLE_SPEND_CAP_EXCEEDED_CODE
            and over_status == "blocked_spend_cap",
            f"code={fx.cap_code} status={over_status}",
        )
    )

    other_visible = False
    try:
        people.get_person(fx.store, fx.other.tenant_id, fx.person["person_id"])
        other_visible = True
    except BusinessError:
        other_visible = False
    checks.append(
        _check(
            "people_tenant_isolation",
            not other_visible
            and people.list_people(fx.store, fx.other.tenant_id) == [],
            f"other_can_read={other_visible}",
        )
    )
    return checks


def _analytics_checks(fx: Fixture) -> list[dict[str, Any]]:
    path = fx.store.path
    tenant_id = fx.tenant.tenant_id
    checks: list[dict[str, Any]] = []
    raw_contract = _rows(
        path,
        "SELECT * FROM bos_metric_contract WHERE tenant_id=? AND metric_id=?",
        (tenant_id, fx.metric["metric_id"]),
    )[0]
    fields_ok = (
        json.loads(raw_contract["formula_json"])
        == {
            "op": "count",
            "table": "bos_task",
            "field": None,
            "filter": {"status": "done"},
        }
        and json.loads(raw_contract["source_tables_json"]) == ["bos_task"]
        and raw_contract["timezone"] == "UTC"
        and int(raw_contract["refresh_cadence_seconds"]) == 60
        and int(raw_contract["acceptable_staleness_seconds"]) == 3600
        and float(raw_contract["target"]) == 2.0
        and float(raw_contract["anomaly_threshold"]) == 1.0
    )
    checks.append(
        _check(
            "metric_contract_fields",
            fields_ok,
            f"formula={raw_contract['formula_json']}",
        )
    )

    raw_done = _scalar(
        path,
        "SELECT COUNT(*) FROM bos_task WHERE tenant_id=? AND business_id=?"
        " AND status='done' AND created_at >= ?",
        (
            tenant_id,
            fx.business.business_id,
            fx.metric_result["window"]["start"],
        ),
    )
    result = fx.metric_result
    checks.append(
        _check(
            "metric_value_matches_raw",
            result["status"] == "fresh"
            and result["value"] == float(raw_done)
            and result["source_count"] == int(raw_done),
            f"value={result['value']} raw={raw_done} status={result['status']}",
        )
    )

    drill_ok = True
    for source in result["source_rows"]:
        row = analytics.drill(
            fx.store,
            tenant_id,
            fx.metric["metric_id"],
            table=source["table"],
            row_id=source["row_id"],
        )
        raw = _rows(
            path,
            "SELECT * FROM bos_task WHERE tenant_id=? AND task_id=?",
            (tenant_id, source["row_id"]),
        )
        if not raw or raw[0]["status"] != "done" or row["row"]["status"] != "done":
            drill_ok = False
    checks.append(
        _check(
            "metric_drill_through",
            drill_ok and len(result["source_rows"]) == int(raw_done),
            f"rows={len(result['source_rows'])}",
        )
    )

    checks.append(
        _check(
            "metric_stale_reported",
            fx.stale_result["status"] == "stale"
            and fx.stale_result["value"] is not None,
            f"status={fx.stale_result['status']} value={fx.stale_result['value']}",
        )
    )
    checks.append(
        _check(
            "metric_unknown_not_fabricated",
            fx.empty_result["status"] == "unknown"
            and fx.empty_result["value"] is None,
            f"status={fx.empty_result['status']} value={fx.empty_result['value']}",
        )
    )
    checks.append(
        _check(
            "metric_formula_validation",
            fx.bad_formula_code == analytics.METRIC_FORMULA_INVALID_CODE,
            f"code={fx.bad_formula_code}",
        )
    )

    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE bos_metric_snapshot SET value=999 WHERE tenant_id=?"
            " AND snapshot_id=?",
            (tenant_id, fx.snapshot["snapshot_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    checks.append(
        _check(
            "planted_snapshot_value_detected",
            analytics.snapshot_matches(
                fx.store, tenant_id, fx.metric["metric_id"]
            )
            is False,
            "fresh recomputation disagrees with the planted 999",
        )
    )
    return checks


def _planted_checks(root: Path) -> list[dict[str, Any]]:
    """Prove the verifier rejects a wrong signed version."""
    fx = Fixture(root, "planted")
    path = fx.store.path
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE bos_legal_contract SET signed_version_id='cver_wrong'"
            " WHERE tenant_id=? AND contract_id=?",
            (fx.tenant.tenant_id, fx.contract["contract_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    signed = legal.get_contract(
        fx.store, fx.tenant.tenant_id, fx.contract["contract_id"]
    )
    versions = {
        row["version_id"]
        for row in _rows(
            path,
            "SELECT version_id FROM bos_legal_contract_version WHERE tenant_id=?"
            " AND contract_id=?",
            (fx.tenant.tenant_id, fx.contract["contract_id"]),
        )
    }
    detected = signed["signed_version_id"] not in versions
    return [
        _check(
            "planted_wrong_signed_version_detected",
            detected,
            f"signed_version_id={signed['signed_version_id']}",
        )
    ]


def _run(work: Path) -> dict[str, Any]:
    good = Fixture(work, "good")
    checks = (
        _legal_checks(good)
        + _people_checks(good)
        + _analytics_checks(good)
        + _planted_checks(work / "planted")
    )
    failed = [item["name"] for item in checks if not item["ok"]]
    return {
        "schema": SCHEMA,
        "ok": not failed,
        "checks_total": len(checks),
        "checks_failed": len(failed),
        "failed": failed,
        "checks": checks,
        "limitations": [
            "hermetic throwaway SQLite; no live service, credential or network",
            "signing is an in-system attestation, never a real or cryptographic signature",
            "jurisdiction rules are stored data selected by effective date; this is not legal or tax advice",
            "KPI freshness is measured against stored row timestamps on this host",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify P5-06 legal and P5-07 people/analytics contracts."
    )
    parser.add_argument("--json", action="store_true", help="emit a JSON report")
    args = parser.parse_args(argv)
    work = Path(tempfile.mkdtemp(prefix="qubit-p5-verify-"))
    try:
        report = _run(work)
    except Exception as exc:  # pragma: no cover - defensive boundary
        report = {
            "schema": SCHEMA,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"ERROR {report['error']}")
        return 2
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for item in report["checks"]:
            print(("PASS " if item["ok"] else "FAIL ") + item["name"] + " — " + item["detail"])
        print(
            f"{report['checks_total'] - report['checks_failed']}/"
            f"{report['checks_total']} checks passed"
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
