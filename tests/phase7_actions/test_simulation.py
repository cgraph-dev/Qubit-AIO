"""P7-01 — data-driven needs/traits/relationships/action catalog (hermetic).

These tests pin the contract in ``docs/QUBIT-OFFICE-COMPLETE-PLAN.md`` §11
(P7-01), ``docs/QUBIT-OFFICE-DEPARTMENTS-AND-INTERACTIONS.md`` §6/§7/§9/§10 and
``docs/QUBIT-OFFICE-DOMAIN-CONTRACT.md`` §2 (Action/FX semantics):

* the shipped catalog covers work, social, needs, conflict and career, and
  every romance/risky action is flagged ``mature_content``;
* selection and replay are deterministic (same state + context + catalog gives
  the same action, score and digest);
* preconditions, cooldowns, props, rooms, approval and consent are enforced
  with typed refusals and a refused action is never executed;
* effects change bounded declared simulation state and relationships;
* a result never exposes a business-truth key and only emits ``simulation.*``
  notes;
* mature content is off by default and only routed when the context opts in.
"""
from __future__ import annotations

import pytest

from voicebridge.business import simulation as sim
from voicebridge.business.errors import BusinessError

pytestmark = pytest.mark.unit


def _context(**overrides):
    values = {
        "room_type": "work",
        "props": frozenset(
            {"computer", "whiteboard", "phone", "water_cooler",
             "coffee_machine"}
        ),
        "privacy_class": "department",
        "time_slot": "morning",
        "participant_count": 2,
        "target_agent_id": "agent-b",
    }
    values.update(overrides)
    return sim.SimulationContext(**values)


def _state(**overrides):
    values = {
        "agent_id": "agent-a",
        "traits": sim.Traits.from_names(("calm", "ambitious")),
        "relationships": {
            "agent-b": sim.Relationship(trust=20.0, friendship=15.0),
        },
    }
    values.update(overrides)
    return sim.AgentSimState(**values)


def _romance_context(**overrides):
    values = {
        "privacy_class": "private",
        "allow_mature": True,
        "consent_granted": True,
        "approval_granted": True,
    }
    values.update(overrides)
    return _context(**values)


def _romance_state():
    return _state(
        relationships={
            "agent-b": sim.Relationship(
                trust=50.0, attraction=60.0, friendship=40.0
            ),
        }
    )


# ------------------------------------------------------------- catalog
def test_catalog_covers_every_required_category():
    catalog = sim.default_catalog()
    counts = catalog.category_counts()
    for category in sim.REQUIRED_CATEGORIES:
        assert counts[category] > 0, f"{category} has no actions"
    assert counts["romance"] > 0
    assert counts["risky"] > 0


def test_every_mature_category_action_is_flagged():
    catalog = sim.default_catalog()
    for action in catalog.actions:
        if action.category in sim.MATURE_CATEGORIES:
            assert action.mature_content is True
            assert action.note, "a mature action must state its nature"


def test_catalog_validate_refuses_a_category_gap():
    only_work = tuple(
        action for action in sim.default_actions()
        if action.category == "work"
    )
    catalog = sim.ActionCatalog(actions=only_work)
    with pytest.raises(sim.SimulationError) as exc:
        catalog.validate()
    assert exc.value.code == sim.SIMULATION_INVALID_CODE


def test_action_definition_refuses_unflagged_mature_and_bad_effects():
    with pytest.raises(sim.SimulationError):
        sim.ActionDefinition(
            action_id="romance-test",
            label="Test romance",
            category="romance",
            mature_content=False,
        )
    with pytest.raises(sim.SimulationError):
        sim.ActionDefinition(
            action_id="work-test",
            label="Test work",
            category="work",
            effects={"wealth": 10.0},
        )
    with pytest.raises(sim.SimulationError):
        sim.ActionDefinition(
            action_id="work-test",
            label="Test work",
            category="work",
            effects={"energy": 101.0},
        )
    with pytest.raises(sim.SimulationError):
        sim.ActionDefinition(
            action_id="work-test",
            label="Test work",
            category="work",
            memory_event="business.order.created",
        )


def test_catalog_and_state_round_trip():
    catalog = sim.default_catalog()
    restored = sim.ActionCatalog.from_dict(catalog.to_dict())
    assert restored.digest == catalog.digest
    assert restored.to_dict() == catalog.to_dict()

    state = _state()
    assert sim.AgentSimState.from_dict(state.to_dict()).digest == state.digest


# --------------------------------------------------------- determinism
def test_selection_is_deterministic_across_runs():
    catalog = sim.default_catalog()
    first = catalog.select(_state(), _context())
    second = catalog.select(_state(), _context())
    assert first.action_id is not None
    assert first.action_id == second.action_id
    assert first.score == second.score
    assert first.to_dict() == second.to_dict()


