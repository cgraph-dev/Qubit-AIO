"""Objective C — real update check/apply (unit-level, voicebridge.update_manager).

Drives ``voicebridge.update_manager.check``/``apply`` with injected fake git
executors so the install detection, DEGRADED-on-network-failure, dirty-tree and
confirm refusals, the fail-closed test guard, and a successful non-destructive
apply are all deterministic and touch no git, network, or host checkout.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

LOCAL_SHA = "a" * 40
REMOTE_SHA = "b" * 40


@pytest.fixture(autouse=True)
def _estop_clear(monkeypatch):
    from voicebridge import update_manager as UM

    monkeypatch.setattr(UM, "_estop", lambda _cfg: False)


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


class _FakeGit:
    """A deterministic stand-in for the injected ``git`` callable.

    Receives the full argv (``["git", "-C", <repo>, ...]``), records every call,
    and answers the bounded git verbs ``update_manager`` uses.
    """

    def __init__(self, *, local=LOCAL_SHA, remote=None, dirty=False,
                 pull=(0, "", ""), post=None):
        self.local = local
        self.remote = remote
        self.dirty = dirty
        self.pull = pull
        self.post = post
        self.calls = []
        self._rev_calls = 0

    def __call__(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        args = argv[3:] if len(argv) > 3 else []
        if args[:1] == ["rev-parse"]:
            self._rev_calls += 1
            sha = self.local if self._rev_calls == 1 else (self.post or self.local)
            return 0, sha + "\n", ""
        if args[:2] == ["status", "--porcelain"]:
            return 0, (" M voicebridge/x.py\n" if self.dirty else ""), ""
        if args[:1] == ["ls-remote"]:
            if self.remote is None:
                return 1, "", "network unreachable"
            return 0, f"{self.remote}\trefs/heads/main\n", ""
        if args[:1] == ["pull"]:
            return self.pull
        return 1, "", f"unexpected command {args}"


def test_check_clean_checkout_reports_update_available():
    from voicebridge import update_manager as UM

    cfg = _Cfg({})
    fake = _FakeGit(remote=REMOTE_SHA)
    out = UM.check(cfg, git=fake)
    assert out["install_method"] == "git-checkout"
    assert out["current_revision"] == LOCAL_SHA
    assert out["remote_revision"] == REMOTE_SHA
    assert out["behind"] == -1            # unknown count, not fabricated
    assert out["update_available"] is True
    assert out["can_apply"] is True
    assert out["state"] == "OK"


def test_check_up_to_date_is_zero_behind():
    from voicebridge import update_manager as UM

    out = UM.check(_Cfg({}), git=_FakeGit(remote=LOCAL_SHA))
    assert out["behind"] == 0
    assert out["update_available"] is False
    assert out["state"] == "OK"


def test_check_network_failure_is_typed_degraded_never_up_to_date():
    from voicebridge import update_manager as UM

    out = UM.check(_Cfg({}), git=_FakeGit(remote=None))
    assert out["state"] == "DEGRADED"
    assert out["behind"] is None
    assert out["update_available"] is False
    assert out["message"] and "couldn't reach" in out["message"].lower()
    assert "latest" not in out["message"].lower()


def test_apply_requires_confirmation_without_calling_git():
    from voicebridge import update_manager as UM

    fake = _FakeGit(remote=REMOTE_SHA)
    out = UM.apply(_Cfg({}), confirm=False, git=fake)
    assert out["code"] == UM.CONFIRM_CODE
    assert out["state"] == "CONFIRM_REQUIRED"
    assert fake.calls == []               # refuse before any executor call


def test_apply_refuses_dirty_worktree_and_does_not_pull():
    from voicebridge import update_manager as UM

    fake = _FakeGit(dirty=True)
    out = UM.apply(_Cfg({}), confirm=True, git=fake)
    assert out["code"] == UM.WORKTREE_DIRTY_CODE
    assert out["ok"] is False
    assert not any(c[3:4] == ["pull"] for c in fake.calls), fake.calls


def test_apply_refuses_non_checkout_with_operator_command(tmp_path):
    from voicebridge import update_manager as UM

    notgit = tmp_path / "notgit"
    notgit.mkdir()
    cfg = _Cfg({"update": {"repo_root": str(notgit)}})
    out = UM.apply(cfg, confirm=True, git=_FakeGit())
    assert out["code"] == UM.OPERATOR_OWNED_CODE
    assert out["update_command"] == "scripts/install.sh"


def test_apply_test_guard_refuses_without_injected_executor(monkeypatch):
    from voicebridge import update_manager as UM

    monkeypatch.setenv("QUBIT_UPDATE_DISABLED", "1")
    out = UM.apply(_Cfg({}), confirm=True)
    assert out["code"] == UM.UNAVAILABLE_CODE
    assert out["state"] == "UNAVAILABLE"


def test_check_test_guard_returns_typed_unavailable(monkeypatch):
    from voicebridge import update_manager as UM

    monkeypatch.setenv("QUBIT_UPDATE_DISABLED", "1")
    out = UM.check(_Cfg({}))
    assert out["state"] == "UNAVAILABLE"
    assert out["behind"] is None
    assert out["update_available"] is False
    assert out["can_apply"] is False


def test_apply_estop_refusal(monkeypatch):
    from voicebridge import update_manager as UM

    monkeypatch.setattr(UM, "_estop", lambda _cfg: True)
    out = UM.apply(_Cfg({}), confirm=True, git=_FakeGit())
    assert out["code"] == UM.ESTOP_CODE


def test_apply_success_is_nondestructive_and_redacts_secrets():
    from voicebridge import update_manager as UM

    secret_url = ("https://x-access-token:ghp_ABCdef0123456789ABCdef"
                  "@github.com/o/r.git")
    leak = f"token=supersecretvalue123\n{secret_url}\n"
    fake = _FakeGit(remote=REMOTE_SHA, post=REMOTE_SHA,
                    pull=(0, "Updating 0000..1111\n" + leak, ""))

    out = UM.apply(_Cfg({}), confirm=True, git=fake)
    assert out["ok"] is True and out["state"] == "OK"
    assert out["pre_revision"] == LOCAL_SHA
    assert out["post_revision"] == REMOTE_SHA
    assert out["changed"] is True
    assert out["update_available"] is True
    assert out["rollback_plan"]

    # The only command run is `git pull --ff-only <remote> <branch>` — no
    # destructive verb/flag. The configured remote/branch are named explicitly
    # so apply() fetches exactly what check() reported.
    pull_calls = [c for c in fake.calls if c[3:4] == ["pull"]]
    assert pull_calls, fake.calls
    assert pull_calls[0][3:] == ["pull", "--ff-only", "origin", "main"]
    flat = " ".join(pull_calls[0])
    for bad in ("reset", "checkout", "stash", "clean", "--force", "-f "):
        assert bad not in flat, (bad, flat)

    # Secrets in the captured git output never reach the receipt.
    blob = json.dumps(out)
    assert "ghp_ABCdef0123456789ABCdef" not in blob
    assert "supersecretvalue123" not in blob
