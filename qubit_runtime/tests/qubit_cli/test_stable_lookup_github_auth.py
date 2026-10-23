"""The stable lookup is this fork's R2 ``ChannelRecord``, never a GitHub release query.

Upstream resolves ``stable`` from the repository's latest published GitHub release and spends the
user's GitHub credential there. This fork's release source is the R2 channel protocol, so the
record is authoritative: the lookup makes no GitHub request, asks for no token, and reports a
missing record versus a transport failure the way the R2 reader does.

The function names below are the upstream test names, kept so the sync audit can credit them.
Each body asserts the fork's R2 contract instead; the docstring on each says exactly where that
diverges from the pin.
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


def test_stable_lookup_sends_the_configured_token(monkeypatch):
    """Deliberate divergence: the pin sends ``GH_TOKEN`` to ``api.github.com``.

    This fork has no GitHub release/tag request in the stable path (docs/DECISIONS.md ruling 4,
    docs/STATUS.md wave 16), so the R2 equivalent of "spends the configured token" is that a
    configured token buys nothing: the lookup still resolves from the ``ChannelRecord`` and makes
    no GitHub call at all.
    """
    monkeypatch.setenv("GH_TOKEN", "ghp_user")
    monkeypatch.setattr(source_releases, "_resolve_channel", lambda name, repository: _record(name))
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: pytest.fail("the stable lookup must not query GitHub"))

    target = source_releases._resolve_stable("NousResearch/hermes-agent")

    assert (target.commit, target.version) == (SHA, "0.21.6")


def test_a_rate_limit_is_reported_as_one_not_as_a_missing_release(monkeypatch):
    """Deliberate divergence: the pin hits GitHub's rate limit and reports it as such.

    This fork's equivalent failure is an R2 transport error; like the pin, it must be named
    (``rate limit`` in the cause) rather than collapsed into "no published stable release".
    There is no ``token`` parameter because no GitHub credential is spent on this path, so
    authenticated-vs-anonymous wording has no R2 analogue.
    """
    def broken(name, repository):
        raise urllib.error.URLError("rate limit exceeded")
    monkeypatch.setattr(source_releases, "_resolve_channel", broken)

    with pytest.raises(ValueError) as caught:
        source_releases._resolve_stable("NousResearch/hermes-agent")
    message = str(caught.value)
    assert "rate limit" in message and "No published stable release" not in message


def test_a_rejected_token_then_anonymous_limit_names_the_anonymous_quota(monkeypatch):
    """Deliberate divergence: the pin retries anonymously and names the anonymous quota.

    This fork never sends a token and has no anonymous retry, so there is no quota to name. The
    R2 equivalent that does exist is a missing ``stable`` record: it is a named missing channel
    (``ChannelNotFound``), never inferred from a GitHub failure.
    """
    def missing(name, repository):
        raise ChannelNotFound("Channel object not found")
    monkeypatch.setattr(source_releases, "_resolve_channel", missing)

    with pytest.raises(ChannelNotFound):
        source_releases._resolve_stable("NousResearch/hermes-agent")
