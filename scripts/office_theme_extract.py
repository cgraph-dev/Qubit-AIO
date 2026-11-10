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
been measured and reviewed.
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from PIL import Image

try:  # imported as a top-level module (scripts/ is on sys.path)
    from office_theme_build import ThemeBuildError
except ImportError:  # imported as scripts.office_theme_extract
    from scripts.office_theme_build import ThemeBuildError  # type: ignore

#: Canonical archive names, in search-preference order.
CHARACTER_ZIP_NAMES: Tuple[str, ...] = ("Character Generator 2.0 Linux Build.zip",)
PORTRAIT_ZIP_NAMES: Tuple[str, ...] = ("Portrait_Generator_1.5.0_Linux_Build.zip",)
FALLBACK_ZIP_NAMES: Tuple[str, ...] = ("modernuserinterface-win.zip",)

_SIZES = (16, 32, 48)

#: Body-side part directories under ``Character Pieces``.
_BODY_PARTS = ("Bodies", "Outfits", "Eyes", "Hairstyles")


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


__all__ = [
    "CHARACTER_ZIP_NAMES",
    "FALLBACK_ZIP_NAMES",
    "PORTRAIT_ZIP_NAMES",
    "extract_layers",
]
