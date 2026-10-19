"""The HEAD-containment probe answers None when git cannot answer, never NameError.

``_head_containing`` must keep HEAD on an unknown relation (a shallow clone, an unreachable
comparison API) and only refuse when the checkout is PROVEN not to contain the release commit.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from qubit_cli import source_check, source_releases


def _git(root, *args) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _commit(root, label: str) -> str:
    (root / "content.txt").write_text(label, encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", label)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Probe Fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    return root


def test_probe_is_none_when_git_cannot_answer(repo):
    assert source_releases._probe(
        ["definitely-not-a-git-binary"], repo, "rev-parse", "HEAD") is None


def test_head_containing_returns_head_for_an_ancestor(repo):
    base = _commit(repo, "base")
    _commit(repo, "next")

    assert source_releases._head_containing(
        ["git"], repo, base, "owner/repo") == _git(repo, "rev-parse", "HEAD")


def test_head_containing_refuses_a_proven_divergent_commit(repo):
    _commit(repo, "base")
    _git(repo, "checkout", "--orphan", "detour")
    stray = _commit(repo, "detour")
    _git(repo, "checkout", "main")

    assert source_releases._head_containing(["git"], repo, stray, "owner/repo") is None


def _pinned(channel: str, commit: str) -> SimpleNamespace:
    return SimpleNamespace(
        terminal={"repository": "owner/repo", "name": channel, "policy": "pinned"},
        requested={"state": "active"},
        manifest={"request": {"commit": commit, "sourceVersion": "1.2.3", "buildId": "b1"}},
    )


def test_forward_only_keeps_a_checkout_that_contains_the_release(repo, monkeypatch):
    base = _commit(repo, "base")
    head = _commit(repo, "next")
    monkeypatch.setattr(source_releases, "_resolve_channel", lambda name, repository: _pinned(name, base))

    target = source_releases.resolve_source_target(
        "canary", ["git"], repo, repository="owner/repo", forward_only=True)

    assert target.commit == head
    assert target.ahead is True
    assert "newer" in target.label


def test_forward_only_off_still_pins_the_release(repo, monkeypatch):
    base = _commit(repo, "base")
    _commit(repo, "next")
    monkeypatch.setattr(source_releases, "_resolve_channel", lambda name, repository: _pinned(name, base))

    target = source_releases.resolve_source_target(
        "canary", ["git"], repo, repository="owner/repo")

    assert target.commit == base
    assert target.ahead is False


def test_head_containing_keeps_head_when_a_shallow_clone_cannot_prove_ancestry(repo, monkeypatch):
    _commit(repo, "base")
    shallow = repo.parent / "shallow"
    _git(repo.parent, "clone", "--depth", "1", f"file://{repo}", str(shallow))
    _git(shallow, "config", "user.name", "Probe Fixture")
    _git(shallow, "config", "user.email", "fixture@example.invalid")
    _git(shallow, "config", "commit.gpgsign", "false")
    _git(shallow, "checkout", "--orphan", "detour")
    stray = _commit(shallow, "detour")
    _git(shallow, "checkout", "main")
    monkeypatch.setattr(source_check, "_github_compare", lambda *args, **kwargs: None)

    assert _git(shallow, "rev-parse", "--is-shallow-repository") == "true"
    assert source_releases._head_containing(
        ["git"], shallow, stray, "owner/repo") == _git(shallow, "rev-parse", "HEAD")
