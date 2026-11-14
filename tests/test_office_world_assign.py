"""The world assignment is explicit, deterministic and never invents desks."""
from voicebridge.office.world import assign, occupancy


class _Agent:
    def __init__(self, aid, floor=None, metadata=None, status="active"):
        self.agent_id = aid
        self.status = status
        self.metadata = dict(metadata or {})
        if floor is not None:
            self.metadata["floor"] = floor
        self.display_name = aid
        self.department = "executive"


class _Service:
    def __init__(self, agents):
        self.agents = list(agents)
        self.updates = []

    def list(self, tenant_id, business_id=None):
        return list(self.agents)

    def update(self, tenant_id, agent_id, *, changes, idempotency_key, actor):
        self.updates.append((agent_id, changes, idempotency_key, actor))
        for a in self.agents:
            if a.agent_id == agent_id:
                a.metadata = dict(changes["metadata"])
                return a
        raise AssertionError(agent_id)


def test_assigns_preferred_floor_and_writes_world_metadata():
    svc = _Service([_Agent("a", floor=2), _Agent("b", floor=2)])
    out = assign.assign_agents(svc, "ten")
    assert out["counts"]["assigned"] == 2
    assert out["counts"]["skipped"] == 0
    desks = {a.metadata["world_desk"] for a in svc.agents}
    assert len(desks) == 2
    assert all(a.metadata["world_floor"] for a in svc.agents)


def test_repeat_is_idempotent_and_never_moves_existing_links():
    svc = _Service([_Agent("a", floor=2, metadata={"world_desk": "manual:desk"})])
    out = assign.assign_agents(svc, "ten")
    assert out["counts"]["already_linked"] == 1
    assert svc.updates == []


def test_dormant_is_skipped():
    svc = _Service([_Agent("a", floor=2, status="paused")])
    out = assign.assign_agents(svc, "ten")
    assert out["counts"]["skipped"] == 1
    assert out["skipped"][0]["reason"] == "dormant"


def test_assignment_is_visible_to_occupancy():
    svc = _Service([_Agent("a", floor=2)])
    assign.assign_agents(svc, "ten")
    from voicebridge.business.roles import RoleRegistry
    proj = occupancy.project_occupancy(floors=RoleRegistry.default().floors,
                                       agents=svc.agents)
    assert len(proj.placements) == 1
    assert proj.unplaced == ()


def test_no_free_desk_is_reported(monkeypatch):
    svc = _Service([_Agent("a", floor=2), _Agent("b", floor=2)])
    # A one-desk index makes the second agent unplaceable.
    real = occupancy._Index
    class One:
        def __init__(self, floors):
            self.desks = tuple(real(floors).desks[:1])
    monkeypatch.setattr(occupancy, "_Index", One)
    out = assign.assign_agents(svc, "ten")
    assert out["counts"]["assigned"] == 1
    assert out["counts"]["skipped"] == 1


def test_world_assign_route_is_registered_before_the_agent_id_route():
    from voicebridge import qubit_compat as HC
    route, _params = HC.match(
        "POST", "/api/office/business/agents/world-assign")
    assert route is not None
    assert route["handler"] == "h_business_agents_assign_world"
    assert "h_business_agents_assign_world" in HC.HANDLERS
