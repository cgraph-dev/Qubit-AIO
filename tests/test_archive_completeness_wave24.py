"""Wave 24 — production-imported modules must ship in both archives.

An archive-completeness audit found runtime-imported modules absent from the
built wheel and/or sdist:

* ``qubit_runtime/scripts/releases/versioning.py`` — imported by
  ``qubit_runtime/qubit_cli/release_channels.py`` (``scripts.releases.versioning``);
* ``qubit_runtime/scripts/bundles/native.py`` — imported by
  ``qubit_runtime/pm/cli.py`` for ``qubit pm bundle`` (``scripts.bundles.native``);
* ``scripts/browser_auto.py`` — imported by
  ``voicebridge/communications/monitor.py`` (``scripts.browser_auto``); it was in
  the sdist (default tree walk) but absent from the wheel.

The wheel ships the runtime under ``voicebridge/_data/qubit_runtime`` — the path
``voicebridge.qubit_runtime.runtime_root()`` resolves first and
``ensure_path()`` puts on ``sys.path`` — and the sdist mirrors the runtime subset
at its original ``qubit_runtime/`` layout so a wheel rebuilds from it. The
``scripts`` tree has no ``__init__.py`` (PEP 420 namespace package), so the
directory mappings cover any future ``__init__.py`` too.

These tests are hermetic over ``pyproject.toml`` + the tracked sources; the
opt-in archive membership scan reads a real ``dist/`` when ``QUBIT_ARCHIVE_DIR``
is set.
"""
from __future__ import annotations

import os
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"

WHEEL_PREFIX = "voicebridge/_data/qubit_runtime"

# (source, wheel destination, sdist destination)
SCRIPTS_MODULES = (
    (
        "qubit_runtime/scripts/releases",
        f"{WHEEL_PREFIX}/scripts/releases",
        "qubit_runtime/scripts/releases",
    ),
    (
        "qubit_runtime/scripts/bundles",
        f"{WHEEL_PREFIX}/scripts/bundles",
        "qubit_runtime/scripts/bundles",
    ),
)

# The helper lives at the repo root in a checkout; it must ship under the
# installed fork root so ``import scripts.browser_auto`` resolves in a wheel.
BROWSER_AUTO_SOURCE = "scripts/browser_auto.py"
BROWSER_AUTO_WHEEL_DEST = f"{WHEEL_PREFIX}/scripts/browser_auto.py"

# module path -> the import literal in production code that needs it
IMPORT_SITES = {
    "scripts.releases.versioning": REPO / "qubit_runtime/qubit_cli/release_channels.py",
    "scripts.bundles.native": REPO / "qubit_runtime/pm/cli.py",
    "scripts.browser_auto": REPO / "voicebridge/communications/monitor.py",
}

EXPECTED_FILES = {
    "scripts.releases.versioning": "qubit_runtime/scripts/releases/versioning.py",
    # ``versioning`` imports this at module import time; the directory mapping
    # must carry it or ``import scripts.releases.versioning`` still fails.
    "scripts.releases.semver": "qubit_runtime/scripts/releases/semver.py",
    "scripts.bundles.native": "qubit_runtime/scripts/bundles/native.py",
    "scripts.browser_auto": BROWSER_AUTO_SOURCE,
}


def _targets() -> tuple[dict, dict]:
    targets = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))[
        "tool"]["hatch"]["build"]["targets"]
    return targets["wheel"], targets["sdist"]


def _tracked() -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                         text=True, check=True).stdout
    return set(out.splitlines())


def test_wheel_force_include_names_the_scripts_modules():
    wheel, _ = _targets()
    force = wheel.get("force-include", {})
    for source, wheel_dest, _ in SCRIPTS_MODULES:
        assert force.get(source) == wheel_dest, (source, force.get(source))
    assert force.get(BROWSER_AUTO_SOURCE) == BROWSER_AUTO_WHEEL_DEST, (
        BROWSER_AUTO_SOURCE, force.get(BROWSER_AUTO_SOURCE))


