"""P7-05 — outside life, weather, seasons and social graph (hermetic)."""
from __future__ import annotations

import copy
import json

import pytest

from voicebridge.office.world import outside_life as OL

pytestmark = pytest.mark.unit


def _build(events, **kwargs):
    return OL.build_outside_life(events, **kwargs)


def test_empty_source_produces_empty_projection(map_obj):
    life = _build([], map_obj=map_obj)
    assert life.summary["observed"] is False
    assert life.environment["observed"] is False
    assert life.environment["weather"] is None
    assert life.environment["day_night"] is None
    assert life.environment["season"] is None
    assert life.environment["transitions"] == []
    assert life.ambient == ()
    assert life.social["records"] == []
    assert life.social["edges"] == []
    assert life.social["nodes"] == []
    assert OL.validate_outside_life(life, map_obj=map_obj) == []


def test_none_source_is_also_empty(map_obj):
    life = _build(None, map_obj=map_obj)
    assert life.summary["observed"] is False
    assert life.ambient == ()
    assert life.social["records"] == []


def test_weather_day_night_and_season_follow_source_events(map_obj, source_events):
    life = _build(source_events, map_obj=map_obj)
    environment = life.environment
    assert environment["observed"] is True
    assert environment["weather"] == "rain"
    assert environment["day_night"] == "dusk"
    assert environment["season"] == "autumn"
    assert environment["transitions_total"] == 6
    for transition in environment["transitions"]:
        assert transition["cue"]
        assert transition["still_cue"]
        assert transition["animated"] == (
            transition["cue"] != transition["still_cue"]
        )


def test_unknown_environment_value_is_ignored_not_defaulted(map_obj):
    events = [
        {
            "event_id": "evt-bad-weather",
            "type": "weather.changed",
            "occurred_at": "2026-01-01T00:00:00Z",
            "payload": {"value": "meteor-shower"},
        }
    ]
    life = _build(events, map_obj=map_obj)
    assert life.environment["observed"] is False
    assert life.environment["weather"] is None
    assert any(
        item["reason"] == "unknown_environment_value"
        for item in life.ignored
    )


def test_only_active_zones_receive_ambient_life(map_obj, source_events):
    life = _build(source_events, map_obj=map_obj)
    active = {zone.zone_id for zone in map_obj.zones if zone.active}
    assert {sprite["zone_id"] for sprite in life.ambient} <= active
    assert len(life.ambient) == 2
    reasons = {(item["reason"], item.get("detail")) for item in life.ignored}
    assert ("inactive_zone", "zone-parking") in reasons
    assert ("unknown_zone", "zone-nowhere") in reasons
    for sprite in life.ambient:
        assert sprite["presentation_only"] is True
        assert sprite["cue"]
        assert sprite["still_cue"]


def test_ambient_sprites_stay_inside_the_zone_bounds(map_obj, source_events):
    life = _build(source_events, map_obj=map_obj)
    bounds = {
        zone.zone_id: {
            "x": min(c.x for c in map_obj.chunks if c.zone_id == zone.zone_id),
            "y": min(c.y for c in map_obj.chunks if c.zone_id == zone.zone_id),
        }
        for zone in map_obj.zones
        if any(c.zone_id == zone.zone_id for c in map_obj.chunks)
    }
    for sprite in life.ambient:
        assert sprite["x"] >= bounds[sprite["zone_id"]]["x"]
        assert sprite["y"] >= bounds[sprite["zone_id"]]["y"]


def test_social_graph_is_folded_from_interaction_events(map_obj, source_events):
    life = _build(source_events, map_obj=map_obj)
    edges = {tuple(edge["participants"]): edge for edge in life.social["edges"]}
    friendly = edges[("agent-a", "agent-b")]
    assert friendly["classification"] == "friendship"
    assert friendly["trust"] == 3.0
    assert friendly["friendship_events"] == 1
    rival = edges[("agent-c", "agent-d")]
    assert rival["classification"] == "rivalry"
    assert rival["trust"] == -4.0
    assert rival["rivalry_events"] == 1
    nodes = {node["node_id"]: node for node in life.social["nodes"]}
    assert set(nodes) == {"agent-a", "agent-b", "agent-c", "agent-d"}
    assert all(node["degree"] == 1 for node in nodes.values())


