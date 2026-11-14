"""P8-02 — MCP security requirements with an injected fake transport.

No network: OAuth uses the product's real lifecycle over a fake provider
transport; schema/allowlist/egress/output checks call the real brokered client
and capability runtime helpers with in-process records. Each test asserts a
denial or a bound, never a happy path.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from voicebridge.business import dynamic_catalog as dc
from voicebridge.business.errors import BusinessError
from voicebridge.integrations.etsy_auth import (
    EtsyAuthError,
    EtsyOAuth,
    EtsyOAuthConfig,
    EtsyTokenStore,
    pkce_challenge,
)
from voicebridge import mcp_client as mc

from _p8_security_helpers import (
    MCP,
    NOW,
    Recorder,
    grant,
    policy_for,
    runtime,
    sandbox_profile,
    scope,
    snapshot,
    store,
)


def _code(exc: pytest.ExceptionInfo[BusinessError]) -> str:
    return exc.value.code


class _FakeTransport:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[tuple] = []

    def request(self, method, url, *, form=None, headers=None):
        self.calls.append((method, url, dict(form or {}), dict(headers or {})))
        return dict(self.response)


def _oauth(tmp_path, transport=None) -> EtsyOAuth:
    path = tmp_path / "oauth.json"
    config = EtsyOAuthConfig(
        client_id="client-p8",
        client_secret="secret-p8",  # noqa: S105 - hermetic fixture, never sent
        redirect_uri="http://127.0.0.1:8765/callback",
    )
    return EtsyOAuth(
        config,
        transport=transport,
        store=EtsyTokenStore(path),
        clock=lambda: NOW,
    )


def _save_pending(oauth: EtsyOAuth, *, state: str = "state-1",
                  verifier: str = "v" * 43, challenge: str | None = None,
                  expires_at: float = NOW + 60) -> None:
    oauth._store.save_pending(
        {
            "schema": "etsy-oauth-pending/1",
            "state": state,
            "code_verifier": verifier,
            "code_challenge": challenge or pkce_challenge(verifier),
            "created_at": NOW,
            "expires_at": expires_at,
            "redirect_uri": oauth.config.redirect_uri,
        }
    )


# -- OAuth 2.1 + PKCE S256 ------------------------------------------------
def test_mcp_oauth_pkce_s256_challenge_is_exact(tmp_path):
    oauth = _oauth(tmp_path)
    verifier = "a" * 43 + "-._~"
    started = oauth.begin_authorization(now=NOW, code_verifier=verifier)
    query = parse_qs(urlsplit(started["url"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [pkce_challenge(verifier)]
    assert query["state"] == [started["state"]]

    # A pending verifier whose challenge does not match is refused before any
    # provider request (transport is never called).
    transport = _FakeTransport({"access_token": "x"})
    oauth = _oauth(tmp_path / "mismatch", transport=transport)
    _save_pending(oauth, challenge="b" * 43)
    with pytest.raises(EtsyAuthError) as exc:
        oauth.complete_authorization("code-1", "state-1", now=NOW)
    assert exc.value.code == "etsy_pkce_mismatch"
    assert transport.calls == []


# -- exact state / CSRF, pending TTL --------------------------------------
def test_mcp_oauth_state_and_pending_window_fail_closed(tmp_path):
    oauth = _oauth(tmp_path)
    # No pending authorization: a callback cannot be accepted.
    with pytest.raises(EtsyAuthError) as exc:
        oauth.complete_authorization("code", "state", now=NOW)
    assert exc.value.code == "etsy_state_invalid"

    _save_pending(oauth, state="expected")
    with pytest.raises(EtsyAuthError) as exc:
        oauth.complete_authorization("code", "wrong", now=NOW)
    assert exc.value.code == "etsy_state_invalid"

    expired = _oauth(tmp_path / "expired")
    _save_pending(expired, state="expected", expires_at=NOW - 1)
    with pytest.raises(EtsyAuthError) as exc:
        expired.complete_authorization("code", "expected", now=NOW)
    assert exc.value.code == "etsy_state_invalid"


# -- token/code replay ----------------------------------------------------
def test_mcp_oauth_callback_cannot_be_replayed(tmp_path):
    transport = _FakeTransport(
        {"access_token": "tok-p8", "expires_in": 3600, "scope": "read"}
    )
    oauth = _oauth(tmp_path, transport=transport)
    started = oauth.begin_authorization(now=NOW)
    status = oauth.complete_authorization(
        "code-1", started["state"], now=NOW
    )
    assert status["connected"] is True
    assert len(transport.calls) == 1

    # The pending state was consumed; replaying the same callback is refused
    # and never reaches the token endpoint again.
    with pytest.raises(EtsyAuthError) as exc:
        oauth.complete_authorization("code-1", started["state"], now=NOW)
    assert exc.value.code == "etsy_state_invalid"
    assert len(transport.calls) == 1


# -- exact issuer (RFC 8414 / RFC 9728) -----------------------------------
def test_mcp_oauth_issuer_must_match_exactly():
    from qubit_runtime.tools.mcp_oauth_provider import metadata_issued_by_origin

    class _Meta:
        issuer = "https://as.example"

    class _Doc:
        status_code = 200
        url = "https://as.example/.well-known/oauth-authorization-server/tenant"

    assert metadata_issued_by_origin(
        _Meta(), "https://as.example/tenant", _Doc()
    ) is True

    class _Redirected(_Doc):
        url = "https://evil.example/.well-known/oauth-authorization-server/tenant"

    class _OtherIssuer:
        issuer = "https://other.example"

    class _RootDoc(_Doc):
        url = "https://as.example/.well-known/oauth-authorization-server"

    assert metadata_issued_by_origin(
        _Meta(), "https://as.example/tenant", _Redirected()
    ) is False
    assert metadata_issued_by_origin(
        _OtherIssuer(), "https://as.example/tenant", _Doc()
    ) is False
    assert metadata_issued_by_origin(
        _Meta(), "https://as.example/tenant", _RootDoc()
    ) is False


# -- complete tool schema hash -------------------------------------------
def test_mcp_complete_tool_schema_hash_covers_input_output_annotations():
    base = dc.mcp_capability(
        "docs", "search",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        output_schema={"type": "object"},
        annotations={"readOnlyHint": True},
    )
    same = dc.mcp_capability(
        "docs", "search",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        output_schema={"type": "object"},
        annotations={"readOnlyHint": True},
    )
    flipped_output = dc.mcp_capability(
        "docs", "search",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        output_schema={"type": "string"},
        annotations={"readOnlyHint": True},
    )
    flipped_annotations = dc.mcp_capability(
        "docs", "search",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        output_schema={"type": "object"},
        annotations={"readOnlyHint": False},
    )
    assert base.schema_hash == same.schema_hash
    assert base.schema_hash != flipped_output.schema_hash
    assert base.schema_hash != flipped_annotations.schema_hash
    assert base.input_schema_hash and base.output_schema_hash


# -- server allowlist + version pin --------------------------------------
def _record(name: str, *, allow=("search",), version="1.0",
            enabled=True, transport="http") -> dict:
    return {
        "name": name,
        "version": version,
        "enabled": enabled,
        "transport": transport,
        "url": "https://mcp.example/rpc",
        "capabilities": list(allow),
    }


def test_mcp_server_allowlist_and_version_are_enforced():
    server = _record("docs")
    mc.check_invocable(server, "search")  # allowed tool passes the gate
    with pytest.raises(mc.McpClientError) as exc:
        mc.check_invocable(server, "write")
    assert exc.value.code == mc.NOT_ALLOWED_CODE

    with pytest.raises(mc.McpClientError) as exc:
        mc.check_invocable(_record("empty", allow=()), "search")
    assert exc.value.code == mc.NOT_ALLOWED_CODE

    with pytest.raises(mc.McpClientError) as exc:
        mc.check_invocable(_record("off", enabled=False), "search")
    assert exc.value.code == mc.DISABLED_CODE

    with pytest.raises(mc.McpClientError) as exc:
        mc.check_invocable(_record("bad", transport="smtp"), "search")
    assert exc.value.code == mc.TRANSPORT_CODE

    # The catalog carries the pinned server version into every record, so a
    # version change is a visible pin change (not a silent upgrade).
    catalog = dc.DynamicCapabilityCatalog(
        [
            dc.FunctionCatalogSource(
                "mcp",
                lambda: dc.mcp_server_capabilities(
                    {
                        "name": "docs",
                        "version": "2.0",
                        "allowlist": ["search"],
                        "tools": [{"name": "search", "inputSchema": {}}],
                    }
                ),
            )
        ]
    )
    assert catalog.get("mcp:docs:search").version == "2.0"


# -- SSRF / private / metadata / redirect --------------------------------
def test_mcp_egress_blocks_loopback_private_and_metadata(tmp_path):
    profile = sandbox_profile(
        tmp_path,
        allow_network=True,
        egress_allowlist=("example.com",),
    )
    for url in (
        "http://127.0.0.1/rpc",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://10.0.0.5/rpc",
    ):
        with pytest.raises(BusinessError):
            profile.check_egress(url)
    # A redirect into a private destination is refused too.
    with pytest.raises(BusinessError):
        profile.check_redirect(
            "https://example.com/rpc", "http://127.0.0.1/rpc"
        )

    # The MCP category is a network category: the runtime validates the URL
    # before the adapter is called.
    bos = store(tmp_path)
    tenant, business = scope(bos)
    recorder = Recorder()
    rt = runtime(
        tmp_path, bos, adapters={MCP: recorder}, sandbox=profile,
        snap=snapshot(tenant.tenant_id, grants=(grant(MCP, "docs", "search"),)),
    )
    with pytest.raises(BusinessError):
        rt.call(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            subject_id="agent:test",
            actor="agent:test",
            capability_id=MCP,
            policy=policy_for(MCP),
            idempotency_key="ssrf-1",
            payload={"url": "http://169.254.169.254/latest/meta-data/"},
            now=NOW,
        )
    assert recorder.count == 0


# -- oversized / untrusted output ----------------------------------------
def test_mcp_oversized_args_and_output_are_bounded(tmp_path):
    # Oversized tool arguments are refused before the transport.
    with pytest.raises(mc.McpClientError) as exc:
        mc._bounded_params({"blob": "x" * (mc.MAX_ARGS_BYTES + 1)})
    assert exc.value.code == mc.PROTOCOL_CODE

    # A huge server response is truncated and labelled redacted, never passed
    # through unbounded.
    normalized = mc._normalize(
        _record("docs"),
        "search",
        "http",
        {"content": [{"type": "text", "text": "x" * (mc.MAX_OUTPUT_CHARS + 500)}]},
        [],
        1.0,
    )
    assert len(normalized["text"]) <= mc.MAX_OUTPUT_CHARS
    assert normalized["redacted"] is True

    # The capability runtime caps adapter output before it can reach a prompt.
    bos = store(tmp_path)
    tenant, business = scope(bos)
    rt = runtime(
        tmp_path, bos,
        adapters={
            MCP: lambda request, capability: {
                "items": ["y" * 1000 for _ in range(1000)]
            }
        },
        snap=snapshot(tenant.tenant_id, grants=(grant(MCP, "docs", "search"),)),
    )
    with pytest.raises(BusinessError) as exc:
        rt.call(
            tenant_id=tenant.tenant_id,
            business_id=business.business_id,
            subject_id="agent:test",
            actor="agent:test",
            capability_id=MCP,
            policy=policy_for(MCP),
            idempotency_key="big-1",
            payload={"query": "x"},
            now=NOW,
        )
    assert _code(exc) == "CAPABILITY_RUNTIME_OUTPUT_TOO_LARGE"


# -- per-server scope ----------------------------------------------------
def test_mcp_tool_and_credential_scope_is_per_server():
    server_a = _record("alpha", allow=("search",))
    server_b = _record("beta", allow=("write",))
    mc.check_invocable(server_a, "search")
    mc.check_invocable(server_b, "write")
    # A tool allowed on one server is not allowed on another.
    with pytest.raises(mc.McpClientError) as exc:
        mc.check_invocable(server_b, "search")
    assert exc.value.code == mc.NOT_ALLOWED_CODE
    with pytest.raises(mc.McpClientError) as exc:
        mc.check_invocable(server_a, "write")
    assert exc.value.code == mc.NOT_ALLOWED_CODE

    # The catalog identity is namespaced per server, so the same tool name on
    # two servers is two distinct capabilities.
    one = dc.mcp_capability("alpha", "search", input_schema={})
    two = dc.mcp_capability("beta", "search", input_schema={})
    assert one.capability_id == "mcp:alpha:search"
    assert two.capability_id == "mcp:beta:search"
    assert json.dumps(one.as_dict(), sort_keys=True) != json.dumps(
        two.as_dict(), sort_keys=True
    )


# -- exact redirect configuration ----------------------------------------
def test_mcp_oauth_requires_configured_redirect_uri(tmp_path):
    # Redirect URIs are server-side configuration, never a client-supplied
    # callback value; a missing exact redirect cannot start a flow.
    config = EtsyOAuthConfig(
        client_id="client-p8", client_secret="secret-p8", redirect_uri=""
    )
    oauth = EtsyOAuth(
        config,
        transport=_FakeTransport({"access_token": "x"}),
        store=EtsyTokenStore(tmp_path / "oauth.json"),
        clock=lambda: NOW,
    )
    with pytest.raises(EtsyAuthError) as exc:
        oauth.begin_authorization(now=NOW)
    assert exc.value.code == "etsy_not_configured"

    started = _oauth(tmp_path / "ok").begin_authorization(now=NOW)
    query = parse_qs(urlsplit(started["url"]).query)
    assert query["redirect_uri"] == ["http://127.0.0.1:8765/callback"]


# -- RFC 8707 resource / audience and per-server token storage -----------
def test_mcp_oauth_resource_audience_is_exact_and_per_server(tmp_path):
    from mcp.shared.auth_utils import (
        check_resource_allowed,
        resource_url_from_server_url,
    )
    from qubit_runtime.tools.mcp_oauth import QubitTokenStorage

    server_url = "https://mcp.example/tenant"
    resource = resource_url_from_server_url(server_url)
    assert resource == server_url
    assert check_resource_allowed(
        requested_resource=resource, configured_resource=server_url
    ) is True
    assert check_resource_allowed(
        requested_resource=resource,
        configured_resource="https://evil.example/tenant",
    ) is False

    alpha = QubitTokenStorage("alpha", qubit_home=tmp_path)
    beta = QubitTokenStorage("beta", qubit_home=tmp_path)
    assert alpha._tokens_path() != beta._tokens_path()
    assert alpha._tokens_path().name == "alpha.json"
    assert beta._tokens_path().name == "beta.json"
