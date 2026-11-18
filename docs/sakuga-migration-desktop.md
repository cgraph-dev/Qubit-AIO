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

## Core component fixes (2026-11-18)

Mirrored from the web layer. `src/sakuga/sakuga.css` carries the same compact
`::part()` chrome (button 5px 12px / 13px; input 7px 12px / 14px; compact tabs
and progress chip; toggle gallery top margin removed) so desktop rows match the
dashboard. `SakugaLoading` is now used for the boot/connecting overlay
(`components/gateway-connecting-overlay.tsx`, determinate from
`boot.progress`), every page loader (`components/page-loader.tsx`), and the
indeterminate phase of the update overlay (`app/updates-overlay.tsx`).
Real-outcome cues fire on gateway connect/failure and update done/error. The
shared wrapper fix (idempotent attribute writes, read-only progress value) is
synced into `qubit_runtime/apps/shared` by `scripts/sync_sakuga_shared.py`.

## Composed extras pass (2026-11-18)

The shared extras (`frontend/qubit-shared/src/sakuga/extras`) are re-exported
from `@/sakuga/ui` as `Textarea`, `NumberField`, `DateField`, `TimeField`,
`FileField`, `MultiSelect`, `FormRow`, `Pagination` and `HoverTooltip`.
`qubit_runtime/apps/shared` is kept byte-identical by
`scripts/sync_sakuga_shared.py --check`.

| Control | Where | Notes |
|---|---|---|
| Textarea | `components/ui/textarea.tsx` (every consumer), starmap share code, goal criterion | The shared wrapper is now the composed `SakugaTextarea`: real native textarea, Sakuga frame, type/focus cues. Native `onChange` shape and `ref` are preserved, `onBlur`/`onKeyDown`/`onPaste` are attached to the inner element, and the frame label stays empty when the caller already provides one. |
| NumberField | plugin settings `NumberControl` | Label comes from the surrounding field; the value is reported as a number and stringified for the draft store. |
| TimeField | cron blueprint `time` slot | Replaces the native `type="time"` input. |
| FileField | settings config import, pet reference photo, kanban attachment upload | Rendered with `className="hidden"` so the existing icon/action button still triggers the real input through the forwarded ref; the field owns the native `FileList`. |
| Pagination | artifacts list | The numbered shadcn strip was replaced by the composed previous/next + page-field control; the item-count label stays. |
| HoverTooltip | artifacts refresh icon, settings footer export/import/reset icons | Hover/focus/tap opens, Escape closes, and the effect fires from the real element. |

### Remaining exceptions (unchanged)

- **Assistant-ui binding textareas** (`app/chat/composer/index.tsx`,
  `components/assistant-ui/thread/user-edit-composer.tsx`): these are the
  invisible `ComposerPrimitive.Input asChild` controls that wire the composer
  state. A composed control would replace the bound native element, so they stay
  native (two `<textarea>` tags).
- **Inline numeric settings rows** (`settings/billing`, `settings/pool-limits`,
  `settings/sessions-settings`, `settings/memory`, `settings/config-field`,
  `plugins/kanban/board`, `components/pane-shell/tree/zone-editor`): the row
  owns the label and persists on blur; the native number input keeps the
  commit-on-blur/Enter contract. `NumberField` is used where the field owns its
  own label.
- **Radix `Tabs` / `Tooltip`** keep composite ARIA/portal geometry; `HoverTooltip`
  is used only where a light-DOM trigger is enough.
- **Indeterminate checkbox, non-plain progress, composite search fields,
  editors/canvas/xterm, nav links** remain as documented in the earlier passes.

## Second extras pass (2026-11-18)

| Unit | Files | Notes |
|---|---|---|
| Number fields | `settings/config-field.tsx`, `plugins/kanban/board.tsx`, `components/pane-shell/tree/zone-editor.tsx` | Schema numbers, kanban priority and the template zone count now render `NumberField`. Callers that commit on blur or own a prefixed/inline label stay native (see exceptions). |
| Download progress | `settings/local-model-download-progress.tsx` | The plain determinate meter is the shared `Progress`, which is the vendored Sakuga bar; an unknown endpoint keeps the animated native track (`indeterminate`), and a paused job keeps its muted fill. |
| Icon tooltips | `settings/providers-settings.tsx`, `settings/connections-registry.tsx` | The remaining icon-only disconnect/remove/edit actions gained `HoverTooltip`; the broader set that already used Radix `Tip` keeps its portalled tooltip. |

`npm run build` exits 0; `tsc -p tsconfig.json --noEmit` reports only the 8
known pre-existing errors (none in the touched files).

### Remaining exceptions (second pass)

- **Inline numeric settings rows** (`settings/billing`, `settings/pool-limits`,
  `settings/sessions-settings`, `settings/memory`, `settings/config-settings`):
  these wrap the number input in their own label, carry a unit/`$` adornment, or
  persist on blur. `NumberField` has its own label row and no `onBlur` passthrough,
  so those stay native.
- **Assistant-ui binding textareas** (two tags) remain native; see above.
- **Radix `Tabs` / `Tooltip`** keep composite ARIA/portal geometry.
- **Indeterminate checkbox, non-plain progress, composite search fields,
  editors/canvas/xterm, nav links** remain as documented.
