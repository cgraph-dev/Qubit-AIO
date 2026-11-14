"""Optional Perplexity research for the War Room.

Hermetic: every test injects a transport or a provider; the socket/HTTP
primitives are replaced with a tripwire. Nothing here reads a real credential,
hits the network, or mutates a live Business OS store.
"""
from __future__ import annotations

import http.client
import json
import socket
import urllib.request

import pytest

from voicebridge import qubit_compat as HC
from voicebridge.business import BusinessStore, warroom
from voicebridge.business import research as RESEARCH
from voicebridge.business import research_run as RUN
from voicebridge.business.errors import (
    RESEARCH_FAILED_CODE,
    RESEARCH_NOT_CONFIGURED_CODE,
    RESEARCH_UNAVAILABLE_CODE,
    BusinessError,
)
from voicebridge.business.research import (
    PERPLEXITY_PROVIDER_ID,
    PerplexityResearchProvider,
    PerplexityRequest,
    ResearchClaim,
    ResearchInput,
    ResearchResult,
    ResearchSource,
    perplexity_settings,
    resolve_provider,
)

KEY = "pplx-test-secret-0123456789"


@pytest.fixture()
def no_network(monkeypatch):
    def tripwire(*_args, **_kwargs):
        raise AssertionError("research attempted network I/O")

    monkeypatch.setattr(socket, "socket", tripwire)
    monkeypatch.setattr(socket, "create_connection", tripwire)
    monkeypatch.setattr(socket, "create_server", tripwire)
    monkeypatch.setattr(http.client, "HTTPConnection", tripwire)
    monkeypatch.setattr(http.client, "HTTPSConnection", tripwire)
    monkeypatch.setattr(urllib.request, "urlopen", tripwire)
    monkeypatch.setattr(
        RESEARCH, "_http_post_json",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("the default transport must not run")
        ),
    )
    return tripwire


class _Cfg:
    def __init__(self, values: dict | None = None, state_dir=None):
        self._values = values or {}
        self._state = state_dir

    def get(self, *path, default=None):
        node = self._values
        for part in path:
            if not isinstance(node, dict):
                return default
            node = node.get(part)
        return default if node is None else node

    def state_dir(self):
        return self._state


def _payload(**overrides):
    payload = {
        "choices": [{
            "message": {
                "content": "Printable planners sell on Etsy [1]. "
                           "Demand may grow next year [2]."
            }
        }],
        "citations": [
            "https://source.test/planners",
            "https://source.test/demand",
        ],
        "search_results": [
            {"url": "https://source.test/planners",
             "title": "Planner market report"},
            {"url": "https://source.test/demand",
             "title": "Demand outlook", "snippet": "demand is seasonal"},
        ],
    }
    payload.update(overrides)
    return payload


def _store(tmp_path) -> BusinessStore:
    return BusinessStore(tmp_path / "business.db")


def _session(store: BusinessStore):
    tenant = store.create_tenant("Acme")
    session, _ = warroom.create_session(
        store,
        tenant_id=tenant.tenant_id,
        title="Printable planner",
        idempotency_key="wr-research",
    )
    return tenant, session


def _research_result() -> ResearchResult:
    source = ResearchSource(
        kind="url",
        locator="https://source.test/planners",
        title="Planner market report",
        text="Printable planners sell on Etsy.",
        digest="digest-planners",
        adapter=PERPLEXITY_PROVIDER_ID,
    )
    return ResearchResult(
        provider=PERPLEXITY_PROVIDER_ID,
        inputs=(ResearchInput(kind="note", locator="do planners sell"),),
        sources=(source,),
        claims=(
            ResearchClaim(
                label="FACT",
                claim="Printable planners sell on Etsy.",
                source_digests=("digest-planners",),
            ),
            ResearchClaim(
                label="INFERENCE",
                claim="Demand may grow next year.",
                source_digests=("digest-planners",),
            ),
        ),
    )


