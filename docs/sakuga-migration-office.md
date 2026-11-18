# Sakuga migration — Qubit Office host shell

**Scope:** `frontend/qubit-app/plugins/qubit-office/game/**` (the React host
shell around the vendored office iframe). The dashboard and the Electron
renderer are separate lanes.

**Status:** composed pass landed. The shell reuses the shared composed controls
through `game/app/sakuga-form.ts`, a thin adapter that keeps the panel factories
React-free (`R.createElement`) and native-shaped props.

## What changed

| Unit | Files | Notes |
|---|---|---|
| Adapters | `app/sakuga-form.ts` | `OfficeInput`, `OfficeSelect`, `OfficeTextarea`, `OfficeTooltip` wrap `SakugaInput` / `SakugaSelect` / `SakugaTextarea` / `SakugaHoverTooltip`. Props stay native-shaped (`onChange({ target: { value } })`, `data-testid`, `aria-label`, `onInput`) so existing tests and selectors are unchanged. |
| Command Center | `app/CommandCenter.tsx` | Search input → `OfficeInput`; kind/status filters → `OfficeSelect`; search has a hover hint (Ctrl/⌘ K, arrows, Enter, Escape). |
| Timeline | `app/Timeline.tsx` | Search + kind/status filters → composed controls; the search hint names the filter. |
| Seat routing | `app/SeatModelPanel.tsx`, `app/OfficeAppShell.tsx` | Seat/provider/model pickers → `OfficeSelect`; the seat picker has a hover hint. |
| Agent control | `app/AgentControlDrawer.tsx` | Managed-agent picker, text fields, permission scope inputs, preview prompt/ranges → composed controls. |
| Room Builder | `app/RoomBuilder.tsx` | Style/palette/light selects, room/signage/department text fields → composed controls. The numeric seed field stays native (documented). |
| War Room | `app/WarRoomPanel.tsx` | Session/source/evidence selects and the idea/source/locator/claim inputs → composed controls. |
| Etsy / Character Studio | `app/EtsyConnectPanel.tsx`, `app/CharacterStudio.tsx` | Redirect/state inputs and the part search → composed controls; the search has a hover hint. |

`officeFx`, `sakugaButton`, `sakugaProgress` and `sakugaLoading` are unchanged,
so the office loading/progress behaviour is untouched.

## Tooltips

`OfficeTooltip` is used on host-shell controls where a short hint helps
(Command Center search shortcuts, Timeline search, seat picker, Character Studio
search). The office's own injected Habblaud icons live inside the **iframe**,
which is a separate document with no host React tree; they cannot mount the
React `SakugaHoverTooltip` wrapper. That boundary is deliberate and documented:
the injected icons keep their native `title`/`aria-label` semantics.

## Remaining exceptions

- **Numeric seed** (`RoomBuilder`) stays a native `type="number"`; the composed
  number control is a desktop-web extra and the office did not need the
  steppers for this one field.
- **Office iframe internals** (Habblaud menu icons, canvas controls) stay
  vendored/native; the shell must not reach into the iframe.
- **Composite widgets** in the shell keep their semantics where replacing them
  would lose keyboard/portal behaviour.

## Evidence

- `npx vitest run --config vitest.office-app.config.ts` → 48 files / 563 tests.
- `npx vite build --config vite.office-game.config.ts` → rebuilt
  `qubit-office.game.js` (8,039.43 kB) and `.css` (91.65 kB).
- `python3 scripts/vendor_habblaud.py --check` → ok (78 files).
- `python3 scripts/check_sakuga_vendor.py` → OK (2 copies).
- `python3 scripts/sync_sakuga_shared.py --check` → OK (17 files).
