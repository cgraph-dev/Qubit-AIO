"""P4-07 operator timeline: causal, evidence-backed, replay-labelled.

Hermetic: a throwaway SQLite store under ``tmp_path``. These tests pin the
contract the Office shell depends on:

* every run status derives from the durable job row;
* ``completed`` requires an artifact or provider receipt — an unverified
  completion claim is projected as ``unknown`` with the claim preserved;
* ``unknown`` stays ``unknown`` and adds a reconciliation item;
* cost comes from the ledger rows and tokens only from recorded usage;
* ordering is causal (a caused record never precedes its cause);
* replay is a labelled simulation with stubbed effects and no writes;
* the projection is tenant-gated and read-only;
* an unavailable source is a typed state, not an empty success.

No network, no credentials, no live service, no real business data.
"""
from __future__ import annotations

import sqlite3

import pytest

from voicebridge import qubit_compat as HC
from voicebridge import webui as W
from voicebridge.business import (
    AgentControlService,
    BusinessStore,
    durable,
    eventlog,
    identity,
    jobs,
    ledger,
    sessions,
)
from voicebridge.business.errors import BusinessError
from voicebridge.business.operator_timeline import (
    SCHEMA,
    operator_timeline,
)


class _Cfg:
    def __init__(self, state_dir):
        self._state = state_dir

    def state_dir(self):
        return str(self._state)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    cfg = _Cfg(tmp_path)
    store = BusinessStore.from_config(cfg)
    return cfg, store


def _scope(store: BusinessStore, name: str = "Acme", slug: str = "acme"):
    tenant = store.create_tenant(f"{name} Holdings")
    business = store.create_business(tenant.tenant_id, name, slug=slug)
    return tenant, business


def _seed(store: BusinessStore, *, name="Acme", slug="acme"):
    tenant, business = _scope(store, name=name, slug=slug)
    owner = identity.create_principal(
        store, tenant.tenant_id, kind="owner", external_ref=f"{slug}-owner")
    agent_principal = identity.create_principal(
        store, tenant.tenant_id, kind="agent", external_ref=f"{slug}-ada")
    service = AgentControlService(store)
    agent, _ = service.create(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        role_id="role.analyst",
        department="Research",
        display_name="Ada",
        human_owner="owner",
        provider="provider-a",
        model="model-a",
        idempotency_key=f"{slug}-agent",
        security_principal_id=agent_principal.principal_id,
        room="floor-001-main",
        desk="d1",
    )
    session = sessions.create_session(
        store, tenant.tenant_id, business.business_id, agent.agent_id,
        owner_principal=owner.principal_id, actor="owner",
        task_title="Draft the report",
    )
    task = store.create_task(
        tenant.tenant_id, business.business_id, "Ship the report",
        assigned_to=agent.agent_id,
    )
    return {
        "tenant": tenant,
        "business": business,
        "owner": owner,
        "agent": agent,
        "session": session,
        "task": task,
    }


def _job(store, seeded, key="job-1", state="running", *, output=None):
    job, _ = jobs.create(
        store,
        tenant_id=seeded["tenant"].tenant_id,
        business_id=seeded["business"].business_id,
        kind="report",
        idempotency_key=key,
        input={
            "task_id": seeded["task"].task_id,
            "agent_id": seeded["agent"].agent_id,
            "session_id": seeded["session"].session_id,
        },
    )
    jobs.enqueue(store, seeded["tenant"].tenant_id, job.job_id)
    if state in ("running", "completed", "unknown", "failed"):
        jobs.claim(store, seeded["tenant"].tenant_id, job.job_id,
                   worker="worker-1")
    if state == "completed":
        jobs.finish(store, seeded["tenant"].tenant_id, job.job_id,
                    "completed", actor="worker-1", output=output)
    elif state == "failed":
        jobs.finish(store, seeded["tenant"].tenant_id, job.job_id,
                    "failed", actor="worker-1")
    elif state == "unknown":
        jobs.mark_unknown(store, seeded["tenant"].tenant_id, job.job_id,
                          actor="worker-1", reason="provider timed out")
    elif state == "queued":
        pass
    return jobs.get(store, seeded["tenant"].tenant_id, job.job_id)


def _call(cfg, query=None):
    return HC.dispatch(
        cfg, "GET", "/api/office/business/operator/timeline",
        query=query or {}, body={})


def _audit_count(store: BusinessStore) -> int:
    with store.read() as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM bos_audit_event").fetchone()[0])


def _by_id(projection, prefix):
    return [item for item in projection["items"]
            if item["item_id"].startswith(prefix)]


