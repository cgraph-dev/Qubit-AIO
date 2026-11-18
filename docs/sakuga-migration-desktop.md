# Sakuga migration — Electron desktop

**Scope:** `qubit_runtime/apps/desktop/src/**` (renderer only). The web dashboard
(`frontend/qubit-web`) and the Office plugin are separate lanes.

**Status:** components sweep complete (pass C), honest accounting below. The
renderer loads the vendored engine, installs the `qubit:fx` bridge, and has
adopted the Sakuga toggle (every settings boolean row), the slider, the single
`<select>`, and the product outcome cues. Pass C then replaced every button in
`src/components/**` and `src/contrib/**`, the shared text-input/checkbox/switch
primitives, and determinate progress. Remaining exceptions are the composite
widgets and unsupported states listed at the end.

## What landed

| Unit | Files | Notes |
|---|---|---|
| Loader + bridge | `src/main.tsx`, `src/sakuga/base.ts`, `src/sakuga/ui.tsx`, `src/sakuga/sakuga.css` | `configureSakuga` + `installFxBridge` at module load, `loadSakugaCore` warmed after first paint, `SakugaToastHost` mounted once. |
| Test harness | `src/test/sakuga-elements.ts`, `vitest.setup.ts` | jsdom doubles for the upstream tags (open shadow root + inner native control + bubbling `change`) and a no-op engine, so wrapper tests exercise real wiring. They never ship. |
| Settings booleans | `src/app/settings/primitives.tsx:232` (`ToggleRow`) | One implementation covers **all 32 `<ToggleRow>` call sites**; the old Radix `Switch` is gone from this row. |
| Range control | `src/components/ui/slider.tsx` | `SakugaSlider` behind the old `Slider` API (maps `onChange` back to `event.target.value`). Used at 4 sites (appearance translucency ×3 + pet scale). |
| Select | `src/app/quick-entry/quick-entry-app.tsx:187` | The renderer's only `<select>` (`Target session`) is now `SakugaSelect`; `grep -rn '<select' src --include=*.tsx` is **0**. |
| Effects | `message-stream.ts`, `server-requests.ts`, `composer/index.tsx` | send → `impact`; reply complete → `success`; failed terminal frame → `error`; billing/history warning → `warning`; approval requested → `approval`; reaction → `celebrate`. |
| Destructive actions | `src/components/ui/button.tsx` | Every `variant="destructive"` button fires `warning` on press instead of the default `impact`. |
| Shared Button | `src/sakuga/ui.tsx`, `src/components/ui/button.tsx` | `Button` renders the vendored `sakuga-button`; the desktop adapter forwards every native prop/event (drag, pointer, double-click, context menu) and stringifies `aria-*`/`data-*` booleans, which React would otherwise write as empty on a custom element. `asChild` still renders links; `type="submit"` calls `form.requestSubmit()`. |
| Native buttons | `src/components/**`, `src/contrib/**` | **0 production `<button>` tags remain** (one doc-comment example in `inline-preview-directive.tsx`). 245 `<Button>` uses across the two trees; `RowButton`, pane tabs, disclosure/tool rows, message chips and onboarding cards are all the vendored control. |
| Text inputs | `src/components/ui/input.tsx` | The shared `Input` renders `sakuga-input` (label derived from `aria-label`/placeholder/id, `aria-labelledby` when an id has a `Field` label). `onChange` keeps the event-shaped contract and also accepts the host's `change`; unsupported types (number/date/file/hidden/colour/range) keep the native input. |
| Checkbox / switch | `src/components/ui/checkbox.tsx`, `switch.tsx` | Boolean checkboxes and switches render the vendored controls; `checked`/`onCheckedChange`/`size` keep their old names. `label` supplies the toggle's accessible name. |
| Determinate progress | `src/components/ui/progress.tsx` | A plain determinate meter is `sakuga-progress`; indeterminate, the sliding pet animation, destructive fill, custom fill or track children keep the native bar. |

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

