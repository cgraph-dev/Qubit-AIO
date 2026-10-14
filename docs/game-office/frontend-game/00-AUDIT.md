# Baseline audit — mistakes, gaps and what to retain

**Classification:** LIVE = reproduced against localhost; SOURCE = established from code; GAP = absent from the inspected office surface; DOC = spec inconsistency; RISK = failure shape requiring a targeted reproduction. This is a comprehensive audit of the requested frontend and its seams, not a guarantee that every backend defect has been found.

## Scope and evidence

Inspected the existing `docs/game-office` set including merge/corrections, current renderer/control/camera/atlas/sprite seams, tower payload construction, plugin host, current manifests and build pipeline; rechecked key mechanisms in all five local refs. The earlier reference audit remains corroborating evidence, not independent verification of every upstream claim.

Live page: `http://127.0.0.1:8890/qubit-office`. Browser: installed Chrome through Playwright, desktop 1600×1000 and mobile 390×844. A second probe used DPR 2. The first input probe froze subsequent responses to its **actual received snapshot**, explicitly to distinguish input changes from polling. No office mutation or agent run was invoked.

## Functional and architectural findings

| ID | Type / priority | Finding and evidence | Required treatment |
|---|---|---|---|
| A01 | LIVE / critical | Zero canvas listeners; wheel and drag do not change pixels. `qubit-office.js:1294-1413` mount-only effect returns when ref is null; loading branch `1424-1428` has no canvas. | Bind on actual canvas mount, dispose on unmount/recovery; test loading→ready and error→ready. |
| A02 | LIVE / high | Zoom changes pixels; Fit and Reset do not restore the initial frame. `qubit-office.js:1227-1234,1280-1283`; fit target overwritten every draw; `_auto` set but redraw checks `_autoZoom`. | Separate canonical fit from current camera and perform explicit refit. |
| A03 | SOURCE / high | Selected inspector stores a character object (`1148-1149,1397,1524-1546`), not a stable ID resolved against new snapshots. Once selection works, inspector can remain stale. | Store `selectedAgentId`; derive current state every revision; close/tombstone on removal. |
| A04 | SOURCE / high | `drawAgent` omits `opts.state` (`qubit-office.js:632-633`). `sprites.js:938-946` explicitly falls back to only walking/idle without it. | Carry semantic state, appearance ID and animation timing all the way to the atlas. |
| A05 | LIVE / medium | DPR=2 but backing canvas is 881×500 and CSS box 883×502. | DPR-aware renderer, tested CSS-space input transforms; cap effective DPR by quality tier. |
| A06 | SOURCE / high | Poll interval 400 ms (`qubit-office.js:144,1195-1198`) means 2.5 full reads/sec/page. Requests are neither serialized nor aborted. | One snapshot/event adapter; no-overlap fallback polling, visibility suspension and measured backoff. |
| A07 | SOURCE / high | Any fetch failure drops `data` to null (`1176-1192`), replacing the world. No retained snapshot + stale/reconnecting state. | Keep last known data with explicit stale age; never present it as fresh. |
| A08 | SOURCE / medium | Fetch promises have no generation fence/unmount cancellation (`1176-1198`). Slow responses can win out of order. | Abort obsolete loads; monotonically apply revision/cursor; cleanup all resources. |
| A09 | SOURCE / medium | Camera width changes only on redraw, capped at 1200; height fixed 500 (`1209-1215,1519`). No container/DPR lifecycle observer. | Full-height game host and container-driven canvas sizing. |
| A10 | SOURCE / medium | Wheel zoom is not cursor-anchored; pointer drag has no movement threshold or capture (`1298-1318,1399-1404`). | Distinguish click/drag/touch/pinch; reject secondary buttons; suppress click after drag. |
| A11 | RISK / medium | Whole-building picking inversely projects at z=0 then filters by cell distance (`1331-1396`), which can reject an upper-floor figure before screen-distance tie-breaking. | Ray/geometry hit-test per shown floor; reproduce stacked same-cell cases. |
| A12 | SOURCE / high | `tower.floorplan:168-180` wraps room coordinates modulo 3×2. Twelve input rooms yield only six unique slots. | Owner supplies arbitrary floor geometry; view must not repeat layout modulo fixed room slots. |
| A13 | SOURCE / high | `tower.walk_for:335-343` produces rounded integer straight-line points; no collision-aware route or cross-floor portal segment. `state:651-655` changes only x/y. | Authoritative multi-segment route contract; never infer agent travel through walls or floors. |
| A14 | SOURCE / high | Unknown fresh event maps to `working` (`tower.py:231-232`); direct function probe confirmed it. | Owner changes fallback to unknown/neutral; frontend unknown state cannot imply work. |
| A15 | SOURCE / medium | Memory and dossier already arrive (`tower.py:683-688`, captured JSON) but inspector never renders them (`qubit-office.js:1524-1546`). | Build evidence-aware character tabs before requesting redundant fields. |
| A16 | SOURCE / medium | Two P&L controls rendered (`1482-1514`); one Zoom control only zooms in (`control-bar.js:260-269`). | One dock action per destination; explicit +/−/fit/reset. |
| A17 | SOURCE / medium | Color, dimensions, panels and fonts are inline/hard-coded (`styles:1119-1138`, stat `331-337`). | Scoped three-layer tokens and shared panel primitives. |
| A18 | GAP / high | Office canvas exposes no keyboard navigation/accessible roster; no focus target or character action controls (`1516-1560`). | Accessible DOM equivalents, keyboard map, focus restoration and screen-reader text. |
| A19 | GAP / high | No entry/exit scenes, lobby, elevator/stairs, door portals or room navigation. Current floor buttons simply select a rendered deck (`1496-1510`). | Build scene/navigation graph, not a reskinned dropdown. |
| A20 | GAP / high | No character conversation, boardroom request answer UI, editor, equipment screens or game settings; control table permits four view verbs and refuses the rest (`control-bar.js:90-162`). | New complete interaction shell via canonical staged actions, not direct writes. |
| A21 | SOURCE / medium | World loader has no image `onerror`, timeout or actionable retry (`iso-engine.js:657-666`); atlas gaps are not surfaced alongside MODULE_GAPS (`qubit-office.js:119-124,1463-1466`). | Visible asset diagnostics and retry; distinguish absent asset from renderer failure. |
| A22 | SOURCE / medium | Furniture dressing is derived from rectangle/seats (`iso-engine.js:603-637`), not placed-object rows. | Mark decoration separately; never treat a drawn shelf/desk as a real capability grant. |
| A23 | LIVE / design | Mobile canvas starts at y≈541 and is 502 px high in an 844px viewport. Document width fits; that does NOT make world controls usable. | Compact top HUD, bottom sheets, safe areas and deliberate game viewport allocation. |
| A24 | SOURCE / high | `voicebridge/office/tower_stream.py:12-35` implements pure fingerprint/delta helpers, explicitly leaving the full handler untouched. No production consumer of `tower_stream` was found in the targeted voicebridge search. | Treat incremental transport as an integration gap, not as a shipped live subscription. Owner wires the existing helper/transport; frontend keeps serialized snapshot fallback. |

