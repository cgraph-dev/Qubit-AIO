"""P7-03 — mature-content policy contracts.

Pins the safe default (off), owner opt-in, department/agent scope, consent,
age, privacy and HR checks, non-graphic labels, the no-how-to rule and the
audit trail. Nothing here depicts content; only policy decisions are tested.
"""
from __future__ import annotations

import pytest

from voicebridge.business.errors import BusinessError
from voicebridge.business.mature_policy import (
    LABEL_CONFLICT,
    LABEL_FADE_TO_BLACK,
    LABEL_SUBSTANCE,
    MATURE_ACTION_UNKNOWN_CODE,
    MATURE_AGE_UNVERIFIED_CODE,
    MATURE_CATEGORY_DISABLED_CODE,
    MATURE_CONSENT_REQUIRED_CODE,
    MATURE_DEPARTMENT_DISABLED_CODE,
    MATURE_DISABLED_CODE,
    MATURE_GRAPHIC_REFUSED_CODE,
    MATURE_HOWTO_REFUSED_CODE,
    MATURE_HR_REQUIRED_CODE,
    MATURE_OWNER_REQUIRED_CODE,
    MATURE_PRIVACY_REQUIRED_CODE,
    MATURE_UNDERAGE_CODE,
    PRESENTATION_FADE,
    PRESENTATION_NON_GRAPHIC,
    PRESENTATION_SIMULATED,
    MatureContentPolicy,
    MatureRequest,
)


OWNER = "owner-1"


def _policy(**kwargs) -> MatureContentPolicy:
    return MatureContentPolicy(owner_id=OWNER, **kwargs)


def _enable(policy: MatureContentPolicy, department="Ops", categories=None):
    return policy.enable_department(
        actor=OWNER,
        department=department,
        categories=categories or ("romance", "substances", "fighting", "conflict"),
    )


def _romance_request(**overrides) -> MatureRequest:
    data = {
        "action": "kiss",
        "department": "Ops",
        "participants": ("a", "b"),
        "actor": "a",
        "ages": {"a": 30, "b": 29},
        "consents": frozenset({"a", "b"}),
        "hr_acknowledged": True,
    }
    data.update(overrides)
    return MatureRequest(**data)


def _code(call) -> str:
    with pytest.raises(BusinessError) as info:
        call()
    return info.value.code


def test_mature_content_is_off_by_default():
    policy = _policy()
    assert policy.enabled is False
    assert _code(lambda: policy.authorize(_romance_request())) == (
        MATURE_DISABLED_CODE
    )
    assert policy.audit_records()[-1]["decision"] == "refused"


def test_only_owner_can_enable_and_disable():
    policy = _policy()
    assert _code(
        lambda: policy.enable_department(actor="a", department="Ops")
    ) == MATURE_OWNER_REQUIRED_CODE
    policy.enable_globally(actor=OWNER)
    assert policy.enabled is True
    policy.disable_all(actor=OWNER)
    assert policy.enabled is False


def test_department_must_opt_in_and_category_must_be_enabled():
    policy = _policy()
    _enable(policy, "Ops", ("romance",))
    assert _code(
        lambda: policy.authorize(_romance_request(department="Finance"))
    ) == MATURE_DEPARTMENT_DISABLED_CODE
    assert _code(
        lambda: policy.authorize(_romance_request(action="alcohol"))
    ) == MATURE_CATEGORY_DISABLED_CODE


def test_non_mature_action_is_typed_refusal():
    policy = _policy()
    _enable(policy)
    request = _romance_request(action="write_report")
    assert _code(lambda: policy.authorize(request)) == (
        MATURE_ACTION_UNKNOWN_CODE
    )


def test_romance_requires_consent_age_and_hr():
    policy = _policy()
    _enable(policy)
    assert _code(
        lambda: policy.authorize(_romance_request(consents=frozenset({"a"})))
    ) == MATURE_CONSENT_REQUIRED_CODE
    assert _code(
        lambda: policy.authorize(_romance_request(ages={"a": 30}))
    ) == MATURE_AGE_UNVERIFIED_CODE
    assert _code(
        lambda: policy.authorize(
            _romance_request(ages={"a": 30, "b": 17})
        )
    ) == MATURE_UNDERAGE_CODE
    assert _code(
        lambda: policy.authorize(
            _romance_request(hr_acknowledged=False)
        )
    ) == MATURE_HR_REQUIRED_CODE


def test_private_encounter_is_fade_to_black_and_needs_privacy():
    policy = _policy()
    _enable(policy)
    public = _romance_request(action="private_encounter")
    assert _code(lambda: policy.authorize(public)) == (
        MATURE_PRIVACY_REQUIRED_CODE
    )
    auth = policy.authorize(
        _romance_request(action="private_encounter", privacy="private")
    )
    assert auth.allowed is True
    assert auth.label == LABEL_FADE_TO_BLACK
    assert auth.presentation == PRESENTATION_FADE


