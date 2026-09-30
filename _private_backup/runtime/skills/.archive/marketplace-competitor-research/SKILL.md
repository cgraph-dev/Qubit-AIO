---
name: marketplace-competitor-research
description: >
  Clean-room competitor research for building a digital-goods marketplace:
  capture public AND authenticated (logged-in) surfaces of competitor
  marketplaces (Z2U, G2G, ZeusX, Eldorado, PlayerAuctions, G2A, FunPay, etc.),
  verify real fee/protection numbers from official help pages, and write
  implementation-ready docs another AI agent can build from. Use when the user
  is building a marketplace and needs page inventories, state machines, fee
  benchmarks, UI/UX organization, filters, categories, or parity plans —
  including pages that require login to see.
platforms: [linux]
version: 1.0.0
author: hermes
license: MIT
category: research
metadata:
  hermes:
    tags: [marketplace, competitor-research, clean-room, playwright, fees, ecommerce]
---

# Marketplace Competitor Research (clean-room)

## When to use

- User is building a marketplace and wants competitor page inventories,
  authenticated surfaces, filters/categories, fee structures, protection
  systems ("Trade Guardian", escrow, seller protection), or UI/UX patterns.
- User wants docs to hand to another AI agent to implement ("add everything
  A→Z so I can give it to an agent").
- User asks to verify competitor fee/price numbers "so we don't make mistakes".

## Core principles (user's rules — non-negotiable)

1. **Clean-room**: learn behavior/structure/states/IA. NEVER copy competitor
   code, copy, assets, listings, seller data, screenshots as production
   assets, or policy text. Record behavioral notes only.
2. **Public fee pages are readable; dynamic price data is not.** Reading
   official help-center fee articles is allowed. Scraping live offer/seller/
   price feeds is not (project research runbooks forbid it).
3. **Verified numbers before proposing.** When the user asks for competitor
   fee/price numbers, FETCH them from official pages now. Do not write
   "verify later" placeholders for facts you can capture in this session —
   the user explicitly wants real numbers in docs so implementing agents
   don't guess. Wrong proposed numbers (e.g. a gift-card fee higher than the
   competitor's) get caught and waste a full rework cycle.
4. **Personal data stays out of git.** Authenticated captures (screenshots,
   dumps, cookies) contain the user's own account data — keep them in a local
   research dir (e.g. `~/.hermes/<project>-research/`), never commit them.
   Docs get structure/behavior only.
5. **Implementation-ready output**: exact routes, state-machine enums,
   transition tables, table columns, form fields, fee ladder, mapping to the
   user's existing routes, and a build order. An agent should be able to
   start Phase 1 without asking.
6. **Exact scope compliance**: only operate on the directories/files the user
   names. Do not broaden scope, touch other profiles, or "clean up" unrelated
   files.

## Workflow

### Phase 1 — Public surfaces (fast, no login)

- curl static help-center pages with a browser UA:
  `curl -sL -m 30 -A "Mozilla/5.0 (X11; Linux x86_64) ... Chrome/126.0 ..." URL -o page.html`
- **Intercom help centers** (support.eldorado.gg, support.playerauctions.com)
  are static HTML — strip `<script>/<style>`, collapse tags, search keyword
  contexts. Works with curl.
- **Zoho Desk portals** (help.zeusx.com) are JS-rendered — curl gets only the
  portal shell. Use the real browser (Phase 2 infra) to render and read
  `document.body.innerText`.
- **G2A** blocks automation aggressively (HTTP2 protocol errors, 404s,
  fingerprinting). Fallback: Wayback Machine snapshots of official pages
  (`web.archive.org/web/2023/https://www.g2a.com/plus` worked), plus observed
  public marketplace prices from any page load that does succeed.
- Cloudflare-walled pages (PlayerAuctions via curl) often pass in a real
  Chrome browser — retry there before giving up.

### Phase 2 — Authenticated surfaces (login-gated pages)

See `templates/browser-research-launcher.py` for the full driver. Pattern:

1. **Persistent headed Chromium** on the user's desktop so THEY log in
   manually (credentials/2FA never touch the agent).
   - Get display: `DISPLAY=:0 XAUTHORITY=/run/user/$(id -u)/gdm/Xauthority`
     (find via `loginctl list-sessions`, `ps aux | grep Xorg`, test with
     `xdpyinfo`; verify window with `wmctrl -l`).
   - `launch_persistent_context(profile_dir, headless=False)`.
2. **HTTP control server, NOT stdin.** Background processes get closed stdin,
   so a `for line in sys.stdin` command loop exits immediately. Embed a
   ThreadingHTTPServer on 127.0.0.1 (e.g. port 8791); drive it with
   `curl -X POST -d '{"action":"dump","name":"..."}'`. Actions: goto, newtab,
   tabs, activatetab, dump (DOM structure JSON to file), shot/shotfull,
   click (real Playwright click), eval, wait, cookies, injectcookies, quit.
3. **Back up cookies IMMEDIATELY after the user logs in.** Session cookies
   die on browser restart — a restart loses all logins and forces the user
   to redo 2FA on every site. `{"action":"cookies"}` saves
   `ctx.cookies()` to JSON; `{"action":"injectcookies"}` restores it.
   NEVER restart the browser mid-capture once sessions are live.
4. **Map the logged-in menu first**: dump nav links / avatar dropdown /
   sidebar accordion URLs. Then visit every page. Sites keep hidden menus
   behind hover/click (avatar button, accordion sections) — extract after
   clicking.
5. **SPA sites (g2g) 404 on direct route loads** — must navigate by clicking
   sidebar links client-side after booting at a known-good route, then wait
   4–8s for async render before dumping. Server-rendered routes are a
   competitive advantage to note, not a pattern to copy.
6. **Cloudflare challenges need a human click.** Raise the window
   (`wmctrl -ia <WID>`) and ask the user to click the checkbox. Batch
   navigation triggers rate-limiting — pace requests (sleep between gotos),
   and note that rapid sequential nav can trip "Just a moment..." pages.

### Phase 3 — Fee & protection benchmarks

- Capture seller fee, buyer fee, withdrawal fee per category from official
  articles. **Compare TOTAL transaction cost** (seller + buyer + withdrawal),
  not just seller fee — e.g. Eldorado charges 0% seller fee on gift cards
  but 8%+$0.30 buyer fee, so its total is HIGHER than a modest seller fee +
  zero buyer fee.
- Capture protection systems: escrow steps, fund-release/hold periods by
  category, auto-complete timers, premium-seller requirements, paid
  membership pricing.
- Save verified numbers as a dated reference doc with source URLs (see
  `references/competitor-fee-benchmark-2026-08.md` for captured values).

### Phase 4 — Implementation-ready docs

Structure that worked well (RankValut):
- Status/scope header + clean-room note + screenshot pointer (relative path).
- Route inventory table (competitor route → behavior notes).
- State machines as one enum + transition table (guards, atomic effects).
- A→Z flows (buyer lifecycle, seller onboarding, dispute flow).
- Mapping table: competitor surface → user's existing route → gap.
- Data-model hints (enums, statuses, ledger shapes).
- `[OWNER DECISION]` markers for anything needing product/legal/provider
  approval; `[propose X]` for defaults with the reasoning.
- Cross-link the doc set (parity plan ↔ research docs ↔ decision pack ↔
  blueprint) and verify every relative link resolves — when you move a doc
  into a subfolder, sibling links need `../` prefixes.
- Marketing/trust copy: only verifiable claims; never "10% cheaper than X"
  without live price data; compute savings figures at render time from the
  approved schedule.

## Pitfalls

- **Background process stdin is closed** → use the HTTP-server driver, not a
  stdin loop.
- **Browser restart = lost logins** → cookie backup before any restart; never
  restart mid-capture.
- **JS-rendered portals** (Zoho) need the browser; static Intercom pages work
  with curl. Test which kind you have before building the pipeline.
- **G2A blocks automation** — plan Wayback + observable prices; mark
  dashboard-only numbers as unverified.
- **Fee proposals must beat the REAL competitor numbers.** Verify first, then
  propose. A base fee set above a competitor's category fee is a strategy
  error, not a typo.
- **Screenshots contain personal data** — keep them out of git; point to
  them with relative paths from the docs only.
- **Don't fabricate counts, prices, protection claims, or seller metrics** in
  docs or UI copy. Mark marketing claims as verified/unverified.

## Verification checklist

- [ ] Every claimed fee/price has a source URL + capture date.
- [ ] Total-cost comparison used (seller+buyer+withdrawal), not seller fee alone.
- [ ] Authenticated captures (cookies/screenshots/dumps) live outside the repo.
- [ ] All internal .md links resolve (run a link checker after moving docs).
- [ ] Docs contain exact routes, enums, table columns, and a build order — an
      agent can start without asking.
- [ ] No competitor names in marketing claims; no scraped price data used.
