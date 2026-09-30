#!/usr/bin/env python3
"""Persistent headed Chromium research launcher with an HTTP control server.

Purpose: let the USER log in manually on competitor sites (credentials/2FA
never touch the agent), then let the agent drive the browser programmatically
to dump page structure, take screenshots, click through SPA flows, and back up
cookies.

Run (background, keep alive):
    export DISPLAY=:0 XAUTHORITY=$(ls /run/user/$(id -u)/gdm/Xauthority 2>/dev/null || echo ~/.Xauthority)
    uv run --with playwright python launcher.py

Control (agent side), POST JSON to http://127.0.0.1:8791/:
    {"action":"tabs"}                          list tab urls
    {"action":"newtab","url":"https://..."}    open tab + navigate (active tab)
    {"action":"goto","url":"...","tab":N}      navigate tab N
    {"action":"activatetab","index":N}         switch active tab
    {"action":"dump","name":"page-x"}          save DOM structure JSON -> dumps/
    {"action":"shot","name":"page-x"}          viewport screenshot -> shots/
    {"action":"shotfull","name":"page-x"}      full-page screenshot
    {"action":"click","selector":"...","name":"opt"}  real click; optional dump
    {"action":"eval","expr":"..."}             evaluate JS, return result
    {"action":"cookies","path":"cookies.json"} backup ALL cookies (do this
                                               BEFORE any browser restart!)
    {"action":"injectcookies","path":"..."}    restore cookies
    {"action":"quit"}                          close and exit

Responses are JSON with {"action":..., "status":"ok"|"error", ...}.

Pitfalls baked in:
- Never restart without `cookies` first: session cookies die on restart.
- Dumps/screenshots go to FILES (dumps/, shots/), not stdout.
- For SPA sites (e.g. G2G): boot at the sidebar URL, then CLICK sidebar links
  via {"action":"click"} instead of goto (direct deep-route loads 404).
- Real user-login happens in the visible window; the agent then drives it.
"""
import json
import os
import queue
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

RESEARCH_DIR = os.path.expanduser("~/.hermes/competitor-research")
PROFILE_DIR = os.path.expanduser("~/.hermes/browser_profiles/competitor-research")
DUMP_DIR = os.path.join(RESEARCH_DIR, "dumps")
SHOT_DIR = os.path.join(RESEARCH_DIR, "shots")
PORT = int(os.environ.get("RESEARCH_PORT", "8791"))
os.makedirs(DUMP_DIR, exist_ok=True)
os.makedirs(SHOT_DIR, exist_ok=True)

STRUCTURE_JS = """() => {
  const txt = el => { if (!el) return ''; const t = (el.textContent || el.innerText || '').replace(/\\s+/g,' ').trim().slice(0,150); return t; };
  const pick = el => ({
    tag: el.tagName, text: txt(el), href: el.href || el.getAttribute('href') || null,
    name: el.name || el.id || null, type: el.type || null,
    value: el.value ? String(el.value).slice(0,80) : null,
    placeholder: el.placeholder || null, checked: el.checked || null
  });
  const all = sel => { try { return Array.from(document.querySelectorAll(sel)); } catch(e) { return []; } };
  const visible = arr => arr.filter(e => {
    const r = e.getBoundingClientRect();
    const st = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
  });
  return {
    url: location.href, title: document.title,
    h1: visible(all('h1')).map(txt), h2: visible(all('h2')).map(txt), h3: visible(all('h3')).map(txt),
    nav_links: visible(all('nav a, header a, aside a, [role=navigation] a, .sidebar a, .side-menu a, .menu a')).map(pick).slice(0,300),
    tabs: visible(all('[role=tab], .tab, .nav-tabs a, .tabs a')).map(pick).slice(0,120),
    buttons: visible(all('button, [role=button], input[type=submit]')).map(pick).slice(0,200),
    inputs: visible(all('input, select, textarea')).map(pick).slice(0,150),
    checkboxes: visible(all('input[type=checkbox]')).map(pick).slice(0,100),
    radios: visible(all('input[type=radio]')).map(pick).slice(0,100),
    links: visible(all('a')).map(pick).slice(0,400),
    table_headers: visible(all('table th')).map(txt).slice(0,120),
    badges: visible(all('.badge, [class*=badge], [class*=status], [class*=tag]')).map(txt).slice(0,120),
    cards: visible(all('[class*=card], [class*=Card], [class*=offer], [class*=Offer]')).map(txt).slice(0,120),
  };
}"""

