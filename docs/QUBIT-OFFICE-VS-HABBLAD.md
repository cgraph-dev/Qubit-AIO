# Qubit Office vs Habblaud — how each works, and the differences

**Status:** comparison and code inventory. **Not a verdict.** Nothing here is `VERIFIED`,
`COMPLETE`, or a parity claim. It describes what exists in the tree today.

This document explains two different things that share one renderer:

1. **Habblaud** — the upstream project (`marmottajr/habblaud`, MIT), a standalone local web
   application for watching Claude Code / Codex agents.
2. **Qubit Office** — the `/qubit-office` page in the Qubit dashboard, which embeds a vendored
   build of Habblaud's **client** and feeds it from Qubit's own office backend.

Habblaud is **not** a desktop app. Both surfaces are browser UIs served over HTTP. The real
difference is *who serves the page*, *who owns the server*, and *where the data comes from*.

---

## 1. At a glance

| | Upstream Habblaud | Qubit Office (`/qubit-office`) |
|---|---|---|
| Form | Standalone local web app | A page inside the Qubit dashboard |
| URL | `http://localhost:4747` | `http://127.0.0.1:8890/qubit-office` |
| Process | Its own Node server (or Docker container) | The Qubit dashboard's Python server |
| Client | Its own Vite build, served at `/` | Habblaud's built client + local art/FX changes, vendored at `/plugin-apps/qubit-office/habblaud/` and shown in an iframe |
| Data source | Claude Code / Codex session files on disk | Qubit's office SQLite store (`office.db`) and Business OS |
| Auth | Loopback by default; optional separate HTTPS port with login | Qubit session token + a loopback/same-origin/Referer/load-nonce gate for the frame |
| Extras | Claude Code mod/plugins, statusline usage tap, Codex bridge, Docker, phone/HTTPS remote, terminal SSE | Qubit host docks (Agents, HUD, Approvals, War Room, Command Center, Timeline, Studio, Campus), Business OS APIs, seat routing, themes/FX |
| Always on | Yes, independent of any dashboard | Only while the Qubit dashboard runs |
| License | MIT (upstream) | Same MIT client, vendored; attribution in `THIRD_PARTY_NOTICES.md` |

---

## 2. How upstream Habblaud works

Architecture, from its own README:

```
~/.claude*/sessions/<pid>.json ────────────────┐
~/.claude*/projects/<project>/<session>.jsonl ─┼─▶ Node server ─▶ office model ─▶ SSE ─▶ browser
.../<session>/subagents/**/agent-*.jsonl ──────┘   (1 s poll)                  (canvas + UI)
```

- **Who is in the office:** an open Claude Code session (`sessions/<pid>.json`) decides which
  project room exists and what its agent is doing (busy, idle, waiting on you, waiting on a shell).
- **What each agent is doing:** the server follows each session's transcript (`*.jsonl`) and turns
  tool calls into activities, tasks, title, model, git and stats. Subagent transcripts become child
  characters.
- **Drawing:** the browser receives a `snapshot` + `feed` over SSE and paints everything on a canvas.
  Furniture, floors and bodies are procedural pixel art; social life, hangouts and wallets are a
  client-side simulation (`client/src/world/social/`) with no effect on real agents.
- **Surrounding pieces:** a Claude Code **mod** plus permission and message plugins, a statusline
  usage tap, a Codex bridge, Docker packaging, and an optional second HTTPS port for phone approval.
- **Routes** (upstream README): `/api/stream`, `/api/snapshot`, `/api/agents/:id`,
  `/api/agents/:id/terminal`, `PUT|DELETE /api/agents/:id/character`, `/api/sessions/recent`,
  `/api/sessions/:account/:sessionId/terminal`, `/api/stats[/days]`, `/api/timeline[/days]`,
  `/api/health`, `/api/demo`, `/api/rooms/rename`, `/api/mod/*`, `/api/permissions*`,
  `/api/messages*`, `/api/codex/*`, `/api/deliveries`.

