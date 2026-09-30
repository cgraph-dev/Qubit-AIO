"""Wave 21 — update-lifecycle resume idempotency + lock hand-off ownership.

Two defects in the post-pull hand-off are fixed here and proven behaviorally:

* **DEFECT 1 — resume re-pulled and re-snapshotted.** The hand-off child is
  launched with ``QUBIT_UPDATE_HANDOFF=1`` / ``QUBIT_UPDATE_RESUME=<token>``.
  ``full_apply`` used to re-run the dirty check, snapshot, and pull, so the
  child pulled a second time on the freshly-pulled tree (old/new code mixed).
  On resume it must skip steps 6/7/8 entirely and run only the post-pull steps.

* **DEFECT 2 — lock hand-off race.** The parent spawned the child and then
  released the marker, so a third updater could start concurrently. The parent
  now transfers the marker to the child pid (its own ``release`` becomes a
  no-op) and a resume child acquires real ownership of that marker; a foreign
  live holder is still a typed ``UPDATE_LOCK_BUSY`` refusal.

Every test uses injected runners/spawners and disposable ``tmp_path`` dirs; no
git, no network, no live service, no ``~/.hermes``.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

A = "a" * 40
B = "b" * 40


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


def _fake_repo(tmp_path: Path) -> Path:
    """A throwaway checkout: only ``.git`` presence matters for method detection."""
    repo = tmp_path / "install"
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    return repo


def _cfg(repo: Path, tmp_path: Path) -> _Cfg:
    """Config pinned entirely inside *tmp_path* (lock, backup, receipts)."""
    return _Cfg({"update": {
        "repo_root": str(repo),
        "remote": "origin",
        "branch": "main",
        "lock_dir": str(tmp_path / "state"),
        "backup_dir": str(tmp_path / "backups"),
        "receipt_dir": str(tmp_path / "receipts"),
    }})


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Force the single e-stop authority clear and strip ambient update flags."""
    from voicebridge import update_manager as UM

    monkeypatch.setattr(UM, "_estop", lambda _cfg: False)
    for name in ("QUBIT_UPDATE_DISABLED", "QUBIT_CONTAINER", "QUBIT_IMMUTABLE",
                 "QUBIT_UPDATE_HANDOFF", "QUBIT_UPDATE_RESUME",
                 "QUBIT_UPDATE_BACKUP_DIR", "QUBIT_UPDATE_PRE_REVISION",
                 "QUBIT_UPDATE_NEW_REVISION", "QUBIT_UPDATE_HANDOFF_PID"):
        monkeypatch.delenv(name, raising=False)


def _record_post_steps(monkeypatch) -> list:
    """Replace the post-pull steps with recorders; return the ordered call log."""
    from voicebridge import update_manager as UM
    from voicebridge import update_config_migration as UCM
    from voicebridge import update_validation as UV

    calls: list = []

    def _rec(name):
        def _f(*_a, **_k):
            calls.append(name)
            return {"ok": True, "state": "OK", "code": ""}
        return _f

    monkeypatch.setattr(UM, "sync_dependencies", _rec("dependencies"))
    monkeypatch.setattr(UCM, "migrate_config", _rec("config_migration"))
    monkeypatch.setattr(UV, "validate_installation", _rec("validation"))
    monkeypatch.setattr(UM, "_restart_service", _rec("service_restart"))
    monkeypatch.setattr(UM, "_health_check", _rec("health"))
    monkeypatch.setattr(UM, "_audit", lambda *_a, **_k: None)
    return calls


def _no_git(argv, timeout):  # pragma: no cover - must never run
    raise AssertionError(f"no git command may run here: {argv}")


