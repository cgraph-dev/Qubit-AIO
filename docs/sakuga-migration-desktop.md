# Sakuga migration — Electron desktop

**Scope:** `qubit_runtime/apps/desktop/src/**` (renderer only). The web dashboard
(`frontend/qubit-web`) and the Office plugin are separate lanes.

**Status:** partial sweep, honest accounting below. The renderer loads the
vendored engine, installs the `qubit:fx` bridge, and has adopted the Sakuga
toggle (every settings boolean row plus the remaining switches), slider, select,
checkbox, buttons and product outcome cues across the app surfaces. Text inputs,
progress bars, hover tooltips, textarea and the file-picker proxies remain
native; each is listed with its reason below. `src/components/**` buttons and
inputs are a separate lane and are untouched here.

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
| Button adapter | `src/sakuga/ui.tsx` | Drop-in `Button` keeps `variant`/`size`/`asChild`/`loading`, forwards Radix pointer handlers and ARIA booleans, focuses the host, and submits the owning form for `type="submit"`. |
| App surface sweep (pass B) | `src/app/**` | **181** files import the adapter; **524** `<Button>` uses. **113** native `<button>` tags were replaced (all non-test native buttons under `src/app`), **22** remaining `<Switch>` sites became `Toggle`, and **6** `<Checkbox>` uses moved to the upstream control. No non-test `<Switch>` remains. |
| Test migration | `src/app/**/*.test.tsx` | Native-`button` selectors in the affected suites were widened to `[role="button"]`; the toggle/checkbox wiring is exercised through the real shadow-double contract. |

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
| Text inputs | `src/components/ui/input.tsx` (147 `<Input>` uses in `src/components`), `src/app/**` (95 `<Input>`, non-test) | 95 in app | Deferred: `SakugaInput` requires a real label and only accepts text/email/search/url/tel/password. Many app fields are `type=number`/`file`, use `prefix`/`suffix` adornments, or are typed by tests through `fireEvent.change` on the host. A label-derivation pass plus shadow typing test migration is needed. |
| Progress | `src/app/settings/local-model-download-progress.tsx`, `billing/index.tsx`, `updates-overlay.tsx` | 6 | Every site carries a state with no upstream equivalent (unknown/paused, destructive/fill-tone, or `indeterminate`). Long-job completion already plays `success` via the turn outcome. |
| Tooltips | `src/components/ui/tooltip.tsx` (`<Tip>` 136 uses in `src/app`) | 136 | Radix hover/focus geometry and portalled placement; Sakuga's tooltip is a click label, so the composite stays and cues fire around it. |
| Textarea | 16 raw `<textarea>` in `src/app` | 16 | No upstream component; stays native. |
| Tabs | `src/components/ui/tabs.tsx` (Radix `Tabs`, 4 usage sites) | 4 | No app-surface `<Tabs>` usage; composite ARIA widget with keyboard semantics stays as-is (components lane). |
| Hidden file pickers | `src/app/settings/config-settings.tsx`, `src/app/pet-generate/pet-generate-content.tsx`, `src/app/pet-overlay/...` | 3 | Still native hidden inputs; proxying each with a Sakuga button is follow-up work. |
| Editors / canvas / xterm | Monaco (`src/app/chat/...` code editor), transcript canvas, terminal views | — | Documented boundary: editors and canvas controls stay native. |
| Nav `<a>` links | `Button asChild` renders the anchor unchanged; router links throughout | — | Documented exception. |

## Verification run for this lane

```bash
cd qubit_runtime/apps/desktop
npx tsc -p tsconfig.json --noEmit             # only the pre-existing errors named below
npm run test:ui -- src/app                    # 3856 passed, 68 failed, 2 skipped
npm run build                                 # Vite renderer + electron bundles built
cd ../../.. && python3 scripts/check_sakuga_vendor.py    # OK (2 copies)
python3 scripts/sync_sakuga_shared.py --check            # OK (7 files)
```

The 68 app-suite failures were re-run on the lane base and split cleanly:
`session-tile` (8), `session/hooks` (10), `contrib/hooks` (32), billing (11),
`config-settings` (suite), `profiles` (suite), `command-center/maintenance` (2),
`command-center/open-session` (1), and the known connections/terminal rows are
pre-existing on `b648916d6` and unchanged by this lane. The only suites this
lane changed are the widened button queries, which pass.

Pre-existing `tsc` errors not chased (unchanged by this lane):
`src/app/chat/session-tile.tsx(234)`, `src/app/contrib/hooks/use-quick-entry-bridge.ts(109)`,
`src/app/contrib/onboarding-kickoff.ts(1)`, `src/app/quick-entry/quick-entry-app.tsx(44,77)`,
`src/app/session/hooks/use-prompt-actions/slash.ts(499)`.

`npm run build` was run after the sweep; it produced the renderer and
electron-main/preload bundles with no new errors.

## Rollback

Revert the integration commits. The vendored `public/sakuga-vfx/` files are
inert static assets; no service, config, or permission change is involved.
`installFxBridge`/`emitFx` are presentation-only and swallow engine failures.
