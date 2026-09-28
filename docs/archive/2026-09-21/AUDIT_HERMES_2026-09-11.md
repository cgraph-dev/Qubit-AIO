# Hermes Parity Gap Audit — qubit-voicebridge

Read-only audit. No repo file was modified. Pinned upstream:
`/tmp/opencode/pinned/hermes-agent` @ `2237be355906fbe6065ce1815711eee52b2d646e`
(verified in `frontend/qubit-app/README.md`, `docs/HERMES_SOURCE_COMPARISON.json`).

Qubit side audited:
- `voicebridge/hermes_compat.py` — `ROUTES` (168) + `GATED` (34 patterns)
- `voicebridge/webui.py` — `/hermes/api/*` dispatch, `_hermes_api()` webui.py:1028; alias table `HERMES_ROUTE_ALIASES` webui.py:684
- `voicebridge/cli.py` — `build_parser()` cli.py:33
- `frontend/qubit-dist` — served SPA; `frontend/qubit-app/plugins/*` — overlay tabs

Method: ripgrep + argparse-tree introspection of both CLIs + a regex matcher that
replays `hermes_compat.match()` (`re.fullmatch` over `ROUTES` in table order,
exact HTTP method). Upstream paths are sampled by substituting `{p}`→`aa`
and `{p:path}`→`aa`; the 2 multi-segment `:path` routes are noted separately.

---

## 0. Headline counts

| Surface | Total | Mapped | Unmapped |
|---|---:|---:|---:|
| Hermes web API routes (routers) | **275** | **164** | **111** |
| — of which implemented (not gated) | 275 | 121 | — |
| — of which typed-gated | 275 | 43 | — |
| — unmapped: absent entirely | — | — | **93** |
| — unmapped: path exists, wrong HTTP verb | — | — | **18** |
| Hermes CLI top-level commands | **72** | 35 mapped | 21 none / 15 partial / 1 oos |
| Hermes CLI leaf entries (cmd + subcmd) | 470 | n/a | n/a |
| Qubit CLI top-level commands | 69 | — | — |
| Qubit CLI leaf entries | 497 | — | — |
| Hermes dashboard SPA page routes | **20** | 20 reachable as URLs | 0 |
| — pages with ≥1 broken backing workflow | — | 15 fully wired | 5 broken |
| Qubit overlay plugin tabs | 10 | — | — |

Route total composition: `web_routers/*.py` = 262, `memory_oauth.py` = 2,
`dashboard_auth/routes.py` = 11 → **275**. Excluding the 11 `dashboard_auth`
(OAuth/portal) routes: **264 total / 164 mapped / 100 unmapped**.
SPA catch-alls `/{full_path:path}` and `/assets/{filename}.css`
(`web_server_dashboard.py:112,177,206`) are **excluded** (not API).
Plugin-provided `/api/plugin/*` routes (mounted by `_mount_plugin_api_routes`,
web_server.py:970) are **excluded** (out of scope).

Qubit `ROUTES` = 168: **134 implemented**, **34 gated**. Gated typed codes:
`OAUTH_MANUAL_HANDOFF`(5), `BLOCKED_REMOTE_SOURCE`(4), `PTY_NO_SHELL`(3),
`SECRET_NEVER_SERVED`(3), `GATEWAY_NO_SUPERVISION`(3), `UPDATE_OPERATOR_OWNED`(3),
`ONBOARDING_GUIDED`(3), `WS_SINGLE_PATH`(2), `WRITE_BROKERED`(2),
`CURATOR_ON_DEMAND`(2), `CHAT_IMAGE_OVERLAY`(1), `BACKUP_PASSPHRASE`(1),
`PLUGIN_PROVIDERS_QUBIT`(1), `BLUEPRINT_STAGED`(1).

---

## 1. UNMAPPED web API routes (111)

Legend for classification:
- **(a) genuinely missing** — no Qubit handler/gate at all; upstream behavior absent.
- **(b) gated/refused with typed code** — intentional policy boundary; either the
  verb is wrong (so it 404s instead of returning the typed envelope) or the
  boundary needs an explicit gate row.
- **(c) out-of-scope** — billing/cloud/portal/OAuth token dance/desktop-only.

### 1A. Absent entirely — 93 routes

