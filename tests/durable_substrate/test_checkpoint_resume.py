"""Checkpoint log: append-only, ordered, restart-stable, hash-bound.

Hermetic: a throwaway SQLite file under ``tmp_path``; no network, no service.
"""
from __future__ import annotations

import hashlib

import pytest

from voicebridge.business import BusinessStore
from voicebridge.business import durable
from voicebridge.business.errors import BusinessError
from voicebridge.business.ids import canonical_json


def _scope(store: BusinessStore, name: str = "Acme", slug: str = "acme"):
    tenant = store.create_tenant(f"{name} Holdings")
    business = store.create_business(tenant.tenant_id, name, slug=slug)
    return tenant, business


def test_append_sequence_is_ordered_and_restart_stable(tmp_path):
    path = tmp_path / "business.db"
    store = BusinessStore(path)
    tenant, business = _scope(store)

    for sequence in (1, 2, 3):
        durable.record_checkpoint(
            store,
            tenant.tenant_id,
            business.business_id,
            "run-1",
            sequence,
            state={"step": sequence},
            pending_actions=[{"action": f"step-{sequence}"}],
            tool_receipts=[{"tool": "fake", "id": str(sequence)}],
            artifact_ids=[f"art-{sequence}"],
            workspace_revision=f"ws-{sequence}",
            policy_revision="pol-7",
            actor="tester",
        )

    rows = durable.list_checkpoints(store, tenant.tenant_id, "run-1")
    assert [row.sequence for row in rows] == [1, 2, 3]
    assert [row.state["step"] for row in rows] == [1, 2, 3]
    assert all(row.policy_revision == "pol-7" for row in rows)
    assert rows[2].workspace_revision == "ws-3"

    # Reopen: the ordered log is read back from disk, not from memory.
    reopened = BusinessStore(path)
    again = durable.list_checkpoints(reopened, tenant.tenant_id, "run-1")
    assert [row.checkpoint_id for row in again] == [row.checkpoint_id for row in rows]
    assert [row.request_hash for row in again] == [row.request_hash for row in rows]


def test_duplicate_sequence_is_a_typed_refusal_and_never_overwrites(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    first = durable.record_checkpoint(
        store, tenant.tenant_id, business.business_id, "run-1", 1,
        state={"step": 1}, actor="tester",
    )

    with pytest.raises(BusinessError) as refused:
        durable.record_checkpoint(
            store, tenant.tenant_id, business.business_id, "run-1", 1,
            state={"step": 999}, actor="tester",
        )
    assert refused.value.code == "BUSINESS_CHECKPOINT_EXISTS"

    rows = durable.list_checkpoints(store, tenant.tenant_id, "run-1")
    assert len(rows) == 1
    assert rows[0].checkpoint_id == first.checkpoint_id
    assert rows[0].state == {"step": 1}


def test_checkpoint_request_hash_is_sha256_of_canonical_json(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    checkpoint = durable.record_checkpoint(
        store,
        tenant.tenant_id,
        business.business_id,
        "run-hash",
        4,
        state={"b": 2, "a": 1},
        pending_actions=[{"z": 1}],
        tool_receipts=[],
        artifact_ids=["artifact-1"],
        workspace_revision="ws-9",
        policy_revision="pol-2",
        actor="tester",
    )
    expected = hashlib.sha256(
        canonical_json(
            {
                "run_id": "run-hash",
                "sequence": 4,
                "state": {"b": 2, "a": 1},
                "pending_actions": [{"z": 1}],
                "tool_receipts": [],
                "artifact_ids": ["artifact-1"],
                "workspace_revision": "ws-9",
                "policy_revision": "pol-2",
            }
        ).encode("utf-8")
    ).hexdigest()
    assert checkpoint.request_hash == expected


def test_checkpoint_payloads_are_redacted(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    checkpoint = durable.record_checkpoint(
        store,
        tenant.tenant_id,
        business.business_id,
        "run-secret",
        1,
        state={"api_key": "sk-abcdef123456", "note": "Bearer abcdef123456"},
        actor="tester",
    )
    assert checkpoint.state["api_key"] == "***REDACTED***"
    assert "abcdef123456" not in checkpoint.state["note"]


def test_checkpoint_append_only_triggers_reject_update_and_delete(tmp_path):
    import sqlite3

    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    checkpoint = durable.record_checkpoint(
        store, tenant.tenant_id, business.business_id, "run-x", 1,
        state={"step": 1}, actor="tester",
    )
    conn = sqlite3.connect(store.path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE bos_checkpoint SET state_json='{}' WHERE checkpoint_id=?",
                (checkpoint.checkpoint_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "DELETE FROM bos_checkpoint WHERE checkpoint_id=?",
                (checkpoint.checkpoint_id,),
            )
    finally:
        conn.close()


def test_checkpoints_are_tenant_scoped(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant_a, business_a = _scope(store, "Acme", "acme")
    tenant_b, business_b = _scope(store, "Globex", "globex")
    durable.record_checkpoint(
        store, tenant_a.tenant_id, business_a.business_id, "run-a", 1,
        state={"owner": "a"}, actor="tester",
    )
    durable.record_checkpoint(
        store, tenant_b.tenant_id, business_b.business_id, "run-b", 1,
        state={"owner": "b"}, actor="tester",
    )
    assert durable.list_checkpoints(store, tenant_a.tenant_id, "run-a")[0].state == {
        "owner": "a"
    }
    assert durable.list_checkpoints(store, tenant_a.tenant_id, "run-b") == []
    assert durable.list_checkpoints(store, tenant_b.tenant_id, "run-a") == []


def test_unknown_business_is_refused(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant = store.create_tenant("Acme Holdings")
    with pytest.raises(BusinessError) as refused:
        durable.record_checkpoint(
            store, tenant.tenant_id, "biz_missing", "run-1", 1,
            state={}, actor="tester",
        )
    assert refused.value.code == "BUSINESS_NOT_FOUND"
