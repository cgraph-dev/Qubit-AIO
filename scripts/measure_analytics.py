#!/usr/bin/env python
"""Measure the /analytics page structure: innerText length, leaf text-block
count, and the longest single text block (the "wall of prose" metric).

Usage: .venv/bin/python scripts/measure_analytics.py [label]
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8890"


def fetch_token() -> str:
    body = urllib.request.urlopen(BASE + "/", timeout=5).read().decode("utf-8", "replace")
    m = re.search(r'window\.__QUBIT_SESSION_TOKEN__="([^"]+)"', body)
    return m.group(1) if m else ""


MEASURE_JS = r"""
() => {
  const body = document.body;
  const text = body.innerText || "";
  // Leaf text blocks: elements that contain text but no element children with text.
  const all = Array.from(body.querySelectorAll("*"));
  const leaves = [];
  for (const el of all) {
    const t = (el.innerText || "").trim();
    if (!t) continue;
    let hasTextChild = false;
    for (const c of el.children) {
      if ((c.innerText || "").trim()) { hasTextChild = true; break; }
    }
    if (!hasTextChild) leaves.push(t);
  }
  // Longest whitespace-normalized leaf block.
  let longest = "";
  for (const l of leaves) {
    const norm = l.replace(/\s+/g, " ").trim();
    if (norm.length > longest.length) longest = norm;
  }
  // Count structural settings classes present.
  const cls = (sel) => body.querySelectorAll(sel).length;
  return {
    innerTextLen: text.length,
    leafCount: leaves.length,
    longestBlockLen: longest.length,
    longestBlockHead: longest.slice(0, 160),
    settingsPage: cls(".settings-page"),
    settingsSection: cls(".settings-section"),
    settingsRow: cls(".settings-row"),
    settingsGroup: cls(".settings-group"),
    settingsSummary: cls(".settings-summary__tile"),
    settingsEmpty: cls(".settings-empty"),
    settingsStatus: cls(".settings-status"),
    h1Count: cls("h1"),
    paragraphs: cls("p"),
  };
}
"""


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    token = fetch_token()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = ctx.new_page()
        page.add_init_script(
            f"window.__QUBIT_SESSION_TOKEN__={json.dumps(token)};"
        )
        page.goto(BASE + "/analytics", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2500)
        data = page.evaluate(MEASURE_JS)
        page.screenshot(path=f"/tmp/analytics-{label}.png", full_page=True)
        browser.close()
    print(json.dumps({"label": label, **data}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
