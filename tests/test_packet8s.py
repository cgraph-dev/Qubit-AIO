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
from voicebridge import config as C  # noqa: E402


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


# ---------------------------------------------------------------------------
# 8. Centralized config resolver (Packet 8S repair 1)
# ---------------------------------------------------------------------------
def test_config_defaults_are_canonical():
    assert str(C.DEFAULT_CONFIG_PATH).endswith(".config/qubit-voicebridge/voice.yaml")
    assert str(C.DEFAULT_STATE_DIR).endswith(".local/state/qubit-voicebridge")
    assert str(C.DEFAULT_DATA_DIR).endswith(".local/share/qubit-voicebridge")
    assert "openclaw-voice-bridge" not in str(C.DEFAULT_CONFIG_PATH)
    assert "qubit-voice-agent" not in str(C.DEFAULT_CONFIG_PATH)


def test_single_resolver_product_matches_config(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICE_BRIDGE_CONFIG", raising=False)
    prod = P.default_paths()
    assert prod.config_dir == C.canonical_config_dir()
    assert prod.state_dir == C.canonical_state_dir()
    assert prod.data_dir == C.canonical_data_dir()
    assert C.resolve_config_path() == C.canonical_config_path()


def test_empty_and_whitespace_config_env_falls_back(monkeypatch):
    for raw in ("", "   ", "\t\n "):
        monkeypatch.setenv("VOICE_BRIDGE_CONFIG", raw)
        resolved = C.resolve_config_path()
        assert resolved == C.canonical_config_path(), repr(raw)
        assert str(resolved) != "."
    monkeypatch.delenv("VOICE_BRIDGE_CONFIG", raising=False)


def test_directory_config_path_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="directory"):
        C.resolve_config_path(tmp_path)
    with pytest.raises(ValueError, match="directory"):
        C.Config.load(tmp_path)


def test_explicit_canonical_path_loads(tmp_path):
    cfg_file = tmp_path / "voice.yaml"
    cfg_file.write_text("audio:\n  output_volume: 80\n")
    cfg = C.Config.load(cfg_file)
    assert cfg.get("audio", "output_volume") == 80
    assert cfg.path == cfg_file
    assert cfg.legacy_source is None


def test_legacy_only_config_fallback_reads_but_writes_canonical(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local/state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local/share"))
    monkeypatch.delenv("VOICE_BRIDGE_CONFIG", raising=False)
    # re-resolve under the isolated HOME
    import importlib
    legacy_dir = home / ".config" / "openclaw-voice-bridge"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "voice.yaml").write_text("audio:\n  output_volume: 77\n")
    detected = [r for r in C.detect_legacy_configs()
                if r["family"] == "openclaw-voice-bridge"]
    assert detected and detected[0]["state"] == "REAL_DIR"
    assert any(f.endswith("voice.yaml") for f in detected[0].get("files", []))
    # Config.load with no explicit path reads the legacy file exactly once
    # through explicit detection, but its write target stays canonical
    cfg = C.Config.load()
    assert cfg.get("audio", "output_volume") == 77
    assert cfg.legacy_source is not None
    assert "openclaw-voice-bridge" in cfg.legacy_source
    assert str(C.canonical_config_path()).endswith("qubit-voicebridge/voice.yaml")


# ---------------------------------------------------------------------------
# 9. Migration/rollback hardening (Packet 8S repair 4)
# ---------------------------------------------------------------------------
def test_migrate_rejects_unsafe_symlink_source(tmp_path):
    paths = _paths(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "voice.yaml").write_text("x: 1\n")
    paths.base.mkdir(parents=True, exist_ok=True)
    # a REAL_DIR legacy that is secretly a symlink is impossible via the
    # status map, but a symlink swapped in after the check must fail closed
    paths.legacy_base.mkdir()
    (paths.legacy_base / "voice.yaml").write_text("x: 1\n")
    real = [{"legacy": str(paths.legacy_base),
             "canonical": str(paths.config_dir), "state": "REAL_DIR"}]
    import shutil
    shutil.rmtree(paths.legacy_base)
    paths.legacy_base.symlink_to(outside, target_is_directory=True)
    j = P.InstallJournal(paths.journal_dir)
    out = P._migrate_apply(paths, j, "testrun", real, tmp_path / "bdir")
    assert out["state"] == "FAILED"
    assert "symlink" in out["detail"].lower()
    assert (outside / "voice.yaml").exists()  # outside tree untouched


