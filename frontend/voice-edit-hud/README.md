# Qubit Voice Edit HUD

Standalone Cyberpunk-2077/neon-magazine operator console for the Voice Edit
pipeline. Next.js + TypeScript + Three.js + GSAP, exported as static files so
the local bridge can serve `out/` directly.

This app is self-contained: it has its own `package.json` and lockfile and does
not change the root frontend workspaces.

## Run

```bash
npm install
npm run dev        # http://localhost:3000/?mock=1
npm run build      # static export -> out/
npm run start      # serve out/ on :4173 (npx serve)
npm run typecheck
npm run lint
npm run test       # Vitest + RTL
```

The production bundle is a static export (`output: 'export'`,
`trailingSlash: true`). `out/index.html` plus `out/_next/**` is the whole app.

Serve locally:

```bash
python3 -m http.server 4173 --directory out
# open http://127.0.0.1:4173/?mock=1
```

## Modes

### Fixture mode (no helper required)

`?mock=1`, or a build with `NEXT_PUBLIC_MOCK=1`.

The fixture runs a deterministic animation:

`hello -> listening -> partials -> final -> processing -> preview -> done`

It exercises every client action: **Apply** settles `done`, **Copy** records a
receipt, **Edit** opens the editor and **Edit submit** replaces the proposal,
**Retry** cycles through the flagged retry preview (injection + dropped facts),
**Revert** restores the original, and **Cancel** returns to idle. If untouched,
the fixture auto-applies after the preview so the `done` ending is visible.

Mock-only controls in the top bar:

- **REPLAY** resets the reducer and restarts the sequence.
- **REFUSAL** injects a typed `error` frame to review the refusal banner.

### Live mode

Open the HUD with `?token=<helper token>`. The client connects to the frozen
endpoint on the same origin:

```
/voice-edit/ws?token=...
```

Without a token the HUD shows a typed `NO TOKEN` state and keeps every action
disabled. A closed socket reconnects with bounded exponential backoff
(0.8 s, 1.6 s, ... capped at 10 s). Missed frames are not replayed; `hello`
must carry the current state.

## Frozen contract

Server -> client:

| frame | fields |
|---|---|
| `hello` | `version`, `state` |
| `status` | `state`, `detail`, `level` |
| `partial` | `text`, `final` |
| `preview` | `payload` (original, proposed, detected, model, provider, privacy_mode, locality, target_app, elapsed_ms, injection_suspected, clarification_returned, facts_dropped) |
| `error` | `code`, `detail` |
| `history` | `kind`, `heard`, `result`, `ts` |
| `level` | `value` (0..1) |
| `ping` | — |

Client -> server:

```json
{ "action": "apply", "payload": {} }
```

`action` is one of `apply`, `copy`, `edit`, `retry`, `revert`, `cancel`,
`edit_submit`. `edit_submit` carries `{ "text": "<proposal>" }`; the other
actions omit `payload`.

The reducer is tolerant where real helpers have drifted:

- `partial.text` may be **segment** text (the pending tail) or **cumulative**
  text (everything heard). A frame starting with what is already known is
  treated as cumulative; a shorter stale frame never erases a longer tail.
- `preview.payload` is canonical; `data`, `original`/`proposed` at the message
  root, and `latency_ms` are accepted for older helpers.
- `facts_dropped` may be a `string[]` or a count.
- `history.ts` may be an ISO timestamp, an epoch string, or a display string.
- Unknown frame types are ignored.

## Keyboard

| key | action |
|---|---|
| `Ctrl/Cmd+Enter` | Apply |
| `Ctrl/Cmd+R` | Retry (prevents the browser reload) |
| `Ctrl/Cmd+Z` | Revert |
| `Esc` | Cancel, or close the editor |
| `E` | Open the editor |
| `C` | Copy |
| `Ctrl/Cmd+Enter` in the editor | Submit the manual edit |

## Design / implementation notes

- Palette: black/violet shell, neon magenta `#ff2bd6`, cyan `#00e6ff`, violet
  `#9b5cff`, with clipped-corner panels, dense micro labels, terminal/scanline
  texture, a level-driven ring, word diff and a bottom action bar.
- No external network assets or fonts. Display/UI/mono use system stacks.
- Three.js background is one grid, three faint rings and a bounded point cloud:
  DPR capped at 1.5, throttled to ~30 fps, paused while hidden, and never
  started under `prefers-reduced-motion` (the CSS grid/vignette fallback stays).
- GSAP drives the entrance, status pulse and preview resolve. Inline styles are
  force-cleared after 1.8 s so a throttled ticker can never leave the UI hidden.
- Reduced motion disables the Three.js scene, scan animation and all CSS
  animation; the ring redraws statically.
- Responsive from 700-1200 px; the action bar is fixed on narrow viewports and
  panels scroll independently on desktop.

## Tests

```
tests/reducer.test.ts   partial merging, status/preview/error/history, reset
tests/actions.test.ts   action availability + payload mapping
tests/ws.test.ts        frame parsing / URL / serialisation
tests/socket.test.tsx   live WebSocket contract + reconnect
tests/mock.test.ts      fixture sequence and every action
tests/diff.test.ts      word diff counts and whitespace
tests/hud.test.tsx      RTL journeys (enable on preview, apply, edit, copy,
                        refusal, replay, retry, no-token)
```

57 tests. The component tests run with `prefers-reduced-motion: reduce` and a
canvas stub so they stay deterministic and never open a GPU context.

## Backend integration notes

- Serve `out/` at a stable path and pass the helper token as `?token=`.
- The WebSocket must be same-origin at `/voice-edit/ws`. If the HUD is mounted
  under a subpath, build with `NEXT_PUBLIC_BASE_PATH=/that/path` (asset URLs are
  prefixed) and mount the WS there too, or adjust `buildWsUrl`.
- `copy` is a request: the backend performs the clipboard write. The HUD only
  writes the clipboard itself in fixture mode.
- `apply` remains a backend-side safe-paste operation; the HUD never applies
  anything optimistically — it waits for the `status`/`history` receipt.
- `hello.state` should reflect the current turn so a reconnect does not show a
  stale idle state.
