"""The purchase extractor reads the paid sheets in memory and builds a pack.

WHAT THIS PINS
--------------
Task 3 of the office UI theme upgrade. The owner's purchased zips are *read*
from ``zipfile`` members straight into Pillow; nothing is extracted into the
repo, and the CLI writes only the composed, derived atlas it owns.

The rules below are the ones that make the extraction safe and reproducible:

  * the Character Generator and Portrait Generator zips resolve from a
    directory or a direct zip path, in memory;
  * the returned parts are deterministic, index-sorted lists whose
    portrait/body sheets are paired by the same numeric index;
  * the portrait pieces inside ``modernuserinterface-win.zip`` are the
    fallback when the Portrait Generator zip is absent;
  * a missing/invalid zip or an empty required part list is a typed
    ``ThemeBuildError`` naming the file/part;
  * the body tone follows the recipe's ``skin`` index (not always body 0);
  * ``--check`` reproduces the pack semantically (decoded pixels + parsed
    JSON), so a re-encode or a reformatted JSON file is not a difference while
    a changed source tile is.

Hermetic: the synthetic zips are generated in ``tmp_path``; no paid art, no
network. The optional real-pack test runs only when ``QUBIT_UI_OFFICE`` names
the purchase directory.
"""
from __future__ import annotations

import io
import json
import os
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

pytestmark = pytest.mark.unit

PERSONAS = ["alpha", "bravo", "charlie"]

#: Distinct colour per (part, index) so a sampled pixel names the sheet.
_COLORS = {
    "skin": (30, 40, 50, 255),
    "portrait_eyes": (70, 80, 90, 255),
    "body_eyes": (100, 110, 120, 255),
    "portrait_hair": (130, 140, 150, 255),
    "body_hair": (160, 170, 180, 255),
    "outfit": (190, 200, 210, 255),
    "body": (220, 120, 90, 255),
}


def _tint(color, index: int):
    """Keep the part identifiable while giving each index distinct pixels."""
    return (color[0], (color[1] + index * 7) % 256, (color[2] + index * 3) % 256, 255)