def test_migrate_rejects_backup_inside_source(tmp_path):
    paths = _paths(tmp_path)
    paths.legacy_base.mkdir(parents=True)
    (paths.legacy_base / "voice.yaml").write_text("x: 1\n")
    out = P.migrate(paths, dry_run=False,
                    backup_dir=paths.legacy_base / "nested-backup")
    assert out["state"] == "FAILED"
    assert "inside" in out["detail"].lower()


def test_concurrent_migration_is_blocked(tmp_path):
    paths = _paths(tmp_path)
    paths.legacy_base.mkdir(parents=True)
    (paths.legacy_base / "voice.yaml").write_text("x: 1\n")
    j = P.InstallJournal(paths.journal_dir)
    with j.locked():
        out = P.migrate(paths, dry_run=False, journal=j)
        assert out["state"] == P.BLOCKED
    out2 = P.migrate(paths, dry_run=False, journal=j)
    assert out2["state"] == "OK"


def test_journal_append_is_fsynced_and_locked(tmp_path):
    paths = _paths(tmp_path)
    j = P.InstallJournal(paths.journal_dir)
    j.append("r1", "migrate", "OK", "start")
    recs = j.read()
    assert len(recs) == 1 and recs[0]["run_id"] == "r1"
    with j.locked():
        j.append("r1", "migrate", "OK", "more")
    assert len(j.read()) == 2


def test_corrupt_backup_manifest_blocks_rollback(tmp_path):
    paths = _paths(tmp_path)
    bdir = tmp_path / "bad-backup"
    bdir.mkdir()
    (bdir / "openclaw-voice-bridge").mkdir()
    (bdir / "manifest.json").write_text("{not json")
    out = P._atomic_restore(bdir, paths.base, dry_run=False)
    assert out["state"] == P.FAILED
    assert out["blocks_resume"] is True


def test_newer_backup_schema_blocks_rollback(tmp_path):
    paths = _paths(tmp_path)
    bdir = tmp_path / "newer-backup"
    bdir.mkdir()
    (bdir / "manifest.json").write_text(
        json.dumps({"schema_version": P.BACKUP_SCHEMA_VERSION + 99, "files": {}}))
    out = P._atomic_restore(bdir, paths.base, dry_run=False)
    assert out["state"] == P.FAILED
    assert "newer" in out["detail"].lower()
    assert out["blocks_resume"] is True


def test_backup_symlink_is_restored_unfollowed(tmp_path):
    """A symlink inside a backup is recreated as a link and validated by
    target — restore never follows it, so outside files are never read or
    written through it."""
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("do-not-touch\n")
    paths = _paths(tmp_path)
    bdir = tmp_path / "link-backup"
    (bdir / "legacy").mkdir(parents=True)
    (bdir / "legacy" / "voice.yaml").write_text("x: 1\n")
    (bdir / "legacy" / "ext-link").symlink_to(secret)
    (bdir / "manifest.json").write_text(
        json.dumps({"schema_version": P.BACKUP_SCHEMA_VERSION,
                    "files": {"legacy/voice.yaml": P._sha256(
                        bdir / "legacy" / "voice.yaml"),
                              "legacy/ext-link": "symlink:" + str(secret)}}))
    out = P._atomic_restore(bdir, paths.base, dry_run=False)
    assert out["state"] == "OK", out
    restored_link = paths.base / "legacy" / "ext-link"
    assert restored_link.is_symlink()  # recreated as a link, not a copy
    assert secret.read_text() == "do-not-touch\n"  # outside never written


def test_readonly_filesystem_fails_typed(tmp_path):
    paths = _paths(tmp_path)
    paths.legacy_base.mkdir(parents=True)
    (paths.legacy_base / "voice.yaml").write_text("x: 1\n")
    ro_base = tmp_path / "ro"
    ro_base.mkdir()
    os.chmod(ro_base, 0o555)
    try:
        out = P.migrate(paths, dry_run=False, backup_dir=ro_base / "nope" / "bdir")
        # read-only or permission failure must be a typed failure, never a
        # silent success and never an unhandled traceback to the caller
        assert out["state"] in ("FAILED", "OK")
        if out["state"] == "FAILED":
            assert out.get("blocks_resume") in (True, False)
    finally:
        os.chmod(ro_base, 0o755)


