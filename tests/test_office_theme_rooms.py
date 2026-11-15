"""Measured room blueprints and synchronized character animation metadata.

WHAT THIS PINS
--------------
The extended local pack carries more than six floor tiles, fourteen furniture
kinds and one static character frame. These hermetic tests build a synthetic
purchase (no paid art, no network) that mirrors the real LimeZu layout and pin:

  * eight real room archetypes with 16px floor/wall/door/window references and
    Theme_Sorter_Singles placements whose footprints are measured, not guessed;
  * body animation rows/frame counts from the Character Generator 2.0
    ``AnimationSO`` data (idle=24, walk=24, sit=12, phone=12, read=12);
  * one shared semantic recipe per agent for body + portrait part indices;
  * metadata-only recipe blocks (no files, indexes, sheets or coordinates);
  * missing and corrupt sources are typed failures, never silent empty art.
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import office_theme_build as B  # noqa: E402
import office_theme_extract as E  # noqa: E402
from voicebridge.office import theme as T  # noqa: E402
from voicebridge.office import theme_cli  # noqa: E402

pytestmark = pytest.mark.unit

SIZE = 16
PERSONAS = ["alpha", "bravo", "charlie"]


# ----------------------------------------------------------------- helpers
def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _solid(width: int, height: int) -> Image.Image:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle(
        [1, 1, width - 2, height - 2], fill=(80, 120, 160, 255))
    return image


def _zip(path: Path, members: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def _character_members() -> dict[str, bytes]:
    base = "Character Generator 2.0 Linux Build/Character Pieces"
    tier = f"{SIZE}x{SIZE}"
    frame = _png(_solid(SIZE, SIZE * 2))
    wide = _png(_solid(SIZE * 24, SIZE * 2 * 8))
    return {
        f"{base}/Bodies/{tier}/Body_01.png": wide,
        f"{base}/Outfits/{tier}/Outfit_01_01.png": wide,
        f"{base}/Eyes/{tier}/Eyes_01.png": wide,
        f"{base}/Hairstyles/{tier}/Hairstyle_01_01.png": wide,
        f"{base}/Accessories/{tier}/Accessory_01_Ladybug_01.png": frame,
    }


def _portrait_members() -> dict[str, bytes]:
    base = (f"Portrait Generator 1.5.0 Linux Build/Portrait Pieces/"
            f"Portrait_Generator - {SIZE}x{SIZE}")
    frame = _png(_solid(SIZE * 2, SIZE * 2))
    return {
        f"{base}/Skins/Skin 1.png": _png(_solid(320, 96)),
        f"{base}/Eyes/Eyes 01.png": _png(_solid(320, 96)),
        f"{base}/Hairstyles/Hairstyle 01 1.png": _png(_solid(320, 96)),
        f"{base}/Accessories/Accessory 01 Ladybug 1.png": _png(_solid(320, 96)),
    }


def _interior_members(*, include_rooms: bool = True) -> dict[str, bytes]:
    tier = f"{SIZE}x{SIZE}"
    members: dict[str, bytes] = {
        f"1_Interiors/{tier}/Interiors_{tier}.png": _png(_solid(32, 32)),
        f"1_Interiors/{tier}/Room_Builder_{tier}.png": _png(_solid(32, 32)),
        f"1_Interiors/{tier}/Theme_Sorter/1_Generic_{tier}.png": _png(_solid(32, 32)),
    }
    if include_rooms:
        members.update({
            E.ROOM_FLOOR_SHEET: _png(_solid(240, 640)),
            E.ROOM_WALL_SHEET: _png(_solid(512, 640)),
            E.ROOM_DOOR_SHEET: _png(_solid(160, 520)),
            E.ROOM_WINDOW_SHEET: _png(_solid(384, 944)),
        })
        for kind, entry in E.ROOM_FURNITURE.items():
            width, height = ((32, 32) if kind in ("table", "sofa", "art")
                             else (16, 32))
            members[
                f"1_Interiors/{tier}/Theme_Sorter_Singles/{entry['path']}"
            ] = _png(_solid(width, height))
    return members


def _purchase(tmp_path: Path, *, include_rooms: bool = True) -> Path:
    root = tmp_path / "UI Office"
    root.mkdir()
    _zip(root / "Character Generator 2.0 Linux Build.zip", _character_members())
    _zip(root / "Portrait_Generator_1.5.0_Linux_Build.zip", _portrait_members())
    _zip(root / "moderninteriors-win.zip",
         _interior_members(include_rooms=include_rooms))
    return root


def _layers() -> dict:
    body = {"body": _solid(SIZE * 24, SIZE * 2 * 8)}
    portrait = {"portrait": _solid(SIZE * 2, SIZE * 2)}
    return {
        "skin": [portrait],
        "eyes": [dict(portrait, **body)],
        "hair": [dict(portrait, **body)],
        "outfit": [body],
        "body": [body],
    }


def _build_catalog(root: Path) -> dict:
    return E.extract_assets(str(root), size=SIZE)


def _valid_room(room: dict) -> dict:
    return room


def _valid_animations() -> dict:
    return {
        "schema": "qubit-office-character-animations/1",
        "cell": {"w": SIZE, "h": SIZE * 2},
        "directions": list(E.ANIMATION_DIRECTIONS),
        "office_states": dict(E.OFFICE_ANIMATION_STATES),
        "states": {
            name: {
                "row": int(spec["row"]),
                "frames": int(spec["frames"]),
                "frames_per_direction": int(spec["frames_per_direction"]),
                "directions": list(E.ANIMATION_DIRECTIONS),
                "fps": spec["fps"],
                "loop": spec["loop"],
            }
            for name, spec in E.ANIMATION_ROWS.items()
        },
        "portrait": {
            "cell": {"w": SIZE * 2, "h": SIZE * 2},
            "cols": int(E.PORTRAIT_GRID["cols"]),
            "rows": int(E.PORTRAIT_GRID["rows"]),
            "directions": {
                name: dict(frame)
                for name, frame in E.PORTRAIT_GRID["directions"].items()
            },
            "part_mapping": dict(E.PORTRAIT_GRID["part_mapping"]),
        },
    }


# ------------------------------------------------------- room extraction
def test_room_extraction_has_eight_measured_archetypes(tmp_path):
    rooms = E.extract_room_blueprints(str(_purchase(tmp_path)))
    assert rooms["archetypes"] == list(E.ROOM_BLUEPRINTS)
    assert len(rooms["rooms"]) == 8
    assert {doc["archetype"] for doc in rooms["rooms"]} == set(T.ROOM_ARCHETYPES)
    assert E.summarize_rooms(rooms)["placements"] == sum(
        len(doc["placements"]) for doc in rooms["rooms"])

    for document in rooms["rooms"]:
        checkable = json.loads(json.dumps(
            {k: v for k, v in document.items() if k != "placements"}))
        checkable["placements"] = []
        for placement in document["placements"]:
            row = {k: v for k, v in placement.items() if k != "blob"}
            row["file"] = "rooms/sprites/placeholder.png"
            checkable["placements"].append(row)
        assert T._validate_room_document(checkable) == []
        size = document["size"]
        assert document["floor"]["sheet"] == E.ROOM_FLOOR_SHEET
        floor = document["floor"]["tile"]
        assert floor == dict(zip(("col", "row"),
                                 E.FLOOR_MAP[document["floor"]["kind"]]))
        assert any(item["kind"] == "door" for item in document["openings"])
        assert sum(item["kind"] == "window" for item in document["openings"]) == 2
        seen = set()
        for placement in document["placements"]:
            assert placement["id"] not in seen
            seen.add(placement["id"])
            assert placement["anchor"] == "bottom-center"
            assert placement["z"] > 0
            x, y = placement["tile"]["x"], placement["tile"]["y"]
            w, h = placement["footprint"]["w"], placement["footprint"]["h"]
            assert 0 <= x and 0 <= y
            assert x + w <= size["w"] and y + h <= size["h"]
            assert placement["sprite"].startswith("furniture-single-")
            assert isinstance(placement["blob"], (bytes, bytearray))


def test_room_footprints_match_the_real_sprite_pixels(tmp_path):
    root = _purchase(tmp_path)
    rooms = E.extract_room_blueprints(str(root))
    expectations = {"table": (2, 2), "desk": (1, 2), "sofa": (2, 2),
                    "art": (2, 2)}
    for document in rooms["rooms"]:
        for placement in document["placements"]:
            kind = placement["kind"]
            if kind not in expectations:
                continue
            assert (placement["footprint"]["w"],
                    placement["footprint"]["h"]) == expectations[kind]


def test_missing_room_sheets_and_sprites_are_typed(tmp_path):
    root = tmp_path / "UI Office"
    root.mkdir()
    _zip(root / "moderninteriors-win.zip",
         {k: v for k, v in _interior_members().items()
          if k != E.ROOM_WINDOW_SHEET})
    with pytest.raises(E.ThemeBuildError) as exc:
        E.extract_room_blueprints(str(root))
    assert any("missing" in problem and "3d_walls" in problem
               for problem in exc.value.problems)

    root2 = tmp_path / "UI Office 2"
    root2.mkdir()
    missing = E.ROOM_FURNITURE["sofa"]["path"]
    _zip(root2 / "moderninteriors-win.zip",
         {k: v for k, v in _interior_members().items()
          if not k.endswith(missing)})
    with pytest.raises(E.ThemeBuildError) as exc2:
        E.extract_room_blueprints(str(root2))
    assert any("furniture sprite missing" in problem
               for problem in exc2.value.problems)


def test_absent_or_corrupt_interior_pack_is_honest(tmp_path):
    absent = tmp_path / "empty"
    absent.mkdir()
    assert E.extract_room_blueprints(str(absent)) == {
        "schema": 1, "archetypes": [], "rooms": []}

    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    (corrupt / "moderninteriors-win.zip").write_bytes(b"not a zip")
    with pytest.raises(E.ThemeBuildError):
        E.extract_room_blueprints(str(corrupt))


# -------------------------------------------------- animation extraction
def test_animation_rows_frames_and_portrait_grid(tmp_path):
    animations = E.extract_character_animations(
        str(_purchase(tmp_path)), size=SIZE)
    assert animations["cell"] == {"w": SIZE, "h": SIZE * 2}
    assert animations["directions"] == list(E.ANIMATION_DIRECTIONS)
    assert animations["states"]["idle"]["frames"] == 24
    assert animations["states"]["idle"]["frames_per_direction"] == 6
    assert animations["states"]["walk"]["row"] == 2
    assert animations["states"]["sit"]["frames"] == 12
    assert animations["states"]["phone"]["row"] == 6
    assert animations["states"]["read"]["frames"] == 12
    assert animations["office_states"]["type"] == "sit"
    assert animations["office_states"]["talk"] == "phone"
    assert animations["portrait"]["cols"] == 10
    assert animations["portrait"]["rows"] == 3
    assert animations["portrait"]["directions"]["front"] == {"row": 0, "col": 0}
    assert animations["portrait"]["part_mapping"]["skin"] == "skin"
    assert T._validate_animations_document(animations) == []
    assert E.summarize_animations(animations)["frame_counts"]["walk"] == 24


def test_animation_catalog_is_none_without_a_character_pack(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    assert B._animation_catalog(str(root), SIZE) is None
    with pytest.raises(E.ThemeBuildError):
        E.extract_character_animations(str(root), size=SIZE)


# ------------------------------------------------------- schema validation
def _room() -> dict:
    return {
        "schema": "qubit-office-room/1",
        "id": "executive",
        "label": "Executive boardroom",
        "archetype": "executive",
        "cell": 16,
        "size": {"w": 10, "h": 8},
        "floor": {"kind": "wood", "sheet": "Room_Builder_Floors_16x16.png",
                  "tile": {"col": 0, "row": 13}},
        "walls": [{"edge": "north", "x": 0, "y": 0, "w": 10, "h": 1,
                   "sheet": "Room_Builder_Walls_16x16.png",
                   "tile": {"col": 0, "row": 2}}],
        "openings": [{"kind": "door", "layer": "door", "x": 4, "y": 4,
                      "w": 2, "h": 4,
                      "sheet": "Room_Builder_Arched_Entryways_16x16.png",
                      "tile": {"col": 0, "row": 0}}],
        "placements": [{
            "id": "desk-1-1", "kind": "desk", "layer": "furniture",
            "sprite": "furniture-single-classroom-and-library-singles-6",
            "file": "rooms/sprites/desk.png",
            "tile": {"x": 1, "y": 1}, "footprint": {"w": 1, "h": 2},
            "anchor": "bottom-center", "z": 20,
        }],
    }


def test_room_schema_rejects_unknown_and_out_of_bounds():
    assert T._validate_room_document(_room()) == []

    bad = _room()
    bad["archetype"] = "spaceship"
    bad["placements"][0]["layer"] = "magic"
    bad["placements"][0]["anchor"] = "middle"
    problems = T._validate_room_document(bad)
    assert any("archetype" in problem for problem in problems)
    assert any("layer" in problem for problem in problems)
    assert any("anchor" in problem for problem in problems)

    outside = _room()
    outside["placements"][0]["tile"] = {"x": 9, "y": 7}
    assert any("exceeds the room" in problem
               for problem in T._validate_room_document(outside))

    duplicate = _room()
    duplicate["placements"].append(dict(duplicate["placements"][0]))
    assert any("duplicate id" in problem
               for problem in T._validate_room_document(duplicate))


def test_animation_schema_rejects_bad_frames_and_recipe_links():
    good = _valid_animations()
    assert T._validate_animations_document(good) == []

    bad = json.loads(json.dumps(good))
    bad["states"]["idle"]["frames"] = 23
    assert any("frames 23 !=" in problem
               for problem in T._validate_animations_document(bad))

    missing_office = json.loads(json.dumps(good))
    missing_office["office_states"]["talk"] = "typing"
    assert any("office_states.talk" in problem
               for problem in T._validate_animations_document(missing_office))

    bad_portrait = json.loads(json.dumps(good))
    bad_portrait["portrait"]["directions"]["front"]["row"] = 9
    assert any("out of bounds" in problem
               for problem in T._validate_animations_document(bad_portrait))


# --------------------------------------------------------- build + recipe
def test_build_pack_writes_rooms_and_shared_animation_recipes(tmp_path):
    root = _purchase(tmp_path)
    out = tmp_path / "pack" / "full-assets"
    rooms = E.extract_room_blueprints(str(root))
    animations = E.extract_character_animations(str(root), size=SIZE)
    manifest = B.build_theme(
        B.DEFAULT_SOURCE, out, size=SIZE,
        layers=B._normalize_layers(_layers()), personas=PERSONAS,
        assets=_build_catalog(root), rooms=rooms, animations=animations)

    assert manifest["rooms"]["count"] == 8
    assert manifest["animations"]["states"] == sorted(E.ANIMATION_ROWS)
    assert "rooms/manifest.json" in manifest["files"]
    assert "characters/animations.json" in manifest["files"]
    for room in rooms["rooms"]:
        assert f"rooms/{room['id']}.json" in manifest["files"]
    first_room = json.loads((out / "rooms" / "executive.json").read_text())
    for placement in first_room["placements"]:
        assert placement["file"].startswith("rooms/sprites/")
        assert (out / placement["file"]).is_file()

    document = json.loads((out / "characters/animations.json").read_text())
    assert set(document["recipes"]) == set(PERSONAS)
    for slug, recipe in document["recipes"].items():
        assert recipe["body"] == {"state": "idle", "direction": "down", "frame": 0}
        assert recipe["portrait"] == {"row": 0, "col": 0}
        assert set(("skin", "eyes", "hair", "outfit")) <= set(recipe)
    assert T.validate_theme(out) == []
    loaded = T.load_theme(out)
    assert loaded.rooms["count"] == 8
    assert loaded.animations["index"] == "characters/animations.json"

    exported = theme_cli._recipe_from_pack(out, manifest)
    assert exported["rooms"]["count"] == 8
    assert exported["animations"]["states"] == sorted(E.ANIMATION_ROWS)
    assert "index" not in json.dumps(exported["rooms"])
    assert "sheet" not in json.dumps(exported["animations"])
    assert T.validate_recipe(exported) == []


def test_recipe_blocks_are_metadata_only(tmp_path):
    root = _purchase(tmp_path)
    rooms = E.extract_room_blueprints(str(root))
    animations = E.extract_character_animations(str(root), size=SIZE)
    _payloads, _manifest, room_recipe = B._prepare_rooms(rooms, {})
    _payloads, _manifest, animation_recipe = B._prepare_animations(
        animations, {"alpha": {"skin": 0, "eyes": 0, "hair": 0, "outfit": 0}},
        size=SIZE)
    assert set(room_recipe) == {"schema", "count", "archetypes"}
    assert "file" not in json.dumps(room_recipe)
    assert "rooms/sprites" not in json.dumps(room_recipe)
    assert set(animation_recipe) == {
        "schema", "states", "directions", "cell", "portrait"}
    assert "index" not in json.dumps(animation_recipe)
    assert "sheet" not in json.dumps(animation_recipe)

    metadata = {"schema": 1, "rooms": room_recipe,
                "animations": animation_recipe}
    assert not T._validate_rooms_recipe(metadata["rooms"])
    assert not T._validate_animations_recipe(metadata["animations"])

    metadata["rooms"]["index"] = "rooms/manifest.json"
    assert T._validate_rooms_recipe(metadata["rooms"])
    metadata["animations"]["file"] = "characters/animations.json"
    assert T._validate_animations_recipe(metadata["animations"])


def test_recipe_out_keeps_layout_and_frames_out_of_the_repo(tmp_path):
    root = _purchase(tmp_path)
    recipe_path = tmp_path / "limezu" / "theme.recipe.json"
    recipe = B.build_recipe(
        B.DEFAULT_SOURCE, recipe_path, size=SIZE,
        layers=B._normalize_layers(_layers()), personas=PERSONAS,
        assets=_build_catalog(root),
        rooms=E.extract_room_blueprints(str(root)),
        animations=E.extract_character_animations(str(root), size=SIZE))
    text = recipe_path.read_text()
    assert '"rooms"' in text and '"animations"' in text
    assert "rooms/sprites" not in text
    assert "rooms/manifest.json" not in text
    assert '"row": 1' not in text  # animation rows stay in the local pack
    assert recipe["rooms"]["count"] == 8
    assert T.validate_recipe(recipe) == []
