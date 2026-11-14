"""P8-01 — the deterministic 100-floor / 1,000-agent scale fixture."""
from __future__ import annotations

import pytest

from voicebridge.business import scale_fixture as sf
from voicebridge.office.world import model as world_model

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def fixture():
    return sf.build_scale_fixture()


def test_counts_match_the_shipped_tower_contract(fixture):
    counts = fixture.counts()
    assert counts["floors"] == 100
    assert counts["wings"] == 5
    assert counts["rooms"] == 300
    assert counts["cells"] == 575
    assert counts["desks"] == 3075
    assert counts["agents"] == 1000
    # Capacity is not a roster: the projection never materialises an agent.
    assert counts["businesses"] >= 1
    assert counts["desks"] == len(fixture.seats)


def test_digest_is_deterministic_for_the_same_seed():
    first = sf.build_scale_fixture()
    second = sf.build_scale_fixture()
    assert first.digest == second.digest
    assert first.counts() == second.counts()


def test_a_new_seed_changes_the_agent_records_not_the_geometry():
    base = sf.build_scale_fixture(seed=1)
    other = sf.build_scale_fixture(seed=2)
    assert base.digest != other.digest
    assert base.counts() == other.counts()
    assert [a.seat_id for a in base.agents] == [a.seat_id for a in other.agents]
    assert [a.agent_id for a in base.agents] == [a.agent_id for a in other.agents]
    assert [a.priority for a in base.agents] != [a.priority for a in other.agents]


def test_every_agent_is_a_tenant_scoped_projection_record(fixture):
    assert fixture.tenant_id == sf.SYNTHETIC_TENANT
    business_ids = {b.business_id for b in fixture.businesses}
    room_ids = {r.room_id for r in fixture.rooms}
    floor_by_room = {r.room_id: r.floor_id for r in fixture.rooms}
    seat_ids = {s.seat_id for s in fixture.seats}
    agent_ids = [a.agent_id for a in fixture.agents]
    assert len(set(agent_ids)) == len(agent_ids) == 1000
    for agent in fixture.agents:
        assert agent.tenant_id == sf.SYNTHETIC_TENANT
        assert agent.business_id in business_ids
        assert agent.room_id in room_ids
        assert agent.floor_id == floor_by_room[agent.room_id]
        assert agent.seat_id in seat_ids
        assert agent.status == "projection"
        assert agent.source in sf.PROJECTION_SOURCES
        assert agent.operational is False
        assert agent.is_projection is True
    assert len({a.seat_id for a in fixture.agents}) == len(fixture.agents)


def test_foreign_tenants_are_isolated(fixture):
    assert len(fixture.agents_for(fixture.tenant_id)) == 1000
    assert fixture.agents_for("tenant:production") == ()
    business = fixture.businesses[0]
    scoped = fixture.agents_for(fixture.tenant_id, business_id=business.business_id)
    assert scoped
    assert all(a.business_id == business.business_id for a in scoped)
    fixture.require_tenant(fixture.tenant_id)
    with pytest.raises(sf.ScaleFixtureError) as exc:
        fixture.require_tenant("tenant:production")
    assert exc.value.code == "SCALE_FIXTURE_FOREIGN_TENANT"


def test_active_and_visible_tiers_are_bounded_and_dormant_is_populated(fixture):
    view = sf.select_scale_view(fixture)
    assert view.active_floor_id == fixture.default_active_floor().floor_id
    assert len(view.simulated_agents) <= sf.DEFAULT_ACTIVE_AGENT_LIMIT
    assert len(view.simulated_agents) == min(
        sf.DEFAULT_ACTIVE_AGENT_LIMIT, view.simulated_candidate_count
    )
    assert len(view.visible_avatars) <= sf.DEFAULT_VISIBLE_AVATAR_LIMIT
    assert len(view.visible_avatars) == min(
        sf.DEFAULT_VISIBLE_AVATAR_LIMIT, view.visible_candidate_count
    )
    assert len(view.active_room_ids) <= sf.DEFAULT_ACTIVE_AGENT_LIMIT
    assert view.dormant_room_ids
    assert view.dormant_agents
    # Simulated agents are drawn, and visible avatars include them.
    simulated_ids = {a.agent_id for a in view.simulated_agents}
    visible_ids = {a.agent_id for a in view.visible_avatars}
    assert simulated_ids <= visible_ids
    assert view.throttled_agent_count >= 0
    # The caps are real: lowering them lowers the drawn set.
    tight = sf.select_scale_view(
        fixture, active_agent_limit=5, visible_avatar_limit=10
    )
    assert len(tight.simulated_agents) == 5
    assert len(tight.visible_avatars) == 10


