"""Room style system: assets, palettes, archetypes, recipes and validation.

The contract is master plan §6 and §18.3. It is deliberately data-only: this
module knows how to describe a room and how to refuse a bad one, but it never
loads, copies, or ships LimeZu art. The paid pack is read for *names* by
:mod:`voicebridge.office.world.catalog`; everything here is metadata.

Rules enforced here, each as a named problem code so a test can pin the refusal:

* **16x16 is the world baseline.** A world asset (floor/wall/furniture/decor)
  may not be 32 or 48; only portrait/UI close-ups may be larger.
* **One shadow policy per scene.** A recipe picks ``shadowless`` or
  ``black_shadow`` and every placed asset must agree.
* **Single-direction furniture is socket-bound.** Wall furniture, sockets,
  doors and windows must declare a wall orientation and ``socket_bound``.
* **Curated palettes are 60/30/10.** The dominant/secondary/accent ratio must
  sum to 100 and every palette entry is a validated colour.
* **No random sprite soup.** A recipe is an archetype + palette + seeded stream;
  variation is limited to secondary furniture, rug, plants, posters, accent and
  desk template. Required anchors and circulation are never varied.

Room-plan validation (:func:`validate_room_plan`) is the walkability / capacity /
collision / interaction / wrong-theme gate the plan asks for. It runs on a
16x12 :class:`RoomPlan` — the same primitive as the world model's cell — so a
style cannot quietly change the room geometry.

Pure Python. No Pillow, no art I/O, no network.
"""
from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .model import CELL_COLS, CELL_ROWS, Tile

__all__ = [
    "RoomStyleError",
    "WORLD_TILE",
    "PORTRAIT_TILES",
    "ASSET_SIZES",
    "SURFACES",
    "LAYERS",
    "SHADOW_VARIANTS",
    "ORIENTATIONS",
    "WALL_ORIENTATIONS",
    "SINGLE_DIRECTION_CATEGORIES",
    "STREAMS",
    "AssetEntry",
    "AssetPlacement",
    "RoomPlan",
    "Palette",
    "RoomArchetype",
    "RoomStyleRecipe",
    "ARCHETYPES",
    "PALETTES",
    "ANCHOR_CATEGORIES",
    "validate_asset_entry",
    "validate_asset_catalog",
    "validate_palette",
    "validate_recipe",
    "validate_room_plan",
    "assert_valid_room_plan",
    "room_seed",
    "SeededStreams",
    "build_room_recipe",
    "catalog_index",
]

# ----------------------------------------------------------------- contract
#: The world baseline tile size. 16 or nothing: 32/48 are portrait/close-up.
WORLD_TILE = 16
PORTRAIT_TILES: Tuple[int, ...] = (32, 48)
ASSET_SIZES: Tuple[int, ...] = (16, 32, 48)

SURFACES: Tuple[str, ...] = (
    "floor", "wall", "rug", "furniture", "decor", "lighting", "portrait", "ui")
LAYERS: Tuple[str, ...] = (
    "floor", "rug", "wall", "furniture", "decor", "lighting", "overlay", "portrait")
SHADOW_VARIANTS: Tuple[str, ...] = ("shadowless", "black_shadow")
ORIENTATIONS: Tuple[str, ...] = ("any", "north", "east", "south", "west")
WALL_ORIENTATIONS: Tuple[str, ...] = ("north", "east", "south", "west")
#: Categories that physically attach to a wall/socket and may not be rotated
#: arbitrarily. A placement of one of these must name a wall orientation and
#: declare ``socket_bound``.
SINGLE_DIRECTION_CATEGORIES: FrozenSet[str] = frozenset({
    "wall_furniture", "wall_decor", "sockets", "doors", "windows",
})

STREAMS: Tuple[str, ...] = (
    "shell", "furniture", "palette", "clutter", "decals", "lighting")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$")
#: File names in the purchased pack contain spaces and parentheses; the safety
#: rule is about traversal and separators, not a narrow ASCII alphabet.
_FILE_RE = re.compile(r"^[^\\:\x00-\x1f]+$")
_SAFE_SUFFIXES = (".png", ".webp", ".json")


class RoomStyleError(Exception):
    """A recipe or room plan is invalid. ``problems`` carries every code."""

    def __init__(self, problems: Iterable[str]):
        self.problems: List[str] = list(problems)
        super().__init__("; ".join(self.problems) or "invalid room style")


