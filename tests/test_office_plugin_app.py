"""The two new webui routes: the plugin-app static server and the office-app
auth predicate (``tests/test_office_plugin_app.py``).

Two surfaces, two layers each, because the two bugs this file guards against
live at DIFFERENT layers:

* ``_serve_plugin_app(sub)`` — ``GET /plugin-apps/<name>/<app>/...``. It is the
  only route that serves an entire HTML application (index.html, ``bundle/*.js``,
  ``assets/*.png``, fonts) rather than a single script asset, so it is the route
  where a path-traversal or an accidental content-type can leak the filesystem.
* ``_office_app_caller_ok()`` — the auth predicate the SSE route
  (``_serve_office_stream``) trusts. It accepts a header token OR a tight
  loopback+Host+Referer triple; the tightness IS the security property.

METHOD: the traversal case is tested at the HANDLER-FUNCTION layer as well as
over HTTP, on purpose. A real HTTP client (curl, http.client, urllib) normalizes
``/plugin-apps/x/../..`` to ``/`` *before* the request is written to the socket,
so an over-HTTP-only test of traversal proves the CLIENT's normalization, not
the server's confinement. Every test name says which layer it asserts:
``[handler]`` for a direct call on the parsed leftovers, ``[wire]`` for a call
that goes through a real socket. The encoded form (``%2e%2e``) survives the
client and so exercises the server's own decode.

House style follows ``tests/test_proxy_fallback_audit9u.py`` /
``tests/test_qubit_package8u.py``: ``WebUIHandler.__new__`` with a fake
``server.app``, ``_send`` stubbed to capture ``(code, body, ctype)``, no sockets
for the handler-layer cases.
"""
from __future__ import annotations

import http.client
import socketserver
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge import webui as W  # noqa: E402

PREFIX = "/plugin-apps/"
SAMPLE_HTML = b"<!doctype html><html><body>office</body></html>"


# --------------------------------------------------------------------------
# Handler-layer harness (no sockets)
# --------------------------------------------------------------------------

def _make_handler(*, sub="", path=None, headers=None, peer="127.0.0.1",
                  host="127.0.0.1"):
    """A bare ``WebUIHandler`` with ``_send`` captured.

    The handler functions under test touch only ``self.app``, ``self.headers``,
    ``self.client_address``, ``self.path`` and ``self._send`` — so ``__new__``
    plus those attributes is a faithful, socket-free harness.

    ``host`` defaults to loopback because a REAL HTTP/1.1 request always carries
    a ``Host``, and the app-document token injector now requires BOTH the peer
    and the Host to be loopback (an off-box peer sending a ``localhost`` Host is
    still off-box). A caller wanting to exercise the refusal passes
    ``host=None`` (no header) or a non-loopback peer.
    """
    h = object.__new__(W.WebUIHandler)
    h.path = path if path is not None else (PREFIX + sub)
    h.headers = dict(headers or {})
    if host is not None and "Host" not in h.headers:
        h.headers["Host"] = host
    h.client_address = (peer, 51234)
    h._head_only = False

    class _FakeApp:
        api_token = ""                 # no configured token => "loopback-open"
        allowed_origins = ()

    h.server = type("S", (), {"app": _FakeApp()})()
    captured: dict = {}

    def _send(code, body, ctype="text/plain"):
        captured["code"] = code
        captured["body"] = body
        captured["ctype"] = ctype

    h._send = _send  # type: ignore[method-assign]
    h._captured = captured
    return h


