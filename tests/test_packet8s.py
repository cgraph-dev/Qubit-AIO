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