# ------------------------------------------------------------------- assets
@dataclass(frozen=True)
class AssetEntry:
    """One catalog row. Names/metadata only; ``file`` is never read here."""

    id: str
    file: str
    theme: str
    category: str
    size: int
    footprint: Tuple[int, int] = (1, 1)
    z: int = 0
    layer: str = "furniture"
    surface: str = "furniture"
    orientations: Tuple[str, ...] = ("any",)
    animation: Optional[str] = None
    shadow_variant: str = "shadowless"
    tags: Tuple[str, ...] = ()
    compatibility: Tuple[str, ...] = ()
    single_direction: bool = False
    socket_bound: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "file": self.file,
            "theme": self.theme,
            "category": self.category,
            "size": int(self.size),
            "footprint": [int(self.footprint[0]), int(self.footprint[1])],
            "z": int(self.z),
            "layer": self.layer,
            "surface": self.surface,
            "orientations": list(self.orientations),
            "animation": self.animation,
            "shadow_variant": self.shadow_variant,
            "tags": list(self.tags),
            "compatibility": list(self.compatibility),
            "single_direction": bool(self.single_direction),
            "socket_bound": bool(self.socket_bound),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AssetEntry":
        return cls(
            id=str(data["id"]),
            file=str(data["file"]),
            theme=str(data["theme"]),
            category=str(data["category"]),
            size=int(data["size"]),
            footprint=tuple(int(x) for x in data.get("footprint", (1, 1))),  # type: ignore[arg-type]
            z=int(data.get("z", 0)),
            layer=str(data.get("layer", "furniture")),
            surface=str(data.get("surface", "furniture")),
            orientations=tuple(str(x) for x in data.get("orientations", ("any",))),
            animation=(str(data["animation"]) if data.get("animation") else None),
            shadow_variant=str(data.get("shadow_variant", "shadowless")),
            tags=tuple(str(x) for x in data.get("tags", ())),
            compatibility=tuple(str(x) for x in data.get("compatibility", ())),
            single_direction=bool(data.get("single_direction", False)),
            socket_bound=bool(data.get("socket_bound", False)),
        )


def _safe_file(path: str) -> bool:
    if not path or path.startswith("/") or "\\" in path or ":" in path:
        return False
    if not _FILE_RE.match(path):
        return False
    return not any(part in ("", ".", "..") for part in path.split("/"))


def validate_asset_entry(entry: AssetEntry) -> List[str]:
    """Every reason one catalog row is invalid, or an empty list."""

    problems: List[str] = []
    where = f"asset {entry.id!r}"
    if not _ID_RE.match(entry.id or ""):
        problems.append(f"{where}: invalid id")
    if not entry.theme or not _ID_RE.match(entry.theme):
        problems.append(f"{where}: invalid theme")
    if not entry.category:
        problems.append(f"{where}: empty category")
    if not _safe_file(entry.file):
        problems.append(f"{where}: unsafe file path {entry.file!r}")
    elif not entry.file.lower().endswith(_SAFE_SUFFIXES):
        problems.append(f"{where}: unsupported file suffix")
    if entry.size not in ASSET_SIZES:
        problems.append(f"{where}: size {entry.size} not in {ASSET_SIZES}")
    elif entry.size != WORLD_TILE and entry.surface not in ("portrait", "ui"):
        problems.append(
            f"{where}: {entry.size}px asset on {entry.surface!r}; the world "
            f"baseline is {WORLD_TILE}px (32/48 are portraits/UI only)")
    if len(entry.footprint) != 2 or any(
            not isinstance(v, int) or v <= 0 or v > 8 for v in entry.footprint):
        problems.append(f"{where}: footprint must be two ints in 1..8")
    if entry.layer not in LAYERS:
        problems.append(f"{where}: layer {entry.layer!r} not in {LAYERS}")
    if entry.surface not in SURFACES:
        problems.append(f"{where}: surface {entry.surface!r} not in {SURFACES}")
    if not entry.orientations or any(o not in ORIENTATIONS for o in entry.orientations):
        problems.append(f"{where}: orientations must be a subset of {ORIENTATIONS}")
    if entry.shadow_variant not in SHADOW_VARIANTS:
        problems.append(f"{where}: shadow_variant {entry.shadow_variant!r}")
    single = entry.single_direction or entry.category in SINGLE_DIRECTION_CATEGORIES
    if single:
        if "any" in entry.orientations or not set(entry.orientations) <= set(WALL_ORIENTATIONS):
            problems.append(f"{where}: single-direction asset needs a wall orientation")
        if not entry.socket_bound:
            problems.append(f"{where}: single-direction asset must be socket_bound")
    for tag in (*entry.tags, *entry.compatibility):
        if not _ID_RE.match(tag):
            problems.append(f"{where}: invalid tag {tag!r}")
    return problems


