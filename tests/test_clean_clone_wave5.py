"""Clean-clone install reproducibility (Wave 5, agent G).

The verifier flagged that the clean-install gate could only PASS because the
git-ignored ``frontend/qubit-dist`` dashboard build happened to exist in the
developer checkout. A fresh clone does not carry that artifact, so a
node-less/network-less host could not serve the dashboard and the "clean"
install was unproven.

These tests simulate a fresh checkout of the tracked tree (no git-ignored
build artifacts) and prove the reproducible path:

* the reproducible build script reports a typed state without building
  (``--check``): present, buildable, or unavailable;
* ``scripts/clean_install_check.sh`` stops with a typed DEGRADED (exit 4) on a
  node-less clone instead of building a dashboard-less wheel and reporting PASS;
* ``scripts/release_qualify.py`` rejects a dashboard-less wheel as a typed FAIL;
* a tree that already carries the built bundle produces a wheel that ships the
  dashboard.

Hermetic: throwaway ``/tmp`` trees only; no network, no live service, and no
operator HOME/config/state is touched. All wheel builds are offline
(``uv build`` from the local cache).
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

BUILD_DASHBOARD = REPO / "scripts" / "build_dashboard.sh"
CLEAN_INSTALL = REPO / "scripts" / "clean_install_check.sh"
INSTALL_SH = REPO / "scripts" / "install.sh"
PYPROJECT = REPO / "pyproject.toml"

BASH = "/bin/bash" if Path("/bin/bash").exists() else shutil.which("bash")


def _tracked_clone(dest: Path) -> None:
    """Materialise a fresh checkout of the tracked working tree at ``dest``.

    ``git ls-files`` lists tracked paths only; the tar stream copies their
    current working-tree content and preserves symlinks. Git-ignored build
    artifacts (``frontend/qubit-dist``, ``node_modules``, ``.venv``) are
    intentionally absent, exactly like a clean clone.
    """
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ("git ls-files -z | tar --null -T - -cf - | "
           f"tar -xf - -C {shlex.quote(str(dest))}")
    r = subprocess.run(cmd, shell=True, cwd=str(REPO),
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    assert (dest / "scripts" / "build_dashboard.sh").is_file()


def _node_free_path(tmp_path: Path) -> str:
    """A PATH with the coreutils the scripts need but WITHOUT node/npm/npx."""
    bindir = tmp_path / "node-free-bin"
    bindir.mkdir()
    tools = ("mktemp", "dirname", "mkdir", "rm", "cp", "mv", "cat", "tr",
             "tail", "head", "ls", "basename", "grep", "sed", "sh", "env",
             "printf", "true", "false", "uname", "sleep")
    for tool in tools:
        src = shutil.which(tool)
        if src:
            (bindir / tool).symlink_to(src)
    assert not (bindir / "node").exists()
    return str(bindir)


@pytest.fixture
def clean_clone(tmp_path: Path) -> Path:
    dest = tmp_path / "clone"
    _tracked_clone(dest)
    return dest


# ===========================================================================
# 1. Premise: a fresh checkout has no git-ignored dashboard artifact
# ===========================================================================
def test_fresh_clone_omits_the_gitignored_dashboard(clean_clone: Path):
    assert not (clean_clone / "frontend" / "qubit-dist").exists()
    # ... but the tracked dashboard source is present, so it can be built.
    assert (clean_clone / "frontend" / "qubit-web" / "package.json").is_file()
    assert (clean_clone / "frontend" / "qubit-web" / "vite.config.ts").is_file()


# ===========================================================================
# 2. Reproducible build script: typed preflight, never a silent skip
# ===========================================================================
def _run_check(clone: Path, env_extra: dict | None = None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(
        [BASH, str(clone / "scripts" / "build_dashboard.sh"), "--check"],
        cwd=str(clone), env=env, capture_output=True, text=True, timeout=60)


def test_build_dashboard_check_is_typed_when_node_is_missing(clean_clone, tmp_path):
    r = _run_check(clean_clone, {"PATH": _node_free_path(tmp_path)})
    out = r.stdout + r.stderr
    assert r.returncode == 3, out
    assert "DASHBOARD_UNAVAILABLE" in out
    assert "frontend/qubit-dist" in out


def test_build_dashboard_check_reports_present_or_buildable(clean_clone):
    r = _run_check(clean_clone)
    out = r.stdout + r.stderr
    if shutil.which("node") and shutil.which("npm"):
        assert r.returncode == 0, out
        assert "DASHBOARD_BUILDABLE" in out, out
    else:
        # environment-dependent: an honest typed state, never a crash
        assert r.returncode == 3
        assert "DASHBOARD_UNAVAILABLE" in out


# ===========================================================================
# 3. clean_install_check: typed DEGRADED on a node-less clone
# ===========================================================================
def test_clean_install_degrades_typed_on_a_node_less_clone(clean_clone, tmp_path):
    env = dict(os.environ)
    env["PATH"] = _node_free_path(tmp_path)
    r = subprocess.run(
        [BASH, str(clean_clone / "scripts" / "clean_install_check.sh")],
        cwd=str(clean_clone), env=env, capture_output=True, text=True,
        timeout=120)
    out = r.stdout + r.stderr
    assert r.returncode == 4, out
    assert "DEGRADED" in out
    assert "frontend/qubit-dist" in out
    # stopped at the typed preflight, before any wheel/venv/network work
    assert "clean install check: PASS" not in out
    assert "== toolchain:" not in out
    assert "build wheel" not in out


# ===========================================================================
# 4. Packaging gate: a dashboard-less wheel is a typed FAIL
# ===========================================================================
def _uv_wheel(src: Path, out: Path) -> Path:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv is not available to build a wheel")
    out.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([uv, "build", "--wheel", "--out-dir", str(out), str(src)],
                       cwd=str(src), capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        pytest.skip(f"offline wheel build unavailable: {r.stderr.strip()[-200:]}")
    wheels = sorted(out.glob("*.whl"))
    assert wheels, "no wheel produced"
    return wheels[0]


def _members(wheel: Path) -> list:
    with zipfile.ZipFile(wheel) as z:
        return z.namelist()


def test_clean_clone_wheel_without_dashboard_is_a_typed_fail(clean_clone, tmp_path):
    out = tmp_path / "dist"
    wheel = _uv_wheel(clean_clone, out)
    names = _members(wheel)
    assert not any("qubit-dist" in n for n in names), names[:5]

    import scripts.release_qualify as RQ

    rec = RQ.inspect_artifacts(out)
    wheel_rec = next(r for r in rec["records"] if r["artifact"] == "wheel")
    assert wheel_rec["state"] == "FAIL", wheel_rec
    assert wheel_rec["dashboard"]["state"] == "MISSING", wheel_rec["dashboard"]
    assert "dashboard" in wheel_rec["detail"]


def test_tree_with_built_dashboard_produces_a_shipping_wheel(clean_clone, tmp_path):
    # ``frontend/qubit-dist`` is a gitignored build input; without a build there
    # is nothing to ship, so the release path is only exercised when it exists.
    if not (REPO / "frontend" / "qubit-dist").is_dir():
        import pytest
        pytest.skip("dashboard bundle not built (frontend/qubit-dist absent)")
    # simulate the release-tree path: the tree already carries the built bundle
    shutil.copytree(REPO / "frontend" / "qubit-dist",
                    clean_clone / "frontend" / "qubit-dist")
    out = tmp_path / "dist"
    wheel = _uv_wheel(clean_clone, out)
    names = _members(wheel)
    assert any(n.endswith("_data/frontend/qubit-dist/index.html")
               for n in names), names[:10]

    import scripts.release_qualify as RQ

    rec = RQ.inspect_artifacts(out)
    wheel_rec = next(r for r in rec["records"] if r["artifact"] == "wheel")
    assert wheel_rec["dashboard"]["state"] == "OK", wheel_rec["dashboard"]


# ===========================================================================
# 5. Declared install/build contract text
# ===========================================================================
def test_installer_has_typed_dashboard_degradation_gate():
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "DASHBOARD_UNAVAILABLE" in text
    assert 'DASHBOARD_STATE="toolchain_missing"' in text
    assert 'DASHBOARD_STATE="build_failed"' in text
    assert "exit 4" in text
    # the established warning contract is preserved
    assert "WARNING: frontend/qubit-dist missing" in text


def test_clean_install_preflights_dashboard_before_building():
    text = CLEAN_INSTALL.read_text(encoding="utf-8")
    assert "frontend/qubit-dist/index.html" in text
    assert 'build_dashboard.sh' in text
    assert "dashboard bundle" in text
    assert "degrade" in text


def test_pyproject_declares_the_dashboard_artifact_and_documents_reproducibility():
    import tomllib

    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    targets = data["tool"]["hatch"]["build"]["targets"]
    for name in ("wheel", "sdist"):
        assert "frontend/qubit-dist" in targets[name].get("artifacts", []), name
    text = PYPROJECT.read_text(encoding="utf-8")
    assert "CLEAN_CLONE_AUDIT_WAVE5.md" in text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
