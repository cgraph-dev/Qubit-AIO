#!/usr/bin/env python3
"""Deterministic portrait + body atlas builder for Qubit Office themes.

WHAT THIS IS
------------
Task 2 of the office UI theme upgrade. It takes *decoded layers* (Pillow
images, supplied by Task 3's extractor or by a test) and turns them into the
local, served theme pack Task 4 consumes:

    <out>/theme.json               the full ``qubit-office-theme/1`` manifest
    <out>/agents.json              frozen per-persona recipes + part counts
    <out>/agent_sheets.json        the calibration (frames + z-order)
    <out>/portraits/atlas32.*      one ``size`` tile per persona
    <out>/portraits/index.json     ``slug -> [col, row]``
    <out>/bodies/atlas32.*         one ``frame_w x frame_h`` tile per persona
    <out>/bodies/index.json        ``slug -> [col, row]``

The repo never ships art. The default output is the owner's local
``$QUBIT_HOME/office/themes/<id>``; this module only writes where it is told,
and the tests write to ``tmp_path``.

WHY KEY-BASED RECIPES
---------------------
Every part is chosen from ``sha256(f"{slug}:{part}") % len(parts)`` — the
DiceBear convention. Adding a part later therefore never reshuffles an
existing persona, and the chosen recipe is frozen in ``agents.json`` so it
never changes even if the source lists do.

CALIBRATION (measured on the purchased sheets, 2026-11-09)
----------------------------------------------------------
At the 32 tier the body frame is 32x64 (row 0, col 0 of the body sheet); the
eyes overlay is row 0, col 0; front-idle hair is row 3, col 0; the outfit
bottom is row 0, row 7 is the shared undershirt, row 8 is the outfit's own
colour and row 14 is optional shoes; a portrait source cell is 64x64 at row 0,
col 0 and is downscaled with ``Image.NEAREST``. Some outfit files have an
empty row 7/8/14 — the builder skips an empty outfit row rather than failing.

The rows are frame indices, so the same calibration works at the 16 and 48
tiers (the frame is ``size x 2*size``).

To review the calibration against the real sheets, ``--calibrate`` writes one
labelled frame-grid PNG per body-view sheet (body / hair / eyes / outfit) with
row/column labels, separators and a green outline on every currently calibrated
frame. It is read-only on the purchase zips and defaults to the sibling
``<out>.calibrate`` so the grids never land inside a pack directory and a
later ``--check`` stays clean (use ``--calibrate-out DIR`` for an explicit
location).

NO DUPLICATED CONTRACT LOGIC
----------------------------
The pack is accepted only when ``voicebridge.office.theme.validate_theme``
returns no problems; this module does not re-implement the contract.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# When run as ``python scripts/office_theme_build.py`` the repo root is not on
# sys.path (Python adds the script's directory instead). Make the package
# importable without requiring PYTHONPATH.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402  (build-time dependency only)

from voicebridge.office import theme as theme_contract  # noqa: E402

#: The shareable/loadable theme contract this pack targets.
TEMPLATE_VERSION = 1
VERSION = "1.0.0"

#: Default pack provenance. ``source`` in the manifest is a *description*;
#: the CLI's ``--source`` (a filesystem path) is only ever given to the
#: extractor, never written into the manifest.
DEFAULT_SOURCE = (
    "owner purchase of LimeZu Modern Interiors + Modern User Interface "
    "(built locally, 2026-11-09)"
)
DEFAULT_CREDIT = "LimeZu — https://limezu.itch.io"
DEFAULT_LICENSE = (
    "LimeZu — Modern Interiors / Modern User Interface licence; "
    "redistribution prohibited; credit required"
)

DEFAULT_COLS = 20
BODY_VARIANT = 0

#: Z-order, exactly as measured. The body draws the base body first, then the
#: outfit's rows, then the shared eyes and hair. The portrait draws the skin,
#: then eyes, hair, and (optional) an accessory.
PORTRAIT_ORDER: Tuple[str, ...] = ("skin", "eyes", "hair", "accessory")
BODY_ORDER: Tuple[str, ...] = (
    "body",
    "outfit_bottom",
    "shoes",
    "outfit_base_top",
    "outfit_top",
    "eyes",
    "hair",
)

#: Outfit rows, in the BODY_ORDER order above.
OUTFIT_FRAMES: Tuple[Tuple[str, int], ...] = (
    ("outfit_bottom", 0),
    ("shoes", 14),
    ("outfit_base_top", 7),
    ("outfit_top", 8),
)

#: The calibrated frames (row, col) per logical layer. Rows are frame indices,
#: so they are size-independent.
CALIBRATION: Dict[str, Dict[str, int]] = {
    "body": {"row": 0, "col": 0},
    "eyes": {"row": 0, "col": 0},
    "hair": {"row": 3, "col": 0},
    "outfit_bottom": {"row": 0, "col": 0},
    "outfit_base_top": {"row": 7, "col": 0},
    "outfit_top": {"row": 8, "col": 0},
    "shoes": {"row": 14, "col": 0},
}

#: Expected part counts in the purchased pack. Informational; the real bound is
#: always the discovered list length.
EXPECTED_PART_COUNTS: Dict[str, int] = {
    "skin": 9,
    "eyes": 7,
    "hair": 29,
    "outfit": 33,
}

#: A portrait/body sheet is either a bare ``PIL.Image`` (used for both views)
#: or a mapping with ``portrait`` and/or ``body`` images.
Sheet = Any

_ALIASES: Dict[str, str] = {
    "skins": "skin",
    "hairstyle": "hair",
    "hairstyles": "hair",
    "outfits": "outfit",
    "accessories": "accessory",
    "bodies": "body",
}
_KNOWN_PARTS = {"skin", "eyes", "hair", "outfit", "accessory", "body"}

DEFAULT_TOKENS: Dict[str, str] = {
    "color-surface": "#0d0d1a",
    "color-surface-raised": "#161a23",
    "color-surface-hover": "#1d2230",
    "color-text-primary": "#e9edf7",
    "color-text-muted": "#8a93a8",
    "color-border": "#334155",
    "color-accent": "#7cc8ff",
    "radius-control": "4px",
}


class ThemeBuildError(Exception):
    """The pack could not be built. ``problems`` carries every reason."""

    def __init__(self, problems: Iterable[str] | str):
        if isinstance(problems, str):
            self.problems: List[str] = [problems]
        else:
            self.problems = list(problems)
        super().__init__("; ".join(self.problems) or "theme build failed")


# ---------------------------------------------------------------------------
# Layer normalisation
# ---------------------------------------------------------------------------
def _is_ascii_alnum(ch: str) -> bool:
    return ("0" <= ch <= "9") or ("a" <= ch <= "z")


def _slug_ok(slug: str) -> bool:
    if not isinstance(slug, str) or not (1 <= len(slug) <= 64):
        return False
    if not _is_ascii_alnum(slug[0]):
        return False
    return all(_is_ascii_alnum(ch) or ch in "_-" for ch in slug)


def _theme_id_ok(theme_id: str) -> bool:
    if not isinstance(theme_id, str) or not (1 <= len(theme_id) <= 40):
        return False
    if not _is_ascii_alnum(theme_id[0]):
        return False
    return all(_is_ascii_alnum(ch) or ch == "-" for ch in theme_id)


def _normalize_layers(layers: Any) -> Dict[str, List[Sheet]]:
    """Canonicalise ``{part: [sheets]}`` and reject empty/unknown parts."""
    if layers is None:
        raise ThemeBuildError(
            "layers are required: extraction from the purchase is supplied by "
            "Task 3 (scripts/office_theme_extract.py::extract_layers)")
    if not isinstance(layers, Mapping):
        raise ThemeBuildError("layers: must be a mapping of part -> sequence")
    normalized: Dict[str, List[Sheet]] = {}
    for raw_key, raw_seq in layers.items():
        if not isinstance(raw_key, str):
            raise ThemeBuildError(f"layers: invalid part name {raw_key!r}")
        key = _ALIASES.get(raw_key, raw_key)
        if key not in _KNOWN_PARTS:
            raise ThemeBuildError(f"layers: unknown part {raw_key!r}")
        if key in normalized:
            raise ThemeBuildError(f"layers: part {key!r} supplied twice")
        if isinstance(raw_seq, (Image.Image, Mapping, str, bytes)):
            raise ThemeBuildError(
                f"layers[{raw_key}]: must be a sequence of sheets")
        try:
            items = list(raw_seq)
        except TypeError as exc:
            raise ThemeBuildError(
                f"layers[{raw_key}]: must be a sequence of sheets") from exc
        if not items:
            raise ThemeBuildError(f"layers[{raw_key}]: is empty")
        normalized[key] = items
    return normalized


def _sheet_image(sheet: Sheet, view: str) -> Image.Image:
    """Return the RGBA image for one view of a sheet.

    A bare ``PIL.Image`` is the explicit "same art for both views" form. A
    mapping must carry the requested view (``portrait`` or ``body``); falling
    back to the other view would silently compose a body frame out of a
    portrait sheet (or vice versa) and still pass QA, so it is refused.
    """
    if isinstance(sheet, Mapping):
        image = sheet.get(view)
        if image is None:
            present = ", ".join(sorted(str(key) for key in sheet)) or "nothing"
            raise ThemeBuildError(
                f"layer sheet is missing the {view!r} view (has: {present})")
    else:
        image = sheet
    if not isinstance(image, Image.Image):
        raise ThemeBuildError(
            f"layer sheet for {view!r} is not a PIL image "
            f"(got {type(image).__name__})")
    return image if image.mode == "RGBA" else image.convert("RGBA")


def _require(layers: Mapping[str, List[Sheet]], parts: Iterable[str]) -> None:
    missing = [part for part in parts if part not in layers]
    if missing:
        raise ThemeBuildError(
            [f"layers: missing required part {part!r}" for part in missing])


# ---------------------------------------------------------------------------
# Recipes
# ---------------------------------------------------------------------------
def _pick_index(slug: str, part: str, count: int) -> int:
    digest = hashlib.sha256(f"{slug}:{part}".encode("utf-8")).digest()
    return int.from_bytes(digest, "big") % count


def _derive_recipe(slug: str, layers: Mapping[str, List[Sheet]]) -> Dict[str, int]:
    recipe: Dict[str, int] = {}
    for part in ("skin", "eyes", "hair", "outfit", "accessory"):
        sheets = layers.get(part)
        if sheets:
            recipe[part] = _pick_index(slug, part, len(sheets))
    return recipe


def derive_recipe(slug: str, layers: Any) -> Dict[str, int]:
    """The frozen recipe for ``slug`` (key-based, never sequential)."""
    return _derive_recipe(slug, _normalize_layers(layers))


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------
def _crop_frame(sheet: Image.Image, *, row: int, col: int, width: int,
                height: int, part: str, empty_ok: bool) -> Optional[Image.Image]:
    x = col * width
    y = row * height
    if x + width > sheet.width or y + height > sheet.height:
        if empty_ok:
            return None
        raise ThemeBuildError(
            f"{part}: frame row {row} col {col} is outside the "
            f"{sheet.width}x{sheet.height} sheet")
    tile = sheet.crop((x, y, x + width, y + height))
    if tile.getchannel("A").getbbox() is None:
        if empty_ok:
            return None
        raise ThemeBuildError(f"{part}: frame row {row} col {col} is blank")
    return tile


def _compose_portrait(recipe: Mapping[str, int], layers: Mapping[str, List[Sheet]],
                      size: int) -> Image.Image:
    _require(layers, ("skin", "eyes", "hair"))
    cell = size * 2
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    for part in PORTRAIT_ORDER:
        index = recipe.get(part)
        if index is None:
            continue
        sheet = _sheet_image(layers[part][index], "portrait")
        tile = _crop_frame(sheet, row=0, col=0, width=cell, height=cell,
                           part=part, empty_ok=(part == "accessory"))
        if tile is None:
            continue
        canvas.alpha_composite(tile.resize((size, size), Image.NEAREST))
    return canvas


def _compose_body(recipe: Mapping[str, int], layers: Mapping[str, List[Sheet]],
                  size: int) -> Image.Image:
    _require(layers, ("body", "outfit", "eyes", "hair"))
    frame_w, frame_h = size, size * 2
    canvas = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))

    # The portrait's skin index selects the matching body tone. The bodies and
    # skins are separate lists (Character Bodies vs Portrait Skins), so clamp
    # into the body list; an absent/invalid skin index keeps the historical
    # first-body fallback.
    skin_index = recipe.get("skin")
    body_list = layers["body"]
    if isinstance(skin_index, int) and not isinstance(skin_index, bool) \
            and skin_index >= 0:
        body_index = min(skin_index, len(body_list) - 1)
    else:
        body_index = BODY_VARIANT
    base = _sheet_image(body_list[body_index], "body")
    canvas.alpha_composite(_crop_frame(
        base, row=0, col=0, width=frame_w, height=frame_h,
        part="body", empty_ok=False))

    outfit = _sheet_image(layers["outfit"][recipe["outfit"]], "body")
    for part, row in OUTFIT_FRAMES:
        tile = _crop_frame(outfit, row=row, col=0, width=frame_w, height=frame_h,
                           part=part, empty_ok=True)
        if tile is not None:
            canvas.alpha_composite(tile)

    for part, row in (("eyes", 0), ("hair", 3)):
        sheet = _sheet_image(layers[part][recipe[part]], "body")
        canvas.alpha_composite(_crop_frame(
            sheet, row=row, col=0, width=frame_w, height=frame_h,
            part=part, empty_ok=False))
    return canvas


def _encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def encode_png(image: Image.Image) -> bytes:
    """PNG bytes for ``image`` (lossless, always available)."""
    return _encode_png(image)


def encode_webp(image: Image.Image) -> bytes:
    """Lossless WebP bytes for ``image``."""
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", lossless=True, quality=100)
    return buffer.getvalue()


def encode_atlas(image: Image.Image) -> Tuple[str, bytes]:
    """The smaller of lossless WebP and PNG, with its file suffix."""
    png = _encode_png(image)
    try:
        webp = encode_webp(image)
    except Exception:  # noqa: BLE001 - a Pillow without WebP falls back to PNG
        return ".png", png
    if len(webp) < len(png):
        return ".webp", webp
    return ".png", png


def portrait_bytes(slug: str, layers: Any, size: int = 32) -> bytes:
    """The PNG bytes of one persona's composed portrait."""
    _check_size(size)
    normalized = layers if isinstance(layers, Mapping) else _normalize_layers(layers)
    recipe = _derive_recipe(slug, normalized)
    return _encode_png(_compose_portrait(recipe, normalized, size))