#### Audio / voice (7) — `web_routers/audio.py`
| Method | Path | upstream | class | what's missing | smallest Qubit fix |
|---|---|---|---|---|---|
| POST | `/api/audio/transcribe` | audio.py:77 | (a) | ASR upload → text | `hermes_compat.py` handler calling `voicebridge.asr`; add to `ROUTES` |
| GET | `/api/audio/voice-config` | audio.py:140 | (a) | active profile STT/TTS config for client voice | map to `voicebridge/profiles.py` + audio settings |
| GET | `/api/audio/elevenlabs/voices` | audio.py:174 | (c) | external ElevenLabs catalog | typed gate `PROVIDER_EXTERNAL` |
| POST | `/api/audio/speak` | audio.py:250 | (a) | synthesize → base64 data URL | map to `voicebridge/audio` TTS |
| POST | `/api/audio/tts-lease` | audio.py:307 | (c) | desktop warm-up/release lease | desktop-only gate |
| WEBSOCKET | `/api/audio/speak-stream` | audio.py:339 | (b) | streaming PCM TTS | WS not served (see §1B) |

#### Chat/console WebSockets (1)
| WEBSOCKET | `/api/pub` | chat_ws.py:575 | (b) | broadcast pub socket | no WS dialect; `WS_SINGLE_PATH` |

#### Config / providers / env (8)
| GET | `/api/egress/status` | config_env.py:102 | (a) | egress proxy status | map to `voicebridge/proxy_status.py` |
| GET/POST | `/api/providers/custom-endpoints` | config_env.py:519/532 | (a) | OpenAI-compatible endpoint CRUD | map to `voicebridge/config.py` providers |
| POST | `/api/providers/custom-endpoints/{endpoint_id}/activate` | config_env.py:546 | (a) | set endpoint default | map to `voicebridge/providers` |
| DELETE | `/api/providers/custom-endpoints/{endpoint_id}` | config_env.py:586 | (a) | remove endpoint | map to providers |
| POST | `/api/providers/custom-endpoints/validate` | config_env.py:610 | (b) | live-probe endpoint | typed gate (network probe) |
| POST | `/api/providers/validate` | config_env.py:638 | (b) | live-probe credential | typed gate (network probe) |

#### Cron (2)
| GET | `/api/cron/jobs/{job_id}/runs` | cron.py:221 | (a) | run history | map to `voicebridge/schedules.py` `history` |
| POST | `/api/cron/fire` | cron.py:271 | (c) | NAS→agent managed-cron webhook | external trigger, out of scope |

#### Dashboard plugins (1)
| GET | `/dashboard-plugins/{plugin_name}/{file_path:path}` | dashboard_ui.py:281 | (a) | plugin static assets | **actually covered** at webui.py:1765 (`_serve_hermes_plugin`), not via `ROUTES`. Not a real gap. |

#### Files / FS (12) — `web_routers/files.py`
| GET | `/api/media` | files.py:241 | (a) | gateway-local image as data URL | map to `voicebridge/files.py` read |
| GET | `/api/files/download` | files.py:443 | (a) | attachment download | map to files (scoped roots) |
| GET | `/api/files/stream` | files.py:463 | (a) | Range-stream audio/video | map to files |
| POST | `/api/files/upload` | files.py:491 | (b) | managed file upload | gate `WRITE_BROKERED` |
| GET | `/api/fs/list` | files.py:607 | (a) | directory listing | map to `voicebridge/files.py` (project roots) |
| GET | `/api/fs/read-text` | files.py:630 | (a) | in-app editor read | map to files |
| POST | `/api/fs/write-text` | files.py:647 | (b) | in-app editor write | gate `WRITE_BROKERED` |
| GET | `/api/fs/read-data-url` | files.py:707 | (a) | file as data URL | map to files |
| GET | `/api/fs/download` | files.py:721 | (a) | fs download | map to files |
| GET | `/api/fs/git-root` | files.py:736 | (a) | git root discovery | map to `voicebridge/git_scope.py` |
| GET | `/api/fs/default-cwd` | files.py:747 | (a) | default cwd | map to files |

