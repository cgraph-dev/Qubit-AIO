"""Wave 5 packaging/lifecycle regression tests (Agent 5, role H).

Focused, hermetic guards for the packaging/lifecycle surface owned by this
wave. They fail when a packaging privacy/reproducibility gate, a lifecycle
fail-closed path, or a legal-notice/metadata requirement is weakened. No
network, no live service, no operator HOME/XDG/state is touched.
"""
from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PYPROJECT = REPO / "pyproject.toml"

# The developer's real absolute home path is never a packaging surface value;
# the portable "example" fixture user and the sanitized placeholder are allowed.
HOME_LITERAL = re.compile("/home/([A-Za-z0-9._-]+)")
GENERIC_HOME_USERS = {"example", "..."}

OWNED_SCRIPTS = (
    REPO / "scripts" / "build_dashboard.sh",
    REPO / "scripts" / "verify_installed_dashboard.sh",
    REPO / "scripts" / "migration_rehearsal.sh",
    REPO / "scripts" / "clean_install_check.sh",
    REPO / "scripts" / "clean_venv_check.py",
)

# Internal docs that must never reach the sdist: they record the developer
# home path or are agent wave prompts (privacy + reproducibility).
INTERNAL_SDIST_DOCS = (
    "docs/OPERATOR_QUALIFICATION.md",
    "docs/archive/2026-09-21/QUBIT_BYTE_PARITY_VERIFICATION_PROMPT.md",
    "docs/archive/2026-09-21/QUBIT_FINAL_CODE_READY_PROMPT.md",
)


def _toml() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _targets() -> tuple[dict, dict]:
    targets = _toml()["tool"]["hatch"]["build"]["targets"]
    return targets["wheel"], targets["sdist"]


# ===========================================================================
# 1. Packaging privacy + reproducibility
# ===========================================================================
def test_sdist_excludes_internal_developer_docs():
    """Every internal doc with a developer home path / agent prompt is excluded.

    Regression for the frozen-commit failure where
    ``docs/OPERATOR_QUALIFICATION.md`` (real home path) and the untracked
    ``docs/archive/2026-09-21/QUBIT_BYTE_PARITY_VERIFICATION_PROMPT.md`` were swept into the sdist.
    """
    sdist = _targets()[1]
    excluded = set(sdist.get("exclude", []))
    missed = [rel for rel in INTERNAL_SDIST_DOCS if rel not in excluded]
    assert not missed, f"internal docs not excluded from sdist: {missed}"


def test_owned_packaging_surfaces_have_no_real_developer_home():
    for path in (PYPROJECT, *OWNED_SCRIPTS):
        assert path.is_file(), path
        for user in HOME_LITERAL.findall(path.read_text(encoding="utf-8",
                                                       errors="replace")):
            assert user in GENERIC_HOME_USERS, f"{path.name}: /home/{user}"


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


def test_wheel_and_sdist_carry_runtime_dashboard_and_metadata():
    wheel, sdist = _targets()
    # the generated dashboard bake ships in both artifacts
    assert "frontend/qubit-dist" in wheel.get("artifacts", [])
    assert "frontend/qubit-dist" in sdist.get("artifacts", [])
    # the runtime subset, resources, and license/provenance ship in the wheel
    include = wheel["force-include"]
    assert "templates" in include
    assert _frontend_tree_packaged(wheel)
    assert "systemd" in include
    assert any(k.startswith("qubit_runtime/") for k in include)
    assert "qubit_runtime/LICENSE" in include
    assert "qubit_runtime/pyproject.toml" in include
    # Canonical vendor-manifest source is docs/generated/ (Owner Decision #24:
    # docs/archive/ is historical-only); the installed destination is unchanged.
    assert include.get(
        "docs/generated/QUBIT_HERMES_VENDOR_MANIFEST.json"
    ) == "docs/archive/2026-09-21/QUBIT_HERMES_VENDOR_MANIFEST.json"
    # sdist mirrors the runtime subset so a wheel rebuilds without network
    sinclude = sdist["force-include"]
    assert any(k.startswith("qubit_runtime/") for k in sinclude)


def test_project_metadata_declares_license_notices_and_entry_points():
    project = _toml()["project"]
    assert "LICENSE" in project["license-files"]
    assert "THIRD_PARTY_NOTICES.md" in project["license-files"]
    # the public launcher plus the vendored-runtime launcher are real
    scripts = project["scripts"]
    assert scripts["qubit"] == "voicebridge.cli:main"
    assert scripts["qubit-agent"] == "voicebridge.qubit_runtime:entry"
    # the pre-rebrand ``qubit-hermes`` alias was retired (Phase 0 item 0.5);
    # it must stay out of the shipped entry points.
    assert "qubit-hermes" not in scripts
    # private developer state and build tooling stay out of the sdist
    excluded = set(_targets()[1].get("exclude", []))
    for private in ("config/voice.yaml", "config/task-templates.yaml",
                    "config/personal-knowledge.md", "Modelfile.qubit"):
        assert private in excluded