# ---------------------------------------------------------------------------
# Route and authority
# ---------------------------------------------------------------------------
def test_route_is_registered_and_is_a_read_authority():
    route, _ = HC.match("GET", "/api/office/business/operator/timeline")
    assert route is not None
    assert route["handler"] == "h_business_operator_timeline"
    assert W.qubit_read_authority(
        "/api/office/business/operator/timeline")


def test_unknown_tenant_is_a_typed_refusal(env):
    cfg, store = env
    _scope(store)
    code, body = _call(cfg, {"tenant_id": "ten_missing"})
    assert code == 404
    assert body["code"] == "BUSINESS_NOT_FOUND"


# ---------------------------------------------------------------------------
# Status truth
# ---------------------------------------------------------------------------
def test_run_completed_with_artifact_is_completed(env):
    cfg, store = env
    seeded = _seed(store)
    job = _job(store, seeded, state="completed")
    artifact = store.record_artifact(
        seeded["tenant"].tenant_id, seeded["business"].business_id,
        kind="report", name="report.txt", body="done", job_id=job.job_id)
    projection = operator_timeline(store, seeded["tenant"].tenant_id)
    run = _by_id(projection, "run:")[0]
    assert run["status"] == "completed"
    assert run["completed"] is True
    assert run["evidence"] is True
    assert any(ref["kind"] == "artifact"
               and ref["id"] == artifact.artifact_id
               for ref in run["evidence_refs"])
    artifact_item = _by_id(projection, "artifact:")[0]
    assert artifact_item["status"] == "completed"
    assert artifact_item["evidence"] is True


def test_run_completed_with_receipt_is_completed(env):
    cfg, store = env
    seeded = _seed(store)
    job = _job(store, seeded, state="completed")
    entry, _ = ledger.append(
        store,
        tenant_id=seeded["tenant"].tenant_id,
        business_id=seeded["business"].business_id,
        account="revenue",
        direction="credit",
        amount_minor=2500,
        currency="USD",
        idempotency_key="lgr-1",
        job_id=job.job_id,
    )
    projection = operator_timeline(store, seeded["tenant"].tenant_id)
    run = _by_id(projection, "run:")[0]
    assert run["status"] == "completed"
    assert any(ref["kind"] == "receipt" and ref["id"] == entry.entry_id
               for ref in run["evidence_refs"])
    receipt = _by_id(projection, "receipt:")[0]
    assert receipt["evidence"] is True
    assert receipt["cost"]["amount_minor"] == 2500


def test_completion_claim_without_evidence_stays_unknown(env):
    cfg, store = env
    seeded = _seed(store)
    _job(store, seeded, state="completed")
    projection = operator_timeline(store, seeded["tenant"].tenant_id)
    run = _by_id(projection, "run:")[0]
    assert run["claimed_status"] == "completed"
    assert run["status"] == "unknown"
    assert run["unknown"] is True
    assert run["completed"] is False
    assert "no artifact" in (run["reason"] or "")
    # No item anywhere may claim completion without evidence.
    for item in projection["items"]:
        assert not (item["status"] == "completed" and not item["evidence"])


def test_pending_and_running_runs_are_not_completed(env):
    cfg, store = env
    seeded = _seed(store)
    _job(store, seeded, key="job-q", state="queued")
    _job(store, seeded, key="job-r", state="running")
    projection = operator_timeline(store, seeded["tenant"].tenant_id)
    runs = _by_id(projection, "run:")
    assert {run["status"] for run in runs} == {"queued", "executing"}
    assert all(run["completed"] is False for run in runs)
    assert projection["counts"]["completed"] == 0


def test_unknown_stays_unknown_and_adds_reconciliation(env):
    cfg, store = env
    seeded = _seed(store)
    _job(store, seeded, state="unknown")
    projection = operator_timeline(store, seeded["tenant"].tenant_id)
    run = _by_id(projection, "run:")[0]
    assert run["status"] == "unknown"
    assert run["unknown"] is True
    assert run["completed"] is False
    reconciliation = _by_id(projection, "reconciliation:")
    assert len(reconciliation) == 1
    assert reconciliation[0]["unknown"] is True
    assert reconciliation[0]["pending"] is True
    assert projection["counts"]["reconciliations"] == 1


def test_task_done_without_artifact_is_not_completed(env):
    cfg, store = env
    seeded = _seed(store)
    store.set_task_status(
        seeded["tenant"].tenant_id, seeded["task"].task_id, "done",
        actor="owner")
    projection = operator_timeline(store, seeded["tenant"].tenant_id)
    task = _by_id(projection, "task:")[0]
    assert task["claimed_status"] == "done"
    assert task["status"] == "done-unverified"
    assert task["completed"] is False
    assert "unverified" in (task["reason"] or "")