#### Git review / worktree (21) — `web_routers/git.py`
Reads: `/api/git/status` (48), `/api/git/gh-auth` (78), `/api/git/worktrees` (90),
`/api/git/branches` (95), `/api/git/base-branches` (100), `/api/git/review/list` (105),
`/api/git/review/diff` (110), `/api/git/file-diff` (117),
`/api/git/review/commit-context` (122), `/api/git/review/rev-parse` (127),
`/api/git/review/ship-info` (132).
Writes: `/api/git/review/pr-list` (137), `/stage` (142), `/unstage` (147),
`/revert` (152), `/commit` (157), `/push` (162), `/create-pr` (167),
`/api/git/worktree/add` (172), `/worktree/remove` (182), `/branch/switch` (189).
**class (a)/(b)**: Qubit CLI has scoped read-only `qubit git status|log|diff|show`
(`cli.py:1640`) but exposes **no** compat route; writes are intentionally brokered.
**fix**: add `hermes_compat` read handlers backed by `voicebridge/git_scope.py`
for the 11 read routes; add `GATED` rows for all writes with a `GIT_WRITE_BROKERED`
typed code pointing at `qubit opencode` / `qubit project`.

#### Local models (16) — `web_routers/local_models.py`
`status`(456), `hardware`(503), `catalog`(580), `runtime/install`(647),
`download`(684), `models/{model_id}` DELETE(698), `quickstart`(730), `server`(797),
`eject`(810), `activate`(828), `jobs`(851), `jobs/{job_id}`(859), `search`(869),
`search/files`(878), `download-browsed`(887), `sideload`(914).
**class**: reads (a), installs/downloads (b). Qubit has `voicebridge/local_llm.py`,
`runtime`/`frontier`/`product model` surfaces.
**fix**: map status/hardware/catalog/jobs to `local_llm`+`runtime_inventory`;
gate runtime/install, download, sideload, server, eject, activate with a
`LOCAL_MODEL_BROKERED` typed code.

#### MCP OAuth callback (1)
| GET | `/api/mcp/oauth/callback/{server_name:path}` | mcp.py:273 | (c) | OAuth redirect | out of scope; add gate row matching `POST`/`GET` `.*/oauth/.*` |

#### Model (1)
| GET | `/api/model/recommended-default` | models.py:160 | (a) | recommended default after auth | map to `voicebridge/providers` catalog in `h_model_info` |

#### Profiles (6) — `web_routers/profiles.py`
`/api/profiles/sessions/sidebar` (415), `/api/profiles/projects/tree` (559),
`/api/profiles/sessions/pull-requests` POST (608), `/api/profiles/{name}/open-terminal` POST (755),
`/api/profiles/{name}/export` POST (906), `/api/profiles/{name}/desktop-overlay` GET (952).
**class**: sidebar/tree reads (a); open-terminal/export (b); desktop-overlay (c).
**fix**: map `projects/tree` + `sessions/sidebar` to `voicebridge/profiles.py`/
`projects`; gate `open-terminal` (`PTY_NO_SHELL`) and `export` (`EXPORT_BROKERED`).

#### Status (4) — `web_routers/status.py`
`/api/ssh/ownership` GET (100), `/api/health` GET (110),
`/api/learning/graph` GET (567), `/api/learning/node` GET/PUT/DELETE (592/599/607).
**class**: `health`/`ssh` (a, trivial reads → `h_status`); `learning/*` (a) but Qubit
has `LearningLedger` behind `qubit lessons`.
**fix**: `h_health`→`h_status`; learning graph/node read+mutate via
`voicebridge/learning.py` (edits gated as staged).

#### Tools (4) — `web_routers/tools.py`
`/api/tools/terminal/backends` GET (617), `/api/tools/terminal/backend` PUT (644),
`/api/tools/computer-use/status` GET (666),
`/api/tools/computer-use/permissions/grant` POST (675).
**class**: reads (a); backend select + permission grant (b).
**fix**: map status/backends to `voicebridge/capabilities.py`/`computer_use.py`;
gate mutations.

#### Dashboard auth / OAuth / portal (10 in this bucket + 1 portal) — `dashboard_auth/routes.py`, `status.py:618`
`/login`(173), `/api/auth/providers`(180), `/auth/login`(194),
`/auth/native/authorize`(242), `/auth/callback`(277),
`/auth/password-login`(359), `/auth/logout`(408), `/auth/native/token`(463),
`/auth/native/refresh`(483). Plus `GET /api/portal` (status.py:618).
**class (c)**: portal/cloud/OAuth token dance. Qubit serves `h_auth_me`
(loopback identity) and refuses the rest; `/api/auth/providers` could return
`[]` for wire-shape safety (smallest fix: `ROUTES` row → `h_auth_me`-style
empty provider list).