def body_bytes(slug: str, layers: Any, size: int = 32) -> bytes:
    """The PNG bytes of one persona's composed body frame."""
    _check_size(size)
    normalized = layers if isinstance(layers, Mapping) else _normalize_layers(layers)
    recipe = _derive_recipe(slug, normalized)
    return _encode_png(_compose_body(recipe, normalized, size))


# ---------------------------------------------------------------------------
# QA
# ---------------------------------------------------------------------------
def _decode(image_bytes: bytes) -> Optional[Image.Image]:
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except Exception:  # noqa: BLE001 - any decode failure is a QA problem
        return None
    return image.convert("RGBA")


def _alpha_problems(image: Image.Image, label: str) -> List[str]:
    total = image.width * image.height
    if total <= 0:
        return [f"{label}: empty image"]
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    opaque = sum(alpha.histogram()[1:])
    coverage = opaque / total
    if bbox is None or opaque == 0:
        return [f"{label}: blank (alpha coverage 0.0%)"]
    problems: List[str] = []
    if coverage < 0.02:
        problems.append(
            f"{label}: alpha coverage {coverage:.1%} below the 2% floor")
    if coverage > 0.98:
        problems.append(
            f"{label}: alpha coverage {coverage:.1%} above the 98% ceiling")
    return problems


