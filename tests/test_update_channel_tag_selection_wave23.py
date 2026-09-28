"""Update channel/tag selection — a real reference must be selected.

Regression for the defect where ``--channel``/``--tag`` were accepted and
echoed but never changed the selected source/reference. These drive
``voicebridge.update_manager.check``/``apply`` with an injected fake git so the
tag resolution is deterministic and touches no git, network, or host checkout.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

LOCAL = "a" * 40
BRANCH = "b" * 40
T123 = "1" * 40
T130 = "2" * 40
T140B = "3" * 40


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
    def __init__(self, *, tags=None, remote_head=BRANCH, local=LOCAL,
                 post=None, dirty=False):
        self.tags = dict(tags or {})
        self.remote_head = remote_head
        self.local = local
        self.post = post
        self.dirty = dirty
        self.calls = []
        self._rev = 0

    def __call__(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        args = argv[3:] if len(argv) > 3 else []
        if args[:2] == ["status", "--porcelain"]:
            return 0, (" M x.py\n" if self.dirty else ""), ""
        if args[:1] == ["rev-parse"]:
            self._rev += 1
            sha = self.local if self._rev == 1 else (self.post or self.local)
            return 0, sha + "\n", ""
        if args[:1] == ["ls-remote"]:
            if args[1:2] == ["--tags"] and args[2:3] == ["--refs"]:
                out = "".join(
                    f"{sha}\trefs/tags/{name}\n" for name, sha in self.tags.items()
                )
                return 0, out, ""
            if args[1:2] == ["--tags"]:
                refspec = args[3] if len(args) > 3 else ""
                name = refspec.split("refs/tags/")[-1]
                if name in self.tags:
                    return 0, f"{self.tags[name]}\trefs/tags/{name}\n", ""
                return 0, "", ""
            if self.remote_head is None:
                return 1, "", "network unreachable"
            return 0, f"{self.remote_head}\trefs/heads/main\n", ""
        if args[:1] == ["pull"]:
            return 0, "Already up to date.\n", ""
        return 1, "", f"unexpected command {args}"


def _calls(fake, verb):
    return [c for c in fake.calls if (c[3:4] or [None]) == [verb]]


def test_check_channel_stable_selects_newest_stable_tag():
    from voicebridge import update_manager as UM

    fake = _FakeGit(tags={"v1.2.3": T123, "v1.3.0": T130,
                          "v1.4.0-beta.1": T140B})
    out = UM.check(_Cfg({}), git=fake, channel="stable")
    assert out["channel"] == "stable"
    assert out["ref_kind"] == "tag"
    assert out["tag"] == "v1.3.0", out
    assert out["remote_revision"] == T130
    assert out["state"] == "OK"


def test_check_explicit_tag_selects_that_tag():
    from voicebridge import update_manager as UM

    fake = _FakeGit(tags={"v1.2.3": T123, "v1.3.0": T130})
    out = UM.check(_Cfg({}), git=fake, tag="v1.2.3")
    assert out["ref_kind"] == "tag"
    assert out["tag"] == "v1.2.3", out
    assert out["remote_revision"] == T123


def test_check_channel_canary_tracks_branch_not_tags():
    from voicebridge import update_manager as UM

    fake = _FakeGit(tags={"v9.9.9": T130})
    out = UM.check(_Cfg({}), git=fake, channel="canary")
    assert out["channel"] == "canary"
    assert out["ref_kind"] == "branch"
    assert out["remote_revision"] == BRANCH
    # canary must not enumerate tags
    assert not any(c[4:6] == ["--tags", "--refs"] for c in fake.calls), fake.calls


def test_check_default_path_does_not_enumerate_tags():
    from voicebridge import update_manager as UM

    fake = _FakeGit(tags={"v1.3.0": T130})
    out = UM.check(_Cfg({}), git=fake)
    assert out["remote_revision"] == BRANCH
    assert "ref_kind" not in out
    assert not any(c[4:6] == ["--tags", "--refs"] for c in fake.calls), fake.calls


def test_apply_channel_pulls_the_selected_tag_ref():
    from voicebridge import update_manager as UM

    fake = _FakeGit(tags={"v1.2.3": T123, "v1.3.0": T130}, post=T130)
    out = UM.apply(_Cfg({}), confirm=True, git=fake, channel="stable")
    assert out["ok"] is True, out
    pulls = _calls(fake, "pull")
    assert pulls, fake.calls
    assert pulls[0][3:] == ["pull", "--ff-only", "origin", "v1.3.0"], pulls[0]
    assert out["ref"] == "v1.3.0" and out["ref_kind"] == "tag"


def test_apply_explicit_tag_pulls_that_ref():
    from voicebridge import update_manager as UM

    fake = _FakeGit(tags={"v1.2.3": T123, "v1.3.0": T130}, post=T123)
    out = UM.apply(_Cfg({}), confirm=True, git=fake, tag="v1.2.3")
    assert out["ok"] is True, out
    assert _calls(fake, "pull")[0][3:] == ["pull", "--ff-only", "origin", "v1.2.3"]


def test_unknown_channel_is_refused():
    from voicebridge.cli import _update_resolve_channel

    with pytest.raises(ValueError):
        _update_resolve_channel(_Cfg({}), None, "bogus", None)
