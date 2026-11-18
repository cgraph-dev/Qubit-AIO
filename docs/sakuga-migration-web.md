# Sakuga control sweep — web dashboard

Owner decision: literal upstream custom elements via the wrappers in
`@qubit/shared/sakuga` (re-exported from `frontend/qubit-web/src/sakuga/ui.tsx`);
original palette, no recolouring. This file records only the controls that were
**not** replaced, and the shared infrastructure changes the sweep required.

## Documented exceptions (control stays native)

| File:line | Control | Reason |
|---|---|---|
| `src/pages/DocsPage.tsx` (header action `<a>`), `src/pages/AnalyticsPage.tsx` (`<Link>`) | navigation links | owner exception: nav `<a>` links stay links |
| `src/pages/AgentCardsPage.tsx` (`AgentActivityScreen`, `DelegateSheet`) | controls rendered by `src/components/om-agent-ui.tsx` | component owned by another lane; not edited here |
| `src/pages/VoiceEditPage.tsx` | audio device / xterm surfaces | third-party widget internals + editor/canvas exception |
| `src/pages/CanvasPage.tsx` (`Stored content` textarea, preview iframe) | canvas/editor surfaces | canvas exception; the page toolbar, file roster actions, add-file name and node controls are Sakuga |
| `src/pages/FilesPage.tsx` (`Drop files here` surface) | drag-and-drop upload button | the wrapper exposes no drag/drop props; a drop target is not a plain control. The hidden `<input type=file>` stays native and is proxied by the Sakuga Upload button (owner exception). |
| `src/pages/FilesPage.tsx`, `src/pages/ConfigPage.tsx`, `src/pages/SystemPage.tsx`, `src/pages/KitchenPage.tsx` | hidden `<input type=file>` | hidden file inputs are proxied by a Sakuga Button (owner exception) |
| `src/components/AutoField.tsx` (rendered by Config) | auto-generated config fields | component owned by another lane; documented rather than edited |
| `src/components/DeleteConfirmDialog.tsx`, `ConfirmDialog.tsx` | confirm dialogs | component-owned; their buttons are outside this file scope |
| `src/pages/LogsPage.tsx` (`Log line N detail` rows) | per-line disclosure | dense monospace text rows, not an action affordance; a Sakuga button per line would add panel chrome to log output. Toolbar controls are Sakuga. |
| `src/pages/CronPage.tsx` (`repeat`), `src/pages/DevicesPage.tsx` (setup-code TTL) | `type=number` inputs | number inputs are outside the required input map (text/email/password/search/url/tel); every text input on both pages is Sakuga. |
| `src/pages/CronPage.tsx`, `src/pages/McpPage.tsx`, `src/pages/McpCallsPage.tsx`, `src/pages/CanvasPage.tsx` | `<textarea>` (prompt/context/env/args/stored-content) | multi-line editors are outside the required input map; their surrounding buttons and single-line fields are Sakuga. |
| `src/components/CommandBlock.tsx` `CopyButton` (Webhooks) | copy-to-clipboard | component-owned |

_(This table grows as pages are swept; every entry names file:line, control and reason.)_

## Test infrastructure

Vitest runs pages under jsdom, which does not load the external
`public/sakuga-vfx/*.js` scripts, so the custom elements never upgrade and the
shared test helpers (`typeIntoSakuga`, `clickSakuga`) cannot find a shadow root.
Two scoped test-only changes make the wrappers testable without touching the
vendored engine:

- `frontend/qubit-web/src/pages/sakuga-test-dom.ts` — installs lightweight
  open-shadow stand-ins for the components used by page tests (button, input,
  checkbox, toggle, radio, tabs, slider, progress, rating, tooltip, toast).
  Production never imports it.
- `frontend/qubit-web/vitest.config.ts` — adds `@qubit/shared` alias + React
  dedupe, matching `vite.config.ts`. Without it a page test loads the shared
  package's own React copy and every hook reads a null dispatcher.

## Toast surface (deferred)

Pages still render the `@nous-research/ui` `Toast` component; `showSakugaToast`
is only used by `PairingPage` so far. Converting the remaining pages would
replace the visible notification surface but every existing test asserts the
toast text in `document.body`, so it needs a shared adapter first. Effects
(`emitFx`) fire alongside the legacy toast on the swept pages.

## Effects wiring

Effects are presentation only. Pages fire a cue for real outcomes only:
success after a successful save/load action, `error` on a caught API/validation
failure, `warning` for destructive or revoking actions, `approval` for staged
approvals. Buttons fire `impact` by default from the wrapper.

## Sweep status (2026-11-18)

Swept (controls imported from `@/sakuga/ui`):