def validate_asset_catalog(entries: Sequence[AssetEntry]) -> List[str]:
    problems: List[str] = []
    seen: Set[str] = set()
    for entry in entries:
        problems.extend(validate_asset_entry(entry))
        if entry.id in seen:
            problems.append(f"asset {entry.id!r}: duplicate id")
        seen.add(entry.id)
    return problems


# ----------------------------------------------------------------- palettes
@dataclass(frozen=True)
class Palette:
    """A curated 60/30/10 palette. One dominant material, one accent."""

    id: str
    dominant: str
    secondary: str
    accent: str
    ratio: Tuple[int, int, int] = (60, 30, 10)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "dominant": self.dominant,
                "secondary": self.secondary, "accent": self.accent,
                "ratio": list(self.ratio)}


def validate_palette(palette: Palette) -> List[str]:
    problems: List[str] = []
    where = f"palette {palette.id!r}"
    for field_name in ("dominant", "secondary", "accent"):
        value = getattr(palette, field_name)
        if not isinstance(value, str) or not _HEX_RE.match(value):
            problems.append(f"{where}: {field_name} is not a hex colour")
    if len(palette.ratio) != 3 or any(not isinstance(v, int) or v <= 0 for v in palette.ratio):
        problems.append(f"{where}: ratio must be three positive ints")
    elif sum(palette.ratio) != 100:
        problems.append(f"{where}: ratio {palette.ratio} does not sum to 100")
    return problems


PALETTES: Mapping[str, Palette] = {
    p.id: p for p in (
        Palette("cool_teal_neutral", "#1f2933", "#3e4c59", "#2dd4bf"),
        Palette("executive_warm", "#2b2118", "#5c4332", "#c8975a"),
        Palette("finance_navy", "#0f172a", "#1e3a5f", "#d4af37"),
        Palette("creative_coral", "#201a2b", "#4a3b5c", "#ff6b6b"),
        Palette("control_room_amber", "#0b0f14", "#1c2733", "#ffb020"),
        Palette("open_office_sand", "#2a2620", "#57503f", "#7cc7a7"),
    )
}


# --------------------------------------------------------------- archetypes
@dataclass(frozen=True)
class RoomArchetype:
    """The functional definition of a room type (before a palette is chosen)."""

    id: str
    label: str
    required_anchors: Tuple[str, ...]
    allowed_categories: Tuple[str, ...]
    forbidden_tags: Tuple[str, ...] = ()
    palette: str = "cool_teal_neutral"
    desk_capacity: int = 6
    shadow: str = "shadowless"
    character_mode: str = "multi"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "required_anchors": list(self.required_anchors),
            "allowed_categories": list(self.allowed_categories),
            "forbidden_tags": list(self.forbidden_tags),
            "palette": self.palette,
            "desk_capacity": int(self.desk_capacity),
            "shadow": self.shadow,
            "character_mode": self.character_mode,
        }


def _a(id: str, label: str, anchors: Tuple[str, ...],
       allowed: Tuple[str, ...], *, forbidden: Tuple[str, ...] = (),
       palette: str = "cool_teal_neutral", capacity: int = 6,
       shadow: str = "shadowless", character_mode: str = "multi") -> RoomArchetype:
    return RoomArchetype(id, label, anchors, allowed, forbidden, palette,
                         capacity, shadow, character_mode)


_CORE = ("desk", "chair", "storage", "decor", "plant", "lighting", "rug",
         "floor", "wall", "sign")

