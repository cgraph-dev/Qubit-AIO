"""Packaging proof that the Qubit-owned Hermes runtime ships in wheel + sdist.

A clean install must carry the fork's runtime subset under
``voicebridge/_data/qubit_runtime`` — the path
``voicebridge.qubit_runtime.runtime_root()`` resolves first — while the sdist
must carry the same subset at its original ``qubit_runtime/`` layout so a wheel
rebuilds from it. The real wheel build and isolated install are exercised by
``scripts/verify_installed_dashboard.sh``; these tests are hermetic and only
read the build configuration and the working tree.
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FORK = REPO_ROOT / "qubit_runtime"
PIN = "69948c005791d4d0e51670ca015238c2c7663749"
# Canonical location (docs/generated/); the frozen archive is read-only lineage.
MANIFEST_SOURCE_REL = "docs/generated/QUBIT_HERMES_VENDOR_MANIFEST.json"
MANIFEST_INSTALLED_REL = "docs/archive/2026-09-21/QUBIT_HERMES_VENDOR_MANIFEST.json"

WHEEL_PREFIX = "voicebridge/_data/qubit_runtime"

RUNTIME_DIRS = [
    "qubit_cli",
    "agent",
    "tools",
    "gateway",
    "plugins",
    "skills",
    "optional-skills",
    "cron",
    "providers",
    "ui-tui",
    "tui_gateway",
    "native",
    "locales",
    "assets",
    "web",
]

NON_RUNTIME_DIRS = ["tests", "apps", "website", "contributors", "evals"]
NON_RUNTIME_FILES = ["package-lock.json", "uv.lock"]


def _targets():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    targets = data["tool"]["hatch"]["build"]["targets"]
    return targets["wheel"], targets["sdist"]


def _top_level_modules():
    return sorted(p.name for p in FORK.glob("*.py"))


def test_wheel_ships_runtime_dirs_under_installed_package_data():
    wheel, _ = _targets()
    force = wheel.get("force-include", {})
    for name in RUNTIME_DIRS:
        source = f"qubit_runtime/{name}"
        assert force.get(source) == f"{WHEEL_PREFIX}/{name}", (
            source,
            force.get(source),
        )


def test_wheel_ships_every_top_level_module_license_and_pyproject():
    wheel, _ = _targets()
    force = wheel.get("force-include", {})
    modules = _top_level_modules()
    assert modules, "no top-level qubit_runtime/*.py modules found"
    for name in modules:
        source = f"qubit_runtime/{name}"
        assert force.get(source) == f"{WHEEL_PREFIX}/{name}", source
    assert force.get("qubit_runtime/LICENSE") == f"{WHEEL_PREFIX}/LICENSE"
    assert force.get("qubit_runtime/pyproject.toml") == f"{WHEEL_PREFIX}/pyproject.toml"


def test_wheel_does_not_ship_non_runtime_material():
    wheel, _ = _targets()
    force = wheel.get("force-include", {})
    assert "qubit_runtime" not in force, "whole-fork mapping would ship tests/apps"
    for source in force:
        if not source.startswith("qubit_runtime/"):
            continue
        rel = source[len("qubit_runtime/"):]
        top = rel.split("/", 1)[0]
        assert top not in NON_RUNTIME_DIRS, source
        assert top not in NON_RUNTIME_FILES, source


def test_wheel_ships_provenance_manifest_for_status():
    wheel, _ = _targets()
    force = wheel.get("force-include", {})
    assert (REPO_ROOT / MANIFEST_SOURCE_REL).is_file()
    assert force.get(MANIFEST_SOURCE_REL) == MANIFEST_INSTALLED_REL
    manifest = json.loads(
        (REPO_ROOT / MANIFEST_SOURCE_REL).read_text(encoding="utf-8"))
    assert manifest["upstream_revision"] == PIN
    assert manifest["local_root"] == "qubit_runtime"


def test_wheel_force_include_sources_all_exist():
    wheel, _ = _targets()
    for source in wheel.get("force-include", {}):
        assert (REPO_ROOT / source).exists(), f"force-include source missing: {source}"


def test_sdist_carries_same_runtime_subset_at_original_layout():
    _, sdist = _targets()
    force = sdist.get("force-include", {})
    for name in RUNTIME_DIRS:
        source = f"qubit_runtime/{name}"
        assert force.get(source) == source, (source, force.get(source))
    for name in _top_level_modules():
        source = f"qubit_runtime/{name}"
        assert force.get(source) == source, source
    assert force.get("qubit_runtime/LICENSE") == "qubit_runtime/LICENSE"
    assert force.get("qubit_runtime/pyproject.toml") == "qubit_runtime/pyproject.toml"


def test_sdist_excludes_whole_fork_from_default_walk():
    _, sdist = _targets()
    assert "qubit_runtime" in set(sdist.get("exclude", [])), sdist.get("exclude")


def test_existing_frontend_artifacts_remain_declared():
    wheel, sdist = _targets()
    assert "frontend/qubit-dist" in wheel.get("artifacts", [])
    assert "frontend/qubit-dist" in sdist.get("artifacts", [])
    # frontend tree packaged under voicebridge/_data/frontend, as a direct
    # force-include mapping or via the filtered custom build hook.
    force = wheel.get("force-include", {})
    if "frontend" in force:
        assert force["frontend"] == "voicebridge/_data/frontend"
    else:
        hook_path = str(wheel.get("hooks", {}).get("custom", {})
                        .get("path", ""))
        assert hook_path and (REPO_ROOT / hook_path).is_file(), wheel
        text = (REPO_ROOT / hook_path).read_text(encoding="utf-8",
                                                 errors="replace")
        assert "voicebridge/_data/frontend/" in text, text


def test_runtime_root_resolves_to_a_real_fork():
    from voicebridge import qubit_runtime as hr

    root = hr.runtime_root()
    assert root.is_dir(), root
    assert (root / "qubit_cli" / "main.py").is_file()


def test_runtime_root_prefers_installed_package_data(monkeypatch, tmp_path):
    from voicebridge import qubit_runtime as hr
    from voicebridge import resources

    fake = tmp_path / "_data"
    (fake / "qubit_runtime" / "qubit_cli").mkdir(parents=True)
    (fake / "qubit_runtime" / "qubit_cli" / "main.py").write_text("# stub\n")
    monkeypatch.setattr(resources, "_PACKAGE_DATA", fake)

    got = hr.runtime_root()
    assert got == fake / "qubit_runtime"
    assert (got / "qubit_cli" / "main.py").is_file()


def test_runtime_status_reports_pinned_revision():
    from voicebridge import qubit_runtime as hr

    st = hr.status()
    assert st["present"] is True
    assert st["importable"] is True
    assert st["upstream_revision"] == PIN
