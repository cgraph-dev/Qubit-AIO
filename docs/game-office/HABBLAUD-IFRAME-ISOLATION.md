# Habblaud iframe isolation — collision audit

**Scope.** The vendored office client (`habblaud`) runs in an iframe at
`/plugin-apps/qubit-office/habblaud/` inside the Qubit dashboard. It was written
to *be* the page, not to be embedded. This document records, with evidence,
every place its assumptions can collide with the host — storage, keyboard,
globals, document, and service worker.

**Sources of truth used.**
- Source: `/tmp/habblaud-full/client/src/` (and `/tmp/habblaud-full/shared/`).
- Shipped bundle (what actually runs):
  `frontend/qubit-app/plugins/qubit-office/habblaud/bundle/{app-U4Pwh2XA.js,main-DND_b4_E.js,ptypane-DxPqTDKo.js,daystats-CUw1NUBG.js}`.
- Mount: `frontend/qubit-app/plugins/qubit-office/game/app/OfficeAppShell.tsx`
  and `.../game/app/office-app.ts`.
- Host storage keys: `frontend/qubit-web/src/**` source + the built dashboard
  `frontend/qubit-dist/assets/*.js`.

**The frame is same-origin, sandboxed, and grants `allow-same-origin`** — the
app needs its own origin/storage APIs while remaining fenced from top-level
navigation and host DOM. `OfficeAppShell.tsx`:

> `sandbox: "allow-scripts allow-same-origin allow-forms allow-downloads allow-popups allow-modals"`

The sandbox does not add `allow-top-navigation` or `allow-top-navigation-by-user-activation`;
scripts inside the iframe cannot replace the dashboard route. `allow-same-origin`
preserves the app's same-origin fetch, localStorage and module worker behavior.
The upstream client has its own namespaced storage keys; the parent and child
still share the origin's storage area, so this is containment, not storage
partitioning.

---

## 1. localStorage keys — CONFIRMED (same-origin sharing), but NO key collision

Because the frame is same-origin and unsandboxed, every key the client writes
lands in the dashboard's own `localStorage`. All keys the client reads or writes:

| Key | Written where (source) | Present in shipped bundle |
|---|---|---|
| `habblaud:prefs` | `client/src/ui/prefs.ts:7,81` | yes (`app-U4Pwh2XA.js`) |
| `habblaud:update-seen` | `client/src/ui/version.ts:13` | yes |
| `habblaud:locale` | `shared/i18n.ts:23` | yes (`app-…js` + `main-…js`) |
| `habblaud.wallets.v1` | `client/src/world/social/wallet.ts:17` | yes |
| `habblaud.deliveries.seen` | `client/src/ui/deliveries.ts:20` | yes |
| `habblaud.tabs.order` | `client/src/ui/agentorder.ts:3` | yes |
| `habblaud.rooms.order` | `client/src/ui/roomorder.ts:3` | yes |
| `habblaud.spawn.folder` | `client/src/ui/spawn.ts:23` | yes |
| `habblaud.move.drawer` | `client/src/ui/drawer.ts:986` (Movable `key`) | yes |
| `habblaud.move.term` | `client/src/ui/pty.ts:97`, `terminal.ts:762` | yes |

Legacy prefix handled by `migrateLegacyKeys` (`prefs.ts:105-107`): old keys were
`codetown:` / `codetown.`; migration rewrites them to `habblaud:` / `habblaud.`
and deletes the old key (`prefs.ts:116-144`). The scan filters strictly on
`startsWith('codetown:')` / `startsWith('codetown.')`, so it cannot touch host
keys.

**Host keys in the dashboard** (enumerated from `frontend/qubit-web/src/**` and the
built `qubit-dist/assets/*.js`):
`vdb_api`, `vdb_token`, `qubit-locale`, `hermes-locale`,
`qubit-dashboard-theme`, `hermes-dashboard-theme`, `qubit-dashboard-font`,
`hermes-dashboard-font`, `qubit-sidebar-collapsed`, `hermes-sidebar-collapsed`,
`qubit.hx.groups`, `qubit.chat.session`, `qubit.chat.progress.*`,
`qubit.chat.transcript.*`, `qubit.pty.token.chat`, `qubit.lastLocation`,
`qubit:plugin-manifests:v2`, plus sessionStorage `qubit:plugin-manifests:v2`.

**Verdict: shared origin storage, NO key collision.** Every client key is namespaced
`habblaud:` or `habblaud.`; every host key is `vdb_*`, `qubit*`, or `hermes-*`.
No shared literal. The two keyspaces are disjoint. The vendor's own comment
("writes to localStorage under its own keys", `office-app.ts:6`) is true as
written.