class _FakeProvider:
    provider_id = PERPLEXITY_PROVIDER_ID

    def __init__(self, result: ResearchResult | None = None):
        self.calls = 0
        self._result = result or _research_result()

    def research(self, inputs):
        self.calls += 1
        return self._result


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------
def test_configured_provider_maps_citations_to_sources_and_facts(no_network):
    seen: dict[str, object] = {}

    def transport(request: PerplexityRequest, headers):
        seen["request"] = request
        seen["headers"] = dict(headers)
        return _payload()

    provider = PerplexityResearchProvider(api_key=KEY, transport=transport)
    result = provider.research([
        ResearchInput(
            kind="note", locator="do planners sell?",
            metadata={"query": True, "text": "do planners sell?"},
        )
    ])

    assert result.provider == PERPLEXITY_PROVIDER_ID
    assert [source.locator for source in result.sources] == [
        "https://source.test/planners",
        "https://source.test/demand",
    ]
    digests = {source.digest for source in result.sources}
    facts = [claim for claim in result.claims if claim.label == "FACT"]
    assert facts, "a cited sentence should be a FACT"
    for claim in facts:
        assert claim.source_digests
        assert set(claim.source_digests) <= digests

    request = seen["request"]
    assert request.model == "sonar-pro"
    assert request.url == "https://api.perplexity.ai/chat/completions"
    assert "do planners sell" in request.prompt
    headers = seen["headers"]
    assert headers["Authorization"] == f"Bearer {KEY}"

    rendered = json.dumps(result.as_dict())
    assert KEY not in rendered
    assert "Bearer" not in rendered


def test_provider_without_a_key_is_typed_not_configured(no_network):
    calls: list[object] = []

    def transport(request, headers):  # pragma: no cover - must not run
        calls.append(request)
        return _payload()

    provider = PerplexityResearchProvider(api_key="", transport=transport)
    assert provider.configured is False
    with pytest.raises(BusinessError) as excinfo:
        provider.research([ResearchInput(kind="note", locator="x")])
    assert excinfo.value.code == RESEARCH_NOT_CONFIGURED_CODE
    assert calls == []


def test_provider_failure_is_typed_and_scrubs_the_key(no_network):
    def transport(request, headers):
        raise RuntimeError(f"upstream rejected {KEY}")

    provider = PerplexityResearchProvider(api_key=KEY, transport=transport)
    with pytest.raises(BusinessError) as excinfo:
        provider.research([ResearchInput(kind="note", locator="x")])
    assert excinfo.value.code == RESEARCH_FAILED_CODE
    assert KEY not in str(excinfo.value)
    assert "***REDACTED***" in str(excinfo.value)


def test_a_claim_is_never_a_fact_without_a_source_url(no_network):
    def transport(request, headers):
        return {
            "choices": [{"message": {
                "content": "Demand is uncertain. Buyers may want this."
            }}],
        }

    provider = PerplexityResearchProvider(api_key=KEY, transport=transport)
    result = provider.research([ResearchInput(kind="note", locator="x")])
    assert result.sources == ()
    assert all(claim.label != "FACT" for claim in result.claims)
    assert {claim.label for claim in result.claims} <= {
        "INFERENCE", "ASSUMPTION", "UNKNOWN",
    }


def test_an_out_of_range_citation_marker_is_not_a_fact(no_network):
    def transport(request, headers):
        return {
            "choices": [{"message": {"content": "A claim [9]."}}],
            "citations": ["https://source.test/one"],
        }

    provider = PerplexityResearchProvider(api_key=KEY, transport=transport)
    result = provider.research([ResearchInput(kind="note", locator="x")])
    assert result.claims[0].label == "INFERENCE"
    assert result.claims[0].source_digests


def test_empty_answer_and_malformed_payload_are_typed_failures(no_network):
    for payload in (
        {"choices": [{"message": {"content": ""}}]},
        {"error": {"message": "quota exhausted"}},
        ["not", "an", "object"],
    ):
        provider = PerplexityResearchProvider(
            api_key=KEY, transport=lambda _r, _h, p=payload: p
        )
        with pytest.raises(BusinessError) as excinfo:
            provider.research([ResearchInput(kind="note", locator="x")])
        assert excinfo.value.code == RESEARCH_FAILED_CODE


