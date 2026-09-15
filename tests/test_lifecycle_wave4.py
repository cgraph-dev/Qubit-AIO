"""Lifecycle audit (Wave 4, agent G): committed packaging + migration evidence.

Hermetic checks over the packaging/lifecycle surfaces owned by the packaging
workstream. Proves the declarative contract behind the isolated install gate
(``scripts/clean_install_check.sh`` / ``scripts/verify_installed_dashboard.sh``)
and runs the new hermetic migration rehearsal
(``scripts/migration_rehearsal.sh``) end to end.

No network, no live service, and no operator HOME/XDG/state is touched.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

PYPROJECT = REPO / "pyproject.toml"
MIGRATIONS_DOC = REPO / "docs" / "MIGRATIONS.md"
LIFECYCLE_DOC = REPO / "docs" / "LIFECYCLE_AUDIT_WAVE4.md"
REHEARSAL = REPO / "scripts" / "migration_rehearsal.sh"
CLEAN_INSTALL = REPO / "scripts" / "clean_install_check.sh"
VERIFY_DASHBOARD = REPO / "scripts" / "verify_installed_dashboard.sh"
RELEASE_QUALIFY = REPO / "scripts" / "release_qualify.py"
BUILD_DASHBOARD = REPO / "scripts" / "build_dashboard.sh"
INSTALL_SH = REPO / "scripts" / "install.sh"
UNIT_INSTALL_SH = REPO / "scripts" / "install_systemd_unit.sh"
SYSTEMD = REPO / "systemd"

HOME_LITERAL = re.compile("/home/" + r"([A-Za-z0-9._-]+)")
GENERIC_HOME_USERS = {"example", "..."}

LIFECYCLE_SURFACES = [
    PYPROJECT, INSTALL_SH, UNIT_INSTALL_SH, RELEASE_QUALIFY, CLEAN_INSTALL,
    VERIFY_DASHBOARD, BUILD_DASHBOARD, REHEARSAL, MIGRATIONS_DOC,
    LIFECYCLE_DOC,
]


def _toml() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _targets() -> tuple[dict, dict]:
    targets = _toml()["tool"]["hatch"]["build"]["targets"]
    return targets["wheel"], targets["sdist"]


# ===========================================================================
# 1. Package-data completeness (wheel + sdist)
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


def test_wheel_and_sdist_declare_the_runtime_resources():
    wheel, sdist = _targets()
    assert wheel.get("packages") == ["voicebridge"]
    assert "frontend/hermes-dist" in wheel.get("artifacts", [])
    assert "frontend/hermes-dist" in sdist.get("artifacts", [])
    assert _frontend_tree_packaged(wheel)
    # the vendored runtime the fork executes from is packaged
    for name in ("hermes_cli", "agent", "tools", "gateway", "plugins",
                 "skills", "providers", "cron"):
        assert f"qubit_hermes/{name}" in wheel["force-include"], name


def test_sdist_mirrors_wheel_runtime_layout_and_excludes_the_whole_fork():
    wheel, sdist = _targets()
    for source, dest in wheel["force-include"].items():
        if source.startswith("qubit_hermes/"):
            assert sdist["force-include"].get(source) == source, source
            assert dest == f"voicebridge/_data/{source}", (source, dest)
    assert "qubit_hermes" in set(sdist.get("exclude", []))


def test_force_include_sources_are_tracked_for_a_clean_clone_build():
    wheel, _ = _targets()
    tracked = set(subprocess.run(["git", "ls-files"], cwd=REPO,
                                 capture_output=True, text=True).stdout.splitlines())
    for source in wheel["force-include"]:
        if source == "frontend":
            continue  # generated artifact declared via `artifacts`
        p = REPO / source
        if p.is_dir():
            assert any(t.startswith(source + "/") for t in tracked), source
        else:
            assert source in tracked, source


# ===========================================================================
# 2. No hardcoded developer-home path in any owned lifecycle surface
# ===========================================================================
def test_lifecycle_surfaces_have_no_developer_home_literal():
    for path in LIFECYCLE_SURFACES:
        assert path.exists(), path
        text = path.read_text(encoding="utf-8", errors="replace")
        for user in HOME_LITERAL.findall(text):
            assert user in GENERIC_HOME_USERS, f"{path.name}: /home/{user} literal"


def test_rehearsal_script_carries_no_secret_assignment_shape():
    from voicebridge import release as R

    text = REHEARSAL.read_text(encoding="utf-8")
    shapes = R.scan_text_secrets(text, is_config=True)
    assert not shapes, f"rehearsal script trips the secret gate: {shapes}"


# ===========================================================================
# 3. Rehearsal script contract + green run
# ===========================================================================
def test_rehearsal_script_is_executable_and_syntax_clean():
    assert REHEARSAL.is_file()
    assert os.access(REHEARSAL, os.X_OK)
    assert subprocess.run(["bash", "-n", str(REHEARSAL)],
                          capture_output=True).returncode == 0


def test_rehearsal_script_is_typed_and_isolated():
    text = REHEARSAL.read_text(encoding="utf-8")
    for token in ("PASS", "FAIL", "DEGRADED", "BLOCKED", "exit 2", "exit 4",
                  "exit 8", "mktemp", "XDG_CONFIG_HOME", "XDG_STATE_HOME",
                  "XDG_DATA_HOME", "home", "product", "migrate-rollback",
                  "package", "uninstall"):
        assert token in text, token
    # never touches the live tree: throwaway HOME and cleanup trap
    assert 'trap ' in text and "rm -rf" in text


def test_migration_rehearsal_runs_green():
    env = dict(os.environ)
    env["PYTHON"] = sys.executable
    r = subprocess.run(["bash", str(REHEARSAL)], cwd=str(REPO), env=env,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, (
        f"rehearsal rc={r.returncode}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    for stage in ("migration", "upgrade", "rollback", "uninstall"):
        assert stage in r.stdout, stage
    assert "state preserved" in r.stdout.lower() or "preserved" in r.stdout


# ===========================================================================
# 4. Migration idempotence + rollback + credential preservation
# ===========================================================================
def test_migration_is_idempotent_reversible_and_preserves_credentials(tmp_path):
    from voicebridge import product as P

    paths = P.default_paths(base=tmp_path / "root")
    paths.legacy_base.mkdir(parents=True)
    (paths.legacy_base / "voice.yaml").write_text("audio: {}\n", encoding="utf-8")
    (paths.legacy_base / "credentials.json").write_text(
        '{"synthetic": "placeholder"}\n', encoding="utf-8")
    paths.legacy_state.mkdir(parents=True)
    (paths.legacy_state / "events.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    paths.legacy_data.mkdir(parents=True)
    (paths.legacy_data / "weights.bin").write_bytes(b"synthetic\n")
    backup = tmp_path / "backup"
    original = (paths.legacy_base / "credentials.json").read_bytes()

    applied = P.migrate(paths, dry_run=False, backup_dir=backup)
    assert applied["state"] == "OK", applied
    assert paths.legacy_base.is_symlink()
    assert (paths.config_dir / "voice.yaml").is_file()
    # the live move preserves the credential file byte-for-byte ...
    assert (paths.config_dir / "credentials.json").read_bytes() == original
    # ... while the safety backup must NOT copy credentials.
    leaked = [str(p) for p in backup.rglob("*") if "credential" in p.name.lower()]
    assert not leaked, f"credentials copied into the backup: {leaked}"

    rerun = P.migrate(paths, dry_run=False, backup_dir=backup)
    assert rerun["state"] == "FRESH", rerun  # idempotent, no duplicate tree

    rolled = P.migrate_rollback(paths, backup_dir=backup, dry_run=False)
    assert rolled["state"] == "OK", rolled
    assert paths.legacy_base.is_dir() and not paths.legacy_base.is_symlink()
    assert (paths.legacy_base / "voice.yaml").is_file()
    assert (paths.legacy_base / "credentials.json").read_bytes() == original
    assert not paths.config_dir.exists()


# ===========================================================================
# 5. Upgrade / uninstall preserve user state
# ===========================================================================
def test_uninstall_plan_requires_confirmation_and_preserves_state():
    from voicebridge import distribution as D

    un = D.uninstall_plan({})
    assert un["action"] == "uninstall"
    assert un["requires_confirmation"] is True
    joined = " ".join(un["preserves_by_default"]).lower()
    assert "state dir" in joined and "user config" in joined
    assert un["steps_if_confirmed"]
    assert un["rollback"]
    up = D.upgrade_plan({})
    assert any("delete state" in s for s in up["does_not"])
    assert up["rollback"]


def test_release_upgrade_uninstall_contract_passes():
    from voicebridge import release as R

    res = R.upgrade_uninstall_contract({})
    assert res["pass"] is True, res["checks"]


def test_uninstall_plan_is_read_only(tmp_path):
    from voicebridge import distribution as D
    from voicebridge.config import Config

    state = tmp_path / "state"
    state.mkdir()
    (state / "events.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    cfg_file = tmp_path / "voice.yaml"
    cfg_file.write_text(f"logging:\n  jsonl_path: {state}\n", encoding="utf-8")
    cfg = Config.load(cfg_file)
    before = {str(p.relative_to(tmp_path)): p.read_bytes()
              for p in tmp_path.rglob("*") if p.is_file()}
    D.uninstall_plan(cfg)
    after = {str(p.relative_to(tmp_path)): p.read_bytes()
             for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before, "uninstall_plan mutated user state"


# ===========================================================================
# 6. Service templates + atomic unit installer
# ===========================================================================
def test_service_templates_are_portable_and_correct():
    canonical = (SYSTEMD / "qubit-voicebridge.service").read_text(encoding="utf-8")
    assert "ExecStart=__PROJECT_DIR__/.venv/bin/python -m voicebridge run" in canonical
    assert "WantedBy=default.target" in canonical
    assert "Restart=on-failure" in canonical
    assert "__PROJECT_DIR__" in canonical
    legacy = (SYSTEMD / "qubit-voice-agent.service").read_text(encoding="utf-8")
    assert "DO NOT INSTALL" in legacy and "__PROJECT_DIR__" in legacy


def test_unit_installer_is_atomic_and_preserving():
    text = UNIT_INSTALL_SH.read_text(encoding="utf-8")
    for token in ("INSTALL_FORCE_UNIT", ".bak.$(date", "template-new", "mktemp",
                  "REPO_SED", "SYSTEMD_UNIT_DIR"):
        assert token in text, token


def test_clean_install_and_dashboard_gates_declare_package_data_probe():
    clean = CLEAN_INSTALL.read_text(encoding="utf-8")
    for token in ("VB_INSTALL_VENV", "resources.resource_path", "hermes_runtime",
                  "frontend/hermes-dist/index.html", "mktemp", "exit 2", "exit 4"):
        assert token in clean, token
    assert "--no-deps" not in clean
    verify = VERIFY_DASHBOARD.read_text(encoding="utf-8")
    for token in ("uv build", "uv venv", "--no-deps", "hr.status()",
                  "/agent/api/dashboard/plugins"):
        assert token in verify, token


# ===========================================================================
# 7. Migration log documents the wave-4 lifecycle rehearsal
# ===========================================================================
def test_migrations_doc_records_the_wave4_lifecycle_entry():
    text = MIGRATIONS_DOC.read_text(encoding="utf-8")
    assert "MIG-025" in text
    assert "migration_rehearsal.sh" in text
    assert "rollback" in text.lower() and "uninstall" in text.lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
