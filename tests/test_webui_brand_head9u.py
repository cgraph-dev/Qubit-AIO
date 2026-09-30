"""UI parity audit (2026-09-28) — served dashboard shell brand head.

D4: the theme ``customCSS`` is injected client-side into a single ``<style>``
tag whose canonical Qubit id is ``qubit-theme-custom-css``. A legacy overlay
theme comment still names ``hermes-theme-custom-css``; the webui head injection
normalizes that reference in the served shell.

D3/F7 constraint: the load-bearing wire identifiers
(``__HERMES_SESSION_TOKEN__``, ``__HERMES_BASE_PATH__``,
``X-Hermes-Session-Token``) are PRESERVED verbatim — they are never renamed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge import webui as ui  # noqa: E402

LEGACY = "hermes-theme-custom-css"
CANONICAL = "qubit-theme-custom-css"


class _FakeApp:
    api_token = "head-brand-test-token"


class _FakeServer:
    app = _FakeApp()


def _serve(base: Path) -> tuple[int, str]:
    """Invoke the real ``_serve_qubit_index`` against ``base`` (no socket)."""
    handler = object.__new__(ui.WebUIHandler)
    handler.server = _FakeServer()
    handler._qubit_dist = lambda: base  # type: ignore[method-assign]
    captured: dict[str, object] = {}

    def _send(code, body, ctype="text/plain"):
        captured["code"] = code
        captured["body"] = body
        captured["ctype"] = ctype

    handler._send = _send  # type: ignore[method-assign]
    handler._serve_qubit_index()
    assert captured["code"] == 200
    return captured["code"], bytes(captured["body"]).decode("utf-8")


def _write_index(base: Path, html: str) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    (base / "index.html").write_text(html, encoding="utf-8")
    return base


def test_debrand_helper_rewrites_only_theme_style_id():
    markup = (
        '<style id="hermes-theme-custom-css">'
        "window.__HERMES_SESSION_TOKEN__;window.__HERMES_BASE_PATH__;"
        "X-Hermes-Session-Token</style>"
    )
    out = ui.debrand_theme_style_id(markup)
    assert LEGACY not in out
    assert CANONICAL in out
    # Wire identifiers are preserved verbatim (never renamed).
    assert "window.__HERMES_SESSION_TOKEN__" in out
    assert "window.__HERMES_BASE_PATH__" in out
    assert "X-Hermes-Session-Token" in out


def test_served_head_replaces_stale_theme_style_id(tmp_path):
    base = _write_index(
        tmp_path / "dist",
        "<!doctype html><html><head><title>Legacy title</title>"
        "<!-- applyTheme injects into <style id=\"hermes-theme-custom-css\"> -->"
        "<script>window.__HERMES_SESSION_TOKEN__=\"t\";"
        "window.__HERMES_BASE_PATH__=\"\";</script></head>"
        "<body><div id=\"root\"></div></body></html>",
    )
    _, html = _serve(base)
    assert LEGACY not in html
    assert CANONICAL in html


def test_served_head_title_is_qubit_and_wire_globs_preserved(tmp_path):
    base = _write_index(
        tmp_path / "dist",
        "<!doctype html><html><head><title>Whatever</title>"
        "<script>window.__HERMES_SESSION_TOKEN__=\"t\";"
        "window.__HERMES_BASE_PATH__=\"\";</script></head><body></body></html>",
    )
    _, html = _serve(base)
    assert "<title>Qubit Agent - Command Center</title>" in html
    assert "window.__HERMES_SESSION_TOKEN__" in html
    assert "window.__HERMES_BASE_PATH__" in html


def test_served_real_dist_head_has_no_legacy_theme_style_id():
    dist = REPO_ROOT / "frontend" / "qubit-dist"
    if not (dist / "index.html").is_file():
        pytest.skip("dashboard dist not built")
    _, html = _serve(dist)
    assert LEGACY not in html
    # The wire globals the SPA reads must survive the head injection.
    assert "window.__HERMES_SESSION_TOKEN__" in html
    assert "window.__HERMES_BASE_PATH__" in html
    # No visible legacy brand text in the served shell (wire identifiers aside).
    lowered = html.lower()
    for token in ("nousresearch", "nous research", "openclaw"):
        assert token not in lowered
