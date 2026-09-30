"""Effective update ref selection for ``--channel`` / ``--tag`` (9u fix).

The parity defect this guards: ``qubit update --channel canary --tag v9.9.9``
echoed the values but the *effective* git ref never changed, so ``--check`` and
``--apply`` still used the configured branch. ``docs/UPDATE.md`` documents the
channel → ref model (stable→strict SemVer tags, beta→rc/beta tags,
canary→branch head; an explicit ``--tag`` wins).

Three layers, all hermetic (temp git repos and injectable resolvers only — no
network, no ``~/.hermes``, no host checkout):

* the pure selector ``update_channel.select_ref`` / ``select_latest_tag`` /
  ``parse_remote_tags``;
* ``update_manager.check`` / ``apply`` against a real temp checkout with real
  tags — proving the effective ref actually changes and the default branch
  behavior is unchanged;
* the CLI forwarding (``_cmd_update``) that threads ``channel``/``tag`` into the
  canonical manager.
"""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import cli as CLI  # noqa: E402
from voicebridge import update_channel as UC  # noqa: E402
from voicebridge import update_manager as UM  # noqa: E402

CANONICAL = "https://github.com/cgraph-dev/qubit-voicebridge.git"


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


# ---------------------------------------------------------------------------
# layer 1 — pure selector
# ---------------------------------------------------------------------------
TAGS = ["v0.9.0", "v1.0.0", "v1.0.1", "v1.1.0-rc.1", "v2.0.0+build.3",
        "not-a-tag", "v2.0.0-beta.2"]


def test_parse_remote_tags_strips_peel_and_ignores_heads():
    out = ("a" * 40 + "\trefs/tags/v1.0.0\n"
           + "b" * 40 + "\trefs/tags/v1.0.0^{}\n"
           + "c" * 40 + "\trefs/heads/main\n"
           + "d" * 40 + "\trefs/tags/v1.0.1\n")
    assert UC.parse_remote_tags(out) == ["v1.0.0", "v1.0.1"]


def test_select_latest_stable_ignores_prereleases_and_malformed():
    assert UC.select_latest_tag("stable", TAGS) == "v1.0.1"
    assert UC.select_latest_tag("canary", TAGS) is None


def test_select_latest_beta_includes_prereleases():
    # v2.0.0+build.3 outranks v2.0.0-beta.2 (release > prerelease).
    assert UC.select_latest_tag("beta", TAGS) == "v2.0.0+build.3"


def test_select_ref_stable_picks_a_tag():
    sel = UC.select_ref("stable", None, TAGS, branch="main")
    assert sel["ref"] == "v1.0.1" and sel["ref_kind"] == "tag"
    assert sel["resolve_latest_tag"] is True


def test_select_ref_explicit_tag_wins_over_channel():
    sel = UC.select_ref("canary", "v9.9.9", TAGS, branch="main")
    assert sel["ref"] == "v9.9.9" and sel["ref_kind"] == "tag"
    assert sel["pinned"] is True and sel["resolve_latest_tag"] is False


def test_select_ref_canary_is_branch_head():
    sel = UC.select_ref("canary", None, TAGS, branch="release")
    assert sel["ref"] == "release" and sel["ref_kind"] == "branch"


def test_select_ref_tag_channel_without_matching_tag_falls_back_to_branch():
    sel = UC.select_ref("stable", None, ["nope", "v0.1.0-beta.1"], branch="main")
    assert sel["ref"] == "main" and sel["ref_kind"] == "branch"
    assert sel["resolve_latest_tag"] is True


def test_select_ref_unknown_channel_raises():
    with pytest.raises(ValueError):
        UC.select_ref("nightly", None, TAGS, branch="main")


# ---------------------------------------------------------------------------
# layer 2 — update_manager against a real temp checkout with real tags
# ---------------------------------------------------------------------------
class _RecordingRunner:
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
    empty = tmp_path / "gitconfig-empty"
    empty.write_text("")
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "Qubit Channel Test",
        "GIT_AUTHOR_EMAIL": "chan@example.invalid",
        "GIT_COMMITTER_NAME": "Qubit Channel Test",
        "GIT_COMMITTER_EMAIL": "chan@example.invalid",
        "GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def _git(args, cwd: Path, env: dict) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, (
        f"git {' '.join(args)} failed: {proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