ARCHETYPES: Mapping[str, RoomArchetype] = {
    a.id: a for a in (
        _a("executive", "Executive boardroom",
           ("board_table", "storage"),
           ("desk", "table", "chair", "storage", "decor", "plant", "lighting",
            "rug", "floor", "wall", "sign"),
           forbidden=("industrial", "outdoor"), palette="executive_warm"),
        _a("open_office", "Open office", ("desk_bank", "storage"),
           _CORE, forbidden=("kitchen",), palette="open_office_sand"),
        _a("reception", "Reception", ("reception_desk", "queue_board"),
           _CORE, forbidden=("industrial",), palette="cool_teal_neutral"),
        _a("support", "Support hub", ("reception_desk", "queue_board"),
           _CORE, forbidden=("executive_marble",), palette="cool_teal_neutral"),
        _a("sales", "Sales floor", ("desk_bank", "display_wall"),
           _CORE + ("display",), forbidden=("kitchen",), palette="cool_teal_neutral"),
        _a("finance", "Finance / accounting", ("desk_bank", "safe"),
           _CORE, forbidden=("outdoor",), palette="finance_navy"),
        _a("creative", "Marketing / content studio",
           ("studio_desk", "display_wall"),
           _CORE + ("display",), forbidden=("industrial_office",),
           palette="creative_coral"),
        _a("control_room", "Engineering / control room",
           ("console", "wall_display"),
           ("console", "display", "desk", "chair", "storage", "decor",
            "lighting", "floor", "wall", "sign"),
           forbidden=("kitchen", "outdoor"), palette="control_room_amber"),
        _a("research", "Research / library",
           ("reading_table", "storage"),
           _CORE, forbidden=("industrial",), palette="cool_teal_neutral"),
        _a("meeting", "Meeting room", ("meeting_table", "display_wall"),
           ("table", "chair", "storage", "decor", "plant", "lighting", "rug",
            "floor", "wall", "display", "sign"),
           forbidden=("kitchen",), palette="cool_teal_neutral"),
        _a("kitchen", "Kitchen / break room", ("counter", "storage"),
           ("counter", "table", "chair", "storage", "decor", "plant",
            "lighting", "floor", "wall", "sign"),
           palette="open_office_sand"),
        _a("wellness", "Wellness / quiet room", ("rest_seat", "plant"),
           ("chair", "table", "decor", "plant", "lighting", "rug", "floor",
            "wall", "sign"),
           palette="cool_teal_neutral"),
        _a("showcase", "Showcase / museum", ("display_wall", "plinth"),
           ("display", "decor", "plant", "lighting", "rug", "floor", "wall",
            "sign", "storage"),
           forbidden=("industrial_office",), palette="executive_warm"),
        _a("server_ops", "Server / ops", ("rack", "console"),
           ("console", "display", "rack", "desk", "chair", "storage", "decor",
            "lighting", "floor", "wall", "sign"),
           forbidden=("kitchen", "outdoor"), palette="control_room_amber",
           shadow="black_shadow"),
        _a("legal", "Legal / compliance", ("desk_bank", "storage"),
           _CORE, forbidden=("outdoor",), palette="finance_navy"),
        _a("training", "Training / classroom", ("desk_bank", "display_wall"),
           _CORE + ("display",), forbidden=("kitchen",),
           palette="cool_teal_neutral"),
        _a("fulfillment", "Fulfillment", ("packing_table", "storage"),
           ("table", "storage", "desk", "chair", "decor", "lighting", "floor",
            "wall", "sign", "rack"),
           forbidden=("executive_marble",), palette="open_office_sand",
           shadow="black_shadow"),
        _a("media", "Media / TV studio", ("console", "display_wall"),
           ("console", "display", "desk", "chair", "decor", "lighting", "rug",
            "floor", "wall", "sign", "storage"),
           forbidden=("kitchen",), palette="creative_coral",
           shadow="black_shadow"),
    )
}

#: What kind of asset satisfies each required anchor. A plan's anchor is only
#: satisfied when the anchor id is present AND the prop's category matches.
ANCHOR_CATEGORIES: Mapping[str, str] = {
    "board_table": "table",
    "storage": "storage",
    "desk_bank": "desk",
    "reception_desk": "desk",
    "queue_board": "sign",
    "safe": "storage",
    "studio_desk": "desk",
    "display_wall": "display",
    "console": "console",
    "reading_table": "table",
    "meeting_table": "table",
    "counter": "counter",
    "rest_seat": "chair",
    "plant": "plant",
    "plinth": "display",
    "rack": "rack",
    "packing_table": "table",
}


# ------------------------------------------------------------------ recipes
@dataclass(frozen=True)
class RoomStyleRecipe:
    """The shareable room recipe from master plan §6/§18.3."""

    archetype: str
    palette: str
    floor: str
    wall: str
    rug: str
    furniture_set: str
    lighting: str
    shadow: str
    decor: Tuple[str, ...] = ()
    seed: int = 0
    style_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "archetype": self.archetype,
            "palette": self.palette,
            "floor": self.floor,
            "wall": self.wall,
            "rug": self.rug,
            "furniture_set": self.furniture_set,
            "lighting": self.lighting,
            "shadow": self.shadow,
            "decor": list(self.decor),
            "seed": int(self.seed),
            "style_version": int(self.style_version),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RoomStyleRecipe":
        return cls(
            archetype=str(data["archetype"]),
            palette=str(data["palette"]),
            floor=str(data["floor"]),
            wall=str(data["wall"]),
            rug=str(data["rug"]),
            furniture_set=str(data["furniture_set"]),
            lighting=str(data["lighting"]),
            shadow=str(data["shadow"]),
            decor=tuple(str(d) for d in data.get("decor", ())),
            seed=int(data.get("seed", 0)),
            style_version=int(data.get("style_version", 1)),
        )