def _portrait_sheet(color, index: int, size: int = 32) -> Image.Image:
    cell = size * 2
    image = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle(
        [cell // 5, cell // 6, cell - cell // 5 - 1, cell - cell // 6 - 1],
        fill=_tint(color, index))
    return image


def _body_sheet(color, index: int, size: int = 32, rows: int = 15,
                narrow: bool = False) -> Image.Image:
    """A body or overlay sheet; ``narrow`` leaves the outer body pixels visible."""
    frame_w, frame_h = size, size * 2
    if narrow:
        x0, x1 = frame_w // 3, frame_w // 2 - 1
    else:
        x0, x1 = frame_w // 8, frame_w - frame_w // 8 - 1
    sheet = Image.new("RGBA", (frame_w, rows * frame_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    fill = _tint(color, index)
    for row in range(rows):
        draw.rectangle(
            [x0, row * frame_h + frame_h // 10,
             x1, row * frame_h + frame_h - frame_h // 16],
            fill=fill)
    return sheet


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _write_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(members):
            archive.writestr(name, members[name])


def _character_members(size: int, eyes: int = 3, hair: int = 3,
                       outfits: int = 3, bodies: int = 3,
                       body_tint: int = 0) -> dict[str, bytes]:
    root = "Character Generator 2.0 Linux Build/Character Pieces/"
    tier = f"{size}x{size}"
    members: dict[str, bytes] = {}
    for index in range(1, bodies + 1):
        members[f"{root}Bodies/{tier}/Body_{index:02d}.png"] = _png(
            _body_sheet(_COLORS["body"], body_tint, size))
    for index in range(1, outfits + 1):
        members[f"{root}Outfits/{tier}/Outfit_{index:02d}_01.png"] = _png(
            _body_sheet(_COLORS["outfit"], index, size, narrow=True))
    for index in range(1, eyes + 1):
        members[f"{root}Eyes/{tier}/Eyes_{index:02d}.png"] = _png(
            _body_sheet(_COLORS["body_eyes"], index, size, narrow=True))
    for index in range(1, hair + 1):
        members[f"{root}Hairstyles/{tier}/Hairstyle_{index:02d}_01.png"] = _png(
            _body_sheet(_COLORS["body_hair"], index, size, narrow=True))
    return members


def _portrait_members(size: int, skins: int = 3, eyes: int = 3,
                      hair: int = 3) -> dict[str, bytes]:
    root = f"Portrait Generator 1.5.0 Linux Build/Portrait Pieces/Portrait_Generator - {size}x{size}/"
    members: dict[str, bytes] = {}
    for index in range(1, skins + 1):
        members[f"{root}Skins/Skin {index}.png"] = _png(
            _portrait_sheet(_COLORS["skin"], index, size))
    for index in range(1, eyes + 1):
        members[f"{root}Eyes/Eyes {index:02d}.png"] = _png(
            _portrait_sheet(_COLORS["portrait_eyes"], index, size))
    for index in range(1, hair + 1):
        members[f"{root}Hairstyles/Hairstyle {index:02d} 1.png"] = _png(
            _portrait_sheet(_COLORS["portrait_hair"], index, size))
    return members


def _fallback_members(size: int, skins: int = 3, eyes: int = 3,
                      hair: int = 3) -> dict[str, bytes]:
    if size == 16:
        root = "16x16/Portrait_Generator/"
        members = {
            **{f"{root}Skins/PG_Skin_{i}.png": _png(
                _portrait_sheet(_COLORS["skin"], i, size))
               for i in range(1, skins + 1)},
            **{f"{root}Eyes/PG_Eyes_{i:02d}.png": _png(
                _portrait_sheet(_COLORS["portrait_eyes"], i, size))
               for i in range(1, eyes + 1)},
            **{f"{root}Hairstyles/PG_Hairstyle_{i:02d}_1.png": _png(
                _portrait_sheet(_COLORS["portrait_hair"], i, size))
               for i in range(1, hair + 1)},
        }
        return members
    tier = f"{size}x{size}"
    root = f"{tier}/Portrait_Generator_{tier}/"
    return {
        **{f"{root}Skins_{tier}/PG_Skin_{tier}_{i}.png": _png(
            _portrait_sheet(_COLORS["skin"], i, size))
           for i in range(1, skins + 1)},
        **{f"{root}Eyes_{tier}/PG_Eyes_{tier}_{i:02d}.png": _png(
            _portrait_sheet(_COLORS["portrait_eyes"], i, size))
           for i in range(1, eyes + 1)},
        **{f"{root}Hairstyles_{tier}/PG_Hairstyle_{i:02d}_{tier}_1.png": _png(
            _portrait_sheet(_COLORS["portrait_hair"], i, size))
           for i in range(1, hair + 1)},
    }


def _make_purchase(tmp_path: Path, *, size: int = 32, portrait: bool = True,
                   fallback: bool = False, character: bool = True,
                   skins: int = 3, eyes: int = 3, hair: int = 3,
                   outfits: int = 3, bodies: int = 3, body_tint: int = 0) -> Path:
    root = tmp_path / "UI Office"
    root.mkdir(parents=True)
    if character:
        _write_zip(root / E.CHARACTER_ZIP_NAMES[0],
                   _character_members(size, eyes=eyes, hair=hair,
                                      outfits=outfits, bodies=bodies,
                                      body_tint=body_tint))
    if portrait:
        _write_zip(root / E.PORTRAIT_ZIP_NAMES[0],
                   _portrait_members(size, skins=skins, eyes=eyes, hair=hair))
    if fallback:
        _write_zip(root / E.FALLBACK_ZIP_NAMES[0],
                   _fallback_members(size, skins=skins, eyes=eyes, hair=hair))
    return root


def _pixel(image: Image.Image, size: int) -> tuple[int, int, int, int]:
    return image.getpixel((size // 2, size // 2))


def _overlay_pixel(image: Image.Image, size: int) -> tuple[int, int, int, int]:
    """A pixel inside the narrow overlay/outfit box, not on a frame boundary."""
    return image.getpixel((size // 3 + 1, size // 4))


# --------------------------------------------------------------- extraction
def test_extract_returns_sorted_paired_layers(tmp_path):
    root = _make_purchase(tmp_path, skins=3, eyes=3, hair=3, outfits=3, bodies=3)
    layers = E.extract_layers(str(root), size=32)

    assert set(layers) == {"skin", "eyes", "hair", "outfit", "body"}
    assert [len(layers[part]) for part in ("skin", "eyes", "hair", "outfit", "body")] == [3, 3, 3, 3, 3]

    # Skin is portrait-only; the body tone comes from the Bodies list.
    assert set(layers["skin"][0]) == {"portrait"}
    assert isinstance(layers["body"][0], Image.Image)
    assert isinstance(layers["outfit"][0], Image.Image)

    for index, sheet in enumerate(layers["eyes"]):
        assert set(sheet) == {"portrait", "body"}
        assert _pixel(sheet["portrait"], 64) == _tint(_COLORS["portrait_eyes"], index + 1)
        assert _overlay_pixel(sheet["body"], 32) == _tint(_COLORS["body_eyes"], index + 1)

    for index, sheet in enumerate(layers["hair"]):
        assert set(sheet) == {"portrait", "body"}
        assert _pixel(sheet["portrait"], 64) == _tint(_COLORS["portrait_hair"], index + 1)
        assert _overlay_pixel(sheet["body"], 32) == _tint(_COLORS["body_hair"], index + 1)

    for index, image in enumerate(layers["outfit"]):
        assert _overlay_pixel(image, 32) == _tint(_COLORS["outfit"], index + 1)


def test_extraction_orders_by_numeric_index_not_filename(tmp_path):
    root = tmp_path / "UI Office"
    root.mkdir()
    _write_zip(root / E.CHARACTER_ZIP_NAMES[0], _character_members(32))
    members = _portrait_members(32)
    # Rebuild the portrait zip with out-of-order insertion order.
    with zipfile.ZipFile(root / E.PORTRAIT_ZIP_NAMES[0], "w") as archive:
        for name in reversed(sorted(members)):
            archive.writestr(name, members[name])

    layers = E.extract_layers(str(root / E.PORTRAIT_ZIP_NAMES[0]), size=32)
    assert [_pixel(s["portrait"], 64) for s in layers["eyes"]] == [
        _tint(_COLORS["portrait_eyes"], i) for i in (1, 2, 3)]


def test_direct_zip_source_finds_its_sibling(tmp_path):
    root = _make_purchase(tmp_path)
    layers = E.extract_layers(str(root / E.CHARACTER_ZIP_NAMES[0]), size=32)
    assert len(layers["skin"]) == 3 and len(layers["hair"]) == 3


def test_modern_ui_fallback_supplies_portraits(tmp_path):
    root = _make_purchase(tmp_path, portrait=False, fallback=True)
    layers = E.extract_layers(str(root), size=32)
    assert len(layers["skin"]) == 3
    assert set(layers["eyes"][0]) == {"portrait", "body"}


@pytest.mark.parametrize("size", [16, 32, 48])
def test_every_tier_reads_its_own_folder(tmp_path, size):
    root = _make_purchase(tmp_path, size=size)
    layers = E.extract_layers(str(root), size=size)
    assert len(layers["body"]) == 3
    assert layers["skin"][0]["portrait"].size == (size * 2, size * 2)


# ---------------------------------------------------------------- refusals
def test_missing_character_zip_is_a_typed_error(tmp_path):
    root = _make_purchase(tmp_path, character=False)
    with pytest.raises(B.ThemeBuildError) as info:
        E.extract_layers(str(root), size=32)
    assert E.CHARACTER_ZIP_NAMES[0] in str(info.value)


def test_missing_portrait_and_fallback_is_a_typed_error(tmp_path):
    root = _make_purchase(tmp_path, portrait=False, fallback=False)
    with pytest.raises(B.ThemeBuildError) as info:
        E.extract_layers(str(root), size=32)
    assert E.PORTRAIT_ZIP_NAMES[0] in str(info.value)


def test_missing_direct_zip_names_the_path(tmp_path):
    missing = tmp_path / "nope.zip"
    with pytest.raises(B.ThemeBuildError) as info:
        E.extract_layers(str(missing), size=32)
    assert "nope.zip" in str(info.value)


def test_invalid_zip_is_a_typed_error(tmp_path):
    broken = tmp_path / "UI Office"
    broken.mkdir()
    (broken / E.CHARACTER_ZIP_NAMES[0]).write_bytes(b"not a zip")
    with pytest.raises(B.ThemeBuildError) as info:
        E.extract_layers(str(broken), size=32)
    assert E.CHARACTER_ZIP_NAMES[0] in str(info.value)


def test_missing_required_part_list_is_a_typed_error(tmp_path):
    root = tmp_path / "UI Office"
    root.mkdir()
    _write_zip(root / E.CHARACTER_ZIP_NAMES[0],
               _character_members(32, eyes=0))
    _write_zip(root / E.PORTRAIT_ZIP_NAMES[0], _portrait_members(32))
    with pytest.raises(B.ThemeBuildError) as info:
        E.extract_layers(str(root), size=32)
    assert "eyes" in str(info.value).lower()


def test_size_must_be_supported(tmp_path):
    root = _make_purchase(tmp_path)
    with pytest.raises(B.ThemeBuildError):
        E.extract_layers(str(root), size=24)


def test_accessory_is_not_returned_in_v1(tmp_path):
    root = _make_purchase(tmp_path)
    assert "accessory" not in E.extract_layers(str(root), size=32)


# --------------------------------------------------------------- body tone
def _tiny_overlay(color, index: int, size: int = 32, rows: int = 15) -> Image.Image:
    """A small overlay well away from the body-tone sample pixel."""
    frame_w, frame_h = size, size * 2
    sheet = Image.new("RGBA", (frame_w, rows * frame_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sheet)
    fill = _tint(color, index)
    for row in range(rows):
        draw.rectangle(
            [0, row * frame_h, frame_w // 8, row * frame_h + frame_h // 8],
            fill=fill)
    return sheet


def _layers_for_body_tone(size: int = 32) -> dict:
    return {
        "skin": [{"portrait": _portrait_sheet(_COLORS["skin"], 1, size)},
                 {"portrait": _portrait_sheet(_COLORS["skin"], 2, size)}],
        "eyes": [{"portrait": _portrait_sheet(_COLORS["portrait_eyes"], 1, size),
                  "body": _tiny_overlay(_COLORS["body_eyes"], 1, size)}],
        "hair": [{"portrait": _portrait_sheet(_COLORS["portrait_hair"], 1, size),
                  "body": _tiny_overlay(_COLORS["body_hair"], 1, size)}],
        "outfit": [_tiny_overlay(_COLORS["outfit"], 1, size)],
        "body": [_body_sheet(_COLORS["body"], 1, size),
                 _body_sheet(_COLORS["body"], 2, size)],
    }


#: The body box starts at x=size//8; the tiny overlays stop well before x=5.
BODY_SAMPLE = (6, 10)


def test_body_tone_follows_the_recipe_skin_index():
    layers = _layers_for_body_tone()
    recipe = {"eyes": 0, "hair": 0, "outfit": 0}

    left = B._compose_body({**recipe, "skin": 0}, layers, 32)
    right = B._compose_body({**recipe, "skin": 1}, layers, 32)
    assert left.tobytes() != right.tobytes()
    assert left.getpixel(BODY_SAMPLE) == _tint(_COLORS["body"], 1)
    assert right.getpixel(BODY_SAMPLE) == _tint(_COLORS["body"], 2)


def test_body_tone_clamps_out_of_range_and_falls_back_when_absent():
    layers = _layers_for_body_tone()
    clamped = B._compose_body(
        {"skin": 99, "eyes": 0, "hair": 0, "outfit": 0}, layers, 32)
    assert clamped.getpixel(BODY_SAMPLE) == _tint(_COLORS["body"], 2)
    absent = B._compose_body(
        {"eyes": 0, "hair": 0, "outfit": 0}, layers, 32)
    assert absent.getpixel(BODY_SAMPLE) == _tint(_COLORS["body"], 1)


# -------------------------------------------------------------------- CLI
def _run_cli(monkeypatch, source: Path, out: Path, *extra: str) -> int:
    monkeypatch.setattr(B, "_default_personas", lambda: list(PERSONAS))
    return B.main(["--source", str(source), "--out", str(out), *extra])


def test_cli_builds_a_real_pack_and_check_reproduces_it(tmp_path, monkeypatch):
    root = _make_purchase(tmp_path)
    out = tmp_path / "pack" / "limezu"
    assert _run_cli(monkeypatch, root, out) == 0
    assert T.validate_theme(out) == []
    assert _run_cli(monkeypatch, root, out, "--check") == 0


def test_cli_ui_sheet_applies_the_sampled_tokens(tmp_path, monkeypatch):
    root = _make_purchase(tmp_path)
    sheet = _ui_sheet(tmp_path)
    out = tmp_path / "pack" / "limezu"
    assert _run_cli(monkeypatch, root, out, "--ui-sheet", str(sheet)) == 0
    manifest = json.loads((out / "theme.json").read_text(encoding="utf-8"))
    assert manifest["tokens"]["color-surface"] == "#e8d8b8"
    assert T.validate_theme(out) == []


def test_compare_packs_is_semantic_not_byte_for_byte(tmp_path, monkeypatch):
    """A re-encoded atlas and a reformatted JSON file are not differences."""
    source = _make_purchase(tmp_path / "src")
    left = tmp_path / "left" / "limezu"
    right = tmp_path / "right" / "limezu"
    assert _run_cli(monkeypatch, source, left) == 0
    assert _run_cli(monkeypatch, source, right) == 0
    assert B.compare_packs(left, right) == []

    manifest = json.loads((left / "theme.json").read_text(encoding="utf-8"))
    for section in ("portraits", "bodies"):
        path = left / manifest[section]["atlas"]
        image = Image.open(path).convert("RGBA")
        if path.suffix == ".webp":
            image.save(path, format="WEBP", lossless=True, quality=5)
        else:
            image.save(path, format="PNG", compress_level=0)
    agents = json.loads((left / "agents.json").read_text(encoding="utf-8"))
    (left / "agents.json").write_text(
        json.dumps(agents, separators=(",", ":")), encoding="utf-8")

    assert B.compare_packs(left, right) == []

    # A real pixel change is still a difference.
    atlas_path = left / manifest["portraits"]["atlas"]
    image = Image.open(atlas_path).convert("RGBA")
    image.putpixel((0, 0), (255, 0, 255, 255))
    if atlas_path.suffix == ".webp":
        image.save(atlas_path, format="WEBP", lossless=True, quality=100)
    else:
        image.save(atlas_path, format="PNG")
    assert any("portraits/atlas" in problem
               for problem in B.compare_packs(left, right))


def test_check_fails_after_a_source_tile_changes(tmp_path, monkeypatch):
    root = _make_purchase(tmp_path)
    out = tmp_path / "pack" / "limezu"
    assert _run_cli(monkeypatch, root, out) == 0

    # Rebuild the character zip with a different body-tone tile.
    _write_zip(root / E.CHARACTER_ZIP_NAMES[0],
               _character_members(32, body_tint=50))

    assert _run_cli(monkeypatch, root, out, "--check") == 1


def test_cli_missing_source_is_a_typed_error(tmp_path, monkeypatch):
    code = _run_cli(monkeypatch, tmp_path / "does-not-exist",
                    tmp_path / "pack" / "limezu")
    assert code == 2
    assert not (tmp_path / "pack" / "limezu").exists()


# ------------------------------------------------------------- calibration
def _zip_digest(root: Path) -> dict[str, str]:
    import hashlib

    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.glob("*.zip"))
    }


def test_calibrate_writes_labelled_grids_for_every_body_sheet(tmp_path, monkeypatch):
    root = _make_purchase(tmp_path)
    before = _zip_digest(root)
    calib = tmp_path / "calibrate"

    monkeypatch.setattr(B, "_default_personas", lambda: list(PERSONAS))
    code = B.main(["--source", str(root), "--calibrate",
                   "--calibrate-out", str(calib), "--size", "32"])
    assert code == 0
    # Calibration reads the purchase and writes no pack.
    assert not (tmp_path / "pack").exists()
    assert _zip_digest(root) == before

    manifest = json.loads(
        (calib / "calibrate.json").read_text(encoding="utf-8"))
    assert manifest["frame"] == {"w": 32, "h": 64}
    assert set(manifest["parts"]) == {"body", "hair", "eyes", "outfit"}

    for part in ("body", "hair", "eyes", "outfit"):
        entries = manifest["parts"][part]
        assert [entry["index"] for entry in entries] == [1, 2, 3]
        for entry in entries:
            path = calib / entry["file"]
            assert path.is_file(), entry["file"]
            with Image.open(path) as image:
                image.load()
                assert image.size[0] % 32 == 0
                assert image.size[1] % 64 == 0
                # The synthetic body sheets are one column, fifteen rows.
                assert image.size == (entry["cols"] * 32,
                                      entry["rows"] * 64) == (32, 960)
                assert image.size == (32, 15 * 64)


def test_calibrate_draws_frames_labels_and_calibration_outline(tmp_path):
    root = _make_purchase(tmp_path, skins=1, eyes=1, hair=1, outfits=1, bodies=1)
    calib = tmp_path / "calibrate"
    assert B.main(["--source", str(root), "--calibrate",
                   "--calibrate-out", str(calib)]) == 0

    with Image.open(calib / "body_01.png") as image:
        grid = image.convert("RGBA")
        # The calibrated body frame (row 0, col 0) is outlined in green.
        assert grid.getpixel((1, 1)) == (60, 255, 120, 255)
        # Row 14 (shoes) is a different calibrated frame only for outfits.
        assert grid.getpixel((1, 14 * 64 + 1)) != (60, 255, 120, 255)

    with Image.open(calib / "outfit_01.png") as image:
        grid = image.convert("RGBA")
        for row in (0, 7, 8, 14):
            assert grid.getpixel((1, row * 64 + 1)) == (60, 255, 120, 255)


def test_calibrate_defaults_beside_the_theme_not_in_the_repo(
        tmp_path, monkeypatch):
    import voicebridge.paths as paths

    home = tmp_path / "home"
    monkeypatch.setattr(paths, "qubit_home", lambda: home)
    root = _make_purchase(tmp_path / "src")
    assert B.main(["--source", str(root), "--calibrate",
                   "--size", "32"]) == 0
    out = home / "office" / "themes" / "ui-office.calibrate"
    assert (out / "body_01.png").is_file()
    assert (out / "calibrate.json").is_file()
    # The sibling is beside the pack, never inside the pack directory.
    assert not (home / "office" / "themes" / "ui-office" / "calibrate").exists()
    # No art or grid was written inside the checkout.
    assert not (REPO / "calibrate").exists()


def test_default_calibrate_keeps_a_built_pack_check_clean(
        tmp_path, monkeypatch):
    root = _make_purchase(tmp_path)
    out = tmp_path / "pack" / "limezu"
    monkeypatch.setattr(B, "_default_personas", lambda: list(PERSONAS))
    assert B.main(["--source", str(root), "--out", str(out)]) == 0
    assert T.validate_theme(out) == []

    # Default --calibrate against the *built pack*: the grids go to the
    # sibling, not inside the pack.
    assert B.main(["--source", str(root), "--out", str(out),
                   "--calibrate"]) == 0
    grids = tmp_path / "pack" / "limezu.calibrate"
    assert (grids / "body_01.png").is_file()
    assert (grids / "calibrate.json").is_file()
    assert not (out / "calibrate").exists()
    assert not any(path.name == "calibrate" for path in out.iterdir())

    # And a rebuild from the same source is still identical.
    assert B.main(["--source", str(root), "--out", str(out),
                   "--check"]) == 0


def test_calibrate_out_without_calibrate_is_a_usage_error(tmp_path, capsys):
    root = _make_purchase(tmp_path)
    with pytest.raises(SystemExit) as info:
        B.main(["--source", str(root), "--calibrate-out",
                str(tmp_path / "calibrate")])
    assert info.value.code == 2
    assert "--calibrate-out requires --calibrate" in capsys.readouterr().err


# ------------------------------------------------------------- real purchase
@pytest.mark.skipif(
    not os.environ.get("QUBIT_UI_OFFICE"),
    reason="set QUBIT_UI_OFFICE to the purchased art directory to run")
def test_real_purchase_extracts_the_calibrated_counts():
    layers = E.extract_layers(os.environ["QUBIT_UI_OFFICE"], size=32)
    assert len(layers["skin"]) == 9
    assert len(layers["eyes"]) == 7
    assert len(layers["hair"]) == 29
    assert len(layers["outfit"]) == 33
    assert len(layers["body"]) == 9
    # Source sheets stay intact; the builder crops the calibrated 64x64 cell
    # from the 32-tier portrait sheet and the 32x64 frame from the body sheet.
    portrait = layers["skin"][0]["portrait"]
    assert portrait.width % 64 == 0 and portrait.height % 64 == 0
    assert layers["body"][0].size == (1792, 1280)


# ---------------------------------------------------------------------------
# Task 7 — palette sampling + contact sheet authoring
# ---------------------------------------------------------------------------
def _ui_sheet(tmp_path: Path) -> Path:
    """A synthetic Modern UI sheet with a warm, high-contrast palette."""
    image = Image.new("RGBA", (64, 64), (232, 216, 184, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 31, 31], fill=(243, 232, 208, 255))     # raised
    draw.rectangle([0, 32, 15, 47], fill=(42, 32, 24, 255))       # text
    draw.rectangle([16, 32, 31, 47], fill=(106, 90, 72, 255))     # muted
    draw.rectangle([32, 32, 47, 47], fill=(184, 168, 136, 255))   # border
    draw.rectangle([48, 32, 63, 47], fill=(176, 74, 58, 255))     # accent
    path = tmp_path / "modern-ui.png"
    image.save(path)
    return path


def test_palette_derives_validated_semantic_tokens(tmp_path):
    result = E.derive_tokens(str(_ui_sheet(tmp_path)))
    assert result["fallback"] is False, result["reasons"]
    tokens = result["tokens"]
    assert set(tokens) >= set(E.SEMANTIC_TOKENS)
    assert T.validate_tokens(tokens) == []
    surface = tokens["color-surface"]
    assert T.contrast_ratio(tokens["color-text-primary"], surface) >= 4.5
    assert T.contrast_ratio(tokens["color-text-muted"], surface) >= 3.0
    assert tokens["color-surface"] != tokens["color-surface-raised"]
    # The saturated swatch is the accent, not a neutral surface colour.
    assert tokens["color-accent"] != surface
    assert tokens["color-accent"] != tokens["color-text-primary"]


def test_palette_is_deterministic(tmp_path):
    sheet = str(_ui_sheet(tmp_path))
    assert E.derive_tokens(sheet) == E.derive_tokens(sheet)


def test_palette_falls_back_and_reports_why_on_low_contrast(tmp_path):
    path = tmp_path / "flat.png"
    Image.new("RGBA", (16, 16), (128, 128, 128, 255)).save(path)
    result = E.derive_tokens(str(path))
    assert result["fallback"] is True
    assert result["tokens"] == B.DEFAULT_TOKENS
    assert any("contrast" in reason for reason in result["reasons"]), result


def test_cli_tokens_reads_a_sheet_and_writes_nothing(tmp_path, capsys):
    sheet = _ui_sheet(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    assert E.main(["--tokens", str(sheet)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["fallback"] is False
    assert T.validate_tokens(payload["tokens"]) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_cli_tokens_reads_a_zip_member(tmp_path, capsys):
    sheet = _ui_sheet(tmp_path)
    archive = tmp_path / "modernuserinterface-win.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.write(sheet, "16x16/Modern_UI_Style_1/sheet.png")
    assert E.main(["--tokens", str(archive),
                   "--member", "16x16/Modern_UI_Style_1/sheet.png"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["fallback"] is False


def test_contact_sheet_is_written_outside_the_repo(tmp_path, capsys):
    sheet = _ui_sheet(tmp_path)
    out = tmp_path / "authoring"
    assert E.main(["--ui-contact-sheet", str(sheet), "--out", str(out),
                   "--scale", "2", "--ui-grid", "16"]) == 0
    payload = json.loads(capsys.readouterr().out)
    written = Path(payload["contact_sheet"])
    assert written.is_file() and written.parent == out
    with Image.open(written) as grid:
        assert grid.size == (128, 128)


def test_contact_sheet_refuses_to_write_inside_the_repo(tmp_path):
    sheet = _ui_sheet(tmp_path)
    image = E.load_ui_sheet(str(sheet))
    with pytest.raises(E.ThemeBuildError) as caught:
        E.write_contact_sheet(image, str(REPO / "ui-contact-sheet"))
    assert "inside the repository" in str(caught.value)
    assert not (REPO / "ui-contact-sheet").exists()
