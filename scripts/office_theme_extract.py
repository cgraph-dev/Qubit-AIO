#!/usr/bin/env python3
"""Extract the owner's purchased LimeZu part sheets, in memory.

WHAT THIS IS
------------
Task 3 of the office UI theme upgrade. ``extract_layers(source)`` turns the
purchased Character Generator 2.0 and Portrait Generator 1.5 zip payloads into
the ``{part: [sheets]}`` mapping ``office_theme_build.build_theme`` composes.

The paid art is **never** extracted to the repo, copied into a build
directory, or committed. Every sheet is opened from a ``zipfile`` member
straight into Pillow and returned as an in-memory ``PIL.Image``; the caller
(the builder CLI) writes only the composed, derived atlas it owns.

SOURCE RESOLUTION
-----------------
``source`` may be:

  * a directory holding the generator zips (canonical names below);
  * a direct path to either generator zip (its sibling zips are searched);
  * a direct path to ``modernuserinterface-win.zip`` (its siblings searched).

The Character Generator zip supplies the body-side sheets (bodies, outfits,
body eyes, body hair). The Portrait Generator zip supplies the portrait-side
sheets; when it is absent the portrait pieces inside
``modernuserinterface-win.zip`` are used instead. A missing or invalid zip is a
typed :class:`office_theme_build.ThemeBuildError` naming the missing file.

LAYOUT (measured on the 2026-11-09 purchase)
--------------------------------------------
Character Generator::

    Character Pieces/Bodies/{s}x{s}/Body_01.png …
    Character Pieces/Outfits/{s}x{s}/Outfit_01_01.png …
    Character Pieces/Eyes/{s}x{s}/Eyes_01.png …
    Character Pieces/Hairstyles/{s}x{s}/Hairstyle_01_01.png …

Portrait Generator::

    Portrait Pieces/Portrait_Generator - {s}x{s}/Skins/Skin 1.png …
    Portrait Pieces/Portrait_Generator - {s}x{s}/Eyes/Eyes 01.png …
    Portrait Pieces/Portrait_Generator - {s}x{s}/Hairstyles/Hairstyle 01 1.png …

Modern User Interface fallback (``s`` = 16 uses ``PG_*`` without the size)::

            {s}x{s}/Portrait_Generator_{s}x{s}/Skins_{s}x{s}/PG_Skin_{s}x{s}_1.png
            {s}x{s}/Portrait_Generator_{s}x{s}/Eyes_{s}x{s}/PG_Eyes_{s}x{s}_01.png
            {s}x{s}/Portrait_Generator_{s}x{s}/Hairstyles_{s}x{s}/PG_Hairstyle_01_{s}x{s}_1.png
            16x16/Portrait_Generator/Skins/PG_Skin_1.png …
            16x16/Portrait_Generator/Eyes/PG_Eyes_01.png …
            16x16/Portrait_Generator/Hairstyles/PG_Hairstyle_01_1.png …

At the 32 tier each portrait sheet is a 640x192 grid of 64x64 cells (the
builder crops row 0, col 0 and downscales); a body sheet is a grid of
``size x 2*size`` frames. ``size`` may be 16, 32, or 48.

PARTS RETURNED
--------------
``skin``    portrait skin (1..9); the body skin comes from the Bodies list.
``eyes``    ``{"portrait": …, "body": …}`` paired by index (1..7).
``hair``    ``{"portrait": …, "body": …}`` paired by style index (1..29);
            extra body colour variants are ignored for v1.
``outfit``  body ``Outfit_{01..33}_01.png`` (bare image, body view only).
``body``    body ``Body_01..09.png`` (bare image, body view only).

``accessory`` is deliberately **not** returned in v1: the portrait generators
carry accessories, but they are not calibrated against the body frame, so a
body accessory would be mis-aligned art. Add it once a body calibration has
been measured and reviewed. The extended catalog below (``extract_assets``)
*does* inventory the accessory sheets as source art, without changing the
composed portrait/body recipes.

EXTENDED ASSET CATALOG
-----------------------
``extract_assets(source)`` reads the rest of the purchased packs in memory and
returns a machine-readable catalog for the local pack builder:

``characters``  body/portrait bodies, outfits, eyes, hair and accessories.
``furniture``   ``Theme_Sorter_Singles`` sprites plus the interior and
                ``Theme_Sorter`` static sheets.
``animated``    ``3_Animated_objects`` spritesheets with frame width/height,
                frame count and fps taken from the matching GIF preview.
``ui``          Modern Interiors UI element sheets and Modern User Interface
                ``Modern_UI_Style_*``/``Gamepad`` sheets at 16/32/48, with
                9-slice candidate flags.
``outside``     Serene Village tiles, props and animated objects (CC-BY 4.0).

The catalog never contains filesystem paths of the purchase and nothing is
written; the builder copies the bytes into the local (never committed) pack.

PALETTE (Task 7)
----------------
``--tokens <sheet.png|zip>`` samples the Modern UI sheet's dominant colours,
maps them onto the semantic tokens (surface/raised/hover/text/muted/border/
accent) and prints the result as JSON. The map is validated with the real
contract (``validate_tokens``: typed values + contrast); when the sampled
palette fails, the current defaults are printed with the reason and
``fallback: true``. ``python scripts/office_theme_build.py --ui-sheet <sheet>``
uses the same validated map for a pack. The sheet is only read — never copied.

CONTACT SHEET (Task 7)
----------------------
``--ui-contact-sheet <sheet.png|zip> --out DIR`` writes an enlarged, labelled
coordinate grid so a human can author ``theme.recipe.json:slices`` by eye. The
purchase is never written into the repository: an ``--out`` inside the checkout
is a typed refusal. The derived sheet is authoring aid only and is not shipped.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import statistics
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from PIL import Image, ImageDraw

# When run as ``python scripts/office_theme_extract.py`` the repo root is not
# on sys.path (Python adds the script's directory instead). Make the package
# importable without requiring PYTHONPATH.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from voicebridge.office import theme as theme_contract  # noqa: E402

try:  # imported as a top-level module (scripts/ is on sys.path)
    from office_theme_build import DEFAULT_TOKENS, ThemeBuildError
except ImportError:  # imported as scripts.office_theme_extract
    from scripts.office_theme_build import DEFAULT_TOKENS, ThemeBuildError  # type: ignore

#: Canonical archive names, in search-preference order.
CHARACTER_ZIP_NAMES: Tuple[str, ...] = ("Character Generator 2.0 Linux Build.zip",)
PORTRAIT_ZIP_NAMES: Tuple[str, ...] = ("Portrait_Generator_1.5.0_Linux_Build.zip",)
FALLBACK_ZIP_NAMES: Tuple[str, ...] = ("modernuserinterface-win.zip",)

_SIZES = (16, 32, 48)

#: Body-side part directories under ``Character Pieces``.
_BODY_PARTS = ("Bodies", "Outfits", "Eyes", "Hairstyles")

#: Canonical interior archive, searched when the source names a directory.
INTERIOR_ZIP_NAMES: Tuple[str, ...] = ("moderninteriors-win.zip",)

#: The measured floor sheet inside the interior archive (16px tiles).
FLOOR_SHEET = "1_Interiors/16x16/Room_Builder_subfiles/Room_Builder_Floors_16x16.png"
FLOOR_TILE = 16

#: ``FloorKind`` -> ``(col, row)`` on :data:`FLOOR_SHEET` (measured 2026-11-09).
#: Only the six kinds the interior pack actually contains are mapped; grass,
#: street and sidewalk are absent from this sheet and keep Habblaud's
#: procedural floor.
FLOOR_MAP: Dict[str, Tuple[int, int]] = {
    "wood": (0, 13),
    "carpet": (0, 16),
    "tile_check": (0, 2),
    "tile_white": (0, 5),
    "marble": (12, 6),
    "concrete": (12, 17),
}


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------
def _zip_names(path: Path) -> List[str]:
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise ThemeBuildError(
            f"{path.name}: not a valid zip archive ({exc})") from exc


def _classify(names: Sequence[str]) -> Optional[str]:
    for name in names:
        norm = name.replace("\\", "/")
        if "Character Pieces/" in norm and "/Bodies/" in norm:
            return "character"
        if "Portrait Pieces/" in norm:
            return "portrait"
        if re.search(r"(?:^|/)Portrait_Generator(?:_\d+x\d+)?/", norm):
            return "fallback"
    return None


def _candidate_zips(directory: Path) -> Dict[str, Path]:
    """Classify the zips directly inside ``directory`` (deterministic order)."""
    found: Dict[str, Path] = {}
    preferred = {
        "character": CHARACTER_ZIP_NAMES,
        "portrait": PORTRAIT_ZIP_NAMES,
        "fallback": FALLBACK_ZIP_NAMES,
    }
    for path in sorted(directory.glob("*.zip"), key=lambda p: p.name):
        if not path.is_file():
            continue
        kind = _classify(_zip_names(path))
        if kind is None:
            continue
        names = preferred[kind]
        if kind in found:
            # Prefer the canonical filename; otherwise keep the first sorted.
            if path.name in names and found[kind].name not in names:
                found[kind] = path
            continue
        found[kind] = path
    # Canonical filenames always win over a same-kind sibling.
    for kind, names in preferred.items():
        for name in names:
            candidate = directory / name
            if candidate.is_file() and kind not in found:
                found[kind] = candidate
    return found


def _resolve_source(source: str) -> Tuple[Path, Path, Optional[Path]]:
    """Return ``(character_zip, portrait_zip, fallback_or_None)``."""
    if not isinstance(source, str) or not source.strip():
        raise ThemeBuildError("source: a purchase directory or zip path is required")
    path = Path(source).expanduser()
    direct: Optional[str] = None
    if path.is_file():
        if path.suffix.lower() != ".zip":
            raise ThemeBuildError(
                f"{path.name}: expected a .zip purchase archive, got "
                f"{path.suffix or 'no extension'}")
        direct = _classify(_zip_names(path))
        if direct is None:
            raise ThemeBuildError(
                f"{path.name}: not a recognised Character Generator, Portrait "
                "Generator, or Modern User Interface archive")
        search_dir = path.parent
    elif path.is_dir():
        search_dir = path
    else:
        raise ThemeBuildError(f"{path}: source not found")

    found = _candidate_zips(search_dir)
    if direct == "character":
        found.setdefault("character", path)
    elif direct == "portrait":
        found.setdefault("portrait", path)
    elif direct == "fallback":
        found.setdefault("fallback", path)

    character = found.get("character")
    if character is None:
        raise ThemeBuildError(
            f"{CHARACTER_ZIP_NAMES[0]}: not found under {search_dir} "
            "(the body sheets are required)")
    portrait = found.get("portrait")
    fallback = found.get("fallback")
    if portrait is None and fallback is None:
        raise ThemeBuildError(
            f"{PORTRAIT_ZIP_NAMES[0]}: not found under {search_dir} "
            f"(name the missing portrait file; fallback "
            f"{FALLBACK_ZIP_NAMES[0]} is also absent)")
    return character, portrait if portrait is not None else fallback, fallback


# ---------------------------------------------------------------------------
# In-memory zip access
# ---------------------------------------------------------------------------
class _Archive:
    """Read PNG members of one zip into Pillow images, never to disk."""

    def __init__(self, path: Path):
        self.path = path
        try:
            self._archive = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ThemeBuildError(
                f"{path.name}: not a valid zip archive ({exc})") from exc
        self._names = [name.replace("\\", "/") for name in self._archive.namelist()]

    def close(self) -> None:
        self._archive.close()

    def __enter__(self) -> "_Archive":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def match(self, pattern: re.Pattern[str]) -> Dict[int, str]:
        """``{index: member name}`` for every member matching ``pattern``."""
        found: Dict[int, str] = {}
        for name in self._names:
            match = pattern.search(name)
            if match is None:
                continue
            found.setdefault(int(match.group(1)), name)
        return found

    def members(self, pattern: re.Pattern[str]) -> List[str]:
        """Every member matching ``pattern`` (no capture group required)."""
        return sorted(name for name in self._names if pattern.search(name))

    def read_image(self, member: str) -> Image.Image:
        try:
            data = self._archive.read(member)
        except (KeyError, OSError, RuntimeError) as exc:
            raise ThemeBuildError(
                f"{self.path.name}: cannot read {member} ({exc})") from exc
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except Exception as exc:  # noqa: BLE001 - any decode failure is typed
            raise ThemeBuildError(
                f"{self.path.name}: {member} is not a decodable image "
                f"({exc})") from exc
        return image if image.mode == "RGBA" else image.convert("RGBA")

    def read_bytes(self, member: str) -> bytes:
        """Raw bytes of a member, so the builder can copy the source exactly."""
        try:
            return self._archive.read(member)
        except (KeyError, OSError, RuntimeError) as exc:
            raise ThemeBuildError(
                f"{self.path.name}: cannot read {member} ({exc})") from exc


def _body_patterns(size: int) -> Dict[str, re.Pattern[str]]:
    tier = re.escape(f"{size}x{size}")
    return {
        "body": re.compile(
            rf"(?:^|/)Character Pieces/Bodies/{tier}/Body_(\d+)\.png$"),
        "outfit": re.compile(
            rf"(?:^|/)Character Pieces/Outfits/{tier}/Outfit_(\d+)_01\.png$"),
        "eyes": re.compile(
            rf"(?:^|/)Character Pieces/Eyes/{tier}/Eyes_(\d+)\.png$"),
        "hair": re.compile(
            rf"(?:^|/)Character Pieces/Hairstyles/{tier}/"
            rf"Hairstyle_(\d+)_01\.png$"),
    }


def _portrait_patterns(size: int, kind: str) -> Dict[str, re.Pattern[str]]:
    if kind == "portrait":
        base = re.escape(f"Portrait Pieces/Portrait_Generator - {size}x{size}/")
        return {
            "skin": re.compile(rf"{base}Skins/Skin (\d+)\.png$"),
            "eyes": re.compile(rf"{base}Eyes/Eyes (\d+)\.png$"),
            "hair": re.compile(rf"{base}Hairstyles/Hairstyle (\d+) 1\.png$"),
        }
    if size == 16:
        base = re.escape("16x16/Portrait_Generator/")
        return {
            "skin": re.compile(rf"{base}Skins/PG_Skin_(\d+)\.png$"),
            "eyes": re.compile(rf"{base}Eyes/PG_Eyes_(\d+)\.png$"),
            "hair": re.compile(rf"{base}Hairstyles/PG_Hairstyle_(\d+)_1\.png$"),
        }
    tier = re.escape(f"{size}x{size}")
    base = re.escape(f"{size}x{size}/Portrait_Generator_{size}x{size}/")
    return {
        "skin": re.compile(
            rf"{base}Skins_{tier}/PG_Skin_{tier}_(\d+)\.png$"),
        "eyes": re.compile(
            rf"{base}Eyes_{tier}/PG_Eyes_{tier}_(\d+)\.png$"),
        "hair": re.compile(
            rf"{base}Hairstyles_{tier}/PG_Hairstyle_(\d+)_{tier}_1\.png$"),
    }


def _portrait_kind(archive: _Archive, size: int) -> str:
    for kind in ("portrait", "fallback"):
        patterns = _portrait_patterns(size, kind)
        if any(archive.match(pattern) for pattern in patterns.values()):
            return kind
    tier = f"{size}x{size}"
    raise ThemeBuildError(
        f"{archive.path.name}: no portrait sheets for the {tier} tier "
        f"(expected Portrait_Generator - {tier} or Portrait_Generator_{tier})")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
def _required(part: str, found: Mapping[int, str], archive_name: str,
              pattern_hint: str) -> Dict[int, str]:
    if not found:
        raise ThemeBuildError(
            f"{archive_name}: no {part} sheets found ({pattern_hint})")
    return dict(sorted(found.items()))


def _paired(part: str, portrait: Mapping[int, str], body: Mapping[int, str],
            archive_name: str) -> List[int]:
    only_portrait = sorted(set(portrait) - set(body))
    only_body = sorted(set(body) - set(portrait))
    if only_portrait or only_body:
        details: List[str] = []
        if only_portrait:
            details.append(
                f"portrait-only indexes {only_portrait} have no body counterpart")
        if only_body:
            details.append(
                f"body-only indexes {only_body} have no portrait counterpart")
        raise ThemeBuildError(
            [f"{part}: cannot pair portrait/body sheets in {archive_name}: "
             + "; ".join(details)])
    return sorted(set(portrait) & set(body))


def extract_layers(source: str, *, size: int = 32) -> Dict[str, List[object]]:
    """Read the purchased part sheets at ``source`` into composed-layer input.

    Returns ``{"skin": [...], "eyes": [...], "hair": [...], "outfit": [...],
    "body": [...]}`` with deterministic index-sorted lists. ``skin`` entries are
    portrait-only mappings; ``eyes``/``hair`` entries pair portrait with body;
    ``outfit``/``body`` entries are bare body-view images (the builder treats a
    bare image as the same art for both views, and only the body view is used).
    """
    if size not in _SIZES:
        raise ThemeBuildError(
            f"size: must be one of {', '.join(map(str, _SIZES))} (got {size!r})")

    character_path, portrait_path, _fallback = _resolve_source(source)
    body_patterns = _body_patterns(size)

    with _Archive(character_path) as character:
        bodies = _required(
            "body", character.match(body_patterns["body"]),
            character_path.name, f"Bodies/{size}x{size}/Body_NN.png")
        outfits = _required(
            "outfit", character.match(body_patterns["outfit"]),
            character_path.name, f"Outfits/{size}x{size}/Outfit_NN_01.png")
        body_eyes = _required(
            "eyes", character.match(body_patterns["eyes"]),
            character_path.name, f"Eyes/{size}x{size}/Eyes_NN.png")
        body_hair = _required(
            "hair", character.match(body_patterns["hair"]),
            character_path.name, f"Hairstyles/{size}x{size}/Hairstyle_NN_01.png")

        layers: Dict[str, List[object]] = {
            "body": [character.read_image(bodies[i]) for i in bodies],
            "outfit": [character.read_image(outfits[i]) for i in outfits],
        }
        body_eyes_images = {i: character.read_image(body_eyes[i]) for i in body_eyes}
        body_hair_images = {i: character.read_image(body_hair[i]) for i in body_hair}

    with _Archive(portrait_path) as portrait:
        kind = _portrait_kind(portrait, size)
        patterns = _portrait_patterns(size, kind)
        skins = _required(
            "skin", portrait.match(patterns["skin"]), portrait_path.name,
            f"portrait Skins for the {size} tier")
        portrait_eyes = _required(
            "eyes", portrait.match(patterns["eyes"]), portrait_path.name,
            f"portrait Eyes for the {size} tier")
        portrait_hair = _required(
            "hair", portrait.match(patterns["hair"]), portrait_path.name,
            f"portrait Hairstyles for the {size} tier")

        layers["skin"] = [
            {"portrait": portrait.read_image(skins[i])} for i in skins
        ]
        eye_indexes = _paired("eyes", portrait_eyes, body_eyes_images,
                              portrait_path.name)
        layers["eyes"] = [
            {
                "portrait": portrait.read_image(portrait_eyes[i]),
                "body": body_eyes_images[i],
            }
            for i in eye_indexes
        ]
        hair_indexes = _paired("hair", portrait_hair, body_hair_images,
                               portrait_path.name)
        layers["hair"] = [
            {
                "portrait": portrait.read_image(portrait_hair[i]),
                "body": body_hair_images[i],
            }
            for i in hair_indexes
        ]

    if not layers.get("skin"):
        raise ThemeBuildError("skin: no portrait skins were extracted")
    return layers


# ---------------------------------------------------------------------------
# Floor surfaces (B1)
# ---------------------------------------------------------------------------
def _inside_repository(path: os.PathLike | str) -> bool:
    resolved = Path(path).resolve()
    return resolved == _REPO_ROOT or _REPO_ROOT in resolved.parents


def _floor_member(names: Sequence[str]) -> Optional[str]:
    """The archive member holding the floor sheet, matched by suffix."""
    normalized = [name.replace("\\", "/") for name in names]
    basename = FLOOR_SHEET.rsplit("/", 1)[-1]
    for name in normalized:
        if name == FLOOR_SHEET or name.endswith("/" + FLOOR_SHEET):
            return name
    for name in normalized:
        if name.rsplit("/", 1)[-1] == basename:
            return name
    return None


def _archive_names(path: Path) -> Optional[List[str]]:
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.namelist()
    except (zipfile.BadZipFile, OSError):
        return None


def _resolve_floor_zip(source: str) -> Tuple[Path, str]:
    """Return ``(archive, member)`` for the measured floor sheet."""
    if not isinstance(source, str) or not source.strip():
        raise ThemeBuildError("source: a purchase directory or zip path is required")
    path = Path(source).expanduser()
    if path.is_file():
        if path.suffix.lower() != ".zip":
            raise ThemeBuildError(
                f"{path.name}: expected a .zip purchase archive, got "
                f"{path.suffix or 'no extension'}")
        names = _archive_names(path)
        if names is None:
            raise ThemeBuildError(
                f"{path.name}: not a valid zip archive")
        member = _floor_member(names)
        if member is not None:
            return path, member
        search_dir = path.parent
    elif path.is_dir():
        search_dir = path
    else:
        raise ThemeBuildError(f"{path}: source not found")

    candidates: List[Path] = []
    for name in INTERIOR_ZIP_NAMES:
        candidate = search_dir / name
        if candidate.is_file():
            candidates.append(candidate)
    for candidate in sorted(search_dir.glob("*.zip"), key=lambda p: p.name):
        if candidate.is_file() and candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        names = _archive_names(candidate)
        if names is None:
            continue
        member = _floor_member(names)
        if member is not None:
            return candidate, member
    raise ThemeBuildError(
        f"{FLOOR_SHEET}: not found under {search_dir} "
        f"(the interior pack {INTERIOR_ZIP_NAMES[0]} supplies the floors)")


def extract_floor_tiles(source: str, out_dir: str, *,
                        size: int = FLOOR_TILE) -> Dict[str, Path]:
    """Crop the mapped floor tiles from the interior purchase, in memory.

    The sheet is read straight from the zip and each mapped 16x16 cell is
    written as ``<out_dir>/surfaces/floor_<kind>.png``. Only the mapped kinds
    are emitted; an unmapped ``FloorKind`` is absent, so the client keeps its
    procedural floor. Raises :class:`office_theme_build.ThemeBuildError` when
    the sheet is missing/undecodable or a mapped cell lies outside it, and it
    refuses to write the paid art inside the repository.
    """
    if (not isinstance(size, int) or isinstance(size, bool)
            or size != FLOOR_TILE):
        raise ThemeBuildError(
            f"size: floor tiles are {FLOOR_TILE}px (got {size!r})")
    target = Path(out_dir)
    if _inside_repository(target):
        raise ThemeBuildError(
            f"floor output: refusing to write inside the repository ({target}); "
            "the purchased art must stay outside the tree")

    archive_path, member = _resolve_floor_zip(source)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            data = archive.read(member)
    except (KeyError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ThemeBuildError(
            f"{archive_path.name}: cannot read {member} ({exc})") from exc
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # noqa: BLE001 - any decode failure is typed
        raise ThemeBuildError(
            f"{archive_path.name}: {member} is not a decodable image "
            f"({exc})") from exc
    image = image.convert("RGBA")

    surfaces = target / "surfaces"
    surfaces.mkdir(parents=True, exist_ok=True)
    written: Dict[str, Path] = {}
    for kind, (col, row) in FLOOR_MAP.items():
        x, y = col * size, row * size
        if x + size > image.width or y + size > image.height:
            raise ThemeBuildError(
                f"floor {kind}: cell ({col},{row}) is outside the "
                f"{image.width}x{image.height} sheet")
        tile = image.crop((x, y, x + size, y + size))
        path = surfaces / f"floor_{kind}.png"
        tile.save(path, format="PNG")
        written[kind] = path
    return written


# ---------------------------------------------------------------------------
# Extended asset catalog (characters, furniture, animated, UI, outside)
# ---------------------------------------------------------------------------
#: Zip names for the packs the extended catalog reads, in preference order.
ASSET_ZIP_NAMES: Dict[str, Tuple[str, ...]] = {
    "interior": ("moderninteriors-win.zip",),
    "ui": ("modernuserinterface-win.zip",),
    "outside": ("Serene_Village_revamped_v1.9.zip",),
}

#: Extracted-directory fallbacks (used only when the zip is absent).
ASSET_DIR_NAMES: Dict[str, Tuple[str, ...]] = {
    "interior": ("moderninteriors-win",),
    "ui": ("modernuserinterface-win",),
    "outside": ("Serene_Village_revamped_v1.9",),
}

#: Directory prefix inside the Serene Village archive.
_SERENE_PREFIX = "SERENE_VILLAGE_REVAMPED"

#: License/credit stamped on outside-world entries (CC-BY 4.0 TASL).
_OUTSIDE_CREDIT = "LimeZu — https://limezu.itch.io"
_OUTSIDE_LICENSE = "CC-BY 4.0 (Serene Village, LimeZu)"


class _AssetTree:
    """Read-only access to a purchased pack, from a zip or an extracted tree.

    Member names are normalised to forward slashes and made relative to an
    optional root prefix (the Serene Village archive nests everything under
    ``SERENE_VILLAGE_REVAMPED/``). Nothing is ever written.
    """

    def __init__(self, path: Path, *, prefix: str = ""):
        self.path = Path(path)
        self.prefix = prefix.replace("\\", "/").strip("/")
        self._zip: Optional[zipfile.ZipFile] = None
        if self.path.is_file():
            try:
                self._zip = zipfile.ZipFile(self.path)
            except (zipfile.BadZipFile, OSError) as exc:
                raise ThemeBuildError(
                    f"{self.path.name}: not a valid zip archive ({exc})") from exc
            raw = [name.replace("\\", "/") for name in self._zip.namelist()]
        elif self.path.is_dir():
            raw = [p.relative_to(self.path).as_posix()
                   for p in self.path.rglob("*") if p.is_file()]
        else:
            raise ThemeBuildError(f"{self.path}: asset source not found")
        if self.prefix:
            marker = self.prefix + "/"
            self._names = [name[len(marker):] for name in raw
                           if name == self.prefix or name.startswith(marker)]
        else:
            self._names = raw

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()

    def __enter__(self) -> "_AssetTree":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def names(self, *, suffix: Optional[str] = None) -> List[str]:
        if suffix is None:
            return sorted(self._names)
        return sorted(name for name in self._names
                      if name.lower().endswith(suffix.lower()))

    def has(self, name: str) -> bool:
        return name in self._names

    def read(self, name: str) -> bytes:
        if self._zip is not None:
            member = f"{self.prefix}/{name}" if self.prefix else name
            try:
                return self._zip.read(member)
            except (KeyError, OSError, RuntimeError) as exc:
                raise ThemeBuildError(
                    f"{self.path.name}: cannot read {name} ({exc})") from exc
        target = self.path / self.prefix / name
        try:
            return target.read_bytes()
        except OSError as exc:
            raise ThemeBuildError(
                f"{self.path.name}: cannot read {name} ({exc})") from exc

    def read_image(self, name: str) -> Image.Image:
        data = self.read(name)
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except Exception as exc:  # noqa: BLE001 - any decode failure is typed
            raise ThemeBuildError(
                f"{self.path.name}: {name} is not a decodable image ({exc})"
            ) from exc
        return image if image.mode == "RGBA" else image.convert("RGBA")


def _purchase_dir(source: str) -> Path:
    path = Path(source).expanduser() if isinstance(source, str) else Path(source)
    if path.is_file():
        return path.parent
    return path


def _resolve_asset_tree(source: str, kind: str) -> Optional[_AssetTree]:
    """Open the requested pack, or return ``None`` when it is not present.

    A malformed present zip is a typed error; an absent pack is simply skipped
    so a partial purchase still builds the categories it does contain.
    """
    path = Path(source).expanduser() if isinstance(source, str) else Path(source)
    zips = ASSET_ZIP_NAMES[kind]
    dirs = ASSET_DIR_NAMES[kind]
    prefix = _SERENE_PREFIX if kind == "outside" else ""
    if path.is_file() and path.name in zips:
        return _AssetTree(path, prefix=prefix)
    search = _purchase_dir(source)
    for name in zips:
        candidate = search / name
        if candidate.is_file():
            return _AssetTree(candidate, prefix=prefix)
    for name in dirs:
        candidate = search / name
        if candidate.is_dir():
            # ``Serene_Village_revamped_v1.9`` nests its content one level down;
            # the tree resolver tolerates a missing/empty prefix either way.
            actual_prefix = prefix
            if prefix and not (candidate / prefix).is_dir():
                actual_prefix = ""
            return _AssetTree(candidate, prefix=actual_prefix)
    if path.is_dir() and path.name in dirs:
        actual_prefix = prefix
        if prefix and not (path / prefix).is_dir():
            actual_prefix = ""
        return _AssetTree(path, prefix=actual_prefix)
    return None


def _slug(value: str, *, fallback: str = "asset") -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    text = re.sub(r"-+", "-", text)[:140].strip("-")
    if not text:
        text = fallback
    if not text[0].isalnum():
        text = "x-" + text
    return text


def _grid_for(image: Image.Image, cell_w: int, cell_h: int) -> Tuple[Dict[str, int],
                                                                    Dict[str, int]]:
    cols = max(1, image.width // cell_w)
    rows = max(1, image.height // cell_h)
    return (
        {"w": cell_w, "h": cell_h, "count": cols * rows, "fps": None},
        {"cell": cell_w, "cols": cols, "rows": rows},
    )


def _gif_timing(blob: bytes) -> Tuple[Tuple[int, int], int, Optional[float]]:
    """``(width, height)``, frame count and fps from a GIF preview."""
    image = Image.open(io.BytesIO(blob))
    try:
        size = image.size
        count = int(getattr(image, "n_frames", 1))
        durations: List[int] = []
        for frame in range(count):
            image.seek(frame)
            duration = image.info.get("duration")
            if isinstance(duration, int) and duration > 0:
                durations.append(duration)
        fps = round(1000.0 / statistics.median(durations), 2) if durations else None
        return size, count, fps
    finally:
        try:
            image.close()
        except Exception:  # noqa: BLE001 - closing a decoded GIF is best effort
            pass


def _matching_gif(tree: _AssetTree, png_name: str,
                  gif_dir: Optional[str] = None) -> Optional[str]:
    """The GIF preview that carries the frame timing for ``png_name``."""
    stem = png_name.rsplit("/", 1)[-1][:-4]
    directory = gif_dir if gif_dir is not None else png_name.rsplit("/", 1)[0]
    prefix = (directory + "/") if directory else ""
    candidates = {
        name.lower(): name
        for name in tree.names(suffix=".gif")
        if name.startswith(prefix)
    }
    for candidate in (f"{prefix}{stem}.gif", f"{prefix}{stem}_16x16.gif",
                      f"{prefix}{stem}_32x32.gif", f"{prefix}{stem}_48x48.gif"):
        found = candidates.get(candidate.lower())
        if found is not None:
            return found
    return None


def _animated_entry(tree: _AssetTree, png_name: str, size: int,
                    *, gif_dir: Optional[str] = None,
                    category: str = "animated",
                    license: Optional[str] = None,
                    credit: Optional[str] = None) -> Dict[str, object]:
    image = tree.read_image(png_name)
    blob = tree.read(png_name)
    gif_name = _matching_gif(tree, png_name, gif_dir)
    fps: Optional[float] = None
    frame_source = "inferred"
    if gif_name is not None:
        gif_size, gif_frames, fps = _gif_timing(tree.read(gif_name))
        frame_w, frame_h = gif_size
        if frame_w > 0 and image.width % frame_w == 0:
            count = max(1, image.width // frame_w)
            # The GIF preview is authoritative for timing; if its frame count
            # disagrees the sheet width wins and the discrepancy is recorded.
            frame_source = "gif" if count == gif_frames else "sheet"
        else:
            frame_w, frame_h = size, image.height
            count = max(1, image.width // size)
            frame_source = "sheet"
    else:
        frame_w, frame_h = size, image.height
        count = max(1, image.width // size)
    entry = {
        "id": f"{category}-{_slug(png_name.rsplit('/', 1)[-1][:-4])}",
        "kind": "animated",
        "sheet": png_name,
        "frame": {"w": frame_w, "h": frame_h, "count": count, "fps": fps},
        "grid": {"cell": frame_w, "cols": count, "rows": 1},
        "nine_slice_candidate": False,
        "frame_source": frame_source,
        "blob": blob,
        "image_size": image.size,
    }
    if license is not None:
        entry["license"] = license
    if credit is not None:
        entry["credit"] = credit
    return entry


def _sheet_entry(tree: _AssetTree, name: str, size: int, kind: str,
                 *, category: str, cell: Optional[int] = None,
                 nine_slice_candidate: bool = False,
                 view: Optional[str] = "sheet",
                 license: Optional[str] = None,
                 credit: Optional[str] = None) -> Dict[str, object]:
    image = tree.read_image(name)
    cell = cell or size
    frame, grid = _grid_for(image, cell, cell)
    entry = {
        "id": f"{category}-{_slug(name.rsplit('/', 1)[-1][:-4])}",
        "kind": kind,
        "sheet": name,
        "frame": frame,
        "grid": grid,
        "nine_slice_candidate": nine_slice_candidate,
        "frame_source": "sheet",
        "blob": tree.read(name),
        "image_size": image.size,
    }
    if view is not None:
        entry["view"] = view
    if license is not None:
        entry["license"] = license
    if credit is not None:
        entry["credit"] = credit
    return entry


def _body_accessory_pattern(size: int) -> re.Pattern[str]:
    tier = re.escape(f"{size}x{size}")
    return re.compile(
        rf"(?:^|/)Character Pieces/Accessories/{tier}/Accessory_[0-9]+_[^/]+\.png$")


def _portrait_accessory_pattern(size: int, kind: str) -> re.Pattern[str]:
    if kind == "portrait":
        base = re.escape(f"Portrait Pieces/Portrait_Generator - {size}x{size}/")
        return re.compile(rf"{base}Accessories/[^/]+\.png$")
    if size == 16:
        base = re.escape("16x16/Portrait_Generator/")
        return re.compile(rf"{base}Accessories/PG_Accessory_[^/]+\.png$")
    tier = re.escape(f"{size}x{size}")
    base = re.escape(f"{size}x{size}/Portrait_Generator_{tier}/")
    return re.compile(rf"{base}Accessories_{tier}/PG_Accessory_[^/]+\.png$")


def _paired_character_entries(entries: List[Dict[str, object]],
                              part: str, view: str, size: int,
                              groups: Mapping[str, Mapping[int, str]],
                              archive: "_Archive") -> None:
    for index, member in sorted(groups.items()):
        image = archive.read_image(member)
        frame, grid = _grid_for(image, size, size * 2)
        entries.append({
            "id": f"characters-{view}-{part}-{index:02d}",
            "kind": "sheet",
            "sheet": member,
            "frame": frame,
            "grid": grid,
            "nine_slice_candidate": False,
            "frame_source": "sheet",
            "view": view,
            "blob": archive.read_bytes(member),
            "image_size": image.size,
        })


def _character_assets(source: str, size: int) -> List[Dict[str, object]]:
    try:
        character_path, portrait_path, _fallback = _resolve_source(source)
    except ThemeBuildError as exc:
        message = " ".join(exc.problems).lower()
        if "not found" in message:
            return []
        raise
    entries: List[Dict[str, object]] = []
    body_patterns = _body_patterns(size)
    with _Archive(character_path) as character:
        bodies = _required(
            "body", character.match(body_patterns["body"]),
            character_path.name, f"Bodies/{size}x{size}/Body_NN.png")
        outfits = _required(
            "outfit", character.match(body_patterns["outfit"]),
            character_path.name, f"Outfits/{size}x{size}/Outfit_NN_01.png")
        eyes = _required(
            "eyes", character.match(body_patterns["eyes"]),
            character_path.name, f"Eyes/{size}x{size}/Eyes_NN.png")
        hair = _required(
            "hair", character.match(body_patterns["hair"]),
            character_path.name, f"Hairstyles/{size}x{size}/Hairstyle_NN_01.png")
        _paired_character_entries(entries, "body", "body", size, bodies, character)
        _paired_character_entries(entries, "outfit", "body", size, outfits, character)
        _paired_character_entries(entries, "eyes", "body", size, eyes, character)
        _paired_character_entries(entries, "hair", "body", size, hair, character)
        for member in character.members(_body_accessory_pattern(size)):
            image = character.read_image(member)
            frame, grid = _grid_for(image, size, size * 2)
            entries.append({
                "id": f"characters-body-accessory-{_slug(member.rsplit('/', 1)[-1][:-4])}",
                "kind": "sheet",
                "sheet": member,
                "frame": frame,
                "grid": grid,
                "nine_slice_candidate": False,
                "frame_source": "sheet",
                "view": "body",
                "blob": character.read_bytes(member),
                "image_size": image.size,
            })
    with _Archive(portrait_path) as portrait:
        kind = _portrait_kind(portrait, size)
        patterns = _portrait_patterns(size, kind)
        skins = _required(
            "skin", portrait.match(patterns["skin"]), portrait_path.name,
            f"portrait Skins for the {size} tier")
        portrait_eyes = _required(
            "eyes", portrait.match(patterns["eyes"]), portrait_path.name,
            f"portrait Eyes for the {size} tier")
        portrait_hair = _required(
            "hair", portrait.match(patterns["hair"]), portrait_path.name,
            f"portrait Hairstyles for the {size} tier")
        for part, groups in (("skin", skins), ("eyes", portrait_eyes),
                             ("hair", portrait_hair)):
            for index, member in sorted(groups.items()):
                image = portrait.read_image(member)
                frame, grid = _grid_for(image, size * 2, size * 2)
                entries.append({
                    "id": f"characters-portrait-{part}-{index:02d}",
                    "kind": "sheet",
                    "sheet": member,
                    "frame": frame,
                    "grid": grid,
                    "nine_slice_candidate": False,
                    "frame_source": "sheet",
                    "view": "portrait",
                    "blob": portrait.read_bytes(member),
                    "image_size": image.size,
                })
        for member in portrait.members(_portrait_accessory_pattern(size, kind)):
            image = portrait.read_image(member)
            frame, grid = _grid_for(image, size * 2, size * 2)
            entries.append({
                "id": f"characters-portrait-accessory-{_slug(member.rsplit('/', 1)[-1][:-4])}",
                "kind": "sheet",
                "sheet": member,
                "frame": frame,
                "grid": grid,
                "nine_slice_candidate": False,
                "frame_source": "sheet",
                "view": "portrait",
                "blob": portrait.read_bytes(member),
                "image_size": image.size,
            })
    return entries


def _furniture_assets(source: str, size: int) -> List[Dict[str, object]]:
    tree = _resolve_asset_tree(source, "interior")
    if tree is None:
        return []
    tier = f"{size}x{size}"
    singles = f"1_Interiors/{tier}/Theme_Sorter_Singles/"
    entries: List[Dict[str, object]] = []
    with tree:
        for name in tree.names(suffix=".png"):
            if not name.startswith(singles):
                continue
            image = tree.read_image(name)
            entries.append({
                "id": f"furniture-single-{_slug(name[len(singles):-4])}",
                "kind": "sprite",
                "sheet": name,
                "frame": {"w": image.width, "h": image.height,
                          "count": 1, "fps": None},
                "nine_slice_candidate": False,
                "frame_source": "sheet",
                "blob": tree.read(name),
                "image_size": image.size,
            })
        sheet_names = [
            f"1_Interiors/{tier}/Interiors_{tier}.png",
            f"1_Interiors/{tier}/Room_Builder_{tier}.png",
        ]
        sorter = f"1_Interiors/{tier}/Theme_Sorter/"
        sheet_names.extend(name for name in tree.names(suffix=".png")
                           if name.startswith(sorter))
        for name in sorted(set(sheet_names)):
            if not tree.has(name):
                continue
            entries.append(_sheet_entry(tree, name, size, "sheet",
                                        category="furniture"))
    return entries


def _animated_assets(source: str, size: int) -> List[Dict[str, object]]:
    tree = _resolve_asset_tree(source, "interior")
    if tree is None:
        return []
    tier = f"{size}x{size}"
    sprites = f"3_Animated_objects/{tier}/spritesheets/"
    gifs = f"3_Animated_objects/{tier}/gif"
    entries: List[Dict[str, object]] = []
    with tree:
        for name in tree.names(suffix=".png"):
            if not name.startswith(sprites):
                continue
            entries.append(_animated_entry(tree, name, size, gif_dir=gifs))
    return entries


def _ui_assets(source: str, size: int) -> List[Dict[str, object]]:
    entries: List[Dict[str, object]] = []
    interior = _resolve_asset_tree(source, "interior")
    if interior is not None:
        with interior:
            for tier in _SIZES:
                for name, kind, flag in (
                    (f"4_User_Interface_Elements/UI_{tier}x{tier}.png",
                     "button_icon_sheet", False),
                    (f"4_User_Interface_Elements/"
                     f"UI_thinking_emotes_animation_{tier}x{tier}.png",
                     "animated", False),
                ):
                    if not interior.has(name):
                        continue
                    entries.append(_sheet_entry(
                        interior, name, size, kind, category="ui",
                        cell=tier, nine_slice_candidate=flag))
    ui = _resolve_asset_tree(source, "ui")
    if ui is not None:
        with ui:
            for tier in _SIZES:
                prefix = f"{tier}x{tier}/"
                for name in ui.names(suffix=".png"):
                    if not name.startswith(prefix):
                        continue
                    base = name.rsplit("/", 1)[-1]
                    if not base.startswith("Modern_UI_"):
                        continue
                    if "Modern_UI_Style" in base:
                        kind, flag = "nine_slice_sheet", True
                    elif "Modern_UI_Gamepad" in base:
                        kind, flag = "gamepad_sheet", False
                    else:
                        kind, flag = "button_icon_sheet", False
                    entries.append(_sheet_entry(
                        ui, name, size, kind, category="ui",
                        cell=tier, nine_slice_candidate=flag))
    return entries


def _outside_assets(source: str, size: int) -> List[Dict[str, object]]:
    tree = _resolve_asset_tree(source, "outside")
    if tree is None:
        return []
    entries: List[Dict[str, object]] = []
    with tree:
        for name in tree.names(suffix=".png"):
            base = name.rsplit("/", 1)[-1]
            if name.startswith("Animated stuff/"):
                entries.append(_animated_entry(
                    tree, name, size, gif_dir="Animated stuff",
                    category="outside", license=_OUTSIDE_LICENSE,
                    credit=_OUTSIDE_CREDIT))
                continue
            if name.startswith("RPG_MAKER_MV/"):
                cell = 48
            elif name.startswith("RPG_MAKER_VX_ACE/") or name.startswith("RPG_MAKER_XP/"):
                cell = 32
            elif name.startswith("Construct 3/"):
                cell = 16
            else:
                match = re.search(r"_(\d+)x(\d+)\.png$", base)
                cell = int(match.group(1)) if match else size
            entries.append(_sheet_entry(
                tree, name, size, "sheet", category="outside", cell=cell,
                license=_OUTSIDE_LICENSE, credit=_OUTSIDE_CREDIT))
    return entries


_ASSET_BUILDERS = {
    "characters": _character_assets,
    "furniture": _furniture_assets,
    "animated": _animated_assets,
    "ui": _ui_assets,
    "outside": _outside_assets,
}


def _selected_categories(categories: Optional[Sequence[str]]) -> List[str]:
    if categories is None:
        return list(theme_contract.ASSET_CATEGORIES)
    selected: List[str] = []
    for category in categories:
        if category not in theme_contract.ASSET_CATEGORIES:
            raise ThemeBuildError(
                f"categories: unknown asset category {category!r} "
                f"(allowed: {', '.join(theme_contract.ASSET_CATEGORIES)})")
        if category not in selected:
            selected.append(category)
    return selected


def extract_assets(source: str, *, size: int = 16,
                   categories: Optional[Sequence[str]] = None
                   ) -> Dict[str, object]:
    """Read the extended purchased packs into a category -> entries catalog.

    Returns ``{"schema": 1, "categories": {name: {"entries": [...]}}}``. Each
    entry carries the raw source image bytes, the source member name, frame
    geometry (width/height/count/fps), an optional grid and a 9-slice hint.
    Nothing is written; the art stays in memory for the builder.

    Absent packs are skipped (a partial purchase still catalogs what it has);
    a present-but-invalid pack is a typed :class:`ThemeBuildError`.
    """
    if size not in _SIZES:
        raise ThemeBuildError(
            f"size: must be one of {', '.join(map(str, _SIZES))} (got {size!r})")
    result: Dict[str, object] = {"schema": 1, "categories": {}}
    for category in _selected_categories(categories):
        entries = _ASSET_BUILDERS[category](source, size)
        if entries:
            result["categories"][category] = {"entries": entries}
    return result


def summarize_assets(catalog: Mapping[str, object]) -> Dict[str, object]:
    """Counts, kinds, geometry and a decoded-pixel digest; no art or paths."""
    categories = catalog.get("categories") if isinstance(catalog, Mapping) else None
    summary: Dict[str, object] = {}
    digest = hashlib.sha256()
    if not isinstance(categories, Mapping):
        return {"categories": {}, "digest": digest.hexdigest()}
    for category in sorted(categories):
        section = categories[category]
        entries = section.get("entries") if isinstance(section, Mapping) else []
        kinds: Dict[str, int] = {}
        frames: Dict[str, int] = {}
        pixel_digest = hashlib.sha256()
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            kind = str(entry.get("kind") or "unknown")
            kinds[kind] = kinds.get(kind, 0) + 1
            frame = entry.get("frame") if isinstance(entry.get("frame"), Mapping) else {}
            geometry = (f"{frame.get('w')}x{frame.get('h')}x{frame.get('count')}"
                        f"@{frame.get('fps')}")
            frames[geometry] = frames.get(geometry, 0) + 1
            blob = entry.get("blob")
            if isinstance(blob, (bytes, bytearray)):
                pixel_digest.update(bytes(blob))
        digest.update(category.encode("utf-8"))
        digest.update(pixel_digest.digest())
        summary[category] = {
            "count": len(entries),
            "kinds": dict(sorted(kinds.items())),
            "frames": dict(sorted(frames.items())),
            "digest": pixel_digest.hexdigest(),
        }
    return {"categories": summary, "digest": digest.hexdigest()}



#: The semantic tokens the palette sampler can derive, in their document order.
SEMANTIC_TOKENS: Tuple[str, ...] = (
    "color-surface",
    "color-surface-raised",
    "color-surface-hover",
    "color-text-primary",
    "color-text-muted",
    "color-border",
    "color-accent",
)

#: Sheet suffixes we will sample. The purchase is PNG; the others are here so a
#: re-encoded pack still works without a code change.
_SHEET_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

#: Bucket size for the deterministic colour histogram. 4 bits per channel
#: groups anti-aliased neighbours without merging distinct UI colours.
_PALETTE_BITS = 4


def load_ui_sheet(source: str, *, member: Optional[str] = None) -> Image.Image:
    """Open one Modern UI sheet (an image or a zip member) in memory.

    A zip is read straight from its member; the caller's purchase is never
    extracted to disk. ``member`` names a member explicitly; otherwise the
    lexicographically first image member is used, so the choice is
    deterministic and documented.
    """
    path = Path(source)
    if not path.is_file():
        raise ThemeBuildError(f"{source}: no such sheet or archive")
    suffix = path.suffix.lower()
    if suffix == ".zip":
        try:
            with zipfile.ZipFile(path) as archive:
                names = sorted(
                    name for name in archive.namelist()
                    if not name.endswith("/")
                    and Path(name).suffix.lower() in _SHEET_SUFFIXES)
                if member is not None:
                    chosen = member if member in names else None
                else:
                    chosen = names[0] if names else None
                if chosen is None:
                    wanted = (f"member {member!r}" if member is not None
                              else "an image member")
                    raise ThemeBuildError(
                        f"{path.name}: no {wanted} in the archive")
                data = archive.read(chosen)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ThemeBuildError(
                f"{path.name}: not a valid zip archive ({exc})") from exc
        try:
            with Image.open(io.BytesIO(data)) as sheet:
                return sheet.convert("RGBA")
        except OSError as exc:
            raise ThemeBuildError(
                f"{path.name}:{chosen}: not a readable image ({exc})") from exc
    if suffix not in _SHEET_SUFFIXES:
        raise ThemeBuildError(
            f"{path.name}: unsupported sheet type {suffix or '(none)'}")
    try:
        with Image.open(path) as sheet:
            return sheet.convert("RGBA")
    except OSError as exc:
        raise ThemeBuildError(
            f"{path.name}: not a readable image ({exc})") from exc


def _luminance(rgb: Sequence[int]) -> float:
    channels = []
    for channel in rgb:
        value = channel / 255.0
        channels.append(value / 12.92 if value <= 0.03928
                        else ((value + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _hex(rgb: Sequence[int]) -> str:
    r, g, b = (max(0, min(255, int(round(v)))) for v in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def _contrast(first: Sequence[int], second: Sequence[int]) -> float:
    a, b = _luminance(first), _luminance(second)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _away(rgb: Sequence[int], toward_text: int, amount: int) -> Tuple[int, int, int]:
    """Move a colour AWAY from the text side (a raised panel keeps contrast)."""
    step = amount if toward_text > 0 else -amount
    return tuple(max(0, min(255, int(round(v - step)))) for v in rgb[:3])  # type: ignore[return-value]


def _blend(first: Sequence[int], second: Sequence[int], t: float) -> Tuple[int, int, int]:
    return tuple(
        max(0, min(255, int(round(a + (b - a) * t))))
        for a, b in zip(first[:3], second[:3]))  # type: ignore[return-value]


def _colour_buckets(image: Image.Image) -> List[Tuple[int, int, int, int]]:
    """Deterministic histogram: ``[(r, g, b, count)]``, largest first.

    Channels are bucketed to ``_PALETTE_BITS`` and the bucket's true average is
    reported, so a flat sheet's dominant colour comes back exactly while
    anti-aliased edges do not invent new palette entries.
    """
    shift = 8 - _PALETTE_BITS
    totals: Dict[Tuple[int, int, int], List[int]] = {}
    # ``Image.getdata`` is deprecated (and a warning fails the strict suite);
    # the RGBA byte buffer carries the same information without that API.
    raw = image.convert("RGBA").tobytes()
    for offset in range(0, len(raw), 4):
        r, g, b, a = (raw[offset], raw[offset + 1],
                       raw[offset + 2], raw[offset + 3])
        if a < 16:
            continue
        key = (r >> shift, g >> shift, b >> shift)
        entry = totals.get(key)
        if entry is None:
            totals[key] = [r, g, b, 1]
        else:
            entry[0] += r
            entry[1] += g
            entry[2] += b
            entry[3] += 1
    buckets = [
        (round(r / count), round(g / count), round(b / count), count)
        for r, g, b, count in totals.values()
    ]
    buckets.sort(key=lambda item: (-item[3], item[:3]))
    return buckets


def _tokens_from_image(image: Image.Image) -> Tuple[Dict[str, str], List[str]]:
    """Map a sheet's dominant colours onto the semantic tokens.

    Returns ``(tokens, reasons)``. ``reasons`` is empty when the sampled palette
    passed the contract's typed-value + contrast validation; otherwise the
    caller should use :data:`DEFAULT_TOKENS` (returned) instead.
    """
    buckets = _colour_buckets(image)
    if not buckets:
        return dict(DEFAULT_TOKENS), ["palette: the sheet has no opaque pixels"]

    total = sum(bucket[3] for bucket in buckets)
    surface = buckets[0][:3]

    def contrast(colour: Sequence[int]) -> float:
        return _contrast(colour, surface)

    # Text: the sampled colour with the most contrast against the surface.
    text = max(buckets, key=lambda item: (contrast(item[:3]), item[3]))[:3]
    toward_text = 1 if _luminance(text) >= _luminance(surface) else -1

    # A raised surface sits on the AWAY side of the text (so text contrast can
    # only improve) and prefers a colour that actually occurs in the sheet.
    raised_side = [
        bucket[:3] for bucket in buckets
        if (_luminance(bucket[:3]) - _luminance(surface)) * -toward_text > 0.03
        and bucket[3] > 0
    ]
    raised = (max(raised_side, key=lambda colour: next(
        b[3] for b in buckets if b[:3] == colour))
        if raised_side else _away(surface, toward_text, 10))
    hover = _away(raised, toward_text, 14)

    # Muted text: the most common sampled colour that clears the 4.5:1 text
    # floor but is not the primary text colour.
    muted_candidates = [
        bucket[:3] for bucket in buckets
        if contrast(bucket[:3]) >= 4.5 and bucket[:3] != text
    ]
    muted = (max(muted_candidates, key=lambda colour: next(
        b[3] for b in buckets if b[:3] == colour))
        if muted_candidates else _blend(surface, text, 0.62))

    # Border: a sampled line that clears the 3:1 non-text floor against both
    # surfaces, else a safe blend toward the text colour.
    border_candidates = [
        bucket[:3] for bucket in buckets
        if contrast(bucket[:3]) >= 3.0 and bucket[:3] not in (surface, text)
    ]
    border = (max(border_candidates, key=lambda colour: next(
        b[3] for b in buckets if b[:3] == colour))
        if border_candidates else _blend(surface, text, 0.62))

    # Accent: the most saturated sampled colour that is not the surface/text.
    min_count = max(2, total // 4000)
    accent_candidates = [
        bucket for bucket in buckets
        if bucket[3] >= min_count
        and max(bucket[:3]) - min(bucket[:3]) > 24
        and bucket[:3] not in (surface, text)
        and contrast(bucket[:3]) >= 3.0
    ]
    accent = (max(
        accent_candidates,
        key=lambda item: (max(item[:3]) - min(item[:3]), item[3]))[:3]
        if accent_candidates else None)

    accent_hex = (_hex(accent) if accent is not None
                  else DEFAULT_TOKENS["color-accent"])
    tokens: Dict[str, str] = {
        "color-surface": _hex(surface),
        "color-surface-raised": _hex(raised),
        "color-surface-hover": _hex(hover),
        "color-text-primary": _hex(text),
        "color-text-muted": _hex(muted),
        "color-border": _hex(border),
        "color-accent": accent_hex,
        "radius-control": DEFAULT_TOKENS["radius-control"],
    }
    problems = theme_contract.validate_tokens(tokens)
    if problems:
        return dict(DEFAULT_TOKENS), problems
    return tokens, []


def derive_tokens(source: str, *, member: Optional[str] = None) -> Dict[str, object]:
    """Derive the semantic tokens from a Modern UI sheet, or fall back.

    The sheet is only read. When the sampled palette fails the contract's
    contrast/typed-value validation the current defaults are returned with the
    reason, so a sheet with too little range cannot silently produce unreadable
    chrome.
    """
    image = load_ui_sheet(source, member=member)
    tokens, reasons = _tokens_from_image(image)
    return {
        "tokens": tokens,
        "fallback": bool(reasons),
        "reasons": reasons,
    }


def _contact_sheet(image: Image.Image, *, scale: int = 2,
                   grid: int = 16) -> Image.Image:
    """Enlarge the sheet and draw a labelled coordinate grid over it."""
    if scale < 1:
        raise ThemeBuildError("contact sheet: scale must be >= 1")
    if grid < 1:
        raise ThemeBuildError("contact sheet: grid must be >= 1")
    wide = image.width * scale
    tall = image.height * scale
    sheet = image.resize((wide, tall), Image.NEAREST)
    draw = ImageDraw.Draw(sheet)
    step = grid * scale
    line = (16, 200, 255, 255)
    for x in range(0, wide, step):
        draw.line([(x, 0), (x, tall)], fill=line)
        draw.text((x + 2, 2), str(x // scale), fill=(0, 0, 0, 255))
    for y in range(0, tall, step):
        draw.line([(0, y), (wide, y)], fill=line)
        draw.text((2, y + 1), str(y // scale), fill=(0, 0, 0, 255))
    return sheet


def write_contact_sheet(image: Image.Image, out: str, *,
                        scale: int = 2, grid: int = 16) -> Path:
    """Write a labelled contact sheet OUTSIDE the repository.

    The contact sheet is derived from the paid art. It exists to author the
    ``slices`` coordinates by eye, and is deliberately never written into the
    repo (ruling 14); a path inside the checkout is a typed error.
    """
    target = Path(out)
    resolved = target.resolve()
    if resolved == _REPO_ROOT or _REPO_ROOT in resolved.parents:
        raise ThemeBuildError(
            f"contact sheet: refusing to write inside the repository ({target}); "
            "the purchased art must stay outside the tree")
    target.mkdir(parents=True, exist_ok=True)
    path = target / "ui-contact-sheet.png"
    sheet = _contact_sheet(image, scale=scale, grid=grid)
    sheet.save(path, format="PNG")
    return path


def _summarize_layers(layers: Mapping[str, Sequence[object]]) -> Dict[str, object]:
    """Counts plus a decoded-pixel digest; no art, names, or filesystem paths."""
    summary: Dict[str, int] = {}
    digest = hashlib.sha256()
    for part in sorted(layers):
        sheets = layers[part]
        summary[part] = len(sheets)
        digest.update(part.encode("utf-8"))
        for sheet in sheets:
            views = sheet if isinstance(sheet, Mapping) else {"": sheet}
            for view in sorted(views):
                image = views[view]
                rgba = image.convert("RGBA")
                digest.update(view.encode("utf-8"))
                digest.update(f"{rgba.width}x{rgba.height}".encode("ascii"))
                digest.update(rgba.tobytes())
    return {"parts": summary, "digest": digest.hexdigest()}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="office_theme_extract.py",
        description=(
            "Read the owner's purchased Modern UI sheets in memory: derive the "
            "semantic palette, or write a labelled contact sheet for authoring "
            "9-slice coordinates. Never writes the purchase into the repo."))
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--source", metavar="PATH",
        help="read the purchased generator zips in memory and report the "
             "discovered parts (counts + a decoded-pixel digest); writes nothing")
    action.add_argument(
        "--tokens", metavar="SHEET",
        help="sample the dominant Modern UI colours and print the validated "
             "semantic tokens (png/jpg/webp or a zip member)")
    action.add_argument(
        "--ui-contact-sheet", metavar="SHEET",
        help="write a labelled coordinate grid of the sheet (requires --out)")
    action.add_argument(
        "--assets", metavar="PATH",
        help="read the extended packs (characters, furniture, animated, UI, "
             "outside) in memory and print a category summary + decoded digest; "
             "writes nothing")
    parser.add_argument(
        "--check", action="store_true",
        help="with --source or --assets, extract twice and verify the decoded "
             "pixels and counts are identical")
    parser.add_argument(
        "--size", type=int, default=32, choices=(16, 32, 48),
        help="tier to read (default 32)")
    parser.add_argument(
        "--categories", default=None,
        help="comma-separated asset categories for --assets "
             "(default: all of characters,furniture,animated,ui,outside)")
    parser.add_argument("--out", default=None,
                        help="output directory for --ui-contact-sheet")
    parser.add_argument("--member", default=None,
                        help="zip member to read (default: first image member)")
    parser.add_argument("--scale", type=int, default=2,
                        help="contact-sheet enlargement (default 2)")
    parser.add_argument("--ui-grid", type=int, default=16,
                        help="contact-sheet grid step in source pixels (default 16)")
    args = parser.parse_args(argv)
    if args.check and not (args.source or args.assets):
        parser.error("--check requires --source or --assets")
    categories = None
    if args.categories is not None:
        categories = [item.strip() for item in args.categories.split(",")
                      if item.strip()]
        if not categories:
            parser.error("--categories must name at least one category")
    try:
        if args.assets:
            first = summarize_assets(extract_assets(
                args.assets, size=args.size, categories=categories))
            if args.check:
                second = summarize_assets(extract_assets(
                    args.assets, size=args.size, categories=categories))
                if first != second:
                    print(
                        "error: asset extraction is not reproducible across two "
                        "reads", file=sys.stderr)
                    return 1
            print(json.dumps({
                "ok": True,
                "size": args.size,
                "checked": bool(args.check),
                **first,
            }, indent=2, sort_keys=True))
            return 0
        if args.source:
            first = _summarize_layers(extract_layers(args.source, size=args.size))
            if args.check:
                second = _summarize_layers(
                    extract_layers(args.source, size=args.size))
                if first != second:
                    print(
                        "error: extraction is not reproducible across two reads",
                        file=sys.stderr)
                    return 1
            print(json.dumps({
                "ok": True,
                "size": args.size,
                "checked": bool(args.check),
                **first,
            }, indent=2, sort_keys=True))
            return 0
        if args.tokens:
            result = derive_tokens(args.tokens, member=args.member)
            print(json.dumps({
                # ``result`` is unambiguous: a fallback is a successful, named
                # outcome — not an "ok" that contradicts itself.
                "result": "fallback" if result["fallback"] else "sampled",
                "source": Path(args.tokens).name,
                **result,
            }, indent=2, sort_keys=True))
            return 0
        if not args.out:
            parser.error("--ui-contact-sheet requires --out")
        image = load_ui_sheet(args.ui_contact_sheet, member=args.member)
        path = write_contact_sheet(image, args.out,
                                   scale=args.scale, grid=args.ui_grid)
        print(json.dumps({
            "ok": True,
            "contact_sheet": str(path),
            "width": image.width,
            "height": image.height,
        }, indent=2, sort_keys=True))
        return 0
    except ThemeBuildError as exc:
        for problem in exc.problems:
            print(f"error: {problem}", file=sys.stderr)
        return 2


__all__ = [
    "ASSET_ZIP_NAMES",
    "CHARACTER_ZIP_NAMES",
    "DEFAULT_TOKENS",
    "FALLBACK_ZIP_NAMES",
    "FLOOR_MAP",
    "FLOOR_SHEET",
    "FLOOR_TILE",
    "INTERIOR_ZIP_NAMES",
    "PORTRAIT_ZIP_NAMES",
    "SEMANTIC_TOKENS",
    "derive_tokens",
    "extract_assets",
    "extract_floor_tiles",
    "extract_layers",
    "load_ui_sheet",
    "main",
    "summarize_assets",
    "write_contact_sheet",
]


if __name__ == "__main__":
    raise SystemExit(main())