def validate_recipe(recipe: RoomStyleRecipe) -> List[str]:
    """Structural recipe validation. No pack is needed."""

    problems: List[str] = []
    where = f"recipe {recipe.archetype!r}"
    archetype = ARCHETYPES.get(recipe.archetype)
    if archetype is None:
        problems.append(f"{where}: unknown archetype")
    if recipe.palette not in PALETTES:
        problems.append(f"{where}: unknown palette {recipe.palette!r}")
    if recipe.shadow not in SHADOW_VARIANTS:
        problems.append(f"{where}: shadow {recipe.shadow!r} not in {SHADOW_VARIANTS}")
    for name in ("floor", "wall", "rug", "furniture_set", "lighting"):
        value = getattr(recipe, name)
        if not value or not _ID_RE.match(value):
            problems.append(f"{where}: {name} must be a slug, got {value!r}")
    if not isinstance(recipe.seed, int) or recipe.seed < 0:
        problems.append(f"{where}: seed must be a non-negative int")
    if not isinstance(recipe.style_version, int) or recipe.style_version < 1:
        problems.append(f"{where}: style_version must be >= 1")
    if len(set(recipe.decor)) != len(recipe.decor):
        problems.append(f"{where}: decor entries must be unique")
    for entry in recipe.decor:
        if not _ID_RE.match(entry):
            problems.append(f"{where}: invalid decor entry {entry!r}")
    if archetype is not None and recipe.decor:
        allowed = set(archetype.allowed_categories)
        for entry in recipe.decor:
            if entry not in allowed:
                problems.append(
                    f"{where}: decor {entry!r} is not an allowed category for "
                    f"{archetype.id!r}")
    return problems


def room_seed(business: str, wing: str, room: str,
              style_version: int = 1) -> int:
    """Deterministic 64-bit seed: ``hash(business, wing, room, style_version)``.

    ``blake2b`` is used rather than :func:`hash` because Python randomises
    ``hash()`` per process; a seed must survive a restart.
    """

    key = f"{business}|{wing}|{room}|{int(style_version)}"
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


@dataclass(frozen=True)
class SeededStreams:
    """Independent deterministic streams derived from one room seed."""

    seed: int

    def value(self, stream: str, *parts: Any, modulus: Optional[int] = None) -> int:
        if stream not in STREAMS:
            raise ValueError(f"unknown stream {stream!r}")
        payload = "|".join([str(int(self.seed)), stream, *(str(p) for p in parts)])
        digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).digest()
        number = int.from_bytes(digest, "big")
        return number % modulus if modulus else number

    def choice(self, stream: str, options: Sequence[str], *parts: Any) -> str:
        if not options:
            raise ValueError("cannot choose from an empty sequence")
        return options[self.value(stream, *parts, modulus=len(options))]

    def sample(self, stream: str, options: Sequence[str], count: int,
               *parts: Any) -> Tuple[str, ...]:
        pool = list(dict.fromkeys(options))
        picked: List[str] = []
        index = 0
        while pool and len(picked) < count:
            choice = self.choice(stream, pool, *parts, index)
            picked.append(choice)
            pool.remove(choice)
            index += 1
        return tuple(picked)

    def rng(self, stream: str) -> random.Random:
        return random.Random(self.value(stream))


