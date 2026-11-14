# Qubit Office loading screen + weather

Phase-1 loading surface for `/qubit-office` (plan §7, §18.2, §25 lane B).
It renders immediately, never blocks the iframe, and fades away when the
office's `load` event fires. It imports no API, business or world module.

## What the owner must supply

Both are optional; the screen works with defaults.

1. **Background art** — a URL or path to the final image. Until then the CSS/SVG
   placeholder sky is used. Put it where the dashboard can serve it (e.g.
   `frontend/qubit-app/plugins/qubit-office/...` or a media route) and set:
   ```js
   window.__QUBIT_OFFICE_LOADING__ = {
     background: "/media/qubit-office-loading.webp",
     backgroundDim: 0.55,          // 0..0.95 readability veil
   };
   ```
   A future server injection can expose the same shape as
   `office.loading.{background,backgroundDim,location,…}` through
   `window.__QUBIT_OFFICE_CONFIG__ = { office: { loading: { … } } }`.
   The direct global wins over the nested one; an explicit React prop wins over
   both. Only `http(s)`, root/relative paths and `data:image/…` are accepted;
   executable schemes and CSS-breaking characters are rejected.

2. **Weather location** (opt-in; no location means no request, neutral screen):
   ```js
   window.__QUBIT_OFFICE_LOADING__ = {
     location: { latitude: 44.4268, longitude: 26.1025, name: "Bucharest" },
   };
   ```
   Or inject a server-normalized snapshot and no network is used at all:
   ```js
   window.__QUBIT_OFFICE_LOADING__ = {
     weather: { condition: "rain", temperatureC: 12, isDay: true },
   };
   ```

Server-side injection is a **follow-up** (this lane does not change the Python
config loader). Until it exists, the owner can set the global from the host
page or pass `OfficeApp`'s `loading` prop from a wrapper that has the values.

## Weather → effect mapping

| Normalized state | Effect | Notes |
|---|---|---|
| `clear-day` | `god-rays` | |
| `clear-night` | — | neutral placeholder sky |
| `partly-cloudy` | `clouds` | |
| `cloudy` | `clouds` | |
| `rain` | `rain` | |
| `storm` | `storm` | storm already contains rain + lightning; rain is never layered under it |
| `snow` | `snow` | |
| `fog` | `fog` | |
| `fallback` | — | neutral; typed reason (`no-location`, `offline`, `timeout`, `http`, `parse`, `aborted`) |

Exactly one effect is active. A new candidate must persist `hysteresisMs`
(default 60 s) before it replaces the current one; the rain↔storm pair uses
twice that hold, so those two cannot flicker.

## Budgets and behaviour

- Frame: fixed **320×180**, CSS upscaled with `image-rendering: pixelated`.
- Layers: background → placeholder → veil → back FX → UI → front FX.
- Weather fetch: **1.5 s** default timeout (clamped 0.5–2 s), **15 min** cache
  (clamped 10–30 min), `sessionStorage`, never on an animation frame.
- No network and no animation before first paint; fallback is neutral.
- VFX loop is 30 fps; the plan's ≤4 ms/frame target is measured by
  `effects.test.ts` (it prints the one-frame cost).
- Reduced motion (OS preference or `reducedMotion` config) and low-power
  (save-data / ≤2 cores / ≤2 GB or `lowPower`) draw **one still frame** per
  layer and stop.
- Every frame, timer, listener and fetch is disposed on unmount or when the
  screen fades; a hidden tab pauses the loop.

## Vendored effects

`pixelfx/` is the MIT-licensed weather subset from
`patch-gg/pixelfx@044e12a45dc9cc312cec50ee606dfc98afe5adc2`. See
`pixelfx/LICENSE`, `pixelfx/NOTICE.md`, `pixelfx/PROVENANCE.md` and
`pixelfx/VENDOR_HASHES.json`. `effects.test.ts` verifies the hashes, the
license, and that no non-weather effect entered the subtree.

## Tests

Run from `frontend/qubit-web` (the office plugin has its own runner):

```bash
npx vitest run --config vitest.office-app.config.ts
```

`weather.test.ts` (mapping/hysteresis/cache/timeout/offline/abort),
`config.test.ts` (background safety + clamping + host globals),
`effects.test.ts` (vendor/license/perf/headless render),
`effect-surface.test.ts` (frame size/throttle/still-frame/cleanup) and
`OfficeLoadingScreen.test.ts` (real React: placeholder/owner art, progress,
error/retry, reduced motion, fade-out cleanup, offline).
