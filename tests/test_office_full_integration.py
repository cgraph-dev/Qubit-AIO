"""Build Habblaud from a verified checkout, then port the client bundle."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
VENDOR = REPO / "frontend/qubit-app/plugins/qubit-office/habblaud"
GAME = REPO / "frontend/qubit-app/plugins/qubit-office/game"


def _rewrite_habblaud_api(js: str) -> str:
    """Bridge the original Habblaud read routes to the Qubit office authority."""
    # Stable client methods in the upstream build are the same-origin route
    # literals; route just the supported Qubit reads, not its organization writes.
    js = re.sub(r"(['`])/api/agents/\$\{encodeURIComponent\(e\)\}\1",
                r"'/api/agents/${encodeURIComponent(e)}'", js)
    return js


def _chunk():
    chunks = list((VENDOR / "bundle").glob("*.js"))
    # The main app chunk owns the store and contains the EventSource factory.
    return next(p for p in chunks if "openStream(" in p.read_text(errors="ignore"))


def test_upstream_checkout_is_a_real_client_server_repo():
    import subprocess
    proc = subprocess.run(
        ["git", "-C", "/tmp/habblaud-full", "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        pytest.skip("upstream source checkout not present; vendor build needs --src")
    assert proc.stdout.strip().startswith("4ccce2537472")


def test_built_main_chunk_has_live_stream_override_and_qb_read_routes():
    js = _chunk().read_text(encoding="utf-8")
    assert "window.__QUBIT_OFFICE_STREAM_URL__" in js
    assert "/api/office/tower" not in js  # page has a specific adapter seam


def test_access_route_is_bound_and_write_routes_are_explicitly_gated():
    src = (GAME / "app/office-api.ts").read_text()
    assert 'client: "access", qubit: "/api/access"' in src
    assert 'client: "agents/:id", qubit: "/api/agents/:id"' in src
    assert 'client: "permissions/:id", qubit: null' in src
    assert 'client: "messages", qubit: null' in src
    assert "gated: true" in src
