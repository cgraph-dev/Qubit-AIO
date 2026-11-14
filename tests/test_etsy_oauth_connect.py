"""Etsy OAuth2 connection: PKCE, typed status, secure store, redaction.

Hermetic: a fake transport (no socket import reaches this file), a throwaway
token store under ``tmp_path`` and a fixed clock. It proves the module never
claims "connected" without a real token, never exposes a token in a status or
error, validates state/PKCE/expiry, and stores files with private modes.
"""
from __future__ import annotations

import json
import os
import stat
from urllib.parse import parse_qs, urlparse

import pytest

from voicebridge.integrations.etsy_auth_transport import (
    EtsyOAuthHttpTransport,
    EtsyTransportError,
)
from voicebridge.integrations.etsy_auth import (
    ETSY_EXCHANGE_FAILED,
    ETSY_NOT_CONFIGURED,
    ETSY_PKCE_MISMATCH,
    ETSY_STATE_INVALID,
    ETSY_TRANSPORT_ERROR,
    EtsyAuthError,
    EtsyOAuth,
    EtsyOAuthConfig,
    EtsyTokenStore,
    default_token_path,
    pkce_challenge,
)

pytestmark = pytest.mark.unit

CLIENT_ID = "etsy-client-id"
CLIENT_SECRET = "etsy-client-secret-value"
REDIRECT = "http://127.0.0.1:8890/qubit-office/etsy/callback"