| Page | Buttons | Inputs | Selects | Toggle/Checkbox |
|---|---|---|---|---|
| DocsPage | 3 | 1 | 0 | 0 |
| AnalyticsPage | 2 | 0 | 0 | 0 |
| AgentsPage | 1 | 0 | 0 | 0 |
| AgentCardsPage | 2 | 0 | 0 | 0 |
| PairingPage | 3 | 0 | 0 | 0 |
| QubitTvPage | 5 | 2 | 0 | 0 |
| FilesPage | 11 | 2 | 0 | 0 |
| ModelsPage | 23 | 1 | 1 | 1 |
| PluginsPage | 19 | 4 | 4 | 3 |
| ChannelsPage | 20 | 3 | 0 | 1 |
| ConfigPage | 10 | 1 | 2 | 1 |
| SystemPage | 37 | 8 | 1 | 2 |
| WebhooksPage | 14 | 4 | 0 | 0 |
| LogsPage | 5 | 1 | 0 | 1 |
| AgentWorkspacePage | 6 | 1 | 0 | 0 |
| BoardAdminPage | 12 | 13 | 1 | 1 |
| CanvasPage | 10 | 2 | 1 | 0 |
| CheckpointsPage | 7 | 2 | 0 | 0 |
| CronPage | 10 | 4 | 5 | 3 |
| DevicesPage | 12 | 4 | 0 | 0 |
| EnvPage | 12 | 2 | 0 | 0 |
| HarnessRouterPage | 21 | 4 | 2 | 1 |
| KitchenPage | 7 | 2 | 0 | 0 |
| McpCallsPage | 4 | 4 | 0 | 0 |
| McpPage | 17 | 7 | 3 | 0 |
| MemoryBackendsPage | 4 | 0 | 0 | 0 |
| NodesPage | 12 | 4 | 0 | 0 |
| OmniRoutePage | 1 | 0 | 0 | 0 |

**Not yet swept (controls remain).** These are unimplemented work, not
accepted exceptions:

`OpenVikingAgentHubPage`,
`OpenVikingPage`, `OpenVikingResourcesPage`, `OpenVikingRetrievalPage`,
`OpenVikingStudioPage`, `OpenVikingUploadsPage`, `ProfileBuilderPage`,
`ProfilesPage`, `QubitBotPage`, `SchedulerMediaPage`, `SessionOpsPage`,
`SkillsPage`, `SkillsReviewPage`, `TalkPage`, `TaskflowsPage`, `TaskInboxPage`,
`TasksPage`, `UsersAdminPage`, `VoiceEditPage` (xterm/audio internals are an
exception; its forms are not), `WebChatPage`, `WorktreesPage`,
`src/plugins/PluginPage.tsx`.

## Admin / infrastructure lane (pass C, swept 2026-11-18)

Imported from `@/sakuga/ui`: `AgentCardsPage`, `AgentsPage`,
`AgentWorkspacePage`, `AnalyticsPage`, `BoardAdminPage`, `CanvasPage`,
`CheckpointsPage`, `CronPage`, `DevicesPage`, `DocsPage`, `EnvPage`,
`FilesPage`, `HarnessRouterPage`, `KitchenPage`, `LogsPage`, `McpCallsPage`,
`McpPage`, `MemoryBackendsPage`, `ModelsPage`, `NodesPage`, `OmniRoutePage`.
Native `<button>`, text/email/password/search/url/tel inputs, `<select>`s,
checkboxes and the native `Segmented`-free tab strips on these pages are
Sakuga controls; select option children were converted to `SakugaSelect`
option arrays. Effects fire on real outcomes: success for a completed
load/save/probe/invoke, `warning` for delete/prune/revoke/remove/cancel,
`approval` when a staged confirmation opens, and `error` on a caught
API/validation failure.

## Chat / shell / shared-components lane (swept 2026-11-18)

Swept by this lane and imported from `@/sakuga/ui`:

- `src/App.tsx` — shell header, sidebar rail (mobile open/close, collapse,
  system actions), `SakugaToastHost` mounted once.
- `src/pages/ChatPage.tsx` — header toggles, mission cards, selection
  copy/clear, retry, confirm/cancel, voice engine + reasoning selects,
  attach/voice/send, new chat/copy/summarize, message-selection and
  voice-follow checkboxes; effects fire on send (`impact`), turn done
  (`success`), turn error (`error`), cancel (`warning`), new chat (`pop`).
- `src/pages/TerminalChatPage.tsx` — side-panel toggles, New chat, copy-last.
- `src/pages/SessionsPage.tsx` — every row action, pagination, prune/import
  dialogs, source filter, bulk actions, rename field and row/source checkboxes;
  toasts now go through `showSakugaToast` (+ success/error effect).
- `src/components/` — `ConfirmDialog`, `LoadErrorNotice`, `ReadAloudButton`,
  `ChatToolsSummary`, `ChatSessionList`, `ChatSidebar`, `ThemeSwitcher`,
  `LanguageSwitcher`, `ProfileSwitcher` (SakugaSelect), `AuthWidget`,
  `ReasoningPicker` (SakugaSelect), `OAuthLoginModal`, `OAuthProvidersCard`,
  `QubitConsoleModal`, `MemoryPressureBanner`, `SharedMetricsConsentBanner`,
  `ChatWorkspacePicker`.

