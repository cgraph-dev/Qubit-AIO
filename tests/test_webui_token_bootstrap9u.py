"""The dashboard token bootstrap is loopback-only and not cross-site."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import webui as ui  # noqa: E402

TOKEN = "tok-bootstrap-123"


class _App:
    api_token = TOKEN

    def __init__(self):
        import threading

        self._office_nonces = {}
        self._office_nonce_lock = threading.Lock()


def _handler(*, peer="127.0.0.1", host="127.0.0.1", headers=None):
    handler = object.__new__(ui.WebUIHandler)
    handler.client_address = (peer, 50000)
    handler.headers = {"Host": host}
    handler.headers.update(headers or {})
    handler.server = type("S", (), {"app": _App()})()
    handler._captured = {}

    def _send(code, body, ctype="text/plain", extra_headers=None):
        handler._captured.update(
            code=code, body=bytes(body), ctype=ctype,
            extra_headers=extra_headers or [])

    handler._send = _send
    return handler


def _index(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>Qubit</title></head>"
        "<body><div id='root'></div></body></html>", encoding="utf-8")
    return dist


def _served(tmp_path, **kwargs):
    handler = _handler(**kwargs)
    handler._qubit_dist = lambda: _index(tmp_path)
    handler._serve_qubit_index()
    return handler._captured["body"].decode("utf-8")


def test_loopback_navigation_receives_the_token(tmp_path):
    body = _served(tmp_path, headers={"Sec-Fetch-Site": "none",
                                      "Sec-Fetch-Mode": "navigate"})
    assert f'window.__QUBIT_SESSION_TOKEN__="{TOKEN}"' in body


def test_fetch_metadata_absent_keeps_local_clients_working(tmp_path):
    assert f'window.__QUBIT_SESSION_TOKEN__="{TOKEN}"' in _served(tmp_path)


def test_a_cross_site_browser_request_gets_no_token(tmp_path):
    body = _served(tmp_path, headers={"Sec-Fetch-Site": "cross-site",
                                      "Sec-Fetch-Mode": "cors"})
    assert '__QUBIT_SESSION_TOKEN__=""' in body
    assert TOKEN not in body


def test_a_foreign_origin_gets_no_token(tmp_path):
    body = _served(tmp_path, headers={"Origin": "https://evil.example",
                                      "Sec-Fetch-Site": "same-origin"})
    assert '__QUBIT_SESSION_TOKEN__=""' in body
    assert TOKEN not in body


def test_own_loopback_origin_keeps_the_token(tmp_path):
    body = _served(tmp_path, headers={"Origin": "http://127.0.0.1:8890",
                                      "Sec-Fetch-Site": "same-origin",
                                      "Sec-Fetch-Mode": "navigate"})
    assert f'window.__QUBIT_SESSION_TOKEN__="{TOKEN}"' in body


def test_non_loopback_peer_never_receives_the_token(tmp_path):
    body = _served(tmp_path, peer="192.168.1.10", host="127.0.0.1",
                   headers={"Sec-Fetch-Site": "none"})
    assert TOKEN not in body


def test_office_iframe_document_honours_the_same_bootstrap_rule(tmp_path):
    target = tmp_path / "index.html"
    target.write_text("<html><head></head><body></body></html>", encoding="utf-8")

    handler = _handler(headers={"Sec-Fetch-Site": "same-origin",
                                "Sec-Fetch-Mode": "navigate"})
    handler._issue_office_nonce = lambda: "nonce"
    handler._serve_app_html(target)
    assert TOKEN in handler._captured["body"].decode("utf-8")

    handler = _handler(headers={"Sec-Fetch-Site": "cross-site",
                                "Sec-Fetch-Mode": "cors"})
    handler._issue_office_nonce = lambda: "nonce"
    handler._serve_app_html(target)
    assert TOKEN not in handler._captured["body"].decode("utf-8")
