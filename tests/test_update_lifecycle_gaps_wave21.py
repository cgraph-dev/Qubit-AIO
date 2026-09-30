"""Wave 21 — lifecycle-gap tests for the ``qubit update`` full apply path.

Test-only; no product code is touched. Each test builds its own disposable git
world under ``tmp_path`` (a local BARE remote, a ``source`` repo, and an
``install`` clone one commit behind) and injects its executors, so nothing here
touches the live checkout, the network, ``~/.hermes``, the live service, or the
gateway daemon.

Covered gaps (the branches ``test_update_lifecycle_wave20.py`` does not):

1. dependency-sync failure  => automatic rollback (HEAD restored)
2. validation/import failure => automatic rollback (HEAD restored)
3. config migration: idempotent, timestamped ``.bak-*`` before a write, and a
   no-explicit-path test run is skipped without touching the canonical config
4. service restart: a ``hermes-*`` unit is a typed ``UPDATE_SERVICE_FORBIDDEN``
   refusal; an owned ``qubit-*`` unit under the test guard is an explicit
   ``SKIPPED`` (never a fabricated success)
5. health verification: a not-running probe fails with ``UPDATE_HEALTH_FAILED``
   and triggers rollback in a full run; a healthy probe yields ``ok``
6. successful-run receipt schema + no secret from injected runner output
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Fake credential material the injected runner emits; must never reach a receipt.
SECRET_URL = "https://x-access-token:ghp_ABCdef0123456789ABCdef@github.com/o/r.git"
SECRET_TOKEN = "token=supersecretvalue123"
SECRET_SK = "sk-" + "a" * 24
SECRET_BLOB = f"fatal: auth failed for '{SECRET_URL}'\n{SECRET_TOKEN}\n{SECRET_SK}\n"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
class _Cfg:
    """Nested-dict config implementing the ``.get(*path, default=)`` API."""

    def __init__(self, data=None):
        self._d = data or {}

    def get(self, *path, default=None):
        node = self._d
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def _git_env(tmp_path: Path) -> dict:
    """A hermetic git environment: local identity, no user/system config."""
    empty = tmp_path / "gitconfig-empty"
    empty.write_text("")
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "Qubit Wave21",
        "GIT_AUTHOR_EMAIL": "wave21@example.invalid",
        "GIT_COMMITTER_NAME": "Qubit Wave21",
        "GIT_COMMITTER_EMAIL": "wave21@example.invalid",
        "GIT_CONFIG_GLOBAL": str(empty),
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def _git(args, cwd: Path, env: dict) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, (
        f"git {' '.join(args)} failed ({proc.returncode}): "
        f"{proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


def _head(repo: Path, env: dict) -> str:
    return _git(["rev-parse", "HEAD"], repo, env)


def _commit(repo: Path, env: dict, text: str, message: str) -> str:
    (repo / "VERSION").write_text(text)
    # A pyproject.toml keeps the dependency-sync step from short-circuiting to a
    # skip, so the deps branch is genuinely exercised.
    (repo / "pyproject.toml").write_text(
        "[project]\nname = \"qubit-temp\"\nversion = \"0.0.0\"\n")
    _git(["add", "-A"], repo, env)
    _git(["commit", "-m", message], repo, env)
    return _head(repo, env)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Hermetic HOME/XDG so no resolver can reach a live path."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("QUBIT_HOME", str(home / "qhome"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "xdg-state"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "xdg-data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / "xdg-cache"))
    monkeypatch.delenv("QUBIT_UPDATE_DISABLED", raising=False)
    from voicebridge import update_manager as UM

    # The single e-stop authority is forced clear; it is exercised elsewhere.
    monkeypatch.setattr(UM, "_estop", lambda _cfg: False)


@pytest.fixture
def install(tmp_path):
    """A temp install one commit behind its bare remote, plus a source repo."""
    env = _git_env(tmp_path)
    bare = tmp_path / "remote.git"
    source = tmp_path / "source"
    checkout = tmp_path / "install"

    _git(["init", "--bare", "-b", "main", str(bare)], tmp_path, env)
    _git(["init", "-b", "main", str(source)], tmp_path, env)
    first = _commit(source, env, "1\n", "first")
    _git(["remote", "add", "origin", str(bare)], source, env)
    _git(["push", "-u", "origin", "main"], source, env)

    _git(["clone", str(bare), str(checkout)], tmp_path, env)
    assert _head(checkout, env) == first

    second = _commit(source, env, "2\n", "second")
    _git(["push", "origin", "main"], source, env)

    assert first != second
    assert _head(checkout, env) == first, "install must still be one behind"
    assert _git(["status", "--porcelain"], checkout, env) == ""
    return SimpleNamespace(checkout=checkout, source=source, bare=bare, env=env,
                           first=first, second=second)


def _cfg(install: SimpleNamespace, tmp_path: Path) -> _Cfg:
    return _Cfg({"update": {
        "repo_root": str(install.checkout),
        "remote": "origin",
        "branch": "main",
        "backup_dir": str(tmp_path / "backups"),
        "lock_dir": str(tmp_path / "lock"),
        "handoff": False,
    }})


# ---------------------------------------------------------------------------
# injected executors
# ---------------------------------------------------------------------------
def _real_git(argv, env) -> tuple:
    proc = subprocess.run(list(argv), capture_output=True, text=True,
                          timeout=30, env=env)
    return proc.returncode, proc.stdout, proc.stderr


class _GitRunner:
    """Really executes git in the temp checkout; records argv."""

    def __init__(self, env):
        self.env = env
        self.calls = []

    def __call__(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        return _real_git(argv, self.env)


class _DepsFailRunner:
    """Succeeds at nothing relevant except: git is separate, and it fails deps.

    The injected ``git`` performs every git op, so this runner is only reached
    by the dependency install; it fails there with a credential-bearing error.
    """

    def __init__(self):
        self.calls = []

    def __call__(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        if argv and argv[0] == sys.executable and "-c" in argv:
            return 0, "", ""            # import probe (never reached in test 1)
        return 1, "", f"pip failed: {SECRET_BLOB}"


class _ValidationFailRunner:
    """Git runs for real; deps succeed; every import probe exits non-zero."""

    def __init__(self, env):
        self.env = env
        self.calls = []

    def __call__(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        if argv and argv[0] == "git":
            return _real_git(argv, self.env)
        if argv and argv[0] == sys.executable and "-c" in argv:
            return 1, "", "ImportError: simulated post-update import failure"
        return 0, "", ""                # dependency sync succeeds


class _HappyRunner:
    """Git runs for real; deps + import probes succeed; optional secret in pull.

    Emitting the credential on the ``git pull`` stdout is what makes the
    redaction assertion in the receipt test meaningful.
    """

    def __init__(self, env, *, secret: str = ""):
        self.env = env
        self.secret = secret
        self.calls = []

    def __call__(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        if argv and argv[0] == "git":
            rc, out, err = _real_git(argv, self.env)
            if self.secret and argv[3:4] == ["pull"]:
                out = out + self.secret
            return rc, out, err
        return 0, "", ""                # deps + import probes succeed


# ===========================================================================
# 1. dependency failure => automatic rollback
# ===========================================================================
def test_dependency_failure_triggers_automatic_rollback(install, tmp_path):
    from voicebridge import update_manager as UM

    git = _GitRunner(install.env)
    deps = _DepsFailRunner()
    out = UM.full_apply(_cfg(install, tmp_path), confirm=True,
                        runner=deps, git=git)

    assert out["ok"] is False, out
    assert out["failure_step"] == "dependencies", out
    assert out["rolled_back"] is True, out
    assert out["dependency_result"]["ok"] is False
    assert out["dependency_result"]["code"] == UM.DEPENDENCY_FAILED_CODE
    # The real checkout is back at the pre-apply revision.
    assert _head(install.checkout, install.env) == install.first
    assert (install.checkout / "VERSION").read_text() == "1\n"

    # A credential in the failing deps output never reaches the receipt.
    blob = json.dumps(out)
    assert "ghp_ABCdef0123456789ABCdef" not in blob
    assert "supersecretvalue123" not in blob
    assert SECRET_SK not in blob


# ===========================================================================
# 2. validation/import failure => automatic rollback
# ===========================================================================
def test_validation_failure_triggers_automatic_rollback(install, tmp_path):
    from voicebridge import update_manager as UM

    runner = _ValidationFailRunner(install.env)
    out = UM.full_apply(_cfg(install, tmp_path), confirm=True, runner=runner)

    assert out["ok"] is False, out
    assert out["failure_step"] == "validation", out
    assert out["rolled_back"] is True, out
    assert out["validation_result"]["ok"] is False
    assert out["validation_result"]["code"] == UM.VALIDATION_FAILED_CODE
    assert out["dependency_result"]["ok"] is True, out
    assert _head(install.checkout, install.env) == install.first
    assert (install.checkout / "VERSION").read_text() == "1\n"


# ===========================================================================
# 3. config migration
# ===========================================================================
def test_config_migration_is_idempotent_and_backed_up(tmp_path):
    from voicebridge import update_config_migration as UCM

    cfg = _Cfg({"update": {}})
    path = tmp_path / "voice.yaml"
    original = "foo: 1\n"
    path.write_text(original)

    first = UCM.migrate_config(cfg, override=path)
    assert first["ok"] is True, first
    assert first["migrated"] is True
    assert first["changed_keys"], first
    assert UCM.SCHEMA_KEY in first["changed_keys"]

    backup = Path(first["backup"])
    assert backup.is_file(), first
    assert ".bak-" in backup.name
    assert backup.read_text() == original, "backup must predate the change"
    migrated_text = path.read_text()
    assert migrated_text != original

    # Second run is a no-op: no write, no new backup.
    before_mtime = path.stat().st_mtime_ns
    second = UCM.migrate_config(cfg, override=path)
    assert second["ok"] is True, second
    assert second["migrated"] is False
    assert second["backup"] is None
    assert path.read_text() == migrated_text
    assert path.stat().st_mtime_ns == before_mtime


def test_config_migration_skips_canonical_config_under_test(monkeypatch,
                                                            tmp_path):
    from voicebridge import update_config_migration as UCM
    from voicebridge import product as P

    canonical = Path(P.default_paths().config_dir) / "voice.yaml"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    sentinel = "sentinel: do-not-touch\n"
    canonical.write_text(sentinel)

    # No explicit config path anywhere on cfg, and we are in a test process.
    cfg = _Cfg({"update": {}})
    out = UCM.migrate_config(cfg)

    assert out["ok"] is True, out
    assert out["skipped"] is True, out
    assert out["migrated"] is False
    assert "test environment" in out["reason"]
    assert canonical.read_text() == sentinel, "canonical config was mutated"
    assert not list(canonical.parent.glob(canonical.name + ".bak-*"))


# ===========================================================================
# 4. service restart
# ===========================================================================
def test_service_restart_forbids_foreign_unit_and_skips_owned_unit(monkeypatch):
    from voicebridge import update_manager as UM

    forbidden = UM._restart_service(
        _Cfg({"update": {"service_unit": "hermes-dashboard.service"}}),
        actor="test:wave21")
    assert forbidden["ok"] is False
    assert forbidden["code"] == UM.SERVICE_FORBIDDEN_CODE
    assert forbidden["state"] == "REFUSED"
    assert forbidden["unit"] == "hermes-dashboard.service"
    assert "non-Qubit unit" in forbidden["detail"]

    monkeypatch.setenv("QUBIT_UPDATE_DISABLED", "1")
    skipped = UM._restart_service(
        _Cfg({"update": {"service_unit": "qubit-voicebridge.service"}}),
        actor="test:wave21")
    assert skipped["ok"] is True
    assert skipped["state"] == "SKIPPED", skipped
    assert skipped["skipped"] is True
    assert skipped["state"] != "OK"
    assert skipped["unit"] == "qubit-voicebridge.service"


# ===========================================================================
# 5. health verification
# ===========================================================================
def test_health_check_fails_when_service_not_running(monkeypatch):
    from voicebridge import update_manager as UM
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(UM, "_under_test", lambda: False)
    monkeypatch.setattr(GS, "status", lambda cfg, runner=None, probe=None: {
        "running": False, "port_in_use": False, "health": "DOWN",
        "service_unit": "qubit-gateway.service", "detail": "unit is inactive"})

    out = UM._health_check(_Cfg({}), pull={"remote_verified": True})
    assert out["ok"] is False, out
    assert out["code"] == UM.HEALTH_FAILED_CODE
    assert out["state"] == "FAILED"
    assert out["version_match"] is True


def test_health_check_passes_when_service_running(monkeypatch):
    from voicebridge import update_manager as UM
    from voicebridge import gateway_supervisor as GS

    monkeypatch.setattr(UM, "_under_test", lambda: False)
    monkeypatch.setattr(GS, "status", lambda cfg, runner=None, probe=None: {
        "running": True, "port_in_use": True, "health": "OK",
        "service_unit": "qubit-gateway.service", "detail": ""})

    out = UM._health_check(_Cfg({}), pull={"remote_verified": True})
    assert out["ok"] is True, out
    assert out["code"] == ""
    assert out["state"] == "OK"
    assert out["listener"] is True


def test_full_apply_health_failure_triggers_rollback(install, tmp_path,
                                                     monkeypatch):
    from voicebridge import update_manager as UM
    from voicebridge import gateway_supervisor as GS

    # Flip the internal guard off so the real health/restart seams are reached,
    # then inject both downstream seams so no live service is touched.
    monkeypatch.setattr(UM, "_under_test", lambda: False)
    monkeypatch.setattr(GS, "control", lambda *a, **kw: {
        "ok": True, "state": "OK", "code": ""})
    monkeypatch.setattr(GS, "status", lambda cfg, runner=None, probe=None: {
        "running": False, "port_in_use": False, "health": "DOWN",
        "service_unit": "qubit-gateway.service", "detail": "unit is inactive"})

    runner = _HappyRunner(install.env)
    out = UM.full_apply(_cfg(install, tmp_path), confirm=True, runner=runner)

    assert out["ok"] is False, out
    assert out["failure_step"] == "health", out
    assert out["rolled_back"] is True, out
    assert out["health_result"]["code"] == UM.HEALTH_FAILED_CODE
    assert out["service_restart_result"]["ok"] is True
    assert _head(install.checkout, install.env) == install.first
    assert (install.checkout / "VERSION").read_text() == "1\n"


# ===========================================================================
# 6. receipt schema on a successful full run + no secret leakage
# ===========================================================================
def test_successful_full_apply_receipt_schema_and_no_secret(install, tmp_path):
    from voicebridge import update_manager as UM
    from voicebridge import update_receipt as UR

    runner = _HappyRunner(install.env, secret=SECRET_BLOB)
    out = UM.full_apply(_cfg(install, tmp_path), confirm=True, runner=runner)

    assert out["ok"] is True, out
    assert out["state"] == "OK"
    assert out["outcome"] == "success"
    assert out["previous_revision"] == install.first
    assert out["new_revision"] == install.second
    assert out["remote"] == "origin" and out["branch"] == "main"
    assert out["backup_dir"] and Path(out["backup_dir"]).is_dir()

    for key in ("previous_revision", "new_revision", "remote", "branch",
                "backup_dir", "dependency_result", "config_migration_result",
                "validation_result", "service_restart_result", "health_result",
                "rolled_back"):
        assert key in out, f"receipt missing {key!r}: {out}"
    assert out["rolled_back"] is False
    # A rollback route must still be recorded/confirmable on success.
    assert (out.get("rollback_info") is not None
            or out.get("rollback") is not None
            or out.get("rollback_plan"))
    assert out["dependency_result"]["ok"] is True
    assert out["validation_result"]["ok"] is True

    blob = json.dumps(out)
    assert "ghp_ABCdef0123456789ABCdef" not in blob
    assert "supersecretvalue123" not in blob
    assert SECRET_SK not in blob

    # The persisted receipt is redacted too.
    persisted = UR.latest_receipt(_cfg(install, tmp_path))
    assert persisted is not None
    disk = json.dumps(persisted)
    assert "ghp_ABCdef0123456789ABCdef" not in disk
    assert "supersecretvalue123" not in disk
    assert SECRET_SK not in disk