def test_wheel_destinations_resolve_under_the_installed_fork_root():
    """The modules must land under the one runtime root ``ensure_path()`` adds
    to ``sys.path``; a package-data destination outside it would not import."""
    wheel, _ = _targets()
    force = wheel.get("force-include", {})
    for source, wheel_dest, _ in SCRIPTS_MODULES:
        assert wheel_dest.startswith(f"{WHEEL_PREFIX}/scripts/"), wheel_dest
    assert BROWSER_AUTO_WHEEL_DEST.startswith(f"{WHEEL_PREFIX}/scripts/")
    # ...and the package-data contract holds (no wheel-root ``scripts/``).
    for source in (BROWSER_AUTO_SOURCE, *(s for s, _, _ in SCRIPTS_MODULES)):
        assert force[source].startswith("voicebridge/_data/"), (
            source, force[source])


def test_sdist_force_include_mirrors_the_scripts_modules():
    _, sdist = _targets()
    force = sdist.get("force-include", {})
    for source, _, sdist_dest in SCRIPTS_MODULES:
        assert force.get(source) == sdist_dest, (source, force.get(source))
    # the sdist excludes ``qubit_runtime`` wholesale, so a missing mirror means
    # a wheel rebuilt from the sdist silently loses the modules again.
    assert "qubit_runtime" in set(sdist.get("exclude", []))


def test_source_files_exist_and_are_referenced_by_production_imports():
    for module, rel in EXPECTED_FILES.items():
        assert (REPO / rel).is_file(), f"{module} source missing: {rel}"
    for module, site in IMPORT_SITES.items():
        assert site.is_file(), site
        text = site.read_text(encoding="utf-8")
        assert (f"from {module} import" in text
                or f"import {module}" in text), (module, site)


def test_force_include_sources_are_tracked_for_a_clean_clone():
    """A force-include source that is not git-tracked makes a clean clone's
    archive silently incomplete (mirrors test_packaging_wave3.py)."""
    wheel, _ = _targets()
    tracked = _tracked()
    for source in (BROWSER_AUTO_SOURCE, *(s for s, _, _ in SCRIPTS_MODULES)):
        path = REPO / source
        if path.is_dir():
            assert any(t.startswith(source + "/") for t in tracked), source
        else:
            assert source in tracked, source


def test_namespace_tree_has_no_init_to_include_separately():
    """``scripts``/``releases``/``bundles`` are PEP 420 namespace packages, so
    the directory mappings cover every member (no ``__init__.py`` to list)."""
    for rel in ("qubit_runtime/scripts", "qubit_runtime/scripts/releases",
                "qubit_runtime/scripts/bundles"):
        assert not (REPO / rel / "__init__.py").exists(), rel


def test_browser_auto_is_not_excluded_from_the_sdist_walk():
    _, sdist = _targets()
    excluded = set(sdist.get("exclude", []))
    assert BROWSER_AUTO_SOURCE not in excluded


# ---------------------------------------------------------------------------
# Opt-in archive membership scan (real wheel + sdist)
# ---------------------------------------------------------------------------
def _artifact_dir() -> Path | None:
    env = os.environ.get("QUBIT_ARCHIVE_DIR", "")
    return Path(env) if env and Path(env).is_dir() else None


@pytest.mark.skipif(_artifact_dir() is None,
                    reason="set QUBIT_ARCHIVE_DIR to a built dist/")
def test_built_archives_contain_the_production_modules():
    art = _artifact_dir()
    wheels = sorted(art.glob("*.whl"))
    sdists = sorted(art.glob("*.tar.gz"))
    assert len(wheels) == 1, wheels
    assert len(sdists) == 1, sdists

    wheel_suffixes = tuple(
        f"{WHEEL_PREFIX}/{rel}" for rel in (
            "scripts/releases/versioning.py",
            "scripts/releases/semver.py",
            "scripts/bundles/native.py",
            "scripts/browser_auto.py",
        ))
    with zipfile.ZipFile(wheels[0]) as z:
        names = z.namelist()
        for suffix in wheel_suffixes:
            assert any(n.endswith(suffix) for n in names), suffix

    sdist_suffixes = tuple(rel for rel in (
        "qubit_runtime/scripts/releases/versioning.py",
        "qubit_runtime/scripts/releases/semver.py",
        "qubit_runtime/scripts/bundles/native.py",
        "scripts/browser_auto.py",
    ))
    with tarfile.open(sdists[0]) as t:
        names = t.getnames()
        for suffix in sdist_suffixes:
            assert any(n.endswith("/" + suffix) or n.endswith(suffix)
                       for n in names), suffix
