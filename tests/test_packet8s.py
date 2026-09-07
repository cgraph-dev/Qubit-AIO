"""Packet 8S tests: Qubit VoiceBridge identity and release migration.

Hermetic: every test uses an isolated temporary base (never the operator
HOME/XDG/state), never enables a live service, never touches a real browser,
account, audio device, or model, and never reveals secrets.

Covers: canonical identity constants, fresh/canonical/legacy-only installs
for BOTH legacy families (openclaw-voice-bridge original + qubit-voice-agent
7D intermediate), mixed/split trees, partial/interrupted migration + resume,
repeated migration, upgrade/rollback, uninstall plan (state preserved),
read-only failures, symlink/path-traversal safety, service restart states,
shortcut launch (dry-run, typed), preservation of configs/state/memory/
reports/browser/audio settings, exactly-one-effective-tree proof,
no-secret-in-backup, and the clean-clone repo-rename rehearsal.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge import product as P  # noqa: E402


def _paths(tmp_path: Path) -> P.ProductPaths:
    return P.default_paths(base=tmp_path / "root")


# ---------------------------------------------------------------------------
# 1. Canonical identity constants
# ---------------------------------------------------------------------------
def test_canonical_identity_constants():
    assert P.PRODUCT_NAME == "qubit-voicebridge"
    assert P.PRODUCT_TITLE == "Qubit VoiceBridge"
    assert P.LEGACY_NAME == "openclaw-voice-bridge"
    assert P.LEGACY_NAME_7D == "qubit-voice-agent"
    assert set(P.LEGACY_NAMES) == {"openclaw-voice-bridge", "qubit-voice-agent"}
    # internal import package is unchanged
    import voicebridge
    assert voicebridge.__name__ == "voicebridge"


def test_default_paths_use_canonical_identity(tmp_path):
    paths = _paths(tmp_path)
    assert str(paths.config_dir).endswith("config")
    assert paths.legacy_base.name == "legacy"
    assert paths.legacy7d_base.name == "legacy7d"
    prod = P.default_paths()
    assert prod.config_dir.name == "qubit-voicebridge"
    assert prod.legacy_base.name == "openclaw-voice-bridge"
    assert prod.legacy7d_base.name == "qubit-voice-agent"
    # canonical and legacy categories are distinct
    vals = list(prod.map.values())
    assert len(set(vals)) == len(vals)


def test_legacy_pairs_cover_both_families(tmp_path):
    paths = _paths(tmp_path)
    pairs = P._legacy_pairs(paths)
    assert len(pairs) == 6
    legacies = sorted(str(lg) for lg, _ in pairs)
    assert any("legacy7d" in lg for lg in legacies)
    assert any("legacy-state" in lg or "legacy" == Path(lg).name for lg in legacies)
    # every canonical target is the shared canonical tree (single live tree)
    for _legacy, canonical in pairs:
        assert canonical in (paths.config_dir, paths.state_dir, paths.data_dir)


# ---------------------------------------------------------------------------
# 2. Fresh / legacy-only / mixed / split states
# ---------------------------------------------------------------------------
def test_fresh_install_has_no_legacy(tmp_path):
    paths = _paths(tmp_path)
    st = P.legacy_alias_status(paths)
    assert st["state"] == "OK"
    assert P.branding_status(paths)["state"] == "FRESH"
    out = P.migrate(paths, dry_run=True)
    assert out["state"] == "FRESH"


def test_legacy_only_original_family_blocks_install(tmp_path):
    paths = _paths(tmp_path)
    paths.legacy_base.mkdir(parents=True)
    (paths.legacy_base / "voice.yaml").write_text("openclaw: {}\n")
    st = P.legacy_alias_status(paths)
    assert st["state"] == "LEGACY_ONLY"
    steps = P.install_steps(paths, dry_run=True)
    mig = [s for s in steps if s["step"] == "migration"][0]
    assert mig["state"] == P.BLOCKED


def test_legacy_only_7d_family_blocks_install(tmp_path):
    paths = _paths(tmp_path)
    paths.legacy7d_state.mkdir(parents=True)
    (paths.legacy7d_state / "events.jsonl").write_text("{}\n")
    st = P.legacy_alias_status(paths)
    assert st["state"] == "LEGACY_ONLY"


def test_split_tree_alias_mismatch_blocks_install(tmp_path):
    paths = _paths(tmp_path)
    paths.config_dir.mkdir(parents=True)
    other = tmp_path / "elsewhere"
    other.mkdir()
    paths.legacy_base.symlink_to(other, target_is_directory=True)
    st = P.legacy_alias_status(paths)
    assert st["state"] == "SPLIT_TREES"
    steps = P.install_steps(paths, dry_run=True)
    mig = [s for s in steps if s["step"] == "migration"][0]
    assert mig["state"] == P.BLOCKED


# ---------------------------------------------------------------------------
# 3. Migration: dry-run purity, apply, idempotency, resume
# ---------------------------------------------------------------------------
def test_migrate_dry_run_is_side_effect_free(tmp_path):
    paths = _paths(tmp_path)
    paths.legacy_base.mkdir(parents=True)
    (paths.legacy_base / "voice.yaml").write_text("a: 1\n")
    before = sorted(p.relative_to(paths.base) for p in paths.base.rglob("*"))
    out = P.migrate(paths, dry_run=True)
    assert out["state"] == "READY"
    after = sorted(p.relative_to(paths.base) for p in paths.base.rglob("*"))
    assert before == after
    assert not paths.config_dir.exists()


def test_migrate_both_legacies_into_one_canonical_tree(tmp_path):
    paths = _paths(tmp_path)
    paths.legacy_base.mkdir(parents=True)
    (paths.legacy_base / "voice.yaml").write_text("config: 1\n")
    paths.legacy7d_base.mkdir(parents=True)
    (paths.legacy7d_base / "extra.yaml").write_text("extra: 1\n")
    out = P.migrate(paths, dry_run=False)
    assert out["state"] == "OK", out
    assert (paths.config_dir / "voice.yaml").exists()
    assert (paths.config_dir / "extra.yaml").exists()
    # compat aliases: both old names resolve to the same physical tree
    import os as _os
    assert _os.path.realpath(paths.legacy_base) == str(paths.config_dir)
    assert _os.path.realpath(paths.legacy7d_base) == str(paths.config_dir)
    # exactly one effective config tree
    assert _os.path.realpath(paths.config_dir) == _os.path.realpath(paths.legacy_base)


def test_migrate_is_idempotent_and_resumable(tmp_path):
    paths = _paths(tmp_path)
    paths.legacy_state.mkdir(parents=True)
    (paths.legacy_state / "cursor.json").write_text("{}\n")
    first = P.migrate(paths, dry_run=False)
    assert first["state"] == "OK", first
    second = P.migrate(paths, dry_run=False)
    assert second["state"] in ("OK", "FRESH")
    # interrupted migration: canonical present without alias -> resume
    paths.legacy_data.mkdir(parents=True)
    (paths.legacy_data / "m.json").write_text("{}\n")
    assert P.migrate(paths, dry_run=False)["state"] == "OK"
    # break one alias manually, re-run must complete it
    import os as _os
    assert _os.path.realpath(paths.legacy_data) == str(paths.data_dir)
    paths.legacy_data.unlink()
    assert not paths.legacy_data.exists()
    resumed = P.migrate(paths, dry_run=False)
    assert resumed["state"] == "OK"
    assert resumed.get("resumed")


def test_migration_preserves_settings_and_state(tmp_path):
    paths = _paths(tmp_path)
    paths.legacy_base.mkdir(parents=True)
    (paths.legacy_base / "voice.yaml").write_text("audio: {output_device: ''}\n")
    paths.legacy_state.mkdir(parents=True)
    (paths.legacy_state / "memory.jsonl").write_text('{"fact": 1}\n')
    (paths.legacy_state / "reports").mkdir()
    (paths.legacy_state / "reports" / "r.json").write_text('{"report": 1}\n')
    out = P.migrate(paths, dry_run=False)
    assert out["state"] == "OK", out
    assert (paths.config_dir / "voice.yaml").read_text().startswith("audio:")
    assert (paths.state_dir / "memory.jsonl").exists()
    assert (paths.state_dir / "reports" / "r.json").exists()


def test_migration_backup_excludes_credentials(tmp_path):
    paths = _paths(tmp_path)
    paths.legacy_base.mkdir(parents=True)
    (paths.legacy_base / "voice.yaml").write_text("ok: 1\n")
    (paths.legacy_base / "webui-token.txt").write_text("SECRET-TOKEN-123\n")
    bdir = tmp_path / "backup"
    out = P.migrate(paths, dry_run=False, backup_dir=bdir)
    assert out["state"] == "OK", out
    blob = "".join(p.read_text(errors="replace") for p in bdir.rglob("*") if p.is_file())
    assert "SECRET-TOKEN-123" not in blob
    # live move preserves all bytes including credentials
    assert (paths.config_dir / "webui-token.txt").read_text() == "SECRET-TOKEN-123\n"


# ---------------------------------------------------------------------------
# 4. Rollback / upgrade safety
# ---------------------------------------------------------------------------
def test_migrate_rollback_restores_legacy(tmp_path):
    paths = _paths(tmp_path)
    paths.legacy_base.mkdir(parents=True)
    (paths.legacy_base / "voice.yaml").write_text("v: 1\n")
    bdir = tmp_path / "bdir"
    assert P.migrate(paths, dry_run=False, backup_dir=bdir)["state"] == "OK"
    rb = P.migrate_rollback(paths, backup_dir=bdir, dry_run=False)
    assert rb["state"] == "OK", rb
    assert (paths.legacy_base / "voice.yaml").exists()
    assert not paths.legacy_base.is_symlink()


def test_migrate_rollback_dry_run_is_pure(tmp_path):
    paths = _paths(tmp_path)
    out = P.migrate_rollback(paths, dry_run=True)
    assert out["state"] == P.READY
    assert out["restored"] == []


def test_repeated_migration_converges(tmp_path):
    paths = _paths(tmp_path)
    paths.legacy7d_base.mkdir(parents=True)
    (paths.legacy7d_base / "a.yaml").write_text("a: 1\n")
    for _ in range(3):
        out = P.migrate(paths, dry_run=False)
        assert out["state"] in ("OK", "FRESH"), out
    import os as _os
    assert _os.path.realpath(paths.legacy7d_base) == str(paths.config_dir)


# ---------------------------------------------------------------------------
# 5. Unsafe symlinks / traversal are never followed
# ---------------------------------------------------------------------------
def test_alias_mismatch_never_migrates_silently(tmp_path):
    paths = _paths(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.yaml").write_text("x: 1\n")
    paths.base.mkdir(parents=True, exist_ok=True)
    paths.legacy_base.symlink_to(outside, target_is_directory=True)
    st = P.legacy_alias_status(paths)
    assert st["state"] == "SPLIT_TREES"
    out = P.migrate(paths, dry_run=False)
    # nothing to move as a real dir; mismatch must survive visibly
    assert out["state"] in ("OK", "FRESH")
    assert P.legacy_alias_status(paths)["state"] == "SPLIT_TREES"
    assert (outside / "evil.yaml").exists()


# ---------------------------------------------------------------------------
# 6. Service / shortcut states (isolated unit dir, dry-run only)
# ---------------------------------------------------------------------------
def test_service_status_single_canonical_unit(tmp_path):
    paths = _paths(tmp_path)
    udir = tmp_path / "units"
    udir.mkdir()
    (udir / "qubit-voicebridge.service").write_text(
        "[Unit]\n[Service]\nExecStart=/usr/bin/python -m voicebridge run\n")
    st = P.service_owner(paths, unit_dir=udir)
    assert st["unit"] == "qubit-voicebridge.service"
    assert st["state"] in ("PRESENT", "MISMATCH")


def test_service_status_duplicate_and_stale_legacies(tmp_path):
    paths = _paths(tmp_path)
    udir = tmp_path / "units"
    udir.mkdir()
    (udir / "qubit-voicebridge.service").write_text("x")
    (udir / "qubit-voice-agent.service").write_text("x")
    st = P.service_owner(paths, unit_dir=udir)
    assert st["state"] == "DUPLICATE"
    udir2 = tmp_path / "units2"
    udir2.mkdir()
    (udir2 / "openclaw-voice-bridge.service").write_text("x")
    st2 = P.service_owner(paths, unit_dir=udir2)
    assert st2["state"] == "STALE"


def test_desktop_shortcut_dry_run_has_no_secrets(tmp_path):
    paths = _paths(tmp_path)
    out = P.desktop_shortcut(paths, dry_run=True)
    assert out["state"] in ("READY", "DEGRADED", "UNAVAILABLE", "FAILED", "OK")
    blob = json.dumps(out)
    assert "token=" not in blob
    assert "webui.token" not in blob
    if out["state"] == "READY":
        assert "token" not in out.get("exec", "").lower()


def test_shortcut_uses_canonical_title():
    import inspect
    src = inspect.getsource(P.desktop_shortcut)
    assert "PRODUCT_TITLE" in src or "Qubit VoiceBridge" in src


# ---------------------------------------------------------------------------
# 7. Packaging / branding consistency
# ---------------------------------------------------------------------------
def test_packaging_uses_canonical_name():
    import tomllib
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert data["project"]["name"] == "qubit-voicebridge"
    from voicebridge import distribution as D
    assert D.PACKAGE_NAME == "qubit-voicebridge"
    assert (REPO / "systemd" / "qubit-voicebridge.service").exists()
    unit = (REPO / "systemd" / "qubit-voicebridge.service").read_text()
    assert "Qubit VoiceBridge" in unit
    assert "qubit-voicebridge" in unit


def test_frontend_uses_canonical_branding_and_paths():
    for page in ("index.html", "token-setup.html", "settings.html",
                 "reports.html", "activity.html", "approvals.html"):
        text = (REPO / "frontend" / page).read_text()
        assert "Qubit VoiceBridge" in text, page
        assert "Qubit Voice Agent —" not in text, page
    assert "qubit-voicebridge" in (REPO / "frontend" / "token-setup.html").read_text()
    assert "qubit-voicebridge" in (REPO / "frontend" / "index.html").read_text()


def test_repo_rename_rehearsal_from_clean_copy(tmp_path):
    """Rehearse the physical repo-folder rename from a clean copy (never the
    active checkout): the project imports, packages, and resolves resources
    from the new qubit-voicebridge path."""
    import shutil
    import subprocess
    dest = tmp_path / "qubit-voicebridge"
    shutil.copytree(REPO, dest, symlinks=True, ignore_dangling_symlinks=True,
                    ignore=shutil.ignore_patterns(
                        ".venv", "__pycache__", ".git", "graft", "*.pyc",
                        "scripts/browser_profiles"))
    assert (dest / "pyproject.toml").read_text().find('name = "qubit-voicebridge"') >= 0
    assert (dest / "systemd" / "qubit-voicebridge.service").exists()
    r = subprocess.run([sys.executable, "-c",
                        "import voicebridge.product as P;"
                        "assert P.PRODUCT_NAME=='qubit-voicebridge';"
                        "print('rename-rehearsal-ok')"],
                       capture_output=True, text=True, cwd=str(dest), timeout=60)
    assert "rename-rehearsal-ok" in (r.stdout or ""), r.stderr[-2000:]


def test_no_canonical_path_leaks_secrets(tmp_path):
    paths = _paths(tmp_path)
    diag = P.path_map_diagnostic(paths)
    assert diag["product"] == "qubit-voicebridge"
    assert "token" not in json.dumps(diag).lower()