@pytest.fixture()
def plugin_root(tmp_path, monkeypatch):
    """A hermetic ``frontend/qubit-app/plugins`` tree.

    ``_serve_plugin_app`` resolves its serving root through
    ``resources.resource_path("frontend/qubit-app/plugins")``. Redirecting that
    single resolution point keeps the test independent of whether the checkout's
    real plugins are present, and — critically — pins the ROOT the confinement
    check compares against, so a test can create a file OUTSIDE the root and
    prove the handler refuses to reach it.
    """
    from voicebridge import resources

    root = tmp_path / "plugins"
    app = root / "qubit-office"
    (app / "bundle").mkdir(parents=True)
    (app / "assets").mkdir(parents=True)
    (app / "index.html").write_bytes(SAMPLE_HTML)
    (app / "bundle" / "index-abc123.js").write_text("console.log(1)")
    (app / "assets" / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (app / "style.css").write_text("body{}")
    (app / "manifest.webmanifest").write_text("{}")
    (app / "font.woff2").write_bytes(b"wOF2")

    real = _real_resource_path
    monkeypatch.setattr(
        resources, "resource_path",
        lambda name: root if name == "frontend/qubit-app/plugins" else real(name))
    return root


_real_resource_path = None  # set below, after import


def _bind_real_resource_path():
    from voicebridge import resources
    global _real_resource_path
    _real_resource_path = resources.resource_path


_bind_real_resource_path()


# --------------------------------------------------------------------------
# 1. PATH TRAVERSAL IS REFUSED
# --------------------------------------------------------------------------

def test_traversal_raw_dotdot_is_refused_handler_layer(plugin_root):
    """[handler] A ``..`` segment in the parsed path 404s at the handler.

    This is the layer that MATTERS: the handler receives ``sub`` after the
    server split the prefix, so ``..`` here is exactly what a raw HTTP request
    would leave behind if the client had not normalized it. Testing the handler
    directly is the only way to prove the server's own ``..`` check fires.
    """
    h = _make_handler(sub="qubit-office/../../../etc/passwd")
    h._serve_plugin_app("qubit-office/../../../etc/passwd")
    assert h._captured["code"] == 404, h._captured


def test_traversal_dotdot_inside_root_is_refused_by_the_explicit_check(
        plugin_root):
    """[handler] A ``..`` that RESOLVES BACK INSIDE the root is still refused.

    This is the test that pins the EXPLICIT ``..`` check rather than the
    resolved-path re-check: ``office/bundle/../index.html`` resolves to
    ``office/index.html`` — a real file inside the plugin root — so the
    confinement re-check would happily serve it. Only the ``parts`` check
    (``".." in parts``) refuses it. Drop that check and this path serves 200;
    every other traversal test here still 404s because the re-check backstops
    them, which is exactly why this case exists.
    """
    h = _make_handler(sub="qubit-office/bundle/../index.html")
    h._serve_plugin_app("qubit-office/bundle/../index.html")
    assert h._captured["code"] == 404, h._captured


def test_traversal_dotdot_at_every_position_is_refused_handler_layer(plugin_root):
    """[handler] ``..`` anywhere in the path 404s, not just the first segment."""
    for sub in (
        "../etc/passwd",
        "qubit-office/../other/index.html",
        "qubit-office/bundle/../../../etc/passwd",
        "qubit-office/assets/../../../../etc/passwd",
        "qubit-office/../../../etc/passwd",
    ):
        h = _make_handler(sub=sub)
        h._serve_plugin_app(sub)
        assert h._captured["code"] == 404, (sub, h._captured)


def test_traversal_url_encoded_dotdot_is_refused_over_http(plugin_root):
    """[wire] The URL-encoded ``%2e%2e`` form reaches the server UNnormalized.

    A raw ``..`` is collapsed by every real client before it hits the socket, so
    an over-the-wire test of the raw form proves nothing about the server. The
    percent-encoded form survives the client and is what the SERVER decodes —
    this is the wire case that actually exercises confinement end to end.
    """
    code, body = _http_get(
        "/plugin-apps/qubit-office/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
        plugin_root=plugin_root)
    assert code == 404, (code, body)


def test_traversal_encoded_mixed_case_is_refused_handler_layer(plugin_root):
    """[handler] ``%2E%2E`` (upper-case hex) is decoded and still refused.

    ``unquote`` is case-insensitive on the hex digits; a check that only looked
    for the literal lower-case string would let this through, so pin both.
    """
    for sub in (
        "qubit-office/%2e%2e/%2e%2e/etc/passwd",
        "qubit-office/%2E%2E/%2E%2E/etc/passwd",
    ):
        h = _make_handler(sub=sub)
        h._serve_plugin_app(sub)
        assert h._captured["code"] == 404, (sub, h._captured)


def test_traversal_symlink_escape_is_refused_handler_layer(plugin_root, tmp_path):
    """[handler] A symlink inside the plugin dir cannot escape the root.

    The handler resolves the target and re-checks it against the resolved plugin
    root, so a symlink pointing at a real file outside the tree must 404 even
    though the requested path contains no ``..``.
    """
    secret = tmp_path / "outside-secret.html"
    secret.write_bytes(b"<html>SECRET</html>")
    link = plugin_root / "qubit-office" / "sneaky.html"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):  # pragma: no cover - platform guard
        pytest.skip("symlinks unavailable in this environment")

    h = _make_handler(sub="qubit-office/sneaky.html")
    h._serve_plugin_app("qubit-office/sneaky.html")
    assert h._captured["code"] == 404, h._captured


