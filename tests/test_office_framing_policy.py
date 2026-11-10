"""Task 6: the office document's server-injected theme, and only the office's.

WHAT THIS PINS
--------------
The theme is served to TWO documents for two different reasons:

* the office **app document** (the same-origin Habblaud iframe) gets ONE
  generated ``<style data-office-theme="<id>">`` from ``_serve_app_html``,
  after its own stylesheet and with allowlisted ``.ui-root`` variables only;
* the dashboard **host** links a generated ``theme.css`` scoped to
  ``:root[data-office-theme="<id>"]`` so our docks inherit the same tokens.

Everything here is fail-closed and narrower than a pack: no pack CSS, no
``url(``/``@import`` in the injected style, no theme on any other plugin app,
and ``classic``/an invalid override produces nothing. The session-token and
stream-URL globals and the CSP contract are unchanged.

Hermetic: a temporary ``QUBIT_HOME`` with a tiny valid pack (arbitrary PNG
bytes — the loader validates size/sha, not pixels); no art is committed, no
operator path is read, no live service is touched.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time

import pytest

from voicebridge import resources
from voicebridge import webui as W
from voicebridge.office import theme as T

pytestmark = pytest.mark.unit

THEME_ID = "demo"
THEME_TOKEN_VAR = "--ui-bg"
THEME_TOKEN_VALUE = "#0d0d1a"
STYLESHEET = '<link rel="stylesheet" href="/assets/app.css">'
_OFFICE_NONCE = "framing-test-nonce"


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------
_real_resource_path = resources.resource_path


def _make_handler(*, sub="", path=None, headers=None, peer="127.0.0.1",
                  host="127.0.0.1", token=""):
    """A bare ``WebUIHandler`` with ``_send`` captured (no socket)."""
    h = object.__new__(W.WebUIHandler)
    h.path = path if path is not None else ("/plugin-apps/" + sub)
    h.headers = dict(headers or {})
    if host is not None and "Host" not in h.headers:
        h.headers["Host"] = host
    h.client_address = (peer, 51234)
    h._head_only = False

    class _FakeApp:
        api_token = token
        allowed_origins = ()

    app = _FakeApp()
    app._office_nonces = {_OFFICE_NONCE: time.monotonic() + 3600}
    app._office_nonce_lock = threading.Lock()
    h.server = type("S", (), {"app": app})()
    captured: dict = {}

    def _send(code, body, ctype="text/plain", extra_headers=None,
              cache_control="no-store"):
        captured["code"] = code
        captured["body"] = body
        captured["ctype"] = ctype
        captured["extra_headers"] = extra_headers
        captured["cache_control"] = cache_control

    h._send = _send  # type: ignore[method-assign]
    h._captured = captured
    return h


@pytest.fixture()
def plugin_root(tmp_path, monkeypatch):
    """A hermetic plugin tree with the office app and an unrelated plugin app."""
    root = tmp_path / "plugins"
    for name in ("qubit-office", "other"):
        html = f"""<!doctype html>