def test_active_set_uses_the_canonical_policy(fixture):
    view = sf.select_scale_view(fixture)
    decision = {d.room_id: d for d in view.decisions}
    simulated = (
        {view.interaction_room_id}
        | set(view.urgent_room_ids)
        | set(view.deadline_room_ids)
        | set(view.approval_room_ids)
        | set(view.notification_room_ids)
    )
    for room_id in simulated:
        assert decision[room_id].state == world_model.RoomState.SIMULATION_ACTIVE
    for room_id in view.camera_room_ids:
        if room_id not in simulated:
            assert decision[room_id].tier == world_model.Tier.PROXY
            assert decision[room_id].state == world_model.RoomState.PRESENTATION_ACTIVE
    for room_id in view.dormant_room_ids:
        assert decision[room_id].tier == world_model.Tier.DORMANT
        assert decision[room_id].state == world_model.RoomState.DORMANT
    assert view.interaction_room_id in view.camera_room_ids
    expected = (
        set(view.camera_room_ids)
        | set(view.urgent_room_ids)
        | set(view.deadline_room_ids)
        | set(view.approval_room_ids)
        | set(view.notification_room_ids)
    )
    assert set(view.active_room_ids) == expected
    assert set(view.dormant_room_ids) == {
        room.room_id for room in fixture.rooms
    } - expected


def test_hierarchical_path_graph_and_pathfinding_budget(fixture):
    graph = sf.build_scale_path_graph(fixture)
    nodes = graph.nodes()
    assert graph.portal_count == len(nodes) - 1
    for prefix in ("campus:", "wing:", "floor:", "room:"):
        assert any(node.startswith(prefix) for node in nodes)
    assert nodes  # non-empty
    # Every cell is a node and every room connects to its cells.
    room = fixture.rooms[0]
    cell = world_model.cell_node(room.room_id, room.cell_ids[0])
    assert cell in nodes
    assert graph.path(world_model.room_node(room.room_id), cell) is not None
    assert graph.path("room:missing", cell) is None
    report = sf.sample_pathfinding(fixture, graph, samples=16, iterations=2)
    assert report.samples == 16
    assert report.iterations == 2
    assert report.queries == 32
    assert report.avg_hops >= 1
    assert report.avg_ms <= 2.0
    assert report.p95_ms <= 8.0
    assert report.max_ms >= report.min_ms >= 0


def test_event_model_coalesces_and_respects_caps():
    budget = sf.model_event_budget(40, 80)
    assert budget.measured is False
    assert budget.normal_events <= sf.EVENT_NORMAL_LIMIT
    assert budget.burst_events <= sf.EVENT_BURST_LIMIT
    assert budget.burst_raw >= budget.burst_events
    assert budget.coalesced == budget.burst_raw - budget.burst_events
    # Lower caps are honoured rather than ignored.
    tight = sf.model_event_budget(
        40, 80, normal_cap=5, burst_cap=10
    )
    assert tight.normal_events == 5
    assert tight.burst_events == 10
    # No agents/avatars still keeps the UI constant under the cap.
    idle = sf.model_event_budget(0, 0)
    assert idle.normal_events == 1
    with pytest.raises(sf.ScaleFixtureError):
        sf.model_event_budget(-1, 0)


def test_device_tiers_carry_the_contract_limits():
    desktop = sf.device_tier_limits("desktop")
    low = sf.device_tier_limits("low")
    required = {
        "frame_p95_ms",
        "heap_mb",
        "decoded_texture_mb",
        "map_chunk_mb",
        "visible_floor_bundle_mb",
        "adjacent_prefetch_mb",
        "active_agents",
        "visible_avatars",
        "pathfinding_avg_ms",
        "pathfinding_p95_ms",
        "events_per_second",
        "event_burst",
    }
    assert required <= set(desktop)
    assert required <= set(low)
    assert desktop["frame_p95_ms"] == 16.7
    assert low["frame_p95_ms"] == 33.3
    assert low["heap_mb"] < desktop["heap_mb"]
    assert desktop["active_agents"] == 40
    assert desktop["visible_avatars"] == 80
    with pytest.raises(sf.ScaleFixtureError) as exc:
        sf.device_tier_limits("tablet")
    assert exc.value.code == "SCALE_FIXTURE_UNKNOWN_TIER"


def test_fixture_to_dict_is_a_projection_not_an_operational_workforce(fixture):
    payload = fixture.to_dict()
    assert payload["schema"] == sf.SCHEMA
    assert payload["synthetic"] is True
    assert payload["projection"] is True
    assert payload["operational"] is False
    assert payload["tenant_id"] == sf.SYNTHETIC_TENANT
    assert payload["counts"]["agents"] == 1000
    assert all(agent["operational"] is False for agent in payload["agents"])
    assert all(agent["status"] == "projection" for agent in payload["agents"])
