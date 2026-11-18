"""Outside-world data for Qubit Office (P3-06).

The exterior is a **validated data map**, not a decorative backdrop. It carries
the campus ground, roads, sidewalks, parking, paths, trees, gardens, fences,
props, the building facade/roof/entrance/windows/signage, the lobby/entrance/
security interior strip, the elevator/stairs portals that link the outside to
tower floors, and weather/day-night/season variants.

Three things this module is deliberate about:

* **Deterministic data.** :func:`build_exterior_map` returns byte-identical
  JSON on every run: no clock, no random seed, no network, no store.
* **Chunks, not one giant scene.** Every tile belongs to a bounded chunk. A
  chunk declares its neighbours, its zone and whether it animates, so a runtime
  can load and animate only the active chunks.
* **Paid art stays local.** Asset references record a local absolute path and a
  manifest digest, never art bytes. :func:`validate_exterior` refuses a paid
  reference whose path resolves inside the repository (the public tree).

The schema is versioned (``qubit-office-world-exterior/1``). Validation returns
a list of human-readable problems; an empty list means the map is structurally
sound. Nothing here renders and nothing here is ``VERIFIED``.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

__all__ = [
    "SCHEMA",
    "VERSION",
    "EXTERIOR_FEATURES",
    "PORTAL_KINDS",
    "LAYERS",
    "CHUNK_KINDS",
    "ExteriorMapError",
    "AssetRef",
    "ExteriorChunk",
    "ExteriorZone",
    "PortalLink",
    "ExteriorVariants",
    "ResourcePolicy",
    "ExteriorMap",
    "build_exterior_map",
    "validate_exterior",
    "exterior_payload",
    "compute_digest",
]

SCHEMA = "qubit-office-world-exterior/1"
VERSION = 1

#: Every visual feature the plan names for the outside world. The shipped map
#: declares at least one asset or chunk for each; validation enforces coverage.
EXTERIOR_FEATURES: Tuple[str, ...] = (
    "ground",
    "road",
    "sidewalk",
    "parking",
    "path",
    "trees",
    "garden",
    "fence",
    "prop",
    "facade",
    "roof",
    "entrance",
    "windows",
    "signage",
)

#: Portal kinds that may link the exterior to the tower.
PORTAL_KINDS: Tuple[str, ...] = ("egress", "elevator", "stairs")

#: Chunk draw layers. Overlap is only legal across layers (a facade may sit on
#: top of the ground), never inside one layer.
LAYERS: Tuple[str, ...] = ("ground", "floor", "building", "overlay")

#: Exterior features plus the inside strip that meets the tower lobby.
CHUNK_KINDS: Tuple[str, ...] = EXTERIOR_FEATURES + (
    "lobby",
    "elevator_bank",
    "security",
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_FLOOR_RE = re.compile(r"^floor-\d{3}$")
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

#: Hard ceilings so a map cannot ask a client to load an unbounded scene.
MAX_CHUNKS = 256
MAX_ACTIVE_CHUNKS = 96
MAX_CHUNK_TILES = 4096
MAX_CHUNK_DIM = 64
MAX_ASSETS_PER_CHUNK = 16
MAX_VARIANTS_PER_KIND = 12

_PAID_CREDIT = "Office art: LimeZu — https://limezu.itch.io"


class ExteriorMapError(ValueError):
    """The map could not be built (a programming error, not bad input data)."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def _digest(payload: Mapping[str, Any]) -> str:
    def strip(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {k: strip(v) for k, v in value.items() if k != "digest"}
        if isinstance(value, (list, tuple)):
            return [strip(v) for v in value]
        return value

    return hashlib.sha256(
        json.dumps(strip(payload), sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True).encode("utf-8")
    ).hexdigest()


# --------------------------------------------------------------- value types
@dataclass(frozen=True)
class AssetRef:
    """One local art reference. Metadata only; the art never ships."""

    asset_id: str
    feature: str
    pack: str
    local_path: str
    sha256: str
    paid: bool
    redistribution_allowed: bool
    local_only: bool
    license_status: str = "unknown"
    credit: str = _PAID_CREDIT

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "feature": self.feature,
            "pack": self.pack,
            "local_path": self.local_path,
            "sha256": self.sha256,
            "paid": bool(self.paid),
            "redistribution_allowed": bool(self.redistribution_allowed),
            "local_only": bool(self.local_only),
            "license_status": self.license_status,
            "credit": self.credit,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AssetRef":
        return cls(
            asset_id=str(data.get("asset_id", "")),
            feature=str(data.get("feature", "")),
            pack=str(data.get("pack", "")),
            local_path=str(data.get("local_path", "")),
            sha256=str(data.get("sha256", "")),
            paid=bool(data.get("paid", False)),
            redistribution_allowed=bool(
                data.get("redistribution_allowed", False)),
            local_only=bool(data.get("local_only", False)),
            license_status=str(data.get("license_status", "unknown")),
            credit=str(data.get("credit", _PAID_CREDIT)),
        )


@dataclass(frozen=True)
class ExteriorChunk:
    """A bounded rectangular group of tiles."""

    chunk_id: str
    zone_id: str
    kind: str
    layer: str
    x: int
    y: int
    width: int
    height: int
    active: bool
    animated: bool
    assets: Tuple[str, ...] = ()
    neighbours: Tuple[str, ...] = ()
    memory_scope: str = "exterior"
    z_index: int = 0

    @property
    def tiles(self) -> int:
        return max(0, int(self.width)) * max(0, int(self.height))

    @property
    def bounds(self) -> Tuple[int, int, int, int]:
        return (int(self.x), int(self.y), int(self.width), int(self.height))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "zone_id": self.zone_id,
            "kind": self.kind,
            "layer": self.layer,
            "bounds": [int(self.x), int(self.y), int(self.width),
                       int(self.height)],
            "tiles": self.tiles,
            "active": bool(self.active),
            "animated": bool(self.animated),
            "assets": list(self.assets),
            "neighbours": list(self.neighbours),
            "memory_scope": self.memory_scope,
            "z_index": int(self.z_index),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExteriorChunk":
        bounds = list(data.get("bounds", (0, 0, 0, 0)))
        while len(bounds) < 4:
            bounds.append(0)
        return cls(
            chunk_id=str(data.get("chunk_id", "")),
            zone_id=str(data.get("zone_id", "")),
            kind=str(data.get("kind", "")),
            layer=str(data.get("layer", "")),
            x=int(bounds[0]),
            y=int(bounds[1]),
            width=int(bounds[2]),
            height=int(bounds[3]),
            active=bool(data.get("active", False)),
            animated=bool(data.get("animated", False)),
            assets=tuple(str(a) for a in data.get("assets", ())),
            neighbours=tuple(str(n) for n in data.get("neighbours", ())),
            memory_scope=str(data.get("memory_scope", "exterior")),
            z_index=int(data.get("z_index", 0)),
        )


@dataclass(frozen=True)
class ExteriorZone:
    """A named group of chunks with its own active/animation policy."""

    zone_id: str
    kind: str
    label: str
    active: bool
    animate: bool
    chunk_ids: Tuple[str, ...]
    entry_chunk: str
    memory_scope: str = "exterior"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "kind": self.kind,
            "label": self.label,
            "active": bool(self.active),
            "animate": bool(self.animate),
            "chunk_ids": list(self.chunk_ids),
            "entry_chunk": self.entry_chunk,
            "memory_scope": self.memory_scope,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExteriorZone":
        return cls(
            zone_id=str(data.get("zone_id", "")),
            kind=str(data.get("kind", "")),
            label=str(data.get("label", "")),
            active=bool(data.get("active", False)),
            animate=bool(data.get("animate", False)),
            chunk_ids=tuple(str(c) for c in data.get("chunk_ids", ())),
            entry_chunk=str(data.get("entry_chunk", "")),
            memory_scope=str(data.get("memory_scope", "exterior")),
        )


@dataclass(frozen=True)
class PortalLink:
    """A link between two chunks, or between a lobby chunk and a tower floor."""

    portal_id: str
    kind: str
    from_chunk: str
    to_chunk: str = ""
    to_floor: str = ""
    bidirectional: bool = True
    capacity: int = 1
    locked: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "portal_id": self.portal_id,
            "kind": self.kind,
            "from_chunk": self.from_chunk,
            "to_chunk": self.to_chunk,
            "to_floor": self.to_floor,
            "bidirectional": bool(self.bidirectional),
            "capacity": int(self.capacity),
            "locked": bool(self.locked),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PortalLink":
        return cls(
            portal_id=str(data.get("portal_id", "")),
            kind=str(data.get("kind", "")),
            from_chunk=str(data.get("from_chunk", "")),
            to_chunk=str(data.get("to_chunk", "")),
            to_floor=str(data.get("to_floor", "")),
            bidirectional=bool(data.get("bidirectional", True)),
            capacity=int(data.get("capacity", 1)),
            locked=bool(data.get("locked", False)),
        )


@dataclass(frozen=True)
class ExteriorVariants:
    """Weather, day/night and seasonal presentation variants."""

    weather: Tuple[str, ...]
    day_night: Tuple[str, ...]
    seasons: Tuple[str, ...]
    default_weather: str
    default_day_night: str
    default_season: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "weather": list(self.weather),
            "day_night": list(self.day_night),
            "seasons": list(self.seasons),
            "default_weather": self.default_weather,
            "default_day_night": self.default_day_night,
            "default_season": self.default_season,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExteriorVariants":
        return cls(
            weather=tuple(str(v) for v in data.get("weather", ())),
            day_night=tuple(str(v) for v in data.get("day_night", ())),
            seasons=tuple(str(v) for v in data.get("seasons", ())),
            default_weather=str(data.get("default_weather", "")),
            default_day_night=str(data.get("default_day_night", "")),
            default_season=str(data.get("default_season", "")),
        )


@dataclass(frozen=True)
class ResourcePolicy:
    """Bounds that keep an exterior read finite."""

    max_chunks: int = MAX_CHUNKS
    max_active_chunks: int = MAX_ACTIVE_CHUNKS
    max_chunk_tiles: int = MAX_CHUNK_TILES
    max_assets_per_chunk: int = MAX_ASSETS_PER_CHUNK
    lru_capacity: int = 48
    preload_depth: int = 1
    only_active_zones_animate: bool = True
    lazy_load: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_chunks": int(self.max_chunks),
            "max_active_chunks": int(self.max_active_chunks),
            "max_chunk_tiles": int(self.max_chunk_tiles),
            "max_assets_per_chunk": int(self.max_assets_per_chunk),
            "lru_capacity": int(self.lru_capacity),
            "preload_depth": int(self.preload_depth),
            "only_active_zones_animate": bool(self.only_active_zones_animate),
            "lazy_load": bool(self.lazy_load),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResourcePolicy":
        return cls(
            max_chunks=int(data.get("max_chunks", MAX_CHUNKS)),
            max_active_chunks=int(data.get("max_active_chunks",
                                          MAX_ACTIVE_CHUNKS)),
            max_chunk_tiles=int(data.get("max_chunk_tiles", MAX_CHUNK_TILES)),
            max_assets_per_chunk=int(data.get("max_assets_per_chunk",
                                              MAX_ASSETS_PER_CHUNK)),
            lru_capacity=int(data.get("lru_capacity", 48)),
            preload_depth=int(data.get("preload_depth", 1)),
            only_active_zones_animate=bool(
                data.get("only_active_zones_animate", True)),
            lazy_load=bool(data.get("lazy_load", True)),
        )


@dataclass(frozen=True)
class ExteriorMap:
    map_id: str
    zones: Tuple[ExteriorZone, ...]
    chunks: Tuple[ExteriorChunk, ...]
    portals: Tuple[PortalLink, ...]
    variants: ExteriorVariants
    assets: Tuple[AssetRef, ...]
    resource_policy: ResourcePolicy
    campus_width: int
    campus_height: int
    tile_size: int = 16
    paid_art_local_only: bool = True
    public_tree_allowed: bool = False
    credit: str = _PAID_CREDIT
    schema: str = SCHEMA
    version: int = VERSION
    digest: str = ""

    # -- lookups -----------------------------------------------------------
    def chunk(self, chunk_id: str) -> ExteriorChunk:
        for chunk in self.chunks:
            if chunk.chunk_id == chunk_id:
                return chunk
        raise ExteriorMapError(f"unknown chunk {chunk_id!r}")

    def zone(self, zone_id: str) -> ExteriorZone:
        for zone in self.zones:
            if zone.zone_id == zone_id:
                return zone
        raise ExteriorMapError(f"unknown zone {zone_id!r}")

    def asset(self, asset_id: str) -> AssetRef:
        for asset in self.assets:
            if asset.asset_id == asset_id:
                return asset
        raise ExteriorMapError(f"unknown asset {asset_id!r}")

    def counts(self) -> Dict[str, int]:
        return {
            "zones": len(self.zones),
            "chunks": len(self.chunks),
            "portals": len(self.portals),
            "assets": len(self.assets),
            "active_zones": sum(1 for z in self.zones if z.active),
            "dormant_zones": sum(1 for z in self.zones if not z.active),
            "active_chunks": sum(1 for c in self.chunks if c.active),
            "animated_chunks": sum(1 for c in self.chunks if c.animated),
            "tiles": sum(c.tiles for c in self.chunks),
        }

    # -- (de)serialisation -------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "version": int(self.version),
            "map_id": self.map_id,
            "campus": {
                "width": int(self.campus_width),
                "height": int(self.campus_height),
                "tile_size": int(self.tile_size),
            },
            "zones": [z.to_dict() for z in self.zones],
            "chunks": [c.to_dict() for c in self.chunks],
            "portals": [p.to_dict() for p in self.portals],
            "variants": self.variants.to_dict(),
            "assets": [a.to_dict() for a in self.assets],
            "resource_policy": self.resource_policy.to_dict(),
            "art_policy": {
                "paid_art_local_only": bool(self.paid_art_local_only),
                "public_tree_allowed": bool(self.public_tree_allowed),
                "credit": self.credit,
            },
            "counts": self.counts(),
            "digest": self.digest,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExteriorMap":
        campus = data.get("campus", {}) or {}
        art = data.get("art_policy", {}) or {}
        return cls(
            map_id=str(data.get("map_id", "")),
            zones=tuple(ExteriorZone.from_dict(z)
                        for z in data.get("zones", ())),
            chunks=tuple(ExteriorChunk.from_dict(c)
                         for c in data.get("chunks", ())),
            portals=tuple(PortalLink.from_dict(p)
                          for p in data.get("portals", ())),
            variants=ExteriorVariants.from_dict(data.get("variants", {})),
            assets=tuple(AssetRef.from_dict(a)
                         for a in data.get("assets", ())),
            resource_policy=ResourcePolicy.from_dict(
                data.get("resource_policy", {})),
            campus_width=int(campus.get("width", 0)),
            campus_height=int(campus.get("height", 0)),
            tile_size=int(campus.get("tile_size", 16)),
            paid_art_local_only=bool(
                art.get("paid_art_local_only", True)),
            public_tree_allowed=bool(
                art.get("public_tree_allowed", False)),
            credit=str(art.get("credit", _PAID_CREDIT)),
            schema=str(data.get("schema", SCHEMA)),
            version=int(data.get("version", VERSION)),
            digest=str(data.get("digest", "")),
        )


def compute_digest(payload: Mapping[str, Any]) -> str:
    """Stable digest of a serialised map (the ``digest`` key is ignored)."""
    return _digest(payload)


# ------------------------------------------------------------------ builder
_PACK_MODERN = "Modern_Interiors_RPG_Maker_Version"
_PACK_SERENE = "Serene_Village_revamped_v1.9"
_LOCAL_ROOT = str(Path.home() / "Desktop" / "UI Office")


def _manifest_digest(pack: str, feature: str, asset_id: str) -> str:
    """Manifest identifier, **not** a decoded pixel hash.

    No paid art is opened by this module (or by any test); the digest is a
    stable key over the pack/feature/id tuple so a reference can be
    deduplicated and drift-checked without reading art bytes.
    """
    return hashlib.sha256(
        f"{pack}/{feature}/{asset_id}".encode("utf-8")).hexdigest()


def _asset(asset_id: str, feature: str, pack: str, rel: str,
           *, paid: bool = True) -> AssetRef:
    path = f"{_LOCAL_ROOT}/{pack}/{rel}"
    return AssetRef(
        asset_id=asset_id,
        feature=feature,
        pack=pack,
        local_path=path,
        sha256=_manifest_digest(pack, feature, asset_id),
        paid=paid,
        redistribution_allowed=not paid,
        local_only=True,
        license_status="paid_local_only" if paid else "unknown",
    )


def _chunk(chunk_id: str, zone_id: str, kind: str, layer: str,
           x: int, y: int, width: int, height: int, *,
           active: bool, animated: bool,
           assets: Sequence[str] = (), neighbours: Sequence[str] = (),
           z_index: int = 0) -> ExteriorChunk:
    return ExteriorChunk(
        chunk_id=chunk_id,
        zone_id=zone_id,
        kind=kind,
        layer=layer,
        x=x, y=y, width=width, height=height,
        active=active, animated=animated,
        assets=tuple(assets), neighbours=tuple(neighbours),
        z_index=z_index,
    )


def _assets() -> Tuple[AssetRef, ...]:
    rows = (
        _asset("asset-ground-grass", "ground", _PACK_SERENE,
               "tiles/ground/grass_16.png", paid=False),
        _asset("asset-ground-concrete", "ground", _PACK_MODERN,
               "floors/concrete.png"),
        _asset("asset-road-asphalt", "road", _PACK_SERENE,
               "tiles/roads/asphalt_16.png", paid=False),
        _asset("asset-sidewalk-paving", "sidewalk", _PACK_MODERN,
               "floors/pavement.png"),
        _asset("asset-parking-markings", "parking", _PACK_MODERN,
               "props/parking_line.png"),
        _asset("asset-path-gravel", "path", _PACK_SERENE,
               "tiles/paths/gravel_16.png", paid=False),
        _asset("asset-tree-oak", "trees", _PACK_SERENE,
               "props/trees/oak.png", paid=False),
        _asset("asset-garden-bed", "garden", _PACK_SERENE,
               "props/garden/flower_bed.png", paid=False),
        _asset("asset-fence-iron", "fence", _PACK_MODERN,
               "props/fence_iron.png"),
        _asset("asset-prop-bench", "prop", _PACK_MODERN,
               "props/bench.png"),
        _asset("asset-facade-brick", "facade", _PACK_MODERN,
               "walls/brick.png"),
        _asset("asset-roof-slate", "roof", _PACK_MODERN,
               "roof/slate.png"),
        _asset("asset-entrance-door", "entrance", _PACK_MODERN,
               "props/door_glass.png"),
        _asset("asset-window-glass", "windows", _PACK_MODERN,
               "props/window_glass.png"),
        _asset("asset-signage-tower", "signage", _PACK_MODERN,
               "props/sign_tower.png"),
    )
    return rows


def _zones_and_chunks() -> Tuple[Tuple[ExteriorZone, ...],
                                 Tuple[ExteriorChunk, ...]]:
    chunks: List[ExteriorChunk] = [
        # campus ground (4 quadrants, then roads/sidewalks)
        _chunk("chunk-ground-w", "zone-campus", "ground", "ground",
               0, 0, 48, 24, active=True, animated=False,
               assets=("asset-ground-grass",),
               neighbours=("chunk-ground-e", "chunk-ground-sw",
                           "chunk-fence-west", "chunk-road-west")),
        _chunk("chunk-ground-e", "zone-campus", "ground", "ground",
               48, 0, 48, 24, active=True, animated=False,
               assets=("asset-ground-grass",),
               neighbours=("chunk-ground-w", "chunk-ground-se",
                           "chunk-trees-east", "chunk-road-east")),
        _chunk("chunk-ground-sw", "zone-campus", "ground", "ground",
               0, 24, 48, 24, active=True, animated=False,
               assets=("asset-ground-grass",),
               neighbours=("chunk-ground-w", "chunk-ground-se",
                           "chunk-path-center",
                           "chunk-sidewalk-west")),
        _chunk("chunk-ground-se", "zone-campus", "ground", "ground",
               48, 24, 48, 24, active=True, animated=False,
               assets=("asset-ground-grass",),
               neighbours=("chunk-ground-e", "chunk-ground-sw",
                           "chunk-path-center",
                           "chunk-sidewalk-east")),
        _chunk("chunk-road-west", "zone-campus", "road", "ground",
               0, 48, 48, 4, active=True, animated=False,
               assets=("asset-road-asphalt",),
               neighbours=("chunk-road-east", "chunk-ground-w",
                           "chunk-sidewalk-west")),
        _chunk("chunk-road-east", "zone-campus", "road", "ground",
               48, 48, 48, 4, active=True, animated=False,
               assets=("asset-road-asphalt",),
               neighbours=("chunk-road-west", "chunk-ground-e",
                           "chunk-sidewalk-east")),
        _chunk("chunk-sidewalk-west", "zone-campus", "sidewalk", "ground",
               0, 52, 48, 4, active=True, animated=False,
               assets=("asset-sidewalk-paving",),
               neighbours=("chunk-road-west", "chunk-ground-sw",
                           "chunk-sidewalk-east", "chunk-entrance-doors")),
        _chunk("chunk-sidewalk-east", "zone-campus", "sidewalk", "ground",
               48, 52, 48, 4, active=True, animated=False,
               assets=("asset-sidewalk-paving",),
               neighbours=("chunk-road-east", "chunk-ground-se",
                           "chunk-sidewalk-west", "chunk-signage")),
        # campus overlays / building
        _chunk("chunk-path-center", "zone-campus", "path", "overlay",
               40, 24, 16, 24, active=True, animated=False,
               assets=("asset-path-gravel", "asset-prop-bench"),
               neighbours=("chunk-ground-sw", "chunk-ground-se",
                           "chunk-facade")),
        _chunk("chunk-trees-east", "zone-campus", "trees", "overlay",
               74, 22, 22, 18, active=True, animated=True,
               assets=("asset-tree-oak",),
               neighbours=("chunk-ground-e", "chunk-garden-north")),
        _chunk("chunk-fence-west", "zone-campus", "fence", "overlay",
               0, 0, 2, 48, active=True, animated=False,
               assets=("asset-fence-iron",),
               neighbours=("chunk-ground-w", "chunk-ground-sw")),
        _chunk("chunk-facade", "zone-campus", "facade", "building",
               28, 8, 40, 32, active=True, animated=False,
               assets=("asset-facade-brick", "asset-window-glass"),
               neighbours=("chunk-roof", "chunk-path-center",
                           "chunk-ground-sw"), z_index=10),
        _chunk("chunk-roof", "zone-campus", "roof", "building",
               28, 0, 40, 8, active=True, animated=False,
               assets=("asset-roof-slate",),
               neighbours=("chunk-facade",), z_index=11),
        _chunk("chunk-signage", "zone-campus", "signage", "overlay",
               56, 44, 12, 4, active=True, animated=True,
               assets=("asset-signage-tower",),
               neighbours=("chunk-sidewalk-east", "chunk-sidewalk-west")),
        # a deliberately dormant zone: it must never animate
        _chunk("chunk-parking-lot", "zone-parking", "parking", "overlay",
               2, 4, 22, 16, active=False, animated=False,
               assets=("asset-parking-markings",),
               neighbours=("chunk-ground-w",)),
        # entrance / lobby / security strip
        _chunk("chunk-entrance-doors", "zone-entrance", "entrance", "building",
               40, 52, 16, 4, active=True, animated=True,
               assets=("asset-entrance-door",),
               neighbours=("chunk-sidewalk-west", "chunk-sidewalk-east",
                           "chunk-lobby-main"), z_index=12),
        _chunk("chunk-lobby-main", "zone-lobby", "lobby", "floor",
               36, 56, 24, 8, active=True, animated=False,
               assets=("asset-ground-concrete", "asset-prop-bench"),
               neighbours=("chunk-lobby-elevators", "chunk-entrance-doors",
                           "chunk-security-desk")),
        _chunk("chunk-lobby-elevators", "zone-lobby", "elevator_bank", "floor",
               60, 56, 16, 8, active=True, animated=True,
               assets=("asset-entrance-door",),
               neighbours=("chunk-lobby-main",)),
        _chunk("chunk-security-desk", "zone-security", "security", "floor",
               20, 56, 16, 8, active=True, animated=False,
               assets=("asset-prop-bench",),
               neighbours=("chunk-lobby-main",)),
        # garden is its own active zone
        _chunk("chunk-garden-north", "zone-garden", "garden", "overlay",
               68, 4, 24, 16, active=True, animated=True,
               assets=("asset-garden-bed", "asset-tree-oak"),
               neighbours=("chunk-ground-e", "chunk-trees-east")),
    ]

    zones: List[ExteriorZone] = [
        ExteriorZone(
            zone_id="zone-campus", kind="campus", label="Campus exterior",
            active=True, animate=True,
            chunk_ids=tuple(c.chunk_id for c in chunks
                            if c.zone_id == "zone-campus"),
            entry_chunk="chunk-sidewalk-west"),
        ExteriorZone(
            zone_id="zone-entrance", kind="entrance",
            label="Building entrance", active=True, animate=True,
            chunk_ids=("chunk-entrance-doors",),
            entry_chunk="chunk-entrance-doors"),
        ExteriorZone(
            zone_id="zone-lobby", kind="lobby", label="Tower lobby",
            active=True, animate=True,
            chunk_ids=("chunk-lobby-main", "chunk-lobby-elevators"),
            entry_chunk="chunk-lobby-main"),
        ExteriorZone(
            zone_id="zone-security", kind="security", label="Lobby security",
            active=True, animate=False,
            chunk_ids=("chunk-security-desk",),
            entry_chunk="chunk-security-desk"),
        ExteriorZone(
            zone_id="zone-garden", kind="garden", label="North garden",
            active=True, animate=True,
            chunk_ids=("chunk-garden-north",),
            entry_chunk="chunk-garden-north"),
        ExteriorZone(
            zone_id="zone-parking", kind="parking", label="Visitor parking",
            active=False, animate=False,
            chunk_ids=("chunk-parking-lot",),
            entry_chunk="chunk-parking-lot"),
    ]
    return tuple(zones), tuple(chunks)


def _portals() -> Tuple[PortalLink, ...]:
    return (
        PortalLink("portal-campus-entrance", "egress",
                   "chunk-sidewalk-east", "chunk-entrance-doors"),
        PortalLink("portal-entrance-lobby", "egress",
                   "chunk-entrance-doors", "chunk-lobby-main"),
        PortalLink("portal-lobby-campus", "egress",
                   "chunk-lobby-main", "chunk-sidewalk-west"),
        PortalLink("portal-lobby-security", "egress",
                   "chunk-lobby-main", "chunk-security-desk"),
        PortalLink("portal-elevator-001", "elevator",
                   "chunk-lobby-elevators", to_floor="floor-001", capacity=8),
        PortalLink("portal-elevator-003", "elevator",
                   "chunk-lobby-elevators", to_floor="floor-003", capacity=8),
        PortalLink("portal-elevator-005", "elevator",
                   "chunk-lobby-elevators", to_floor="floor-005", capacity=8),
        PortalLink("portal-stairs-001", "stairs",
                   "chunk-lobby-elevators", to_floor="floor-001", capacity=4),
    )


def build_exterior_map() -> ExteriorMap:
    """Build the deterministic exterior map every caller shares."""
    zones, chunks = _zones_and_chunks()
    variants = ExteriorVariants(
        weather=("clear", "rain", "snow", "fog"),
        day_night=("dawn", "day", "dusk", "night"),
        seasons=("spring", "summer", "autumn", "winter"),
        default_weather="clear",
        default_day_night="day",
        default_season="summer",
    )
    exterior = ExteriorMap(
        map_id="exterior-campus-main",
        zones=zones,
        chunks=chunks,
        portals=_portals(),
        variants=variants,
        assets=_assets(),
        resource_policy=ResourcePolicy(),
        campus_width=96,
        campus_height=64,
    )
    return ExteriorMap.from_dict(
        {**exterior.to_dict(), "digest": compute_digest(exterior.to_dict())})


# --------------------------------------------------------------- validation
def _duplicates(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    dupes: List[str] = []
    for value in values:
        if value in seen and value not in dupes:
            dupes.append(value)
        seen.add(value)
    return dupes


def _reachable(start: str, edges: Mapping[str, Sequence[str]]) -> set[str]:
    seen = {start}
    stack = [start]
    while stack:
        node = stack.pop()
        for nxt in edges.get(node, ()):
            if nxt not in seen and nxt in edges:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def validate_exterior(map_obj: ExteriorMap,
                      *, repo_root: Path | str | None = None) -> List[str]:
    """Return every structural defect in the map, or ``[]``.

    Deterministic and pure except for path containment checks. The repository
    root is used **only** to prove a paid reference stays outside the public
    tree; override it in tests with a temp directory.
    """
    root = Path(repo_root) if repo_root is not None else _repo_root()
    problems: List[str] = []

    if map_obj.schema != SCHEMA:
        problems.append(f"schema: {map_obj.schema!r} != {SCHEMA!r}")
    if map_obj.version != VERSION:
        problems.append(f"version: {map_obj.version} != {VERSION}")
    if map_obj.tile_size != 16:
        problems.append(f"tile_size: {map_obj.tile_size} != 16 (world baseline)")
    if map_obj.campus_width <= 0 or map_obj.campus_height <= 0:
        problems.append("campus: width/height must be positive")
    if map_obj.digest and map_obj.digest != compute_digest(
            map_obj.to_dict()):
        problems.append("digest: does not match the serialised map")

    # --- uniqueness ------------------------------------------------------
    for label, ids in (
        ("zone", [z.zone_id for z in map_obj.zones]),
        ("chunk", [c.chunk_id for c in map_obj.chunks]),
        ("portal", [p.portal_id for p in map_obj.portals]),
        ("asset", [a.asset_id for a in map_obj.assets]),
    ):
        for dup in _duplicates(ids):
            problems.append(f"{label} {dup!r}: duplicate id")
        for value in ids:
            if not _ID_RE.match(value or ""):
                problems.append(f"{label} {value!r}: invalid id")

    zone_ids = {z.zone_id for z in map_obj.zones}
    chunk_by_id = {c.chunk_id: c for c in map_obj.chunks}
    asset_ids = {a.asset_id for a in map_obj.assets}
    chunk_zone: Dict[str, str] = {}

    # --- zones -----------------------------------------------------------
    for zone in map_obj.zones:
        if not zone.chunk_ids:
            problems.append(f"zone {zone.zone_id!r}: declares no chunks")
        if zone.entry_chunk not in zone.chunk_ids:
            problems.append(
                f"zone {zone.zone_id!r}: entry {zone.entry_chunk!r} is not"
                f" one of its chunks")
        if not zone.animate and any(
                chunk_by_id.get(c) and chunk_by_id[c].animated
                for c in zone.chunk_ids):
            problems.append(
                f"zone {zone.zone_id!r}: static zone contains an animated"
                f" chunk")
        for chunk_id in zone.chunk_ids:
            if chunk_id not in chunk_by_id:
                problems.append(
                    f"zone {zone.zone_id!r}: unknown chunk {chunk_id!r}")
                continue
            if chunk_id in chunk_zone:
                problems.append(
                    f"chunk {chunk_id!r}: declared by more than one zone")
            chunk_zone[chunk_id] = zone.zone_id
            chunk = chunk_by_id[chunk_id]
            if chunk.zone_id != zone.zone_id:
                problems.append(
                    f"chunk {chunk_id!r}: zone field {chunk.zone_id!r} !="
                    f" declaring zone {zone.zone_id!r}")

    # --- chunks ----------------------------------------------------------
    for chunk in map_obj.chunks:
        where = f"chunk {chunk.chunk_id!r}"
        if chunk.zone_id not in zone_ids:
            problems.append(f"{where}: unknown zone {chunk.zone_id!r}")
        zone = next((z for z in map_obj.zones
                     if z.zone_id == chunk.zone_id), None)
        if zone is None:
            continue
        if chunk.kind not in CHUNK_KINDS:
            problems.append(f"{where}: kind {chunk.kind!r} is not a feature")
        if chunk.layer not in LAYERS:
            problems.append(f"{where}: layer {chunk.layer!r} not in {LAYERS}")
        if chunk.width <= 0 or chunk.height <= 0:
            problems.append(f"{where}: bounds must be positive")
        if chunk.width > MAX_CHUNK_DIM or chunk.height > MAX_CHUNK_DIM:
            problems.append(
                f"{where}: {chunk.width}x{chunk.height} exceeds"
                f" {MAX_CHUNK_DIM}px?! (tiles)")
        if chunk.tiles > map_obj.resource_policy.max_chunk_tiles:
            problems.append(
                f"{where}: {chunk.tiles} tiles exceed the"
                f" {map_obj.resource_policy.max_chunk_tiles}-tile budget")
        if (chunk.x < 0 or chunk.y < 0
                or chunk.x + chunk.width > map_obj.campus_width
                or chunk.y + chunk.height > map_obj.campus_height):
            problems.append(f"{where}: bounds fall outside the campus")
        if chunk.active and not zone.active:
            problems.append(f"{where}: active in dormant zone {zone.zone_id!r}")
        if chunk.animated and not zone.active:
            problems.append(
                f"{where}: animates while zone {zone.zone_id!r} is dormant")
        if chunk.animated and not zone.animate:
            problems.append(
                f"{where}: animates while zone {zone.zone_id!r} is"
                f" non-animating")
        if len(chunk.assets) > map_obj.resource_policy.max_assets_per_chunk:
            problems.append(
                f"{where}: {len(chunk.assets)} assets exceed the"
                f" {map_obj.resource_policy.max_assets_per_chunk} budget")
        for asset_id in chunk.assets:
            if asset_id not in asset_ids:
                problems.append(f"{where}: unknown asset {asset_id!r}")
        for neighbour in chunk.neighbours:
            if neighbour == chunk.chunk_id:
                problems.append(f"{where}: neighbours itself")
            elif neighbour not in chunk_by_id:
                problems.append(f"{where}: unknown neighbour {neighbour!r}")

    # --- chunk overlap (same layer only) ---------------------------------
    by_layer: Dict[str, List[ExteriorChunk]] = {}
    for chunk in map_obj.chunks:
        by_layer.setdefault(chunk.layer, []).append(chunk)
    for layer, layer_chunks in by_layer.items():
        for i, first in enumerate(layer_chunks):
            for second in layer_chunks[i + 1:]:
                ax, ay, aw, ah = first.bounds
                bx, by, bw, bh = second.bounds
                if (ax < bx + bw and bx < ax + aw
                        and ay < by + bh and by < ay + ah):
                    problems.append(
                        f"chunks {first.chunk_id!r} and {second.chunk_id!r}"
                        f": overlap on layer {layer!r}")

    # --- connectivity ----------------------------------------------------
    edges: Dict[str, List[str]] = {}
    for chunk in map_obj.chunks:
        edges.setdefault(chunk.chunk_id, [])
    for chunk in map_obj.chunks:
        for neighbour in chunk.neighbours:
            if neighbour in edges:
                edges[chunk.chunk_id].append(neighbour)
                edges[neighbour].append(chunk.chunk_id)

    if map_obj.chunks:
        start = map_obj.zones[0].entry_chunk if map_obj.zones else ""
        if start in edges:
            reached = _reachable(start, edges)
            for chunk in map_obj.chunks:
                if chunk.chunk_id not in reached:
                    problems.append(
                        f"chunk {chunk.chunk_id!r}: unreachable from"
                        f" {start!r}")

    for zone in map_obj.zones:
        members = [c for c in zone.chunk_ids if c in chunk_by_id]
        if len(members) <= 1:
            continue
        local: Dict[str, List[str]] = {c: [] for c in members}
        member_set = set(members)
        for chunk_id in members:
            for neighbour in chunk_by_id[chunk_id].neighbours:
                if neighbour in member_set:
                    local[chunk_id].append(neighbour)
        reached = _reachable(zone.entry_chunk, local)
        for chunk_id in members:
            if chunk_id not in reached:
                problems.append(
                    f"zone {zone.zone_id!r}: chunk {chunk_id!r} is not"
                    f" connected within the zone")

    # --- portals / egress ------------------------------------------------
    portal_ids = {p.portal_id for p in map_obj.portals}
    if len(portal_ids) != len(map_obj.portals):
        problems.append("portals: duplicate ids")
    incident: Dict[str, List[PortalLink]] = {
        c.chunk_id: [] for c in map_obj.chunks}
    for portal in map_obj.portals:
        where = f"portal {portal.portal_id!r}"
        if portal.kind not in PORTAL_KINDS:
            problems.append(f"{where}: kind {portal.kind!r} not in"
                            f" {PORTAL_KINDS}")
        if portal.from_chunk not in chunk_by_id:
            problems.append(f"{where}: unknown from_chunk"
                            f" {portal.from_chunk!r}")
        else:
            incident[portal.from_chunk].append(portal)
        if portal.to_chunk:
            if portal.to_chunk not in chunk_by_id:
                problems.append(f"{where}: unknown to_chunk"
                                f" {portal.to_chunk!r}")
            else:
                incident[portal.to_chunk].append(portal)
            if portal.kind in ("elevator", "stairs"):
                problems.append(f"{where}: a floor portal needs to_floor")
        elif portal.kind in ("elevator", "stairs"):
            if not _FLOOR_RE.match(portal.to_floor or ""):
                problems.append(
                    f"{where}: to_floor {portal.to_floor!r} is not a tower"
                    f" floor id")
        if portal.capacity <= 0:
            problems.append(f"{where}: capacity must be positive")

    lobby = next((z for z in map_obj.zones if z.kind == "lobby"), None)
    if lobby is None:
        problems.append("zones: no lobby zone; the tower needs an inside"
                        " connection")
    else:
        ladder = {"elevator": 0, "stairs": 0}
        egress_to_outside = 0
        for chunk_id in lobby.chunk_ids:
            for portal in incident.get(chunk_id, ()):
                if portal.kind in ladder:
                    ladder[portal.kind] += 1
                    if not portal.to_floor:
                        problems.append(
                            f"portal {portal.portal_id!r}: lobby"
                            f" {portal.kind} has no floor")
                if portal.kind == "egress" and portal.to_chunk:
                    target_zone = chunk_by_id[portal.to_chunk].zone_id
                    if target_zone != lobby.zone_id:
                        egress_to_outside += 1
        for kind, count in ladder.items():
            if count < 1:
                problems.append(
                    f"lobby: no {kind} portal; exterior cannot reach floors")
        if egress_to_outside < 2:
            problems.append(
                "lobby: needs at least two egress links (entrance + outside)")

    # every interior/connector zone must be portal-reachable
    for zone in map_obj.zones:
        if zone.kind in {"lobby", "entrance", "security"}:
            if not any(incident.get(c) for c in zone.chunk_ids):
                problems.append(
                    f"zone {zone.zone_id!r}: no portal connects it")

    # --- variant sets ----------------------------------------------------
    variants = map_obj.variants
    for label, values, default in (
        ("weather", variants.weather, variants.default_weather),
        ("day_night", variants.day_night, variants.default_day_night),
        ("season", variants.seasons, variants.default_season),
    ):
        if not values:
            problems.append(f"variants.{label}: empty")
        if len(values) > MAX_VARIANTS_PER_KIND:
            problems.append(
                f"variants.{label}: {len(values)} exceed"
                f" {MAX_VARIANTS_PER_KIND}")
        if len(set(values)) != len(values):
            problems.append(f"variants.{label}: duplicate values")
        if default not in values:
            problems.append(
                f"variants.{label}: default {default!r} is not declared")

    # --- resource bounds -------------------------------------------------
    policy = map_obj.resource_policy
    if policy.max_chunks <= 0 or policy.max_chunks > MAX_CHUNKS:
        problems.append(f"resource_policy.max_chunks {policy.max_chunks}"
                        f" outside 1..{MAX_CHUNKS}")
    if len(map_obj.chunks) > policy.max_chunks:
        problems.append(
            f"chunks: {len(map_obj.chunks)} exceed max_chunks"
            f" {policy.max_chunks} (unbounded loading)")
    active_chunks = sum(1 for c in map_obj.chunks if c.active)
    if policy.max_active_chunks <= 0:
        problems.append("resource_policy.max_active_chunks must be positive")
    elif active_chunks > policy.max_active_chunks:
        problems.append(
            f"chunks: {active_chunks} active exceed max_active_chunks"
            f" {policy.max_active_chunks}")
    if policy.max_chunk_tiles <= 0 or policy.max_chunk_tiles > MAX_CHUNK_TILES:
        problems.append("resource_policy.max_chunk_tiles outside 1.."
                        f"{MAX_CHUNK_TILES}")
    if (policy.max_assets_per_chunk <= 0
            or policy.max_assets_per_chunk > MAX_ASSETS_PER_CHUNK):
        problems.append("resource_policy.max_assets_per_chunk outside 1.."
                        f"{MAX_ASSETS_PER_CHUNK}")
    if not (0 < policy.lru_capacity <= max(1, policy.max_chunks)):
        problems.append("resource_policy.lru_capacity must be in 1..max_chunks")
    if not (0 <= policy.preload_depth <= 2):
        problems.append("resource_policy.preload_depth must be 0..2")
    if not policy.only_active_zones_animate:
        problems.append(
            "resource_policy.only_active_zones_animate must be true")
    if not policy.lazy_load:
        problems.append("resource_policy.lazy_load must be true")

    # --- asset / paid-art policy ----------------------------------------
    if not map_obj.paid_art_local_only:
        problems.append("art_policy: paid art must be local-only")
    if map_obj.public_tree_allowed:
        problems.append("art_policy: paid art must never enter the public tree")
    if not map_obj.credit.strip():
        problems.append("art_policy: the LimeZu credit is required")

    features = {a.feature for a in map_obj.assets}
    features.update(c.kind for c in map_obj.chunks)
    for feature in EXTERIOR_FEATURES:
        if feature not in features:
            problems.append(f"features: {feature!r} has no asset or chunk")

    for asset in map_obj.assets:
        where = f"asset {asset.asset_id!r}"
        if asset.feature not in EXTERIOR_FEATURES:
            problems.append(f"{where}: unknown feature {asset.feature!r}")
        if not _SHA_RE.match(asset.sha256 or ""):
            problems.append(f"{where}: sha256 must be 64 lowercase hex")
        if not asset.local_path:
            problems.append(f"{where}: empty local path")
        if asset.paid:
            if not asset.local_only:
                problems.append(f"{where}: paid art must be local_only")
            if asset.redistribution_allowed:
                problems.append(
                    f"{where}: paid art must not be redistributable")
            path = Path(asset.local_path)
            if not path.is_absolute():
                problems.append(
                    f"{where}: paid art path must be absolute and local")
            elif _inside(path, root):
                problems.append(
                    f"{where}: paid art path is inside the repository"
                    f" (public tree): {asset.local_path}")
        if asset.local_only and asset.local_path:
            path = Path(asset.local_path)
            if path.is_absolute() and _inside(path, root):
                problems.append(
                    f"{where}: local-only art path is inside the repository"
                    f" (public tree): {asset.local_path}")

    return problems


def exterior_payload(map_obj: ExteriorMap) -> Dict[str, Any]:
    """The bounded JSON payload for a validator/client read."""
    return {
        "ok": not validate_exterior(map_obj),
        "schema": SCHEMA,
        "version": VERSION,
        "digest": map_obj.digest,
        "counts": map_obj.counts(),
        "map": map_obj.to_dict(),
    }
