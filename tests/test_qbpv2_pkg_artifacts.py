"""QBPV2 wave-2 W5 (role H): built-archive content + metadata tests.

Two layers:

* structural assertions over ``pyproject.toml`` (always run, hermetic); and
* a full archive scan of a built wheel + sdist, opt-in via
  ``QBPV2_ARTIFACT_DIR`` pointing at a ``dist/`` directory. The scan proves the
  fork runtime, dashboard bundle, plugin assets, and metadata/licenses ship,
  and that no developer home path or ``node_modules`` build tooling leaks.
"""
from __future__ import annotations

import os
import re
import tarfile
import tomllib
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"

# Internal agent reports/evidence that must never reach the release sdist.
INTERNAL_SDIST_DOCS = (
    "docs/QUBIT_QBPV2_PACKAGING.md",
    "docs/QUBIT_QBPV_PACKAGING_SECURITY.md",
    "docs/QUBIT_BYTE_PARITY_EVIDENCE.md",
    "docs/QUBIT_REACHABILITY_AUDIT.md",
)

PLUGINS = ("qubit-approvals", "qubit-boards", "qubit-brand", "qubit-chat",
           "qubit-devices", "qubit-gateway", "qubit-memory", "qubit-ops",
           "qubit-reports", "qubit-runtime")


def _targets() -> tuple[dict, dict]:
    targets = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))[
        "tool"]["hatch"]["build"]["targets"]
    return targets["wheel"], targets["sdist"]


def _artifact_dir() -> Path | None:
    env = os.environ.get("QBPV2_ARTIFACT_DIR", "")
    if env and Path(env).is_dir():
        return Path(env)
    return None


# ===========================================================================
# 1. Structural packaging contract
# ===========================================================================
def _frontend_tree_packaged(wheel: dict) -> bool:
    """Frontend tree packaged under voicebridge/_data/frontend, as a direct
    force-include directory mapping or the filtered custom build hook."""
    if wheel.get("force-include", {}).get("frontend") == \
            "voicebridge/_data/frontend":
        return True
    hook = wheel.get("hooks", {}).get("custom", {})
    hook_path = str(hook.get("path", ""))
    if not hook_path or not (REPO / hook_path).is_file():
        return False
    text = (REPO / hook_path).read_text(encoding="utf-8", errors="replace")
    return "voicebridge/_data/frontend/" in text and "frontend" in text


def test_pyproject_ships_fork_dashboard_and_plugins():
    wheel, sdist = _targets()
    # dashboard bake ships in both artifacts
    assert "frontend/hermes-dist" in wheel["artifacts"]
    assert "frontend/hermes-dist" in sdist["artifacts"]
    include = wheel["force-include"]
    # fork runtime + package data + license
    assert any(k.startswith("qubit_hermes/") for k in include)
    assert include.get("qubit_hermes/LICENSE") == \
        "voicebridge/_data/qubit_hermes/LICENSE"
    # whole frontend tree (dashboard + hermes-qubit plugin assets) is packaged
    assert _frontend_tree_packaged(wheel)
    # provenance manifest travels with the installed runtime
    assert "docs/QUBIT_HERMES_VENDOR_MANIFEST.json" in include
    # the sdist mirrors the fork subset so a wheel can be rebuilt offline
    assert any(k.startswith("qubit_hermes/") for k in sdist["force-include"])


def test_pyproject_excludes_internal_wave_docs_from_sdist():
    _, sdist = _targets()
    excluded = set(sdist.get("exclude", []))
    missed = [d for d in INTERNAL_SDIST_DOCS if d not in excluded]
    assert not missed, f"internal docs not excluded from sdist: {missed}"


def test_pyproject_metadata_declares_launchers_and_licenses():
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert "LICENSE" in project["license-files"]
    assert "THIRD_PARTY_NOTICES.md" in project["license-files"]
    scripts = project["scripts"]
    assert scripts["qubit"] == "voicebridge.cli:main"
    assert scripts["qubit-hermes"] == "voicebridge.hermes_runtime:entry"


# ===========================================================================
# 2. Built-archive scan (opt-in)
# ===========================================================================
@pytest.mark.skipif(_artifact_dir() is None,
                    reason="set QBPV2_ARTIFACT_DIR to a built dist/")
def test_wheel_contains_fork_dashboard_plugins_and_metadata():
    art = _artifact_dir()
    wheels = list(art.glob("*.whl"))
    assert len(wheels) == 1, f"expected one wheel in {art}: {wheels}"
    with zipfile.ZipFile(wheels[0]) as z:
        names = z.namelist()
        assert any(n.endswith("_data/qubit_hermes/hermes_cli/main.py")
                   for n in names), "fork runtime missing from wheel"
        assert any(n.endswith("_data/qubit_hermes/LICENSE") for n in names)
        assert any(n.endswith("_data/frontend/hermes-dist/index.html")
                   for n in names), "dashboard bundle missing from wheel"
        for plugin in PLUGINS:
            assert any(n.endswith(
                f"_data/frontend/hermes-qubit/plugins/{plugin}/"
                f"{plugin}.js") for n in names), f"plugin asset missing: {plugin}"
        assert any(n.endswith("docs/QUBIT_HERMES_VENDOR_MANIFEST.json")
                   for n in names)
        assert any("dist-info/licenses/THIRD_PARTY_NOTICES.md" in n
                   for n in names)
        assert any("dist-info/licenses/LICENSE" in n for n in names)
        entry = z.read(next(n for n in names
                            if n.endswith("dist-info/entry_points.txt"))).decode()
        assert re.search(r"^qubit\s*=\s*voicebridge\.cli:main$", entry, re.M)
        assert re.search(r"^qubit-hermes\s*=\s*voicebridge\.hermes_runtime:entry$",
                         entry, re.M)
        _assert_clean(names, "wheel")


@pytest.mark.skipif(_artifact_dir() is None,
                    reason="set QBPV2_ARTIFACT_DIR to a built dist/")
def test_sdist_contains_fork_dashboard_plugins_and_metadata():
    art = _artifact_dir()
    sdists = list(art.glob("*.tar.gz"))
    assert len(sdists) == 1, f"expected one sdist in {art}: {sdists}"
    with tarfile.open(sdists[0]) as t:
        names = t.getnames()
        assert any(n.endswith("/qubit_hermes/hermes_cli/main.py")
                   for n in names), "fork runtime missing from sdist"
        assert any(n.endswith("/qubit_hermes/LICENSE") for n in names)
        assert any(n.endswith("/frontend/hermes-dist/index.html")
                   for n in names), "dashboard bundle missing from sdist"
        for plugin in PLUGINS:
            assert any(n.endswith(
                f"/frontend/hermes-qubit/plugins/{plugin}/{plugin}.js")
                for n in names), f"plugin asset missing: {plugin}"
        assert any(n.endswith("/pyproject.toml") for n in names)
        assert any(n.endswith("/PKG-INFO") for n in names)
        _assert_clean(names, "sdist")
        for doc in INTERNAL_SDIST_DOCS:
            assert not any(n.endswith("/" + doc) for n in names), doc


def _assert_clean(names: list[str], label: str) -> None:
    """No developer home path and no node_modules build tooling leak."""
    home = [n for n in names if "home/trick" in n]
    node = [n for n in names if "node_modules" in n]
    assert not home, f"{label} ships a developer home path: {home[:3]}"
    assert not node, f"{label} ships node_modules build tooling: {node[:3]}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