## Pass D sweep (2026-11-18)

Swept by this lane and importing the controls from `@/sakuga/ui`:

- **Pages** — `OpenVikingPage`, `OpenVikingAgentHubPage`,
  `OpenVikingResourcesPage`, `OpenVikingRetrievalPage`, `OpenVikingStudioPage`,
  `OpenVikingUploadsPage`, `ProfilesPage`, `ProfileBuilderPage`, `QubitBotPage`,
  `SchedulerMediaPage`, `SessionOpsPage`, `SkillsPage`, `SkillsReviewPage`,
  `TalkPage`, `TaskflowsPage`, `TaskInboxPage`, `TasksPage`, `UsersAdminPage`,
  `VoiceEditPage`, `WebChatPage`, `WorktreesPage`; `SystemPage` and
  `WebhooksPage` notifications moved to `showSakugaToast` (+ success/error FX).
- **Plugins** — `src/plugins/PluginPage.tsx` renders no controls; no
  plugin-owned surface needed a change.
- **Shared components** — `AutoField`, `AutomationBlueprints`,
  `ModelPickerDialog`, `ScheduleBuilder`, `SkillEditorDialog`,
  `ToolsetConfigDrawer`, `ReportList`, `om-ui` (`OmButton`/`OmField`/
  `OmListItem`), `om-agent-ui`, `om-computer-workspace`, `ChatWorkPanel`,
  `MultiplexStandaloneBanner`, `PageErrorBoundary`, `console-ui`,
  `VoiceEditPreview`, and the toast hosts in `AutomationBlueprints` /
  `ToolsetConfigDrawer` / `SystemPage` / `WebhooksPage`.

### Documented exceptions added by this pass

| Control | Reason |
|---|---|
| `type="number"` inputs (`VoiceEditPage`, `AutoField`, `ScheduleBuilder`, `om-ui` `OmField`) | Number input is outside the upstream component set (text/email/search/url/tel/password only); kept native. |
| `type="time"` / `type="datetime-local"` (`ScheduleBuilder`, `AutomationBlueprints`) | Date/time inputs are outside the upstream component set; kept native. |
| `<textarea>` (`VoiceEditPage`, `ProfilesPage`, `ProfileBuilderPage`, `SkillsPage`, `SkillsReviewPage`, `WebChatPage`, `QubitBotPage` composer, `SkillEditorDialog`, `AutoField`) | Multi-line text is outside the upstream component set; kept native (the QubitBot composer grows with content). |
| `src/components/CommandPalette.tsx` search + option rows | Composite ARIA listbox: the input owns ↑/↓/Enter/Esc and rows are mouse-only `role="option"` elements; converting loses that contract. Kept native per the composite-widget exception. |
| `src/components/LanguageSwitcher.tsx` `role="option"` rows | Composite listbox options, same reason. |
| Hidden `<input type="file">` (`SystemPage`, `FilesPage`, `ConfigPage`) | Proxied by a Sakuga Button (owner exception). |
| Segmented `role="radio"` filters (`SessionsPage`, `FilesPage`, and similar toolbar switches) | Composite segmented control: horizontal roving-arrow radio group. The upstream radio is a one-column list, so a literal swap changes layout and keys; the filter keeps its semantics and effects fire around it. |

### Shared-layer fixes this pass required

- `Tabs` now reads the `CustomEvent.detail` from React's `nativeEvent` wrapper,
  so a tab change actually reaches the wrapper's `onChange` (React wraps a
  custom element's `change` in a synthetic event).
- `Input` lets an explicit caller `aria-label` win over the visible `label`
  prop, so existing `aria-label`-keyed tests stay honest.
- Both fixes are mirrored to `qubit_runtime/apps/shared` by
  `python3 scripts/sync_sakuga_shared.py` (7 files, `--check` OK).

## Remaining composite/exception surfaces

| File | Control | Reason |
|---|---|---|
| `src/components/CommandPalette.tsx` | search input + option rows | composite ARIA widget (above) |
| `src/components/LanguageSwitcher.tsx` | option rows | composite listbox options (above) |
| `src/pages/VoiceEditPage.tsx` | number inputs, textareas | outside the upstream input/textarea set |
| `src/pages/DocsPage.tsx`, `AnalyticsPage.tsx`, `OpenVikingStudioPage.tsx` | navigation `<a>` links | owner exception: nav links stay links |
| third-party dialog internals | Radix dialog surfaces | third-party widget internals |

## Core component fixes (2026-11-18)

Owner review fixes for the shared wrappers and control density.

- **Toggle ON animation.** `writeValue` re-wrote `checked` with `setAttribute`
  on every render even when the attribute was already present. `setAttribute`
  fires `attributeChangedCallback` for an unchanged value, and the vendored
  toggle resets `seq` to a one-frame jump unless `flipping` is set — so the
  43-frame ON animation never played. OFF worked only because removing an
  absent attribute is a no-op. The wrapper now compares before writing, and
  skips property assignment when the property is read-only (progress `value`).
  Pinned by `src/sakuga/core-controls.test.tsx` (a controlled click leaves
  `seq.length === 43` and `pos` advances; a read-only progress value updates).
