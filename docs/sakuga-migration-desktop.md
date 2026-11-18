# Sakuga migration — Electron desktop

**Scope:** `qubit_runtime/apps/desktop/src/**` (renderer only). The web dashboard
(`frontend/qubit-web`) and the Office plugin are separate lanes.

**Status:** three passes landed; honest accounting below. The renderer loads the
vendored engine, installs the `qubit:fx` bridge, and uses the upstream controls
for app buttons, shared-component buttons, settings toggles, sliders, selects,
boolean checkboxes, determinate progress and product outcome cues. Remaining
exceptions are the composite widgets and unsupported states listed at the end.

## What landed

| Unit | Files | Notes |
|---|---|---|
| Loader + bridge | `src/main.tsx`, `src/sakuga/base.ts`, `src/sakuga/ui.tsx`, `src/sakuga/sakuga.css` | `configureSakuga` + `installFxBridge` at module load, `loadSakugaCore` warmed after first paint, `SakugaToastHost` mounted once. |
| Test harness | `src/test/sakuga-elements.ts`, `vitest.setup.ts` | jsdom doubles for the upstream tags (open shadow root + inner native control + bubbling `change`) and a no-op engine, so wrapper tests exercise real wiring. They never ship. |
| Button adapter | `src/sakuga/ui.tsx` | Drop-in `Button` keeps `variant`/`size`/`asChild`/`loading`, forwards Radix pointer/key/data props and ARIA booleans, focuses the host, and submits the owning form for `type="submit"`. `asChild` renders links unchanged. |
| Settings booleans | `src/app/settings/primitives.tsx` (`ToggleRow`) | One implementation covers **all 32 `<ToggleRow>` call sites**; the Radix `Switch` is gone from that row. |
| Range control | `src/components/ui/slider.tsx` | `SakugaSlider` behind the old `Slider` API. Used at 4 sites (appearance translucency ×3 + pet scale). |
| Select | `src/app/quick-entry/quick-entry-app.tsx` | The renderer's only `<select>` is now `SakugaSelect`; `grep -rn '<select' src --include=*.tsx` is **0**. |
| Effects | `message-stream.ts`, `server-requests.ts`, `composer/index.tsx` | send → `impact`; reply complete → `success`; failed terminal frame → `error`; billing/history warning → `warning`; approval requested → `approval`; reaction → `celebrate`. |
| Destructive actions | `src/components/ui/button.tsx` | Every `variant="destructive"` button fires `warning` on press (20 call sites) instead of the default `impact`. |
| App surface sweep (pass B) | `src/app/**` | **181** files import the adapter; **524** `<Button>` uses. **113** native `<button>` tags replaced (all non-test native buttons under `src/app`), **22** remaining `<Switch>` sites became `Toggle`, and **6** `<Checkbox>` uses moved to the upstream control. No non-test `<Switch>` remains. |
| Shared components (pass C) | `src/components/**`, `src/contrib/**` | **0 production `<button>` tags remain** (one doc-comment example in `inline-preview-directive.tsx`). 245 `<Button>` uses across the two trees; `RowButton`, pane tabs, disclosure/tool rows, message chips and onboarding cards use the vendored control. |
| Shared Input / Checkbox / Switch / Progress | `src/components/ui/{input,checkbox,switch,progress}.tsx` | `Input` renders `sakuga-input` (label derived from `aria-label`/placeholder/id, `aria-labelledby` when an id has a `Field` label); boolean checkboxes/switches render the vendored controls; a plain determinate meter is `sakuga-progress`. Unsupported types/states keep native. |
| Plugin control sweep (pass D) | `src/plugins/**` | **10** native `<button>` tags replaced: kanban `ui.tsx` (status trigger), `plugin.tsx` (status-bar count), `board.tsx` (collapsed lane, collapse, lane add), `drawer.tsx` (assignee trigger, link chips); hermes-bots `user-sections-ui.tsx` (section options), `screen-hero.tsx` (hero), `screen-portal.tsx` (portal row). The **6** boolean `<Switch>` sites (`kanban/board`, `kanban/orchestration`, `hermes-bots/cron`, `model-visibility-dialog`, `catalog-advanced-dialog` ×2) already render through the shared Sakuga `Toggle`; `cron` gained a derived `label`. **0** text-like native `<input>` remain in `src/plugins`. |
| Test migration | `src/app/**/*.test.tsx`, `src/components/**/*.test.tsx` | Native-`button` selectors widened to `[role="button"]`; toggle/checkbox wiring exercised through the real shadow-double contract. No test deleted or weakened. |

Base URL that works in both shapes: `sakugaBaseUrl(document.baseURI)` →
`new URL('sakuga-vfx/', document.baseURI).href` (`src/sakuga/base.ts`). It
resolves to `http://127.0.0.1:5174/sakuga-vfx/` under `vite dev` and
`file:///…/sakuga-vfx/` under `loadFile`. `import.meta.env.BASE_URL` is `./`
and is not a usable script prefix.

## Retired particle usage

The old heart particle burst is no longer fired from the message-stream surface:
`gateway-event/message-stream.ts` imports `emitFx` instead of `burstVibeHearts`,
so a reaction now plays Sakuga's `celebrate` cue. The particle module itself
(`src/components/chat/vibe-hearts.tsx`, `src/components/particles/particle-field.tsx`)
is **kept**: the pet overlay still calls `playVibeHearts()` and the pet sprite
still imports the field. Removing it would break a surface that has not adopted
Sakuga.

## Not replaced (documented exceptions / remaining work)

The plugin sweep added no new exception. One structural note: the collapsed
kanban lane is still a native **drag-and-drop target**, so its `dragenter`/
`dragover`/`dragleave`/`drop` handlers stay on the plain wrapper and the
vendored `Button` is the click control inside it (`src/plugins/kanban/board.tsx`,
collapsed-lane branch). The shared Sakuga host forwards pointer and key events,
not drag events, so moving the handlers onto it would silently disable drops.

| Control | Where (example) | Count | Reason / next step |
|---|---|---|---|
| Unsupported input types | `src/components/ui/input.tsx`, `src/app/**` | large | `sakuga-input` only accepts text/email/search/url/tel/password. Number/date/time/datetime-local/file/hidden/colour/range keep native. App text inputs that use `prefix`/`suffix` adornments or are typed through native `fireEvent.change` need a label-derivation + shadow-typing pass. |
| Indeterminate checkbox | `src/components/ui/checkbox.tsx` | provider header | The vendored checkbox has two states; the tri-state provider selector keeps native. |
| Non-plain progress | `src/components/ui/progress.tsx` | app + components | Unknown endpoint, the sliding pet animation, destructive fill, custom fill/track children have no upstream equivalent. |
| Composite search fields | `find-bar`, `ui/search-field`, dropdown filters | — | Bespoke focus/clear/find-in-page keyboard behaviour; composite semantics stay. |
| Radix `Tabs` / `Tooltip` | `src/components/ui/{tabs,tooltip}.tsx` | 4 Tabs, 133 `<Tip>` | Composite ARIA widgets with hover/focus/portal geometry; Sakuga's tooltip is a click help-trigger, so the composite stays and cues fire around it. |
| Textarea | 4 raw `<textarea>` | 4 | No upstream component. |
| Hidden file pickers | `config-settings.tsx`, pet generate/overlay | 3 | Still native hidden inputs; proxy-button work remains. |
| Editors / canvas / xterm | Monaco code editor, transcript canvas, terminal views | — | Documented boundary. |
| Nav `<a>` links | `Button asChild` renders the anchor unchanged | — | Documented exception. |
