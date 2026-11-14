"""Per-agent control contract and service.

Hermetic: a throwaway SQLite file under ``tmp_path``. No network, no service,
no credentials, no paid art. These tests pin the parts a UI will rely on:

* identity/runtime/permissions/safety round-trip and validation;
* deny-by-default scopes, with wildcards and unknown scopes refused by name;
* pause/resume/takeover/revoke/archive transitions and idempotent replay;
* a stop records ``interrupted`` and can never report ``completed``;
* the audit trail is append-only and ordered;
* budgets, turns, tokens and rate limits refuse before anything is stored.
"""
from __future__ import annotations

import sqlite3

import pytest

from voicebridge.business import AgentControlService, BusinessStore
from voicebridge.business.agents import Permissions, Safety
from voicebridge.business.errors import BusinessError


def _store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


def _scope(store: BusinessStore):
    tenant = store.create_tenant("Acme Holdings")
    business = store.create_business(tenant.tenant_id, "Acme", slug="acme")
    return tenant, business


def _svc(store: BusinessStore) -> AgentControlService:
    return AgentControlService(store)


def _create(svc: AgentControlService, tenant, business, **overrides):
    args = dict(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        role_id="role.social",
        department="Marketing",
        display_name="Ada",
        human_owner="owner",
        provider="provider-a",
        model="model-a",
        idempotency_key="create-1",
        permissions={"social": ["read", "draft"], "files": ["/tmp/workspace"]},
        safety={
            "autonomy": "L2",
            "daily_spend_minor": 1000,
            "monthly_spend_minor": 5000,
            "per_action_limit_minor": 400,
        },
    )
    args.update(overrides)
    return svc.create(**args)


def _scalar(store: BusinessStore, sql: str, params: tuple = ()) -> int:
    conn = sqlite3.connect(store.path)
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


# --- identity, runtime and deny-by-default permissions ------------------
def test_create_round_trips_identity_runtime_and_scopes(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)

    agent, created = _create(
        svc,
        tenant,
        business,
        room="Marketing/2-14",
        desk="desk-3",
        reasoning_effort="high",
        fallback_model="model-b",
        fallback_provider="provider-b",
        temperature=0.3,
        max_tokens=4096,
        schedule="0 9 * * 1-5",
        timezone="Europe/Bucharest",
        max_concurrent_turns=2,
    )

    assert created is True
    assert agent.agent_id.startswith("agt_")
    assert agent.status == "active"
    assert agent.tenant_id == tenant.tenant_id
    assert agent.business_id == business.business_id
    assert agent.role_id == "role.social"
    assert agent.department == "Marketing"
    assert agent.room == "Marketing/2-14"
    assert agent.desk == "desk-3"
    assert agent.display_name == "Ada"
    assert agent.human_owner == "owner"
    assert (agent.provider, agent.model) == ("provider-a", "model-a")
    assert agent.reasoning_effort == "high"
    assert (agent.fallback_provider, agent.fallback_model) == (
        "provider-b",
        "model-b",
    )
    assert agent.temperature == 0.3
    assert agent.max_tokens == 4096
    assert agent.schedule == "0 9 * * 1-5"
    assert agent.timezone == "Europe/Bucharest"
    assert agent.max_concurrent_turns == 2
    assert agent.permissions.social == ("draft", "read")
    assert agent.permissions.files == ("/tmp/workspace",)
    assert agent.safety.autonomy == "L2"

    fetched = svc.get(tenant.tenant_id, agent.agent_id)
    assert fetched == agent
    assert [item.agent_id for item in svc.list(tenant.tenant_id)] == [
        agent.agent_id
    ]
    assert svc.list(tenant.tenant_id, department="Marketing")[0].agent_id == (
        agent.agent_id
    )
    assert svc.list(tenant.tenant_id, status="paused") == []


def test_permissions_default_to_deny_every_category(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)

    agent, _ = _create(svc, tenant, business, permissions=None, safety=None)

    assert agent.permissions.is_empty()
    assert all(
        getattr(agent.permissions, category) == ()
        for category in (
            "tools",
            "toolsets",
            "files",
            "browser",
            "network",
            "terminal",
            "email",
            "social",
            "etsy",
            "payments",
            "data",
        )
    )
    # Conservative defaults: no spend without an explicit budget, manual
    # autonomy and a human escalation target.
    assert agent.safety.autonomy == "L0"
    assert agent.safety.daily_spend_minor == 0
    assert agent.safety.monthly_spend_minor == 0
    assert agent.safety.per_action_limit_minor == 0
    assert agent.safety.escalation_target == "human-owner"