def _commit(repo: Path, env: dict, text: str, message: str) -> str:
    (repo / "VERSION").write_text(text)
    _git(["add", "-A"], repo, env)
    _git(["commit", "-m", message], repo, env)
    return _git(["rev-parse", "HEAD"], repo, env)


@pytest.fixture
def install(tmp_path):
    """A temp install at v0.9.0 with newer tagged/untagged commits upstream.

    History (oldest→newest):  A=v0.9.0  B=v1.0.0  C=v1.1.0-rc.1  D=main head.
    The install clone sits at A (one tag behind).
    """
    env = _git_env(tmp_path)
    bare = tmp_path / "remote.git"
    source = tmp_path / "source"
    checkout = tmp_path / "install"

    _git(["init", "--bare", "-b", "main", str(bare)], tmp_path, env)
    _git(["init", "-b", "main", str(source)], tmp_path, env)
    a = _commit(source, env, "0.9.0\n", "first")
    _git(["tag", "v0.9.0"], source, env)
    _git(["remote", "add", "origin", str(bare)], source, env)
    _git(["push", "-u", "origin", "main", "--tags"], source, env)

    _git(["clone", str(bare), str(checkout)], tmp_path, env)
    assert _git(["rev-parse", "HEAD"], checkout, env) == a

    b = _commit(source, env, "1.0.0\n", "second")
    _git(["tag", "v1.0.0"], source, env)
    c = _commit(source, env, "1.1.0rc1\n", "third")
    _git(["tag", "v1.1.0-rc.1"], source, env)
    d = _commit(source, env, "main\n", "fourth")
    _git(["push", "origin", "main", "--tags"], source, env)

    assert len({a, b, c, d}) == 4
    return SimpleNamespace(checkout=checkout, source=source, bare=bare, env=env,
                           a=a, b=b, c=c, d=d)


@pytest.fixture(autouse=True)
def _estop_clear(monkeypatch):
    monkeypatch.setattr(UM, "_estop", lambda _cfg: False)


def _cfg(install, *, branch="main") -> _Cfg:
    return _Cfg({"update": {"repo_root": str(install.checkout),
                            "remote": "origin", "branch": branch}})


def test_check_default_uses_branch_head_unchanged(install):
    out = UM.check(_cfg(install), git=_RecordingRunner(install.env))
    assert out["state"] == "OK"
    assert out["remote"] == "origin" and out["branch"] == "main"
    assert out["ref"] == "main" and out["ref_kind"] == "branch"
    assert out["remote_revision"] == install.d


def test_check_canary_uses_branch_head(install):
    out = UM.check(_cfg(install), git=_RecordingRunner(install.env),
                   channel="canary")
    assert out["state"] == "OK"
    assert out["ref_kind"] == "branch" and out["ref"] == "main"
    assert out["remote_revision"] == install.d
    assert out["channel"] == "canary"


def test_check_stable_selects_newest_stable_tag(install):
    out = UM.check(_cfg(install), git=_RecordingRunner(install.env),
                   channel="stable")
    assert out["state"] == "OK"
    assert out["ref_kind"] == "tag" and out["ref"] == "v1.0.0"
    assert out["remote_revision"] == install.b


def test_check_beta_selects_prerelease_tag(install):
    out = UM.check(_cfg(install), git=_RecordingRunner(install.env),
                   channel="beta")
    assert out["state"] == "OK"
    assert out["ref_kind"] == "tag" and out["ref"] == "v1.1.0-rc.1"
    assert out["remote_revision"] == install.c


def test_check_explicit_tag_wins_over_channel(install):
    out = UM.check(_cfg(install), git=_RecordingRunner(install.env),
                   channel="canary", tag="v0.9.0")
    assert out["state"] == "OK"
    assert out["ref"] == "v0.9.0" and out["ref_kind"] == "tag"
    assert out["remote_revision"] == install.a


def test_apply_stable_pulls_the_selected_tag(install):
    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install), confirm=True, git=runner, channel="stable")

    assert out["ok"] is True and out["state"] == "OK", out
    assert out["ref"] == "v1.0.0" and out["ref_kind"] == "tag"
    assert out["post_revision"] == install.b
    assert _git(["rev-parse", "HEAD"], install.checkout, install.env) == install.b
    pulls = [c for c in runner.calls if c[3:4] == ["pull"]]
    assert pulls == [["git", "-C", str(install.checkout), "pull", "--ff-only",
                      "origin", "v1.0.0"]]