<html><head><meta charset="utf-8">{STYLESHEET}</head>
<body><script type="module" src="/main.js"></script></body></html>"""
        (root / name / "habblaud").mkdir(parents=True, exist_ok=True)
        (root / name / "habblaud" / "index.html").write_text(html, encoding="utf-8")
    # The other plugin's own app path, so the test proves the OFFICE check is
    # what matters, not a missing document (which would 404 either way).
    (root / "other" / "app").mkdir(parents=True)
    (root / "other" / "app" / "index.html").write_text(
        f"<html><head>{STYLESHEET}</head><body>other</body></html>",
        encoding="utf-8")
    monkeypatch.setattr(
        resources, "resource_path",
        lambda name: root if name == "frontend/qubit-app/plugins"
        else _real_resource_path(name))
    return root


def _tokens():
    return {
        "color-surface": THEME_TOKEN_VALUE,
        "color-surface-raised": "#161a23",
        "color-surface-hover": "#1d2230",
        "color-text-primary": "#e9edf7",
        "color-text-muted": "#8a93a8",
        "color-border": "#334155",
        "color-accent": "#7cc8ff",
        "radius-control": "4px",
    }


def _write_pack(home):
    root = home / "office" / "themes" / THEME_ID
    files = {}

    def write(rel, data):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    write("portraits/atlas.png", b"\x89PNG\r\n\x1a\nportrait")
    write("bodies/atlas.png", b"\x89PNG\r\n\x1a\nbody")
    write("portraits/index.json", json.dumps(
        {"schema": 1, "size": 32, "cols": 4, "rows": 4, "cells": {}}).encode())
    write("bodies/index.json", json.dumps(
        {"schema": 1, "size": 32, "frame": {"w": 32, "h": 64},
         "cols": 4, "rows": 4, "cells": {}}).encode())
    manifest = {
        "format": T.FORMAT, "schema": T.SCHEMA, "id": THEME_ID,
        "label": "Demo", "version": "1.0.0",
        "credit": "LimeZu — https://limezu.itch.io",
        "license": "LimeZu licence; redistribution prohibited",
        "source": "owner purchase, 2026-11-09",
        "template_version": 1,
        "tokens": _tokens(),
        "slices": {},
        "files": files,
        "limits": {"max_total_bytes": 10_000_000, "max_files": 20},
        "portraits": {"atlas": "portraits/atlas.png",
                      "index": "portraits/index.json",
                      "size": 32, "cols": 4, "rows": 4},
        "bodies": {"atlas": "bodies/atlas.png",
                   "index": "bodies/index.json",
                   "size": 32, "cols": 4, "rows": 4,
                   "frame": {"w": 32, "h": 64}},
    }
    (root / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@pytest.fixture()
def themed_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    monkeypatch.delenv("QUBIT_OFFICE_THEME", raising=False)
    _write_pack(home)
    return home


def _body(h):
    assert h._captured["code"] == 200, h._captured
    return h._captured["body"].decode("utf-8")


def _style_blocks(body):
    return re.findall(
        r'<style data-office-theme="([^"]*)">(.*?)</style>', body, re.S)


# --------------------------------------------------------------------------
# 1. The office document gets exactly one style, after its stylesheet
# --------------------------------------------------------------------------
def test_office_document_gets_one_theme_style(plugin_root, themed_home):
    h = _make_handler(sub="qubit-office/habblaud/?theme=demo")
    h._serve_plugin_app("qubit-office/habblaud/?theme=demo")
    body = _body(h)

    blocks = _style_blocks(body)
    assert len(blocks) == 1, body
    theme_id, css = blocks[0]
    assert theme_id == THEME_ID
    assert "html body .ui-root" in css
    assert f"{THEME_TOKEN_VAR}: {THEME_TOKEN_VALUE};" in css


def test_style_is_after_the_stylesheet_and_inside_head(plugin_root, themed_home):
    h = _make_handler(sub="qubit-office/habblaud/?theme=demo")
    h._serve_plugin_app("qubit-office/habblaud/?theme=demo")
    body = _body(h)

    style_at = body.index("<style data-office-theme=")
    sheet_at = body.index(STYLESHEET)
    head_end = body.index("</head>")
    assert sheet_at < style_at < head_end, body
    # Not at the very start of the document (which would put it before the
    # stylesheet it is meant to override).
    assert not body.lstrip().startswith("<style data-office-theme=")


def test_style_contains_only_allowlisted_vars_and_no_fetches(plugin_root,
                                                             themed_home):
    h = _make_handler(sub="qubit-office/habblaud/?theme=demo")
    h._serve_plugin_app("qubit-office/habblaud/?theme=demo")
    _, css = _style_blocks(_body(h))[0]

    variables = set(re.findall(r"(--[a-z0-9-]+):", css))
    assert variables, css
    assert variables <= set(T.ALLOWED_THEME_VARS), variables - set(
        T.ALLOWED_THEME_VARS)
    assert "url(" not in css
    assert "@import" not in css
    assert "<script" not in css.lower()


# --------------------------------------------------------------------------
# 2. Classic / other apps / invalid overrides get nothing
# --------------------------------------------------------------------------
def test_classic_office_document_has_no_theme_style(plugin_root, themed_home):
    h = _make_handler(sub="qubit-office/habblaud/")
    h._serve_plugin_app("qubit-office/habblaud/")
    body = _body(h)
    assert "data-office-theme" not in body, body


def test_other_plugin_app_never_gets_the_theme_style(plugin_root, themed_home):
    h = _make_handler(sub="other/app/?theme=demo")
    h._serve_plugin_app("other/app/?theme=demo")
    body = _body(h)
    assert "data-office-theme" not in body, body
    assert "<style" not in body, body


@pytest.mark.parametrize("requested", ["Not_An_Id", "..%2fevil", "bad id", "x" * 80])
def test_invalid_override_falls_back_to_classic(plugin_root, themed_home,
                                                requested):
    h = _make_handler()
    h._serve_plugin_app(f"qubit-office/habblaud/?theme={requested}")
    body = _body(h)
    assert "data-office-theme" not in body, body


def test_env_kill_switch_wins_over_the_query(plugin_root, themed_home,
                                             monkeypatch):
    monkeypatch.setenv("QUBIT_OFFICE_THEME", "classic")
    h = _make_handler()
    h._serve_plugin_app("qubit-office/habblaud/?theme=demo")
    assert "data-office-theme" not in _body(h)

    monkeypatch.setenv("QUBIT_OFFICE_THEME", "demo")
    h = _make_handler()
    h._serve_plugin_app("qubit-office/habblaud/")
    assert len(_style_blocks(_body(h))) == 1


# --------------------------------------------------------------------------
# 3. The existing token/stream globals survive, and the CSP is unchanged
# --------------------------------------------------------------------------
def test_token_and_stream_globals_survive_the_style(plugin_root, themed_home):
    h = _make_handler(sub="qubit-office/habblaud/?theme=demo", token="secret")
    h._serve_plugin_app("qubit-office/habblaud/?theme=demo")
    body = _body(h)
    assert "window.__QUBIT_SESSION_TOKEN__=" in body
    assert "window.__QUBIT_OFFICE_STREAM_URL__=" in body
    assert len(_style_blocks(body)) == 1


def test_csp_still_allows_inline_styles():
    headers = dict(W.security_headers(allow_same_origin_frame=True))
    csp = headers["Content-Security-Policy"]
    assert "style-src 'self' 'unsafe-inline'" in csp
    assert "script-src 'self' 'unsafe-inline'" in csp
    # The office document is the one same-origin frame.
    assert "frame-ancestors 'self'" in csp


# --------------------------------------------------------------------------
# 4. The host stylesheet is generated and host-scoped
# --------------------------------------------------------------------------
def test_host_theme_stylesheet_is_generated_and_scoped(themed_home):
    h = _make_handler()
    h._serve_theme_static(f"{W.WebUIHandler._THEME_STATIC_PREFIX}{THEME_ID}/theme.css")
    assert h._captured["code"] == 200, h._captured
    assert h._captured["ctype"] == "text/css; charset=utf-8"
    css = h._captured["body"].decode("utf-8")
    assert f':root[data-office-theme="{THEME_ID}"]' in css
    assert f"{THEME_TOKEN_VAR}: {THEME_TOKEN_VALUE};" in css
    assert "url(" not in css
    variables = set(re.findall(r"(--[a-z0-9-]+):", css))
    assert variables <= set(T.ALLOWED_THEME_VARS)


def test_host_theme_stylesheet_404s_for_classic_and_unknown(themed_home):
    for theme_id in ("classic", "nope", "../demo"):
        h = _make_handler()
        h._serve_theme_static(f"{W.WebUIHandler._THEME_STATIC_PREFIX}{theme_id}/theme.css")
        assert h._captured["code"] == 404, (theme_id, h._captured)


# --------------------------------------------------------------------------
# 5. The dashboard shell injects the resolved host payload (office route only)
# --------------------------------------------------------------------------
def _shell_body(tmp_path, path, index_html=None):
    dist = tmp_path / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text(
        index_html or
        "<!doctype html><html><head></head><body>shell</body></html>",
        encoding="utf-8")
    h = _make_handler(path=path)
    h._qubit_dist = lambda: dist  # type: ignore[method-assign]
    h._serve_qubit_index()
    return _body(h)


def _theme_global(body):
    match = re.search(
        r"window\.__QUBIT_OFFICE_THEME__=(\{.*?\});</script>", body, re.S)
    assert match, body
    return json.loads(match.group(1))


def test_office_route_injects_the_classic_global(tmp_path, themed_home):
    body = _shell_body(tmp_path, "/qubit-office")
    payload = _theme_global(body)
    assert payload["id"] == "classic"
    assert payload["active"] is False
    # The small host payload shape, with no file map and no filesystem path.
    for key in ("label", "version", "credit", "license", "templateVersion",
                "assetBase", "atlas", "index", "bodies", "tokens", "slices"):
        assert key in payload, key
    assert "files" not in payload
    assert "/home/" not in json.dumps(payload)


def test_office_route_injects_the_env_active_theme(tmp_path, themed_home,
                                                  monkeypatch):
    monkeypatch.setenv("QUBIT_OFFICE_THEME", THEME_ID)
    body = _shell_body(tmp_path, "/qubit-office/")
    payload = _theme_global(body)
    assert payload["id"] == THEME_ID
    assert payload["active"] is True
    assert payload["tokens"]["color-surface"] == THEME_TOKEN_VALUE
    assert payload["assetBase"].endswith(f"/themes/{THEME_ID}")
    assert "files" not in payload


def test_office_route_honours_the_theme_query(tmp_path, themed_home):
    body = _shell_body(tmp_path, f"/qubit-office?theme={THEME_ID}")
    payload = _theme_global(body)
    assert payload["id"] == THEME_ID
    assert payload["active"] is True


def test_office_route_invalid_override_is_classic(tmp_path, themed_home):
    body = _shell_body(tmp_path, "/qubit-office?theme=Not_An_Id")
    payload = _theme_global(body)
    assert payload["id"] == "classic"
    assert payload["active"] is False


@pytest.mark.parametrize("path", ["/chat", "/sessions", "/qubit-office2",
                                  "/qubit-office-extra"])
def test_other_shell_routes_get_no_office_theme_global(tmp_path, themed_home,
                                                       path):
    body = _shell_body(tmp_path, path)
    assert "__QUBIT_OFFICE_THEME__" not in body, (path, body)


def test_pack_text_cannot_break_out_of_the_script(tmp_path, themed_home,
                                                  monkeypatch):
    payload = T._classic_payload(None)
    payload["label"] = '</script><script>alert(1)</script>'
    monkeypatch.setattr(
        T, "theme_payload",
        lambda cfg, requested=None: dict(payload))
    body = _shell_body(tmp_path, "/qubit-office")
    assert "<script>alert(1)</script>" not in body
    assert "\\u003c/script\\u003e" in body
    # The real global still parses after escaping.
    assert _theme_global(body)["label"] == payload["label"]


def test_host_stylesheet_rejects_an_invalid_theme_id(themed_home):
    # A directory alone must not make an id valid: _ID_RE is checked first.
    bad = T.user_theme_root() / "Demo"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "theme.json").write_text("{}", encoding="utf-8")
    h = _make_handler()
    h._serve_theme_static(
        f"{W.WebUIHandler._THEME_STATIC_PREFIX}Demo/theme.css")
    assert h._captured["code"] == 404
