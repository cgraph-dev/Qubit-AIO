# Qubit Office vs Habblaud vs StarNet — three agent-workspace designs

**Status:** comparison and code inventory. **Not a verdict.** Nothing here is `VERIFIED`,
`COMPLETE`, or a parity claim. It describes what exists in the tree today.

*(The filename keeps its original two-project name so existing links and the STATUS log do not
break; the document now covers three projects.)*

This document explains three different products that put agents in a pixel-art workspace:

1. **Habblaud** — the upstream project (`marmottajr/habblaud`, MIT), a standalone local web
   application for watching Claude Code / Codex agents.
2. **Qubit Office** — the `/qubit-office` page in the Qubit dashboard, which embeds a vendored
   build of Habblaud's **client** and feeds it from Qubit's own office backend.
3. **StarNet** — the upstream project (`androoAGI/starnet`, MIT code), a local-first **desktop
   harness** that creates and runs its own AI-agent teams and renders their live state as a
   pixel-art space station.

The form difference is real but often mis-stated: **Habblaud and Qubit Office are browser UIs;
StarNet is a desktop app (Tauri).** The deeper difference is *who owns the agent runtime*:
Habblaud observes Claude Code, Qubit Office projects Qubit's own office/Business OS, and StarNet
runs its own harness.

---

## 1. At a glance

| | Upstream Habblaud | Qubit Office (`/qubit-office`) | StarNet |
|---|---|---|---|
| Form | Standalone local web app | A page inside the Qubit dashboard | Local-first desktop app (Tauri) |
| Launch | `http://localhost:4747` | `http://127.0.0.1:8890/qubit-office` | installers; `node sidecar/index.js` → `:8787`; `starnet` terminal client |
| Process | Its own Node server (or Docker) | The Qubit dashboard's Python server | Rust/Tauri shell + embedded Node sidecar |
| Client | Its own Vite build, served at `/` | Habblaud's built client + local art/FX, in an iframe | Vanilla JS station UI (no frontend build step) |
| Data source | Claude Code / Codex session files | Qubit's office SQLite store + Business OS | StarNet's own local workspace, transcripts and ledgers |
| Who runs agents | Claude Code (external) | Qubit runtime (Hermes-derived) | StarNet's own sidecar harness |
| Visual | Office rooms | Office + Qubit host docks | Pixel-art space station |
| Auth | Loopback; optional HTTPS + login | Qubit token + loopback/same-origin/nonce gate | Local app; keys in OS keychain; local API auth |
| Extras | Claude Code mod/plugins, statusline usage, Codex bridge, Docker, remote, terminal SSE | Host docks, Business OS APIs, seat routing, themes/FX | Models, tools/MCP, channels, cron, spend, permissions/E-STOP, voice |
| Platforms | macOS / Linux (Node or Docker) | Wherever the Qubit dashboard runs | Windows / macOS public; Linux internal |
| Always on | Yes | Only while the dashboard runs | Yes (tray/background) |
| License | MIT | MIT client, vendored; attribution in `THIRD_PARTY_NOTICES.md` | MIT code; name/logo/art not licensed |

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

## 3. How StarNet works

> **Path scope:** every path in §3 (for example `frontend/app/…`, `sidecar/…`,
> `docs/VOICE_CHAT_PLAN.md`) is inside the **StarNet repository**, not this Qubit tree.
> StarNet is not vendored here.

StarNet (`https://github.com/androoAGI/starnet`, MIT code, © 2026 Andrew Sims) is a
**local-first desktop harness** — not a viewer. Its own description: "A local-first, gamified
desktop harness for building and running real AI-agent teams." You create agents, give them
workspaces, tools and bounded permissions, and watch them work; the station is the projection
of that runtime state.

Verified with a shallow clone on 2026-11-26 (commands in §8):

- default branch `feat/harness-backend`, HEAD `e316231d70610aac7b181c4efd1a10b3fc833cdc`
  (2026-11-07), version `0.15.0` (`package.json` and `src-tauri/tauri.conf.json`);
- `LICENSE` is MIT, Copyright (c) 2026 Andrew Sims; `NOTICE.md` records third-party skill
  credits. The README states the MIT licence covers the **code only** — the StarNet name,
  logo, station art and sprites are **not** licensed with it;
- no `habblaud` or `qubit` references were found in the tree; the harness is partly ported
  from Hermes (see §3.5).

### 3.1 Process model

ONE Node process: `node sidecar/index.js` → HTTP + SSE on `:8787` (`sidecar/index.js`), serving
the frontend and running the agent loop in-process. The Rust/Tauri desktop app embeds that same
sidecar and compiles the frontend into the executable (`src-tauri/`). The README also documents
a `starnet` terminal client that reads the same station state and can start the app in the
background. Public installers are Windows and macOS; Linux is an internal build target only.

