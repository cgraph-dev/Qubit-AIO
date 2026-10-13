# 90 — Agent contract (7 builders + 1 verifier, parallel, collision-free)

**Status: OWNER-APPROVED 2026-10-13.** This file supersedes the partition implied by
`70-MILESTONES.md` — **logic is first-class**, not a UI/art afterthought. Where this file and
`70-MILESTONES.md` disagree on who owns a file, **this file wins**.

Every claim below was verified against source in the merge session (file:line cites). Anything
not verifiable is marked `UNVERIFIED — do not depend on it`.

---

## 0. The one rule everything else serves

> **No event, no motion, no sprite** (C7, `10-DECISIONS.md` M14). Animation state is a pure
> function of `(office_event rows, clock)` in `voicebridge/office/tower.py::state`
> (`tower.py:580`). A builder may change *what an event draws*, never *whether it draws without
> an event*.

Two mechanical consequences a builder must not break:

1. **The visual layer is timer-free.** The served modules must contain exactly one
   `setInterval` and no `setTimeout` / `requestAnimationFrame` — enforced in code (comments
   stripped) by `tests/test_office_bundle.py:252-254`. A new module adding a timer fails the
   suite.
2. **The renderer holds no write authority.** Per-module write-verb bans are enforced by
   `tests/test_office_bundle.py:239-258` and `tests/test_office_tower_plugin.py`. The Office
   reads `GET /agent/api/office/tower` and nothing else.

---

## 1. File-ownership partition (exclusive — one writer per path)

A path is owned by exactly one agent. **If your agent is not the owner, you may READ the file
but you must not WRITE it** — not a typo fix, not a comment, not a `git add`. Cross-owner edits
are the only collision class this contract exists to prevent.

| Agent | Owns (write) | Direction |
|---|---|---|
| **A1 — Asset vendor** | `scripts/vendor_office_assets.py` (new); `frontend/qubit-app/plugins/qubit-office/assets/**` (new tree); `tests/test_office_assets_vendored.py` (new) | produces the manifest every other agent reads |
| **A2 — Sprite renderer** | `frontend/qubit-app/plugins/qubit-office/sprites.js`; `frontend/qubit-app/plugins/qubit-office/atlas.js` (new module) | reads A1 asset paths; never touches `qubit-office.js` |
| **A3 — World renderer** | `frontend/qubit-app/plugins/qubit-office/iso-engine.js`; `frontend/qubit-app/plugins/qubit-office/qubit-office.js` | reads A2 globals + A5 payload fields; **sole writer of `qubit-office.js`** |
| **A4 — Agent engine** | `voicebridge/office/runner.py`; `voicebridge/office/dispatch.py`; `voicebridge/office/work.py`; `voicebridge/office/task_proposal.py`; `voicebridge/office/survival.py`; `voicebridge/office/autowork.py` (new) | consumes A5's `agent_memory` API + emits events; never creates a second memory store |
| **A5 — Memory & linkage** | `voicebridge/office/org.py`; `voicebridge/office/tower.py`; `voicebridge/office/tower_stream.py`; `voicebridge/office/room_layout.py`; `voicebridge/office/agent_memory.py` (new) | defines `agent_memory.recall/.remember`; owns the payload shape; lands FIRST |
| **A6 — Surfaces & panels** | `voicebridge/qubit_compat.py` (**office handlers only** — see §2); `voicebridge/office/escalation.py`; `voicebridge/office/business.py`; `frontend/qubit-app/plugins/qubit-office/panels/**` (new) | reuses the shared journal; no second messaging path |
| **A7 — Audio/font + serving** | `qubit_runtime/qubit_cli/web_routers/dashboard_ui.py` (the `.wav` line, **only**); `frontend/qubit-app/plugins/qubit-office/control-bar.js`; `assets/fonts/**`; `assets/sfx/**`; `scripts/build_office_bundle.py` (**the `ORDER` list only** — see §2) | makes audio/font servable; the only agent allowed to touch `ORDER` |
| **V — Verifier** (not a builder) | **new** `tests/test_office_*.py` files **only**; `scripts/render_office_screenshot.py` | writes no implementation; proof only |

### Shared files have ONE owner and a narrow edit surface

Some paths are load-bearing for several agents. The owner is fixed; the *edit surface* is
narrow, and no other agent may touch them at all:

| file | owner | the ONLY allowed change | why it is dangerous |
|---|---|---|---|
| `scripts/build_office_bundle.py` | **A7** | add exactly one `(filename, "window.QubitX")` tuple to `ORDER` (`build_office_bundle.py:52-58`) | a module in `ORDER` that fails to export its global makes the build **refuse** (`build_office_bundle.py:117-127,154-158`) — loud, good; a module on disk but not in `ORDER` is **never served** — silent, fatal |
| `voicebridge/qubit_compat.py` | **A6** | `h_office_*` handlers and any office-specific read route **only** | the file is 10,900+ lines of shared compat; an edit outside office handlers is a collision with the whole product |
| `qubit_runtime/qubit_cli/web_routers/dashboard_ui.py` | **A7** | add `".wav": "audio/wav"` to `_PLUGIN_ASSET_CONTENT_TYPES` (`dashboard_ui.py:379-386`) and nothing else | the vendored fork is manifest-tracked as `QUBIT_PATCHED` (see §4); any other edit is a recorded-divergence change, not an office change |

---

## 2. Interfaces between agents (the seams that make parallelism safe)

These are the **only** sanctioned cross-agent touch points. Everything else is private.

### 2.1 A1 → A2/A3/A6 : the asset path + manifest

- A1 produces `frontend/qubit-app/plugins/qubit-office/assets/MANIFEST.json`
  (`{path, sha256, bytes}` per file, from `40-ASSET-PIPELINE.md` §4).
- Consumers reference assets by the served URL
  `/dashboard-plugins/qubit-office/assets/<path>` — **not** a filesystem path.
- **A2/A3/A6 must not hardcode an asset filename that is absent from `MANIFEST.json`.** The
  merge-consistency test (V) asserts every asset the renderer loads is in the manifest
  (`60-WIRING-TRAPS.md` Trap 9).

### 2.2 A5 → A4 : the `agent_memory` API (A5 defines, A4 calls)

A5 lands this **before** A4 consumes it. Frozen signatures:

```python
# voicebridge/office/agent_memory.py  (A5 owns)
def recall(cfg, agent_id: int, *, kind: str = "", limit: int = 20) -> Dict[str, Any]: ...
def remember(cfg, agent_id: int, *, kind: str, text: str,
             ref: str = "", meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]: ...
```

- A4 **calls** `recall(cfg, agent_id)` and `remember(...)`. **A4 must NOT open its own store,
  table, or JSON file.** One authority per concept (repo convention); a second memory path is
  the exact split-brain bug `50-ARCHITECTURE.md` §5.5 forbids.
- Return shape is `agent_memory.py`'s to define; A4 depends only on it being a JSON-safe dict
  with no exceptions on an empty store.

### 2.3 A5 → A3 : the additive payload fields

- A3 renders **only** fields A5's `tower.state()` produces. The seam
  (`50-ARCHITECTURE.md` §5.2 seam 1): **the merge adds no field the backend cannot produce.**
- New art fields (e.g. a per-character `sprite_set`) are **additive and default-safe**: absent
  ⇒ the renderer falls back and **names the missing module/set in the page** rather than
  silently blanking (`50-ARCHITECTURE.md` §5.4; `70-MILESTONES.md` M3 task 3).
- **A3 depends on A5's fields landing first.** Until `state()` emits a field, A3 must not
  require it.

### 2.4 A5 → A2/A3 : the animation-state vocabulary

- `tower._ANIMATION` (`tower.py:46-70`) maps event kind → animation state. **The sprite layer
  renders only these states. A new pose may be added; the state machine may not**
  (`50-ARCHITECTURE.md` §5.2 seam 3).
- A5's job includes emitting the **8 mapped-but-unemitted** kinds (verified this session):
  `escalation, ledger_write, mcp_call, meeting, memory_written, note_written, skill_used,
  task_unverified`. Today they exist in `_ANIMATION` but nothing emits them, so those poses are
  unreachable. Wiring them is what makes A2's pose atlas *reachable* — this is the concrete
  proof that logic precedes art.

### 2.5 A6 → the shared journal (escalation)

- Boss chat is the **owner → agent direction of the same journal** (`escalation.py`,
  `10-DECISIONS.md` M13). A6 extends `escalation.py`; **no websocket, no MessageBus, no second
  queue** (`70-MILESTONES.md` M6 proof: `grep -c "def .*channel|MessageBus|websocket"
  voicebridge/office/*.py` must be `0`).

### 2.6 A7 → A2/A3 : the bundle `ORDER` (the one cross-cutting write)

