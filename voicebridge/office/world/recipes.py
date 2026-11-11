"""The five sample archetype recipes, and a deterministic sample room plan.

These are *metadata* samples, not art. Each recipe pins the fields from master
plan §6/§18.3 and passes :func:`voicebridge.office.world.style.validate_recipe`.
The seeds are fixed literals so a test can prove that two builds, in two
processes, produce the same recipe.

``sample_plan`` builds a walkable 16x12 plan for a recipe from any catalog
(real extracted names or a test fixture). It exists so validation has a
positive case for every sample archetype, not only negative ones.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable, List, Mapping, Tuple

from .model import CELL_COLS, CELL_ROWS, CELL_DESK_CAPACITY, CELL_DESK_SLOTS, Tile
from .style import (
    ANCHOR_CATEGORIES,
    ARCHETYPES,
    AssetEntry,
    AssetPlacement,
    RoomPlan,
    RoomStyleRecipe,
    build_room_recipe,
    catalog_index,
    validate_recipe,
)

__all__ = [
    "SAMPLE_SEEDS",
    "SAMPLE_RECIPES",
    "sample_recipe",
    "sample_plan",
]

#: Fixed seeds for the five sample archetypes. They are literals, not
#: ``room_seed(...)`` calls, so the samples stay stable if the seed helper
#: changes; a business room uses :func:`room_seed` instead.
SAMPLE_SEEDS: Mapping[str, int] = {
    "executive": 184729,
    "support": 271003,
    "finance": 330917,
    "creative": 415601,
    "control_room": 508213,
}

SAMPLE_RECIPES: Mapping[str, RoomStyleRecipe] = {
    archetype: build_room_recipe(archetype, seed)
    for archetype, seed in SAMPLE_SEEDS.items()
}


def sample_recipe(archetype: str) -> RoomStyleRecipe:
    try:
        return SAMPLE_RECIPES[archetype]
    except KeyError as exc:  # pragma: no cover - exact error path
        raise KeyError(
            f"no sample recipe for {archetype!r}; choose one of "
            f"{sorted(SAMPLE_RECIPES)}") from exc


# --------------------------------------------------------------- sample plan
#: Where sample required anchors sit. Keeping them in the top band leaves the
#: aisle and the desk rows walkable; only the scenario's own props block.
_ANCHOR_TILES: Tuple[Tuple[int, int], ...] = (
    (1, 0), (6, 0), (11, 0), (1, 4), (11, 4), (6, 4),
)
#: Decorative props are non-blocking in the sample: they are posters, plants on
#: ledges and wall art, not aisle furniture.
_DECOR_TILES: Tuple[Tuple[int, int], ...] = (
    (14, 0), (14, 3), (14, 7), (0, 5), (0, 9), (15, 5),
)


def _first_by_category(
    catalog: Mapping[str, AssetEntry], category: str,
) -> AssetEntry | None:
    for asset in catalog.values():
        if asset.category == category:
            return asset
    return None


def _placement_from_asset(
    asset: AssetEntry,
    tile: Tile,
    *,
    shadow: str,
    anchor_id: str | None = None,
    blocks: bool = True,
) -> AssetPlacement:
    orientation = ("north" if "north" in asset.orientations
                   else (asset.orientations[0] if asset.orientations else "any"))
    return AssetPlacement(
        asset_id=asset.id,
        tile=tile,
        footprint=asset.footprint,
        layer=asset.layer,
        surface=asset.surface,
        orientation=orientation,
        shadow_variant=shadow,
        category=asset.category,
        anchor_id=anchor_id,
        blocks=blocks,
        socket_bound=asset.socket_bound,
        single_direction=asset.single_direction,
    )


def sample_plan(
    recipe: RoomStyleRecipe,
    catalog: Iterable[AssetEntry] | Mapping[str, AssetEntry],
) -> RoomPlan:
    """Build a deterministic, walkable 16x12 plan for ``recipe``.

    The plan is intentionally simple: six desk slots, the archetype's required
    anchors along the top band, and a few non-blocking decorations. It is a
    *validation fixture*, not a production floor plan.
    """

    problems = validate_recipe(recipe)
    if problems:
        raise ValueError("; ".join(problems))
    archetype = ARCHETYPES[recipe.archetype]
    index = catalog_index(catalog)
    placements: List[AssetPlacement] = []
    desk_anchors: List[Tile] = []
    required: List[Tuple[str, Tile]] = []

    desk_asset = _first_by_category(index, "desk")
    if desk_asset is not None:
        for slot in range(min(CELL_DESK_CAPACITY, archetype.desk_capacity)):
            x, y = CELL_DESK_SLOTS[slot]
            desk_tile = Tile(x, y)
            placements.append(_placement_from_asset(
                desk_asset, desk_tile, shadow=recipe.shadow))
            # Stand in front of the desk (one tile below its footprint).
            desk_anchors.append(Tile(x, y + desk_asset.footprint[1]))

    for anchor_id, (x, y) in zip(archetype.required_anchors, _ANCHOR_TILES):
        category = ANCHOR_CATEGORIES.get(anchor_id, "decor")
        asset = _first_by_category(index, category)
        if asset is None:
            continue
        w, h = asset.footprint
        at = Tile(min(x, CELL_COLS - w), min(y, CELL_ROWS - h))
        access = Tile(at.x, min(at.y + h, CELL_ROWS - 1))
        placement = _placement_from_asset(
            asset, at, shadow=recipe.shadow, anchor_id=anchor_id)
        placement = replace(placement, interaction_tile=access)
        placements.append(placement)
        required.append((anchor_id, access))

    # Decor is a *secondary* stream and never blocks circulation in the sample.
    for i, category in enumerate(recipe.decor):
        if i >= len(_DECOR_TILES):
            break
        asset = _first_by_category(index, category)
        if asset is None:
            continue
        x, y = _DECOR_TILES[i]
        w, h = asset.footprint
        at = Tile(min(x, CELL_COLS - w), min(y, CELL_ROWS - h))
        placements.append(_placement_from_asset(
            asset, at, shadow=recipe.shadow, blocks=False))

    return RoomPlan(
        cols=CELL_COLS,
        rows=CELL_ROWS,
        entry=Tile(CELL_COLS // 2, CELL_ROWS - 1),
        placements=tuple(placements),
        desk_anchors=tuple(desk_anchors),
        required_anchors=tuple(required),
    )