| Control | Where (example) | Count | Reason / next step |
|---|---|---|---|
| Unsupported input types | `src/components/ui/input.tsx` fallback | 3 in scope | Upstream `sakuga-input` only accepts text/email/search/url/tel/password. Number/date/file/hidden/colour/range keep the native element (the number spinner in `zone-editor`, the colour swatch in `onboarding-chat/options`, etc.). |
| Indeterminate checkbox | `src/components/ui/checkbox.tsx` fallback | provider header | The vendored checkbox has two states; the tri-state provider selector keeps the native control. |
| Non-plain progress | `src/components/ui/progress.tsx` fallback | 2 in scope | Unknown endpoint, the sliding pet animation, destructive fill, custom fill/track children have no upstream equivalent. |
| Composite search fields | `src/components/find-bar.tsx`, `src/components/ui/search-field.tsx`, `src/components/ui/dropdown-menu.tsx` (menu filter), `src/components/model-visibility-dialog.tsx` (inline filter) | 4 | Bespoke focus/clear/find-in-page widgets with their own keyboard behaviour, not ordinary labelled text fields. The design keeps composite widgets on their own semantics; revisit with `SakugaInput` if the callers accept the upstream look. |
| Tabs | `src/components/ui/tabs.tsx` (Radix `Tabs`) | 0 in scope | Every usage is under `src/app/**` (parallel lane). A Sakuga swap needs a call-site rewrite (the upstream component owns its strip, so it cannot compose with `TabsContent`); no component in this pass uses it. |
| Select | `src/components/ui/select.tsx` | 1 in scope | `catalog-advanced-dialog` uses the composite Radix select; the design keeps composite ARIA widgets and the desktop has no native `<select>`. |
| Tooltips | `src/components/ui/tooltip.tsx` (`<Tip>`) | — | Composite Radix hover/focus geometry with portals; Sakuga's tooltip is a click label. No standalone help-trigger exists in the swept tree, so nothing was swapped. |
| Textarea | raw `<textarea>` | 2 in scope | No upstream textarea component; stays native. |
| Hidden file pickers | `src/app/settings/config-settings.tsx`, `src/plugins/kanban/drawer.tsx`, `src/app/pet-generate/pet-generate-content.tsx` | 0 in scope | The three known pickers live under `src/app/**`/`src/plugins/**`, outside this pass; the Button proxy pattern is ready once that lane reaches them. |
| Editors / canvas / xterm | Monaco/code editors, transcript canvas, terminal | — | Documented boundary: editors and canvas controls stay native. |
| Nav `<a>` links | router links throughout | 33 | Documented exception; `Button asChild` keeps them links. |

## Verification run for this lane

```bash
cd qubit_runtime/apps/desktop
npx vitest run --project ui src/components src/contrib   # 247 files, 1532 tests; 1 pre-existing failure
npx vitest run --project ui src/sakuga/sakuga.test.tsx   # 9 passed
npx tsc -p tsconfig.json --noEmit                        # only the 8 known pre-existing errors
```

Pass C covered (per `grep` over production files in `src/components` +
`src/contrib`):

| Control | Count |
|---|---|
| `<Button>` uses | 245 |
| Production native `<button>` tags | 0 (one doc-comment example) |
| `<Input>` uses | 20 |
| `<Switch>` uses | 3 |
| `<Checkbox>` uses | 3 |
| `<Progress>` uses (2 determinate swap, others native fallback) | 4 |
| Native `type="file"` inputs | 0 |

The one remaining failure is the pre-existing `assistant-ui/thread/streaming.test.tsx`
"renders assistant text incrementally before completion": the test looks for a
`role="status"` named "Hermes is loading a response" while the product renders
the canonical Qubit string (and the status row carries no matching role). It is
unrelated to controls and reproduces on the lane base.

The pre-existing typecheck errors are the five files named in the task
(`session-tile`, `use-quick-entry-bridge`, `onboarding-kickoff`,
`quick-entry-app`, `slash`); this lane adds none.

`npm run build` was not run in this lane; the renderer build is the same Vite
pipeline and the lane only adds source modules and a public asset path already
shipped by the foundation commit.

## Rollback

Revert the integration commits. The vendored `public/sakuga-vfx/` files are
inert static assets; no service, config, or permission change is involved.
`installFxBridge`/`emitFx` are presentation-only and swallow engine failures.
