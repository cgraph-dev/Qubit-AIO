"""Reference-dashboard parity lane (fix4): media proxy + MoA truthfulness.

Covers the two reference routes the audit found missing/mismatched on the
Qubit dashboard:

* ``GET /api/media/proxy`` — implemented with the reference body shape but a
  stricter per-hop SSRF policy (host allowlist + redirect re-validation +
  size/content-type bounds). These tests are fully hermetic: DNS resolution
  and the HTTP layer are injected, so no live network is touched.
* ``GET /api/model/moa`` — a real read-only surface behind the same opt-in gate
  the model picker uses. Qubit's engine executes MoA; the compat handler serves
  the profile's actual presets once ``moa.presets`` is enabled, and an explicit
  opt-in refusal otherwise (never the merged defaults for an unconfigured
  profile, and never a writable dashboard PUT).
"""
from __future__ import annotations

import base64
import socket
import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class _FakeResponse:
    def __init__(self, body: bytes = b"", ctype: str = "image/png",
                 status: int = 200, location: str = "") -> None:
        self._body = body
        self._offset = 0
        self.status = status
        headers = {"Content-Type": ctype}
        if location:
            headers["Location"] = location
        self.headers = headers

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return "https://v3.fal.media/media/hash"

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = len(self._body) - self._offset
        chunk = self._body[self._offset:self._offset + n]
        self._offset += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


def _redirect(location: str) -> HTTPError:
    return HTTPError("https://v3.fal.media/media/hash", 302, "Found",
                     {"Location": location}, None)


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    """Keep the SSRF policy hermetic; individual tests can override it."""
    from voicebridge import websites

    monkeypatch.setattr(websites, "blocked_host_reason",
                        lambda host, resolve=True: None)


def test_media_proxy_requires_a_url():
    from voicebridge import qubit_compat as QC

    code, out = QC.h_media_proxy(None, {}, {}, {})
    assert code == 400 and out["ok"] is False
    assert out["code"] == "MEDIA_PROXY_BAD_URL"


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/secret.png",
    "http://localhost/secret.png",
    "http://169.254.169.254/latest/meta-data.png",
    "https://evil.example/secret.png",
    "https://evilfal.media/secret.png",
    "ftp://v3.fal.media/secret.png",
])
def test_media_proxy_rejects_disallowed_hosts_and_schemes(monkeypatch, url):
    from voicebridge import qubit_compat as QC

    def _must_not_fetch(*_a, **_k):
        raise AssertionError("the proxy must not open a disallowed target")

    monkeypatch.setattr(QC, "_media_proxy_open", _must_not_fetch)
    code, out = QC.h_media_proxy(None, {"url": url}, {}, {})
    assert code in (400, 403), (url, code, out)
    assert out["ok"] is False
    assert out["code"] in {"MEDIA_PROXY_BAD_URL",
                           "MEDIA_PROXY_HOST_NOT_ALLOWED"}


def test_media_proxy_rejects_explicit_non_image_extension(monkeypatch):
    from voicebridge import qubit_compat as QC

    monkeypatch.setattr(QC, "_media_proxy_open",
                        lambda *_a, **_k: pytest.fail("must not fetch"))
    code, out = QC.h_media_proxy(
        None, {"url": "https://v3.fal.media/media/evil.exe"}, {}, {})
    assert code == 415 and out["code"] == "MEDIA_PROXY_UNSUPPORTED_TYPE"


def test_media_proxy_fetches_allowlisted_image_without_credentials(monkeypatch):
    from voicebridge import qubit_compat as QC

    payload = b"\x89PNG\r\n\x1a\n" + b"image-bytes"
    seen = []

    def _open(req, timeout):
        seen.append(req)
        assert timeout == QC._MEDIA_PROXY_TIMEOUT_S
        return _FakeResponse(payload, "image/png")

    monkeypatch.setattr(QC, "_media_proxy_open", _open)
    code, out = QC.h_media_proxy(
        None, {"url": "https://v3.fal.media/media/abc123"}, {}, {})

    assert code == 200 and out["ok"] is True
    assert out["data_url"].startswith("data:image/png;base64,")
    encoded = out["data_url"].split(",", 1)[1]
    assert base64.b64decode(encoded) == payload
    assert len(seen) == 1
    # No cookie/authorization/none of Qubit's own credentials are forwarded.
    assert seen[0].get_header("Authorization") is None
    assert seen[0].get_header("Cookie") is None