### 3.2 The station is a projection, not a simulation

The pixel-art station is rendered from live harness state. The README's product contract: a room
is a capability-scoped team, a hallway is an authorized handoff lane, and a placed object is a
real capability grant — "the layout you draw *is* the workflow the agents run." It states that
StarNet does not simulate revenue, completed work, model activity or spend, and that the
interface must never assert state the harness cannot prove.

### 3.3 What the harness owns

From `CODE_MAP.md` and the tree:

- **Agent loop + providers** (`sidecar/loop.js`, `sidecar/providers/`): Anthropic, OpenRouter,
  OpenAI-compatible, Gemini, Codex, Claude CLI and local Ollama.
- **Tools and capability gates** (`sidecar/tools/`, `sidecar/capability/`): a registry plus
  web/browser/computer/desktop/fs/notebook/recall/skills/image/shell tools, with a
  station-layout → tool-allowlist gate before tools run.
- **Consent + permissions** (`permissions.js`, `permgrants.js`, `halt.js`): an explicit consent
  broker, grants and an E-STOP.
- **Channels** (`sidecar/channels/`): Telegram, Discord, Slack, Signal and Matrix, with pairing,
  a delivery ledger and admission.
- **MCP** (`sidecar/mcp/`): stdio/HTTP transports and an OAuth client.
- **Cron / routines / loops**: schedules, locks, stores and channel notifications.
- **Spend and ledgers** (`cost.js`, `spend.js`, `credits.js`, `ledger.js`): real USD
  reconciliation, budgets and run history.
- **Persistence and memory**: atomic stores for checkpoints, transcripts, skills and workshop
  state; Cortex recall/redaction and context compaction.
- **Frontend** (`frontend/`, vanilla JS modules; no frontend build step): the canvas station,
  COMMS chat/voice, quests/goals/recipes/workstreams and the station editor.

### 3.4 Voice

`docs/VOICE_CHAT_PLAN.md` (91 lines) specifies two-way voice for COMMS: push-to-talk STT into
the same chat send path, agent replies spoken once per finished reply, and a deterministic
per-agent voice. It leaves an explicit seam for neural voices. The current frontend includes
`app/voice.js` and `app/voice-state.js`.

Qubit already keeps a read-only map of StarNet's voice pipeline in
`docs/STARNET_VOICE_STUDY.md` (2026-10-17), read from a local `starnet-live` checkout. That
checkout's current HEAD is `54899e5ff3f2b002b457757916b3977818db6fc1` (2026-10-09) — older than
the `e316231d…` pin used here — and the study did not record a commit. Read that study for the
exact STT/TTS ladder rather than re-deriving it here.

### 3.5 Relationship to Qubit and Habblaud

- **No direct code relationship with Qubit Office or Habblaud**; the repo has no references to either.
- **Lineage via Hermes:** `NOTICE.md` says the harness was "in part ported from" the Hermes
  agent project (© 2025 Nous Research, MIT). Qubit's runtime is also Hermes-derived, so the two
  share an upstream ancestor but are independent products. StarNet can import an existing
  OpenClaw or Hermes home as a new StarNet agent.
- **The deleted `/qubit-office3`** (Voice Chat) was a Qubit-authored reimplementation of
  `docs/VOICE_CHAT_PLAN.md`, not vendored StarNet code, and was removed on 2026-11-26. StarNet
  itself remains the reference design.

---

## 4. How Qubit Office works

### 4.1 Page, plugin, iframe

- The dashboard route is `frontend/qubit-web/src/App.tsx:418`:
  `"/qubit-office": () => <PluginPage name="qubit-office" />`.
- The plugin manifest (`frontend/qubit-app/plugins/qubit-office/manifest.json:12`) declares
  `entry: "qubit-office.game.js"` and `css: "qubit-office.game.css"`.
- The entry (`game/entry.ts:67-73`) registers the React component. Default is the **vendored
  Habblaud app**; `?shell=1` mounts the older Qubit vector shell for comparison only.
- `game/app/office-app.ts:25` sets `OFFICE_APP_PATH = "/plugin-apps/qubit-office/habblaud/"`, and
  `OfficeAppShell.tsx:796` renders that document in a sandboxed `<iframe>`. `?theme=` and
  `?floor=live:<n>` are appended to the iframe URL; `?shell`/`?app` choose the renderer.

### 4.2 Host shell around the iframe

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

### 4.3 Backend: Python replaces Habblaud's Node server

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

### 4.4 How the vendored client's `/api/*` calls are answered

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

### 4.5 What is real vs. refused in the embedded client

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

### 4.6 What the embedded client does not get from Qubit