- **Compact chrome.** `src/sakuga/sakuga.css` reduces only the control chrome
  via `::part()`: button 5px 12px / 13px / 2.5px / 10px / 3px 4px (34px tall,
  was ~55px), input 7px 12px / 14px / 2.5px / 4px 5px (37px, was 54px), tabs
  and the progress percentage chip compacted, toggle side/bottom margin kept
  with the 89px gallery top margin removed. The engine, the 108×54 toggle art
  and the 38px checkbox box are unchanged.
- **Loading primitive.** `SakugaLoading` (progress + caption; busy crawl when
  no `value`, determinate when given one) is exported from
  `@qubit/shared/sakuga`. The dashboard's route `Suspense` fallback
  (`RouteFallback`) is the example usage.
- **Plugin SDK checkbox.** `src/plugins/registry.ts` now exposes a
  Sakuga-backed `Checkbox` that keeps the Radix `checked`/`onCheckedChange`
  contract, so bundled plugin surfaces (kanban, achievements) render the
  original box instead of the design-system check. The indeterminate tri-state
  keeps the design-system control (no upstream equivalent).
- **Status indicators.** The Config "configured" check icons and other
  `Check`/`CheckCircle` glyphs are status/selection indicators, not
  checkboxes; they were left unchanged deliberately.

## Component gallery + wrapper fidelity (2026-11-18, fidelity lane)

The owner asked for a live gallery ("all 12 must work and be nice") and a fix
for every control, not only the checkbox. Two surfaces ship:

- **Dashboard route `/sakuga-gallery`** — a hidden route (lazy
  `src/pages/SakugaGalleryPage.tsx`, no sidebar row). 18 cards: the twelve
  upstream gallery cards (primary/secondary button, toggle off/on, slider,
  checkbox, radio, input, tabs, progress, rating, toast, tooltip), the Qubit
  extras (`SakugaSelect`, `ListItem` rows, `SakugaLoading` busy and
  determinate, the `emitFx` bus), and a disabled/focus state card. Each card
  states what the control does and how Qubit uses it, and prints the real
  value/change the control emitted.
- **Standalone `/sakuga-gallery.html`** — `public/sakuga-gallery.html`, which
  loads the eleven vendored files with plain script tags exactly like the
  upstream gallery. It links to the dashboard gallery for the Qubit extras.
  It is deliberately outside `public/sakuga-vfx/` so the vendor manifest stays
  exact.

Real-browser verification (Chromium, 1440/900/390):
`npx tsc -b` clean; `npx vitest run` **168 files / 1333 tests passed**; the
focused `src/sakuga/*.test.tsx` set **20 passed**; the standalone and dashboard
galleries each upgraded every component with **0 Sakuga console errors / 0
failed `/sakuga-vfx/` requests**. `scripts/check_sakuga_vendor.py` and
`scripts/sync_sakuga_shared.py --check` are green. Details:
`docs/STATUS.md` (Sakuga fidelity entry).

### Wrapper fixes that a real browser exposed

The jsdom test helpers dispatch `composed: true` input events, so they could
not see two of these. The vendored files are untouched.

- **Button variant was too late.** `variant`/`type` are read once in the
  element's first `render()` and are not observed attributes. The wrapper wrote
  them in a layout effect, after `connectedCallback`, so
  `<sakuga-button variant="secondary">` stayed primary in the browser. They
  (and `disabled`) are now initial attributes, which React writes before
  insertion.
- **Input `type` was too late.** Same class for `type`, `label`,
  `placeholder`, `autocomplete`; every `type="email"`/`password` field rendered
  as `type="text"`. Now initial attributes.
- **Radio/tabs/rating labels.** `label` is not observed and is read during the
  first render; a late write left the inner `radiogroup`/`tablist` named
  "Options"/"Tabs". Now an initial attribute, and a label change remounts.
- **Real keystrokes never reached React.** The vendored input dispatches its
  `input` event with `bubbles: true` but `composed: false`, so it never crosses
  the shadow boundary. The wrapper now listens on the inner input and
  re-publishes the event on the host (not the input, which would double the
  vendored dust). `SakugaSelect`'s filter depends on this; it was unusable for
  typing before.
- **Document reset zeroed `:host` margins.** A document-level `* { margin: 0 }`
  wins over a shadow-tree `:host` rule. `sakuga.css` restates the documented
  upstream margins for the slider (30px, the value bubble), tabs (10px) and
  tooltip (60px, the hint); without them the bubble/hint overlapped the content
  above in the gallery.
