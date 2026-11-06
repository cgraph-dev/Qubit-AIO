# WORKBENCH_SHIM_MAP — hermes-webui `/api/*` → Qubit delegation map

Status: **contract table authored first (write-only step)**, implementation follows.
Upstream pin: `github.com/nesquena/hermes-webui@3d9488fca6bb248c77f988bfbffcca9d8cfd9561`
(README/auth companion in `voicebridge/workbench/__init__.py::UPSTREAM_PIN`).
Every shape below was read from upstream's own handler in `api/` and, where the
client parses a field, from the consuming JS in `static/`.

Route family: `/api/wb/*` — Qubit's shim table lives in
`voicebridge/workbench_shim_routes.py`, handlers in
`voicebridge/workbench_shim/`. The upstream **paths are not re-used**: Qubit
already serves `/api/sessions*` (receipt/chat shape), `/api/crons` is a real
vendored path family, and `/api/status` is `StatusResponse`. Binding the
upstream shapes to those paths would break the dashboard that exists today. The
ported static UI is pointed at `/api/wb/*` by the porter; the shapes below are
upstream's, byte-for-byte where Qubit has the data.

Legend: **DELEGATE** = Qubit source answers it · **TRANSFORM** = Qubit source
answers it, response re-shaped to upstream's contract · **GAP** = Qubit has no
counterpart, typed `501 NOT_PORTED`.

---

## 1. Sessions — `QubitSessionStore` (authority) + `chat_transport.turn` (send)

| # | Upstream route | Request (verbatim) | Upstream response shape | Qubit source | Disposition |
|---|---|---|---|---|---|
| 1 | `GET /api/wb/sessions` | query: `include_archived=1`, `all_profiles=1` (both optional flags) | `{sessions:[row], sidebar_reference_sessions:[], cli_count:int, archived_count:int, archived_webui_count:int, archived_cli_count:int, include_archived:bool, all_profiles:bool, active_profile:str\|null, other_profile_count:int, server_time:float, server_tz:str}` — `routes.py:3721-3743`. `row` = bounded sidebar field set (`route_session_list_cache.py:47-120`), the keys the JS reads being `session_id,title,display_title,workspace,model,model_provider,message_count,visible_message_count,created_at,updated_at,last_message_at,pinned,archived,is_streaming,is_cli_session,session_source,source_label,read_only` (`static/sessions.js:7685-7720`) | `QubitSessionStore.list(limit,offset,query,include_archived)` → `{state,sessions:[{id,source,model,title,name,archived,started_at,ended_at,last_active,is_active,message_count,tool_call_count,input_tokens,output_tokens,preview,snippet,match_role}],total,limit,offset}` (`qubit_session_store.py:560-686`) | **TRANSFORM** — rename `id→session_id`, `started_at→created_at`, `last_active→updated_at`/`last_message_at`; `is_streaming` from `is_active`; `is_cli_session=false` (all Qubit chat rows are the same store); the profile/cron/webhook-specific counters are honest zeros or derived counts, never invented rows. `active_profile` = `"default"`, `all_profiles` echoed, `cli_count=0` |
| 2 | `GET /api/wb/sessions/{id}` (and upstream `GET /api/session?session_id=`) | path `id`; query `messages=0` skips the transcript | `{session:{...}, messages:[msg], ...}` — `_handle_session_get` `routes.py:16639+`, messages skipped when `messages=0` | `QubitSessionStore.show(id)` → `{state,session:{id,name,model,created_at,updated_at,archived,message_count,source,identity},messages:[{role,content,timestamp,meta?}],message_count}` (`:687-698`) **+** `.messages(id,limit,offset)` for paging | **TRANSFORM** — session object re-keyed to the sidebar row shape (`session_id`,`title`,`created_at`,`updated_at`), messages passed through (they already carry `role`/`content`/`timestamp`, which is what the JS renders). `state=="MISSING"` → `404 {"error":"Session not found"}` (the JS self-heals on 404, `messages.js:2232`) |
| 3 | `POST /api/wb/chat/start` | body `{session_id (required), message, model, workspace, model_provider, profile, explicit_model_pick?, attachments?, moa_config?, regenerate?, goal_continuation_id}` (`static/messages.js:2205-2228`, `routes.py:31125`) | 200 `{stream_id, session_id, pending_started_at, turn_id, title, effective_model?, effective_model_provider?}` (`routes.py:29993-30004`); 409 `{error:"session already has an active stream", active_stream_id}`; 404 `{error:"Session not found"}`; 503 `{error, code}` | `voicebridge.chat_transport.turn(cfg, text, session=, model=, reasoning=, actor=)` — the ONE send path `/qubit-chat` uses (`qubit_compat.py:7886-7916`); returns `{ok,reply,session_id,…}` | **TRANSFORM (delegate, never re-send)** — call `CT.turn`, map its result onto upstream's start shape: `session_id` from the result, `title` from the store's row after the turn, `stream_id` = Qubit has no background stream for a synchronous turn → `null` (the JS treats `null` as "no live stream", `messages.js:2319`), `pending_started_at` = `null`, `turn_id` = `null`, `effective_model`/`effective_model_provider` = the model the transport actually used when it reports one. A transport refusal maps to upstream's `409`/`503` with the Qubit `code` preserved in `code` |
| 4 | `POST /api/wb/chat/steer` | body `{session_id, text}` (both required) | 200 `{accepted:bool, fallback:str\|null, stream_id:str\|null}`; 400 `{error}` (`streaming.py:19266-19340`) | **GAP** — Qubit's `chat_transport.turn` is synchronous; there is no in-flight agent object to stash steering text into (`_pending_steer` is an upstream agent-loop concept Qubit's turn path does not expose) | **GAP → 501 NOT_PORTED** `MID_TURN_STEER`. Honest alternative for the UI: `accepted:false, fallback:"no_cached_agent"` is only truthful when a run exists — Qubit has no run, so the typed 501 is the accurate answer |

