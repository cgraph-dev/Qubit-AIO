"""Wave 20 — disposable update-lifecycle matrix for ``voicebridge.update_manager``.

Every test here builds its *own* git world under ``tmp_path``: a local BARE
remote (``remote.git``), a ``source`` working repo, and an ``install`` clone that
is one commit behind. No network, no product checkout, no ``~/.hermes``.

The point is behavioral proof, not mocked-HTTP-200s: ``apply`` is driven through
an injected ``runner`` that really executes the command ``update_manager``
builds, bound to the temp repo, and the tests assert the on-disk revision moved.
Rows where real git cannot produce the state (a corrupt backup, an unreachable
remote path, a secret-in-stderr error) use a deterministic injected executor so
the refusal/redaction branch is exercised exactly.

Covered matrix
--------------
clean apply fast-forward · dirty-worktree refusal (byte-identical tree) ·
missing branch / missing remote typed DEGRADED (never "up to date") ·
unreachable remote path typed DEGRADED within timeout · confirm gate runs no
git · e-stop gate runs no git · rollback restores the recorded revision and is
reversible · rollback without backup typed refusal · backup manifest readable +
corrupt manifest typed BACKUP_INVALID · concurrent update lock (xfail: core
lock absent) · QUBIT_HOME sentinel preserved · secret redaction on the error
path.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Captured before any test patches HOME: the read-only reference home.
REAL_HOME = Path(os.path.expanduser("~")).resolve()

FORBIDDEN_VERBS = {"reset", "checkout", "stash", "clean", "rebase", "merge",
                   "rm", "filter-branch", "prune", "gc"}
FORBIDDEN_FLAGS = {"--force", "-f", "--hard", "--all", "--prune"}


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


class _RecordingRunner:
    """Injected ``runner``: records argv, then really executes it (temp repo)."""

    def __init__(self, env):
        self.env = env
        self.calls = []

    def __call__(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, env=self.env)
        return proc.returncode, proc.stdout, proc.stderr


def _git_env(tmp_path: Path) -> dict:
    """A hermetic git environment: local identity, no user/system config."""
    empty = tmp_path / "gitconfig-empty"
    empty.write_text("")
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "Qubit Wave20",
        "GIT_AUTHOR_EMAIL": "wave20@example.invalid",
        "GIT_COMMITTER_NAME": "Qubit Wave20",
        "GIT_COMMITTER_EMAIL": "wave20@example.invalid",
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
    _git(["add", "-A"], repo, env)
    _git(["commit", "-m", message], repo, env)
    return _head(repo, env)


def _tree_digest(root: Path) -> str:
    """Hash every working-tree file under *root* (``.git`` excluded)."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if ".git" in p.parts:
            continue
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            h.update(f"L {rel} {os.readlink(p)}\n".encode())
        elif p.is_dir():
            h.update(f"D {rel}\n".encode())
        else:
            h.update(f"F {rel}\n".encode())
            h.update(p.read_bytes())
    return h.hexdigest()


@pytest.fixture(autouse=True)
def _estop_clear(monkeypatch):
    """The single e-stop authority is forced clear; its refusal is tested here."""
    from voicebridge import update_manager as UM

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
    _commit(source, env, "1\n", "first")
    _git(["remote", "add", "origin", str(bare)], source, env)
    _git(["push", "-u", "origin", "main"], source, env)

    _git(["clone", str(bare), str(checkout)], tmp_path, env)
    first = _head(checkout, env)

    second = _commit(source, env, "2\n", "second")
    _git(["push", "origin", "main"], source, env)

    assert first != second
    assert _head(checkout, env) == first, "install must still be one behind"
    assert _git(["status", "--porcelain"], checkout, env) == ""
    return SimpleNamespace(checkout=checkout, source=source, bare=bare, env=env,
                           first=first, second=second)


def _cfg(checkout: Path, *, remote="origin", branch="main", backup_dir=None) -> _Cfg:
    update = {"repo_root": str(checkout), "remote": remote, "branch": branch}
    if backup_dir is not None:
        update["backup_dir"] = str(backup_dir)
    return _Cfg({"update": update})