# ---------------------------------------------------------------------------
# Ordering / causality
# ---------------------------------------------------------------------------
def test_event_with_causation_sorts_after_its_cause(env):
    cfg, store = env
    seeded = _seed(store)
    tenant = seeded["tenant"].tenant_id
    first = eventlog.append_event(
        store, tenant, seeded["task"].task_id, "task.assigned",
        {"task_id": seeded["task"].task_id, "stage": "assigned"},
        actor="owner", idempotency_key="ev-1",
        correlation_id="cor-1", now=1_000.0)
    second = eventlog.append_event(
        store, tenant, seeded["task"].task_id, "task.claimed",
        {"task_id": seeded["task"].task_id, "stage": "claimed"},
        actor="agent", idempotency_key="ev-2",
        correlation_id="cor-1", causation_id=first.event_id, now=1_000.0)
    projection = operator_timeline(store, tenant)
    ids = [item["item_id"] for item in projection["items"]
           if item["source"] == "bos_event"]
    assert ids.index(f"event:{first.event_id}") < ids.index(
        f"event:{second.event_id}")
    assert projection["items"][0]["at"] <= projection["items"][-1]["at"]


def test_timeline_is_chronological_with_stage_ties(env):
    cfg, store = env
    seeded = _seed(store)
    job = _job(store, seeded, state="completed")
    store.record_artifact(
        seeded["tenant"].tenant_id, seeded["business"].business_id,
        kind="report", name="r.txt", body="x", job_id=job.job_id)
    events = [
        eventlog.append_event(
            store, seeded["tenant"].tenant_id, seeded["task"].task_id,
            "task.assigned", {"task_id": seeded["task"].task_id},
            actor="owner", idempotency_key=f"e-{i}", correlation_id="cor")
        for i in range(3)
    ]
    projection = operator_timeline(store, seeded["tenant"].tenant_id)
    timestamps = [item["at"] for item in projection["items"]]
    assert timestamps == sorted(timestamps)
    # Every event is present exactly once.
    for event in events:
        assert len([item for item in projection["items"]
                    if item["item_id"] == f"event:{event.event_id}"]) == 1


# ---------------------------------------------------------------------------
# Cost, tokens, filters, limit
# ---------------------------------------------------------------------------
def test_cost_totals_match_ledger_rows(env):
    cfg, store = env
    seeded = _seed(store)
    job = _job(store, seeded, state="completed")
    tenant = seeded["tenant"].tenant_id
    business = seeded["business"].business_id
    ledger.append(store, tenant_id=tenant, business_id=business,
                  account="revenue", direction="credit", amount_minor=5000,
                  currency="USD", idempotency_key="l1", job_id=job.job_id)
    ledger.append(store, tenant_id=tenant, business_id=business,
                  account="fees", direction="debit", amount_minor=1200,
                  currency="USD", idempotency_key="l2", job_id=job.job_id)
    projection = operator_timeline(store, tenant)
    cost = projection["cost"]
    assert cost["debit_minor"] == 1200
    assert cost["credit_minor"] == 5000
    assert cost["net_minor"] == 3800
    assert cost["entries"] == 2
    assert cost["by_currency"]["USD"]["debit_minor"] == 1200


def test_tokens_come_from_recorded_usage_only(env):
    cfg, store = env
    seeded = _seed(store)
    job = _job(store, seeded, state="unknown")
    durable.record_checkpoint(
        store, seeded["tenant"].tenant_id, seeded["business"].business_id,
        run_id=job.job_id, sequence=1,
        state={"usage": {"prompt_tokens": 11, "completion_tokens": 4,
                         "total_tokens": 15}},
        actor="worker-1", job_id=job.job_id)
    job_two = _job(store, seeded, key="job-2", state="running")
    projection = operator_timeline(store, seeded["tenant"].tenant_id)
    run = next(item for item in projection["items"]
               if item["item_id"] == f"run:{job.job_id}")
    assert run["tokens"] == {"prompt": 11, "completion": 4, "total": 15}
    run_two = next(item for item in projection["items"]
                   if item["item_id"] == f"run:{job_two.job_id}")
    assert run_two["tokens"] is None
    assert projection["tokens"] == {
        "prompt": 11, "completion": 4, "total": 15, "runs": 1}


def test_filters_narrow_by_run_task_agent_session(env):
    cfg, store = env
    seeded = _seed(store)
    job = _job(store, seeded, state="unknown")
    tenant = seeded["tenant"].tenant_id
    projection = operator_timeline(store, tenant, run=job.job_id)
    assert all(item["run_id"] == job.job_id for item in projection["items"])
    assert projection["query"]["run"] == job.job_id
    projection = operator_timeline(store, tenant, task=seeded["task"].task_id)
    assert all(item["task_id"] == seeded["task"].task_id
               for item in projection["items"] if item["kind"] == "task")
    projection = operator_timeline(store, tenant, agent=seeded["agent"].agent_id)
    assert projection["query"]["agent"] == seeded["agent"].agent_id
    projection = operator_timeline(
        store, tenant, session=seeded["session"].session_id)
    assert projection["query"]["session"] == seeded["session"].session_id