How to run it (from its README): `npm start` after `npm run build` (Node 22.12+), or
`npm run docker:up`; then open `http://localhost:4747`.

The upstream build output is not committed; `client/src` is the source of truth (170 files at the
pin, verified below).

---

## 3. How Qubit Office works

### 3.1 Page, plugin, iframe

- The dashboard route is `frontend/qubit-web/src/App.tsx:418`:
  `"/qubit-office": () => <PluginPage name="qubit-office" />`.
- The plugin manifest (`frontend/qubit-app/plugins/qubit-office/manifest.json:12`) declares
  `entry: "qubit-office.game.js"` and `css: "qubit-office.game.css"`.
- The entry (`game/entry.ts:67-73`) registers the React component. Default is the **vendored
  Habblaud app**; `?shell=1` mounts the older Qubit vector shell for comparison only.
- `game/app/office-app.ts:25` sets `OFFICE_APP_PATH = "/plugin-apps/qubit-office/habblaud/"`, and
  `OfficeAppShell.tsx:796` renders that document in a sandboxed `<iframe>`. `?theme=` and
  `?floor=live:<n>` are appended to the iframe URL; `?shell`/`?app` choose the renderer.

### 3.2 Host shell around the iframe

The Qubit shell is not upstream. It adds:

- a host-side poll of `GET /agent/api/office/tower` every 2 s
  (`game/app/office-api.ts:48,50`, `game/adapter.ts:129`) for the seat list and a real connection
  banner;
- docks opened from the office's own Panels icon row: **Agents, HUD, Approvals, War Room, Command
  Center, Timeline, Studio** (Room Builder, Outside World, Panel Inventory), plus **CampusWorld**,
  **TowerNavigator**, **CharacterStudio**, **SeatModelPanel** and **EtsyConnectPanel**;
- per-seat model routing (`game/entry.ts:90`) and the 2Five/theme skin injection. The shell patches
  the vendored topbar to say "Qubit Office" and it does **not** paint a persistent credit bar over
  the game (asserted by `OfficeAppShell.test.ts`). The LimeZu tile credit appears in the Outside
  World panel. Legal attribution lives in `THIRD_PARTY_NOTICES.md`; the vendored
  `frontend/qubit-app/plugins/qubit-office/habblaud/LICENSE` travels with the served tree and is
  served by `_serve_plugin_app` (pinned by `test_extensionless_licence_is_served_as_a_notice`).

### 3.3 Backend: Python replaces Habblaud's Node server

- The office store is SQLite at `$QUBIT_HOME/voice/state/office/office.db`
  (`voicebridge/office/store.py:250` defines `office_meta`; `:289` `office_agent`; `:603`
  `office_event`; 20 `office_*` tables are declared there).
- `voicebridge/office/tower.py:597` folds the store into the tower report (floors, rooms, desks,
  characters, HUD). `voicebridge/office/runner.py:170` runs a real agent turn and writes events and
  receipts.
- `voicebridge/office_stream.py:368` (`to_snapshot`) and `:581` (`to_feed`) translate that tower into
  the client's `OfficeSnapshot`/`FeedItem` schema, and `:705` (`stream_frames`) emits the SSE
  frames. This is a **translation of the same tower read**, not a second source of truth.
- The dashboard serves the frame's live feed at `GET /api/stream` via
  `voicebridge/webui.py:2832` (`_serve_office_stream`), and the one-shot JSON at `/api/snapshot`.

### 3.4 How the vendored client's `/api/*` calls are answered

The client was written against Habblaud's own server and issues literal `/api/...` requests. Qubit
does **not** let those reach the general dashboard API:

- `voicebridge/webui.py:4440` routing sends the office client's literal `/api/*` reads to
  `_serve_office_client_api` (`:2405`), which remaps `/api/<suffix>` to the Qubit compat family
  `/api/office/habblaud/<suffix>` and dispatches through the one compat route table.