def qa_portrait(png_bytes: bytes) -> List[str]:
    """Every reason a portrait tile is unusable (empty list = acceptable)."""
    image = _decode(png_bytes)
    if image is None:
        return ["portrait: not a decodable image"]
    problems: List[str] = []
    if image.width != image.height:
        problems.append("portrait: tile must be square")
    problems.extend(_alpha_problems(image, "portrait"))
    return problems


def _body_problems(image: Image.Image, frame_h: int) -> List[str]:
    problems: List[str] = []
    if image.height != frame_h:
        problems.append(
            f"body: tile height {image.height} != frame height {frame_h}")
    problems.extend(_alpha_problems(image, "body"))
    bbox = image.getchannel("A").getbbox()
    if bbox is not None and bbox[3] < frame_h // 2:
        problems.append(
            f"body: feet end at y={bbox[3]} outside the lower half of the "
            f"{frame_h}px frame")
    return problems


def qa_body(png_bytes: bytes, *, frame_h: int) -> List[str]:
    """Every reason a body tile is unusable (empty list = acceptable)."""
    image = _decode(png_bytes)
    if image is None:
        return ["body: not a decodable image"]
    return _body_problems(image, frame_h)


# ---------------------------------------------------------------------------
# agent_sheets.json (build-side calibration)
# ---------------------------------------------------------------------------
def calibration_document(size: int, parts: Mapping[str, int]) -> Dict[str, Any]:
    """The build-side calibration record (frames, z-order, part counts)."""
    return {
        "schema": 1,
        "template_version": TEMPLATE_VERSION,
        "frame": {"w": size, "h": size * 2},
        "portrait": {"cell": size * 2, "size": size, "row": 0, "col": 0},
        "frames": {name: dict(frame) for name, frame in CALIBRATION.items()},
        "z_order": {
            "portrait": list(PORTRAIT_ORDER),
            "body": list(BODY_ORDER),
        },
        "parts": dict(parts),
        "expected_parts": dict(EXPECTED_PART_COUNTS),
    }


