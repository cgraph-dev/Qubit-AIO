# 25 — Ported code and its provenance

The tower's visual layer adapts technique from five third-party projects.
This file exists because MIT requires the notice to travel with the code and
because AGENTS.md requires the exact repository, commit, source path,
licence and local change to be recorded **before** implementation.

**No upstream file is copied verbatim.** Each row names the file whose
technique was studied and the local function it was re-expressed in, against
our own payload. Those functions are named in the header comment of
`frontend/qubit-app/plugins/qubit-office/qubit-office.js` so the attribution
travels with the source.

## Adopted (permissive, notice preserved)

| upstream repo | branch | commit | licence | source path | local change |
|---|---|---|---|---|---|
| `ruiqili2/agent-monitor` | `main` | `bec78ad8cb6405` | MIT | `src/engine/canvas.ts` | painter's-algorithm depth sort on `col + row` -> `paintOrder()` (owner read from the checkout's own LICENSE, `Copyright (c) 2026 Ruiqi Li`; a sibling doc previously cited a different owner, corrected there too) |
| `pixel-agents-hq/pixel-agents` | `main` | `d1e007a9fdf3` | MIT | `webview-ui/src/office/colorize.ts` | HSL recolour that preserves per-pixel alpha -> shade()/tint() |
| `pixel-agents-hq/pixel-agents` | `main` | `d1e007a9fdf3` | MIT | `webview-ui/src/office/components/OfficeCanvas.tsx` | canvas camera: tile->screen with zoom, pan clamp -> iso()/clampPan() |
| `androoAGI/starnet` | `feat/harness-backend` | `54899e5ff3f2` | MIT | `frontend/agent-demo/sprites.js` | feet-anchored sprite, ground shadow, walk bob -> drawAgent()/drawShadow() |
| `androoAGI/starnet` | `feat/harness-backend` | `54899e5ff3f2` | MIT | `frontend/agent-demo/stationbake.js` | hue-preserving shade(), isometric wall-face proportion -> shade()/drawTower() |
| `FulAppiOS/Agent-Quest` | `main` | `6c32b2986096` | MIT | `client/src/components/Minimap.tsx` | world-extent minimap with rooms and markers -> drawMinimap() |
| `a16z-infra/ai-town` | `main` | `8e05997f2409` | MIT | `convex/aiTown/worldMap.ts` | tile grid with an explicit tile dimension -> TILE_W/TILE_H |
| `a16z-infra/ai-town` | `main` | `8e05997f2409` | MIT | `src/components/Character.tsx` | facing from travel direction, status marker over the head -> drawAgent()/drawMarker() |

## Read for design, code deliberately NOT used

| repo | licence | why no code |
|---|---|---|
| `rsanandres/aphae` | **none** | No licence file means all rights reserved. Its agent-needs, event-log and HUD ideas informed the surrounding panel layout; none of its GDScript was copied, adapted or transliterated. |

`FujiwaraChoki/MoneyPrinterV2` (AGPL-3.0) was assessed and **not** adopted: copyleft would reach the whole runtime. It is not present in this repo in any form.

## What was ours to add

Every upstream HUD was deliberately not ported. None of the four projects has charts, because none of them keeps a ledger. The P&L panel, the derived figures and the per-day history series are this repo's own, and every number in them is a `GROUP BY` over `office_ledger`.

