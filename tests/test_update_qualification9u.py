"""Isolated qualification — Qubit update manager (step 9).

Exercises ``voicebridge.update_manager.check``/``apply`` against a *real* git
checkout built entirely under ``tmp_path``: a local bare remote, a source repo,
and an install clone that is one commit behind. The install is addressed through
the module's public seam (``update.repo_root``), and every git command runs
through an injected runner bound to that temp repo's hermetic environment — so
the product worktree, the network, and ``~/.hermes`` are never touched.

Covered: update available, up-to-date, configured remote/branch respected,
unreachable-remote DEGRADED (never a fabricated "up to date"), dirty-tree
refusal, confirmation gate, e-stop refusal, post-update revision recorded and
matching the remote, a rollback plan that restores the pre revision, a scan
proving no destructive verb/flag is ever executed, the injected-runner
availability regression, and the ``qubit_compat.dispatch`` update routes.
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

# The git verbs/flags update_manager is contractually forbidden to execute.
FORBIDDEN_VERBS = {"reset", "checkout", "stash", "clean", "rebase", "merge",
                   "rm", "filter-branch", "prune", "gc"}
FORBIDDEN_FLAGS = {"--force", "-f", "--hard", "--all", "--prune"}


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


@pytest.fixture(autouse=True)
def _estop_clear(monkeypatch):
    from voicebridge import update_manager as UM

    monkeypatch.setattr(UM, "_estop", lambda _cfg: False)


def _git_env(tmp_path):
    empty = tmp_path / "gitconfig-empty"
    empty.write_text("")
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "Qubit Qualification",
        "GIT_AUTHOR_EMAIL": "qualification@example.invalid",
        "GIT_COMMITTER_NAME": "Qubit Qualification",
        "GIT_COMMITTER_EMAIL": "qualification@example.invalid",
        "GIT_CONFIG_GLOBAL": str(empty),
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def _git(args, cwd, env):
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, (
        f"git {' '.join(args)} failed ({proc.returncode}): "
        f"{proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


def _head(repo, env):
    return _git(["rev-parse", "HEAD"], repo, env)


def _write_tracked(repo, text):
    (repo / "VERSION").write_text(text)


def _commit_all(repo, env, message):
    _git(["add", "-A"], repo, env)
    _git(["commit", "-m", message], repo, env)


@pytest.fixture
def install(tmp_path):
    """A temp install one commit behind its bare remote, plus a release branch."""
    env = _git_env(tmp_path)
    bare = tmp_path / "remote.git"
    source = tmp_path / "source"
    checkout = tmp_path / "install"

    _git(["init", "--bare", "-b", "main", str(bare)], tmp_path, env)
    _git(["init", "-b", "main", str(source)], tmp_path, env)
    _write_tracked(source, "1\n")
    _commit_all(source, env, "first")
    _git(["remote", "add", "origin", str(bare)], source, env)
    _git(["push", "-u", "origin", "main"], source, env)

    _git(["clone", str(bare), str(checkout)], tmp_path, env)
    first = _head(checkout, env)

    _write_tracked(source, "2\n")
    _commit_all(source, env, "second")
    _git(["push", "origin", "main"], source, env)
    second = _head(source, env)

    _git(["checkout", "-b", "release"], source, env)
    _write_tracked(source, "3\n")
    _commit_all(source, env, "third")
    _git(["push", "origin", "release"], source, env)
    third = _head(source, env)

    assert len({first, second, third}) == 3
    assert _head(checkout, env) == first, "install must still be one behind"
    assert _git(["status", "--porcelain"], checkout, env) == ""
    return SimpleNamespace(checkout=checkout, source=source, bare=bare, env=env,
                           first=first, second=second, third=third)


def _cfg(checkout, *, remote="origin", branch="main"):
    return _Cfg({"update": {"repo_root": str(checkout),
                            "remote": remote, "branch": branch}})


def _assert_no_destructive(calls):
    assert calls, "expected at least one injected git call"
    for argv in calls:
        assert argv[0] == "git", argv
        args = argv[3:]                      # drop git -C <repo>
        assert args, argv
        assert args[0] not in FORBIDDEN_VERBS, argv
        assert not (set(args) & FORBIDDEN_FLAGS), argv


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------
def test_check_reports_update_available(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    out = UM.check(_cfg(install.checkout), git=runner)
    assert out["install_method"] == "git-checkout"
    assert out["current_revision"] == install.first
    assert out["remote_revision"] == install.second
    assert out["update_available"] is True and out["behind"] == -1
    assert out["can_apply"] is True and out["state"] == "OK"
    _assert_no_destructive(runner.calls)


def test_check_up_to_date_after_apply(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    UM.apply(_cfg(install.checkout), confirm=True, runner=runner)
    out = UM.check(_cfg(install.checkout), git=runner)
    assert out["current_revision"] == install.second
    assert out["remote_revision"] == install.second
    assert out["behind"] == 0 and out["update_available"] is False


def test_configured_remote_and_branch_are_respected(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    cfg = _cfg(install.checkout, remote="origin", branch="release")
    checked = UM.check(cfg, git=runner)
    assert checked["remote_revision"] == install.third, checked

    out = UM.apply(cfg, confirm=True, runner=runner)
    assert out["ok"] is True and out["remote"] == "origin"
    assert out["branch"] == "release"
    assert out["post_revision"] == install.third
    pulls = [c for c in runner.calls if c[3:4] == ["pull"]]
    assert pulls == [["git", "-C", str(install.checkout), "pull", "--ff-only",
                      "origin", "release"]], pulls


def test_check_unreachable_remote_is_typed_degraded(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    out = UM.check(_cfg(install.checkout, remote="does-not-exist"), git=runner)
    assert out["state"] == "DEGRADED"
    assert out["behind"] is None and out["update_available"] is False
    assert "couldn't reach" in (out["message"] or "").lower()
    assert "latest" not in (out["message"] or "").lower()


def test_check_accepts_injected_runner_without_git_binary(install, monkeypatch):
    """Regression: availability must consider the injected runner, not only the
    ``git`` binary (mirrors apply(); a host without git could still be checked)."""
    from voicebridge import update_manager as UM

    monkeypatch.setattr(UM.shutil, "which", lambda _name: None)
    runner = _RecordingRunner(install.env)
    out = UM.check(_cfg(install.checkout), git=runner)
    assert out["state"] == "OK", out
    assert out["current_revision"] == install.first
    assert out["remote_revision"] == install.second


# ---------------------------------------------------------------------------
# apply: gates, success, rollback
# ---------------------------------------------------------------------------
def test_apply_requires_confirmation_without_calling_git(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install.checkout), confirm=False, runner=runner)
    assert out["code"] == UM.CONFIRM_CODE and out["state"] == "CONFIRM_REQUIRED"
    assert runner.calls == []


def test_apply_refuses_dirty_worktree_and_leaves_tree_untouched(install):
    from voicebridge import update_manager as UM

    (install.checkout / "VERSION").write_text("locally edited\n")
    before = _head(install.checkout, install.env)
    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install.checkout), confirm=True, runner=runner)
    assert out["code"] == UM.WORKTREE_DIRTY_CODE and out["state"] == "CONFLICT"
    assert not [c for c in runner.calls if c[3:4] == ["pull"]], runner.calls
    assert _head(install.checkout, install.env) == before
    assert (install.checkout / "VERSION").read_text() == "locally edited\n"


def test_apply_estop_refuses(install, monkeypatch):
    from voicebridge import update_manager as UM

    monkeypatch.setattr(UM, "_estop", lambda _cfg: True)
    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install.checkout), confirm=True, runner=runner)
    assert out["code"] == UM.ESTOP_CODE and out["ok"] is False
    assert runner.calls == []


def test_apply_fast_forwards_and_records_matching_remote_revision(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    checked = UM.check(_cfg(install.checkout), git=runner)
    out = UM.apply(_cfg(install.checkout), confirm=True, runner=runner)

    assert out["ok"] is True and out["state"] == "OK"
    assert out["pre_revision"] == install.first
    assert out["post_revision"] == install.second
    assert out["post_revision"] == checked["remote_revision"]
    assert out["changed"] is True and out["update_available"] is True
    assert _head(install.checkout, install.env) == install.second
    # The bare remote's main head is exactly what was recorded.
    assert _git(["rev-parse", "refs/heads/main"], install.bare, install.env) == \
        install.second
    _assert_no_destructive(runner.calls)


def test_rollback_plan_restores_pre_revision(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install.checkout), confirm=True, runner=runner)
    assert out["post_revision"] == install.second
    assert install.first in out["rollback_plan"]
    assert str(install.checkout) in out["rollback_plan"]

    # update_manager only *describes* the rollback; the plan is executed here,
    # in the temp repo, to prove it really reverses the fast-forward.
    _git(["reset", "--hard", install.first], install.checkout, install.env)
    assert _head(install.checkout, install.env) == install.first
    assert _head(install.bare, install.env) == install.second


def test_no_destructive_command_is_ever_executed(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    UM.apply(_cfg(install.checkout, branch="release"), confirm=True,
             runner=runner)
    _assert_no_destructive(runner.calls)
    # The rollback plan is a named operator command, not an executed one.
    flat = " ".join(" ".join(c[3:]) for c in runner.calls)
    for bad in ("reset", "checkout", "stash", "clean", "--force", "--hard"):
        assert bad not in flat, (bad, flat)


# ---------------------------------------------------------------------------
# integration through the real qubit_compat.dispatch routes
# ---------------------------------------------------------------------------
def _wire_update(monkeypatch, install):
    """Lift the guard and bind the default runner to the temp repo only."""
    from voicebridge import update_manager as UM

    monkeypatch.setattr(UM, "_real_control_disabled", lambda: False)
    runner = _RecordingRunner(install.env)
    monkeypatch.setattr(UM, "_default_runner", runner)
    return runner


def _dispatch(cfg, method, path, body=None):
    from voicebridge import qubit_compat as HC

    return HC.dispatch(cfg, method, path, {}, body or {})


def test_dispatch_update_check_route(install, monkeypatch):
    _wire_update(monkeypatch, install)
    status, body = _dispatch(_cfg(install.checkout), "GET",
                             "/api/qubit/update/check")
    assert status == 200 and body["ok"] is True, (status, body)
    assert body["update_available"] is True
    assert body["current_revision"] == install.first
    assert body["remote_revision"] == install.second


def test_dispatch_update_apply_confirm_dirty_and_success(install, monkeypatch):
    _wire_update(monkeypatch, install)

    status, body = _dispatch(_cfg(install.checkout), "POST", "/api/qubit/update")
    assert status == 409 and body["code"] == "CONFIRM_REQUIRED", (status, body)

    (install.checkout / "VERSION").write_text("dirty\n")
    status, body = _dispatch(_cfg(install.checkout), "POST", "/api/qubit/update",
                             {"confirm": True})
    assert status == 409 and body["code"] == "WORKTREE_DIRTY", (status, body)

    _git(["checkout", "--", "VERSION"], install.checkout, install.env)
    status, body = _dispatch(_cfg(install.checkout), "POST", "/api/qubit/update",
                             {"confirm": True})
    assert status == 200 and body["ok"] is True, (status, body)
    assert body["post_revision"] == install.second
    assert _head(install.checkout, install.env) == install.second


def test_dispatch_update_apply_estop_and_guard(install, monkeypatch):
    from voicebridge import update_manager as UM

    _wire_update(monkeypatch, install)
    monkeypatch.setattr(UM, "_estop", lambda _cfg: True)
    status, body = _dispatch(_cfg(install.checkout), "POST", "/api/qubit/update",
                             {"confirm": True})
    assert status == 403 and body["code"] == "UPDATE_ESTOP", (status, body)

    # Restore the fail-closed guard with no injected executor -> typed 503.
    monkeypatch.setattr(UM, "_estop", lambda _cfg: False)
    monkeypatch.setattr(UM, "_real_control_disabled", lambda: True)
    status, body = _dispatch(_cfg(install.checkout), "POST", "/api/qubit/update",
                             {"confirm": True})
    assert status == 503 and body["code"] == "UPDATE_UNAVAILABLE", (status, body)


def test_dispatch_update_receipt_has_no_secret_or_destructive_plan(install,
                                                                   monkeypatch):
    _wire_update(monkeypatch, install)
    status, body = _dispatch(_cfg(install.checkout), "POST", "/api/qubit/update",
                             {"confirm": True})
    assert status == 200 and body["ok"] is True, (status, body)
    blob = json.dumps(body)
    assert "UPDATE_OPERATOR_OWNED" not in blob
    # rollback_plan names the pre revision (an operator command, never executed
    # by the manager); the executed command list contains no destructive verb.
    assert install.first in body["rollback_plan"]
