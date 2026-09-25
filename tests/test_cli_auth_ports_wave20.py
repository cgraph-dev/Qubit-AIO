"""W9 auth-port slice — ported missing ``qubit_cli`` modules (from pin ``d337b736``).

Covers the three genuinely-missing modules ported this wave, plus the dependency
symbols they needed on the vendored tree:

* ``qubit_cli/auth_codex_browser.py``  ← ``hermes_cli/auth_codex_browser.py``
* ``qubit_cli/auth_openrouter.py``     ← ``hermes_cli/auth_openrouter.py``
* ``qubit_cli/shared_session_attach.py`` ← ``hermes_cli/shared_session_attach.py``
* ``auth_device_flow`` loopback helpers + ``auth_constants`` OpenRouter constants
  (the runtime dependencies the three modules import).

Behavior is hermetic: fake HTTP servers on ephemeral ports replace the real
authorize/token endpoints, and QUBIT_HOME is a temp dir. The operator's live home
and the reference at ``127.0.0.1:9119`` are never touched. No M1/M2/VERIFIED claim.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "qubit_runtime"


@pytest.fixture(autouse=True)
def _vendored_import(tmp_path, monkeypatch):
    """Put the vendored fork on sys.path and redirect QUBIT_HOME to a temp dir."""
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qh"))
    if str(RUNTIME_ROOT) not in sys.path:
        sys.path.insert(0, str(RUNTIME_ROOT))
    yield


def _jwt(email: str) -> str:
    b64 = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()  # noqa: E731
    return f"{b64(b'{}')}.{b64(json.dumps({'email': email}).encode())}.sig"


# ---------------------------------------------------------------------------
# Dependency symbols (loopback helpers / OpenRouter constants) resolve
# ---------------------------------------------------------------------------

def test_loopback_helpers_are_real_pkce_and_callback():
    from qubit_cli.auth_device_flow import (
        _bind_loopback_callback_server, _make_loopback_callback_handler, _pkce_code_challenge,
        _pkce_code_verifier, _serve_loopback_callback)

    verifier = _pkce_code_verifier()
    derived = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert _pkce_code_challenge(verifier) == derived  # S256, RFC 7636
    assert "=" not in _pkce_code_challenge(verifier)

    def _err(message, code):
        from qubit_cli.auth_constants import AuthError
        return AuthError(message, code=code)

    handler_cls, result = _make_loopback_callback_handler("/callback/nonce", display_name="Probe")
    server = _bind_loopback_callback_server("127.0.0.1", 0, handler_cls, err=_err, bind_failed_code="x")
    port = server.server_address[1]
    threading.Thread(
        target=lambda: urllib.request.urlopen(
            f"http://127.0.0.1:{port}/callback/nonce?{urlencode({'code': 'C1', 'state': 'S1'})}",
            timeout=5).read(),
        daemon=True).start()
    served = _serve_loopback_callback(server, result, timeout_seconds=5.0, err=_err, timeout_code="t")
    assert served["code"] == "C1" and served["state"] == "S1"


def test_openrouter_constants_and_error_factory_present():
    from qubit_cli import auth_constants as C
    from qubit_cli.auth import _openrouter_pkce_login  # re-exported for auth_commands

    assert C.OPENROUTER_AUTH_URL and C.OPENROUTER_AUTH_KEYS_URL and C.OPENROUTER_OAUTH_DOCS_URL
    err = C._openrouter_err("boom", "some_code")
    assert err.provider == "openrouter" and err.code == "some_code"
    assert callable(_openrouter_pkce_login)


# ---------------------------------------------------------------------------
# auth_codex_browser — opt-in browser PKCE flow + device-code fallback
# ---------------------------------------------------------------------------

class _FakeOpenAI(ThreadingHTTPServer):
    """Authorize → 302 with a code; token → verifies PKCE and returns tokens; records both."""

    def __init__(self):
        self.seen: dict = {}
        super().__init__(("127.0.0.1", 0), _FakeOpenAIHandler)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _FakeOpenAIHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.server.seen["authorize"] = q
        self.send_response(302)
        self.send_header("Location", f"{q['redirect_uri']}?{urlencode({'code': 'AC-1', 'state': q['state']})}")
        self.end_headers()

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        form = {k: v[0] for k, v in parse_qs(body).items()}
        self.server.seen["token"] = form
        challenge = self.server.seen["authorize"]["code_challenge"]
        derived = base64.urlsafe_b64encode(
            hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        ok = form.get("grant_type") == "authorization_code" and form.get("code") == "AC-1" and derived == challenge
        payload = json.dumps(
            {"access_token": _jwt("pkce@example.com"), "refresh_token": "rt-pkce"} if ok else {"error": "invalid_grant"}
        ).encode()
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def fake_openai():
    server = _FakeOpenAI()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _seed_auth_home() -> Path:
    home = Path(os.environ["QUBIT_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text(json.dumps({"version": 1, "providers": {}}))
    return home


def _args(**overrides):
    base = dict(provider="openai-codex", auth_type="oauth", api_key=None, label=None, priority=None,
                browser=False, no_browser=True, timeout=10.0, scope=None)
    return SimpleNamespace(**{**base, **overrides})


def test_codex_browser_flag_runs_loopback_pkce_and_stores_source(monkeypatch, fake_openai):
    home = _seed_auth_home()
    from qubit_cli import auth_codex_browser as browser_mod
    from qubit_cli.auth_commands import auth_add_command

    monkeypatch.setattr(browser_mod, "CODEX_OAUTH_AUTHORIZE_URL", f"{fake_openai.base}/oauth/authorize")
    monkeypatch.setattr(browser_mod, "CODEX_OAUTH_TOKEN_URL", f"{fake_openai.base}/oauth/token")
    monkeypatch.setattr(browser_mod, "CODEX_BROWSER_CALLBACK_PORT", 0)  # ephemeral; production is 1455
    monkeypatch.setattr(browser_mod, "_can_open_graphical_browser", lambda: True)
    monkeypatch.setattr(
        "qubit_cli.auth._codex_device_code_login",
        lambda: pytest.fail("--browser must not run the device-code flow"))

    def _browser(url):  # the "browser": follow the authorize redirect back to the loopback listener
        threading.Thread(target=lambda: urllib.request.urlopen(url, timeout=5).read(), daemon=True).start()
        return True
    monkeypatch.setattr(browser_mod.webbrowser, "open", _browser)

    auth_add_command(_args(browser=True, no_browser=False))

    payload = json.loads((home / "auth.json").read_text())
    [entry] = payload["credential_pool"]["openai-codex"]
    assert entry["source"] == "manual:loopback_pkce"
    assert entry["access_token"] == _jwt("pkce@example.com") and entry["refresh_token"] == "rt-pkce"
    assert payload["active_provider"] == "openai-codex"
    authorize, token = fake_openai.seen["authorize"], fake_openai.seen["token"]
    assert authorize["code_challenge_method"] == "S256" and authorize["response_type"] == "code"
    assert authorize["redirect_uri"].startswith("http://localhost:") and authorize["redirect_uri"].endswith("/auth/callback")
    assert token["redirect_uri"] == authorize["redirect_uri"] and token["client_id"] == authorize["client_id"]


def test_codex_default_is_device_code_and_busy_port_falls_back(monkeypatch, capsys):
    home = _seed_auth_home()
    from qubit_cli import auth_codex_browser as browser_mod
    from qubit_cli.auth_commands import auth_add_command

    device_logins = []

    def _device():
        device_logins.append(1)
        return {"tokens": {"access_token": _jwt("device@example.com"), "refresh_token": "rt-dev"},
                "base_url": "https://chatgpt.com/backend-api/codex", "last_refresh": "2026-01-01T00:00:00Z"}
    monkeypatch.setattr("qubit_cli.auth._codex_device_code_login", _device)
    bind_attempts = []
    real_bind = browser_mod._bind_loopback_callback_server
    monkeypatch.setattr(
        browser_mod, "_bind_loopback_callback_server",
        lambda *a, **kw: bind_attempts.append(1) or real_bind(*a, **kw))

    # Default (no flag): device code, and the loopback listener is never even bound.
    auth_add_command(_args())
    assert device_logins == [1] and bind_attempts == []

    # --browser while the registered port is taken (a Codex CLI login in progress): notice + device code.
    with socket.socket() as occupant:
        occupant.bind(("127.0.0.1", 0))
        occupant.listen(1)
        monkeypatch.setattr(browser_mod, "CODEX_BROWSER_CALLBACK_PORT", occupant.getsockname()[1])
        auth_add_command(_args(browser=True, label="second"))
    assert device_logins == [1, 1] and bind_attempts == [1]
    out = capsys.readouterr().out
    assert "already in use" in out and "falling back to the device-code login" in out
    sources = [e["source"] for e in json.loads((home / "auth.json").read_text())["credential_pool"]["openai-codex"]]
    assert sources == ["manual:device_code", "manual:device_code"]


# ---------------------------------------------------------------------------
# auth_openrouter — PKCE loopback exchange
# ---------------------------------------------------------------------------

class _FakeOpenRouter(ThreadingHTTPServer):
    def __init__(self):
        self.seen: dict = {}
        super().__init__(("127.0.0.1", 0), _FakeOpenRouterHandler)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _FakeOpenRouterHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.server.seen["authorize"] = q
        self.send_response(302)
        self.send_header("Location", f"{q['callback_url']}?{urlencode({'code': 'OR-1'})}")
        self.end_headers()

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        form = json.loads(body)
        self.server.seen["token"] = form
        payload = json.dumps({"key": "sk-or-v1-test"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def test_openrouter_pkce_loopback_mints_api_key(monkeypatch):
    from qubit_cli import auth_openrouter as or_mod
    from qubit_cli.auth_commands import _OAUTH_ADD_SPECS

    server = _FakeOpenRouter()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(or_mod, "OPENROUTER_AUTH_URL", f"{server.base}/auth")
        monkeypatch.setattr(or_mod, "OPENROUTER_AUTH_KEYS_URL", f"{server.base}/api/v1/auth/keys")
        monkeypatch.setattr(or_mod, "_is_remote_session", lambda: False)
        monkeypatch.setattr(or_mod, "_can_open_graphical_browser", lambda: True)

        def _browser(url):  # follow the authorize redirect to the nonce loopback path
            threading.Thread(target=lambda: urllib.request.urlopen(url, timeout=5).read(), daemon=True).start()
            return True
        monkeypatch.setattr(or_mod.webbrowser, "open", _browser)

        creds = _OAUTH_ADD_SPECS["openrouter"].login(_args(provider="openrouter", auth_type="oauth", no_browser=False))
        assert creds == {"api_key": "sk-or-v1-test"}
        assert server.seen["token"]["code_challenge_method"] == "S256"
        assert server.seen["token"]["code"] == "OR-1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_openrouter_spec_is_api_key_row_and_exchange_refusals(monkeypatch):
    from qubit_cli import auth_openrouter as or_mod
    from qubit_cli.auth_commands import _OAUTH_ADD_SPECS, _OAUTH_CAPABLE_PROVIDERS, _OAUTH_DEFAULT_PROVIDERS

    # openrouter is OAuth-capable but stays API-key-first when --type is omitted.
    assert "openrouter" in _OAUTH_CAPABLE_PROVIDERS and "openrouter" not in _OAUTH_DEFAULT_PROVIDERS
    spec = _OAUTH_ADD_SPECS["openrouter"]
    assert spec.auth_type == "api_key" and spec.source == "manual:openrouter_pkce"

    # The exchange maps a 403 to a typed, relogin-flagged refusal.
    class _Resp:
        status_code = 403
        text = "denied"

        def json(self):
            return {}
    monkeypatch.setattr(or_mod.httpx, "post", lambda *a, **k: _Resp())
    from qubit_cli.auth_constants import AuthError
    with pytest.raises(AuthError) as caught:
        or_mod._openrouter_exchange_code("c", "v")
    assert caught.value.code == "openrouter_token_exchange_denied" and caught.value.relogin_required


def test_codex_spec_source_derives_pool_source():
    from qubit_cli.auth_commands import _OAUTH_ADD_SPECS

    source = _OAUTH_ADD_SPECS["openai-codex"].source
    assert callable(source)
    assert source({"source": "loopback_pkce"}) == "manual:loopback_pkce"
    assert source({}) == "manual:device_code"


# ---------------------------------------------------------------------------
# shared_session_attach — fenced local owner handshake
# ---------------------------------------------------------------------------

def test_discover_attach_url_uses_exact_profile_and_owner_handshake(tmp_path):
    from qubit_cli.active_sessions import try_acquire_active_session
    from qubit_cli.shared_session_attach import configure_tui_attachment, discover_attach_url

    home = tmp_path / "profile"
    other = tmp_path / "other"
    reply = {}
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            payload = json.dumps(reply).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    lease, error = try_acquire_active_session(
        session_id="same-id", surface="desktop", config={}, registry_home=home,
        metadata={"live_session_id": "live", "shared_runtime_url": origin},
    )
    assert error is None
    reply.update(session_id="same-id", lease_id=lease.lease_id,
                 profile_home=str(home.resolve()), websocket_url=origin.replace("http:", "ws:") + "/api/ws?token=real-token")
    try:
        assert discover_attach_url("same-id", registry_home=other) is None
        assert requests == []
        assert discover_attach_url("same-id", registry_home=home) == reply["websocket_url"]
        assert len(requests) == 1
        env = {"HERMES_TUI_GATEWAY_URL": "   "}
        configure_tui_attachment(env, "same-id", registry_home=home)
        assert env["HERMES_TUI_GATEWAY_URL"] == reply["websocket_url"]
        reply["profile_home"] = str(other.resolve())
        with pytest.raises(ValueError, match="identity"):
            discover_attach_url("same-id", registry_home=home)
        reply["profile_home"] = str(home.resolve())
        reply["websocket_url"] = "ws://example.com/api/ws?token=secret"
        with pytest.raises(ValueError, match="endpoint"):
            discover_attach_url("same-id", registry_home=home)
    finally:
        lease.release()
        server.shutdown()
        server.server_close()
        thread.join()


def test_discover_attach_url_refuses_unsupported_owner_with_resume_hint(tmp_path):
    from qubit_cli.active_sessions import active_session_registry_snapshot, try_acquire_active_session
    from qubit_cli.shared_session_attach import discover_attach_url

    lease, error = try_acquire_active_session(
        session_id="old", surface="desktop", config={}, registry_home=tmp_path,
    )
    assert error is None
    try:
        with pytest.raises(ValueError, match="not available in this build") as caught:
            discover_attach_url("old", registry_home=tmp_path)
        first, details = str(caught.value).splitlines()
        assert "qubit --resume old" in first
        assert details.startswith("Details: ")
        assert active_session_registry_snapshot(tmp_path)[0]["lease_id"] == lease.lease_id
    finally:
        lease.release()


def test_configure_tui_attachment_keeps_explicit_transport(tmp_path):
    from qubit_cli.shared_session_attach import configure_tui_attachment

    env = {"HERMES_TUI_GATEWAY_URL": "ws://explicit/api/ws"}
    configure_tui_attachment(env, "sid", registry_home=tmp_path)
    assert env["HERMES_TUI_GATEWAY_URL"] == "ws://explicit/api/ws"


# ---------------------------------------------------------------------------
# Parser wiring: `qubit auth add openai-codex --browser`
# ---------------------------------------------------------------------------

def test_auth_parser_exposes_browser_flag():
    from qubit_cli.subcommands.auth import build_auth_parser

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    build_auth_parser(sub, cmd_auth=lambda a: None)
    ns = parser.parse_args(["auth", "add", "openai-codex", "--browser"])
    assert ns.provider == "openai-codex" and ns.browser is True and ns.no_browser is False


def test_session_owner_details_shape():
    from qubit_cli.active_sessions import session_owner_details

    line = session_owner_details("sid", {"surface": "desktop"})
    assert line.startswith("Details: session sid opened by desktop")
