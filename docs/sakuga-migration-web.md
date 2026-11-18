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
| `src/pages/CanvasPage.tsx` | canvas controls | canvas exception |
| `src/pages/FilesPage.tsx` (`Drop files here` surface) | drag-and-drop upload button | the wrapper exposes no drag/drop props; a drop target is not a plain control. The hidden `<input type=file>` stays native and is proxied by the Sakuga Upload button (owner exception). |
| `src/pages/FilesPage.tsx`, `src/pages/ConfigPage.tsx`, `src/pages/SystemPage.tsx` | hidden `<input type=file>` | hidden file inputs are proxied by a Sakuga Button (owner exception) |
| `src/components/AutoField.tsx` (rendered by Config) | auto-generated config fields | component owned by another lane; documented rather than edited |
| `src/components/DeleteConfirmDialog.tsx`, `ConfirmDialog.tsx` | confirm dialogs | component-owned; their buttons are outside this file scope |
| `src/pages/LogsPage.tsx` (`Log line N detail` rows) | per-line disclosure | dense monospace text rows, not an action affordance; a Sakuga button per line would add panel chrome to log output. Toolbar controls are Sakuga. |
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
| ModelsPage | 20 | 1 | 1 | 1 |
| PluginsPage | 19 | 4 | 4 | 3 |
| ChannelsPage | 20 | 3 | 0 | 1 |
| ConfigPage | 10 | 1 | 2 | 1 |
| SystemPage | 37 | 8 | 1 | 2 |
| WebhooksPage | 14 | 4 | 0 | 0 |
| LogsPage | 5 | 1 | 0 | 1 |

**Not yet swept (controls remain).** These are unimplemented work, not
accepted exceptions:

`AgentWorkspacePage`, `BoardAdminPage`, `CanvasPage` (canvas controls are an
exception; its toolbar is not), `CheckpointsPage`, `CronPage`, `DevicesPage`,
`EnvPage`, `HarnessRouterPage`, `KitchenPage`, `McpCallsPage`, `McpPage`,
`MemoryBackendsPage`, `NodesPage`, `OmniRoutePage`, `OpenVikingAgentHubPage`,
`OpenVikingPage`, `OpenVikingResourcesPage`, `OpenVikingRetrievalPage`,
`OpenVikingStudioPage`, `OpenVikingUploadsPage`, `ProfileBuilderPage`,
`ProfilesPage`, `QubitBotPage`, `SchedulerMediaPage`, `SessionOpsPage`,
`SkillsPage`, `SkillsReviewPage`, `TalkPage`, `TaskflowsPage`, `TaskInboxPage`,
`TasksPage`, `UsersAdminPage`, `VoiceEditPage` (xterm/audio internals are an
exception; its forms are not), `WebChatPage`, `WorktreesPage`,
`src/plugins/PluginPage.tsx`.

Excluded by the task scope: `ChatPage.tsx`, `TerminalChatPage.tsx`,
`SessionsPage.tsx`, `src/App.tsx`, `src/components/**`.
