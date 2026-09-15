"""Dashboard packaging + resource resolution (owner directive 2026-09-11).

The built Hermes dashboard and the Qubit overlay are resolved from package
data first (a clean wheel install ships them under ``voicebridge/_data/``)
and fall back to the checkout. This suite proves the resolution contract and
that the build artifacts are declared in the project metadata, so an
isolated install serves the real dashboard with no untracked checkout files.
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DIST = REPO_ROOT / "frontend" / "hermes-dist"
needs_dist = pytest.mark.skipif(
    not (DIST / "index.html").is_file(),
    reason="hermes-dist not built (run scripts/build_dashboard.sh)")


def test_pyproject_declares_dashboard_artifacts():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    wheel = data["tool"]["hatch"]["build"]["targets"]["wheel"]
    sdist = data["tool"]["hatch"]["build"]["targets"]["sdist"]
    assert "frontend/hermes-dist" in wheel.get("artifacts", []), wheel
    assert "frontend/hermes-dist" in sdist.get("artifacts", []), sdist
    # the frontend tree (source + overlay) is packaged in the wheel, either as
    # a direct force-include mapping or via the filtered custom build hook.
    force = wheel.get("force-include", {})
    if "frontend" not in force:
        hook_path = str(wheel.get("hooks", {}).get("custom", {})
                        .get("path", ""))
        assert hook_path and (REPO_ROOT / hook_path).is_file(), wheel
        text = (REPO_ROOT / hook_path).read_text(encoding="utf-8",
                                                 errors="replace")
        assert "voicebridge/_data/frontend/" in text, text


def test_resource_resolution_prefers_package_data(monkeypatch, tmp_path):
    from voicebridge import resources
    # simulate an installed package-data layout
    fake = tmp_path / "_data"
    (fake / "frontend" / "hermes-dist").mkdir(parents=True)
    (fake / "frontend" / "hermes-dist" / "index.html").write_text("<html>x</html>")
    (fake / "frontend" / "hermes-qubit" / "plugins" / "qubit-ops").mkdir(parents=True)
    monkeypatch.setattr(resources, "_PACKAGE_DATA", fake)
    got = resources.resource_path("frontend/hermes-dist")
    assert got == fake / "frontend" / "hermes-dist"
    assert (got / "index.html").is_file()
    overlay = resources.resource_path("frontend/hermes-qubit/plugins")
    assert overlay == fake / "frontend" / "hermes-qubit" / "plugins"


@needs_dist
def test_webui_dist_resolves_to_the_real_build():
    from voicebridge import resources
    from voicebridge.webui import WebUIHandler

    handler = WebUIHandler.__new__(WebUIHandler)  # _hermes_dist uses no state
    dist = handler._hermes_dist()
    assert dist == resources.resource_path("frontend/hermes-dist")
    assert dist == DIST
    assert (dist / "index.html").is_file()
    assert any((dist / "assets").glob("index-*.js"))


@needs_dist
def test_overlay_plugins_resolve_for_serving():
    from voicebridge import hermes_compat as HC
    from voicebridge import resources
    assert HC.OVERLAY_DIR == REPO_ROOT / "frontend" / "hermes-qubit"
    plugdir = resources.resource_path("frontend/hermes-qubit/plugins")
    assert plugdir.is_dir()
    names = sorted(p.name for p in plugdir.iterdir() if p.is_dir())
    for expected in ("qubit-approvals", "qubit-ops", "qubit-devices",
                     "qubit-memory", "qubit-reports"):
        assert expected in names, names


def test_build_script_present_and_executable():
    script = REPO_ROOT / "scripts" / "build_dashboard.sh"
    assert script.is_file()
    import os
    assert os.access(script, os.X_OK)
    text = script.read_text()
    assert "vite build --base=/" in text
    assert "--outDir" in text
