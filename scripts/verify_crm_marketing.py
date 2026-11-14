#!/usr/bin/env python3
"""Independent verifier for P5-03 CRM and P5-04 marketing.

It drives the real modules through their public seams over a throwaway
Business OS SQLite file and recomputes the acceptance claims instead of
comparing a module to itself:

CRM: identity dedupe, consent/suppression per channel, pipeline aging,
single-owner assignment leases, SLA/handoff, internal-note vs customer-message
separation, approval payload binding, tenant isolation.

Marketing: draft-vs-published, no publish without an approved approval,
payload-confusion refusal, duplicate-publish dedupe, mandatory receipt,
rate-limit refusal before the adapter, unknown-outcome reconciliation, and the
no-fake-engagement rule.

Hermetic: no network, no live service, no credentials, no real outbound
contact. ``--json`` prints one machine-readable report.

Exit codes:
    0  every check passed
    1  a check failed
    2  usage / internal error
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge.business import approvals, crm, marketing  # noqa: E402
from voicebridge.business.errors import BusinessError  # noqa: E402
from voicebridge.business.store import BusinessStore  # noqa: E402

VERIFICATION_SCHEMA = "qubit-office-crm-marketing-verification/1"
OWNER = "owner"
NOW = 1_800_000_000.0
PUBLISHED_AT = "2026-11-14T00:00:00Z"


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _refused(call, code: str) -> tuple[bool, str]:
    try:
        call()
    except BusinessError as exc:
        return exc.code == code, exc.code
    except Exception as exc:  # noqa: BLE001 - report the class, never pass
        return False, f"{type(exc).__name__}: {exc}"
    return False, "no refusal"


class SandboxAdapter:
    """A deterministic sandbox publisher; a receipt, never a real effect."""

    def __init__(self, *, unknown: bool = False, empty: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.unknown = unknown
        self.empty = empty

    def publish(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.unknown:
            raise marketing.PublicationUnknown("provider timeout")
        if self.empty:
            return {}
        return {
            "receipt_id": f"rcpt-{len(self.calls)}",
            "venue": kwargs["venue"],
            "payload_sha256": kwargs["payload_sha256"],
            "external_id": f"post-{len(self.calls)}",
            "published_at": PUBLISHED_AT,
            "environment": "sandbox",
        }


def _seed(workdir: Path) -> dict[str, Any]:
    store = BusinessStore(workdir / "business.db")
    tenant = store.create_tenant("Verify Holdings")
    business = store.create_business(tenant.tenant_id, "Verify", slug="verify")
    other = store.create_tenant("Other")
    other_business = store.create_business(
        other.tenant_id, "Other", slug="other"
    )
    return {
        "store": store,
        "tenant": tenant.tenant_id,
        "business": business.business_id,
        "other_tenant": other.tenant_id,
        "other_business": other_business.business_id,
    }


def _contact(env: dict[str, Any], *, email: str = "ada@example.com",
             ref: str = "ext-1"):
    contact, _ = crm.upsert_contact(
        env["store"], env["tenant"], env["business"],
        kind="lead", display_name="Ada Lovelace", email=email,
        external_ref=ref, actor=OWNER, idempotency_key=f"c-{email}-{ref}",
        now=NOW,
    )
    return contact


def _ticket(env: dict[str, Any], contact, *, sla_seconds: float = 100.0):
    return crm.open_ticket(
        env["store"], env["tenant"], env["business"], contact.contact_id,
        subject="Where is my order?", body="It has not arrived.",
        channel="email", priority="normal", sla_seconds=sla_seconds,
        assigned_to="agent:a", actor=OWNER,
        idempotency_key=f"t-{contact.contact_id}", now=NOW,
    )


def _approve(env: dict[str, Any], approval_id: str) -> None:
    approvals.decide(
        env["store"], tenant_id=env["tenant"], approval_id=approval_id,
        decision="approved", decided_by=OWNER, reason="verified", now=NOW,
    )


def _crm_checks(env: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    store, tenant = env["store"], env["tenant"]

    first, created = crm.upsert_contact(
        store, tenant, env["business"], kind="lead",
        display_name="Ada Lovelace", email="Ada@Example.com",
        external_ref="ext-verify", actor=OWNER,
        idempotency_key="verify-contact", now=NOW,
    )
    same, created_again = crm.upsert_contact(
        store, tenant, env["business"], kind="customer",
        display_name="Ada L.", email="ada@example.com", actor=OWNER,
        idempotency_key="verify-contact-2", now=NOW,
    )
    checks.append(_check(
        "identity_dedupe",
        created is True and created_again is False
        and same.contact_id == first.contact_id
        and len(store.list_contacts(tenant, business_id=env["business"])) == 1,
        f"created={created} repeat={created_again}"
        f" contacts={len(store.list_contacts(tenant))}"))

    ok_consent, consent_code = _refused(
        lambda: crm.assert_contactable(store, tenant, first.contact_id, "email"),
        crm.CRM_CONSENT_BLOCKED_CODE,
    )
    crm.record_consent(
        store, tenant, first.contact_id, channel="email", status="granted",
        actor=OWNER, idempotency_key="verify-consent", now=NOW,
    )
    allowed = crm.assert_contactable(store, tenant, first.contact_id, "email")
    crm.record_suppression(
        store, tenant, first.contact_id, channel="all", status="suppressed",
        reason="do not contact", actor=OWNER,
        idempotency_key="verify-suppress", now=NOW,
    )
    ok_suppressed, suppress_code = _refused(
        lambda: crm.assert_contactable(store, tenant, first.contact_id, "email"),
        crm.CRM_SUPPRESSED_CODE,
    )
    checks.append(_check(
        "consent_and_suppression",
        ok_consent and allowed["consent"] == "granted" and ok_suppressed,
        f"consent_refusal={consent_code} suppression_refusal={suppress_code}"))
    crm.record_suppression(
        store, tenant, first.contact_id, channel="all", status="cleared",
        actor=OWNER, idempotency_key="verify-unsuppress", now=NOW,
    )

    crm.set_stage(
        store, tenant, first.contact_id, "qualified", actor=OWNER,
        idempotency_key="verify-stage", now=NOW,
    )
    stale = crm.pipeline(
        store, tenant, business_id=env["business"], now=NOW + 8 * 86400,
    )[0]
    checks.append(_check(
        "pipeline_aging",
        stale["stage"] == "qualified" and stale["aging"] is True,
        f"stage={stale['stage']} age={stale['age_seconds']:.0f}"
        f" aging={stale['aging']}"))

    lease = crm.assign(
        store, tenant, first.contact_id, holder="agent:a", ttl_seconds=50,
        actor=OWNER, idempotency_key="verify-assign", now=NOW,
    )
    ok_second, second_code = _refused(
        lambda: crm.assign(
            store, tenant, first.contact_id, holder="agent:b",
            ttl_seconds=50, actor=OWNER,
            idempotency_key="verify-assign-b", now=NOW + 10,
        ),
        "BUSINESS_LEASE_CONFLICT",
    )
    crm.release_assignment(
        store, tenant, first.contact_id, lease_id=lease["lease_id"],
        holder="agent:a", fencing_token=lease["fencing_token"], actor=OWNER,
        idempotency_key="verify-release", now=NOW + 20,
    )
    reassigned = crm.assign(
        store, tenant, first.contact_id, holder="agent:b", ttl_seconds=50,
        actor=OWNER, idempotency_key="verify-assign-c", now=NOW + 20,
    )
    checks.append(_check(
        "assignment_single_owner",
        ok_second and reassigned["holder"] == "agent:b",
        f"second_refusal={second_code} reassigned={reassigned['holder']}"))

    ticket = _ticket(env, first)
    before = crm.ticket_sla(store, tenant, ticket.ticket_id, now=NOW + 40)
    after = crm.ticket_sla(store, tenant, ticket.ticket_id, now=NOW + 150)
    handed = crm.handoff_ticket(
        store, tenant, ticket.ticket_id, to_assignee="agent:b",
        reason="specialist", actor=OWNER,
        idempotency_key="verify-handoff", now=NOW + 150,
    )
    checks.append(_check(
        "sla_and_handoff",
        before["overdue"] is False and after["overdue"] is True
        and handed.assignee == "agent:b"
        and handed.handoffs[0]["sla_breached"] is True,
        f"before={before['overdue']} after={after['overdue']}"
        f" assignee={handed.assignee} breaches={after['breaches']}"))

    note = crm.add_note(
        store, tenant, ticket.ticket_id, body="Internal refund note.",
        actor="agent:a", idempotency_key="verify-note", now=NOW,
    )
    notes = crm.list_notes(store, tenant, ticket.ticket_id)
    customer_before = crm.list_customer_messages(store, tenant, ticket.ticket_id)
    checks.append(_check(
        "note_visibility",
        [item.message_id for item in notes] == [note.message_id]
        and customer_before == [],
        f"notes={len(notes)} customer_messages={len(customer_before)}"))

    approval = crm.request_message_approval(
        store, tenant, ticket.ticket_id,
        body="Your order is on the way.", requested_by=OWNER,
        idempotency_key="verify-msg-approval", now=NOW,
    )
    _approve(env, approval.approval_id)
    ok_confused, confused_code = _refused(
        lambda: crm.send_customer_message(
            store, tenant, ticket.ticket_id, body="A different message.",
            approval_id=approval.approval_id, actor="agent:a",
            idempotency_key="verify-msg-bad", now=NOW,
        ),
        "BUSINESS_APPROVAL_INTENT_MISMATCH",
    )
    message = crm.send_customer_message(
        store, tenant, ticket.ticket_id, body="Your order is on the way.",
        approval_id=approval.approval_id, actor="agent:a",
        idempotency_key="verify-msg-good", now=NOW,
    )
    customer_after = crm.list_customer_messages(store, tenant, ticket.ticket_id)
    checks.append(_check(
        "approval_payload_binding",
        ok_confused and len(customer_after) == 1
        and customer_after[0].message_id == message.message_id,
        f"confused_refusal={confused_code} messages={len(customer_after)}"))

    ok_other, other_code = _refused(
        lambda: crm.get_contact(store, env["other_tenant"], first.contact_id),
        "BUSINESS_NOT_FOUND",
    )
    checks.append(_check(
        "crm_tenant_isolation",
        ok_other and crm.pipeline(store, env["other_tenant"]) == [],
        f"refusal={other_code}"))

    return checks


def _marketing_checks(env: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    store, tenant = env["store"], env["tenant"]

    draft, created = marketing.create_draft(
        store, tenant, env["business"], venue="x",
        title="Launch", text="Launch day. #ad", disclosure="#ad",
        disclosure_required=True, actor=OWNER,
        idempotency_key="verify-draft", now=NOW,
    )
    checks.append(_check(
        "draft_is_not_published",
        created is True and draft.status == marketing.STATUS_DRAFT
        and draft.published is False,
        f"status={draft.status} published={draft.published}"))

    adapter = SandboxAdapter()
    ok_no_approval, no_approval_code = _refused(
        lambda: marketing.publish(
            store, tenant, draft.campaign_id, adapter=adapter,
            approval_id="", actor=OWNER,
            idempotency_key="verify-pub-none", now=NOW,
        ),
        marketing.MARKETING_APPROVAL_REQUIRED_CODE,
    )
    checks.append(_check(
        "no_publish_without_approval",
        ok_no_approval and adapter.calls == []
        and marketing.get_campaign(
            store, tenant, draft.campaign_id
        ).status == marketing.STATUS_DRAFT,
        f"refusal={no_approval_code} adapter_calls={len(adapter.calls)}"))

    approval = marketing.request_approval(
        store, tenant, draft.campaign_id, requested_by=OWNER,
        idempotency_key="verify-draft-approval", now=NOW,
    )
    _approve(env, approval.approval_id)
    other = marketing.create_draft(
        store, tenant, env["business"], venue="x",
        title="Other", text="A different launch. #ad", disclosure="#ad",
        disclosure_required=True, actor=OWNER,
        idempotency_key="verify-draft-b", now=NOW,
    )[0]
    ok_confused, confused_code = _refused(
        lambda: marketing.publish(
            store, tenant, other.campaign_id, adapter=adapter,
            approval_id=approval.approval_id, actor=OWNER,
            idempotency_key="verify-pub-b", now=NOW,
        ),
        "BUSINESS_APPROVAL_INTENT_MISMATCH",
    )
    checks.append(_check(
        "payload_confusion_refused",
        ok_confused and adapter.calls == [],
        f"refusal={confused_code} adapter_calls={len(adapter.calls)}"))

    receipt, made = marketing.publish(
        store, tenant, draft.campaign_id, adapter=adapter,
        approval_id=approval.approval_id, actor=OWNER,
        idempotency_key="verify-pub", now=NOW, rate_limit=1,
        rate_window_seconds=3600,
    )
    repeat, made_again = marketing.publish(
        store, tenant, draft.campaign_id, adapter=adapter,
        approval_id=approval.approval_id, actor=OWNER,
        idempotency_key="verify-pub-repeat", now=NOW,
    )
    checks.append(_check(
        "duplicate_publish_deduped",
        made is True and made_again is False
        and repeat.receipt_id == receipt.receipt_id
        and len(adapter.calls) == 1,
        f"first={made} repeat={made_again} adapter_calls={len(adapter.calls)}"))
    checks.append(_check(
        "publication_receipt",
        receipt.venue == "x" and receipt.payload_sha256 == draft.payload_sha256
        and receipt.published_at == PUBLISHED_AT
        and receipt.environment == "sandbox",
        f"venue={receipt.venue} external_id={receipt.external_id}"
        f" environment={receipt.environment}"))

    second_approval = marketing.request_approval(
        store, tenant, other.campaign_id, requested_by=OWNER,
        idempotency_key="verify-other-approval", now=NOW,
    )
    _approve(env, second_approval.approval_id)
    ok_limited, limited_code = _refused(
        lambda: marketing.publish(
            store, tenant, other.campaign_id, adapter=adapter,
            approval_id=second_approval.approval_id, actor=OWNER,
            idempotency_key="verify-pub-limited", now=NOW,
            rate_limit=1, rate_window_seconds=3600,
        ),
        marketing.MARKETING_RATE_LIMITED_CODE,
    )
    checks.append(_check(
        "rate_limit_refusal",
        ok_limited and len(adapter.calls) == 1,
        f"refusal={limited_code} adapter_calls={len(adapter.calls)}"))

    empty_adapter = SandboxAdapter(empty=True)
    receiptless = marketing.create_draft(
        store, tenant, env["business"], venue="linkedin",
        title="Receiptless", text="A post with no receipt. #ad",
        disclosure="#ad", disclosure_required=True, actor=OWNER,
        idempotency_key="verify-draft-receipt", now=NOW,
    )[0]
    receiptless_approval = marketing.request_approval(
        store, tenant, receiptless.campaign_id, requested_by=OWNER,
        idempotency_key="verify-receipt-approval", now=NOW,
    )
    _approve(env, receiptless_approval.approval_id)
    ok_receipt, receipt_code = _refused(
        lambda: marketing.publish(
            store, tenant, receiptless.campaign_id, adapter=empty_adapter,
            approval_id=receiptless_approval.approval_id, actor=OWNER,
            idempotency_key="verify-pub-receipt", now=NOW,
        ),
        marketing.MARKETING_RECEIPT_REQUIRED_CODE,
    )
    checks.append(_check(
        "receipt_required",
        ok_receipt and marketing.get_campaign(
            store, tenant, receiptless.campaign_id
        ).status != marketing.STATUS_PUBLISHED,
        f"refusal={receipt_code}"))

    unknown_draft = marketing.create_draft(
        store, tenant, env["business"], venue="threads",
        title="Unknown", text="Outcome unknown. #ad", disclosure="#ad",
        disclosure_required=True, actor=OWNER,
        idempotency_key="verify-draft-unknown", now=NOW,
    )[0]
    unknown_approval = marketing.request_approval(
        store, tenant, unknown_draft.campaign_id, requested_by=OWNER,
        idempotency_key="verify-unknown-approval", now=NOW,
    )
    _approve(env, unknown_approval.approval_id)
    ok_unknown, unknown_code = _refused(
        lambda: marketing.publish(
            store, tenant, unknown_draft.campaign_id,
            adapter=SandboxAdapter(unknown=True),
            approval_id=unknown_approval.approval_id, actor=OWNER,
            idempotency_key="verify-pub-unknown", now=NOW,
        ),
        marketing.MARKETING_UNKNOWN_CODE,
    )
    reconciled = marketing.reconcile(
        store, tenant, unknown_draft.campaign_id,
        receipt={
            "receipt_id": "rcpt-reconcile",
            "venue": "threads",
            "external_id": "post-reconciled",
            "published_at": PUBLISHED_AT,
            "payload_sha256": unknown_draft.payload_sha256,
            "environment": "sandbox",
            "approval_id": unknown_approval.approval_id,
        },
        actor=OWNER, idempotency_key="verify-reconcile", now=NOW,
    )
    resolved = marketing.get_campaign(store, tenant, unknown_draft.campaign_id)
    checks.append(_check(
        "unknown_then_reconciled",
        ok_unknown and reconciled.reconciled is True
        and resolved.status == marketing.STATUS_PUBLISHED,
        f"unknown_refusal={unknown_code} reconciled={reconciled.reconciled}"
        f" status={resolved.status}"))

    ok_engagement, engagement_code = _refused(
        lambda: marketing.record_engagement(store, tenant, "x", "like", 10),
        marketing.MARKETING_ENGAGEMENT_REFUSED_CODE,
    )
    ok_fake, fake_code = _refused(
        lambda: marketing.create_draft(
            store, tenant, env["business"], venue="instagram",
            text="Buy followers and likes today", actor=OWNER,
            idempotency_key="verify-draft-fake", now=NOW,
        ),
        marketing.MARKETING_CONTENT_REFUSED_CODE,
    )
    checks.append(_check(
        "no_fake_engagement",
        ok_engagement and ok_fake,
        f"record_refusal={engagement_code} content_refusal={fake_code}"))

    return checks


def run_verification(workdir: Path | str | None = None) -> dict[str, Any]:
    tmp = (
        Path(workdir)
        if workdir is not None
        else Path(tempfile.mkdtemp(prefix="qubit-crm-marketing-verify-"))
    )
    tmp.mkdir(parents=True, exist_ok=True)
    env = _seed(tmp)
    checks = _crm_checks(env) + _marketing_checks(env)
    failed = sum(1 for check in checks if not check["ok"])
    return {
        "schema": VERIFICATION_SCHEMA,
        "ok": failed == 0,
        "mode": "self-check",
        "checks_total": len(checks),
        "checks_failed": failed,
        "checks": checks,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify Qubit Office P5-03 CRM and P5-04 marketing."
    )
    parser.add_argument(
        "--json", action="store_true",
        help="print one machine-readable JSON report",
    )
    args = parser.parse_args(argv)
    try:
        report = run_verification()
    except Exception as exc:  # noqa: BLE001 - never a pass
        if args.json:
            print(json.dumps(
                {"ok": False, "error": str(exc), "schema": VERIFICATION_SCHEMA}
            ))
        else:
            print(f"verify_crm_marketing: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for check in report["checks"]:
            mark = "PASS" if check["ok"] else "FAIL"
            print(f"[{mark}] {check['name']}: {check['detail']}")
        print("ok" if report["ok"] else "failed")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
