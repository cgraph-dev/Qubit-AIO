"""The office theme builder is deterministic, QA-gated, and contract-checked.

WHAT THIS PINS
--------------
Task 2 of the office UI theme upgrade. The builder takes decoded layers
(supplied here synthetically; Task 3 supplies the real extraction) and writes
a local, served theme pack. The rules below are the ones that make the pack
safe to build and reproducible:

  * the same slug always composes the same pixels, and different slugs differ;
  * each part is chosen by ``sha256(f"{slug}:{part}") % len(parts)`` and the
    recipe is frozen in ``agents.json`` — never sequential bytes;
  * the portrait atlas is ``cols x rows`` ``size`` tiles and the body atlas is
    ``cols x frame_w`` by ``rows x frame_h``;
  * both indexes cover every slug inside ``cols x rows``;
  * one recipe per slug drives both the portrait and the body;
  * QA rejects blank and overfull tiles (and a body whose feet leave the frame);
  * empty credit/licence and a filesystem ``source`` are refused;
  * the manifest declares sha256 + bytes for every file, and the real
    ``validate_theme``/``validate_recipe`` accept the built pack;
  * the ``agent_sheets.json`` calibration is recorded and validated;
  * the CLI refuses a missing purchase source, and builds/checks with an
    injected extraction.

Hermetic: temporary directories only, no network, no live service. The
synthetic layer sheets are generated in memory; no paid art is read or written.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import office_theme_build as B  # noqa: E402
from voicebridge.office import theme as T  # noqa: E402

pytestmark = pytest.mark.unit

PERSONAS = ["alpha", "bravo", "charlie", "delta", "echo"]
VARIANTS = 6

#: Distinct colour per (part, variant) so a sampled pixel names the part.
PART_BASE = {
    "skin": 20, "eyes": 50, "hair": 80,
    "outfit": 110, "accessory": 140, "body": 170,
}
PORTRAIT_BOX = {
    "skin": (0.20, 0.20, 0.80, 0.80),
    "eyes": (0.20, 0.15, 0.45, 0.35),
    "hair": (0.55, 0.05, 0.90, 0.30),
    "accessory": (0.60, 0.60, 0.90, 0.90),
}
BODY_BOX = {
    "body": (0.20, 0.10, 0.80, 0.80),
    "outfit": (0.25, 0.35, 0.75, 0.90),
    "eyes": (0.20, 0.15, 0.45, 0.35),
    "hair": (0.55, 0.05, 0.90, 0.30),
}


# ----------------------------------------------------------------- helpers
def _color(part: str, variant: int) -> tuple[int, int, int, int]:
    return (PART_BASE[part] + variant, 220, 40, 255)


def _draw_box(image: Image.Image, box, color) -> None:
    width, height = image.size
    x0, y0, x1, y1 = box
    ImageDraw.Draw(image).rectangle(
        [int(x0 * width), int(y0 * height),
         int(x1 * width) - 1, int(y1 * height) - 1],
        fill=color)


def _portrait_sheet(part: str, variant: int, size: int = 32) -> Image.Image:
    cell = size * 2
    image = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
    _draw_box(image, PORTRAIT_BOX[part], _color(part, variant))
    return image


def _body_sheet(part: str, variant: int, size: int = 32, rows: int = 15,
                empty_rows=()) -> Image.Image:
    frame_w, frame_h = size, size * 2
    sheet = Image.new("RGBA", (frame_w, rows * frame_h), (0, 0, 0, 0))
    color = _color(part, variant)
    for row in range(rows):
        if row in empty_rows:
            continue
        frame = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
        _draw_box(frame, BODY_BOX[part], color)
        sheet.paste(frame, (0, row * frame_h))
    return sheet


def _layers(size: int = 32, variants: int = VARIANTS,
            outfit_empty=()) -> dict:
    return {
        "skin": [{"portrait": _portrait_sheet("skin", v, size)}
                 for v in range(variants)],
        "eyes": [{"portrait": _portrait_sheet("eyes", v, size),
                  "body": _body_sheet("eyes", v, size)}
                 for v in range(variants)],
        "hair": [{"portrait": _portrait_sheet("hair", v, size),
                  "body": _body_sheet("hair", v, size)}
                 for v in range(variants)],
        "accessory": [{"portrait": _portrait_sheet("accessory", v, size)}
                      for v in range(variants)],
        "outfit": [{"body": _body_sheet("outfit", v, size,
                                       empty_rows=outfit_empty)}
                   for v in range(variants)],
        "body": [{"body": _body_sheet("body", 0, size)}],
    }


def _bare_sheet(part: str, variant: int, size: int = 32) -> Image.Image:
    """One bare ``PIL.Image`` that is valid for both the portrait and body views."""
    sheet = Image.new("RGBA", (size * 2, 15 * size * 2), (0, 0, 0, 0))
    cell = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
    _draw_box(cell, PORTRAIT_BOX[part], _color(part, variant))
    sheet.paste(cell, (0, 0))
    for row in (0, 3):
        frame = Image.new("RGBA", (size, size * 2), (0, 0, 0, 0))
        _draw_box(frame, BODY_BOX[part], _color(part, variant))
        sheet.paste(frame, (0, row * size * 2))
    return sheet


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _build(root: Path, personas=None, **kwargs):
    layers = kwargs.pop("layers", None) or _layers()
    out = root / "demo"
    manifest = B.build_theme(B.DEFAULT_SOURCE, out,
                             personas=personas or PERSONAS,
                             layers=layers, **kwargs)
    return out, manifest


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _tile(atlas: Image.Image, col: int, row: int, size: int,
          frame_h: int | None = None) -> Image.Image:
    height = frame_h or size
    left, top = col * size, row * size
    return atlas.crop((left, top, left + size, top + height))


# ------------------------------------------------------------ determinism
def test_same_slug_produces_identical_portrait_pixels():
    layers = _layers()
    assert B.portrait_bytes("alpha", layers) == B.portrait_bytes("alpha", layers)


def test_two_builds_are_byte_identical(tmp_path):
    layers = _layers()
    one = tmp_path / "one" / "demo"
    two = tmp_path / "two" / "demo"
    B.build_theme(B.DEFAULT_SOURCE, one, personas=PERSONAS, layers=layers)
    B.build_theme(B.DEFAULT_SOURCE, two, personas=PERSONAS, layers=layers)
    assert B.compare_packs(one, two) == []


def test_different_slugs_produce_different_pixels():
    layers = _layers()
    candidates = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
    pairs = [
        (left, right)
        for left in candidates
        for right in candidates
        if left < right
        and B.derive_recipe(left, layers) != B.derive_recipe(right, layers)
    ]
    assert pairs, "expected at least two slugs with different recipes"
    left, right = pairs[0]
    assert B.portrait_bytes(left, layers) != B.portrait_bytes(right, layers)


def test_recipes_are_key_based_not_sequential():
    layers = _layers(variants=7)
    recipe = B.derive_recipe("alpha", layers)
    assert recipe
    for part, index in recipe.items():
        expected = int.from_bytes(
            hashlib.sha256(f"alpha:{part}".encode("utf-8")).digest(), "big"
        ) % len(layers[part])
        assert index == expected, part
    recipes = {slug: B.derive_recipe(slug, layers)
               for slug in ["alpha", "bravo", "charlie", "delta", "echo"]}
    assert any(set(r.values()) != {0} for r in recipes.values())


# ---------------------------------------------------------------- atlases
def _open_rgba(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return image.convert("RGBA")


def test_portrait_atlas_dimensions(tmp_path):
    out, manifest = _build(tmp_path)
    section = manifest["portraits"]
    atlas = _open_rgba(out / section["atlas"])
    assert atlas.size == (section["cols"] * section["size"],
                          section["rows"] * section["size"])


def test_body_atlas_dimensions(tmp_path):
    out, manifest = _build(tmp_path)
    section = manifest["bodies"]
    atlas = _open_rgba(out / section["atlas"])
    assert atlas.size == (section["cols"] * section["frame"]["w"],
                          section["rows"] * section["frame"]["h"])
    assert section["frame"] == {"w": 32, "h": 64}


def test_indexes_cover_every_slug_in_bounds(tmp_path):
    out, manifest = _build(tmp_path)
    for section in ("portraits", "bodies"):
        doc = _read_json(out / manifest[section]["index"])
        assert set(doc["cells"]) == set(PERSONAS)
        cols, rows = doc["cols"], doc["rows"]
        for slug in sorted(PERSONAS):
            col, row = doc["cells"][slug]
            assert 0 <= col < cols and 0 <= row < rows
        # deterministic row-major placement
        for index, slug in enumerate(sorted(PERSONAS)):
            assert doc["cells"][slug] == [index % cols, index // cols]


def test_one_recipe_drives_both_portrait_and_body(tmp_path):
    out, manifest = _build(tmp_path)
    agents = _read_json(out / "agents.json")
    recipes = agents["recipes"]
    assert set(recipes) == set(PERSONAS)

    portraits = _open_rgba(out / manifest["portraits"]["atlas"])
    bodies = _open_rgba(out / manifest["bodies"]["atlas"])
    p_index = _read_json(out / manifest["portraits"]["index"])
    b_index = _read_json(out / manifest["bodies"]["index"])
    size = manifest["portraits"]["size"]
    frame_h = manifest["bodies"]["frame"]["h"]

    for slug in PERSONAS:
        recipe = recipes[slug]
        eyes_expected = PART_BASE["eyes"] + recipe["eyes"]
        p_col, p_row = p_index["cells"][slug]
        b_col, b_row = b_index["cells"][slug]
        portrait = _tile(portraits, p_col, p_row, size)
        body = _tile(bodies, b_col, b_row, size, frame_h)
        # eye boxes are disjoint from hull/hair in the synthetic sheets
        assert portrait.getpixel((10, 8))[0] == eyes_expected, slug
        assert body.getpixel((10, 16))[0] == eyes_expected, slug


def test_empty_outfit_rows_are_skipped(tmp_path):
    layers = _layers(outfit_empty=(7, 8))
    out, manifest = _build(tmp_path, layers=layers)
    assert T.validate_theme(out) == []
    assert manifest["bodies"]["frame"] == {"w": 32, "h": 64}


def test_missing_body_view_is_refused_not_substituted(tmp_path):
    layers = _layers()
    # portrait-only eye mappings: using them as a body would be silent corruption
    layers["eyes"] = [{"portrait": sheet["portrait"]} for sheet in layers["eyes"]]

    with pytest.raises(B.ThemeBuildError) as info:
        B.body_bytes("alpha", layers)
    assert "missing the 'body' view" in str(info.value)

    out = tmp_path / "demo"
    with pytest.raises(B.ThemeBuildError):
        B.build_theme(B.DEFAULT_SOURCE, out, personas=["alpha"], layers=layers)
    assert not out.exists()


def test_missing_portrait_view_is_refused_not_substituted():
    layers = _layers()
    # body-only hair mappings must not become a portrait
    layers["hair"] = [{"body": sheet["body"]} for sheet in layers["hair"]]

    with pytest.raises(B.ThemeBuildError) as info:
        B.portrait_bytes("alpha", layers)
    assert "missing the 'portrait' view" in str(info.value)


def test_a_bare_image_is_the_explicit_both_views_form():
    layers = _layers()
    layers["eyes"] = [_bare_sheet("eyes", v) for v in range(VARIANTS)]
    layers["hair"] = [_bare_sheet("hair", v) for v in range(VARIANTS)]

    assert B.qa_portrait(B.portrait_bytes("alpha", layers)) == []
    assert B.qa_body(B.body_bytes("alpha", layers), frame_h=64) == []


def test_webp_is_only_used_when_smaller():
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    _draw_box(image, (0.1, 0.1, 0.9, 0.9), (10, 20, 30, 255))
    suffix, data = B.encode_atlas(image)
    png = B.encode_png(image)
    if suffix == ".webp":
        assert len(data) < len(png)
    else:
        assert suffix == ".png" and data == png
    with Image.open(io.BytesIO(data)) as decoded:
        assert decoded.convert("RGBA").tobytes() == image.convert("RGBA").tobytes()


# --------------------------------------------------------------------- QA
def test_qa_rejects_blank_and_overfull_tiles():
    blank = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    overfull = Image.new("RGBA", (32, 32), (255, 0, 0, 255))
    valid = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    ImageDraw.Draw(valid).rectangle([8, 8, 23, 23], fill=(1, 2, 3, 255))

    assert B.qa_portrait(_png(blank))
    assert B.qa_portrait(_png(overfull))
    assert B.qa_portrait(_png(valid)) == []


def test_body_qa_rejects_feet_outside_the_frame():
    floating = Image.new("RGBA", (32, 64), (0, 0, 0, 0))
    ImageDraw.Draw(floating).rectangle([4, 4, 27, 20], fill=(1, 2, 3, 255))
    assert B.qa_body(_png(floating), frame_h=64)
    grounded = Image.new("RGBA", (32, 64), (0, 0, 0, 0))
    ImageDraw.Draw(grounded).rectangle([4, 30, 27, 60], fill=(1, 2, 3, 255))
    assert B.qa_body(_png(grounded), frame_h=64) == []


# --------------------------------------------------------------- refusals
@pytest.mark.parametrize("field", ["credit", "license"])
def test_empty_credit_or_license_is_refused(tmp_path, field):
    out = tmp_path / field / "demo"
    with pytest.raises(B.ThemeBuildError) as info:
        B.build_theme(B.DEFAULT_SOURCE, out, personas=["alpha"],
                      layers=_layers(), **{field: "   "})
    assert field in str(info.value)
    assert not out.exists()


def test_source_must_be_a_description_not_a_path(tmp_path):
    with pytest.raises(B.ThemeBuildError) as info:
        B.build_theme("/home/alice/Desktop/UI Office", tmp_path / "demo",
                      personas=["alpha"], layers=_layers())
    assert "source" in str(info.value)
    assert not (tmp_path / "demo").exists()


def test_output_inside_the_repository_is_refused(tmp_path):
    out = REPO / ".office-theme-in-repo-refusal" / "demo"
    with pytest.raises(B.ThemeBuildError) as info:
        B.build_theme(B.DEFAULT_SOURCE, out, personas=["alpha"],
                      layers=_layers())
    assert "inside the repository" in str(info.value)
    assert not out.parent.exists()


def test_layers_are_required_and_name_task_3(tmp_path):
    with pytest.raises(B.ThemeBuildError) as info:
        B.build_theme(B.DEFAULT_SOURCE, tmp_path / "demo",
                      personas=["alpha"], layers=None)
    assert "Task 3" in str(info.value)


def test_out_directory_name_must_be_a_valid_theme_id(tmp_path):
    with pytest.raises(B.ThemeBuildError):
        B.build_theme(B.DEFAULT_SOURCE, tmp_path / "Bad ID",
                      personas=["alpha"], layers=_layers())


def test_size_must_be_supported(tmp_path):
    with pytest.raises(B.ThemeBuildError):
        B.build_theme(B.DEFAULT_SOURCE, tmp_path / "demo",
                      personas=["alpha"], layers=_layers(), size=24)


# --------------------------------------------------------------- manifest
def test_manifest_declares_sha_and_bytes_for_every_file(tmp_path):
    out, manifest = _build(tmp_path)
    assert manifest["files"]
    for relative, entry in manifest["files"].items():
        data = (out / relative).read_bytes()
        assert entry["bytes"] == len(data), relative
        assert entry["sha256"] == hashlib.sha256(data).hexdigest(), relative
    on_disk = {
        path.relative_to(out).as_posix()
        for path in out.rglob("*") if path.is_file()
    }
    assert on_disk == set(manifest["files"]) | {"theme.json"}


def test_built_pack_passes_the_real_contract(tmp_path):
    out, manifest = _build(tmp_path)
    assert T.validate_theme(out) == []
    recipes = _read_json(out / "agents.json")["recipes"]
    assert T.validate_recipe(B.shareable_recipe(manifest, recipes)) == []


def test_build_recipe_writes_only_a_valid_art_free_recipe(tmp_path):
    recipe_path = tmp_path / "limezu" / "theme.recipe.json"
    recipe = B.build_recipe(B.DEFAULT_SOURCE, recipe_path,
                            personas=PERSONAS, layers=_layers())
    assert recipe_path.is_file()
    assert sorted(p.name for p in recipe_path.parent.iterdir()) == [
        "theme.recipe.json"]
    assert T.validate_recipe(recipe) == []
    assert recipe["id"] == "limezu"
    assert recipe["slices"] == {}
    assert "files" not in recipe
    assert "portraits" not in recipe and "bodies" not in recipe
    assert set(recipe["agents"]) == set(PERSONAS)
    assert recipe["parts"]["outfit"] == VARIANTS
    # The default palette itself must clear the whole contrast matrix.
    assert T.validate_tokens(recipe["tokens"]) == []


def test_build_recipe_refuses_a_path_as_source(tmp_path):
    with pytest.raises(B.ThemeBuildError) as info:
        B.build_recipe(str(tmp_path / "purchase"),
                       tmp_path / "limezu" / "theme.recipe.json",
                       personas=PERSONAS, layers=_layers())
    assert "description, not a filesystem path" in str(info.value)


def test_build_recipe_uses_the_parent_directory_as_the_theme_id(tmp_path):
    with pytest.raises(B.ThemeBuildError):
        B.build_recipe(B.DEFAULT_SOURCE,
                       tmp_path / "Bad Id" / "theme.recipe.json",
                       personas=PERSONAS, layers=_layers())


def test_built_pack_json_has_no_operator_paths(tmp_path):
    out, _ = _build(tmp_path)
    for path in out.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        for marker in ("/home/", "~/", "C:\\Users", "\\\\"):
            assert marker not in text, (path.name, marker)


def test_manifest_source_and_template_version(tmp_path):
    out, manifest = _build(tmp_path)
    assert manifest["source"] == B.DEFAULT_SOURCE
    assert manifest["template_version"] == B.TEMPLATE_VERSION > 0
    assert manifest["credit"] and manifest["license"]
    assert manifest["id"] == "demo"


# -------------------------------------------------------- agent_sheets.json
def test_agent_sheets_records_and_validates_the_calibration(tmp_path):
    out, manifest = _build(tmp_path)
    sheets = _read_json(out / "agent_sheets.json")
    assert B.validate_agent_sheets(sheets) == []
    assert sheets["frame"] == {"w": 32, "h": 64}
    assert sheets["portrait"]["cell"] == 64
    assert sheets["portrait"]["size"] == 32
    assert sheets["frames"]["hair"] == {"row": 3, "col": 0}
    assert sheets["frames"]["outfit_base_top"] == {"row": 7, "col": 0}
    assert sheets["frames"]["outfit_top"] == {"row": 8, "col": 0}
    assert sheets["frames"]["shoes"] == {"row": 14, "col": 0}
    assert sheets["z_order"]["portrait"] == list(B.PORTRAIT_ORDER)
    assert sheets["z_order"]["body"] == list(B.BODY_ORDER)
    assert sheets["parts"]["outfit"] == VARIANTS
    assert "agent_sheets.json" in manifest["files"]


def test_tampered_calibration_is_rejected(tmp_path):
    out, _ = _build(tmp_path)
    sheets = _read_json(out / "agent_sheets.json")
    sheets["frames"]["hair"]["row"] = 4
    assert B.validate_agent_sheets(sheets)


# ------------------------------------------------------------ default grid
def test_default_layout_is_twenty_columns_for_the_roster(tmp_path):
    from voicebridge.office import agency_org

    slugs = [row["slug"] for row in agency_org.personas()]
    assert len(slugs) == 283
    out, manifest = _build(tmp_path / "roster", personas=slugs)
    assert manifest["portraits"]["cols"] == 20
    assert manifest["portraits"]["rows"] == math.ceil(283 / 20) == 15
    index = _read_json(out / "portraits/index.json")
    assert set(index["cells"]) == set(slugs)
    assert max(cell[1] for cell in index["cells"].values()) == 14


# ------------------------------------------------------------------- CLI
def test_cli_with_a_missing_source_is_a_typed_error(tmp_path, capsys):
    code = B.main(["--source", str(tmp_path / "purchase"),
                   "--out", str(tmp_path / "demo")])
    captured = capsys.readouterr()
    assert code == 2
    assert "purchase" in captured.err
    assert not (tmp_path / "demo").exists()


def test_default_out_lives_under_the_qubit_home(monkeypatch, tmp_path):
    import voicebridge.paths as paths

    monkeypatch.setattr(paths, "qubit_home", lambda: tmp_path / "home")
    assert B.default_out("/purchase/UI Office") == (
        tmp_path / "home" / "office" / "themes" / "ui-office")
    # an unusable basename falls back to the canonical pack id
    assert B.default_out("/purchase/???") == (
        tmp_path / "home" / "office" / "themes" / "limezu")


def test_cli_defaults_out_under_the_qubit_home(tmp_path, monkeypatch):
    import voicebridge.paths as paths

    monkeypatch.setattr(paths, "qubit_home", lambda: tmp_path / "home")
    monkeypatch.setattr(B, "extract_layers", lambda source, size=32: _layers())
    monkeypatch.setattr(B, "_default_personas", lambda: list(PERSONAS))

    assert B.main(["--source", "/purchase/limezu"]) == 0
    out = tmp_path / "home" / "office" / "themes" / "limezu"
    assert (out / "theme.json").is_file()
    assert T.validate_theme(out) == []


def test_cli_recipe_out_writes_only_the_recipe(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "extract_layers", lambda source, size=32: _layers())
    monkeypatch.setattr(B, "_default_personas", lambda: list(PERSONAS))
    recipe_path = tmp_path / "limezu" / "theme.recipe.json"

    assert B.main(["--source", str(tmp_path / "purchase"),
                   "--recipe-out", str(recipe_path)]) == 0
    assert recipe_path.is_file()
    assert sorted(p.name for p in recipe_path.parent.iterdir()) == [
        "theme.recipe.json"]
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    assert T.validate_recipe(recipe) == []
    assert len(recipe["agents"]) == len(PERSONAS)


def test_cli_builds_and_checks_with_injected_extraction(tmp_path, monkeypatch):
    layers = _layers()
    monkeypatch.setattr(B, "extract_layers",
                        lambda source, size=32: layers)
    monkeypatch.setattr(B, "_default_personas", lambda: list(PERSONAS))
    out = tmp_path / "demo"

    assert B.main(["--source", str(tmp_path / "purchase"),
                   "--out", str(out)]) == 0
    assert T.validate_theme(out) == []
    assert B.main(["--source", str(tmp_path / "purchase"),
                   "--out", str(out), "--check"]) == 0

    (out / "agents.json").write_text("{}", encoding="utf-8")
    assert B.main(["--source", str(tmp_path / "purchase"),
                   "--out", str(out), "--check"]) == 1