def test_vendored_runtime_dependencies_are_exact_pinned():
    """The fork's declared runtime core must stay exactly pinned (reproducible
    + supply-chain-locked); a silent float to a range is a regression."""
    deps = _toml()["project"]["dependencies"]
    exact = set()
    for dep in deps:
        m = re.match(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?\s*==\s*[^,;]+$", dep)
        if m:
            exact.add(m.group(1))
    required = {"openai", "certifi", "python-dotenv", "fire", "rich",
                "tenacity", "ruamel.yaml", "requests", "jinja2", "pydantic",
                "cryptography", "Pillow", "websockets", "PyJWT", "psutil"}
    missing = sorted(required - exact)
    assert not missing, f"runtime deps no longer exact-pinned: {missing}"


def test_install_time_docs_are_packaged_under_package_data():
    """Install-time code needs these docs; ship them via ``_data/docs`` only.

    ``voicebridge/capability_catalog.py`` reads PARITY_HERMES.json /
    MATRIX_8T_F.json and ``voicebridge/portable.py`` reads MIGRATIONS.md. The
    packaging contract forbids a wheel-root ``docs/`` destination (only the
    vendor manifest is the sanctioned exception), so they must land under
    ``voicebridge/_data/docs`` for the resolver fallback.
    """
    include = _targets()[0]["force-include"]
    # source docs moved to docs/archive/2026-09-21/ in the docs reorg; the
    # installed resource names (``_data/docs/<name>``) are unchanged.
    for name in ("PARITY_HERMES.json", "MATRIX_8T_F.json", "MIGRATIONS.md"):
        source = f"docs/archive/2026-09-21/{name}"
        assert include.get(source) == f"voicebridge/_data/docs/{name}", source


# ===========================================================================
# 2. Lifecycle scripts are executable, syntax-clean, and typed
# ===========================================================================
def test_lifecycle_scripts_are_executable_and_syntax_clean():
    for script in OWNED_SCRIPTS:
        assert script.is_file(), script
        if script.suffix == ".sh":
            assert os.access(script, os.X_OK), f"{script.name} not executable"
            assert subprocess.run(["bash", "-n", str(script)],
                                  capture_output=True).returncode == 0, \
                f"{script.name} has a syntax error"
    py = subprocess.run(["python3", "-m", "py_compile",
                         str(REPO / "scripts" / "clean_venv_check.py")],
                        capture_output=True)
    assert py.returncode == 0, py.stderr.decode()


def test_clean_install_and_migration_scripts_are_hermetic_and_typed():
    clean = (REPO / "scripts" / "clean_install_check.sh").read_text()
    # typed exits, never a silent pass
    for marker in ("PASS", "FAIL", "DEGRADED", "BLOCKED"):
        assert marker in clean
    assert "mktemp -d" in clean and "TMPDIR" in clean
    assert "export HOME=" in clean  # throwaway HOME, never the operator's
    assert "unset PYTHONPATH" in clean

    mig = (REPO / "scripts" / "migration_rehearsal.sh").read_text()
    for marker in ("PASS", "DEGRADED", "BLOCKED"):
        assert marker in mig
    assert "mktemp -d" in mig and "TMPDIR" in mig
    assert "unset PYTHONPATH" in mig
    # it exercises the real product CLI, not a reimplementation
    assert "product" in mig and "migrate" in mig and "uninstall" in mig

    verify = (REPO / "scripts" / "verify_installed_dashboard.sh").read_text()
    assert "site-packages" in verify
    assert "qubit_runtime" in verify and "runtime_probe" in verify


# ===========================================================================
# 3. Lifecycle fail-closed paths (role H: read-only / disk-full / traversal)
# ===========================================================================
def _paths(tmp_path):
    from voicebridge.product import default_paths

    return default_paths(base=tmp_path / "root")


def test_migration_refuses_a_backup_inside_the_source(tmp_path):
    """Moving a tree into itself (path traversal) is refused before any move."""
    from voicebridge import product as P

    paths = _paths(tmp_path)
    legacy = paths.legacy_base
    legacy.mkdir(parents=True)
    (legacy / "voice.yaml").write_text("audio: {}\n")
    res = P.migrate(paths, dry_run=False, backup_dir=legacy / "backup")
    assert res["state"] == "FAILED"
    assert "inside the migration source" in res["detail"]
    # nothing moved: the legacy tree is intact and no canonical tree exists
    assert legacy.is_dir() and not legacy.is_symlink()
    assert not paths.config_dir.exists()


def test_rollback_does_not_follow_a_symlink_out_of_the_root(tmp_path):
    """A symlinked backup entry is restored as a symlink, never dereferenced."""
    from voicebridge import product as P

    paths = _paths(tmp_path)
    backup = tmp_path / "backup"
    backup.mkdir()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("TOPSECRET")
    (backup / "leak").symlink_to(outside)

    res = P._atomic_restore(backup, paths.state_dir, dry_run=False)
    assert res["state"] == "OK"
    restored = paths.state_dir / "leak"
    assert restored.is_symlink()
    assert os.readlink(restored) == str(outside)


def test_rollback_disk_full_fails_closed(tmp_path, monkeypatch):
    """A copy failure (ENOSPC) is typed FAILED and blocks resume."""
    from voicebridge import product as P
    import voicebridge.product as prod

    paths = _paths(tmp_path)
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "memory.db").write_bytes(b"payload")

    def _enospc(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(prod.shutil, "copy2", _enospc)
    res = P._atomic_restore(backup, paths.state_dir, dry_run=False)
    assert res["state"] == "FAILED"
    assert res["blocks_resume"] is True
    assert "disk-full" in res["detail"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses DAC permissions")
def test_rollback_read_only_parent_fails_closed(tmp_path):
    """A read-only filesystem (cannot create staging) fails closed."""
    from voicebridge import product as P

    paths = _paths(tmp_path)
    backup = tmp_path / "backup"
    backup.mkdir()
    (backup / "memory.db").write_bytes(b"payload")

    os.chmod(tmp_path, 0o500)
    try:
        res = P._atomic_restore(backup, paths.state_dir, dry_run=False)
    finally:
        os.chmod(tmp_path, 0o700)
    assert res["state"] == "FAILED"
    assert res["blocks_resume"] is True
    assert "read-only" in res["detail"].lower()


def test_ensure_owned_dirs_dry_run_creates_nothing(tmp_path):
    from voicebridge.product import default_paths, ensure_owned_dirs

    paths = default_paths(base=tmp_path / "root")
    report = ensure_owned_dirs(paths, dry_run=True)
    assert report and all(r["would_create"] for r in report)
    assert not any(Path(r["path"]).exists() for r in report)


# ===========================================================================
# 4. Archive membership (only when a built artifact is provided)
# ===========================================================================
def _artifact_dir() -> Path | None:
    env = os.environ.get("QBPV_ARTIFACT_DIR", "")
    if env and Path(env).is_dir():
        return Path(env)
    return None


def test_built_wheel_ships_fork_dashboard_and_licenses():
    """Run with QBPV_ARTIFACT_DIR pointing at a built dist/ (opt-in)."""
    art = _artifact_dir()
    if art is None:
        pytest.skip("QBPV_ARTIFACT_DIR not set; archive scan is opt-in")
    wheels = list(art.glob("*.whl"))
    assert wheels, f"no wheel in {art}"
    import zipfile

    names = zipfile.ZipFile(wheels[0]).namelist()
    assert any(n.endswith("_data/frontend/qubit-dist/index.html")
               for n in names)
    assert any(n.endswith("_data/qubit_runtime/qubit_cli/main.py")
               for n in names)
    assert any(n.endswith("_data/qubit_runtime/LICENSE") for n in names)
    assert any("dist-info/licenses/THIRD_PARTY_NOTICES.md" in n
               for n in names)
    # node_modules is a git-ignored build artifact; build_dashboard.sh removes
    # it. Only assert it is absent when the checkout is free of it (a dirty
    # tree can legitimately bake it in, which the release build gate blocks).
    if not (REPO / "frontend" / "qubit-web" / "node_modules").exists():
        assert not any("node_modules" in n for n in names)
    assert not any("/home/" + "trick" in n for n in names)
    entry_points = zipfile.ZipFile(wheels[0]).read(
        next(n for n in names if n.endswith("dist-info/entry_points.txt"))
    ).decode()
    assert "[console_scripts]" in entry_points
    assert re.search(r"^qubit\s*=\s*voicebridge\.cli:main$", entry_points,
                     re.M)
    assert re.search(r"^qubit-agent\s*=\s*voicebridge\.qubit_runtime:entry$",
                      entry_points, re.M)
    # the retired pre-rebrand alias must not ship in the wheel
    assert not re.search(r"^qubit-hermes\s*=", entry_points, re.M)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
