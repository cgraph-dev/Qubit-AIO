#!/usr/bin/env python3
"""Independent verifier for Phase 5 finance (P5-05) and factory (P5-08).

WHY THIS EXISTS
---------------
A finance module grading its own homework is not evidence. This script builds a
real throwaway Business OS store, exercises the actual ``finance`` and
``business_factory`` modules, and then re-reads the SQLite file directly:

* every posting's debit total equals its credit total, and a planted
  single-sided row makes the same predicate report ``False``;
* duplicate postings/intents are one effect, not two;
* an unknown payment stays unknown until an explicit resolution with provider
  evidence, and a narrative cannot move it;
* refunds/chargebacks post balanced entries;
* two legal entities keep separate ledger namespaces;
* cloning copies structure and never grants/budgets/agents/credentials/ledger
  history, and each implicit-inheritance request is a typed refusal;
* every factory creation leaves an audit row and an event.

Hermetic by default: no network, no live service, no credential, no money
movement. Exit codes: ``0`` all checks pass, ``1`` a check failed, ``2``
usage/internal error.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voicebridge.business import approvals, finance  # noqa: E402
from voicebridge.business import business_factory as factory  # noqa: E402
from voicebridge.business import identity  # noqa: E402
from voicebridge.business.errors import BusinessError  # noqa: E402
from voicebridge.business.store import BusinessStore  # noqa: E402

OWNER = "owner:verify"


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _rows(store: BusinessStore, sql: str, params: tuple = ()) -> list[Any]:
    conn = sqlite3.connect(store.path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _ledger_balanced(
    store: BusinessStore, tenant_id: str, business_id: str | None = None
) -> tuple[bool, str]:
    sql = (
        "SELECT direction, COALESCE(SUM(amount_minor), 0)"
        " FROM bos_ledger_entry WHERE tenant_id=?"
    )
    params: list[Any] = [tenant_id]
    if business_id is not None:
        sql += " AND business_id=?"
        params.append(business_id)
    sql += " GROUP BY direction"
    totals = {row[0]: int(row[1]) for row in _rows(store, sql, tuple(params))}
    debit = totals.get("debit", 0)
    credit = totals.get("credit", 0)
    return debit == credit, f"debit={debit} credit={credit}"


def _latest_payment_state(
    store: BusinessStore, tenant_id: str, intent_id: str
) -> str:
    mapping = {
        "finance.payment.created": "pending",
        "finance.payment.sent": "sent",
        "finance.payment.unknown": "unknown",
        "finance.payment.acked": "acked",
        "finance.payment.completed": "completed",
        "finance.payment.failed": "failed",
        "finance.payment.compensated": "compensated",
    }
    rows = _rows(
        store,
        "SELECT type, payload_json FROM bos_event WHERE tenant_id=?"
        " AND aggregate_id=? ORDER BY seq",
        (tenant_id, intent_id),
    )
    state = ""
    for event_type, payload in rows:
        if event_type in mapping:
            state = mapping[event_type]
        elif event_type == "finance.payment.resolved":
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                payload = {}
            state = str(payload.get("outcome") or state)
    return state


def _count(store: BusinessStore, table: str, where: str = "", params: tuple = ()) -> int:
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return int(_rows(store, sql, params)[0][0])


def _approved_sent(store: BusinessStore, tenant_id: str, business_id: str, key: str):
    intent, _ = finance.create_payment_intent(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        provider="verify-provider",
        provider_idempotency_key=key,
        amount_minor=500,
        currency="USD",
        account="expense:supplies",
        actor=OWNER,
    )
    approvals.decide(
        store,
        tenant_id=tenant_id,
        approval_id=intent.approval_id,
        decision="approved",
        decided_by=OWNER,
    )
    return finance.execute_payment(
        store, tenant_id=tenant_id, intent_id=intent.intent_id, actor=OWNER
    )


def run(tmp: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    store = BusinessStore(tmp / "business.db")

    # --- the balance predicate can fail ---------------------------------
    planted = BusinessStore(tmp / "planted.db")
    tenant = planted.create_tenant("Planted")
    business = planted.create_business(tenant.tenant_id, "Planted Books", slug="p")
    with planted.transaction() as conn:
        conn.execute(
            "INSERT INTO bos_ledger_entry(entry_id, tenant_id, business_id,"
            " account, direction, amount_minor, currency, idempotency_key,"
            " request_hash, memo, created_at)"
            " VALUES('lgr_planted',?,?,'cash','debit',100,'USD','planted',"
            " 'hash','', '2026-11-01T00:00:00.000Z')",
            (tenant.tenant_id, business.business_id),
        )
    balanced, detail = _ledger_balanced(
        planted, tenant.tenant_id, business.business_id
    )
    checks.append(
        _check("balance_check_can_fail", not balanced, detail)
    )

    # --- personal books -------------------------------------------------
    personal = factory.create_personal_books(store, owner_name="Verify Owner")
    tenant_id = personal.tenant_id
    business_id = personal.business_id
    checks.append(
        _check(
            "personal_books_created",
            bool(personal.created.get("legal_entity"))
            and bool(personal.created.get("business")),
            json.dumps(personal.created, sort_keys=True),
        )
    )

    # --- double entry ---------------------------------------------------
    posting, created = finance.post(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        lines=[("expense:office", "debit", 1000), ("cash", "credit", 1000)],
        currency="USD",
        idempotency_key="verify-post-1",
    )
    balanced, detail = _ledger_balanced(store, tenant_id, business_id)
    checks.append(
        _check(
            "balanced_posting",
            created and posting.balanced and balanced,
            f"{detail} posting={posting.posting_id}",
        )
    )

    try:
        finance.post(
            store,
            tenant_id=tenant_id,
            business_id=business_id,
            lines=[("cash", "debit", 50)],
            currency="USD",
            idempotency_key="verify-unbalanced",
        )
        refused = False
        code = "no refusal"
    except BusinessError as exc:
        refused = exc.code == finance.UNBALANCED_CODE
        code = exc.code
    checks.append(
        _check("unbalanced_posting_refused", refused, code)
    )

    replay, recreated = finance.post(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        lines=[("expense:office", "debit", 1000), ("cash", "credit", 1000)],
        currency="USD",
        idempotency_key="verify-post-1",
    )
    checks.append(
        _check(
            "posting_idempotent",
            (not recreated) and replay.posting_id == posting.posting_id
            and _count(store, "bos_ledger_entry") == 2,
            f"posting={replay.posting_id} rows={_count(store, 'bos_ledger_entry')}",
        )
    )

    # --- payment intent idempotency ------------------------------------
    first, first_created = finance.create_payment_intent(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        provider="verify-provider",
        provider_idempotency_key="verify-dupe",
        amount_minor=500,
        currency="USD",
        account="expense:supplies",
        actor=OWNER,
    )
    second, second_created = finance.create_payment_intent(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        provider="verify-provider",
        provider_idempotency_key="verify-dupe",
        amount_minor=500,
        currency="USD",
        account="expense:supplies",
        actor=OWNER,
    )
    checks.append(
        _check(
            "payment_idempotent",
            first_created and not second_created
            and first.intent_id == second.intent_id
            and _count(
                store, "bos_event", "type='finance.payment.created'"
            ) == 1,
            f"intent={first.intent_id}",
        )
    )

    # --- approval required ---------------------------------------------
    try:
        finance.execute_payment(
            store, tenant_id=tenant_id, intent_id=first.intent_id, actor=OWNER
        )
        approval_refused = False
        approval_code = "no refusal"
    except BusinessError as exc:
        approval_refused = exc.code in (
            "BUSINESS_APPROVAL_REQUIRED",
            finance.PAYMENT_APPROVAL_REQUIRED_CODE,
        )
        approval_code = exc.code
    checks.append(
        _check("payment_requires_approval", approval_refused, approval_code)
    )

    # --- narrative cannot mark paid ------------------------------------
    approvals.decide(
        store,
        tenant_id=tenant_id,
        approval_id=first.approval_id,
        decision="approved",
        decided_by=OWNER,
    )
    sent = finance.execute_payment(
        store, tenant_id=tenant_id, intent_id=first.intent_id, actor=OWNER
    )
    finance.mark_payment_unknown(
        store, tenant_id=tenant_id, intent_id=sent.intent_id, reason="timeout"
    )
    finance.record_narrative(
        store,
        tenant_id=tenant_id,
        intent_id=sent.intent_id,
        text="provider says paid",
        actor=OWNER,
    )
    narrative_state = _latest_payment_state(store, tenant_id, sent.intent_id)
    checks.append(
        _check(
            "narrative_is_not_truth",
            narrative_state == finance.STATE_UNKNOWN,
            narrative_state,
        )
    )

    # --- explicit unknown resolutions ---------------------------------
    resolution_rows = []
    for outcome, with_receipt in (
        ("sent", True),
        ("compensated", True),
        ("failed", False),
    ):
        intent = _approved_sent(
            store, tenant_id, business_id, f"verify-resolve-{outcome}"
        )
        finance.mark_payment_unknown(
            store,
            tenant_id=tenant_id,
            intent_id=intent.intent_id,
            reason="provider outcome unknown",
        )
        receipt = None
        if with_receipt:
            receipt = {
                "provider": "verify-provider",
                "provider_ref": f"verify-ch-{outcome}",
                "amount_minor": 500,
                "currency": "USD",
                "status": "acked",
            }
        resolved = finance.resolve_unknown(
            store,
            tenant_id=tenant_id,
            intent_id=intent.intent_id,
            outcome=outcome,
            receipt=receipt,
            actor=OWNER,
        )
        resolved_state = _latest_payment_state(store, tenant_id, intent.intent_id)
        resolution_rows.append(
            {
                "outcome": outcome,
                "module": resolved.state,
                "stored": resolved_state,
            }
        )
    checks.append(
        _check(
            "unknown_resolution_explicit",
            all(
                row["module"] == row["outcome"]
                and row["stored"] == row["outcome"]
                for row in resolution_rows
            ),
            json.dumps(resolution_rows, sort_keys=True),
        )
    )

    try:
        pending = _approved_sent(
            store, tenant_id, business_id, "verify-no-receipt"
        )
        finance.mark_payment_unknown(
            store,
            tenant_id=tenant_id,
            intent_id=pending.intent_id,
            reason="timeout",
        )
        finance.resolve_unknown(
            store,
            tenant_id=tenant_id,
            intent_id=pending.intent_id,
            outcome="sent",
            actor=OWNER,
        )
        receipt_refused = False
        receipt_code = "no refusal"
    except BusinessError as exc:
        receipt_refused = exc.code == finance.NARRATIVE_NOT_TRUTH_CODE
        receipt_code = exc.code
    checks.append(
        _check(
            "financial_resolution_needs_receipt",
            receipt_refused,
            receipt_code,
        )
    )

    # --- complete a payment and reconcile ------------------------------
    settled = _approved_sent(store, tenant_id, business_id, "verify-settle")
    acked = finance.acknowledge_payment(
        store,
        tenant_id=tenant_id,
        intent_id=settled.intent_id,
        receipt={
            "provider_ref": "verify-ch-settle",
            "amount_minor": 500,
            "currency": "USD",
            "status": "acked",
        },
        actor=OWNER,
    )
    completed = finance.complete_payment(
        store, tenant_id=tenant_id, intent_id=acked.intent_id, actor=OWNER
    )
    balanced, detail = _ledger_balanced(store, tenant_id, business_id)
    checks.append(
        _check(
            "payment_completion_posts_balance",
            completed.state == finance.STATE_COMPLETED and balanced,
            f"{detail} postings={list(completed.postings)}",
        )
    )

    # --- refunds / chargebacks ----------------------------------------
    refund_approval = finance.request_money_approval(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        action="finance.refund",
        target=completed.intent_id,
        amount_minor=200,
        currency="USD",
        requested_by=OWNER,
    )
    approvals.decide(
        store,
        tenant_id=tenant_id,
        approval_id=refund_approval.approval_id,
        decision="approved",
        decided_by=OWNER,
    )
    refund = finance.record_refund(
        store,
        tenant_id=tenant_id,
        intent_id=completed.intent_id,
        amount_minor=200,
        approval_id=refund_approval.approval_id,
        actor=OWNER,
        idempotency_key="verify-refund",
    )
    charge_approval = finance.request_money_approval(
        store,
        tenant_id=tenant_id,
        business_id=business_id,
        action="finance.chargeback",
        target=completed.intent_id,
        amount_minor=100,
        currency="USD",
        requested_by=OWNER,
    )
    approvals.decide(
        store,
        tenant_id=tenant_id,
        approval_id=charge_approval.approval_id,
        decision="approved",
        decided_by=OWNER,
    )
    chargeback = finance.record_chargeback(
        store,
        tenant_id=tenant_id,
        intent_id=completed.intent_id,
        amount_minor=100,
        approval_id=charge_approval.approval_id,
        actor=OWNER,
        idempotency_key="verify-chargeback",
    )
    balanced, detail = _ledger_balanced(store, tenant_id, business_id)
    checks.append(
        _check(
            "refund_chargeback_balance",
            refund.balanced and chargeback.balanced and balanced,
            detail,
        )
    )

    # --- reconciliation ------------------------------------------------
    report = finance.reconcile(store, tenant_id, business_id=business_id)
    checks.append(
        _check(
            "reconciliation_reports_truth",
            len(report["matched"]) >= 1
            and report["ledger_balanced"]
            and any(row["intent_id"] == completed.intent_id for row in report["matched"]),
            f"matched={len(report['matched'])} unknown={len(report['unknown'])}"
            f" mismatched={len(report['mismatched'])}",
        )
    )

    # --- entity isolation ---------------------------------------------
    entity_a = factory.create_legal_entity(store, tenant_id, name="Verify Alpha LLC")
    entity_b = factory.create_legal_entity(store, tenant_id, name="Verify Beta LLC")
    biz_a = factory.create_business(
        store,
        tenant_id,
        name="Alpha Books",
        slug="verify-alpha-books",
        legal_entity_id=entity_a.legal_entity_id,
    )
    biz_b = factory.create_business(
        store,
        tenant_id,
        name="Beta Books",
        slug="verify-beta-books",
        legal_entity_id=entity_b.legal_entity_id,
    )
    books_a = factory.entity_books(store, tenant_id, biz_a.business_id)
    books_b = factory.entity_books(store, tenant_id, biz_b.business_id)
    books_a.post(
        lines=[("cash", "debit", 300), ("revenue", "credit", 300)],
        currency="USD",
        idempotency_key="verify-alpha-sale",
    )
    books_b.post(
        lines=[("cash", "debit", 900), ("revenue", "credit", 900)],
        currency="USD",
        idempotency_key="verify-beta-sale",
    )
    alpha = books_a.summary()
    beta = books_b.summary()
    cross_isolated = (
        alpha["entry_count"] == 2
        and beta["entry_count"] == 2
        and alpha["debit_minor"] == 300
        and beta["debit_minor"] == 900
        and books_a.namespace != books_b.namespace
    )
    try:
        factory.entity_books(
            store,
            tenant_id,
            biz_a.business_id,
            legal_entity_id=entity_b.legal_entity_id,
        )
        mismatch_refused = False
        mismatch_code = "no refusal"
    except BusinessError as exc:
        mismatch_refused = exc.code == factory.FACTORY_ENTITY_MISMATCH_CODE
        mismatch_code = exc.code
    checks.append(
        _check(
            "entity_ledger_isolation",
            cross_isolated and mismatch_refused,
            f"alpha={alpha['entry_count']} beta={beta['entry_count']}"
            f" refusal={mismatch_code}",
        )
    )

    # --- clone has no inherited power ---------------------------------
    principal = identity.create_principal(
        store, tenant_id, kind="agent", external_ref="verify-agent"
    )
    role = identity.create_role(store, tenant_id, "verify-role")
    identity.grant(
        store,
        tenant_id,
        subject_id=principal.principal_id,
        capability="tool:read",
        resource="shared",
        action="read",
        role_id=role.role_id,
    )
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO bos_budget(budget_id, tenant_id, business_id, currency,"
            " period, limit_minor, created_at)"
            " VALUES('bgt_verify',?,?,?,?,?,?)",
            (
                tenant_id,
                biz_a.business_id,
                "USD",
                "2026-11",
                100000,
                "2026-11-01T00:00:00.000Z",
            ),
        )
        conn.execute(
            "INSERT INTO bos_agent(agent_id, tenant_id, business_id, role_id,"
            " department, display_name, human_owner, status, provider, model,"
            " reasoning_effort, max_tokens, schedule, timezone,"
            " max_concurrent_turns, permissions_json, safety_json,"
            " idempotency_key, request_hash, created_at, updated_at)"
            " VALUES('agt_verify',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                tenant_id,
                biz_a.business_id,
                "role_verify",
                "finance",
                "Verify Agent",
                OWNER,
                "active",
                "test",
                "test-model",
                "low",
                1024,
                "manual",
                "UTC",
                1,
                "{}",
                "{}",
                "verify-agent",
                "verify-hash",
                "2026-11-01T00:00:00.000Z",
                "2026-11-01T00:00:00.000Z",
            ),
        )
    grants_before = _count(store, "bos_grant")
    principals_before = _count(store, "bos_principal")
    clone = factory.clone_business(
        store,
        tenant_id,
        biz_a.business_id,
        name="Alpha Clone",
        slug="verify-alpha-clone",
    )
    clone_business = store.get_business(tenant_id, clone.business_id)
    clone_clean = (
        clone_business.metadata.get("category") is None
        and _count(store, "bos_grant") == grants_before
        and _count(store, "bos_principal") == principals_before
        and _count(
            store, "bos_budget", "business_id=?", (clone.business_id,)
        ) == 0
        and _count(
            store, "bos_agent", "business_id=?", (clone.business_id,)
        ) == 0
        and _count(
            store, "bos_ledger_entry", "business_id=?", (clone.business_id,)
        ) == 0
    )
    checks.append(
        _check(
            "clone_has_no_inherited_power",
            clone_clean and bool(clone.not_copied),
            f"business={clone.business_id} not_copied="
            + ",".join(row["class"] for row in clone.not_copied),
        )
    )

    try:
        factory.clone_business(
            store,
            tenant_id,
            biz_a.business_id,
            name="Forbidden Clone",
            slug="verify-forbidden-clone",
            inherit_authority=True,
        )
        inherit_refused = False
        inherit_code = "no refusal"
    except BusinessError as exc:
        inherit_refused = exc.code == factory.FACTORY_INHERIT_REFUSED_CODE
        inherit_code = exc.code
    checks.append(
        _check("implicit_inheritance_refused", inherit_refused, inherit_code)
    )

    # --- every creation is audited and evented ------------------------
    audit_actions = {
        row[0]
        for row in _rows(
            store,
            "SELECT DISTINCT action FROM bos_audit_event WHERE tenant_id=?",
            (tenant_id,),
        )
    }
    event_types = {
        row[0]
        for row in _rows(
            store,
            "SELECT DISTINCT type FROM bos_event WHERE tenant_id=?",
            (tenant_id,),
        )
    }
    checks.append(
        _check(
            "creation_is_audited",
            {
                "factory.entity.create",
                "factory.business.create",
                "factory.brand.create",
                "factory.business.clone",
            }.issubset(audit_actions)
            and {
                "finance.legal_entity.created",
                "factory.business.created",
                "factory.brand.created",
                "factory.business.cloned",
            }.issubset(event_types),
            f"audit={len(audit_actions)} events={len(event_types)}",
        )
    )

    # --- tenant isolation ---------------------------------------------
    other = factory.create_personal_books(store, owner_name="Other Verify")
    try:
        finance.get_payment(store, other.tenant_id, completed.intent_id)
        tenant_refused = False
        tenant_code = "no refusal"
    except BusinessError as exc:
        tenant_refused = exc.code == finance.PAYMENT_NOT_FOUND_CODE
        tenant_code = exc.code
    checks.append(
        _check(
            "tenant_isolation",
            tenant_refused
            and finance.list_payments(store, other.tenant_id) == []
            and len(factory.list_legal_entities(store, other.tenant_id)) == 1,
            tenant_code,
        )
    )

    return {
        "status": "PASS" if all(item["ok"] for item in checks) else "FAIL",
        "checks": checks,
        "passed": sum(1 for item in checks if item["ok"]),
        "failed": sum(1 for item in checks if not item["ok"]),
        "limitations": [
            "hermetic: throwaway SQLite, no network, no live provider or bank",
            "a provider is a label and a receipt is caller-supplied evidence;"
            " no live payment was attempted",
            "the verifier re-reads SQLite but exercises the real finance/factory"
            " modules, so it complements rather than replaces pytest",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--keep", action="store_true", help="keep the throwaway store for inspection"
    )
    args = parser.parse_args(argv)
    try:
        if args.keep:
            tmp = Path(tempfile.mkdtemp(prefix="qubit-finance-factory-"))
            report = run(tmp)
            report["workdir"] = str(tmp)
        else:
            with tempfile.TemporaryDirectory(prefix="qubit-finance-factory-") as raw:
                report = run(Path(raw))
    except Exception:  # pragma: no cover - internal failure
        report = {
            "status": "FAIL",
            "checks": [],
            "error": traceback.format_exc(),
        }
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(report["error"], file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for item in report["checks"]:
            mark = "PASS" if item["ok"] else "FAIL"
            print(f"{mark} {item['name']}: {item['detail']}")
        print(report["status"])
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
