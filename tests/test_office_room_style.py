"""Room style system: catalog schema, archetypes, recipes, validation.

Pinned here:

  * the five sample archetype recipes validate against the JSON schema and
    against :func:`validate_recipe`, and are deterministic;
  * all 18 plan archetypes exist, with curated 60/30/10 palettes;
  * a 16x12 plan is accepted for every sample recipe;
  * walkability, capacity, collision, interaction, wrong-theme props, mixed
    shadow policy, world tile size and single-direction furniture are refused;
  * ``room_seed`` and the seeded streams are stable across calls and independent
    across streams.

Hermetic: pure metadata, no art, no network.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import jsonschema
import pytest

from voicebridge.office.world import recipes as R
from voicebridge.office.world import style as S

SCHEMA_DIR = Path(S.__file__).resolve().parent / "schema"


def _schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _catalog() -> list[S.AssetEntry]:
    rows = [
        ("desk", "desk"),
        ("table", "table"),
        ("storage", "storage"),
        ("sign", "sign"),
        ("plant", "plant"),
        ("decor", "decor"),
        ("display", "display"),
        ("console", "console"),
        ("chair", "chair"),
        ("rack", "rack"),
    ]
    out = []
    for category, _ in rows:
        layer = "decor" if category in ("sign", "plant", "decor") else "furniture"
        surface = "decor" if layer == "decor" else "furniture"
        out.append(S.AssetEntry(
            id=f"asset-{category}",
            file=f"16x16/{category}.png",
            theme="limezu",
            category=category,
            size=16,
            footprint=(2, 1),
            layer=layer,
            surface=surface,
            shadow_variant="shadowless",
            tags=(category,),
        ))
    out.append(S.AssetEntry(
        id="asset-counter",
        file="16x16/counter.png",
        theme="limezu",
        category="counter",
        size=16,
        footprint=(2, 1),
        shadow_variant="shadowless",
    ))
    out.append(S.AssetEntry(
        id="asset-door",
        file="16x16/door.png",
        theme="limezu",
        category="doors",
        size=16,
        footprint=(1, 1),
        layer="decor",
        surface="decor",
        orientations=("any",),
        shadow_variant="shadowless",
        single_direction=True,
        socket_bound=False,
    ))
    out.append(S.AssetEntry(
        id="asset-32-desk",
        file="32x32/desk.png",
        theme="limezu",
        category="desk",
        size=32,
        footprint=(2, 1),
        shadow_variant="shadowless",
    ))
    out.append(S.AssetEntry(
        id="asset-kitchen-decor",
        file="16x16/decor.png",
        theme="limezu",
        category="decor",
        size=16,
        footprint=(1, 1),
        layer="decor",
        surface="decor",
        shadow_variant="shadowless",
        tags=("kitchen",),
    ))
    return out


CATALOG = _catalog()
COMPLETE_RECIPES = ("executive", "support", "finance", "creative",
                    "control_room")


# --- schemas and the five samples ------------------------------------------

@pytest.mark.parametrize("archetype", COMPLETE_RECIPES)
def test_sample_recipes_validate_against_the_json_schema(archetype):
    recipe = R.sample_recipe(archetype)
    jsonschema.validate(recipe.to_dict(), _schema("room_style.schema.json"))
    assert S.validate_recipe(recipe) == []


def test_all_18_archetypes_exist():
    expected = {
        "executive", "open_office", "reception", "support", "sales",
        "finance", "creative", "control_room", "research", "meeting",
        "kitchen", "wellness", "showcase", "server_ops", "legal", "training",
        "fulfillment", "media",
    }
    assert set(S.ARCHETYPES) == expected
    for archetype in S.ARCHETYPES.values():
        assert archetype.required_anchors
        assert archetype.allowed_categories
        assert archetype.palette in S.PALETTES


def test_palettes_are_curated_60_30_10():
    for palette in S.PALETTES.values():
        assert S.validate_palette(palette) == []
        assert palette.ratio == (60, 30, 10)
    assert len({p.id for p in S.PALETTES.values()}) == len(S.PALETTES)


@pytest.mark.parametrize("archetype", COMPLETE_RECIPES)
def test_sample_recipes_are_deterministic(archetype):
    seed = R.SAMPLE_SEEDS[archetype]
    first = S.build_room_recipe(archetype, seed)
    second = S.build_room_recipe(archetype, seed)
    assert first == second == R.sample_recipe(archetype)
    # A round-trip through the JSON shape must not lose anything.
    assert S.RoomStyleRecipe.from_dict(first.to_dict()) == first


def test_room_seed_is_stable_and_input_sensitive():
    assert S.room_seed("biz", "wing", "room", 1) == S.room_seed(
        "biz", "wing", "room", 1)
    assert S.room_seed("biz", "wing", "room", 1) != S.room_seed(
        "biz", "wing", "room", 2)
    assert S.room_seed("biz", "wing", "room", 1) != S.room_seed(
        "biz", "wing", "other", 1)


def test_seeded_streams_are_independent():
    streams = S.SeededStreams(12345)
    assert streams.value("shell", "floor") == streams.value("shell", "floor")
    values = {streams.value(stream, "x") for stream in S.STREAMS}
    assert len(values) == len(S.STREAMS)
    assert streams.sample("clutter", ("a", "b", "c"), 2) == streams.sample(
        "clutter", ("a", "b", "c"), 2)


# --- recipe refusals --------------------------------------------------------

@pytest.mark.parametrize(
    "bad",
    [
        {"archetype": "does-not-exist"},
        {"shadow": "soft"},
        {"seed": -1},
        {"style_version": 0},
        {"palette": "not-a-palette"},
        {"decor": ("decor", "decor")},
        {"decor": ("kitchen",)},
        {"floor": "Not A Slug"},
    ],
)
def test_recipe_validation_refuses_bad_values(bad):
    recipe = replace(R.sample_recipe("executive"), **bad)
    problems = S.validate_recipe(recipe)
    assert problems, bad


def test_asset_entry_validation_and_schema():
    for entry in CATALOG:
        schema = _schema("asset_catalog.schema.json")
        # The invalid fixture assets are intentionally invalid; validate only
        # the ones the extractor would actually emit.
        if entry.id in ("asset-door", "asset-32-desk"):
            continue
        jsonschema.validate([entry.to_dict()], schema)
    problems = S.validate_asset_catalog(CATALOG[:5])
    assert problems == []
    # A world-baseline asset may not be 32px.
    assert any("16px" in p or "baseline" in p
               for p in S.validate_asset_entry(
                   next(e for e in CATALOG if e.id == "asset-32-desk")))
    # A single-direction asset must be socket-bound and wall-oriented.
    door_problems = S.validate_asset_entry(
        next(e for e in CATALOG if e.id == "asset-door"))
    assert any("wall orientation" in p for p in door_problems)
    assert any("socket_bound" in p for p in door_problems)
    # Path traversal and absolute paths are refused.
    assert S.validate_asset_entry(replace(
        CATALOG[0], file="../evil.png"))
    assert S.validate_asset_entry(replace(CATALOG[0], file="/abs/x.png"))


# --- room plan: positive cases ---------------------------------------------

@pytest.mark.parametrize("archetype", COMPLETE_RECIPES)
def test_sample_plans_pass_walkability_capacity_and_theme_validation(archetype):
    recipe = R.sample_recipe(archetype)
    plan = R.sample_plan(recipe, CATALOG)
    assert (plan.cols, plan.rows) == (16, 12)
    assert plan.capacity == S.ARCHETYPES[archetype].desk_capacity
    assert S.validate_room_plan(plan, recipe, CATALOG) == []
    jsonschema.validate(plan.to_dict(), _schema("room_plan.schema.json"))


# --- room plan: refusals ---------------------------------------------------

def _plan() -> tuple[S.RoomPlan, S.RoomStyleRecipe]:
    recipe = R.sample_recipe("executive")
    return R.sample_plan(recipe, CATALOG), recipe


def test_stretched_plan_is_refused():
    plan, recipe = _plan()
    problems = S.validate_room_plan(replace(plan, cols=24), recipe, CATALOG)
    assert any(p.startswith("ROOM_STRETCHED") for p in problems)


def test_missing_or_blocked_entry_is_refused():
    plan, recipe = _plan()
    assert any(p.startswith("NO_ENTRY") for p in S.validate_room_plan(
        replace(plan, entry=None), recipe, CATALOG))
    blocked = replace(plan, entry=plan.placements[0].tile)
    assert any(p.startswith("NO_ENTRY") for p in S.validate_room_plan(
        blocked, recipe, CATALOG))


def test_collision_is_refused():
    plan, recipe = _plan()
    overlap = S.AssetPlacement(
        asset_id="asset-storage", tile=plan.placements[0].tile,
        footprint=(2, 1), category="storage", shadow_variant=recipe.shadow,
        blocks=True)
    problems = S.validate_room_plan(
        replace(plan, placements=plan.placements + (overlap,)), recipe, CATALOG)
    assert any(p.startswith("COLLISION") for p in problems)


def test_unreachable_desk_and_anchor_are_refused():
    plan, recipe = _plan()
    blocked_anchor = replace(
        plan, desk_anchors=(plan.placements[0].tile, *plan.desk_anchors[1:]))
    problems = S.validate_room_plan(blocked_anchor, recipe, CATALOG)
    assert any(p.startswith("UNREACHABLE_DESK") for p in problems)

    anchor_id = S.ARCHETYPES["executive"].required_anchors[0]
    unreachable_access = replace(
        plan,
        required_anchors=((anchor_id, plan.placements[0].tile),),
    )
    problems = S.validate_room_plan(unreachable_access, recipe, CATALOG)
    assert any(p.startswith("UNREACHABLE_ANCHOR") for p in problems)


def test_missing_required_anchor_is_refused():
    plan, recipe = _plan()
    anchor_id = S.ARCHETYPES["executive"].required_anchors[0]
    stripped = replace(
        plan,
        placements=tuple(p for p in plan.placements
                         if p.anchor_id != anchor_id),
        required_anchors=tuple(
            (a, t) for a, t in plan.required_anchors if a != anchor_id),
    )
    problems = S.validate_room_plan(stripped, recipe, CATALOG)
    assert any(p.startswith("MISSING_ANCHOR") for p in problems)


def test_wrong_theme_and_forbidden_tags_are_refused():
    plan, recipe = _plan()
    wrong_category = S.AssetPlacement(
        asset_id="asset-counter", tile=S.Tile(0, 10), footprint=(2, 1),
        category="counter", shadow_variant=recipe.shadow, blocks=False)
    problems = S.validate_room_plan(
        replace(plan, placements=plan.placements + (wrong_category,)),
        recipe, CATALOG)
    assert any(p.startswith("WRONG_THEME_PROP") for p in problems)

    control_recipe = R.sample_recipe("control_room")
    control_plan = R.sample_plan(control_recipe, CATALOG)
    kitchen = S.AssetPlacement(
        asset_id="asset-kitchen-decor", tile=S.Tile(0, 10), footprint=(1, 1),
        category="decor", shadow_variant=control_recipe.shadow, blocks=False)
    problems = S.validate_room_plan(
        replace(control_plan, placements=control_plan.placements + (kitchen,)),
        control_recipe, CATALOG)
    assert any(p.startswith("WRONG_THEME_PROP") for p in problems)


def test_mixed_shadow_and_world_size_are_refused():
    plan, recipe = _plan()
    mixed = replace(plan.placements[0], shadow_variant="black_shadow")
    problems = S.validate_room_plan(
        replace(plan, placements=(mixed, *plan.placements[1:])),
        recipe, CATALOG)
    assert any(p.startswith("MIXED_SHADOW") for p in problems)

    big = S.AssetPlacement(
        asset_id="asset-32-desk", tile=S.Tile(0, 10), footprint=(2, 1),
        category="desk", shadow_variant=recipe.shadow, blocks=False)
    problems = S.validate_room_plan(
        replace(plan, placements=plan.placements + (big,)), recipe, CATALOG)
    assert any(p.startswith("WRONG_TILE_SIZE") for p in problems)


def test_single_direction_furniture_must_be_socket_bound():
    plan, recipe = _plan()
    door = S.AssetPlacement(
        asset_id="asset-door", tile=S.Tile(15, 11), footprint=(1, 1),
        category="doors", orientation="any", single_direction=True,
        socket_bound=False, shadow_variant=recipe.shadow, blocks=False)
    problems = S.validate_room_plan(
        replace(plan, placements=plan.placements + (door,)), recipe, CATALOG)
    assert any(p.startswith("SINGLE_DIRECTION_VIOLATION") for p in problems)


def test_capacity_short_and_decor_overflow_are_refused():
    plan, recipe = _plan()
    short = replace(plan, desk_anchors=plan.desk_anchors[:2])
    assert any(p.startswith("CAPACITY_SHORT") for p in S.validate_room_plan(
        short, recipe, CATALOG))

    decor = tuple(
        S.AssetPlacement(
            asset_id="asset-decor", tile=S.Tile(0, 0), footprint=(1, 1),
            layer="decor", category="decor", shadow_variant=recipe.shadow,
            blocks=False)
        for _ in range(17)
    )
    problems = S.validate_room_plan(
        replace(plan, placements=plan.placements + decor), recipe, CATALOG)
    assert any(p.startswith("DECOR_OVERFLOW") for p in problems)