def validate_agent_sheets(document: Any) -> List[str]:
    """Validate a build-side ``agent_sheets.json`` against the calibration."""
    if not isinstance(document, dict):
        return ["agent_sheets: must be an object"]
    problems: List[str] = []
    if document.get("schema") != 1:
        problems.append("agent_sheets.schema: must be 1")
    template_version = document.get("template_version")
    if (not isinstance(template_version, int) or isinstance(template_version, bool)
            or template_version <= 0):
        problems.append("agent_sheets.template_version: must be a positive integer")

    frame = document.get("frame")
    if not isinstance(frame, dict):
        problems.append("agent_sheets.frame: must be an object")
        frame = {}
    for key in ("w", "h"):
        value = frame.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            problems.append(f"agent_sheets.frame.{key}: must be a positive integer")
    if (isinstance(frame.get("w"), int) and isinstance(frame.get("h"), int)
            and frame.get("h") != frame.get("w") * 2):
        problems.append("agent_sheets.frame: height must be twice the width")

    portrait = document.get("portrait")
    if not isinstance(portrait, dict):
        problems.append("agent_sheets.portrait: must be an object")
        portrait = {}
    for key in ("cell", "size"):
        value = portrait.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            problems.append(f"agent_sheets.portrait.{key}: must be a positive integer")
    if (isinstance(portrait.get("cell"), int) and isinstance(portrait.get("size"), int)
            and portrait.get("cell") != portrait.get("size") * 2):
        problems.append("agent_sheets.portrait.cell: must be twice the size")
    for key in ("row", "col"):
        value = portrait.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            problems.append(f"agent_sheets.portrait.{key}: must be a non-negative integer")

    frames = document.get("frames")
    if not isinstance(frames, dict):
        problems.append("agent_sheets.frames: must be an object")
        frames = {}
    for name, expected in CALIBRATION.items():
        frame = frames.get(name)
        if not isinstance(frame, dict):
            problems.append(f"agent_sheets.frames.{name}: must be an object")
            continue
        for key in ("row", "col"):
            value = frame.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                problems.append(
                    f"agent_sheets.frames.{name}.{key}: must be a non-negative integer")
        if frame.get("row") != expected["row"] or frame.get("col") != expected["col"]:
            problems.append(
                f"agent_sheets.frames.{name}: must be the calibrated "
                f"row {expected['row']} col {expected['col']}")

    z_order = document.get("z_order")
    if not isinstance(z_order, dict):
        problems.append("agent_sheets.z_order: must be an object")
    else:
        if list(z_order.get("portrait") or []) != list(PORTRAIT_ORDER):
            problems.append("agent_sheets.z_order.portrait: does not match the z-order")
        if list(z_order.get("body") or []) != list(BODY_ORDER):
            problems.append("agent_sheets.z_order.body: does not match the z-order")

    parts = document.get("parts")
    if not isinstance(parts, dict):
        problems.append("agent_sheets.parts: must be an object")
    else:
        for name, count in parts.items():
            if not isinstance(name, str) or name not in _KNOWN_PARTS:
                problems.append(f"agent_sheets.parts: unknown part {name!r}")
                continue
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                problems.append(
                    f"agent_sheets.parts.{name}: must be a non-negative integer")
    return problems


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------
def _check_size(size: int) -> None:
    if size not in (16, 32, 48):
        raise ThemeBuildError(f"size: must be 16, 32, or 48 (got {size!r})")


