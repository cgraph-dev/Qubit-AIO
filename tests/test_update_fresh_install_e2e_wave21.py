"""Fresh-install / update / rollback end-to-end proof (wave 21).

Hermetic, disposable, host-safe: every path resolves under ``tmp_path``
(``HOME``/``QUBIT_HOME``/``XDG_*``), the update target is a throwaway git
checkout cloned from a throwaway bare remote, and the host service steps are
disabled by the updater's fail-closed guard (``QUBIT_UPDATE_DISABLED=1``).
The git/network operations are driven by an injected runner over real local
git, never the live checkout, the real reference home, or any service.

What this encodes:

* a clean install is detected as an updatable ``git-checkout`` and the remote
  ahead revision is reported truthfully (never fabricated);
* ``full_apply`` fast-forwards the checkout, records a receipt, runs the
  dependency + config-migration steps, and leaves the user's ``QUBIT_HOME``
  state (a sentinel) untouched;
* ``rollback`` restores the exact pre-apply revision and writes a
  pre-rollback snapshot;
* the real ``~/.hermes`` reference home is never required — path resolution
  is confined to the temp ``HOME``;
* a dirty worktree and a missing confirmation are refused without mutation.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from voicebridge.config import Config
from voicebridge import update_manager as UM


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return proc.stdout.strip()


def _run(argv, timeout):
    """Real executor injected as the updater's ``runner`` (bypasses the guard)."""
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True,
                              timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"{argv[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "", "command timed out"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """An isolated HOME/QUBIT_HOME/XDG tree plus a disposable origin + checkout.

    Returns the checkout path, its v1/v2 revisions, the config object, the
    backup root, and the ``QUBIT_HOME`` sentinel path.
    """
    home = tmp_path / "home"
    qubit_home = tmp_path / "qubit-home"
    for key, value in (
        ("HOME", home),
        ("QUBIT_HOME", qubit_home),
        ("XDG_CONFIG_HOME", tmp_path / "xdg-config"),
        ("XDG_STATE_HOME", tmp_path / "xdg-state"),
        ("XDG_DATA_HOME", tmp_path / "xdg-data"),
        ("XDG_CACHE_HOME", tmp_path / "xdg-cache"),
    ):
        value.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(key, str(value))
    # Host-service steps must never run from a test.
    monkeypatch.setenv("QUBIT_UPDATE_DISABLED", "1")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Qubit E2E")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "e2e@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Qubit E2E")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "e2e@example.invalid")

    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    checkout = tmp_path / "checkout"
    bump = tmp_path / "bump"

    subprocess.run(["git", "init", "--bare", str(origin)], check=True,
                   capture_output=True)
    subprocess.run(["git", "init", "-b", "main", str(seed)], check=True,
                   capture_output=True)
    (seed / "VERSION").write_text("1\n")
    (seed / "pyproject.toml").write_text(
        "[project]\nname = \"qubit-e2e\"\nversion = \"0.0.1\"\n")
    (seed / "frontend/qubit-dist").mkdir(parents=True, exist_ok=True)
    (seed / "frontend/qubit-dist/index.html").write_text("<html/>\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "v1")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "origin", "main")
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    v1 = _git(seed, "rev-parse", "HEAD")

    subprocess.run(["git", "clone", "-b", "main", str(origin), str(checkout)],
                   check=True, capture_output=True)

    subprocess.run(["git", "clone", "-b", "main", str(origin), str(bump)],
                   check=True, capture_output=True)
    (bump / "VERSION").write_text("2\n")
    _git(bump, "add", "-A")
    _git(bump, "commit", "-m", "v2")
    _git(bump, "push", "origin", "main")
    v2 = _git(bump, "rev-parse", "HEAD")

    cfg_file = tmp_path / "xdg-config" / "qubit-voicebridge" / "voice.yaml"
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    deps_marker = tmp_path / "deps.ran"
    backup_dir = tmp_path / "backups"
    data = {
        "update": {
            "repo_root": str(checkout),
            "remote": "origin",
            "branch": "main",
            "backup_dir": str(backup_dir),
            "config_path": str(cfg_file),
            "deps_command": ["/bin/sh", "-c", f"printf deps-ok > {deps_marker}"],
            "skip_validation": True,
            "handoff": False,
        },
        "logging": {"level": "INFO", "jsonl": True,
                    "jsonl_path": str(tmp_path / "logs" / "events.jsonl")},
    }
    import yaml

    cfg_file.write_text(yaml.safe_dump(data, sort_keys=False))
    cfg = Config(data=data, path=cfg_file)

    sentinel = qubit_home / "sentinel.txt"
    sentinel.write_text("user-state-survives\n")

    return {
        "checkout": checkout, "origin": origin, "v1": v1, "v2": v2,
        "cfg": cfg, "backup_dir": backup_dir, "deps_marker": deps_marker,
        "sentinel": sentinel, "tmp_path": tmp_path,
        "qubit_home": qubit_home, "home": home,
    }


def test_check_reports_real_revision_and_remote_ahead(isolated):
    cfg = isolated["cfg"]
    out = UM.check(cfg, runner=_run)
    assert out["install_method"] == "git-checkout"
    assert out["current_revision"] == isolated["v1"]
    assert out["remote_revision"] == isolated["v2"]
    assert out["update_available"] is True
    assert out["state"] == "OK"


def test_detect_install_method_is_git_checkout(isolated):
    method = UM.detect_install_method(isolated["cfg"])
    assert method["method"] == "git-checkout"
    assert method["supported_in_place"] is True


def test_full_apply_fast_forwards_and_preserves_user_state(isolated):
    cfg = isolated["cfg"]
    full = UM.full_apply(cfg, confirm=True, source="origin", runner=_run,
                         actor="test:e2e")
    assert full["state"] == "OK", full
    assert full["outcome"] == "success"
    assert full["previous_revision"] == isolated["v1"]
    assert full["new_revision"] == isolated["v2"]
    assert full["changed"] is True
    assert _git(isolated["checkout"], "rev-parse", "HEAD") == isolated["v2"]

    # dependency + config migration ran; validation/service/health skipped.
    assert full["dependency_result"]["state"] == "OK"
    assert isolated["deps_marker"].exists()
    assert full["config_migration_result"]["migrated"] is True
    assert "qubit_config_schema" in isolated["cfg"].path.read_text()
    assert full["validation_result"]["state"] == "SKIPPED"
    assert "skip_validation" in full["validation_result"]["reason"]
    assert full["service_restart_result"]["state"] == "SKIPPED"
    assert full["health_result"]["state"] == "SKIPPED"

    # a receipt was written under the isolated backup root.
    receipts = UM.update_backup_root(cfg) / "receipts"
    assert receipts.is_dir()
    assert list(receipts.glob("receipt-*.json"))
    assert (receipts / "latest.json").is_file()

    # the QUBIT_HOME sentinel survived the update.
    assert isolated["sentinel"].read_text() == "user-state-survives\n"


def test_rollback_restores_pre_apply_revision(isolated):
    cfg = isolated["cfg"]
    UM.full_apply(cfg, confirm=True, source="origin", runner=_run,
                  actor="test:e2e")
    assert _git(isolated["checkout"], "rev-parse", "HEAD") == isolated["v2"]

    rb = UM.rollback(cfg, runner=_run, actor="test:e2e")
    assert rb["ok"] is True, rb
    assert rb["restored_revision"] == isolated["v1"]
    assert _git(isolated["checkout"], "rev-parse", "HEAD") == isolated["v1"]
    assert rb["pre_rollback_snapshot"]
    assert Path(rb["pre_rollback_snapshot"]).is_dir()
    assert isolated["sentinel"].read_text() == "user-state-survives\n"


def test_no_real_reference_home_is_required(isolated):
    """Path resolution is confined to the temp HOME; nothing reads ~/.hermes."""
    cfg = isolated["cfg"]
    full = UM.full_apply(cfg, confirm=True, source="origin", runner=_run,
                         actor="test:e2e")
    assert full["state"] == "OK"

    from voicebridge import qubit_runtime

    # The only "external default" the fork can compute is <temp HOME>/.hermes,
    # which cannot reach the operator's real reference home.
    external = qubit_runtime._external_default_home()
    assert str(external).startswith(str(isolated["home"]))
    assert Path.home() == isolated["home"]
    assert UM.repo_root(cfg) == isolated["checkout"].resolve()
    assert str(UM.update_backup_root(cfg)).startswith(str(isolated["tmp_path"]))

    # No artifact records a real reference-home path.
    receipts = UM.update_backup_root(cfg) / "receipts"
    for receipt in receipts.glob("*.json"):
        assert "/home/trick/.hermes" not in receipt.read_text()


def test_dirty_worktree_is_refused_without_mutation(isolated):
    cfg = isolated["cfg"]
    (isolated["checkout"] / "uncommitted.txt").write_text("do not lose me\n")
    full = UM.full_apply(cfg, confirm=True, source="origin", runner=_run,
                         actor="test:e2e")
    assert full["code"] == UM.WORKTREE_DIRTY_CODE
    assert full["state"] == "CONFLICT"
    assert _git(isolated["checkout"], "rev-parse", "HEAD") == isolated["v1"]
    assert isolated["sentinel"].read_text() == "user-state-survives\n"


def test_apply_without_confirm_is_refused(isolated):
    full = UM.full_apply(isolated["cfg"], confirm=False, source="origin",
                         runner=_run, actor="test:e2e")
    assert full["code"] == UM.CONFIRM_CODE
    assert _git(isolated["checkout"], "rev-parse", "HEAD") == isolated["v1"]


def test_lifecycle_guard_fails_closed_without_runner(isolated):
    """No injected runner + the guard set => apply is a typed no-op."""
    out = UM.apply(isolated["cfg"], confirm=True, source="origin")
    assert out["code"] == UM.UNAVAILABLE_CODE
    assert _git(isolated["checkout"], "rev-parse", "HEAD") == isolated["v1"]