def test_limit_bounds_and_reports_truncation(env):
    cfg, store = env
    seeded = _seed(store)
    _job(store, seeded, state="unknown")
    projection = operator_timeline(
        store, seeded["tenant"].tenant_id, limit=2)
    assert len(projection["items"]) == 2
    assert projection["counts"]["truncated"] is True
    with pytest.raises(BusinessError) as excinfo:
        operator_timeline(store, seeded["tenant"].tenant_id, limit=0)
    assert excinfo.value.code == "BUSINESS_BAD_INPUT"


def test_typed_empty_state(env):
    cfg, store = env
    tenant, _business = _scope(store)
    projection = operator_timeline(store, tenant.tenant_id)
    assert projection["state"] == "empty"
    assert projection["items"] == []
    assert projection["counts"]["total"] == 0


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------
def test_replay_is_labelled_and_effects_stubbed(env):
    cfg, store = env
    seeded = _seed(store)
    _job(store, seeded, state="unknown")
    before = _audit_count(store)
    projection = operator_timeline(
        store, seeded["tenant"].tenant_id, replay=True)
    assert projection["mode"] == "replay"
    assert projection["replay"]["enabled"] is True
    assert projection["replay"]["effects"] == "stubbed"
    assert projection["replay"]["real_calls"] is False
    assert "simulated" in projection["replay"]["label"].lower()
    for item in projection["items"]:
        assert item["replay"] is True
        assert item["effect"] == "stubbed"
        assert item["simulated"] is True
    # A replay is a viewer: no row was written.
    assert _audit_count(store) == before


def test_replay_does_not_change_truth(env):
    cfg, store = env
    seeded = _seed(store)
    _job(store, seeded, state="completed")  # no evidence
    live = operator_timeline(store, seeded["tenant"].tenant_id)
    replay = operator_timeline(
        store, seeded["tenant"].tenant_id, replay=True)
    live_run = _by_id(live, "run:")[0]
    replay_run = _by_id(replay, "run:")[0]
    assert live_run["status"] == replay_run["status"] == "unknown"
    assert replay_run["completed"] is False


# ---------------------------------------------------------------------------
# Tenant isolation, read-only, unavailable
# ---------------------------------------------------------------------------
def test_tenant_isolation(env):
    cfg, store = env
    first = _seed(store, name="Acme", slug="acme")
    second = _seed(store, name="Beta", slug="beta")
    _job(store, first, key="job-a", state="unknown")
    _job(store, second, key="job-b", state="unknown")
    projection = operator_timeline(store, first["tenant"].tenant_id)
    assert all(item["item_id"] != f"run:{_job_id(second, store)}"
               for item in projection["items"])
    assert projection["counts"]["runs"] == 1


def _job_id(seeded, store):
    with store.read() as conn:
        row = conn.execute(
            "SELECT job_id FROM bos_job WHERE tenant_id=?"
            " ORDER BY created_at DESC LIMIT 1",
            (seeded["tenant"].tenant_id,),
        ).fetchone()
    return row["job_id"]


def test_projection_is_read_only(env):
    cfg, store = env
    seeded = _seed(store)
    _job(store, seeded, state="unknown")
    before = _audit_count(store)
    _call(cfg, {"tenant_id": seeded["tenant"].tenant_id})
    assert _audit_count(store) == before


def test_unavailable_source_is_typed_not_empty(env, monkeypatch):
    cfg, store = env
    seeded = _seed(store)
    _job(store, seeded, state="unknown")

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is busy")

    monkeypatch.setattr(store, "list_tasks", boom)
    projection = operator_timeline(store, seeded["tenant"].tenant_id)
    assert projection["sources"]["tasks"]["state"] == "unavailable"
    assert projection["sources"]["tasks"]["code"] == "OperationalError"
    assert "tasks" in projection["unavailable_sources"]
    assert projection["state"] == "partial"


def test_http_payload_shape(env):
    cfg, store = env
    seeded = _seed(store)
    _job(store, seeded, state="unknown")
    code, body = _call(cfg, {"tenant_id": seeded["tenant"].tenant_id,
                             "replay": "1"})
    assert code == 200
    assert body["ok"] is True
    assert body["schema"] == SCHEMA
    projection = body["projection"]
    assert projection["schema"] == SCHEMA
    assert projection["mode"] == "replay"
    assert isinstance(projection["items"], list)
