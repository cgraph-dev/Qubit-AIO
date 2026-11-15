"""The extended LimeZu asset catalog, built locally and never committed.

WHAT THIS PINS
--------------
The owner's purchase carries more than six floor tiles and character recipes:
interior furniture, animated objects, UI sheets and outside/world tiles. These
tests build a synthetic purchase in ``tmp_path`` (no paid art, no network) that
mirrors the real archive layout, then pin the contract:

  * ``extract_assets`` catalogs characters, furniture, animated objects, UI and
    outside assets with source sheet, frame geometry and sprite ids;
  * animated frame timing comes from the matching GIF preview when present, and
    is honestly marked ``inferred`` when it is not;
  * a missing pack is skipped, while a present-but-invalid pack is a typed
    ``ThemeBuildError`` naming the file;
  * the built pack carries per-category indexes whose file map and hashes agree
    with ``theme.json`` and with the real pixels on disk;
  * the shareable recipe carries counts/kinds only - no files, index paths,
    sheet names or hashes - and a smuggled file map is refused;
  * extraction writes nothing into the purchase tree.
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
PERSONAS = ["alpha", "bravo"]


# ----------------------------------------------------------------- helpers
def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _solid(width: int, height: int, colour=(80, 120, 160, 255)) -> Image.Image:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle(
        [width // 5, height // 6, width - width // 5 - 1, height - height // 6 - 1],
        fill=colour)
    return image


def _gif(width: int, height: int, frames: int, duration: int = 100) -> bytes:
    images = [_solid(width, height, (20 + index, 90, 140, 255))
              for index in range(frames)]
    buffer = io.BytesIO()
    images[0].save(buffer, format="GIF", save_all=True,
                   append_images=images[1:], duration=duration, loop=0)
    return buffer.getvalue()


def _zip(path: Path, members: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def _character_members() -> dict[str, bytes]:
    base = "Character Generator 2.0 Linux Build/Character Pieces"
    tier = f"{SIZE}x{SIZE}"
    return {
        f"{base}/Bodies/{tier}/Body_01.png": _png(_solid(32, 64)),
        f"{base}/Outfits/{tier}/Outfit_01_01.png": _png(_solid(32, 64, (190, 100, 90, 255))),
        f"{base}/Eyes/{tier}/Eyes_01.png": _png(_solid(32, 64, (60, 70, 80, 255))),
        f"{base}/Hairstyles/{tier}/Hairstyle_01_01.png": _png(_solid(32, 64, (120, 80, 50, 255))),
        f"{base}/Accessories/{tier}/Accessory_01_Ladybug_01.png": _png(_solid(32, 64, (200, 40, 40, 255))),
    }


def _portrait_members() -> dict[str, bytes]:
    base = (f"Portrait Generator 1.5.0 Linux Build/Portrait Pieces/"
            f"Portrait_Generator - {SIZE}x{SIZE}")
    return {
        f"{base}/Skins/Skin 1.png": _png(_solid(32, 32)),
        f"{base}/Eyes/Eyes 01.png": _png(_solid(32, 32, (60, 70, 80, 255))),
        f"{base}/Hairstyles/Hairstyle 01 1.png": _png(_solid(32, 32, (120, 80, 50, 255))),
        f"{base}/Accessories/Accessory 01 Ladybug 1.png": _png(_solid(32, 32, (200, 40, 40, 255))),
    }


def _interior_members() -> dict[str, bytes]:
    tier = f"{SIZE}x{SIZE}"
    return {
        f"1_Interiors/{tier}/Theme_Sorter_Singles/2_Living_Room_Singles/"
        "Living_Room_Singles_1.png": _png(_solid(SIZE, SIZE)),
        f"1_Interiors/{tier}/Theme_Sorter_Singles/2_Living_Room_Singles/"
        "Living_Room_Singles_2.png": _png(_solid(SIZE, SIZE * 2)),
        f"1_Interiors/{tier}/Interiors_{tier}.png": _png(_solid(32, 32)),
        f"1_Interiors/{tier}/Room_Builder_{tier}.png": _png(_solid(32, 32)),
        f"1_Interiors/{tier}/Theme_Sorter/2_LivingRoom_{tier}.png": _png(_solid(32, 32)),
        f"3_Animated_objects/{tier}/spritesheets/animated_candle.png": _png(_solid(48, 32)),
        f"3_Animated_objects/{tier}/gif/animated_candle.gif": _gif(16, 32, 3),
        f"3_Animated_objects/{tier}/spritesheets/animated_no_preview.png": _png(_solid(32, 16)),
        "4_User_Interface_Elements/UI_16x16.png": _png(_solid(32, 32)),
        "4_User_Interface_Elements/UI_thinking_emotes_animation_16x16.png": _png(_solid(32, 16)),
    }


def _ui_members() -> dict[str, bytes]:
    members = {}
    for tier in (16, 32, 48):
        suffix = "" if tier == 16 else f"_{tier}x{tier}"
        members[f"{tier}x{tier}/Modern_UI_Style_1{suffix}.png"] = _png(_solid(tier * 2, tier * 2))
        members[f"{tier}x{tier}/Modern_UI_Gamepad{suffix}.png"] = _png(_solid(tier * 2, tier * 2))
    return members


def _outside_members() -> dict[str, bytes]:
    return {
        "SERENE_VILLAGE_REVAMPED/Serene_Village_16x16.png": _png(_solid(32, 32)),
        "SERENE_VILLAGE_REVAMPED/Animated stuff/campfire_16x16.png": _png(_solid(64, 16)),
        "SERENE_VILLAGE_REVAMPED/Animated stuff/campfire_16x16.gif": _gif(16, 16, 4),
        "SERENE_VILLAGE_REVAMPED/RPG_MAKER_MV/Outside_Stuff_TILESET_B-C-D-E.png": _png(_solid(96, 96)),
    }


def _purchase(tmp_path: Path) -> Path:
    root = tmp_path / "UI Office"
    root.mkdir()
    _zip(root / "Character Generator 2.0 Linux Build.zip", _character_members())
    _zip(root / "Portrait_Generator_1.5.0_Linux_Build.zip", _portrait_members())
    _zip(root / "moderninteriors-win.zip", _interior_members())
    _zip(root / "modernuserinterface-win.zip", _ui_members())
    _zip(root / "Serene_Village_revamped_v1.9.zip", _outside_members())
    return root


def _body_sheet(rows: int = 4) -> Image.Image:
    frame = _solid(SIZE, SIZE * 2)
    sheet = Image.new("RGBA", (SIZE, rows * SIZE * 2), (0, 0, 0, 0))
    for row in (0, 3):
        sheet.paste(frame, (0, row * SIZE * 2))
    return sheet


def _portrait() -> Image.Image:
    return _solid(SIZE * 2, SIZE * 2)


def _layers() -> dict:
    return {
        "skin": [{"portrait": _portrait()}],
        "eyes": [{"portrait": _portrait(), "body": _body_sheet()}],
        "hair": [{"portrait": _portrait(), "body": _body_sheet()}],
        "outfit": [{"body": _body_sheet()}],
        "body": [{"body": _body_sheet()}],
    }


def _catalog(root: Path) -> dict:
    return E.extract_assets(str(root), size=SIZE)


def _write_tree(root: Path) -> dict[str, tuple[int, int]]:
    return {
        path.relative_to(root).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*") if path.is_file()
    }


# ------------------------------------------------------------ extraction
def test_extract_assets_catalogs_every_category(tmp_path):
    catalog = _catalog(_purchase(tmp_path))
    categories = catalog["categories"]
    assert set(categories) == {"animated", "characters", "furniture", "outside", "ui"}

    animated = categories["animated"]["entries"]
    candle = next(entry for entry in animated if entry["id"] == "animated-animated-candle")
    assert candle["frame"] == {"w": 16, "h": 32, "count": 3, "fps": 10.0}
    assert candle["frame_source"] == "gif"
    no_preview = next(entry for entry in animated
                      if entry["id"] == "animated-animated-no-preview")
    assert no_preview["frame"]["count"] == 2
    assert no_preview["frame_source"] == "inferred"

    furniture = categories["furniture"]["entries"]
    assert sum(1 for entry in furniture if entry["kind"] == "sprite") == 2
    singles = [entry for entry in furniture if entry["kind"] == "sprite"]
    assert {entry["frame"]["count"] for entry in singles} == {1}

    ui = categories["ui"]["entries"]
    style = next(entry for entry in ui if entry["kind"] == "nine_slice_sheet")
    assert style["nine_slice_candidate"] is True
    assert style["grid"]["cell"] == 16
    button = next(entry for entry in ui if entry["kind"] == "button_icon_sheet")
    assert button["nine_slice_candidate"] is False

    outside = categories["outside"]["entries"]
    campfire = next(entry for entry in outside if entry["id"] == "outside-campfire-16x16")
    assert campfire["frame"] == {"w": 16, "h": 16, "count": 4, "fps": 10.0}
    assert campfire["license"].startswith("CC-BY 4.0")
    assert "https://limezu.itch.io" in campfire["credit"]

    characters = categories["characters"]["entries"]
    kinds = {entry["view"] for entry in characters}
    assert kinds == {"body", "portrait"}
    assert any("accessory" in entry["id"] for entry in characters)


def test_extract_assets_is_reproducible(tmp_path):
    root = _purchase(tmp_path)
    first = E.summarize_assets(_catalog(root))
    second = E.summarize_assets(_catalog(root))
    assert first == second
    assert first["digest"] == second["digest"]


def test_extract_assets_skips_missing_packs(tmp_path):
    root = tmp_path / "partial"
    root.mkdir()
    _zip(root / "Character Generator 2.0 Linux Build.zip", _character_members())
    _zip(root / "Portrait_Generator_1.5.0_Linux_Build.zip", _portrait_members())
    catalog = E.extract_assets(str(root), size=SIZE)
    assert set(catalog["categories"]) == {"characters"}


def test_present_but_corrupt_pack_is_a_typed_error(tmp_path):
    root = _purchase(tmp_path)
    (root / "moderninteriors-win.zip").write_bytes(b"not a zip")
    with pytest.raises(B.ThemeBuildError) as info:
        E.extract_assets(str(root), size=SIZE)
    assert "moderninteriors-win.zip" in str(info.value)
    assert "not a valid zip" in str(info.value)


def test_unknown_asset_category_is_refused(tmp_path):
    with pytest.raises(B.ThemeBuildError) as info:
        E.extract_assets(str(_purchase(tmp_path)), size=SIZE,
                         categories=["characters", "lava"])
    assert "lava" in str(info.value)


def test_extraction_writes_nothing_into_the_purchase(tmp_path):
    root = _purchase(tmp_path)
    before = _write_tree(root)
    E.extract_assets(str(root), size=SIZE)
    assert _write_tree(root) == before


# ---------------------------------------------------------------- build
def _build(out: Path, **kwargs) -> dict:
    return B.build_theme(B.DEFAULT_SOURCE, out, size=SIZE,
                         personas=PERSONAS, layers=_layers(), **kwargs)


def test_build_with_assets_validates_and_indexes(tmp_path):
    catalog = _catalog(_purchase(tmp_path))
    out = tmp_path / "pack"
    manifest = _build(out, assets=catalog)

    assert T.validate_theme(out) == []
    assert set(manifest["assets"]["categories"]) == set(catalog["categories"])
    declared = manifest["files"]
    for category, config in manifest["assets"]["categories"].items():
        index_path = out / config["index"]
        index = json.loads(index_path.read_text(encoding="utf-8"))
        assert index["category"] == category
        assert index["count"] == config["count"] == len(index["sprites"])
        for entry in index["sprites"]:
            assert entry["file"] in declared, entry["file"]
            on_disk = out / entry["file"]
            assert on_disk.is_file()
            assert declared[entry["file"]]["sha256"] == entry["source_sha256"]
            with Image.open(on_disk) as image:
                assert entry["sha256"] == B._pixel_digest(image)
            assert entry["sheet"] and entry["sheet"].startswith((
                "Character Generator", "Portrait Generator", "1_Interiors",
                "3_Animated_objects", "4_User_Interface_Elements", "16x16",
                "32x32", "48x48", "Serene_Village", "Animated stuff",
                "RPG_MAKER_MV"))


def test_build_with_assets_is_reproducible(tmp_path):
    catalog = _catalog(_purchase(tmp_path))
    first = tmp_path / "a" / "pack"
    second = tmp_path / "b" / "pack"
    _build(first, assets=catalog)
    _build(second, assets=catalog)
    assert B.compare_packs(first, second) == []


def test_build_rejects_an_invalid_asset_entry(tmp_path):
    catalog = _catalog(_purchase(tmp_path))
    catalog["categories"]["ui"]["entries"][0]["kind"] = "not-a-kind"
    with pytest.raises(B.ThemeBuildError) as info:
        _build(tmp_path / "pack", assets=catalog)
    assert "unknown kind" in str(info.value)


def test_build_without_assets_is_unchanged(tmp_path):
    manifest = _build(tmp_path / "pack")
    assert "assets" not in manifest
    assert manifest["limits"] == {"max_total_bytes": 64 * 1024 * 1024,
                                  "max_files": 32}
    assert T.validate_theme(tmp_path / "pack") == []


# --------------------------------------------------------------- recipe
def test_recipe_carries_asset_metadata_only(tmp_path):
    catalog = _catalog(_purchase(tmp_path))
    recipe_path = tmp_path / "limezu" / "theme.recipe.json"
    recipe = B.build_recipe(B.DEFAULT_SOURCE, recipe_path, size=SIZE,
                            personas=PERSONAS, layers=_layers(), assets=catalog)

    assert T.validate_recipe(recipe) == []
    assert recipe["assets"] == {
        "schema": 1,
        "categories": {
            category: {
                "count": len(catalog["categories"][category]["entries"]),
                "kinds": recipe["assets"]["categories"][category]["kinds"],
            }
            for category in sorted(catalog["categories"])
        },
    }
    assert "files" not in recipe
    assert "portraits" not in recipe and "bodies" not in recipe
    text = json.dumps(recipe)
    assert ".png" not in text
    assert "index.json" not in text
    assert "sha256" not in text
    assert "source_sha256" not in text
    # Only the recipe was written.
    assert sorted(path.name for path in recipe_path.parent.iterdir()) == [
        "theme.recipe.json"]


def test_recipe_assets_file_map_is_refused():
    recipe = {
        "format": T.FORMAT,
        "schema": T.SCHEMA,
        "id": "demo",
        "label": "Demo",
        "version": "1.0.0",
        "credit": "LimeZu — https://limezu.itch.io",
        "license": "LimeZu licence; redistribution prohibited",
        "source": "owner purchase",
        "template_version": 1,
        "tokens": {
            "color-surface": "#0d0d1a",
            "color-surface-raised": "#161a23",
            "color-surface-hover": "#1d2230",
            "color-text-primary": "#e9edf7",
            "color-text-muted": "#8a93a8",
            "color-border": "#64748b",
            "color-accent": "#7cc8ff",
            "radius-control": "4px",
        },
        "slices": {},
    }
    for smuggled in (
        {"index": "assets/furniture/index.json"},
        {"sha256": "a" * 64},
        {"file": "assets/furniture/abc.png"},
        {"count": 3, "index": "assets/ui/index.json"},
    ):
        bad = dict(recipe)
        bad["assets"] = {"schema": 1,
                         "categories": {"furniture": smuggled}}
        assert T.validate_recipe(bad), smuggled


def test_recipe_export_from_a_pack_is_metadata_only(tmp_path):
    catalog = _catalog(_purchase(tmp_path))
    out = tmp_path / "pack"
    manifest = _build(out, assets=catalog)
    recipe = theme_cli._recipe_from_pack(out, manifest)
    assert T.validate_recipe(recipe) == []
    assert "files" not in recipe and "portraits" not in recipe
    assert recipe["assets"]["categories"]["furniture"]["count"] == (
        manifest["assets"]["categories"]["furniture"]["count"])
    assert "index" not in json.dumps(recipe["assets"])


def test_committed_recipe_and_theme_dir_contain_no_paid_art():
    """The repo ships metadata only: no atlases, sprites or file map."""
    theme_dir = (REPO / "frontend/qubit-web/public/plugins/qubit-office/themes")
    recipe = json.loads(
        (theme_dir / "limezu" / "theme.recipe.json").read_text(encoding="utf-8"))
    assert T.validate_recipe(recipe) == []
    assert "files" not in recipe
    assert "portraits" not in recipe and "bodies" not in recipe
    assert "index" not in json.dumps(recipe.get("assets", {}))
    assert "sha256" not in json.dumps(recipe.get("assets", {}))
    # The only files under the theme directory are metadata.
    files = [path for path in theme_dir.rglob("*") if path.is_file()]
    assert files
    for path in files:
        assert path.suffix.lower() in {".json", ".txt"}, path


# ------------------------------------------------------------------ CLI
def test_cli_assets_summary_is_reproducible(tmp_path, capsys):
    root = _purchase(tmp_path)
    assert E.main(["--assets", str(root), "--size", str(SIZE), "--check"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["checked"] is True
    assert set(payload["categories"]) == {
        "animated", "characters", "furniture", "outside", "ui"}


def test_cli_build_and_check_include_assets(tmp_path, monkeypatch, capsys):
    root = _purchase(tmp_path)
    monkeypatch.setattr(B, "extract_layers", lambda source, size=32: _layers())
    monkeypatch.setattr(B, "_default_personas", lambda: list(PERSONAS))
    out = tmp_path / "pack"

    assert B.main(["--source", str(root), "--out", str(out),
                   "--size", str(SIZE)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["assets"]) == {
        "animated", "characters", "furniture", "outside", "ui"}
    manifest = json.loads((out / "theme.json").read_text(encoding="utf-8"))
    assert "assets" in manifest
    assert B.main(["--source", str(root), "--out", str(out),
                   "--size", str(SIZE), "--check"]) == 0

    recipe_path = tmp_path / "limezu" / "theme.recipe.json"
    assert B.main(["--source", str(root), "--recipe-out", str(recipe_path),
                   "--size", str(SIZE)]) == 0
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    assert "assets" in recipe and "files" not in recipe


def test_cli_no_assets_keeps_the_old_pack(tmp_path, monkeypatch):
    root = _purchase(tmp_path)
    monkeypatch.setattr(B, "extract_layers", lambda source, size=32: _layers())
    monkeypatch.setattr(B, "_default_personas", lambda: list(PERSONAS))
    out = tmp_path / "pack"
    assert B.main(["--source", str(root), "--out", str(out),
                   "--size", str(SIZE), "--no-assets"]) == 0
    manifest = json.loads((out / "theme.json").read_text(encoding="utf-8"))
    assert "assets" not in manifest
