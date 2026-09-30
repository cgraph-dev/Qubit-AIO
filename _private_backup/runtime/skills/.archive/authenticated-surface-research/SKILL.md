---
name: authenticated-surface-research
description: Capture login-gated pages (customer/seller dashboards, seller tools, settings) of web apps using a persistent Playwright browser the user logs into manually, then turn them into clean-room implementation docs. Use for competitor page-parity research, "pages VS Code can't see", and dashboard/UX inventories.
---

# Authenticated Surface Research (login-gated page capture)

## When to use
- User asks to document pages that require login: dashboards, seller tools, order pages, settings, verification flows — "the pages that can't be seen without logging in".
- Competitor/parity research where public scraping covers only the unauthenticated surface (marketplaces: z2u, zeusx, eldorado.gg, g2g, g2a, eneba, seagm, playerauctions, funpay).
- Any task needing a real headed browser the user logs into manually so credentials/2FA never touch the agent.

## Core architecture
- **Persistent Chromium profile** — user logs in ONCE per site; sessions survive across runs.
- **Localhost HTTP control server** inside the Playwright process. CRITICAL: background processes started via terminal(background=true) get CLOSED stdin, so piping commands via process(write/submit) dies instantly. The launcher must expose an HTTP endpoint (default port 8791) and the agent drives it with curl / urllib POST JSON.
- **Cookie backup immediately after login** — many sites (z2u) use session cookies that do NOT survive a browser restart. Export via `{"action":"cookies"}` right after the user says "done"; re-inject with `{"action":"injectcookies"}` after any restart. This turns a fatal re-login into a non-event.
- **Structure dumps + screenshots to files**, never stdout (stdout truncates/scrolls; JSON dumps can be 50KB+).

## Workflow
1. **Find the user's desktop session** (headed browser must appear on their screen): check `who`, `loginctl`, `/tmp/.X11-unix`, then `xwininfo -root -tree`. Typical Linux: `DISPLAY=:0`, `XAUTHORITY=/run/user/<uid>/gdm/Xauthority`. Verify a headed window actually renders before asking the user to log in (launch a throwaway page + screenshot the root window with `import -window root`).
2. **Launch** `scripts/launcher.py` in background with DISPLAY+XAUTHORITY exported (Playwright browsers live under `~/.cache/ms-playwright`; run via `uv run --with playwright python ...`).
3. Open one tab per target site (`{"action":"newtab","url":...}`); raise the window with `wmctrl -ia <wid>` so the user sees it.
4. Ask the user to log in on each tab — customer AND seller accounts where both exist. Tell them to leave the browser open and say "done".
5. On "done": immediately `{"action":"cookies"}` → backup. Then map the menus: eval JS to collect all `<a href>` plus hidden accordion/dropdown links (many dashboards keep the real URLs in collapse sections that need a click to expand).
6. **Batch capture**: for each page `goto → dump → shot`. For SPAs, boot at the sidebar route then click nav links client-side (see pitfalls).
7. Extract: nav links, tabs, buttons, inputs/selects, table headers, badges/status, full body text, form field labels.
8. Write the deliverable doc (see format) into the repo `docs/`; keep screenshots with personal data OUT of git or warn the user first.

## Pitfalls (all hit in the field)
- **Background stdin is closed** → never try to drive a background process via stdin; the HTTP control server is the pattern.
- **Session cookies are volatile** → restarting the browser without a cookie backup = user re-login. Back up first, every time.
- **SPA routes 404 on direct load** (g2g): `/user/wallet` works only after SPA boot at a valid sidebar route + client-side link click. Use `scripts/deep_capture.py` (click-driven) and wait 5–8s for hydration before dumping.
- **Cloudflare / rate limits**: rapid navigation triggers "Just a moment..." challenges; pace captures (1–2s between gotos). A challenge often needs a human click — raise the window (`wmctrl -ia`) and ask the user to click the checkbox; reload can also clear it.
- **DOM extraction**: inputs/void elements lack `innerText` → use `(el.textContent || el.innerText || '')`; filter to visible elements via `getBoundingClientRect()` + computed style.
- **Framework buttons ignore JS `.click()`** (Quasar/Svelte `q-btn`, `h-button`) — use Playwright's real click (`{"action":"click","selector":"text=..."}`), which sends genuine mouse events.
- **Rapid tab churn can close pages** (TargetClosedError) — after failures, re-verify with `{"action":"tabs"}` and re-open tabs; sessions survive via profile+cookies.
- **Screenshots = personal data** (orders, balances, phone/address, DOB) — never commit to a public repo without asking; committed docs must be structural only.

## Clean-room rules (this user's standard)
- Learn behavior, state transitions, information architecture — do NOT copy competitor code, assets, screenshots-as-assets, listings, prices, seller data, or wording.
- Never fabricate counts, metrics, or policy in the docs; mark claims as "observed capability", not verified quality.
- Deliverable docs should contain: page inventory with EXACT routes, UI/UX organization, filters/categories, forms/fields, A→Z flows (order state machines, seller onboarding steps, dispute flows), a mapping table to the user's existing routes, and data-model hints (enums/statuses) so another agent can start implementing without re-browsing.

## Scripts
- `scripts/launcher.py` — persistent-browser driver with HTTP control: goto, newtab, tabs, activatetab, dump (DOM structure JSON), shot/shotfull, click, eval, cookies, injectcookies, wait, quit.
- `scripts/batch_capture.py` — loop over `name::url` pairs: goto, dump, screenshot, emit compact markdown summary (for non-SPA sites).
- `scripts/deep_capture.py` — SPA click-driven capture: boot at sidebar route, click each link, wait, dump body text + structure + screenshot (for SPAs like g2g).

## References
- `references/z2u-g2g-url-maps.md` — exact authenticated route inventories, order state machines, seller flows, and session gotchas for Z2U and G2G (captured 2026-08-08). Reuse when implementing RankValut pages or re-capturing.
