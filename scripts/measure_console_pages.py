#!/usr/bin/env python
"""Measure console-page structure (before/after rebuild).

Per page: innerText length, leaf text-block count, and counts of elements
with a NON-transparent computed background-color and/or a visible border.
Also records console errors.

Usage: .venv/bin/python scripts/measure_console_pages.py <label>
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8890"

ROUTES = [
    ("scheduler", "/qubit-scheduler"),
    ("session-ops", "/session-ops"),
    ("worktrees", "/worktrees"),
    ("checkpoints", "/checkpoints"),
    ("nodes", "/nodes"),
    ("skills-review", "/admin/skills-review"),
]


def fetch_token() -> str:
    body = urllib.request.urlopen(BASE + "/", timeout=5).read().decode("utf-8", "replace")
    m = re.search(r'window\.__QUBIT_SESSION_TOKEN__="([^"]+)"', body)
    return m.group(1) if m else ""


MEASURE_JS = r"""
() => {
  const body = document.body;
  const text = body.innerText || "";
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
  let longest = "";
  for (const l of leaves) {
    const norm = l.replace(/\s+/g, " ").trim();
    if (norm.length > longest.length) longest = norm;
  }
  // Elements with a non-transparent computed background OR a visible border.
  const vis = body.getBoundingClientRect();
  let styledSurfaces = 0;
  let bordered = 0;
  let filled = 0;
  const samples = [];
  for (const el of all) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const cs = getComputedStyle(el);
    const bg = cs.backgroundColor || "";
    // parse alpha from rgb()/rgba() or color(srgb ... / a)
  function alphaOf(col) {
    if (!col) return 0;
    const m = col.match(/rgba?\(([^)]+)\)/);
    if (m) {
      const parts = m[1].split(",").map((s) => s.trim());
      return parts.length === 4 ? parseFloat(parts[3]) : 1;
    }
    const c = col.match(/color\([^)]*\)/);
    if (c) {
      const slash = c[0].match(/\/\s*([0-9.]+)\s*\)/);
      return slash ? parseFloat(slash[1]) : 1;
    }
    return col === "transparent" ? 0 : 1;
  }
  const vis = body.getBoundingClientRect();
  let styledSurfaces = 0;
  let bordered = 0;
  let filled = 0;
  const samples = [];
  for (const el of all) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const cs = getComputedStyle(el);
    const bgVisible = alphaOf(cs.backgroundColor) > 0.01;
    // border: width and style and a non-transparent colour
    const bw = ["Top","Right","Bottom","Left"].map((s) => parseFloat(cs["border" + s + "Width"]) || 0);
    const bs = cs.borderTopStyle;
    const borderVisible = Math.max(...bw) >= 1 && bs !== "none" && alphaOf(cs.borderTopColor) > 0.01;
    if (bgVisible) filled++;
    if (borderVisible) bordered++;
    if (bgVisible || borderVisible) {
      styledSurfaces++;
      if (samples.length < 12) {
        samples.push((el.className && typeof el.className === "string" ? el.className : "").slice(0, 70) || el.tagName);
      }
    }
  }
  const cls = (sel) => body.querySelectorAll(sel).length;
  return {
    innerTextLen: text.length,
    leafCount: leaves.length,
    longestBlockLen: longest.length,
    longestBlockHead: longest.slice(0, 120),
    styledSurfaces,
    filled,
    bordered,
    samples,
    settingsPage: cls(".settings-page"),
    settingsSection: cls(".settings-section"),
    settingsRow: cls(".settings-row"),
    settingsGroup: cls(".settings-group"),
    omCard: cls('[data-om="card"]'),
    buttons: cls("button"),
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
                extra_http_headers={"X-Hermes-Session-Token": token},
            )
            page = ctx.new_page()
            page.on("console", lambda msg, _e=errors: _e.append(msg.text[:200]) if msg.type == "error" else None)
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