- Any **new** JS module must be added to `ORDER` in `scripts/build_office_bundle.py:52-58` by
  **A7**, with the exact browser global it must define. The builder asserts the global exists
  (`build_office_bundle.py:104-127`) — a module in `ORDER` that fails to export fails the build.
- A7 is the sole writer of `ORDER`. **A2/A3 must request the `ORDER` line from A7; they must not
  add it themselves** (that would be two writers on one file).

### 2.7 A2 → A3 : the sprite draw interface (frozen)

- A2 owns `sprites.js` + its new `atlas.js`; A3 owns `qubit-office.js` and **calls into** A2.
- The interface is frozen as `window.QubitSprites.drawAgentSprite(ctx, …)` — the existing
  global, extended, never renamed. A3 must call **that** global, not reach into A2's internals.
- A2 keeps `bakeAgent`'s feet-anchor and `drawShadow` contract **unchanged** so the sort key
  does not move (M12, `70-MILESTONES.md` M3 task 1): the sprite sorts on its feet point's
  screen-y, exactly like the vector figure did.

---

## 3. Merge order (encode this literally)

The dependency graph, not a preference. An agent may start only when its listed predecessors
have **landed and their proof passed** (`55-VERIFICATION.md`).

```
A1 ──▶ A7(.wav line)                       [assets servable + audio/font servable]
 │
 ├──▶ A5 (agent_memory + payload + emissions)   ◀── MUST land before A4 and A3
 │        │
 │        ├──▶ A4 (autowork loop consumes recall/remember, emits events)
 │        └──▶ A3 (world renderer renders new states)
 │                 ▲
 └──▶ A2 ────────────┘  (A2 may run in parallel with A5: it edits sprites.js/atlas.js only)

A6 ── independent (escalation/business/qubit_compat/panels) — may run any time after A1
A3 must be LAST among renderers: it is the sole writer of qubit-office.js and reads
   both A2's globals and A5's fields.
V  ── continuous; writes new proofs after each milestone; final gate last.
```

**Hard ordering rules (verbatim):**
1. A5's `agent_memory` API + event emissions **land before** A4's loop consumes them.
2. A5's additive payload fields **land before** A3 renders the new animation states.
3. A1's manifest **lands before** A2/A3/A6 read any asset path.
4. A5 owns `tower.py`; **A3 depends on A5's `tower.py` writes landing first**.
5. A4 **must not create a second memory store** — it calls A5's `agent_memory`.

---

## 4. The hard "never touch" list

Binding for all seven builders and V. A violation is a merge-blocking defect, not a style nit.

**Existing tests encode the current contract. Builders must NOT edit any existing test file.**
V writes **new** `tests/test_office_*.py` files only. If an existing test breaks because a
contract genuinely moved, the fix is a **new** test plus an explicit, justified edit by V —
never a quiet builder edit. The following existing tests **will break** on the named change and
are called out so no one mistakes the red for their own bug:

| test file | will break when | why (verified) | who fixes it |
|---|---|---|---|
| `tests/test_office_bundle.py` | **any new JS module is added to `ORDER`** | hardcodes `MODULES = ["iso-engine.js","sprites.js","camera.js","control-bar.js","motion.js"]` (`:43-44`), `GLOBALS` (`:45-46`), asserts `res["loaded"] == GLOBALS` (`:151`), iterates the fixed list (`:133,182,239`) | **V** (new test) — A2/A3/A7 must not edit it |
| `tests/test_office_plugin_wiring.py` | same new-module change | hardcodes the 5 globals (`:57`, `:229`) | **V** |
| `tests/test_office_tower_stream.py` | A5 removes/renames a top-level `state()` key | `test_the_section_names_mirror_the_real_state_schema` (`:515-527`) parses `tower.py` source for `out["<key>"]`/`"<key>":` literals and asserts `set(TS.SECTIONS) <= real_keys`; `SECTIONS` (`tower_stream.py:70-82`) lists `empty, empty_reason, hud, floorplan, characters, activity, history, history_totals, grid, preview, preview_legend` | **A5** must keep every literal key present |
| `tests/test_office_hud_panels.py`, `tests/test_office_visual_scenes.py` | A5 changes the `schema` string or a mirrored field | both build literal payloads with `"schema": "qubit-office-tower/1"` (`hud_panels.py:397`, `visual_scenes.py:262`) | **A5** |

