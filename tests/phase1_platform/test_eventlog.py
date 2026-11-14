"""P1-02 — authoritative append-only event log, outbox and checkpoints.

Hermetic: a throwaway SQLite file under ``tmp_path``; no network, no service.
"""
from __future__ import annotations

import sqlite3

import pytest

from voicebridge.business import BusinessStore, eventlog
from voicebridge.business.errors import BusinessError
from voicebridge.business.ids import fingerprint


def _scope(store: BusinessStore, name: str = "Acme", slug: str = "acme"):
    tenant = store.create_tenant(f"{name} Holdings")
    business = store.create_business(tenant.tenant_id, name, slug=slug)
    return tenant, business


def _append(store, tenant, business, *, key, payload=None, aggregate="agg-1",
            kind="task.created", **overrides):
    values = {
        "actor": "tester",
        "idempotency_key": key,
        "business_id": business.business_id,
    }
    values.update(overrides)
    return eventlog.append_event(
        store,
        tenant.tenant_id,
        aggregate,
        kind,
        {"value": 1} if payload is None else payload,
        **values,
    )


def _count(store: BusinessStore, table: str, tenant_id: str | None = None) -> int:
    conn = sqlite3.connect(store.path)
    try:
        if tenant_id is None:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        else:
            row = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE tenant_id=?", (tenant_id,)
            ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def test_aggregate_sequence_is_monotonic_and_ordered(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    events = [
        _append(store, tenant, business, key=f"key-{n}", payload={"n": n})
        for n in range(1, 4)
    ]
    assert [event.sequence for event in events] == [1, 2, 3]
    assert [event.event_id for event in events] == sorted(
        event.event_id for event in events
    )

    all_events = eventlog.read_events(store, tenant.tenant_id, "agg-1")
    assert [event.sequence for event in all_events] == [1, 2, 3]
    assert [event.payload["n"] for event in all_events] == [1, 2, 3]

    tail = eventlog.read_events(store, tenant.tenant_id, "agg-1", after_seq=1)
    assert [event.sequence for event in tail] == [2, 3]

    # A different aggregate has its own sequence space.
    other = _append(store, tenant, business, key="other-key", aggregate="agg-2")
    assert other.sequence == 1


def test_correlation_and_causation_chain_is_preserved(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    root = _append(
        store, tenant, business, key="root", correlation_id="cor_root",
    )
    child = _append(
        store,
        tenant,
        business,
        key="child",
        correlation_id="cor_root",
        causation_id=root.event_id,
    )
    assert root.correlation_id == "cor_root"
    assert root.causation_id is None
    assert child.correlation_id == "cor_root"
    assert child.causation_id == root.event_id

    # An omitted correlation id is still a real id, never an empty string.
    auto = _append(store, tenant, business, key="auto")
    assert auto.correlation_id.startswith("cor_")


def test_tenant_isolation_on_append_and_read(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant_a, business_a = _scope(store, "Tenant A", "tenant-a")
    tenant_b, business_b = _scope(store, "Tenant B", "tenant-b")
    _append(store, tenant_a, business_a, key="a-1")
    _append(store, tenant_b, business_b, key="b-1")

    a_events = eventlog.read_events(store, tenant_a.tenant_id, "agg-1")
    b_events = eventlog.read_events(store, tenant_b.tenant_id, "agg-1")
    assert [event.tenant_id for event in a_events] == [tenant_a.tenant_id]
    assert [event.tenant_id for event in b_events] == [tenant_b.tenant_id]

    # The same aggregate id/sequence exists independently per tenant.
    assert a_events[0].sequence == b_events[0].sequence == 1

    with pytest.raises(BusinessError):
        eventlog.append_event(
            store,
            tenant_a.tenant_id,
            "agg-1",
            "task.created",
            {"value": 2},
            actor="tester",
            idempotency_key="cross",
            business_id=business_b.business_id,
        )


def test_idempotent_replay_returns_the_existing_event(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    first = _append(store, tenant, business, key="same-key", payload={"a": 1})
    second = _append(store, tenant, business, key="same-key", payload={"a": 1})

    assert first.event_id == second.event_id
    assert first.sequence == second.sequence == 1
    assert _count(store, "bos_event") == 1
    assert _count(store, "bos_outbox") == 1


def test_same_key_with_different_payload_is_a_typed_conflict(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    _append(store, tenant, business, key="same-key", payload={"a": 1})

    with pytest.raises(BusinessError) as refused:
        _append(store, tenant, business, key="same-key", payload={"a": 2})
    assert refused.value.code == "BUSINESS_IDEMPOTENCY_CONFLICT"
    assert _count(store, "bos_event") == 1
    assert _count(store, "bos_outbox") == 1


def test_event_and_outbox_are_written_in_one_transaction(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    event = _append(store, tenant, business, key="atomic-1")

    assert _count(store, "bos_event", tenant.tenant_id) == 1
    assert _count(store, "bos_outbox", tenant.tenant_id) == 1

    with store.read() as conn:
        outbox = conn.execute(
            "SELECT * FROM bos_outbox WHERE tenant_id=?", (tenant.tenant_id,)
        ).fetchone()
    assert outbox["aggregate_id"] == "agg-1"
    assert outbox["kind"] == "event.task.created"
    assert outbox["request_hash"] == event.payload_hash
    assert outbox["idempotency_key"] == "event:atomic-1"


def test_outbox_failure_rolls_the_event_back(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)

    # Pre-empt the outbox key the append would derive. The event insert then
    # succeeds inside the transaction, the outbox insert collides, and both
    # must disappear together.
    with store.transaction() as conn:
        conn.execute(
            "INSERT INTO bos_outbox(outbox_id, tenant_id, business_id,"
            " aggregate_id, kind, idempotency_key, request_hash, payload_json,"
            " status, attempts, created_at, updated_at)"
            " VALUES('obx_seed',?,?,?,?,?,?,'{}','pending',0,?,?)",
            (
                tenant.tenant_id,
                business.business_id,
                "agg-1",
                "event.task.created",
                "event:rollback-key",
                "seed",
                "2026-01-01T00:00:00.000Z",
                "2026-01-01T00:00:00.000Z",
            ),
        )

    with pytest.raises(BusinessError):
        _append(store, tenant, business, key="rollback-key")

    assert _count(store, "bos_event", tenant.tenant_id) == 0
    assert _count(store, "bos_outbox", tenant.tenant_id) == 1


def test_event_log_rejects_update_and_delete(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    event = _append(store, tenant, business, key="append-only-1")

    conn = sqlite3.connect(store.path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE bos_event SET payload_json='{}' WHERE event_id=?",
                (event.event_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "DELETE FROM bos_event WHERE event_id=?", (event.event_id,)
            )
    finally:
        conn.close()


def test_replay_equals_a_fresh_read_and_survives_reopen(tmp_path):
    path = tmp_path / "business.db"
    store = BusinessStore(path)
    tenant, business = _scope(store)
    for n in range(1, 4):
        _append(store, tenant, business, key=f"key-{n}", payload={"n": n})

    replay_before = eventlog.replay(store, tenant.tenant_id, "agg-1")
    read_before = eventlog.read_events(store, tenant.tenant_id, "agg-1")
    assert [event.event_id for event in replay_before] == [
        event.event_id for event in read_before
    ]

    reopened = BusinessStore(path)
    replay_after = eventlog.replay(reopened, tenant.tenant_id, "agg-1")
    assert [event.event_id for event in replay_after] == [
        event.event_id for event in replay_before
    ]
    assert [event.payload for event in replay_after] == [
        {"n": 1},
        {"n": 2},
        {"n": 3},
    ]


def test_projection_checkpoint_advances_and_survives_restart(tmp_path):
    path = tmp_path / "business.db"
    store = BusinessStore(path)
    tenant, business = _scope(store)

    assert eventlog.read_projection_checkpoint(
        store, tenant.tenant_id, "office-tower"
    ) is None

    saved = eventlog.save_projection_checkpoint(
        store, tenant.tenant_id, "office-tower", 2
    )
    assert saved.last_seq == 2

    reopened = BusinessStore(path)
    checkpoint = eventlog.read_projection_checkpoint(
        reopened, tenant.tenant_id, "office-tower"
    )
    assert checkpoint is not None
    assert checkpoint.last_seq == 2

    advanced = eventlog.save_projection_checkpoint(
        reopened, tenant.tenant_id, "office-tower", 5
    )
    assert advanced.last_seq == 5

    with pytest.raises(BusinessError) as refused:
        eventlog.save_projection_checkpoint(
            reopened, tenant.tenant_id, "office-tower", 1
        )
    assert refused.value.code == eventlog.PROJECTION_REWIND_CODE


def test_payload_is_redacted_before_hashing_and_storage(tmp_path):
    store = BusinessStore(tmp_path / "business.db")
    tenant, business = _scope(store)
    event = _append(
        store,
        tenant,
        business,
        key="secret-key",
        payload={"api_key": "sk-live-1234567890", "note": "ok"},
    )
    redacted = {"api_key": "***REDACTED***", "note": "ok"}
    assert event.payload == redacted
    assert event.payload_hash == fingerprint(redacted)

    conn = sqlite3.connect(store.path)
    try:
        raw = conn.execute(
            "SELECT payload_json FROM bos_event WHERE event_id=?",
            (event.event_id,),
        ).fetchone()[0]
        outbox_raw = conn.execute(
            "SELECT payload_json FROM bos_outbox WHERE idempotency_key=?",
            ("event:secret-key",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert "sk-live-1234567890" not in raw
    assert "sk-live-1234567890" not in outbox_raw
