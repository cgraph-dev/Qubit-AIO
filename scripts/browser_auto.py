#!/usr/bin/env python3
"""Single-browser agent reader via CDP — reads/scrapes REAL logged-in tabs.

Snap Chromium is launched ONCE on its real logged-in profile with a CDP port,
then Playwright `connect_over_cdp` attaches (real cookies + fingerprint => no
"bot secure"). Kept as ONE engine (Chromium) because all the accounts — Gmail,
Messenger, Facebook, TikTok, Vercel, Reddit, X — are logged in there.

Commands:
  list_tabs            -> JSON of open tabs (title, url, id)
  read  <tab_id|url>   -> extract readable text from a tab
  write <tab_id> <js>  -> run JS in a tab (scrape/click helpers)
  open  <url>          -> open a new tab
  perf                 -> last N calls + avg ms (kept for ongoing monitoring)

Output is JSON on stdout for the agent to feed generate_report.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import os
from pathlib import Path
from urllib.request import urlopen

PROF_HOME = Path(__file__).resolve().parent / "browser_auto_state"
LOG_DIR = PROF_HOME / "perf"
LOG_DIR.mkdir(parents=True, exist_ok=True)
PERF_LOG = LOG_DIR / "perf.jsonl"

# Snap Chromium is the single agent browser (all accounts logged in here).
BINARY = "/snap/chromium/current/usr/lib/chromium-browser/chrome"
PROFILE = str(Path.home() / "snap/chromium/common/chromium")
PORT = 9222


def _g(engine: str, key: str):
    return ENGINES[engine].get(key)


def _launch() -> None:
    """Launch snap Chromium on its real profile with a CDP port if not already up."""
    if _cdp_alive(PORT):
        return
    if not os.path.exists(BINARY) or not os.path.exists(PROFILE):
        raise RuntimeError(f"snap chromium missing: {BINARY} or {PROFILE}")
    cmd = [BINARY,
           f"--remote-debugging-port={PORT}",
           "--remote-debugging-address=127.0.0.1",
           f"--user-data-dir={PROFILE}",
           "--no-first-run",
           "--disable-session-crashed-bubble",
           "--restore-last-session"]
    devnull = open(os.devnull, "w")
    subprocess.Popen(cmd, stdout=devnull, stderr=devnull, start_new_session=True)
    for _ in range(16):
        if _cdp_alive(PORT):
            return
        time.sleep(0.5)
    raise RuntimeError("snap chromium did not open CDP in time")


def _cdp_alive(port: int = PORT) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5) as r:
            json.loads(r.read().decode())
            return True
    except Exception:
        return False


def _connect():
    """Yield a Playwright CDP connection to snap chromium."""
    _launch()
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        context = browser.contexts[0] if browser.contexts else None
        yield context, browser
    finally:
        try:
            pw.stop()
        except Exception:
            pass


def _log(op: str, ms: float, ok: bool, extra: str = "") -> None:
    with open(PERF_LOG, "a") as f:
        f.write(json.dumps({"ts": time.time(), "engine": "chromium", "op": op,
                            "ms": round(ms, 1), "ok": bool(ok), "extra": extra}) + "\n")


def list_tabs() -> dict:
    t0 = time.time()
    try:
        conn = _connect()
        context, browser = next(conn)
        tabs = [{"id": str(pg), "title": pg.title() or "", "url": pg.url}
                for pg in context.pages]
        _log("list_tabs", (time.time() - t0) * 1000, True)
        return {"engine": "chromium", "tabs": tabs}
    except Exception as e:
        _log("list_tabs", (time.time() - t0) * 1000, False, str(e)[:120])
        return {"engine": "chromium", "error": str(e)[:200], "tabs": []}


def _find_page(context, target: str):
    for pg in context.pages:
        if target and (target == str(pg) or target in pg.url):
            return pg
    return context.pages[0] if context.pages else None


def read_tab(target: str = "") -> dict:
    t0 = time.time()
    try:
        conn = _connect()
        context, browser = next(conn)
        pg = _find_page(context, target)
        if pg is None:
            raise ValueError("no page found")
        # JS-heavy pages (TikTok, web apps) render after the first paint; give
        # them several seconds and retry before falling back to DOM extraction.
        text = (pg.inner_text("body") or "").strip()
        for _ in range(8):
            if len(text) >= 200:
                break
            try:
                pg.wait_for_timeout(1200)
            except Exception:
                break
            text = (pg.inner_text("body") or "").strip()
        if len(text) < 60:
            # fallback: concat visible article/tab text + title
            try:
                text = pg.evaluate(
                    "() => { const t=document.title||''; "
                    "const b=(document.body&&document.body.innerText)||''; "
                    "return (t + '\\n' + b).trim().slice(0,20000); }") or ""
            except Exception:
                pass
        _log("read", (time.time() - t0) * 1000, True)
        return {"engine": "chromium", "url": pg.url, "text": text[:20000]}
    except Exception as e:
        _log("read", (time.time() - t0) * 1000, False, str(e)[:120])
        return {"engine": "chromium", "error": str(e)[:200]}


def run_js(target: str, js: str) -> dict:
    t0 = time.time()
    try:
        conn = _connect()
        context, browser = next(conn)
        pg = _find_page(context, target)
        if pg is None:
            raise ValueError("no page found")
        val = pg.evaluate(js)
        _log("run_js", (time.time() - t0) * 1000, True)
        return {"engine": "chromium", "url": pg.url, "value": val}
    except Exception as e:
        _log("run_js", (time.time() - t0) * 1000, False, str(e)[:120])
        return {"engine": "chromium", "error": str(e)[:200]}


def open_url(url: str) -> dict:
    t0 = time.time()
    try:
        conn = _connect()
        context, browser = next(conn)
        pg = context.new_page()
        pg.goto(url, timeout=45000, wait_until="domcontentloaded")
        _log("open", (time.time() - t0) * 1000, True)
        return {"engine": "chromium", "url": pg.url, "title": pg.title()}
    except Exception as e:
        _log("open", (time.time() - t0) * 1000, False, str(e)[:120])
        return {"engine": "chromium", "error": str(e)[:200]}


def perf() -> dict:
    """Read the perf log; report avg/max ms and call count (ongoing monitoring)."""
    rows = []
    if PERF_LOG.exists():
        for l in PERF_LOG.read_text().splitlines():
            try:
                rows.append(json.loads(l))
            except Exception:
                pass
    by_op = {}
    total_ms = []
    for r in rows:
        op = r.get("op", "?")
        by_op.setdefault(op, {"n": 0, "ms": []})
        by_op[op]["n"] += 1
        by_op[op]["ms"].append(r.get("ms", 0))
        total_ms.append(r.get("ms", 0))
    for op, s in by_op.items():
        s["avg_ms"] = round(sum(s["ms"]) / len(s["ms"]), 1) if s["ms"] else 0
        s["max_ms"] = round(max(s["ms"]), 1) if s["ms"] else 0
        s.pop("ms", None)
    avg = round(sum(total_ms) / len(total_ms), 1) if total_ms else 0
    return {"engine": "chromium", "calls": len(rows), "avg_ms": avg,
            "by_op": by_op, "perf_log": str(PERF_LOG)}


if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0] if args else "list_tabs"
    if cmd == "list_tabs":
        print(json.dumps(list_tabs(), ensure_ascii=False))
    elif cmd == "read":
        print(json.dumps(read_tab(args[1] if len(args) > 1 else ""), ensure_ascii=False))
    elif cmd == "write":
        print(json.dumps(run_js(args[1] if len(args) > 1 else "",
                                args[2] if len(args) > 2 else ""), ensure_ascii=False))
    elif cmd == "open":
        print(json.dumps(open_url(args[1] if len(args) > 1 else "about:blank"), ensure_ascii=False))
    elif cmd == "perf":
        print(json.dumps(perf(), ensure_ascii=False))
    else:
        print(json.dumps({"error": f"unknown command {cmd}"}))
