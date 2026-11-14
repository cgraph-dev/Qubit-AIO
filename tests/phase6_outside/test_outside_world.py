"""P6-03 — outside-world presentation projection (hermetic, no art, no clock)."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from voicebridge.office.world import outside_world as OW
from voicebridge.office.world import exterior as EX

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_outside_world.py"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "verify_outside_world_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tampered(world: OW.OutsideWorld, **changes) -> OW.OutsideWorld:
    data = copy.deepcopy(world.to_dict())
    for key, value in changes.items():
        data[key] = value
    data["digest"] = ""
    fresh = OW.OutsideWorld.from_dict(data)
    data = fresh.to_dict()
    data["digest"] = OW.compute_digest(data)
    return OW.OutsideWorld.from_dict(data)


def test_projection_is_deterministic_and_valid(tmp_path):
    first = OW.build_outside_world()
    second = OW.build_outside_world()
    assert first.to_dict() == second.to_dict()
    assert first.digest == second.digest
    assert OW.validate_outside_world(first, repo_root=tmp_path) == []
    assert first.map_digest == EX.build_exterior_map().digest


def test_projection_totals_match_the_exterior():
    world = OW.build_outside_world()
    summary = world.summary
    assert summary["zones"] == 6
    assert summary["active_zones"] == 5
    assert summary["dormant_zones"] == 1
    assert summary["chunks"] == 20
    assert summary["active_chunks"] == 19
    assert summary["animated_chunks"] == 5
    assert summary["portals"] == 8
    assert summary["elevator_links"] == 3
    assert summary["stairs_links"] == 1
    assert summary["places"] == 20


def test_every_required_place_is_presented():
    world = OW.build_outside_world()
    kinds = {place["kind"] for place in world.places}
    kinds.update(portal["kind"] for portal in world.portals)
    for label, kind in OW.REQUIRED_PLACES:
        assert kind in kinds, f"{label} ({kind}) missing"


def test_only_active_zones_animate_and_dormant_stays_dormant():
    world = OW.build_outside_world()
    zone_state = {zone["zone_id"]: zone["state"] for zone in world.zones}
    animated = [c for c in world.chunks if c["animated"]]
    assert animated
    for chunk in animated:
        assert zone_state[chunk["zone_id"]] == "active"
    parking = [
        c for c in world.chunks if c["zone_id"] == "zone-parking"]
    assert parking
    assert all(not c["active"] and not c["animated"] for c in parking)
    assert all(c["state"] == "dormant" for c in parking)


def test_environment_events_cover_every_declared_variant():
    map_obj = EX.build_exterior_map()
    events = OW.environment_events(map_obj)
    mapped = {(event["kind"], event["value"]) for event in events}
    declared = {
        ("weather", v) for v in map_obj.variants.weather
    } | {
        ("day_night", v) for v in map_obj.variants.day_night
    } | {
        ("season", v) for v in map_obj.variants.seasons
    }
    assert mapped == declared
    for event in events:
        assert event["label"].strip()
        assert event["still_cue"].strip()
        assert event["cue"].strip()
        assert event["animated"] == (event["cue"] != event["still_cue"])


def test_lobby_links_reach_the_tower_floors():
    world = OW.build_outside_world()
    links = world.floor_links
    assert any(link["kind"] == "elevator" for link in links)
    assert any(link["kind"] == "stairs" for link in links)
    assert any(link["floor_id"] == "floor-001" for link in links)
    assert all(link["floor_id"].startswith("floor-") for link in links)


def test_paid_art_is_metadata_only_and_credited():
    world = OW.build_outside_world()
    paid = [asset for asset in world.assets if asset["paid"]]
    assert paid
    for asset in paid:
        assert asset["local_only"] is True
        assert asset["redistribution_allowed"] is False
        assert asset["credit"] == world.credit
    assert world.credit == "Office art: LimeZu — https://limezu.itch.io"


def test_projection_carries_no_business_truth():
    payload = OW.build_outside_world().to_dict()
    assert payload["mode"] == "presentation"
    assert payload["business_truth"] is False
    assert payload["constraints"]["presentation_only"] is True
    assert payload["constraints"]["business_truth"] is False

    def keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    seen = set(keys(payload))
    assert not seen & set(OW.FORBIDDEN_TRUTH_KEYS)


def test_validation_rejects_a_tampered_projection(tmp_path):
    world = OW.build_outside_world()

    # A dormant chunk that claims to animate must be caught.
    chunks = copy.deepcopy(world.to_dict()["chunks"])
    for chunk in chunks:
        if chunk["chunk_id"] == "chunk-parking-lot":
            chunk["animated"] = True
            chunk["active"] = True
            chunk["state"] = "active"
    problems = OW.validate_outside_world(
        _tampered(world, chunks=chunks), repo_root=tmp_path)
    assert any("dormant zone" in p for p in problems)

    # An unmapped environment value must be caught.
    environment = copy.deepcopy(world.to_dict()["environment"])
    environment["events"] = [
        event for event in environment["events"]
        if event["kind"] != "weather"]
    problems = OW.validate_outside_world(
        _tampered(world, environment=environment), repo_root=tmp_path)
    assert any("unmapped values" in p for p in problems)

    # A lost stairs link must be caught.
    problems = OW.validate_outside_world(
        _tampered(world, floor_links=[]), repo_root=tmp_path)
    assert any("stairs" in p for p in problems)

    # A business-truth key must be caught.
    data = copy.deepcopy(world.to_dict())
    data["zones"][0]["agent_id"] = "agent-1"
    data["digest"] = ""
    fresh = OW.OutsideWorld.from_dict(data)
    data = fresh.to_dict()
    data["digest"] = OW.compute_digest(data)
    problems = OW.validate_outside_world(
        OW.OutsideWorld.from_dict(data), repo_root=tmp_path)
    assert any("business-truth" in p for p in problems)

    # A paid path inside the public tree must be caught.
    map_obj = EX.build_exterior_map()
    data = copy.deepcopy(map_obj.to_dict())
    paid = next(a for a in data["assets"] if a["paid"])
    paid["local_path"] = str(Path(tmp_path) / "repo" / "paid.png")
    (Path(tmp_path) / "repo").mkdir()
    rebuilt = EX.ExteriorMap.from_dict(
        {**data, "digest": EX.compute_digest(
            {k: v for k, v in data.items() if k != "digest"})})
    world_map = OW.build_outside_world(rebuilt)
    problems = OW.validate_outside_world(
        world_map, repo_root=Path(tmp_path) / "repo", map_obj=rebuilt)
    assert any("inside the public" in p for p in problems)


def test_payload_is_versioned_and_bounded():
    world = OW.build_outside_world()
    payload = OW.outside_world_payload(world)
    assert payload["ok"] is True
    assert payload["schema"] == OW.SCHEMA
    assert payload["version"] == OW.VERSION
    assert payload["digest"] == world.digest
    assert payload["world"]["summary"] == world.summary


def test_verify_script_passes_and_reports_its_checks():
    module = _load_script()
    report = module.run_checks()
    assert report["ok"] is True
    assert report["checks_failed"] == 0
    names = {check["name"] for check in report["checks"]}
    assert {
        "deterministic", "projection_valid", "semantic_places",
        "only_active_zones_animate", "environment_event_mapping",
        "reduced_motion_cues", "lobby_to_tower_floors", "paid_art_local_only",
        "presentation_only",
    } <= names
    # The report must serialise cleanly (the --json contract).
    assert json.loads(json.dumps(report))["ok"] is True


def test_verify_script_exit_codes(monkeypatch):
    module = _load_script()
    assert module.main(["--json"]) == 0

    def boom():
        raise RuntimeError("build failed")

    monkeypatch.setattr(module, "run_checks", boom)
    assert module.main(["--json"]) == 2
