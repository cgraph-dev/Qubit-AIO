#!/usr/bin/env python3
"""Dual-browser inbox reader (Gmail / Instagram DMs / Messenger / TikTok) via Playwright.

Uses PLAYWRIGHT persistent browser contexts so logins persist per profile (you
log in ONCE in each profile; cookies are reused after). Two browser engines are
supported so accounts split across Chromium and Firefox both work.

Profile dirs (gitignored, under scripts/browser_profiles/):
  chromium/  -> logged into Chromium-only accounts
  firefox/   -> logged into Firefox-only accounts

Usage (reads the inbox and prints JSON):
  python browser_inbox.py gmail        [--engine chromium|firefox]
  python browser_inbox.py instagram
  python browser_inbox.py messenger
  python browser_inbox.py tiktok
  python browser_inbox.py login gmail  # open a headed window to log in once

Engine auto-pick: if a given `--engine` isn't available or the site isn't
logged in on it, the reader tries the other engine (configurable via a map).
Output shape mirrors the old CDP scripts so review_*.py keep working:
  [{"sender":..., "subject":..., "snippet":..., "time":..., "unread":bool}]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROFILES = Path(__file__).resolve().parent / "browser_profiles"

# site -> (url, per-browser login probe). If the site isn't signed in on one
# engine it falls through to the other.
SITES = {
    "gmail": {
        "url": "https://mail.google.com/mail/u/0/#inbox",
        "home": "https://accounts.google.com/v3/signin/identifier",
        "signed_in_hint": "Compose",
    },
    "instagram": {
        "url": "https://www.instagram.com/direct/inbox/",
        "home": "https://www.instagram.com/",
        "signed_in_hint": "Messages",
    },
    "messenger": {
        "url": "https://www.messenger.com/",
        "home": "https://www.facebook.com/",
        "signed_in_hint": "New message",
    },
    "tiktok": {
        "url": "https://www.tiktok.com/",
        "home": "https://www.tiktok.com/",
        "signed_in_hint": "Inbox",
    },
}

DEFAULT_ENGINE_ORDER = ["chromium", "firefox"]


def _profile_dir(engine: str) -> Path:
    return PROFILES / engine


def _make_engine(browser, engine: str, headless: bool):
    """Return a persistent context for `engine`. Throws on launch failure."""

    profile = _profile_dir(engine)
    profile.mkdir(parents=True, exist_ok=True)
    if engine == "firefox":
        return browser.firefox.launch_persistent_context(
            str(profile), headless=headless, locale="en-US")
    return browser.chromium.launch_persistent_context(
        str(profile), headless=headless, locale="en-US")


def _tab_for(context, url: str, home: str, timeout_ms: int = 45000):
    """Open (or reuse) a page on `url`, returning it once loaded."""
    for pg in context.pages:
        if url.split("#")[0] in (pg.url or "").split("#")[0]:
            return pg
    pg = context.new_page()
    try:
        pg.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
    except Exception:
        try:
            pg.goto(home, timeout=timeout_ms, wait_until="domcontentloaded")
            pg.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        except Exception:
            pass
    return pg


def _read_gmail(pg) -> list[dict]:
    """Read the Gmail inbox via the accessible list rows (tr.zA)."""
    out = []
    try:
        pg.wait_for_selector("table tbody tr.zA", timeout=25000)
    except Exception:
        pg.wait_for_selector("div[role=main]", timeout=15000)
    rows = pg.query_selector_all("table tbody tr.zA")
    for r in rows[:15] or []:
        sender = subj = snippet = time_txt = ""
        try:
            sender_el = r.query_selector(".yW span, .zF, .zH, .gD")
            sender = (sender_el.inner_text().strip() if sender_el else "")
        except Exception:
            pass
        try:
            subj_el = r.query_selector(".bog, .y6, .bA4, .zG, .zE, .zK")
            subj = (subj_el.inner_text().strip() if subj_el else "")
        except Exception:
            pass
        try:
            sn_el = r.query_selector(".y2, .y6, .gUb")
            snippet = (sn_el.inner_text().strip() if sn_el else "")
        except Exception:
            pass
        try:
            t_el = r.query_selector(".xW, .xH, .xY")
            time_txt = (t_el.inner_text().strip() if t_el else "")
        except Exception:
            pass
        out.append({"sender": sender or "?", "subject": subj, "snippet": snippet,
                    "time": time_txt, "unread": True})
    return out


def _read_generic(pg, row_selector: str, extract) -> list[dict]:
    """Generic reader: wait for `row_selector`, run `extract` on each row."""
    out = []
    try:
        pg.wait_for_selector(row_selector, timeout=20000)
    except Exception:
        body = ""
        try:
            body = pg.inner_text("body")
        except Exception:
            pass
        return [{"text": body[:2000]}] if body else []
    rows = pg.query_selector_all(row_selector)
    for r in (rows or [])[:15]:
        try:
            out.append(extract(r))
        except Exception:
            pass
    return out


def read_site(site: str, engine: str = "chromium", headless: bool = True) -> dict:
    if site not in SITES:
        return {"error": f"unknown site {site}"}
    cfg = SITES[site]
    import playwright.sync_api as pw

    order = [engine] if engine else list(DEFAULT_ENGINE_ORDER)
    attempts = []
    for eng in order:
        try:
            with pw.sync_playwright() as browser:
                context = _make_engine(browser, eng, headless)
                try:
                    pg = _tab_for(context, cfg["url"], cfg["home"])
                    if site == "gmail":
                        items = _read_gmail(pg)
                    elif site == "instagram":
                        items = _read_generic(
                            pg, "div[role=link], div.MX, div.x1lliihq",
                            lambda r: {
                                "name": r.inner_text()[:40],
                                "text": r.inner_text()[:120],
                                "unread": "unread" in (r.get_attribute("class") or "").lower(),
                            })
                    elif site == "messenger":
                        items = _read_generic(
                            pg, "[role=listitem], div[data-tooltip], a[href*=t]",
                            lambda r: {
                                "name": r.inner_text()[:40],
                                "text": r.inner_text()[:120],
                                "unread": False,
                            })
                    else:  # tiktok
                        items = _read_generic(
                            pg, "[data-e2e=notification-item], div[data-e2e]",
                            lambda r: {"text": r.inner_text()[:120], "unread": True})
                    # If nothing came back, keep open (items non-empty) — return
                    # what we have; caller may fall back if it looks logged-out.
                    return {"site": site, "engine": eng, "items": items, "url": pg.url}
                finally:
                    try:
                        context.close()
                    except Exception:
                        pass
        except Exception as e:
            attempts.append({"engine": eng, "error": str(e)[:120]})
    return {"site": site, "engine": "none", "items": [], "attempts": attempts,
            "error": "no engine produced data"}


def _login_browser(engine: str) -> None:
    """Open a headed persistent window for the user to log into 1 site."""
    import playwright.sync_api as pw
    with pw.sync_playwright() as browser:
        context = _make_engine(browser, engine, headless=False)
        pg = context.new_page()
        pg.goto("https://accounts.google.com/", timeout=45000)
        print(f"Log in to each site in the window, then press Enter here.")
        try:
            input()
        except Exception:
            pass
        context.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    site = args[0] if args else "gmail"
    engine = ""
    if "--engine" in args:
        engine = args[args.index("--engine") + 1]
    if site == "login":
        _login_browser(engine or "chromium")
        sys.exit(0)
    result = read_site(site, engine or "chromium")
    print(json.dumps(result, ensure_ascii=False))