def test_cross_scope_social_records_are_denied(map_obj, source_events):
    engineering = OL.ScopedSocialACL(
        OL.DEFAULT_TENANT, departments=["engineering"]
    )
    sales = OL.ScopedSocialACL(OL.DEFAULT_TENANT, departments=["sales"])
    life = _build(source_events, map_obj=map_obj, acl=engineering)
    scopes = {record["scope_id"] for record in life.social["records"]}
    assert scopes == {"engineering"}
    assert life.social["denied"] == 1
    assert {participant for edge in life.social["edges"]
            for participant in edge["participants"]} == {
        "agent-a", "agent-b"
    }

    record = OL.SocialMemoryRecord.from_payload(life.social["records"][0])
    assert engineering.can_read(None, record) is True
    assert sales.can_read(None, record) is False
    assert engineering.can_write(None, record.scope, record) is True
    assert sales.can_write(None, record.scope, record) is False
    OL.require_social_read(record, engineering, None)
    with pytest.raises(OL.SocialScopeDenied):
        OL.require_social_read(record, sales, None)
    with pytest.raises(OL.SocialScopeDenied):
        _build(source_events, map_obj=map_obj, acl=engineering,
               strict_acl=True)


def test_retention_and_provenance_on_every_social_record(map_obj, source_events):
    life = _build(source_events, map_obj=map_obj)
    for record in life.social["records"]:
        assert record["tenant_id"]
        assert record["scope"] in OL.SCOPES
        assert record["scope_id"]
        assert record["visibility"] in OL.VISIBILITIES
        assert record["retention"] in OL.RETENTION_CLASSES
        assert record["simulation_only"] is True
        assert record["source_event_ids"]
        assert record["provenance"]["event_ids"]
        assert -OL.MAX_TRUST_DELTA <= record["trust_delta"] <= OL.MAX_TRUST_DELTA


def test_expired_social_records_are_not_projected(map_obj):
    events = [
        {
            "event_id": "evt-expired",
            "type": "interaction",
            "occurred_at": "2026-01-01T00:00:00Z",
            "payload": {
                "participants": ["agent-a", "agent-b"],
                "department": "engineering",
                "relationship": "friendship",
                "expires_at": "2020-01-01T00:00:00Z",
            },
        }
    ]
    without_now = _build(events, map_obj=map_obj)
    assert len(without_now.social["records"]) == 1
    with_now = _build(events, map_obj=map_obj,
                      now="2026-01-01T00:00:00Z")
    assert with_now.social["records"] == []
    assert any(item["reason"] == "invalid_interaction_event"
               for item in with_now.ignored)


def test_bad_scope_and_visibility_are_ignored(map_obj):
    events = [
        {
            "event_id": "evt-bad-scope",
            "type": "interaction",
            "occurred_at": "2026-01-01T00:00:00Z",
            "payload": {
                "participants": ["agent-a", "agent-b"],
                "scope": "galaxy",
                "scope_id": "milky-way",
            },
        },
        {
            "event_id": "evt-bad-visibility",
            "type": "interaction",
            "occurred_at": "2026-01-01T00:01:00Z",
            "payload": {
                "participants": ["agent-c", "agent-d"],
                "department": "engineering",
                "visibility": "everyone",
            },
        },
    ]
    life = _build(events, map_obj=map_obj)
    assert life.social["records"] == []
    assert sum(1 for item in life.ignored
               if item["reason"] == "invalid_interaction_event") == 2


def test_deterministic_replay_survives_input_order(map_obj, source_events):
    first = _build(source_events, map_obj=map_obj)
    second = _build(copy.deepcopy(source_events), map_obj=map_obj)
    shuffled = _build(list(reversed(source_events)), map_obj=map_obj)
    assert first.to_dict() == second.to_dict()
    assert first.digest == second.digest == shuffled.digest


