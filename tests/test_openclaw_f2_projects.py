"""OC-33 — local confined project git clone/checkout (F2 production proofs).

Drives ``voicebridge.projects.clone`` / ``checkout`` directly: a LOCAL git
repository is cloned into the confined ``projects.clone_root`` and registered
as a normal workspace; a validated ref is checked out. Remote/URL sources,
non-repositories, and option-injected refs are typed-refused, and the clone
destination is confined to the clone root.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from voicebridge import projects as PJ


def _cfg(tmp_path, monkeypatch, clone_root=None):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    root = clone_root or (tmp_path / "clones")
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n"
                 f"projects:\n  clone_root: '{root}'\n")
    return C.Config.load(p)


def _git(args, cwd):
    import os

    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, check=True, env=env)


def _source_repo(tmp_path):
    """A local repo with a `feature` branch containing one extra commit."""
    src = tmp_path / "src"
    src.mkdir()
    _git(["init", "-q"], src)
    (src / "a.txt").write_text("one\n")
    _git(["add", "."], src)
    _git(["-c", "user.email=t@e", "-c", "user.name=T",
          "commit", "-q", "-m", "one"], src)
    base_sha = _git(["rev-parse", "HEAD"], src).stdout.strip()
    base_branch = _git(["rev-parse", "--abbrev-ref", "HEAD"],
                       src).stdout.strip()
    _git(["checkout", "-q", "-b", "feature"], src)
    (src / "b.txt").write_text("two\n")
    _git(["add", "."], src)
    _git(["-c", "user.email=t@e", "-c", "user.name=T",
          "commit", "-q", "-m", "two"], src)
    feature_sha = _git(["rev-parse", "HEAD"], src).stdout.strip()
    _git(["checkout", "-q", base_branch], src)
    return src, base_branch, base_sha, feature_sha


def test_clone_local_repo_confined_and_registered(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    src, base, base_sha, feature_sha = _source_repo(tmp_path)
    out = PJ.clone(cfg, "Site", str(src), ref="feature", confirmed=True)
    assert out["state"] == "OK", out
    dest = Path(out["destination"])
    assert dest.is_dir()
    assert str(dest) == out["project"]["folders"][0]
    assert dest.is_relative_to((tmp_path / "clones").resolve())
    assert out["head"] == feature_sha
    # Registered as a normal workspace with the real cloned folder.
    shown = PJ.show(cfg, "site")
    assert shown["project"]["folders"] == [str(dest)]
    assert (dest / ".git").exists()


def test_clone_remote_sources_are_refused(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    for remote in ("https://example.com/x.git", "git@github.com:x/y.git",
                   "ssh://host/repo", "file:///tmp/src"):
        with pytest.raises(PJ.ProjectError) as exc:
            PJ.clone(cfg, "R", remote, confirmed=True)
        assert exc.value.code == PJ.CLONE_REMOTE_CODE, remote
    assert PJ.list_projects(cfg)["projects"] == []


def test_clone_requires_confirm_and_a_real_repo(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    src, *_ = _source_repo(tmp_path)
    with pytest.raises(PJ.ProjectError) as exc:
        PJ.clone(cfg, "Site", str(src), confirmed=False)
    assert exc.value.code == PJ.CONFIRM_CODE
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(PJ.ProjectError) as exc:
        PJ.clone(cfg, "Plain", str(plain), confirmed=True)
    assert exc.value.code == PJ.BAD_FOLDER_CODE
    with pytest.raises(PJ.ProjectError) as exc:
        PJ.clone(cfg, "Missing", str(tmp_path / "nope"), confirmed=True)
    assert exc.value.code == PJ.BAD_FOLDER_CODE


def test_clone_destination_conflict_and_duplicate(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    src, *_ = _source_repo(tmp_path)
    PJ.clone(cfg, "Site", str(src), confirmed=True)
    with pytest.raises(PJ.ProjectError) as exc:
        PJ.clone(cfg, "Site", str(src), confirmed=True)
    assert exc.value.code == PJ.EXISTS_CODE
    # A pre-existing destination (no registry entry) is typed-refused.
    (tmp_path / "clones" / "other").mkdir(parents=True)
    with pytest.raises(PJ.ProjectError) as exc:
        PJ.clone(cfg, "Other", str(src), confirmed=True)
    assert exc.value.code == PJ.CLONE_DEST_CODE
    # The refused clone never registered a project.
    assert [p["slug"] for p in PJ.list_projects(cfg)["projects"]] == ["site"]


def test_checkout_switches_ref_and_types_bad_ref(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    src, base, base_sha, feature_sha = _source_repo(tmp_path)
    out = PJ.clone(cfg, "Site", str(src), ref="feature", confirmed=True)
    assert out["head"] == feature_sha
    back = PJ.checkout(cfg, "site", base, confirmed=True)
    assert back["state"] == "OK"
    assert back["head"] == base_sha
    with pytest.raises(PJ.ProjectError) as exc:
        PJ.checkout(cfg, "site", "-f", confirmed=True)
    assert exc.value.code == PJ.BAD_NAME_CODE
    with pytest.raises(PJ.ProjectError) as exc:
        PJ.checkout(cfg, "site", "main", confirmed=False)
    assert exc.value.code == PJ.CONFIRM_CODE