## 2. Workspace — `voicebridge/workbench/adapters_workspace.py` (already confined)

Upstream's file surface is session-scoped: `?session_id=&path=` resolved against
the session's `workspace` (`routes.py:23502-23574`, `26642-26659`). Qubit's
adapter is project-scoped, confined to registered project folders via
`voicebridge.files` — the stronger boundary. The shim therefore takes
`workspace` (the root the client got back) plus `path`, and **reuses the
adapter's `list_tree` / `preview_file` unchanged**.

| # | Upstream route | Request | Upstream response shape | Qubit source | Disposition |
|---|---|---|---|---|---|
| 5 | `GET /api/wb/workspace/list` (upstream `GET /api/list`) | query `session_id` (required upstream), `path` (default `.`) | `{entries:[{name,path,type:"dir"\|"file"\|"symlink",size:int\|null,mtime_ns:str,birthtime_ns:str\|null,workspace_sort_rank:0\|1\|2,is_dir?,target?,target_outside_workspace?}], signature:str, path:str, workspace:str, workspace_recovered:bool}` (`routes.py:23561-23570`, `workspace.py:1632-1745,1620-1629`) | `adapters_workspace.list_tree(cfg, path)` → `{entries:[{name,path,type,size,mtime_ns,sort_rank,target_outside_workspace?}],shown,truncated,path,folder,workspace,roots,read_only,…}` (`adapters_workspace.py:222-370`) | **TRANSFORM** — `sort_rank→workspace_sort_rank`, add `is_dir` from `type`, `birthtime_ns:null` (Qubit's adapter reads `st_mtime_ns` only; upstream emits `null` when the stat lacks it), `signature` = a real content signature over the returned rows (the client uses it only to detect change), `workspace_recovered:false` (Qubit never silently re-binds a workspace). Confinement refusals pass through with `path`/`workspace` set so the UI shows the reason |
| 6 | `GET /api/wb/workspace/read` (upstream `GET /api/file`) | query `session_id`, `path` (required) | `{path, content, size:int, lines:int}` (`workspace.py:1886-1887`) | `adapters_workspace.preview_file(cfg, path, workspace=, max_chars=)` → `{file,name,size,text,clipped,lines,trust,…}` | **TRANSFORM** — `text→content`, `file→path`, keep `size`/`lines`. `clipped:true` → `lines` reflects the returned window and `clipped` is added (upstream truncates too, via `MAX_FILE_BYTES`; it reports a 404 for oversize, Qubit reports the window — the extra `clipped` key is additive and ignored by upstream's reader) |
| 7 | `POST /api/wb/workspace/save` (upstream write lane) | body `{path, content}` | upstream writes through `safe_resolve_ws` + anchor | **GAP (policy)** — Qubit file writes are brokered, confirmed actions (`voicebridge/file_patch.py` via broker), not a viewer write | **GAP → 501 NOT_PORTED** `BROKERED_WRITE` naming `voicebridge.file_patch` → `voicebridge.broker` |
| 8 | `POST /api/wb/workspace/upload` (upstream `POST /api/workspace/upload`, `routes.py:19164`) | multipart body | upstream persists into the session workspace | **GAP (policy)** — same brokered-write boundary; Qubit's uploader is `/files` (SPA) through the broker | **GAP → 501 NOT_PORTED** `BROKERED_WRITE` |

## 3. Crons — `voicebridge.cron_bridge` (the store the scheduler actually fires)

| # | Upstream route | Request | Upstream response shape | Qubit source | Disposition |
|---|---|---|---|---|---|
| 9 | `GET /api/wb/crons` (upstream `GET /api/crons`) | query `all_profiles=1` | `{jobs:[job], all_profiles:bool, active_profile:str, other_profile_count:int}` (`routes.py:18519-18525`); `cron_unavailable:true` + `jobs:[]` when the package is absent | `cron_bridge.list_jobs(cfg)` → `[shape_job]` with `{id,profile,profile_name,qubit_home,is_default_profile,name,prompt,script,skills,schedule:{kind,expr,run_at,minutes,display},schedule_display,repeat:{times,completed}\|null,enabled,state,deliver,model,provider,base_url,no_agent,context_from,enabled_toolsets,workdir,last_run_at,next_run_at,last_status,last_error,last_delivery_error}` (`cron_bridge.py:49-104`) | **TRANSFORM (thin)** — upstream's `_cron_job_for_api` adds `toast_notifications`/`badge_notifications` (defaulting `true`) and `profile`; add exactly those, pass the rest through. `active_profile:"default"`, `all_profiles` echoed, `other_profile_count:0` (one cron home) |
| 10 | `POST /api/wb/crons/create` (upstream `POST /api/crons/create`, `routes.py:32136-32183`) | body `{prompt*, schedule*, name?, deliver?, skills?, model?, provider?, profile?, toast_notifications?, badge_notifications?, enabled?}` | `{ok:true, job:{…}}`; 400 `{error}` | `cron_bridge.create(cfg, name,prompt,schedule,deliver,skills,provider,model,base_url,script,no_agent,context_from,enabled_toolsets,workdir,repeat)` | **TRANSFORM** — `{ok:true, job: shaped}`; errors → `400 {"error": str}`, matching upstream (`:32182`) |
| 11 | `POST /api/wb/crons/update` (upstream `POST /api/crons/update`, `:32215-32248`) | body `{job_id*, …any editable field, enabled?}` | `{ok:true, job:{…}}`; 400 `{error}`; 404 `{error:"Job not found"}` | `cron_bridge.update(cfg, job_id, mutation)` | **TRANSFORM** — same envelope; unknown id → `404` |
| 12 | `POST /api/wb/crons/delete` (upstream `:32251-32261`) | body `{job_id*}` | `{ok:true, job_id}`; 404 `{error}` | `cron_bridge.delete(cfg, job_id)` → bool | **TRANSFORM** — `{ok:true, job_id}` / `404` |
| 13 | `POST /api/wb/crons/pause` (upstream `:32298-32306`) | body `{job_id*, reason?}` | `{ok:true, job:{…}}`; 404 | `cron_bridge.pause(cfg, job_id, reason)` | **TRANSFORM** — `{ok:true, job: shaped}` / `404` |
| 14 | `POST /api/wb/crons/resume` (upstream `:32308-32326`) | body `{job_id*}` | `{ok:true, job:{…}}`; 404 | `cron_bridge.resume(cfg, job_id)` | **TRANSFORM** — as above |
| 15 | `POST /api/wb/crons/run` (upstream `:32264-32305`) | body `{job_id*}` | `{ok:true, job_id, status:"running"}`; `{ok:false, job_id, status:"already_running", elapsed}`; 404 | `cron_bridge.trigger(cfg, job_id)` — schedules the job for the next scheduler tick (the store's run-now) | **TRANSFORM** — `{ok:true, job_id, status:"running"}`; unknown → `404`. **Contract note:** Qubit's scheduler fires on its own tick, so the job starts within a tick rather than in a request-scoped thread; `elapsed`/`already_running` are not observable through `cron.jobs` and are therefore not fabricated |
| 16 | `GET /api/wb/crons/status` (upstream `:27712-27722`) | query `job_id?` | with id: `{job_id, running:bool, elapsed:float}`; without: `{running:{job_id: elapsed}}` | `cron.jobs` execution ledger via `cron_bridge.runs(cfg, job_id, limit)` → `is_active`/`state` on the newest execution | **TRANSFORM** — `running` from the newest execution's `state in ("claimed","running")`; `elapsed` computed from that execution's `started_at`. Without `job_id`: one probe per job (bounded) → `{running:{…}}` |
| 17 | `GET /api/wb/crons/history` (upstream `:27515-27562`) | query `job_id*`, `offset`, `limit` | `{job_id, runs:[{filename,size,modified,usage}], total, offset, limit}` (output-file listing) | `cron_bridge.runs(cfg, job_id, limit)` → `[{id,job_id,started_at,ended_at,finished_at,state,code,detail,is_active,archived}]` | **TRANSFORM with contract diff** — Qubit's ledger is structured executions, not `.md` output files. Serve `runs:[{id,started_at,finished_at,state,code,detail,is_active}]` + `total`, and keep `filename`/`modified`/`usage` absent rather than faked. The UI's history pane must read the structured row; a `?output=1` (file content) read is **GAP** → 501 `CRON_OUTPUT_FILES` |
| 18 | `GET /api/wb/crons/recent` (upstream `:27725-27785`) | query `since` (epoch float) | `{completions:[{job_id,name,status,completed_at,toast_notifications,badge_notifications,session_id,message_count?}], since}` | `cron_bridge.list_jobs` → `last_run_at` (ISO) parsed to epoch | **TRANSFORM** — same derivation upstream does (`:27744-27773`); `session_id` = the cron run's session id when the ledger knows it, else `""` (upstream also emits `""`) |
| 19 | `GET /api/wb/crons/delivery-options` (upstream `:32186-32213`) | — | `{platforms:[{value,label}]}` starting `local`/`origin` | `cron.jobs` delivery vocabulary: `local`, `origin` + the store's known platform names | **TRANSFORM** — the two guaranteed rows always, plus the store's own `_KNOWN_DELIVERY_PLATFORMS` when importable (upstream's exact fallback chain) |

## 4. Approvals / clarify — `voicebridge.approvals` journal

| # | Upstream route | Request | Upstream response shape | Qubit source | Disposition |
|---|---|---|---|---|---|
| 20 | `GET /api/wb/approval/pending` (upstream `:26673-26698`) | query `session_id` | `{pending:{approval_id,description,command,pattern_key?,pattern_keys?}\|null, pending_count:int}` | `ApprovalJournal(cfg.state_dir()/"approvals.jsonl").pending()` → `[Approval{id,capability,summary,created_at,expires_at,state,meta}]` (`approvals.py:137-141`) | **TRANSFORM with contract diff** — `approval_id←id`, `description←summary`, `command←meta.tool/meta.command when journaled else capability`, `pending_count` = live list length, `pending` = head. Qubit has **one global journal**, not a per-session queue: every Qubit approval is reported, and the shim adds `session_id` from `meta` when the record carries one |
| 21 | `POST /api/wb/approval/respond` (upstream `:34054-34110`, `:21401`) | body `{session_id*, choice: "once"\|"session"\|"always"\|"deny", approval_id, yolo?}` | `{ok:true, ...}`; 400 `{error}` | `ApprovalJournal.respond(id, "approve"\|"deny")` (settle-once, `approvals.py:162-187`) | **TRANSFORM** — `once`/`session`/`always` → `approve`, `deny` → `deny`; `{ok:true, approval_id, state, choice}`. `yolo:true` is **GAP** (Qubit has no per-session yolo switch; upstream flips a session flag — Qubit's equivalent is a policy change, refused) → component-level 501 detail inside a 400? No: **GAP → 501 NOT_PORTED** `SESSION_YOLO` when `yolo` is requested. Unknown/expired id → `409 {"ok":false,"error":…,"stale":true}` (upstream's stale semantics) |
| 22 | `GET /api/wb/clarify/pending` (upstream `:26791-26796`) | query `session_id` | `{pending:{question,choices_offered?,requested_at,clarify_id}\|null}` | **GAP** — Qubit's `chat_transport` has a `clarify` *route* (it answers ambiguously-scoped utterances), but no parked-question queue an agent blocks on; there is no `api/clarify.py` counterpart | **GAP → 501 NOT_PORTED** `CLARIFY_QUEUE` |
| 23 | `POST /api/wb/clarify/respond` (upstream `:34363-34397`) | body `{session_id*, response*\|answer\|choice, clarify_id}` | `{ok:true, response}`; 409 `{ok:false,error,stale:true}`; 400 | same GAP | **GAP → 501 NOT_PORTED** `CLARIFY_QUEUE` |

## 5. Auth/status, background, commands

| # | Upstream route | Request | Upstream response shape | Qubit source | Disposition |
|---|---|---|---|---|---|
| 24 | `GET /api/wb/auth/status` (upstream `:17559-17595`) | — | `{auth_enabled, logged_in, oidc_enabled, password_auth_enabled, passwordless_enabled, passkeys_enabled, passkeys_count, passkey_feature_flag, auth_disabled_acknowledged, trusted_auth_enabled?, auth_type?, user?, bound_profile?}` | Qubit auth is **one scheme**: `X-Qubit-Session-Token` (already validated before dispatch). `/api/status` carries the session, `/api/auth/status` in Qubit lists credential *references* (`auth_status.recent_sessions`), which is a different concept | **TRANSFORM (honest static)** — `auth_enabled:true` (the token gate is on), `logged_in`: whether the request carried the valid token (always true past the gate), `oidc_enabled:false`, `password_auth_enabled:false`, `passkeys_enabled:false`, `passkeys_count:0`, `passkey_feature_flag:false`, `passwordless_enabled:false`. No second scheme is introduced and no passkey store is implied. The JS only branches on `passkey_feature_flag` (`panels.js:13094`), which is truthfully `false` |
| 25 | `GET /api/wb/status` (upstream `GET /api/status` — StatusResponse) | — | Qubit already serves `GET /api/status` as `StatusResponse` (`qubit_compat.h_status`, `:523`) | `runtime_inventory.runtime_inventory(cfg)` (the same function `qubit status` calls) | **GAP (deliberate)** — upstream's StatusResponse and Qubit's `StatusResponse` are different wire types and Qubit's is already live at `/api/status`. Re-binding it would break the dashboard. The shim does **not** redefine it: `GET /api/wb/status` → **501 NOT_PORTED** `STATUS_RESPONSE_DIVERGES`, with a `qubit_counterpart: "/api/status"` pointer so the porter can map the client's `.model`/`.version` reads onto Qubit's existing keys |
| 26 | `GET /api/wb/background/status` (upstream `:18066-18072`) | query `session_id*` | upstream returns the background-agent results for that session | **GAP** — Qubit has no parallel background-agent lane per session (`api/background.py` is upstream's) | **GAP → 501 NOT_PORTED** `BACKGROUND_AGENT` |
| 27 | `GET /api/wb/commands` (upstream `:18292-18294`) | — | `{commands:[{name,description,category,aliases,args_hint,subcommands,cli_only,gateway_only}]}` (`api/commands.py:94-109`) | Qubit's slash catalog is already served at `GET /api/chat/slash-commands` (`qubit_compat.h_chat_slash_commands`, `:10518`) | **TRANSFORM** — re-key Qubit's slash catalog onto upstream's eight fields; a command with no `aliases`/`subcommands` gets empty lists, `cli_only=false`, `gateway_only=false`. Never invent a command Qubit does not have |

---

## Summary

| Group | Routes | DELEGATE/TRANSFORM | GAP (501 NOT_PORTED) |
|---|---|---|---|
| 1 Sessions | 4 | 3 | 1 (`MID_TURN_STEER`) |
| 2 Workspace | 4 | 2 | 2 (`BROKERED_WRITE` ×2) |
| 3 Crons | 11 | 11 | 0 |
| 4 Approvals/clarify | 4 | 2 | 2 (`SESSION_YOLO`, `CLARIFY_QUEUE`) |
| 5 Auth/status/background/commands | 4 | 2 | 2 (`STATUS_RESPONSE_DIVERGES`, `BACKGROUND_AGENT`) |
| **Total** | **27** | **20** | **7** |

Out of scope of this shim (upstream routes Qubit has no source for at all, so the
porter must leave them at 501): the SSE families (`/api/sessions/events`,
`/api/approval/stream`, `/api/clarify/stream`, `/api/session/stream`,
`/api/chat/stream`) — Qubit's turn is synchronous and its event path is the
dashboard's own; git routes; terminal/PTY (typed refusal already exists in
`qubit_compat`); shares; media/raw-file streaming; office-document preview;
kanban; goals; MoA; plugins/extensions; onboarding/OAuth; passkeys; profiles
management; updates; memory/skills/scripts panels.

## Invariants the implementation must hold

1. **Delegate, never duplicate** — sessions only through `QubitSessionStore`;
   turns only through `chat_transport.turn`; files only through
   `adapters_workspace`; crons only through `cron_bridge`; approvals only
   through `ApprovalJournal`.
2. **No direct `state.db`** — the shim module must contain **zero**
   `sqlite3`/`state.db` references outside comments explaining why not.
3. **No invented data** — a GAP is a typed `501 {"ok":false,"code":"NOT_PORTED",
   "detail":…}` naming the missing Qubit concept.
4. **No second auth scheme** — `X-Qubit-Session-Token`, enforced by the existing
   listener before dispatch; the shim reads no header.
5. **Registration** — one block of lines delivered for
   `voicebridge/qubit_compat.py`, not edited by this branch.
