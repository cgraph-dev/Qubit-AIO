"""Update-path hardening + clean-install/packaging rehearsal (step 9u).

Two halves, both hermetic:

1. **Update path** — drives ``voicebridge.update_manager.check``/``apply`` and
   the receipt's rollback plan against a *real* git checkout built under
   ``tmp_path`` (a local bare remote, a source repo that publishes commits, and
   an install clone one commit behind). Covers configured remote/branch
   selection, the confirmation and e-stop gates, dirty-tree refusal with the
   tree left untouched, a fast-forward whose post revision equals the remote
   head, a missing-branch diagnosis that is NOT misreported as unreachable, the
   rollback plan literally restoring the pre revision, and a scan proving no
   destructive git verb/flag is ever executed.

2. **Clean-install rehearsal** — exercises ``scripts/clean_install_rehearsal.py``
   (the hermetic ``--check`` preflight and the isolation planner). Proves the
   rehearsal's HOME/XDG/QUBIT_HOME/venv/dist/state all stay under a throwaway
   temp root and can never target ``~/.hermes``, that missing entry points and a
   missing dashboard bundle are typed, and that ``--run`` refuses before it
   builds when there is no dashboard to ship.

Nothing here touches the product worktree, the network, or ``~/.hermes``.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FORBIDDEN_VERBS = {"reset", "checkout", "stash", "clean", "rebase", "merge",
                   "rm", "filter-branch", "prune", "gc"}
FORBIDDEN_FLAGS = {"--force", "-f", "--hard", "--all", "--prune"}


# ---------------------------------------------------------------------------
# shared git harness
# ---------------------------------------------------------------------------
class _Cfg:
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
    """Injected ``runner``: records argv, then really executes it in temp."""

    def __init__(self, env):
        self.env = env
        self.calls = []

    def __call__(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, env=self.env)
        return proc.returncode, proc.stdout, proc.stderr


class _FakeGit:
    """Deterministic injected git for the branches real git cannot stage."""

    def __init__(self, *, local, remote=None, post=None, dirty=False):
        self.local = local
        self.remote = remote
        self.post = post
        self.dirty = dirty
        self.calls = []
        self._revs = 0

    def __call__(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        args = argv[3:]
        if args[:1] == ["rev-parse"]:
            self._revs += 1
            sha = self.local if self._revs == 1 else (self.post or self.local)
            return 0, sha + "\n", ""
        if args[:2] == ["status", "--porcelain"]:
            return 0, (" M x\n" if self.dirty else ""), ""
        if args[:1] == ["ls-remote"]:
            if self.remote is None:
                return 1, "", "network unreachable"
            return 0, f"{self.remote}\trefs/heads/main\n", ""
        if args[:1] == ["pull"]:
            return 0, "Updating\n", ""
        return 1, "", f"unexpected {args}"


@pytest.fixture(autouse=True)
def _estop_clear(monkeypatch):
    from voicebridge import update_manager as UM

    monkeypatch.setattr(UM, "_estop", lambda _cfg: False)


def _git_env(tmp_path: Path) -> dict:
    empty = tmp_path / "gitconfig-empty"
    empty.write_text("")
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "Qubit Packaging", "GIT_AUTHOR_EMAIL": "pack@example.invalid",
        "GIT_COMMITTER_NAME": "Qubit Packaging", "GIT_COMMITTER_EMAIL": "pack@example.invalid",
        "GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def _git(args, cwd: Path, env: dict) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, (
        f"git {' '.join(args)} failed ({proc.returncode}): {proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


def _head(repo: Path, env: dict) -> str:
    return _git(["rev-parse", "HEAD"], repo, env)


def _commit(repo: Path, env: dict, text: str, message: str) -> str:
    (repo / "VERSION").write_text(text)
    _git(["add", "-A"], repo, env)
    _git(["commit", "-m", message], repo, env)
    return _head(repo, env)


@pytest.fixture
def install(tmp_path):
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

    _git(["checkout", "-b", "release"], source, env)
    third = _commit(source, env, "3\n", "third")
    _git(["push", "origin", "release"], source, env)
    _git(["checkout", "main"], source, env)

    assert len({first, second, third}) == 3
    assert _head(checkout, env) == first
    assert _git(["status", "--porcelain"], checkout, env) == ""
    return SimpleNamespace(checkout=checkout, source=source, bare=bare, env=env,
                           first=first, second=second, third=third)


def _cfg(checkout: Path, *, remote="origin", branch="main") -> _Cfg:
    return _Cfg({"update": {"repo_root": str(checkout),
                            "remote": remote, "branch": branch}})


def _assert_no_destructive(calls):
    assert calls, "expected at least one injected git call"
    for argv in calls:
        assert argv[0] == "git", argv
        args = argv[3:]
        assert args and args[0] not in FORBIDDEN_VERBS, argv
        assert not (set(args) & FORBIDDEN_FLAGS), argv


# ---------------------------------------------------------------------------
# update manager: real git
# ---------------------------------------------------------------------------
def test_check_reports_update_and_resolves_remote_branch(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    out = UM.check(_cfg(install.checkout), git=runner)

    assert out["state"] == "OK"
    assert out["current_revision"] == install.first
    assert out["remote_revision"] == install.second
    assert out["remote"] == "origin" and out["branch"] == "main"
    assert out["remote_reachable"] is True and out["remote_branch_found"] is True
    assert out["update_available"] is True and out["behind"] == -1
    assert out["can_apply"] is True
    _assert_no_destructive(runner.calls)


def test_check_follows_configured_branch_selection(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    out = UM.check(_cfg(install.checkout, branch="release"), git=runner)
    assert out["remote_revision"] == install.third
    assert out["branch"] == "release"
    _assert_no_destructive(runner.calls)


def test_check_missing_branch_is_not_misreported_as_unreachable(install):
    """A reachable remote with no such branch must say so, not 'couldn't reach'."""
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    out = UM.check(_cfg(install.checkout, branch="no-such-branch"), git=runner)

    assert out["state"] == "DEGRADED"
    assert out["remote_reachable"] is True
    assert out["remote_branch_found"] is False
    assert out["behind"] is None and out["update_available"] is False
    assert "no branch" in (out["message"] or "")
    assert "couldn't reach" not in (out["message"] or "").lower()
    assert out["can_apply"] is False
    _assert_no_destructive(runner.calls)


