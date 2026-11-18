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

## Effects wiring

Effects are presentation only. Pages fire a cue for real outcomes only:
success after a successful save/load action, `error` on a caught API/validation
failure, `warning` for destructive or revoking actions, `approval` for staged
approvals. Buttons fire `impact` by default from the wrapper.
