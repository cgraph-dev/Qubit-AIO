#!/usr/bin/env python3
"""Measure /qubit-memory and /canvas from the LIVE DOM (before/after tie-breaker).

Measures, per route:
  text   visible leaf-text chars inside <main>
  ctl    visible interactive controls inside <main>
  empty  controls with no accessible name (innerText/aria-label/placeholder)
  errs   uncaught page errors
And the RENDERED value of the memory status fields that must come from real
backend keys (never a fabricated fallback):
  Store state, Enabled, Active provider  -> [data-testid="memory-store-state" |
        "memory-enabled" | "memory-active-provider"]

Canvas e2e (create -> list -> read) is driven through the REAL page: fill the
add form, click Add (confirm-gated), click Confirm, wait for the list to show
the new file, then read it back via GET /api/qubit/canvas/get.

AUTH: read __QUBIT_SESSION_TOKEN__ out of GET / with a regex and inject it as
an init script (the entry bundle reads ``window.__QUBIT_SESSION_TOKEN__``).

Usage:  .venv/bin/python scripts/measure_memory_canvas.py [--e2e]
"""
from __future__ import annotations

import argparse
import asyncio
import re
import urllib.request

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8890"

PROBE = """() => {
  const main = document.querySelector('main') || document.body;
  const vis = (e) => {
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const leaf = [...main.querySelectorAll('*')]
    .filter(e => e.children.length === 0 && (e.textContent || '').trim() && vis(e));
  const ctl = [...main.querySelectorAll('button,input,select,textarea')].filter(vis);
  const empty = ctl.filter(e => !((e.innerText
    || e.getAttribute('aria-label') || e.getAttribute('placeholder') || '').trim())).length;
  const tid = (id) => {
    const el = main.querySelector(`[data-testid="${id}"]`);
    return el ? (el.textContent || '').trim() : null;
  };
  return {
    text: (main.innerText || '').length,
    innerText: (main.innerText || ''),
    ctl: ctl.length,
    empty,
    active: tid('memory-active-provider'),
    storeState: tid('memory-store-state'),
    enabled: tid('memory-enabled'),
    probe: tid('memory-read-probe'),
  };
}"""


def _roster_text(d: dict) -> str:
    return d.get("innerText") or ""


def token() -> str:
    html = urllib.request.urlopen(BASE + "/").read().decode()
    m = re.search(r'__QUBIT_SESSION_TOKEN__\s*=\s*"([^"]+)"', html)
    if not m:
        raise SystemExit("could not read __QUBIT_SESSION_TOKEN__ from /")
    return m.group(1)


async def measure(route: str, page) -> dict:
    errors: list[str] = []
    handler = lambda e: errors.append(str(e))  # noqa: E731
    page.on("pageerror", handler)
    try:
        await page.goto(BASE + route, wait_until="commit", timeout=45000)
        await page.wait_for_timeout(2500)
        d = await page.evaluate(PROBE)
    finally:
        page.remove_listener("pageerror", handler)
    d["errs"] = len(errors)
    d["route"] = route
    return d


async def canvas_e2e(page, tok: str, name: str, content: str) -> dict:
    """Drive create -> list through the live page; read back via the API."""
    await page.goto(BASE + "/canvas", wait_until="commit", timeout=45000)
    await page.wait_for_timeout(2500)

    await page.fill('[aria-label="New canvas file name"]', name)
    await page.fill('[aria-label="New canvas file content"]', content)
    await page.click('[aria-label="Add canvas file"]')
    # Confirm-gated: the page shows a Confirm button in its dialog.
    await page.wait_for_selector('button:has-text("Confirm")', timeout=15000)
    await page.click('button:has-text("Confirm")')
    # The list refreshes after the settle; wait for our file to appear.
    await page.wait_for_selector(f'text={name}', timeout=15000)
    await page.wait_for_timeout(800)

    listed = await page.evaluate(PROBE)
    listed["route"] = "/canvas (after add)"
    listed["errs"] = 0

    req = urllib.request.Request(
        BASE + f"/agent/api/qubit/canvas/get?path={name}",
        headers={"X-Qubit-Session-Token": tok, "Accept": "application/json"},
    )
    import json as _json
    read_back = _json.loads(urllib.request.urlopen(req).read().decode())
    return {"listed": listed, "read_back": read_back}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--e2e", action="store_true", help="also run the canvas create->read proof")
    args = ap.parse_args()

    tok = token()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1440, "height": 950})
        await ctx.add_init_script(f'window.__QUBIT_SESSION_TOKEN__ = "{tok}";')
        page = await ctx.new_page()

        for route in ("/qubit-memory", "/canvas"):
            d = await measure(route, page)
            print(f"{d['route']:22s} text={d['text']:5d} ctl={d['ctl']:3d} "
                  f"empty={d['empty']:2d} errs={d['errs']}")
            if route == "/qubit-memory":
                print(f"    Active provider : {d['active']!r}")
                print(f"    Store state     : {d['storeState']!r}")
                print(f"    Enabled         : {d['enabled']!r}")
                print(f"    Read probe      : {d['probe']!r}")

        if args.e2e:
            name = "e2e-proof.html"
            content = "<!doctype html><h1>e2e canvas proof</h1>"
            print()
            try:
                res = await canvas_e2e(page, tok, name, content)
                print(f"canvas e2e: created {name!r} through the page form")
                print(f"  file visible in rendered list : "
                      f"{name in _roster_text(res['listed'])}")
                print(f"  GET /api/qubit/canvas/get?path={name} -> "
                      f"state={res['read_back'].get('state')} "
                      f"content={res['read_back'].get('content')!r}")
            except Exception as exc:  # noqa: BLE001
                print(f"canvas e2e FAILED: {type(exc).__name__}: {exc}")
                raise

        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
