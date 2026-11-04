"""The public release-candidate snapshot must be buildable.

``scripts/export_public_tree.py`` intentionally omits internal docs and the
generated/archive provenance data.  Some of those paths are wheel
``force-include`` sources, so a naive hatchling build of the snapshot dies
with ``Forced include not found``.  ``scripts/public_export_packaging.py``
holds the narrow exception: in a tree carrying the exporter's manifest marker
only the known intentionally-omitted sources are dropped.

These tests are hermetic: they use the checkout's local Git objects and
``tmp_path`` only (no network, service, or operator HOME).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import tomllib

REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "scripts" / "public_export_packaging.py"


def _module():
    spec = importlib.util.spec_from_file_location(
        "qubit_public_export_packaging_test", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wheel_force_include() -> dict[str, str]:
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return data["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]


def _export_drops(
    source: str,
    repo: Path,
    tracked: set[str],
    included: set[str],
) -> bool:
    """True when the public export would not carry the force-include source."""
    if source in tracked:
        return source not in included
    path = repo / source
    if path.is_dir():
        return not any(rel.startswith(source + "/") for rel in included)
    return True


def test_normal_checkout_keeps_strict_force_include(tmp_path: Path) -> None:
    mod = _module()
    force = {"docs/README.md": "voicebridge/_data/docs/MIGRATIONS.md"}
    assert mod.drop_missing_optional_sources(tmp_path, force) == []
    assert force == {"docs/README.md": "voicebridge/_data/docs/MIGRATIONS.md"}


def test_public_export_drops_only_known_omissions(tmp_path: Path) -> None:
    mod = _module()
    (tmp_path / mod.PUBLIC_EXPORT_MARKER).write_text("# export\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "PUBLIC.md").write_text("# public\n", encoding="utf-8")

    force = {
        "docs/PUBLIC.md": "voicebridge/_data/docs/PUBLIC.md",
        "docs/README.md": "voicebridge/_data/docs/MIGRATIONS.md",
        "docs/not-known-missing.md": "voicebridge/_data/docs/x.md",
        "frontend/runtime.js": "voicebridge/_data/frontend/runtime.js",
    }
    dropped = mod.drop_missing_optional_sources(tmp_path, force)

    assert dropped == ["docs/README.md"]
    assert "docs/PUBLIC.md" in force
    assert "docs/README.md" not in force
    # An unknown missing source stays so hatchling still fails loudly.
    assert "docs/not-known-missing.md" in force
    # The dynamic frontend staging is never touched.
    assert "frontend/runtime.js" in force


def test_public_export_handles_hatchling_absolute_sources(tmp_path: Path) -> None:
    """Hatchling normalizes force-include keys to absolute paths."""
    mod = _module()
    (tmp_path / mod.PUBLIC_EXPORT_MARKER).write_text("# export\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "PUBLIC.md").write_text("# public\n", encoding="utf-8")

    force = {
        str(tmp_path / "docs" / "PUBLIC.md"): "voicebridge/_data/docs/PUBLIC.md",
        str(tmp_path / "docs" / "ARCHITECTURE.md"): "voicebridge/_data/docs/x.md",
    }
    dropped = mod.drop_missing_optional_sources(tmp_path, force)

    assert dropped == ["docs/ARCHITECTURE.md"]
    assert str(tmp_path / "docs" / "PUBLIC.md") in force
    assert str(tmp_path / "docs" / "ARCHITECTURE.md") not in force


def test_optional_sources_match_the_real_export() -> None:
    """Every force-include source the real export drops must be declared."""
    mod = _module()
    force = _wheel_force_include()

    spec = importlib.util.spec_from_file_location(
        "qubit_export_public_tree_test", REPO / "scripts" / "export_public_tree.py"
    )
    assert spec and spec.loader
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)

    tracked_paths, included, _excluded, _counts, _personal = exporter.selected_paths(REPO)
    included_set = set(included)
    tracked_set = set(tracked_paths)

    missing = sorted(
        source for source in force
        if source != "frontend" and _export_drops(source, REPO, tracked_set, included_set)
    )
    assert missing, "the export is expected to omit some force-include sources"
    undeclared = sorted(set(missing) - set(mod.PUBLIC_EXPORT_OPTIONAL_SOURCES))
    assert not undeclared, (
        "force-include sources dropped by the public export but not declared in "
        f"PUBLIC_EXPORT_OPTIONAL_SOURCES: {undeclared}"
    )

    # The declared exception stays honest: it names tracked sources that the
    # exporter really omits, so it cannot accumulate stale entries.
    for source in mod.PUBLIC_EXPORT_OPTIONAL_SOURCES:
        assert source in tracked_set, source
        assert source not in included_set, source
