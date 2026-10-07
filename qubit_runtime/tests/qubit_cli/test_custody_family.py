"""Custody family (``qubit_cli.update_custody`` + the checkout-lock half of
``qubit_cli.update_lock``): the spawn policy that locks the install while any
update child can still write the checkout.

WHY THIS TEST EXISTS
--------------------
This is the riskiest code in the tree: it locks the install and kills/rebuilds
running processes. It landed from three separate agents and was never exercised
end to end. Two failure modes are cheap to catch here and catastrophic live:

1. **A silent cross-language drift.** The lock and marker NAMES are a contract
   shared byte-for-byte with the Rust installer and the Electron updater. If
   Python alone renames one, two updaters lock DIFFERENT paths and run
   concurrently against one checkout, corrupting the install. The docstrings
   say "KEPT verbatim"; these tests pin the literal bytes so a rebrand sweep
   cannot quietly break that promise.

2. **A missing helper.** A previous attempt at this family failed outright with
   ``AttributeError`` on a helper (``_try_lock``) that ``update_custody`` calls
   through ``update_lock``. Nothing imported the two together, so a green
   ``import update_lock`` proved nothing. These tests assert every symbol
   ``update_custody`` reaches for actually resolves on ``update_lock``.

The behavioural core is the custody-vs-network decision: only LOCAL MUTATORS
(things that write the worktree, index or refs on this machine) may inherit the
checkout lock fd; network/credential commands (fetch, push, ls-remote) and
readers must NOT, or a ``git credential-cache--daemon`` they start holds the
checkout locked for its (default 900 s) lifetime.

Real files, real fds -- no mocks -- because what matters is the bytes on disk
the other two language owners read.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import sys

import pytest

from qubit_cli import update_custody as custody
from qubit_cli import update_lock as lock

POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX fd inheritance")


# --- 1. the cross-language name contract -----------------------------------
#
# Owners of each name (verified against the milestone ee8dd6c886):
#   MARKER_NAME          update_lock.py:27  <-> Rust marker.rs / update.rs  <-> Electron update-marker.ts:33
#   CHECKOUT_LOCK_NAME   update_lock.py:54  <-> Rust marker.rs:562         <-> Electron marker-helper.ts:236
#   GIT_CHECKOUT_LOCK_NAME update_lock.py:55 <-> Rust marker.rs:563        <-> Electron marker-helper.ts:267
#
# The tests assert the LITERAL STRING, not the Python constant against itself:
# a rebrand that rewrites only the constant must fail here, not in the field.

def test_marker_name_is_the_shared_contract_string():
    # Electron update-marker.ts:33 is `path.join(hermesHome, '.hermes-update-in-progress')`.
    assert lock.MARKER_NAME == ".hermes-update-in-progress"


def test_checkout_lock_names_are_the_shared_contract_strings():
    # Rust marker.rs:562 `const CHECKOUT_LOCK_NAME: &str = ".hermes-update.lock";`
    assert lock.CHECKOUT_LOCK_NAME == ".hermes-update.lock"
    # Rust marker.rs:563 `const GIT_CHECKOUT_LOCK_NAME: &str = "hermes-update.lock";`
    assert lock.GIT_CHECKOUT_LOCK_NAME == "hermes-update.lock"


def test_handoff_pid_env_is_the_shared_contract_string():
    # Rust update.rs `update_child_env` exports this exact var to the child stage.
    assert lock.HANDOFF_PID_ENV == "HERMES_UPDATE_HANDOFF_PID"


def test_concurrent_exit_code_matches_the_tauri_updater():
    # Rust update.rs `UPDATE_EXIT_CONCURRENT` -- matched by the Tauri updater to
    # show "Qubit is still running". A different code silently means something else.
    assert lock.UPDATE_EXIT_CONCURRENT == 2


def test_marker_max_age_ceiling_matches_electron():
    # update-marker.ts `UPDATE_MARKER_MAX_AGE_MS`; a shorter ceiling here would let
    # Python steal a lock Electron still considers live.
    assert lock.UPDATE_MARKER_MAX_AGE_SECONDS == 20 * 60


def test_create_suspended_matches_winbase_constant():
    # CREATE_SUSPENDED (WinBase.h). The value is duplicated in update_custody;
    # if the two ever diverge, one path spawns a child that can escape the job.
    assert lock.CREATE_SUSPENDED == 0x00000004
    assert custody._CREATE_SUSPENDED == 0x00000004


# --- 2. every helper update_custody reaches for resolves --------------------

_UPDATE_LOCK_SYMBOLS = (
    "_HELD",
    "_JOBS",
    "_git_common_dir",
    "checkout_lock_path",
    "_inherited_lock_fd",
    "checkout_lock_fds",
    "custody_spawn_kwargs",
    "bind_child_to_update_tree",
    "resume_suspended_child",
    "_bind_to_kill_on_close_job",
    "update_tree_job",
    "_default_install_root",
)


@pytest.mark.parametrize("name", _UPDATE_LOCK_SYMBOLS)
def test_custody_reaches_resolvable_update_lock_helpers(name):
    """update_custody calls these through update_lock by name.

    A previous attempt at this family died with AttributeError the moment an
    update ran, because one of these (a lock helper) was absent while
    ``import update_lock`` still succeeded. Resolution is the contract here.
    """
    assert hasattr(lock, name), f"update_lock.{name} is missing; update_custody would AttributeError at runtime"


def test_custody_monkeypatch_seams_exist():
    """The functions update_custody imports *inside* function bodies (lazy, so a
    broken one only fails on the update path). Import-by-name is the contract.
    """
    from qubit_cli import update_lock as ul

    for name in ("_bind_to_kill_on_close_job", "resume_suspended_child",
                 "bind_child_to_update_tree", "update_tree_job",
                 "checkout_lock_fds", "custody_spawn_kwargs", "_git_common_dir"):
        assert getattr(ul, name, None) is not None
    # and update_custody's own private seams
    assert custody._lock_fds is not None
    assert custody._held is not None


# --- 3. the custody-vs-network decision ------------------------------------

_LOCAL_MUTATORS = ("add", "commit", "merge", "checkout", "reset", "rebase",
                   "stash", "cherry-pick", "revert", "rm", "mv", "clean",
                   "restore", "switch", "update-ref", "gc", "tag", "branch")
_NETWORK_OR_READERS = ("fetch", "push", "pull", "ls-remote", "credential",
                       "rev-parse", "status", "log", "diff", "cat-file",
                       "show", "describe", "for-each-ref")


@pytest.mark.parametrize("sub", _LOCAL_MUTATORS)
def test_local_mutators_get_custody(sub):
    assert custody.is_local_mutator([sub]) is True
    # and the lock-inheriting branch is what run_git takes for them
    assert custody.git_subcommand([sub]) == sub


@pytest.mark.parametrize("sub", _NETWORK_OR_READERS)
def test_network_and_readers_do_not_get_custody(sub):
    assert custody.is_local_mutator([sub]) is False


def test_pull_is_not_a_local_mutator():
    """`pull` fetches (network) then merges. If it were treated as one mutator it
    would hold the fd across its network half; the updater fetches, then merges."""
    assert custody.is_local_mutator(["pull"]) is False


def test_git_subcommand_skips_global_options_with_values():
    """`git -C /repo -c foo=bar commit` -- the subcommand is `commit`, not `-C`'s value."""
    assert custody.git_subcommand(["-C", "/repo", "-c", "a=b", "commit"]) == "commit"
    assert custody.is_local_mutator(["-C", "/repo", "commit"]) is True
    assert custody.is_local_mutator(["-C", "/repo", "fetch"]) is False


