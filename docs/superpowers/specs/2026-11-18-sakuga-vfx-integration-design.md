# Sakuga VFX integration — design

**Date:** 2026-11-18
**Owner decision:** adopt the Sakuga VFX effects engine and controls across the web
dashboard, Qubit Office (host shell **and** in-canvas), and the Electron desktop
renderer. Keep the upstream original colours; do not recolor to Qubit themes.
**Status:** design approved; superseded only by a later owner decision.

## 1. Source and licence

- Upstream `https://github.com/finktheartist/sakuga-vfx`, pin
  `c3dabb173928a3ad2372183103caf2e6f50953d0`, MIT © 2026 Justin Finkelman.
- Vendored byte-identical: `core.js` (shared engine) and the eleven component
  files (`button`, `toggle`, `slider`, `checkbox`, `radio`, `input`, `tabs`,
  `progress`, `rating`, `toast`, `tooltip`). The upstream README says "twelve";
  the repository ships eleven control files plus the core.
- Hashes: `frontend/qubit-web/public/sakuga-vfx/VENDOR.json`. Gate:
  `python3 scripts/check_sakuga_vendor.py`.
- Attribution: `THIRD_PARTY_NOTICES.md` §"Sakuga VFX"; upstream's statement that
  the SFX lettering and toggle frames were GPT-Image generated is carried over.
- Palette (unchanged): `#ffffff`, `#b8b3ff`, `#5a4fd8`, `#ff5fa8`, `#1d1a4a`,
  plus the `--sk-*` variables.

## 2. Surfaces in scope

| Surface | Path | Work |
|---|---|---|
| Web dashboard | `frontend/qubit-web` | loader, wrappers, effect bus, full control sweep |
| Qubit Office host shell | `frontend/qubit-app/plugins/qubit-office/game/app` | menu/docks/rows use the controls; office events fire cues |
| Qubit Office canvas | `frontend/qubit-app/plugins/qubit-office/habblaud` | additive overlay anchored to real characters |
| Electron desktop | `qubit_runtime/apps/desktop` | loader, wrappers, effect bus, full control sweep |
| Shared layer | `frontend/qubit-shared/src/sakuga` + `qubit_runtime/apps/shared/src/sakuga` | one source, mirrored by `scripts/sync_sakuga_shared.py --check` |

Out of scope: the Ink TUI (no canvas surface; terminal colour flourishes are a
different feature) and third-party widget internals we do not render.

## 3. Shared layer (foundation, already landed)

- `types.ts` — typed surface of `window.SakugaFX`.
- `loader.ts` — injects `core.js` and per-component scripts from
  `/sakuga-vfx/{name}/{name}.js`; deduplicated per URL; `configureSakuga({base})`
  for the office frame / Electron.
- `fx.ts` — `emitFx(cue, opts)`, `emitFxAt(cue, el)`, `fireFx` (dispatches
  `qubit:fx`), `installFxBridge()`, `onFx`. Cues: `impact`, `success`, `error`,
  `warning`, `celebrate`, `pop`, `slash`, `tick`, `focus`, `type`, `approval`,
  `agents`. Uses the upstream primitives only (`burst`, `puff`, `shards`, `ring`,
  `slash`, `lines`, `ticks`, `wob`, `kira`, `sfx` with `don|gogo|kachi|pon|shu|zuza`).
- `elements.tsx` — `SakugaButton/Toggle/Checkbox/Radio/Input/Slider/Tabs/Progress/Rating/Tooltip`,
  `showSakugaToast`, `SakugaToastHost`. Host-level role/label mirrors light-DOM
  accessibility; the inner native control stays authoritative.
- `select.tsx` — `SakugaSelect`: input-styled `role="combobox"` trigger; panel
  with a `SakugaInput` filter and a `SakugaRadio` list; hidden input for forms.
- `test-helpers.ts` — `sakugaShadow`, `sakugaInner`, `typeIntoSakuga`,
  `clickSakuga`, `sakugaValue`, `waitForSakuga`.

Loading policy: core eagerly after first paint; components lazily on first
render. The toggle (~506 KB) must never be in an initial payload.

## 4. Effect vocabulary