# ---------------------------------------------------------------------------
# DEFECT 1 — resume skips dirty-check + snapshot + pull, runs the rest
# ---------------------------------------------------------------------------
def test_resume_skips_dirty_snapshot_and_pull(tmp_path, monkeypatch):
    from voicebridge import update_manager as UM
    from voicebridge import update_handoff as UH

    repo = _fake_repo(tmp_path)
    cfg = _cfg(repo, tmp_path)

    monkeypatch.setenv(UH.RESUME_ENV, "resume-token")
    monkeypatch.setenv(UH.BACKUP_DIR_ENV, str(tmp_path / "parent-backup"))
    monkeypatch.setenv(UH.PRE_REVISION_ENV, A)
    monkeypatch.setenv(UH.NEW_REVISION_ENV, B)

    def _boom(*_a, **_k):
        raise AssertionError("resume must not run dirty-check / snapshot / pull")

    monkeypatch.setattr(UM, "_is_clean", _boom)
    monkeypatch.setattr(UM, "snapshot", _boom)
    monkeypatch.setattr(UM, "apply", _boom)

    calls = _record_post_steps(monkeypatch)

    out = UM.full_apply(cfg, confirm=True, runner=_no_git)

    assert out["ok"] is True, out
    assert out["state"] == "OK", out
    assert out["resumed"] is True
    assert out["pre_revision"] == A and out["post_revision"] == B
    assert out["previous_revision"] == A and out["new_revision"] == B
    assert out["backup_dir"] == str(tmp_path / "parent-backup")
    assert calls == ["dependencies", "config_migration", "validation",
                     "service_restart", "health"], calls


def test_non_resume_still_runs_the_pull_then_handoff(tmp_path, monkeypatch):
    """Regression guard: the normal (non-resume) path is unchanged."""
    from voicebridge import update_manager as UM
    from voicebridge import update_handoff as UH

    repo = _fake_repo(tmp_path)
    cfg = _cfg(repo, tmp_path)

    monkeypatch.setattr(UM, "_is_clean", lambda _e, _r: (True, ""))
    monkeypatch.setattr(UM, "snapshot", lambda *_a, **_k: {
        "ok": True, "backup_dir": str(tmp_path / "backups" / "snap"),
        "pre_revision": A})
    monkeypatch.setattr(UM, "apply", lambda *_a, **_k: {
        "ok": True, "state": "OK", "code": "", "pre_revision": A,
        "post_revision": B, "remote": "origin", "branch": "main",
        "changed": True, "remote_verified": True})
    monkeypatch.setattr(UH, "_real_control_disabled", lambda: False)
    monkeypatch.setattr(UH, "spawn_handoff", lambda _argv, _env: os.getpid())
    monkeypatch.setattr(UM, "_audit", lambda *_a, **_k: None)

    out = UM.full_apply(cfg, confirm=True, runner=_no_git)
    assert out["state"] == "HANDOFF", out
    assert out.get("resumed") is not True
    assert out["handoff_result"]["spawned"] is True


