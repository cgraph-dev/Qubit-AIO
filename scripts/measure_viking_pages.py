#!/usr/bin/env python
"""Measure the five Open Viking console pages for shell consistency.

Per page: innerText length, section count, count of elements with a
non-transparent computed background OR a visible border, number of
controls, number of controls with NO surface in themselves or their
3 nearest ancestors, console errors, and any vertical-text leaf
(element narrower than 20px and taller than 30px that carries text).

Usage: .venv/bin/python scripts/measure_viking_pages.py <label>
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8890"

ROUTES = [
    ("playground", "/open-viking"),
    ("retrieval", "/open-viking/retrieval"),
    ("uploads", "/open-viking/uploads"),
    ("resources", "/open-viking/resources"),
    ("agent-hub", "/open-viking/agent-hub"),
]

HEADER = "X-Hermes-Session-Token"


def fetch_token() -> str:
    body = urllib.request.urlopen(BASE + "/", timeout=5).read().decode("utf-8", "replace")
    m = re.search(r'window\.__QUBIT_SESSION_TOKEN__="([^"]+)"', body)
    return m.group(1) if m else ""


MEASURE_JS = r"""
() => {
  const body = document.body;
  const text = body.innerText || "";
  const all = Array.from(body.querySelectorAll("*"));
  function alphaOf(col) {
    if (!col) return 0;
    const m = col.match(/rgba?\(([^)]+)\)/);
    if (m) {
      const parts = m[1].split(",").map((s) => s.trim());
      return parts.length === 4 ? parseFloat(parts[3]) : 1;
    }
    if (col === "transparent" || col === "rgba(0, 0, 0, 0)") return 0;
    return 1;
  }
  function hasSurface(el) {
    const cs = getComputedStyle(el);
    if (alphaOf(cs.backgroundColor) > 0.01) return true;
    const bw = ["Top", "Right", "Bottom", "Left"].map((s) => parseFloat(cs["border" + s + "Width"]) || 0);
    const bs = cs.borderTopStyle;
    return Math.max(...bw) >= 1 && bs !== "none" && alphaOf(cs.borderTopColor) > 0.01;
  }
  let styledSurfaces = 0;
  const vertical = [];
  for (const el of all) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    if (hasSurface(el)) styledSurfaces++;
    const t = (el.innerText || "").trim();
    const isLeaf = t && !Array.from(el.children).some((c) => (c.innerText || "").trim());
    if (isLeaf && r.width < 20 && r.height > 30) {
      vertical.push({ cls: (typeof el.className === "string" ? el.className : "").slice(0, 60), text: t.slice(0, 40), w: Math.round(r.width), h: Math.round(r.height) });
    }
  }
  const controls = Array.from(body.querySelectorAll("button, input, textarea, select, a[href], [role=button], [role=tab]"));
  let controlCount = 0;
  let unsurfaced = 0;
  const unsurfacedSamples = [];
  for (const c of controls) {
    const r = c.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;  // not painted
    controlCount++;
    let surf = false;
    let cur = c;
    for (let i = 0; i < 4 && cur && cur !== body; i++) {
      if (hasSurface(cur)) { surf = true; break; }
      cur = cur.parentElement;
    }
    if (!surf) {
      unsurfaced++;
      if (unsurfacedSamples.length < 8) unsurfacedSamples.push(((typeof c.className === "string" ? c.className : "") || c.tagName).slice(0, 70));
    }
  }
  const cls = (sel) => body.querySelectorAll(sel).length;
  return {
    innerTextLen: text.length,
    sectionCount: cls(".settings-section"),
    rowCount: cls(".settings-row"),
    styledSurfaces,
    controls: controlCount,
    controlsNoSurface: unsurfaced,
    unsurfacedSamples,
    verticalText: vertical,
    hasHeader: !!document.querySelector("h1"),
  };
}
"""


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    token = fetch_token()
    out = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for name, route in ROUTES:
            errors: list[str] = []
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 950},
                extra_http_headers={HEADER: token},
            )
            page = ctx.new_page()
            page.on(
                "console",
                lambda msg, _e=errors: _e.append(msg.text[:200]) if msg.type == "error" else None,
            )
            try:
                page.goto(BASE + route, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(2000)
                data = page.evaluate(MEASURE_JS)
            except Exception as exc:  # noqa: BLE001
                data = {"error": f"{type(exc).__name__}: {exc}"}
            data["consoleErrors"] = errors
            out[name] = data
            ctx.close()
        browser.close()
    print(json.dumps({"label": label, "pages": out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