@pytest.mark.parametrize(
    "permissions",
    [
        {"payments": ["*"]},
        {"tools": ["*"]},
        {"social": ["all"]},
        {"bogus": ["read"]},
        {"browser": ["teleport"]},
        {"files": ["/"]},
        {"files": ["../secrets"]},
        {"files": ["relative/path"]},
        {"network": "read"},
    ],
)
def test_unknown_or_wildcard_scopes_are_refused(tmp_path, permissions):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)

    with pytest.raises(BusinessError) as exc:
        _create(svc, tenant, business, permissions=permissions)

    assert exc.value.code == "AGENT_UNKNOWN_SCOPE"
    assert _scalar(store, "SELECT COUNT(*) FROM bos_agent") == 0


def test_models_autonomy_and_budgets_are_validated(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)

    cases = [
        dict(reasoning_effort="ultra"),
        dict(temperature=3.0),
        dict(max_tokens=0),
        dict(max_concurrent_turns=0),
        dict(timezone="Not/AZone"),
        dict(safety={"autonomy": "L9"}),
        dict(safety={"daily_spend_minor": -1}),
        dict(safety={"max_tokens_per_turn": 0}),
        dict(safety={"approval_required": ["telepathy"]}),
    ]
    for index, overrides in enumerate(cases):
        with pytest.raises(BusinessError) as exc:
            _create(
                svc,
                tenant,
                business,
                idempotency_key=f"bad-{index}",
                **overrides,
            )
        assert exc.value.code == "BUSINESS_BAD_INPUT", overrides
    assert _scalar(store, "SELECT COUNT(*) FROM bos_agent") == 0


# --- create/update idempotency -----------------------------------------
def test_create_idempotency_is_one_effect_and_conflicts_on_change(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)

    first, created_first = _create(svc, tenant, business)
    second, created_second = _create(svc, tenant, business)

    assert created_first is True
    assert created_second is False
    assert second.agent_id == first.agent_id
    assert _scalar(store, "SELECT COUNT(*) FROM bos_agent") == 1
    assert len(svc.audit(tenant.tenant_id, first.agent_id)) == 1

    with pytest.raises(BusinessError) as exc:
        _create(svc, tenant, business, model="model-different")
    assert exc.value.code == "BUSINESS_IDEMPOTENCY_CONFLICT"
    assert _scalar(store, "SELECT COUNT(*) FROM bos_agent") == 1