def _assert_no_destructive(calls):
    assert calls, "expected at least one injected git call"
    for argv in calls:
        assert argv[0] == "git", argv
        args = argv[3:]                      # drop git -C <repo>
        assert args, argv
        assert args[0] not in FORBIDDEN_VERBS, argv
        assert not (set(args) & FORBIDDEN_FLAGS), argv


# ---------------------------------------------------------------------------
# 1. clean apply fast-forwards
# ---------------------------------------------------------------------------
def test_clean_apply_fast_forwards_temp_checkout(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install.checkout), confirm=True, runner=runner)

    assert out["ok"] is True and out["state"] == "OK", out
    assert out["pre_revision"] == install.first
    assert out["post_revision"] == install.second
    assert out["changed"] is True and out["update_available"] is True
    assert out["remote_verified"] is True
    # The real tree moved.
    assert _head(install.checkout, install.env) == install.second
    assert (install.checkout / "VERSION").read_text() == "2\n"
    assert _git(["status", "--porcelain"], install.checkout, install.env) == ""
    pulls = [c for c in runner.calls if c[3:4] == ["pull"]]
    assert pulls == [["git", "-C", str(install.checkout), "pull", "--ff-only",
                      "origin", "main"]], pulls
    _assert_no_destructive(runner.calls)


# ---------------------------------------------------------------------------
# 2. dirty worktree refusal leaves the tree byte-identical
# ---------------------------------------------------------------------------
def test_dirty_worktree_refusal_is_byte_identical(install):
    from voicebridge import update_manager as UM

    (install.checkout / "VERSION").write_text("locally edited\n")
    (install.checkout / "UNTRACKED.txt").write_text("operator work\n")
    before_head = _head(install.checkout, install.env)
    before_digest = _tree_digest(install.checkout)

    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install.checkout), confirm=True, runner=runner)

    assert out["ok"] is False
    assert out["code"] == UM.WORKTREE_DIRTY_CODE and out["state"] == "CONFLICT"
    assert not [c for c in runner.calls if c[3:4] == ["pull"]], runner.calls
    assert _head(install.checkout, install.env) == before_head
    assert _tree_digest(install.checkout) == before_digest
    assert (install.checkout / "VERSION").read_text() == "locally edited\n"
    _assert_no_destructive(runner.calls)


# ---------------------------------------------------------------------------
# 3. missing branch / missing remote => typed DEGRADED, never "up to date"
# ---------------------------------------------------------------------------
def test_missing_branch_and_missing_remote_are_typed_degraded(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    missing_branch = UM.check(_cfg(install.checkout, branch="no-such-branch"),
                              git=runner)
    assert missing_branch["state"] == "DEGRADED"
    assert missing_branch["remote_reachable"] is True
    assert missing_branch["remote_branch_found"] is False
    assert missing_branch["behind"] is None
    assert missing_branch["update_available"] is False
    assert "no branch" in (missing_branch["message"] or "")
    assert "latest" not in (missing_branch["message"] or "").lower()

    missing_remote = UM.check(_cfg(install.checkout, remote="does-not-exist"),
                              git=runner)
    assert missing_remote["state"] == "DEGRADED"
    assert missing_remote["remote_reachable"] is False
    assert missing_remote["behind"] is None
    assert missing_remote["update_available"] is False
    assert "couldn't reach" in (missing_remote["message"] or "").lower()
    assert "latest" not in (missing_remote["message"] or "").lower()
    _assert_no_destructive(runner.calls)


# ---------------------------------------------------------------------------
# 4. unreachable remote path => typed DEGRADED within timeout
# ---------------------------------------------------------------------------
def test_unreachable_remote_path_is_typed_degraded_within_timeout(install,
                                                                  tmp_path):
    from voicebridge import update_manager as UM

    bogus = tmp_path / "definitely-not-a-remote"
    runner = _RecordingRunner(install.env)
    start = time.monotonic()
    out = UM.check(_cfg(install.checkout), source=str(bogus), git=runner)
    elapsed = time.monotonic() - start

    assert out["state"] == "DEGRADED", out
    assert out["remote_reachable"] is False
    assert out["update_available"] is False and out["behind"] is None
    assert "couldn't reach" in (out["message"] or "").lower()
    assert "latest" not in (out["message"] or "").lower()
    assert elapsed < UM.TIMEOUT_S, f"check took {elapsed:.1f}s > timeout"
    _assert_no_destructive(runner.calls)


def test_apply_against_unreachable_remote_fails_not_silently_up_to_date(install,
                                                                       tmp_path):
    from voicebridge import update_manager as UM

    bogus = tmp_path / "no-such-remote"
    before = _head(install.checkout, install.env)
    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install.checkout), confirm=True, runner=runner,
                   source=str(bogus))

    assert out["ok"] is False
    assert out["code"] == UM.FAILED_CODE and out["state"] == "FAILED"
    assert out["pre_revision"] == before
    assert _head(install.checkout, install.env) == before
    assert out["rollback_plan"]
    _assert_no_destructive(runner.calls)


