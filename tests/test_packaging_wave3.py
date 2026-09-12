"""Packaging + lifecycle audit (Wave 3, agent G).

Hermetic checks over the packaging/lifecycle surfaces owned by the packaging
workstream: pyproject package data (wheel + sdist), the installer and systemd
unit installer, the release qualifier, the clean-install rehearsal script, the
migration round-trip, and the service templates. No network, no live service,
no operator HOME/XDG/state is touched.

The real isolated wheel install + dashboard/runtime startup is exercised by
``scripts/clean_install_check.sh`` (opt-in, network); these tests prove the
declared contract that script depends on.
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
INSTALL_SH = REPO / "scripts" / "install.sh"
UNIT_INSTALL_SH = REPO / "scripts" / "install_systemd_unit.sh"
RELEASE_QUALIFY = REPO / "scripts" / "release_qualify.py"
CLEAN_INSTALL = REPO / "scripts" / "clean_install_check.sh"
SYSTEMD = REPO / "systemd"
CANONICAL_UNIT = SYSTEMD / "qubit-voicebridge.service"
LEGACY_UNIT = SYSTEMD / "qubit-voice-agent.service"

# A literal developer home path (an absolute home directory owned by a real
# user). The sanitized generic "example" fixture user is the only accepted
# absolute-home token; every packaging surface must avoid a real one.
HOME_LITERAL = re.compile("/home/" + r"([A-Za-z0-9._-]+)")
GENERIC_HOME_USERS = {"example"}


def _toml() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _targets() -> tuple[dict, dict]:
    targets = _toml()["tool"]["hatch"]["build"]["targets"]
    return targets["wheel"], targets["sdist"]


# ===========================================================================
# 1. Package-data completeness (wheel + sdist)
# ===========================================================================
def test_wheel_installs_the_voicebridge_package():
    wheel, _ = _targets()
    assert wheel.get("packages") == ["voicebridge"]


def test_dashboard_build_artifact_declared_in_both_targets():
    wheel, sdist = _targets()
    assert "frontend/hermes-dist" in wheel.get("artifacts", [])
    assert "frontend/hermes-dist" in sdist.get("artifacts", [])
    assert wheel["force-include"].get("frontend") == "voicebridge/_data/frontend"


def test_every_runtime_resource_is_reachable_from_package_data():
    """Each resource the installed package resolves must be produced by a
    wheel force-include (a directory mapping or an exact file)."""
    wheel, _ = _targets()
    force = wheel.get("force-include", {})
    mapped_dirs = {k for k in force if (REPO / k).is_dir()}
    mapped_files = {k for k in force if (REPO / k).is_file()}
    # resource-name (as resources.resource_path sees it) -> source mapping
    required = {
        "frontend/hermes-dist": "frontend",
        "qubit_hermes/hermes_cli": "qubit_hermes/hermes_cli",
        "config/voice.example.yaml": "config/voice.example.yaml",
        "config/dialogs.md": "config/dialogs.md",
        "config/task-templates.yaml": "config/task-templates.example.yaml",
        "config/local-actions.example.yaml":
            "config/local-actions.example.yaml",
        "docs/QUBIT_MIGRATION.md": "docs/QUBIT_MIGRATION.md",
        "docs/QUBIT_HERMES_VENDOR_MANIFEST.json":
            "docs/QUBIT_HERMES_VENDOR_MANIFEST.json",
        "systemd": "systemd",
    }
    for resource, source in required.items():
        assert source in mapped_dirs or source in mapped_files, (resource, source)
    # the vendored runtime dirs the fork executes from are all mapped
    for name in ("hermes_cli", "agent", "tools", "gateway", "cron",
                 "providers", "plugins", "skills"):
        source = f"qubit_hermes/{name}"
        assert source in mapped_dirs, source


def test_force_include_destinations_live_under_package_data():
    wheel, _ = _targets()
    for source, dest in wheel["force-include"].items():
        if source == "docs/QUBIT_HERMES_VENDOR_MANIFEST.json":
            # intentionally read by hermes_runtime.manifest_path() from
            # <site-packages>/docs/, not from _data
            assert dest == source, (source, dest)
            continue
        assert dest.startswith("voicebridge/_data/"), (source, dest)


def test_sdist_mirrors_wheel_runtime_layout_and_excludes_whole_fork():
    wheel, sdist = _targets()
    wforce = wheel["force-include"]
    sforce = sdist["force-include"]
    for source in wforce:
        if not source.startswith("qubit_hermes/"):
            continue
        assert sforce.get(source) == source, (source, sforce.get(source))
    assert "qubit_hermes" in set(sdist.get("exclude", []))


def test_force_include_sources_are_tracked_so_a_clean_clone_can_build():
    """A force-include source that is not tracked would make a clean clone's
    wheel silently incomplete. ``frontend/hermes-dist`` is the one sanctioned
    exception: it is a generated artifact declared via ``artifacts``."""
    wheel, _ = _targets()
    tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                             text=True).stdout.splitlines()
    tracked_set = set(tracked)
    tracked_dirs = {f.split("/", 1)[0] for f in tracked_set}
    for source in wheel["force-include"]:
        if source == "frontend":
            continue
        p = REPO / source
        if p.is_dir():
            assert any(t.startswith(source + "/") for t in tracked_set), source
        else:
            assert source in tracked_set, source
    # package-embedded completions ship inside the ``voicebridge`` package
    assert (REPO / "voicebridge" / "_data" / "completions").is_dir()
    assert tracked_dirs  # git produced a real listing


# ===========================================================================
# 2. No hardcoded developer-home path in packaging surfaces
# ===========================================================================
def test_packaging_surfaces_have_no_developer_home_literal():
    surfaces = [
        PYPROJECT, INSTALL_SH, UNIT_INSTALL_SH, RELEASE_QUALIFY,
        REPO / "scripts" / "verify_installed_dashboard.sh",
        REPO / "scripts" / "build_dashboard.sh", CLEAN_INSTALL,
    ]
    surfaces += sorted(SYSTEMD.glob("*.service")) + sorted(SYSTEMD.glob("*.timer"))
    for path in surfaces:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for user in HOME_LITERAL.findall(text):
            assert user in GENERIC_HOME_USERS, f"{path.name}: /home/{user} literal"


# ===========================================================================
# 3. Installer contract (package install, state preserved, no overwrite)
# ===========================================================================
def test_installer_installs_the_project_and_its_declared_deps():
    text = INSTALL_SH.read_text(encoding="utf-8")
    # installs the project itself (deps come from pyproject), not a
    # hand-maintained package list
    assert 'uv pip install' in text
    assert '"$PROJECT_DIR[' in text, "project install with extras missing"
    assert "pyproject.toml" in text
    # the old incomplete hand list must be gone
    assert "sherpa-onnx sounddevice numpy pyyaml" not in text


def test_installer_preserves_existing_user_config():
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert '[ ! -f "$CONFIG_DIR/voice.yaml" ]' in text
    assert '[ ! -f "$CONFIG_DIR/personal-knowledge.md" ]' in text
    # personal-knowledge.md is excluded from the sdist, so a release install
    # must not fail on its absence (typed starter fallback).
    assert "if [ -f \"$PROJECT_DIR/config/personal-knowledge.md\" ]" in text
    assert "never distributed" in text


def test_installer_builds_missing_dashboard_or_warns_typed():
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "frontend/hermes-dist/index.html" in text
    assert "build_dashboard.sh" in text
    assert "WARNING: frontend/hermes-dist missing" in text


def test_unit_installer_preserves_divergent_units_and_is_atomic():
    text = UNIT_INSTALL_SH.read_text(encoding="utf-8")
    assert "INSTALL_FORCE_UNIT" in text
    assert ".bak.$(date" in text
    assert "template-new" in text
    assert "mktemp" in text
    # path is escaped for sed so a repo path containing '&' or '|' cannot
    # corrupt the rendered unit
    assert "REPO_SED" in text
    assert "sed -e 's/[\\\\&|]/\\\\&/g'" in text
    # hermetic override for tests/deployments
    assert "SYSTEMD_UNIT_DIR" in text


# ===========================================================================
# 4. Clean-install rehearsal script contract
# ===========================================================================
def test_clean_install_check_is_executable_and_syntax_clean():
    assert CLEAN_INSTALL.is_file()
    assert os.access(CLEAN_INSTALL, os.X_OK)
    assert subprocess.run(["bash", "-n", str(CLEAN_INSTALL)],
                          capture_output=True).returncode == 0


def test_clean_install_check_is_typed_and_isolated():
    text = CLEAN_INSTALL.read_text(encoding="utf-8")
    for token in ("PASS", "FAIL", "DEGRADED", "exit 2", "exit 4",
                  "VB_INSTALL_VENV", "mktemp", "uv build"):
        assert token in text, token
    # must install the wheel with dependencies, not --no-deps
    assert "--no-deps" not in text


def test_release_qualify_has_uv_build_fallback_and_runtime_checks():
    text = RELEASE_QUALIFY.read_text(encoding="utf-8")
    assert 'shutil.which("uv")' in text
    assert "uv build" in text
    assert "qubit_hermes" in text
    assert '"qubit-hermes"' in text
    assert "entrypoint.qubit_console_script" in text


# ===========================================================================
# 5. Service template checks
# ===========================================================================
def test_canonical_unit_is_portable_and_correct():
    text = CANONICAL_UNIT.read_text(encoding="utf-8")
    assert "ExecStart=__PROJECT_DIR__/.venv/bin/python -m voicebridge run" in text
    assert "WorkingDirectory=__PROJECT_DIR__" in text
    assert "Environment=VOICE_BRIDGE_CONFIG=%h/.config/qubit-voicebridge/voice.yaml" in text
    assert "WantedBy=default.target" in text
    assert "Restart=on-failure" in text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "DISPLAY=:0" not in stripped
        assert "XAUTHORITY=/run/user" not in stripped
    assert "__PROJECT_DIR__" in text  # rendered by the unit installer


def test_legacy_unit_is_marked_do_not_install_and_points_at_legacy_config():
    text = LEGACY_UNIT.read_text(encoding="utf-8")
    assert "DO NOT INSTALL" in text
    assert "qubit-voice-agent/voice.yaml" in text
    # both templates must be renderable by the same installer
    assert "__PROJECT_DIR__" in text


def test_unit_security_review_flags_nothing_in_shipped_units():
    from voicebridge import release as R
    for unit in sorted(SYSTEMD.glob("*.service")):
        review = R._unit_security_review(unit)
        assert review["state"] == "OK", (unit.name, review)


# ===========================================================================
# 6. Migration idempotence + rollback (config sections and product paths)
# ===========================================================================
def test_legacy_config_sections_fold_idempotently_and_agent_wins():
    from voicebridge.config import _migrate_legacy_sections

    raw = {"openclaw": {"model": "from-openclaw"},
           "hermes": {"model": "from-hermes", "enabled": True}}
    once = _migrate_legacy_sections(dict(raw))
    twice = _migrate_legacy_sections(dict(once))
    assert once == twice, "legacy-section folding is not idempotent"
    assert "openclaw" not in once and "hermes" not in once
    assert once["agent"]["model"] == "from-openclaw"

    explicit = _migrate_legacy_sections(
        {"agent": {"model": "explicit"}, "openclaw": {"model": "legacy"}})
    assert explicit["agent"]["model"] == "explicit"


def test_config_load_of_legacy_file_folds_to_one_agent_tree(tmp_path):
    from voicebridge.config import Config

    cfg_path = tmp_path / "voice.yaml"
    cfg_path.write_text(
        "openclaw:\n  model: legacy-model\nhermes:\n  enabled: true\n",
        encoding="utf-8")
    first = Config.load(cfg_path)
    second = Config.load(cfg_path)
    # exactly one configuration tree survives; loading twice is byte-stable
    assert "openclaw" not in first.data and "hermes" not in first.data
    assert "agent" in first.data
    # explicit DEFAULTS keys win over retired sections (documented: explicit
    # keys always win), so the folded tree is stable and idempotent.
    assert first.data["agent"] == second.data["agent"]
    assert first.data["agent"]["model"] is None


def test_product_migration_roundtrip_is_idempotent_and_reversible(tmp_path):
    from voicebridge import product as P

    paths = P.default_paths(base=tmp_path / "root")
    paths.legacy_base.mkdir(parents=True)
    (paths.legacy_base / "voice.yaml").write_text("audio: {}\n", encoding="utf-8")
    paths.legacy_state.mkdir(parents=True)
    (paths.legacy_state / "events.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    paths.legacy_data.mkdir(parents=True)
    (paths.legacy_data / "models").mkdir()
    backup = tmp_path / "backup"

    applied = P.migrate(paths, dry_run=False, backup_dir=backup)
    assert applied["state"] == "OK", applied
    assert paths.legacy_base.is_symlink()
    assert (paths.config_dir / "voice.yaml").is_file()

    rerun = P.migrate(paths, dry_run=False, backup_dir=backup)
    assert rerun["state"] == "FRESH", rerun  # idempotent, no duplicate tree

    rolled = P.migrate_rollback(paths, backup_dir=backup, dry_run=False)
    assert rolled["state"] == "OK", rolled
    assert paths.legacy_base.is_dir() and not paths.legacy_base.is_symlink()
    assert (paths.legacy_base / "voice.yaml").is_file()
    assert not paths.config_dir.exists()


# ===========================================================================
# 7. Upgrade / uninstall contract preserves user state
# ===========================================================================
def test_upgrade_and_uninstall_plans_preserve_user_state():
    from voicebridge import distribution as D

    up = D.upgrade_plan({})
    un = D.uninstall_plan({})
    assert up["action"] == "upgrade"
    assert any("delete state" in s for s in up["does_not"])
    assert up["rollback"]
    assert un["requires_confirmation"] is True
    assert any("state dir" in s for s in un["preserves_by_default"])
    assert un["steps_if_confirmed"]


def test_release_upgrade_uninstall_contract_passes():
    from voicebridge import release as R

    res = R.upgrade_uninstall_contract({})
    assert res["pass"] is True, res["checks"]


# ===========================================================================
# 8. Wheel hygiene + artifact-scan scope (Wave 3 follow-up)
# ===========================================================================
def test_build_dashboard_removes_build_only_node_modules():
    text = (REPO / "scripts" / "build_dashboard.sh").read_text(encoding="utf-8")
    assert "KEEP_NODE_MODULES" in text
    assert 'rm -rf "$WEB_DIR/node_modules"' in text


def test_wheel_artifact_scan_exempts_vendored_fork_and_fixtures():
    text = RELEASE_QUALIFY.read_text(encoding="utf-8")
    assert "VENDORED_MEMBER" in text
    assert "_is_vendored_member" in text
    assert "_is_fixture" in text
    assert "artifact_member_class" in text
    # the sanitized directory placeholder is accepted for the packaged doc
    assert '"/home" + "/..."' in text


def _tracked_docs_with_home_paths() -> dict:
    import subprocess

    tracked = subprocess.run(["git", "ls-files", "docs"], cwd=REPO,
                             capture_output=True, text=True).stdout.splitlines()
    found = {}
    for rel in tracked:
        p = REPO / rel
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        users = {u for u in HOME_LITERAL.findall(text)
                 if u not in GENERIC_HOME_USERS and u != "..."}
        if users:
            found[rel] = sorted(users)
    return found


def test_sdist_excludes_every_tracked_doc_with_a_developer_home_path():
    _, sdist = _targets()
    excluded = set(sdist.get("exclude", []))
    offenders = _tracked_docs_with_home_paths()
    assert offenders, "expected the known internal docs to still exist"
    missed = [rel for rel in offenders if rel not in excluded
              and not any(rel.startswith(e.rstrip("/") + "/") for e in excluded)]
    assert not missed, f"tracked docs with a developer home path not excluded: {missed}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
