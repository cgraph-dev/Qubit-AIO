from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from voicebridge.office.world import exterior
from voicebridge.office.world.exterior import (
    CHUNK_KINDS,
    EXTERIOR_FEATURES,
    SCHEMA,
    VERSION,
    AssetRef,
    ExteriorChunk,
    ExteriorMap,
    ExteriorZone,
    PortalLink,
    build_exterior_map,
    exterior_payload,
    validate_exterior,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validate_exterior.py"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "validate_exterior_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rebuild(map_obj: ExteriorMap, **overrides) -> ExteriorMap:
    data = copy.deepcopy(map_obj.to_dict())
    data.update(overrides)
    data["digest"] = ""
    fresh = ExteriorMap.from_dict(data)
    data = fresh.to_dict()
    data["digest"] = exterior.compute_digest(data)
    return ExteriorMap.from_dict(data)


def test_build_is_deterministic_and_valid(tmp_path):
    first = build_exterior_map()
    second = build_exterior_map()
    assert first.to_dict() == second.to_dict()
    assert first.digest == second.digest
    assert validate_exterior(first, repo_root=tmp_path) == []

    counts = first.counts()
    assert counts["zones"] == 6
    assert counts["chunks"] == 20
    assert counts["portals"] == 8
    assert counts["assets"] == 15
    assert counts["dormant_zones"] == 1
    assert counts["active_zones"] == 5


def test_payload_is_versioned_and_carries_all_features():
    map_obj = build_exterior_map()
    payload = exterior_payload(map_obj)
    assert payload["schema"] == SCHEMA
    assert payload["version"] == VERSION
    assert payload["digest"] == map_obj.digest
    assert payload["ok"] is True

    features = {a.feature for a in map_obj.assets}
    features.update(c.kind for c in map_obj.chunks)
    assert set(EXTERIOR_FEATURES) <= features
    assert set(c.kind for c in map_obj.chunks) <= set(CHUNK_KINDS)


def test_lobby_links_exterior_to_tower_floors():
    map_obj = build_exterior_map()
    elevator = [p for p in map_obj.portals if p.kind == "elevator"]
    stairs = [p for p in map_obj.portals if p.kind == "stairs"]
    assert elevator and stairs
    floors = {p.to_floor for p in (*elevator, *stairs)}
    assert "floor-001" in floors and "floor-005" in floors
    lobby = map_obj.zone("zone-lobby")
    lobby_chunks = set(lobby.chunk_ids)
    assert all(p.from_chunk in lobby_chunks for p in (*elevator, *stairs))


def test_dormant_zone_must_not_animate(tmp_path):
    map_obj = build_exterior_map()
    data = copy.deepcopy(map_obj.to_dict())
    parking = next(c for c in data["chunks"]
                   if c["chunk_id"] == "chunk-parking-lot")
    parking["animated"] = True
    rebuilt = _rebuild(map_obj, chunks=data["chunks"])
    problems = validate_exterior(rebuilt, repo_root=tmp_path)
    assert any("dormant" in p and "animat" in p for p in problems)


def test_duplicate_ids_are_rejected(tmp_path):
    map_obj = build_exterior_map()
    data = copy.deepcopy(map_obj.to_dict())
    data["chunks"].append(copy.deepcopy(data["chunks"][0]))
    rebuilt = _rebuild(map_obj, chunks=data["chunks"])
    problems = validate_exterior(rebuilt, repo_root=tmp_path)
    assert any("duplicate id" in p for p in problems)


def test_chunk_outside_campus_is_rejected(tmp_path):
    map_obj = build_exterior_map()
    data = copy.deepcopy(map_obj.to_dict())
    chunk = next(c for c in data["chunks"]
                 if c["chunk_id"] == "chunk-ground-w")
    chunk["bounds"] = [80, 0, 48, 24]
    rebuilt = _rebuild(map_obj, chunks=data["chunks"])
    problems = validate_exterior(rebuilt, repo_root=tmp_path)
    assert any("outside the campus" in p for p in problems)


def test_disconnected_chunk_is_rejected(tmp_path):
    map_obj = build_exterior_map()
    data = copy.deepcopy(map_obj.to_dict())
    for chunk in data["chunks"]:
        if chunk["chunk_id"] == "chunk-garden-north":
            chunk["neighbours"] = []
        if chunk["chunk_id"] == "chunk-trees-east":
            chunk["neighbours"] = [
                n for n in chunk["neighbours"]
                if n != "chunk-garden-north"
            ]
    rebuilt = _rebuild(map_obj, chunks=data["chunks"])
    problems = validate_exterior(rebuilt, repo_root=tmp_path)
    assert any("unreachable" in p for p in problems)


def test_lobby_without_egress_is_rejected(tmp_path):
    map_obj = build_exterior_map()
    data = copy.deepcopy(map_obj.to_dict())
    data["portals"] = [
        p for p in data["portals"]
        if p["portal_id"] not in {"portal-lobby-campus",
                                  "portal-lobby-security"}
    ]
    rebuilt = _rebuild(map_obj, portals=data["portals"])
    problems = validate_exterior(rebuilt, repo_root=tmp_path)
    assert any("egress links" in p for p in problems)


def test_floor_portal_must_name_a_floor(tmp_path):
    map_obj = build_exterior_map()
    data = copy.deepcopy(map_obj.to_dict())
    for portal in data["portals"]:
        if portal["portal_id"] == "portal-elevator-001":
            portal["to_floor"] = "basement"
    rebuilt = _rebuild(map_obj, portals=data["portals"])
    problems = validate_exterior(rebuilt, repo_root=tmp_path)
    assert any("tower" in p and "floor" in p for p in problems)


def test_paid_art_inside_the_repository_is_rejected(tmp_path):
    map_obj = build_exterior_map()
    data = copy.deepcopy(map_obj.to_dict())
    paid = next(a for a in data["assets"] if a["paid"])
    paid["local_path"] = str(tmp_path / "repo" / "paid-art.png")
    (tmp_path / "repo").mkdir()
    rebuilt = _rebuild(map_obj, assets=data["assets"])
    problems = validate_exterior(rebuilt, repo_root=tmp_path / "repo")
    assert any("inside the repository" in p for p in problems)


def test_paid_art_must_be_local_only_and_non_redistributable(tmp_path):
    map_obj = build_exterior_map()
    data = copy.deepcopy(map_obj.to_dict())
    paid = next(a for a in data["assets"] if a["paid"])
    paid["local_only"] = False
    paid["redistribution_allowed"] = True
    rebuilt = _rebuild(map_obj, assets=data["assets"])
    problems = validate_exterior(rebuilt, repo_root=tmp_path)
    assert any("local_only" in p for p in problems)
    assert any("redistributable" in p for p in problems)


def test_unbounded_policy_is_rejected(tmp_path):
    map_obj = build_exterior_map()
    policy = copy.deepcopy(map_obj.to_dict()["resource_policy"])
    policy["max_active_chunks"] = 1
    policy["lru_capacity"] = 0
    policy["only_active_zones_animate"] = False
    rebuilt = _rebuild(map_obj, resource_policy=policy)
    problems = validate_exterior(rebuilt, repo_root=tmp_path)
    assert any("active exceed" in p for p in problems)
    assert any("lru_capacity" in p for p in problems)
    assert any("only_active_zones_animate" in p for p in problems)


def test_animated_chunk_requires_active_animating_zone(tmp_path):
    map_obj = build_exterior_map()
    data = copy.deepcopy(map_obj.to_dict())
    for zone in data["zones"]:
        if zone["zone_id"] == "zone-security":
            zone["animate"] = False
    for chunk in data["chunks"]:
        if chunk["chunk_id"] == "chunk-security-desk":
            chunk["animated"] = True
    rebuilt = _rebuild(map_obj, zones=data["zones"], chunks=data["chunks"])
    problems = validate_exterior(rebuilt, repo_root=tmp_path)
    assert any("non-animating" in p for p in problems)


def test_unknown_asset_reference_is_rejected(tmp_path):
    map_obj = build_exterior_map()
    data = copy.deepcopy(map_obj.to_dict())
    for chunk in data["chunks"]:
        if chunk["chunk_id"] == "chunk-ground-w":
            chunk["assets"].append("asset-does-not-exist")
    rebuilt = _rebuild(map_obj, chunks=data["chunks"])
    problems = validate_exterior(rebuilt, repo_root=tmp_path)
    assert any("unknown asset" in p for p in problems)


def test_chunk_overlap_on_the_same_layer_is_rejected(tmp_path):
    map_obj = build_exterior_map()
    data = copy.deepcopy(map_obj.to_dict())
    for chunk in data["chunks"]:
        if chunk["chunk_id"] == "chunk-ground-e":
            chunk["bounds"] = [24, 0, 48, 24]
    rebuilt = _rebuild(map_obj, chunks=data["chunks"])
    problems = validate_exterior(rebuilt, repo_root=tmp_path)
    assert any("overlap" in p for p in problems)


def test_variants_must_declare_their_default(tmp_path):
    map_obj = build_exterior_map()
    variants = copy.deepcopy(map_obj.to_dict()["variants"])
    variants["default_weather"] = "hail"
    rebuilt = _rebuild(map_obj, variants=variants)
    problems = validate_exterior(rebuilt, repo_root=tmp_path)
    assert any("default" in p and "weather" in p for p in problems)


def test_asset_ref_roundtrip_and_unknown_lookup():
    ref = AssetRef(
        asset_id="asset-x", feature="prop", pack="pack",
        local_path="/tmp/x.png", sha256="a" * 64, paid=False,
        redistribution_allowed=True, local_only=False)
    assert AssetRef.from_dict(ref.to_dict()) == ref
    with pytest.raises(exterior.ExteriorMapError):
        build_exterior_map().asset("nope")


def test_chunk_and_portal_dataclasses_roundtrip():
    zone = ExteriorZone("z", "lobby", "L", True, True, ("c",), "c")
    chunk = ExteriorChunk("c", "z", "lobby", "floor", 0, 0, 16, 12,
                          True, False)
    portal = PortalLink("p", "elevator", "c", to_floor="floor-001")
    for obj in (zone, chunk, portal):
        assert type(obj).from_dict(obj.to_dict()) == obj
    assert chunk.tiles == 192
    assert chunk.bounds == (0, 0, 16, 12)


def test_validator_script_json_pass(capsys):
    module = _load_script()
    assert module.main(["--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["checks_failed"] == 0
    assert report["problems"] == []
    assert report["map_schema"] == SCHEMA


def test_validator_script_fails_on_a_broken_map(monkeypatch, capsys):
    module = _load_script()
    broken = build_exterior_map()
    data = copy.deepcopy(broken.to_dict())
    data["chunks"] = data["chunks"][:-1]
    rebuilt = _rebuild(broken, chunks=data["chunks"])
    monkeypatch.setattr(module, "build_exterior_map", lambda: rebuilt)
    assert module.main(["--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["problems"]