#: Per-archetype option pools. Only secondary choices live here; required
#: anchors, doors, circulation, capacity and collision are never varied.
_STYLE_OPTIONS: Mapping[str, Mapping[str, Tuple[str, ...]]] = {
    "executive": {
        "floor": ("floor_wood_dark", "floor_marble_warm", "floor_carpet_deep"),
        "wall": ("wall_panel_walnut", "wall_panel_cream", "wall_plaster_warm"),
        "rug": ("rug_persian_accent", "rug_plain_border"),
        "furniture_set": ("executive_boardroom", "executive_lounge"),
        "lighting": ("light_warm_chandelier", "light_soft_pendants"),
        "decor": ("decor", "plant", "sign"),
    },
    "support": {
        "floor": ("floor_carpet_dark", "floor_tile_grey", "floor_wood_light"),
        "wall": ("wall_panel_light", "wall_plaster_cool", "wall_glass_partition"),
        "rug": ("rug_teal_accent", "rug_plain_grey"),
        "furniture_set": ("support_desks_modern", "support_desks_compact"),
        "lighting": ("light_cool_overhead", "light_soft_panels"),
        "decor": ("decor", "plant", "sign"),
    },
    "finance": {
        "floor": ("floor_carpet_navy", "floor_wood_dark", "floor_tile_white"),
        "wall": ("wall_panel_navy", "wall_plaster_cool", "wall_glass_partition"),
        "rug": ("rug_plain_border", "rug_navy_accent"),
        "furniture_set": ("finance_desks_classic", "finance_desks_compact"),
        "lighting": ("light_cool_overhead", "light_focused_task"),
        "decor": ("decor", "plant", "sign"),
    },
    "creative": {
        "floor": ("floor_wood_light", "floor_concrete_polished", "floor_tile_check"),
        "wall": ("wall_brick_painted", "wall_plaster_warm", "wall_glass_partition"),
        "rug": ("rug_coral_accent", "rug_plain_grey"),
        "furniture_set": ("creative_studio_desks", "creative_lounge_desks"),
        "lighting": ("light_soft_pendants", "light_warm_strings"),
        "decor": ("decor", "plant", "sign", "display"),
    },
    "control_room": {
        "floor": ("floor_concrete_polished", "floor_tile_grey", "floor_metal_plate"),
        "wall": ("wall_tech_panel", "wall_plaster_dark", "wall_glass_partition"),
        "rug": ("rug_plain_grey", "rug_dark_border"),
        "furniture_set": ("control_consoles_modern", "control_consoles_compact"),
        "lighting": ("light_amber_console", "light_cool_overhead"),
        "decor": ("decor", "sign", "display"),
    },
}
_DEFAULT_STYLE_OPTIONS: Mapping[str, Tuple[str, ...]] = {
    "floor": ("floor_wood_light", "floor_carpet_dark", "floor_tile_grey"),
    "wall": ("wall_panel_light", "wall_plaster_cool", "wall_glass_partition"),
    "rug": ("rug_plain_grey", "rug_plain_border"),
    "furniture_set": ("open_office_desks_modern", "open_office_desks_compact"),
    "lighting": ("light_cool_overhead", "light_soft_panels"),
    "decor": ("decor", "plant", "sign"),
}


def build_room_recipe(
    archetype: str,
    seed: int,
    *,
    palette: Optional[str] = None,
    style_version: int = 1,
    decor_count: int = 3,
) -> RoomStyleRecipe:
    """Build a deterministic recipe. Same inputs -> identical output, forever."""

    if archetype not in ARCHETYPES:
        raise ValueError(f"unknown archetype {archetype!r}")
    if not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a non-negative int")
    meta = ARCHETYPES[archetype]
    options = _STYLE_OPTIONS.get(archetype, _DEFAULT_STYLE_OPTIONS)
    streams = SeededStreams(seed)
    chosen_palette = palette or meta.palette
    recipe = RoomStyleRecipe(
        archetype=archetype,
        palette=chosen_palette,
        floor=streams.choice("shell", options["floor"], "floor"),
        wall=streams.choice("shell", options["wall"], "wall"),
        rug=streams.choice("furniture", options["rug"], "rug"),
        furniture_set=streams.choice(
            "furniture", options["furniture_set"], "set"),
        lighting=streams.choice("lighting", options["lighting"], "light"),
        shadow=meta.shadow,
        decor=streams.sample(
            "clutter", options["decor"], min(decor_count, len(options["decor"])),
            "decor"),
        seed=seed,
        style_version=style_version,
    )
    problems = validate_recipe(recipe)
    if problems:
        raise RoomStyleError(problems)
    return recipe


# ------------------------------------------------------------- room plans
@dataclass(frozen=True)
class AssetPlacement:
    """One asset placed in a 16x12 room plan."""

    asset_id: str
    tile: Tile
    footprint: Tuple[int, int] = (1, 1)
    layer: str = "furniture"
    surface: str = "furniture"
    orientation: str = "any"
    shadow_variant: Optional[str] = None
    tags: Tuple[str, ...] = ()
    category: Optional[str] = None
    anchor_id: Optional[str] = None
    interaction_tile: Optional[Tile] = None
    socket_bound: bool = False
    single_direction: bool = False
    blocks: bool = True

    def effective_shadow(self, asset: Optional[AssetEntry]) -> str:
        if self.shadow_variant:
            return self.shadow_variant
        if asset is not None:
            return asset.shadow_variant
        return "shadowless"

    def effective_category(self, asset: Optional[AssetEntry]) -> str:
        return self.category or (asset.category if asset else "")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "tile": self.tile.to_dict(),
            "footprint": [int(self.footprint[0]), int(self.footprint[1])],
            "layer": self.layer,
            "surface": self.surface,
            "orientation": self.orientation,
            "shadow_variant": self.shadow_variant,
            "tags": list(self.tags),
            "category": self.category,
            "anchor_id": self.anchor_id,
            "interaction_tile": (self.interaction_tile.to_dict()
                                 if self.interaction_tile else None),
            "socket_bound": bool(self.socket_bound),
            "single_direction": bool(self.single_direction),
            "blocks": bool(self.blocks),
        }