def _looks_like_path(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered.startswith(("/", "~/", "~\\", "\\\\")):
        return True
    if len(lowered) > 2 and lowered[1] == ":" and lowered[2] in "\\/":
        return True
    if "/home/" in lowered or "\\users\\" in lowered:
        return True
    if ".." in re.split(r"[\\/]", lowered):
        return True
    return False


def _default_theme_id(source: str) -> str:
    """A safe theme id from the purchase directory name (fallback ``limezu``)."""
    name = Path(str(source).rstrip("/\\")).name or "limezu"
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")[:40].strip("-")
    return slug if _theme_id_ok(slug) else "limezu"


def default_out(source: str) -> Path:
    """The local, never-committed default pack location for ``source``."""
    from voicebridge.paths import qubit_home

    return qubit_home() / "office" / "themes" / _default_theme_id(source)


def _default_personas() -> List[str]:
    from voicebridge.office import agency_org

    return [row["slug"] for row in agency_org.personas()]


def _resolve_personas(personas: Any) -> List[str]:
    if personas is None:
        slugs = list(_default_personas())
    else:
        if isinstance(personas, (str, bytes, Mapping)):
            raise ThemeBuildError(
                "personas: must be a sequence of slugs, not a single value")
        slugs = []
        for item in personas:
            slug = item.get("slug") if isinstance(item, Mapping) else item
            if not _slug_ok(slug):
                raise ThemeBuildError(f"personas: invalid slug {slug!r}")
            slugs.append(slug)
    if not slugs:
        raise ThemeBuildError("personas: at least one persona is required")
    seen: set[str] = set()
    duplicates: List[str] = []
    for slug in slugs:
        if slug in seen:
            duplicates.append(slug)
        seen.add(slug)
    if duplicates:
        raise ThemeBuildError(
            [f"personas: duplicate slug {slug!r}" for slug in sorted(set(duplicates))])
    return sorted(slugs)


def _json_bytes(document: Any) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def shareable_recipe(manifest: Mapping[str, Any],
                     recipes: Mapping[str, Mapping[str, int]]) -> Dict[str, Any]:
    """The shareable ``theme.recipe.json`` derived from a built manifest."""
    return {
        "format": manifest["format"],
        "schema": manifest["schema"],
        "id": manifest["id"],
        "label": manifest["label"],
        "version": manifest["version"],
        "credit": manifest["credit"],
        "license": manifest["license"],
        "source": manifest["source"],
        "template_version": manifest["template_version"],
        "tokens": dict(manifest["tokens"]),
        "slices": dict(manifest.get("slices") or {}),
        "parts": dict(manifest["parts"]),
        "agents": {slug: dict(recipe) for slug, recipe in recipes.items()},
    }


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def _publish(temp_root: Path, out: Path) -> None:
    """Move the validated temp pack into place, restoring on failure."""
    backup: Optional[Path] = None
    if out.exists():
        backup = out.with_name(f".{out.name}.old-{os.getpid()}")
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        os.replace(out, backup)
    try:
        os.replace(temp_root, out)
    except Exception:
        if backup is not None and not out.exists():
            os.replace(backup, out)
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)