### 1B. Path exists but wrong HTTP verb — 18 routes

These are the most insidious: the browser sends the upstream verb, Qubit has
no matching row, so it 404s **instead of** the intended typed refusal. The gate
exists but is bypassed.

| Upstream | file:line | Qubit has | class | smallest fix |
|---|---|---|---|---|
| `PUT /api/config/raw` | analytics.py:50 | `GET` gated `SECRET_NEVER_SERVED` | (b) | add `("PUT", r"/api/config/raw", _NO_SECRET)` |
| `WEBSOCKET /api/console` | chat_ws.py:248 | `GET` gated `PTY_NO_SHELL` | (b) | webui WS upgrade path returns typed close (see §1D) |
| `WEBSOCKET /api/pty` | chat_ws.py:404 | `GET`/`POST` gated `PTY_NO_SHELL` | (b) | same |
| `WEBSOCKET /api/ws` | chat_ws.py:541 | `GET` gated `WS_SINGLE_PATH` | (b) | same |
| `WEBSOCKET /api/events` | chat_ws.py:587 | `GET` implemented `h_events` | (b) | `GET /api/events` returns JSON but upstream is WS-only; add SSE/WS contract note |
| `POST /api/env/reveal` | config_env.py:716 | `GET` gated `SECRET_NEVER_SERVED` | (b) | add `POST` to `_NO_SECRET` row |
| `PUT /api/mcp/servers` | mcp.py:117 | `GET`/`POST` implemented | (a) | add `PUT` → inventory update / typed gate |
| `POST /api/mcp/servers/{name}/auth` | mcp.py:194 | `GET` gated `OAUTH_MANUAL_HANDOFF` | (b) | change gate row to `POST` (frontend uses POST) |
| `DELETE /api/mcp/oauth/flows/{flow_id}` | mcp.py:259 | `GET` gated `OAUTH_MANUAL_HANDOFF` | (b) | add DELETE gate row |
| `PUT /api/mcp/servers/{name}/enabled` | mcp.py:305 | `POST` implemented `mcp_enabled` | (b) | add `PUT` row → `MCP_NO_MOUNT` 501 (frontend uses PUT) |
| `POST /api/credentials/pool` | ops.py:317 | `GET` gated `SECRET_NEVER_SERVED` | (b) | add `POST` gate row |
| `DELETE /api/credentials/pool/{provider}/{index}` | ops.py:375 | `GET` gated | (b) | add `DELETE` gate row |
| `GET /api/profiles/sessions` | profiles.py:362 | `PATCH`/`DELETE` implemented | (a) | map to `voicebridge/profiles.py` session list |
| `POST /api/profiles` | profiles.py:645 | `GET` implemented `h_profiles` | (a) | add profile-create via `profiles.py` (staged) |
| `POST /api/profiles/import` | profiles.py:924 | `PATCH`/`DELETE` implemented | (b) | gate `PROFILE_IMPORT_REVIEW` |
| `POST /api/sessions/owner-backfill` | sessions.py:579 | `GET`/`DELETE`/`PATCH` implemented | (b) | Qubit receipts are append-only → `SESSIONS_RECEIPT_STORE` 501 |
| `GET /api/tools/toolsets/{name}/models` | tools.py:404 | `PUT` implemented `toolset_write` | (a) | add GET read via `h_toolsets` |
| `POST /api/memory/providers/{provider}/oauth/start` | memory_oauth.py:50 | `GET` implemented `mem_prov` | (c) | gate OAuth provider connect |

### 1C. Frontend-visible breakage (highest-confidence)
Endpoints the **vendored SPA** actually calls (verified in
`frontend/…/web/src/lib/api.ts`) that land on unmapped, wrong-verb, or
hard-gated routes:

| API used by SPA | method (api.ts) | Qubit result | page |
|---|---|---|---|
| `/api/config/raw` | PUT (:582) | 404 (GET only, gated) | ConfigPage raw YAML editor |
| `/api/env/reveal` | POST (:601) | 404 (GET only, gated) | EnvPage reveal |
| `/api/credentials/pool` | POST (:1160) / DELETE (:1169) | 404 (GET only, gated) | Env/Config credential pool |
| `/api/mcp/servers/{name}/enabled` | PUT (:1072) | 404 (Qubit POST only) | McpPage toggle |
| `/api/mcp/servers/{name}/auth` | POST (:1054) | 404 (Qubit GET only) | McpPage OAuth connect |
| `/api/profiles` | POST (:685) | 404 (Qubit GET only) | ProfilesPage create |
| `/api/profiles/import` | POST | 404 | ProfilesPage import |
| `/api/profiles/sessions`, `/sidebar`, `/projects/tree` | GET | 404 | ProfilesPage sidebar |
| `/api/pty`, `/api/ws`, `/api/console`, `/api/events` | WS | no upgrade (gated for HTTP verbs only) | ChatPage, HermesConsoleModal, ChatSidebar |
| `/api/config/raw` | GET (:579) | 403 `SECRET_NEVER_SERVED` (intended) | ConfigPage raw load |

### 1D. WebSocket contract (cross-cutting)
`webui.py` `do_GET/POST/PUT/PATCH/DELETE` all funnel `/hermes/api/*` to
`hermes_compat.dispatch()`, which only understands HTTP verbs. There is **no
WS upgrade branch**, so upstream's 5 WebSocket endpoints
(`/api/pty`, `/api/ws`, `/api/console`, `/api/events`, `/api/pub`,
`/api/audio/speak-stream`) can never be answered — not even with a typed
refusal. The built-in Chat tab therefore cannot attach, and `ChatSidebar`'s
event subscriber cannot connect. Qubit's overlay `qubit-chat` tab
(`POST /api/qubit/chat`) is the intended replacement, so this is a **(b)
intentional boundary with a safe equivalent**, but the mechanics currently
fail as a bare connection error rather than the typed `PTY_NO_SHELL` /
`WS_SINGLE_PATH` envelopes the gate table advertises.
**Smallest fix**: in `webui.py` `do_GET`, detect `Upgrade: websocket` on the
`/hermes/api/*` paths and complete a handshake then send the typed close
frame (or return HTTP 426 with the typed JSON), routed from the existing
`GATED` codes — one handler, no new WS dialect.

---

## 2. CLI parity

Introspected with the real `build_parser()` trees (isolated venv; no repo
writes): Hermes 470 nodes, Qubit 497 nodes.

### 2A. Hermes top-level commands with no Qubit equivalent
**Firm gaps (21, no Qubit family):**
`bundles, claw, config, console, debug, desktop, dump, egress, gui, insights,
journey, logs, memory-graph, moa, pause, pets, plugins, prompt-size, resume,
tools, worktree`

**Partial (15, related family exists but not the Hermes surface):**
`backup→export-state/setup backup, chat→run/task/voice, curator→lessons,
dashboard→webui, doctor→setup diagnose, fallback→frontier, model→profile
select, monitoring→monitor, secrets→brain vault, security→setup audit,
send→msg send, serve→webui/gateway run, slack→msg, uninstall→package
uninstall/product, update→product upgrade`

### 2B. Clearly mapped top-level families (35)
`acp, approvals, auth, browser, checkpoints, completion, computer-use,
cron→schedules, gateway, hooks, import→import-state, import-agent, kanban,
learning→lessons, login→auth login, logout→auth logout, lsp, mcp, memory,
migrate→product migrate, pairing, peer, profile, project, proxy, sessions,
setup, skills, skin→theme, status, sync, verify/verify-release, webhook,
whatsapp→msg, whatsapp-cloud→msg`.
`portal` is out-of-scope (cloud/portal).