| Event | Cue |
|---|---|
| primary button press | `impact` (burst + clouds + shards + ドン!) |
| success / turn completed / payment completed | `success` |
| failure / error / denied | `error` (slash + ズザッ) |
| warning / unknown / quarantined | `warning` |
| approval requested | `approval` (ring + カチッ) |
| agent created / arrived | `agents` (ゴゴゴゴ) |
| checkbox / toggle / radio | control's own upstream reaction |
| input key | upstream dust (component) + optional `type` |
| toast | upstream smear + ズザッ |
| tooltip | upstream puffs + カチッ |

All cues are presentation only. They never invent business state, mirroring
`voicebridge/office/fx.py`.

## 5. Office in-canvas (additive Habblaud patch)

- Patch the Habblaud source checkout (`/home/trick/qubit-worktrees/habblaud-theme`,
  local commit `f0354b0`), not the vendored bundle.
- Add an overlay canvas inside the office document and an anchor registry fed by
  the renderer's existing per-character `heads` map (`renderer.ts` already tracks
  screen coordinates for every character).
- Consume `snapshot.fx` from the office stream. `voicebridge/office/fx.py`
  already projects committed Business OS events into deterministic `FXEvent`s but
  has no renderer; expose a bounded `fx` array in the tower payload
  (`voicebridge/office_stream.py::to_snapshot`) and map each animation to the
  Sakuga cues. No new business truth; replay/reduced-motion semantics stay.
- Fallback anchor: room centre, then office centre, then the office edge.
- Reduced motion: essential effects render the existing `still` cue; nonessential
  effects are dropped (the backend already decides this).
- `office.fx: sakuga|off` (default `sakuga`). Classic theme keeps working; the
  overlay is additive and theme-independent.
- Re-vendor with `python3 scripts/vendor_habblaud.py --src <patched> --check`,
  update `PROVENANCE.json` (`local_changes`), add before/after goldens, and keep
  `vendor_habblaud.py --check` green.

## 6. Control sweep

Order (one commit per surface):

1. App shell, sidebar, header, chat composer, primary actions.
2. Sessions, Files, Models, Plugins, Channels, Config, System.
3. Office host shell (menu icons, docks, Agents/Approvals rows).
4. Desktop renderer (its own `main.tsx`, components, forms).
5. Low-traffic admin pages.

Replaced literally: buttons (`@nous-research/ui` Button and native `<button>`),
text/password/email inputs, checkboxes, radios, range inputs, tab strips,
progress bars, toasts, tooltips, and every `<select>` via `SakugaSelect`.
Documented boundaries: nav `<a>` links stay links; xterm, Monaco/editors and
canvas controls stay native; hidden file inputs are proxied by a Sakuga button;
composite ARIA widgets (command palette, model picker) keep their keyboard
semantics and fire effects around the interaction.

## 7. Accessibility and tests

- Host-level roles/labels keep `ByRole`/`ByLabel`/`ByText` working where the text
  is slotted light DOM. Inner controls remain real native elements.
- Tests that type/click inside a component use the shadow helpers from
  `@qubit/shared/sakuga`.
- `prefers-reduced-motion` disables effects inside the engine; state changes stay.
- Run axe on the main pages after the sweep; the office keeps its existing a11y
  suite; the desktop keeps its own.
- Focused suites per surface during implementation; one final matrix after freeze.

## 8. Build and rollback

- Web: `frontend/qubit-dist` is a build artifact (gitignored); rebuild with
  `KEEP_NODE_MODULES=1 bash scripts/build_dashboard.sh`.
- Office: rebuild `frontend/qubit-app/plugins/qubit-office/qubit-office.game.js`
  with `npx vite build --config frontend/qubit-web/vite.office-game.config.ts`
  from `frontend/qubit-web` (or the documented command in the office README).
- Habblaud: rebuild the client, re-vendor, provenance + goldens.
- Desktop: `npm run build` in `qubit_runtime/apps/desktop`, then its tests.
- Rollback: revert the integration commit(s); the vendored assets are inert
  public files, and `office.fx: off` disables the in-canvas overlay without a
  Habblaud rebuild.

## 9. Known costs and risks

- ~1.1 MB of static assets; lazy loading keeps the initial path near core only.
- Shadow DOM breaks naive Testing Library queries; the helper file is the
  supported path and the sweep updates affected tests.
- The office re-vendor is the highest-risk step; goldens, `--check`, and the
  classic fallback mitigate it.
- The canvas sits above modals upstream (documented limit); Qubit keeps that
  behaviour, with `pointer-events: none`.
- Desktop already has a particle system; it is retired for surfaces that adopt
  Sakuga to avoid double effects.