@dataclass(frozen=True)
class RoomPlan:
    """A 16x12 style plan: placements, desks, anchors and an entry tile."""

    cols: int = CELL_COLS
    rows: int = CELL_ROWS
    entry: Optional[Tile] = None
    placements: Tuple[AssetPlacement, ...] = ()
    desk_anchors: Tuple[Tile, ...] = ()
    #: ``(anchor_id, access_tile)`` pairs. The access tile must be walkable.
    required_anchors: Tuple[Tuple[str, Tile], ...] = ()

    @property
    def capacity(self) -> int:
        return len(self.desk_anchors)

    def blocked_tiles(self) -> FrozenSet[Tile]:
        blocked: Set[Tile] = set()
        for placement in self.placements:
            if not placement.blocks:
                continue
            w, h = placement.footprint
            for dx in range(w):
                for dy in range(h):
                    blocked.add(Tile(placement.tile.x + dx, placement.tile.y + dy))
        return frozenset(blocked)

    def in_bounds(self, tile: Tile) -> bool:
        return 0 <= tile.x < self.cols and 0 <= tile.y < self.rows

    def walkable_tiles(self) -> FrozenSet[Tile]:
        blocked = self.blocked_tiles()
        return frozenset(
            Tile(x, y)
            for x in range(self.cols)
            for y in range(self.rows)
            if Tile(x, y) not in blocked
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cols": int(self.cols),
            "rows": int(self.rows),
            "entry": self.entry.to_dict() if self.entry else None,
            "capacity": self.capacity,
            "placements": [p.to_dict() for p in self.placements],
            "desk_anchors": [t.to_dict() for t in self.desk_anchors],
            "required_anchors": [
                {"anchor_id": anchor, "access": tile.to_dict()}
                for anchor, tile in self.required_anchors
            ],
        }


def catalog_index(catalog: Iterable[AssetEntry] | Mapping[str, AssetEntry]
                  ) -> Dict[str, AssetEntry]:
    if isinstance(catalog, Mapping):
        return dict(catalog)
    return {entry.id: entry for entry in catalog}


def _reachable(plan: RoomPlan, start: Tile) -> Set[Tile]:
    walkable = plan.walkable_tiles()
    if start not in walkable:
        return set()
    seen = {start}
    queue = [start]
    while queue:
        tile = queue.pop(0)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nxt = Tile(tile.x + dx, tile.y + dy)
            if nxt in walkable and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _rect(placement: AssetPlacement) -> Tuple[int, int, int, int]:
    w, h = placement.footprint
    return (placement.tile.x, placement.tile.y, int(w), int(h))


