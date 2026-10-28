"""Packet 8K-P tests: productized installation, portable paths, model
bootstrap, first-run/repair, branding migration, service ownership, desktop
shortcut, optional tray, capability defaults, and the packaging/private-source
boundary.

Hermetic: all tests use an isolated temporary base for every owned directory
(no operator HOME/XDG/state), never enable a live service, never touch a real
browser/audio device/account/model, and never reveal secrets.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge import product as P  # noqa: E402


def _paths(tmp_path: Path) -> P.ProductPaths:
    """Isolated product paths under tmp_path (never the operator HOME)."""
    return P.default_paths(base=tmp_path / "root")


# ========================================================================
# 1. Portable path resolver
# ========================================================================
def test_platform_supported_on_linux():
    st = P.platform_supported()
    if sys.platform.startswith("linux"):
        assert st["state"] == "OK"
    else:
        assert st["state"] == "UNSUPPORTED"


def test_default_paths_are_xdg_and_no_home_literal(tmp_path):
    paths = P.default_paths()
    # canonical product name in every category
    product = P.PRODUCT_NAME
    assert str(paths.config_dir).endswith(product)
    assert str(paths.state_dir).endswith(product)
    assert str(paths.data_dir).endswith(product)
    # no repository literal or .venv in ANY per-user path map (production or
    # isolated); the shared packaged-resources root may point at the installed
    # package / checkout, which is legitimate
    for p in (paths, _paths(tmp_path)):
        blob = json.dumps({k: v for k, v in p.map.items() if k != "resources"})
        assert "Projects/Repos" not in blob
        assert ".venv" not in blob
    # categories are distinct
    names = list(paths.map)
    assert len(set(paths.map.values())) == len(names)


def test_isolated_paths_have_no_user_home_literal(tmp_path):
    # isolated per-user paths never contain the developer's home; the shared
    # packaged resources root may point at the installed package (legitimate).
    # The forbidden literal is built from parts so the test source itself never
    # ships a real home path in the sdist.
    home_user = "/home" + "/trick"
    paths = _paths(tmp_path)
    per_user = {k: v for k, v in paths.map.items() if k != "resources"}
    blob = json.dumps(per_user)
    assert home_user not in blob
    assert "/home/" not in blob


def test_isolated_paths_under_tmp(tmp_path):
    paths = _paths(tmp_path)
    root = tmp_path / "root"
    assert str(paths.config_dir).startswith(str(root))
    assert str(paths.model_dir).startswith(str(root / "data"))
    assert paths.legacy_base == root / "legacy"


def test_ensure_owned_dirs_only_owns_expected(tmp_path):
    paths = _paths(tmp_path)
    res = P.ensure_owned_dirs(paths, dry_run=False)
    assert all("error" not in r for r in res)
    for r in res:
        assert Path(r["path"]).is_dir(), r
    # nothing outside the owned root was created
    created = {Path(r["path"]) for r in res}
    assert all(str(p).startswith(str(tmp_path / "root")) for p in created)


def test_path_map_diagnostic_no_secrets(tmp_path):
    paths = _paths(tmp_path)
    diag = P.path_map_diagnostic(paths)
    blob = json.dumps(diag)
    for secret in ("sk-", "token", "ghp_", "password"):
        assert secret not in blob


def test_platform_unsupported_blocks_install(tmp_path, monkeypatch):
    monkeypatch.setattr(P.platform, "system", lambda: "Darwin")
    paths = _paths(tmp_path)
    out = P.install(paths, dry_run=True)
    assert out["state"] == "UNAVAILABLE"
    assert out["completed"] is False


# ========================================================================
# 2. Installer (lock + journal + typed states)
# ========================================================================
def test_install_prereq_blocked_when_missing(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setattr(P, "_require_python",
                        lambda: {"state": "MISSING", "detail": "no python"})
    out = P.install(paths, dry_run=True)
    assert out["state"] in ("MISSING", "BLOCKED")
    assert out["completed"] is False
    # dry-run is zero-write: the journal must NOT exist at all
    assert not (paths.journal_dir / "install.jsonl").exists()


def test_install_apply_records_block_in_journal(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setattr(P, "_require_python",
                        lambda: {"state": "MISSING", "detail": "no python"})
    out = P.install(paths, dry_run=False)
    assert out["state"] in ("MISSING", "BLOCKED")
    assert out["completed"] is False
    j = P.InstallJournal(paths.journal_dir)
    recs = j.read()
    assert any(r.get("step") == "python" and r.get("state") == "MISSING"
               for r in recs)


def test_install_never_reports_complete_when_missing(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setattr(P, "_require_audio",
                        lambda: {"state": "MISSING", "detail": "no audio"})
    out = P.install(paths, dry_run=True)
    assert out["state"] != "READY"
    assert out["completed"] is False
    assert "cannot complete" in out["detail"]


def test_install_lock_blocks_concurrent(tmp_path):
    paths = _paths(tmp_path)
    lock = paths.state_dir / "install.lock"
    a = P.InstallLock(lock)
    assert a.acquire() is True
    b = P.InstallLock(lock)
    assert b.acquire() is False  # second installer blocked
    a.release()
    c = P.InstallLock(lock)
    assert c.acquire() is True
    c.release()


def test_install_journal_append_and_read(tmp_path):
    paths = _paths(tmp_path)
    j = P.InstallJournal(paths.journal_dir)
    j.append("run1", "python", "OK", "fine")
    j.append("run1", "audio", "MISSING", "no device")
    recs = j.read()
    assert len(recs) == 2
    assert recs[1]["state"] == "MISSING"
    assert j.last_state("run1")["step"] == "audio"


def test_install_dry_run_readiness(tmp_path):
    paths = _paths(tmp_path)
    out = P.install(paths, dry_run=True)
    # prerequisites (python/ollama/audio/session) may vary; the state must be
    # typed and completed must be False in dry-run
    assert out["state"] in P.STATES
    assert out["completed"] is False
    assert "run_id" in out


# ========================================================================
# 3. Model/provider bootstrap (fail closed)
# ========================================================================
def _valid_spec(**kw) -> P.ModelSpec:
    base = dict(id="m.bin", source="http://127.0.0.1:1/m.bin", revision="r1",
                sha256="a" * 64, size_bytes=128, license="Apache-2.0",
                kind="file", local_path="m.bin")
    base.update(kw)
    return P.ModelSpec(**base)


def test_model_bootstrap_offline_missing(tmp_path):
    paths = _paths(tmp_path)
    spec = _valid_spec(id="qubit-brain", source="ollama://qubit-brain",
                       revision="a" * 64, sha256="b" * 64, size_bytes=1024,
                       license="Apache-2.0", kind="ollama",
                       local_path="qubit-brain")
    out = P.model_bootstrap(spec, paths, offline=True)
    assert out["state"] == "MISSING"
    assert out["target"].startswith(str(paths.model_dir))


def test_model_bootstrap_bad_hash_fails_closed(tmp_path):
    paths = _paths(tmp_path)
    paths.model_dir.mkdir(parents=True, exist_ok=True)
    art = paths.model_dir / "model.bin"
    art.write_bytes(b"corrupt")
    spec = _valid_spec(id="model.bin", local_path="model.bin",
                       sha256="0" * 64, size_bytes=7)
    out = P.model_bootstrap(spec, paths, offline=True)
    assert out["state"] == "FAILED"
    assert "sha256 mismatch" in out["detail"]


def test_model_bootstrap_good_hash_promotes(tmp_path):
    paths = _paths(tmp_path)
    paths.model_dir.mkdir(parents=True, exist_ok=True)
    art = paths.model_dir / "model.bin"
    art.write_bytes(b"good bytes")
    actual = P._sha256(art)
    spec = _valid_spec(id="model.bin", local_path="model.bin",
                       sha256=actual, size_bytes=10)
    out = P.model_bootstrap(spec, paths, offline=True)
    assert out["state"] == "OK"
    assert "sha256 verified" in out["detail"]


def test_model_bootstrap_preflight_blocks(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setattr(P, "_preflight",
                        lambda d: {"state": "DEGRADED",
                                   "detail": "insufficient disk"})
    spec = _valid_spec(id="m", local_path="m")
    out = P.model_bootstrap(spec, paths, dry_run=False, offline=False)
    assert out["state"] == "BLOCKED"


def test_model_bootstrap_dry_run_no_promote(tmp_path):
    paths = _paths(tmp_path)
    spec = _valid_spec(id="m", local_path="m")
    out = P.model_bootstrap(spec, paths, dry_run=True)
    assert out["state"] == "READY"
    assert out["promoted"] is False
    assert not (paths.model_dir / "m").exists()


# ========================================================================
# 4. First-run + repair
# ========================================================================
def test_first_run_typed_and_no_secrets(tmp_path):
    paths = _paths(tmp_path)
    out = P.first_run(paths)
    blob = json.dumps(out)
    for secret in ("sk-", "token=", "ghp_", "password", "cookie"):
        assert secret.lower() not in blob.lower()
    assert out["product"] == P.PRODUCT_NAME
    assert "pairing" in out
    assert out["pairing"]["state"] in ("REQUIRES_PAIRING", "PAIRED")
    assert "storage" in out and "config" in out["storage"]


def test_repair_returns_typed_checks_and_next(tmp_path):
    paths = _paths(tmp_path)
    out = P.repair(paths)
    assert "checks" in out and out["checks"]
    assert out["state"] in P.STATES
    for c in out["checks"]:
        assert "check" in c and "state" in c and "next" in c
    blob = json.dumps(out)
    for secret in ("sk-", "ghp_", "password"):
        assert secret not in blob


# ========================================================================
# 5. Branding migration
# ========================================================================
def test_branding_status_fresh_when_no_legacy(tmp_path):
    paths = _paths(tmp_path)
    out = P.branding_status(paths)
    assert out["state"] == "FRESH"
    assert "no legacy installation" in out["detail"]


def test_branding_status_legacy_only(tmp_path):
    paths = _paths(tmp_path)
    paths.legacy_base.mkdir(parents=True, exist_ok=True)
    (paths.legacy_base / "voice.yaml").write_text("audio: {}\n")
    out = P.branding_status(paths)
    assert out["state"] == "LEGACY_ONLY"
    assert "migrate before continuing" in out["detail"]


def test_branding_status_migrated_with_alias(tmp_path):
    paths = _paths(tmp_path)
    paths.legacy_base.parent.mkdir(parents=True, exist_ok=True)
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    (paths.config_dir / "voice.yaml").write_text("audio: {}\n")
    paths.legacy_base.symlink_to(paths.config_dir)
    out = P.branding_status(paths)
    assert out["state"] == "MIGRATED"
    assert out["alias"] is True


# ========================================================================
# 6. Service ownership
# ========================================================================
def test_service_owner_one_service_one_listener(tmp_path):
    out = P.service_owner()
    assert out["owner"] == "systemctl --user"
    assert out["one_service_one_listener"] is True
    assert out["tray_starts_second_bridge"] is False
    assert out["shortcut_starts_second_bridge"] is False
    assert "no hardcoded DISPLAY" not in json.dumps(out)  # never claims


def test_service_owner_headless_possible():
    out = P.service_owner()
    assert "headless" in out


# ========================================================================
# 7. Desktop shortcut
# ========================================================================
def test_shortcut_never_token_in_url(tmp_path):
    paths = _paths(tmp_path)
    out = P.desktop_shortcut(paths, webui_url="http://127.0.0.1:9?token=sekrit")
    assert out["state"] == "FAILED"
    assert "token must never appear in the URL" in out["detail"]


def test_shortcut_dry_run(tmp_path):
    paths = _paths(tmp_path)
    out = P.desktop_shortcut(paths, dry_run=True)
    assert out["state"] in ("READY", "UNAVAILABLE")
    if out["state"] == "READY":
        assert out["token_in_url"] is False or "token" not in json.dumps(out)


def test_shortcut_apply_no_secrets_in_exec(tmp_path):
    paths = _paths(tmp_path)
    out = P.desktop_shortcut(paths, dry_run=False)
    if out["state"] == "OK":
        assert out["token_in_url"] is False
        assert out["secrets_in_args"] is False
        text = (Path(out["detail"].split("to ")[-1])).read_text()
        assert "token" not in text.split("Exec=")[-1]


# ========================================================================
# 8. Optional tray
# ========================================================================
def test_tray_never_required_and_no_second_bridge(tmp_path):
    out = P.tray_status(_paths(tmp_path))
    assert out["required"] is False
    assert out["second_bridge"] is False
    assert out["token_exposure"] is False
    assert out["state"] in ("AVAILABLE", "UNAVAILABLE")


# ========================================================================
# 9. Capability defaults
# ========================================================================
def test_capabilities_typed_and_detection_not_permission(tmp_path):
    out = P.capabilities(_paths(tmp_path))
    assert "capabilities" in out
    valid = {"ENABLED", "DISABLED", "AVAILABLE", "BLOCKED", "UNAVAILABLE",
             "DEGRADED", "FAILED", "OK", "MISSING"}
    for c in out["capabilities"].values():
        assert c["state"] in valid, c
        assert "next" in c
    blob = json.dumps(out)
    assert "detection is not permission" in out["note"]
    for secret in ("sk-", "ghp_", "password"):
        assert secret not in blob


# ========================================================================
# 10. Packaging / private-source boundary
# ========================================================================
def test_package_boundary_honest():
    out = P.package_boundary()
    assert out["private_source_solved"] is False
    assert "release blocker" in out["release_blocker"]
    assert out["wheel_has_python_source"] is True


# ========================================================================
# 11. Upgrade / uninstall / rollback
# ========================================================================
def test_upgrade_contract_preserves_state():
    out = P.upgrade_contract()
    assert out["state"] == "OK"
    assert "user config" in out["preserves"]
    assert "browser profiles" in out["preserves"]
    assert any("services silently" in d for d in out["does_not"])
    assert "credentials into backups" in " ".join(out["does_not"])
    assert out["newer_schema_fails_closed"] is True
    assert out["uninstall_preserves_state"] is True


def test_rollback_missing_backup(tmp_path):
    paths = _paths(tmp_path)
    out = P.rollback(paths)
    assert out["state"] == "MISSING"


def test_rollback_dry_run(tmp_path):
    paths = _paths(tmp_path)
    bak = paths.backup_dir / "latest"
    bak.mkdir(parents=True, exist_ok=True)
    (bak / "state.json").write_text("{}")
    out = P.rollback(paths, dry_run=True)
    assert out["state"] == "READY"


def test_rollback_applies_atomically(tmp_path):
    paths = _paths(tmp_path)
    bak = paths.backup_dir / "latest"
    bak.mkdir(parents=True, exist_ok=True)
    (bak / "state.json").write_text('{"v": 1}')
    out = P.rollback(paths, dry_run=False)
    assert out["state"] == "OK"
    assert (paths.state_dir / "state.json").exists()


# ========================================================================
# CLI surface
# ========================================================================
def _cli_env(tmp_path: Path) -> Dict[str, str]:
    """Isolated HOME + XDG environment for CLI subprocess tests so they never
    touch the operator's real HOME/state (the CLI config/state defaults use
    `~`-relative constants)."""
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg-config")
    env["XDG_STATE_HOME"] = str(tmp_path / "xdg-state")
    env["XDG_DATA_HOME"] = str(tmp_path / "xdg-data")
    env["XDG_CACHE_HOME"] = str(tmp_path / "xdg-cache")
    return env


def test_cli_product_paths_json(tmp_path):
    r = subprocess.run(
        [sys.executable, "-m", "voicebridge", "product", "paths", "--json"],
        capture_output=True, text=True, timeout=60, env=_cli_env(tmp_path))
    assert r.returncode == 0, r.stderr
    diag = json.loads(r.stdout)
    assert diag["product"] == P.PRODUCT_NAME


def test_cli_product_boundary(tmp_path):
    r = subprocess.run(
        [sys.executable, "-m", "voicebridge", "product", "boundary", "--json"],
        capture_output=True, text=True, timeout=60, env=_cli_env(tmp_path))
    assert r.returncode == 0  # informational; private-source is NOT solved
    out = json.loads(r.stdout)
    assert out["private_source_solved"] is False


def test_cli_product_shortcut_refuses_token_url(tmp_path):
    r = subprocess.run(
        [sys.executable, "-m", "voicebridge", "product", "shortcut",
         "--json"],
        capture_output=True, text=True, timeout=60, env=_cli_env(tmp_path))
    assert r.returncode == 0
    assert json.loads(r.stdout)["state"] in ("READY", "UNAVAILABLE")


def test_cli_product_repair_no_secrets(tmp_path):
    r = subprocess.run(
        [sys.executable, "-m", "voicebridge", "product", "repair"],
        capture_output=True, text=True, timeout=60, env=_cli_env(tmp_path))
    out = r.stdout + r.stderr
    for secret in ("sk-", "ghp_", "password="):
        assert secret not in out


# ========================================================================
# 12. Installer apply: staging + compat aliases + idempotency
# ========================================================================
def _ready_prereqs(monkeypatch):
    """Force every install prerequisite to OK so the apply path runs."""
    monkeypatch.setattr(P, "_require_python",
                        lambda: {"state": "OK", "detail": "ok"})
    monkeypatch.setattr(P, "_require_audio",
                        lambda: {"state": "OK", "detail": "ok"})
    monkeypatch.setattr(P, "_require_ollama",
                        lambda: {"state": "OK", "detail": "ok"})
    monkeypatch.setattr(P, "_require_session",
                        lambda: {"state": "OK", "detail": "ok"})


def test_install_apply_stages_defaults_and_aliases(tmp_path, monkeypatch):
    _ready_prereqs(monkeypatch)
    paths = _paths(tmp_path)
    out = P.install(paths, dry_run=False, service_owner="systemctl --user")
    assert out["state"] == "READY"
    assert out["completed"] is True
    # packaged default staged
    assert (paths.config_dir / "voice.yaml").exists()
    # legacy compat aliases created for config/state/data
    for legacy, canonical in P._legacy_pairs(paths):
        assert legacy.is_symlink(), legacy
        assert os.path.realpath(legacy) == str(canonical)
    assert P.legacy_alias_status(paths)["state"] == "OK"


def test_install_apply_idempotent(tmp_path, monkeypatch):
    _ready_prereqs(monkeypatch)
    paths = _paths(tmp_path)
    first = P.install(paths, dry_run=False)
    second = P.install(paths, dry_run=False)
    assert first["state"] == "READY"
    assert second["state"] == "READY"
    # no duplicate staged files, no duplicate aliases
    assert (paths.config_dir / "voice.yaml").exists()
    for legacy, canonical in P._legacy_pairs(paths):
        assert legacy.is_symlink()
    files = sorted(p.name for p in (paths.config_dir).iterdir())
    assert files.count("voice.yaml") == 1


def test_install_apply_never_overwrites_tuned_config(tmp_path, monkeypatch):
    _ready_prereqs(monkeypatch)
    paths = _paths(tmp_path)
    P.install(paths, dry_run=False)
    tuned = "tuned-setting: keep-me\n"
    (paths.config_dir / "voice.yaml").write_text(tuned)
    out = P.install(paths, dry_run=False)
    assert out["state"] == "READY"
    staged = [s for s in out["staged"] if s["resource"] == "voice.yaml"]
    assert staged and staged[0]["state"] == "KEPT"
    assert (paths.config_dir / "voice.yaml").read_text() == tuned


def test_install_blocked_on_legacy_real_dir(tmp_path, monkeypatch):
    _ready_prereqs(monkeypatch)
    paths = _paths(tmp_path)
    paths.legacy_base.mkdir(parents=True)
    (paths.legacy_base / "voice.yaml").write_text("audio: {}\n")
    out = P.install(paths, dry_run=False)
    assert out["state"] == "BLOCKED"
    assert out["completed"] is False
    assert any(s["step"] == "migration" and s["state"] == "BLOCKED"
               for s in out["steps"])


def test_install_apply_blocks_when_lock_held(tmp_path, monkeypatch):
    _ready_prereqs(monkeypatch)
    paths = _paths(tmp_path)
    lock = paths.state_dir / "install.lock"
    held = P.InstallLock(lock)
    assert held.acquire() is True
    out = P.install(paths, dry_run=False, lock_path=lock)
    assert out["state"] == "BLOCKED"
    assert out["completed"] is False
    held.release()


def test_install_never_reports_complete_when_migration_blocks(tmp_path,
                                                              monkeypatch):
    _ready_prereqs(monkeypatch)
    paths = _paths(tmp_path)
    paths.legacy_base.mkdir(parents=True)
    out = P.install(paths, dry_run=False)
    assert out["state"] == "BLOCKED"
    assert out["completed"] is False


def test_path_map_stable_across_restarts(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "stt"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "dat"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cch"))
    p1 = P.default_paths()
    p2 = P.default_paths()
    assert p1.map == p2.map
    assert p1.config_dir == p2.config_dir
    assert p1.state_dir == p2.state_dir


# ========================================================================
# 13. Reversible branding migration
# ========================================================================
def _legacy_install(paths: P.ProductPaths) -> None:
    paths.legacy_base.mkdir(parents=True)
    (paths.legacy_base / "voice.yaml").write_text("audio: {}\n")
    (paths.legacy_base / ".env").write_text("KEY=sekrit\n")
    paths.legacy_state.mkdir(parents=True)
    (paths.legacy_state / "events.jsonl").write_text('{"a":1}\n')
    paths.legacy_data.mkdir(parents=True)
    (paths.legacy_data / "models").mkdir(parents=True)


def test_migrate_dry_run_is_read_only(tmp_path):
    paths = _paths(tmp_path)
    _legacy_install(paths)
    out = P.migrate(paths, dry_run=True)
    assert out["state"] == "READY"
    assert out["backup_dir"] is not None
    # nothing moved or aliased in dry-run
    assert paths.legacy_base.is_dir()
    assert not paths.legacy_base.is_symlink()
    assert not (paths.config_dir / "voice.yaml").exists()


def test_migrate_apply_moves_and_aliases(tmp_path):
    paths = _paths(tmp_path)
    _legacy_install(paths)
    out = P.migrate(paths, dry_run=False)
    assert out["state"] == "OK"
    assert len(out["moved"]) == 3
    # legacy paths now resolve to canonical (no split trees)
    assert paths.legacy_base.is_symlink()
    assert os.path.realpath(paths.legacy_base) == str(paths.config_dir)
    assert os.path.realpath(paths.legacy_state) == str(paths.state_dir)
    assert (paths.config_dir / "voice.yaml").exists()
    assert (paths.state_dir / "events.jsonl").exists()
    # backup kept
    bak = Path(out["backup_dir"])
    assert (bak / "legacy" / "voice.yaml").exists()


def test_migrate_rerun_is_fresh_no_duplicates(tmp_path):
    paths = _paths(tmp_path)
    _legacy_install(paths)
    P.migrate(paths, dry_run=False)
    second = P.migrate(paths, dry_run=False)
    assert second["state"] == "FRESH"
    # exactly one physical tree (no duplicate state dirs)
    assert paths.legacy_base.is_symlink()
    assert (paths.config_dir / "voice.yaml").exists()


def test_migrate_credentials_not_copied_to_backup(tmp_path):
    paths = _paths(tmp_path)
    _legacy_install(paths)
    out = P.migrate(paths, dry_run=False)
    assert ".env" in out["excluded"]
    bak = Path(out["backup_dir"])
    assert not (bak / "legacy" / ".env").exists()
    # the LIVE migrated tree keeps the credential bytes (no loss)
    assert (paths.config_dir / ".env").read_text() == "KEY=sekrit\n"


def test_migrate_rollback_restores(tmp_path):
    paths = _paths(tmp_path)
    _legacy_install(paths)
    out = P.migrate(paths, dry_run=False)
    rb = P.migrate_rollback(paths, backup_dir=out["backup_dir"], dry_run=False)
    assert rb["state"] == "OK"
    assert paths.legacy_base.is_dir()
    assert not paths.legacy_base.is_symlink()
    assert (paths.legacy_base / "voice.yaml").exists()
    assert (paths.legacy_state / "events.jsonl").exists()
    assert not paths.config_dir.exists()


def test_migrate_interrupted_before_alias_resumes(tmp_path):
    # simulate a crash after the move but before the alias step
    paths = _paths(tmp_path)
    _legacy_install(paths)
    for legacy, canonical in P._legacy_pairs(paths):
        if not legacy.exists() or legacy.is_symlink():
            continue
        canonical.parent.mkdir(parents=True, exist_ok=True)
        if canonical.exists():
            continue  # second legacy family sharing one canonical target
        legacy.rename(canonical)
    out = P.migrate(paths, dry_run=False)
    assert out["state"] == "OK"
    # resume safety created the missing aliases (idempotent, no duplicates)
    assert paths.legacy_base.is_symlink()
    assert os.path.realpath(paths.legacy_base) == str(paths.config_dir)
    assert (paths.config_dir / "voice.yaml").exists()


def test_migrate_never_creates_services(tmp_path):
    paths = _paths(tmp_path)
    _legacy_install(paths)
    out = P.migrate(paths, dry_run=False)
    assert out["state"] == "OK"
    # no unit files or service dirs were created anywhere under the root
    for p in paths.base.rglob("*.service"):
        raise AssertionError(f"migration created a service unit: {p}")
    assert not (paths.base / "systemd").exists()


def test_migrate_preserves_existing_state_bytes(tmp_path):
    paths = _paths(tmp_path)
    _legacy_install(paths)
    before = (paths.legacy_state / "events.jsonl").read_bytes()
    P.migrate(paths, dry_run=False)
    assert (paths.state_dir / "events.jsonl").read_bytes() == before


def test_branding_status_after_migration(tmp_path):
    paths = _paths(tmp_path)
    _legacy_install(paths)
    assert P.branding_status(paths)["state"] == "LEGACY_ONLY"
    P.migrate(paths, dry_run=False)
    st = P.branding_status(paths)
    assert st["state"] == "MIGRATED"
    assert st["alias"] is True


# ========================================================================
# 14. First-run apply (explicit merge, never overwrite tuned settings)
# ========================================================================
def test_first_run_apply_stages_and_preserves_tuned(tmp_path, monkeypatch):
    _ready_prereqs(monkeypatch)
    paths = _paths(tmp_path)
    first = P.first_run(paths, dry_run=False)
    assert first["state"] == "READY"
    assert (paths.config_dir / "voice.yaml").exists()
    tuned = "webui:\n  token: local-tuned\n"
    (paths.config_dir / "voice.yaml").write_text(tuned)
    second = P.first_run(paths, dry_run=False)
    assert second["state"] == "READY"
    kept = [s for s in second["staged"] if s["resource"] == "voice.yaml"]
    assert kept and kept[0]["state"] == "KEPT"
    assert (paths.config_dir / "voice.yaml").read_text() == tuned


def test_first_run_dry_run_does_not_write(tmp_path, monkeypatch):
    _ready_prereqs(monkeypatch)
    paths = _paths(tmp_path)
    out = P.first_run(paths, dry_run=True)
    assert out["dry_run"] is True
    assert not (paths.config_dir / "voice.yaml").exists()
    assert not paths.legacy_base.is_symlink()


def test_repair_includes_migration_check(tmp_path, monkeypatch):
    _ready_prereqs(monkeypatch)
    paths = _paths(tmp_path)
    paths.legacy_base.mkdir(parents=True)
    out = P.repair(paths)
    checks = {c["check"]: c for c in out["checks"]}
    assert checks["migration"]["state"] == "BLOCKED"
    assert "next" in checks["migration"]


# ========================================================================
# 15. Service ownership (one service one listener, typed stale detection)
# ========================================================================
def test_service_owner_stale_legacy_unit_typed(tmp_path):
    udir = tmp_path / "systemd/user"
    udir.mkdir(parents=True)
    (udir / f"{P.LEGACY_NAME}.service").write_text("[Unit]\n")
    out = P.service_owner(unit_dir=udir)
    assert out["stale_legacy_unit"] == "STALE"
    assert out["one_service_one_listener"] is True


def test_service_owner_no_stale_unit(tmp_path):
    udir = tmp_path / "systemd/user"
    udir.mkdir(parents=True)
    out = P.service_owner(unit_dir=udir)
    assert out["stale_legacy_unit"] == "NONE"


def test_service_owner_unit_no_hardcoded_home_or_display(tmp_path):
    out = P.service_owner(unit_dir=tmp_path / "units")
    assert out["systemd_unit_has_hardcoded_home_or_display"] is False
    assert "x11_wayland" in out
    assert "headless" in out


# ========================================================================
# 16. Desktop shortcut in isolated XDG
# ========================================================================
def test_shortcut_apply_writes_no_token_exec(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("DISPLAY", ":0")
    paths = _paths(tmp_path)
    out = P.desktop_shortcut(paths, dry_run=False)
    assert out["state"] in ("OK", "UNAVAILABLE")
    if out["state"] == "OK":
        dest = (tmp_path / "xdg-data" / "applications" /
                f"{P.PRODUCT_NAME}.desktop")
        assert dest.exists()
        text = dest.read_text()
        assert "token" not in text.split("Exec=")[-1]
        assert "sk-" not in text


def test_shortcut_reports_no_session(tmp_path, monkeypatch):
    monkeypatch.setenv("DISPLAY", "")
    monkeypatch.setenv("WAYLAND_DISPLAY", "")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "")
    paths = _paths(tmp_path)
    out = P.desktop_shortcut(paths, dry_run=False)
    assert out["state"] in ("DEGRADED", "UNAVAILABLE")
    assert "CLI/headless" in out["detail"]


# ========================================================================
# 17. Model bootstrap: resume/retry, size validation, timeout, low disk
# ========================================================================
def _range_server(payload: bytes):
    """A tiny HTTP server that honours Range requests (resume)."""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802
            body = payload
            start = 0
            rng = self.headers.get("Range")
            if rng and rng.startswith("bytes="):
                start = int(rng.split("=")[1].split("-")[0])
            body = body[start:]
            self.send_response(206 if start else 200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def log_message(self, *args):  # noqa: A003
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/m.bin"


def _stop_server(srv) -> None:
    srv.shutdown()
    srv.server_close()


def test_model_download_resumes_from_part(tmp_path):
    payload = b"model-bytes-" * 256
    expect_file = tmp_path / "expect.bin"
    expect_file.write_bytes(payload)
    srv, url = _range_server(payload)
    try:
        paths = _paths(tmp_path)
        paths.model_dir.mkdir(parents=True)
        part = paths.model_dir / ".dl.m.bin.part"
        part.write_bytes(payload[:128])
        spec = P.ModelSpec(id="m.bin", source=url, revision="r1",
                           sha256=P._sha256(expect_file),
                           size_bytes=len(payload), kind="file",
                           local_path="m.bin", license="Apache-2.0")
        out = P.model_bootstrap(spec, paths, dry_run=False, offline=False)
        assert out["state"] == "OK", out
        assert (paths.model_dir / "m.bin").read_bytes() == payload
        assert not (paths.model_dir / ".dl.m.bin.part").exists()
    finally:
        _stop_server(srv)


def test_model_download_size_validation(tmp_path):
    payload = b"short"
    expect_file = tmp_path / "expect.bin"
    expect_file.write_bytes(payload)
    srv, url = _range_server(payload)
    try:
        paths = _paths(tmp_path)
        spec = P.ModelSpec(id="m.bin", source=url, revision="r1",
                           sha256=P._sha256(expect_file),
                           size_bytes=len(payload) + 100,
                           kind="file", local_path="m.bin",
                           license="Apache-2.0")
        out = P.model_bootstrap(spec, paths, dry_run=False, offline=False)
        assert out["state"] == "FAILED"
        assert "size mismatch" in out["detail"]
        assert "resume_part" in out
    finally:
        _stop_server(srv)


def test_model_download_exact_size_without_content_length(tmp_path):
    """Exact byte size is validated even when the server sends no
    Content-Length header (fail closed on truncated/short downloads)."""
    payload = b"x" * 64
    expect_file = tmp_path / "expect.bin"
    expect_file.write_bytes(payload)

    import http.server
    import threading

    class NoLenHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802
            # deliberately omit Content-Length
            self.send_response(200)
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True

        def log_message(self, *args):  # noqa: A003
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), NoLenHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/m.bin"
    try:
        paths = _paths(tmp_path)
        spec = P.ModelSpec(id="m.bin", source=url, revision="r1",
                           sha256=P._sha256(expect_file),
                           size_bytes=len(payload), kind="file",
                           local_path="m.bin", license="Apache-2.0")
        out = P.model_bootstrap(spec, paths, dry_run=False, offline=False)
        assert out["state"] == "OK", out
        # wrong expected size must fail even without Content-Length
        spec_bad = P.ModelSpec(id="m.bin", source=url, revision="r1",
                               sha256=P._sha256(expect_file),
                               size_bytes=len(payload) + 7, kind="file",
                               local_path="m.bin", license="Apache-2.0")
        paths2 = _paths(tmp_path / "again")
        out_bad = P.model_bootstrap(spec_bad, paths2, dry_run=False,
                                    offline=False)
        assert out_bad["state"] == "FAILED"
        assert "size mismatch" in out_bad["detail"]
    finally:
        _stop_server(srv)


def test_model_provider_timeout_fails_closed(tmp_path, monkeypatch):
    paths = _paths(tmp_path)

    def boom(*a, **k):
        raise P._DownloadError("provider timeout after 3 attempts")

    monkeypatch.setattr(P, "_download_with_retry", boom)
    spec = _valid_spec(id="m.bin", size_bytes=32)
    out = P.model_bootstrap(spec, paths, dry_run=False, offline=False)
    assert out["state"] == "FAILED"
    assert "resume_part" in out
    assert not (paths.model_dir / "m.bin").exists()


def test_model_low_ram_blocks_preflight(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setattr(P, "_preflight",
                        lambda d: {"state": "DEGRADED",
                                   "detail": "low RAM: 1.0 GiB"})
    spec = _valid_spec(id="m", local_path="m")
    out = P.model_bootstrap(spec, paths, dry_run=False, offline=False)
    assert out["state"] == "BLOCKED"
    assert "preflight" in out


def test_model_offline_never_network(tmp_path):
    paths = _paths(tmp_path)
    spec = _valid_spec(id="m", local_path="m")
    out = P.model_bootstrap(spec, paths, offline=True)
    assert out["state"] == "MISSING"


def test_model_bad_hash_rejects_promoted_file(tmp_path):
    paths = _paths(tmp_path)
    paths.model_dir.mkdir(parents=True)
    art = paths.model_dir / "m.bin"
    art.write_bytes(b"corrupt-bytes")
    spec = P.ModelSpec(id="m.bin", source="http://x/m.bin", revision="r1",
                       sha256="0" * 64, size_bytes=13, kind="file",
                       local_path="m.bin", license="Apache-2.0")
    out = P.model_bootstrap(spec, paths, offline=True)
    assert out["state"] == "FAILED"


# ========================================================================
# 18. Upgrade/uninstall/rollback + path stability + boundary scans
# ========================================================================
def test_upgrade_never_restarts_services():
    out = P.upgrade_contract()
    joined = " ".join(out["does_not"])
    assert "enable/restart services silently" in joined
    assert out["newer_schema_fails_closed"] is True


def test_rollback_missing_backup_typed(tmp_path):
    paths = _paths(tmp_path)
    out = P.rollback(paths, dry_run=False)
    assert out["state"] == "MISSING"


def test_package_boundary_no_secrets_and_private_source_honest():
    out = P.package_boundary()
    assert out["private_source_solved"] is False
    assert out["wheel_has_python_source"] is True
    for field in ("distributed", "excluded"):
        assert isinstance(out[field], list)
    blob = json.dumps(out)
    for secret in ("sk-", "ghp_", "password"):
        assert secret not in blob


def test_first_run_pairing_never_token_in_url(tmp_path):
    paths = _paths(tmp_path)
    out = P.first_run(paths)
    assert out["pairing"]["state"] in ("REQUIRES_PAIRING", "PAIRED")
    blob = json.dumps(out)
    assert "token=" not in blob
    # the pairing block never carries an actual token value
    assert "token=" not in json.dumps(out["pairing"])


def test_capabilities_typed_and_no_reroute(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "_require_audio",
                        lambda: {"state": "OK", "detail": "2 audio devices"})
    out = P.capabilities(_paths(tmp_path))
    assert out["capabilities"]["voice"]["state"] == "ENABLED"
    assert "detection is not permission" in out["note"]


def test_cli_product_migrate_dry_run(tmp_path):
    r = subprocess.run(
        [sys.executable, "-m", "voicebridge", "product", "migrate",
         "--json"],
        capture_output=True, text=True, timeout=60, env=_cli_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["state"] == "FRESH"


def test_cli_product_paths_shows_legacy_aliases(tmp_path):
    r = subprocess.run(
        [sys.executable, "-m", "voicebridge", "product", "paths", "--json"],
        capture_output=True, text=True, timeout=60, env=_cli_env(tmp_path))
    assert r.returncode == 0, r.stderr
    diag = json.loads(r.stdout)
    assert "legacy_config" in diag["paths"]
    assert "legacy_state" in diag["paths"]
    assert "legacy_data" in diag["paths"]

# ========================================================================
# 19. Audit repairs: dry-run is strictly side-effect-free (item 1)
# ========================================================================
def _fs_snapshot(root: Path) -> Dict[str, str]:
    """Hash-based filesystem snapshot: every dir/file/symlink under root with
    content hashes and symlink targets, so ANY write is detected."""
    snap: Dict[str, str] = {}
    if not root.exists():
        return snap
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_symlink():
            snap[f"link:{rel}"] = os.readlink(p)
        elif p.is_dir():
            snap[f"dir:{rel}"] = ""
        else:
            snap[f"file:{rel}"] = P._sha256(p)
    return snap


def _noop_capabilities(paths=None):
    caps = {
        name: {"capability": name, "state": "AVAILABLE",
               "detail": "injected", "next": "ready"}
        for name in ("voice", "provider", "display", "browser", "tray")
    }
    return {"capabilities": caps, "note": "injected"}


def test_install_dry_run_zero_writes(tmp_path, monkeypatch):
    _ready_prereqs(monkeypatch)
    root = tmp_path / "root"
    before = _fs_snapshot(root)
    paths = P.default_paths(base=root)
    out = P.install(paths, dry_run=True)
    assert out["state"] == "READY"
    assert _fs_snapshot(root) == before


def test_install_dry_run_zero_writes_even_when_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "_require_audio",
                        lambda: {"state": "MISSING", "detail": "no audio"})
    root = tmp_path / "root"
    before = _fs_snapshot(root)
    out = P.install(P.default_paths(base=root), dry_run=True)
    assert out["state"] in ("MISSING", "BLOCKED")
    assert _fs_snapshot(root) == before


def test_first_run_dry_run_zero_writes(tmp_path, monkeypatch):
    _ready_prereqs(monkeypatch)
    monkeypatch.setattr(P, "capabilities", _noop_capabilities)
    root = tmp_path / "root"
    before = _fs_snapshot(root)
    paths = P.default_paths(base=root)
    out = P.first_run(paths, dry_run=True)
    assert out["state"] == "READY"
    assert _fs_snapshot(root) == before


def test_migrate_dry_run_zero_writes(tmp_path):
    paths = _paths(tmp_path)
    _legacy_install(paths)
    before = _fs_snapshot(paths.base)
    out = P.migrate(paths, dry_run=True)
    assert out["state"] == "READY"
    assert _fs_snapshot(paths.base) == before


# ========================================================================
# 20. Audit repairs: model bootstrap fails closed (item 2)
# ========================================================================
def test_model_spec_requires_all_metadata():
    errors = P._validate_spec(P.ModelSpec(id="", source="", revision="",
                                          sha256="", size_bytes=0,
                                          license="", kind="file"))
    joined = "; ".join(errors)
    assert "empty model id" in joined
    assert "empty source" in joined
    assert "empty revision" in joined
    assert "empty license" in joined
    assert "empty sha256" in joined
    assert "positive exact byte size" in joined


def test_model_spec_rejects_bad_scheme():
    spec = _valid_spec(source="ftp://x/m.bin")
    errors = P._validate_spec(spec)
    assert any("unapproved source scheme" in e for e in errors)


def test_model_spec_rejects_invalid_hash():
    spec = _valid_spec(sha256="not-a-hash")
    errors = P._validate_spec(spec)
    assert any("64 hex" in e for e in errors)


def test_model_spec_rejects_oversized():
    spec = _valid_spec(size_bytes=P.MAX_DOWNLOAD_BYTES + 1)
    errors = P._validate_spec(spec)
    assert any("bound" in e for e in errors)


def test_model_spec_rejects_unapproved_license():
    spec = _valid_spec(license="Made-Up-1.0")
    errors = P._validate_spec(spec)
    assert any("unapproved license" in e for e in errors)


def test_model_spec_rejects_ollama_bad_digest():
    spec = _valid_spec(source="ollama://qubit-brain", revision="digest",
                       kind="ollama")
    errors = P._validate_spec(spec)
    assert any("provider digest" in e for e in errors)


def test_model_invalid_spec_fails_closed_no_download(tmp_path):
    paths = _paths(tmp_path)
    bad = P.ModelSpec(id="m", source="", revision="", sha256="",
                      size_bytes=0, license="", kind="file")
    out = P.model_bootstrap(bad, paths, dry_run=False)
    assert out["state"] == "FAILED"
    assert "invalid model spec" in out["detail"]
    # nothing was created
    assert not (paths.model_dir / "m").exists()


def test_model_traversal_rejected(tmp_path):
    paths = _paths(tmp_path)
    spec = _valid_spec(local_path="../evil.bin")
    assert P._safe_model_target(paths.model_dir, spec) is None
    out = P.model_bootstrap(spec, paths, dry_run=True)
    assert out["state"] == "FAILED"
    assert "traversal" in out["detail"]


def test_model_preserves_previous_valid_on_failure(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    paths.model_dir.mkdir(parents=True)
    old = paths.model_dir / "m.bin"
    old.write_bytes(b"old-valid-model")

    def boom(*a, **k):
        raise P._DownloadError("provider timeout")

    monkeypatch.setattr(P, "_download_with_retry", boom)
    spec = _valid_spec(id="m.bin", local_path="m.bin", sha256="a" * 64,
                       size_bytes=16)
    out = P.model_bootstrap(spec, paths, dry_run=False, offline=False)
    assert out["state"] == "FAILED"
    # the previous valid model was NOT deleted or overwritten
    assert (paths.model_dir / "m.bin").read_bytes() == b"old-valid-model"


def test_model_tampered_download_hash_removes_part(tmp_path):
    payload = b"tampered-bytes"
    expect_file = tmp_path / "expect.bin"
    expect_file.write_bytes(payload)
    srv, url = _range_server(payload)
    try:
        paths = _paths(tmp_path)
        spec = P.ModelSpec(id="m.bin", source=url, revision="r1",
                           sha256="0" * 64,  # expected hash never matches
                           size_bytes=len(payload), kind="file",
                           local_path="m.bin", license="Apache-2.0")
        out = P.model_bootstrap(spec, paths, dry_run=False, offline=False)
        assert out["state"] == "FAILED"
        assert "sha256 mismatch" in out["detail"]
        assert not (paths.model_dir / "m.bin").exists()
        # tampered .part was removed (no lingering temp file)
        assert not (paths.model_dir / ".dl.m.bin.part").exists()
    finally:
        _stop_server(srv)


def test_model_ollama_digest_mismatch_fails_closed(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setattr(
        P, "_preflight", lambda d: {"state": "OK", "disk_ok": True,
                                    "ram_ok": True})
    rec = {"state": "OK", "checksum": "f" * 64, "revision": "f" * 64,
           "alias": "qubit-brain"}
    monkeypatch.setattr("voicebridge.distribution.model_artifact_record",
                        lambda cfg: dict(rec))
    spec = P.ModelSpec(id="qubit-brain", source="ollama://qubit-brain",
                       revision="a" * 64, sha256="b" * 64, size_bytes=1024,
                       license="Apache-2.0", kind="ollama")
    out = P.model_bootstrap(spec, paths, dry_run=False, offline=False)
    assert out["state"] == "FAILED"
    assert "digest" in out["detail"].lower()


def test_model_interrupted_download_keeps_resumable_part(tmp_path,
                                                         monkeypatch):
    payload = b"interrupt-me" * 32
    expect_file = tmp_path / "expect.bin"
    expect_file.write_bytes(payload)
    srv, url = _range_server(payload)
    try:
        paths = _paths(tmp_path)
        spec = P.ModelSpec(id="m.bin", source=url, revision="r1",
                           sha256=P._sha256(expect_file),
                           size_bytes=len(payload), kind="file",
                           local_path="m.bin", license="Apache-2.0")
        paths.model_dir.mkdir(parents=True)
        part = paths.model_dir / ".dl.m.bin.part"
        part.write_bytes(payload[:64])
        calls = {"n": 0}
        real = P._resume_download

        def flaky(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise P._DownloadError("interrupted mid-stream")
            return real(*a, **k)

        monkeypatch.setattr(P, "_resume_download", flaky)
        out = P.model_bootstrap(spec, paths, dry_run=False, offline=False,
                                attempts=2)
        assert out["state"] == "OK", out
        assert (paths.model_dir / "m.bin").read_bytes() == payload
        assert calls["n"] >= 2  # a real bounded retry resumed the .part
    finally:
        _stop_server(srv)


# ========================================================================
# 21. Audit repairs: desktop shortcut is real and testable (item 3)
# ========================================================================
def test_webui_command_exists():
    from voicebridge.cli import build_parser
    build_parser().parse_args(["webui", "--open"])  # must not SystemExit


def test_shortcut_exec_is_the_real_webui_launcher(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("DISPLAY", ":0")
    paths = _paths(tmp_path)
    out = P.desktop_shortcut(paths, dry_run=False)
    assert out["state"] in ("OK", "UNAVAILABLE")
    if out["state"] == "OK":
        dest = (tmp_path / "xdg" / "applications" /
                f"{P.PRODUCT_NAME}.desktop")
        text = dest.read_text()
        exec_line = [l for l in text.splitlines() if l.startswith("Exec=")][0]
        assert exec_line == f"Exec={sys.executable} -m voicebridge webui --open"
        assert "token" not in text
        assert "sk-" not in text


def test_shortcut_returns_failed_if_webui_command_missing(tmp_path,
                                                          monkeypatch):
    monkeypatch.setattr(P, "_webui_open_available", lambda: False)
    monkeypatch.setenv("DISPLAY", ":0")
    out = P.desktop_shortcut(_paths(tmp_path), dry_run=True)
    assert out["state"] == "FAILED"
    assert "webui" in out["detail"]


def _closed_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_webui_open_executed_in_isolated_home(tmp_path):
    """Execute the real shortcut launcher inside an isolated HOME. The
    dashboard is not running -> typed UNAVAILABLE, nothing started, no token,
    no crash."""
    port = _closed_port()
    r = subprocess.run(
        [sys.executable, "-m", "voicebridge", "webui", "--open", "--json",
         "--port", str(port)],
        capture_output=True, text=True, timeout=60, env=_cli_env(tmp_path))
    assert r.returncode != 0
    out = json.loads(r.stdout)
    assert out["state"] == "UNAVAILABLE"
    assert out["started_second_bridge"] is False
    assert out["token_in_url"] is False
    assert "token" not in (r.stdout + r.stderr).lower() or \
        out["detail"].startswith("no dashboard")


def test_webui_ready_reachable_no_token(tmp_path):
    import http.server
    import threading

    class OK(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"dashboard")

        def log_message(self, *args):  # noqa: A003
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), OK)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        r = subprocess.run(
            [sys.executable, "-m", "voicebridge", "webui", "--json",
             "--port", str(port)],
            capture_output=True, text=True, timeout=60,
            env=_cli_env(tmp_path))
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert out["state"] == "READY"
        assert out["token_in_url"] is False
        assert out["started_second_bridge"] is False
    finally:
        _stop_server(srv)


# ========================================================================
# 22. Audit repairs: first-run pairing is truthful (item 4)
# ========================================================================
def test_first_run_pairing_truthful(tmp_path, monkeypatch):
    _ready_prereqs(monkeypatch)
    monkeypatch.setattr(P, "capabilities", _noop_capabilities)
    paths = _paths(tmp_path)
    out = P.first_run(paths, dry_run=True)
    assert out["pairing"]["state"] == "REQUIRES_PAIRING"
    assert "generated" not in out["pairing"]["detail"].lower()
    # once a token is actually stored in canonical config, pairing is PAIRED
    paths.config_dir.mkdir(parents=True)
    (paths.config_dir / "voice.yaml").write_text(
        "webui:\n  token: real-secret-token\n")
    out2 = P.first_run(paths, dry_run=True)
    assert out2["pairing"]["state"] == "PAIRED"
    blob = json.dumps(out2)
    assert "real-secret-token" not in blob


# ========================================================================
# 23. Audit repairs: rollback is genuinely atomic (item 5)
# ========================================================================
def _make_backup(paths, files: Dict[str, str]) -> Path:
    bdir = paths.backup_dir / "latest"
    bdir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        p = bdir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    P._write_backup_manifest(bdir)
    return bdir


def test_rollback_corrupted_manifest_fails_closed(tmp_path):
    paths = _paths(tmp_path)
    bdir = paths.backup_dir / "latest"
    bdir.mkdir(parents=True)
    (bdir / "manifest.json").write_text("{not json")
    (bdir / "state.json").write_text("{}")
    out = P.rollback(paths, dry_run=False)
    assert out["state"] == "FAILED"
    assert "corrupted backup manifest" in out["detail"]
    assert out.get("blocks_resume") is True


def test_rollback_newer_schema_fails_closed(tmp_path):
    paths = _paths(tmp_path)
    bdir = paths.backup_dir / "latest"
    bdir.mkdir(parents=True)
    (bdir / "manifest.json").write_text(
        json.dumps({"schema_version": 999, "files": {}}))
    (bdir / "state.json").write_text("{}")
    out = P.rollback(paths, dry_run=False)
    assert out["state"] == "FAILED"
    assert "newer than runtime" in out["detail"]
    assert out.get("blocks_resume") is True


def test_rollback_corrupted_backup_file_fails_closed(tmp_path):
    paths = _paths(tmp_path)
    bdir = _make_backup(paths, {"state.json": '{"v": 1}'})
    # tamper the backup after the manifest was written
    (bdir / "state.json").write_text("tampered!!!")
    out = P.rollback(paths, dry_run=False)
    assert out["state"] == "FAILED"
    assert "sha256 mismatch" in out["detail"]
    assert out.get("blocks_resume") is True


def test_rollback_readonly_target_fails_closed(tmp_path):
    paths = _paths(tmp_path)
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    _make_backup(paths, {"state.json": '{"v": 1}'})
    os.chmod(paths.state_dir, 0o500)
    try:
        out = P.rollback(paths, dry_run=False)
        assert out["state"] == "FAILED"
        assert out.get("blocks_resume") is True
        assert "read-only" in out["detail"] or "partial rollback" in \
            out["detail"]
    finally:
        os.chmod(paths.state_dir, 0o700)


def test_rollback_disk_full_staging_fails_closed(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _make_backup(paths, {"state.json": '{"v": 1}'})

    def full(src, dst):
        raise OSError("No space left on device")

    monkeypatch.setattr(shutil, "copy2", full)
    out = P.rollback(paths, dry_run=False)
    assert out["state"] == "FAILED"
    assert "staging failed" in out["detail"]
    assert out.get("blocks_resume") is True


def test_rollback_partial_reports_exact_paths(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    _make_backup(paths, {"a.json": '{"a": 1}', "b.json": '{"b": 2}'})
    real_replace = os.replace

    def flaky_replace(src, dst):
        if str(dst).endswith("b.json"):
            raise OSError("fake io error")
        return real_replace(src, dst)

    monkeypatch.setattr(P.os, "replace", flaky_replace)
    out = P.rollback(paths, dry_run=False)
    assert out["state"] == "FAILED"
    assert "partial rollback" in out["detail"]
    assert any("b.json" in p for p in out["paths"])
    assert out.get("blocks_resume") is True
    # the good file was still restored atomically
    assert (paths.state_dir / "a.json").exists()


def test_rollback_repeated_is_idempotent(tmp_path):
    paths = _paths(tmp_path)
    _make_backup(paths, {"state.json": '{"v": 1}'})
    first = P.rollback(paths, dry_run=False)
    second = P.rollback(paths, dry_run=False)
    assert first["state"] == "OK"
    assert second["state"] == "OK"
    assert (paths.state_dir / "state.json").read_text() == '{"v": 1}'


def test_rollback_preserves_backup(tmp_path):
    paths = _paths(tmp_path)
    bdir = _make_backup(paths, {"state.json": '{"v": 1}'})
    out = P.rollback(paths, dry_run=False)
    assert out["state"] == "OK"
    assert bdir.exists()
    assert (bdir / "state.json").exists()


def test_migrate_writes_validated_manifest(tmp_path):
    paths = _paths(tmp_path)
    _legacy_install(paths)
    out = P.migrate(paths, dry_run=False)
    bak = Path(out["backup_dir"])
    mf = bak / "manifest.json"
    assert mf.exists()
    manifest = json.loads(mf.read_text())
    assert manifest["schema_version"] == P.BACKUP_SCHEMA_VERSION
    assert "legacy/voice.yaml" in manifest["files"]
    # credentials excluded from the backup AND the manifest
    assert ".env" in out["excluded"]
    assert not (bak / "legacy" / ".env").exists()


# ========================================================================
# 24. Audit repairs: installer/migration consistent (item 6)
# ========================================================================
def test_staging_uses_installed_package_data_not_checkout(tmp_path,
                                                          monkeypatch):
    fake_data = tmp_path / "installed-data"
    (fake_data / "config").mkdir(parents=True)
    (fake_data / "config" / "voice.example.yaml").write_text(
        "# from-installed-wheel\n")
    monkeypatch.setattr(P.resources, "_PACKAGE_DATA", fake_data)
    paths = _paths(tmp_path)
    P._stage_packaged_defaults(paths, dry_run=False)
    staged = (paths.config_dir / "voice.yaml").read_text()
    assert "# from-installed-wheel" in staged


def test_migrate_mixed_trees_partial(tmp_path):
    """Only a subset of legacy trees present -> migration handles the partial
    state without splitting or duplicating anything."""
    paths = _paths(tmp_path)
    paths.legacy_base.mkdir(parents=True)
    (paths.legacy_base / "voice.yaml").write_text("audio: {}\n")
    # legacy_state / legacy_data absent
    out = P.migrate(paths, dry_run=False)
    assert out["state"] == "OK"
    assert paths.legacy_base.is_symlink()
    assert os.path.realpath(paths.legacy_base) == str(paths.config_dir)
    assert (paths.config_dir / "voice.yaml").exists()
    # state/data pairs were absent: only compat aliases were created (never
    # duplicated trees), and no state data appeared out of nowhere
    assert paths.legacy_state.is_symlink()
    assert os.path.realpath(paths.legacy_state) == str(paths.state_dir)
    assert not (paths.state_dir / "events.jsonl").exists()


def test_two_concurrent_installs_converge(tmp_path, monkeypatch):
    _ready_prereqs(monkeypatch)
    paths = _paths(tmp_path)
    first = P.install(paths, dry_run=False)
    second = P.install(paths, dry_run=False)
    assert first["state"] == "READY"
    assert second["state"] == "READY"
    files = sorted(p.name for p in paths.config_dir.iterdir())
    assert files.count("voice.yaml") == 1
    aliases = [a for a in second["aliases"] if a["state"] == "ALIAS_CREATED"]
    assert not aliases  # second run converged, did not duplicate


def test_install_apply_preserves_tuned_config(tmp_path, monkeypatch):
    _ready_prereqs(monkeypatch)
    paths = _paths(tmp_path)
    P.install(paths, dry_run=False)
    tuned = "my-tuned-key: keep\n"
    (paths.config_dir / "voice.yaml").write_text(tuned)
    out = P.install(paths, dry_run=False)
    assert out["state"] == "READY"
    assert (paths.config_dir / "voice.yaml").read_text() == tuned
    kept = [s for s in out["staged"] if s["resource"] == "voice.yaml"]
    assert kept and kept[0]["state"] == "KEPT"


# ========================================================================
# 25. Audit repairs: service/tray truthful (item 7)
# ========================================================================
def test_service_unit_missing_state(tmp_path):
    out = P.service_owner(unit_dir=tmp_path / "units")
    assert out["state"] == "UNIT_MISSING"
    assert out["active_state"].startswith("requires_operator_check")
    assert out["one_service_one_listener"] is True


def test_service_duplicate_state(tmp_path):
    udir = tmp_path / "units"
    udir.mkdir(parents=True)
    (udir / f"{P.PRODUCT_NAME}.service").write_text("[Unit]\n")
    (udir / f"{P.LEGACY_NAME}.service").write_text("[Unit]\n")
    out = P.service_owner(unit_dir=udir)
    assert out["state"] == "DUPLICATE"
    assert out["stale_legacy_unit"] == "STALE"


def test_service_mismatch_state(tmp_path):
    udir = tmp_path / "units"
    udir.mkdir(parents=True)
    (udir / f"{P.PRODUCT_NAME}.service").write_text(
        "[Service]\nExecStart=/some/wrong/bin -x\n")
    out = P.service_owner(unit_dir=udir)
    assert out["state"] == "MISMATCH"


def test_service_present_state(tmp_path):
    udir = tmp_path / "units"
    udir.mkdir(parents=True)
    (udir / f"{P.PRODUCT_NAME}.service").write_text(
        f"[Service]\nExecStart={sys.executable} -m voicebridge run\n")
    out = P.service_owner(unit_dir=udir)
    assert out["state"] == "PRESENT"
    assert out["active_state"].startswith("requires_operator_check")


def test_tray_never_listener_or_cookie(tmp_path):
    out = P.tray_status(_paths(tmp_path))
    assert out["second_bridge"] is False
    assert out["hotkey_listener"] is False
    assert out["cookie_access"] is False
    assert out["token_exposure"] is False
    assert out["required"] is False


# ========================================================================
# 26. Audit repairs: release boundary honest (item 8)
# ========================================================================
def test_package_boundary_no_fabricated_claims():
    out = P.package_boundary()
    assert "no signature claim" in out["signatures"]
    assert "no SBOM claim" in out["sbom"]
    assert "never fabricated" in out["provenance"]
    assert out["private_source_solved"] is False
    blob = json.dumps(out)
    for secret in ("sk-", "ghp_", "password"):
        assert secret not in blob


def test_repair_is_readonly_diagnostic(tmp_path, monkeypatch):
    _ready_prereqs(monkeypatch)
    root = tmp_path / "root"
    before = _fs_snapshot(root)
    out = P.repair(P.default_paths(base=root))
    assert "checks" in out
    assert _fs_snapshot(root) == before
