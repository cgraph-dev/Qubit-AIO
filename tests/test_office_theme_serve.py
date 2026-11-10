"""Task 4: serve local theme files and expose seat atlas cells.

WHAT THIS PINS
--------------
Tasks 1-3 built the data-only theme contract, the loader, and the atlas
builder. Task 4 connects them to the running product:

  * ``GET /api/office/theme`` returns the trimmed payload (never a file/hash
    map), falls back to ``classic`` with a reason, and is a Qubit read
    authority (never fork-proxied);
  * every agents-panel seat row carries ``portrait``/``body`` cells only when
    the active theme's index lists that persona, and the panel still returns
    all 283 seats;
  * ``/plugins/qubit-office/themes/<id>/<rel>`` serves the pack from
    ``$QUBIT_HOME/office/themes/<id>`` with a short cache and an image type,
    while traversal, symlinks, unlisted suffixes, and classic/unknown ids are
    all 404.

Hermetic: a temporary ``QUBIT_HOME`` and tiny Pillow-written images only. No
art is committed, no operator path is read, no live service is touched.
"""
from __future__ import annotations

import hashlib
import io
import json

import pytest
from PIL import Image

from voicebridge import qubit_compat as HC
from voicebridge import webui as ui
from voicebridge.office import agent_panel as PANEL
from voicebridge.office import theme as T

pytestmark = pytest.mark.unit

ROUTE = "/api/office/theme"
THEME_ID = "demo"
ASSET_PREFIX = f"/plugins/qubit-office/themes/{THEME_ID}/"


# ======================================================================
# Fixtures / helpers
# ======================================================================
def _tokens():
    # The same values the contract test uses; they clear the contrast floor.
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


def _png_bytes(size=(32, 32), color=(24, 30, 48, 255)):
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _webp_bytes(size=(8, 8), color=(200, 40, 80, 255)):
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="WEBP")
    return buf.getvalue()


def _write(root, relpath, data):
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _isolate(monkeypatch, tmp_path):
    """A throwaway home with no ambient active theme."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(home))
    monkeypatch.delenv("QUBIT_OFFICE_THEME", raising=False)
    return home


def _write_pack_at(themes_root, portrait_cells=None, body_cells=None):
    """Write a complete, valid local pack under an arbitrary themes root."""
    root = themes_root / THEME_ID
    root.mkdir(parents=True, exist_ok=True)
    portrait_cells = {} if portrait_cells is None else portrait_cells
    body_cells = {} if body_cells is None else body_cells

    files = {}
    files["portraits/atlas32.png"] = _write(
        root, "portraits/atlas32.png", _png_bytes())
    files["bodies/atlas32.png"] = _write(
        root, "bodies/atlas32.png", _png_bytes(size=(32, 64)))
    files["preview.webp"] = _write(root, "preview.webp", _webp_bytes())

    portrait_index = {"schema": 1, "size": 32, "cols": 4, "rows": 4,
                      "cells": dict(portrait_cells)}
    body_index = {"schema": 1, "size": 32, "frame": {"w": 32, "h": 64},
                  "cols": 4, "rows": 4, "cells": dict(body_cells)}
    files["portraits/index.json"] = _write(
        root, "portraits/index.json", json.dumps(portrait_index).encode())
    files["bodies/index.json"] = _write(
        root, "bodies/index.json", json.dumps(body_index).encode())

    manifest = {
        "format": T.FORMAT,
        "schema": T.SCHEMA,
        "id": THEME_ID,
        "label": "Demo",
        "version": "1.0.0",
        "credit": "LimeZu — https://limezu.itch.io",
        "license": "LimeZu licence; redistribution prohibited",
        "source": "owner purchase, 2026-11-09",
        "template_version": 1,
        "tokens": _tokens(),
        "slices": {},
        "files": files,
        "limits": {"max_total_bytes": 10_000_000, "max_files": 20},
        "portraits": {"atlas": "portraits/atlas32.png",
                      "index": "portraits/index.json",
                      "size": 32, "cols": 4, "rows": 4},
        "bodies": {"atlas": "bodies/atlas32.png",
                   "index": "bodies/index.json",
                   "size": 32, "cols": 4, "rows": 4,
                   "frame": {"w": 32, "h": 64}},
    }
    (root / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _write_pack(home, portrait_cells=None, body_cells=None):
    """Write a valid pack into the operator's user theme root."""
    return _write_pack_at(home / "office" / "themes",
                          portrait_cells=portrait_cells,
                          body_cells=body_cells)


class _FakeApp:
    api_token = "theme-serve-token"
    allowed_origins: list[str] = []


class _FakeServer:
    app = _FakeApp()


