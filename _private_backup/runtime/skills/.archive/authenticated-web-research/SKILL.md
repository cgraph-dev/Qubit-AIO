---
name: authenticated-web-research
description: Capture and document pages behind login (customer dashboards, seller portals, account areas) on third-party sites. Uses a persistent headed Chromium the HUMAN logs into manually, driven by the agent over a localhost HTTP control server. Use when a user wants logged-in surfaces documented (feature parity, competitor audits, UI/UX research, filter/category inventories) and has credentials you should never see.
---

# Authenticated Web Research (human-in-the-loop browser bridge)

## When to use
- User asks to document/inventory pages that require login: "pages VS Code can't see", "the ones where I'm logged in", customer + seller dashboards, account settings, order/listing management surfaces.
- Competitor parity / feature-gap research that must go beyond public pages.
- Any time you need to browse as a specific user but must NOT receive their password/2FA.

## Architecture (why this shape)
Three-way split that keeps credentials private and state persistent:
1. **Persistent Chromium profile** (`launch_persistent_context`) — user logs in once per site. NOTE: logins do NOT survive a launcher restart — sites like z2u use session cookies that vanish when the browser process closes. Back up cookies with the `cookies` action immediately after the user finishes logging in, and `injectcookies` after any relaunch. Avoid restarts mid-capture entirely; if you must patch launcher code, do it BEFORE the user logs in.
2. **Embedded HTTP server on 127.0.0.1** inside the launcher process — agent sends JSON commands via curl, gets JSON responses. NOT stdin: Hermes background processes get closed stdin (EOF immediately), so a stdin command loop dies in ~2s. The HTTP loop is the only reliable control channel.
3. **Dumps/screenshots written to files** outside the repo (e.g. `~/.hermes/<project>-research/`) — stdout stays compact (paths only), and personal account data never enters git.

## Steps
1. **Find the user's desktop session** (agent shell usually has empty DISPLAY):
   ```bash
   loginctl list-sessions; who; ls /tmp/.X11-unix/
   # XAUTHORITY is typically /run/user/$(id -u)/gdm/Xauthority (find via:
   cat /proc/$(pgrep -u $USER -f gnome-session | head -1)/environ | tr '\0' '\n' | grep -E '^(DISPLAY|XAUTHORITY)'
   ```
   Set `export DISPLAY=:0 XAUTHORITY=/run/user/1000/gdm/Xauthority` in EVERY command that touches the browser.
2. **Verify Playwright works headless first**, then headed, before handing to the user:
   ```bash
   cd <any-project> && uv run --with playwright python -c "from playwright.sync_api import sync_playwright; print('ok')"
   ```
   Browsers are pre-cached at `~/.cache/ms-playwright/` (chromium, firefox, webkit) — no `playwright install` needed. Use `--no-sandbox`.
3. **Copy the driver from `templates/launcher.py`** into `~/.hermes/<project>-research/launcher.py`, adjust `PROFILE_DIR`, `DUMP_DIR`, `SHOT_DIR`, `PORT`, and the `SITES` list. Launch as a background process (no stdin redirect!). Confirm `READY profile=... port=...` via process poll.
4. **Open all target sites in tabs** via the control server (`newtab`), then raise the window so the user sees it:
   ```bash
   wmctrl -ia <window-id>   # window id from: xwininfo -root -tree | grep -i "chrome for testing"
   ```
   Verify the window really appeared with xwininfo/wmctrl — not with vision_analyze (see pitfalls).
5. **Hand off to the user**: "log in on each tab (customer + seller accounts where applicable), leave the browser open, tell me when done." Say you'll wait silently.
6. **Back up cookies immediately after login** — `{"action":"cookies"}` writes `cookies.json`. This is the insurance policy: if the launcher crashes or must be restarted, `{"action":"injectcookies"}` restores all sessions so the user does NOT have to re-login. Do this before any other capture work.
7. **Drive the capture**: per site, walk nav links from dumps → `goto` each authenticated page → `dump` structure (nav, tabs, buttons, inputs, checkboxes, radios, links, table headers, badges, cards) → `shot`/`shotfull` screenshots. Compose findings into a research doc in the project's `docs/`.

## Control-server command reference
POST JSON to `http://127.0.0.1:<PORT>/`:
- `{"action":"goto","url":"...","tab":N}` — navigate tab (default 0)
- `{"action":"newtab","url":"..."}` / `{"action":"tabs"}` / `{"action":"activatetab","index":N}`
- `{"action":"dump","name":"..."}` — DOM structure JSON → `dumps/<name>-<ts>.json`, returns path
- `{"action":"shot","name":"..."}` / `{"action":"shotfull","name":"..."}` — PNG → `shots/`
- `{"action":"click","selector":"...","name":"..."}` — click, wait 1.5s, optional dump
- {"action":"eval","expr":"..."} / {"action":"wait","ms":N} / {"action":"quit"}
- {"action":"cookies"} — save ALL context cookies to ~/.hermes/<project>-research/cookies.json (call right after login; restore after any restart)
- {"action":"injectcookies"} — load cookies back into the context (after relaunching the launcher)
Each response is JSON {"action":..., "status":"ok|error", ...}. See templates/launcher.py for the full command set.