- Two gates authorise it: `_office_client_read_ok` (`:2303`) for GET and
  `_office_client_mutation_ok` (`:2372`) for writes. Both require a loopback peer, a same-origin
  `Host`, an own-app `Referer`, and the live server-issued load nonce; a query token is never
  accepted. The write gate also validates a real session token when one is supplied. The read gate
  deliberately refuses a token-bearing request, so a real API caller is never served the office
  client's document instead of the normal route.
- The host also tries a small `fetch` bridge before setting the iframe `src`
  (`frontend/qubit-app/plugins/qubit-office/game/app/api-bridge.ts`,
  `OfficeAppShell.tsx:612`), but a same-origin navigation replaces the frame's realm and discards
  the patch — `_office_client_read_ok`'s own docstring records this. The server-side front door is
  therefore the effective boundary, not the bridge.
- A path inside the client's allowlisted dialect that has no compat row gets a **typed JSON
  refusal**, never a bare 404; the catch-all is
  `h_office_habblaud_refuse` (`voicebridge/qubit_compat.py:7545`), answering
  `403 OFFICE_CLIENT_UNSUPPORTED`.

### 3.5 What is real vs. refused in the embedded client

Real (bound to Qubit data or the shared journals):

- reads: `access`, `status`, `snapshot`, `rooms`, `agents`, `agents/panel`, one agent,
  `sessions/recent`, `timeline[/days]`, `stats[/days]`, `spawn/folders`, `spawn/<id>`,
  `dictation`, `permissions`, `messages`;
- writes: character `PUT`/`DELETE`, permission decision (settles the **shared approval journal**),
  message send (persists an `office_message` and delivers a real claimed office task), room rename,
  queued spawn task, seat model assignment, demo toggle, read-only update check.

Typed refusals (by design):

- raw PTY / terminal streams, session terminal streams, unknown agent sub-actions, dictation
  writes, spawn attachments, and the POST catch-all → `403 OFFICE_CLIENT_UNSUPPORTED`;
- remote logout → `409 NO_REMOTE_SESSION` (there is no remote office session to end);
- `GET deliveries` is a typed **empty** answer (`200`, `supported:false`, empty list) because Qubit
  has no deliveries registry — it is not a fabricated list and not a 403.

The snapshot's `meta.terminal` and `meta.messages` gates are `true` because the permission-decision
and room-rename controls are really bound — it is **not** a claim that Qubit exposes a PTY.

### 3.6 What the embedded client does not get from Qubit

- `to_snapshot` returns `accounts: []`. Upstream's account letters and 5-hour/weekly usage counters
  have no Qubit source, so those parts of the topbar are empty rather than fabricated.
- Upstream's Claude Code mod, Codex bridge, mod inbox and remote HTTPS login are not vendored.
  Deliveries has only a typed empty response (no registry), not a real replacement.
- Upstream reads Claude Code transcripts directly; Qubit reads its own office store. A Qubit
  character is a Business OS/office seat, not a live `claude` process.

### 3.7 Vendored client provenance and local changes

- Source: `https://github.com/marmottajr/habblaud`, upstream commit
  `4ccce2537472fe5a8d432f06974494d2e778ff15`; local source commit
  `dbce5de1d881922853f92f344979b27542f0daf7` (see
  `frontend/qubit-app/plugins/qubit-office/habblaud/PROVENANCE.json`).
- Only the built **client** is vendored: 41 assets, 33 bundle chunks, 10 Sakuga files,
  `index.html`, `LICENSE` and `manifest.webmanifest` — 87 files / 3,046,780 bytes as recorded by the
  vendor manifest, plus `frontend/qubit-app/plugins/qubit-office/habblaud/PROVENANCE.json` beside
  them (88 files on disk). The server, Claude Code mod and Docker setup are not present.
- `scripts/vendor_habblaud.py` performs the build copy: it rewrites absolute asset paths to the
  plugin prefix, pins the locale to `en`, renames user-facing document/PWA copy to "Qubit Office",
  and records hashes. `python3 scripts/vendor_habblaud.py --check` must stay green.