## Contract and documentation mistakes

| ID | Type | Evidence | Correction needed |
|---|---|---|---|
| D01 | DOC | C4 permits staged ops (`95-CONTRACT:91-104`), but HUD doc `28-HUD-PANELS:361-378` and test `test_office_tower_plugin:225-240` forbid all write verbs and all other API paths. | Restore no-direct-write semantics; allow canonical staged interaction gateway. |
| D02 | DOC | C7 forbids fabricated activity (`95-CONTRACT:200-214`); tests `test_office_tower_plugin:244-273` prohibit RAF, CSS motion and even loading UI. | Separate real work state, bounded route presentation, view navigation and ambient decoration. |
| D03 | DOC | Approved D11/architecture still say Phaser (`10-DECISIONS:157-176`, `20-ARCHITECTURE:7-10`); actual renderer is Canvas2D; `70-ASSETS:3-6` corrects only its introduction while later sections still describe Phaser. | State actual baseline; record chosen engine migration explicitly. |
| D04 | DOC | HUD claims roster already present (`28-HUD-PANELS:238-245`), but full roster is absent from current render; only unseated badges exist (`qubit-office.js:1587-1603`). | Correct coverage matrix and build real roster. |
| D05 | DOC | HUD progress is both carried and absent (`28-HUD-PANELS:122,138-145,323,399`); code carries it (`tower.py:663-664`). | Remove contradictory gap claims without redoing the fixed backend. |
| D06 | DOC | HUD says memory/dossier absent (`28-HUD-PANELS:298-300,390,404-406`); live capture and code include both. | Gap is frontend display, not an absent field. |
| D07 | DOC | Merge says eight animation kinds never emitted (`merge/90-CORRECTIONS:146-155`); current emitters exist in agent_memory, escalation, runner and receipts. `meeting` was found only in mappings/documentation in the targeted source scan. | Recompute reachability; do not repeat the historical count or claim every pose is live. |
| D08 | DOC | Main contract header says C1–C32, but C33 exists; declaration IDs are contiguous with no duplicates. | Correct range, not rules. |
| D09 | DOC | Baseline main README did not directly index 29 markdown files including detailed audits and merge companions; count excludes this new spec set. | Explicit linked sub-index/read order; not all need to be duplicated in one table. |
| D10 | DOC | Handoff says M2 “After M1” (`99-HANDOFF:22`), while current org/backend modules exist and other docs say built. | Separate present source, focused proof, live integration and future work. |
| D11 | DOC | Merge equates watcher pause with C7 (`merge/10-DECISIONS:149-155`). Absence of viewers and absence of work events are different conditions. | Suspend presentation/network on visibility change, never pause real workers implicitly. |
| D12 | DOC | Legacy verification requires an increasing non-background pixel share (`merge/55-VERIFICATION:96-109,194-202`). Filling the canvas proves neither legibility nor interaction. | Keep asset/network proof, add screenshot-region checks and actual interaction journeys. |
| D13 | DOC | Root context names top-level STATUS/PLAN/BRAND/DECISIONS/README files absent in this checkout. | Reconcile documentation authority before treating absent files as active instructions; no invented replacement. |
| D14 | DOC | Static source bans/test harness arg limits drive module placement (`iso-engine.js:643-651`) and implementation workarounds. | Behavioral browser/engine tests; retire byte-pattern contracts only with an approved amendment. |
| D15 | DOC | Main README says the Office page is built (`README.md:116-121`) and then says “No tower page yet” (`:134`). The current live browser proves the page exists, but not the claimed full capability completion. | The new index note labels these as historical snapshots; the underlying legacy lines remain for a dedicated reconciliation pass. |
| D16 | DOC | Main README headline says 16 modules (`:122`); the current office backend has 28 Python modules. Its 221 passing-test claim was not rerun in this audit. | Regenerate inventory totals; label historical test evidence instead of asserting a current pass total. |