## SPA capture (client-side rendered sites)
Some sites (g2g confirmed) return 404 / blank content when you `goto` a deep authenticated route directly (`/user/wallet`, `/user/offers`). The SPA must boot at a known-good route first, then navigate client-side:
1. `goto` the boot route (e.g. `/user/wallet` — the one place the sidebar renders), wait ~6s for hydration.
2. Extract the sidebar's `href` list from the DOM (they're real `<a>` elements even in the dropdown).
3. For each page, CLICK the sidebar link via `eval` (`target.click()` on `a[href=...]`), wait ~4-7s (lazy content), then `dump` + `shot`. Re-`goto` the boot route between pages to reset.
4. Use scripts/spa_capture.py and scripts/deep_capture.py for this; scripts/batch_capture.py is for sites that tolerate direct `goto` (z2u).
Lazy-loaded pages may need a second longer pass capturing `document.body.innerText` (deep_capture.py) — the first pass often catches only the header shell.

## Hidden menus and expandable navigation
- Account menus are often collapsed: expand `[id^=dashDropbody]`, `[data-toggle=collapse]`, `[href*=collapse]` sections and click avatar dropdowns, THEN re-run the link extraction.
- Header mega-menus can share text with page content ("Game Accounts" appears twice): disambiguate with `closest('header')` or element Y-position before clicking.
- Some deep surfaces are access-gated (z2u seller pages show the registration gate when the account is buyer-only) — check the header for "Become Seller" vs a real dashboard and report the access boundary honestly instead of forcing it.

## Pitfalls
- **Background processes get closed stdin** — never design the launcher around `sys.stdin`; use the HTTP server pattern. (Also: don't launch with `< /dev/null` thinking stdin will stay open; it EOFs instantly.)
- **vision_analyze fails on large screenshots** (JSON deserialize 400 on multi-MB PNGs) and may be unavailable for the active model. Don't rely on it to confirm the browser opened — use `xwininfo -root -tree | grep -i chrome` and `wmctrl -l`. If you do need image analysis, downscale first (`convert in.png -resize 1280x720 -quality 70 out.jpg`).
- **Personal data hygiene**: logged-in dumps/screenshots contain the user's real orders, balance, messages. Default: they must live OUTSIDE the git repo (e.g. `~/.hermes/...`). Docs written into `docs/` describe only structure/behavior — page inventory, filters, categories, UI patterns — never copied listings, seller data, prices, or screenshots.
- **User override on screenshots**: this user may explicitly direct screenshots into the repo (observed 2026-08-08: moved all 58 PNGs into `docs/competitor-auth-research/screenshots/`). When the user explicitly asks, comply — but state the privacy risk once (personal account data in a repo that could be pushed to a remote) and offer a `.gitignore` entry. Do not lecture repeatedly; the user owns the call.
- **Login redirects**: some sites (e.g. z2u) auto-redirect to Google OAuth on first load; that tab may end up on a failed Google sign-in page. Tell the user to use regular email/password login, or close that tab and navigate fresh.
- **`innerText` is undefined on some elements** (inputs, etc.) — the extraction JS crashes with `TypeError: Cannot read properties of undefined (reading 'replace')` if you call `.replace()` directly. Use `(el.textContent || el.innerText || '').replace(...)`. The template already has the fix; don't regress it.
- **Captcha/anti-bot may block the automation browser** even headed — in practice zeusx, eldorado.gg, and g2a rejected it while z2u and g2g passed. Don't fight it; scope the capture to the sites that pass, tell the user exactly which sites are left, and let them decide (manual HTML saves, cookies from their real browser, or partial scope). Never bypass captcha.
- **Rapid sequential navigation triggers Cloudflare** ("Just a moment… / Performing security verification") even on sites that passed login — batch runs hitting many URLs back-to-back will get challenged. Pause 1-2s between navigations; if challenged, wait ~20-30s (sometimes auto-passes), retry a reload, or ask the user to click the checkbox in the visible window. The batch scripts take ~1s gaps, which is usually fine; don't reduce it.
- **Restart = logout**: if the launcher dies or is restarted, ALL sessions are lost (session cookies). Relaunch + `injectcookies` before telling the user anything is done; if cookies weren't backed up, the user must re-login.
- **Sites list is per-project**: keep the 5 benchmark sites (z2u, zeusx, eldorado.gg, g2g, g2a) for RankValut; adjust for other audits.
- Don't scrape private data at scale or bypass site controls; the runbook in RankValut docs explicitly forbids automated crawling of authenticated pages — this human-in-the-loop flow is the sanctioned alternative.

## Support files
- `templates/launcher.py` — full working driver (persistent profile + HTTP control server + structure-extraction JS). Copy and adjust; this is the known-good version from RankValut research setup.
- `scripts/batch_capture.py` — capture `name::url` pairs → markdown summary + dumps + screenshots (for sites tolerating direct `goto`, e.g. z2u).
- `scripts/spa_capture.py` — click-driven capture for SPA sites: boot route, click sidebar links by href, dump + shot each (g2g).
- `scripts/deep_capture.py` — long-wait (7s) body-text capture for lazy-loaded SPA pages; second pass after spa_capture.
- `references/rankvalut-captured-surfaces-2026-08.md` — concrete z2u/g2g captured inventory (sidebar URL maps, order state machines, seller-registration steps) to continue from when the remaining sites (zeusx, eldorado.gg, g2a) get unblocked.