def build_theme(source: str, out: os.PathLike | str, *, size: int = 32,
                personas: Any = None, layers: Any = None,
                license: str = DEFAULT_LICENSE,
                credit: str = DEFAULT_CREDIT,
                tokens: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Build a local theme pack and return its manifest.

    ``layers`` must be supplied (Task 3's extractor does that for the CLI);
    ``source`` is the manifest *description*, never a filesystem path.
    The pack is written to a temporary sibling, validated with the real
    contract, and only then moved into place.

    ``tokens`` overrides the default palette. The caller is expected to have
    validated it (the extractor's ``--tokens`` does); an invalid map is still
    refused by the contract when the temp pack is validated below.
    """
    problems: List[str] = []
    _check_size(size)
    if not isinstance(source, str) or not source.strip():
        problems.append("source: must be a non-empty description")
    elif _looks_like_path(source):
        problems.append("source: must be a description, not a filesystem path")
    if not isinstance(credit, str) or not credit.strip():
        problems.append("credit: must be a non-empty string")
    if not isinstance(license, str) or not license.strip():
        problems.append("license: must be a non-empty string")

    out_path = Path(out)
    if not _theme_id_ok(out_path.name):
        problems.append(
            "id: output directory name must match ^[a-z0-9][a-z0-9-]{0,39}$ "
            f"(got {out_path.name!r})")
    if problems:
        raise ThemeBuildError(problems)

    normalized = _normalize_layers(layers)
    _require(normalized, ("skin", "eyes", "hair", "outfit", "body"))
    slugs = _resolve_personas(personas)
    cols = DEFAULT_COLS
    rows = max(1, math.ceil(len(slugs) / cols))

    # Frozen recipes (one per slug) drive both the portrait and the body.
    recipes = {slug: _derive_recipe(slug, normalized) for slug in slugs}
    parts = {
        part: len(normalized[part])
        for part in ("skin", "eyes", "hair", "outfit", "accessory")
        if normalized.get(part)
    }

    # Compose + QA every tile before writing anything.
    portrait_tiles: List[Image.Image] = []
    body_tiles: List[Image.Image] = []
    qa_problems: List[str] = []
    for slug in slugs:
        portrait = _compose_portrait(recipes[slug], normalized, size)
        body = _compose_body(recipes[slug], normalized, size)
        qa_problems.extend(
            f"{slug}: {problem}"
            for problem in _alpha_problems(portrait, "portrait"))
        qa_problems.extend(
            f"{slug}: {problem}"
            for problem in _body_problems(body, size * 2))
        portrait_tiles.append(portrait)
        body_tiles.append(body)
    if qa_problems:
        raise ThemeBuildError(qa_problems)

    portrait_atlas = Image.new(
        "RGBA", (cols * size, rows * size), (0, 0, 0, 0))
    body_atlas = Image.new(
        "RGBA", (cols * size, rows * size * 2), (0, 0, 0, 0))
    cells: Dict[str, List[int]] = {}
    for index, slug in enumerate(slugs):
        col, row = index % cols, index // cols
        cells[slug] = [col, row]
        portrait_atlas.paste(portrait_tiles[index], (col * size, row * size))
        body_atlas.paste(body_tiles[index], (col * size, row * size * 2))

    portrait_ext, portrait_data = encode_atlas(portrait_atlas)
    body_ext, body_data = encode_atlas(body_atlas)

    portrait_atlas_rel = f"portraits/atlas{size}{portrait_ext}"
    body_atlas_rel = f"bodies/atlas{size}{body_ext}"
    portrait_index = {
        "schema": 1, "size": size, "cols": cols, "rows": rows, "cells": cells,
    }
    body_index = {
        "schema": 1, "size": size, "cols": cols, "rows": rows,
        "frame": {"w": size, "h": size * 2}, "cells": cells,
    }
    agents_doc = {
        "schema": 1,
        "templateVersion": TEMPLATE_VERSION,
        "parts": parts,
        "recipes": recipes,
    }
    sheets_doc = calibration_document(size, parts)
    sheet_problems = validate_agent_sheets(sheets_doc)
    if sheet_problems:
        raise ThemeBuildError(sheet_problems)

    payloads: Dict[str, bytes] = {
        portrait_atlas_rel: portrait_data,
        "portraits/index.json": _json_bytes(portrait_index),
        body_atlas_rel: body_data,
        "bodies/index.json": _json_bytes(body_index),
        "agents.json": _json_bytes(agents_doc),
        "agent_sheets.json": _json_bytes(sheets_doc),
    }

    label = out_path.name.replace("-", " ").replace("_", " ").title()
    manifest: Dict[str, Any] = {
        "format": theme_contract.FORMAT,
        "schema": theme_contract.SCHEMA,
        "id": out_path.name,
        "label": label,
        "version": VERSION,
        "credit": credit,
        "license": license,
        "source": source,
        "template_version": TEMPLATE_VERSION,
        "tokens": dict(tokens or DEFAULT_TOKENS),
        "slices": {},
        "portraits": {
            "atlas": portrait_atlas_rel,
            "index": "portraits/index.json",
            "size": size,
            "cols": cols,
            "rows": rows,
        },
        "bodies": {
            "atlas": body_atlas_rel,
            "index": "bodies/index.json",
            "size": size,
            "frame": {"w": size, "h": size * 2},
            "cols": cols,
            "rows": rows,
        },
        "parts": parts,
        "files": {
            relative: {"sha256": _sha(data), "bytes": len(data)}
            for relative, data in sorted(payloads.items())
        },
        "limits": {"max_total_bytes": 64 * 1024 * 1024, "max_files": 32},
    }

    structural = theme_contract.validate_manifest(manifest)
    if structural:
        raise ThemeBuildError(structural)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(
        prefix=f".{out_path.name}.build-", dir=str(out_path.parent)))
    try:
        for relative, data in payloads.items():
            target = temp_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (temp_root / "theme.json").write_bytes(_json_bytes(manifest))

        load_problems = theme_contract.validate_theme(temp_root)
        if load_problems:
            raise ThemeBuildError(load_problems)
        _publish(temp_root, out_path)
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)
    return manifest


_IMAGE_SUFFIXES = frozenset({".png", ".webp", ".gif", ".jpg", ".jpeg"})


def _same_payload(relative: str, left: bytes, right: bytes) -> bool:
    """Compare a pack file semantically: decoded pixels, or sorted JSON.

    Raw PNG/WebP bytes are not a reproducibility contract — a re-encode or a
    different compression level changes them without changing the pack. JSON
    object key order and indentation are likewise presentation. Everything
    else still compares byte-for-byte.
    """
    if relative.endswith(".json"):
        try:
            return json.loads(left) == json.loads(right)
        except (ValueError, TypeError):
            return left == right
    suffix = Path(relative).suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        decoded: List[Image.Image] = []
        try:
            for data in (left, right):
                image = Image.open(io.BytesIO(data))
                image.load()
                decoded.append(image.convert("RGBA"))
        except Exception:  # noqa: BLE001 - fall back to exact bytes
            return left == right
        return (decoded[0].size == decoded[1].size
                and decoded[0].tobytes() == decoded[1].tobytes())
    return left == right


def compare_packs(reference: os.PathLike | str,
                  rebuilt: os.PathLike | str) -> List[str]:
    """Every difference between two built packs (empty = identical).

    Comparison is semantic: image files are decoded and compared by pixels
    (not container bytes), and JSON files by parsed value (not key order or
    indentation). This is what ``--check`` promises — a rebuild from the same
    source reproduces the same *pack*, whatever encoder/formatting wrote it.
    """
    reference = Path(reference)
    rebuilt = Path(rebuilt)
    problems: List[str] = []

    def files(root: Path) -> Dict[str, bytes]:
        found: Dict[str, bytes] = {}
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    found[path.relative_to(root).as_posix()] = path.read_bytes()
        return found

    left = files(reference)
    right = files(rebuilt)
    for relative in sorted(set(left) - set(right)):
        problems.append(f"missing: {relative}")
    for relative in sorted(set(right) - set(left)):
        problems.append(f"extra: {relative}")
    for relative in sorted(set(left) & set(right)):
        if not _same_payload(relative, left[relative], right[relative]):
            problems.append(f"changed: {relative}")
    return problems


# ---------------------------------------------------------------------------
# Calibration contact sheets (--calibrate)
# ---------------------------------------------------------------------------
#: Contact-sheet parts and the calibrated frames each one carries, in
#: ``CALIBRATION`` keys. Portrait views are deliberately absent: the grid is
#: the *body* frame (``size x 2*size``).
CALIBRATE_PARTS: Tuple[str, ...] = ("body", "hair", "eyes", "outfit")
_PART_FRAMES: Dict[str, Tuple[str, ...]] = {
    "body": ("body",),
    "eyes": ("eyes",),
    "hair": ("hair",),
    "outfit": ("outfit_bottom", "outfit_base_top", "outfit_top", "shoes"),
}


def _calibrated_frames(part: str) -> List[Dict[str, int]]:
    return [dict(CALIBRATION[name]) for name in _PART_FRAMES[part]]


def _labelled_grid(sheet: Image.Image, *, part: str, size: int,
                   index: int) -> Image.Image:
    """A contact sheet with row/column labels, separators and the live frames.

    The returned image is exactly ``cols*frame_w x rows*frame_h`` so its
    dimensions stay divisible by the frame size; labels and separators are
    drawn *inside* the cells rather than in margins. Calibrated frames get a
    green outline so a human can confirm or change them at a glance.
    """
    frame_w, frame_h = size, size * 2
    grid = sheet.convert("RGBA").copy()
    rows, cols = grid.height // frame_h, grid.width // frame_w
    overlay = Image.new("RGBA", grid.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default()

    for col in range(1, cols):
        x = col * frame_w
        draw.line([(x, 0), (x, grid.height - 1)],
                  fill=(255, 0, 255, 150), width=1)
    for row in range(1, rows):
        y = row * frame_h
        draw.line([(0, y), (grid.width - 1, y)],
                  fill=(255, 0, 255, 150), width=1)

    for row in range(rows):
        for col in range(cols):
            text = f"r{row}c{col}"
            x = col * frame_w + 2
            y = row * frame_h + 2
            box = draw.textbbox((x, y), text, font=font)
            draw.rectangle((box[0] - 1, box[1] - 1, box[2] + 1, box[3] + 1),
                           fill=(0, 0, 0, 170))
            draw.text((x, y), text, font=font, fill=(255, 255, 160, 255))

    for frame in _calibrated_frames(part):
        row, col = frame["row"], frame["col"]
        if row >= rows or col >= cols:
            continue
        x0, y0 = col * frame_w + 1, row * frame_h + 1
        draw.rectangle((x0, y0, x0 + frame_w - 2, y0 + frame_h - 2),
                       outline=(60, 255, 120, 255), width=2)

    return Image.alpha_composite(grid, overlay)


def default_calibrate_out(out: os.PathLike | str) -> Path:
    """The default ``--calibrate`` directory: a sibling of the pack.

    It must never be *inside* the pack: ``compare_packs`` walks every file and
    would report the grids as ``extra``/``missing`` on the next ``--check``.
    """
    out_path = Path(out)
    return out_path.with_name(f"{out_path.name}.calibrate")


def _calibrate(args: argparse.Namespace,
               layers: Mapping[str, List[Sheet]]) -> int:
    """Write one labelled frame-grid PNG per body-view sheet."""
    size = args.size
    frame_w, frame_h = size, size * 2
    out = Path(args.calibrate_out) if args.calibrate_out \
        else default_calibrate_out(args.out)
    out.mkdir(parents=True, exist_ok=True)

    parts: Dict[str, List[Dict[str, Any]]] = {}
    for part in CALIBRATE_PARTS:
        entries: List[Dict[str, Any]] = []
        for index, sheet in enumerate(layers[part], start=1):
            image = _sheet_image(sheet, "body")
            if image.width % frame_w or image.height % frame_h:
                raise ThemeBuildError(
                    f"calibrate: {part} sheet {index} is "
                    f"{image.width}x{image.height}, not a multiple of the "
                    f"{frame_w}x{frame_h} frame")
            rows, cols = image.height // frame_h, image.width // frame_w
            grid = _labelled_grid(image, part=part, size=size, index=index)
            name = f"{part}_{index:02d}.png"
            (out / name).write_bytes(_encode_png(grid))
            entries.append({
                "index": index,
                "file": name,
                "rows": rows,
                "cols": cols,
                "frame": {"w": frame_w, "h": frame_h},
                "calibrated_frames": _calibrated_frames(part),
            })
        parts[part] = entries

    document = {
        "schema": 1,
        "template_version": TEMPLATE_VERSION,
        "size": size,
        "frame": {"w": frame_w, "h": frame_h},
        "calibration": {name: dict(frame)
                        for name, frame in CALIBRATION.items()},
        "parts": parts,
    }
    (out / "calibrate.json").write_bytes(_json_bytes(document))
    print(json.dumps({
        "ok": True,
        "calibrate": str(out),
        "files": sum(len(entries) for entries in parts.values()),
    }, indent=2, sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def extract_layers(source: str, *, size: int = 32) -> Dict[str, List[Sheet]]:
    """Turn the purchased art at ``source`` into in-memory part sheets.

    The extraction lives in ``scripts/office_theme_extract.py`` (Task 3) and
    reads the owner's zips in memory. A missing extractor is still a typed
    error rather than an invented extraction.
    """
    try:
        import office_theme_extract as extractor  # type: ignore
    except Exception as exc:  # noqa: BLE001 - missing module or its deps
        raise ThemeBuildError(
            "theme extraction is supplied by Task 3 "
            "(scripts/office_theme_extract.py::extract_layers); "
            "tests pass layers= to build_theme instead") from exc
    function = getattr(extractor, "extract_layers", None)
    if not callable(function):
        raise ThemeBuildError(
            "scripts/office_theme_extract.py does not export extract_layers")
    return function(source, size=size)


def _check(args: argparse.Namespace,
           layers: Mapping[str, List[Sheet]]) -> int:
    out = Path(args.out)
    if not out.is_dir():
        raise ThemeBuildError(f"check: {out} does not exist")
    with tempfile.TemporaryDirectory(prefix="office-theme-check-") as work:
        rebuilt = Path(work) / out.name
        build_theme(DEFAULT_SOURCE, rebuilt, size=args.size, layers=layers)
        problems = compare_packs(out, rebuilt)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    print("OK")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="office_theme_build.py",
        description=(
            "Build a deterministic Qubit Office portrait + body theme pack "
            "from the owner's purchased art."))
    parser.add_argument(
        "--source", required=True,
        help="path to the purchased art (extraction is supplied by Task 3)")
    parser.add_argument(
        "--out", default=None,
        help="output theme directory "
             "(default: $QUBIT_HOME/office/themes/<id>, never committed)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="store_true",
        help="verify --out is a deterministic rebuild; write nothing")
    mode.add_argument(
        "--calibrate", action="store_true",
        help="write labelled frame-grid contact sheets for the body-view "
             "sheets instead of building a pack (read-only on the zips)")
    parser.add_argument(
        "--calibrate-out", default=None,
        help="directory for --calibrate output "
             "(default: <out>.calibrate, beside the pack)")
    parser.add_argument("--size", type=int, default=32, choices=(16, 32, 48),
                        help="tile size (default 32)")
    parser.add_argument(
        "--ui-sheet", default=None,
        help="sample the palette from the owner's Modern UI sheet (png or zip) "
             "and use the validated semantic tokens instead of the defaults")
    args = parser.parse_args(argv)
    if args.calibrate_out and not args.calibrate:
        parser.error("--calibrate-out requires --calibrate")
    args.out = args.out or str(default_out(args.source))

    try:
        layers = extract_layers(args.source, size=args.size)
        if args.check:
            return _check(args, layers)
        if args.calibrate:
            return _calibrate(args, layers)
        tokens = None
        if args.ui_sheet:
            # The palette sampler lives with the extractor (it already opens
            # the purchase in memory); a missing/unsafe sheet is a typed error.
            import office_theme_extract as extractor  # type: ignore
            sampled = extractor.derive_tokens(args.ui_sheet)
            tokens = sampled["tokens"]
            if sampled["fallback"]:
                for reason in sampled["reasons"]:
                    print(f"warning: palette fallback: {reason}", file=sys.stderr)
        manifest = build_theme(DEFAULT_SOURCE, args.out, size=args.size,
                               layers=layers, tokens=tokens)
        recipes = json.loads(
            (Path(args.out) / "agents.json").read_text(encoding="utf-8")
        )["recipes"]
        print(json.dumps({
            "ok": True,
            "id": manifest["id"],
            "files": len(manifest["files"]),
            "recipes": len(recipes),
        }, indent=2, sort_keys=True))
        return 0
    except ThemeBuildError as exc:
        for problem in exc.problems:
            print(f"error: {problem}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