# ---------------------------------------------------------------------------
# 5. confirm-required refusal runs no git
# ---------------------------------------------------------------------------
def test_confirm_required_refusal_runs_no_git(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install.checkout), confirm=False, runner=runner)

    assert out["ok"] is False
    assert out["code"] == UM.CONFIRM_CODE
    assert out["state"] == "CONFIRM_REQUIRED"
    assert runner.calls == [], runner.calls


# ---------------------------------------------------------------------------
# 6. e-stop refusal runs no git
# ---------------------------------------------------------------------------
def test_estop_refusal_runs_no_git(install, monkeypatch):
    from voicebridge import update_manager as UM

    monkeypatch.setattr(UM, "_estop", lambda _cfg: True)
    runner = _RecordingRunner(install.env)

    applied = UM.apply(_cfg(install.checkout), confirm=True, runner=runner)
    assert applied["code"] == UM.ESTOP_CODE and applied["ok"] is False
    assert runner.calls == []

    rolled = UM.rollback(_cfg(install.checkout), runner=runner)
    assert rolled["code"] == UM.ESTOP_CODE and rolled["ok"] is False
    assert runner.calls == []


# ---------------------------------------------------------------------------
# 7. rollback restores the recorded revision and is reversible
# ---------------------------------------------------------------------------
def test_rollback_restores_recorded_revision_and_is_reversible(install,
                                                               tmp_path):
    from voicebridge import update_manager as UM

    backups = tmp_path / "backups"
    runner = _RecordingRunner(install.env)

    snap = UM.snapshot(_cfg(install.checkout), runner=runner,
                       backup_dir=backups, reason="pre-apply")
    assert snap["ok"] is True, snap
    assert snap["pre_revision"] == install.first
    assert (Path(snap["backup_dir"]) / "manifest.json").is_file()
    assert (Path(snap["backup_dir"]) / "code.bundle").is_file()

    applied = UM.apply(_cfg(install.checkout), confirm=True, runner=runner)
    assert applied["ok"] is True and applied["post_revision"] == install.second
    assert _head(install.checkout, install.env) == install.second

    rolled = UM.rollback(_cfg(install.checkout), runner=runner,
                         backup_dir=backups)
    assert rolled["ok"] is True, rolled
    assert rolled["restored_revision"] == install.first
    assert rolled["from_revision"] == install.second
    assert rolled["reversible"] is True
    assert _head(install.checkout, install.env) == install.first
    assert (install.checkout / "VERSION").read_text() == "1\n"

    # The pre-rollback snapshot exists and makes the rollback itself reversible.
    pre_rb = Path(rolled["pre_rollback_snapshot"])
    assert pre_rb.is_dir() and (pre_rb / "manifest.json").is_file()
    assert json.loads((pre_rb / "manifest.json").read_text())["reason"] == \
        "pre-rollback"

    # rollback is the ONE sanctioned destructive path: exactly one reset --hard.
    resets = [c for c in runner.calls if c[3:4] == ["reset"]]
    assert resets == [["git", "-C", str(install.checkout), "reset", "--hard",
                       install.first]], resets