def test_traversal_attempt_never_returns_passwd_content_over_http(plugin_root):
    """[wire] Belt-and-braces: no traversal variant ever leaks file content."""
    payloads = [
        "/plugin-apps/qubit-office/../../../../etc/passwd",
        "/plugin-apps/qubit-office/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
        "/plugin-apps/..%2f..%2f..%2fetc%2fpasswd",
        "/plugin-apps/qubit-office/..%5c..%5cetc%5cpasswd",
    ]
    for url in payloads:
        code, body = _http_get(url, plugin_root=plugin_root)
        assert code == 404, (url, code, body)
        assert b"root:" not in body, (url, body)


# --------------------------------------------------------------------------
# 2. EXTENSION ALLOWLIST
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name,ctype", [
    ("index.html", "text/html; charset=utf-8"),
    ("bundle/index-abc123.js", "application/javascript"),
    ("style.css", "text/css"),
    ("manifest.webmanifest", "application/manifest+json"),
    ("assets/icon.png", "image/png"),
    ("font.woff2", "font/woff2"),
])
def test_allowlisted_extensions_serve_with_correct_ctype_handler(plugin_root,
                                                                  name, ctype):
    """[handler] Every allowlisted type serves, with its declared content-type."""
    sub = "qubit-office/" + name
    h = _make_handler(sub=sub)
    h._serve_plugin_app(sub)
    assert h._captured["code"] == 200, (sub, h._captured)
    assert h._captured["ctype"] == ctype, (sub, h._captured["ctype"])


@pytest.mark.parametrize("name", [
    "server.py", "run.sh", ".env", "secrets.env", "data.bin",
    "archive.tar.gz", "noext", "executable", "id_rsa",
])
def test_disallowed_extension_is_refused_not_guessed_handler(plugin_root, name):
    """[handler] A disallowed/unlisted type 404s — never served with a guess.

    The route uses an ALLOWLIST. The failure mode this pins is the opposite of a
    denylist: an unknown suffix must be refused, not handed a guessed
    ``application/octet-stream`` and streamed to the client.
    """
    target = plugin_root / "qubit-office" / name
    target.write_bytes(b"# forbidden payload")
    sub = "qubit-office/" + name
    h = _make_handler(sub=sub)
    h._serve_plugin_app(sub)
    assert h._captured["code"] == 404, (sub, h._captured)
    assert h._captured.get("ctype") != "application/octet-stream", h._captured


def test_disallowed_extension_refused_over_http(plugin_root):
    """[wire] The refusal holds over a real socket too, and no body leaks."""
    (plugin_root / "qubit-office" / "server.py").write_bytes(b"SECRET = 1\n")
    code, body = _http_get("/plugin-apps/qubit-office/server.py",
                           plugin_root=plugin_root)
    assert code == 404, (code, body)
    assert b"SECRET" not in body, body


def test_extension_check_is_case_insensitive_and_lowercased(plugin_root):
    """[handler] ``.HTML`` serves as HTML (suffix lowercased before lookup)."""
    (plugin_root / "qubit-office" / "upper.HTML").write_bytes(SAMPLE_HTML)
    h = _make_handler(sub="qubit-office/upper.HTML")
    h._serve_plugin_app("qubit-office/upper.HTML")
    assert h._captured["code"] == 200, h._captured
    assert h._captured["ctype"] == "text/html; charset=utf-8", h._captured


# --------------------------------------------------------------------------
# 3. MISSING FILE / DIRECTORY / MISSING PLUGIN
# --------------------------------------------------------------------------

def test_missing_file_404s_handler(plugin_root):
    """[handler] A path inside a real plugin that names no file 404s."""
    h = _make_handler(sub="qubit-office/nope.html")
    h._serve_plugin_app("qubit-office/nope.html")
    assert h._captured["code"] == 404, h._captured


