"""The dashboard session token authenticates under every accepted header name.

Rebrand regression: the reader compared only ``X-Hermes-Session-Token`` while the
desktop shell and the SPA send ``X-Qubit-Session-Token``, so every authenticated
call returned a bare 401 while /api/status (token-free) kept answering. The fix
accepts both names (``_SESSION_HEADER_ALIASES``); these tests pin that BOTH names
authenticate with the exact token, and that a wrong or missing token is still
rejected under either name — accepting an alias must not widen what is accepted
from a credential, only which header carries it.
"""

import pytest


_ALIAS_HEADERS = ("X-Qubit-Session-Token", "X-Hermes-Session-Token")
_HEADER_NAMES = _ALIAS_HEADERS


def _request(headers: dict):
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/api/env",
                    "headers": raw, "query_string": b""})


def test_alias_list_covers_both_names():
    """The primary constant must be one of the accepted aliases, else the
    single-name readers (self-test, SPA injection, ticket principal) disagree
    with the validator."""
    from qubit_cli import web_server

    assert web_server._SESSION_HEADER_NAME in web_server._SESSION_HEADER_ALIASES
    assert set(_HEADER_NAMES) <= set(web_server._SESSION_HEADER_ALIASES)


@pytest.mark.parametrize("header_name", _HEADER_NAMES)
def test_valid_token_via_either_header_authenticates(header_name, monkeypatch):
    from qubit_cli import web_server

    monkeypatch.setattr(web_server, "_SESSION_TOKEN", "s3cret-token")
    assert web_server._has_valid_session_token(_request({header_name: "s3cret-token"})) is True


@pytest.mark.parametrize("header_name", _HEADER_NAMES)
def test_wrong_token_via_either_header_is_rejected(header_name, monkeypatch):
    from qubit_cli import web_server

    monkeypatch.setattr(web_server, "_SESSION_TOKEN", "s3cret-token")
    assert web_server._has_valid_session_token(_request({header_name: "wrong-token"})) is False


@pytest.mark.parametrize("header_name", _HEADER_NAMES)
def test_empty_token_via_either_header_is_rejected(header_name, monkeypatch):
    from qubit_cli import web_server

    monkeypatch.setattr(web_server, "_SESSION_TOKEN", "s3cret-token")
    assert web_server._has_valid_session_token(_request({header_name: ""})) is False


def test_no_token_rejected(monkeypatch):
    from qubit_cli import web_server

    monkeypatch.setattr(web_server, "_SESSION_TOKEN", "s3cret-token")
    assert web_server._has_valid_session_token(_request({})) is False
    # An unrelated header that merely looks like a credential carries nothing.
    assert web_server._has_valid_session_token(
        _request({"X-Session-Token": "s3cret-token"})) is False


def test_valid_alias_does_not_excuse_a_bad_bearer(monkeypatch):
    """A present-but-wrong alias header must not fall through to a Bearer that
    also happens to be wrong."""
    from qubit_cli import web_server

    monkeypatch.setattr(web_server, "_SESSION_TOKEN", "s3cret-token")
    assert web_server._has_valid_session_token(_request({
        "X-Qubit-Session-Token": "wrong-token",
        "Authorization": "Bearer also-wrong",
    })) is False


@pytest.mark.parametrize("header_name", _HEADER_NAMES)
def test_bearer_fallback_still_requires_exact_token(header_name, monkeypatch):
    from qubit_cli import web_server

    monkeypatch.setattr(web_server, "_SESSION_TOKEN", "s3cret-token")
    assert web_server._has_valid_session_token(
        _request({"Authorization": "Bearer s3cret-token"})) is True
    assert web_server._has_valid_session_token(
        _request({"Authorization": "Bearer wrong-token"})) is False
    assert web_server._has_valid_session_token(
        _request({"Authorization": "s3cret-token"})) is False
    # Both at once: a good alias wins even when the Bearer is garbage.
    assert web_server._has_valid_session_token(_request({
        header_name: "s3cret-token", "Authorization": "Basic dXNlcjpwYXNz"})) is True


def test_file_ticket_session_token_reads_either_header(monkeypatch):
    """``session_token_of`` (file-ticket binding) reads the same alias list."""
    from starlette.applications import Starlette

    from qubit_cli import web_server
    from qubit_cli.web_server_file_tickets import session_token_of

    monkeypatch.setattr(web_server, "_SESSION_TOKEN", "s3cret-token")
    app = Starlette()
    app.state.auth_required = False
    for header_name in _HEADER_NAMES:
        request = _request({header_name: "s3cret-token"})
        request.scope["app"] = app
        assert session_token_of(request) == "s3cret-token"


@pytest.mark.parametrize("header_name", _HEADER_NAMES)
def test_http_endpoint_authenticates_via_either_header(header_name, monkeypatch):
    """End to end through the real app gate: the alias reaches a token-gated
    /api route, and the same header with a wrong token does not."""
    try:
        from fastapi.testclient import TestClient
    except ImportError:  # pragma: no cover - fastapi is a hard dependency
        pytest.skip("fastapi/starlette not installed")

    from qubit_cli import web_server

    monkeypatch.setattr(web_server, "_SESSION_TOKEN", "s3cret-token")
    client = TestClient(web_server.app)
    assert client.get("/api/env", headers={header_name: "s3cret-token"}).status_code == 200
    assert client.get("/api/env", headers={header_name: "wrong-token"}).status_code == 401
    assert client.get("/api/env").status_code == 401
