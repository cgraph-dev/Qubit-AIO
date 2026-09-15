"""Dashboard /docs page: curated user guide over authenticated backends.

Hermes DocsPage equivalent: an allowlisted, secret-scanned doc set with
safe client rendering (DOM-only, no innerHTML; same-origin/http links
only). Unknown ids and traversal fail typed; API stays header-token
authenticated.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _start(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "local"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui, ui._server.server_address[1]


def _http(port, method, path, body=None, token="test-token"):
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 method=method, data=data)
    if token:
        req.add_header("X-Api-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def test_docs_page_served_with_shell(tmp_path):
    ui, port = _start(tmp_path)
    try:
        # Qubit rebrand: /docs is the Qubit Docs workspace, served at root.
        import http.client
        for route in ("/docs", "/docs.html"):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("GET", route)
            resp = conn.getresponse()
            body = resp.read()
            assert resp.status == 200, (route, resp.status)
            assert b'id="root"' in body, route
        # The labelled /classic/ fallback still serves the static docs page.
        status, ctype, body = _http(port, "GET", "/classic/docs")
        assert status == 200, status
        assert "text/html" in ctype
        html = body.decode("utf-8", "replace")
        assert 'id="main-content"' in html
        assert 'href="/docs" aria-current="page"' in html
        assert "/static/vb.js" in html and "/static/ui.js" in html
    finally:
        ui.stop()


def test_docs_api_index_and_fetch(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, _, body = _http(port, "GET", "/api/docs")
        assert status == 200
        index = json.loads(body.decode())
        assert index["docs"], index
        ids = [d["id"] for d in index["docs"]]
        assert ids == ["howto", "audio", "system", "migration"], ids
        for doc_id in ids:
            status, ctype, body = _http(port, "GET", f"/api/docs/{doc_id}")
            assert status == 200, doc_id
            doc = json.loads(body.decode())
            assert doc["id"] == doc_id and doc["markdown"], doc_id
            assert "<script" not in doc["markdown"].lower(), doc_id
    finally:
        ui.stop()


def test_docs_api_rejects_unknown_and_traversal(tmp_path):
    ui, port = _start(tmp_path)
    try:
        for probe in ("/api/docs/nope", "/api/docs/..%2f..%2fsecret",
                      "/api/docs/.", "/api/docs/%2e%2e"):
            status, _, _ = _http(port, "GET", probe)
            assert status == 404, (probe, status)
    finally:
        ui.stop()


def test_docs_api_requires_header_token(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, _, _ = _http(port, "GET", "/api/docs", token=None)
        assert status == 401
        status, _, _ = _http(port, "GET", "/api/docs/howto",
                             token="wrong")
        assert status == 401
    finally:
        ui.stop()


def test_user_docs_allowlist_matches_shipped_files():
    from voicebridge import webui as W

    assert [d[0] for d in W.USER_DOCS] == ["howto", "audio", "system",
                                          "migration"]
    for _, _, filename in W.USER_DOCS:
        assert (REPO_ROOT / "docs" / filename).is_file(), filename
