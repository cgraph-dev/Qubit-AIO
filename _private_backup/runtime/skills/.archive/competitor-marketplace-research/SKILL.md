---
name: competitor-marketplace-research
description: >
  Research competitor marketplace platforms (gaming marketplaces, e-commerce,
  SaaS marketplaces) for feature parity, page inventory, UI/UX, filters, and
  fee/protection policies. Covers authenticated surfaces via a real-browser
  login bridge, clean-room capture (structure + screenshots, personal data kept
  out of git), verified fee benchmarking, and writing implementation-ready
  research docs an agent can build from. Use when the user asks to "check what
  competitors have", "complete the pages", compare fees/pricing, or produce a
  parity/blueprint doc pack.
platforms: [linux]
version: 1.0.0
author: Hermes
license: MIT
category: research
metadata:
  hermes:
    tags: [research, marketplace, competitor-analysis, clean-room, playwright, fees, parity]
---

# Competitor Marketplace Research

## When to use

- User wants a competitor page/feature inventory **including logged-in surfaces**
  (customer dashboards, seller tooling) that plain scraping can't see.
- User wants competitor **fee / pricing / protection** analysis ("trade
  guardian", escrow, membership) with correct numbers.
- User needs **implementation-ready parity docs** to hand to another AI agent.

## Core workflow

1. **Scope.** Public surfaces first (help centers, fee pages, support articles),
   then authenticated. Check the repo's existing research docs so new work
   extends rather than duplicates.
2. **Bridge.** Launch a persistent headed Chromium the USER logs into manually
   (credentials/2FA never touch the agent); drive it programmatically afterwards.
3. **Capture.** DOM-structure dumps + screenshots per page. Keep anything with
   personal data (orders, names, addresses) OUTSIDE the git repo.
4. **Verify fees/policies** from official pages with a real browser; Wayback
   Machine as fallback; record numbers WITH source + capture date.
5. **Write docs.** Per-site research → verified benchmark → blueprint →
   decision pack → spec. Cross-link them; mark `[OWNER DECISION]` items;
   fix the numbers so the implementing agent never guesses.
6. **Consistency pass.** grep for stale numbers, verify `.md` links resolve,
   check state-machine/term naming across docs, add supersede notes when older
   docs conflict.

## The browser login bridge (authenticated surfaces)

The reliable pattern that emerged:

1. Use **Playwright** with a **persistent profile** (`launch_persistent_context`
   with a dedicated `user_data_dir`) so login state survives.
2. Run it **headed on the user's desktop** (`DISPLAY=:0 XAUTHORITY=/run/user/$(id -u)/gdm/Xauthority`
   on Ubuntu GNOME) so the user sees the window and logs in manually.
3. **Do NOT drive it via stdin** — background processes get closed stdin.
   Instead, embed a small **HTTP control server on 127.0.0.1** that accepts
   JSON commands (`goto`, `dump`, `shot`, `click`, `eval`, `tabs`,
   `cookies`, `injectcookies`, ...) and the agent drives it with `curl`.
   See `scripts/launcher.py` — copy and adapt it.
4. **Back up cookies BEFORE any browser restart** (`context.cookies()` → JSON,
   `context.add_cookies()` to restore). Session cookies die on restart; losing
   them costs the user a re-login.
5. Capture structure to **files** (JSON dumps, PNG screenshots), not stdout —
   stdout has size limits and big DOM dumps truncate.

## Pitfalls (learned the hard way)

- **Session cookies die on browser restart.** Never restart the research
  browser after the user logs in without first dumping cookies.
- **SPA sites (G2G) 404 on direct deep-route loads.** `/user/*` routes fail
  when loaded directly; you must boot the SPA at its root/sidebar URL then
  **click** the sidebar links (client-side routing). Write a click-driven
  capture loop (boot → click href → wait → dump → back to boot).
- **Cloudflare / bot walls.** The real headed browser passes basic challenges;
  direct `curl`/`urllib` often gets 403. If a site still blocks automation
  (G2A dropped HTTP2 connections), fall back to the **Wayback Machine**, or
  record honestly that the number is dashboard-only rather than inventing it.
- **JS-rendered help centers** (Zoho Desk, Intercom) have no article body in
  raw HTML — always fetch through the browser for those.
- **Rate limiting on rapid navigation.** Competitors throttle fast loops;
  space requests, use tabs, sleep 5–10 s between actions, and reload the
  offending page after a pause.
- **Fee pages move/404.** G2G's fee article path was wrong on first guess;
  find the real URL via the support home / search before concluding data is
  unavailable.

## Clean-room rules (from the user's research runbook)

- Read competitor public pages for behavior/structure; never scrape dynamic
  offer/price/seller/account data, and never copy assets, listings, policy
  text, or personal data.
- Screenshots containing the user's own logged-in data must NOT be committed
  to a repo that could go public.
- Never fabricate counts, reviews, delivery times, protection claims, or fee
  numbers. "Verify or mark unverified" beats guessing.

## Fee verification method

- **Compare TOTAL transaction cost** (seller fee + buyer fee + withdrawal
  cost), not just seller fee — some competitors hide margin on the buyer side
  (Eldorado: 0% seller on gift cards but 8%+$0.30 buyer fee).
- Pull current rates from official fee pages (they change — PlayerAuctions
  announced a fee update effective 2026-08-19).
- Positioning rule used for RankValut: base fee = verified competitor total
  − 1–2 percentage points per category, rank-ladder discounts floor-capped,
  profitability guardrail `take − payment cost − ops cost > 0`.
- See `references/competitor-fee-benchmark-2026-08.md` for verified numbers
  (ZeusX, Eldorado, PlayerAuctions, G2G, G2A) captured 2026-08-08.

## Doc authoring conventions (this user)

- The user wants **exact verified numbers in the docs** ("so the agent that
  will work won't make mistakes") — NOT "verify at decision time" placeholders
  when the numbers are obtainable now.
- Include A→Z flows, exact routes, table columns, form fields, and state
  machines. Docs are handed to other agents to implement from.
- Doc chain used successfully: per-site research → verified fee benchmark →
  implementation blueprint → Phase-0 decision pack → product spec. One doc is
  the **authoritative source** per number; others reference it ("if these
  disagree, the X spec wins").
- Marketing copy must be verifiable from published schedules; never a literal
  "10% cheaper than [competitor]" claim without live price data.
- When a brand/naming decision exists (e.g. "Valut Protect" instead of
  "escrow" in user-facing copy), encode it in the spec so agents use one term
  everywhere.

## Support files

- `scripts/launcher.py` — persistent-headed-Chromium HTTP-control launcher
  (login bridge + structure dump + screenshot + cookie backup commands).
- `references/competitor-fee-benchmark-2026-08.md` — verified competitor fee /
  protection numbers with sources (ZeusX, Eldorado, PlayerAuctions, G2G, G2A).
