"""Outside-world presentation projection for Qubit Office (P6-03).

This module is a *presentation* layer over :mod:`voicebridge.office.world.exterior`.
It never mutates the exterior map, never reads the clock, never touches a
store or the network, and never creates business truth. Everything it emits is
derived, byte-identical on every run, and explicitly labelled as presentation
so a renderer cannot mistake a zone, a chunk or a weather cue for an agent, a
desk, a task or a completed run.

What the projection carries
---------------------------
* the campus exterior landmarks (ground, roads, sidewalks, parking, paths,
  trees, gardens, fences, props, facade, roof, entrance, windows, signage);
* the lobby / security / elevator / stairs interior strip that meets the tower;
* the weather / day-night / seasonal environment and a deterministic mapping
  from every declared variant to an animated cue **and** a reduced-motion
  still cue (so motion is never the only status channel);
* per-zone active/dormant state and per-chunk active/animated state;
* portal links from the lobby to tower floors.

What it deliberately does not do
--------------------------------
* It never animates a dormant zone: ``only_active_zones_animate`` is a hard
  invariant, not a renderer hint.
* It never loads unbounded content: active chunks and total chunks are checked
  against the exterior resource policy.
* It never amplifies a business fact: :func:`validate_outside_world` rejects a
  payload that grew an occupant/agent/run/desk field.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from . import exterior as _exterior

__all__ = [
    "SCHEMA",
    "VERSION",
    "REQUIRED_PLACES",
    "FORBIDDEN_TRUTH_KEYS",
    "OutsideWorldError",
    "OutsideWorld",
    "build_outside_world",
    "environment_events",
    "outside_world_payload",
    "validate_outside_world",
    "compute_digest",
]

SCHEMA = "qubit-office-world-outside/1"
VERSION = 1

#: Landmark categories the outside world must be able to present. The value is
#: the chunk kind or portal kind that satisfies it.
REQUIRED_PLACES: Tuple[Tuple[str, str], ...] = (
    ("ground", "ground"),
    ("roads", "road"),
    ("paths", "path"),
    ("trees", "trees"),
    ("gardens", "garden"),
    ("fences", "fence"),
    ("facade", "facade"),
    ("roof", "roof"),
    ("entrance", "entrance"),
    ("signage", "signage"),
    ("lobby", "lobby"),
    ("security", "security"),
    ("elevators", "elevator"),
    ("stairs", "stairs"),
)

#: A presentation payload may not grow any of these keys. They would turn the
#: projection into a second source of business truth.
FORBIDDEN_TRUTH_KEYS: Tuple[str, ...] = (
    "agent", "agents", "agent_id", "occupant", "occupants", "desk", "desks",
    "desk_id", "seat", "seats", "seat_id", "run", "run_id", "task", "task_id",
    "working", "completed", "business_id", "tenant_id", "session_id",
)

# weather -> (event id, label, animated cue, reduced-motion still cue)
_WEATHER_CUES: Dict[str, Tuple[str, str, str, str]] = {
    "clear": ("weather-clear", "Clear sky", "sky-clear", "sky-clear"),
    "rain": ("weather-rain", "Rain", "rain-streaks", "rain-static"),
    "snow": ("weather-snow", "Snow", "snowfall", "snow-static"),
    "fog": ("weather-fog", "Fog", "fog-bands", "fog-static"),
}

_DAY_NIGHT_CUES: Dict[str, Tuple[str, str, str, str]] = {
    "dawn": ("day-dawn", "Dawn", "warm-rim", "warm-rim"),
    "day": ("day-day", "Day", "bright-sky", "bright-sky"),
    "dusk": ("day-dusk", "Dusk", "amber-glow", "amber-glow"),
    "night": ("day-night", "Night", "dark-blue", "dark-blue"),
}

_SEASON_CUES: Dict[str, Tuple[str, str, str, str]] = {
    "spring": ("season-spring", "Spring", "blossom-petals", "blossom-static"),
    "summer": ("season-summer", "Summer", "heat-haze", "heat-static"),
    "autumn": ("season-autumn", "Autumn", "falling-leaves", "leaves-static"),
    "winter": ("season-winter", "Winter", "frost-drift", "frost-static"),
}

_CUE_TABLES = {
    "weather": _WEATHER_CUES,
    "day_night": _DAY_NIGHT_CUES,
    "season": _SEASON_CUES,
}


class OutsideWorldError(ValueError):
    """The projection could not be built (a programming error, not input)."""


# ------------------------------------------------------------------- helpers
def _strip_digest(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _strip_digest(v) for k, v in value.items() if k != "digest"}
    if isinstance(value, (list, tuple)):
        return [_strip_digest(v) for v in value]
    return value


def compute_digest(payload: Mapping[str, Any]) -> str:
    """Stable SHA-256 of a serialised projection (``digest`` ignored)."""
    return hashlib.sha256(
        json.dumps(_strip_digest(payload), sort_keys=True, separators=(",", ":"),
                   ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _bounds_union(chunks: Sequence[_exterior.ExteriorChunk]) -> Dict[str, int]:
    if not chunks:
        return {"x": 0, "y": 0, "width": 0, "height": 0}
    xs = [c.x for c in chunks]
    ys = [c.y for c in chunks]
    x = min(xs)
    y = min(ys)
    width = max(c.x + c.width for c in chunks) - x
    height = max(c.y + c.height for c in chunks) - y
    return {"x": int(x), "y": int(y), "width": int(width), "height": int(height)}


def _cue(kind: str, value: str) -> Dict[str, Any]:
    table = _CUE_TABLES[kind]
    try:
        event_id, label, cue, still_cue = table[value]
    except KeyError as exc:  # pragma: no cover - guarded by validation
        raise OutsideWorldError(
            f"no {kind} presentation cue for {value!r}") from exc
    return {
        "event_id": event_id,
        "kind": kind,
        "value": value,
        "label": label,
        "cue": cue,
        "still_cue": still_cue,
        "animated": cue != still_cue,
    }


# -------------------------------------------------------------------- events
def environment_events(map_obj: Optional[_exterior.ExteriorMap] = None
                       ) -> List[Dict[str, Any]]:
    """Map every declared weather/day-night/season variant to presentation cues.

    Deterministic; a variant with no cue is a typed error rather than a silent
    skip, so the renderer can never receive an unmapped environment value.
    """
    world_map = map_obj or _exterior.build_exterior_map()
    variants = world_map.variants
    events: List[Dict[str, Any]] = []
    for kind, values in (
        ("weather", variants.weather),
        ("day_night", variants.day_night),
        ("season", variants.seasons),
    ):
        for value in values:
            events.append(_cue(kind, value))
    return events


# ---------------------------------------------------------------- projection
@dataclass(frozen=True)
class OutsideWorld:
    """A derived, presentation-only view of the exterior map."""

    map_digest: str
    environment: Dict[str, Any]
    zones: Tuple[Dict[str, Any], ...]
    chunks: Tuple[Dict[str, Any], ...]
    places: Tuple[Dict[str, Any], ...]
    portals: Tuple[Dict[str, Any], ...]
    floor_links: Tuple[Dict[str, Any], ...]
    assets: Tuple[Dict[str, Any], ...]
    summary: Dict[str, Any]
    constraints: Dict[str, Any]
    credit: str
    schema: str = SCHEMA
    version: int = VERSION
    digest: str = ""

    def zone(self, zone_id: str) -> Dict[str, Any]:
        for zone in self.zones:
            if zone["zone_id"] == zone_id:
                return zone
        raise OutsideWorldError(f"unknown zone {zone_id!r}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "version": int(self.version),
            "mode": "presentation",
            "business_truth": False,
            "map_digest": self.map_digest,
            "digest": self.digest,
            "credit": self.credit,
            "environment": _strip_digest(self.environment),
            "zones": [_strip_digest(z) for z in self.zones],
            "chunks": [_strip_digest(c) for c in self.chunks],
            "places": [_strip_digest(p) for p in self.places],
            "portals": [_strip_digest(p) for p in self.portals],
            "floor_links": [_strip_digest(f) for f in self.floor_links],
            "assets": [_strip_digest(a) for a in self.assets],
            "summary": dict(self.summary),
            "constraints": dict(self.constraints),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OutsideWorld":
        return cls(
            map_digest=str(data.get("map_digest", "")),
            environment=dict(data.get("environment", {}) or {}),
            zones=tuple(dict(z) for z in data.get("zones", ())),
            chunks=tuple(dict(c) for c in data.get("chunks", ())),
            places=tuple(dict(p) for p in data.get("places", ())),
            portals=tuple(dict(p) for p in data.get("portals", ())),
            floor_links=tuple(dict(f) for f in data.get("floor_links", ())),
            assets=tuple(dict(a) for a in data.get("assets", ())),
            summary=dict(data.get("summary", {}) or {}),
            constraints=dict(data.get("constraints", {}) or {}),
            credit=str(data.get("credit", "")),
            schema=str(data.get("schema", SCHEMA)),
            version=int(data.get("version", VERSION)),
            digest=str(data.get("digest", "")),
        )


def _zone_rows(map_obj: _exterior.ExteriorMap) -> Tuple[Dict[str, Any], ...]:
    chunks_by_zone: Dict[str, List[_exterior.ExteriorChunk]] = {}
    for chunk in map_obj.chunks:
        chunks_by_zone.setdefault(chunk.zone_id, []).append(chunk)
    rows: List[Dict[str, Any]] = []
    for zone in map_obj.zones:
        members = [c for c in chunks_by_zone.get(zone.zone_id, ())]
        animated = [c for c in members if c.animated]
        rows.append({
            "zone_id": zone.zone_id,
            "kind": zone.kind,
            "label": zone.label,
            "state": "active" if zone.active else "dormant",
            "animated": bool(zone.active and zone.animate),
            "entry_chunk": zone.entry_chunk,
            "chunk_ids": list(zone.chunk_ids),
            "bounds": _bounds_union(members),
            "counts": {
                "chunks": len(members),
                "active_chunks": sum(1 for c in members if c.active),
                "animated_chunks": len(animated),
                "tiles": sum(c.tiles for c in members),
            },
        })
    return tuple(rows)


def _chunk_rows(map_obj: _exterior.ExteriorMap) -> Tuple[Dict[str, Any], ...]:
    zone_state = {z.zone_id: z.active for z in map_obj.zones}
    rows: List[Dict[str, Any]] = []
    for chunk in map_obj.chunks:
        active_zone = bool(zone_state.get(chunk.zone_id, False))
        rendered_active = bool(chunk.active and active_zone)
        rows.append({
            "chunk_id": chunk.chunk_id,
            "zone_id": chunk.zone_id,
            "kind": chunk.kind,
            "layer": chunk.layer,
            "bounds": {"x": chunk.x, "y": chunk.y,
                       "width": chunk.width, "height": chunk.height},
            "tiles": chunk.tiles,
            "active": rendered_active,
            "animated": bool(chunk.animated and rendered_active),
            "state": "active" if rendered_active else "dormant",
            "assets": list(chunk.assets),
            "z_index": chunk.z_index,
        })
    return tuple(rows)


def _place_rows(map_obj: _exterior.ExteriorMap
                ) -> Tuple[Dict[str, Any], ...]:
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for chunk in map_obj.chunks:
        place_id = f"place-{chunk.chunk_id}"
        if place_id in seen:
            continue
        seen.add(place_id)
        zone = map_obj.zone(chunk.zone_id)
        rows.append({
            "place_id": place_id,
            "kind": chunk.kind,
            "label": f"{zone.label} · {chunk.kind}",
            "zone_id": chunk.zone_id,
            "chunk_id": chunk.chunk_id,
            "active": bool(chunk.active and zone.active),
            "animated": bool(chunk.animated and zone.active and zone.animate),
        })
    return tuple(rows)


def _portal_rows(map_obj: _exterior.ExteriorMap
                 ) -> Tuple[Dict[str, Any], ...]:
    rows: List[Dict[str, Any]] = []
    for portal in map_obj.portals:
        if portal.kind in ("elevator", "stairs"):
            label = f"{portal.kind.title()} to {portal.to_floor}"
        else:
            label = f"{portal.kind.title()} link"
        rows.append({
            "portal_id": portal.portal_id,
            "kind": portal.kind,
            "from_chunk": portal.from_chunk,
            "to_chunk": portal.to_chunk,
            "to_floor": portal.to_floor,
            "label": label,
            "bidirectional": bool(portal.bidirectional),
            "capacity": int(portal.capacity),
            "locked": bool(portal.locked),
        })
    return tuple(rows)


def _floor_links(map_obj: _exterior.ExteriorMap
                 ) -> Tuple[Dict[str, Any], ...]:
    rows: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()
    for portal in map_obj.portals:
        if portal.kind not in ("elevator", "stairs") or not portal.to_floor:
            continue
        key = (portal.kind, portal.to_floor)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "kind": portal.kind,
            "floor_id": portal.to_floor,
            "portal_id": portal.portal_id,
            "label": f"Take the {portal.kind} to {portal.to_floor}",
        })
    return tuple(rows)


def _asset_rows(map_obj: _exterior.ExteriorMap) -> Tuple[Dict[str, Any], ...]:
    rows: List[Dict[str, Any]] = []
    for asset in map_obj.assets:
        rows.append({
            "asset_id": asset.asset_id,
            "feature": asset.feature,
            "pack": asset.pack,
            "paid": bool(asset.paid),
            "local_only": bool(asset.local_only),
            "redistribution_allowed": bool(asset.redistribution_allowed),
            "license_status": asset.license_status,
            "credit": asset.credit,
        })
    return tuple(rows)


def build_outside_world(
    map_obj: Optional[_exterior.ExteriorMap] = None,
) -> OutsideWorld:
    """Build the deterministic presentation projection."""
    world_map = map_obj or _exterior.build_exterior_map()
    zones = _zone_rows(world_map)
    chunks = _chunk_rows(world_map)
    places = _place_rows(world_map)
    portals = _portal_rows(world_map)
    floor_links = _floor_links(world_map)
    assets = _asset_rows(world_map)
    events = environment_events(world_map)
    policy = world_map.resource_policy
    variants = world_map.variants

    active_chunks = [c for c in chunks if c["active"]]
    animated_chunks = [c for c in chunks if c["animated"]]
    summary = {
        "zones": len(zones),
        "active_zones": sum(1 for z in zones if z["state"] == "active"),
        "dormant_zones": sum(1 for z in zones if z["state"] == "dormant"),
        "chunks": len(chunks),
        "active_chunks": len(active_chunks),
        "dormant_chunks": len(chunks) - len(active_chunks),
        "animated_chunks": len(animated_chunks),
        "portals": len(portals),
        "elevator_links": sum(1 for f in floor_links
                              if f["kind"] == "elevator"),
        "stairs_links": sum(1 for f in floor_links if f["kind"] == "stairs"),
        "places": len(places),
        "assets": len(assets),
    }
    constraints = {
        "presentation_only": True,
        "business_truth": False,
        "only_active_zones_animate": bool(
            policy.only_active_zones_animate),
        "max_chunks": int(policy.max_chunks),
        "max_active_chunks": int(policy.max_active_chunks),
        "max_chunk_tiles": int(policy.max_chunk_tiles),
        "lru_capacity": int(policy.lru_capacity),
        "preload_depth": int(policy.preload_depth),
        "lazy_load": bool(policy.lazy_load),
        "reduced_motion_supported": True,
    }
    environment = {
        "weather": variants.default_weather,
        "day_night": variants.default_day_night,
        "season": variants.default_season,
        "weather_values": list(variants.weather),
        "day_night_values": list(variants.day_night),
        "season_values": list(variants.seasons),
        "events": events,
    }
    world = OutsideWorld(
        map_digest=world_map.digest,
        environment=environment,
        zones=zones,
        chunks=chunks,
        places=places,
        portals=portals,
        floor_links=floor_links,
        assets=assets,
        summary=summary,
        constraints=constraints,
        credit=world_map.credit,
    )
    payload = world.to_dict()
    payload["digest"] = compute_digest(payload)
    return OutsideWorld.from_dict(payload)


def outside_world_payload(world: OutsideWorld) -> Dict[str, Any]:
    """The bounded JSON payload a client reads (projection + summary)."""
    return {
        "ok": not validate_outside_world(world),
        "schema": SCHEMA,
        "version": VERSION,
        "digest": world.digest,
        "summary": dict(world.summary),
        "world": world.to_dict(),
    }


# ---------------------------------------------------------------- validation
def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _walk_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_keys(item)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def validate_outside_world(
    world: OutsideWorld,
    *,
    repo_root: Path | str | None = None,
    map_obj: Optional[_exterior.ExteriorMap] = None,
) -> List[str]:
    """Return every structural defect in the projection, or ``[]``.

    Reads no art. The repository root is used only to prove that a local-only
    art reference cannot live inside the public tree.
    """
    world_map = map_obj or _exterior.build_exterior_map()
    root = Path(repo_root) if repo_root is not None else _repo_root()
    problems: List[str] = []

    if world.schema != SCHEMA:
        problems.append(f"schema: {world.schema!r} != {SCHEMA!r}")
    if world.version != VERSION:
        problems.append(f"version: {world.version} != {VERSION}")
    if world.map_digest != world_map.digest:
        problems.append("map_digest: does not match the exterior map")
    if world.digest and world.digest != compute_digest(world.to_dict()):
        problems.append("digest: does not match the serialised projection")
    if not world.credit.strip():
        problems.append("credit: the LimeZu credit is required")

    # -- environment mapping ---------------------------------------------
    events = world.environment.get("events") or []
    declared = {
        "weather": list(world_map.variants.weather),
        "day_night": list(world_map.variants.day_night),
        "season": list(world_map.variants.seasons),
    }
    for kind, values in declared.items():
        mapped = {e.get("value") for e in events if e.get("kind") == kind}
        missing = [v for v in values if v not in mapped]
        if missing:
            problems.append(
                f"environment.{kind}: unmapped values {sorted(missing)}")
    for event in events:
        kind = event.get("kind")
        if kind not in _CUE_TABLES:
            problems.append(f"environment event: unknown kind {kind!r}")
            continue
        if event.get("value") not in declared.get(kind, ()):
            problems.append(
                f"environment event {event.get('event_id')!r}: value"
                f" {event.get('value')!r} is not declared")
        if not str(event.get("label") or "").strip():
            problems.append(
                f"environment event {event.get('event_id')!r}: no text label")
        if not str(event.get("still_cue") or "").strip():
            problems.append(
                f"environment event {event.get('event_id')!r}: no reduced-"
                f"motion still cue")
    for kind, default in (
        ("weather", world_map.variants.default_weather),
        ("day_night", world_map.variants.default_day_night),
        ("season", world_map.variants.default_season),
    ):
        if world.environment.get(kind) != default:
            problems.append(
                f"environment.{kind}: default {world.environment.get(kind)!r}"
                f" != map default {default!r}")

    # -- zones / chunks ---------------------------------------------------
    zone_state = {z["zone_id"]: z for z in world.zones}
    if not world.zones:
        problems.append("zones: projection has no zones")
    for zone in world.zones:
        if zone.get("state") not in ("active", "dormant"):
            problems.append(
                f"zone {zone.get('zone_id')!r}: bad state"
                f" {zone.get('state')!r}")
        if zone.get("animated") and zone.get("state") != "active":
            problems.append(
                f"zone {zone.get('zone_id')!r}: dormant zone animates")
        if not zone.get("chunk_ids"):
            problems.append(f"zone {zone.get('zone_id')!r}: no chunks")

    for chunk in world.chunks:
        where = f"chunk {chunk.get('chunk_id')!r}"
        zone = zone_state.get(chunk.get("zone_id"))
        if zone is None:
            problems.append(f"{where}: unknown zone {chunk.get('zone_id')!r}")
            continue
        if chunk.get("animated") and zone.get("state") != "active":
            problems.append(
                f"{where}: animates inside dormant zone"
                f" {zone.get('zone_id')!r}")
        if chunk.get("active") and zone.get("state") != "active":
            problems.append(
                f"{where}: active inside dormant zone"
                f" {zone.get('zone_id')!r}")
        if chunk.get("animated") and not chunk.get("active"):
            problems.append(f"{where}: animates while inactive")

    # -- bounds -----------------------------------------------------------
    policy = world_map.resource_policy
    total = len(world.chunks)
    active = sum(1 for c in world.chunks if c.get("active"))
    animated = sum(1 for c in world.chunks if c.get("animated"))
    if total > policy.max_chunks:
        problems.append(
            f"chunks: {total} exceed max_chunks {policy.max_chunks}"
            f" (unbounded loading)")
    if active > policy.max_active_chunks:
        problems.append(
            f"chunks: {active} active exceed"
            f" max_active_chunks {policy.max_active_chunks}")
    if world.constraints.get("only_active_zones_animate") is not True:
        problems.append("constraints: only_active_zones_animate must be true")
    if world.constraints.get("presentation_only") is not True:
        problems.append("constraints: projection must be presentation-only")
    if world.constraints.get("business_truth") is not False:
        problems.append("constraints: projection must not claim business truth")
    if world.summary.get("animated_chunks") != animated:
        problems.append("summary: animated_chunks disagrees with chunks")
    if world.summary.get("active_chunks") != active:
        problems.append("summary: active_chunks disagrees with chunks")

    # -- required places --------------------------------------------------
    kinds = {p.get("kind") for p in world.places}
    kinds.update(p.get("kind") for p in world.portals)
    for label, kind in REQUIRED_PLACES:
        if kind not in kinds:
            problems.append(f"places: {label!r} ({kind}) is not presented")

    # -- portals to the tower lobby --------------------------------------
    lobby_ids = {z["zone_id"] for z in world.zones if z.get("kind") == "lobby"}
    if not lobby_ids:
        problems.append("zones: no lobby zone")
    floor_ids = {f["floor_id"] for f in world.floor_links}
    if not any(f.get("kind") == "elevator" for f in world.floor_links):
        problems.append("floor_links: no elevator to a tower floor")
    if not any(f.get("kind") == "stairs" for f in world.floor_links):
        problems.append("floor_links: no stairs to a tower floor")
    for link in world.floor_links:
        if not _floor_id_ok(str(link.get("floor_id") or "")):
            problems.append(
                f"floor_links {link.get('portal_id')!r}: {link.get('floor_id')!r}"
                f" is not a tower floor id")
    if not any(link.get("floor_id") == "floor-001" for link in world.floor_links):
        problems.append("floor_links: the lobby must reach floor-001")
    if floor_ids and not lobby_ids:
        problems.append("portals: floor links exist without a lobby zone")
    lobby_chunks = {
        c["chunk_id"] for c in world.chunks
        if zone_state.get(c.get("zone_id"), {}).get("kind") == "lobby"
    }
    egress_out = 0
    for portal in world.portals:
        if portal.get("kind") != "egress":
            continue
        if portal.get("from_chunk") not in lobby_chunks:
            continue
        target_zone = next(
            (c["zone_id"] for c in world.chunks
             if c["chunk_id"] == portal.get("to_chunk")), None)
        if target_zone and target_zone not in lobby_ids:
            egress_out += 1
    if egress_out < 2:
        problems.append(
            "lobby: needs entrance and outside egress links")

    # -- art policy -------------------------------------------------------
    for asset in world.assets:
        where = f"asset {asset.get('asset_id')!r}"
        if asset.get("paid"):
            if not asset.get("local_only"):
                problems.append(f"{where}: paid art must stay local-only")
            if asset.get("redistribution_allowed"):
                problems.append(f"{where}: paid art must not be redistributable")
            path = _asset_local_path(world_map, str(asset.get("asset_id")))
            if path is not None:
                if not path.is_absolute():
                    problems.append(f"{where}: local path must be absolute")
                elif _inside(path, root):
                    problems.append(
                        f"{where}: local-only art path is inside the public"
                        f" tree")
        if str(asset.get("credit") or "").strip() != world_map.credit.strip():
            problems.append(f"{where}: credit does not match the map credit")

    # -- no business truth -----------------------------------------------
    forbidden = sorted(
        {k for k in _walk_keys(world.to_dict()) if k in FORBIDDEN_TRUTH_KEYS})
    if forbidden:
        problems.append(
            f"presentation: forbidden business-truth keys {forbidden}")

    return problems


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _floor_id_ok(value: str) -> bool:
    if len(value) != 9 or not value.startswith("floor-"):
        return False
    return value[6:].isdigit()


def _asset_local_path(map_obj: _exterior.ExteriorMap,
                      asset_id: str) -> Optional[Path]:
    for asset in map_obj.assets:
        if asset.asset_id == asset_id and asset.local_path:
            return Path(asset.local_path)
    return None
