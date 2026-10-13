# 15 — Seven-agent workstreams

The owner's order: *"deploy 7 agents and start."* This file is the dispatch brief. Two review
agents run first (`45-ENHANCEMENTS.md`, `90-AGENT-CONTRACT.md`, `55-VERIFICATION.md`); the seven
builders start against this partition.

**Owner's ask (verbatim):** *"not only the ui, the logic how everything works and is linked
and results — and maybe even to improve."*

The scope-defining clause is **logic**, not art. The five repos are not art donors: their value
is their **engines** — how agents decide, remember, self-direct, and how that work links to the
visual. The owner also wants the panels/pages in scope, but logic is the priority.

**THE CENTRAL FINDING (verified this session):** the Office has a real *spine* (ledger,
envelopes, reviews, audit, dispatch with worker-picking, task claim/record/review, turn budgets,
proposals) — but **no autonomous driver and no agent memory**. Every turn today is a human
running `qubit office run` from the CLI. The `_ANIMATION` map in `tower.py` already defines
`tool_call`, `memory_written`, `skill_used`, `mcp_call` — and **nothing emits any of them**.
The tower looks dead because almost nothing happens, not because it draws badly.

`43-OPEN-ITEMS.md` A2 already records half of this: *"No autonomous loop calls `propose()`. The
org can execute work but cannot notice work for itself."* That is the gap the logic merge closes.

**Binding constraints (owner-approved, do not relitigate):**
- Merge into the existing `/qubit-office` page — not a new app.
- Full build: **logic + assets + UI**, logic first-class.
- **100 MB** asset cap.
- **No license or attribution files** — private local project (owner ruling). Do not raise it.
- Assets copied into the plugin dir, never served from `/refs`.
- Keep the z-sort (M12); no event, no motion (M14). **The autonomous loop must produce real
  `office_event` rows — it may not animate anything it cannot prove (C7).**

**Baseline is committed:** `276aa3e14` — the working tower before any borrowed asset. Every
agent's revert point.

---

## The partition (by domain, so ownership never overlaps)

| # | agent | owns (exclusive write) | deliverable |
|---|---|---|---|
| **A1** | Asset vendor | `scripts/vendor_office_assets.py`, `frontend/qubit-app/plugins/qubit-office/assets/**`, `tests/test_office_assets_vendored.py` | the vendored art tree + manifest, ≤100 MB |
| **A2** | Sprite renderer | `frontend/qubit-app/plugins/qubit-office/sprites.js`, any new atlas-loader module | real animated crew figures (M6) |
| **A3** | World renderer | `frontend/qubit-app/plugins/qubit-office/{iso-engine.js,qubit-office.js}` | tiles, furniture, labels, depth-sorted (M7, M8, M10, M12) |
| **A4** | **Agent engine** | `voicebridge/office/{runner.py,dispatch.py,work.py,task_proposal.py,survival.py}`, new `voicebridge/office/autowork.py` | **the autonomous loop** — agents wake, notice work, propose, claim, run, settle on a cadence; the proposal generator A2-of-43-OPEN-ITEMS asks for |
| **A5** | **Memory & linkage** | `voicebridge/office/{org.py,tower.py,tower_stream.py,room_layout.py}`, new `voicebridge/office/agent_memory.py` | per-agent memory + personality + relationships; emit `tool_call`/`memory_written`/`skill_used`/`mcp_call` so the tower has real state; layout=capabilities (M11) |
| **A6** | Surfaces & panels | `voicebridge/qubit_compat.py` (office handlers only), `voicebridge/office/{escalation.py,business.py}`, new `frontend/qubit-app/plugins/qubit-office/panels/**` | boss chat (M13) + the panel/page set adapted from the repos |
| **A7** | Audio/font + serving | `qubit_runtime/qubit_cli/web_routers/dashboard_ui.py` (the `.wav` line), `frontend/qubit-app/plugins/qubit-office/{control-bar.js,assets/fonts,assets/sfx}` | sound (M9), VT323 labels, the allowlist line |

**Collision rules:**
- **A1 owns the bytes.** Nobody else writes under `assets/`. A2/A3/A6 consume files by path.
- **A1 publishes `assets/MANIFEST.json` first** — A2/A3/A6 read it to know the exact filenames.
  A1 is the first agent to produce output; the others stub against the documented path scheme
  (`assets/sprites/<set>/<pose>.png`, `assets/furniture/<name>.png`, `assets/tiles/<kind>/…`).
