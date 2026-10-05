#!/usr/bin/env python
"""Capture the 'before' state without reverting files: strip the qb-upstream
scope class (and, for checkpoints, the settings rebuild is already present but
its container classes are equivalent) at runtime before measuring."""
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
    for (const c of el.children) { if ((c.innerText || "").trim()) { hasTextChild = true; break; } }
    if (!hasTextChild) leaves.push(t);
  }
  function alphaOf(col) {
    if (!col) return 0;
    const m = col.match(/rgba?\(([^)]+)\)/);
    if (m) { const p = m[1].split(",").map((s)=>s.trim()); return p.length===4?parseFloat(p[3]):1; }
    const c = col.match(/color\([^)]*\)/);
    if (c) { const sl = c[0].match(/\/\s*([0-9.]+)\s*\)/); return sl?parseFloat(sl[1]):1; }
    return col==="transparent"?0:1;
  }
  let styledSurfaces=0, bordered=0, filled=0;
  for (const el of all) {
    const r = el.getBoundingClientRect();
    if (r.width<2||r.height<2) continue;
    const cs = getComputedStyle(el);
    const bgVisible = alphaOf(cs.backgroundColor) > 0.01;
    const bw = ["Top","Right","Bottom","Left"].map((s)=>parseFloat(cs["border"+s+"Width"])||0);
    const borderVisible = Math.max(...bw) >= 1 && cs.borderTopStyle!=="none" && alphaOf(cs.borderTopColor)>0.01;
    if (bgVisible) filled++;
    if (borderVisible) bordered++;
    if (bgVisible||borderVisible) styledSurfaces++;
  }
  const cls=(s)=>document.body.querySelectorAll(s).length;
  return { innerTextLen:text.length, leafCount:leaves.length, styledSurfaces, filled, bordered,
           settingsRow:cls(".settings-row"), settingsGroup:cls(".settings-group"), buttons:cls("button") };
}
"""


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "before"
    strip = len(sys.argv) > 2 and sys.argv[2] == "strip"
    token = fetch_token()
    out = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        for name, route in ROUTES:
            ctx = browser.new_context(viewport={"width":1440,"height":950},
                                      extra_http_headers={"X-Hermes-Session-Token":token})
            page = ctx.new_page()
            page.goto(BASE + route, wait_until="networkidle", timeout=30000)
            if strip:
                page.evaluate("() => document.querySelectorAll('.qb-upstream').forEach(e=>e.classList.remove('qb-upstream'))")
            page.wait_for_timeout(1200)
            out[name] = page.evaluate(MEASURE_JS)
            ctx.close()
        browser.close()
    print(json.dumps({"label":label,"pages":out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
