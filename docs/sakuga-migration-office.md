# Sakuga migration — Qubit Office host shell

**Scope:** `frontend/qubit-app/plugins/qubit-office/game/**` (the React host
shell around the vendored office iframe). The dashboard and the Electron
renderer are separate lanes.

**Status:** composed pass landed. The shell reuses the shared composed controls
through `game/app/sakuga-form.ts`, a thin adapter that keeps the panel factories
React-free (`R.createElement`) and native-shaped props.

## What changed

| Unit | Files | Notes |
|---|---|---|
| Adapters | `app/sakuga-form.ts` | `OfficeInput`, `OfficeSelect`, `OfficeTextarea`, `OfficeTooltip` wrap `SakugaInput` / `SakugaSelect` / `SakugaTextarea` / `SakugaHoverTooltip`. Props stay native-shaped (`onChange({ target: { value } })`, `data-testid`, `aria-label`, `onInput`) so existing tests and selectors are unchanged. |
| Command Center | `app/CommandCenter.tsx` | Search input → `OfficeInput`; kind/status filters → `OfficeSelect`; search has a hover hint (Ctrl/⌘ K, arrows, Enter, Escape). |
| Timeline | `app/Timeline.tsx` | Search + kind/status filters → composed controls; the search hint names the filter. |
| Seat routing | `app/SeatModelPanel.tsx`, `app/OfficeAppShell.tsx` | Seat/provider/model pickers → `OfficeSelect`; the seat picker has a hover hint. |
| Agent control | `app/AgentControlDrawer.tsx` | Managed-agent picker, text fields, permission scope inputs, preview prompt/ranges → composed controls. |
| Room Builder | `app/RoomBuilder.tsx` | Style/palette/light selects, room/signage/department text fields → composed controls. The numeric seed field stays native (documented). |
| War Room | `app/WarRoomPanel.tsx` | Session/source/evidence selects and the idea/source/locator/claim inputs → composed controls. |
| Etsy / Character Studio | `app/EtsyConnectPanel.tsx`, `app/CharacterStudio.tsx` | Redirect/state inputs and the part search → composed controls; the search has a hover hint. |

`officeFx`, `sakugaButton`, `sakugaProgress` and `sakugaLoading` are unchanged,
so the office loading/progress behaviour is untouched.

## Tooltips

`OfficeTooltip` is used on host-shell controls where a short hint helps
(Command Center search shortcuts, Timeline search, seat picker, Character Studio
search). The office's own injected Habblaud icons live inside the **iframe**,
which is a separate document with no host React tree; they cannot mount the
React `SakugaHoverTooltip` wrapper. That boundary is deliberate and documented:
the injected icons keep their native `title`/`aria-label` semantics.

## Remaining exceptions

- **Numeric seed** (`RoomBuilder`) stays a native `type="number"`; the composed
  number control is a desktop-web extra and the office did not need the
  steppers for this one field.
- **Office iframe internals** are covered by the in-iframe passes below. The
  shell still never reaches into the iframe; the vendored client upgrades
  itself. The second in-iframe pass converted the row/structural buttons too.
- **Day selects** (timelapse and daily stats) stay native `<select>`s: the
  native option list and keyboard contract are the reason, not an omission.
- **Textareas and the hidden file input** stay native: Sakuga ships no
  textarea, and the file picker is deliberately hidden behind a styled button.
- **Composite widgets** in the shell keep their semantics where replacing them
  would lose keyboard/portal behaviour.

## Evidence

- `npx vitest run --config vitest.office-app.config.ts` → 48 files / 563 tests.
- `npx vite build --config vite.office-game.config.ts` → rebuilt
  `qubit-office.game.js` (8,039.43 kB) and `.css` (91.65 kB).
- `python3 scripts/vendor_habblaud.py --check` → ok (78 files).
- `python3 scripts/check_sakuga_vendor.py` → OK (2 copies).
- `python3 scripts/sync_sakuga_shared.py --check` → OK (17 files).

## Follow-up pass — more buttons, progress, tooltips (2026-11-18)

The owner asked for "more buttons, more places, more things". This pass extended
the sweep rather than changing the contracts.

| Unit | Files | Notes |
|---|---|---|
| Vector shell buttons | `game/OfficeGameShell.tsx` | The HUD's text/motion/settings controls and every settings choice pill now use `sakugaButton`. The shell is still comparison-only (`?shell=1`). |
| Character Studio | `app/CharacterStudio.tsx` | Category and lock chips moved from native `<button>` to `sakugaButton`; the layer up/down arrows gained hover hints. `data-testid`/`data-category`/`aria-pressed` are unchanged. |
| Loading progress | `CommandCenter`, `Timeline`, `AgentsPanel`, `ApprovalsPanel`, `EtsyConnectPanel`, `EffectiveStatePanel`, `PanelInventory`, `SeatModelPanel`, `AgentControlDrawer` | Each existing `role="status"` loading line now carries the vendored `sakuga-progress` bar beside its honest label. The root element keeps its `data-state`/`data-testid`, so the harness and a11y contracts are unchanged. |
| Tooltips | `EtsyConnectPanel`, `ApprovalsPanel`, `CharacterStudio` | `OfficeTooltip` added to the Etsy status refresh, the approvals refresh, and the Character Studio layer arrows. |