### 2C. Subcommand-surface gaps inside mapped families (highest impact)
| Hermes family | Hermes subcmds | Qubit counterpart | missing |
|---|---|---|---|
| `sessions` | 19 (list, export, delete, prune, archive, optimize, repair, recover, stats, rename, pin …) | `qubit sessions` **1** (`list`) | 18 subcommands; Qubit receipts are append-only by design (gated) |
| `mcp` | 14 (add, remove, test, configure, catalog, install, serve, login, …) | `qubit mcp` **1** (`status`) | 13; Qubit inventories, never mounts (intentional) |
| `config` | 9 (show/edit/get/set/unset/path/env-path/check/migrate) | none top-level; `flags`/`capability` partial | 9 |
| `plugins` | 16 (install, update, remove, enable, disable, capabilities, doctor, pack …) | none top-level | 16 |
| `cron` | 17 | `qubit schedules` 8 | ~9 (incidents, history, doctor, tick …) |
| `skills` | 21 | `qubit skills` 8 + `verified-skills` | remote tap/publish/audit (intentionally remote-gated) |
| `model` | no subparsers (picker) | `profile select`, `frontier` | direct model-switch command |
| `tools` | 4 (list/disable/enable/post-setup) | none top-level | 4 (toolsets) |
| `worktree` | 4 | none (git read-only + opencode worktrees) | 4 |

These are largely the same policy boundaries as the route gates (remote hub,
raw PTY, secrets, gateway supervision) — cross-reference
`voicebridge/voice_commands.py` `VOICE_IMPOSSIBLE` (23) / `VOICE_GUARDS` (14)
and `CLI_COVERAGE` (69).

---

## 3. Dashboard pages

Pinned `web/src/App.tsx` builtin routes (20):
`/`→`/sessions`, `/chat`, `/sessions`, `/files`, `/analytics`, `/models`,
`/logs`, `/cron`, `/skills`, `/plugins`, `/mcp`, `/pairing`, `/channels`,
`/webhooks`, `/system`, `/profiles`, `/profiles/new`, `/config`, `/env`,
`/docs` (App.tsx:154-176, 451-455). Plugin manifests may add tab routes.

All 20 are reachable as **URLs** because Qubit serves the byte-identical dist
at `/hermes/` with SPA fallback (`webui.py:_serve_hermes_static`), and
`HERMES_ROUTE_ALIASES` (webui.py:684) maps 18 normal Qubit paths onto them.
`/profiles/new` has no normal alias (SPA-internal nav only); overlay tabs
`/qubit-chat`, `/qubit-boards`, `/qubit-runtime`, `/qubit-gateway`,
`/qubit-brand` likewise have no normal alias (reachable only inside the SPA).

**Pages/workflows with no fully-reachable Qubit workflow** (5 pages, plus the
shared console/sidebar components, all tied to the route gaps in §1C):

| Page | broken workflow | root gap |
|---|---|---|
| ConfigPage | Raw YAML view/save | `/api/config/raw` GET gated + PUT 404 |
| EnvPage | Reveal secret, credential pool | `/api/env/reveal` POST 404; `/api/credentials/pool` POST/DELETE 404 |
| McpPage | OAuth connect, enable/disable toggle | POST auth 404; PUT enabled 404 |
| ProfilesPage | Create/import/session sidebar/export | POST `/api/profiles`, `/import`, `/sessions*` 404 |
| ChatPage | PTY terminal (built-in) | WS `/api/pty` unreachable — replaced by overlay Qubit Chat |
| HermesConsoleModal / ChatSidebar | Console WS, event subscriber WS | `/api/console`, `/api/events` WS unreachable |

Everything else (Sessions, Files list, Analytics, Models, Logs, Cron, Skills,
Plugins, Pairing, Channels, Webhooks, System, Docs, Operations, Boards, Runtime,
Gateway, Reports, Memory, Devices, Approvals) loads its backing routes
(mapped or typed-gated).

---

## 4. Classification summary

- **(a) genuinely missing behavior**: the bulk of the 93 absent routes, led by
  Git (21), Local models (16), Files/FS (12), Audio (6),
  providers/custom-endpoints (4), Profiles (5), Learning (4), Tools (4), plus
  small readers (health, cron runs, egress, model recommended, ssh ownership).
- **(b) intentionally gated / refused with a typed code, but reachable only
  for the *wrong* verb or not at all**: all 18 wrong-verb rows, the 5 WS
  endpoints, and the write routes among the absent set (file write/upload,
  local-model install/download, profile import/export/open-terminal,
  computer-use grant, provider probes). The gate policy exists; the route/verb
  coverage is incomplete.
- **(c) out-of-scope** (billing/cloud/portal/OAuth/desktop): `/api/portal`,
  the 11 `dashboard_auth` routes, `/api/providers/oauth*` (already gated),
  `/api/mcp/oauth/*`, `/api/cron/fire`, elevenlabs voices, tts-lease,
  desktop-overlay.

---