# ---------------------------------------------------------------------------
# DEFECT 2 — clean ownership transfer on spawn
# ---------------------------------------------------------------------------
def test_parent_transfers_lock_marker_to_spawned_child(tmp_path, monkeypatch):
    from voicebridge import update_manager as UM
    from voicebridge import update_handoff as UH
    from voicebridge import update_lock as UL

    repo = _fake_repo(tmp_path)
    backup = tmp_path / "backups"
    cfg = _cfg(repo, tmp_path)

    monkeypatch.setattr(UM, "_is_clean", lambda _e, _r: (True, ""))
    monkeypatch.setattr(UM, "snapshot", lambda *_a, **_k: {
        "ok": True, "backup_dir": str(backup), "pre_revision": A})
    monkeypatch.setattr(UM, "apply", lambda *_a, **_k: {
        "ok": True, "state": "OK", "code": "", "pre_revision": A,
        "post_revision": B, "remote": "origin", "branch": "main",
        "changed": True, "remote_verified": True})
    monkeypatch.setattr(UM, "_audit", lambda *_a, **_k: None)

    # The hand-off spawner seam: fail the test guard so the fake is used, and
    # return a distinct child pid.
    monkeypatch.setattr(UH, "_real_control_disabled", lambda: False)
    child_pid = os.getpid() + 1000
    seen = {}

    def _fake_spawn(argv, env):
        seen["argv"] = list(argv)
        seen["env"] = dict(env)
        return child_pid

    monkeypatch.setattr(UH, "spawn_handoff", _fake_spawn)

    out = UM.full_apply(cfg, confirm=True, runner=_no_git)

    assert out["state"] == "HANDOFF", out
    assert out["lock_transfer"] == "ok", out
    assert out["handoff_result"]["spawned"] is True
    assert out["handoff_result"]["pid"] == child_pid

    # The parent's release() must NOT delete the marker it handed over.
    marker = UL.lock_path(cfg)
    assert marker.is_file(), "parent release deleted the transferred marker"
    assert marker.read_text(encoding="utf-8-sig").splitlines()[0].strip() == \
        str(child_pid)

    # The parent's backup/revision hand-off state travelled to the child.
    assert seen["env"][UH.BACKUP_DIR_ENV] == str(backup)
    assert seen["env"][UH.PRE_REVISION_ENV] == A
    assert seen["env"][UH.NEW_REVISION_ENV] == B
    assert seen["env"][UH.RESUME_ENV]  # token present, not in argv
    assert seen["env"][UH.RESUME_ENV] not in seen["argv"]


def test_unknown_child_pid_records_failed_transfer_and_releases(tmp_path,
                                                               monkeypatch):
    """A spawner that reports no pid must not drop the lock silently."""
    from voicebridge import update_manager as UM
    from voicebridge import update_handoff as UH
    from voicebridge import update_lock as UL

    repo = _fake_repo(tmp_path)
    cfg = _cfg(repo, tmp_path)

    monkeypatch.setattr(UM, "_is_clean", lambda _e, _r: (True, ""))
    monkeypatch.setattr(UM, "snapshot", lambda *_a, **_k: {
        "ok": True, "backup_dir": str(tmp_path / "backups"), "pre_revision": A})
    monkeypatch.setattr(UM, "apply", lambda *_a, **_k: {
        "ok": True, "state": "OK", "code": "", "pre_revision": A,
        "post_revision": B, "changed": True, "remote_verified": True})
    monkeypatch.setattr(UM, "_audit", lambda *_a, **_k: None)
    monkeypatch.setattr(UH, "_real_control_disabled", lambda: False)
    monkeypatch.setattr(UH, "spawn_handoff", lambda _argv, _env: None)

    out = UM.full_apply(cfg, confirm=True, runner=_no_git)

    assert out["state"] == "HANDOFF", out
    assert out["lock_transfer"] == "failed"
    # The parent kept ownership, so its release cleaned the marker up.
    assert not UL.lock_path(cfg).exists()


# ---------------------------------------------------------------------------
# resume child lock ownership / foreign holder
# ---------------------------------------------------------------------------
def test_resume_child_acquires_and_releases_own_marker(tmp_path, monkeypatch):
    from voicebridge import update_manager as UM
    from voicebridge import update_handoff as UH
    from voicebridge import update_lock as UL

    repo = _fake_repo(tmp_path)
    cfg = _cfg(repo, tmp_path)
    monkeypatch.setenv(UH.RESUME_ENV, "resume-token")
    monkeypatch.setenv(UH.PRE_REVISION_ENV, A)
    monkeypatch.setenv(UH.NEW_REVISION_ENV, B)

    # The parent transferred the marker to this process's pid.
    marker = UL.lock_path(cfg)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{os.getpid()}\n{int(time.time())}\n", encoding="utf-8")

    calls = _record_post_steps(monkeypatch)
    out = UM.full_apply(cfg, confirm=True, runner=_no_git)

    assert out["ok"] is True, out
    assert out["resumed"] is True
    assert out["lock_acquired"] is True
    assert calls, "the post-pull steps must still run on resume"
    assert not marker.exists(), "the resume child must release its own marker"


