"""Wave-2 W3 — product identity at the render boundary.

Qubit-owned backends still author upstream product names in free-text payloads
(MCP catalog descriptions, messaging platform descriptions, config help). The
frozen identity promise requires the normal dashboard to render "Qubit" while
legal/provenance records keep the upstream names. The SPA normalises those
strings in one shared helper (`src/lib/rebrand.ts`).
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

SRC = REPO_ROOT / "frontend" / "hermes-web" / "src"
DIST = REPO_ROOT / "frontend" / "hermes-dist"

UPSTREAM = re.compile(r"\b(Hermes|OpenClaw)\b")


def test_rebrand_helper_exists_and_is_used_at_the_render_boundary():
    helper = (SRC / "lib" / "rebrand.ts").read_text(encoding="utf-8")
    assert "UPSTREAM_PRODUCT_RE" in helper
    assert '"Qubit"' in helper
    for rel in ("components/AutoField.tsx", "pages/ChannelsPage.tsx",
                "pages/McpPage.tsx"):
        src = (SRC / rel).read_text(encoding="utf-8")
        assert "rebrand" in src and "@/lib/rebrand" in src, rel


@pytest.mark.skipif(not (DIST / "index.html").is_file(),
                    reason="hermes-dist not built (npm build step)")
def test_no_visible_upstream_product_name_on_free_text_pages(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")

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
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    port = ui._server.server_address[1]
    try:
        with pw.sync_playwright() as browser_pw:
            browser = browser_pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            for slug in ("channels", "config", "mcp", "skills", "models"):
                page.goto(f"http://127.0.0.1:{port}/{slug}",
                          timeout=30000)
                page.wait_for_timeout(2200)
                visible = page.evaluate("""() => {
                  const out = [];
                  const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT);
                  let n;
                  while ((n = walker.nextNode())) {
                    let el = n.parentElement, ok = true;
                    while (el && el !== document.body) {
                      if (getComputedStyle(el).display === 'none') {
                        ok = false; break;
                      }
                      el = el.parentElement;
                    }
                    if (ok && n.textContent.trim()) out.push(n.textContent.trim());
                  }
                  return out.join('\\n');
                }""")
                assert not UPSTREAM.search(visible), \
                    (slug, UPSTREAM.findall(visible)[:5])
            browser.close()
    finally:
        ui.stop()
