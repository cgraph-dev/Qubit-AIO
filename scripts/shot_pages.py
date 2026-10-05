#!/usr/bin/env python
"""Headless screenshot sweep of the ported Qubit dashboard pages.

For each route: navigate, wait for network idle, capture a full-page PNG,
and record console errors + failed requests (4xx/5xx) + a text fingerprint of
the visible content so an empty-hero-on-history bug is visible in the report.

Usage: .venv/bin/python scripts/shot_pages.py [outdir]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8890"

ROUTES: list[tuple[str, str]] = [
    ("task-inbox", "/qubit-task-inbox"),
    ("board-admin", "/qubit-board-admin"),
    ("taskflows", "/qubit-taskflows"),
    ("worktrees", "/worktrees"),
    ("checkpoints", "/checkpoints"),
    ("session-ops", "/session-ops"),
    ("scheduler", "/qubit-scheduler"),
    ("nodes", "/nodes"),
    ("devices", "/devices"),
    ("harness-router", "/harness-router"),
    ("qubit-bot", "/qubit-bot"),
    ("webchat", "/webchat"),
    ("memory-backends", "/qubit-memory"),
    ("canvas", "/canvas"),
    ("viking", "/open-viking"),
    ("viking-retrieval", "/open-viking/retrieval"),
    ("viking-uploads", "/open-viking/uploads"),
    ("viking-agent-hub", "/open-viking/agent-hub"),
    ("viking-resources", "/open-viking/resources"),
    ("mcp-calls", "/admin/mcp-calls"),
    ("skills-review", "/admin/skills-review"),
]


def main() -> int:
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/qubit-shots")
    outdir.mkdir(parents=True, exist_ok=True)
    report = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = ctx.new_page()

        for name, route in ROUTES:
            console_errors: list[str] = []
            failed: list[str] = []
            statuses: list[tuple[str, int]] = []

            def on_console(msg, _ce=console_errors):
                if msg.type == "error":
                    _ce.append(msg.text[:300])

            def on_response(resp, _st=statuses):
                if resp.status >= 400:
                    _st.append((resp.url[:160], resp.status))

            def on_requestfailed(req, _f=failed):
                _f.append(f"{req.url[:160]} :: {req.failure}")

            page.on("console", on_console)
            page.on("response", on_response)
            page.on("requestfailed", on_requestfailed)

            entry: dict = {"name": name, "route": route}
            try:
                page.goto(BASE + route, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(2000)
                shot = outdir / f"{name}.png"
                page.screenshot(path=str(shot), full_page=True)

                text = page.inner_text("body")
                entry["shot"] = str(shot)
                entry["chars"] = len(text)
                # A page with real content renders a heading + rows or an
                # explicit empty state.  Record both so emptiness is visible.
                entry["head"] = " | ".join(
                    t.strip() for t in text.splitlines() if t.strip()
                )[:400]
            except Exception as exc:  # noqa: BLE001
                entry["error"] = f"{type(exc).__name__}: {exc}"
            entry["console_errors"] = console_errors
            entry["http_errors"] = statuses
            entry["req_failed"] = failed
            report.append(entry)
            page.remove_listener("console", on_console)
            page.remove_listener("response", on_response)
            page.remove_listener("requestfailed", on_requestfailed)
            print(json.dumps(entry)[:500], flush=True)
            time.sleep(0.2)

        browser.close()

    (outdir / "report.json").write_text(json.dumps(report, indent=2))
    bad = [r for r in report if r.get("error") or r.get("console_errors")]
    print(f"\n=== {len(report)} pages, {len(bad)} with errors ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
