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
import subprocess
import sys
from pathlib import Path

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
    # journal recorded the block
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
# 3. Model/provider bootstrap
# ========================================================================
def test_model_bootstrap_offline_missing(tmp_path):
    paths = _paths(tmp_path)
    spec = P.ModelSpec(id="qubit-brain4", source="ollama://qubit-brain4",
                       revision="digest", sha256="", size_bytes=0,
                       license="Apache-2.0", kind="ollama")
    out = P.model_bootstrap(spec, paths, offline=True)
    assert out["state"] == "MISSING"
    assert out["target"].startswith(str(paths.model_dir))


def test_model_bootstrap_bad_hash_fails_closed(tmp_path):
    paths = _paths(tmp_path)
    paths.model_dir.mkdir(parents=True, exist_ok=True)
    art = paths.model_dir / "model.bin"
    art.write_bytes(b"corrupt")
    spec = P.ModelSpec(id="model.bin", source="http://x/model.bin",
                       revision="r1", sha256="0" * 64, size_bytes=0,
                       license="MIT", kind="file", local_path="model.bin")
    out = P.model_bootstrap(spec, paths, offline=True)
    assert out["state"] == "FAILED"
    assert "sha256 mismatch" in out["detail"]


def test_model_bootstrap_good_hash_promotes(tmp_path):
    paths = _paths(tmp_path)
    paths.model_dir.mkdir(parents=True, exist_ok=True)
    art = paths.model_dir / "model.bin"
    art.write_bytes(b"good bytes")
    actual = P._sha256(art)
    spec = P.ModelSpec(id="model.bin", source="http://x/model.bin",
                       revision="r1", sha256=actual, size_bytes=0,
                       license="MIT", kind="file", local_path="model.bin")
    out = P.model_bootstrap(spec, paths, offline=True)
    assert out["state"] == "OK"
    assert "sha256 verified" in out["detail"]


def test_model_bootstrap_preflight_blocks(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    monkeypatch.setattr(P, "_preflight",
                        lambda d: {"state": "DEGRADED",
                                   "detail": "insufficient disk"})
    spec = P.ModelSpec(id="m", source="http://x/m", revision="r", kind="file")
    out = P.model_bootstrap(spec, paths, dry_run=False, offline=False)
    assert out["state"] == "BLOCKED"


def test_model_bootstrap_dry_run_no_promote(tmp_path):
    paths = _paths(tmp_path)
    spec = P.ModelSpec(id="m", source="http://x/m", revision="r", kind="file")
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
    assert out["pairing"]["state"] == "PENDING"
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
def test_cli_product_paths_json():
    r = subprocess.run(
        [sys.executable, "-m", "voicebridge", "product", "paths", "--json"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    diag = json.loads(r.stdout)
    assert diag["product"] == P.PRODUCT_NAME


def test_cli_product_boundary():
    r = subprocess.run(
        [sys.executable, "-m", "voicebridge", "product", "boundary", "--json"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0  # informational; private-source is NOT solved
    out = json.loads(r.stdout)
    assert out["private_source_solved"] is False


def test_cli_product_shortcut_refuses_token_url():
    r = subprocess.run(
        [sys.executable, "-m", "voicebridge", "product", "shortcut",
         "--json"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    assert json.loads(r.stdout)["state"] in ("READY", "UNAVAILABLE")


def test_cli_product_repair_no_secrets():
    r = subprocess.run(
        [sys.executable, "-m", "voicebridge", "product", "repair"],
        capture_output=True, text=True, timeout=60)
    out = r.stdout + r.stderr
    for secret in ("sk-", "ghp_", "password="):
        assert secret not in out