def test_directory_request_gets_index_html_handler(plugin_root):
    """[handler] A trailing-slash directory request resolves to index.html."""
    for sub in ("qubit-office/", "qubit-office", "qubit-office/bundle/"):
        h = _make_handler(sub=sub)
        h._serve_plugin_app(sub)
        if sub.rstrip("/").endswith("bundle"):
            # bundle/ has no index.html in the fixture -> 404, not a listing.
            assert h._captured["code"] == 404, (sub, h._captured)
        else:
            assert h._captured["code"] == 200, (sub, h._captured)
            assert h._captured["body"] == SAMPLE_HTML, (sub, h._captured)


def test_directory_request_serves_index_over_http(plugin_root):
    """[wire] ``GET /plugin-apps/qubit-office/`` returns the app's index.html."""
    code, body = _http_get("/plugin-apps/qubit-office/", plugin_root=plugin_root)
    assert code == 200, (code, body)
    assert body == SAMPLE_HTML, body


def test_habblaud_index_injects_encoded_stream_url_before_module(tmp_path):
    from urllib.parse import quote

    html = ("<!doctype html><html><head><meta charset=\"utf-8\"></head><body>"
            "<script type=\"module\" src=\"/main.js\"></script></body></html>")
    target = tmp_path / "index.html"
    target.write_text(html, encoding="utf-8")
    h = _make_handler()
    h.server.app.api_token = "secret /?&=<>\"'"
    h._serve_app_html(target)

    assert h._captured["code"] == 200
    body = h._captured["body"].decode("utf-8")
    script_start = body.index("<script>")
    module = body.index('<script type="module"')
    assert script_start < module
    assert 'window.__QUBIT_OFFICE_STREAM_URL__="/api/stream?token=' + quote(
        h.server.app.api_token, safe="") + '";' in body
    assert 'window.__QUBIT_SESSION_TOKEN__=' in body
    assert h.server.app.api_token not in body


def test_directory_request_with_no_configured_token_does_not_inject_stream_url(tmp_path):
    target = tmp_path / "index.html"
    target.write_text("<html><head></head><body></body></html>", encoding="utf-8")
    h = _make_handler()
    h._serve_app_html(target)
    body = h._captured["body"].decode("utf-8")
    assert "__QUBIT_OFFICE_STREAM_URL__" not in body


def test_unknown_plugin_directory_404s_handler(plugin_root):
    """[handler] A plugin name that does not exist 404s."""
    h = _make_handler(sub="does-not-exist/index.html")
    h._serve_plugin_app("does-not-exist/index.html")
    assert h._captured["code"] == 404, h._captured


def test_unknown_plugin_directory_404s_over_http(plugin_root):
    """[wire] Same refusal over a real socket."""
    code, body = _http_get("/plugin-apps/does-not-exist/index.html",
                           plugin_root=plugin_root)
    assert code == 404, (code, body)


def test_empty_sub_path_404s_handler(plugin_root):
    """[handler] No plugin named => 404, never the plugins root itself."""
    for sub in ("", "/", "/?x=1"):
        h = _make_handler(sub=sub)
        h._serve_plugin_app(sub)
        assert h._captured["code"] == 404, (sub, h._captured)


# --------------------------------------------------------------------------
# 4. AUTH — _office_app_caller_ok
# --------------------------------------------------------------------------

def _auth_ok(*, peer="127.0.0.1", host="127.0.0.1:8890",
             referer=None, token=None, cfg_token=""):
    """Call ``_office_app_caller_ok`` directly with a crafted request context.

    ``_qubit_token_ok`` is stubbed to the OUTCOME implied by the crafted
    ``token``/``cfg_token`` so this exercises the SECOND half of the predicate
    (peer/Host/Referer) in isolation. The real header path is covered by
    ``test_auth_true_with_valid_header_token_regardless_of_referer`` and
    ``test_qubit_token_ok_accepts_a_valid_header`` below.
    """
    headers = {}
    if host is not None:
        headers["Host"] = host
    if referer is not None:
        headers["Referer"] = referer

    h = _make_handler(path="/api/stream", headers=headers, peer=peer)
    h.server.app.api_token = cfg_token
    # Isolate the Referer/Host/peer half: a valid header token is the only way
    # _qubit_token_ok returns True here (empty cfg => loopback-open, which we
    # do NOT want to leak into the Referer-path assertions).
    token_valid = bool(token) and token == cfg_token
    h._qubit_token_ok = lambda: token_valid  # type: ignore[method-assign]
    return h._office_app_caller_ok()


