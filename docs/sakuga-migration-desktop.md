# Sakuga migration — Electron desktop

**Scope:** `qubit_runtime/apps/desktop/src/**` (renderer only). The web dashboard
(`frontend/qubit-web`) and the Office plugin are separate lanes.

**Status:** partial sweep, honest accounting below. The renderer now loads the
vendored engine, installs the `qubit:fx` bridge, and has adopted the Sakuga
toggle (every settings boolean row), the slider, the single `<select>`, and the
product outcome cues. Buttons, text inputs, checkboxes, the 33 remaining
`<Switch>` sites, tabs, progress bars, tooltips/textarea, and the file-picker
proxies are **not** replaced yet; each group is listed with file:line and the
reason it was deferred.

## What landed

| Unit | Files | Notes |
|---|---|---|
| Loader + bridge | `src/main.tsx`, `src/sakuga/base.ts`, `src/sakuga/ui.tsx`, `src/sakuga/sakuga.css` | `configureSakuga` + `installFxBridge` at module load, `loadSakugaCore` warmed after first paint, `SakugaToastHost` mounted once. |
| Test harness | `src/test/sakuga-elements.ts`, `vitest.setup.ts` | jsdom doubles for the upstream tags (open shadow root + inner native control + bubbling `change`) and a no-op engine, so wrapper tests exercise real wiring. They never ship. |
| Settings booleans | `src/app/settings/primitives.tsx:232` (`ToggleRow`) | One implementation covers **all 32 `<ToggleRow>` call sites**; the old Radix `Switch` is gone from this row. |
| Range control | `src/components/ui/slider.tsx` | `SakugaSlider` behind the old `Slider` API (maps `onChange` back to `event.target.value`). Used at 4 sites (appearance translucency ×3 + pet scale). |
| Select | `src/app/quick-entry/quick-entry-app.tsx:187` | The renderer's only `<select>` (`Target session`) is now `SakugaSelect`; `grep -rn '<select' src --include=*.tsx` is **0**. |
| Effects | `message-stream.ts`, `server-requests.ts`, `composer/index.tsx` | send → `impact`; reply complete → `success`; failed terminal frame → `error`; billing/history warning → `warning`; approval requested → `approval`; reaction → `celebrate`. |
| Destructive actions | `src/components/ui/button.tsx:111` | Every `variant="destructive"` button fires `warning` on press (20 call sites) without changing the native element. |

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
| Native `<button>` + the `Button` primitive | `src/components/ui/button.tsx`; `src/app/shell/wslg-window-controls.tsx:49`; `src/app/chat/composer/attachments.tsx:149`; `src/app/chat/composer/trigger-popover.tsx:204` | ~280 raw tags; ~669 `<Button>` uses | Deferred: the Sakuga button's inner shadow `<button>` cannot submit the outer `<form>`, and `asChild` renders nav `<a>` links (documented exception). 98 existing tests assert `querySelector('button')`/`tagName`. A follow-up must (a) route submit controls through `form.requestSubmit()` and (b) migrate those tests to the shadow helpers. |
| Text inputs | `src/components/ui/input.tsx` (147 `<Input>` uses), 26 raw `<input>` | 173 | Deferred: the shared `SakugaInput` API requires a `label`; `Input` consumers rely on associated `<label htmlFor>` and on `userEvent.type` against a native input. Adopting needs a label-derivation pass and rewriting the input-heavy tests to `typeIntoSakuga`. |
| Checkbox | `src/components/ui/checkbox.tsx` (`<Checkbox>` 20 uses) | 20 | Deferred with the input lane: same typing/click test migration. |
| Remaining switches | `src/plugins/kanban/board.tsx:793`, `src/plugins/kanban/orchestration.tsx:167`, `src/plugins/hermes-bots/cron.tsx:569`, `src/app/capabilities/connectors/connector-dialog.tsx:180` | 33 | Not all switches go through `ToggleRow`. Same adapter as `ToggleRow` is a mechanical follow-up. |
| Tabs | `src/components/ui/tabs.tsx` (Radix `Tabs`, 4 usage sites) | 4 | Deferred: composite ARIA widget with keyboard semantics; the design allows keeping composite semantics and firing cues around the interaction. |
| Progress | `src/components/ui/progress.tsx` (13 uses, incl. `ProgressBar` in local-model downloads) | 13 | Deferred: `indeterminate`/`animated`/`destructive` have no upstream equivalent; a determinate-only swap would silently drop those states. Long-job completion already plays `success` via the turn outcome. |
| Tooltips | `src/components/ui/tooltip.tsx` (`<Tip>` 218 uses) | 218 | Deferred: Radix hover/focus geometry and portalled placement; Sakuga's tooltip is a click label. Keep the composite and fire cues around it. |
| Textarea | 20 raw `<textarea>` | 20 | No upstream component; stays native. |
| Hidden file pickers | `src/app/settings/config-settings.tsx:390`, `src/plugins/kanban/drawer.tsx:550`, `src/app/pet-generate/pet-generate-content.tsx:274` | 3 | Still native hidden inputs; proxying each with a Sakuga button is follow-up work. |
| Editors / canvas / xterm | Monaco (`src/app/chat/...` code editor), transcript canvas, terminal views | — | Documented boundary: editors and canvas controls stay native. |
| Nav `<a>` links | router links throughout | 33 | Documented exception. |

## Verification run for this lane

```bash
cd qubit_runtime/apps/desktop
npm run test:ui -- src/sakuga/sakuga.test.tsx            # 9 passed
npm run test:ui -- src/app/settings                      # 402 passed, 15 pre-existing failures
npm run test:ui -- src/app/session/hooks/use-message-stream/gateway-event  # 81 passed
npm run test:ui -- src/app/quick-entry/quick-entry-app.test.tsx            # 1 passed
npm run test:ui -- src/app/settings/appearance-settings src/app/settings/pet-settings src/components/boot-failure-overlay  # 19 passed
npx tsc -p . --noEmit                                    # only the pre-existing errors (quick-entry/slash/contrib/store)
cd ../../.. && python3 scripts/check_sakuga_vendor.py    # OK (2 copies)
python3 scripts/sync_sakuga_shared.py --check            # OK (7 files)
```

The 15 settings failures were re-run on the lane base with the toggle change
stashed and are byte-identical there (billing portal URLs, a `config-settings`
mock, a connections sign-in row, two terminal-disconnect cases): pre-existing,
not caused by this lane.

`npm run build` was not run in this lane; the renderer build is the same Vite
pipeline and the lane only adds source modules and a public asset path already
shipped by the foundation commit.

## Rollback

Revert the integration commits. The vendored `public/sakuga-vfx/` files are
inert static assets; no service, config, or permission change is involved.
`installFxBridge`/`emitFx` are presentation-only and swallow engine failures.