def test_projection_is_bounded(map_obj):
    events = [
        {
            "event_id": f"evt-v-{index}",
            "type": "outside.visitor",
            "occurred_at": f"2026-01-01T00:00:{index:02d}Z",
            "zone_id": "zone-campus",
            "payload": {"count": 4},
        }
        for index in range(60)
    ]
    events.extend(
        {
            "event_id": f"evt-s-{index}",
            "type": "interaction",
            "occurred_at": f"2026-01-01T01:00:{index % 60:02d}Z",
            "payload": {
                "participants": [f"agent-{index}-a", f"agent-{index}-b"],
                "department": "engineering",
            },
        }
        for index in range(700)
    )
    life = _build(events, map_obj=map_obj)
    assert len(life.ambient) <= OL.MAX_AMBIENT
    assert len(life.social["records"]) <= OL.MAX_SOCIAL_RECORDS
    assert len(life.social["edges"]) <= OL.MAX_SOCIAL_EDGES
    assert len(life.social["nodes"]) <= OL.MAX_SOCIAL_NODES
    assert any(item["reason"] == "social_record_cap_reached"
               for item in life.ignored)


def test_presentation_only_and_no_business_truth(map_obj, source_events):
    life = _build(source_events, map_obj=map_obj)
    payload = life.to_dict()
    assert payload["mode"] == "presentation"
    assert payload["business_truth"] is False
    assert life.constraints["presentation_only"] is True
    assert life.constraints["simulation_only"] is True
    assert life.constraints["source_driven"] is True

    def keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    assert not set(keys(payload)) & set(OL.FORBIDDEN_TRUTH_KEYS)


def test_payload_is_versioned(map_obj, source_events):
    life = _build(source_events, map_obj=map_obj)
    payload = OL.outside_life_payload(life)
    assert payload["ok"] is True
    assert payload["schema"] == OL.SCHEMA
    assert payload["version"] == OL.VERSION
    assert payload["digest"] == life.digest


def test_validation_rejects_a_tampered_projection(map_obj, source_events):
    life = _build(source_events, map_obj=map_obj)

    data = copy.deepcopy(life.to_dict())
    data["environment"]["weather"] = "meteor-shower"
    data["digest"] = ""
    fresh = OL.OutsideLife.from_dict(data)
    data = fresh.to_dict()
    data["digest"] = OL.compute_digest(data)
    problems = OL.validate_outside_life(
        OL.OutsideLife.from_dict(data), map_obj=map_obj
    )
    assert any("environment.weather" in problem for problem in problems)

    data = copy.deepcopy(life.to_dict())
    data["ambient"][0]["zone_id"] = "zone-parking"
    data["digest"] = ""
    fresh = OL.OutsideLife.from_dict(data)
    data = fresh.to_dict()
    data["digest"] = OL.compute_digest(data)
    problems = OL.validate_outside_life(
        OL.OutsideLife.from_dict(data), map_obj=map_obj
    )
    assert any("outside an active zone" in problem for problem in problems)

    data = copy.deepcopy(life.to_dict())
    data["social"]["records"][0]["retention"] = "forever"
    data["digest"] = ""
    fresh = OL.OutsideLife.from_dict(data)
    data = fresh.to_dict()
    data["digest"] = OL.compute_digest(data)
    problems = OL.validate_outside_life(
        OL.OutsideLife.from_dict(data), map_obj=map_obj
    )
    assert any("bad retention" in problem for problem in problems)


def test_verifier_passes_and_reports_checks():
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "verify_outside_life.py"
    spec = importlib.util.spec_from_file_location(
        "verify_outside_life_test", script
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    report = module.run_checks()
    assert report["ok"] is True
    assert report["checks_failed"] == 0
    names = {check["name"] for check in report["checks"]}
    assert {
        "source_event_environment_mapping",
        "empty_source_empty_projection",
        "deterministic_replay",
        "active_zone_only",
        "social_graph_from_events",
        "cross_scope_denied",
        "retention_provenance",
        "bounded",
        "presentation_only",
        "no_business_truth",
    } <= names
    assert json.loads(json.dumps(report))["ok"] is True


def test_verifier_exit_codes(monkeypatch):
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "verify_outside_life.py"
    spec = importlib.util.spec_from_file_location(
        "verify_outside_life_exit_test", script
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(["--json"]) == 0

    def boom():
        raise RuntimeError("build failed")

    monkeypatch.setattr(module, "run_checks", boom)
    assert module.main(["--json"]) == 2
