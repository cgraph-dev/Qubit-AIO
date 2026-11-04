"""Build-time handling for public-tree release-candidate snapshots.

``scripts/export_public_tree.py`` deliberately publishes only the agreed
public docs (``PUBLIC_DOC_FILES``) and never the internal docs, generated
provenance records, or frozen archive data.  Some of those omitted paths are
also ``force-include`` sources in ``pyproject.toml`` because the full product
wheel ships them as installed guide resources and provenance data.

A wheel built from a public snapshot therefore finds those force-include
sources absent.  Hatchling treats every missing source as fatal ("Forced
include not found"), which makes the release candidate unbuildable.  This
module holds the narrow, explicit exception: in a tree that carries the
exporter's ``PUBLIC_TREE_MANIFEST.md`` marker, only the known intentionally
omitted sources are dropped.  Any *other* missing force-include source still
raises, so a real packaging regression is never hidden.

The full repo has no marker, so its builds keep the strict behaviour.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, MutableMapping

#: Written by ``scripts/export_public_tree.py`` into the snapshot root.
PUBLIC_EXPORT_MARKER = "PUBLIC_TREE_MANIFEST.md"

#: ``pyproject.toml`` wheel force-include sources the public exporter omits:
#: internal operator docs plus the generated/archive provenance data sets.
#: Keep this list in lockstep with ``docs/PUBLIC_TREE_MANIFEST.md`` (the export
#: manifest) and ``tests/test_public_export_buildable9u.py``.
PUBLIC_EXPORT_OPTIONAL_SOURCES = frozenset({
    "docs/ARCHITECTURE.md",
    "docs/MIGRATION.md",
    "docs/README.md",
    "docs/VOICE-BRIDGE-AUDIT.md",
    "docs/archive/2026-09-21/MATRIX_8T_F.json",
    "docs/archive/2026-09-21/PARITY_HERMES.json",
    "docs/generated/QUBIT_HERMES_VENDOR_MANIFEST.json",
})


def _relative_source(base: Path, source: str) -> str | None:
    """Normalize a force-include source (relative or absolute) to root-relative."""
    path = Path(source)
    if not path.is_absolute():
        path = base / path
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return None


def is_public_export(root: Path | str) -> bool:
    """True when *root* is a snapshot produced by the public exporter."""
    return (Path(root) / PUBLIC_EXPORT_MARKER).is_file()


def drop_missing_optional_sources(
    root: Path | str,
    force_include: MutableMapping[str, str],
) -> list[str]:
    """Remove intentionally-omitted force-include sources from a public export.

    Hatchling normalizes force-include keys to absolute paths before the build
    hook sees them, so sources are compared after normalizing both spellings
    to a root-relative path.

    Returns the sources that were dropped (empty for a normal checkout or when
    every optional source is present).  A missing source that is not in
    ``PUBLIC_EXPORT_OPTIONAL_SOURCES`` is left untouched so hatchling still
    fails loudly on a genuine packaging defect.
    """
    base = Path(root)
    if not is_public_export(base):
        return []
    dropped: list[str] = []
    for source in list(force_include):
        source_path = Path(source)
        if not source_path.is_absolute():
            source_path = base / source_path
        if source_path.exists():
            continue
        relative = _relative_source(base, source)
        if relative in PUBLIC_EXPORT_OPTIONAL_SOURCES:
            del force_include[source]
            dropped.append(relative)
    return sorted(dropped)


def missing_force_include_sources(
    root: Path | str,
    sources: Iterable[str],
) -> list[str]:
    """Force-include sources absent from *root* (excluding the dynamic frontend)."""
    base = Path(root)
    missing: list[str] = []
    for source in sources:
        relative = _relative_source(base, source)
        if relative == "frontend" or relative is None:
            continue
        if not (base / relative).exists():
            missing.append(relative)
    return sorted(missing)