- `to_snapshot` returns `accounts: []`. Upstream's account letters and 5-hour/weekly usage counters
  have no Qubit source, so those parts of the topbar are empty rather than fabricated.
- Upstream's Claude Code mod, Codex bridge, mod inbox and remote HTTPS login are not vendored.
  Deliveries has only a typed empty response (no registry), not a real replacement.
- Upstream reads Claude Code transcripts directly; Qubit reads its own office store. A Qubit
  character is a Business OS/office seat, not a live `claude` process.

### 4.7 Vendored client provenance and local changes

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

## 5. Differences that matter

| Area | Upstream Habblaud | Qubit Office | StarNet |
|---|---|---|---|
| Deployment | Own Node/Docker process on `:4747` | Served by the Qubit Python dashboard on `:8890` | Tauri desktop app; sidecar on `:8787` in source/browser mode |
| Independence | Runs with or without a dashboard | Requires the Qubit dashboard | Standalone desktop app; can run in the tray |
| Agent runtime owner | Claude Code / Codex (external) | Qubit's runtime / Business OS seats | StarNet's own Node sidecar harness |
| Data / state | Claude session files on disk | Qubit `office.db` + Business OS | StarNet workspace, saves, transcripts, ledgers |
| Identity of an agent | A live Claude session / subagent | A Business OS / office seat | A created StarNet agent with class, workspace and permissions |
| Server / engine code | TypeScript Node server | Python `voicebridge/office` + `qubit_compat` | Node sidecar (`sidecar/`, agent loop, providers, tools) |
| Client code | Its own Vite build, served at `/` | Habblaud build + local art/FX, re-pathed, English, re-branded | Vanilla JS station UI, no frontend build step |
| Visual metaphor | Office rooms | Office + Qubit host docks | Pixel-art space station |
| Multi-agent | One room per open session | Office floors / rooms / desks (seats) | Several concurrent agents, each a separate bounded run |
| Models / providers | Not applicable (observes Claude) | The Qubit profile's models | Anthropic, OpenRouter, OpenAI-compatible, Gemini, Codex, Claude CLI, Ollama |
| Tools / MCP | Mod hooks only | Qubit tools/approvals; MCP is Qubit's own | Tools + MCP client, gated by the station layout |
| Channels | Mod permission/message plugins | Qubit channels (its own) | Telegram, Discord, Slack, Signal, Matrix |
| Cron / routines | None | Qubit cron (its own) | Routines, loops and schedules in the harness |
| Permissions / consent | Mod + hook queues | Qubit approval journal + office message/task | Consent broker, grants, E-STOP |
| Spend / budget | Reads Claude account usage | Not a Qubit Office feature | Real USD reconciliation, budgets and ledgers |
| Memory | Reads Claude session transcripts | Qubit memory (its own) | Cortex recall/redaction + context compaction |
| Voice | Dictation in (Whisper); no agent voice | Dictation status via the compat route (writes refused); no agent voice | Push-to-talk STT + spoken replies (one locked station voice; per-agent override) |
| Business / office concept | Project rooms only | Revenue, cost, tasks, artifacts, War Room, departments | Station rooms/bays are capability teams; work lands in OUTBOX |
| Account / usage counters | From Claude accounts | Empty (`accounts: []`) | Spend/budget shown from StarNet's own ledger |
| Terminal | Real terminal SSE | Typed refusal | `starnet` CLI client; desktop tools through the harness |
| Remote / phone | Optional HTTPS + login | Not applicable; loopback dashboard | Channel messaging (Telegram/Discord/Slack/Signal/Matrix) |
| Themes / art | Procedural pixel art | Procedural art + optional locally built theme pack (2Five/LimeZu slices) | StarNet's own station art and sprites |
| Projected effects | None from Qubit business events | Sakuga VFX from real `office_event` + recent `bos_event` rows | World animation from live harness events |
| Update path | `npm`/Docker, upstream releases | Rebuild the vendored client + bump PROVENANCE | Upstream installers/updater |
| Attribution | Its own LICENSE/README | `THIRD_PARTY_NOTICES.md` + vendored LICENSE; LimeZu tile credit in Outside World | MIT code; `NOTICE.md`; brand/art reserved |
| Hermes lineage | None | Runtime is Hermes-derived | Harness is partly ported from Hermes (per `NOTICE.md`) |

---

## 6. Practical consequences

- **They answer three different questions.** Habblaud: "what are my Claude Code agents doing right
  now?" Qubit Office: "what is my Qubit office/Business OS doing?" StarNet: "run my own agent team
  in a station." They are not three skins of one product.
- **Only StarNet is a desktop app.** Habblaud and Qubit Office are browser UIs served over HTTP.
- **You cannot use `/qubit-office` as a drop-in Habblaud replacement.** It shows Qubit's office,
  not your Claude Code processes. A project room appears because the office store has a
  floor/room, not because a terminal is open.