def test_check_unreachable_remote_is_typed_degraded(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    out = UM.check(_cfg(install.checkout, remote="does-not-exist"), git=runner)
    assert out["state"] == "DEGRADED"
    assert out["remote_reachable"] is False
    assert out["remote_branch_found"] is False
    assert out["update_available"] is False and out["can_apply"] is False
    assert "couldn't reach" in (out["message"] or "").lower()
    assert "latest" not in (out["message"] or "").lower()


def test_apply_confirmation_and_estop_gates_run_no_git(install, monkeypatch):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install.checkout), confirm=False, runner=runner)
    assert out["code"] == UM.CONFIRM_CODE and runner.calls == []

    monkeypatch.setattr(UM, "_estop", lambda _cfg: True)
    out = UM.apply(_cfg(install.checkout), confirm=True, runner=runner)
    assert out["code"] == UM.ESTOP_CODE and runner.calls == []


def test_apply_dirty_worktree_refused_tree_untouched(install):
    from voicebridge import update_manager as UM

    (install.checkout / "VERSION").write_text("locally edited\n")
    before = _head(install.checkout, install.env)
    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install.checkout), confirm=True, runner=runner)

    assert out["code"] == UM.WORKTREE_DIRTY_CODE and out["state"] == "CONFLICT"
    assert not [c for c in runner.calls if c[3:4] == ["pull"]]
    assert _head(install.checkout, install.env) == before
    assert (install.checkout / "VERSION").read_text() == "locally edited\n"
    _assert_no_destructive(runner.calls)


def test_apply_fast_forwards_and_post_equals_remote_head(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install.checkout), confirm=True, runner=runner)

    assert out["ok"] is True and out["state"] == "OK"
    assert out["pre_revision"] == install.first
    assert out["post_revision"] == install.second
    assert out["post_revision"] == _head(install.bare, install.env)
    assert out["remote_verified"] is True
    assert out["changed"] is True and out["update_available"] is True
    assert _head(install.checkout, install.env) == install.second
    assert (install.checkout / "VERSION").read_text() == "2\n"
    pulls = [c for c in runner.calls if c[3:4] == ["pull"]]
    assert pulls == [["git", "-C", str(install.checkout), "pull", "--ff-only",
                      "origin", "main"]]
    _assert_no_destructive(runner.calls)


def test_apply_post_mismatch_is_failed_not_hidden():
    """A pull whose post revision does not match the remote head fails closed."""
    from voicebridge import update_manager as UM

    fake = _FakeGit(local="a" * 40, remote="b" * 40, post="c" * 40)
    out = UM.apply(_Cfg({}), confirm=True, git=fake)
    assert out["ok"] is False and out["code"] == UM.FAILED_CODE
    assert out["remote_verified"] is False
    assert out["post_revision"] == "c" * 40
    assert "does not match" in out["detail"]


def test_receipt_carries_non_executed_operator_rollback(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install.checkout), confirm=True, runner=runner)

    rb = out["rollback"]
    assert rb["kind"] == "operator" and rb["executed"] is False
    assert rb["revision"] == install.first
    assert install.first in out["rollback_plan"]
    assert str(install.checkout) in out["rollback_plan"]
    # The rollback is described; the manager itself never ran a reset/checkout.
    _assert_no_destructive(runner.calls)


