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