The office still never invents state: the progress bars are indeterminate-looking
status chrome (`value` 0.08) attached to real read states, not completion
percentages, and `office.fx: off` remains the default. The injected Habblaud
icons stay iframe-native and keep their `title`/`aria-label`.

### Evidence (follow-up)

- `npx vitest run --config vitest.office-app.config.ts` → 48 files / 563 tests.
- `npx vite build --config vite.office-game.config.ts` → rebuilt
  `qubit-office.game.js` (8,040.63 kB); CSS unchanged (91.65 kB).
- Office Python focused set → 99 passed; the 4 CSS-scope assertions in
  `test_office_game_shell.py` fail identically at the base commit (documented).
- `vendor_habblaud.py --check` ok (78 files); `check_sakuga_vendor.py` OK;
  `sync_sakuga_shared.py --check` OK.
- No live browser run in this worktree (no bridge is started here); the served
  `:8890` process still carries the previous bundle.


## In-iframe client pass (2026-11-18)

**Scope:** the vendored Habblaud client (`frontend/qubit-app/plugins/qubit-office/habblaud/**`,
source `/home/trick/qubit-worktrees/habblaud-theme`). This is still additive:
the office stream, permission/terminal contracts, theme payload and FX overlay
are untouched, and the control files are the byte-identical upstream copies.

| Unit | Change |
|---|---|
| Loader | `client/src/ui/sakuga.ts` loads `sakuga-vfx/core.js` + `button`/`input`/`progress` from the client's own `public/sakuga-vfx/` (served inside the iframe) before `createUI`. The 506 KB `toggle` and the `slider` load when the settings panel opens, and `checkbox` loads when a permission card is visible; ids are deduplicated. The component files are copied byte-identically from the Qubit `/sakuga-vfx/` vendor and never edited. |
| Helpers | `client/src/ui/dom.ts` adds `sakugaButton`, `sakugaInput`, `sakugaSlider`, `sakugaToggle`, `sakugaCheckbox`, `sakugaProgress` and `sakugaTooltip`. They keep the `h()` contract (`class`/`text`/`attrs`/`hidden`/`on`/children) and mirror what the shadow control does not observe (`aria-label`, `maxlength`, `spellcheck`, inner `input` → host `input`, `.value`/`.focus()`/`.select()`). With the flag off they build the original native element. |
| Converted | Standard action/modal/permission buttons, settings switches (108×54 toggle art preserved), the volume slider, the permission interrupt checkbox (38px box preserved), standalone text/search fields (character name, room rename, spawn path, permission “Outro”, sidebar/history/terminal search), and the splash loader (Sakuga progress). |
| Native exceptions (this pass; superseded by the second pass below for the row controls) | No light-DOM `<button>` remains. Still native by contract: the two day `<select>`s (native option list/keyboard), `<textarea>` (composer/spawn/reason; Sakuga has no textarea), the hidden file input, and the AskUserQuestion option radios/checkboxes (grouped named native inputs read by the form model). The terminal-tab close stays a `role="button"` span inside the tab (a nested `<button>` is invalid). |

**Rollback:** `QUBIT_OFFICE_SAKUGA=0` (env, read by `webui.py` for the office
document) or `?sakuga=0` on the iframe URL. The client then builds the original
native elements; no rebuild is required.

**Evidence (this pass):**

- Habblaud `npm test` → 119 files / 1,353 passed, 2 skipped; `npm run typecheck` clean; `npm run build:client` ok.
- Re-vendor `python3 scripts/vendor_habblaud.py --check` → ok (87 files, 3,038,538 bytes, commit `4ccce2537472`).
- Office Python focused set (plugin app/auth, stream/auth/fx, theme/build, tower-plan, business bridge, vendor, static gzip) → 375 passed. The 4 `test_office_game_shell` CSS-scope assertions fail identically at the lane base.
- `frontend/qubit-web`: `npx vitest run --config vitest.office-app.config.ts` → 52 files / 609 passed; `npx vite build --config vite.office-game.config.ts` → rebuilt the game bundle.
- `check_sakuga_vendor.py` OK (2 copies); `sync_sakuga_shared.py --check` OK (17 files).
- Static same-origin probe of the vendored document (`?mock=1`): 86 Sakuga buttons, 22 native row buttons, 5 Sakuga inputs, 2 native inputs (file + timelapse range), 4 toggle hosts, 1 slider, 0 console errors, 0 failed requests, FX overlay present. Initial requests were exactly `core.js`, `button/button.js`, `input/input.js`, `progress/progress.js`; opening Settings added `toggle/toggle.js` and `slider/slider.js` and both shadow controls upgraded. `?sakuga=0` → 0 Sakuga elements, 109 native buttons, all native inputs, 0 console errors.
- The live `:8890` bridge was not restarted in this worktree; deployment is a separate step.

