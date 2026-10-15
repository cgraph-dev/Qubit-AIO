"""Regression tests for Habblaud's authenticated EventSource stream URL."""
from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge import webui as W  # noqa: E402


def _handler(*, peer="127.0.0.1", host="127.0.0.1:8890", referer=None,
             path="/api/stream", token="secret"):
    h = object.__new__(W.WebUIHandler)
    h.path = path
    h.client_address = (peer, 1234)
    h.headers = {"Host": host}  # type: ignore[assignment]
    if referer:
        h.headers["Referer"] = referer
    h.server = type("S", (), {"app": type("A", (), {"api_token": token})()})()  # type: ignore[assignment]
    h._api_token_ok = lambda: False
    return h


def test_valid_query_token_authorizes_eventsource_even_without_referer():
    """The dedicated legacy query-token path remains accepted for migration."""
    h = _handler(path="/api/stream?token=secret")
    assert h._office_app_caller_ok() is True


def test_valid_header_token_still_authorizes_eventsource():
    h = _handler(path="/api/stream")
    h.headers["X-Qubit-Session-Token"] = "secret"
    h._api_token_ok = lambda: True
    assert h._office_app_caller_ok() is True


def test_invalid_query_token_does_not_fall_through_to_loopback_exemption():
    h = _handler(path="/api/stream?token=wrong",
                 referer="http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/")
    assert h._office_app_caller_ok() is False


def test_invalid_query_token_rejected_for_dedicated_stream_endpoint():
    h = _handler(path="/api/stream?token=wrong")
    assert h._office_app_caller_ok() is False


def test_invalid_query_token_rejected_for_snapshot_endpoint():
    h = _handler(path="/api/snapshot?token=wrong")
    assert h._office_app_caller_ok() is False


def test_configured_token_does_not_allow_missing_query_token():
    h = _handler(referer="http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/")
    assert h._office_app_caller_ok() is False


def test_token_query_parses_encoded_and_unrelated_parameters():
    h = _handler(path="/api/stream?foo=1&token=sec%20ret")
    h.server.app.api_token = "sec ret"  # type: ignore[attr-defined]
    assert h._office_app_caller_ok() is True


def test_query_auth_does_not_mutate_the_normal_api_auth_policy():
    ok, reason = W.token_ok("secret", "secret", query_token_present=True)
    assert ok is False
    assert reason == "query-token-rejected"


def test_unconfigured_local_app_referer_remains_allowed():
    h = _handler(referer="http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/",
                 token="")
    assert h._office_app_caller_ok() is True


def test_non_loopback_client_cannot_use_no_token_exception():
    h = _handler(peer="192.168.1.10", token="")
    assert h._office_app_caller_ok() is False


def test_stream_helper_builds_a_secret_free_same_origin_url(tmp_path):
    from test_office_game_shell import _run_app

    got = _run_app(tmp_path, """
      globalThis.window = {__QUBIT_SESSION_TOKEN__: 'secret /?'};
      return {url: AP.officeStreamUrl(), absent: (() => {
        globalThis.window = {};
        return AP.officeStreamUrl();
      })()};
    """)
    assert isinstance(got, dict)
    assert got["url"] == "/api/stream"
    assert got["absent"] == "/api/stream"



