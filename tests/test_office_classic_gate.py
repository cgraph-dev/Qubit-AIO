"""The classic rollback gate: ``office.theme: classic`` == the pre-theme office.

WHAT THIS PINS
--------------
The owner's zero-regression condition is that turning the theme off
(``office.theme: classic`` / ``QUBIT_OFFICE_THEME=classic``) renders exactly the
office that shipped before themes existed. The live deploy runs ``limezu``, so
the classic path has to be proven rather than assumed:

* the resolver reports ``classic`` and generates no theme style;
* the served office document carries no ``data-office-theme`` marker and no
  ``/plugins/qubit-office/themes/...`` URL — nothing that needs the local pack;
* the theme global, when the document carries it, is the inactive classic
  payload (``active: false``, ``surfaces: null``); the patched Habblaud provider
  treats that exactly like an absent global and keeps the procedural floors;
* ``QUBIT_OFFICE_THEME=classic`` beats both ``?theme=`` and ``office.theme``;
* the ``limezu`` case still serves the active payload, the 6 measured floor
  surfaces and the theme style.

The vendored provider is a build artifact (its source tests live in the patched
upstream checkout), so the last test is a narrow re-vendor tripwire: the bundled
bytes must still read the global, require ``active === true``, and fall through
to the procedural floor when no tile is loaded.

Hermetic: a temporary ``QUBIT_HOME`` with a synthetic pack; no art, no network,
no live service.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path

import pytest

from voicebridge import resources
from voicebridge import webui as W
from voicebridge.office import theme as T

pytestmark = pytest.mark.unit

THEME_ID = "limezu"
SIX_FLOOR_KINDS = (
    "carpet",
    "wood",
    "tile_check",
    "tile_white",
    "concrete",
    "marble",
)
ASSET_PREFIX = f"/plugins/qubit-office/themes/{THEME_ID}/"
SAMPLE_HTML = (
    "<!doctype html>\n"
    "<html><head><meta charset=\"utf-8\">"
    "<link rel=\"stylesheet\" href=\"/assets/app.css\"></head>\n"
    "<body><script type=\"module\" src=\"/main.js\"></script></body></html>"
)
_OFFICE_NONCE = "classic-gate-nonce"

_REPO = Path(__file__).resolve().parents[1]
_VENDOR = _REPO / "frontend/qubit-app/plugins/qubit-office/habblaud"

_real_resource_path = resources.resource_path


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------
def _tokens():
    return {
        "color-surface": "#0d0d1a",
        "color-surface-raised": "#161a23",
        "color-surface-hover": "#1d2230",
        "color-text-primary": "#e9edf7",
        "color-text-muted": "#8a93a8",
        "color-border": "#64748b",
        "color-accent": "#7cc8ff",
        "radius-control": "4px",
    }


def _write(root: Path, relpath: str, data: bytes) -> dict:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _write_pack(home: Path, kinds=SIX_FLOOR_KINDS) -> Path:
    """A complete, valid pack with the requested floor surfaces."""
    root = home / "office" / "themes" / THEME_ID
    files: dict = {}

    def write(rel: str, data: bytes) -> None:
        files[rel] = _write(root, rel, data)

    write("portraits/atlas.png", b"PNG-PORTRAITS")
    write("bodies/atlas.png", b"PNG-BODIES")
    write("portraits/index.json", json.dumps(
        {"schema": 1, "size": 32, "cols": 4, "rows": 4, "cells": {}}).encode())
    write("bodies/index.json", json.dumps(
        {"schema": 1, "size": 32, "frame": {"w": 32, "h": 64},
         "cols": 4, "rows": 4, "cells": {}}).encode())
    floor = {}
    for kind in kinds:
        rel = f"surfaces/floor_{kind}.png"
        write(rel, f"PNG-FLOOR-{kind}".encode())
        floor[kind] = rel

    manifest = {
        "format": T.FORMAT,
        "schema": T.SCHEMA,
        "id": THEME_ID,
        "label": "LimeZu test pack",
        "version": "1.0.0",
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
        "surfaces": {"cell": 16, "floor": floor},
    }
    (root / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@pytest.fixture()
def home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(root))
    monkeypatch.delenv("QUBIT_OFFICE_THEME", raising=False)
    return root


@pytest.fixture()
def plugin_root(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    (root / "qubit-office" / "habblaud").mkdir(parents=True)
    (root / "qubit-office" / "habblaud" / "index.html").write_text(
        SAMPLE_HTML, encoding="utf-8")
    monkeypatch.setattr(
        resources, "resource_path",
        lambda name: root if name == "frontend/qubit-app/plugins"
        else _real_resource_path(name))
    return root


def _make_handler(*, sub: str, cfg=None, token: str = ""):
    handler = object.__new__(W.WebUIHandler)
    handler.path = "/plugin-apps/" + sub
    handler.headers = {"Host": "127.0.0.1"}
    handler.client_address = ("127.0.0.1", 51234)
    handler._head_only = False

    class _FakeApp:
        api_token = token
        allowed_origins = ()

    app = _FakeApp()
    app._office_nonces = {_OFFICE_NONCE: time.monotonic() + 3600}
    app._office_nonce_lock = threading.Lock()
    if cfg is not None:
        app.orch = type("O", (), {"cfg": cfg})()
    handler.server = type("S", (), {"app": app})()
    captured: dict = {}

    def _send(code, body, ctype="text/plain", extra_headers=None,
              cache_control="no-store"):
        captured.update(code=code, body=body, ctype=ctype,
                        extra_headers=extra_headers,
                        cache_control=cache_control)

    handler._send = _send  # type: ignore[method-assign]
    handler._captured = captured
    return handler


def _serve(handler, sub: str) -> str:
    handler._serve_plugin_app(sub)
    assert handler._captured["code"] == 200, handler._captured
    return handler._captured["body"].decode("utf-8")


def _office_global(body: str):
    match = re.search(
        r"window\.__QUBIT_OFFICE_THEME__=(\{.*?\});</script>", body, re.S)
    assert match, body
    return json.loads(match.group(1))


def _global_script(body: str) -> str:
    match = re.search(
        r"<script>window\.__QUBIT_OFFICE_THEME__=\{.*?\};</script>", body, re.S)
    assert match, body
    return match.group(0)


# --------------------------------------------------------------------------
# 1. The resolver reports classic and injects no theme style
# --------------------------------------------------------------------------
def test_classic_resolver_payload_and_style(home, monkeypatch):
    _write_pack(home)
    monkeypatch.setenv("QUBIT_OFFICE_THEME", "classic")

    assert T.active_theme_id({"office": {"theme": THEME_ID}}) == "classic"
    payload = T.theme_payload({"office": {"theme": THEME_ID}})
    assert payload["id"] == "classic"
    assert payload["active"] is False
    assert payload["assetBase"] == ""
    assert payload["surfaces"] is None
    assert T.theme_style_html({"office": {"theme": THEME_ID}}) == ""

    script = T.office_document_script({"office": {"theme": THEME_ID}})
    assert json.loads(re.search(
        r"=(\{.*?\});</script>", script, re.S).group(1))["active"] is False


def test_config_classic_is_the_default_when_unset(home):
    _write_pack(home)
    assert T.active_theme_id({"office": {"theme": "classic"}}) == "classic"
    assert T.active_theme_id({"office": {}}) == "classic"
    assert T.active_theme_id(None) == "classic"
    assert T.theme_payload({"office": {"theme": "classic"}})["id"] == "classic"


# --------------------------------------------------------------------------
# 2. The served office document is the pre-theme document under classic
# --------------------------------------------------------------------------
def test_classic_document_has_no_theme_marker_or_asset_dependency(
        plugin_root, home, monkeypatch):
    _write_pack(home)
    monkeypatch.setenv("QUBIT_OFFICE_THEME", "classic")
    handler = _make_handler(sub="qubit-office/habblaud/")
    body = _serve(handler, "qubit-office/habblaud/")

    # No theme style, so no data-office-theme marker.
    assert "data-office-theme" not in body
    # Nothing references the local pack: no static asset is needed.
    assert "/plugins/qubit-office/themes/" not in body
    # The global is the inactive classic payload, never an active theme.
    payload = _office_global(body)
    assert payload["id"] == "classic"
    assert payload["active"] is False
    assert payload["assetBase"] == ""
    assert payload["surfaces"] is None
    assert payload["tokens"] == {}
    # Strip the one inert classic global and the served document is the app's
    # own HTML byte-for-byte (no token was configured on this fake app).
    assert body.replace(_global_script(body), "") == SAMPLE_HTML


def test_config_classic_document_is_also_the_pre_theme_document(
        plugin_root, home):
    _write_pack(home)
    # office.theme is the persisted rollback switch; no env/query involved.
    handler = _make_handler(
        sub="qubit-office/habblaud/",
        cfg={"office": {"theme": "classic"}})
    body = _serve(handler, "qubit-office/habblaud/")
    assert "data-office-theme" not in body
    assert "/plugins/qubit-office/themes/" not in body
    assert _office_global(body)["active"] is False


def test_env_classic_kill_switch_beats_query_and_config(
        plugin_root, home, monkeypatch):
    _write_pack(home)
    monkeypatch.setenv("QUBIT_OFFICE_THEME", "classic")
    handler = _make_handler(
        sub="qubit-office/habblaud/?theme=limezu",
        cfg={"office": {"theme": THEME_ID}})
    body = _serve(handler, "qubit-office/habblaud/?theme=limezu")
    assert "data-office-theme" not in body
    assert "/plugins/qubit-office/themes/" not in body
    assert _office_global(body)["id"] == "classic"


# --------------------------------------------------------------------------
# 3. limezu still serves the active payload, 6 surfaces and the style
# --------------------------------------------------------------------------
def test_limezu_query_serves_the_payload_and_six_surfaces(plugin_root, home):
    _write_pack(home)
    handler = _make_handler(sub="qubit-office/habblaud/?theme=limezu")
    body = _serve(handler, "qubit-office/habblaud/?theme=limezu")

    payload = _office_global(body)
    assert payload["id"] == THEME_ID
    assert payload["active"] is True
    assert payload["assetBase"] == f"/plugins/qubit-office/themes/{THEME_ID}"
    assert payload["surfaces"]["cell"] == 16
    assert set(payload["surfaces"]["floor"]) == set(SIX_FLOOR_KINDS)
    for kind in SIX_FLOOR_KINDS:
        url = payload["surfaces"]["floor"][kind]
        assert url == f"{ASSET_PREFIX}surfaces/floor_{kind}.png?v=1.0.0"
    assert payload["tokens"]
    assert payload["credit"].startswith("LimeZu")
    assert payload["license"]

    styles = re.findall(
        r'<style data-office-theme="([^"]*)">(.*?)</style>', body, re.S)
    assert len(styles) == 1 and styles[0][0] == THEME_ID
    assert "--ui-bg:" in styles[0][1]
    assert body.index("<style data-office-theme=") < body.index("</head>")


def test_limezu_from_config_without_a_query(plugin_root, home):
    _write_pack(home)
    handler = _make_handler(
        sub="qubit-office/habblaud/",
        cfg={"office": {"theme": THEME_ID}})
    body = _serve(handler, "qubit-office/habblaud/")
    payload = _office_global(body)
    assert payload["id"] == THEME_ID and payload["active"] is True
    assert set(payload["surfaces"]["floor"]) == set(SIX_FLOOR_KINDS)


# --------------------------------------------------------------------------
# 4. The vendored provider still falls back to the procedural floor
# --------------------------------------------------------------------------
def _bundle_file(pattern: str) -> str:
    matches = sorted((_VENDOR / "bundle").glob(pattern))
    assert matches, f"vendored bundle chunk {pattern} is missing"
    return "\n".join(path.read_text(encoding="utf-8") for path in matches)


def test_vendored_provider_reads_only_active_payloads_and_falls_back():
    main = _bundle_file("main-*.js")
    app = _bundle_file("app-*.js")

    # The provider reads the server-injected global...
    assert "__QUBIT_OFFICE_THEME__" in main
    # ...and only an ``active === true`` payload yields surfaces (classic/absent
    # both become null, so the client keeps the procedural floor).
    assert re.search(r"\.active!==!0\)return null", main)
    # The theme-tile getter returns null when nothing was cached.
    assert re.search(r"\?\?null:null", main)
    # floorChunk asks for a theme tile, uses it only when it has pixels, and the
    # very next statement is the procedural switch that shipped before themes.
    gate = re.search(
        r"let (\w+)=\w+\(e\);if\(\1&&\1\.w>0&&\1\.h>0\)return \w+\(\1\);"
        r"let \w+=new \w+\([^)]*\)",
        app)
    assert gate, "vendored floorChunk no longer falls through to procedural"
    tail = app[gate.end():]
    assert re.search(r"switch\(e\)\{case`wood`:", tail)
    assert re.search(r"case`marble`:", tail)