- Local Habblaud source changes on top of the pin: 22 commits, 63 files, **+5,706 / −268**, 20 new
  files. They fall into three groups:
  1. **2Five room art** — `client/src/world/layout/twofive.ts`, core/project room surfaces,
     furniture, sakura tree and paper lantern, stable seat ids;
  2. **Sakuga VFX/UI** — vendored `client/public/sakuga-vfx/*` (10 JS files),
     `client/src/ui/sakuga.ts`, `client/src/art/sakuga/fx.ts`, converted controls, an FX overlay
     anchored to real character positions, feed/topbar fixes, and the exposed Sakuga back layer;
  3. **Optional theme assets** — `client/src/art/theme-assets.ts`, themed floor tiles with a procedural
     fallback, theme character/furniture/Modern UI slices, and extra host fields in `shared/types.ts` (upstream root).

---

## 4. Differences that matter

| Area | Upstream Habblaud | Qubit Office |
|---|---|---|
| Deployment | Own Node/Docker process on :4747 | Served by the Qubit Python dashboard on :8890 |
| Independence | Runs with or without a dashboard | Requires the Qubit dashboard |
| Data | Claude Code/Codex session files | Qubit `office.db` + Business OS |
| Identity of an agent | A live Claude session / subagent | A Business OS / office seat |
| Server code | TypeScript Node server | Python `voicebridge/office` + `qubit_compat` |
| Client code | Its own build, served at `/` | Habblaud build + documented local art/FX changes, re-pathed, English, re-branded |
| Account/usage counters | From Claude accounts | Empty (`accounts: []`) |
| Terminal | Real terminal SSE | Typed refusal |
| Permissions / messages | Mod + hook queues | Qubit approval journal + office message/task |
| Codex support | Codex bridge | Not vendored |
| Remote/phone | Optional HTTPS + login | Not applicable; loopback dashboard |
| Panels | Habblaud's own UI | Habblaud UI plus Qubit host docks |
| Business data | None (project rooms only) | Revenue, cost, tasks, artifacts, War Room, departments |
| Themes/art | Procedural pixel art | Procedural art + an optional theme pack built locally (2Five/LimeZu slices; not committed) |
| Projected effects | None from Qubit business events | Sakuga VFX from real `office_event` + recent `bos_event` rows |
| Update path | `npm`/Docker, upstream releases | Rebuild the vendored client + bump PROVENANCE |
| Attribution | Its own LICENSE/README | `THIRD_PARTY_NOTICES.md` + vendored LICENSE; LimeZu tile credit in Outside World |

---

## 5. Practical consequences

- **You cannot use `/qubit-office` as a drop-in Habblaud replacement.** It shows Qubit's office,
  not your Claude Code processes. A project room appears because the office store has a
  floor/room, not because a terminal is open.
- **Upstream-only capabilities are absent or refused**, by design: raw terminal, deliveries, the
  Codex bridge, account usage, remote phone login, and the Claude Code mod.
- **Qubit-only capabilities are additive**: the host docks, Business OS APIs
  (`/api/office/business/*`, `/api/office/world/*`, `/api/office/tower`), seat model routing, the
  character store, and the theme/FX layer.
- **Shared**: the canvas renderer, the SSE protocol (`snapshot` + `feed`), the room/agent model,
  character editing, timeline/stats views (adapted to Qubit data), and the demo toggle.
- **`?shell=1`** still mounts the older Qubit vector shell. It is a comparison/fallback surface, not
  a second product.

---

## 6. Where the code lives