## 5. Top 15 highest-impact gaps (ranked)

1. **No WebSocket upgrade path** (`webui.py` `/hermes/api/*`): `/api/pty`,
   `/api/ws`, `/api/console`, `/api/events`, `/api/pub`,
   `/api/audio/speak-stream` (chat_ws.py:248-587, audio.py:339) — built-in Chat
   and console are dead; gates advertised but not delivered. Fix: typed-close
   WS branch in `webui.py do_GET`.
2. **`PUT /api/config/raw` unmapped** (analytics.py:50) — SPA ConfigPage save
   hard-404s; only GET is gated. Fix: add PUT to `_NO_SECRET` gate.
3. **`POST /api/env/reveal` wrong verb** (config_env.py:716) — EnvPage reveal
   404s instead of typed refusal. Fix: add POST gate.
4. **`POST`/`DELETE /api/credentials/pool`** (ops.py:317/375) — credential-pool
   UI 404s. Fix: add verb gate rows.
5. **MCP page verbs** — `POST /api/mcp/servers/{name}/auth` (mcp.py:194) and
   `PUT /api/mcp/servers/{name}/enabled` (mcp.py:305) 404 on the SPA's verbs.
   Fix: align gate/method rows.
6. **Profile CRUD/import/sidebar** — `POST /api/profiles` (profiles.py:645),
   `POST /import` (924), `GET /sessions|/sidebar|/projects/tree` (362/415/559),
   `POST /export` (906). Fix: handlers in `hermes_compat` over
   `voicebridge/profiles.py`; stage writes.
7. **Git review surface absent (21 routes)** (git.py:48-189) — no read routes
   despite `qubit git`. Fix: 11 read handlers via `git_scope.py`, gate writes.
8. **Local-models surface absent (16 routes)** (local_models.py:456-914) — no
   route for `local_llm`/runtime inventory. Fix: map reads, gate installs.
9. **Files/FS surface absent (12 routes)** (files.py:241-747) — only list/read
   exist in CLI. Fix: read handlers + `WRITE_BROKERED` gates.
10. **Audio/voice routes absent (6 HTTP + 1 WS)** (audio.py:77-339) — Qubit has
    ASR/TTS but exposes no compat route. Fix: map transcribe/speak/voice-config.
11. **Providers custom-endpoints + live validate absent (6)** (config_env.py:519-638)
    — Fix: read/write via `voicebridge/config.py`; gate network probes.
12. **Learning graph/node absent (4)** (status.py:567-607) — Fix: map to
    `voicebridge/learning.py` over the same `qubit lessons` ledger.
13. **Tools terminal/computer-use absent (4)** (tools.py:617-675) — Fix: map
    reads to `capabilities.py`/`computer_use.py`, gate grants.
14. **`PUT /api/mcp/servers` and `GET /api/tools/toolsets/{name}/models`**
    (mcp.py:117, tools.py:404) — live CRUD/read verbs missing. Fix as above.
15. **Small readers** — `GET /api/health` (status.py:110), `GET /api/cron/jobs/{id}/runs`
    (cron.py:221), `GET /api/egress/status` (config_env.py:102),
    `GET /api/model/recommended-default` (models.py:160),
    `POST /api/gateway/drain` (actions.py:146), `GET /api/ssh/ownership`
    (status.py:100). Fix: thin handlers/gates, ~1 line each.

---

## 6. Method limitations / not-verified

- Route matching replays Qubit's `re.fullmatch` table order and samples
  parameter values; multi-segment `:path` params are exercised with a
  slashless sample. Two upstream routes with `:path`
  (`/dashboard-plugins/...`, `/api/mcp/oauth/callback/...`) do not affect
  counts materially (`/dashboard-plugins` is served at webui.py:1765).
- WebSocket endpoints are compared as routes only; no WS handshake was
  attempted against a live Qubit server.
- CLI mapping is by purpose, not by 1:1 prototype; "partial" rows are marked.
- Qubit's own `docs/PARITY_HERMES.json` (68 CLI rows) and
  `docs/HERMES_SURFACE.json` were **not** used as evidence — every claim here
  comes from reading the pinned source and the Qubit route/CLI tables.
- The Qubit WebUI was not booted; route coverage is static (table-based).
  Numbers are reproducible with `/tmp/opencode/audit/final.py`.