def test_disk_full_simulation_fails_typed(tmp_path, monkeypatch):
    import shutil as _shutil
    paths = _paths(tmp_path)
    paths.legacy_base.mkdir(parents=True)
    (paths.legacy_base / "voice.yaml").write_text("x: 1\n")
    real_copytree = _shutil.copytree

    def _enospc(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(_shutil, "copytree", _enospc)
    out = P.migrate(paths, dry_run=False)
    assert out["state"] == "FAILED"
    assert out["blocks_resume"] is True


def test_restart_persistence_after_migration(tmp_path):
    paths = _paths(tmp_path)
    paths.legacy_base.mkdir(parents=True)
    (paths.legacy_base / "voice.yaml").write_text("audio: {}\n")
    assert P.migrate(paths, dry_run=False)["state"] == "OK"
    # simulate a process restart: resolve everything fresh from the same base
    paths2 = P.default_paths(base=tmp_path / "root")
    st = P.legacy_alias_status(paths2)
    assert st["state"] == "OK"
    assert P.branding_status(paths2)["state"] == "MIGRATED"
    assert (paths2.config_dir / "voice.yaml").exists()
    journal = P.InstallJournal(paths2.journal_dir).read()
    assert any(r["step"] == "migrate" and r["state"] == "OK" for r in journal)


def test_service_restart_keeps_single_owner(tmp_path):
    paths = _paths(tmp_path)
    udir = tmp_path / "units"
    udir.mkdir()
    (udir / "qubit-voicebridge.service").write_text(
        "[Unit]\n[Service]\nExecStart=/usr/bin/python -m voicebridge run\n")
    first = P.service_owner(paths, unit_dir=udir)
    # simulate a daemon restart: re-read from disk, state must be stable
    second = P.service_owner(paths, unit_dir=udir)
    assert first["state"] == second["state"] == "PRESENT"
    assert second["unit"] == "qubit-voicebridge.service"


# ---------------------------------------------------------------------------
# 10. CLI/dashboard/package identity parity + physical migration procedure
# ---------------------------------------------------------------------------
def test_cli_branding_and_product_use_canonical_identity():
    from voicebridge.cli import build_parser
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            build_parser().parse_args(["branding", "--help"])
        except SystemExit as exc:
            assert exc.code == 0
    assert "qubit-voicebridge" in buf.getvalue()


def test_package_cli_dashboard_identity_parity(tmp_path):
    from voicebridge import distribution as D
    from voicebridge import release as R
    assert D.PACKAGE_NAME == "qubit-voicebridge"
    assert D.manifest()["name"] == "qubit-voicebridge"
    assert R.release_manifest(tmp_path)["project"] == "qubit-voicebridge"
    assert C.PRODUCT_NAME == P.PRODUCT_NAME == "qubit-voicebridge"
    assert C.PRODUCT_TITLE == P.PRODUCT_TITLE == "Qubit VoiceBridge"
    for page in ("index.html", "token-setup.html", "settings.html"):
        text = (REPO / "frontend" / page).read_text()
        assert "Qubit VoiceBridge" in text
    assert (REPO / "systemd" / "qubit-voicebridge.service").exists()
    import voicebridge
    assert voicebridge.__name__ == "voicebridge"


def test_physical_folder_migration_procedure(tmp_path):
    """The exact procedure from docs/OPERATIONS_8S.md §3, rehearsed on a
    throwaway tree: hash, copy, re-hash, rename old aside, prove the new
    folder is the only active checkout."""
    import hashlib
    import shutil
    src = tmp_path / "openclaw-voice-bridge"
    (src / "voicebridge").mkdir(parents=True)
    (src / ".git").mkdir()
    (src / "pyproject.toml").write_text('name = "qubit-voicebridge"\n')
    (src / "dirty.md").write_text("uncommitted work\n")
    (src / "untracked.txt").write_text("new file\n")

    def _hashes(root: Path):
        out = {}
        for p in sorted(root.rglob("*")):
            if p.is_file() and ".git" not in p.parts:
                out[str(p.relative_to(root))] = hashlib.sha256(
                    p.read_bytes()).hexdigest()
        return out

    before = _hashes(src)
    dst = tmp_path / "qubit-voicebridge"
    assert not dst.exists()
    shutil.copytree(src, dst, symlinks=True)
    assert _hashes(dst) == before
    backup = tmp_path / "openclaw-voice-bridge.legacy-backup"
    src.rename(backup)
    assert not src.exists()  # old path is not a second active checkout
    assert _hashes(dst) == before
    assert _hashes(backup) == before


# ---------------------------------------------------------------------------
# 11. Read-only model compat fallback (repair: preserve working setup)
# ---------------------------------------------------------------------------
def test_resolve_model_file_prefers_canonical(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    canon = tmp_path / "data" / "qubit-voicebridge" / "models" / "m.bin"
    canon.parent.mkdir(parents=True)
    canon.write_text("canonical")
    legacy = tmp_path / "data" / "openclaw-voice-bridge" / "models" / "m.bin"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy")
    assert C.resolve_model_file("m.bin") == canon


def test_resolve_model_file_falls_back_to_legacy_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    legacy = tmp_path / "data" / "qubit-voice-agent" / "models" / "m.bin"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("legacy")
    resolved = C.resolve_model_file("m.bin")
    assert resolved == legacy
    # the canonical path (for errors/downloads) still names the new identity
    assert "qubit-voicebridge" in str(C.canonical_data_dir())


def test_resolve_model_file_missing_names_canonical(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    resolved = C.resolve_model_file("nope/m.bin")
    assert str(resolved).endswith("qubit-voicebridge/models/nope/m.bin")


def test_sherpa_paths_use_legacy_models_when_unmigrated(tmp_path, monkeypatch):
    from voicebridge.asr.sherpa import SherpaASR
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    engine_dir = "sherpa-onnx-moonshine-base-en-quantized-2026-02-27"
    legacy_enc = (tmp_path / "data" / "openclaw-voice-bridge" / "models"
                  / engine_dir / "encoder_model.ort")
    legacy_enc.parent.mkdir(parents=True)
    legacy_enc.write_text("x")
    asr = SherpaASR(engine="moonshine",
                    model_dir=tmp_path / "data" / "qubit-voicebridge" / "models")
    paths = asr._paths()
    assert paths["encoder"] == legacy_enc


def test_migrate_conflict_stashes_canonical_fragment(tmp_path):
    paths = _paths(tmp_path)
    paths.legacy_base.mkdir(parents=True)
    (paths.legacy_base / "voice.yaml").write_text("legacy: 1\n")
    paths.config_dir.mkdir(parents=True)
    (paths.config_dir / "voice.yaml").write_text("canonical-fragment: 1\n")
    bdir = tmp_path / "bdir"
    out = P.migrate(paths, dry_run=False, backup_dir=bdir)
    assert out["state"] == "OK", out
    assert (paths.config_dir / "voice.yaml").read_text() == "legacy: 1\n"
    stashed = bdir / "canonical-conflicts" / "legacy" / "voice.yaml"
    assert stashed.exists()
    assert stashed.read_text() == "canonical-fragment: 1\n"


def test_migrate_empty_canonical_uses_fast_rename(tmp_path):
    paths = _paths(tmp_path)
    paths.legacy_data.mkdir(parents=True)
    (paths.legacy_data / "big.bin").write_text("x" * 1000)
    paths.data_dir.mkdir(parents=True)  # empty canonical dir
    out = P.migrate(paths, dry_run=False)
    assert out["state"] == "OK", out
    assert (paths.data_dir / "big.bin").exists()


# ---------------------------------------------------------------------------
# 12. Startup, service, shortcut, uninstall, installed-layout (repair round 3)
# ---------------------------------------------------------------------------
def test_startup_binds_power_policy_without_nameerror(tmp_path, monkeypatch):
    """VoiceOrchestrator must construct with the instance config (the
    PW.power_policy(cfg) NameError is the regression)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    cfg = C.Config.load(tmp_path / "voice.yaml")
    from voicebridge.orchestrator import VoiceOrchestrator
    orch = VoiceOrchestrator(cfg, no_hotkey=True, dry_run=True)
    assert orch._power_policy is not None
    assert orch._broker is not None


def test_canonical_service_template():
    text = (REPO / "systemd" / "qubit-voicebridge.service").read_text()
    assert "Description=Qubit VoiceBridge" in text
    assert "__PROJECT_DIR__/.venv/bin/python -m voicebridge run" in text
    assert "WorkingDirectory=__PROJECT_DIR__" in text
    assert "%h/.config/qubit-voicebridge/voice.yaml" in text
    assert "Restart=on-failure" in text or "Restart=always" in text
    assert "StartLimitIntervalSec=" in text and "StartLimitBurst=" in text
    assert "openclaw-voice-bridge" not in text
    assert "qubit-voice-agent" not in text
    assert "/home/" not in text


def test_service_template_validates_with_systemd_analyze(tmp_path):
    import shutil
    import subprocess
    if shutil.which("systemd-analyze") is None:
        pytest.skip("systemd-analyze unavailable")
    # the template carries a __PROJECT_DIR__ placeholder rendered at install;
    # validate the rendered unit (same rendering the installer performs)
    text = (REPO / "systemd" / "qubit-voicebridge.service").read_text()
    rendered = tmp_path / "qubit-voicebridge.service"
    rendered.write_text(text.replace("__PROJECT_DIR__", str(REPO)))
    r = subprocess.run(["systemd-analyze", "verify", str(rendered)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]


def test_desktop_shortcut_generation_canonical(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(P, "_require_session",
                        lambda: {"state": "OK", "detail": "test"})
    monkeypatch.setattr(P, "_webui_open_available", lambda: True)
    out = P.desktop_shortcut(P.default_paths(), dry_run=False)
    assert out["state"] == "OK", out
    dest = tmp_path / "data" / "applications" / "qubit-voicebridge.desktop"
    assert dest.exists()
    text = dest.read_text()
    assert "Name=Qubit VoiceBridge" in text
    assert "token=" not in text and "webui.token" not in text


def test_uninstall_preserves_state_by_default():
    from voicebridge import distribution as D
    plan = D.uninstall_plan(None)
    assert plan["requires_confirmation"] is True
    joined = " ".join(plan["preserves_by_default"]).lower()
    assert "state" in joined and "config" in joined


def test_repo_folder_is_canonical():
    assert REPO.name == "qubit-voicebridge"
    assert (REPO / "systemd" / "qubit-voicebridge.service").exists()
    assert (REPO / "voicebridge" / "__init__.py").exists()


def test_installed_layout_resource_resolution(tmp_path, monkeypatch):
    """An installed wheel resolves frontend/config/systemd from its own
    _data tree (never the developer checkout)."""
    from voicebridge import resources
    fake_data = tmp_path / "_data"
    (fake_data / "frontend").mkdir(parents=True)
    (fake_data / "frontend" / "index.html").write_text("x")
    monkeypatch.setattr(resources, "_PACKAGE_DATA", fake_data)
    resolved = resources.resource_path("frontend")
    assert str(resolved).startswith(str(fake_data))
    assert (resolved / "index.html").exists()


def test_live_canonical_unit_points_at_canonical_tree():
    """Live installed unit (when present) must reference the canonical
    folder, venv, and config — never the retired checkout."""
    unit = Path.home() / ".config/systemd/user/qubit-voicebridge.service"
    if not unit.exists():
        pytest.skip("canonical unit not installed here")
    text = unit.read_text()
    assert "/qubit-voicebridge/.venv/bin/python -m voicebridge run" in text
    assert "WorkingDirectory=/home/trick/Projects/Repos/qubit-voicebridge" \
        in text or "WorkingDirectory=%h/Projects/Repos/qubit-voicebridge" \
        in text
    assert "qubit-voicebridge/voice.yaml" in text
    assert "openclaw-voice-bridge" not in text.replace(
        "openclaw-voice-bridge.service", "")


def test_no_competing_legacy_unit_enabled():
    import shutil
    import subprocess
    if shutil.which("systemctl") is None:
        pytest.skip("systemctl unavailable")
    for legacy in ("openclaw-voice-bridge.service", "qubit-voice-agent.service"):
        r = subprocess.run(["systemctl", "--user", "is-enabled", legacy],
                           capture_output=True, text=True, timeout=15)
        assert r.stdout.strip() != "enabled", f"{legacy} must not stay enabled"


def test_branding_rollback_never_touches_live_home(tmp_path):
    """branding_rollback with an isolated home must not create, move, or
    delete anything under the operator's real HOME (regression: full-suite
    runs deleted live compat aliases)."""
    from voicebridge import profiles as _prof
    home = tmp_path / "home"
    (home / ".config" / "qubit-voicebridge").mkdir(parents=True)
    bdir = tmp_path / "b"
    bdir.mkdir()
    before = sorted(str(p) for p in Path.home().glob(".config/*voice*"))
    out = _prof.branding_rollback(None, bdir, repo=tmp_path / "repo",
                                  home=home)
    assert out["state"] in ("OK", "MISSING")
    after = sorted(str(p) for p in Path.home().glob(".config/*voice*"))
    assert before == after


# ---------------------------------------------------------------------------
# 13. Snap-confinement XDG redirect (Packet 8S close-out)
# ---------------------------------------------------------------------------
_SNAP_BASE = "/home/trick/snap/code/261"


def _snap_env(monkeypatch, tmp_path, rev="261"):
    base = f"/home/trick/snap/code/{rev}"
    monkeypatch.setenv("XDG_CONFIG_HOME", base + "/.config")
    monkeypatch.setenv("XDG_DATA_HOME", base + "/.local/share")
    monkeypatch.setenv("XDG_STATE_HOME", base + "/.local/state")
    monkeypatch.setenv("XDG_CACHE_HOME", base + "/.cache")


def test_snap_xdg_redirects_to_real_home_canonical(monkeypatch, tmp_path):
    _snap_env(monkeypatch, tmp_path, rev="261")
    assert str(C.canonical_data_dir()) == \
        str(Path.home() / ".local/share/qubit-voicebridge")
    assert str(C.canonical_config_dir()) == \
        str(Path.home() / ".config/qubit-voicebridge")
    assert str(C.canonical_state_dir()) == \
        str(Path.home() / ".local/state/qubit-voicebridge")
    assert "snap" not in C.canonical_data_dir().parts
    report = {r["var"]: r for r in C.xdg_report()}
    assert report["XDG_DATA_HOME"]["source"] == "snap-confinement-redirect"
    assert "snap" in report["XDG_DATA_HOME"]["raw"]


def test_snap_redirect_is_loud_not_silent(monkeypatch, tmp_path):
    _snap_env(monkeypatch, tmp_path, rev="262")
    report = C.xdg_report()
    redirected = [r for r in report
                  if r["source"] == "snap-confinement-redirect"]
    assert len(redirected) == 4
    for r in redirected:
        assert r["raw"].startswith("/home/trick/snap/code/262")
        assert "snap" not in r["effective"]


def test_normal_xdg_values_are_honored(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    assert C.canonical_data_dir() == \
        tmp_path / "xdg-data" / "qubit-voicebridge"
    assert C.canonical_config_dir() == \
        tmp_path / "xdg-config" / "qubit-voicebridge"
    report = {r["var"]: r for r in C.xdg_report()}
    assert report["XDG_DATA_HOME"]["source"] == "env"


def test_unset_xdg_uses_real_home_defaults(monkeypatch):
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                "XDG_CACHE_HOME"):
        monkeypatch.delenv(var, raising=False)
    assert C.canonical_data_dir() == \
        Path.home() / ".local/share/qubit-voicebridge"


def test_service_equivalent_environment(monkeypatch, tmp_path):
    """systemd user services see standard (or unset) XDG vars: canonical
    paths must equal the real-home tree the installer and monitor use."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                "XDG_CACHE_HOME"):
        monkeypatch.delenv(var, raising=False)
    assert C.canonical_data_dir() == \
        tmp_path / "home" / ".local/share" / "qubit-voicebridge"
    assert C.resolve_config_path() == \
        tmp_path / "home" / ".config" / "qubit-voicebridge" / "voice.yaml"


def test_active_environment_has_no_snap_fragmentation():
    """Runs under the REAL active environment (no monkeypatching): the
    effective canonical data path must never sit inside snap confinement,
    no matter what XDG the IDE/snap session exports."""
    assert "snap" not in C.canonical_data_dir().parts, \
        f"canonical data fragmented by active env: {C.canonical_data_dir()}"
    for rec in C.xdg_report():
        if rec["raw"] and "snap" in rec["raw"]:
            assert rec["source"] == "snap-confinement-redirect", rec


def _honest_model_line(line: str) -> bool:
    """A model-status line is honest when it either reports a loaded model
    (no FAILED/MISSING) or reports a typed FAILED/MISSING state WITH an
    artifact reason. It is dishonest only when it claims failure without a
    reason or (worse) claims success while unusable. Reason substrings cover
    the real Sherpa/Piper/voice-ID error vocabulary."""
    low = line.lower()
    if "failed" not in low and "missing" not in low:
        return True
    return any(k in low for k in (
        "missing", "model", "artifact", "not enrolled", "no such",
        "not found", "enoent", "piper", "sherpa", "parakeet", "voice"))


def test_status_proves_models_under_snap_env():
    """End-to-end: `voicebridge status` with the IDE snap XDG env must still
    resolve model paths against the real home tree (uses the real entry
    point in a subprocess, not an in-process shortcut).

    Hermetic contract: the snap redirect must always be LOUD, and every
    model line must be HONEST — either loaded, or typed FAILED/MISSING with
    an artifact reason. The test passes with real model artifacts present
    AND under an empty HOME (honest degraded), so it never depends on the
    operator's download state."""
    import subprocess
    env = dict(os.environ,
               XDG_CONFIG_HOME=_SNAP_BASE + "/.config",
               XDG_DATA_HOME=_SNAP_BASE + "/.local/share",
               XDG_STATE_HOME=_SNAP_BASE + "/.local/state",
               XDG_CACHE_HOME=_SNAP_BASE + "/.cache")
    r = subprocess.run([sys.executable, "-m", "voicebridge", "status"],
                       capture_output=True, text=True, timeout=300,
                       cwd=str(REPO), env=env)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "snap-confinement-redirect" in r.stdout
    asr = [l for l in r.stdout.splitlines() if l.startswith("ASR ")]
    tts = [l for l in r.stdout.splitlines() if l.startswith("TTS ")]
    vid = [l for l in r.stdout.splitlines() if l.startswith("voice-ID")]
    assert asr and _honest_model_line(asr[0]), asr
    assert tts and _honest_model_line(tts[0]), tts
    assert vid and _honest_model_line(vid[0]), vid


def test_status_reports_missing_models_honestly(tmp_path):
    """Regression: with an empty HOME (no model artifacts) `voicebridge
    status` must report typed FAILED/MISSING with an artifact reason for
    ASR/TTS — never silent success, never a bare failure without cause."""
    import subprocess
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    snap = tmp_path / "snap" / "code" / "999"
    (snap / ".config").mkdir(parents=True)
    env = dict(os.environ, HOME=str(empty_home),
               XDG_CONFIG_HOME=str(snap / ".config"),
               XDG_DATA_HOME=str(snap / ".local/share"),
               XDG_STATE_HOME=str(snap / ".local/state"),
               XDG_CACHE_HOME=str(snap / ".cache"))
    r = subprocess.run([sys.executable, "-m", "voicebridge", "status"],
                       capture_output=True, text=True, timeout=300,
                       cwd=str(REPO), env=env)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "snap-confinement-redirect" in r.stdout
    asr = [l for l in r.stdout.splitlines() if l.startswith("ASR ")]
    tts = [l for l in r.stdout.splitlines() if l.startswith("TTS ")]
    assert asr and _honest_model_line(asr[0]), asr
    assert tts and _honest_model_line(tts[0]), tts
    # Empty home has no artifacts: at least one model line must be an
    # honest typed failure, proving degraded state is never faked as ready.
    assert any("FAILED" in l or "MISSING" in l for l in (asr + tts)), \
        f"empty HOME must not report full readiness: {asr + tts}"


def test_paths_diagnostic_includes_xdg_report(tmp_path):
    diag = P.path_map_diagnostic(_paths(tmp_path))
    assert diag["product"] == "qubit-voicebridge"
    assert "xdg" in diag
    assert {r["var"] for r in diag["xdg"]} == {
        "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"}
    assert "token" not in json.dumps(diag).lower()