def test_replay_produces_identical_state_and_results():
    catalog = sim.default_catalog()
    contexts = (
        _context(),
        _context(
            room_type="break_room",
            props=frozenset(
                {"coffee_machine", "water_cooler", "snack_machine"}
            ),
        ),
        _context(
            room_type="work",
            props=frozenset({"computer", "whiteboard", "phone"}),
        ),
    )
    actions = ("social-coffee", "needs-water", "work-code-review")

    def _run():
        state = _state()
        digests = []
        for action_id, context in zip(actions, contexts):
            state, result = sim.simulate(
                state, catalog, context, action_id=action_id
            )
            assert result.executed
            digests.append(result.to_dict()["digest"])
        return state, digests

    state_a, digests_a = _run()
    state_b, digests_b = _run()
    assert state_a.digest == state_b.digest
    assert digests_a == digests_b


# ----------------------------------------------------------- rejections
def test_need_precondition_is_a_typed_refusal_and_not_executed():
    catalog = sim.default_catalog()
    tired = _state(needs=sim.Needs(energy=5.0))
    state, result = sim.simulate(
        tired, catalog, _context(), action_id="work-deadline-sprint"
    )
    assert result.accepted is False
    assert result.executed is False
    assert result.code == sim.SIMULATION_PRECONDITION_CODE
    assert state.digest == tired.digest
    assert result.needs_after == result.needs_before


def test_cooldown_blocks_an_immediate_repeat():
    catalog = sim.default_catalog()
    state, first = sim.simulate(
        _state(), catalog, _context(), action_id="social-coffee"
    )
    assert first.executed
    unchanged, second = sim.simulate(
        state, catalog, _context(), action_id="social-coffee"
    )
    assert second.accepted is False
    assert second.code == sim.SIMULATION_COOLDOWN_CODE
    assert second.executed is False
    assert unchanged.digest == state.digest


def test_missing_prop_is_refused():
    catalog = sim.default_catalog()
    state, result = sim.simulate(
        _state(),
        catalog,
        _context(props=frozenset()),
        action_id="social-coffee",
    )
    assert result.code == sim.SIMULATION_MISSING_PROP_CODE
    assert result.executed is False
    assert state.digest == _state().digest


def test_wrong_room_is_refused():
    catalog = sim.default_catalog()
    _, result = sim.simulate(
        _state(),
        catalog,
        _context(room_type="bathroom"),
        action_id="social-coffee",
    )
    assert result.code == sim.SIMULATION_WRONG_ROOM_CODE
    assert result.executed is False


def test_participant_privacy_and_time_are_enforced():
    catalog = sim.default_catalog()

    _, alone = sim.simulate(
        _state(), catalog, _context(participant_count=1),
        action_id="social-coffee",
    )
    assert alone.code == sim.SIMULATION_PARTICIPANTS_CODE

    _, public = sim.simulate(
        _state(),
        catalog,
        _context(
            room_type="bathroom",
            privacy_class="public",
            props=frozenset({"toilet"}),
        ),
        action_id="needs-bathroom",
    )
    assert public.code == sim.SIMULATION_PRIVACY_CODE

    _, day = sim.simulate(
        _state(),
        catalog,
        _context(
            room_type="break_room",
            props=frozenset({"tv"}),
            time_slot="morning",
        ),
        action_id="social-watch-tv",
    )
    assert day.code == sim.SIMULATION_PRECONDITION_CODE


def test_role_precondition_is_enforced():
    action = sim.ActionDefinition(
        action_id="work-manager-review",
        label="Manager review",
        category="work",
        preconditions=sim.Preconditions(roles=("manager",)),
    )
    catalog = sim.ActionCatalog(actions=(action,))
    evaluation = catalog.evaluate(
        _state(), "work-manager-review", _context(roles=frozenset({"ic"}))
    )
    assert evaluation.ok is False
    assert evaluation.code == sim.SIMULATION_PRECONDITION_CODE
    _, result = sim.simulate(
        _state(), catalog, _context(roles=frozenset({"ic"}))
    )
    assert result.code == sim.SIMULATION_NO_ACTION_CODE


def test_approval_and_consent_are_enforced():
    catalog = sim.default_catalog()
    _, approval = sim.simulate(
        _state(), catalog, _context(), action_id="career-promotion"
    )
    assert approval.code == sim.SIMULATION_APPROVAL_CODE
    assert approval.executed is False

    _, consent = sim.simulate(
        _romance_state(),
        catalog,
        _romance_context(consent_granted=False),
        action_id="romance-kiss",
    )
    assert consent.code == sim.SIMULATION_CONSENT_CODE
    assert consent.executed is False


