"""Regression checks for rewriting the vendored app's stream endpoint."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vendor_habblaud", REPO / "scripts/vendor_habblaud.py")
assert SPEC and SPEC.loader
VENDOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VENDOR)


def test_rewriter_changes_exact_client_stream_factory_call_only():
    source = "this.openStream('/api/stream'); this.openStream('/api/other');"
    got = VENDOR.rewrite_js(source)
    assert "this.openStream(window.__QUBIT_OFFICE_STREAM_URL__||'/api/stream')" in got
    assert "this.openStream('/api/other')" in got
    assert "token=" not in got


def test_all_built_stream_chunks_use_host_stream_url_override():
    chunks = list((REPO / "frontend/qubit-app/plugins/qubit-office/habblaud/bundle").glob("*.js"))
    matches = [(p, p.read_text(errors="ignore")) for p in chunks
               if "window.__QUBIT_OFFICE_STREAM_URL__" in p.read_text(errors="ignore")]
    assert matches, "no built client SSE factory call found"
    for path, text in matches:
        rewritten = VENDOR.rewrite_js(text)
        assert "window.__QUBIT_OFFICE_STREAM_URL__" in rewritten, path
        assert "openStream(window.__QUBIT_OFFICE_STREAM_URL__||`/api/stream`)" in rewritten, path


def test_real_shipped_callsite_is_rewritten_and_preserves_upstream_fallback():
    bundle = REPO / "frontend/qubit-app/plugins/qubit-office/habblaud/bundle/app-U4Pwh2XA.js"
    text = bundle.read_text(errors="ignore")
    assert "openStream(window.__QUBIT_OFFICE_STREAM_URL__||`/api/stream`)" in text
    assert "window.__QUBIT_OFFICE_STREAM_URL__" in VENDOR.rewrite_js(
        "x.openStream('/api/stream')")


def test_vite_dynamic_preload_paths_stay_inside_embedded_app():
    source = ('const __vite__mapDeps=(i,m=__vite__mapDeps,d=(m.f||'
              '(m.f=["bundle/app.js","bundle/app.css"])))=>i.map(i=>d[i]);')
    rewritten = VENDOR.rewrite_js(source)
    assert "=>i.map(i=>new URL(d[i],`${location.origin}/plugin-apps/qubit-office/habblaud/`).href)" in rewritten
    # This is a document-relative URL, not a root-relative or protocol-relative
    # path. It keeps both the current origin and the Habblaud app directory.
    from urllib.parse import urljoin
    app_document = "https://example.test/nested/plugin-apps/qubit-office/habblaud/"
    assert urljoin(app_document, "bundle/app.js") == (
        "https://example.test/nested/plugin-apps/qubit-office/habblaud/bundle/app.js")
    assert urljoin(app_document, "/plugin-apps/qubit-office/habblaud/bundle/app.js") == (
        "https://example.test/plugin-apps/qubit-office/habblaud/bundle/app.js")


def test_shipped_main_chunk_rewrites_vite_preload_helper():
    chunk = REPO / "frontend/qubit-app/plugins/qubit-office/habblaud/bundle/main-DND_b4_E.js"
    assert chunk.is_file()
    text = chunk.read_text(errors="ignore")
    assert "=>i.map(i=>new URL(d[i],`${location.origin}/plugin-apps/qubit-office/habblaud/`).href)" in text