def test_update_merges_scopes_and_rejects_unknown_or_status_fields(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(svc, tenant, business)

    updated = svc.update(
        tenant.tenant_id,
        agent.agent_id,
        changes={
            "permissions": {"payments": ["read", "charge"]},
            "model": "model-c",
            "temperature": 0.1,
            "safety": {"daily_spend_minor": 2500},
        },
        idempotency_key="update-1",
    )

    assert updated.permissions.payments == ("charge", "read")
    assert updated.permissions.social == ("draft", "read")  # retained
    assert updated.model == "model-c"
    assert updated.temperature == 0.1
    assert updated.safety.daily_spend_minor == 2500
    assert updated.status == "active"
    assert updated.revision == 2

    with pytest.raises(BusinessError) as exc:
        svc.update(
            tenant.tenant_id,
            agent.agent_id,
            changes={"unknown_field": 1},
            idempotency_key="update-2",
        )
    assert exc.value.code == "BUSINESS_BAD_INPUT"

    with pytest.raises(BusinessError) as exc:
        svc.update(
            tenant.tenant_id,
            agent.agent_id,
            changes={"status": "paused"},
            idempotency_key="update-3",
        )
    assert exc.value.code == "BUSINESS_BAD_INPUT"


def test_update_idempotency_replays_the_recorded_snapshot(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(svc, tenant, business)

    args = dict(
        tenant_id=tenant.tenant_id,
        agent_id=agent.agent_id,
        changes={"model": "model-c"},
        idempotency_key="update-same",
    )
    first = svc.update(**args)
    second = svc.update(**args)
    assert second == first
    assert svc.get(tenant.tenant_id, agent.agent_id).model == "model-c"
    assert len(svc.audit(tenant.tenant_id, agent.agent_id)) == 2  # create+update

    with pytest.raises(BusinessError) as exc:
        svc.update(
            tenant.tenant_id,
            agent.agent_id,
            changes={"model": "model-d"},
            idempotency_key="update-same",
        )
    assert exc.value.code == "BUSINESS_IDEMPOTENCY_CONFLICT"


# --- lifecycle transitions ---------------------------------------------
def test_pause_resume_takeover_revoke_archive(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(svc, tenant, business)

    paused = svc.pause(
        tenant.tenant_id, agent.agent_id, idempotency_key="pause-1", actor="owner"
    )
    assert paused.status == "paused"
    with pytest.raises(BusinessError) as exc:
        svc.pause(
            tenant.tenant_id, agent.agent_id, idempotency_key="pause-2"
        )
    assert exc.value.code == "BUSINESS_BAD_STATE"

    taken = svc.takeover(
        tenant.tenant_id,
        agent.agent_id,
        idempotency_key="takeover-1",
        actor="owner",
    )
    assert taken.status == "paused"
    assert taken.taken_over_by == "owner"
    assert taken.taken_over_at is not None

    resumed = svc.resume(
        tenant.tenant_id, agent.agent_id, idempotency_key="resume-1"
    )
    assert resumed.status == "active"
    assert resumed.taken_over_by is None
    assert resumed.taken_over_at is None

    partial = svc.revoke(
        tenant.tenant_id,
        agent.agent_id,
        idempotency_key="revoke-partial",
        scopes=["social"],
    )
    assert partial.status == "active"
    assert partial.permissions.social == ()
    assert partial.permissions.payments == ()
    assert partial.permissions.files == ("/tmp/workspace",)

    revoked = svc.revoke(
        tenant.tenant_id, agent.agent_id, idempotency_key="revoke-all"
    )
    assert revoked.status == "revoked"
    assert revoked.permissions.is_empty()

    with pytest.raises(BusinessError) as exc:
        svc.resume(
            tenant.tenant_id, agent.agent_id, idempotency_key="resume-revoked"
        )
    assert exc.value.code == "BUSINESS_BAD_STATE"

    archived = svc.archive(
        tenant.tenant_id, agent.agent_id, idempotency_key="archive-1"
    )
    assert archived.status == "archived"
    assert archived.archived_at is not None
    with pytest.raises(BusinessError):
        svc.pause(
            tenant.tenant_id, agent.agent_id, idempotency_key="pause-archived"
        )
    with pytest.raises(BusinessError):
        svc.archive(
            tenant.tenant_id, agent.agent_id, idempotency_key="archive-2"
        )


def test_takeover_pauses_a_running_agent(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(svc, tenant, business)
    assert agent.status == "active"

    taken = svc.takeover(
        tenant.tenant_id,
        agent.agent_id,
        idempotency_key="takeover-active",
        actor="human-owner",
    )
    assert taken.status == "paused"
    assert taken.taken_over_by == "human-owner"


def test_lifecycle_idempotency_is_one_effect(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(svc, tenant, business)

    args = dict(
        tenant_id=tenant.tenant_id,
        agent_id=agent.agent_id,
        idempotency_key="pause-idem",
        actor="owner",
        note="handover",
    )
    first = svc.pause(**args)
    second = svc.pause(**args)
    assert second == first
    assert svc.get(tenant.tenant_id, agent.agent_id).status == "paused"
    pause_events = [
        event
        for event in svc.audit(tenant.tenant_id, agent.agent_id)
        if event.action == "agent.pause"
    ]
    assert len(pause_events) == 1
    assert _scalar(store, "SELECT COUNT(*) FROM bos_agent_operation") == 1

    with pytest.raises(BusinessError) as exc:
        svc.pause(
            tenant.tenant_id,
            agent.agent_id,
            idempotency_key="pause-idem",
            note="different",
        )
    assert exc.value.code == "BUSINESS_IDEMPOTENCY_CONFLICT"


def test_revoke_rejects_unknown_category(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(svc, tenant, business)

    with pytest.raises(BusinessError) as exc:
        svc.revoke(
            tenant.tenant_id,
            agent.agent_id,
            idempotency_key="revoke-bad",
            scopes=["telepathy"],
        )
    assert exc.value.code == "AGENT_UNKNOWN_SCOPE"
    assert svc.get(tenant.tenant_id, agent.agent_id).status == "active"


# --- turns and the honest stop -----------------------------------------
def test_stop_interrupts_and_never_reports_completion(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(svc, tenant, business)

    turn = svc.begin_turn(
        tenant.tenant_id, agent.agent_id, idempotency_key="turn-1"
    )
    assert turn.status == "active"

    stopped = svc.stop(
        tenant.tenant_id,
        agent.agent_id,
        idempotency_key="stop-1",
        actor="owner",
        note="operator pressed stop",
    )
    assert stopped.turn_id == turn.turn_id
    assert stopped.status == "interrupted"
    assert stopped.ended_at is not None
    assert stopped.note == "operator pressed stop"

    # The agent itself is still active; only the turn was interrupted.
    assert svc.get(tenant.tenant_id, agent.agent_id).status == "active"
    assert _scalar(
        store,
        "SELECT COUNT(*) FROM bos_agent_turn WHERE status='completed'",
    ) == 0
    assert _scalar(
        store,
        "SELECT COUNT(*) FROM bos_agent_turn WHERE status='interrupted'",
    ) == 1

    stop_events = [
        event
        for event in svc.audit(tenant.tenant_id, agent.agent_id)
        if event.action == "agent.turn.stop"
    ]
    assert len(stop_events) == 1
    assert stop_events[0].detail["state"] == "interrupted"

    # An interrupted turn cannot be completed afterwards; that would fake the
    # outcome the operator stopped.
    with pytest.raises(BusinessError) as exc:
        svc.finish_turn(
            tenant.tenant_id,
            agent.agent_id,
            turn.turn_id,
            state="completed",
            idempotency_key="finish-after-stop",
        )
    assert exc.value.code == "BUSINESS_BAD_STATE"


def test_finish_turn_refuses_interrupted_state(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(svc, tenant, business)
    turn = svc.begin_turn(
        tenant.tenant_id, agent.agent_id, idempotency_key="turn-1"
    )

    with pytest.raises(BusinessError) as exc:
        svc.finish_turn(
            tenant.tenant_id,
            agent.agent_id,
            turn.turn_id,
            state="interrupted",
            idempotency_key="finish-interrupted",
        )
    assert exc.value.code == "BUSINESS_BAD_INPUT"
    assert svc.get(tenant.tenant_id, agent.agent_id).status == "active"


def test_stop_without_active_turn_is_a_typed_refusal(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(svc, tenant, business)

    with pytest.raises(BusinessError) as exc:
        svc.stop(
            tenant.tenant_id, agent.agent_id, idempotency_key="stop-none"
        )
    assert exc.value.code == "AGENT_NO_ACTIVE_TURN"


def test_begin_turn_honours_concurrency_and_status(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(
        svc, tenant, business, max_concurrent_turns=1
    )

    first = svc.begin_turn(
        tenant.tenant_id,
        agent.agent_id,
        idempotency_key="turn-1",
        turn_id="trn_manual_1",
    )
    assert first.turn_id == "trn_manual_1"

    with pytest.raises(BusinessError) as exc:
        svc.begin_turn(
            tenant.tenant_id, agent.agent_id, idempotency_key="turn-2"
        )
    assert exc.value.code == "AGENT_TURN_LIMIT"

    svc.pause(tenant.tenant_id, agent.agent_id, idempotency_key="pause-1")
    with pytest.raises(BusinessError) as exc:
        svc.begin_turn(
            tenant.tenant_id, agent.agent_id, idempotency_key="turn-3"
        )
    assert exc.value.code == "BUSINESS_BAD_STATE"


def test_stop_idempotency_replays_the_interrupted_turn(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(svc, tenant, business)
    svc.begin_turn(tenant.tenant_id, agent.agent_id, idempotency_key="turn-1")

    args = dict(
        tenant_id=tenant.tenant_id,
        agent_id=agent.agent_id,
        idempotency_key="stop-idem",
    )
    first = svc.stop(**args)
    second = svc.stop(**args)
    assert second == first
    assert _scalar(
        store,
        "SELECT COUNT(*) FROM bos_audit_event WHERE action='agent.turn.stop'",
    ) == 1


# --- audit -------------------------------------------------------------
def test_audit_is_ordered_and_append_only(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(svc, tenant, business)
    svc.pause(tenant.tenant_id, agent.agent_id, idempotency_key="pause-1")
    svc.resume(tenant.tenant_id, agent.agent_id, idempotency_key="resume-1")

    actions = [event.action for event in svc.audit(tenant.tenant_id, agent.agent_id)]
    assert actions == ["agent.create", "agent.pause", "agent.resume"]

    conn = sqlite3.connect(store.path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE bos_audit_event SET outcome='refused' WHERE tenant_id=?",
                (tenant.tenant_id,),
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "DELETE FROM bos_audit_event WHERE tenant_id=?",
                (tenant.tenant_id,),
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE bos_agent_operation SET action='tamper'")
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM bos_agent_operation")
    finally:
        conn.close()


# --- budgets, usage, approvals -----------------------------------------
def test_budget_overflow_is_refused_before_storage(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(
        svc,
        tenant,
        business,
        safety={
            "daily_spend_minor": 1000,
            "monthly_spend_minor": 1000,
            "per_action_limit_minor": 1000,
            "max_turns_per_day": 10,
        },
    )

    first = svc.record_usage(
        tenant.tenant_id,
        agent.agent_id,
        idempotency_key="usage-1",
        cost_minor=600,
        kind="tool",
    )
    assert first.cost_minor == 600

    with pytest.raises(BusinessError) as exc:
        svc.record_usage(
            tenant.tenant_id,
            agent.agent_id,
            idempotency_key="usage-2",
            cost_minor=500,
            kind="tool",
        )
    assert exc.value.code == "AGENT_BUDGET_EXCEEDED"
    assert _scalar(store, "SELECT COUNT(*) FROM bos_agent_usage") == 1
    assert _scalar(
        store,
        "SELECT COUNT(*) FROM bos_audit_event WHERE action='agent.usage'",
    ) == 1

    with pytest.raises(BusinessError) as exc:
        svc.record_usage(
            tenant.tenant_id,
            agent.agent_id,
            idempotency_key="usage-per-action",
            cost_minor=1001,
            kind="tool",
        )
    assert exc.value.code == "AGENT_BUDGET_EXCEEDED"


def test_turn_token_and_rate_limits_are_enforced(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(
        svc,
        tenant,
        business,
        safety={
            "daily_spend_minor": 10000,
            "monthly_spend_minor": 10000,
            "per_action_limit_minor": 10000,
            "max_turns_per_day": 2,
            "max_tokens_per_turn": 100,
            "rate_limit_per_minute": 1,
        },
    )

    with pytest.raises(BusinessError) as exc:
        svc.record_usage(
            tenant.tenant_id,
            agent.agent_id,
            idempotency_key="tokens",
            tokens=101,
            kind="turn",
        )
    assert exc.value.code == "AGENT_TOKEN_LIMIT"

    svc.record_usage(
        tenant.tenant_id,
        agent.agent_id,
        idempotency_key="turn-1",
        tokens=10,
        kind="turn",
    )
    with pytest.raises(BusinessError) as exc:
        svc.record_usage(
            tenant.tenant_id,
            agent.agent_id,
            idempotency_key="turn-rate",
            tokens=10,
            kind="turn",
        )
    assert exc.value.code == "AGENT_RATE_LIMITED"

    # A different kind bypasses the turn-rate bucket but still counts spend.
    svc.record_usage(
        tenant.tenant_id,
        agent.agent_id,
        idempotency_key="tool-1",
        cost_minor=1,
        kind="tool",
    )

    # Turn daily limit: raise the rate gate out of the way by using a fresh
    # agent with a generous rate limit.
    agent2, _ = _create(
        svc,
        tenant,
        business,
        idempotency_key="create-2",
        safety={
            "daily_spend_minor": 10000,
            "monthly_spend_minor": 10000,
            "per_action_limit_minor": 10000,
            "max_turns_per_day": 1,
            "max_tokens_per_turn": 100,
            "rate_limit_per_minute": 10,
        },
    )
    svc.record_usage(
        tenant.tenant_id,
        agent2.agent_id,
        idempotency_key="a2-turn-1",
        tokens=10,
        kind="turn",
    )
    with pytest.raises(BusinessError) as exc:
        svc.record_usage(
            tenant.tenant_id,
            agent2.agent_id,
            idempotency_key="a2-turn-2",
            tokens=10,
            kind="turn",
        )
    assert exc.value.code == "AGENT_TURN_LIMIT"


def test_usage_idempotency_is_one_effect(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(svc, tenant, business)

    args = dict(
        tenant_id=tenant.tenant_id,
        agent_id=agent.agent_id,
        idempotency_key="usage-same",
        cost_minor=10,
        kind="tool",
    )
    first = svc.record_usage(**args)
    second = svc.record_usage(**args)
    assert second.usage_id == first.usage_id
    assert _scalar(store, "SELECT COUNT(*) FROM bos_agent_usage") == 1

    with pytest.raises(BusinessError) as exc:
        svc.record_usage(
            tenant.tenant_id,
            agent.agent_id,
            idempotency_key="usage-same",
            cost_minor=11,
            kind="tool",
        )
    assert exc.value.code == "BUSINESS_IDEMPOTENCY_CONFLICT"


def test_authorize_turn_requires_a_human_approval_for_flagged_actions(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(
        svc,
        tenant,
        business,
        safety={
            "autonomy": "L2",
            "daily_spend_minor": 1000,
            "monthly_spend_minor": 1000,
            "per_action_limit_minor": 500,
            "approval_required": ["publish", "spend"],
        },
    )

    allowed = svc.authorize_turn(
        tenant.tenant_id, agent.agent_id, cost_minor=10, tokens=20
    )
    assert allowed["allowed"] is True
    assert allowed["remaining"]["daily_spend_remaining_minor"] == 990

    with pytest.raises(BusinessError) as exc:
        svc.authorize_turn(
            tenant.tenant_id,
            agent.agent_id,
            action="publish",
        )
    assert exc.value.code == "AGENT_APPROVAL_REQUIRED"

    approved = svc.authorize_turn(
        tenant.tenant_id,
        agent.agent_id,
        action="publish",
        human_approved=True,
    )
    assert approved["approval_required"] is True

    with pytest.raises(BusinessError) as exc:
        svc.authorize_turn(
            tenant.tenant_id, agent.agent_id, cost_minor=501
        )
    assert exc.value.code == "AGENT_BUDGET_EXCEEDED"

    svc.pause(tenant.tenant_id, agent.agent_id, idempotency_key="pause-1")
    with pytest.raises(BusinessError) as exc:
        svc.authorize_turn(tenant.tenant_id, agent.agent_id)
    assert exc.value.code == "BUSINESS_BAD_STATE"


def test_autonomy_l4_always_needs_approval(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(
        svc,
        tenant,
        business,
        safety={
            "autonomy": "L4",
            "daily_spend_minor": 1000,
            "monthly_spend_minor": 1000,
            "per_action_limit_minor": 500,
            "approval_required": [],
        },
    )

    with pytest.raises(BusinessError) as exc:
        svc.authorize_turn(
            tenant.tenant_id, agent.agent_id, action="send"
        )
    assert exc.value.code == "AGENT_APPROVAL_REQUIRED"


# --- scope -------------------------------------------------------------
def test_tenant_isolation(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(svc, tenant, business)
    other = store.create_tenant("Other Ltd")

    with pytest.raises(BusinessError) as exc:
        svc.get(other.tenant_id, agent.agent_id)
    assert exc.value.code == "AGENT_NOT_FOUND"
    assert svc.list(other.tenant_id) == []
    with pytest.raises(BusinessError) as exc:
        svc.audit(other.tenant_id, agent.agent_id)
    assert exc.value.code == "AGENT_NOT_FOUND"


def test_permissions_and_safety_value_objects_normalise(tmp_path):
    permissions = Permissions.from_mapping(
        {"payments": ["refund", "read", "read"], "files": ["/tmp/a"]}
    )
    assert permissions.payments == ("read", "refund")
    assert permissions.to_dict()["browser"] == []

    safety = Safety.from_mapping({"autonomy": "l3", "approval_required": ["publish"]})
    assert safety.autonomy == "L3"
    assert safety.approval_required == ("publish",)


def test_v1_database_migrates_forward_to_the_agent_schema(tmp_path):
    from voicebridge.business.schema import MIGRATIONS, SCHEMA_VERSION

    path = tmp_path / "v1.db"
    conn = sqlite3.connect(path)
    try:
        for statement in MIGRATIONS[0][1]:
            conn.execute(statement)
        conn.execute("PRAGMA user_version=1")
        conn.commit()
    finally:
        conn.close()

    store = BusinessStore(path)
    tenant, business = _scope(store)
    agent, created = _create(_svc(store), tenant, business)

    assert created is True
    assert agent.status == "active"
    assert _scalar(store, "SELECT COUNT(*) FROM bos_agent") == 1
    assert _scalar(store, "PRAGMA user_version") == SCHEMA_VERSION


def test_usage_rows_are_append_only(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    svc = _svc(store)
    agent, _ = _create(svc, tenant, business)
    svc.record_usage(
        tenant.tenant_id,
        agent.agent_id,
        idempotency_key="usage-append",
        cost_minor=1,
        kind="tool",
    )

    conn = sqlite3.connect(store.path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE bos_agent_usage SET cost_minor=99")
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM bos_agent_usage")
    finally:
        conn.close()
