"""The stable lookup is this fork's R2 ``ChannelRecord``, never a GitHub release query.

Upstream resolves ``stable`` from the repository's latest published GitHub release and spends the
user's GitHub credential there. This fork's release source is the R2 channel protocol, so the
record is authoritative: the lookup makes no GitHub request, asks for no token, and reports a
missing record versus a transport failure the way the R2 reader does.
"""
from types import SimpleNamespace
import urllib.error

import pytest

pytestmark = pytest.mark.real_release_channels

from qubit_cli import source_releases
from qubit_cli.release_channels import ChannelNotFound

SHA = "818c13be1dc4fd28987e1e881a9408224afd4535"


def _record(name="stable", *, policy="pinned", commit=SHA, version="0.21.6",
            state="active", manifest=True):
    record = {"name": name, "repository": "NousResearch/hermes-agent", "state": state,
              "policy": policy}
    if policy == "source-branch":
        record["delivery"] = {"kind": "source-branch", "branch": name}
    resolved = SimpleNamespace(requested=record, terminal=record, manifest=None)
    if manifest:
        resolved.manifest = {"request": {"commit": commit, "sourceVersion": version,
                                        "buildId": "build-1"}}
    return resolved


def test_stable_lookup_reads_the_r2_record_without_a_github_request(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_user")
    monkeypatch.setattr(source_releases, "_resolve_channel", lambda name, repository: _record(name))
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: pytest.fail("the stable lookup must not query GitHub"))

    target = source_releases._resolve_stable("NousResearch/hermes-agent")

    assert (target.commit, target.version) == (SHA, "0.21.6")


def test_a_missing_stable_record_is_a_missing_channel_not_a_github_failure(monkeypatch):
    def missing(name, repository):
        raise ChannelNotFound("Channel object not found")
    monkeypatch.setattr(source_releases, "_resolve_channel", missing)

    with pytest.raises(ChannelNotFound):
        source_releases._resolve_stable("NousResearch/hermes-agent")


def test_an_r2_transport_failure_is_reported_as_one_not_as_a_missing_release(monkeypatch):
    def broken(name, repository):
        raise urllib.error.URLError("rate limit exceeded")
    monkeypatch.setattr(source_releases, "_resolve_channel", broken)

    with pytest.raises(ValueError) as caught:
        source_releases._resolve_stable("NousResearch/hermes-agent")
    message = str(caught.value)
    assert "rate limit" in message and "No published stable release" not in message
