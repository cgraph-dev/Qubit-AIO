# The Merge — combining `/refs` into the Qubit Office

**Owner ask (2026-10-13):** *"those are my repos in the refs folder, I want to merge them
into something better — the UI, the logic, the assets, everything. Target: merge into the
existing `/qubit-office` page. Scope: full build."*

Decisions recorded: `2a 3d` (`docs/game-office/merge/10-DECISIONS.md`) plus **dropping
`aphae`** (`M0`).

**Read order:**

| File | What it settles |
|---|---|
| `00-INDEX.md` | This page — what the merge is, and the one-paragraph truth about each source |
| `10-DECISIONS.md` | M1–M14: every structural ruling, each with the alternative it beat |
| `30-SOURCE-INVENTORY.md` | Per repo: exact paths, file counts, what is taken, what is rejected |
| `40-ASSET-PIPELINE.md` | How art gets from `/refs` into the served plugin bundle |
| `50-ARCHITECTURE.md` | Where each borrowed system lives in the tower, and the seams |
| `55-VERIFICATION.md` | Per-milestone proofs, regression gates, and how to prove no regression against the two anchors |
| `60-WIRING-TRAPS.md` | The serving/allowlist/build facts that will bite implementation |
| `70-MILESTONES.md` | M0–M6 with one behavioral proof each |
| `80-RISKS.md` | R1–R9, the thin defence, and what is deliberately not done |
| `90-AGENT-CONTRACT.md` | The 7-builder + verifier partition: file ownership, interfaces, merge order, and the hard "never touch" list |

---

## What the merge is

The six folders in `/home/trick/Projects/Repos/refs` are **copies of five upstream projects**
(six before `aphae` was dropped). None of them is mergeable *as a program* — each is a
different stack (Godot, Convex+PixiJS, VS Code extension, Next.js dashboard, Tauri harness)
and the Office is a JavaScript canvas plugin inside the Qubit dashboard.

So "merge" here has a precise meaning, and it is the meaning the plan uses everywhere:

> **Take the art, the proven render techniques, and the backend *rules* from the
> five sources, and fold them into the one tower that already renders and already reads a
> real ledger.**

It is not a monorepo union and not a new app. It is the Office absorbing five projects'
worth of parts — which is what `docs/IDENTITY.md` says Qubit does.

---

## The one-paragraph truth about each source

| source | what it actually gives the merge | what it does not |
|---|---|---|
| **starnet** | **The bulk of the value.** 50 animated crew sprite sets (8-directional rotation, walk, sit, type), 48 furniture PNGs, 12 UI sounds, the VT323 font, and the proven "layout *is* runtime state" law | Its Tauri/desktop harness; its own station art style |
| **agent-quest** | **207 Tiny Swords tiles/buildings/UI**, and the best single idea in any source: *the building an agent walks to **is** what it is doing* | Its fantasy theme; its session-log scraping |
| **pixel-agents** | The **occlusion law** (single-array z-sort on tile bottom) and **areas** (paint a region, bind a team) | Its VS Code extension form; its Claude-Code hook |
| **ai-town** | The **three-layer architecture split** (simulation engine + shared state + vector search) and "world pauses when nothing watches" = our C7 | Convex (a cloud dependency); its socialize focus |
| **agent-monitor** | **Boss chat** (broadcast + per-agent direct) and **autowork** on a ticker | Its OpenClaw gateway coupling |
| ~~**aphae**~~ | **none — dropped** | nothing (M0) | — |

**`aphae` is removed from `/refs` and from this plan.** It ships no license file at any
upstream path, and the owner ruled it out of the merge (M0).

---

## Why this is a spec set, not a task list

The plan skill's rule: a product-scale ask ("combine these repos into something better")
gets a multi-document specification, because bite-sized tasks over an unsettled design get
rewritten. The Office already has 12,787 lines of docs in `docs/game-office/`; this set
**does not replace them**. It is the merge-specific layer: source inventory,
asset pipeline, architecture seams, and milestones. Where it touches an existing doc it
cites that doc rather than restating it.

## Status

**PROPOSED — not yet approved.** Every decision in `10-DECISIONS.md` is a proposal until the
owner stamps it. Nothing here is built.