- **Gallery layout.** The back-canvas the button/progress draw reaches 160px
  past the control (upstream documents it can be clipped); it inflated the
  dashboard's inner scroller at 390/900/1440. The gallery root uses
  `overflow-x: clip` so the page-wide effects and vertical scroll are
  untouched. The select filter fills its panel, and the dense `ListItem` rows
  keep the gallery ink for their active row instead of inheriting the dark
  shell's `--midground`.

### Upstream limitations that remain

- Effects are drawn on one page-wide canvas at the highest z-index; lettering
  can fly over a card header (the tooltip's カチッ does). The hint itself is
  reserved inside the stage.
- The button/progress back-canvas is clipped by a parent with `overflow:
  hidden`; the gallery keeps it visible and clips only the layout overflow.
- Reduced motion plays no effects and jumps animations to their end frame
  (`fx.calm`; the toggle's sequence is still 43 frames with `pos === 42`).

## Office host shell + in-canvas (2026-11-18 completion pass)

The office host already rendered every dock/menu action as `sakuga-button` and
the Agents/Approvals/Etsy/Studio panels were on the same control. This pass
added the loading/progress surfaces and more real-event cues:

- `game/loading/OfficeLoadingScreen.tsx` — the light-DOM progress bar is now
  `sakuga-progress` (indeterminate crawls to 90%, determinate when a real
  `progress` is supplied) and Retry is `sakuga-button`. Cues: `success` when
  the frame is ready, `error` on the failure screen.
- `game/app/OfficeAppShell.tsx` — a compact `sakuga-progress` under the
  connecting banner, a `SakugaLoading` in the campus directory load, and cues
  on campus ready/error, floor open/clear, and Studio tab changes.
- Panel outcomes fire real cues: approval approve/reject/failure, Etsy
  authorize/complete/disconnect/failure, room publish/rollback, War Room
  record/decision/failure, agent save/stop/revoke/archive.
- `voicebridge/office_stream.py` now projects recent committed `bos_event`
  rows (task/artifact/receipt/finance/marketing/CRM/memory) beside the office
  activity rows; `voicebridge/office/fx.py` gained aliases for the
  task/artifact/receipt/finance/marketing/CRM/memory office-event kinds. The
  projection stays bounded, deterministic, read-only, and `office.fx: off`
  still sends `[]`.

Documented office exceptions (unchanged): the two icon buttons injected into
the vendored app's own Panels group are created with `document.createElement`
inside the Habblaud document because they must inherit its
`.ui-btn-group`/`ui-px-icon` chrome; they keep the upstream DOM contract. Panels
keep native `<select>` controls (composite filter/seat pickers) and the
controlled search `<input>`s, which the upstream input cannot replace without
piercing its shadow event contract; the shell's own inputs/selects are
compact-sized to the same row height.

## Composed extras (2026-11-18, combos lane)

Owner directive: use the upstream source to build Qubit-owned compositions, so
Sakuga can reach controls upstream does not ship. The vendored files are
untouched; these are native elements in the Sakuga frame, plus the vendored
Button/Input/Checkbox/Tooltip around them. Import them from the shared layer
(`@qubit/shared/sakuga`) or, inside the dashboard, from `@/sakuga/ui` (the
re-export aliases are in parentheses):

| Extra (`@/sakuga/ui` alias) | Composes | Replaces | Native contract |
|---|---|---|---|
| `SakugaTextarea` (`Textarea`) | native `<textarea>` + `--sk-*` frame; `type` cue on input, `focus` cue on focus | multi-line prompts, notes, skill bodies, descriptions | value/defaultValue, name, rows, required, maxLength, autoFocus, disabled |
| `SakugaNumber` (`NumberField`) | native `type=number` (spinner hidden) + two compact `SakugaButton` steppers | retry counts, thresholds, row limits, numeric config fields | value, min/max/step, name; onChange gets a number, or the raw string while empty |
| `SakugaDate` (`DateField`) / `SakugaTime` (`TimeField`) | native `type=date` / `type=time` + a Sakuga button calling `showPicker()` where supported | schedules, deadlines, automation times | value/min/max/name; graceful fallback when `showPicker` is absent |
| `SakugaFile` (`FileField`) | `SakugaButton` trigger + hidden native file input + removable chips | upload pickers, import dialogs | hidden input keeps `name` and the real `FileList`; `accept`, `multiple`, `onFiles(File[])`; pick fires `impact`, remove fires `pop` |
| `SakugaMultiSelect` (`MultiSelect`) | combobox trigger + panel with a filtering `SakugaInput` and `SakugaCheckbox` rows | tags, enabled toolsets, notification channels, any multi-value setting | one hidden input per value under `name`; Escape/outside click close; focus returns to the trigger |
| `SakugaFormRow` (`FormRow`) | label + a control + the upstream click-help `SakugaTooltip` + optional description | dense settings rows that currently lack an explanation | `label`, `help`, `htmlFor`, `description`, children |
| `SakugaPagination` (`Pagination`) | `SakugaButton` prev/next + a compact `SakugaInput` page field + "of N" | long list pagination (sessions, logs, calls) | `page`, `pageCount`, `onChange`; commits on Enter/blur and clamps to the real range |

All of them keep the upstream palette and the compact chrome from
`sakuga.css` (buttons ~34px, fields ~37px). The extra CSS is limited to the
focus ring, the hidden number spinner, the help-bubble margin inside a row and
the page-field width; no canvas effect is scaled.

Gallery: `/sakuga-gallery` gained one card per extra (date and time share a
card), each printing the real emitted value. The standalone
`/sakuga-gallery.html` cannot run React, so it names the extras and links to
the dashboard gallery. Focused tests live in `src/sakuga/extras.test.tsx`
(render, typing, form values, steppers, showPicker, file chips/removal,
multi-select open/filter/toggle/Escape, form row help, pagination clamp).

Import paths for page work:

```ts
import {
  Textarea, NumberField, DateField, TimeField, FileField,
  MultiSelect, FormRow, Pagination,
} from "@/sakuga/ui";
// or, from the shared layer:
import { SakugaTextarea, SakugaNumber, SakugaDate, SakugaTime,
  SakugaFile, SakugaMultiSelect, SakugaFormRow, SakugaPagination } from "@qubit/shared/sakuga";
```

They supersede the documented native-control exceptions for `<textarea>`,
`type=number`, `type=date`/`type=time` and hidden-file proxies wherever a page
owner adopts them; the gallery's own combobox/command-palette exceptions are
unchanged.

## Composed-controls adoption sweep (2026-11-18, `feat/sakuga-use-web`)

The extras are now adopted on the dashboard pages rather than only in the
gallery. The native elements that remain are named with the reason.

- **Textarea (21 sites):** Profiles (description, description editor, soul
  editor), Profile Builder MCP env, Skills learn/notes, Skills Review payload,
  MCP env + invoke args, MCP calls args, Cron context ids + prompt, Voice Edit
  prompt / try-it text / import JSON, Canvas content, OpenViking memory text,
  the skill editor body, Voice Edit preview, AutoField text, the om field/
  workspace editors. Labels stay attached via the caller's existing `Label` or
  the textarea's own label. Hidden test ids were moved to a wrapper where a
  test depended on them.
- **NumberField:** Cron repeat/interval/day-of-month, Devices setup-code TTL,
  AutoField numeric values, om numeric fields. Raw number inputs that remain:
  - Voice Edit's tuning grid (17 inputs) is a dense label+hint+`data-testid`
    layout whose tests read `.value` from those exact nodes and whose values
    are string-typed update payloads; the number composition's own label and
    stepper row would duplicate the label and change the grid. Left native,
    tracked here rather than silently half-converted.
  - `SessionsPage` prune days uses the Sakuga `Input` with `type=number` (the
    host input, not a raw one).
- **DateField / TimeField:** Schedule Builder (once uses the date+time pair and
  still emits `YYYY-MM-DDTHH:MM`) and the automation blueprint time field.
- **Pagination:** Sessions page (overview and list) now uses `SakugaPagination`;
  the range label (`1–20 of N`) stays beside it.
- **Select:** the language switcher is now the Sakuga select; the bespoke
  desktop listbox, mobile bottom sheet and drop-up portal are gone.
- **Input + ListItem:** the command palette keeps Arrow/Enter/Escape and the
  active row, with the search field on `SakugaInput` and option rows on
  `ListItem` (`role=option`, `data-testid` preserved).
- **HoverTooltip:** config export/import/reset, session resume/rename/export/
  delete, files refresh/download/delete, chat terminal/debug/side-panel
  toggles. Hover and focus open the bubble, Escape closes it (browser-verified
  on `/config`: bubble text, then closed on Escape).

Documented native exceptions after this sweep:

- The four chat composer textareas across `TerminalChatPage`, `WebChatPage` and
  `QubitBotPage` (two) stay native `<textarea>`: Enter-to-send, Shift+Enter newline, image paste/
  drop, auto-resize and composer-only CSS are not exposed by `SakugaTextarea`,
  and wrapping them would either lose those handlers or add a second frame.
- The six hidden `type=file` inputs stay native: each is a programmatic picker
  behind an existing icon button (config import, chat attach, system import,
  files upload, kitchen folder including `webkitdirectory`, sessions import).
  `SakugaFile` always renders its own visible trigger and chips, which would
  duplicate the real button; the folder picker also needs `webkitdirectory`,
  which the composition does not expose.
- No `type=color` inputs exist in the dashboard.
- `MultiSelect` was not adopted: the only multi-value checkbox surface is the
  sessions source filter, which carries per-source counts and icons and a
  null-means-all default; the generic select would drop that information.
- Tabs: existing `Tabs` strips already animate; the gallery verifies selection
  lands (`selected`/`aria-selected` move on click). Three custom tab-strip
  buttons in Agent Workspace / Agent Cards / Kitchen stayed native because
  their tests address real `button[role=tab]` nodes and their strips carry
  counts/icons that the Sakuga tab options cannot express.

## Pass E — remaining web controls + achievements plugin (2026-11-18)

Owner ask: "find even more places". This pass closes the form/upload/disclosure gaps
and converts the bundled Achievements plugin.

### Achievements plugin (bundled IIFE, not a React source tree)

Changed both copies (`qubit_runtime/plugins/qubit-achievements/dashboard/dist/` and the
served `frontend/qubit-web/public/plugins/qubit-achievements/dist/`):

- A small local loader injects `/sakuga-vfx/button/button.js` and
  `/sakuga-vfx/progress/progress.js` once and renders `sakuga-button` /
  `sakuga-progress` with the SDK's single React runtime.
- `SakugaProgress` writes `value` as an **attribute** from a ref (the property is a
  read-only getter, so React assigning it would throw); verified live with an
  in-progress payload: `value="0.5"`, `el.value === 0.5`, no page error.
- Converted: Rescan/Reset/Confirm reset/Cancel, the category and visibility filter
  pill strips, share trigger, Share-on-X / Copy image / Download PNG, the share-close
  button, and the in-flight scan banner bar.
- Added a hover/focus/tap/Escape tooltip to the tier badge (reuses the dashboard's
  shared `.qsk-hover-tip` classes). The plugin's API calls, i18n, share canvas,
  badges, links and card grid are unchanged.
- Kept native: the per-card progress **metric bar** (dense data visualisation inside
  each card, not an action or a busy indicator; 60 cards would otherwise each carry
  poster-sized progress chrome). The filter strips are wrapping pill lists, not a
  tab strip, so they use Sakuga buttons with `aria-pressed` rather than
  `sakuga-tabs` (which does not wrap).
- `node --check` passes on both copies. Measured in the dev browser: 81 Sakuga
  controls on the page, 0 native light-DOM buttons inside `.ha-page`, filter click
  60 → 10 cards with `aria-pressed=true`, tier tooltip opens and Escape closes it,
  share dialog opens with 3 Sakuga actions + Sakuga close, scan progress at 50%.

### Kanban plugin (bundled IIFE, not a React source tree)

Changed both copies (`qubit_runtime/plugins/kanban/dashboard/dist/` and the
served `frontend/qubit-web/public/plugins/kanban/dist/`). The two copies keep
their own live path (WebSocket tail vs. the Qubit single-event poll); only the
controls were swept.

- A small local loader injects `/sakuga-vfx/{button,input,checkbox,progress}/…js`
  once and renders `sakuga-*` tags with the SDK's single React runtime. No second
  React, no vendored edit.
- Converted: **41** `Button` uses plus **21** native `<button>`s to
  `sakuga-button`; **21** `Input` to `sakuga-input`; **13** `Select` plus **2**
  native `<select>`s to `SakugaSelect`; **7** `Checkbox` plus **2** native
  checkboxes to `sakuga-checkbox`; the initial load state to `sakuga-progress`.
- `SakugaSelect` is the documented composition: the upstream set has no select,
  so it renders the same option model as a native `<select>` inside the Sakuga
  frame (`qsk-select__native`). Keyboard, form and screen-reader behaviour stay
  native; the DS `SelectOption` children and the raw `<option>`/`<optgroup>`
  children are both understood.
- `SakugaProgress` writes `value` as an **attribute** from a ref (the property is
  a read-only getter). The board's loading state uses the upstream `demo`
  self-play so it never invents a percentage.
- Hover/focus/tap/Escape `HoverTip` (shared `.qsk-hover-tip` classes) on the
  icon-only actions (`?` docs, column add, drawer close, attention dismiss,
  project unbind, dependency remove) and the dense toolbar help labels (Nudge
  dispatcher, Clear filters).
- Preserved: drag-and-drop and the touch drag threshold, per-copy live updates,
  markdown, i18n, every API call, `data-*`/ARIA labels and handlers.
- Rollback: set `localStorage["qubit:sakuga-plugin-ui"] = "off"` (or
  `window.__QUBIT_SAKUGA_PLUGIN_UI__ = false`) and reload — every helper renders
  its native control and `HoverTip` renders only its trigger. No rebuild needed.
- Kept native: `type="number"` inputs (outside the upstream text-input set), the
  hidden `<input type="file">` proxied by the Sakuga upload button, the
  description/comment `<textarea>`, and the card's `N/M` child-progress pill
  (a dense textual metric, not a bar). The board has no tab strip; its filters
  are the search input, two selects and the archive/lane checkboxes, all swept.

Audit of the served plugin UIs: only two dashboard plugin bundles ship UI —
`qubit-achievements` (already converted) and `kanban` (this section). The other
catalog entries expose backend tools only, and `qubit-office` is the iframe app
handled by the Office lanes, not a `plugins/*/dashboard/dist` bundle.

Evidence: `node --check` passes on both bundles. A jsdom harness that loads the
real vendored components and renders the board with fixture data measured **13**
`sakuga-button`, **1** `sakuga-input`, **10** `sakuga-checkbox`, **3** framed
selects and **9** hover tips, with **0** native buttons, **0** native text
inputs, **0** native checkboxes and **0** console errors; typing `First` in the
filter reduced the cards 2 → 1, the Show-archived checkbox toggled and reflected
`checked`, the loading `sakuga-progress[demo]` appeared before the board and was
replaced on load. The rollback flag produced 13 native buttons / 1 native text
input / 10 native checkboxes / 0 Sakuga hosts on the same fixture.

Also verified in a real Chromium against the live `:8890` dashboard with only
the two plugin assets route-intercepted from this worktree (the backend, auth
and Sakuga scripts stayed live): **0** console/page errors, **17** `sakuga-button`
and **0** light-DOM `<button>` inside `.qubit-kanban`, **1** Sakuga filter input
(typing set `el.value="abc"`), **6** Sakuga checkboxes, **2** framed selects and
**7** hover tips; the docs tooltip opened on hover and closed on Escape; the
Show-archived checkbox went `false → true`; the inline create dialog had **0**
light-DOM native buttons and **0** native checkboxes, with **2** Sakuga inputs,
**2** Sakuga buttons and **2** framed selects (its native number input and
`<textarea>` are the documented exceptions). A delayed board request showed the
visible `sakuga-progress[demo][label="Loading Kanban board…"]`, which was
replaced by the board on load. This is still not a deploy: the live `:8890`
frontend continues to serve its installed plugin copy.

### Web dashboard controls

- **Voice Edit** — all 17 numeric tuning inputs are `NumberField` (Sakuga frame +
  steppers). String-backed fields (wake/session/continuous) stay uncontrolled with
  empty/raw-string passthrough and remount only when server settings are applied
  (`numericEpoch`), so typing `0.0025` is not parsed/reset mid-keystroke; test ids
  travel through `inputProps`. Settings-backed fields are controlled and clamp as
  before. Live check: `0.0025` stayed in the field; 32 steppers rendered.
- **Composers** — `ChatPage` (`#qubit-chat-input`), `WebChatPage`, `QubitBotPage`
  now use `Textarea` with `autoResize`, `onKeyDown`, `onPaste`/`onDrop` preserved.
  Live check: chat composer grew 56 → 108 px on five lines. `TerminalChatPage` has
  no app textarea — its composer is xterm's hidden textarea (third-party exception).
- **Hidden uploads** — Config import, Chat attach, System restore zip, Files upload,
  Kitchen folder (`directory` + `multiple`) and Sessions import are `FileField` with
  `showList={false}`, the existing icon/text trigger as children, and the old refs
  retained (the Files drop surface still opens the same input). Upload/import
  handlers now take `File[]`.
- **Listbox / disclosures** — the ChatPage slash-command option rows use
  `ListItem` (role/aria-selected/keyboard handled by the composer as before);
  ThemeSwitcher and SlashPopover already used `ListItem`. Logs line disclosure and
  the ToolCards "Explore full result" button are Sakuga controls; the Files drop
  surface is a `ListItem` inside a drag/drop wrapper.
- **Busy states** — Analytics' settings skeleton and WebChat's opening session are
  `SakugaLoading`; Agents/Models/Sessions/Config/System already were.
- **More tooltips** — icon/refresh/action hints added on Analytics, Models, Agents,
  Profiles, Files upload, Chat attach/voice, and Channel Test/Configure; Config
  already used them. Hover/focus/tap/Escape verified on Models (`Refresh`) and the
  Achievements tier badge.

### Evidence

- `npx tsc -b` clean; `npx vitest run` **171 files / 1352 tests passed**.
- `python3 scripts/check_sakuga_vendor.py` OK (2 copies);
  `python3 scripts/sync_sakuga_shared.py --check` OK (17 files).
- Browser: temp Vite dev server `:8891` proxying live `:8890`; the plugin entry/CSS
  were route-intercepted from this worktree because the live backend still serves
  the installed copy. Routes `/achievements`, `/config`, `/sessions`, `/files`,
  `/models`, `/plugins`, `/system`, `/chat`, `/qubit-chat`, `/skills/review`,
  `/voice-edit`: no app console/page errors and no failed API requests. The only
  console noise was the dev server's `@fs` font allowlist 403 for two DS fonts
  (outside Vite's serving allow list; not a product error). Not deployed to the live
  service — `:8890` still serves the previous bundle and installed plugin copy.

### Exceptions after this pass

| Control | Reason |
|---|---|
| Achievements card progress bar | dense per-card metric visualisation (see above) |
| `TerminalChatPage` composer | xterm's hidden textarea (third-party widget internals) |
| `SakugaGalleryPage` pill buttons | the Sakuga gallery's own raw demo controls |
| Radix portal menus/dialog internals | third-party widget internals; their own triggers are Sakuga |
| `type="number"` inside `SakugaNumber`, `Textarea` inside `SakugaTextarea`, `<input type=file>` inside `FileField` | these are the native controls the Sakuga extras intentionally compose |
