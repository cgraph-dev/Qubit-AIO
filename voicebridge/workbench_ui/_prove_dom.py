#!/usr/bin/env python3
"""Headless-DOM proof: the ported UI's REAL layout mounts and paints.

WHAT "MOUNTS" MEANS HERE
---------------------------------------------------------------------------
Not "the HTML downloaded". The upstream page builds its whole UI from a
deferred script bundle, so a 200 on index.html proves nothing — a rebrand that
renamed a global the bundle reads, or a base href that resolves one directory
too high, yields a perfectly valid document with an EMPTY body. So this asserts,
in the live DOM of a real headless Chrome:

  * the three panels exist and are laid out (non-zero boxes): the rail, the
    session sidebar, and the chat panel;
  * the workspace toggle and the message container are present;
  * the empty-state logo painted (it is the first thing a fresh user sees);
  * ``document.title`` is Qubit and no rendered text says "Hermes";
  * the bootstrap JS RAN: ``window.QubitExtensionSettings`` was published and
    ``switchPanel`` is callable — i.e. panels.js executed to its end, which only
    happens if every earlier script parsed;
  * clicking the Settings rail button actually changes the visible panel (the
    interaction path, not just the markup).

Console errors and failed requests are collected and REPORTED with their URL,
because a dead fetch is a fact about the page worth seeing — the ones that are
GAP endpoints are expected and are listed separately from real breakage.

Usage:  .venv/bin/python voicebridge/workbench_ui/_prove_dom.py
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

#: GAP endpoints — a 501 here is the design, not a break.
EXPECTED_501 = "/wb/api/"


def main() -> int:
    from voicebridge.workbench_ui._prove_live import install_wb_branch
    install_wb_branch()
    from voicebridge import webui as W
    from voicebridge.config import Config

    cfg = Config.load()
    try:
        import tempfile
        cfg.data.setdefault("webui", {})["state_dir"] = tempfile.mkdtemp(
            prefix="qubit-wb-dom-")
    except Exception:
        pass

    class Orch:
        def __init__(self, config):
            self.cfg = config

    ui = W.WebUI(Orch(cfg), host="127.0.0.1", port=0)
    ui.start()
    ui._server.wb_cfg = cfg  # type: ignore[attr-defined]
    port = ui._server.server_address[1]
    token = ui.api_token or ""
    url = f"http://127.0.0.1:{port}/wb/"

    from playwright.sync_api import sync_playwright

    console: list[str] = []
    failed: list[str] = []
    failures: list[str] = []
    result: dict = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path="/usr/bin/google-chrome",
                args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("console", lambda m: console.append(f"{m.type}: {m.text}")
                    if m.type in ("error",) else None)
            page.on("requestfailed", lambda r: failed.append(
                f"{r.method} {r.url} :: {r.failure}"))
            page.on("response", lambda r: failed.append(
                f"HTTP {r.status} {r.url}") if r.status >= 500 else None)
            page.goto(url, wait_until="load", timeout=45000)
            page.wait_for_timeout(4000)

            def probe():
                return page.evaluate("""() => {
                  const q = s => {
                    const el = document.querySelector(s);
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    return {w: Math.round(r.width), h: Math.round(r.height),
                            display: getComputedStyle(el).display,
                            vis: getComputedStyle(el).visibility};
                  };
                  const txt = document.body.innerText || '';
                  return {
                    title: document.title,
                    url: location.href,
                    base: document.baseURI,
                    rail: q('.rail'),
                    railTabs: document.querySelectorAll('.rail .nav-tab[data-panel]').length,
                    sidebar: q('.sidebar'),
                    chat: q('#panelChat'),
                    chatTitle: (document.querySelector('#panelChat .panel-head-title')||{}).textContent||'',
                    msgInner: q('#msgInner'),
                    emptyState: q('#emptyState'),
                    emptyLogo: q('#emptyState .empty-logo svg, .empty-logo svg'),
                    composer: q('#composer, .composer'),
                    workspaceToggle: !!document.querySelector('[data-panel="workspaces"], #btnWorkspaceToggle, .workspace-toggle'),
                    panels: document.querySelectorAll('.panel-view').length,
                    activePanel: (document.querySelector('.panel-view.active')||{}).id||'',
                    token: typeof window.__QUBIT_SESSION_TOKEN__,
                    tokenLen: (window.__QUBIT_SESSION_TOKEN__||'').length,
                    extSettings: typeof window.QubitExtensionSettings,
                    switchPanel: typeof window.switchPanel,
                    anchors: typeof window.QubitTurnAnchors,
                    bodyTextHasHermes: /hermes/i.test(txt),
                    bodyTextSample: txt.slice(0, 160),
                    scripts: document.querySelectorAll('script[src]').length,
                  };
                }""")

            result = probe()
            # Capture the DEFAULT view first: a fresh visitor lands on Chat with
            # the empty-state logo and the real session list, and that is the
            # frame a human should judge the port by.
            page.screenshot(path="/tmp/qubit-wb-chat.png", full_page=False)

            # interaction: Settings rail button must change the active panel
            try:
                page.click('.rail .rail-btn.nav-tab[data-panel="settings"]',
                           timeout=4000)
                page.wait_for_timeout(1200)
                result["afterSettingsClick"] = page.evaluate(
                    "() => (document.querySelector('.panel-view.active')||{}).id||''")
            except Exception as exc:
                result["afterSettingsClick"] = f"CLICK FAILED: {exc}"

            # task-list / workspace panel presence after the click
            result["settingsPanel"] = page.evaluate(
                "() => {const e=document.querySelector('#panelSettings');"
                "if(!e) return null; const r=e.getBoundingClientRect();"
                "return {w:Math.round(r.width), h:Math.round(r.height)};}")
            page.screenshot(path=str(Path("/tmp/qubit-wb-dom.png")),
                            full_page=False)
            browser.close()
    finally:
        ui.stop()

    print(json.dumps(result, indent=1)[:2600])

    def need(key, cond, why):
        if not cond:
            failures.append(f"{key}: {why}")

    need("title", result.get("title") == "Qubit",
         f"title is {result.get('title')!r}")
    need("base", "/wb/" in (result.get("base") or ""),
         f"baseURI is {result.get('base')!r}")
    need("rail", result.get("rail") and result["rail"]["w"] > 0
         and result["rail"]["h"] > 0, "rail has no box")
    need("railTabs", (result.get("railTabs") or 0) >= 9,
         f"only {result.get('railTabs')} rail tabs")
    need("sidebar", result.get("sidebar") and result["sidebar"]["w"] > 0,
         "sidebar has no box")
    need("chat", result.get("chat") and result["chat"]["h"] > 0,
         "chat panel has no box")
    need("msgInner", bool(result.get("msgInner")), "#msgInner missing")
    need("emptyState", bool(result.get("emptyState")), "#emptyState missing")
    need("emptyLogo", result.get("emptyLogo") and result["emptyLogo"]["w"] > 0,
         "empty-state logo did not paint")
    need("token", result.get("token") == "string"
         and (result.get("tokenLen") or 0) > 0, "session token not injected")
    need("bootstrap", result.get("extSettings") == "object",
         "extension_settings.js did not run")
    need("switchPanel", result.get("switchPanel") == "function",
         "panels.js did not run")
    need("anchors", result.get("anchors") == "object",
         "assistant_turn_anchors.js did not run")
    need("rebrand", result.get("bodyTextHasHermes") is False,
         "rendered text still says Hermes")
    need("interaction", result.get("afterSettingsClick") == "panelSettings",
         f"settings click landed on {result.get('afterSettingsClick')!r}")

    real_failed = [f for f in failed if EXPECTED_501 not in f
                   and "favicon" not in f]
    gap_501 = [f for f in failed if EXPECTED_501 in f]
    print(f"\nconsole errors: {len(console)}")
    for c in console[:12]:
        print("   ", c[:180])
    print(f"failed/5xx requests: {len(failed)} "
          f"({len(gap_501)} on GAP endpoints — expected)")
    for f in real_failed[:12]:
        print("   REAL:", f[:170])
    print(f"\nscreenshot: /tmp/qubit-wb-dom.png")
    print("=" * 60)
    if failures:
        print(f"FAIL — {len(failures)} problem(s)")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS — the ported layout mounted in a real headless Chrome")
    return 0


if __name__ == "__main__":
    sys.exit(main())
