# HUD tabs — what renders, and what has no truthful data

Status of the Qubit HUD (`frontend/qubit-app/plugins/qubit-hud/`, served at
`http://127.0.0.1:8890/qubit-hud`) against upstream `hermes-hudui`'s tab set.

Every row below was established by probing the **live** server at
`127.0.0.1:8890` and by driving the live page in headless Chrome — not by
reading source. A route returning `200` is not evidence a panel renders;
each shipped tab was clicked and its rendered text read.

## Probe method

- Data routes: `curl -H "X-Qubit-Session-Token: <token>" /agent/api/<route>`,
  token read from `window.__QUBIT_SESSION_TOKEN__` on `/qubit-workbench`.
- Rendering: Playwright (`executable_path=/usr/bin/google-chrome`,
  `--no-sandbox`), open `/qubit-hud`, click each tab by label, dump the panel's
  `innerText`. Zero console errors on the load.
- Plugin bundle is served at `/dashboard-plugins/qubit-hud/qubit-hud.js`
  (156,950 bytes, byte-identical to the worktree file). `/qubit-hud/<file>`
  answers with the SPA fallback (`index.html`, 3,191 bytes) — that is the SPA
  catch-all, not the asset.

## The 19 tabs — all shipped, all on real Qubit data

| Tab | Route | Status | Shipped | Evidence / source authority |
|---|---|---|---|---|
| Health | `/agent/api/hud/health` | 200, real | yes | 10,206 b body; live vitals |
| Sessions | `/agent/api/hud/sessions` | 200, real | yes | 12,012 b; session authority |
| Cron | `/agent/api/hud/cron` | 200, real | yes | 15,757 b; cron store |
| Models | `/agent/api/model/options` + `/model/info` | 200, real | yes | `/api/models` does not exist — see gap 1 |
| Skills | `/agent/api/hud/skills` | 200, real | yes | 167,450 b; skill resolution |
| Plugins | `/agent/api/hud/plugins` | 200, real | yes | 70,668 b; plugin loader |
| Costs | `/agent/api/hud/costs` | 200, real | yes | 17,582 b; analytics |
| Sudo | `/agent/api/hud/sudo` | 200, real | yes | "0 pending, 882 settled record(s)" |
| Replay | `/agent/api/hud/replay` | 200, real | yes | "50 replayable run(s) of 1,070" |
| Corrections | `/agent/api/hud/corrections` | 200, real (0 rows) | yes | store present, 0 corrections — says so on screen |
| Patterns | `/agent/api/hud/patterns` | 200, real | yes | analysed 300 recent sessions |
| Verify | `/agent/api/hud/verify` | 200, real (0 rows) | yes | no checkpoint recorded — says how to make one |
| **Providers** | `/agent/api/hud/providers` | 200, real | **yes** | 6 providers from `voicebridge.credentials.inventory` |
| **Projects** | `/agent/api/hud/projects` | 200, real | **yes** | 8 projects, 7 git repos, live branch/dirty/commit state |
| **Profiles** | `/agent/api/hud/profiles` | 200, real | **yes** | 1 profile island, 1,070 sessions, 290 skills |
| **Memory** | `/agent/api/hud/memory` | 200, real (0 rows) | **yes** | SQLite store readable, 0 active, 1 tombstoned |
| **Gateway** | `/agent/api/hud/gateway` | 200, real | **yes** | 14,070 b; supervisor + 3/3 platforms connected |
| Live Chat | `/agent/api/hud/live-chat` | 200, real (partial) | yes | no chat WS (`WS_SINGLE_PATH`); renders the refusal + real run tails |
| Live Models | `/agent/api/hud/live-models` | 200, real (partial) | yes | 14 of 40 models resolved; **no probe issued**, says so |

## The five candidate routes from the earlier analysis

The earlier analysis proposed `/api/profiles`, `/api/memory`, `/api/providers`,
`/api/gateway`, `/api/projects` as the truth sources. Probed directly:

| Route | Status | Shipped as source? | Why |
|---|---|---|---|
| `/agent/api/profiles` | 200, 487 b | no — `hud/profiles` used instead | Compat route exposes a thin identity view; the HUD collector adds session/skill counts from `QubitSessionStore` and labels what it cannot probe |
| `/agent/api/memory` | 200, 3,272 b | no — `hud/memory` used instead | Compat route is the **provider registry** (openviking/byterover…), not the active-memory store. Those are different things; feeding one to a "Memory" panel would mislabel it |
| `/agent/api/providers` | **404** | no | `{"ok": false, "code": "NOT_FOUND", "detail": "no compat route for GET /api/providers"}` — the panel reads `voicebridge.credentials.inventory` directly. **Gap 1** |
| `/agent/api/gateway` | 200, 3,005 b | no — `hud/gateway` used instead | Real `process` block, but `gateway.gateways` is an **empty `[]`** — a panel driven off that array would show zero gateways while one runs. The collector reads the supervisor's live answer, which also carries the platforms and control socket (**Gap 2**) |
| `/agent/api/projects` | **404** | no | `{"ok": false, "code": "NOT_FOUND", "detail": "no compat route for GET /api/projects"}` — the panel reads the `qubit-projects/1` registry. **Gap 3** |

## Gaps — recorded, not papered over

1. **`GET /api/providers` does not exist** (404). No compat handler is
   registered in this tree. The Providers panel therefore reads the credential
   inventory in-process. No panel was built *over the 404*; the 404 is stated
   in the panel footer area only as a provenance note.

2. **`GET /api/gateway` carries an empty `gateways: []`.** Its `process` block
   is real (live PID/port/restart count), but a panel driven off the `gateways`
   array would render zero gateways while one is running — a fabricated zero.
   The Gateway panel reads `voicebridge.gateway_supervisor` instead, which also
   supplies the 3 connected platforms and the control-socket answer.

3. **`GET /api/projects` does not exist** (404). The Projects panel reads the
   `qubit-projects/1` registry plus real on-disk git state. Note the registry
   honestly reports 1 project (`Qubit Voicebridge`) whose registered folder is
   **missing from disk** — the panel renders `MISSING`, it does not hide the row
   or count it as present.

4. **`GET /api/models` does not exist** (404). The Models tab reads
   `/api/model/options` and `/api/model/info`, and says so on screen.

5. **No live provider probe exists.** `/api/providers/validate` is a recorded
   refusal (`_NO_NETWORK_PROBE`). The panel's "configured" therefore never
   claims "authenticated". This is deliberate: an invented green tick is worse
   than an absent one.

6. **Upstream tabs with no Qubit counterpart at all** were not built. Upstream's
   set includes panels over a `MEMORY.md`/`USER.md` markdown memory, a
   character-budget meter, keyword-categorised memory, and a per-step tool
   trace in Replay/Patterns. Qubit has none of those artefacts:
   - memory is a SQLite store, not markdown — no file to size against a budget;
   - the session authority publishes role/content/timestamp only, so Replay has
     no per-step tool trace and Patterns has no tool-sequence graph.
   Each of these is stated **on the panel that could otherwise imply it**, so a
   reader is never shown an empty chart as though it were a real zero.

## Verified rendering (live, headless Chrome)

Panel text captured after clicking each tab — abridged:

- **Providers** — `6` providers / `5` configured; rows `copilot · GITHUB_TOKEN ·
  api_key · env:GITHUB_TOKEN · None`, `openai-api · … · exhausted`, …,
  `nous · (no credential entry)`; footnote "no value is read".
- **Projects** — `8` projects / `7` git repos / `7` dirty / `1` missing folder;
  rows carry real branch (`main`, `canary`, `master`), real commit subjects and
  real counts (`78`, `5,609`, `14,803`).
- **Profiles** — `active: default`; `default · deepseek-v4.1-flash ·
  opencode-go · 1,070 · 290 · PRESENT`.
- **Memory** — `STATE OK / ENABLED no / E-STOP clear`; `ACTIVE 0 / STAGED 0 /
  TOMBSTONED 1`; "No active memory yet" with the store path's own detail.
- **Gateway** — `qubit-gateway.service · OK · running · PID 3,960,399 ·
  port 8,890 · restarts 0`; `3 of 3 connected` (discord, telegram, whatsapp).

No console errors; no `coming soon`, placeholder, lorem or hardcoded-stat
literal anywhere in the bundle (grep for `coming soon|TODO|placeholder|dummy|
mock` finds only two `placeholder:` input properties, which are search-box
hints, not data).