- **Upstream-Habblaud-only capabilities are absent or refused**, by design: raw terminal,
  deliveries, the Codex bridge, account usage, remote phone login, and the Claude Code mod.
- **Qubit-only capabilities are additive**: the host docks, Business OS APIs
  (`/api/office/business/*`, `/api/office/world/*`, `/api/office/tower`), seat model routing, the
  character store, and the theme/FX layer.
- **StarNet adds what neither other product owns**: its own agent loop, providers, tools/MCP,
  channels, routines, spend ledgers, consent/E-STOP and a desktop shell. Its visual station is a
  projection of that harness, not a view of someone else's sessions.
- **Shared between Habblaud and Qubit Office**: the canvas renderer, the SSE protocol
  (`snapshot` + `feed`), the room/agent model, character editing, timeline/stats views (adapted to
  Qubit data), and the demo toggle. StarNet has **no direct code relationship** with either; its only lineage link is that both its
  harness and Qubit's runtime are partly derived from Hermes.
- **`?shell=1`** still mounts the older Qubit vector shell. It is a comparison/fallback surface, not
  a second product.

---

## 7. Where the code lives

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

StarNet is **not vendored** in this repository. Its paths below are inside the external repo:

| StarNet concern | Path (in `androoAGI/starnet`) |
|---|---|
| Agent runtime / server | `sidecar/loop.js`, `sidecar/index.js` |
| Providers / tools / MCP | `sidecar/providers/`, `sidecar/tools/`, `sidecar/mcp/` |
| Channels / cron / spend | `sidecar/channels/`, `sidecar/cron*.js`, `sidecar/cost.js` |
| Station UI / COMMS / voice | `frontend/app/world.js`, `frontend/app/chat.js`, `frontend/app/voice.js` |
| Desktop shell | `src-tauri/` |
| Voice design | `docs/VOICE_CHAT_PLAN.md` |

## 8. How to check this document

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

# StarNet (external, not vendored): pin, version, licence and voice plan
git clone --depth 1 https://github.com/androoAGI/starnet "$STARNET_SRC"
git -C "$STARNET_SRC" ls-remote --symref origin HEAD | head -1
git -C "$STARNET_SRC" log -1 --format='%H %cI %s'
sha256sum "$STARNET_SRC/LICENSE" "$STARNET_SRC/NOTICE.md"
python3 -c "import json;print(json.load(open('$STARNET_SRC/package.json'))['version'])"
wc -l "$STARNET_SRC/docs/VOICE_CHAT_PLAN.md"

# The deployment contract (served truth, not the filesystem)
.venv/bin/python -m pytest -q tests/test_office_deployment_contract.py -m contract
```

## 9. Verification status

Run against the current tree while writing this document:

- `python3 scripts/vendor_habblaud.py --check` → `ok: 87 files, 3046780 bytes, commit 4ccce2537472`.
- `.venv/bin/python -m pytest -q tests/test_office_deployment_contract.py -m contract` against the
  live dashboard → **5 passed, 2 deselected** (the served-truth HTTP/route checks).
- `.venv/bin/python scripts/verify_doc_citations.py` → `OK — every citation resolves`.
- The cited line numbers and the vendored/tree counts were re-derived with the commands in §8.
- `/qubit-office` and `/api/health` answered HTTP 200 on `127.0.0.1:8890`.

StarNet (external repository, shallow clone on 2026-11-26):

- `git ls-remote --symref origin HEAD` → default branch `feat/harness-backend`, commit
  `e316231d70610aac7b181c4efd1a10b3fc833cdc` (2026-11-07), version `0.15.0`.
- `LICENSE` is MIT (Copyright (c) 2026 Andrew Sims), sha256
  `576bf9e6651870a13ef8b0067ccbd85e4781999b6da32c767891997cf65d46b7`;
  `NOTICE.md` sha256 `347ffb1ccd1489a4bed8659c6cc62b629ea0f3fc275162baba0538e6a3359c6c`.
- `docs/VOICE_CHAT_PLAN.md` is 91 lines; a repo-wide search found no `habblaud` or `qubit`
  references.
- Qubit's earlier read-only study, `docs/STARNET_VOICE_STUDY.md` (2026-10-17), read a local
  `starnet-live` checkout whose HEAD is `54899e5ff3f2b002b457757916b3977818db6fc1` (2026-10-09);
  the upstream HEAD verified here is newer (`e316231d…`). That study did not record a commit, and
  its voice details were not re-run.

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
- Habblaud's own test suite and its mod/Codex/Docker paths were not exercised.
- StarNet's own test suite, desktop builds, Tauri shell and live harness were not run; only its
  repository facts (pin, licence, version, tree shape) were verified.