def test_romance_label_is_non_graphic_and_explicit_is_refused():
    policy = _policy()
    _enable(policy)
    auth = policy.authorize(_romance_request())
    assert auth.label == "non-graphic-romance"
    assert auth.presentation == PRESENTATION_NON_GRAPHIC
    assert _code(
        lambda: policy.authorize(
            _romance_request(detail={"explicit": True})
        )
    ) == MATURE_GRAPHIC_REFUSED_CODE


def test_substances_are_simulated_without_how_to():
    policy = _policy()
    _enable(policy)
    base = MatureRequest(
        action="alcohol",
        department="Ops",
        participants=("a",),
        actor="a",
        ages={"a": 30},
        consents=frozenset({"a"}),
    )
    auth = policy.authorize(base)
    assert auth.label == LABEL_SUBSTANCE
    assert auth.presentation == PRESENTATION_SIMULATED
    assert auth.support_available is True
    assert _code(
        lambda: policy.authorize(
            MatureRequest(
                action="alcohol",
                department="Ops",
                participants=("a",),
                actor="a",
                ages={"a": 30},
                consents=frozenset({"a"}),
                detail={"dose": "2 mg"},
            )
        )
    ) == MATURE_HOWTO_REFUSED_CODE
    assert _code(
        lambda: policy.authorize(
            MatureRequest(
                action="alcohol",
                department="Ops",
                participants=("a",),
                actor="a",
                ages={"a": 30},
                consents=frozenset({"a"}),
                detail={"notes": "how to make it at home"},
            )
        )
    ) == MATURE_HOWTO_REFUSED_CODE


def test_fighting_is_non_graphic_and_no_gore():
    policy = _policy()
    _enable(policy)
    base = MatureRequest(
        action="fight",
        department="Ops",
        participants=("a", "b"),
        actor="a",
        ages={"a": 30, "b": 31},
        consents=frozenset({"a", "b"}),
        hr_acknowledged=True,
    )
    auth = policy.authorize(base)
    assert auth.label == LABEL_CONFLICT
    assert auth.presentation == PRESENTATION_NON_GRAPHIC
    assert auth.support_available is True
    assert _code(
        lambda: policy.authorize(
            MatureRequest(
                action="fight",
                department="Ops",
                participants=("a", "b"),
                actor="a",
                ages={"a": 30, "b": 31},
                consents=frozenset({"a", "b"}),
                hr_acknowledged=True,
                detail={"gore": True},
            )
        )
    ) == MATURE_GRAPHIC_REFUSED_CODE


def test_conflict_is_allowed_with_hr_review_label():
    policy = _policy()
    _enable(policy)
    request = MatureRequest(
        action="argument",
        department="Ops",
        participants=("a", "b"),
        actor="a",
    )
    auth = policy.authorize(request)
    assert auth.allowed is True
    assert auth.label == LABEL_CONFLICT
    assert auth.hr_review_required is True
    assert auth.support_available is True


def test_agent_override_cannot_exceed_department_scope():
    policy = _policy()
    _enable(policy, "Ops", ("romance", "conflict"))
    assert _code(
        lambda: policy.set_agent_override(
            actor=OWNER,
            department="Ops",
            agent_id="agent-1",
            categories=("substances",),
        )
    ) == MATURE_CATEGORY_DISABLED_CODE
    policy.set_agent_override(
        actor=OWNER,
        department="Ops",
        agent_id="agent-1",
        categories=("conflict",),
    )
    assert _code(
        lambda: policy.authorize(
            _romance_request(agent_id="agent-1")
        )
    ) == MATURE_CATEGORY_DISABLED_CODE


def test_every_allowed_and_refused_event_is_audited():
    calls: list[dict] = []
    policy = _policy(audit_sink=lambda record: calls.append(dict(record)))
    _enable(policy)
    policy.authorize(_romance_request())
    _code(lambda: policy.authorize(_romance_request(consents=frozenset())))
    records = policy.audit_records()
    assert [record["decision"] for record in records] == [
        "allowed",
        "refused",
    ]
    assert len(calls) == 2
    assert "detail_keys" in records[0]
    assert records[0]["label"] == "non-graphic-romance"


def test_owner_can_disable_all_after_enabling():
    policy = _policy()
    _enable(policy)
    policy.disable_all(actor=OWNER)
    assert _code(lambda: policy.authorize(_romance_request())) == (
        MATURE_DISABLED_CODE
    )