# ------------------------------------------------------------- effects
def test_effects_and_relationships_change_declared_state():
    catalog = sim.default_catalog()
    before = _state()
    state, result = sim.simulate(
        before, catalog, _context(), action_id="social-coffee"
    )
    assert result.executed is True
    assert result.business_truth is False
    assert result.declared_only is True
    assert state.needs.social > before.needs.social
    assert state.needs.stress < before.needs.stress
    assert state.relationships["agent-b"].friendship > (
        before.relationships["agent-b"].friendship
    )
    assert result.relationship_after["friendship"] > (
        result.relationship_before["friendship"]
    )
    assert result.memory_event.startswith(sim.MEMORY_EVENT_PREFIX)
    assert state.notes[-1] == result.memory_event


def test_state_never_leaves_declared_bounds():
    catalog = sim.default_catalog()
    contexts = (
        _context(),
        _context(
            room_type="break_room",
            props=frozenset(
                {"coffee_machine", "water_cooler", "snack_machine"}
            ),
        ),
        _context(
            room_type="bathroom",
            privacy_class="private",
            props=frozenset({"sink", "toilet", "shower"}),
        ),
        _context(
            room_type="work",
            props=frozenset({"computer", "whiteboard", "phone"}),
        ),
    )
    state = _state()
    for step in range(200):
        state, _ = sim.simulate(
            state, catalog, contexts[step % len(contexts)]
        )
    assert all(
        0.0 <= value <= 100.0 for value in state.needs.as_dict().values()
    )
    assert sim.REPUTATION_MIN <= state.reputation <= sim.REPUTATION_MAX
    for relationship in state.relationships.values():
        for value in relationship.as_dict().values():
            assert sim.RELATIONSHIP_MIN <= value <= sim.RELATIONSHIP_MAX
    assert len(state.history) <= 32
    assert len(state.notes) <= 32
    assert state.revision == 200


# --------------------------------------------------------- no truth
def test_results_never_expose_business_truth():
    catalog = sim.default_catalog()
    _, result = sim.simulate(
        _state(), catalog, _context(), action_id="social-coffee"
    )
    sim.validate_result(result)
    keys = set()

    def _walk(value):
        if isinstance(value, dict):
            for key, inner in value.items():
                keys.add(str(key))
                _walk(inner)
        elif isinstance(value, (list, tuple)):
            for inner in value:
                _walk(inner)

    _walk(result.to_dict())
    assert not (keys & set(sim.FORBIDDEN_TRUTH_KEYS))
    assert result.business_truth is False


def test_a_business_memory_event_is_refused():
    catalog = sim.default_catalog()
    _, result = sim.simulate(
        _state(), catalog, _context(), action_id="social-coffee"
    )
    tampered = sim.ActionResult(
        action_id=result.action_id,
        agent_id=result.agent_id,
        category=result.category,
        accepted=True,
        executed=True,
        memory_event="business.order.created",
    )
    with pytest.raises(sim.SimulationError) as exc:
        sim.validate_result(tampered)
    assert exc.value.code == sim.SIMULATION_INVALID_CODE


# --------------------------------------------------------- mature
def test_mature_content_is_off_by_default():
    catalog = sim.default_catalog()
    selection = catalog.select(_state(), _context())
    assert selection.action is not None
    assert selection.action.mature_content is False
    rejected = {row["action_id"]: row["code"] for row in selection.rejected}
    assert rejected["romance-flirt"] == sim.SIMULATION_MATURE_CODE
    assert rejected["risky-gambling"] == sim.SIMULATION_MATURE_CODE

    _, explicit = sim.simulate(
        _romance_state(), catalog, _context(), action_id="romance-flirt"
    )
    assert explicit.code == sim.SIMULATION_MATURE_CODE
    assert explicit.executed is False


def test_mature_routing_requires_opt_in_consent_and_privacy():
    catalog = sim.default_catalog()
    selection = catalog.select(
        _romance_state(), _romance_context(), categories=("romance",)
    )
    assert selection.action is not None
    assert selection.action.mature_content is True
    assert selection.action.category == "romance"

    # Consent is a hard gate, not a hint.
    denied = catalog.select(
        _romance_state(),
        _romance_context(consent_granted=False),
        categories=("romance",),
    )
    assert denied.action is None
    assert denied.code == sim.SIMULATION_NO_ACTION_CODE


def test_mature_action_never_runs_without_the_flag():
    # Explicitly construct a romance action that forgot the flag: construction
    # refuses it before it could ever run.
    with pytest.raises(sim.SimulationError):
        sim.ActionDefinition(
            action_id="romance-unflagged",
            label="Unflagged romance",
            category="romance",
        )
    with pytest.raises(sim.SimulationError):
        sim.ActionDefinition(
            action_id="risky-unflagged",
            label="Unflagged risk",
            category="risky",
        )


def test_errors_are_business_errors():
    with pytest.raises(BusinessError) as exc:
        sim.default_catalog().action("does-not-exist")
    assert exc.value.code == sim.SIMULATION_UNKNOWN_ACTION_CODE
