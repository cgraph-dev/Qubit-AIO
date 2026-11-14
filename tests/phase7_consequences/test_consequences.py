"""P7-02 — consequence engine contracts.

Hermetic and in-memory: no store, no network, no live service. The tests pin
the separation between simulation effects and authoritative events, the
approval requirement for sensitive consequences, bounded effects, recovery
paths and the audit/dead-letter trail.
"""
from __future__ import annotations

import pytest

from voicebridge.business.consequences import (
    CONSEQUENCE_ACTION_UNKNOWN_CODE,
    CONSEQUENCE_APPROVAL_INVALID_CODE,
    CONSEQUENCE_APPROVAL_REQUIRED_CODE,
    CONSEQUENCE_AUTHORITY_FAILED_CODE,
    CONSEQUENCE_AUTHORITY_UNAVAILABLE_CODE,
    CONSEQUENCE_CONSENT_REQUIRED_CODE,
    AuthorityPort,
    ApprovalGrant,
    ConsequenceAction,
    ConsequenceEngine,
    ConsentGrant,
    SimulationState,
)
from voicebridge.business.errors import BusinessError


def _errors(call):
    with pytest.raises(BusinessError) as info:
        call()
    return info.value


def _spy_sink():
    calls: list[dict] = []

    def sink(payload):
        calls.append(dict(payload))
        return f"evt_{len(calls)}"

    return calls, sink


def test_effects_apply_and_are_bounded():
    engine = ConsequenceEngine()
    result = engine.apply(
        "coffee_break", actor="a", subject="a", correlation_id="cor-1"
    )
    assert result.before["stress"] == 50.0
    assert result.after["stress"] == 42.0
    assert result.effects_applied["stress"] == -8.0

    engine.register(
        ConsequenceAction(
            "huge",
            effects={"stress": 1000, "health": -1000, "morale": 1000},
        )
    )
    huge = engine.apply("huge", actor="a", subject="a")
    assert huge.after["stress"] == 100.0
    assert huge.after["health"] == 0.0
    assert huge.after["morale"] == 100.0
    for value in huge.after.values():
        assert 0.0 <= value <= 100.0


def test_unknown_action_is_typed_and_dead_lettered():
    engine = ConsequenceEngine(dead_letter_limit=2)
    exc = _errors(lambda: engine.apply("nope", actor="a", subject="a"))
    assert exc.code == CONSEQUENCE_ACTION_UNKNOWN_CODE
    assert len(engine.dead_letters()) == 1
    assert engine.dead_letters()[0]["reason"] == "unknown_action"


def test_dead_letter_queue_is_bounded():
    engine = ConsequenceEngine(dead_letter_limit=2)
    for index in range(5):
        _errors(lambda i=index: engine.apply(f"nope-{i}", actor="a", subject="a"))
    assert len(engine.dead_letters()) == 2


def test_simulation_only_never_calls_authority():
    calls, sink = _spy_sink()
    authority = AuthorityPort(
        emit_hr=sink, emit_legal=sink, emit_business=sink
    )
    engine = ConsequenceEngine(authority=authority)
    result = engine.apply(
        "conflict_argument",
        actor="a",
        subject="a",
        target="b",
        correlation_id="cor-2",
    )
    assert calls == []
    assert result.authoritative_events == ()
    assert result.simulation_events
    assert all(
        event["authoritative"] is False for event in result.simulation_events
    )
    assert result.relationship_effects_applied["trust"] == -10.0
    assert engine.state.relationship("b", "a")["trust"] == 40.0


def test_sensitive_requires_approval_before_any_change():
    calls, sink = _spy_sink()
    engine = ConsequenceEngine(
        authority=AuthorityPort(emit_hr=sink)
    )
    before = engine.state.metrics("a")
    exc = _errors(
        lambda: engine.apply("termination", actor="hr", subject="a")
    )
    assert exc.code == CONSEQUENCE_APPROVAL_REQUIRED_CODE
    assert calls == []
    assert engine.state.metrics("a") == before
    assert engine.audit_records()[-1]["decision"] == "refused"
    assert engine.audit_records()[-1]["reason_code"] == (
        CONSEQUENCE_APPROVAL_REQUIRED_CODE
    )


def test_approval_must_match_action_subject_and_scope():
    calls, sink = _spy_sink()
    engine = ConsequenceEngine(authority=AuthorityPort(emit_hr=sink))
    wrong = ApprovalGrant("apr-1", "promotion", "a", "career")
    exc = _errors(
        lambda: engine.apply(
            "termination", actor="hr", subject="a", approved=wrong
        )
    )
    assert exc.code == CONSEQUENCE_APPROVAL_INVALID_CODE
    expired = ApprovalGrant("apr-2", "termination", "a", "termination", 10.0)
    engine = ConsequenceEngine(
        authority=AuthorityPort(emit_hr=sink), clock=lambda: 100.0
    )
    exc = _errors(
        lambda: engine.apply(
            "termination", actor="hr", subject="a", approved=expired
        )
    )
    assert exc.code == CONSEQUENCE_APPROVAL_INVALID_CODE
    assert calls == []


