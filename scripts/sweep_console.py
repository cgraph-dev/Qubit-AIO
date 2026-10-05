#!/usr/bin/env python3
"""Independent console sweep — one row per route, measured from the live DOM.

WHY THIS EXISTS
Agent self-reports in this repo have a proven record of over-claiming ("1:1",
"verified") while the live DOM says otherwise, and hand-rolled probes have
twice produced phantom defects because they counted a control with no
background/border as broken when nav rows and wrapper-styled fields are correct
by design. This script is the tie-breaker: it measures, prints the raw numbers,
and never asserts.

Usage:
    .venv/bin/python scripts/sweep_console.py                 # default routes
    .venv/bin/python scripts/sweep_console.py /logs /sessions # specific routes

Reading the output:
  text    visible leaf-text characters inside <main>. LOW IS NOT PROOF OF
          BREAKAGE — check the data first (`/profiles` is legitimately 155
          chars when the machine has one profile).
  ctl     interactive controls inside <main>.
  unsurf  controls with no computed background AND no border, AFTER crediting
          a wrapper ancestor that carries the surface (up to 3 levels up).
          Rows rendered as bare <button>s are deliberate in `/logs` and
          `/files`; treat a high count there as expected, not a defect.
  empty   controls with no accessible name (innerText/aria-label/placeholder).
          THIS is always a real defect.
  vert    leaf text elements narrower than 20px and taller than 30px — the
          one-character-per-line signature.
  errs    uncaught page errors.

The entry chunk hash is printed first and again at the end. Concurrent agents
rebuild `frontend/qubit-dist` while this runs, so a 'verified' pass against a
bundle that has since been replaced is worthless: if the hash moves, re-run.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import sys
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8890"
DIST = Path(__file__).resolve().parent.parent / "frontend" / "qubit-dist" / "assets"

DEFAULT_ROUTES = [
    "/qubit-bot", "/qubit-harness-router",
    "/open-viking", "/open-viking/retrieval", "/open-viking/uploads",
    "/open-viking/resources", "/open-viking/agent-hub",
    "/agent-cards", "/canvas", "/nodes", "/worktrees", "/checkpoints",
    "/session-ops", "/qubit-taskflows", "/qubit-memory", "/admin/skills-review",
    "/analytics", "/computer", "/logs", "/sessions", "/channels", "/cron",
    "/models", "/files", "/profiles", "/pairing",
]

# Measures inside <main> only: the sidebar is shared chrome and would otherwise
# dominate both the control count and any unsurfaced-control noise.
PROBE = """() => {
  const main = document.querySelector('main') || document.body;
  const vis = (e) => {
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const leaf = [...main.querySelectorAll('*')]
    .filter(e => e.children.length === 0 && (e.textContent || '').trim() && vis(e));
  const vert = leaf.filter(e => {
    const r = e.getBoundingClientRect();
    return r.width < 20 && r.height > 30;
  }).length;

  const ctl = [...main.querySelectorAll('button,input,select,textarea')].filter(vis);
  let unsurf = 0;
  for (const e of ctl) {
    const s = getComputedStyle(e);
    const bare = (s.backgroundColor === 'rgba(0, 0, 0, 0)' || s.backgroundColor === 'transparent')
              && (s.borderTopWidth === '0px' || s.borderTopWidth === '');
    if (!bare) continue;
    // Correct-by-design controls put the surface on a wrapper (fields inside a
    // bordered .field, nav rows inside a tinted container). Credit it.
    let w = e.parentElement, wrap = false;
    for (let i = 0; i < 3 && w; i++) {
      const ws = getComputedStyle(w);
      if (ws.backgroundColor !== 'rgba(0, 0, 0, 0)' && ws.backgroundColor !== 'transparent') { wrap = true; break; }
      if (ws.borderTopWidth !== '0px' && ws.borderTopWidth !== '') { wrap = true; break; }
      w = w.parentElement;
    }
    if (!wrap) unsurf++;
  }
  const empty = ctl.filter(e => !((e.innerText
    || e.getAttribute('aria-label') || e.getAttribute('placeholder') || '').trim())).length;

  return {
    text: (main.innerText || '').length,
    ctl: ctl.length,
    unsurf,
    empty,
    vert,
  };
}"""


def entry_chunk() -> tuple[str, str]:
    """(chunk name, md5) of the entry bundle the server is currently serving."""
    html = urllib.request.urlopen(BASE + "/").read().decode()
    m = re.search(r"assets/(index-[A-Za-z0-9_\-]+\.js)", html)
    if not m:
        return "?", "?"
    name = m.group(1)
    path = DIST / name
    if not path.exists():
        return name, "not-on-disk"
    return name, hashlib.md5(path.read_bytes()).hexdigest()


def token() -> str:
    html = urllib.request.urlopen(BASE + "/").read().decode()
    m = re.search(r'__QUBIT_SESSION_TOKEN__\s*=\s*"([^"]+)"', html)
    if not m:
        raise SystemExit("could not read __QUBIT_SESSION_TOKEN__ from /")
    return m.group(1)


async def main(routes: list[str]) -> int:
    name, md5 = entry_chunk()
    print(f"entry chunk: {name}  md5: {md5}\n")
    tok = token()
    header = f"{'route':30s} {'text':>6s} {'ctl':>4s} {'unsurf':>6s} {'empty':>5s} {'vert':>4s} {'errs':>4s}"
    print(header)
    print("-" * len(header))

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1440, "height": 950})
        await ctx.add_init_script(f'window.__QUBIT_SESSION_TOKEN__ = "{tok}";')
        page = await ctx.new_page()

        for route in routes:
            errors: list[str] = []
            handler = lambda e: errors.append(str(e))  # noqa: E731
            page.on("pageerror", handler)
            try:
                # `commit` then settle: `networkidle` can hang on a page that
                # polls (logs live-tail), and this is a smoke sweep, not a
                # load-time benchmark.
                await page.goto(BASE + route, wait_until="commit", timeout=45000)
                await page.wait_for_timeout(2500)
                d = await page.evaluate(PROBE)
                print(f"{route:30s} {d['text']:6d} {d['ctl']:4d} {d['unsurf']:6d} "
                      f"{d['empty']:5d} {d['vert']:4d} {len(errors):4d}")
            except Exception as exc:  # noqa: BLE001 — one bad route must not end the sweep
                print(f"{route:30s}   ERR {type(exc).__name__}: {str(exc)[:60]}")
            finally:
                page.remove_listener("pageerror", handler)

        await browser.close()

    name2, md5_2 = entry_chunk()
    print()
    if (name2, md5_2) != (name, md5):
        print(f"!! BUNDLE CHANGED MID-SWEEP: {name} {md5} -> {name2} {md5_2}")
        print("!! Re-run before publishing any number above.")
    else:
        print(f"bundle stable across the sweep: {name} {md5}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:] or DEFAULT_ROUTES)))
