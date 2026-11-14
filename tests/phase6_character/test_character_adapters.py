from __future__ import annotations

import json
from dataclasses import replace

from voicebridge.business import character_profile as cp
from voicebridge.business.character_profile import (
    ADAPTERS,
    ANIMATIONS,
    CATALOG_INDEX,
    PART_COUNTS,
    PROJECTION_SCHEMA,
    apply_fallbacks,
    default_profile,
    project_habblaud,
    project_limezu_character,
    project_limezu_portrait,
    project_profile,
)


def _profile(**changes):
    return replace(default_profile("profile-a", "agent-a"), **changes)


def test_measured_part_counts_are_the_p6_contract():
    counts = cp.part_counts()
    assert {key: counts[key] for key in PART_COUNTS} == dict(PART_COUNTS)
    assert counts == {
        "body": 9, "skin": 9, "eyes": 7, "hair": 29, "outfit": 33,
        "accessory": 6,
    }


def test_bundle_has_exactly_three_adapters_and_is_deterministic():
    profile = _profile(revision=4, seed=17)
    first = project_profile(profile)
    second = project_profile(profile)
    assert first == second
    assert first["schema"] == PROJECTION_SCHEMA
    assert set(first["adapters"]) == set(ADAPTERS) == {
        "habblaud", "limezu_character", "limezu_portrait"}
    assert first["profile_id"] == "profile-a"
    assert first["agent_id"] == "agent-a"
    assert first["revision"] == 4
    assert first["seed"] == 17


def test_habblaud_projection_follows_declared_layer_order():
    order = ("body", "outfit", "skin", "hair", "eyes", "accessory")
    profile = _profile(
        layer_order=order,
        accessory_ids=("accessory-01", "accessory-04"),
        shadow_policy="room",
    )
    projection = project_habblaud(profile)
    assert projection["adapter"] == "habblaud"
    assert projection["layer_order"] == list(order)
    assert [row["layer"] for row in projection["layers"]] == list(order)
    assert [row["z"] for row in projection["layers"]] == list(range(6))
    body = projection["layers"][0]
    assert body["part_id"] == profile.body_id
    assert body["index"] == CATALOG_INDEX[profile.body_id].index
    accessory = projection["layers"][-1]
    assert accessory["part_ids"] == ["accessory-01", "accessory-04"]
    assert accessory["indices"] == [1, 4]
    assert accessory["visible"] is True
    assert projection["shadow_policy"] == "room"
    assert projection["animation_set"] == list(ANIMATIONS)


def test_limezu_character_projection_maps_part_indices():
    profile = _profile(
        body_id="body-03", skin_id="skin-08", eyes_id="eyes-05",
        hair_id="hair-27", outfit_id="outfit-30",
        accessory_ids=("accessory-02",),
    )
    projection = project_limezu_character(profile)
    assert projection["adapter"] == "limezu-character-generator-2.0"
    assert projection["parts"] == {
        "body": 3, "skin": 8, "eyes": 5, "hair": 27, "outfit": 30,
    }
    assert projection["part_ids"] == {
        "body": "body-03", "skin": "skin-08", "eyes": "eyes-05",
        "hair": "hair-27", "outfit": "outfit-30",
    }
    assert projection["accessories"] == [
        {"part_id": "accessory-02", "index": 2}]


def test_limezu_portrait_projection_maps_cell_and_both_sizes():
    profile = _profile(portrait_cell="cell-3", portrait_size=32)
    small = project_limezu_portrait(profile)
    assert small["adapter"] == "limezu-portrait-generator-1.5"
    assert small["portrait_cell"] == "cell-3"
    assert small["cell_index"] == 3
    assert small["portrait_size"] == 32
    assert small["frame"] == "cell-3_32"
    assert small["part_indices"]["body"] == 0

    large = project_limezu_portrait(replace(profile, portrait_size=48))
    assert large["portrait_size"] == 48
    assert large["frame"] == "cell-3_48"


def test_unknown_cell_is_reported_without_guessing_art():
    projection = project_limezu_portrait(_profile(portrait_cell="not-a-cell"))
    assert projection["cell_index"] == -1
    assert projection["portrait_cell"] == "not-a-cell"


def test_projections_carry_no_local_or_paid_asset_path():
    profile = _profile(accessory_ids=("accessory-01",))
    serialised = json.dumps(project_profile(profile), sort_keys=True)
    assert "/home/trick" not in serialised
    assert ".png" not in serialised
    assert "local_path" not in serialised
    assert "source_asset_refs" not in serialised
    assert "paid" not in serialised


def test_projections_are_pure_and_echo_identity_and_seed():
    profile = _profile(revision=6, seed=99, style_version=cp.STYLE_VERSION)
    before = profile.to_dict()
    projection = project_profile(profile)
    assert profile.to_dict() == before
    for adapter in projection["adapters"].values():
        assert adapter["profile_id"] == "profile-a"
        assert adapter["revision"] == 6
        assert adapter["seed"] == 99
    habblaud = projection["adapters"]["habblaud"]
    assert habblaud["style_version"] == cp.STYLE_VERSION
    assert habblaud["agent_id"] == "agent-a"


def test_missing_part_fallback_then_projection_is_clean(tmp_path):
    profile = _profile(body_id="body-999", hair_id="hair-999",
                       accessory_ids=("accessory-999",))
    resolved, fallbacks = apply_fallbacks(profile, repo_root=tmp_path)
    assert {row["category"] for row in fallbacks} >= {"body", "hair",
                                                      "accessory"}
    projection = project_profile(resolved)
    assert projection["adapters"]["limezu_character"]["parts"]["body"] == 0
    assert projection["adapters"]["limezu_character"]["parts"]["hair"] == 0
    assert projection["adapters"]["habblaud"]["layers"]
    assert projection["adapters"]["limezu_character"]["accessories"] == []


def test_projection_survives_export_and_import(tmp_path):
    store = cp.CharacterProfileStore(tmp_path / "state",
                                     repo_root=tmp_path / "repo")
    saved = store.save(_profile(revision=0, seed=12))
    document = store.export(saved.profile_id)
    other = cp.CharacterProfileStore(tmp_path / "state-b",
                                     repo_root=tmp_path / "repo")
    imported = other.import_document(document)
    original = project_profile(store.load(saved.profile_id))
    rebuilt = project_profile(other.load(imported.profile_id))
    assert rebuilt["adapters"] == original["adapters"]