## Reusable internals — not a working game frontend

- **Owner acceptance is failed.** The current dashboard composition is the wrong product presentation. None of the isolated successes below proves entry/exit, playable interiors, floor travel, conversation or a cohesive Japanese/anime game UI.
- Same authenticated route and plugin registration, six loaded module globals, decoded atlas, successful asset requests in the live baseline.
- Fresh generated bundle; feet anchoring, tint support and depth sorting exist. Do not call them unimplemented merely because older ledgers do.
- Floor buttons and local P&L work. Minimap has rendered pixels; it is not a blank placeholder.
- Backend provides honest money folds, seat hierarchy, current events, memory/dossier summaries, preview distinction and known null cases.
- The owner's primary request is UI. Backend geometry/events/mutations are handoff requirements, not license to rewrite the backend here.

## Visual/design failure diagnosis

The current page presents stacked room plates in a small bordered canvas surrounded
by stats, ordinary view buttons, a fixed side rail and technical event text. It does
not present a district, a building entrance, an inhabited lobby or a playable floor.
The cast is seen as small world markers rather than coherent portrait/world identities;
the shell has no shared ARPG panel, dialogue or settings language. The backdrop is a
plain void (`qubit-office.js:786-789`), not an authored location. Room/furniture dressing
is a procedural art assembly (`iso-engine.js:603-637`), not a finished office environment.

Rebuild priorities are: spatial world/navigation first; readable matching cast and
interiors second; unified game HUD/settings/dialogue third; then the complete
operational surfaces. Merely adding glow, more sprites or new boxes to the old page
does not address any of those failures. The first art/navigation slice needs explicit
owner visual acceptance before scaling production.

## Five reference repos: take mechanisms, not five applications

| Source | Rechecked local seam | Contribution | Do not inherit |
|---|---|---|---|
| Agent Quest | `client/src/App.tsx:86-146`; game scene/entity APIs in recovered audit | React↔game event bridge, hero/building selection, HUD/settings/tutorial structure | Village metaphor, its server, assumed multi-floor support |
| Pixel Agents | `webview-ui/src/office/editor/editorState.ts:12-75` | Furniture catalog/placement, ghost preview, undo/redo and selection | Local layout authority overwriting the live org |
| AI Town | `src/components/PixiViewport.tsx:24-44` | Drag/pinch/wheel, inertia, clamping; social interaction concepts | Convex, Clerk, separate player/session store |
| Agent Monitor | `src/components/office/OfficeCanvas.tsx:54-90` | 30Hz render cadence, behavior zones, inspectable agent panels | Fixed-map ceilings and demo-as-live defaults |
| StarNet | `frontend/app/build.js:14-37` | Diegetic build mode, workstations, spatial feedback | Global-script app wholesale, brand/franchise cast |

Local snapshots do not themselves establish every upstream commit/license. Existing comments call StarNet artwork MIT, while its `NOTICE.md:165-171` reserves its own artwork/brand. Record this mismatch; do not assert blanket commercial rights. This plan copies no external source or art. Original cohesive art is recommended for style and publication readiness.

## Bounds of this audit

No broad pytest/Vitest/Playwright sweep, paid provider call, load stress test, backend mutation, independent review, or production security certification was performed. Browser probes verify the named baseline journeys; source findings and proposed improvements are labelled separately. External game gallery pages were retrieved as UI references, but some screenshot fetching was blocked; no unviewed screenshot is claimed as visually audited.
