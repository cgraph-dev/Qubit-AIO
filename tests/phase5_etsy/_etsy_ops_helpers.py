"""Shared hermetic helpers for the P5-01 Etsy operations tests."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from voicebridge.business import BusinessStore
from voicebridge.business.etsy_operations import (
    EtsyOperations,
    InMemoryEtsySandboxTransport,
    sandbox_etsy_adapter,
)

LISTING = {
    "title": "Minimal weekly planner",
    "description": "A printable weekly planner with a clean layout.",
    "price_minor": 600,
    "currency": "USD",
    "digital_files": ["planner.pdf"],
    "tags": ["planner", "printable"],
    "asset_version": "v1",
}


def build_ops(store: BusinessStore, **transport_kwargs):
    transport = InMemoryEtsySandboxTransport(**transport_kwargs)
    adapter = sandbox_etsy_adapter(transport)
    return EtsyOperations(store, adapter=adapter), transport


def record_inputs(ops: EtsyOperations, tenant, business, *, niche="niche-1"):
    ops.record_niche(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:etsy",
        niche_id=niche,
        title="Printable planners",
        evidence={
            "demand": {"label": "UNKNOWN", "note": "no market data yet"},
            "policy": {"label": "FACT", "note": "digital downloads only"},
        },
        source_ref="research:planner-niche:1",
    )
    ops.reference_asset(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:etsy",
        asset_ref="asset:planner-v1",
        version="v1",
        license="original-work",
        license_source="creator-owned",
        paid=False,
        redistribution_allowed=True,
    )
    return niche


def create_listing(
    ops: EtsyOperations,
    tenant,
    business,
    *,
    key: str = "listing-1",
    listing=None,
    niche="niche-1",
):
    if listing is None:
        listing = LISTING
    return ops.create_listing(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:etsy",
        niche_id=niche,
        asset_id="asset:planner-v1",
        listing=listing,
        idempotency_key=key,
    )


def publish_listing(ops: EtsyOperations, tenant, created):
    return ops.publish_listing(
        tenant_id=tenant.tenant_id,
        job_id=created.job.job_id,
        actor="agent:etsy",
    )


def approve(ops: EtsyOperations, tenant, created, *, decided_by="owner"):
    return ops.approve_listing(
        tenant_id=tenant.tenant_id,
        approval_id=created.approval.approval_id,
        decided_by=decided_by,
    )


def record_order(ops: EtsyOperations, tenant, business, created, **kwargs):
    payload = {
        "tenant_id": tenant.tenant_id,
        "business_id": business.business_id,
        "actor": "agent:etsy",
        "job_id": created.job.job_id,
        "order_id": "order-1",
        "receipt_id": "receipt-1",
        "amount_minor": 600,
        "currency": "USD",
        "buyer_ref": "buyer:sha256:001",
    }
    payload.update(kwargs)
    return ops.record_order(**payload)


def refund_flow(ops: EtsyOperations, tenant, business, *, amount_minor=200):
    requested = ops.request_refund(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        actor="agent:etsy",
        order_id="order-1",
        amount_minor=amount_minor,
        reason="planner did not match the preview",
        idempotency_key="refund-1",
        case_ref="case-1",
    )
    ops.approve_refund(
        tenant_id=tenant.tenant_id,
        approval_id=requested.approval.approval_id,
        decided_by="owner",
    )
    return ops.refund(
        tenant_id=tenant.tenant_id,
        job_id=requested.job.job_id,
        actor="agent:etsy",
    )


def artifact_by_name(store: BusinessStore, tenant_id, name: str):
    for artifact in store.list_artifacts(tenant_id):
        if artifact.name == name:
            return artifact
    raise AssertionError(f"no artifact named {name!r}")


def tamper_artifact_body(store: BusinessStore, artifact_id: str, mutator):
    conn = sqlite3.connect(store.path)
    try:
        row = conn.execute(
            "SELECT body FROM bos_artifact WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        body = json.loads(row[0])
        mutator(body)
        import hashlib

        text = json.dumps(body, sort_keys=True, separators=(",", ":"))
        conn.execute(
            "UPDATE bos_artifact SET body=?, sha256=? WHERE artifact_id=?",
            (text, hashlib.sha256(text.encode("utf-8")).hexdigest(), artifact_id),
        )
        conn.commit()
    finally:
        conn.close()


def ledger_rows(store: BusinessStore, tenant_id, account: str | None = None):
    conn = sqlite3.connect(store.path)
    try:
        query = "SELECT account, direction, amount_minor FROM bos_ledger_entry WHERE tenant_id=?"
        params = [tenant_id]
        if account is not None:
            query += " AND account=?"
            params.append(account)
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def event_rows(store: BusinessStore, tenant_id):
    conn = sqlite3.connect(store.path)
    try:
        rows = conn.execute(
            "SELECT seq, type, idempotency_key, payload_hash, payload_json"
            " FROM bos_event WHERE tenant_id=? ORDER BY seq",
            (tenant_id,),
        ).fetchall()
        return rows
    finally:
        conn.close()


def count_artifacts(store: BusinessStore, kind: str) -> int:
    conn = sqlite3.connect(store.path)
    try:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM bos_artifact WHERE kind=?", (kind,)
            ).fetchone()[0]
        )
    finally:
        conn.close()


def path_of(store: BusinessStore) -> Path:
    return Path(store.path)