class _FakeWfile:
    def __init__(self):
        self.buf = io.BytesIO()

    def write(self, data):
        self.buf.write(data)

    def getvalue(self):
        return self.buf.getvalue()


def _handler(accept_encoding=None):
    handler = object.__new__(ui.WebUIHandler)
    handler.server = _FakeServer()
    handler._head_only = False
    handler.headers = (
        {"Accept-Encoding": accept_encoding} if accept_encoding else {})
    handler._qubit_dist = lambda: None  # type: ignore[method-assign]
    handler.wfile = _FakeWfile()
    captured = {}

    def send_response(code):
        captured["code"] = code

    def send_header(name, value):
        captured.setdefault("headers", {})[name] = value

    handler.send_response = send_response  # type: ignore[method-assign]
    handler.send_header = send_header  # type: ignore[method-assign]
    handler.end_headers = lambda: None  # type: ignore[method-assign]
    return handler, captured


# ======================================================================
# 1 - the route, the classic payload, and the fallback
# ======================================================================
def test_theme_route_is_registered():
    route, _params = HC.match("GET", ROUTE)
    assert route is not None, f"no compat route matches {ROUTE}"
    assert route["handler"] == "h_office_theme"


def test_theme_is_a_qubit_read_authority():
    assert ROUTE in ui.QUBIT_READ_AUTHORITY
    assert ui.qubit_read_authority(ROUTE)
    assert ui.qubit_read_authority("/api/office/theme/")


