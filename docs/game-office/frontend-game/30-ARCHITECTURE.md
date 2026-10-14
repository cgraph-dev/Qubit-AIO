# Frontend architecture and integration boundaries

## Target structure

One world renderer, one React shell, one authoritative data adapter. Navigation and animation never become new business logic.

```
existing /qubit-office plugin host + shared auth
  └─ OfficeGameShell (host React, one mount)
      ├─ HUD / panels / settings / dialogue / modal controller
      ├─ Navigation state (district/building/floor/room + owner viewpoint)
      └─ Phaser world host (one Game instance)
          ├─ Boot / exterior / lobby / floor scene contexts
          ├─ camera / geometry / sprite / interaction / presentation systems
          └─ typed adapter
              ├─ existing tower snapshot / canonical event + session clients
              └─ owner-owned optional building/action/chat contracts
```

**Domain truth:** existing backend snapshots, messages, action receipts, ledger and entities. **Local view state:** camera, open panel, selected stable ID, graphics/audio preferences, visitor viewpoint, draft editor commands. **Presentation state:** interpolation, sprite clip time, transition progress, hover/focus and scenery effects.

## Exact existing seams

| Concern | Existing path |
|---|---|
| Office route/name | `frontend/qubit-app/plugins/qubit-office/manifest.json` |
| Registered React component | `frontend/qubit-app/plugins/qubit-office/qubit-office.js:1618` |
| Single-script bundle pipeline | `scripts/build_office_bundle.py:52-65` |
| Host component and SDK | `frontend/qubit-web/src/plugins/PluginPage.tsx`; `registry.ts`; `sdk.d.ts` |
| Fleet / asset root | `frontend/qubit-web/src/plugins/fleet.ts:46-48`; public + served `fleet.json` |
| Host route sizing | `frontend/qubit-web/src/lib/chat-routes.ts:53-57`; `App.tsx` |
| Auth/prefixed request helper | `frontend/qubit-web/src/lib/api.ts`; SDK `fetchJSON`/`authedFetch` |
| Staged action helper | Existing `runMutationWithConfirm`/confirmation handling in `lib/api.ts`; inspect exact helper signature before binding |
| Existing data | `GET /agent/api/office/tower` externally; SDK uses `/api/office/tower` |
| Agent state/money authority | `voicebridge/office/tower.py` and existing ledger/org modules — not rewritten here |
| Canonical chat/services | Existing `/chat` TUI and `/qubit-chat` native surface; reuse backend/session contracts, don't replace either primary chat |

SDK React/hook objects are supplied by the host. Do not import a second bundled React or assume SDK versioned mutation helpers that have not been exposed.

## Proposed file boundaries (new files, not claims of existing APIs)

Under `frontend/qubit-app/plugins/qubit-office/game/`:

- `entry.ts`: host SDK lookup, root registration, asset URL injection.
- `OfficeGameShell.ts`: host-React component composition, rendering via host React/createElement.
- `types.ts`: domain/view/presentation DTOs; all unavailable fields optional with explicit capability status.
- `adapter.ts`: SDK-backed snapshot reads, existing events/client delegation, owner contract hooks.
- `navigation.ts`: pure transitions, valid locations, browser route/deep-link serialization.
- `input.ts`: context-specific input routing, capture/gesture thresholds, keyboard/touch accessibility.
- `GameHost.ts`: construct/destroy one Phaser instance and bind/unbind bridge.
- `scenes/BootScene.ts`, `DistrictScene.ts`, `LobbyScene.ts`, `FloorScene.ts`: scene lifecycle only.
- `systems/CameraController.ts`, `InteractionController.ts`, `CharacterPresenter.ts`, `WorldAssembler.ts`: one job each; stable entity IDs.
- `ui/Hud.ts`, `PanelHost.ts`, `CharacterPanel.ts`, `ConversationPanel.ts`, `BoardroomPanel.ts`, `MapPanel.ts`, `SettingsPanel.ts`, `EditorPanel.ts`.
- `styles/tokens.css`, `styles/game.css`, `assets/catalog.json`.
- `tests/` for pure reducer/adapter/input tests (configured by the implementation packet).

Compile this as a single plugin entry. Keep the legacy entry available until the new slice passes; proposed manifest switch then selects exactly ONE entry. Never leave duplicate native and plugin route owners active.

## Build and mounting decision