# ---------------------------------------------------------------------------
# 8. rollback without a backup is a typed refusal
# ---------------------------------------------------------------------------
def test_rollback_without_backup_is_typed_refusal(install, tmp_path):
    from voicebridge import update_manager as UM

    empty = tmp_path / "no-backups"
    empty.mkdir()
    runner = _RecordingRunner(install.env)

    out = UM.rollback(_cfg(install.checkout), runner=runner, backup_dir=empty)
    assert out["ok"] is False
    assert out["code"] == UM.NO_BACKUP_CODE and out["state"] == "MISSING"
    assert not [c for c in runner.calls if c[3:4] == ["reset"]], runner.calls


# ---------------------------------------------------------------------------
# 9. concurrent update lock — second acquire is typed busy (cross-process)
# ---------------------------------------------------------------------------
def test_concurrent_update_lock_second_acquire_is_typed_busy(tmp_path):
    try:
        from voicebridge import update_lock as UL
    except ImportError:
        pytest.xfail(
            "voicebridge.update_manager exposes no cross-process update lock "
            "(missing module/symbol: voicebridge.update_lock.UpdateLock); "
            "a concurrent second acquire has no typed busy refusal here.")

    marker = tmp_path / "update.lock"
    ready = tmp_path / "holder.ready"
    holder_script = tmp_path / "holder.py"
    holder_script.write_text(
        "import sys, time\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from voicebridge import update_lock as UL\n"
        f"lk = UL.UpdateLock(path={str(marker)!r})\n"
        "assert lk.acquire() is True, 'holder failed to acquire'\n"
        f"open({str(ready)!r}, 'w').write('HELD')\n"
        "time.sleep(60)\n")

    proc = subprocess.Popen([sys.executable, str(holder_script)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            env=dict(os.environ))
    try:
        deadline = time.monotonic() + 30
        while not ready.exists():
            assert proc.poll() is None, "lock holder exited before acquiring"
            assert time.monotonic() < deadline, "lock holder never acquired"
            time.sleep(0.05)
        assert marker.is_file()

        second = UL.UpdateLock(path=marker)
        assert second.acquire() is False
        assert second.acquired is False
        assert second.holder is not None
        assert second.holder.pid == proc.pid
        holder_text = UL.describe_holder(second.holder)
        assert str(proc.pid) in holder_text
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            proc.kill()
            proc.wait(timeout=30)

    # The marker is reclaimable once the holder is gone.
    fresh = UL.UpdateLock(path=marker)
    assert fresh.acquire() is True and fresh.acquired is True
    fresh.release()
    assert not marker.exists()


# ---------------------------------------------------------------------------
# 10. backup manifest readable + corruption is a typed BACKUP_INVALID
# ---------------------------------------------------------------------------
def test_backup_manifest_readable_and_corruption_typed_invalid(install,
                                                               tmp_path):
    from voicebridge import update_manager as UM

    backups = tmp_path / "backups"
    runner = _RecordingRunner(install.env)

    snap = UM.snapshot(_cfg(install.checkout), runner=runner,
                       backup_dir=backups)
    assert snap["ok"] is True, snap
    manifest_path = Path(snap["backup_dir"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["kind"] == "qubit-update"
    assert manifest["pre_revision"] == install.first
    assert manifest["schema"] == 1

    # Truncated/corrupt manifest.json => typed BACKUP_INVALID, never a restore.
    manifest_path.write_text('{"kind": "qubit-update", "pre_rev')
    out = UM.rollback(_cfg(install.checkout), runner=runner,
                      backup_dir=backups)
    assert out["ok"] is False
    assert out["code"] == UM.BACKUP_INVALID_CODE
    assert out["state"] == "FAILED"
    assert not [c for c in runner.calls if c[3:4] == ["reset"]], runner.calls


# ---------------------------------------------------------------------------
# 11. QUBIT_HOME preservation — a sentinel survives check + apply
# ---------------------------------------------------------------------------
def test_qubit_home_sentinel_preserved(install, tmp_path, monkeypatch):
    from voicebridge import update_manager as UM

    qhome = tmp_path / "qhome"
    qhome.mkdir()
    sentinel = qhome / "SENTINEL.txt"
    sentinel.write_text("do-not-touch\n")
    monkeypatch.setenv("QUBIT_HOME", str(qhome))

    backups = qhome / "backups"
    runner = _RecordingRunner(install.env)
    cfg = _cfg(install.checkout, backup_dir=backups)

    checked = UM.check(cfg, git=runner)
    assert checked["state"] == "OK", checked
    snap = UM.snapshot(cfg, runner=runner, backup_dir=backups)
    assert snap["ok"] is True, snap
    applied = UM.apply(cfg, confirm=True, runner=runner)
    assert applied["ok"] is True and applied["post_revision"] == install.second

    assert sentinel.is_file()
    assert sentinel.read_text() == "do-not-touch\n"
    assert not (qhome / ".hermes").exists()
    assert not (tmp_path / ".hermes").exists()


# ---------------------------------------------------------------------------
# 12. secret redaction on the error path
# ---------------------------------------------------------------------------
class _FailingGit:
    """Deterministic executor whose failure output carries fake credentials."""

    def __init__(self, *, local, fail_verb="pull", secret_lines="", dirty=False):
        self.local = local
        self.fail_verb = fail_verb
        self.secret_lines = secret_lines
        self.dirty = dirty
        self.calls = []

    def __call__(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        args = argv[3:]
        if args[:1] == ["rev-parse"]:
            return 0, self.local + "\n", ""
        if args[:2] == ["status", "--porcelain"]:
            return 0, (" M x\n" if self.dirty else ""), ""
        if args[:1] == ["ls-remote"]:
            if self.fail_verb == "ls-remote":
                return 1, "", self.secret_lines
            return 0, "b" * 40 + "\trefs/heads/main\n", ""
        if args[:1] == ["pull"]:
            if self.fail_verb == "pull":
                return 1, "", self.secret_lines
            return 0, "Updating\n", ""
        return 1, "", f"unexpected {args}"


SECRET_URL = "https://x-access-token:ghp_ABCdef0123456789ABCdef@github.com/o/r.git"
SECRET_TOKEN = "token=supersecretvalue123"
SECRET_SK = "sk-" + "a" * 24
SECRET_BLOB = f"fatal: auth failed for '{SECRET_URL}'\n{SECRET_TOKEN}\n{SECRET_SK}\n"


def _assert_no_secret(blob: str) -> None:
    assert "ghp_ABCdef0123456789ABCdef" not in blob
    assert "supersecretvalue123" not in blob
    assert SECRET_SK not in blob


def test_secret_redaction_on_apply_error_path():
    from voicebridge import update_manager as UM

    fake = _FailingGit(local="a" * 40, fail_verb="pull",
                       secret_lines=SECRET_BLOB)
    out = UM.apply(_Cfg({}), confirm=True, git=fake)
    assert out["ok"] is False and out["code"] == UM.FAILED_CODE
    _assert_no_secret(json.dumps(out))


def test_secret_redaction_on_check_error_path(tmp_path):
    from voicebridge import update_manager as UM

    repo = tmp_path / "checkout"
    (repo / ".git").mkdir(parents=True)
    fake = _FailingGit(local="a" * 40, fail_verb="ls-remote",
                       secret_lines=SECRET_BLOB)
    out = UM.check(_Cfg({"update": {"repo_root": str(repo)}}), git=fake)
    assert out["state"] == "DEGRADED"
    _assert_no_secret(json.dumps(out))
