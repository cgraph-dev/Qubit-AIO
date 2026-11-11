"""Business/Office bridge for the Etsy OAuth connection.

Hermetic: a fake injected transport, a throwaway ``QUBIT_HOME`` and a temp
``business.db``. The routes must never fabricate a connected state, must gate
the mutation on explicit owner intent, must not echo a token/code/secret, and
must never reach the network without the injected transport.
"""
from __future__ import annotations

import json

import pytest

from voicebridge import qubit_compat as HC
from voicebridge import webui as W
from voicebridge.business import bridge as BRIDGE
from voicebridge.integrations.etsy_auth import default_token_path

pytestmark = pytest.mark.unit

CLIENT_ID = "client-id"
CLIENT_SECRET = "client-secret-value"
REDIRECT = "http://127.0.0.1:8890/qubit-office/etsy/callback"


class _Cfg:
    """Minimal Config stand-in: state_dir plus a ``get`` for ``etsy.*``."""

    def __init__(self, state_dir):
        self._state = state_dir

    def state_dir(self):
        return self._state

    def get(self, *path, default=None):
        return default


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, *, form=None, headers=None):
        self.calls.append((method, url, dict(form or {})))
        return self.response


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    monkeypatch.setenv("QUBIT_ETSY_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("QUBIT_ETSY_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("QUBIT_ETSY_REDIRECT_URI", REDIRECT)
    return _Cfg(tmp_path), home


def _call(cfg, method, path, *, body=None, query=None):
    return HC.dispatch(cfg, method, path, query=query or {}, body=body or {})


def _token_response():
    return {
        "access_token": "access-value",
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": "refresh-value",
        "scope": "shops_r listings_r",
    }


def test_routes_are_registered():
    for method, path in (
        ("GET", "/api/office/business/etsy/status"),
        ("POST", "/api/office/business/etsy/authorize"),
        ("POST", "/api/office/business/etsy/complete"),
        ("POST", "/api/office/business/etsy/disconnect"),
    ):
        route, _params = HC.match(method, path)
        assert route is not None, f"{method} {path} has no compat route"


def test_status_route_is_a_qubit_read_authority():
    assert W.qubit_read_authority("/api/office/business/etsy/status")


def test_status_is_not_configured_when_settings_are_empty(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    for key in (
        "QUBIT_ETSY_CLIENT_ID",
        "QUBIT_ETSY_CLIENT_SECRET",
        "QUBIT_ETSY_REDIRECT_URI",
    ):
        monkeypatch.delenv(key, raising=False)
    code, payload = _call(_Cfg(tmp_path), "GET",
                          "/api/office/business/etsy/status")
    assert code == 200
    etsy = payload["etsy"]
    assert etsy["state"] == "not_configured"
    assert etsy["connected"] is False
    assert etsy["missing"] == ["client_id", "client_secret", "redirect_uri"]


def test_configured_status_is_authorization_required(env):
    cfg, _home = env
    code, payload = _call(cfg, "GET", "/api/office/business/etsy/status")
    assert code == 200
    etsy = payload["etsy"]
    assert etsy["state"] == "authorization_required"
    assert etsy["connected"] is False
    assert etsy["missing"] == []
    assert CLIENT_SECRET not in json.dumps(payload)


def test_authorize_requires_owner_intent(env):
    cfg, _home = env
    code, payload = _call(cfg, "POST", "/api/office/business/etsy/authorize", body={})
    assert code == 403
    assert payload["code"] == "BUSINESS_OWNER_ONLY"


def test_authorize_returns_a_pkce_url_and_never_a_verifier(env):
    cfg, _home = env
    code, payload = _call(
        cfg, "POST", "/api/office/business/etsy/authorize",
        body={"owner": True},
    )
    assert code == 200
    etsy = payload["etsy"]
    assert etsy["url"].startswith("https://www.etsy.com/oauth/connect?")
    assert "code_challenge=" in etsy["url"]
    assert etsy["state"]
    assert "code_verifier" not in json.dumps(payload)
    pending = default_token_path().with_name("pending.json")
    assert pending.exists()


def test_complete_without_owner_is_refused(env):
    cfg, _home = env
    code, payload = _call(
        cfg, "POST", "/api/office/business/etsy/complete",
        body={"code": "c", "state": "s"},
    )
    assert code == 403
    assert payload["code"] == "BUSINESS_OWNER_ONLY"


def test_complete_exchanges_with_the_injected_transport(env, monkeypatch):
    cfg, home = env
    transport = FakeTransport(_token_response())
    monkeypatch.setattr(BRIDGE, "_etsy_transport", lambda: transport)

    _code, started = _call(
        cfg, "POST", "/api/office/business/etsy/authorize",
        body={"owner": True},
    )
    state = started["etsy"]["state"]
    code, payload = _call(
        cfg, "POST", "/api/office/business/etsy/complete",
        body={"owner": True, "code": "auth-code", "state": state},
    )
    assert code == 200
    etsy = payload["etsy"]
    assert etsy["state"] == "connected"
    assert etsy["connected"] is True
    assert etsy["has_refresh"] is True
    # No token, code, verifier or client secret is echoed to the UI.
    body = json.dumps(payload)
    for secret in (
        "access-value", "refresh-value", CLIENT_SECRET, "auth-code",
        "code_verifier",
    ):
        assert secret not in body

    stored = json.loads(default_token_path().read_text(encoding="utf-8"))
    assert stored["access_token"] == "access-value"
    assert default_token_path().is_relative_to(home)
    assert transport.calls[0][2]["code"] == "auth-code"


def test_complete_state_mismatch_is_a_typed_409(env, monkeypatch):
    cfg, _home = env
    monkeypatch.setattr(
        BRIDGE, "_etsy_transport", lambda: FakeTransport(_token_response()))
    _code, started = _call(
        cfg, "POST", "/api/office/business/etsy/authorize",
        body={"owner": True},
    )
    assert started["etsy"]["state"]
    code, payload = _call(
        cfg, "POST", "/api/office/business/etsy/complete",
        body={"owner": True, "code": "auth-code", "state": "wrong"},
    )
    assert code == 409
    assert payload["code"] == "etsy_state_invalid"


def test_complete_missing_transport_is_typed(env, monkeypatch):
    cfg, _home = env
    monkeypatch.setattr(BRIDGE, "_etsy_transport", lambda: None)
    _code, started = _call(
        cfg, "POST", "/api/office/business/etsy/authorize",
        body={"owner": True},
    )
    code, payload = _call(
        cfg, "POST", "/api/office/business/etsy/complete",
        body={"owner": True, "code": "auth-code",
              "state": started["etsy"]["state"]},
    )
    assert code == 502
    assert payload["code"] == "etsy_transport_error"


def test_disconnect_clears_the_token(env, monkeypatch):
    cfg, _home = env
    monkeypatch.setattr(
        BRIDGE, "_etsy_transport", lambda: FakeTransport(_token_response()))
    _code, started = _call(
        cfg, "POST", "/api/office/business/etsy/authorize",
        body={"owner": True},
    )
    _code, _connected = _call(
        cfg, "POST", "/api/office/business/etsy/complete",
        body={"owner": True, "code": "auth-code",
              "state": started["etsy"]["state"]},
    )
    assert default_token_path().exists()

    code, payload = _call(
        cfg, "POST", "/api/office/business/etsy/disconnect",
        body={"owner": True},
    )
    assert code == 200
    assert payload["etsy"]["state"] == "authorization_required"
    assert not default_token_path().exists()
