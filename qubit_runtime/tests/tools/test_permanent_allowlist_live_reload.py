"""A hand edit to ``command_allowlist`` must reach a long-lived process's approval check.

The unified gateway daemon serves every local session, so the import-time load of
``command_allowlist`` lived as long as the daemon: an entry the operator removed by hand
(the documented way to withdraw a standing approval) stayed auto-approved until restart.

Ported from upstream ``tests/tools/test_permanent_allowlist_live_reload.py`` at
``43faacfb8de7b29548547a7fbdedad0cc6e7391c``; the only change is the Qubit package name.
"""

import threading

import pytest

import qubit_cli.config as config_module
from qubit_cli.config import get_config_path
from tools import approval
from tools.approval_floors import _command_matches_permanent_allowlist


@pytest.fixture
def allowlist_file():
    """Write ``command_allowlist`` into the isolated test home's config.yaml."""
    saved_approved = set(approval._permanent_approved)
    saved_baseline = dict(approval._permanent_baseline_by_home)
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    def _write(entries):
        lines = ["command_allowlist:"] + [f"- '{e}'" for e in entries] if entries else ["command_allowlist: []"]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    try:
        yield _write
    finally:
        approval._permanent_approved.clear()
        approval._permanent_approved.update(saved_approved)
        approval._permanent_baseline_by_home.clear()
        approval._permanent_baseline_by_home.update(saved_baseline)


def test_entry_removed_by_hand_prompts_again(allowlist_file):
    allowlist_file(["rm -rf build", "ls *"])
    approval.load_permanent_allowlist()
    assert _command_matches_permanent_allowlist("rm -rf build")

    allowlist_file(["ls *"])                     # operator withdraws the standing approval

    assert not _command_matches_permanent_allowlist("rm -rf build")
    assert not approval.is_approved("s1", "rm -rf build")
    assert _command_matches_permanent_allowlist("ls -la")


def test_entry_added_by_hand_takes_effect(allowlist_file):
    allowlist_file(["ls *"])
    approval.load_permanent_allowlist()
    assert not _command_matches_permanent_allowlist("podman ps")

    allowlist_file(["ls *", "podman *"])

    assert _command_matches_permanent_allowlist("podman ps")


def test_approval_made_in_process_survives_an_unrelated_hand_edit(allowlist_file):
    """An [a]lways whose save failed lives only in memory; a later edit must not drop it."""
    allowlist_file(["ls *", "git status"])
    approval.load_permanent_allowlist()
    approval.approve_permanent("recursive delete")

    allowlist_file(["ls *"])

    assert approval._is_permanently_approved("recursive delete")
    assert not _command_matches_permanent_allowlist("git status")


def test_warning_tuples_keep_tirith_session_max(allowlist_file):
    """The gate's own shape must keep working: a dangerous key goes permanent, a tirith
    finding is session-only even on ``always``."""
    allowlist_file([])
    approval.load_permanent_allowlist()

    approval._persist_choice(
        "s1", "always",
        [("docker *", "docker run", False), ("tirith:rule", "content finding", True)],
    )

    assert approval._is_permanently_approved("docker *")
    assert not approval._is_permanently_approved("tirith:rule")
    assert approval.is_approved("s1", "tirith:rule")


@pytest.mark.parametrize("bad", [("only", "two"), ("a", "b", False, "extra"), 42])
def test_unrecognized_warning_shape_raises(bad):
    """A malformed entry is a caller bug, not something to skip silently."""
    with pytest.raises(ValueError):
        approval._normalize_warnings([bad])


def test_removal_synced_while_an_always_save_is_pending_is_not_written_back(allowlist_file, monkeypatch):
    """An [a]lways save snapshots the allowlist, drops ``_lock`` and then writes. If another
    thread's check syncs a hand removal in that window, the save must not treat the removed
    entry in its stale snapshot as a new grant and write it back."""
    allowlist_file(["rm -rf build", "ls *"])
    approval.load_permanent_allowlist()

    real_load_config = config_module.load_config
    save_entered, release_save = threading.Event(), threading.Event()

    def paused_load_config(*args, **kwargs):
        save_entered.set()
        assert release_save.wait(timeout=10)
        return real_load_config(*args, **kwargs)

    monkeypatch.setattr(config_module, "load_config", paused_load_config)
    saver = threading.Thread(target=approval._persist_choice, args=("s1", "always", ["docker *"]))
    saver.start()
    try:
        assert save_entered.wait(timeout=10)
        allowlist_file(["ls *"])                     # operator withdraws the standing approval
        assert not approval.is_approved("s2", "rm -rf build")   # this check syncs the removal
    finally:
        release_save.set()
        saver.join(timeout=10)
    assert not saver.is_alive()

    assert sorted(approval._read_permanent_allowlist()) == ["docker *", "ls *"]
    assert not _command_matches_permanent_allowlist("rm -rf build")
    assert approval._is_permanently_approved("docker *")