## Second in-iframe pass — row/structural buttons (2026-11-19)

Owner: "do the same everywhere in qubit office". The first pass deliberately
left the row-shaped controls native; this pass converts them without changing
the rows' visual contract or their drag/keyboard behaviour.

**Helper.** `client/src/ui/dom.ts` gains a `row: true` option on `sakugaButton`
(class `sakuga-row` on the host). The **host** keeps the original class' box and
state (background, hover, `is-selected`, `is-off`, opacity, drag, the room
accent `::before`); the **Sakuga middle** loses the gallery chrome
(`transparent`, `border:0`, `box-shadow:none`, `font/color: inherit`) and takes
the row layout through `::part(button)` in `client/src/ui/styles.css`. The
press/impact effects stay anchored on the part, which fills the host box.
`aria-label/checked/pressed/expanded/current/selected/disabled/haspopup` and
`tabIndex` are mirrored to the inner button (the element that actually receives
focus), so roving radiogroups and Tab order keep working.

**Converted (no light-DOM native `<button>` left):** agent rows, room headers,
account filters, history items, feed header + feed rows, delivery toast/cards/
attachments, toast body, terminal tabs, terminal copy/new, counters (waiting +
shells), version pill, usage "how" link, room link, day legend keys, character
swatches/chips, social name chips, spawn folder cards, timelapse peak markers,
and the AskUserQuestion-style rows that had already been converted.

**Inputs.** The timelapse scrubber is now a `sakuga-slider`. It stays an
invisible drag surface over the chart (`opacity: 0`, inset 0) so the rendered
timeline and its needle are unchanged; the lazy slider is pulled in when the
timelapse mounts (`ensureSakuga('slider')`), so it works even if Settings was
never opened. `value`/`disabled` are attribute-backed so a write before the
component upgrade cannot shadow its accessor, and `min`/`max`/`step` are the
component's observed attributes. The file input stays hidden. Text/search
fields and the volume slider were already Sakuga from the first pass.

**Exceptions (by contract, not omission):** the two day `<select>`s (native
option list/keyboard), `<textarea>` (composer/spawn/reason; Sakuga ships no
textarea), the hidden file input, the AskUserQuestion grouped native
radios/checkboxes, and the terminal-tab close (`role="button"` span inside the
tab; a nested `<button>` is invalid).

**Rollback.** `QUBIT_OFFICE_SAKUGA=0` / `?sakuga=0` still builds the original
native elements; the new `::part()` rules find no target.

**Evidence:**

- Habblaud `npm test` → 119 files / 1,353 passed, 2 skipped; `npm run typecheck`
  clean; `npm run build:client` ok.
- Re-vendor from `/home/trick/qubit-worktrees/habblaud-theme` →
  `vendor_habblaud.py --check` ok (87 files, 3,042,531 bytes, commit
  `4ccce2537472`; `local_commit` = the Habblaud lane commit).
- Office-app Vitest → 52 files / 609 passed; `npx vite build --config
  vite.office-game.config.ts` rebuilt the game bundle.
- Office Python focused set (plugin app/auth, vendor, reproducibility, stream,
  stream auth, stream FX) → 181 passed, 1 skipped. The rollback-global test
  (`test_office_sakuga_rollback_global_is_opt_in`) passes.
- `check_sakuga_vendor.py` OK (2 copies); `sync_sakuga_shared.py --check` OK
  (17 files).
- Standalone probe of the vendored document (`?mock=1`, Google Chrome via
  `playwright-core`): **0 light-DOM `<button>`**, 107 `sakuga-button` hosts (21
  with `sakuga-row`), 5 `sakuga-input`, 0 non-file native inputs, 2 native day
  `<select>`s, 2 `sakuga-slider`, FX overlay present, **0 console errors / 0
  failed requests** on the base load. Opening Settings upgrades the toggle and
  slider; opening Timelapse adds the slider whose inner `input` carries
  `min/max/step/value`. Clicking an account filter sets `is-off`, a room header
  selects, an agent row opens the drawer with that agent, the version pill opens
  Settings/About and the usage "how" opens Help; a room drag emits
  `dragstart`/`dragover`/`drop`. Opening Timelapse against the static server
  logs the expected `/api/timeline/days` 404 (no backend), identical in
  rollback.
- `?sakuga=0` on the same vendored document → 110 native `<button>`, 0 Sakuga
  elements, 2 native ranges, 2 selects, no FX canvas; filter/room/agent
  interactions still work.
- No live `:8890` restart in this worktree.