cmd_queue = queue.Queue()
result_holder = {}


def dump_structure(page, name):
    data = page.evaluate(STRUCTURE_JS)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(DUMP_DIR, f"{name}-{ts}.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path, data


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            cmd = json.loads(body.decode("utf-8"))
            result_holder["result"] = None
            result_holder["event"].clear()
            cmd_queue.put(cmd)
            result_holder["event"].wait(timeout=120)
            result = result_holder.get("result")
            payload = json.dumps(result, default=str, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as e:
            payload = json.dumps({"error": repr(e)}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass


def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            args=["--no-sandbox", "--start-maximized"],
            viewport={"width": 1500, "height": 950},
            locale="en-US",
            ignore_default_args=["--enable-automation"],
        )
        if ctx.pages:
            page = ctx.pages[0]
        else:
            page = ctx.new_page()

        result_holder["event"] = threading.Event()
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"READY profile={PROFILE_DIR} port={PORT}", flush=True)

        while True:
            cmd = cmd_queue.get()
            action = cmd.get("action")
            out = {"action": action}
            try:
                if action == "quit":
                    server.shutdown()
                    ctx.close()
                    out["status"] = "bye"
                elif action == "goto":
                    idx = cmd.get("tab", 0)
                    if idx < len(ctx.pages):
                        page = ctx.pages[idx]
                    page.goto(cmd["url"], wait_until="domcontentloaded", timeout=60000)
                    time.sleep(1.5)
                    out.update(status="ok", url=page.url)
                elif action == "newtab":
                    tab = ctx.new_page()
                    if cmd.get("url"):
                        tab.goto(cmd["url"], wait_until="domcontentloaded", timeout=60000)
                        time.sleep(1.5)
                    page = tab
                    out.update(status="ok", url=page.url)
                elif action == "tabs":
                    out.update(status="ok", tabs=[pg.url for pg in ctx.pages])
                elif action == "activatetab":
                    page = ctx.pages[cmd["index"]]
                    page.bring_to_front()
                    out.update(status="ok", url=page.url)
                elif action == "dump":
                    path, data = dump_structure(page, cmd.get("name", "page"))
                    out.update(status="ok", path=path, url=data["url"], title=data["title"])
                elif action == "shot":
                    path = os.path.join(SHOT_DIR, f"{cmd.get('name','shot')}.png")
                    page.screenshot(path=path)
                    out.update(status="ok", path=path)
                elif action == "shotfull":
                    path = os.path.join(SHOT_DIR, f"{cmd.get('name','shot')}-full.png")
                    page.screenshot(path=path, full_page=True)
                    out.update(status="ok", path=path)
                elif action == "click":
                    page.click(cmd["selector"], timeout=15000)
                    time.sleep(1.5)
                    out.update(status="ok", url=page.url)
                    if cmd.get("name"):
                        path, data = dump_structure(page, cmd["name"])
                        out.update(dump_path=path)
                elif action == "eval":
                    out.update(status="ok", result=page.evaluate(cmd["expr"]))
                elif action == "cookies":
                    path = cmd.get("path") or os.path.join(RESEARCH_DIR, "cookies.json")
                    with open(path, "w") as f:
                        json.dump(ctx.cookies(), f, indent=2)
                    out.update(status="ok", count=len(ctx.cookies()), path=path)
                elif action == "injectcookies":
                    path = cmd.get("path") or os.path.join(RESEARCH_DIR, "cookies.json")
                    with open(path) as f:
                        ctx.add_cookies(json.load(f))
                    out.update(status="ok")
                elif action == "wait":
                    time.sleep(cmd.get("ms", 1000) / 1000.0)
                    out.update(status="ok")
                else:
                    out.update(status="error", error=f"unknown action {action}")
            except Exception as e:
                out.update(status="error", error=repr(e))
            result_holder["result"] = out
            result_holder["event"].set()


if __name__ == "__main__":
    main()
