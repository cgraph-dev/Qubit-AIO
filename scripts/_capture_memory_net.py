#!/usr/bin/env python3
"""Capture the exact JSON the /qubit-memory page receives from /api/memory."""
import asyncio, re, urllib.request, json
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8890"

def token():
    html = urllib.request.urlopen(BASE + "/").read().decode()
    return re.search(r'__QUBIT_SESSION_TOKEN__\s*=\s*"([^"]+)"', html).group(1)

async def main():
    tok = token()
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context()
        await ctx.add_init_script(f'window.__QUBIT_SESSION_TOKEN__ = "{tok}";')
        page = await ctx.new_page()
        seen = {}
        async def on_resp(resp):
            u = resp.url
            if "/api/memory" in u:
                try:
                    seen[u] = await resp.json()
                except Exception as e:
                    seen[u] = f"<non-json {e}>"
        page.on("response", on_resp)
        await page.goto(BASE + "/qubit-memory", wait_until="commit", timeout=45000)
        await page.wait_for_timeout(3000)
        print(json.dumps(seen, indent=2)[:3000])
        await b.close()

asyncio.run(main())
