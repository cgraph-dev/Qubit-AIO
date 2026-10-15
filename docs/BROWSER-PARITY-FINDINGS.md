# Habblaud-in-Qubit-Office — Browser Parity Findings

Hostile-reviewer browser probe of the vendored Habblaud pixel-art office
(`frontend/qubit-app/plugins/qubit-office/habblaud/`, MIT) served by
`voicebridge/webui.py::_serve_plugin_app` at
`/plugin-apps/qubit-office/habblaud/`, mounted by `/qubit-office`.

Probe date: 2026-10-15 · Branch `pre-sync-worktree-snapshot` @ `c3b005f18`.
Probe scripts (read-only on the repo; written only to the scratch dir):
`$SCRATCH/probe_office_parity.py`, `retry_390.py`, `shell_paint_probe.py`,
`ui_dump_probe.py`
where `$SCRATCH = /home/trick/.local/state/qubit-voicebridge/runtime/cache/scratch`.

**Method.** Headless Chrome via CDP (`--remote-debugging-port`, fresh
`--user-data-dir` in `/tmp`), viewport pinned with
`Emulation.setDeviceMetricsOverride`. Every measurement is a value read back
from the live DOM (`getBoundingClientRect`, `documentElement.scrollHeight`,
`getImageData`) — no eyeballing. The service on `:8890` was **not** restarted and
no file under `frontend/qubit-*/` or `voicebridge/` was edited.

Browsers: **Google Chrome 149.0.7827.155** (`/usr/bin/google-chrome`).
**Mozilla Firefox 157.0.1** is installed but its live probe is **BLOCKED** (see
§2) — the vendored app's live render is therefore **NOT verified in Firefox**.

---

## Results table

| # | Check | Command run (abbreviated) | Observed | Verdict |
|---|-------|---------------------------|----------|---------|
| 1a | `/qubit-office` is in `FULL_HEIGHT_ROUTE_PATHS` (source) | `grep -n FULL_HEIGHT frontend/qubit-web/src/lib/chat-routes.ts` | Line 53–58: list is `[...CHAT_ROUTE_PATHS, "/webchat", "/qubit-bot", "/qubit-office"]` | **PASS** |
| 1b | The **served** SPA bundle actually contains it | `sha256sum frontend/qubit-dist/assets/index-CAakg9VA.js`; `grep -oE 'jt=\[[^]]*\]' …js`; `curl -s http://127.0.0.1:8890/assets/index-CAakg9VA.js \| sha256sum` | File hash `755378d4e0d596f5f9e9e65514b8d4ee5714485b6b442556d1c47d9b3c5b51d0`; served hash identical; minified literal `jt=[...Dt,\`/webchat\`,\`/qubit-bot\`,\`/qubit-office\`]` present in the **served** bytes | **PASS** |
| 1c | Mobile 390×844: no page overflow | `probe_office_parity.py` (measure, retry settle=18s) | `innerW=390 innerH=844 scrollH=844 overflowY=0 overflowX=0` | **PASS** |
| 1d | Mobile 360×800: no page overflow | same | `innerW=360 innerH=800 scrollH=800 overflowY=0 overflowX=0` | **PASS** |
| 1e | 390×844: iframe fills the office page | same | iframe `368×742 @ (11,102)`; shell wrapper `368×742`; `appState=ready`; no loading/failed banner | **PASS** |
| 1f | 360×800: iframe fills the office page | same | iframe `338×698 @ (11,102)`; shell wrapper `338×698`; `appState=ready` | **PASS** |
| 1g | 390×844: world canvas fills the iframe | same (`#world` box + backing store) | `#world` 368×742 == iframe inner 368×742; `scrollH=742 overflowY=0`; `attrW/H` 368×742 | **PASS** |
| 1h | 360×800: world canvas fills the iframe | same | `#world` 338×698 == iframe inner 338×698; `overflowY=0`; painted sample **100 % non-background** | **PASS** |
| 1i | Mobile office UI (`#ui`) does **not** collapse to nothing | `ui_dump_probe.py` | 390×844: `#ui` 368×742, **123 buttons, 11 inputs, 31 clickable**; `ui-topbar` 368×108 visible; `ui-feed` (Activity) visible with real event text | **PASS** |
| 1j | 360×800 office UI (`#ui`) | same | `#ui` 338×698, **123 buttons, 11 inputs, 31 clickable**; `ui-topbar` 338×108 visible; `ui-feed` visible | **PASS** |
| 1k | Mobile SSE stream live | `probe_office_parity.py` (Network events) | `GET /api/stream?token=…` → `200 text/event-stream`; `netfails=[]`; **zero console errors** | **PASS** |
| 2a | Firefox installed | `which firefox && firefox --version` | `/usr/bin/firefox`, `Mozilla Firefox 157.0.1` | **PASS (installed)** |
| 2b | Firefox live probe of the vendored app | `probe_office_parity.py firefox` | `RuntimeError: browser exited 1`; log: *"Firefox is already running, but is not responding … use a different profile."* See §2 for the full blocker chain. | **UNKNOWN (blocked)** |
| 2c | Firefox-specific static risk surface (absolute URLs, ES-module resolution, CSP, service worker) | `curl -s .../habblaud/` and `.../bundle/main-DND_b4_E.js` | All refs are **root-relative** under `/plugin-apps/qubit-office/habblaud/`; `__vite__mapDeps` resolves via `location.origin+\`/plugin-apps/qubit-office/habblaud/\`` (origin-preserving); **no service worker**; **no CSP / X-Frame-Options / COOP header** on either document | **PASS (static only — not a browser render)** |
| 3a | `?shell=1` fallback renders + does not throw (desktop) | `shell_paint_probe.py` | `office-game-shell` `1305×925`; engine canvas `1305×879` painted **94 %** non-bg; HUD text `PLACE Outside the building A+ Motion Settings`; `errors=[]` | **PASS** |
| 3b | `?shell=1` fallback (390×844) | same | shell `368×742`; engine canvas `367×704` painted **95 %**; HUD present; `errors=[]` | **PASS** |
| 3c | `?shell=1` fallback (360×800) | same | shell `338×698`; engine canvas `337×623` painted **95 %**; HUD present; `errors=[]` | **PASS** |
| 3d | `?shell=1` uses its own data path (not the SSE route) | `probe_office_parity.py shell` | Polls `GET /agent/api/office/tower` (OfficeAdapter); no `/api/stream`; `errors=[]` | **PASS** |