def test_settings_read_environment_and_configured_endpoint():
    cfg = _Cfg({"intent": {
        "perplexity_url": "https://proxy.test/chat/completions",
        "perplexity_model": "sonar-small",
    }})
    key, endpoint, model = perplexity_settings(
        cfg, environ={"PERPLEXITY_API_KEY": KEY}
    )
    assert key == KEY
    assert endpoint == "https://proxy.test/chat/completions"
    assert model == "sonar-small"

    disabled = _Cfg({"intent": {"perplexity_enabled": False}})
    key, _, _ = perplexity_settings(
        disabled, environ={"PERPLEXITY_API_KEY": KEY}
    )
    assert key == ""


def test_resolve_provider_builds_perplexity_and_keeps_local_default(no_network):
    local = resolve_provider()
    assert local.provider_id == RESEARCH.LOCAL_PROVIDER_ID

    provider = resolve_provider(
        "perplexity", cfg=_Cfg({}), environ={"PERPLEXITY_API_KEY": KEY}
    )
    assert isinstance(provider, PerplexityResearchProvider)
    assert provider.configured is True

    unconfigured = resolve_provider(
        "perplexity", cfg=_Cfg({}), environ={}
    )
    with pytest.raises(BusinessError) as excinfo:
        unconfigured.research([ResearchInput(kind="note", locator="x")])
    assert excinfo.value.code == RESEARCH_NOT_CONFIGURED_CODE

    with pytest.raises(BusinessError) as excinfo:
        resolve_provider("not-a-provider")
    assert excinfo.value.code == RESEARCH_UNAVAILABLE_CODE


# ---------------------------------------------------------------------------
# Run/append and idempotency
# ---------------------------------------------------------------------------
def test_run_appends_sources_evidence_and_is_idempotent(tmp_path, no_network):
    store = _store(tmp_path)
    tenant, session = _session(store)
    provider = _FakeProvider()

    first = RUN.run_research(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        query="do printable planners sell?",
        provider=provider,
    )
    assert first.created is True
    assert provider.calls == 1
    assert len(first.sources) == 1
    assert first.sources[0].status == "extracted"
    assert first.sources[0].locator == "https://source.test/planners"
    labels = {item.label for item in first.evidence}
    assert "FACT" in labels
    fact = next(item for item in first.evidence if item.label == "FACT")
    assert fact.source_id == first.sources[0].source_id
    assert fact.metadata["research_provider"] == PERPLEXITY_PROVIDER_ID

    bundle = warroom.get_bundle(store, tenant.tenant_id, session.session_id)
    assert bundle["session"].state == "intake"
    assert bundle["decisions"] == [] and bundle["tasks"] == []

    second = RUN.run_research(
        store,
        tenant_id=tenant.tenant_id,
        session_id=session.session_id,
        query="do printable planners sell?",
        provider=provider,
    )
    assert second.created is False
    assert provider.calls == 1, "a repeated query must not call the provider"
    assert second.source_ids == first.source_ids
    assert second.evidence_ids == first.evidence_ids

    after = warroom.get_bundle(store, tenant.tenant_id, session.session_id)
    assert len(after["sources"]) == 1
    assert len(after["evidence"]) == 2
    assert after["decisions"] == [] and after["tasks"] == []


def test_run_refuses_after_the_owner_decision(tmp_path, no_network):
    store = _store(tmp_path)
    tenant, session = _session(store)
    warroom.advance(store, tenant_id=tenant.tenant_id,
                    session_id=session.session_id, target="research")
    warroom.advance(store, tenant_id=tenant.tenant_id,
                    session_id=session.session_id, target="debate")
    warroom.advance(store, tenant_id=tenant.tenant_id,
                    session_id=session.session_id, target="synthesis")
    warroom.advance(store, tenant_id=tenant.tenant_id,
                    session_id=session.session_id, target="owner_decision")

    with pytest.raises(BusinessError) as excinfo:
        RUN.run_research(
            store, tenant_id=tenant.tenant_id,
            session_id=session.session_id, provider=_FakeProvider(),
        )
    assert excinfo.value.code == "BUSINESS_BAD_STATE"


