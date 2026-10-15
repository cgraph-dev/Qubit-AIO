# Qubit Office — full game frontend audit and plan

**Status: audited baseline + proposed frontend specification. No game implementation performed.**

> **Annotation (2026-10-14, later session):** the "no game implementation performed" line is
> **historical and no longer true**. F01–F09 and F12 are built and their suites pass
> (`tests/test_office_game_*.py` → **204 passed**, 0 skipped); the Office tab renders from the
> hand-built bundle `qubit-office.game.js`. See `../frontend-game/60-IMPLEMENTATION-PLAN.md` per-packet
> STATUS blocks and `docs/OPEN_THREADS.md` § 4.14. Separately, the **vendored third-party app**
> (`habblaud`, MIT, commit `4ccce2537472`) is served at its own route
> `/plugin-apps/qubit-office/habblaud/` — a parallel implementation, **not** the default tab.
**Scope:** frontend only; the owner implements backend additions. Execute sequentially, on Sol 6.1, without delegated agents. Do not commit, push, install dependencies, spend on art, or restart services under this plan without the relevant approval.

## Recommendation

**Owner verdict: the current frontend does not work as the requested product and looks wrong.**
Isolated working buttons, loaded assets and backend records are not a usable office
game. Do not reclassify this as a polish-only task or present incremental fixes as
meeting the brief. Rebuild the frontend experience; preserve only independently
useful internals behind the new world/interaction/UI architecture.

Replace the current page composition with **one cohesive Japanese-anime-inspired 2.5D office game** at `/qubit-office`: an exterior district, buildings you can enter and leave, lobby, elevator/stairs, distinct floors and rooms, original characters, portrait conversations, and a restrained ARPG/gacha-style HUD. Preserve the existing ledger, org, auth, sessions, review and operation authorities.

This is a game **presentation and interaction layer over a real agent corporation**, not a fake economy, combat simulator, gacha casino, or five embedded reference apps. Gacha influence means the polish of character screens, tabs, settings, transitions and presentation—not random paid recruitment.

## Read order

| File | Purpose |
|---|---|
| [00-AUDIT.md](00-AUDIT.md) | Confirmed failures, source-confirmed risks, missing features and doc drift |
| [10-DECISIONS.md](10-DECISIONS.md) | Owner scope, proposed mechanisms, alternatives and amendment boundaries |
| [20-GAME-DESIGN.md](20-GAME-DESIGN.md) | Enter/exit buildings, floor travel, character interactions and all panels |
| [30-ARCHITECTURE.md](30-ARCHITECTURE.md) | Frontend modules, renderer choice, lifecycle and exact integration boundaries |
| [40-ART-AND-ASSETS.md](40-ART-AND-ASSETS.md) | Japanese/anime visual direction, backgrounds, cast, furniture, UI kit and sound |
| [50-BACKEND-CONTRACT.md](50-BACKEND-CONTRACT.md) | Existing data versus proposed owner-owned interfaces; honest unavailable states |
| [60-IMPLEMENTATION-PLAN.md](60-IMPLEMENTATION-PLAN.md) | Sequential packets, exact proposed paths, dependencies and proofs |
| [70-ACCEPTANCE-AND-HANDOFF.md](70-ACCEPTANCE-AND-HANDOFF.md) | Observable completion criteria, commands, risks, stop conditions and handoff |
| [evidence/](evidence/) | Actual live browser results, screenshots and captured tower data |

## The findings that change the plan

1. **Drag, wheel and character selection are dead on initial load.** Canvas listeners are attached in a mount-only effect while the loading branch renders no canvas. Live instrumentation found zero canvas listeners.
2. **Fit and Reset fail after Zoom.** Their target is overwritten by every redraw, and the force-fit flag is not the flag the redraw reads.
3. **The sprite renderer drops semantic state.** The atlas supports working/meeting/etc.; the caller supplies only walking/progress, so the atlas falls back to walking or idle.
4. **A poll is not a game loop.** Full snapshots every 400 ms tie drawing to network delivery and still cannot produce smooth game navigation.
5. **The tests over-restrict the product.** They ban every write and frame animation, whereas C4 allows staged operations and C7 forbids fabricated work, not camera motion or a visible request-loading indicator.
6. **Buildings, elevators, room interaction, conversations and settings are absent from the current office UI.** Existing data is a tower snapshot, not a full game-world contract.

## Current evidence, not prior completion claims

- Existing doc corpus: **62 files / 15,650 lines** before this new set; computed from disk.
- Office frontend: **7 JS source modules / 5,347 lines**, excluding generated bundle.
- Live snapshot: **3 floors / 10 characters**. Six supporting globals loaded; atlas ready; zero page exceptions and zero failed requests during the baseline probe.
- Working baseline controls: floor buttons, Zoom button and P&L. Broken: direct canvas inputs, Fit/Reset.
- Bundle freshness: `build_office_bundle.py --check` returned OK, 5,406 generated lines.
- At DPR 2 the game canvas still used an approximately 1x backing resolution.
- The minimap is **not blank**: direct pixel inspection found rendered content. Do not repeat the image-analysis model's contrary description.

The live baseline is captured in `evidence/probe.json`, `evidence/controls.json`, `evidence/tower.json`, and the PNGs. Fixtures copied from these files are recordings, not proof of live backend behavior.

## Authority and proposed changes

This set does not silently rewrite `../10-DECISIONS.md`, `../95-CONTRACT.md`, or any frozen contract. Current owner instructions establish frontend-only scope, Japanese/ARPG/gacha styling, building entry/exit and floor travel, and sequential execution. Mechanism decisions below are **PROPOSED** unless explicitly marked owner-directed.

Before implementation, append a frontend amendment distinguishing authoritative state from presentation, and reconcile restrictive tests with the approved product scope. **Done 2026-10-14** — see `../05-AMENDMENT-LOG.md` § Frontend amendment; its contract/test-change list is T1–T6.

Repo-context references to top-level `docs/STATUS.md` do not resolve in this checkout — **confirmed absent 2026-10-14, not merely unread**. The authorities that DO resolve here are `../05-AMENDMENT-LOG.md`, `../95-CONTRACT.md`, `../90-MILESTONES.md`, `../00-IDENTITY.md`, and `../README.md` (the doc-set index). **No substitute STATUS was invented.** Open until the owner names the governing authority or `docs/STATUS.md` is restored.

The 40 findings are **not** independently re-verified here. They carry the provenance `00-AUDIT.md` § Bounds states: browser-probe results and source findings are labelled separately, and nothing in this set upgrades a source finding into a probe result.

Planning workflow: the installed `plan` skill incorporates writing craft from obra/superpowers. No standalone Superpowers skill was installed or used to spawn agents.

## What can happen next

Review the numbered decisions, then authorize one frontend vertical slice: **enter headquarters → use elevator → select a character → open a conversation panel → exit**. Build this before expanding the complete cast and department art. Backend integration completion remains contingent on the owner's declared interfaces; a labelled preview is not a live capability.