---

## §1 Mobile / narrow viewport — detail

The mobile surface is sound. Measured, not eyeballed:

* **The iframe is NOT a small box.** At 390×844 it is `368×742`; at 360×800 it
  is `338×698`. It occupies the full content box of the office page under the
  dashboard's sidebar/header — the wrapper (`.office-game`) matches the iframe
  exactly, so there is no letterboxing.
* **The world canvas fills the iframe.** `#world` box equals the iframe's inner
  viewport exactly (368×742 / 338×698), and the iframe's own
  `documentElement.scrollHeight == innerHeight` (`overflowY=0`, `overflowX=0`).
  The upstream CSS is `#world{width:100vw;height:100vh;position:fixed}` — inside
  a correctly-sized frame that resolves to the frame's own viewport, which is
  exactly what was measured.
* **No page-level overflow** on either viewport (`overflowY=0`, `overflowX=0`).
  This is the specific bug the repo's `game.css` comments call out (a 390px phone
  that once got a 574px shell / 500px stage); it does not reproduce.
* **The office UI does not collapse.** `#ui` reports **123 `<button>`, 11
  form inputs, 31 interactive elements**. `ui-topbar` (338–368 × 108) and the
  Activity `ui-feed` are `visibility:visible` with live event text
  (*"ceo started a turn … wren used a tool …"*). `ui-sidebar` / `ui-drawer`
  exist but are parked off-canvas (`x≈-348`, `visibility:hidden`) — that is the
  app's own slide-in drawer, opened by the topbar control, not a collapsed UI.
* **Live data on mobile.** `GET /api/stream?token=…` → `200 text/event-stream`,
  no failed requests, zero console errors.

**Noted, not a port failure:** the always-visible Activity strip (`ui-feed`) is
only **38px tall** on both mobile viewports. That is upstream Habblaud's own
layout, unchanged by the port; it is cramped but readable and is expandable.

**A transient was observed and diagnosed.** The first 390×844 run (settle 9s)
returned `frameCount=0` with 20 `net::ERR_ABORTED` and no iframe. Re-running the
identical viewport with `settle=18s` booted cleanly (`frameCount=1`,
`appState=ready`, SSE 200, `netfails=0`). It is a **boot race in the probe**
(the dashboard fetches ~15 plugin manifests before the office plugin mounts), not
a viewport-specific failure — 360×800 and 1600×1000 booted clean at the shorter
settle. The final numbers above are from the clean runs.

---

## §2 Both browsers — Firefox is BLOCKED (UNKNOWN)

**Firefox 157.0.1 is installed.** I could not run a live probe of it, and I did
**not** install anything or kill the user's browser. The blocker chain, with
exact observed output:

1. `/usr/bin/firefox` is a **snap wrapper**, not a native binary:
   `head -3 /usr/bin/firefox` → `#!/bin/sh` / `if ! [ -x /snap/bin/firefox ]`.
   `snap list | grep firefox` → `firefox 157.0.1-1 … mozilla**`.
2. Running from this session fails immediately:
   `firefox --headless --profile /tmp/…` →
   `Could not find profile folder.` / and via the probe,
   `RuntimeError: browser exited 1`.
3. Running it via `systemd-run --user --scope` (to escape the
   `qubit-voicebridge.service` cgroup — snap refuses to launch inside a
   non-snap cgroup: *"…is not a snap cgroup for tag snap.firefox.firefox"*)
   gets the launcher to start, but Firefox then refuses:
   **`Firefox is already running, but is not responding. To use Firefox, you
   must first close the existing Firefox process, restart your device, or use a
   different profile.`**
4. That lock belongs to the **user's own live Firefox** (`pgrep` → pid 30983,
   running since 09:16, `DISPLAY=:0`, `XDG_SESSION_TYPE=x11`). Snap overrides
   `HOME` to `~/snap/firefox/common` (`MOZ_LEGACY_HOME=1`,
   `SNAP_REAL_HOME=/home/trick`), so isolated `--profile`, alternate `HOME`, and
   DBus-session attempts all still collide with the single-instance lock. I will
   not kill a running user process to make a probe pass.

**Consequence:** the vendored app's **live render in Firefox is UNKNOWN** — not
PASS, not FAIL. The repo's own skill
(`qubit-habblaud-vendoring`) explicitly says *"never claim Firefox compatibility
solely from static checks"*; I am honouring that.

**Static Firefox-risk surface** (checked against the actual served bytes, so a
maintainer can act, but this is **not** a browser run):

* All sub-resource URLs are **root-relative** under the plugin path (favicon,
  manifest, `bundle/main-DND_b4_E.js`) — no absolute-origin literals.
* Vite's `__vite__mapDeps` resolves chunks as
  `new URL(d[i], location.origin + \`/plugin-apps/qubit-office/habblaud/\`).href`
  — origin-preserving, which is what the skill requires for subpath serving.
* **No service worker** registration in the bundle.
* **No `Content-Security-Policy`, `X-Frame-Options`, or `Cross-Origin-Opener-Policy`
  header** is set on either the plugin document or the SPA document (following
  the skill's stated Firefox-risk list, absence of CSP is *good* here — nothing
  to block the module import).

To remove the UNKNOWN, run the probe on a host where no Firefox is already using
the snap profile, or with a non-snap Firefox build
(`firefox-esr` / tarball). Neither is available on this host.

---

## §3 `?shell=1` rollback (hand-built React shell)

The rollback works and paints real pixels at every viewport tested — a blank
canvas would have been a FAIL.

| Viewport | shell box | engine canvas (visible) | painted non-bg | HUD | console errors |
|----------|-----------|-------------------------|----------------|-----|----------------|
| 1600×1000 | 1305×925 | 1305×879 | **94 %** | `PLACE Outside the building A+ Motion Settings` | 0 |
| 390×844  | 368×742  | 367×704  | **95 %** | same | 0 |
| 360×800  | 338×698  | 337×623  | **95 %** | same | 0 |

* Two canvases exist as designed: the hidden 880×500 measuring canvas
  (`data-testid="office-game-canvas"`, `display:none`, `box 0×0`) and the engine
  canvas the shell mounts into the stage (`1305×879` / `367×704` / `337×623`,
  `display:block`, `visibility:visible`). No "two visible canvases" bug.
* Data path is independent of the SSE route: the shell polls
  `GET /agent/api/office/tower` via `OfficeAdapter` and never opens
  `/api/stream` — which is exactly why it is a viable escape hatch if the
  stream regresses.
* `data-office-*` attributes render (`reduced-motion=0`, `contrast=normal`,
  `scale=1`); HUD text is present.

---

## Honest gaps

1. **Firefox live render: UNKNOWN** — blocked by the snap single-instance lock
   owned by the user's running Firefox (§2). Do not read §2c's static PASS as a
   Firefox-compatibility claim.
2. **The first 390×844 sample was a probe boot race**, re-run clean (§1). The
   verdict uses the clean run.
3. **SSE was observed for ~8–18s per run**, not for the full
   `STREAM_MAX_SECONDS`. Long-run stream stability is out of scope here.
4. **Touch *interaction*** (tapping a drawer/topbar control and observing the
   result) was not exercised — only control **presence and geometry** were
   measured. The task asked about rendering/overflow, which is covered.
5. No file under `frontend/qubit-*/` or `voicebridge/` was modified; the service
   on `:8890` was not restarted.
