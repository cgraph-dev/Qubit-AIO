"""Measure /qubit-task-inbox and /qubit-tasks from the LIVE page.

Reproduces before/after: visible text length, control count, controls with no
accessible name, and the count of ROWS containing real task titles. Inject the
session token read from GET / as an init script.

Usage: .venv/bin/python /tmp/taskpages_measure.py before|after
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

from playwright.sync_api import sync_playwright

LIVE = "http://127.0.0.1:8890"
PAGES = ["/qubit-task-inbox", "/qubit-tasks"]
TITLE_PROBES = ["Weekly review", "live-probe", "AI10-board-probe", "cli-approval-probe"]


def token() -> str:
    html = urllib.request.urlopen(LIVE + "/", timeout=10).read().decode("utf-8", "replace")
    m = re.search(r"__QUBIT_SESSION_TOKEN__\s*=\s*[\"']([^\"']+)", html)
    if not m:
        raise SystemExit("token not found in GET /")
    return m.group(1)


def measure(page, path: str) -> dict:
    page.goto(LIVE + path, wait_until="commit")
    page.wait_for_timeout(3500)  # networkidle never fires on streaming pages
    return page.evaluate(
        """() => {
          const body = document.body;
          const text = (body.innerText || "").trim();
          const controls = [...body.querySelectorAll(
            'button, a[href], input, select, textarea, [role="button"]')];
          const noName = controls.filter(el => {
            const name = (el.getAttribute('aria-label') || el.getAttribute('title')
              || (el.innerText || '').trim() || el.getAttribute('placeholder')
              || el.getAttribute('alt') || '').trim();
            return !name;
          });
          const probes = ['Weekly review','live-probe','AI10-board-probe','cli-approval-probe'];
          const all = [...body.querySelectorAll('*')];
          const rows = all.filter(el => {
            const t = (el.innerText || '');
            if (!probes.some(p => t.includes(p))) return false;
            // leaf-ish container: has a probe title but is the smallest wrapper
            return !probes.some(p => [...el.children].some(c => (c.innerText||'').includes(p))
                                 && (el.tagName !== 'LI' && el.tagName !== 'TR'));
          });
          const byText = {};
          for (const p of probes) {
            byText[p] = text.includes(p);
          }
          return {
            textLen: text.length,
            controlCount: controls.length,
            controlsNoName: noName.length,
            controlsNoNameSample: noName.slice(0,8).map(e => e.outerHTML.slice(0,120)),
            rowCandidates: rows.length,
            titlesVisible: byText,
            refusalTestIds: [...body.querySelectorAll('[data-testid^="refusal-"]')]
              .map(e => e.getAttribute('data-testid')),
            emptyStates: [...body.querySelectorAll(
              '.workboard-empty-state, .settings-empty, [data-state="empty"]')]
              .map(e => (e.innerText||'').trim().slice(0,160)),
            dataTestIds: [...body.querySelectorAll('[data-testid]')]
              .map(e => e.getAttribute('data-testid')),
          };
        }"""
    )


def main() -> None:
    tag = sys.argv[1] if len(sys.argv) > 1 else "before"
    tok = token()
    out = {"tag": tag, "token_len": len(tok), "pages": {}}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        ctx.add_init_script(f'window.__QUBIT_SESSION_TOKEN__="{tok}";')
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for path in PAGES:
            out["pages"][path] = measure(page, path)
        out["page_errors"] = errors
        browser.close()
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