def test_auth_ok_loopback_peer_host_and_app_referer():
    """[handler] The browser's EventSource case: loopback peer + Host +
    Referer under the plugin-app prefix is the ONE path that is allowed."""
    assert _auth_ok(
        peer="127.0.0.1",
        host="127.0.0.1:8890",
        referer="http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/",
    ) is True


@pytest.mark.parametrize("host", ["localhost:8890", "127.0.0.1",
                                  "localhost"])
def test_auth_ok_variants_of_a_loopback_host(host):
    """[handler] Every spelling of a loopback Host is accepted.

    ``::1`` is deliberately NOT in this list: the handler splits the Host on the
    FIRST colon (``.split(":")[0]``), so a bare ``::1`` yields ``""`` and is
    refused. That is the code's real behavior and is asserted explicitly in
    ``test_auth_bare_ipv6_host_is_refused_by_the_colon_split`` so the gap is
    visible rather than silently assumed.
    """
    assert _auth_ok(host=host,
                    referer="http://127.0.0.1:8890/plugin-apps/qubit-office/") \
        is True


@pytest.mark.parametrize("host", ["::1", "[::1]:8890"])
def test_auth_bare_ipv6_host_is_refused_by_the_colon_split(host):
    """[handler] An IPv6 Host is refused by the ``split(":")[0]`` parse.

    ``host.split(":")[0]`` on ``::1`` gives ``""`` and on ``[::1]:8890`` gives
    ``[`` — neither is in the loopback set. Pinning the ACTUAL behavior: this is
    a narrowness, not a hole (it fails closed), and the loopback host this app
    is served from is ``127.0.0.1``/``localhost``.
    """
    assert _auth_ok(host=host,
                    referer="http://127.0.0.1:8890/plugin-apps/qubit-office/") \
        is False


def test_auth_false_when_referer_absent():
    """[handler] No Referer => refused (the same-origin exemption cannot apply)."""
    assert _auth_ok(peer="127.0.0.1", host="127.0.0.1:8890",
                    referer=None) is False


@pytest.mark.parametrize("referer", [
    "http://127.0.0.1:8890/",
    "http://127.0.0.1:8890/agent/",
    "http://127.0.0.1:8890/dashboard/",
    "http://127.0.0.1:8890/plugin-app",       # prefix missing the trailing slash
    "",
])
def test_auth_false_when_referer_is_not_the_app_directory(referer):
    """[handler] A Referer that does not contain ``/plugin-apps/`` is refused."""
    assert _auth_ok(peer="127.0.0.1", host="127.0.0.1:8890",
                    referer=referer) is False


def test_auth_referer_check_is_substring_and_origin_agnostic():
    """[handler] PINS an observed narrowness: the Referer check is a substring.

    ``OFFICE_APP_PREFIX in referer`` does not look at the Referer's ORIGIN, so
    ``http://evil.example.com/plugin-apps/`` satisfies it. This is NOT a hole in
    practice: the peer AND Host must also be loopback before the Referer is even
    consulted, so only a loopback-origin page can reach this branch — and a page
    served by this same server is exactly the caller the exemption exists for.
    Pinned so a future change to a substring→origin comparison is a deliberate,
    visible edit rather than an accident.
    """
    assert _auth_ok(peer="127.0.0.1", host="127.0.0.1:8890",
                    referer="http://evil.example.com/plugin-apps/") is True
    # But the SAME Referer from a non-loopback peer or a non-loopback Host is
    # still refused by the earlier gates.
    assert _auth_ok(peer="10.0.0.5", host="127.0.0.1:8890",
                    referer="http://evil.example.com/plugin-apps/") is False
    assert _auth_ok(peer="127.0.0.1", host="evil.example.com:8890",
                    referer="http://evil.example.com/plugin-apps/") is False


@pytest.mark.parametrize("host", ["evil.example.com:8890", "192.168.1.10:8890",
                                  "example.com", "10.0.0.5"])
def test_auth_false_when_host_is_not_loopback(host):
    """[handler] A non-loopback Host is refused even for a loopback peer."""
    assert _auth_ok(peer="127.0.0.1", host=host,
                    referer="http://127.0.0.1:8890/plugin-apps/qubit-office/") \
        is False