def test_apply_explicit_tag_wins(install):
    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install), confirm=True, git=runner,
                   channel="canary", tag="v0.9.0")
    assert out["ok"] is True, out
    # v0.9.0 == the checkout's current revision, so no move but the ref is right.
    assert out["ref"] == "v0.9.0" and out["ref_kind"] == "tag"
    pulls = [c for c in runner.calls if c[3:4] == ["pull"]]
    assert pulls[0][3:] == ["pull", "--ff-only", "origin", "v0.9.0"]


def test_apply_default_pull_unchanged(install):
    runner = _RecordingRunner(install.env)
    out = UM.apply(_cfg(install), confirm=True, git=runner)
    assert out["ok"] is True, out
    assert out["ref"] == "main" and out["ref_kind"] == "branch"
    assert out["post_revision"] == install.d
    pulls = [c for c in runner.calls if c[3:4] == ["pull"]]
    assert pulls == [["git", "-C", str(install.checkout), "pull", "--ff-only",
                      "origin", "main"]]


def test_apply_unknown_channel_is_typed_refusal(install):
    out = UM.apply(_cfg(install), confirm=True, git=_RecordingRunner(install.env),
                   channel="nightly")
    assert out["ok"] is False
    assert out["code"] == "UPDATE_CHANNEL_INVALID"


# ---------------------------------------------------------------------------
# layer 3 — CLI forwarding
# ---------------------------------------------------------------------------
def _ns(**kw):
    base = {"check": False, "apply": False, "rollback": False, "json": True,
            "source": "", "channel": "", "tag": "", "backup_dir": None,
            "dry_run": False, "confirm": False}
    base.update(kw)
    return argparse.Namespace(**base)


def _cli_cfg() -> _Cfg:
    return _Cfg({"update": {"remote_url": CANONICAL}})


def test_cli_check_forwards_channel_and_tag(monkeypatch):
    seen = {}

    def _fake_check(cfg, **kwargs):
        seen.update(kwargs)
        return {"state": "OK", "message": "ok"}

    monkeypatch.setattr(UM, "check", _fake_check)
    monkeypatch.setattr(CLI, "_update_runtime_state", lambda cfg: None)
    with redirect_stdout(io.StringIO()):
        rc = CLI._cmd_update(_cli_cfg(),
                             _ns(check=True, channel="canary", tag="v9.9.9"))
    assert rc == 0
    assert seen["channel"] == "canary" and seen["tag"] == "v9.9.9"


def test_cli_check_default_passes_neither_channel_nor_tag(monkeypatch):
    seen = {}

    def _fake_check(cfg, source=None):
        seen["source"] = source
        return {"state": "OK", "message": "ok"}

    monkeypatch.setattr(UM, "check", _fake_check)
    monkeypatch.setattr(CLI, "_update_runtime_state", lambda cfg: None)
    with redirect_stdout(io.StringIO()):
        CLI._cmd_update(_cli_cfg(), _ns(check=True, source="/tmp/local.git"))
    assert seen["source"] == "/tmp/local.git"


def test_cli_apply_forwards_tag_to_full_apply(monkeypatch):
    monkeypatch.setattr(UM, "estop_engaged", lambda cfg: False)
    seen = {}

    def _full(cfg, *, confirm=False, source=None, channel=None, tag=None,
              actor=""):
        seen.update({"confirm": confirm, "source": source,
                     "channel": channel, "tag": tag})
        return {"ok": True, "state": "OK"}

    monkeypatch.setattr(UM, "full_apply", _full)
    with redirect_stdout(io.StringIO()):
        rc = CLI._cmd_update(_cli_cfg(),
                             _ns(apply=True, channel="stable", tag="v1.2.3"))
    assert rc == 0
    assert seen["confirm"] is True
    assert seen["channel"] == "stable" and seen["tag"] == "v1.2.3"


def test_cli_invalid_channel_is_typed_refusal_rc2(monkeypatch):
    out = {}

    def _fake_check(cfg, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("invalid channel must not reach the manager")

    monkeypatch.setattr(UM, "check", _fake_check)
    monkeypatch.setattr(CLI, "_update_runtime_state", lambda cfg: None)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_update(_cli_cfg(), _ns(check=True, channel="nightly"))
    import json
    body = json.loads(buf.getvalue())
    assert rc == 2
    assert body["code"] == "UPDATE_CHANNEL_UNKNOWN"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