class FakeTransport:
    """The injected provider seam: records forms, returns canned JSON."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls: list[tuple[str, str, dict, dict]] = []

    def request(self, method, url, *, form=None, headers=None):
        self.calls.append((method, url, dict(form or {}), dict(headers or {})))
        if not self.responses:
            raise AssertionError("unexpected Etsy token request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _config(**overrides) -> EtsyOAuthConfig:
    base = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT,
    }
    base.update(overrides)
    return EtsyOAuthConfig.from_mapping(base)


def _flow(tmp_path, transport=None, now=1_700_000_000.0):
    store = EtsyTokenStore(tmp_path / "etsy" / "oauth.json")
    flow = EtsyOAuth(
        _config(), transport=transport, store=store,
        clock=lambda: now,
    )
    return flow, store


def _token_response(**overrides):
    payload = {
        "access_token": "access-value",
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": "refresh-value",
        "scope": "shops_r listings_r",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Configuration and status
# ---------------------------------------------------------------------------
def test_not_configured_reports_the_missing_labels_and_no_connected_state(
        tmp_path):
    flow = EtsyOAuth(
        EtsyOAuthConfig(),
        store=EtsyTokenStore(tmp_path / "oauth.json"),
        clock=lambda: 1_700_000_000.0,
    )
    status = flow.status()
    assert status["state"] == "not_configured"
    assert status["connected"] is False
    assert status["configured"] is False
    assert status["missing"] == ["client_id", "client_secret", "redirect_uri"]
    # No token/secret-shaped value is present at all.
    assert "access_token" not in status
    assert "refresh_token" not in status
    assert CLIENT_SECRET not in json.dumps(status)


def test_status_is_authorization_required_with_config_and_no_token(tmp_path):
    flow, _store = _flow(tmp_path)
    status = flow.status()
    assert status["state"] == "authorization_required"
    assert status["connected"] is False
    assert status["pending"] is False
    assert status["missing"] == []


def test_env_overrides_resolve_config(monkeypatch):
    monkeypatch.setenv("QUBIT_ETSY_CLIENT_ID", "env-id")
    monkeypatch.setenv("QUBIT_ETSY_CLIENT_SECRET", "env-secret")
    monkeypatch.setenv("QUBIT_ETSY_REDIRECT_URI", "https://example.test/cb")
    cfg = EtsyOAuthConfig.from_config({})
    assert cfg.configured is True
    assert cfg.client_id == "env-id"
    assert cfg.redirect_uri == "https://example.test/cb"


def test_default_token_path_is_under_qubit_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("QUBIT_HOME", str(home))
    path = default_token_path()
    assert path == home / "voice" / "state" / "integrations" / "etsy" / "oauth.json"


# ---------------------------------------------------------------------------
# Authorization URL, state and PKCE
# ---------------------------------------------------------------------------
def test_begin_builds_pkce_url_and_stores_pending_privately(tmp_path):
    flow, store = _flow(tmp_path)
    started = flow.begin_authorization(state="fixed-state")
    parsed = urlparse(started["url"])
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.etsy.com"
    assert query["response_type"] == ["code"]
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [REDIRECT]
    assert query["state"] == ["fixed-state"]
    assert query["code_challenge_method"] == ["S256"]
    challenge = query["code_challenge"][0]
    assert len(challenge) >= 43

    pending = store.load_pending()
    assert pending is not None
    assert pending["state"] == "fixed-state"
    assert pkce_challenge(pending["code_verifier"]) == challenge
    # The verifier is only in the private store, never in the URL.
    assert pending["code_verifier"] not in started["url"]
    assert "code_verifier" not in started

    mode = stat.S_IMODE(os.stat(store.pending_path).st_mode)
    assert mode == 0o600
    dir_mode = stat.S_IMODE(os.stat(store.pending_path.parent).st_mode)
    assert dir_mode == 0o700


def test_begin_refuses_when_not_configured(tmp_path):
    flow = EtsyOAuth(
        EtsyOAuthConfig(client_id=CLIENT_ID),
        store=EtsyTokenStore(tmp_path / "oauth.json"),
        clock=lambda: 1_700_000_000.0,
    )
    with pytest.raises(EtsyAuthError) as exc:
        flow.begin_authorization()
    assert exc.value.code == ETSY_NOT_CONFIGURED
    assert "client_secret" in exc.value.detail


# ---------------------------------------------------------------------------
# Code exchange
# ---------------------------------------------------------------------------
def test_exchange_success_stores_tokens_and_reports_connected(tmp_path):
    transport = FakeTransport([_token_response()])
    flow, store = _flow(tmp_path, transport)
    flow.begin_authorization(state="state-1")
    status = flow.complete_authorization("the-code", "state-1")
    assert status["state"] == "connected"
    assert status["connected"] is True
    assert status["has_refresh"] is True
    assert status["expires_at"] == 1_700_000_000.0 + 3600
    assert "access-value" not in json.dumps(status)
    assert "refresh-value" not in json.dumps(status)

    record = store.load()
    assert record["access_token"] == "access-value"
    assert record["refresh_token"] == "refresh-value"
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert store.load_pending() is None

    method, url, form, _headers = transport.calls[0]
    assert method == "POST"
    assert url.endswith("/v3/public/oauth/token")
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "the-code"
    assert form["code_verifier"]
    assert form["client_secret"] == CLIENT_SECRET


def test_exchange_rejects_a_state_mismatch(tmp_path):
    transport = FakeTransport([_token_response()])
    flow, _store = _flow(tmp_path, transport)
    flow.begin_authorization(state="right-state")
    with pytest.raises(EtsyAuthError) as exc:
        flow.complete_authorization("code", "wrong-state")
    assert exc.value.code == ETSY_STATE_INVALID
    assert transport.calls == []

    # A non-ASCII state is a typed refusal, not a hmac TypeError.
    with pytest.raises(EtsyAuthError) as exc2:
        flow.complete_authorization("code", "stäté")
    assert exc2.value.code == ETSY_STATE_INVALID


def test_exchange_rejects_tampered_pkce(tmp_path):
    transport = FakeTransport([_token_response()])
    flow, store = _flow(tmp_path, transport)
    flow.begin_authorization(state="state-1")
    pending = store.load_pending()
    pending["code_challenge"] = "not-the-derived-challenge"
    store.save_pending(pending)
    with pytest.raises(EtsyAuthError) as exc:
        flow.complete_authorization("code", "state-1")
    assert exc.value.code == ETSY_PKCE_MISMATCH
    assert transport.calls == []


def test_expired_pending_state_is_refused(tmp_path):
    transport = FakeTransport([_token_response()])
    flow, _store = _flow(tmp_path, transport)
    flow.begin_authorization(state="state-1")
    # 601 s later the pending authorization is stale.
    with pytest.raises(EtsyAuthError) as exc:
        flow.complete_authorization("code", "state-1", now=1_700_000_601.0)
    assert exc.value.code == ETSY_STATE_INVALID
    assert transport.calls == []


def test_exchange_failure_is_typed_and_stores_nothing(tmp_path):
    transport = FakeTransport([{"error": "invalid_grant"}])
    flow, store = _flow(tmp_path, transport)
    flow.begin_authorization(state="state-1")
    with pytest.raises(EtsyAuthError) as exc:
        flow.complete_authorization("bad-code", "state-1")
    assert exc.value.code == ETSY_EXCHANGE_FAILED
    assert "invalid_grant" in exc.value.detail
    assert "access-value" not in exc.value.detail
    assert store.load() is None


def test_missing_transport_is_a_typed_refusal(tmp_path):
    flow, _store = _flow(tmp_path, None)
    flow.begin_authorization(state="state-1")
    with pytest.raises(EtsyAuthError) as exc:
        flow.complete_authorization("code", "state-1")
    assert exc.value.code == ETSY_TRANSPORT_ERROR


# ---------------------------------------------------------------------------
# Expiry and refresh
# ---------------------------------------------------------------------------
def test_status_reports_expired_then_refresh_reconnects(tmp_path):
    transport = FakeTransport([_token_response(access_token="new-access",
                                               expires_in=1800)])
    now = 1_700_000_000.0
    flow, store = _flow(tmp_path, transport, now=now)
    store.save({
        "schema": "qubit-etsy-oauth/1",
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "token_type": "Bearer",
        "expires_at": now - 10,
        "obtained_at": now - 4000,
        "scope": "shops_r",
    })
    expired = flow.status()
    assert expired["state"] == "expired"
    assert expired["connected"] is False
    assert expired["has_refresh"] is True

    refreshed = flow.refresh()
    assert refreshed["state"] == "connected"
    assert refreshed["expires_at"] == now + 1800
    assert store.load()["access_token"] == "new-access"
    _method, _url, form, _headers = transport.calls[0]
    assert form["grant_type"] == "refresh_token"
    assert form["refresh_token"] == "old-refresh"


def test_refresh_without_a_refresh_value_requires_reauthorization(tmp_path):
    flow, store = _flow(tmp_path, FakeTransport())
    store.save({
        "schema": "qubit-etsy-oauth/1",
        "access_token": "only-access",
        "refresh_token": "",
        "expires_at": 1_700_000_000.0 - 1,
        "obtained_at": 1_699_000_000.0,
    })
    with pytest.raises(EtsyAuthError) as exc:
        flow.refresh()
    assert exc.value.code == "etsy_authorization_required"


def test_disconnect_clears_the_store(tmp_path):
    transport = FakeTransport([_token_response()])
    flow, store = _flow(tmp_path, transport)
    flow.begin_authorization(state="state-1")
    flow.complete_authorization("code", "state-1")
    status = flow.disconnect()
    assert status["state"] == "authorization_required"
    assert store.load() is None
    assert store.load_pending() is None


# ---------------------------------------------------------------------------
# Corrupt store and error redaction
# ---------------------------------------------------------------------------
def test_corrupt_store_is_a_typed_error(tmp_path):
    store = EtsyTokenStore(tmp_path / "oauth.json")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json", encoding="utf-8")
    flow = EtsyOAuth(_config(), store=store, clock=lambda: 1_700_000_000.0)
    status = flow.status()
    assert status["state"] == "error"
    assert status["connected"] is False
    assert status["code"] == "etsy_store_error"
    assert CLIENT_SECRET not in json.dumps(status)


def test_transport_exception_message_cannot_leak_a_token(tmp_path):
    class Exploding:
        def request(self, *_args, **_kwargs):
            raise RuntimeError("Authorization: Bearer super-secret-token")

    flow, store = _flow(tmp_path, Exploding())  # type: ignore[arg-type]
    flow.begin_authorization(state="state-1")
    with pytest.raises(EtsyAuthError) as exc:
        flow.complete_authorization("code", "state-1")
    assert exc.value.code == ETSY_EXCHANGE_FAILED
    assert "super-secret-token" not in exc.value.detail
    assert "RuntimeError" in exc.value.detail
    assert store.load() is None


# ---------------------------------------------------------------------------
# The optional HTTP transport
# ---------------------------------------------------------------------------
def test_http_transport_refuses_non_https_and_non_post_without_network():
    transport = EtsyOAuthHttpTransport()
    with pytest.raises(EtsyTransportError):
        transport.request("POST", "http://www.etsy.com/oauth/token")
    with pytest.raises(EtsyTransportError):
        transport.request("GET", "https://api.etsy.com/v3/public/oauth/token")
