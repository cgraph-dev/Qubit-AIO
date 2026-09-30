#!/usr/bin/env python3
"""RankValut-style authenticated-surface research driver (HTTP control).

Launches a persistent headed Chromium so the user can log in manually on each
marketplace, then exposes a localhost HTTP control server. Commands are POSTed
as JSON to http://127.0.0.1:8791/ and responses come back as JSON.

Commands:
  {"action":"goto","url":"...","tab":N}       navigate tab N (default 0)
  {"action":"newtab","url":"..."}              open new tab and navigate
  {"action":"tabs"}                            list tab urls
  {"action":"activatetab","index":N}           switch active tab
  {"action":"dump","name":"..."}               save DOM structure JSON, return path
  {"action":"shot","name":"..."}               viewport screenshot -> shots/, return path
  {"action":"shotfull","name":"..."}           full-page screenshot
  {"action":"click","selector":"...","name":"..."}  real mouse click, wait, optional dump
  {"action":"eval","expr":"..."}               evaluate JS, return result
  {"action":"cookies","path":"..."}            backup all cookies to JSON
  {"action":"injectcookies","path":"..."}      restore cookies from JSON
  {"action":"wait","ms":N}                     sleep
  {"action":"quit"}                            close browser and exit
"""
import json
import os
import threading
import queue
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

RESEARCH_DIR = os.path.expanduser("~/.hermes/rankvalut-research")
PROFILE_DIR = os.path.expanduser("~/.hermes/browser_profiles/rankvalut-research")
DUMP_DIR = os.path.join(RESEARCH_DIR, "dumps")
SHOT_DIR = os.path.join(RESEARCH_DIR, "shots")
PORT = int(os.environ.get("RV_RESEARCH_PORT", "8791"))
os.makedirs(DUMP_DIR, exist_ok=True)
os.makedirs(SHOT_DIR, exist_ok=True)

SITES = [
    ("z2u", "https://www.z2u.com/"),
    ("zeusx", "https://www.zeusx.com/"),
    ("eldorado", "https://www.eldorado.gg/"),
    ("g2g", "https://www.g2g.com/"),
    ("g2a", "https://www.g2a.com/"),
]

STRUCTURE_JS = """() => {
  const txt = el => { if (!el) return ''; const t = (el.textContent || el.innerText || '').replace(/\\s+/g,' ').trim().slice(0,150); return t; };
  const pick = el => ({
    tag: el.tagName,
    text: txt(el),
    href: el.href || el.getAttribute('href') || null,
    name: el.name || el.id || null,
    type: el.type || null,
    value: el.value ? String(el.value).slice(0,80) : null,
    placeholder: el.placeholder || null,
    checked: el.checked || null
  });
  const all = sel => { try { return Array.from(document.querySelectorAll(sel)); } catch(e) { return []; } };
  const visible = arr => arr.filter(e => {
    const r = e.getBoundingClientRect();
    const st = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
  });
  return {
    url: location.href,
    title: document.title,
    h1: visible(all('h1')).map(txt),
    h2: visible(all('h2')).map(txt),
    h3: visible(all('h3')).map(txt),
    nav_links: visible(all('nav a, header a, aside a, [role=navigation] a, .sidebar a, .side-menu a, .menu a, .menu-item a')).map(pick).slice(0,300),
    tabs: visible(all('[role=tab], .tab, .nav-tabs a, .tabs a, .tab-item a')).map(pick).slice(0,120),
    buttons: visible(all('button, [role=button], input[type=submit]')).map(pick).slice(0,200),
    inputs: visible(all('input, select, textarea')).map(pick).slice(0,150),
    checkboxes: visible(all('input[type=checkbox]')).map(pick).slice(0,100),
    radios: visible(all('input[type=radio]')).map(pick).slice(0,100),
    links: visible(all('a')).map(pick).slice(0,400),
    table_headers: visible(all('table th')).map(txt).slice(0,120),
    badges: visible(all('.badge, [class*=badge], [class*=status], [class*=tag]')).map(txt).slice(0,120),
    cards: visible(all('[class*=card], [class*=Card], [class*=offer], [class*=Offer], [class*=listing], [class*=order]')).map(txt).slice(0,120),
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
        if page.url in ("about:blank", ""):
            page.goto(SITES[0][1], wait_until="domcontentloaded", timeout=60000)

        result_holder["event"] = threading.Event()
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"READY profile={PROFILE_DIR} port={PORT} tabs={len(ctx.pages)}", flush=True)

        while True:
            cmd = cmd_queue.get()
            action = cmd.get("action")
            out = {"action": action}
            try:
                if action == "quit":
                    server.shutdown()
                    ctx.close()
                    out["status"] = "bye"
                    result_holder["result"] = out
                    result_holder["event"].set()
                    break
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
                    name = cmd.get("name", "page")
                    path, data = dump_structure(page, name)
                    out.update(status="ok", path=path, url=data["url"], title=data["title"])
                elif action == "shot":
                    name = cmd.get("name", "shot")
                    path = os.path.join(SHOT_DIR, f"{name}.png")
                    page.screenshot(path=path)
                    out.update(status="ok", path=path)
                elif action == "shotfull":
                    name = cmd.get("name", "shot")
                    path = os.path.join(SHOT_DIR, f"{name}-full.png")
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
                    cookies = ctx.cookies()
                    with open(path, "w") as f:
                        json.dump(cookies, f, indent=2)
                    out.update(status="ok", count=len(cookies), path=path)
                elif action == "injectcookies":
                    path = cmd.get("path") or os.path.join(RESEARCH_DIR, "cookies.json")
                    with open(path) as f:
                        cookies = json.load(f)
                    ctx.add_cookies(cookies)
                    out.update(status="ok", count=len(cookies))
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