def test_classic_payload_is_empty(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    code, out = HC.dispatch(None, "GET", ROUTE)
    assert code == 200
    assert out["ok"] is True
    assert out["schema"] == "qubit-office-theme-payload/1"
    assert out["id"] == "classic"
    assert out["active"] is False
    assert out["assetBase"] == ""
    assert out["atlas"] is None and out["index"] is None
    assert out["bodies"] == {"atlas": None, "index": None,
                             "size": None, "frame": None}
    assert out["tokens"] == {} and out["slices"] == {}


def test_theme_query_returns_the_active_payload(monkeypatch, tmp_path):
    home = _isolate(monkeypatch, tmp_path)
    _write_pack(home)
    code, out = HC.dispatch(None, "GET", ROUTE, query={"theme": THEME_ID})
    assert code == 200
    assert out["id"] == THEME_ID and out["active"] is True
    assert out["assetBase"] == f"/plugins/qubit-office/themes/{THEME_ID}"
    assert out["atlas"].startswith(ASSET_PREFIX + "portraits/atlas32.png")
    assert out["index"].startswith(ASSET_PREFIX + "portraits/index.json")
    assert out["bodies"]["atlas"].startswith(ASSET_PREFIX + "bodies/atlas32.png")
    assert out["bodies"]["index"].startswith(ASSET_PREFIX + "bodies/index.json")
    assert "?v=1.0.0" in out["atlas"]
    assert out["tokens"] and out["slices"] == {}
    # The payload is URLs + tokens, never a file/hash map or a filesystem path.
    assert "files" not in out
    assert str(home) not in json.dumps(out)


def test_missing_and_invalid_theme_fall_back_to_classic_with_a_reason(
        monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    code, out = HC.dispatch(None, "GET", ROUTE, query={"theme": "nope"})
    assert (code, out["id"]) == (200, "classic")
    assert "not found" in out["reason"]

    code, out = HC.dispatch(None, "GET", ROUTE, query={"theme": "Bad Id"})
    assert (code, out["id"]) == (200, "classic")
    assert "invalid" in out["reason"]


# ======================================================================
# 2 - seat rows carry atlas cells only when the index lists the persona
# ======================================================================
def test_seat_rows_carry_cells_only_for_indexed_personas(monkeypatch, tmp_path):
    home = _isolate(monkeypatch, tmp_path)

    rows = PANEL.panel()["seats"]
    assert len(rows) == 283
    listed = rows[0]["persona_slug"]
    unlisted = next(r["persona_slug"] for r in rows
                    if r["persona_slug"] != listed)

    _write_pack(home,
                portrait_cells={listed: [1, 2]},
                body_cells={listed: [3, 0]})
    monkeypatch.setenv("QUBIT_OFFICE_THEME", THEME_ID)

    out = PANEL.panel(
        portrait=lambda slug: T.cell_for(None, slug, "portraits"),
        body=lambda slug: T.cell_for(None, slug, "bodies"),
    )
    assert out["ok"] is True
    assert len(out["seats"]) == 283

    by_persona = {r["persona_slug"]: r for r in out["seats"]}
    assert by_persona[listed]["portrait"] == [1, 2]
    assert by_persona[listed]["body"] == [3, 0]
    assert by_persona[unlisted]["portrait"] is None
    assert by_persona[unlisted]["body"] is None
    # Both keys exist on every row, indexed or not.
    assert all("portrait" in r and "body" in r for r in out["seats"])


def test_panel_route_passes_theme_cells(monkeypatch, tmp_path):
    home = _isolate(monkeypatch, tmp_path)
    rows = PANEL.panel()["seats"]
    listed = rows[0]["persona_slug"]
    _write_pack(home, portrait_cells={listed: [2, 1]})
    monkeypatch.setenv("QUBIT_OFFICE_THEME", THEME_ID)

    code, out = HC.dispatch(None, "GET", "/api/office/habblaud/agents/panel")
    assert code == 200 and out["ok"] is True
    assert out["counts"]["headcount"] == 283
    by_persona = {r["persona_slug"]: r for r in out["seats"]}
    assert by_persona[listed]["portrait"] == [2, 1]
    # The body index did not list it.
    assert by_persona[listed]["body"] is None


def test_panel_route_honours_the_theme_query(monkeypatch, tmp_path):
    """Config/env are classic; only ``?theme=demo`` activates the cells."""
    home = _isolate(monkeypatch, tmp_path)
    rows = PANEL.panel()["seats"]
    listed = rows[0]["persona_slug"]
    _write_pack(home, portrait_cells={listed: [2, 1]},
                body_cells={listed: [3, 0]})
    # _isolate cleared QUBIT_OFFICE_THEME and dispatch gets cfg=None (classic).
    code, out = HC.dispatch(None, "GET", "/api/office/habblaud/agents/panel",
                            query={"theme": THEME_ID})
    assert code == 200 and out["ok"] is True
    assert out["counts"]["headcount"] == 283
    by_persona = {r["persona_slug"]: r for r in out["seats"]}
    assert by_persona[listed]["portrait"] == [2, 1]
    assert by_persona[listed]["body"] == [3, 0]
    # Every other seat still carries null cells for the fixture pack.
    other = next(r for r in out["seats"]
                 if r["persona_slug"] != listed)
    assert other["portrait"] is None and other["body"] is None


# ======================================================================
# 3 - static serving from the local pack root
# ======================================================================
def test_demo_atlas_is_served_with_an_image_type(monkeypatch, tmp_path):
    home = _isolate(monkeypatch, tmp_path)
    _write_pack(home)
    handler, captured = _handler()
    handler._serve_qubit_static(ASSET_PREFIX + "portraits/atlas32.png")
    assert captured["code"] == 200
    assert captured["headers"]["Content-Type"] == "image/png"
    assert captured["headers"]["Cache-Control"] == "public, max-age=3600"
    assert handler.wfile.getvalue()[:8] == b"\x89PNG\r\n\x1a\n"


def test_webp_is_served_with_its_type(monkeypatch, tmp_path):
    home = _isolate(monkeypatch, tmp_path)
    _write_pack(home)
    handler, captured = _handler()
    handler._serve_qubit_static(ASSET_PREFIX + "preview.webp")
    assert captured["code"] == 200
    assert captured["headers"]["Content-Type"] == "image/webp"
    assert handler.wfile.getvalue()[:4] == b"RIFF"


def test_traversal_is_refused(monkeypatch, tmp_path):
    home = _isolate(monkeypatch, tmp_path)
    root = _write_pack(home)
    (home / "secret.png").write_bytes(_png_bytes())
    handler, captured = _handler()
    handler._serve_qubit_static(ASSET_PREFIX + "../../secret.png")
    assert captured["code"] == 404
    # The resolver refuses a traversal even if the route did not pre-filter it.
    assert T.theme_asset_path(THEME_ID, "../secret.png") is None
    assert T.theme_asset_path(THEME_ID, "portraits/../../secret.png") is None
    # An absolute path is refused too.
    assert T.theme_asset_path(THEME_ID, "/etc/passwd") is None
    assert root.is_dir()


def test_unlisted_suffix_is_refused(monkeypatch, tmp_path):
    home = _isolate(monkeypatch, tmp_path)
    root = _write_pack(home)
    (root / "evil.css").write_text("body{display:none}", encoding="utf-8")
    handler, captured = _handler()
    handler._serve_qubit_static(ASSET_PREFIX + "evil.css")
    assert captured["code"] == 404
    assert T.theme_asset_path(THEME_ID, "evil.css") is None


def test_symlinked_declared_file_is_refused(monkeypatch, tmp_path):
    home = _isolate(monkeypatch, tmp_path)
    root = _write_pack(home)
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png_bytes(color=(1, 2, 3, 255)))
    atlas = root / "portraits" / "atlas32.png"
    atlas.unlink()
    try:
        atlas.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("this filesystem cannot create symlinks")
    assert T.theme_asset_path(THEME_ID, "portraits/atlas32.png") is None
    handler, captured = _handler()
    handler._serve_qubit_static(ASSET_PREFIX + "portraits/atlas32.png")
    assert captured["code"] == 404


def test_unknown_theme_is_404(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    handler, captured = _handler()
    handler._serve_qubit_static(
        "/plugins/qubit-office/themes/nope/portraits/atlas32.png")
    assert captured["code"] == 404
    assert T.theme_asset_path("nope", "portraits/atlas32.png") is None


def test_classic_has_no_files(monkeypatch, tmp_path):
    home = _isolate(monkeypatch, tmp_path)
    _write_pack(home)
    handler, captured = _handler()
    handler._serve_qubit_static(
        "/plugins/qubit-office/themes/classic/portraits/atlas32.png")
    assert captured["code"] == 404
    assert T.theme_asset_path("classic", "portraits/atlas32.png") is None


# ----------------------------------------------------------------------
# 3a - the static route serves ONLY from the user theme root
# ----------------------------------------------------------------------
def test_pack_only_in_a_bundled_root_is_not_served(monkeypatch, tmp_path):
    """A pack the loader can see in a bundled root must never be served.

    ``find_theme`` still searches the bundled/source roots (the payload may
    report such a pack), but the static allowlist is the operator's
    ``$QUBIT_HOME/office/themes``. This registers a fake bundled root with a
    real, valid pack in it and proves the route answers 404.
    """
    home = _isolate(monkeypatch, tmp_path)
    bundled = tmp_path / "bundled" / "themes"
    bundled_pack = _write_pack_at(bundled)
    monkeypatch.setattr(T, "theme_roots", lambda: [bundled])

    # The loader-level lookup DOES see it...
    assert T.find_theme(THEME_ID) == bundled_pack
    # ...but the user root has no pack, so nothing is served.
    assert not (home / "office" / "themes" / THEME_ID).exists()
    assert T.theme_asset_path(THEME_ID, "portraits/atlas32.png") is None
    handler, captured = _handler()
    handler._serve_qubit_static(ASSET_PREFIX + "portraits/atlas32.png")
    assert captured["code"] == 404


def test_declared_atlas_is_served_from_the_user_root(monkeypatch, tmp_path):
    """The bytes served are the user-root pack's, even if dist has a decoy."""
    home = _isolate(monkeypatch, tmp_path)
    user_pack = _write_pack(home)
    resolved = T.theme_asset_path(THEME_ID, "portraits/atlas32.png")
    assert resolved == (user_pack / "portraits" / "atlas32.png").resolve()

    dist = tmp_path / "dist" / "plugins" / "qubit-office" / "themes"
    (dist / THEME_ID / "portraits").mkdir(parents=True)
    (dist / THEME_ID / "portraits" / "atlas32.png").write_bytes(
        _png_bytes(color=(255, 0, 0, 255)))
    handler, captured = _handler()
    handler._qubit_dist = lambda: tmp_path / "dist"  # type: ignore[assignment]
    handler._serve_qubit_static(ASSET_PREFIX + "portraits/atlas32.png")
    assert captured["code"] == 200
    assert handler.wfile.getvalue() == _png_bytes()


# ----------------------------------------------------------------------
# 3b - only declared files of a valid pack are served
# ----------------------------------------------------------------------
def test_theme_json_is_not_an_asset(monkeypatch, tmp_path):
    home = _isolate(monkeypatch, tmp_path)
    root = _write_pack(home)
    assert (root / "theme.json").is_file()
    assert T.theme_asset_path(THEME_ID, "theme.json") is None
    handler, captured = _handler()
    handler._serve_qubit_static(ASSET_PREFIX + "theme.json")
    assert captured["code"] == 404


def test_undeclared_file_is_not_served(monkeypatch, tmp_path):
    home = _isolate(monkeypatch, tmp_path)
    root = _write_pack(home)
    (root / "portraits" / "extra.png").write_bytes(_png_bytes())
    assert T.theme_asset_path(THEME_ID, "portraits/extra.png") is None
    handler, captured = _handler()
    handler._serve_qubit_static(ASSET_PREFIX + "portraits/extra.png")
    assert captured["code"] == 404


def test_invalid_pack_exposes_nothing(monkeypatch, tmp_path):
    home = _isolate(monkeypatch, tmp_path)
    root = _write_pack(home)
    (root / "portraits" / "atlas32.png").unlink()  # declared file now missing
    assert T.theme_asset_path(THEME_ID, "portraits/atlas32.png") is None
    handler, captured = _handler()
    handler._serve_qubit_static(ASSET_PREFIX + "portraits/atlas32.png")
    assert captured["code"] == 404
    # Even the index (still on disk and declared) is not served from an
    # invalid pack.
    assert T.theme_asset_path(THEME_ID, "portraits/index.json") is None