def test_sensitive_event_is_only_the_authority_receipt():
    calls, sink = _spy_sink()
    engine = ConsequenceEngine(authority=AuthorityPort(emit_hr=sink))
    approval = ApprovalGrant("apr-3", "termination", "a", "termination")
    result = engine.apply(
        "termination",
        actor="hr",
        subject="a",
        target="b",
        approved=approval,
        correlation_id="cor-3",
    )
    assert len(calls) == 1
    assert calls[0]["action_id"] == "termination"
    assert calls[0]["approval_id"] == "apr-3"
    assert result.authoritative_events == (
        {
            "kind": "hr",
            "category": "termination",
            "event_id": "evt_1",
        },
    )
    assert all(
        event["type"].startswith("simulation.")
        for event in result.simulation_events
    )
    assert engine.incidents()[-1]["action_id"] == "termination"
    assert engine.audit_records()[-1]["decision"] == "applied"


def test_sensitive_refuses_without_authority_channel():
    engine = ConsequenceEngine()
    approval = ApprovalGrant("apr-4", "termination", "a", "termination")
    before = engine.state.metrics("a")
    exc = _errors(
        lambda: engine.apply(
            "termination", actor="hr", subject="a", approved=approval
        )
    )
    assert exc.code == CONSEQUENCE_AUTHORITY_UNAVAILABLE_CODE
    assert engine.state.metrics("a") == before
    assert engine.dead_letters()[-1]["reason"] == "authority_unavailable"


def test_authority_failure_leaves_state_untouched():
    def boom(_payload):
        raise RuntimeError("hr down")

    engine = ConsequenceEngine(authority=AuthorityPort(emit_hr=boom))
    approval = ApprovalGrant("apr-5", "termination", "a", "termination")
    before = engine.state.metrics("a")
    exc = _errors(
        lambda: engine.apply(
            "termination", actor="hr", subject="a", approved=approval
        )
    )
    assert exc.code == CONSEQUENCE_AUTHORITY_FAILED_CODE
    assert engine.state.metrics("a") == before
    assert engine.dead_letters()[-1]["reason"] == "authority_failed"
    assert engine.audit_records()[-1]["decision"] == "refused"


def test_recovery_path_is_available_after_punishment():
    _calls, sink = _spy_sink()
    engine = ConsequenceEngine(authority=AuthorityPort(emit_hr=sink))
    approval = ApprovalGrant("apr-6", "termination", "a", "termination")
    engine.apply("termination", actor="hr", subject="a", approved=approval)
    stressed = engine.state.metrics("a")
    support = engine.apply("counseling_support", actor="hr", subject="a")
    assert support.recovery is True
    assert support.after["stress"] < stressed["stress"]
    assert support.after["health"] > stressed["health"]
    assert support.after["morale"] > stressed["morale"]


def test_consent_grant_is_required_for_declared_actions():
    _calls, sink = _spy_sink()
    engine = ConsequenceEngine(authority=AuthorityPort(emit_hr=sink))
    engine.register(
        ConsequenceAction(
            "mediated_hr_resolution",
            effects={"stress": -5},
            sensitive="hr_case",
            requires_consent=True,
        )
    )
    approval = ApprovalGrant(
        "apr-7", "mediated_hr_resolution", "a", "hr_case"
    )
    exc = _errors(
        lambda: engine.apply(
            "mediated_hr_resolution",
            actor="a",
            subject="a",
            target="b",
            approved=approval,
        )
    )
    assert exc.code == CONSEQUENCE_CONSENT_REQUIRED_CODE
    consent = ConsentGrant(
        "con-1", "mediated_hr_resolution", frozenset({"a", "b"})
    )
    result = engine.apply(
        "mediated_hr_resolution",
        actor="a",
        subject="a",
        target="b",
        approved=approval,
        consent=consent,
    )
    assert result.authoritative_events[0]["kind"] == "hr"


def test_custom_state_can_be_shared_and_is_not_business_truth():
    state = SimulationState(initial=40.0)
    engine = ConsequenceEngine(state=state)
    engine.apply("overtime", actor="a", subject="a")
    assert state.metrics("a")["stress"] == 52.0
    assert engine.apply("coffee_break", actor="a", subject="a").after[
        "stress"
    ] == 44.0
    assert not hasattr(engine, "store")


def test_consent_is_bound_to_action_and_expiry():
    _calls, sink = _spy_sink()
    engine = ConsequenceEngine(
        authority=AuthorityPort(emit_hr=sink), clock=lambda: 200.0
    )
    engine.register(
        ConsequenceAction(
            "mediated_hr_resolution",
            effects={"stress": -5},
            sensitive="hr_case",
            requires_consent=True,
        )
    )
    approval = ApprovalGrant(
        "apr-8", "mediated_hr_resolution", "a", "hr_case"
    )
    wrong = ConsentGrant(
        "con-wrong", "termination", frozenset({"a", "b"})
    )
    exc = _errors(
        lambda: engine.apply(
            "mediated_hr_resolution",
            actor="a",
            subject="a",
            target="b",
            approved=approval,
            consent=wrong,
        )
    )
    assert exc.code == CONSEQUENCE_CONSENT_REQUIRED_CODE
    expired = ConsentGrant(
        "con-expired",
        "mediated_hr_resolution",
        frozenset({"a", "b"}),
        10.0,
    )
    exc = _errors(
        lambda: engine.apply(
            "mediated_hr_resolution",
            actor="a",
            subject="a",
            target="b",
            approved=approval,
            consent=expired,
        )
    )
    assert exc.code == CONSEQUENCE_CONSENT_REQUIRED_CODE