def test_media_proxy_rejects_non_image_content_type(monkeypatch):
    from voicebridge import qubit_compat as QC

    monkeypatch.setattr(
        QC, "_media_proxy_open",
        lambda *_a, **_k: _FakeResponse(b"<html>", "text/html"))
    code, out = QC.h_media_proxy(
        None, {"url": "https://v3.fal.media/media/hash"}, {}, {})
    assert code == 415 and out["code"] == "MEDIA_PROXY_UNSUPPORTED_TYPE"


def test_media_proxy_bounds_the_response(monkeypatch):
    from voicebridge import qubit_compat as QC

    monkeypatch.setattr(QC, "_MEDIA_PROXY_MAX_BYTES", 8)
    monkeypatch.setattr(
        QC, "_media_proxy_open",
        lambda *_a, **_k: _FakeResponse(b"123456789", "image/png"))
    code, out = QC.h_media_proxy(
        None, {"url": "https://v3.fal.media/media/hash"}, {}, {})
    assert code == 413 and out["code"] == "MEDIA_PROXY_OVERSIZED"


def test_media_proxy_follows_only_an_allowlisted_redirect(monkeypatch):
    from voicebridge import qubit_compat as QC

    calls = []

    def _open(req, _timeout):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise _redirect("https://v3.fal.media/media/final.png")
        return _FakeResponse(b"final", "image/png")

    monkeypatch.setattr(QC, "_media_proxy_open", _open)
    code, out = QC.h_media_proxy(
        None, {"url": "https://v3.fal.media/media/hash"}, {}, {})
    assert code == 200, out
    assert calls == ["https://v3.fal.media/media/hash",
                     "https://v3.fal.media/media/final.png"]


def test_media_proxy_refuses_a_redirect_into_the_local_network(monkeypatch):
    from voicebridge import qubit_compat as QC

    calls = []

    def _open(req, _timeout):
        calls.append(req.full_url)
        raise _redirect("http://127.0.0.1:8890/api/health")

    monkeypatch.setattr(QC, "_media_proxy_open", _open)
    code, out = QC.h_media_proxy(
        None, {"url": "https://v3.fal.media/media/hash"}, {}, {})
    assert code == 403 and out["code"] == "MEDIA_PROXY_REDIRECT_BLOCKED"
    assert len(calls) == 1


def test_media_proxy_refuses_an_allowlisted_host_resolving_private(monkeypatch):
    from voicebridge import qubit_compat as QC
    from voicebridge import websites

    monkeypatch.setattr(websites, "blocked_host_reason",
                        lambda host, resolve=True: "PRIVATE_NETWORK")
    monkeypatch.setattr(QC, "_media_proxy_open",
                        lambda *_a, **_k: pytest.fail("must not fetch"))
    code, out = QC.h_media_proxy(
        None, {"url": "https://v3.fal.media/media/hash"}, {}, {})
    assert code == 403 and out["code"] == "MEDIA_PROXY_HOST_NOT_ALLOWED"


def test_media_proxy_maps_timeout_and_http_errors(monkeypatch):
    from voicebridge import qubit_compat as QC

    def _timeout(*_a, **_k):
        raise socket.timeout("timed out")

    monkeypatch.setattr(QC, "_media_proxy_open", _timeout)
    code, out = QC.h_media_proxy(
        None, {"url": "https://v3.fal.media/media/hash"}, {}, {})
    assert code == 504 and out["code"] == "MEDIA_PROXY_TIMEOUT"

    def _http_500(*_a, **_k):
        raise HTTPError("https://v3.fal.media/media/hash", 500, "err",
                        {"Content-Type": "text/plain"}, None)

    monkeypatch.setattr(QC, "_media_proxy_open", _http_500)
    code, out = QC.h_media_proxy(
        None, {"url": "https://v3.fal.media/media/hash"}, {}, {})
    assert code == 502 and out["code"] == "MEDIA_PROXY_HTTP_ERROR"


def test_media_proxy_route_is_registered_and_read_authoritative():
    from voicebridge import qubit_compat as QC
    from voicebridge import webui

    route, _params = QC.match("GET", "/api/media/proxy")
    assert route is not None and route["handler"] == "h_media_proxy"
    assert callable(QC.HANDLERS.get("h_media_proxy"))
    assert webui.qubit_read_authority("/api/media/proxy") is True