| Concern | Path |
|---|---|
| Route + sidebar entry | `frontend/qubit-web/src/App.tsx:418`, `:585` |
| Plugin manifest | `frontend/qubit-app/plugins/qubit-office/manifest.json` |
| Entry / renderer choice | `frontend/qubit-app/plugins/qubit-office/game/entry.ts` |
| Host shell + docks | `frontend/qubit-app/plugins/qubit-office/game/app/OfficeAppShell.tsx` |
| Iframe URL + stream URL | `frontend/qubit-app/plugins/qubit-office/game/app/office-app.ts` |
| Client `fetch` bridge | `frontend/qubit-app/plugins/qubit-office/game/app/api-bridge.ts` |
| Vendored Habblaud client | `frontend/qubit-app/plugins/qubit-office/habblaud/` |
| Vendor/verify script | `scripts/vendor_habblaud.py` |
| Office store + tower | `voicebridge/office/store.py`, `voicebridge/office/tower.py` |
| Turn execution | `voicebridge/office/runner.py` |
| SSE translation | `voicebridge/office_stream.py` |
| Server routes / auth gates | `voicebridge/webui.py` (`_serve_office_stream:2832`, `_office_client_read_ok:2303`, `_office_client_mutation_ok:2372`, `_serve_office_client_api:2405`) |
| Compat handlers / refusals | `voicebridge/qubit_compat.py` (`h_office_habblaud_refuse:7545`) |
| Business OS | `voicebridge/business/` |

## 7. How to check this document

```bash
# Vendored client identity and hash manifest (should print ok + pin)
python3 scripts/vendor_habblaud.py --check

# The iframe path and the client entry
grep -n 'OFFICE_APP_PATH' frontend/qubit-app/plugins/qubit-office/game/app/office-app.ts
grep -n 'register("qubit-office"' frontend/qubit-app/plugins/qubit-office/game/entry.ts

# The compat route table for the embedded client
grep -n 'api/office/habblaud' voicebridge/qubit_compat.py | head -40

# The server-side auth gates
grep -n '_office_client_read_ok\|_office_client_mutation_ok' voicebridge/webui.py

# Local Habblaud changes on top of the pin (in a Habblaud checkout)
git -C "$HABBLAUD_SRC" diff --stat 4ccce2537472fe5a8d432f06974494d2e778ff15..dbce5de1d881922853f92f344979b27542f0daf7
git -C "$HABBLAUD_SRC" log --oneline 4ccce2537472fe5a8d432f06974494d2e778ff15..dbce5de1d881922853f92f344979b27542f0daf7

# Upstream client source size at the pin
git -C "$HABBLAUD_SRC" ls-tree -r --name-only 4ccce2537472fe5a8d432f06974494d2e778ff15 -- client/src | wc -l

# The deployment contract (served truth, not the filesystem)
.venv/bin/python -m pytest -q tests/test_office_deployment_contract.py -m contract
```

## 8. Verification status

Run against the current tree while writing this document:

- `python3 scripts/vendor_habblaud.py --check` → `ok: 87 files, 3046780 bytes, commit 4ccce2537472`.
- `.venv/bin/python -m pytest -q tests/test_office_deployment_contract.py -m contract` against the
  live dashboard → **5 passed, 2 deselected** (the served-truth HTTP/route checks).
- `.venv/bin/python scripts/verify_doc_citations.py` → `OK — every citation resolves`.
- The cited line numbers and the vendored/tree counts were re-derived with the commands in §7.
- `/qubit-office` and `/api/health` answered HTTP 200 on `127.0.0.1:8890`.

Verification found and fixed one real defect: `GET /plugin-apps/qubit-office/habblaud/LICENSE`
returned **404** on the running service because `_serve_plugin_app`'s directory-default rule
appended `index.html` to any extensionless basename, making the intended NOTICE_NAMES branch
unreachable. The source is fixed and pinned by
`tests/test_office_pages_security_contract.py::test_extensionless_licence_is_served_as_a_notice`
(proved red without the fix, green with it). The running service was not restarted, so that live
URL stays 404 until it is.

Not verified here:

- No headless-browser `-m live` run was made for this document.
- No dashboard rebuild or service restart was performed, so the served bundle can predate source
  edits.
- `frontend/qubit-dist/plugins/fleet.json` and the built bundle are artifacts; the source tree is
  authoritative after a rebuild.
- Upstream's own test suite and its mod/Codex/Docker paths were not exercised.
