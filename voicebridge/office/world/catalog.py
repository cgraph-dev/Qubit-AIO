"""Read LimeZu pack/theme-sorter *names* into an asset catalog. Never art.

The purchased Modern Interiors pack is a directory tree (or a zip) of PNG
sheets. The room-style system needs metadata — theme, category, size, layer,
surface, shadow variant, tags — not the pixels. This module therefore walks
file **names** only:

* a directory source is traversed with :func:`pathlib.Path.rglob`;
* a ``.zip`` source is read with :mod:`zipfile`'s ``namelist``;
* no image is opened, decoded, copied, or written.

The extractor is intentionally conservative. It recognises the pack layouts
measured on the owner's 2026-11-09 purchase:

    Modern_Interiors_RPG_Maker_Version/RPG_MAKER_MV/Interiors/Theme_Sorter_MV/Office_01.png
    .../16x16/Animated/...                      (16x16 baseline)
    .../32x32/Portrait_Generator_32x32/...      (portrait close-up)
    .../48x48/Portrait_Generator_48x48/...      (portrait close-up)

Any other file name is still catalogued as a generic sheet, defaulting to the
16x16 world baseline. Size tokens in the path win; the default is never 32/48.

No art is committed by this module or its CLI. The output is JSON metadata.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .style import (
    AssetEntry,
    ASSET_SIZES,
    WORLD_TILE,
    validate_asset_catalog,
)

__all__ = [
    "CatalogError",
    "extract_catalog",
    "catalog_entries_to_dicts",
    "catalog_summary",
]

_IMAGE_SUFFIXES = (".png", ".webp")
_SIZE_RE = re.compile(r"(?:^|[/_ -])(16|32|48)x\1(?:[/_ -]|$)", re.IGNORECASE)
_HIDDEN_PARTS = {"__MACOSX"}
_IGNORED_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}

#: Path/name token -> (category, surface, layer). Order matters: the first
#: matching token wins. These are *sheet* categories, not individual props.
_CATEGORY_RULES: Tuple[Tuple[str, Tuple[str, str, str]], ...] = (
    ("theme_sorter", ("theme_sorter", "furniture", "furniture")),
    ("portrait", ("portrait", "portrait", "portrait")),
    ("character", ("portrait", "portrait", "portrait")),
    ("animated", ("animated", "decor", "decor")),
    ("floor", ("floor", "floor", "floor")),
    ("wall", ("wall", "wall", "wall")),
    ("tileset", ("tileset", "furniture", "furniture")),
    ("interior", ("interior_sheet", "furniture", "furniture")),
)

#: Words worth carrying as tags when they appear in the file name. They are not
#: a taxonomy; they help a reviewer find a sheet without opening it.
_TAG_WORDS: Tuple[str, ...] = (
    "office", "kitchen", "living", "bedroom", "bathroom", "museum", "gym",
    "hospital", "classroom", "library", "restaurant", "grocery", "shop",
    "door", "window", "rug", "plant", "lamp", "table", "chair", "desk",
    "console", "display", "rack", "counter", "shadowless", "animated",
)


class CatalogError(Exception):
    """The source cannot be read, or the metadata it yields is invalid."""

    def __init__(self, problems: Iterable[str]):
        self.problems: List[str] = list(problems)
        super().__init__("; ".join(self.problems) or "invalid catalog source")


def _slugify(relative: str, *, max_len: int = 80) -> str:
    stem = relative.rsplit("/", 1)[-1]
    stem = stem.rsplit(".", 1)[0]
    slug = re.sub(r"[^a-z0-9._-]+", "-", stem.lower()).strip("-._")
    if not slug:
        slug = "sheet"
    if len(slug) > max_len:
        import hashlib  # local, tiny, deterministic
        digest = hashlib.blake2b(relative.encode("utf-8"), digest_size=4).hexdigest()
        slug = slug[: max_len - 10].rstrip("-._") + "-" + digest
    if not slug[0].isalnum():
        slug = "s-" + slug
    return slug


def _size_from_path(relative: str) -> int:
    match = _SIZE_RE.search(relative)
    if match:
        value = int(match.group(1))
        if value in ASSET_SIZES:
            return value
    return WORLD_TILE


def _category_from_path(relative: str) -> Tuple[str, str, str]:
    lowered = relative.lower()
    for token, mapping in _CATEGORY_RULES:
        if token in lowered:
            return mapping
    return ("sheet", "furniture", "furniture")


def _shadow_from_path(relative: str) -> str:
    lowered = relative.lower()
    if "shadowless" in lowered:
        return "shadowless"
    if "shadow" in lowered:
        return "black_shadow"
    return "shadowless"


def _tags_from_path(relative: str) -> Tuple[str, ...]:
    lowered = relative.lower()
    tags: List[str] = []
    for word in _TAG_WORDS:
        if word in lowered and word not in tags:
            tags.append(word)
    return tuple(tags)


def _iter_names(source: Path, max_entries: int) -> List[str]:
    if source.is_dir():
        names = [
            path.relative_to(source).as_posix()
            for path in source.rglob("*")
            if path.is_file()
        ]
    elif source.is_file() and source.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(source) as archive:
                names = [name for name in archive.namelist()
                         if not name.endswith("/")]
        except zipfile.BadZipFile as exc:
            raise CatalogError([f"{source}: not a readable zip ({exc})"]) from exc
    else:
        raise CatalogError([
            f"{source}: expected a pack directory or a .zip file"])
    if len(names) > max_entries:
        raise CatalogError([
            f"{source}: {len(names)} entries exceeds max_entries={max_entries}"])
    return sorted(names)


def _safe_relative(name: str) -> bool:
    if not name or name.startswith("/") or "\\" in name or ":" in name:
        return False
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return False
    if any(part in _HIDDEN_PARTS for part in parts):
        return False
    return True


def extract_catalog(
    source: str | Path,
    *,
    theme: str = "limezu",
    max_entries: int = 50_000,
) -> Tuple[AssetEntry, ...]:
    """Extract metadata entries from a pack directory or zip. Names only."""

    root = Path(source).expanduser()
    names = _iter_names(root, max_entries)
    entries: List[AssetEntry] = []
    seen: Dict[str, int] = {}
    for name in names:
        if not _safe_relative(name):
            continue
        lowered = name.lower()
        if lowered.rsplit("/", 1)[-1] in _IGNORED_NAMES:
            continue
        if not lowered.endswith(_IMAGE_SUFFIXES):
            continue
        category, surface, layer = _category_from_path(name)
        size = _size_from_path(name)
        if size != WORLD_TILE and surface not in ("portrait", "ui"):
            # A 32/48 folder that is not portrait/UI is still a close-up sheet;
            # keep it out of the world catalog rather than mislabel it.
            continue
        base_id = _slugify(name)
        count = seen.get(base_id, 0)
        seen[base_id] = count + 1
        asset_id = base_id if count == 0 else f"{base_id}-{count + 1}"
        entries.append(AssetEntry(
            id=asset_id,
            file=name,
            theme=theme,
            category=category,
            size=size,
            footprint=(1, 1),
            z=0,
            layer=layer,
            surface=surface,
            orientations=("any",),
            animation=("animated" if category == "animated" else None),
            shadow_variant=_shadow_from_path(name),
            tags=_tags_from_path(name),
            compatibility=(),
        ))
    problems = validate_asset_catalog(entries)
    if problems:
        raise CatalogError(problems)
    return tuple(entries)


def catalog_entries_to_dicts(entries: Sequence[AssetEntry]) -> List[Dict[str, Any]]:
    return [entry.to_dict() for entry in entries]


def catalog_summary(entries: Sequence[AssetEntry]) -> Dict[str, Any]:
    """Counts by theme/category/size, for a CLI or a reviewer."""

    by_theme: Dict[str, int] = {}
    by_category: Dict[str, int] = {}
    by_size: Dict[str, int] = {}
    for entry in entries:
        by_theme[entry.theme] = by_theme.get(entry.theme, 0) + 1
        by_category[entry.category] = by_category.get(entry.category, 0) + 1
        key = f"{entry.size}x{entry.size}"
        by_size[key] = by_size.get(key, 0) + 1
    return {
        "total": len(entries),
        "by_theme": dict(sorted(by_theme.items())),
        "by_category": dict(sorted(by_category.items())),
        "by_size": dict(sorted(by_size.items())),
    }