def test_model_moa_serves_the_real_read_only_state(tmp_path, monkeypatch):
    """MoA is a real engine behind an opt-in gate, not an always-empty refusal.

    The vendored runtime executes MoA (``agent/moa_loop.py`` /
    ``qubit_cli/moa_config.py``) and the model picker advertises the ``moa``
    provider once the RAW config enables a preset. The compat GET mirrors that
    gate: real presets when opted in, an explicit opt-in refusal otherwise
    (never the merged DEFAULT_CONFIG preset for a profile that did not opt in).
    """
    from voicebridge import qubit_compat as QC

    home = tmp_path / "qh"
    home.mkdir()

    # Unconfigured profile: empty state, exact reason, no fabricated preset.
    monkeypatch.setenv("QUBIT_HOME", str(home))
    code, body = QC.h_model_moa(None, {}, {}, {})
    assert code == 200 and body["ok"] is True
    assert body["enabled"] is False
    assert body["presets"] == {}
    assert body["reference_models"] == []
    assert body["read_only"] is True
    assert "opt-in" in body["detail"]
    assert "qubit moa configure" in body["detail"]
    assert "openai-codex" not in str(body) and "openrouter" not in str(body)

    # Opted-in profile: the real preset the engine would run.
    (home / "config.yaml").write_text(
        "moa:\n"
        "  default_preset: studio\n"
        "  presets:\n"
        "    studio:\n"
        "      reference_models:\n"
        "        - {provider: openrouter, model: deepseek/deepseek-v4-pro}\n"
        "      aggregator: {provider: openrouter, model: anthropic/claude-opus-4.8}\n"
        "      enabled: true\n"
    )
    code, body = QC.h_model_moa(None, {}, {}, {})
    assert code == 200 and body["ok"] is True, body
    assert body["enabled"] is True
    assert body["read_only"] is True
    assert body["default_preset"] == "studio"
    assert set(body["presets"]) == {"studio"}
    preset = body["presets"]["studio"]
    assert preset["reference_models"][0]["provider"] == "openrouter"
    assert preset["aggregator"]["model"] == "anthropic/claude-opus-4.8"
    assert "qubit moa configure" in body["detail"]

    # Writes stay a typed read-only refusal, naming the supported path.
    code, refused = QC.moa_put(None, {}, {"reference_models": []}, {})
    assert code == 501 and refused["code"] == "MOA_READ_ONLY"
    assert "qubit moa" in refused["qubit_counterpart"]


def test_raw_media_proxy_route_auth_and_allowlist(tmp_path, monkeypatch):
    """The literal reference path is served, token-gated, and still allowlisted.

    The SPA reaches the handler through ``/agent/api``; the reference clients
    (and the parity audit) call the literal ``/api/media/proxy``. This drives a
    real ephemeral loopback server so the front-door branch is exercised.
    """
    import json
    import urllib.error
    import urllib.request

    from voicebridge import qubit_compat as QC
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    monkeypatch.setattr(
        QC, "_media_proxy_open",
        lambda *_a, **_k: _FakeResponse(b"image-bytes", "image/png"))

    class StubOrch:
        pass

    orch = StubOrch()
    orch.cfg = Config.load()
    orch.cfg.data["webui"].update(host="127.0.0.1", token="tok123")

    w = WebUI(orchestrator=orch, port=0, state_dir=tmp_path)
    w.start()
    try:
        base = f"http://127.0.0.1:{w._server.server_address[1]}"

        def get(query: str, token: str = ""):
            req = urllib.request.Request(f"{base}/api/media/proxy{query}")
            if token:
                req.add_header("X-Api-Token", token)
            return urllib.request.urlopen(req, timeout=5)

        with pytest.raises(urllib.error.HTTPError) as ei:
            get("?url=https://v3.fal.media/media/hash")
        assert ei.value.code == 401
        ei.value.close()

        with pytest.raises(urllib.error.HTTPError) as ei2:
            get("?url=https://evil.example/x.png", token="tok123")
        assert ei2.value.code == 403
        ei2.value.close()

        r = get("?url=https://v3.fal.media/media/hash", token="tok123")
        try:
            body = json.loads(r.read())
        finally:
            r.close()
        assert body["ok"] is True
        assert body["data_url"].startswith("data:image/png;base64,")
    finally:
        w.stop()
