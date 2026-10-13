# 80 — Risks and Licenses

Honest list. Each risk has a mitigation that is already in the design, or an owner decision it
needs.

## License

| Source | Code license | Brand | Usable? |
|---|---|---|---|
| starnet | MIT | Name/logo/art **not** licensed | Code yes, with our own name/art. We take ideas only. |
| pepeclaw | MIT | — | Ideas + MIT code usable with attribution. |
| pixel-agents | MIT | — | Ideas + MIT code usable with attribution. |
| Agent-Quest | MIT | — | Ideas + MIT code usable. CC0 Tiny Swords assets are safe to bundle. |
| ai-town | MIT | — | Ideas + code usable. |
| agent-monitor | MIT | — | Ideas + code usable. |
| **aphae** | **All rights reserved** | — | **Ideas only. No code, no assets, no sprites.** |
| Tripo assets | Vendor terms per tier | — | Paid-tier output only; recorded per asset (D10). |

**Rule for the repo:** nothing from any reference is copied until its exact upstream commit,
source path, license, and local changes are recorded — the repo already requires this, and this
project is a magnet for it. aphae is a hard no on code.

## Risk register

| # | Risk | Severity | Mitigation in the design |
|---|---|---|---|
| R1 | **The org never makes money and just burns capital** | High | Bootstrap is 5 seats (~$105/mo — recomputed, `45-ECONOMY.md`). Kill criteria required before any business goes live. The CEO must produce a revenue path before the org scales past bootstrap. |
| R2 | **Legal exposure from agent-authored publications** | High | Publishing is a **mandatory review** under every posture (D22) — the org cannot publish without the owner seeing it. Every business thesis must state its regulatory exposure or it cannot go live. Owner holds legal + compliance by design. |
| R3 | **Agents optimize into ToS violations** (spam, scraping, dark patterns) | High | **D25 removed the reputation brake, so this risk is now carried by D29 item 6 alone:** acting against a platform's published terms is a hardline denial, and platform bans/refunds are fed back to the daily reflection as evidence. Publishing is still a mandatory review (D22). This is the plan's thinnest defence and a reviewer should probe it (`05-AMENDMENT-LOG.md`). |
| R4 | **Runaway inference cost** | High | Three hard caps: per-agent daily, org daily, concurrency. Checked at admission, not after. |
| R5 | **The reflection loop eats the budget** | Medium | One turn/day/agent, capped output (one change), cheap tier. ~$27/mo at 71 seats (recomputed 2026-10-12 — see `45-ECONOMY.md`). |
| R6 | **The tower lies** (shows work that isn't happening) | Medium | D14: render only from `office_event`. No event, no animation. |
| R7 | **Tripo asset license gap** | Medium | D10 build gate: refuses to ship a non-commercial-tier asset into a revenue surface; records account + tier + date per asset. |
| R8 | **Attribution is wrong → the ratchet trains on noise** | Medium | Artifacts carry `run_id` → ledger rows; an entry without artifact-backed score stays inactive. |
| R9 | **Provider/model pricing changes break the model** | Medium | Costs are recorded from real usage, not estimated. Budget caps are in cents, so a price change shows up as fewer turns, not as a surprise bill. |
| R10 | **The office grows into a second agent runtime** (split-brain) | Medium | D9: no new loop, store, registry, or skill format. Every capability call goes through the existing seam. Reviewed at each milestone. |
| R11 | **Termination becomes punitive/nondeterministic** | Low | Explicit runway formula, manager notified first, human override always available. Terminated ≠ deleted. |
| R12 | **Payment processing needs the owner's legal identity** | Medium | Owner handles legal/compliance and fee setup — stated up front, and every revenue channel records which owner-held account it settles to. |
| R13 | **Multi-currency + tax on real sales** | Medium | Ledger stores `currency` per row from day one; tax is owner-side. Do not retrofit currency later. |
| R14 | **The owner can't see the org's real state at a glance** | Low | The success criterion in `00-IDENTITY.md` is literally a ten-second read: which floor carries the company, which floor is a cost center. |

| R15 | **The chain of command is nominal** (supervisors recorded, but nothing rates) | Medium | D36/D39: a quarterly review is refused at write time without artifact evidence, and `office_review.rating` is derived from ledger-backed metrics per seat |
| R16 | **The auditor becomes a second CEO, or a rubber stamp** | Medium | D40: the auditor can read everything and change nothing; it is rated on missed findings, and its own rating is written by the CEO with the owner's countersign |
| R17 | **A budget envelope removes the pressure that makes the org real** | Medium | D37 keeps the hard cap in the runner (C8) and adds envelope utilisation to the manager's rating — under-spending is visible, not rewarded |
| R18 | **Nine directors on best-tier models make the CEO's floor the new bottleneck** | Medium | D37 reserves ~20% for the CEO's fast-track, and D38 limits the owner to four classes of item; the directorate is flat (all report to the CEO) by design so cross-portfolio sequencing stays one conversation |
| R19 | **Performance review drifts into theatre** (ratings without consequence) | Medium | Every rating carries an `action`, two PIPs a year terminate, and an upheld appeal is itself a finding about the reviewer |

## Explicit non-goals (v1)

- ~~No editor (D12).~~ **Stale — D12 was flipped: the editor is in scope as M7** (`75-EDITOR.md`).
- No mobile/narrow-viewport tower (desktop-first; the dashboard's existing mobile support is not a
  tower requirement).
- No multi-owner, no multi-tenant, no cloud deployment.
- No voice control of the tower beyond what the dashboard already exposes.
- No second memory/vector store.
- No free-tier Tripo assets.

## Open questions for the owner

1. **Which Tripo product line** — Studio subscription or the developer API? They are separately
   billed and Studio credits ≠ API credits. The pipeline design assumes the API for scripted
   generation and Studio for hand-authored hero assets.
2. **What legal entity sells?** The plan assumes the owner is the counterparty on every sale.
   If a company exists, the ledger should carry its identity.
3. **First business vertical** — the plan deliberately does not pick one. The first **scraper /
   analyst / researcher** wave (`13-PIPELINE.md`) proposes three with theses; the owner picks. Everything downstream (art, prompts, channels)
   depends on that pick.
4. **Payment rail** — which processor, and is it the owner's existing account or a new one.
   Blocks R12 and the first real revenue row.
