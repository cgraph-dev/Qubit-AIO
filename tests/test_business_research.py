"""War Room research-provider seam: labels, determinism, typed refusals.

Everything here is hermetic: the local adapter is pure and the "no network"
test replaces the socket/HTTP primitives with a tripwire before running it.
"""
from __future__ import annotations

import http.client
import socket
import urllib.request

import pytest

from voicebridge.business.errors import (
    RESEARCH_NOT_CONFIGURED_CODE,
    RESEARCH_UNAVAILABLE_CODE,
    BusinessError,
)
from voicebridge.business.research import (
    LOCAL_PROVIDER_ID,
    LocalResearchProvider,
    RemoteResearchProvider,
    ResearchClaim,
    ResearchInput,
    ResearchResult,
    resolve_provider,
)
from voicebridge.business.warroom import EVIDENCE_LABELS


@pytest.fixture()
def no_network(monkeypatch):
    def tripwire(*_args, **_kwargs):
        raise AssertionError("a research adapter attempted network I/O")

    monkeypatch.setattr(socket, "socket", tripwire)
    monkeypatch.setattr(socket, "create_connection", tripwire)
    monkeypatch.setattr(socket, "create_server", tripwire)
    monkeypatch.setattr(http.client, "HTTPConnection", tripwire)
    monkeypatch.setattr(http.client, "HTTPSConnection", tripwire)
    monkeypatch.setattr(urllib.request, "urlopen", tripwire)
    return tripwire


def test_local_provider_labels_are_honest_and_every_fact_has_a_source(no_network):
    result = LocalResearchProvider().research([
        ResearchInput(kind="url", locator="https://example.test/article"),
        ResearchInput(kind="youtube", locator="https://youtu.be/abc123"),
        ResearchInput(kind="note", locator="owner says planners sell"),
        ResearchInput(kind="post", locator="a post about printables"),
    ])
    assert isinstance(result, ResearchResult)
    labels = {claim.label for claim in result.claims}
    assert labels == set(EVIDENCE_LABELS)
    for claim in result.claims:
        assert claim.claim.strip()
        if claim.label == "FACT":
            assert claim.source_digests, "a FACT must cite a captured source"
            digests = {source.digest for source in result.sources}
            assert set(claim.source_digests) <= digests
    # The local adapter never fetches a locator; it records that it did not.
    url_source = next(source for source in result.sources if source.kind == "url")
    assert "not fetched" in url_source.text


def test_local_provider_is_deterministic(no_network):
    inputs = [ResearchInput(kind="note", locator="same note")]
    first = LocalResearchProvider().research(inputs)
    second = LocalResearchProvider().research(inputs)
    assert first == second
    assert first.provider == LOCAL_PROVIDER_ID


def test_local_provider_uses_supplied_note_text(no_network):
    result = LocalResearchProvider().research([
        ResearchInput(
            kind="note",
            locator="note-1",
            metadata={"text": "The playbook requires human approval."},
        )
    ])
    source = result.sources[0]
    assert "human approval" in source.text


def test_unknown_kind_is_a_typed_unavailable(no_network):
    with pytest.raises(BusinessError) as excinfo:
        LocalResearchProvider().research([
            ResearchInput(kind="image", locator="a.png")
        ])
    assert excinfo.value.code == RESEARCH_UNAVAILABLE_CODE


def test_fact_without_a_source_is_refused():
    with pytest.raises(BusinessError) as excinfo:
        ResearchClaim(label="FACT", claim="demand is high")
    assert excinfo.value.code == "BUSINESS_WARROOM_UNVERIFIED_FACT"


def test_real_provider_is_disabled_by_default(no_network):
    provider = resolve_provider("remote")
    assert isinstance(provider, RemoteResearchProvider)
    with pytest.raises(BusinessError) as excinfo:
        provider.research([ResearchInput(kind="url", locator="https://x.test")])
    assert excinfo.value.code == RESEARCH_NOT_CONFIGURED_CODE


def test_real_provider_seam_is_injectable_without_implicit_io(no_network):
    calls: list[list[ResearchInput]] = []

    def transport(inputs: list[ResearchInput]) -> ResearchResult:
        calls.append(inputs)
        return ResearchResult(
            provider="remote",
            inputs=tuple(inputs),
            sources=(),
            claims=(ResearchClaim(label="UNKNOWN", claim="remote not returned"),),
        )

    disabled = RemoteResearchProvider(transport=transport)
    with pytest.raises(BusinessError):
        disabled.research([ResearchInput(kind="url", locator="https://x.test")])
    assert calls == []

    enabled = RemoteResearchProvider(enabled=True, transport=transport)
    result = enabled.research([ResearchInput(kind="url", locator="https://x.test")])
    assert result.provider == "remote"
    assert len(calls) == 1


def test_unknown_provider_is_a_typed_unavailable(no_network):
    with pytest.raises(BusinessError) as excinfo:
        resolve_provider("made-up")
    assert excinfo.value.code == RESEARCH_UNAVAILABLE_CODE
