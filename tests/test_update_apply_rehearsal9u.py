"""Rehearsal: prove the "Update Qubit" apply+rollback path on a throwaway repo.

Unlike ``tests/test_update_manager9u.py`` (which drives the logic with a fully
faked git), this file exercises ``voicebridge.update_manager.apply`` against a
*real* git checkout built entirely under ``tmp_path``:

* a local BARE remote (``remote.git``),
* a ``source`` working repo whose second commit is pushed to the bare remote,
* an ``install`` clone that is one commit behind.

The install is pointed at through the module's documented public seam
(``update.repo_root`` in the config), and ``apply`` receives an injected
``runner`` that is a thin wrapper over ``subprocess.run`` — so the only real
command executed is the one ``update_manager`` builds, bound to the temp repo.
Nothing here touches the product worktree, the network, or ``~/.hermes``.

Covered: real fast-forward + pre/post revision receipt, dirty-worktree refusal
with the tree left untouched, the fail-closed test guard when no executor is
injected, a scan proving no destructive git verb/flag is ever passed, and a
rehearsal of the receipt's ``rollback_plan`` restoring the pre-revision.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# The git verbs/flags update_manager is contractually forbidden to run.
FORBIDDEN_VERBS = {"reset", "checkout", "stash", "clean", "rebase", "merge",
                   "rm", "filter-branch", "prune", "gc"}
FORBIDDEN_FLAGS = {"--force", "-f", "--hard", "--all", "--prune"}


@pytest.fixture(autouse=True)
def _estop_clear(monkeypatch):
    """The one e-stop authority is exercised in test_update_manager9u.py."""
    from voicebridge import update_manager as UM

    monkeypatch.setattr(UM, "_estop", lambda _cfg: False)


class _Cfg:
    """Minimal nested-dict config implementing the ``.get(*path, default=)`` API."""

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
    """Injected ``runner``: records argv, then really executes it."""

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
        "GIT_AUTHOR_NAME": "Qubit Rehearsal",
        "GIT_AUTHOR_EMAIL": "rehearsal@example.invalid",
        "GIT_COMMITTER_NAME": "Qubit Rehearsal",
        "GIT_COMMITTER_EMAIL": "rehearsal@example.invalid",
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


def _write_tracked(repo: Path, text: str) -> None:
    (repo / "VERSION").write_text(text)


def _commit_all(repo: Path, env: dict, message: str) -> None:
    _git(["add", "-A"], repo, env)
    _git(["commit", "-m", message], repo, env)


@pytest.fixture
def install(tmp_path):
    """A temp install (one commit behind its bare remote) + the source repo."""
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

    # Clone the bare remote -> the install; it tracks origin/main.
    _git(["clone", str(bare), str(checkout)], tmp_path, env)
    first = _head(checkout, env)

    # A second commit is published so an update is available to the install.
    _write_tracked(source, "2\n")
    _commit_all(source, env, "second")
    _git(["push", "origin", "main"], source, env)
    second = _head(source, env)

    assert first != second
    assert _head(checkout, env) == first, "install must still be one behind"
    assert _git(["status", "--porcelain"], checkout, env) == ""

    return SimpleNamespace(checkout=checkout, source=source, bare=bare,
                           env=env, first=first, second=second)


def _cfg(checkout: Path) -> _Cfg:
    return _Cfg({"update": {"repo_root": str(checkout),
                            "remote": "origin", "branch": "main"}})


def _assert_no_destructive(calls):
    assert calls, "expected at least one injected git call"
    for argv in calls:
        assert argv[0] == "git", argv
        args = argv[3:]                      # drop git -C <repo>
        assert args, argv
        assert args[0] not in FORBIDDEN_VERBS, argv
        assert not (set(args) & FORBIDDEN_FLAGS), argv


def test_check_reads_real_revisions_from_temp_repo(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    out = UM.check(_cfg(install.checkout), git=runner)

    assert out["install_method"] == "git-checkout"
    assert out["current_revision"] == install.first
    assert out["remote_revision"] == install.second
    assert out["update_available"] is True
    assert out["behind"] == -1               # unknown count, not fabricated
    assert out["can_apply"] is True
    assert out["state"] == "OK"
    _assert_no_destructive(runner.calls)


def test_apply_fast_forwards_temp_checkout_and_records_receipt(install):
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install.checkout), confirm=True, runner=runner)

    assert out["ok"] is True and out["state"] == "OK"
    assert out["install_method"] == "git-checkout"
    assert out["pre_revision"] == install.first
    assert out["post_revision"] == install.second
    assert out["changed"] is True
    assert out["update_available"] is True
    assert out["rollback_plan"]

    # The real tree fast-forwarded: HEAD and content both moved.
    assert _head(install.checkout, install.env) == install.second
    assert (install.checkout / "VERSION").read_text() == "2\n"
    assert _git(["status", "--porcelain"], install.checkout, install.env) == ""

    # Exactly one executed update command, non-destructive, naming the
    # configured remote/branch explicitly.
    pulls = [c for c in runner.calls if c[3:4] == ["pull"]]
    assert pulls == [["git", "-C", str(install.checkout), "pull", "--ff-only",
                      "origin", "main"]]
    _assert_no_destructive(runner.calls)


def test_apply_dirty_worktree_refused_and_tree_unchanged(install):
    from voicebridge import update_manager as UM

    (install.checkout / "VERSION").write_text("locally edited\n")
    before = _head(install.checkout, install.env)

    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install.checkout), confirm=True, runner=runner)

    assert out["ok"] is False
    assert out["code"] == UM.WORKTREE_DIRTY_CODE
    assert out["state"] == "CONFLICT"
    # Not a single pull was attempted and the tree is exactly as we left it.
    assert not [c for c in runner.calls if c[3:4] == ["pull"]]
    assert _head(install.checkout, install.env) == before
    assert (install.checkout / "VERSION").read_text() == "locally edited\n"
    _assert_no_destructive(runner.calls)


def test_apply_test_guard_refuses_without_injected_executor(install, monkeypatch):
    from voicebridge import update_manager as UM

    monkeypatch.setenv("QUBIT_UPDATE_DISABLED", "1")

    def _must_not_run(*_a, **_kw):        # pragma: no cover - guard must stop it
        raise AssertionError("the real git executor must never be reached")

    monkeypatch.setattr(UM, "_default_runner", _must_not_run)

    before = _head(install.checkout, install.env)
    out = UM.apply(_cfg(install.checkout), confirm=True)

    assert out["ok"] is False
    assert out["code"] == UM.UNAVAILABLE_CODE
    assert out["state"] == "UNAVAILABLE"
    assert _head(install.checkout, install.env) == before


def test_rollback_plan_restores_prerevision_in_temp_repo(install):
    """The receipt's rollback command really reverses the fast-forward."""
    from voicebridge import update_manager as UM

    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install.checkout), confirm=True, runner=runner)
    assert out["post_revision"] == install.second

    # The plan names the pre-update revision and the exact repo.
    assert install.first in out["rollback_plan"]
    assert str(install.checkout) in out["rollback_plan"]

    # update_manager itself never ran a reset/checkout (destructive) command.
    _assert_no_destructive(runner.calls)
    assert not [c for c in runner.calls if set(c[3:]) & FORBIDDEN_FLAGS]
    assert not [c for c in runner.calls if c[3:4] and c[3] in FORBIDDEN_VERBS]

    # Executing the documented plan (in the temp repo only) restores the
    # pre-update revision; the bare remote is untouched.
    _git(["reset", "--hard", install.first], install.checkout, install.env)
    assert _head(install.checkout, install.env) == install.first
    assert _head(install.bare, install.env) == install.second
