"""Packet 8U — brokered, SSRF-safe web fetch + typed-unavailable search.

Hermetic: the HTTP layer (``voicebridge.web_tool._open``) is monkeypatched, so
these tests make no live network call and no real DNS lookup (public targets
are IP literals). They prove the tool reuses the canonical
``voicebridge.websites`` URL/SSRF policy instead of a second blocklist, and
that every refusal is typed.
"""
from __future__ import annotations

import json

import pytest

from voicebridge import web_tool as WT


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


class _Resp:
    def __init__(self, body=b"", *, status=200,
                 url="http://93.184.216.34/page", ctype="text/html"):
        self._body = body
        self._status = status
        self._url = url
        self.headers = {"Content-Type": ctype}

    def getcode(self):
        return self._status

    def geturl(self):
        return self._url

    def read(self, n=-1):
        if n is None or n < 0:
            return self._body
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeHTTP:
    """Scripted HTTP boundary: each call pops the next response/exception."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []

    def __call__(self, req, timeout):
        self.calls.append((req, timeout))
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _redirect_exc(location, code=302):
    from urllib.error import HTTPError

    return HTTPError("http://93.184.216.34/", code, "Found",
                     {"Location": location}, None)


def _boom(req, timeout):
    raise AssertionError("HTTP layer must not be reached")


# --------------------------------------------------------------- happy path
def test_fetch_public_returns_bounded_redacted_text(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWX"
    body = (b"<html><head><title>Report</title></head><body>"
            b"<script>steal()</script>Public status ok. key="
            + secret.encode() + b" end.</body></html>")
    http = _FakeHTTP([_Resp(body)])
    monkeypatch.setattr(WT, "_open", http)

    out = WT.fetch(cfg, "http://93.184.216.34/page",
                   max_bytes=4096, timeout=5.0)
    assert out["state"] == WT.OK
    assert out["code"] == "WEB_FETCH_OK"
    assert "Public status ok." in out["text"]
    assert "steal()" not in out["text"]            # script stripped
    assert secret not in out["text"]
    assert "[REDACTED]" in out["text"]
    assert len(out["text"]) <= WT.MAX_RESULT_CHARS
    assert out["receipt"]["origin"] == "http://93.184.216.34"
    assert out["receipt"]["bytes_read"] == len(body)
    assert secret not in json.dumps(out)           # never in receipt/result


def test_fetch_text_is_bounded_to_result_cap(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    body = b"<p>" + b"a" * (WT.MAX_RESULT_CHARS * 3) + b"</p>"
    monkeypatch.setattr(WT, "_open", _FakeHTTP([_Resp(body)]))
    out = WT.fetch(cfg, "http://93.184.216.34/tiny", max_bytes=200_000,
                   timeout=5.0)
    assert len(out["text"]) <= WT.MAX_RESULT_CHARS


# --------------------------------------------------------- scheme/creds/query
@pytest.mark.parametrize("url", [
    "ftp://93.184.216.34/",
    "file:///etc/passwd",
    "gopher://93.184.216.34/",
])
def test_scheme_allowlist_refused(tmp_path, monkeypatch, url):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(WT, "_open", _boom)
    with pytest.raises(WT.WebToolError) as exc:
        WT.fetch(cfg, url)
    assert exc.value.code == WT.SCHEME_CODE


def test_credential_url_refused(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(WT, "_open", _boom)
    with pytest.raises(WT.WebToolError) as exc:
        WT.fetch(cfg, "http://user:pass@93.184.216.34/")
    assert exc.value.code == WT.CREDENTIALS_CODE


@pytest.mark.parametrize("url", [
    "http://93.184.216.34/?token=abc",
    "http://93.184.216.34/?api_key=abc",
    "http://93.184.216.34/?session_id=abc",
])
def test_query_token_url_refused(tmp_path, monkeypatch, url):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(WT, "_open", _boom)
    with pytest.raises(WT.WebToolError) as exc:
        WT.fetch(cfg, url)
    assert exc.value.code == WT.QUERY_TOKEN_CODE


# ------------------------------------------------------------------ SSRF
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://127.0.0.1:8080/admin",
    "http://10.0.0.1/",
    "http://192.168.1.10/",
    "http://172.16.5.5/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
    "http://0.0.0.0/",
])
def test_private_loopback_metadata_linklocal_refused(
        tmp_path, monkeypatch, url):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(WT, "_open", _boom)
    with pytest.raises(WT.WebToolError) as exc:
        WT.fetch(cfg, url)
    assert exc.value.code == WT.BLOCKED_HOST_CODE


def test_redirect_to_private_refused_not_followed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    http = _FakeHTTP([_redirect_exc("http://127.0.0.1/secret"),
                      _Resp(b"<p>never</p>")])
    monkeypatch.setattr(WT, "_open", http)
    with pytest.raises(WT.WebToolError) as exc:
        WT.fetch(cfg, "http://93.184.216.34/start")
    assert exc.value.code == WT.REDIRECT_BLOCKED_CODE
    assert len(http.steps) == 1                     # second hop never fetched


def test_redirect_to_public_is_followed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    http = _FakeHTTP([
        _redirect_exc("http://93.184.216.34/final"),
        _Resp(b"<p>done</p>", url="http://93.184.216.34/final"),
    ])
    monkeypatch.setattr(WT, "_open", http)
    out = WT.fetch(cfg, "http://93.184.216.34/start", max_redirects=3)
    assert out["state"] == WT.OK
    assert out["redirects"] == 1
    assert "done" in out["text"]


def test_too_many_redirects_refused(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    http = _FakeHTTP([_redirect_exc("http://93.184.216.34/next")
                      for _ in range(5)])
    monkeypatch.setattr(WT, "_open", http)
    with pytest.raises(WT.WebToolError) as exc:
        WT.fetch(cfg, "http://93.184.216.34/start", max_redirects=2)
    assert exc.value.code == WT.TOO_MANY_REDIRECTS_CODE


# ------------------------------------------------------------ bounds/errors
def test_oversized_body_refused(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(WT, "_open",
                        _FakeHTTP([_Resp(b"x" * 200, ctype="text/plain")]))
    with pytest.raises(WT.WebToolError) as exc:
        WT.fetch(cfg, "http://93.184.216.34/big", max_bytes=64)
    assert exc.value.code == WT.OVERSIZED_CODE


def test_timeout_refused(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    def _slow(req, timeout):
        raise TimeoutError("slow")

    monkeypatch.setattr(WT, "_open", _slow)
    with pytest.raises(WT.WebToolError) as exc:
        WT.fetch(cfg, "http://93.184.216.34/slow", timeout=1.0)
    assert exc.value.code == WT.TIMEOUT_CODE


def test_unsupported_content_type_refused(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        WT, "_open", _FakeHTTP([_Resp(b"\x89PNG", ctype="image/png")]))
    with pytest.raises(WT.WebToolError) as exc:
        WT.fetch(cfg, "http://93.184.216.34/img.png")
    assert exc.value.code == WT.CONTENT_TYPE_CODE


def test_unreachable_refused(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from urllib.error import URLError

    monkeypatch.setattr(
        WT, "_open", _FakeHTTP([URLError("no route")]))
    with pytest.raises(WT.WebToolError) as exc:
        WT.fetch(cfg, "http://93.184.216.34/down")
    assert exc.value.code == WT.UNREACHABLE_CODE


def test_bad_bounds_refused(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(WT, "_open", _boom)
    with pytest.raises(WT.WebToolError) as exc:
        WT.fetch(cfg, "http://93.184.216.34/", max_bytes=0)
    assert exc.value.code == WT.BAD_INPUT_CODE
    with pytest.raises(WT.WebToolError) as exc:
        WT.fetch(cfg, "http://93.184.216.34/", timeout=0)
    assert exc.value.code == WT.BAD_INPUT_CODE


# ------------------------------------------------------------------- estop
def test_estop_blocks_fetch_and_search(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge.power import power_control_store

    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="user")
    monkeypatch.setattr(WT, "_open", _boom)
    with pytest.raises(WT.WebToolError) as exc:
        WT.fetch(cfg, "http://93.184.216.34/")
    assert exc.value.code == WT.ESTOP_CODE
    with pytest.raises(WT.WebToolError) as exc:
        WT.search(cfg, "anything")
    assert exc.value.code == WT.ESTOP_CODE


# ------------------------------------------------------------------ search
def test_search_without_provider_is_typed_unavailable(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(WT, "_open", _boom)
    out = WT.search(cfg, "python asyncio")
    assert out["state"] == WT.UNAVAILABLE
    assert out["code"] == WT.SEARCH_UNAVAILABLE_CODE
    assert out["results"] == []
    assert out["count"] == 0
    assert "receipt" not in out                 # nothing was fabricated


def test_search_unknown_provider_typed_unavailable(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.data["web"] = {"search": {"provider": "nope",
                                  "endpoint": "http://93.184.216.34/s"}}
    out = WT.search(cfg, "query")
    assert out["state"] == WT.UNAVAILABLE
    assert out["code"] == WT.SEARCH_UNAVAILABLE_CODE


def test_search_provider_returns_bounded_redacted_results(tmp_path,
                                                          monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    secret = "sk-ZYXWVUTSRQPONMLKJIHGFEDC"
    monkeypatch.setenv("SEARCH_KEY", secret)
    cfg.data["web"] = {"search": {
        "provider": "searxng",
        "endpoint": "http://93.184.216.34/search",
        "api_key_env": "SEARCH_KEY",
        "api_key_header": "X-API-Key",
    }}
    payload = {
        "results": [
            {"title": "Qubit", "url": "https://example.org/a",
             "content": f"hello {secret}"},
            {"title": "private", "url": "http://127.0.0.1/x",
             "content": "must be skipped"},
        ],
    }
    http = _FakeHTTP([_Resp(json.dumps(payload).encode(),
                            ctype="application/json")])
    monkeypatch.setattr(WT, "_open", http)

    out = WT.search(cfg, "qubit", limit=10)
    assert out["state"] == WT.OK
    assert out["count"] == 1                         # private result skipped
    assert out["results"][0]["title"] == "Qubit"
    assert secret not in json.dumps(out)
    assert "[REDACTED]" in out["results"][0]["snippet"]
    # the API key was sent as a header but never serialized into the receipt
    sent = dict(http.calls[0][0].headers)
    assert any(secret in str(v) for v in sent.values())


def test_search_provider_error_is_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.data["web"] = {"search": {"provider": "searxng",
                                  "endpoint": "http://93.184.216.34/search"}}
    monkeypatch.setattr(WT, "_open",
                        _FakeHTTP([_Resp(b"not json",
                                         ctype="application/json")]))
    out = WT.search(cfg, "query")
    assert out["state"] == "FAILED"
    assert out["code"] == WT.SEARCH_PROVIDER_CODE
    assert out["results"] == []


# ---------------------------------------------------------------- agent tool
def test_run_dispatches_known_tools_and_refuses_unknown(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(WT, "_open",
                        _FakeHTTP([_Resp(b"<p>ok</p>")]))
    out = WT.run(cfg, "web.fetch", {"url": "http://93.184.216.34/"})
    assert out["state"] == WT.OK
    assert "web.fetch" in WT.TOOL_SPECS
    assert "web.search" in WT.TOOL_SPECS
    with pytest.raises(WT.WebToolError) as exc:
        WT.run(cfg, "web.delete", {})
    assert exc.value.code == WT.BAD_INPUT_CODE


# ------------------------------------------------------------- x_search
def test_x_search_is_typed_unavailable_and_never_touches_network(
        tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    cfg.data["web"] = {"x_search": {"provider": "xai"}}
    monkeypatch.setattr(WT, "_open", _boom)
    out = WT.x_search(cfg, "qubit release")
    assert out["state"] == WT.UNAVAILABLE
    assert out["code"] == WT.X_SEARCH_UNAVAILABLE_CODE
    assert out["results"] == []
    assert out["provider"] == "xai"
    assert "No request was attempted." in out["explanation"]


def test_run_dispatches_x_search(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = WT.run(cfg, "web.x_search", {"query": "hello"})
    assert out["state"] == WT.UNAVAILABLE
    assert "web.x_search" in WT.TOOL_SPECS


def test_x_search_rejects_empty_query(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(WT, "_open", _boom)
    with pytest.raises(WT.WebToolError) as exc:
        WT.x_search(cfg, "   ")
    assert exc.value.code == WT.BAD_INPUT_CODE


def test_x_search_respects_estop(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(WT, "estop_engaged", lambda cfg: True)
    out = WT.x_search(cfg, "q")
    assert out["state"] == "DENIED"
    assert out["code"] == WT.ESTOP_CODE