**A5 — `tower.state()` payload invariants (do NOT change):**
- The **`SCHEMA` string** stays `"qubit-office-tower/1"` (live-verified this session; asserted in
  `test_office_tower_stream.py:83`, `test_office_hud_panels.py:397`,
  `test_office_visual_scenes.py:262`).
- Every existing top-level key stays **present and literal** in the source of `state()`:
  `empty, empty_reason, hud, floorplan, characters, activity, history, history_totals, grid,
  preview, preview_legend` (`tower_stream.py:70-82`, enforced at
  `test_office_tower_stream.py:515-527`; live payload confirmed `schema,activity,characters,
  empty,empty_reason,floorplan,grid,history,history_totals,hud,motion_rule,now,ok,preview,
  read_only,walk_s`).
- `grid` **is** `floorplan["grid"]` (same object) — `test_office_tower_stream.py:326-330`.
- New fields are **additive only**; no key is removed or repurposed.

**Never touch (any agent):**
- `voicebridge/office/store.py`, `voicebridge/office/ledger.py` — the append-only store and
  ledger. A4/A5 read them; neither owns them. A schema change here is a separate workstream.
- `frontend/qubit-app/plugins/qubit-office/manifest.json` — the plugin manifest is correct as-is
  (`entry = qubit-office.bundle.js`); adding an `assets/` folder needs **no** manifest change
  (`60-WIRING-TRAPS.md` Trap 5). No agent writes this.
- `frontend/qubit-web/public/plugins/fleet.json` **and** `frontend/qubit-dist/plugins/fleet.json`
  — the office row is already identical in both (verified this session) and adding an assets
  folder needs no change. **If any agent believes it must edit fleet, stop and escalate** — the
  two copies must stay byte-equal (Trap 5 / `55-DASHBOARD-PAGE.md` deviation 3).
- Any path under `sandbox/` and any `.venv/` file — not part of the product.
- `/home/trick/Projects/Repos/refs/**` — staging only, never imported, never served
  (`10-DECISIONS.md` M5).

---

## 5. Hard facts a builder must not re-derive (all verified this session)

| fact | source |
|---|---|
| SPA API is under `/agent`: `GET /agent/api/office/tower` (401 without token); bare `/api/office/tower` 404s | live curl; `60-WIRING-TRAPS.md` Trap 1 |
| Plugin assets are served at `/dashboard-plugins/qubit-office/<file>` from `frontend/qubit-app/plugins/qubit-office/` | live: bundle 200/188073b, manifest 200, sprites.js 200; `webui.py:1610,2495-2496` |
| `/plugins/qubit-office/<file>` → 404 (dist has no office folder) | live curl; Trap 4 |
| The served allowlist is `_PLUGIN_ASSET_CONTENT_TYPES` (`dashboard_ui.py:379-386`) — **no `.wav`**; the daemon resolves `dashboard_ui` from the **local `qubit_runtime/` checkout**, not the installed copy | verified: `qubit_runtime.ensure_path()` → local root; `.wav` absent |
| Auth header for the tower read is **`X-Qubit-Session-Token`** | `webui.py:1505-1521`; live 401/200 |
| Two canvas, 0 console errors, `non_background_pct = 27.188` (881×500); baseline | live `render_office_screenshot.py --json` |
| Office test baseline: **885 passed, 3 skipped, 9 deselected** (888 collected) | live pytest run this session |
| `dashboard_ui.py` is `QUBIT_PATCHED` in the vendor manifest with a recorded `sha256` and `affected_regression_test: tests/test_qubit_runtime_vendor8u.py` | `docs/generated/QUBIT_HERMES_VENDOR_MANIFEST.json` |
| `_ANIMATION` (`tower.py:46-70`) has 15 kinds; **8 are mapped but never emitted** | source scan this session |

---

## 6. Escalation / stop rules

- A builder who needs a file it does not own: **request it, do not edit it.** The blocker is
  reported, not worked around.
- A builder who finds an existing test red on a **pre-existing** contract: report it to V; do
  not "fix" it by loosening the test.
- A change to the `SCHEMA` string, the plugin `entry`, or either `fleet.json` is **out of
  scope for all seven agents** — it needs owner sign-off (`docs/DECISIONS.md` gate).
- If the render probe regresses (see `55-VERIFICATION.md` §4), **stop and roll back to checkpoint
  `276aa3e14`** before continuing. The working render is the asset the merge is trying to keep.