def test_resume_foreign_live_holder_is_typed_busy(tmp_path, monkeypatch):
    from voicebridge import update_manager as UM
    from voicebridge import update_handoff as UH
    from voicebridge import update_lock as UL

    repo = _fake_repo(tmp_path)
    cfg = _cfg(repo, tmp_path)
    monkeypatch.setenv(UH.RESUME_ENV, "resume-token")

    foreign = subprocess.Popen([sys.executable, "-c",
                                "import time; time.sleep(60)"])
    try:
        marker = UL.lock_path(cfg)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{foreign.pid}\n{int(time.time())}\n",
                          encoding="utf-8")

        calls = _record_post_steps(monkeypatch)
        out = UM.full_apply(cfg, confirm=True, runner=_no_git)

        assert out["ok"] is False
        assert out["state"] == "CONFLICT"
        assert out["code"] == UM.LOCK_BUSY_CODE
        assert out["lock_holder"] == foreign.pid
        assert calls == [], "a busy resume must run no post-pull step"
        # The foreign holder's marker is untouched.
        assert marker.is_file()
        assert marker.read_text(
            encoding="utf-8-sig").splitlines()[0].strip() == str(foreign.pid)
    finally:
        foreign.terminate()
        try:
            foreign.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            foreign.kill()
            foreign.wait(timeout=10)


# ---------------------------------------------------------------------------
# hand-off environment / argv contract
# ---------------------------------------------------------------------------
def test_handoff_env_carries_state_and_token_stays_out_of_argv(tmp_path,
                                                               monkeypatch):
    from voicebridge import update_handoff as UH

    token = "resume-token-xyz"
    env = UH.handoff_env(token, repo_root=tmp_path,
                         backup_dir="/tmp/parent-backup",
                         pre_revision=A, new_revision=B)

    assert env[UH.HANDOFF_ENV] == "1"
    assert env[UH.RESUME_ENV] == token
    assert env[UH.BACKUP_DIR_ENV] == "/tmp/parent-backup"
    assert env[UH.PRE_REVISION_ENV] == A
    assert env[UH.NEW_REVISION_ENV] == B
    assert env[UH.HANDOFF_PID_ENV] == str(os.getpid())

    argv = UH.build_handoff_argv(object(), resume_token=token)
    assert "--apply" in argv
    assert token not in argv
    assert not any(token in str(a) for a in argv), argv
    # ``--apply`` IS the confirmation (CLI calls full_apply(confirm=True)); the
    # token is the only secret and it never appears in a process listing.
    assert "--json" in argv

    # The resume accessors read the same keys back from the environment.
    monkeypatch.setenv(UH.BACKUP_DIR_ENV, "/tmp/from-env")
    monkeypatch.setenv(UH.PRE_REVISION_ENV, "c" * 40)
    monkeypatch.setenv(UH.NEW_REVISION_ENV, "d" * 40)
    assert UH.resume_backup_dir() == "/tmp/from-env"
    assert UH.resume_pre_revision() == "c" * 40
    assert UH.resume_new_revision() == "d" * 40


def test_transfer_to_is_owner_guarded(tmp_path):
    """Only the live owner may transfer; a bad pid never drops the lock."""
    from voicebridge import update_lock as UL

    marker = tmp_path / "update.lock"
    lock = UL.UpdateLock(path=marker)
    assert lock.acquire() is True

    # Unknown/bad pids must not transfer and must not clear ownership.
    assert lock.transfer_to(None) is False
    assert lock.transfer_to(0) is False
    assert lock.acquired is True
    assert marker.exists()

    # A real pid transfers the marker and makes release a no-op.
    assert lock.transfer_to(4321) is True
    assert lock.acquired is False
    lock.release()
    assert marker.is_file()
    assert marker.read_text().splitlines()[0].strip() == "4321"

    # A non-owner lock cannot steal the marker.
    other = UL.UpdateLock(path=marker)
    assert other.transfer_to(9999) is False
    assert marker.read_text().splitlines()[0].strip() == "4321"