- **A3 owns `qubit-office.js`** (the renderer entry). A2 writes only `sprites.js` and its loader;
  A3 calls into it. The interface is `window.QubitSprites.drawAgentSprite(ctx, …)` — frozen.
- **A4 owns the turn path** (`runner`/`dispatch`/`work`/proposal). **A5 owns the state the turn
  reads and writes** (`org`/`tower`/memory). They meet at one interface: A4 calls
  `agent_memory.recall(cfg, agent_id)` / `.remember(...)` and emits events; A5 defines those. A4
  must not add a second memory store.
- **A5 owns `tower.py`.** A3 must not need a payload field A5 has not added: A5 lands its
  additive fields *first*, then A3 renders them.
- **A6 touches `qubit_compat.py` only inside the office handler block** — it must not disturb
  any other route.
- **A7 owns the one server line.** Nobody else edits `dashboard_ui.py`.
- **A7-verify writes only new test files** and may read everything. Renamed to avoid confusion
  with agent A7 (audio) — the verifier is **V** below.

## The verifier (not a builder)

| agent | owns | deliverable |
|---|---|---|
| **V** | new `tests/test_office_*.py` files only, `scripts/render_office_screenshot.py` | per-milestone proofs + the regression gate against the 27.2% pixel baseline and the 885 passing tests |

## Merge order (dependency-correct)

```
A1 (assets exist) ──────┐
                        ├─▶ A2 (sprites)      ─┐
A5 (memory + events) ───┤                      │
   lands its fields 1st ├─▶ A3 (world art)    ─┼─▶ V (verify each)
                        │                      │
A4 (autonomous loop) ───┘   needs A5's memory  │
                        └─▶ A6 (panels)       ─┘
A7 (audio/font/serving line)
```

**Hard ordering:** A5's memory API and event emissions land **before** A4's loop consumes them,
and **before** A3 renders the new states. A1's manifest lands before A2/A3/A6 read asset paths.
Everything else runs in parallel.

---

## The builder brief (shared preamble — every agent gets this)

> **Project:** `/home/trick/Projects/Repos/qubit-voicebridge` — a personal AI-agent dashboard.
> The **Office** is a dashboard tab (`/qubit-office`) rendering an isometric tower where AI
> agents are characters. It reads ONE endpoint: `GET /agent/api/office/tower` (the `/agent`
> prefix is required; bare `/api/office/tower` 404s). The backend
> (`voicebridge/office/*.py`, 26 modules, 10,337 LOC, append-only SQLite ledger) computes every
> animation state as a pure function of `(office_event rows, clock)` — the C7 rule: **no event,
> no motion**. The frontend is `frontend/qubit-app/plugins/qubit-office/`: `qubit-office.js`
> (renderer + React shell) plus `iso-engine.js`, `sprites.js`, `camera.js`, `control-bar.js`,
> `motion.js`, concatenated by `scripts/build_office_bundle.py` into `qubit-office.bundle.js`
> (ONE `<script>` per manifest entry — a new module must be added to that script's `ORDER`).
>
> **Reference art/code** is in `/home/trick/Projects/Repos/refs/` (starnet, agent-quest,
> pixel-agents, ai-town, agent-monitor). Copy assets INTO
> `frontend/qubit-app/plugins/qubit-office/assets/` — never serve or import from `/refs`.
>
> **Run tests:** `.venv/bin/python -m pytest tests/test_office_*.py -q` — the **system python3
> has no pytest**, always use `.venv/bin/python`. **Verify the live page:**
> `.venv/bin/python scripts/render_office_screenshot.py --json` (real Chrome; reports
> `non_background_pct`, canvases, `console_errors`).
>
> **Baseline:** commit `276aa3e14` is the rollback point. 885 office tests pass; the live render
> shows **27.2% non-background pixels** on the largest canvas (the tower is mostly empty — the
> thing this merge fixes).
>
> **Hard rules:** no license or attribution files (owner ruling). No new server, store, event
> stream, or confirmation path. No background timers — motion comes only from events. Do not
> touch another agent's files (see the partition table).