@pytest.mark.parametrize("peer", ["192.168.1.10", "10.0.0.5", "8.8.8.8",
                                  "::2", "evil.example.com"])
def test_auth_false_when_peer_is_not_loopback(peer):
    """[handler] A non-local peer is refused outright, whatever the headers."""
    assert _auth_ok(peer=peer, host="127.0.0.1:8890",
                    referer="http://127.0.0.1:8890/plugin-apps/qubit-office/") \
        is False


def test_auth_true_with_valid_header_token_regardless_of_referer():
    """[handler] A valid header token wins FIRST — Referer is irrelevant."""
    for referer in (None, "", "http://evil.example.com/",
                    "http://127.0.0.1:8890/nothing/"):
        assert _auth_ok(peer="127.0.0.1", host="127.0.0.1:8890",
                        referer=referer, token="s3cret", cfg_token="s3cret") \
            is True, referer


def test_auth_header_token_works_even_for_a_non_loopback_peer():
    """[handler] The header path is token-only: a remote peer with the right
    token is accepted (the loopback gate guards only the Referer path)."""
    assert _auth_ok(peer="10.0.0.5", host="10.0.0.5:8890", referer=None,
                    token="s3cret", cfg_token="s3cret") is True


def test_auth_false_with_bad_header_token_and_no_referer():
    """[handler] A wrong token does not fall through to a Referer exemption."""
    assert _auth_ok(peer="127.0.0.1", host="127.0.0.1:8890", referer=None,
                    token="wrong", cfg_token="s3cret") is False


def test_qubit_token_ok_accepts_a_valid_header_token():
    """[handler] The real ``_qubit_token_ok`` the predicate calls first.

    This is the un-stubbed header path: with a configured token, the matching
    ``X-Qubit-Session-Token`` header passes and a wrong one fails.
    """
    h = _make_handler(path="/api/stream",
                      headers={"X-Qubit-Session-Token": "s3cret"})
    h.server.app.api_token = "s3cret"
    assert h._qubit_token_ok() is True

    h2 = _make_handler(path="/api/stream",
                       headers={"X-Qubit-Session-Token": "wrong"})
    h2.server.app.api_token = "s3cret"
    assert h2._qubit_token_ok() is False


def test_office_app_caller_ok_real_header_path_end_to_end():
    """[handler] Full predicate, real ``_qubit_token_ok``: a valid header wins
    even with NO Referer and a non-loopback Host."""
    h = _make_handler(path="/api/stream",
                      headers={"X-Qubit-Session-Token": "s3cret",
                               "Host": "evil.example.com:8890"},
                      peer="10.0.0.5")
    h.server.app.api_token = "s3cret"
    assert h._office_app_caller_ok() is True


def test_stream_handler_auth_requires_configured_token():
    """The stream route must keep strict session-token auth."""
    h = _make_handler(path="/api/stream", headers={
        "Host": "127.0.0.1:8890",
        "Referer": "http://127.0.0.1:8890/plugin-apps/qubit-office/habblaud/",
    })
    h.server.app.api_token = "secret"
    h._qubit_token_ok = lambda: False
    assert h._office_app_caller_ok() is False
    assert h._office_app_caller_ok(blind_probe=True) is False


def test_office_access_is_bound_read_only_and_disables_unavailable_actions():
    h = _make_handler(path="/api/access",
                      headers={"X-Qubit-Session-Token": "s3cret"})
    h.server.app.api_token = "s3cret"
    h._office_app_caller_ok = lambda **_: True
    h._json = lambda obj, code=200: setattr(h, "_captured_json", (code, obj))
    h._serve_office_access()
    code, body = h._captured_json
    assert code == 200
    assert body == {
        "remote": False, "local": True, "permissions": False,
        "messages": False, "terminal": False, "spawn": False,
    }


def test_office_access_route_dispatches_to_capability_read():
    h = _make_handler(path="/api/access")
    h._office_app_caller_ok = lambda **_: True
    h._serve_office_access = lambda: setattr(h, "_access_called", True)
    h.command = "GET"
    h._head_only = False
    h.path = "/api/access"
    # Exercise the GET route dispatch without requiring a full socket server.
    h._maybe_pty_upgrade = lambda: False
    h._maybe_stt_upgrade = lambda: False
    h._maybe_events_upgrade = lambda: False
    h._maybe_gateway_rpc_upgrade = lambda: False
    h._refuse_qubit_ws = lambda: False
    h._send = lambda *args, **kwargs: None
    h.do_GET()
    assert getattr(h, "_access_called", False)