Residual (not a collision, worth naming): same-origin means the shared ~5 MB
quota is shared too, and the client's `habblaud.wallets.v1` map
(`wallet.ts:191`) grows unbounded in principle. It cannot evict host keys (a
`QuotaExceededError` only aborts the client's own write — see §4), so the failure
mode is "client stops persisting", not "host data lost".

## 2. Keyboard — NOT PRESENT (the iframe boundary contains it)

The client installs global key handlers on **its own window**, i.e. the iframe's
`window`:

- `client/src/world/input.ts:221` — `window.addEventListener('keydown', onKey)`,
  removed at `:230`. `window` here resolves to the frame's window.
- `client/src/ui/index.ts:323` — bare `addEventListener('keydown', (e) => onKey(e))`;
  a bare `addEventListener` in a module resolves to the frame's global `window`.
- `client/src/ui/sound.ts:18-19` — bare `addEventListener('pointerdown'|'keydown', …)`.

Shipped confirmation: the only unqualified global key attach in the bundle is one
`window.addEventListener('keydown'` plus three bare `addEventListener('keydown'`;
all other matches are element-scoped (`el.`, `input.`, `searchInput.`, `e.` …).
`app-U4Pwh2XA.js`.

Iframe keyboard events do **not** propagate across the frame boundary to the host
document, so these handlers are scoped to the frame's document. **No code
attaches to `window.parent` or `window.top`:** a grep for
`window.parent`, `window.top`, `parent.document`, `top.document` across the
source and the shipped bundle returns **zero** hits. The only `postMessage` in the
shipped bundle (`ptypane-DxPqTDKo.js`) is inside a vendored VS Code
scheduler shim posting to its own worker global (`Nt.postMessage(...)`), not to
the parent.

**Verdict: NOT PRESENT.** The boundary contains the keyboard handlers; nothing
escapes to the host.

## 3. `document.documentElement.lang` and meta tags — NOT PRESENT (host-contained)

- `client/src/app.ts:15` — `document.documentElement.lang = getLocale();`
- `client/src/app.ts:16-…` — `document.querySelector('meta[name="description"]')?.setAttribute(...)`.
- `client/src/ui/notify.ts:46` — `document.title = title;` (new-message count in
  the frame's title).

Shipped confirmation: `app-U4Pwh2XA.js` contains exactly one
`document.documentElement.lang` and one `document.title`. In the frame, `document`
refers to the iframe's own document — it cannot reach the host document (no
`parent`/`top` refs; see §2).

Note the host also owns `document.documentElement.lang`
(`frontend/qubit-web/src/i18n/context.tsx:128`). Because the two `document`s are
distinct, both set their own and neither overwrites the other. The client's
toolbar title (`document.title`) also stays inside the frame; the tab title the
user sees is the host's.

**Verdict: NOT PRESENT.** Contained by the frame boundary; no way to restyle or
retitle the host.

## 4. Storage quota / private mode — CONFIRMED (handled, cannot crash the frame)

- `safeLocalStorage()` (`prefs.ts:93-99`): returns `localStorage` only if defined,
  else `null`; wraps the access in `try/catch`. Browsers that throw on merely
  *touching* `localStorage` (blocked cookies / some private modes) yield `null`.
- `loadPrefs` (`prefs.ts:68-76`) and `savePrefs` (`prefs.ts:78-85`) wrap
  `getItem`/`setItem` in `try/catch`; `savePrefs` swallows `QuotaExceededError`
  ("prefs valid only for this session").
- `migrateLegacyKeys` (`prefs.ts:116-144`): the enumeration loop is wrapped in
  `try/catch` returning `0`; each key's copy/remove is individually wrapped, and
  it removes the legacy key only after a successful copy — a failed write leaves
  the old key for the next boot.
- Other call sites still use the bare global with their own guards: `wallet.ts`
  reads `globalThis.localStorage` behind a `try/catch` (`wallet.ts:61-64`),
  `deliveries.ts:91-99` wraps its read/write, `movable.ts:284-297`,
  `agentorder.ts:33-53`, `roomorder.ts:30-39`, `spawn.ts:97-105` wrap theirs.

Shipped confirmation: `app-U4Pwh2XA.js` shows the guarded helpers
(`localStorage.getItem(…)) ?? … }catch{…}`, `localStorage.removeItem(e)}catch{}`,
`catch{return …}`) and **no `localStorage.clear()`** anywhere in the bundle.
No code path lets a storage exception escape to abort the frame.

**Verdict: CONFIRMED as a risk class, but correctly handled.** The client cannot
crash the frame on storage failure. It degrades to in-memory/session-only
preferences. No fix needed.

## 5. Service worker — NOT PRESENT

- Source: `grep -rn 'serviceWorker|registerSW' /tmp/habblaud-full/client/src/`
  → **no matches**.
- Shipped bundle: `grep -oE 'serviceWorker|navigator\.serviceWorker' bundle/*.js`
  → **no matches**.

The app ships `manifest.webmanifest` (`index.html:31`) for install-to-homescreen
metadata only; there is no service worker registered by the client.

**Verdict: NOT PRESENT.** No SW is registered, so none can claim scope over the
dashboard or cache host resources.

---

## Summary table

| # | Suspicion | Verdict | Key evidence |
|---|---|---|---|
| 1 | localStorage key collision with host | **CONFIRMED shared origin / NO key collision** | sandboxed same-origin (`OfficeAppShell.tsx`); client keys all `habblaud:/.` (`prefs.ts:7`, `version.ts:13`, `i18n.ts:23`, `wallet.ts:17`, `deliveries.ts:20`, `agentorder.ts:3`, `roomorder.ts:3`, `spawn.ts:23`, `drawer.ts:986`, `pty.ts:97`); host keys all `vdb_*`/`qubit*`/`hermes-*`. Disjoint. |
| 2 | global keydown leaks to host | **NOT PRESENT** | `input.ts:221`, `index.ts:323`, `sound.ts:19` — all frame-window scoped; zero `window.parent`/`window.top` in source & bundle. |
| 3 | `documentElement.lang` / meta restyle host | **NOT PRESENT** | `app.ts:15-16`, `notify.ts:46` — operate on the frame's own `document`; no `parent`/`top`. |
| 4 | storage throw crashes the frame | **CONFIRMED (handled)** | `safeLocalStorage` `prefs.ts:93-99`; guards `prefs.ts:68-85,116-144`; no `localStorage.clear()` in bundle. |
| 5 | service worker registered from frame | **NOT PRESENT** | zero `serviceWorker` hits in source and shipped bundle. |

## Confirmed harmful collisions

**None.** The one confirmed fact that could have been harmful — same-origin
storage sharing — does not produce a key collision because the client namespaces
every key under `habblaud:/.`, and its legacy-key migration is prefix-guarded to
`codetown:/.` so it cannot touch `vdb_*`/`qubit*`/`hermes-*`.

## Minimal fix required

**Sandbox the iframe while retaining its required same-origin capabilities.**
The upstream page owns a full document and global input handlers; containing it
inside a sandbox prevents top-level navigation while preserving its canvas,
storage, module loading and API access. Two forward-looking notes:

1. **Mount invariant to preserve.** Do not remove `allow-same-origin` without
   also redesigning the API/canvas boundaries. Do not add either top-navigation
   sandbox token; that would allow the vendored app to navigate the host.
2. **Vendor-script guard (recommended, not required).** The disjoint-keyspace
   result holds only while the vendored client keeps its `habblaud:` / `habblaud.`
   prefix. Any new client key must be re-checked against the host key set. If a
   future vendored version ever stops prefixing keys (or adds a bare
   `localStorage.clear()`), that would be the first real collision. The vendor
   regeneration script should ideally assert that every storage key the built
   bundle references starts with `habblaud`. This audit is the baseline for that
   check; it is a recommendation, not a defect.

## Unverifiable / not checked

- **Runtime, in a live browser.** Every claim here is from source + shipped
  bundle reading. I did not load the dashboard in a browser and observe the
  frame's storage/keyboard at runtime. The bundle grep confirms what shipped;
  it does not exercise it. (The reasoning is nevertheless robust for the
  keyboard/document claims because they rest on the iframe boundary, which is a
  browser guarantee, not an implementation detail.)
- **Keys written indirectly by vendored third-party chunks.** `ptypane-*.js`
  (a vendored terminal/pane lib) and `daystats-*.js` were scanned for
  `localStorage` writes and `serviceWorker` and are clean at the literal level,
  but I did not read their full source, only grepped the shipped output. No
  third-party storage writes were found; I cannot positively rule out a
  dynamically-computed key inside a minified third-party path.
- **`qubit-office.game.js`** is a large built Phaser bundle; I identified the
  iframe mount from the plugin *source* (`game/app/*.tsx|ts`), not by reading the
  built copy end-to-end.
- **Clipboard.** The mount passes `allow="clipboard-read; clipboard-write"`
  (`OfficeAppShell.tsx:66`); the client uses the async clipboard API. This is an
  explicit grant, not a collision, and I did not audit its usage.
