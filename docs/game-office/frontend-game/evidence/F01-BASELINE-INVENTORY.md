# F01 — Baseline archive, asset inventory and journey census

**Status: complete 2026-10-14.** Recorded facts only; no repair performed (repairs are F02/F03).

## 1. Archive manifest

Office-owned frontend files captured to `baseline-archive/`, byte-identical to the source tree at
2026-10-14 11:47. Rehearsed restore: `cp baseline-archive/* frontend/qubit-app/plugins/qubit-office/`
returns the tree to these hashes exactly, because the archive is a flat copy of that flat directory.

| File | Bytes | SHA-256 (first 12) |
|---|---|---|
| `iso-engine.js` | 35,370 | `85387055efec` |
| `atlas.js` | 25,859 | `764da19b6883` |
| `sprites.js` | 50,806 | `fe4b45f63a1f` |
| `camera.js` | 17,455 | `322f16dd9a30` |
| `control-bar.js` | 17,383 | `bf9be4df2007` |
| `motion.js` | 26,300 | `179ed5b0043e` |
| `qubit-office.js` | 75,699 | `ced301de2b5c` |
| `qubit-office.bundle.js` | 251,473 | `8006283bf27f` |
| `manifest.json` | 479 | `23d8aadaf9f7` |

**Explicitly out of scope for this archive:** `frontend/qubit-web/package-lock.json`,
`frontend/qubit-web/public/plugins/fleet.json` and 20+ unrelated modified files in the worktree.
Committing unrelated work is forbidden by the execution rule; the archive contains only
office-owned files.

## 2. Module/global order — the seam the rebuild must preserve or supersede

The bundle is a dependency-ordered concatenation (`scripts/build_office_bundle.py:51-65`). Each
module defines exactly one global, and a module **must** be defined before its consumer:

| Order | File | Global |
|---|---|---|
| 1 | `iso-engine.js` | `window.QubitIso` |
| 2 | `atlas.js` | `window.QubitAtlas` |
| 3 | `sprites.js` | `window.QubitSprites` |
| 4 | `camera.js` | `window.QubitCamera` |
| 5 | `control-bar.js` | `window.QubitControlBar` |
| 6 | `motion.js` | `window.QubitOfficeMotion` |
| 7 | `qubit-office.js` | *the renderer* — reads the globals above |

**Load-bearing trap for the rebuild:** `sprites.js` reads `root.QubitAtlas` at load time
(`build_office_bundle.py:55-56`), so `atlas.js` must precede it. Any bundler the rebuild introduces
must preserve or explicitly replace this ordering rule; a naive ESM graph that reorders these two
will silently null the atlas.

Bundle freshness at archive time: `python3 scripts/build_office_bundle.py --check` →
`OK: qubit-office.bundle.js is up to date (8006283bf27f, 5406 lines)`.

## 3. Asset inventory

**606 files** under `frontend/qubit-web/public/plugins/qubit-office/assets/`, all under
`assets/sprites/`. Seven sprite sets:

`approved_android`, `approved_animegirl`, `approved_astronaut`, `approved_blank_amber`,
`approved_blank_blue`, `approved_blank_green`, `approved_blank_red`.

Live probe (`evidence/probe.json`) reports `atlas.ready: true, atlas.gaps: []` — the atlas loads
with no gaps. Runtime module globals observed live: `QubitIso`, `QubitAtlas`, `QubitSprites`,
`QubitCamera`, `QubitControlBar`, `QubitOfficeMotion` (all six, matching the source order above).

## 4. Broken-journey census (probe-confirmed, not source-inferred)

From `evidence/probe.json` — these are live browser results:

| Journey | Result | Reading |
|---|---|---|
| Canvas attach input listeners | `canvas_event_listeners: []` | **broken** — the canvas has zero listeners |
| Wheel zoom changes pixels | `wheel_changed_pixels: false` | **broken** |
| Drag changes pixels | `drag_changed_pixels: false` | **broken** |
| Floor button changes pixels | `floor_button_changed_pixels: true` | works |
| P&L button opens chart | `pnl_button_opens_chart: true` | works |
| Page exceptions | `errors: []` | none |
| Failed requests | `failed_requests: []` | none |

`controls.json` must be read alongside this for the Fit/Reset-after-Zoom failure; the probe above
does not test it and this document does not claim it beyond what `00-AUDIT.md` finding 2 already
records.

## 5. What F01 does NOT establish

- No repair. The dead listeners are still dead.
- The fixture is a **recording** — see `fixtures/PROVENANCE.md`. It is never live.
- `docs/STATUS.md` remains absent; see `frontend-game/README.md` § Authority.
