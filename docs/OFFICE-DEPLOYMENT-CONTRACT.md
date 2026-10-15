# The Qubit Office page — deployment contract

The office page is assembled from pieces that live in four different places and
are stitched together **at serve time, not at build time**. Every failure mode
below has bitten this project, and every one of them is **silent**: the page
still returns `200`, the tab still opens, and the office is simply absent or
wrong. A rebuild or a restart can break any of them with **no tracked diff**,
because the piece that broke was an untracked artifact.

`tests/test_office_deployment_contract.py` pins all of it, against the **SERVED
truth over HTTP** — not the filesystem.

## Running it

```sh
# The served-truth HTTP/route checks (the dashboard must be up):
.venv/bin/python -m pytest tests/test_office_deployment_contract.py -m contract -v

# The headless-Chrome browser checks (need Chrome; excluded by the default -m):
.venv/bin/python -m pytest tests/test_office_deployment_contract.py -m live -v

# Both:
.venv/bin/python -m pytest tests/test_office_deployment_contract.py -m "contract or live" -v
```

The dashboard base defaults to `http://127.0.0.1:8890`; override with
`QUBIT_OFFICE_BASE_URL`. Chrome defaults to `/usr/bin/google-chrome`; override
with `QUBIT_CHROME`.

**A skip is not coverage.** When the dashboard is genuinely down the HTTP and
browser checks SKIP WITH A REASON naming the cause — never a silent pass. A
green run that proved nothing is worse than a red one.

## What is pinned, check by check

| # | Check | Test | Catches |
|---|-------|------|---------|
| 1 | **Entry agreement** | `test_the_three_entry_declaration_sites_agree_with_the_served_truth` | editing one of the three declaration sites while the SERVED copy still names the OLD entry |
| 1 | **Entry is served** | `test_the_named_entry_is_actually_served_with_a_js_content_type` | the declared entry 404s, or is served without a JS content type (browser refuses a module) |
| 2 | **Asset routes** | `test_the_entry_and_an_image_are_served_on_their_respective_routes` | an image under the wrong root 404s with **no console error** — missing sprites, no visible failure |
| 3 | **Route predicate** | `test_the_office_route_is_full_height_and_not_a_chat_route` | the office dropped from `FULL_HEIGHT_ROUTE_PATHS`, or merged into `CHAT_ROUTE_PATHS` |
| 3 | **Predicate in the SERVED bundle** | `test_the_SERVED_bundle_still_carries_the_office_in_the_full_height_list` | a source edit landed but the dashboard was never rebuilt (the predicate is a BUILT artifact) |
| 4 | **Iframe + stream** | `test_the_office_iframe_mounts_paints_and_opens_the_stream` | the tab opens but the office is blank / the SSE never opened |
| 5 | **Rollback** | `test_the_shell_rollback_renders_with_zero_console_errors` | `?shell=1` no longer renders the hand-built shell (the escape hatch rots) |

Marks: `contract` = served-truth HTTP/route checks; `live` = headless-Chrome
browser checks (opt-in; excluded by the repo's default `addopts`).

## The four silent-breakage modes

### 1. The entry is declared in THREE places

The plugin `entry` (`qubit-office.game.js`) is named in:

1. `frontend/qubit-app/plugins/qubit-office/manifest.json` — the plugin's own
   manifest (tracked, the one a human edits first).
2. `frontend/qubit-dist/plugins/fleet.json` — **the SERVED list** the loader
   merges. The API list wins over the bundled one.
3. `frontend/qubit-web/public/plugins/fleet.json` — the SPA's bundled list.

Editing the wrong copy leaves the page loading the OLD entry while the disk says
otherwise. The test reads the **served** list as the authority and requires the
other two sites to AGREE with it, so a one-copy edit is red immediately.

### 2. `frontend/qubit-dist/` is UNTRACKED and rebuild-wiped

`frontend/qubit-dist/` is untracked, and a dashboard rebuild
(`scripts/build_dashboard.sh`, which runs `--emptyOutDir`) can **WIPE** it —
breaking the tab with **no tracked diff**. The served-truth checks do not read
the filesystem for the served answer; they `curl` the running dashboard, so a
wiped directory shows up as a red HTTP check, not as a clean `git status`.

> Note today: the served entry resolves to
> `/dashboard-plugins/qubit-office/qubit-office.game.js` (from the app source),
> while the plugin's **assets** serve from `/plugins/qubit-office/…` (the SPA's
> `public/` tree). The two routes serve **different roots**; both are pinned.

### 3. The full-height predicate is a BUILT artifact

`FULL_HEIGHT_ROUTE_PATHS` lives in `frontend/qubit-web/src/lib/chat-routes.ts`.
It controls the layout chain in `App.tsx`: a page that relies on `height: 100%`
(a fixed-footer page, like the office's iframe) expands to its content height
unless its path is listed — the measured **104,305 px** bug (a 104,305 px
transcript in a 950 px viewport).

Full-height and chat are **different predicates** and must not be merged: chat
routes own the terminal surface; full-height only bounds the layout. Merging
them silently restyles the game.

Because the predicate ships in a **built** bundle, a source edit does nothing
until the dashboard is rebuilt — so there are **two** checks: the source
contains the office, and the **SERVED** index bundle contains it. The second is
the one that catches "edited but not rebuilt".

### 4. Early sampling is a false negative

Sampling the live page too early reports `Connecting… / 0 non-background pixels`:
the office takes ~0.3 s to connect and the iframe longer to attach. A single
reading is therefore worthless. Both browser checks **poll until the canvas has
painted** (and, for the SSE check, keep draining CDP events until the
`/api/stream` response is recorded) instead of taking one read.

> The CDP client's `cdp.events` only fills **while a call is waiting for its own
> response**. A passive "has the SSE arrived?" check never drains and flakes to
> *no /api/stream observed* — so the stream wait issues a cheap CDP call each
> iteration.

## Recovering the page after a break

1. Identify which check is red; each one names the site it read.
2. If **entry agreement** is red: make the plugin manifest, the SERVED
   `fleet.json`, and the SPA `public/fleet.json` all name the same entry, then
   **rebuild the dashboard** (the served list is a build output).
3. If **the served route 404s**: the artifact was wiped (see mode 2) — rebuild
   the dashboard and re-run, confirming the office suites still **execute**:
   `.venv/bin/python -m pytest tests/test_office_game_*.py -q` (a wall of `s` is
   the tell, not a pass).
4. If **the route predicate** is red: add `"/qubit-office"` to
   `FULL_HEIGHT_ROUTE_PATHS` (and **not** to `CHAT_ROUTE_PATHS`) in
   `chat-routes.ts`, then rebuild.
5. If **the iframe/stream** is red but assets serve: check the vendored app's
   own route (`/plugin-apps/qubit-office/habblaud/`, 200) and the SSE auth
   policy (`/api/stream` must return `200 text/event-stream` with the token).
   Escape in one reload with `?shell=1`.

## Related failures this suite does not cover

These are documented elsewhere and are out of scope here, but they break the
office page the same silent way:

- `frontend/qubit-app/plugins/qubit-office/qubit-office.game.js` is a bundle
  with a hard size ceiling for `node -e` harnesses (see the
  `office-bundle-module-size-and-key-contracts` skill) — but it is evaluated by
  the browser here, so no argv limit applies.
- The bundled game engine (Phaser) probes the browser while its module body
  evaluates, so the artifact cannot be evaluated in a DOM-less `node`/`vm`
  context at all — this suite drives a real browser instead.
- `iso-engine.js`'s export list and hard constraints are pinned by the
  `test_office_iso_engine.py` suite, not here.