def _overlap(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def validate_room_plan(
    plan: RoomPlan,
    recipe: RoomStyleRecipe,
    catalog: Iterable[AssetEntry] | Mapping[str, AssetEntry],
) -> List[str]:
    """Validate walkability, capacity, collision, interaction and theme fit.

    Every failure is a prefixed problem string; the caller can show them all at
    once rather than one at a time.
    """

    problems: List[str] = list(validate_recipe(recipe))
    index = catalog_index(catalog)
    archetype = ARCHETYPES.get(recipe.archetype)

    if (plan.cols, plan.rows) != (CELL_COLS, CELL_ROWS):
        problems.append(
            f"ROOM_STRETCHED: {plan.cols}x{plan.rows}; cells are fixed "
            f"{CELL_COLS}x{CELL_ROWS}")
    if archetype is None:
        return problems
    if plan.entry is None:
        problems.append("NO_ENTRY: plan has no entry tile")
        return problems
    if not plan.in_bounds(plan.entry):
        problems.append("NO_ENTRY: entry is outside the cell")
        return problems
    if plan.entry in plan.blocked_tiles():
        problems.append("NO_ENTRY: entry is blocked by a placement")
        return problems

    # --- collision and bounds
    blocking_rects: List[Tuple[int, int, int, int]] = []
    for placement in plan.placements:
        w, h = placement.footprint
        if w <= 0 or h <= 0:
            problems.append(f"BAD_FOOTPRINT: {placement.asset_id!r}")
            continue
        if (placement.tile.x < 0 or placement.tile.y < 0
                or placement.tile.x + w > plan.cols
                or placement.tile.y + h > plan.rows):
            problems.append(
                f"OUT_OF_BOUNDS: {placement.asset_id!r} at "
                f"({placement.tile.x},{placement.tile.y}) {w}x{h}")
        if placement.blocks:
            rect = _rect(placement)
            if any(_overlap(rect, other) for other in blocking_rects):
                problems.append(
                    f"COLLISION: {placement.asset_id!r} overlaps another "
                    "blocking placement")
            blocking_rects.append(rect)

    # --- per-placement theme, size, shadow and single-direction rules
    for placement in plan.placements:
        asset = index.get(placement.asset_id)
        if asset is None:
            problems.append(f"UNKNOWN_ASSET: {placement.asset_id!r} not in catalog")
            continue
        category = placement.effective_category(asset)
        if category not in archetype.allowed_categories:
            problems.append(
                f"WRONG_THEME_PROP: {placement.asset_id!r} category "
                f"{category!r} not allowed in {archetype.id!r}")
        bad_tags = set(placement.tags or asset.tags) & set(archetype.forbidden_tags)
        if bad_tags:
            problems.append(
                f"WRONG_THEME_PROP: {placement.asset_id!r} carries forbidden "
                f"tags {sorted(bad_tags)}")
        if asset.size != WORLD_TILE and asset.surface not in ("portrait", "ui"):
            problems.append(
                f"WRONG_TILE_SIZE: {placement.asset_id!r} is {asset.size}px "
                f"on {asset.surface!r}")
        if placement.effective_shadow(asset) != recipe.shadow:
            problems.append(
                f"MIXED_SHADOW: {placement.asset_id!r} uses "
                f"{placement.effective_shadow(asset)!r}, recipe uses "
                f"{recipe.shadow!r}")
        single = (placement.single_direction or asset.single_direction
                  or category in SINGLE_DIRECTION_CATEGORIES)
        if single:
            if placement.orientation not in WALL_ORIENTATIONS:
                problems.append(
                    f"SINGLE_DIRECTION_VIOLATION: {placement.asset_id!r} "
                    f"orientation {placement.orientation!r}")
            if not (placement.socket_bound or asset.socket_bound):
                problems.append(
                    f"SINGLE_DIRECTION_VIOLATION: {placement.asset_id!r} "
                    "is not socket_bound")

    # --- capacity
    if plan.capacity < archetype.desk_capacity:
        problems.append(
            f"CAPACITY_SHORT: {plan.capacity} desks for {archetype.id!r}, "
            f"needs {archetype.desk_capacity}")

    # --- walkability from the entry
    reachable = _reachable(plan, plan.entry)
    for anchor in plan.desk_anchors:
        if not plan.in_bounds(anchor):
            problems.append(
                f"UNREACHABLE_DESK: anchor outside cell at "
                f"({anchor.x},{anchor.y})")
        elif anchor not in reachable:
            problems.append(
                f"UNREACHABLE_DESK: no walkable path to desk anchor at "
                f"({anchor.x},{anchor.y})")

    # --- required anchors and their interaction/access tiles
    satisfied = {p.anchor_id for p in plan.placements if p.anchor_id}
    for anchor_id in archetype.required_anchors:
        if anchor_id not in satisfied:
            problems.append(
                f"MISSING_ANCHOR: {anchor_id!r} required by {archetype.id!r}")
    access_lookup = dict(plan.required_anchors)
    for anchor_id in archetype.required_anchors:
        access = access_lookup.get(anchor_id)
        if access is None:
            # A placed anchor may carry its own interaction tile.
            for placement in plan.placements:
                if placement.anchor_id == anchor_id and placement.interaction_tile:
                    access = placement.interaction_tile
                    break
        if access is not None:
            if not plan.in_bounds(access):
                problems.append(
                    f"ANCHOR_OUT_OF_BOUNDS: {anchor_id!r} access tile")
            elif access not in reachable:
                problems.append(
                    f"UNREACHABLE_ANCHOR: no walkable path to {anchor_id!r}")

    # --- decor budget (no sprite soup)
    decor = [p for p in plan.placements if p.layer == "decor" and not p.anchor_id]
    if len(decor) > 16:
        problems.append(f"DECOR_OVERFLOW: {len(decor)} decorative placements >16")

    return problems


def assert_valid_room_plan(plan: RoomPlan, recipe: RoomStyleRecipe,
                           catalog: Iterable[AssetEntry] | Mapping[str, AssetEntry]) -> None:
    problems = validate_room_plan(plan, recipe, catalog)
    if problems:
        raise RoomStyleError(problems)
