"""Agent 3 (wave 2) — Qubit rebrand scan + served-vs-tracked bundle parity.

Static scan of the built/served dashboard bundle for upstream product identity,
and a byte-level parity check that the bundle the Qubit listener serves is the
tracked source bundle. Visible-string branding is asserted live in
``test_qbpv2_agent3_workflows`` (zero upstream names across every route); this
file covers the build/serve boundary and the render-boundary normalizer.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DIST = REPO_ROOT / "frontend" / "qubit-dist"
PUBLIC = REPO_ROOT / "frontend" / "qubit-web" / "public"
SRC = REPO_ROOT / "frontend" / "qubit-web" / "src"

REBRAND_FILES = [
    SRC / "lib" / "rebrand.ts",
    SRC / "pages" / "McpPage.tsx",
    SRC / "pages" / "ChannelsPage.tsx",
    SRC / "components" / "AutoField.tsx",
]


def test_rebrand_helper_covers_free_text_render_boundaries():
    helper = (SRC / "lib" / "rebrand.ts").read_text(encoding="utf-8")
    assert "UPSTREAM_PRODUCT_RE" in helper
    assert "Qubit" in helper
    for f in REBRAND_FILES[1:]:
        assert "@/lib/rebrand" in f.read_text(encoding="utf-8"), f


@pytest.mark.skipif(not (DIST / "index.html").is_file(),
                    reason="hermes-dist not built")
def test_built_shell_has_no_upstream_identity():
    html = (DIST / "index.html").read_text(encoding="utf-8")
    for bad in ("Hermes", "OpenClaw"):
        assert bad not in html, bad
    # No upstream-named public route prefix is emitted in the built shell.
    assert "/hermes/" not in html
    assert "/openclaw/" not in html


@pytest.mark.skipif(not (DIST / "index.html").is_file(),
                    reason="hermes-dist not built")
def test_served_plugin_bundles_carry_no_visible_upstream_name():
    for name in ("qubit-achievements", "kanban"):
        bundle = (DIST / "plugins" / name / "dist" / "index.js")
        src = bundle.read_text(encoding="utf-8")
        # The only allowed upstream token is the compat auth header name.
        for i, line in enumerate(src.splitlines(), 1):
            for bad in ("Hermes", "OpenClaw"):
                if bad in line:
                    assert "X-Qubit-Session-Token" in line, (name, i, line)


@pytest.mark.skipif(not (DIST / "index.html").is_file(),
                    reason="hermes-dist not built")
def test_served_bundle_matches_tracked_source_bundle(tmp_path):
    """The listener serves the tracked SPA bundle, byte-for-byte."""
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: test-token\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = 0.0

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    port = ui._server.server_address[1]
    try:
        checked = 0
        for root, _, files in os.walk(DIST):
            for fname in files:
                if fname == "index.html":
                    # The shell is transformed at serve time (token/prefix
                    # injection); the raw file itself was checked above.
                    continue
                path = Path(root) / fname
                rel = path.relative_to(DIST)
                url = f"http://127.0.0.1:{port}/{rel.as_posix()}"
                served = urllib.request.urlopen(url, timeout=15).read()
                disk = path.read_bytes()
                assert hashlib.sha256(served).hexdigest() == \
                    hashlib.sha256(disk).hexdigest(), f"served != disk: {rel}"
                checked += 1
        assert checked >= 50, checked
        # Plugin bundles the SPA loads are the tracked public copies.
        for name in ("qubit-achievements", "kanban"):
            for sub in ("dist/index.js", "dist/style.css"):
                served = urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/plugins/{name}/{sub}",
                    timeout=15).read()
                tracked = (PUBLIC / "plugins" / name / sub).read_bytes()
                assert hashlib.sha256(served).hexdigest() == \
                    hashlib.sha256(tracked).hexdigest(), (name, sub)
        fleet = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/plugins/fleet.json", timeout=15).read())
        assert {m["name"] for m in fleet} == {"qubit-achievements", "kanban"}
    finally:
        ui.stop()
