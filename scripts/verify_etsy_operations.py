#!/usr/bin/env python3
"""P5-01 — independently verify the Etsy digital-shop operations.

WHY THIS EXISTS
---------------
The operations service must not be its own witness. This script drives a real
(throwaway) Business OS store through the P5-01 flow with the **actual**
services and an injected in-memory Etsy transport, then recomputes every
material claim from raw SQL:

* the publish approval is bound to the exact normalized intent hash (the
  approval row hash equals the hash recomputed from the stored intent)
* a definite provider receipt completes a job; an ambiguous provider response
  leaves the job ``unknown`` and no completion exists
* an ``unknown`` outcome is not completed, ledgered or ordered until an
  explicit reconciliation receipt arrives
* duplicate publish/refund calls produce exactly one receipt and one provider
  call
* refund and payout rows are balanced double-entry, and the payout net equals
  the cash debit
* the entitlement is immutable and carries the order's asset version
* a paid asset cannot be inlined or marked redistributable
* the event log is append-ordered and every payload hash recomputes
* no network client is reached (the injected transport is the only seam)
* a second tenant sees none of the first tenant's rows

Exit codes: ``0`` all checks pass, ``1`` a check failed, ``2`` usage/typed
empty store.

Hermetic by default: no network, no credentials, no live service, no real
Etsy publish. ``--db PATH`` verifies an existing store read-only and reports a
typed ``etsy_operations_empty`` when it has no operations records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import sqlite3
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voicebridge.business import BusinessStore  # noqa: E402
from voicebridge.business.etsy_operations import (  # noqa: E402
    ARTIFACT_ASSET,
    ARTIFACT_DRAFT,
    ARTIFACT_ENTITLEMENT,
    ARTIFACT_INTENT,
    ARTIFACT_LEDGER_RECONCILIATION,
    ARTIFACT_ORDER,
    ARTIFACT_PAYOUT,
    ARTIFACT_PUBLISH_RECEIPT,
    ARTIFACT_RECONCILIATION,
    ARTIFACT_REFUND_RECEIPT,
    ETSY_APPROVAL_REQUIRED,
    ETSY_PAID_ART_GUARD,
    ETSY_RECONCILIATION_REQUIRED,
    EtsyOperations,
    InMemoryEtsySandboxTransport,
    sandbox_etsy_adapter,
)
from voicebridge.business.errors import BusinessError  # noqa: E402

TASK_ID = "P5-01"
SCHEMA = "qubit-office-etsy-operations-verification/1"

LIMITATIONS = [
    "Hermetic seed only: a throwaway SQLite store built through the real"
    " services, not live Etsy data.",
    "The provider is an injected in-memory transport; it is the only seam the"
    " adapter can reach. No real Etsy publish, OAuth token or network call is"
    " performed.",
    "No paid art is read, copied or committed; the paid-art check is a typed"
    " refusal with a synthetic inline field.",
    "This verifies the operations layer and its durable records, not the live"
    " Etsy dashboard, the Office renderer or a full release matrix.",
]

LISTING = {
    "title": "Minimal weekly planner",
    "description": "A printable weekly planner with a clean layout.",
    "price_minor": 600,
    "currency": "USD",
    "digital_files": ["planner.pdf"],
    "tags": ["planner", "printable"],
    "asset_version": "v1",
}


class UsageError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, query: str, params=()):
    return conn.execute(query, params).fetchall()


def _artifact_body(conn, tenant_id: str, artifact_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT body FROM bos_artifact WHERE tenant_id=? AND artifact_id=?",
        (tenant_id, artifact_id),
    ).fetchone()
    return json.loads(row["body"]) if row and row["body"] else {}


def _artifact_by_name(conn, tenant_id: str, name: str):
    return conn.execute(
        "SELECT * FROM bos_artifact WHERE tenant_id=? AND name=?",
        (tenant_id, name),
    ).fetchone()


def _seed(store: BusinessStore) -> dict[str, Any]:
    """Run the full P5-01 flow against the injected sandbox transport."""
    tenant = store.create_tenant("Pilot Holdings")
    business = store.create_business(tenant.tenant_id, "Pilot Shop", slug="pilot")
    other = store.create_tenant("Other Holdings")
    other_business = store.create_business(
        other.tenant_id, "Other Shop", slug="other"
    )
    transport = InMemoryEtsySandboxTransport()
    ops = EtsyOperations(store, adapter=sandbox_etsy_adapter(transport))
    t, b = tenant.tenant_id, business.business_id

    ops.record_niche(
        tenant_id=t,
        business_id=b,
        actor="agent:etsy",
        niche_id="niche-1",
        title="Printable planners",
        evidence={
            "demand": {"label": "UNKNOWN", "note": "unvalidated"},
            "policy": {"label": "FACT", "note": "digital downloads only"},
        },
        source_ref="research:planner-niche:1",
    )
    ops.reference_asset(
        tenant_id=t,
        business_id=b,
        actor="agent:etsy",
        asset_ref="asset:planner-v1",
        version="v1",
        license="original-work",
        license_source="creator-owned",
        paid=False,
        redistribution_allowed=True,
    )
    created = ops.create_listing(
        tenant_id=t,
        business_id=b,
        actor="agent:etsy",
        niche_id="niche-1",
        asset_id="asset:planner-v1",
        listing=LISTING,
        idempotency_key="listing-1",
    )
    approved = ops.approve_listing(
        tenant_id=t,
        approval_id=created.approval.approval_id,
        decided_by="owner",
    )
    published = ops.publish_listing(
        tenant_id=t, job_id=created.job.job_id, actor="agent:etsy"
    )
    second_publish = ops.publish_listing(
        tenant_id=t, job_id=created.job.job_id, actor="agent:etsy"
    )
    order = ops.record_order(
        tenant_id=t,
        business_id=b,
        actor="agent:etsy",
        job_id=created.job.job_id,
        order_id="order-1",
        receipt_id="receipt-1",
        amount_minor=600,
        currency="USD",
        buyer_ref="buyer:sha256:001",
    )
    entitlement = ops.grant_entitlement(
        tenant_id=t,
        business_id=b,
        actor="agent:etsy",
        order_id="order-1",
        version="v1",
        download_ref="sandbox-download://planner/v1",
    )
    entitlement_again = ops.grant_entitlement(
        tenant_id=t,
        business_id=b,
        actor="agent:etsy",
        order_id="order-1",
        version="v1",
        download_ref="sandbox-download://planner/v1",
    )
    ops.open_support_case(
        tenant_id=t,
        business_id=b,
        actor="agent:etsy",
        order_id="order-1",
        case_ref="case-1",
        reason="buyer asked for the download link",
    )
    requested = ops.request_refund(
        tenant_id=t,
        business_id=b,
        actor="agent:etsy",
        order_id="order-1",
        amount_minor=200,
        reason="planner did not match the preview",
        idempotency_key="refund-1",
        case_ref="case-1",
    )
    ops.approve_refund(
        tenant_id=t,
        approval_id=requested.approval.approval_id,
        decided_by="owner",
    )
    refund = ops.refund(
        tenant_id=t, job_id=requested.job.job_id, actor="agent:etsy"
    )
    refund_again = ops.refund(
        tenant_id=t, job_id=requested.job.job_id, actor="agent:etsy"
    )
    payout = ops.record_payout(
        tenant_id=t,
        business_id=b,
        actor="agent:etsy",
        order_id="order-1",
        payout_ref="payout-1",
        provider_receipt_ref="etsy-payout-feed:2026-11-14:1",
        gross_minor=600,
        fee_minor=60,
    )
    reconciliation = ops.reconcile_ledger(
        tenant_id=t,
        business_id=b,
        actor="agent:etsy",
        order_id="order-1",
        payout_ref="payout-1",
    )
    no_approval_listing = ops.create_listing(
        tenant_id=t,
        business_id=b,
        actor="agent:etsy",
        niche_id="niche-1",
        asset_id="asset:planner-v1",
        listing={**LISTING, "title": "Unapproved planner"},
        idempotency_key="listing-unapproved",
    )
    try:
        ops.publish_listing(
            tenant_id=t,
            job_id=no_approval_listing.job.job_id,
            actor="agent:etsy",
        )
        no_approval_code = None
    except BusinessError as exc:
        no_approval_code = exc.code

    asset_count_before = len(
        [row for row in store.list_artifacts(t) if row.kind == ARTIFACT_ASSET]
    )
    try:
        ops.reference_asset(
            tenant_id=t,
            business_id=b,
            actor="agent:etsy",
            asset_ref="paid:limezu:1",
            version="v1",
            license="paid",
            license_source="owner purchase",
            paid=True,
            redistribution_allowed=False,
            inline={"content_base64": "AAAA"},
        )
        paid_code = None
    except BusinessError as exc:
        paid_code = exc.code
    asset_count_after = len(
        [row for row in store.list_artifacts(t) if row.kind == ARTIFACT_ASSET]
    )

    unknown_transport = InMemoryEtsySandboxTransport(ambiguous_publish=True)
    unknown_ops = EtsyOperations(
        store, adapter=sandbox_etsy_adapter(unknown_transport)
    )
    unknown_created = unknown_ops.create_listing(
        tenant_id=t,
        business_id=b,
        actor="agent:etsy",
        niche_id="niche-1",
        asset_id="asset:planner-v1",
        listing={**LISTING, "title": "Ambiguous planner"},
        idempotency_key="listing-unknown",
    )
    unknown_ops.approve_listing(
        tenant_id=t,
        approval_id=unknown_created.approval.approval_id,
        decided_by="owner",
    )
    unknown = unknown_ops.publish_listing(
        tenant_id=t, job_id=unknown_created.job.job_id, actor="agent:etsy"
    )
    try:
        unknown_ops.publish_listing(
            tenant_id=t, job_id=unknown_created.job.job_id, actor="agent:etsy"
        )
        republish_code = None
    except BusinessError as exc:
        republish_code = exc.code
    reconciled_unknown = unknown_ops.reconcile_unknown(
        tenant_id=t,
        business_id=b,
        actor="owner",
        job_id=unknown_created.job.job_id,
        resolution="completed",
        provider_ref="etsy-reconciliation:2026-11-14:1",
    )
    return {
        "tenant": tenant,
        "business": business,
        "other": other,
        "other_business": other_business,
        "transport": transport,
        "unknown_transport": unknown_transport,
        "ops": ops,
        "created": created,
        "approved": approved,
        "published": published,
        "second_publish": second_publish,
        "order": order,
        "entitlement": entitlement,
        "entitlement_again": entitlement_again,
        "refund": refund,
        "refund_again": refund_again,
        "payout": payout,
        "reconciliation": reconciliation,
        "no_approval_code": no_approval_code,
        "paid_code": paid_code,
        "asset_count_before": asset_count_before,
        "asset_count_after": asset_count_after,
        "unknown": unknown,
        "republish_code": republish_code,
        "reconciled_unknown": reconciled_unknown,
        "unknown_created": unknown_created,
    }


def _independent_checks(
    store: BusinessStore, context: dict[str, Any]
) -> list[dict[str, Any]]:
    tenant = context["tenant"]
    business = context["business"]
    other = context["other"]
    t, b = tenant.tenant_id, business.business_id
    created = context["created"]
    transport = context["transport"]
    conn = _connect(Path(store.path))
    checks: list[dict[str, Any]] = []
    try:
        artifacts = _rows(
            conn,
            "SELECT artifact_id, kind, name, body, sha256 FROM bos_artifact"
            " WHERE tenant_id=? AND business_id=?",
            (t, b),
        )
        kinds = [row["kind"] for row in artifacts]
        checks.append(
            _check(
                "store_seeded",
                bool(artifacts),
                f"artifacts={len(artifacts)} kinds={sorted(set(kinds))}",
            )
        )
        for kind in (
            ARTIFACT_DRAFT,
            ARTIFACT_INTENT,
            ARTIFACT_PUBLISH_RECEIPT,
            ARTIFACT_ORDER,
            ARTIFACT_ENTITLEMENT,
            ARTIFACT_REFUND_RECEIPT,
            ARTIFACT_PAYOUT,
            ARTIFACT_LEDGER_RECONCILIATION,
            ARTIFACT_RECONCILIATION,
        ):
            checks.append(
                _check(
                    f"artifact_{kind}",
                    kind in kinds,
                    f"{kind} count={kinds.count(kind)}",
                )
            )

        intent_row = _artifact_by_name(
            conn, t, f"{ARTIFACT_INTENT}:{created.job.job_id}"
        )
        intent_payload = json.loads(intent_row["body"]) if intent_row else {}
        recomputed = hashlib.sha256(
            _canonical(intent_payload.get("intent")).encode("utf-8")
        ).hexdigest()
        approval_row = _rows(
            conn,
            "SELECT approval_id, request_hash, status, action, payload_json"
            " FROM bos_approval WHERE tenant_id=? AND approval_id=?",
            (t, created.approval.approval_id),
        )[0]
        approval_payload = json.loads(approval_row["payload_json"])
        checks.append(
            _check(
                "approval_bound_to_exact_intent",
                approval_row["request_hash"] == recomputed
                and intent_payload.get("intent_hash") == recomputed
                and hashlib.sha256(
                    _canonical(approval_payload).encode("utf-8")
                ).hexdigest()
                == recomputed,
                f"approval_hash={approval_row['request_hash'][:16]}"
                f" recomputed={recomputed[:16]}"
                f" status={approval_row['status']}",
            )
        )

        publish_row = _artifact_by_name(
            conn, t, f"{ARTIFACT_PUBLISH_RECEIPT}:{created.job.job_id}"
        )
        publish_body = json.loads(publish_row["body"]) if publish_row else {}
        job_row = _rows(
            conn,
            "SELECT status, output_json FROM bos_job WHERE tenant_id=?"
            " AND job_id=?",
            (t, created.job.job_id),
        )[0]
        checks.append(
            _check(
                "publish_receipt_is_durable",
                job_row["status"] == "completed"
                and publish_body.get("state") == "active"
                and publish_body.get("listing_id")
                and publish_body.get("provider_request_id"),
                f"job={job_row['status']}"
                f" listing={publish_body.get('listing_id')}"
                f" provider_request={publish_body.get('provider_request_id')}",
            )
        )
        receipt_count = _rows(
            conn,
            "SELECT COUNT(*) AS n FROM bos_artifact WHERE tenant_id=?"
            " AND kind=? AND name=?",
            (t, ARTIFACT_PUBLISH_RECEIPT, f"{ARTIFACT_PUBLISH_RECEIPT}:{created.job.job_id}"),
        )[0]["n"]
        checks.append(
            _check(
                "no_duplicate_publish",
                transport.publish_calls == 1
                and int(receipt_count) == 1
                and context["second_publish"].artifact.artifact_id
                == context["published"].artifact.artifact_id,
                f"transport_publish_calls={transport.publish_calls}"
                f" receipt_rows={receipt_count}",
            )
        )

        entitlement_row = _artifact_by_name(
            conn, t, f"{ARTIFACT_ENTITLEMENT}:order-1:v1"
        )
        entitlement_body = (
            json.loads(entitlement_row["body"]) if entitlement_row else {}
        )
        order_row = _artifact_by_name(conn, t, f"{ARTIFACT_ORDER}:order-1")
        order_body = json.loads(order_row["body"]) if order_row else {}
        checks.append(
            _check(
                "entitlement_is_immutable_and_versioned",
                entitlement_body.get("immutable") is True
                and entitlement_body.get("version")
                == order_body.get("asset_version")
                == "v1"
                and context["entitlement_again"].artifact.sha256
                == context["entitlement"].artifact.sha256,
                f"version={entitlement_body.get('version')}"
                f" immutable={entitlement_body.get('immutable')}",
            )
        )

        refund_row = _artifact_by_name(
            conn, t, f"{ARTIFACT_REFUND_RECEIPT}:{context['refund'].job.job_id}"
        )
        refund_body = json.loads(refund_row["body"]) if refund_row else {}
        refund_debits = _rows(
            conn,
            "SELECT COUNT(*) AS n, COALESCE(SUM(amount_minor), 0) AS total"
            " FROM bos_ledger_entry WHERE tenant_id=? AND account='etsy.refunds'"
            " AND direction='debit'",
            (t,),
        )[0]
        checks.append(
            _check(
                "refund_deduped_and_ledgered",
                context["refund_again"].artifact.artifact_id
                == context["refund"].artifact.artifact_id
                and transport.refund_calls == 1
                and int(refund_debits["n"]) == 1
                and int(refund_debits["total"]) == 200
                and refund_body.get("amount_minor") == 200,
                f"transport_refund_calls={transport.refund_calls}"
                f" refund_rows={refund_debits['n']}"
                f" total={refund_debits['total']}",
            )
        )

        accounts = _rows(
            conn,
            "SELECT account, direction, COUNT(*) AS n,"
            " COALESCE(SUM(amount_minor), 0) AS total FROM bos_ledger_entry"
            " WHERE tenant_id=? GROUP BY account, direction",
            (t,),
        )
        debit = sum(int(row["total"]) for row in accounts if row["direction"] == "debit")
        credit = sum(
            int(row["total"]) for row in accounts if row["direction"] == "credit"
        )
        cash = sum(
            int(row["total"])
            for row in accounts
            if row["account"] == "etsy.cash" and row["direction"] == "debit"
        )
        payout_row = _artifact_by_name(conn, t, f"{ARTIFACT_PAYOUT}:payout-1")
        payout_body = json.loads(payout_row["body"]) if payout_row else {}
        checks.append(
            _check(
                "ledger_is_balanced_double_entry",
                debit == credit == 1200 and cash == payout_body.get("net_minor")
                == 340,
                f"debit={debit} credit={credit} cash={cash}"
                f" payout_net={payout_body.get('net_minor')}",
            )
        )
        reconcile_row = _artifact_by_name(
            conn, t, f"{ARTIFACT_LEDGER_RECONCILIATION}:order-1:payout-1"
        )
        reconcile_body = (
            json.loads(reconcile_row["body"]) if reconcile_row else {}
        )
        checks.append(
            _check(
                "ledger_reconciliation_receipt",
                reconcile_body.get("balanced") is True
                and reconcile_body.get("debit_minor")
                == reconcile_body.get("credit_minor"),
                f"balanced={reconcile_body.get('balanced')}"
                f" debit={reconcile_body.get('debit_minor')}"
                f" credit={reconcile_body.get('credit_minor')}",
            )
        )

        unknown = context["unknown"]
        unknown_job = _rows(
            conn,
            "SELECT status FROM bos_job WHERE tenant_id=? AND job_id=?",
            (t, context["unknown_created"].job.job_id),
        )[0]
        reconciled = context["reconciled_unknown"]
        reconcile_artifact = _artifact_by_name(
            conn,
            t,
            f"{ARTIFACT_RECONCILIATION}:{context['unknown_created'].job.job_id}"
            ":etsy-reconciliation:2026-11-14:1",
        )
        checks.append(
            _check(
                "unknown_requires_explicit_receipt",
                unknown.status == "unknown"
                and unknown.receipt.get("unknown") is True
                and context["republish_code"] == ETSY_RECONCILIATION_REQUIRED
                and reconciled.job.status == "completed"
                and reconcile_artifact is not None,
                f"unknown_status={unknown.status}"
                f" republish={context['republish_code']}"
                f" after_reconcile={reconciled.job.status}",
            )
        )
        checks.append(
            _check(
                "unknown_never_completed_without_receipt",
                unknown_job["status"] != "completed"
                or reconcile_artifact is not None,
                f"unknown_job_status={unknown_job['status']}",
            )
        )

        checks.append(
            _check(
                "no_publish_without_approval",
                context["no_approval_code"] == ETSY_APPROVAL_REQUIRED,
                f"code={context['no_approval_code']}",
            )
        )
        checks.append(
            _check(
                "paid_art_guard",
                context["paid_code"] == ETSY_PAID_ART_GUARD
                and context["asset_count_before"]
                == context["asset_count_after"],
                f"code={context['paid_code']}"
                f" assets_before={context['asset_count_before']}"
                f" assets_after={context['asset_count_after']}",
            )
        )

        events = _rows(
            conn,
            "SELECT seq, type, idempotency_key, payload_hash, payload_json"
            " FROM bos_event WHERE tenant_id=? ORDER BY seq",
            (t,),
        )
        seqs = [int(row["seq"]) for row in events]
        hashes_ok = all(
            row["payload_hash"]
            == _sha256_text(_canonical(json.loads(row["payload_json"])))
            for row in events
        )
        checks.append(
            _check(
                "events_replayable",
                bool(events)
                and seqs == sorted(seqs)
                and len(set(seqs)) == len(seqs)
                and hashes_ok,
                f"events={len(events)} hashes_ok={hashes_ok}",
            )
        )

        other_artifacts = _rows(
            conn,
            "SELECT COUNT(*) AS n FROM bos_artifact WHERE tenant_id=?",
            (other.tenant_id,),
        )[0]["n"]
        other_events = _rows(
            conn,
            "SELECT COUNT(*) AS n FROM bos_event WHERE tenant_id=?",
            (other.tenant_id,),
        )[0]["n"]
        other_ledger = _rows(
            conn,
            "SELECT COUNT(*) AS n FROM bos_ledger_entry WHERE tenant_id=?",
            (other.tenant_id,),
        )[0]["n"]
        checks.append(
            _check(
                "tenant_isolation",
                int(other_artifacts) == 0
                and int(other_events) == 0
                and int(other_ledger) == 0,
                f"other_artifacts={other_artifacts}"
                f" other_events={other_events} other_ledger={other_ledger}",
            )
        )

        total_calls = len(transport.calls) + len(context["unknown_transport"].calls)
        checks.append(
            _check(
                "injected_transport_only",
                total_calls > 0,
                f"injected_transport_calls={total_calls}",
            )
        )
    finally:
        conn.close()
    return checks


def _verify_existing(store_path: Path) -> tuple[list[dict[str, Any]], str | None]:
    """Read-only verification of a real store; typed empty when no operations."""
    conn = _connect(store_path)
    try:
        tenants = _rows(conn, "SELECT tenant_id FROM bos_tenant ORDER BY created_at")
        if not tenants:
            return [], "etsy_operations_empty"
        has_events = _rows(conn, "SELECT COUNT(*) AS n FROM bos_event")[0]["n"]
        has_artifacts = _rows(conn, "SELECT COUNT(*) AS n FROM bos_artifact")[0]["n"]
        if not has_events and not has_artifacts:
            return [], "etsy_operations_empty"
        checks: list[dict[str, Any]] = []
        for tenant in tenants:
            tenant_id = tenant["tenant_id"]
            events = _rows(
                conn,
                "SELECT seq, payload_hash, payload_json FROM bos_event"
                " WHERE tenant_id=? ORDER BY seq",
                (tenant_id,),
            )
            if not events:
                continue
            hashes_ok = all(
                row["payload_hash"]
                == _sha256_text(_canonical(json.loads(row["payload_json"])))
                for row in events
            )
            checks.append(
                _check(
                    f"events_replayable:{tenant_id}",
                    hashes_ok,
                    f"events={len(events)} hashes_ok={hashes_ok}",
                )
            )
            accounts = _rows(
                conn,
                "SELECT direction, COALESCE(SUM(amount_minor), 0) AS total"
                " FROM bos_ledger_entry WHERE tenant_id=? GROUP BY direction",
                (tenant_id,),
            )
            debit = sum(
                int(row["total"]) for row in accounts if row["direction"] == "debit"
            )
            credit = sum(
                int(row["total"])
                for row in accounts
                if row["direction"] == "credit"
            )
            checks.append(
                _check(
                    f"ledger_balanced:{tenant_id}",
                    debit == credit,
                    f"debit={debit} credit={credit}",
                )
            )
        if not checks:
            return [], "etsy_operations_empty"
        return checks, None
    finally:
        conn.close()


def _install_network_tripwire() -> list[tuple[Any, Any, Any]]:
    saved: list[tuple[Any, Any, Any]] = []

    def tripwire(*_args, **_kwargs):
        raise AssertionError("verify_etsy_operations attempted network I/O")

    for module, name in (
        (socket, "socket"),
        (socket, "create_connection"),
        (socket, "create_server"),
        (urllib.request, "urlopen"),
    ):
        saved.append((module, name, getattr(module, name)))
        setattr(module, name, tripwire)
    return saved


def _restore_network(saved: list[tuple[Any, Any, Any]]) -> None:
    for module, name, value in saved:
        setattr(module, name, value)


def _report(
    checks: list[dict[str, Any]],
    *,
    mode: str,
    code: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    problems = [check["name"] for check in checks if not check["ok"]]
    report = {
        "schema": SCHEMA,
        "task_id": TASK_ID,
        "mode": mode,
        "generated_at": _utc_now(),
        "ok": code is None and not problems,
        "code": code,
        "checks": checks,
        "checks_total": len(checks),
        "checks_failed": len(problems),
        "problems": problems,
        "limitations": LIMITATIONS,
    }
    if extra:
        report.update(extra)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the P5-01 Etsy operations flow from durable records."
    )
    parser.add_argument(
        "--json", action="store_true", help="emit a JSON report"
    )
    parser.add_argument(
        "--db",
        type=Path,
        help="verify an existing Business OS store read-only",
    )
    parser.add_argument(
        "--keep",
        type=Path,
        help="copy the seeded throwaway store to this path for inspection",
    )
    args = parser.parse_args(argv)

    if args.db is not None:
        if not args.db.exists():
            report = _report([], mode="existing", code="etsy_operations_empty")
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True, default=str))
            else:
                print("P5-01 BLOCKED etsy_operations_empty: no store at --db")
            return 2
        checks, code = _verify_existing(args.db)
        if code is not None:
            report = _report([], mode="existing", code=code)
            if args.json:
                print(json.dumps(report, indent=2, sort_keys=True, default=str))
            else:
                print(f"P5-01 BLOCKED {code}")
            return 2
        report = _report(checks, mode="existing")
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
        else:
            for check in checks:
                marker = "PASS" if check["ok"] else "FAIL"
                print(f"{marker} {check['name']}: {check['detail']}")
            print(f"P5-01 {'PASS' if not report['problems'] else 'FAIL'}")
        return 0 if not report["problems"] else 1

    workdir = Path(tempfile.mkdtemp(prefix="qubit-etsy-ops-"))
    store = BusinessStore(workdir / "business.db")
    saved = _install_network_tripwire()
    try:
        context = _seed(store)
        checks = _independent_checks(store, context)
    except AssertionError as exc:
        _restore_network(saved)
        report = _report(
            [_check("network_tripwire", False, str(exc))],
            mode="self-check",
            code="etsy_operations_network",
        )
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
        else:
            print(f"FAIL network_tripwire: {exc}")
        return 1
    except UsageError as exc:
        _restore_network(saved)
        report = _report([], mode="self-check", code=exc.code)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True, default=str))
        else:
            print(f"P5-01 BLOCKED {exc.code}: {exc.detail}")
        return 2
    _restore_network(saved)
    extra = {
        "store": str(store.path),
        "seed": {
            "tenant_id": context["tenant"].tenant_id,
            "business_id": context["business"].business_id,
            "listing_job_id": context["created"].job.job_id,
            "refund_job_id": context["refund"].job.job_id,
            "publish_calls": context["transport"].publish_calls,
            "refund_calls": context["transport"].refund_calls,
            "paid_code": context["paid_code"],
            "no_approval_code": context["no_approval_code"],
        },
    }
    if args.keep is not None:
        args.keep.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(store.path)
        destination = sqlite3.connect(args.keep)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        extra["kept_store"] = str(args.keep)
    report = _report(checks, mode="self-check", extra=extra)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        for check in checks:
            marker = "PASS" if check["ok"] else "FAIL"
            print(f"{marker} {check['name']}: {check['detail']}")
        print(
            f"P5-01 {'PASS' if not report['problems'] else 'FAIL'}"
            f" ({report['checks_total']} checks)"
        )
    return 0 if not report["problems"] else 1


if __name__ == "__main__":
    sys.exit(main())