def test_git_argv_injects_no_detach_for_every_command():
    """Every updater git call carries gc.autoDetach/maintenance.auto=false, or git
    forks a detached gc that inherits the lock fd (POSIX) or outlives it (Windows)."""
    for sub in ("add", "fetch", "commit", "ls-remote"):
        argv = custody.git_argv(["git"], [sub])
        assert argv[:5] == ["git", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false"], (sub, argv)


def test_git_argv_injects_mutator_config_only_for_mutators():
    """Mutators add fsmonitor/hooks/credential-helper suppression; network commands
    keep the user's credential helpers (they must be able to authenticate)."""
    mut_arg = custody.git_argv(["git"], ["commit", "-m", "x"])
    assert "-c" in mut_arg and f"core.hooksPath={os.devnull}" in mut_arg
    assert "credential.helper=" in mut_arg
    net_arg = custody.git_argv(["git"], ["fetch", "origin"])
    assert "credential.helper=" not in net_arg
    assert f"core.hooksPath={os.devnull}" not in net_arg


# --- 4. checkout_lock_path resolution (matches Rust/Electron) ---------------

def test_checkout_lock_path_plain_checkout_uses_dotname(tmp_path):
    root = tmp_path / "install"
    (root / ".git").mkdir(parents=True)
    assert lock.checkout_lock_path(root) == root / ".git" / "hermes-update.lock"


def test_checkout_lock_path_no_git_uses_root_dotname(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    assert lock.checkout_lock_path(root) == root / ".hermes-update.lock"


def test_checkout_lock_path_worktree_gitdir_file(tmp_path):
    """A linked worktree's `.git` is a `gitdir:` file; its `commondir` names the
    shared dir. Rust marker.rs:625 and Electron marker-helper.ts:236 both walk
    this exactly; the lock must land in the COMMON dir, not the worktree."""
    root = tmp_path / "wt"
    root.mkdir()
    store = tmp_path / "store" / "worktrees" / "x"
    store.mkdir(parents=True)
    (store / "commondir").write_text("../..\n", encoding="utf-8")
    (root / ".git").write_text(f"gitdir: {store}\n", encoding="utf-8")
    expected = (tmp_path / "store").resolve()
    assert lock.checkout_lock_path(root) == expected / "hermes-update.lock"


# --- 5. the fd fallback and inheritance (POSIX) ----------------------------

@POSIX_ONLY
def test_checkout_lock_fds_empty_when_nothing_owned(monkeypatch):
    """No _HELD and no inherited fd -> (), so custody_spawn_kwargs hands the child nothing."""
    monkeypatch.setattr(lock, "_HELD", None)
    monkeypatch.setattr(lock, "_inherited_lock_fd", lambda path: None)
    assert lock.checkout_lock_fds() == ()
    assert lock.custody_spawn_kwargs() == {}


@POSIX_ONLY
def test_checkout_lock_fds_falls_back_to_inherited_fd(monkeypatch, tmp_path):
    """THE FALLBACK the task names: nothing owned, but an inherited fd holds the
    lock -> checkout_lock_fds hands that fd to the child.

    NOTE the deliberate asymmetry with custody_spawn_kwargs (below): only
    checkout_lock_fds has this fallback.
    """
    monkeypatch.setattr(lock, "_HELD", None)
    monkeypatch.setattr(lock, "_inherited_lock_fd", lambda path: 42)
    assert lock.checkout_lock_fds() == (42,)


@POSIX_ONLY
def test_custody_spawn_kwargs_requires_owned_lock_no_fallback(monkeypatch):
    """custody_spawn_kwargs is the *owned-lock* path and does NOT fall back to an
    inherited fd -- this asymmetry is upstream's design (byte-identical to
    hermes_cli/update_lock.py @ ee8dd6c886), so it is pinned, not "fixed".

    The two callers differ:
      * checkout_lock_fds(root) -- contained_command (a build child that may be a
        non-owner of a DIFFERENT checkout, holding an inherited fd) NEEDS the probe;
      * custody_spawn_kwargs() -- a process that owns no lock legitimately hands
        the child nothing (it will join/bind the lock another way).
    If a future change "unifies" them, one of the two callers changes behaviour;
    this test makes that visible instead of silent.
    """
    monkeypatch.setattr(lock, "_HELD", None)
    monkeypatch.setattr(lock, "_inherited_lock_fd", lambda path: 42)
    assert lock.custody_spawn_kwargs() == {}
    # ... while checkout_lock_fds, same state, DOES find the fd:
    assert lock.checkout_lock_fds() == (42,)


@POSIX_ONLY
def test_checkout_lock_fds_prefers_held_over_inherited(monkeypatch):
    """When we own the lock, that fd wins -- never a probe of the filesystem."""
    monkeypatch.setattr(lock, "_HELD", {"path": None, "fd": 7, "owned": True, "depth": 1})
    monkeypatch.setattr(lock, "_inherited_lock_fd", lambda path: 99)
    assert lock.checkout_lock_fds() == (7,)


@POSIX_ONLY
def test_checkout_lock_fds_held_without_fd_is_empty(monkeypatch):
    """Holding a lock object with no fd (joined, not owner): nothing to donate."""
    monkeypatch.setattr(lock, "_HELD", {"path": None, "fd": None, "owned": False, "depth": 0})
    assert lock.checkout_lock_fds() == ()


@POSIX_ONLY
def test_inherited_lock_fd_finds_only_inheritable_fd(tmp_path):
    """pass_fds clears CLOEXEC in the child; a local CLOEXEC open on the same path
    (e.g. a status probe) must NOT be adopted -- its owner may close it while we
    still need custody."""
    path = tmp_path / "h.lock"
    inheritable = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    os.set_inheritable(inheritable, True)
    fcntl.flock(inheritable, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert lock._inherited_lock_fd(path) == inheritable

        # A second, CLOEXEC fd on the same path must be ignored: close the
        # inheritable one so the probe can only find the CLOEXEC candidate.
        os.close(inheritable)
        cloexec = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        os.set_inheritable(cloexec, False)
        try:
            assert lock._inherited_lock_fd(path) is None
        finally:
            os.close(cloexec)
    finally:
        with contextlib.suppress(OSError):
            os.close(inheritable)


@POSIX_ONLY
def test_custody_kwargs_adds_pass_fds_for_mutator(monkeypatch):
    """_custody_kwargs merges the lock fd into the child's pass_fds for a mutator."""
    monkeypatch.setattr(lock, "_HELD", {"path": None, "fd": 5, "owned": True, "depth": 1})
    kwargs = custody._custody_kwargs(True, {})
    assert kwargs.get("pass_fds") == (5,)
    # a non-inheriting (network/reader) child gets no lock fd
    plain = custody._custody_kwargs(False, {})
    assert plain.get("pass_fds", ()) == ()


@POSIX_ONLY
def test_lock_fds_matches_custody_spawn_kwargs(monkeypatch):
    """update_custody._lock_fds is a thin view over update_lock.custody_spawn_kwargs;
    they must not disagree on the fd set."""
    monkeypatch.setattr(lock, "_HELD", {"path": None, "fd": 11, "owned": True, "depth": 1})
    assert custody._lock_fds(True) == lock.custody_spawn_kwargs()["pass_fds"]
    assert custody._lock_fds(False) == ()


# --- 6. custodian launcher sanity (the fd drop + name) ---------------------

def test_reap_tree_launcher_drops_lock_only_after_tree_gone():
    """The POSIX custodian must (a) take the donated fds, (b) leave the caller's
    process group, (c) reap until no descendant is left before exiting (which
    releases the fd). Pin the structural markers so a rewrite cannot drop them."""
    src = custody._REAP_TREE
    assert "pass_fds=fds" in src          # (a) the lock fd reaches node
    assert "PR_SET_CHILD_SUBREAPER" in src  # (c) descendants re-parent to the custodian
    assert "os.setpgid(0, 0)" in src        # (b) own group: survives a group kill
    assert "sys.exit(code if code >= 0 else 128 - code)" in src


def test_owner_watch_script_is_posix_only_and_kills_on_eof():
    """The macOS/Linux fetch watchdog kills the child on EOF from the owner (owner
    died) -- a fetch that rewrites refs must not outlive its owner."""
    src = custody._OWNER_WATCH
    assert "os.read(0, 1)" in src
    assert "signal.SIGKILL" in src
