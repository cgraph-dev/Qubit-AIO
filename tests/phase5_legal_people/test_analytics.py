"""P5-07 KPI contract formula, drill-through, freshness and staleness."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from voicebridge.business import analytics
from voicebridge.business.errors import BusinessError


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _task(store, tenant, business, title, *, status="open", created_at=None):
    task = store.create_task(tenant.tenant_id, business.business_id, title)
    moment = created_at or _iso(datetime.now(timezone.utc))
    if status != "open" or created_at is not None:
        conn = sqlite3.connect(store.path)
        try:
            conn.execute(
                "UPDATE bos_task SET status=?, created_at=?, updated_at=?"
                " WHERE tenant_id=? AND task_id=?",
                (status, moment, moment, tenant.tenant_id, task.task_id),
            )
            conn.commit()
        finally:
            conn.close()
    return task


def _contract(store, tenant, business, *, formula=None, staleness=3600, **kwargs):
    return analytics.create_contract(
        store,
        tenant.tenant_id,
        name=kwargs.pop("name", "Open tasks"),
        formula=formula or {"op": "count", "table": "bos_task"},
        owner="alice",
        business_id=business.business_id,
        attribution_window_seconds=kwargs.pop("window", 7200),
        refresh_cadence_seconds=60,
        acceptable_staleness_seconds=staleness,
        idempotency_key=kwargs.pop("key", "kpi-1"),
        **kwargs,
    )


def test_kpi_contract_fields_compute_and_drill_through(store, scope):
    tenant, business = scope
    _task(store, tenant, business, "a", status="done")
    _task(store, tenant, business, "b", status="done")
    _task(store, tenant, business, "c", status="open")
    contract = _contract(
        store,
        tenant,
        business,
        name="Done tasks",
        formula={
            "op": "count",
            "table": "bos_task",
            "filter": {"status": "done"},
        },
        target=2.0,
        anomaly_threshold=1.0,
    )
    stored = analytics.get_contract(store, tenant.tenant_id, contract["metric_id"])
    assert stored["formula"]["filter"] == {"status": "done"}
    assert stored["source_tables"] == ["bos_task"]
    assert stored["timezone"] == "UTC"
    assert stored["owner"] == "alice"
    assert stored["refresh_cadence_seconds"] == 60
    assert stored["acceptable_staleness_seconds"] == 3600
    assert stored["target"] == 2.0
    assert stored["anomaly_threshold"] == 1.0

    result = analytics.compute(store, tenant.tenant_id, contract["metric_id"])
    assert result["status"] == "fresh"
    assert result["value"] == 2.0
    assert result["source_count"] == 2
    assert len(result["source_rows"]) == 2
    assert result["drillable"] is True
    assert result["anomaly"] is False
    assert result["methodology"] == "measured"
    assert result["derived"] is False

    row = analytics.drill(
        store,
        tenant.tenant_id,
        contract["metric_id"],
        table="bos_task",
        row_id=result["source_rows"][0]["row_id"],
    )
    assert row["row"]["status"] == "done"
    assert row["row"]["business_id"] == business.business_id


def test_kpi_ratio_and_sum(store, scope):
    tenant, business = scope
    _task(store, tenant, business, "a", status="done")
    _task(store, tenant, business, "b", status="open")
    ratio = _contract(
        store,
        tenant,
        business,
        name="Completion rate",
        formula={
            "op": "ratio",
            "numerator": {
                "op": "count",
                "table": "bos_task",
                "filter": {"status": "done"},
            },
            "denominator": {"op": "count", "table": "bos_task"},
        },
        key="kpi-ratio",
    )
    result = analytics.compute(store, tenant.tenant_id, ratio["metric_id"])
    assert result["status"] == "fresh"
    assert result["value"] == 0.5

    empty_denominator = _contract(
        store,
        tenant,
        business,
        name="Zero denominator",
        formula={
            "op": "ratio",
            "numerator": {"op": "count", "table": "bos_task"},
            "denominator": {
                "op": "count",
                "table": "bos_task",
                "filter": {"status": "nope"},
            },
        },
        key="kpi-zero",
    )
    zero = analytics.compute(
        store, tenant.tenant_id, empty_denominator["metric_id"]
    )
    assert zero["status"] == "unknown"
    assert zero["value"] is None
    assert zero["reason"] == "denominator_zero"


def test_stale_and_unknown_are_never_fabricated(store, scope):
    tenant, business = scope
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    _task(store, tenant, business, "old", status="done", created_at=_iso(old))
    stale_contract = _contract(
        store,
        tenant,
        business,
        name="Old work",
        formula={"op": "count", "table": "bos_task", "filter": {"status": "done"}},
        staleness=60,
        window=10800,
        key="kpi-stale",
    )
    stale = analytics.compute(store, tenant.tenant_id, stale_contract["metric_id"])
    assert stale["status"] == "stale"
    assert stale["value"] == 1.0
    assert stale["reason"] == "data_older_than_acceptable_staleness"

    empty_contract = _contract(
        store,
        tenant,
        business,
        name="Nothing",
        formula={"op": "count", "table": "bos_task", "filter": {"status": "missing"}},
        key="kpi-empty",
    )
    unknown = analytics.compute(store, tenant.tenant_id, empty_contract["metric_id"])
    assert unknown["status"] == "unknown"
    assert unknown["value"] is None
    assert unknown["source_count"] == 0

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    future_result = analytics.compute(
        store,
        tenant.tenant_id,
        empty_contract["metric_id"],
        as_of=_iso(future),
    )
    assert future_result["status"] == "unknown"
    assert future_result["reason"] == "as_of_in_future"
    assert future_result["value"] is None


def test_formula_validation_refuses_bad_shapes(store, scope):
    tenant, business = scope
    with pytest.raises(BusinessError) as exc:
        analytics.validate_formula({"op": "drop", "table": "bos_task"})
    assert exc.value.code == analytics.METRIC_FORMULA_INVALID_CODE

    with pytest.raises(BusinessError) as exc:
        analytics.validate_formula({"op": "count", "table": "bos_secret"})
    assert exc.value.code == analytics.METRIC_SOURCE_UNKNOWN_CODE

    with pytest.raises(BusinessError) as exc:
        analytics.validate_formula(
            {"op": "count", "table": "bos_task", "field": "title"}
        )
    assert exc.value.code == analytics.METRIC_FORMULA_INVALID_CODE

    with pytest.raises(BusinessError) as exc:
        analytics.validate_formula(
            {"op": "count", "table": "bos_task", "filter": {"not_a_column": 1}}
        )
    assert exc.value.code == analytics.METRIC_FORMULA_INVALID_CODE

    with pytest.raises(BusinessError):
        _contract(
            store,
            tenant,
            business,
            formula={"op": "count", "table": "bos_secret"},
            key="kpi-bad",
        )
    with pytest.raises(BusinessError):
        _contract(
            store,
            tenant,
            business,
            methodology="derived",
            key="kpi-derived-bad",
        )


def test_derived_estimate_is_labelled_and_anomaly_is_computed(store, scope):
    tenant, business = scope
    _task(store, tenant, business, "a", status="done")
    contract = _contract(
        store,
        tenant,
        business,
        name="Projected revenue",
        formula={
            "op": "count",
            "table": "bos_task",
            "filter": {"status": "done"},
        },
        methodology="derived",
        derivation_note="count * fixed average ticket",
        target=10.0,
        anomaly_threshold=1.0,
        key="kpi-derived",
    )
    result = analytics.compute(store, tenant.tenant_id, contract["metric_id"])
    assert result["methodology"] == "derived"
    assert result["derived"] is True
    assert result["derivation_note"] == "count * fixed average ticket"
    assert result["anomaly"] is True


def test_kpi_is_tenant_scoped(store, scope):
    tenant, business = scope
    contract = _contract(store, tenant, business)
    other = store.create_tenant("Other")
    with pytest.raises(BusinessError):
        analytics.get_contract(store, other.tenant_id, contract["metric_id"])
    with pytest.raises(BusinessError):
        analytics.compute(store, other.tenant_id, contract["metric_id"])
    assert analytics.list_contracts(store, other.tenant_id) == []


def test_snapshot_value_can_be_checked_against_sources(store, scope):
    tenant, business = scope
    _task(store, tenant, business, "a", status="done")
    contract = _contract(store, tenant, business)
    result = analytics.compute(store, tenant.tenant_id, contract["metric_id"])
    snapshot = analytics.record_snapshot(
        store, tenant.tenant_id, contract["metric_id"], result=result
    )
    assert snapshot["value"] == 1.0
    assert analytics.snapshot_matches(
        store, tenant.tenant_id, contract["metric_id"]
    ) is True
    conn = sqlite3.connect(store.path)
    try:
        conn.execute(
            "UPDATE bos_metric_snapshot SET value=999 WHERE snapshot_id=?",
            (snapshot["snapshot_id"],),
        )
        conn.commit()
    finally:
        conn.close()
    assert analytics.snapshot_matches(
        store, tenant.tenant_id, contract["metric_id"]
    ) is False