Extend the office-specific build pipeline, not the shared plugin loader, to compile the typed game entry with the existing frontend toolchain. Prototype the standalone bundle config with host React before rewriting the whole UI. Phaser dependency belongs to the actual owning frontend package/build; no unreviewed global script/CDN.

Preserve script/asset separation: JS can be served from `/dashboard-plugins/qubit-office/...`; current images/fonts use `/plugins/qubit-office/assets/...` in built static output. Keep source assets under `frontend/qubit-web/public/plugins/qubit-office/`; dist is generated. Verify actual URLs and content types, not an extension table. Never edit the generated bundle by hand.

For fullscreen game layout, add `/qubit-office` to the appropriate full-height host predicate or an explicitly scoped game-layout predicate. Do NOT add it to `CHAT_ROUTE_PATHS`. Immersive mode hides normal dashboard chrome only for this view and offers Exit to dashboard; it is not a second listener/server.

## Render and data clocks

- Snapshot bootstrap applies a coherent revision, then existing event subscriptions update entity state where a compatible stream is actually available.
- Fallback polling is serialized (one outstanding request), abortable and slower than animation. Suggested 1–2 s visible fallback is a proposal to measure, not a proven optimum. Pause ordinary scene polling while hidden; resume by snapshot/cursor recovery.
- Render clock animates camera/viewpoint/scenery and bounded presentation. It must not increment budget, task status, worker progress, memory count or a persistent worker location.
- Authoritative route interpolation requires route ID, nodes, time bounds and floor/portal segments. Existing point-only payload may be displayed faithfully, but cannot be upgraded by inventing an agent path.
- Unknown event/status shows neutral unknown. An unpriced cost is unknown, not zero. A known idle character can breathe/blink cosmetically after the amendment, without a working badge.

## Building and floor transitions

Navigation context contains `buildingId`, `floorId`, `roomId`, optional `portalId`, viewpoint pose and camera state. State graph: exterior→entering→lobby→travelling→floor→room, with symmetric exit/back transitions. IDs—not integer labels—bind floors to buildings.

Transition opens only after destination existence/capability checks. Preload bounded asset set; on success atomically commit the view context; on failure keep previous valid context. Modal/panel focus owns input while open. Deep links and voice shortcuts use the SAME navigation reducer.

Real worker location does not follow the visitor avatar. Multi-floor worker routes require backend segments, separate from owner navigation. Never relocate all agents to a newly selected floor.

## Dialogues and panels

Panel controller is a small explicit state machine: closed / primary panel / contextual drawer / exclusive modal. No generic global window framework. Selection is stable entity ID, dereferenced against the current data revision.

Conversation adapter submits to the existing target/session service, listens for canonical message IDs and finalization, handles interruption/reconnect, and returns action receipts. The portrait/text-choice UI is presentation around that service, not a new AI engine or message store. Follow existing primary-chat restrictions; when no safe integration exists yet, expose unavailable status rather than replace the TUI.

## Editor

Local draft commands, preview ghost and undo/redo are allowed; committed org state is never locally fabricated. Distinguish cosmetic decoration from capability-bearing props. Stage via existing operation gateway; accepted revision replaces draft only after readback. Replay failed/conflicting edits against the new revision or offer explicit discard. Closing with a dirty draft asks Save draft / Discard / Cancel—not an invisible apply.

## Lifecycle and failure containment

Mount creates one renderer, subscriptions and input listeners; unmount destroys all of them. React StrictMode mount→unmount→mount must not duplicate the engine. Late loader completion cannot attach listeners to a disposed canvas. ResizeObserver + capped DPR configure backing size and camera together.

Texture decode failure, missing portrait, unknown event, disconnected session and missing capabilities are localized panel/asset failures. Retain usable world/last snapshot with explicit stale status. Do not crash or blank the app shell.

## Performance plan (targets, not measured results)

Measure baseline before optimizing. Aim for smooth 60Hz desktop presentation, selectable 30Hz low-power mode, bounded sprites/labels/particles, floor-local loading and scene culling. Do not claim a supported headcount/asset budget until the configured stress fixture passes. Never tie texture allocation, whole-scene reconstruction or React rerenders to every animation frame.

Scale fixtures are labelled recordings/synthetic performance fixtures, never live office data. Use 10/50/100 rendered-character scenarios only to characterize frontend limits; do not create those real agents.