def test_rollback_plan_plan_string_restores_pre_revision(install):
    """Execute the literal plan string (temp repo only) and prove restoration."""
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install.checkout), confirm=True, runner=runner)
    assert out["post_revision"] == install.second

    plan = shlex.split(out["rollback_plan"])
    assert plan[0] == "git" and plan[1] == "-C" and plan[2] == str(install.checkout)
    assert plan[3:] == ["reset", "--hard", install.first]
    # This destructive command is the OPERATOR's; the manager never ran it.
    assert not [c for c in runner.calls if c[3:4] and c[3] in FORBIDDEN_VERBS]

    proc = subprocess.run(plan, capture_output=True, text=True, env=install.env)
    assert proc.returncode == 0, proc.stderr
    assert _head(install.checkout, install.env) == install.first
    assert _head(install.bare, install.env) == install.second


def test_no_destructive_flag_is_ever_executed_by_the_manager(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    UM.check(_cfg(install.checkout, branch="release"), git=runner)
    UM.apply(_cfg(install.checkout, branch="release"), confirm=True,
             runner=runner)
    _assert_no_destructive(runner.calls)
    flat = " ".join(" ".join(c[3:]) for c in runner.calls)
    for bad in ("reset", "checkout", "stash", "clean", "--force", "--hard"):
        assert bad not in flat, (bad, flat)


# ---------------------------------------------------------------------------
# packaging rehearsal script
# ---------------------------------------------------------------------------
def _rehearsal_module():
    import importlib
    return importlib.import_module("scripts.clean_install_rehearsal")


def test_rehearsal_check_passes_and_is_isolated(tmp_path):
    R = _rehearsal_module()
    work = tmp_path / "work"
    out = R.check(REPO_ROOT, work_root=work)
    assert out["state"] == "OK", out
    assert out["isolation_findings"] == []
    assert out["missing_entry_points"] == []
    assert out["dashboard_bundle_present"] is True
    assert Path(out["plan"]["home"]).is_relative_to(work)
    assert Path(out["plan"]["state"]).is_relative_to(work)


def test_rehearsal_isolation_rejects_hermes_and_real_home():
    R = _rehearsal_module()
    bad = R.build_plan(REPO_ROOT, work_root=Path.home() / ".hermes" / "rehearsal")
    findings = R.isolation_findings(bad)
    assert findings, "a ~/.hermes work root must be rejected"
    assert any(".hermes" in f for f in findings)


def test_rehearsal_missing_entry_points_is_fail(tmp_path):
    R = _rehearsal_module()
    fake = tmp_path / "repo"
    fake.mkdir()
    (fake / "pyproject.toml").write_text(
        "[project]\nname='x'\n[project.scripts]\nnotqubit='x:y'\n")
    (fake / "frontend/qubit-dist").mkdir(parents=True)
    (fake / "frontend/qubit-dist/index.html").write_text("<html></html>")
    out = R.check(fake, work_root=tmp_path / "work")
    assert out["state"] == "FAIL"
    assert "qubit" in out["missing_entry_points"]


def test_rehearsal_missing_dashboard_is_degraded(tmp_path):
    R = _rehearsal_module()
    fake = tmp_path / "repo"
    fake.mkdir()
    (fake / "pyproject.toml").write_text(
        "[project]\nname='x'\n[project.scripts]\n"
        "qubit='voicebridge.cli:main'\nvoicebridge='voicebridge.cli:main'\n"
        "qubit-dev='voicebridge.terminal:main'\n"
        "qubit-agent='voicebridge.qubit_runtime:entry'\n")
    out = R.check(fake, work_root=tmp_path / "work")
    assert out["state"] == "DEGRADED"
    assert out["dashboard_bundle_present"] is False
    assert out["missing_entry_points"] == []


def test_rehearsal_run_refuses_before_building_without_dashboard(tmp_path):
    R = _rehearsal_module()
    fake = tmp_path / "repo"
    fake.mkdir()
    (fake / "pyproject.toml").write_text(
        "[project]\nname='x'\n[project.scripts]\n"
        "qubit='voicebridge.cli:main'\nvoicebridge='voicebridge.cli:main'\n"
        "qubit-dev='voicebridge.terminal:main'\n"
        "qubit-agent='voicebridge.qubit_runtime:entry'\n")
    work = tmp_path / "work"
    out = R.run(fake, work_root=work)
    assert out["state"] == "DEGRADED"
    assert not (work / "venv").exists()
    assert not (work / "dist").exists()


def test_rehearsal_check_cli_is_hermetic(tmp_path):
    R = _rehearsal_module()
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "fake-home")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "fake-home/.config")
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/clean_install_rehearsal.py"),
         "--check", "--repo", str(REPO_ROOT)],
        capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == R.EXIT_OK, proc.stdout + proc.stderr
    body = json.loads(proc.stdout)
    assert body["state"] == "OK"
    assert body["isolation_findings"] == []
    assert not (tmp_path / "fake-home" / ".hermes").exists()
    for key in ("home", "venv", "dist", "state"):
        assert "fake-home" not in body["plan"][key], body["plan"][key]
    assert Path(R.REPO).is_dir()
