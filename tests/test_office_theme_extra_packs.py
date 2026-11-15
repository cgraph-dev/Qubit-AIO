"""Extra purchased packs: UI atlases, icons, effects, tilesets and frames.

WHAT THIS PINS
--------------
The owner added eight sources beyond Modern Interiors. This test builds a
synthetic copy of their layout (no paid art, no network) and pins:

  * each new category is catalogued with stable ids, kinds, roles and measured
    frame geometry (GIF frames become deterministic PNG strips);
  * the Super Pixel Effects frame count/FPS come from the pack's own metadata
    and readme, not from a guessed strip length;
  * every entry carries the licence/credit read from that pack, and an
    unlicensed pack is marked ``unverified`` instead of claiming a licence;
  * the shareable recipe carries counts, kinds and pack provenance only;
  * missing packs are skipped, corrupt sources are typed, and a rebuild is
    byte-identical semantically.
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


def _png(width: int, height: int, colour=(80, 120, 160, 255)) -> bytes:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle(
        [1, 1, width - 2, height - 2], fill=colour)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _gif(width: int, height: int, frames: int, duration: int = 130) -> bytes:
    images = [Image.new("RGBA", (width, height), (20 + i, 90, 140, 255))
              for i in range(frames)]
    buffer = io.BytesIO()
    images[0].save(buffer, format="GIF", save_all=True,
                   append_images=images[1:], duration=duration, loop=0)
    return buffer.getvalue()


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _effects_metadata(path: Path, source: str, width: int, height: int,
                      frames: int) -> None:
    lines = [f"{source}/frame{index:04d}.png = {index * width} 0 {width} {height}"
             for index in range(frames)]
    _text(path, "\n".join(lines) + "\n")


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "UI Office"
    root.mkdir()

    dark = root / "DarkAgesUi_v1.0"
    _text(dark / "LICENSE.txt", "Created by Hypnobius\nNo resale.\n")
    _text(dark / "README.txt", "Dark Ages UI")
    _write(dark / "32x32-Tilesheet.png", _png(384, 352))

    dragon = root / "dragon-huds/dragon-huds"
    _write(dragon / "buttons/singleplayerbutton.png", _png(94, 31))
    _write(dragon / "buttons/exitbutton.png", _png(80, 30))
    _write(dragon / "main-menu/mainmenu-transparent.png", _png(688, 384))
    _text(root / "dragon-huds/readme.txt", "Created by Kibyra")

    medieval = root / "Free_Medieval_Fantasy_UI_Pack"
    _write(medieval / "UI Icons/icon1.png", _png(32, 32))
    _write(medieval / "UI Icons 2/icon1.png", _png(32, 32))
    _write(medieval / "icons3.png", _png(256, 256))
    _text(medieval / "Readme.txt", "Created by Kibyra")

    fonts = root / "uı-fonts/uı-fonts"
    _write(fonts / "uı-1/uı-1-grey/uı-1-grey/u-grey1.png", _png(32, 32))
    _write(fonts / "uı-1/uı-1-grey/uı-1-grey/u-grey2.png", _png(32, 32))
    _write(fonts / "uı-1/uı-1-brown.png", _png(256, 256))
    _write(fonts / "uı-logos/uı-logos.png", _png(256, 256))
    _text(root / "uı-fonts/readme.txt", "Created by Kibyra")

    icons = root / "Free - Raven Fantasy Icons/Free - Raven Fantasy Icons"
    for tier in ("16x16", "32x32"):
        for index in (1, 2, 3):
            _write(icons / "Separated Files" / tier / f"fa{index}.png",
                   _png(int(tier.split("x")[0]), int(tier.split("x")[0])))
    _write(icons / "Full Spritesheet/32x32.png", _png(512, 4384))
    _write(icons / "RPG Maker MV and MZ/IconSet.png", _png(512, 4384))
    _text(root / "Free - Raven Fantasy Icons/Special Note to the Dev.txt",
          "Thank you for your purchase! Clockwork Raven Studios")

    forest = root / "Raven Fantasy - Pixel Art Tileset - Green Forest v2"
    _write(forest / "All Tileset/16x16.png", _png(416, 240))
    _write(forest / "RPG Maker MV and MZ/RF_Green Forest_A1.png", _png(768, 576))
    _write(forest / "RPG Maker MV and MZ/!$Fire and Water.png", _png(432, 384))
    _text(forest / "A Note to the Dev.txt", "Clockwork Raven Studios")
    _text(forest / "Palette/Pallete.txt", "Palette by Kerrie Lake")

    effects = (root / "Super Pixel Effects Gigapack (Free Version) v3.2.0" /
               "Super Pixel Effects Gigapack (Free Version)")
    _text(effects / "license.txt", "Will Tice / unTied Games")
    _text(effects / "readme.txt", "FPS: 15 (66.7 ms/frame)")
    sheet_dir = effects / "spritesheet/Explosions/epic_explosion_001"
    for variant, width, height, frames in (
            ("large_orange", 128, 128, 3),
            ("small_orange", 64, 64, 3)):
        folder = sheet_dir / f"epic_explosion_001_{variant}"
        _write(folder / "spritesheet.png", _png(width * frames, height))
        source = (f"PNG/Explosions/epic_explosion_001/"
                  f"epic_explosion_001_{variant}")
        _effects_metadata(folder / "spritesheet.txt", source, width, height,
                          frames)

    frames = tmp_path / "NEW FRAMES"
    _write(frames / "pixellab-test.gif", _gif(256, 256, 9, 130))
    return root


def _figure(width: int, height: int, colour) -> Image.Image:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle(
        [1, 1, width - 2, height - 2], fill=colour)
    return image


def _layers() -> dict:
    body = {"body": _figure(SIZE * 4, SIZE * 8, (60, 80, 100, 255))}
    portrait = {"portrait": _figure(SIZE * 2, SIZE * 2, (120, 90, 70, 255))}
    return {
        "skin": [portrait],
        "eyes": [dict(portrait, **body)],
        "hair": [dict(portrait, **body)],
        "outfit": [body],
        "body": [body],
    }


# ------------------------------------------------------------- extraction
def test_new_categories_are_catalogued_with_roles_and_geometry(tmp_path):
    root = _source(tmp_path)
    catalog = E.extract_assets(str(root), size=SIZE)
    categories = catalog["categories"]

    assert set(categories) == {
        "ui_extra", "icons", "effects", "tilesets", "frames"}
    ui_extra = categories["ui_extra"]["entries"]
    assert any(entry["role"] == "ui_atlas" for entry in ui_extra)
    assert any(entry["role"] == "button" for entry in ui_extra)
    assert any(entry["role"] == "font_glyph" for entry in ui_extra)
    assert any(entry["role"] == "font_atlas" for entry in ui_extra)
    assert all(entry["pack"] for entry in ui_extra)

    icons = categories["icons"]["entries"]
    assert len(icons) == 6 + 2  # 3 per 16/32 tier + two sheets
    assert any(entry["kind"] == "sheet" for entry in icons)
    assert any(entry["tier"] == "16x16" for entry in icons)

    effects = categories["effects"]["entries"]
    assert len(effects) == 2
    large = next(entry for entry in effects if "large" in entry["id"])
    assert large["kind"] == "animated"
    assert large["frame"] == {"w": 128, "h": 128, "count": 3, "fps": 15.0}
    assert large["role"] == "effect"

    tilesets = categories["tilesets"]["entries"]
    fire = next(entry for entry in tilesets if "Fire" in entry["sheet"])
    assert fire["kind"] == "animated"
    assert fire["frame"]["count"] == 9

    frames = categories["frames"]["entries"]
    assert len(frames) == 1
    assert frames[0]["frame"] == {"w": 256, "h": 256, "count": 9, "fps": 7.692}
    assert frames[0]["frame_source"] == "gif"


def test_every_entry_carries_measured_licence_and_credit(tmp_path):
    catalog = E.extract_assets(str(_source(tmp_path)), size=SIZE)
    packs = catalog["packs"]
    assert packs["darkages"]["status"] == "declared"
    assert "Hypnobius" in packs["darkages"]["credit"]
    assert packs["raven_icons"]["status"] == "unverified"
    assert "no licence" in packs["raven_icons"]["license"].lower()
    assert packs["pixel_effects"]["credit"] == "Will Tice / unTied Games"
    assert packs["frames"]["status"] == "unverified"
    for category in catalog["categories"].values():
        for entry in category["entries"]:
            assert entry["license"]
            assert entry["credit"]
            assert entry["pack"] in packs


def test_missing_packs_are_skipped_and_corrupt_sources_are_typed(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    catalog = E.extract_assets(str(empty), size=SIZE,
                               categories=["effects", "icons", "frames"])
    assert catalog["categories"] == {}

    root = _source(tmp_path)
    bad = tmp_path / "NEW FRAMES/pixellab-test.gif"
    bad.write_bytes(b"not a gif")
    with pytest.raises(E.ThemeBuildError) as exc:
        E.extract_assets(str(root), size=SIZE, categories=["frames"])
    assert any("unreadable GIF" in problem for problem in exc.value.problems)

    broken = root / "DarkAgesUi_v1.0/32x32-Tilesheet.png"
    broken.write_bytes(b"not a png")
    with pytest.raises(E.ThemeBuildError):
        E.extract_assets(str(root), size=SIZE, categories=["ui_extra"])


# ------------------------------------------------------- build + recipes
def test_build_pack_includes_new_categories_and_provenance(tmp_path):
    root = _source(tmp_path)
    out = tmp_path / "pack" / "extra-assets"
    catalog = E.extract_assets(str(root), size=SIZE)
    manifest = B.build_theme(
        B.DEFAULT_SOURCE, out, size=SIZE,
        layers=B._normalize_layers(_layers()), personas=PERSONAS,
        assets=catalog)

    assert set(manifest["assets"]["categories"]) == set(catalog["categories"])
    assert set(manifest["assets"]["packs"]) >= {
        "darkages", "dragon_huds", "medieval_ui", "ui_fonts",
        "raven_icons", "green_forest", "pixel_effects", "frames"}
    assert T.validate_theme(out) == []
    index = json.loads((out / "assets/frames/index.json").read_text())
    entry = index["sprites"][0]
    assert entry["frame"]["count"] == 9
    assert (out / entry["file"]).is_file()


def test_recipe_carries_metadata_and_provenance_only(tmp_path):
    root = _source(tmp_path)
    recipe_path = tmp_path / "limezu" / "theme.recipe.json"
    catalog = E.extract_assets(str(root), size=SIZE)
    recipe = B.build_recipe(
        B.DEFAULT_SOURCE, recipe_path, size=SIZE,
        layers=B._normalize_layers(_layers()), personas=PERSONAS,
        assets=catalog)
    text = recipe_path.read_text()
    assert '"ui_extra"' in text and '"effects"' in text
    assert '"packs"' in text
    assert "assets/ui_extra/" not in text
    assert "assets/effects/" not in text
    for category in ("ui_extra", "icons", "effects", "tilesets", "frames"):
        assert set(recipe["assets"]["categories"][category]) == {
            "count", "kinds"}
    assert T.validate_recipe(recipe) == []

    smuggled = json.loads(json.dumps(recipe))
    smuggled["assets"]["categories"]["icons"]["index"] = "assets/icons/index.json"
    assert T._validate_assets_recipe(smuggled["assets"])


def test_pack_rebuild_is_reproducible(tmp_path):
    root = _source(tmp_path)
    catalog = E.extract_assets(str(root), size=SIZE)
    first = tmp_path / "first" / "extra-assets"
    second = tmp_path / "second" / "extra-assets"
    for out in (first, second):
        B.build_theme(
            B.DEFAULT_SOURCE, out, size=SIZE,
            layers=B._normalize_layers(_layers()), personas=PERSONAS,
            assets=catalog)
    assert B.compare_packs(first, second) == []


def test_pack_export_keeps_provenance_metadata_only(tmp_path):
    root = _source(tmp_path)
    out = tmp_path / "pack" / "extra-assets"
    catalog = E.extract_assets(str(root), size=SIZE)
    manifest = B.build_theme(
        B.DEFAULT_SOURCE, out, size=SIZE,
        layers=B._normalize_layers(_layers()), personas=PERSONAS,
        assets=catalog)
    exported = theme_cli._recipe_from_pack(out, manifest)
    assert exported["assets"]["packs"]["pixel_effects"]["credit"] == (
        "Will Tice / unTied Games")
    assert "index" not in json.dumps(exported["assets"])
    assert "assets/effects/" not in json.dumps(exported)
    assert T.validate_recipe(exported) == []
