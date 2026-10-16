"""Unit tests for the served-dashboard bundle freshness check.

The check (``voicebridge.webui.check_qubit_bundle``) is what stops a stale
``frontend/qubit-dist`` from failing silently: a bundle built from pre-rename
source reads a session-token global the server no longer emits, so every API
read 401s while the server logs nothing. These tests drive the checker
against fixture bundle trees, never the real checkout.
"""
from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge import webui as W  # noqa: E402


def _write_dist(root: Path, *, chunks: dict, refs=("assets/app.js",)) -> Path:
    dist = root / "qubit-dist"
    (dist / "assets").mkdir(parents=True)
    srcs = " ".join(f'src="/{r}"' for r in refs)
    (dist / "index.html").write_text(f"<html><head>{srcs}</head></html>")
    for name, body in chunks.items():
        (dist / name).write_text(body)
    return dist


def test_missing_index_is_not_ok(tmp_path):
    check = W.check_qubit_bundle(tmp_path / "qubit-dist")
    assert check["ok"] is False
    assert any("index.html" in i for i in check["issues"])


def test_legacy_token_global_is_flagged(tmp_path):
    dist = _write_dist(tmp_path, chunks={
        "assets/app.js": "var t=window.__HERMES_SESSION_TOKEN__;fetch(t);",
    })
    check = W.check_qubit_bundle(dist)
    assert check["ok"] is False
    assert check["has_legacy_global"] is True
    assert any("rebuild" in i for i in check["issues"])


def test_missing_asset_is_flagged(tmp_path):
    dist = _write_dist(tmp_path, chunks={
        "assets/app.js": "var t=window.__QUBIT_SESSION_TOKEN__;fetch(t);",
    }, refs=("assets/app.js", "assets/gone.js"))
    check = W.check_qubit_bundle(dist)
    assert check["ok"] is False
    assert check["missing_assets"] == ["assets/gone.js"]


def test_current_bundle_shape_is_ok(tmp_path):
    dist = _write_dist(tmp_path, chunks={
        "assets/app.js": "var t=window.__QUBIT_SESSION_TOKEN__;fetch(t);",
    })
    check = W.check_qubit_bundle(dist)
    assert check["ok"] is True, check["issues"]
    assert check["has_token_global"] is True
    assert check["has_legacy_global"] is False


def test_legacy_name_in_comments_only_is_ok(tmp_path):
    dist = _write_dist(tmp_path, chunks={
        "assets/app.js": ("// delegates instead of reading window.__HERMES_SESSION_TOKEN__\n"
                          "/* legacy __HERMES_SESSION_TOKEN__ compat note */\n"
                          "var t=window.__QUBIT_SESSION_TOKEN__;fetch(t);"),
    })
    check = W.check_qubit_bundle(dist)
    assert check["ok"] is True, check["issues"]
    assert check["has_legacy_global"] is False