def test_office_agent_route_dispatches_read_but_not_writes():
    """``GET /api/agents/:id`` is served by the office-client front door.

    The old direct ``_serve_office_agent`` required a session header the
    tokenless iframe cannot send, so the live drawer got a 401. The read now
    goes through ``_serve_office_client_api``, which maps it onto the compat
    family's ``agents/<id>`` row — the SAME office store, so the two paths
    cannot disagree. ``_serve_office_agent`` stays for direct callers and is
    covered by ``test_office_agent_read_uses_real_seat_and_event_rows``.
    """
    h = _make_handler(path="/api/agents/7")
    h.command = "GET"
    h._head_only = False
    h._serve_office_client_api = \
        lambda method: setattr(h, "_front_door_called", method)
    for name in ("_maybe_pty_upgrade", "_maybe_stt_upgrade",
                 "_maybe_events_upgrade", "_maybe_gateway_rpc_upgrade",
                 "_refuse_qubit_ws"):
        setattr(h, name, lambda: False)
    h._send = lambda *args, **kwargs: None
    h.do_GET()
    assert h._front_door_called == "GET"

    # Mutating Habblaud verbs are routed the same way (to a typed refusal /
    # C4 stage), never to the read-only handler.
    h.command = "PUT"
    h.path = "/api/agents/7/character"
    h._front_door_called = None
    h.do_PUT()
    assert h._front_door_called == "PUT"


def test_office_agent_read_uses_real_seat_and_event_rows(monkeypatch):
    class _Conn:
        closed = False
        def execute(self, sql, params=()):
            if "FROM office_agent" in sql:
                return type("Rows", (), {"fetchone": lambda self: {
                    "id": 7, "slug": "analyst", "name": "Analyst",
                    "seat_type": "analyst", "department_id": 2,
                    "supervisor_id": None, "status": "active",
                }})()
            assert params == (7,)
            return [{"id": 11, "ts": 1792050000.5, "kind": "run_started",
                     "payload": '{"text":"Started a real task"}'}]
        def close(self):
            self.closed = True

    conn = _Conn()
    from voicebridge.office import store as OfficeStore
    monkeypatch.setattr(OfficeStore, "connect", lambda cfg: conn)
    monkeypatch.setattr(OfficeStore, "migrate", lambda conn: None)
    h = _make_handler(path="/api/agents/7")
    h.server.app.orch = type("O", (), {"cfg": object()})()
    h._office_app_caller_ok = lambda **_: True
    h._json = lambda obj, code=200: setattr(h, "_captured_json", (code, obj))
    h._serve_office_agent("7")
    code, body = h._captured_json
    assert code == 200
    assert body["name"] == "Analyst"
    assert body["slug"] == "analyst"
    assert body["recent"][0]["text"] == "Started a real task"
    assert body["conversation"] is None
    assert conn.closed


# --------------------------------------------------------------------------
# Wire harness: a real socket, with the plugin root patched in the SERVER
# --------------------------------------------------------------------------

class _QuietServer(socketserver.ThreadingMixIn,
                   socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _http_get(url_path: str, *, plugin_root: Path):
    """GET ``url_path`` from a real in-process HTTP server.

    The plugin root is patched at ``resources.resource_path`` for the duration
    of the serving thread, so the handler resolves the fixture tree. The server
    is a real ``http.server`` stack so the request line the handler sees is the
    one the CLIENT wrote — which is exactly what makes the ``%2e%2e`` case a
    genuine server-side test.
    """
    import http.server
    from voicebridge import resources

    original = resources.resource_path

    def _patched(name):
        if name == "frontend/qubit-app/plugins":
            return plugin_root
        return original(name)

    class _Bound(http.server.HTTPServer):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.app = type("A", (), {"api_token": "",
                                      "allowed_origins": ()})()

    # Patch module-global for the server's lifetime.
    resources.resource_path = _patched
    srv = _Bound(("127.0.0.1", 0), W.WebUIHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", url_path)
        resp = conn.getresponse()
        body = resp.read()
        code = resp.status
        conn.close()
    finally:
        t.join(timeout=5)
        srv.server_close()
        resources.resource_path = original
    return code, body
