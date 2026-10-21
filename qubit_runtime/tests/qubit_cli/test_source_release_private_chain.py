"""The pin's private release-resolution chain is the public paths' implementation.

``resolve_source_target`` delegates through ``_resolve_source_target`` and ``_resolve_stable``;
``resolve_source_release`` delegates through ``_resolve_release``. The fork's ``stable`` channel
is an R2 ``ChannelRecord`` (not an inferred GitHub release), so ``_resolve_stable`` reads that
record and names a transport failure instead of silently treating it as "no release".
"""
from __future__ import annotations

import urllib.error
from types import SimpleNamespace

import pytest

from qubit_cli import source_releases
from qubit_cli.release_channels import ChannelNotFound


def _record(name="stable", *, policy="pinned", commit="a" * 40, version="1.2.3",
            state="active", manifest=True):
    record = {"name": name, "repository": "owner/repo", "state": state, "policy": policy}
    if policy == "source-branch":
        record["delivery"] = {"kind": "source-branch", "branch": name}
    resolved = SimpleNamespace(requested=record, terminal=record, manifest=None)
    if manifest:
        resolved.manifest = {"request": {"commit": commit, "sourceVersion": version,
                                        "buildId": "build-1"}}
    return resolved


def test_public_target_resolves_through_the_private_helper(monkeypatch):
    seen = []

    def resolve(channel, git_cmd=None, cwd=None, *, repository=None):
        seen.append((channel, repository))
        return source_releases.SourceTarget(channel, channel, repository or "owner/repo", branch=channel)

    monkeypatch.setattr(source_releases, "_resolve_source_target", resolve)
    target = source_releases.resolve_source_target("preview", repository="owner/repo")
    assert seen == [("preview", "owner/repo")]
    assert target.branch == "preview"


def test_stable_is_delegated_to_resolve_stable(monkeypatch):
    seen = []

    def stable(repository, git_cmd=None, cwd=None):
        seen.append(repository)
        return source_releases.SourceTarget("stable", "stable", repository, branch="stable")

    monkeypatch.setattr(source_releases, "_resolve_stable", stable)
    target = source_releases._resolve_source_target("stable", repository="owner/repo")
    assert seen == ["owner/repo"]
    assert target.channel == "stable"


def test_resolve_stable_reads_the_local_r2_record_not_github(monkeypatch):
    monkeypatch.setattr(source_releases, "_resolve_channel",
                        lambda name, repository: _record(name))
    monkeypatch.setattr(source_releases, "_resolve_release",
                        lambda *a, **k: pytest.fail("stable must not fall back to GitHub releases"))
    target = source_releases._resolve_stable("owner/repo")
    assert (target.commit, target.version, target.build_id) == ("a" * 40, "1.2.3", "build-1")


def test_resolve_stable_keeps_a_missing_record_a_missing_channel(monkeypatch):
    def missing(name, repository):
        raise ChannelNotFound("Channel object not found")
    monkeypatch.setattr(source_releases, "_resolve_channel", missing)
    with pytest.raises(ChannelNotFound):
        source_releases._resolve_stable("owner/repo")


def test_resolve_stable_describes_a_transport_failure(monkeypatch):
    def broken(name, repository):
        raise urllib.error.URLError("rate limit exceeded")
    monkeypatch.setattr(source_releases, "_resolve_channel", broken)
    with pytest.raises(ValueError) as caught:
        source_releases._resolve_stable("owner/repo")
    assert "rate limit" in str(caught.value)
    assert "No published stable release" not in str(caught.value)


def test_public_release_resolves_through_the_private_helper(monkeypatch):
    seen = []

    def resolve(channel, git_cmd=None, cwd=None, *, repository=None, pointer=True):
        seen.append((channel, repository, pointer))
        return "v1.2.3", "b" * 40

    monkeypatch.setattr(source_releases, "_resolve_release", resolve)
    assert source_releases.resolve_source_release(
        "stable", repository="owner/repo", pointer=False) == ("v1.2.3", "b" * 40)
    assert seen == [("stable", "owner/repo", False)]


def test_public_release_swallows_the_private_failure(monkeypatch):
    def resolve(*a, **k):
        raise ValueError("not a published release")
    monkeypatch.setattr(source_releases, "_resolve_release", resolve)
    assert source_releases.resolve_source_release("canary") == (None, None)


def test_private_release_rejects_a_non_release_channel():
    with pytest.raises(ValueError, match="Not a release channel"):
        source_releases._resolve_release("preview")
