"""War Room / Idea Lab — the BOS-backed idea pipeline.

Hermetic: a throwaway SQLite file under ``tmp_path``. No network, no live
service, no paid art, no external publish/spend/outreach call.

These tests pin the master-plan contract (§28, §32–§36): intake is idempotent
and deduped, extraction is deterministic and persisted, a FACT must cite an
extracted source, only the owner decides, dissent is not averaged away, tasks
exist only after approval, a launch side effect is only ever a pending BOS
approval, and a restart loses nothing.
"""
from __future__ import annotations

import sqlite3

import pytest

from voicebridge.business import BusinessStore, warroom
from voicebridge.business.errors import BusinessError
from voicebridge.business.warroom_adapters import (
    Extraction,
    SourceAdapterError,
    StubAdapter,
)


def _store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


def _scope(store: BusinessStore, name: str = "Acme", slug: str = "acme"):
    tenant = store.create_tenant(f"{name} Holdings")
    business = store.create_business(tenant.tenant_id, name, slug=slug)
    return tenant, business


def _count(store: BusinessStore, table: str) -> int:
    conn = sqlite3.connect(store.path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def _brief(**overrides):
    brief = {
        "idea": "printable budget planner",
        "niche": "personal-finance printables",
        "icp": "budget-conscious Etsy shoppers",
        "offer": "downloadable PDF planner, $6",
        "channels": ["etsy", "pinterest"],
        "content_plan": ["Pinterest pins", "one blog post"],
        "lead_gen_plan": ["email capture on the landing page"],
        "website_app_scope": "static landing page, no app",
        "pricing": {"currency": "USD", "price_minor": 600},
        "budget": {"monthly_minor": 2000},
        "risks": ["IP/licence", "ad cost", "platform policy"],
        "plan_30_60_90": {"30": "ship 3 listings", "60": "first sale", "90": "review"},
        "tasks": [
            "Draft 3 planner layouts",
            {"title": "Write listing copy", "assigned_to": "agent:marketer"},
        ],
    }
    brief.update(overrides)
    return brief


def _score(**overrides):
    score = {"demand": 8, "effort": 4, "cost": 3, "risk": 3, "fit": 7}
    score.update(overrides)
    return score


def _intake(store, tenant, business, *, title="planner", key="wr-1"):
    return warroom.create_session(
        store,
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        title=title,
        idempotency_key=key,
    )


def _source(store, tenant, session_id, *, kind="url", locator="https://example.test/a"):
    return warroom.add_source(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session_id,
        kind=kind,
        locator=locator,
    )


def _extracted_source(store, tenant, session_id):
    source, _ = _source(store, tenant, session_id)
    return warroom.extract_source(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session_id,
        source_id=source.source_id,
    )


def _to_owner_decision(store, tenant, session_id):
    warroom.advance(
        store, tenant_id=tenant.tenant_id, session_id=session_id, target="research"
    )
    warroom.advance(
        store, tenant_id=tenant.tenant_id, session_id=session_id, target="debate"
    )
    warroom.synthesize(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session_id,
        brief=_brief(),
        score=_score(),
        experiments=[
            {"name": "Pinterest pin A/B", "success_metric": "saves"},
        ],
    )
    warroom.advance(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session_id,
        target="owner_decision",
    )


# --- intake: idempotency and source dedupe ------------------------------
def test_intake_is_idempotent_and_dedupes_sources(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    args = dict(
        tenant_id=tenant.tenant_id,
        business_id=business.business_id,
        title="planner idea",
        idempotency_key="intake-1",
    )
    first, created_first = warroom.create_session(store, **args)
    second, created_second = warroom.create_session(store, **args)
    assert created_first is True
    assert created_second is False
    assert second.session_id == first.session_id
    assert first.state == "intake"
    assert _count(store, "bos_warroom_session") == 1

    with pytest.raises(BusinessError) as exc:
        warroom.create_session(store, **{**args, "title": "a different idea"})
    assert exc.value.code == "BUSINESS_IDEMPOTENCY_CONFLICT"
    assert _count(store, "bos_warroom_session") == 1

    source_args = dict(
        tenant_id=tenant.tenant_id,
        session_id=first.session_id,
        kind="url",
        locator="https://example.test/idea",
    )
    one, created = warroom.add_source(store, **source_args)
    again, created_again = warroom.add_source(store, **source_args)
    assert created is True
    assert created_again is False
    assert again.source_id == one.source_id
    assert one.status == "pending"
    assert _count(store, "bos_warroom_source") == 1

    other, created_other = warroom.add_source(
        store,
        tenant_id=tenant.tenant_id,
        session_id=first.session_id,
        kind="note",
        locator="The owner's own words about why this caught the eye.",
    )
    assert created_other is True
    assert other.source_id != one.source_id


def test_tenant_scope_hides_sessions_and_sources(tmp_path):
    store = _store(tmp_path)
    t1, b1 = _scope(store, "Acme", "acme")
    t2, _b2 = _scope(store, "Beta", "beta")
    session, _ = _intake(store, t1, b1)
    _source(store, t1, session.session_id)

    assert warroom.list_sessions(store, t2.tenant_id) == []
    with pytest.raises(BusinessError) as exc:
        warroom.get_session(store, t2.tenant_id, session.session_id)
    assert exc.value.code == "BUSINESS_NOT_FOUND"
    with pytest.raises(BusinessError) as exc:
        warroom.add_source(
            store,
            tenant_id=t2.tenant_id,
            session_id=session.session_id,
            kind="url",
            locator="https://example.test/x",
        )
    assert exc.value.code == "BUSINESS_NOT_FOUND"


# --- extraction ----------------------------------------------------------
def test_source_extraction_is_deterministic_and_persists_artifacts(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    session, _ = _intake(store, tenant, business)

    kinds = {
        "youtube": "https://youtube.test/watch?v=abc",
        "url": "https://example.test/page",
        "post": "https://social.test/p/1",
        "note": "Owner note: people keep asking for a simple planner.",
        "image": "https://example.test/screenshot.png",
        "doc": "https://example.test/spec.pdf",
    }
    extracted = {}
    for kind, locator in kinds.items():
        source, _ = _source(store, tenant, session.session_id, kind=kind, locator=locator)
        assert source.status == "pending"
        done = warroom.extract_source(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            source_id=source.source_id,
        )
        assert done.status == "extracted"
        assert done.text and done.text.strip()
        assert done.artifact_id is not None
        assert done.artifact_id.startswith("art_")
        assert done.metadata["stub"] is True
        extracted[kind] = done

    # The note keeps the owner's own words rather than inventing them.
    assert extracted["note"].text == kinds["note"]
    assert _count(store, "bos_artifact") == len(kinds)

    # Re-extraction is idempotent: same text, no second artifact.
    again = warroom.extract_source(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        source_id=extracted["url"].source_id,
    )
    assert again.text == extracted["url"].text
    assert again.artifact_id == extracted["url"].artifact_id
    assert _count(store, "bos_artifact") == len(kinds)

    # A restart reads the same durable extraction.
    restarted = BusinessStore(store.path)
    persisted = warroom.get_source(
        restarted, tenant.tenant_id, extracted["youtube"].source_id
    )
    assert persisted.status == "extracted"
    assert persisted.text == extracted["youtube"].text


def test_a_session_without_a_business_still_persists_extracted_text(tmp_path):
    store = _store(tmp_path)
    tenant = store.create_tenant("Solo")
    session, _ = warroom.create_session(
        store,
        tenant_id=tenant.tenant_id,
        title="pre-business idea",
        idempotency_key="solo-1",
    )
    source, _ = _source(store, tenant, session.session_id, kind="note", locator="idea text")
    done = warroom.extract_source(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        source_id=source.source_id,
    )
    assert done.status == "extracted"
    assert done.text == "idea text"
    assert done.artifact_id is None
    assert _count(store, "bos_artifact") == 0


def test_adapter_failures_are_typed_and_mark_the_source_failed(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    session, _ = _intake(store, tenant, business)
    source, _ = _source(store, tenant, session.session_id, kind="url")

    class Broken:
        kind = "url"

        def extract(self, locator, *, metadata=None):
            raise SourceAdapterError("paywall or no captions")

    with pytest.raises(BusinessError) as exc:
        warroom.extract_source(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            source_id=source.source_id,
            registry={"url": Broken()},
        )
    assert exc.value.code == "BUSINESS_WARROOM_ADAPTER_FAILED"
    failed = warroom.get_source(store, tenant.tenant_id, source.source_id)
    assert failed.status == "failed"
    assert failed.metadata["error_code"] == "BUSINESS_WARROOM_ADAPTER_FAILED"

    other, _ = _source(store, tenant, session.session_id, kind="doc", locator="doc-2")
    with pytest.raises(BusinessError) as exc:
        warroom.extract_source(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            source_id=other.source_id,
            registry={},
        )
    assert exc.value.code == "BUSINESS_WARROOM_ADAPTER_UNAVAILABLE"
    assert warroom.get_source(
        store, tenant.tenant_id, other.source_id
    ).status == "failed"


def test_stub_adapter_never_touches_the_network(tmp_path):
    adapter = StubAdapter("url", "Web page text")
    first = adapter.extract("https://example.test/x")
    second = adapter.extract("https://example.test/x")
    assert isinstance(first, Extraction)
    assert first.text == second.text
    assert "no network" in first.text
    assert first.metadata["stub"] is True


# --- evidence labels -----------------------------------------------------
def test_evidence_labels_are_enforced_and_facts_need_an_extracted_source(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    session, _ = _intake(store, tenant, business)
    extracted = _extracted_source(store, tenant, session.session_id)
    pending, _ = _source(
        store, tenant, session.session_id, kind="post", locator="https://social.test/p/2"
    )

    fact, created = warroom.add_evidence(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        label="fact",
        claim="Shoppers search for printable planners",
        source_id=extracted.source_id,
        quote="...planner...",
    )
    assert created is True
    assert fact.label == "FACT"

    with pytest.raises(BusinessError) as exc:
        warroom.add_evidence(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            label="FACT",
            claim="An unsourced fact",
        )
    assert exc.value.code == "BUSINESS_WARROOM_UNVERIFIED_FACT"

    with pytest.raises(BusinessError) as exc:
        warroom.add_evidence(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            label="FACT",
            claim="A fact citing unread material",
            source_id=pending.source_id,
        )
    assert exc.value.code == "BUSINESS_WARROOM_UNVERIFIED_FACT"

    # Unsourced inference/assumption/unknown claims are allowed, but labelled.
    for label in ("INFERENCE", "ASSUMPTION", "UNKNOWN"):
        item, _ = warroom.add_evidence(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            label=label,
            claim=f"{label} claim about the market",
        )
        assert item.label == label
        assert item.source_id is None

    with pytest.raises(BusinessError) as exc:
        warroom.add_evidence(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            label="GUESS",
            claim="not a label",
        )
    assert exc.value.code == "BUSINESS_BAD_INPUT"

    # Replaying the same labelled claim is the same evidence row.
    replay, created_again = warroom.add_evidence(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        label="FACT",
        claim="Shoppers search for printable planners",
        source_id=extracted.source_id,
    )
    assert created_again is False
    assert replay.evidence_id == fact.evidence_id


def test_a_fact_cannot_cite_another_sessions_source(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    one, _ = _intake(store, tenant, business, title="one", key="one")
    two, _ = _intake(store, tenant, business, title="two", key="two")
    foreign = _extracted_source(store, tenant, one.session_id)

    with pytest.raises(BusinessError) as exc:
        warroom.add_evidence(
            store,
            tenant_id=tenant.tenant_id,
            session_id=two.session_id,
            label="FACT",
            claim="borrowed fact",
            source_id=foreign.source_id,
        )
    assert exc.value.code == "BUSINESS_WARROOM_UNVERIFIED_FACT"


# --- state machine and decision -----------------------------------------
def test_state_machine_refuses_out_of_order_and_owner_edges(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    session, _ = _intake(store, tenant, business)

    with pytest.raises(BusinessError) as exc:
        warroom.synthesize(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            brief=_brief(),
            score=_score(),
        )
    assert exc.value.code == "BUSINESS_BAD_STATE"

    with pytest.raises(BusinessError) as exc:
        warroom.advance(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            target="approved",
        )
    assert exc.value.code == "BUSINESS_BAD_STATE"

    warroom.advance(
        store, tenant_id=tenant.tenant_id, session_id=session.session_id, target="research"
    )
    with pytest.raises(BusinessError) as exc:
        warroom.advance(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            target="synthesis",
        )
    assert exc.value.code == "BUSINESS_BAD_STATE"

    # No shortcut into the decision.
    with pytest.raises(BusinessError) as exc:
        warroom.record_decision(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            decision="approved",
            decided_by="owner",
        )
    assert exc.value.code == "BUSINESS_BAD_STATE"


def test_only_the_owner_can_decide(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    session, _ = _intake(store, tenant, business)
    _to_owner_decision(store, tenant, session.session_id)

    with pytest.raises(BusinessError) as exc:
        warroom.record_decision(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            decision="approved",
            decided_by="agent:ceo",
            idempotency_key="dec-1",
        )
    assert exc.value.code == "BUSINESS_WARROOM_OWNER_ONLY"
    assert warroom.get_session(
        store, tenant.tenant_id, session.session_id
    ).state == "owner_decision"
    assert _count(store, "bos_warroom_decision") == 0

    decision, created = warroom.record_decision(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        decision="approved",
        decided_by="owner",
        note="ship it under the Etsy pilot",
        idempotency_key="dec-1",
    )
    assert created is True
    assert decision.status == "approved"
    assert decision.decided_by == "owner"
    decided = warroom.get_session(store, tenant.tenant_id, session.session_id)
    assert decided.state == "approved"
    assert decided.owner_note == "ship it under the Etsy pilot"

    # Replay is the same decision, not a second one.
    replay, created_again = warroom.record_decision(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        decision="approved",
        decided_by="owner",
        note="ship it under the Etsy pilot",
        idempotency_key="dec-1",
    )
    assert created_again is False
    assert replay.decision_id == decision.decision_id
    assert _count(store, "bos_warroom_decision") == 1

    # A different decision after approval is a state refusal, not a rewrite.
    with pytest.raises(BusinessError) as exc:
        warroom.record_decision(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            decision="rejected",
            decided_by="owner",
            idempotency_key="dec-2",
        )
    assert exc.value.code == "BUSINESS_BAD_STATE"


def test_rejection_is_terminal_and_creates_no_tasks(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    session, _ = _intake(store, tenant, business)
    _to_owner_decision(store, tenant, session.session_id)
    decision, created = warroom.record_decision(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        decision="rejected",
        decided_by="owner",
        note="too generic",
    )
    assert created is True
    assert decision.status == "rejected"
    assert warroom.get_session(
        store, tenant.tenant_id, session.session_id
    ).state == "rejected"
    assert _count(store, "bos_task") == 0

    with pytest.raises(BusinessError) as exc:
        warroom.create_tasks(
            store, tenant_id=tenant.tenant_id, session_id=session.session_id
        )
    assert exc.value.code == "BUSINESS_BAD_STATE"
    with pytest.raises(BusinessError):
        warroom.advance(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            target="tasks_created",
        )


# --- tasks only after approval ------------------------------------------
def test_tasks_exist_only_after_the_owner_approves(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    session, _ = _intake(store, tenant, business)
    _to_owner_decision(store, tenant, session.session_id)

    with pytest.raises(BusinessError) as exc:
        warroom.create_tasks(
            store, tenant_id=tenant.tenant_id, session_id=session.session_id
        )
    assert exc.value.code == "BUSINESS_BAD_STATE"
    assert _count(store, "bos_task") == 0

    warroom.record_decision(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        decision="approved",
        decided_by="owner",
    )
    tasks = warroom.create_tasks(
        store, tenant_id=tenant.tenant_id, session_id=session.session_id
    )
    assert [task.title for task in tasks] == [
        "Draft 3 planner layouts",
        "Write listing copy",
    ]
    assert tasks[1].assigned_to == "agent:marketer"
    assert all(
        task.metadata.get("warroom_session_id") == session.session_id
        for task in tasks
    )
    assert warroom.get_session(
        store, tenant.tenant_id, session.session_id
    ).state == "tasks_created"

    # Replay does not duplicate work.
    again = warroom.create_tasks(
        store, tenant_id=tenant.tenant_id, session_id=session.session_id
    )
    assert [task.task_id for task in again] == [task.task_id for task in tasks]
    assert _count(store, "bos_task") == 2


def test_tasks_need_a_business_on_the_session(tmp_path):
    store = _store(tmp_path)
    tenant = store.create_tenant("Solo")
    session, _ = warroom.create_session(
        store,
        tenant_id=tenant.tenant_id,
        title="no business yet",
        idempotency_key="solo-2",
    )
    _to_owner_decision(store, tenant, session.session_id)
    warroom.record_decision(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        decision="approved",
        decided_by="owner",
    )
    with pytest.raises(BusinessError) as exc:
        warroom.create_tasks(
            store, tenant_id=tenant.tenant_id, session_id=session.session_id
        )
    assert exc.value.code == "BUSINESS_BAD_INPUT"


# --- no side effects -----------------------------------------------------
def test_no_side_effects_before_approval_and_launch_is_only_an_approval(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    session, _ = _intake(store, tenant, business)
    _extracted_source(store, tenant, session.session_id)
    _to_owner_decision(store, tenant, session.session_id)

    # Intake, research, debate and synthesis have produced no work, money,
    # external call or approval.
    for table in (
        "bos_task",
        "bos_job",
        "bos_tool_invocation",
        "bos_approval",
        "bos_ledger_entry",
    ):
        assert _count(store, table) == 0

    with pytest.raises(BusinessError) as exc:
        warroom.request_action(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            action="tool:social.publish",
            requested_by="agent:social",
            idempotency_key="publish-1",
            payload={"channel": "pinterest"},
        )
    assert exc.value.code == "BUSINESS_BAD_STATE"
    assert _count(store, "bos_approval") == 0

    warroom.record_decision(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        decision="approved",
        decided_by="owner",
    )
    approval, created = warroom.request_action(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        action="tool:social.publish",
        requested_by="agent:social",
        idempotency_key="publish-1",
        payload={"channel": "pinterest"},
    )
    assert created is True
    assert approval.status == "pending"
    assert approval.action == "tool:social.publish"
    assert approval.payload["warroom_session_id"] == session.session_id
    # Requesting a gate still executes nothing.
    assert _count(store, "bos_tool_invocation") == 0
    assert _count(store, "bos_job") == 0
    assert _count(store, "bos_ledger_entry") == 0


# --- dissent and synthesis ----------------------------------------------
def test_dissent_is_preserved_through_synthesis(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    session, _ = _intake(store, tenant, business)
    source = _extracted_source(store, tenant, session.session_id)
    fact, _ = warroom.add_evidence(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        label="FACT",
        claim="Planner searches are seasonal",
        source_id=source.source_id,
    )
    warroom.advance(
        store, tenant_id=tenant.tenant_id, session_id=session.session_id, target="research"
    )
    warroom.advance(
        store, tenant_id=tenant.tenant_id, session_id=session.session_id, target="debate"
    )

    turns = [
        ("moderator", "neutral", "Frame the decision.", []),
        ("researcher", "support", "Demand exists and is seasonal.", [fact.evidence_id]),
        ("skeptic", "oppose", "The market is crowded and IP risk is real.", []),
        ("marketer", "question", "Which channel actually converts?", []),
        ("finance", "oppose", "Fees may erase the margin at $6.", []),
    ]
    for round_no, (role, stance, text, refs) in enumerate(turns, start=1):
        warroom.add_argument(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            round_no=round_no,
            role=role,
            stance=stance,
            text=text,
            evidence_ids=refs,
        )

    session_done = warroom.synthesize(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        brief=_brief(),
        score=_score(),
        experiments=[{"name": "Pinterest pin A/B", "success_metric": "saves"}],
    )
    assert session_done.state == "synthesis"
    dissent_texts = {item["text"] for item in session_done.dissent}
    assert dissent_texts == {
        "The market is crowded and IP risk is real.",
        "Which channel actually converts?",
        "Fees may erase the margin at $6.",
    }
    assert all(item["stance"] in ("oppose", "question") for item in session_done.dissent)
    assert "Demand exists and is seasonal." not in dissent_texts

    # Arguments are append-only and cannot be added after synthesis.
    with pytest.raises(BusinessError) as exc:
        warroom.add_argument(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            round_no=6,
            role="builder",
            stance="support",
            text="late support",
        )
    assert exc.value.code == "BUSINESS_BAD_STATE"

    experiments = warroom.list_experiments(
        store, tenant.tenant_id, session.session_id
    )
    assert [item.name for item in experiments] == ["Pinterest pin A/B"]
    bundle = warroom.get_bundle(store, tenant.tenant_id, session.session_id)
    assert len(bundle["arguments"]) == 5
    assert bundle["session"].dissent == session_done.dissent


def test_brief_and_score_validation(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    session, _ = _intake(store, tenant, business)
    warroom.advance(
        store, tenant_id=tenant.tenant_id, session_id=session.session_id, target="research"
    )
    warroom.advance(
        store, tenant_id=tenant.tenant_id, session_id=session.session_id, target="debate"
    )

    incomplete = _brief()
    incomplete.pop("pricing")
    with pytest.raises(BusinessError) as exc:
        warroom.synthesize(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            brief=incomplete,
            score=_score(),
        )
    assert exc.value.code == "BUSINESS_BAD_INPUT"

    with pytest.raises(BusinessError) as exc:
        warroom.synthesize(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            brief=_brief(tasks=[]),
            score=_score(),
        )
    assert exc.value.code == "BUSINESS_BAD_INPUT"

    with pytest.raises(BusinessError) as exc:
        warroom.synthesize(
            store,
            tenant_id=tenant.tenant_id,
            session_id=session.session_id,
            brief=_brief(),
            score=_score(demand=11),
        )
    assert exc.value.code == "BUSINESS_BAD_INPUT"

    # Nothing was written by the refusals.
    assert warroom.get_session(
        store, tenant.tenant_id, session.session_id
    ).state == "debate"


# --- append-only record of what was said and decided ---------------------
def test_evidence_arguments_and_decision_are_append_only(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    session, _ = _intake(store, tenant, business)
    source = _extracted_source(store, tenant, session.session_id)
    warroom.add_evidence(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        label="FACT",
        claim="Listings can be digital downloads",
        source_id=source.source_id,
    )
    warroom.advance(
        store, tenant_id=tenant.tenant_id, session_id=session.session_id, target="research"
    )
    warroom.advance(
        store, tenant_id=tenant.tenant_id, session_id=session.session_id, target="debate"
    )
    warroom.add_argument(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        round_no=1,
        role="skeptic",
        stance="oppose",
        text="Too crowded.",
    )
    warroom.synthesize(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        brief=_brief(),
        score=_score(),
    )
    warroom.advance(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        target="owner_decision",
    )
    warroom.record_decision(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        decision="approved",
        decided_by="owner",
    )

    conn = sqlite3.connect(store.path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE bos_warroom_evidence SET claim='rewritten'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM bos_warroom_evidence")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE bos_warroom_argument SET text='rewritten'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM bos_warroom_argument")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM bos_warroom_decision")
    finally:
        conn.close()


# --- persistence ---------------------------------------------------------
def test_restart_preserves_the_whole_session_and_decision_replay(tmp_path):
    store = _store(tmp_path)
    tenant, business = _scope(store)
    session, _ = _intake(store, tenant, business)
    source = _extracted_source(store, tenant, session.session_id)
    warroom.add_evidence(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        label="FACT",
        claim="Listings can be published as digital downloads",
        source_id=source.source_id,
    )
    _to_owner_decision(store, tenant, session.session_id)
    decision, _ = warroom.record_decision(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        decision="approved",
        decided_by="owner",
        note="pilot it",
        idempotency_key="restart-decision",
    )
    tasks = warroom.create_tasks(
        store, tenant_id=tenant.tenant_id, session_id=session.session_id
    )
    artifacts_before = _count(store, "bos_artifact")

    restarted = BusinessStore(store.path)
    bundle = warroom.get_bundle(restarted, tenant.tenant_id, session.session_id)
    assert bundle["session"].state == "tasks_created"
    assert bundle["session"].brief["idea"] == "printable budget planner"
    assert bundle["session"].score["total"] == pytest.approx(5.0)
    assert len(bundle["sources"]) == 1
    assert len(bundle["evidence"]) == 1
    assert [item.name for item in bundle["experiments"]] == ["Pinterest pin A/B"]
    assert [item.status for item in bundle["decisions"]] == ["approved"]
    assert [item.task_id for item in bundle["tasks"]] == [
        item.task_id for item in tasks
    ]

    replay, created = warroom.record_decision(
        restarted,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        decision="approved",
        decided_by="owner",
        note="pilot it",
        idempotency_key="restart-decision",
    )
    assert created is False
    assert replay.decision_id == decision.decision_id
    # Re-reading extraction never adds an artifact or an external effect.
    warroom.extract_source(
        restarted,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        source_id=source.source_id,
    )
    assert _count(restarted, "bos_artifact") == artifacts_before