def test_run_refuses_a_fact_without_a_returned_source(tmp_path, no_network):
    store = _store(tmp_path)
    tenant, session = _session(store)
    result = ResearchResult(
        provider=PERPLEXITY_PROVIDER_ID,
        inputs=(ResearchInput(kind="note", locator="x"),),
        sources=(),
        claims=(ResearchClaim(
            label="FACT", claim="unsupported", source_digests=("missing",),
        ),),
    )
    provider = _FakeProvider(result)
    with pytest.raises(BusinessError) as excinfo:
        RUN.run_research(
            store, tenant_id=tenant.tenant_id,
            session_id=session.session_id, provider=provider,
        )
    assert excinfo.value.code == "BUSINESS_WARROOM_UNVERIFIED_FACT"
    assert warroom.list_evidence(
        store, tenant.tenant_id, session.session_id) == []


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------
@pytest.fixture()
def endpoint_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    cfg = _Cfg(state_dir=tmp_path)
    store = BusinessStore.from_config(cfg)
    return cfg, store


def _call(cfg, method, path, *, body=None, query=None):
    return HC.dispatch(cfg, method, path, query=query or {}, body=body or {})


def test_research_route_is_registered_and_owner_gated(endpoint_env, monkeypatch):
    cfg, store = endpoint_env
    _code, created = _call(
        cfg, "POST", "/api/office/business/warroom/sessions",
        body={"title": "Planner", "owner": True, "idempotency_key": "wr-1"},
    )
    sid = created["session"]["session_id"]
    base = f"/api/office/business/warroom/sessions/{sid}"

    route, _params = HC.match("POST", f"{base}/research")
    assert route is not None
    assert route["handler"] == "h_business_warroom_research"

    code, out = _call(cfg, "POST", f"{base}/research",
                      body={"query": "do planners sell"})
    assert code == 403 and out["code"] == "BUSINESS_OWNER_ONLY"
    assert warroom.list_evidence(store, created["tenant_id"], sid) == []


def test_research_endpoint_appends_and_is_idempotent(endpoint_env, monkeypatch):
    cfg, store = endpoint_env
    _code, created = _call(
        cfg, "POST", "/api/office/business/warroom/sessions",
        body={"title": "Planner", "owner": True, "idempotency_key": "wr-1"},
    )
    sid = created["session"]["session_id"]
    tenant = created["tenant_id"]
    provider = _FakeProvider()
    monkeypatch.setattr(RUN, "resolve_provider", lambda *_a, **_k: provider)

    code, first = _call(
        cfg, "POST", f"/api/office/business/warroom/sessions/{sid}/research",
        body={"query": "do planners sell", "owner": True,
              "provider": "perplexity"},
    )
    assert code == 200, first
    assert first["created"] is True
    assert first["provider"] == PERPLEXITY_PROVIDER_ID
    assert first["source_ids"] and first["evidence_ids"]
    assert provider.calls == 1

    code, again = _call(
        cfg, "POST", f"/api/office/business/warroom/sessions/{sid}/research",
        body={"query": "do planners sell", "owner": True,
              "provider": "perplexity"},
    )
    assert code == 200, again
    assert again["created"] is False
    assert again["source_ids"] == first["source_ids"]
    assert again["evidence_ids"] == first["evidence_ids"]
    assert provider.calls == 1

    bundle = warroom.get_bundle(store, tenant, sid)
    assert bundle["decisions"] == [] and bundle["tasks"] == []


def test_research_endpoint_returns_typed_not_configured(endpoint_env,
                                                       monkeypatch):
    cfg, _store = endpoint_env
    monkeypatch.setattr(
        RESEARCH, "perplexity_settings",
        lambda *_a, **_k: (
            "", RESEARCH.DEFAULT_PERPLEXITY_URL, RESEARCH.DEFAULT_PERPLEXITY_MODEL,
        ),
    )
    _code, created = _call(
        cfg, "POST", "/api/office/business/warroom/sessions",
        body={"title": "Planner", "owner": True, "idempotency_key": "wr-1"},
    )
    sid = created["session"]["session_id"]
    code, out = _call(
        cfg, "POST", f"/api/office/business/warroom/sessions/{sid}/research",
        body={"query": "do planners sell", "owner": True},
    )
    assert code == 400
    assert out["code"] == RESEARCH_NOT_CONFIGURED_CODE
