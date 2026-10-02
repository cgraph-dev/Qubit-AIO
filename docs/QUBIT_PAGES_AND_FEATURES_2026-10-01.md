# Qubit pages + features inventory — 2026-10-01

Inventory only. No milestone claimed, no code changed, no behavioral proof asserted.
Derived read-only from `frontend/qubit-web/src/App.tsx`, the 40 non-test page
modules under `frontend/qubit-web/src/pages/`, the 10 family API clients under
`frontend/qubit-web/src/lib/ocFamily*.ts`, the 131-row family bridge in
`voicebridge/qubit_compat.py`, `frontend/qubit-web/public/plugins/fleet.json`,
`frontend/qubit-app/plugins/qubit-boards/manifest.json`, and
`qubit agent --help`. Qubit naming only below; see §6 for the two required
non-product references (legal attribution + frozen records).

## 1. Parity pages carried from the reference (19)

Each page below exists on the reference dashboard (read-only oracle at
`http://127.0.0.1:9119/?profile=default`) and has a working Qubit counterpart
on the same authority path (broker / policy / audit / confirmation / e-stop).
PORTED means registered-with-handler, not end-to-end verified.

### `/sessions` — SessionsPage.tsx
Session roster + transcript management over the single canonical session store.
Controls: search/filter inputs, open/resume/refresh, rename (`api.renameSession`),
export (`api.exportSessionUrl`), import (`api.importSessions`), prune
(`api.pruneSessions`), delete-empty (`api.deleteEmptySessions`), bulk delete
(`api.bulkDeleteSessions`), single delete (`api.deleteSession`), patch
(`api.patchChatSession`), archive counts, restore. Gates: destructive ops are
confirm-gated (409 round-trip + staged-confirm banner settle); e-stop + audit
on every mutation.

### `/files` — FilesPage.tsx
Project file browser rooted at the active folder. Controls: list
(`api.listFiles`), read (`api.readFile`), mkdir (`api.createDirectory`),
upload (`api.uploadFile`, real multipart bytes), brokered reversible delete
(`api.deleteFile`). Gates: binary upload runs the brokered `local.files_upload`
capability — path-confined to the approved project root, confirm + audit +
e-stop; outside-root answers typed `FILE_PATCH_OUTSIDE_ROOT`.

### `/analytics` — AnalyticsPage.tsx
Usage/token analytics read (`api.getModelsAnalytics`-adjacent reads). Controls:
range selectors, per-model/token tables, refresh. Gate: page renders an
explanation unless `dashboard.show_token_analytics=true` (same rule as the
reference); the token-analytics display gate is intentional, not a gap.

### `/models` — ModelsPage.tsx
Model/provider assignment (MODEL card). Controls: provider/model pickers,
`api.setModelAssignment` (main scope), `api.setTerminalProfile`, active-profile
and terminal-pin reads, Ollama local row (`brain4` / `spark17` / `spark4`),
auxiliary-model reads. Gates: non-main scope writes are refused
(`AUX_PIN_REFUSED`, client + server); below-floor picks keep the terminal pin
with a naming reason; local picks need no endpoint/credential.

### `/logs` — LogsPage.tsx
Backend log viewer. Controls: search input, export, offset pagination, level
filter, refresh. Backend honors `?search=`; served lines match `agent.log`
byte-for-byte. No mutation gates (read-only page).

### `/cron` — CronPage.tsx
Scheduled-job management. Controls: create dialog (`api.createCronJob`),
pause/resume (`api.pauseCronJob` / `api.resumeCronJob`), trigger
(`api.triggerCronJob`), edit (`api.updateCronJob`), delete (`api.deleteCronJob`),
timezone/retry fields, banner confirm settle. Gates: pause/resume/run-due and
webhook-create settle through the compat runner (id-only confirm re-enters the
real mutator); Last/Next dates are honest epoch-derived values.

### `/skills` — SkillsPage.tsx
Skill catalog + install surface. Controls: catalog list (`api.getSkills`),
toolset filter (`api.getToolsets`), hub search/scan/install
(`api.searchSkillsHub` / `api.scanSkillFromHub` / `api.installSkillFromHub`),
toggle (`api.toggleSkill`), update-from-hub (`api.updateSkillsFromHub`),
action-status polling. Gates: installs stage through confirm + audit; `skills_create`
re-enters the real creator (no bare `CONFIRM_UNSUPPORTED`).

### `/plugins` — PluginsPage.tsx
Plugin catalog (70 real entries) + lifecycle. Controls: list, rescan
(`api.rescanPlugins`), reload (`api.reloadPlugins`), install/remove/enable/
disable (`api.installAgentPlugin` etc.), update (`api.updateAgentPlugin`),
visibility (`api.setPluginVisibility`), provider mapping
(`api.savePluginProviders`), memory-provider setup/config. Gates: remote install
is operator-gated + pin-verified against the curated pinned index; plugin-update
refusal is policy; memory-provider read/write share one typed gate.

### `/mcp` — McpPage.tsx
MCP server registry. Controls: server list, add (`api.addMcpServer`), remove
(`api.removeMcpServer`), enable toggle (`api.setMcpServerEnabled`), live test
(`api.testMcpServer`), tool invoke (`api.invokeMcpTool`), resources / prompts /
resource-template reads. Gates: unknown server answers `MCP_NOT_FOUND`;
tools-only servers answer honest typed `MCP_PROTOCOL_ERROR` on resource reads;
capabilities + `enabled` are preserved on edit (no wipe).

### `/pairing` — PairingPage.tsx
Device/DM pairing approvals. Controls: pending queue, approve
(`api.approvePairing`), reject (`api.revokePairing`), clear-pending
(`api.clearPendingPairing`), GET returns `request_id`. Gate: single-store
reads/writes (GET and write paths reconciled to one store).

### `/channels` — ChannelsPage.tsx
Messaging-platform connections (33-platform catalog). Controls: catalog list,
enable/disable (`api.updateMessagingPlatform` x2), live test
(`api.testMessagingPlatform`), WhatsApp onboarding start/poll/apply/cancel,
Telegram onboarding start/cancel (real pairing id + deep-link + QR payload +
expiry), gateway restart (confirm-callback settle). Gates: channel secrets stay
references-only (`env:`/`file:`); literal values refused; restart settles the
supervisor 409 with press-as-confirmation.

### `/webhooks` — WebhooksPage.tsx
Webhook subscriptions. Controls: create (`api.createWebhook`), enable toggle
(`api.setWebhookEnabled`), test (`api.testWebhook`), rotate
(`api.rotateWebhookSecret`), deliveries (`api.getWebhookDeliveries`), retry
(`api.retryWebhook`), delete (`api.deleteWebhook`), gateway restart
(confirm-callback settle). Gates: id-scoped rotate/test/deliveries/retry;
secret must be an `env:`/`file:` ref; URLs allowlisted.

### `/system` — SystemPage.tsx
Operator console. Controls: gateway start/stop/restart (confirm dialogs),
update check/apply (`api.checkQubitUpdate` / `api.updateQubit`), backup
(`api.runBackup`), import (`api.runImport` / `api.runImportUpload`), debug share
(`api.runDebugShare`), power status (`api.getPowerStatus` x2), curator pause
(`api.setCuratorPaused`), skills-hub update. Gates: in-place self-update renders
honestly disabled (`UPDATE_OPERATOR_OWNED`, staged upgrades only); restart is
confirm-gated; SystemPage cancel is honest (no fake success toast).

### `/agents` — AgentsPage.tsx
Worker-lane roster (read-only home). Controls: roster list, refresh, status
read over `GET /api/qubit/agents` (same modules the CLI and voice use).
Gates: no agent mutations exist here by design — agent mutations are a
desktop-app surface and the page says so in place (no dead buttons).

### `/profiles` — ProfilesPage.tsx
Profile registry (single reconciled registry). Controls: list
(`api.getProfiles` x3), create (`api.createProfile`), delete
(`api.deleteProfile`), rename (`api.renameProfile`), set-active
(`api.setActiveProfile`), model pick (`api.setProfileModel`), description/SOUL
edits, setup-command read, describe-auto (`api.describeProfileAuto`).
Gates: create is confirm-gated (409 + disclosed partial-field behavior);
provider/model validated best-effort per the reference shape.

### `/profiles/new` — ProfileBuilderPage.tsx
Guided new-profile form. Controls: name/description/model fields, clone-source
picker, validation hints, submit (same `createProfile` backend as `/profiles`).
Gate: same confirm gate as profile create; invalid names refused before submit.

### `/config` — ConfigPage.tsx
Runtime config editor. Controls: config get (`api.getConfig` x3), save
(`api.saveConfig`), section editors, round-trip status. Backend behavior:
16→17→16 round-trip with atomic backup. Gates: secret values never served
(`SECRET_NEVER_SERVED`); the dead raw-YAML control was removed (no fantasy UI).

### `/env` — EnvPage.tsx (Keys)
Credential-reference inventory (names only, never values). Controls: ref list,
set (`api.setEnvVar`), delete (`api.deleteEnvVar`), `is_set` badges,
`env:`/`vault:`/`file:` round-trip. Gates: literal secret values refused by
design; reveal control removed; presence shown as stored/not-stored + metadata.

### `/docs` — DocsPage.tsx
Product docs + API reference. Controls: doc tree, search, OpenAPI group
browser, Qubit-owned `/agent/openapi.json` link. Gate: legacy-vendor route
groups render collapsed and annotated as compat aliases (routes still resolve);
no fabricated service URLs (canonical repo links or nothing).

## 2. Qubit-original pages

### `/chat` — TerminalChatPage.tsx (real terminal)
The full terminal surface: vendored xterm terminal over the single Qubit
WebSocket upgrade path (`/agent/api/pty`), auth via short-lived single-use
scoped ticket (`POST /agent/api/auth/ws-ticket`, never on the URL). Controls:
terminal pane, reconnect, ticket refresh, model/pin status chips, resume
deep-link (`/chat?resume=<id>`). Gates: raw-shell PTY refused by policy
(`PTY_NO_SHELL` — ticketed terminal only); empty resume opens FRESH (no fossil
reopen); below-floor model pins keep the terminal pin with a reason.

### `/qubit-chat` — ChatPage.tsx (native voice chat)
Qubit-native SSE chat on the same backend (`POST /api/qubit/chat/stream`,
same `chat_transport` module as CLI/voice). Controls: transcript (server-backed,
reload-safe via `GET /api/qubit/chat/sessions/<id>/messages`), streaming deltas
with loading/empty/error/retry, sessions switcher, summarize button (local tier
report), open-in-`/chat` deep link, optional browser voice input. Gates: none on
reads; send path shares the canonical gateway turn (broker/policy/audit).

### `/voice-edit` — VoiceEditPage.tsx (voice typing settings)
System-wide voice-typing / selected-text editing settings + live "try it" panel
driving the same begin/capture/transform/preview/apply backend as the desktop
helper. Controls: prompt + routing stores, wake-word threshold/gate inputs,
per-app overrides, service/stt URL inputs, restart hints, preview/apply trial.
Gates: feature OFF by default (enabling here is the explicit opt-in); unknown
keys refused (`VOICE_EDIT_BAD_SETTINGS`); `per_app:{}` deletes the node.

### `/agents` — worker-lane home (see §1)
Listed in §1 as the roster; Qubit-original in that the reference has no worker
lane — the single in-process registry the CLI, dashboard, and voice share, with
no second registry and no fabricated agents.

### Plugin board tabs — `/kanban`, `/achievements`, `/qubit-boards`
Rendered by the plugin host (`PluginPage`), not built-in routes. `/kanban`
(multi-agent collaboration board: columns, cards, threads, assignees) and
`/achievements` (workflow achievements) ship bundled via `fleet.json`;
`/qubit-boards` (boards/cards on the canonical store — same backend as the
`kanban` CLI and voice) ships as the overlay plugin. Hidden plugin routes
(`/qubit-approvals`, `/qubit-brand`) claim URLs without sidebar entries.

## 3. New family pages (13) — per-button backend maps + honest gates

All 13 pages call the 5 family API clients, which call `GET/POST/PUT
/api/qubit/<family>/<action>`; the bridge serves the 108-row allowlist verbatim
and delegates to the already-registered gateway method behind each row. Reads
execute immediately; mutations are e-stop gated then confirm-gated (409 +
staged id, re-POST executes exactly once); unknown family/action is a typed 404.

### `/agent-workspace` — AgentWorkspacePage.tsx (3 bridge rows)
File roster `GET .../list` → `agents.workspace.list`; open-file `GET .../get`
→ `agents.workspace.get`; run-status `GET .../run-status` → `agent.wait`.
Controls: Refresh workspace, Open workspace file, Check run status (run-id
input). Honest gate: workspace create/edit/delete has no backend — disclosed on
the page, no button pretends otherwise.

### `/nodes` — NodesPage.tsx (9 bridge rows)
List → `node.list`; describe → `node.describe`; enroll/approve →
`node.pair.approve` (confirm); revoke → `node.pair.reject` (confirm); remove →
`node.pair.remove` (confirm); capabilities → `node.pluginSurface.refresh`;
dispatch → `node.invoke` (confirm, `task`→`capability` alias); rename →
`node.rename` (confirm + re-enroll warning). Unmanageable rows render disabled
with reason; no message-gateway controls on this page.

### `/devices` — DevicesPage.tsx (10 bridge rows, 1 honest gate)
Setup status → `device.pair.setupStatus`; mint code →
`device.pair.setupCode` (confirm, `ttl_seconds`→`ttl_s`); pairings →
`device.pair.list`; approve/reject/remove → `device.pair.approve/reject/remove`
(confirm); token rotate/revoke → `device.token.rotate/revoke` (confirm); enroll
→ `device.pair.approve` (confirm). Honest gate: `identity` answers typed
`DEVICE_IDENTITY_UNAVAILABLE` — pairings are the canonical read. Secrets never
displayed (stored/not-stored badges + metadata).

### `/session-ops` — SessionOpsPage.tsx (20 bridge rows)
Reset/rewind/compact/fork → `sessions.reset/rewind/compact/fork` (confirm);
branch-switch → `sessions.branches.switch` (confirm); compaction branch/restore
→ `sessions.compaction.branch/restore` (confirm); cleanup-empty →
`sessions.cleanup` (confirm); goals set/clear → `sessions.goal.update/clear`
(confirm); suggestions list/add/resolve → `session.suggestions.*` (add/resolve
confirm); groups list/create/rename/delete → `sessions.groups.*` (mutations
confirm); companion ask/state/reset → `sessions.companion.ask/state/reset`
(ask/state read, reset confirm). Rendered honesty: fork = copy-into-NEW
(source untouched); branch-switch = restore-in-place (rewrites current).

### `/worktrees` — WorktreesPage.tsx (4 bridge rows + roster reuse)
Create/remove/restore/prune → `worktrees.create/remove/restore/gc` (all
confirm; create/restore derive `name` from `path`). Roster reads reuse the
existing `GET /api/git/worktrees` + `GET /api/git/branches` directly.
Restore/prune are labeled with their typed codes (never presented as reference
restores); staged ops settle through the shared confirm round-trip.

### `/checkpoints` — CheckpointsPage.tsx (2 honest gates + journal reuse)
Create/verify answer typed `CHECKPOINTS_JOURNAL_VERIFY_ONLY` — no gateway
method is registered behind them (journal surface is verify-only; no restore
path exists and none is rendered). Working reads/writes: journal list via `GET
/api/ops/checkpoints`, prune via `POST /api/ops/checkpoints/prune` (confirm).
Compaction branch/restore live on `/session-ops` (linked, not duplicated).

### `/qubit-board-admin` — BoardAdminPage.tsx (11 bridge rows)
Tabs/widgets/roster/log reads → `board.get` (tabs, widgets), `board.widget.grant`
(roster), `board.event` (log); tab create/rename/move/delete → `board.update`
with `tab_create` / `tab_update_title` / `tab_update_position` / `tab_delete`
ops (confirm); widget add → `board.widget.put` (confirm); widget remove →
`board.update` `widget_remove` op (confirm); role grant → `board.widget.grant`
(confirm). Disabled-with-reason: `board.event`, prompt-authorize, and
board-action writes (need a widget sandbox / signed view-ticket runtime).

### `/qubit-task-inbox` — TaskInboxPage.tsx (4 bridge rows + 3 disabled)
Ledger → `task.history`; suggestions → `task.suggestions`; cancel/retry →
`tasks.cancel/retry` (confirm, by action receipt or live chat session — never a
bare task id). Disabled-with-reason: `tasks.dismiss` (no delivery registry) and
`mentions.list` / `mentions.dismiss` (no inbox service; channels counterpart
linked). Receipt/session inputs, no phantom sends.

### `/qubit-talk` — TalkPage.tsx (8 bridge rows, realtime removed)
Mode/catalog/config/voicewake reads → `talk.mode/catalog/config`,
`voicewake.get` (voicewake consumed read-only here); client steer/transcript/
tool-call → `talk.client.steer/transcript/toolCall` (confirm, canonical session
+ broker); speak → `talk.speak` (confirm). Disabled-with-reason (never called):
every `talk.session.*` realtime row + the client create/close pair (no duplex
runtime). Session-key, steer-text, tool-name/args inputs; spoken-audio output.

### `/qubit-scheduler` — SchedulerMediaPage.tsx (9 bridge rows)
Schedule toggle → `cron.update` (confirm, enabled-only); TTS provider/enable/
disable/persona → `tts.setProvider/enable/disable/setPersona` (confirm,
canonical speech profiles); power reads → `gateway.suspend.prepare/resume`;
task cancel/retry → `tasks.cancel/retry` (confirm, receipt or session).
Disabled-with-reason: per-job scratch, suspend handoff, portal proxy,
suggestion/dismiss registries (no local counterpart).

### `/admin/skills-review` — SkillsReviewPage.tsx (14 bridge rows)
Queue list/inspect → `skills.proposals.list/inspect`; history status →
`skills.proposals.historyStatus`; create → `skills.proposals.create` (confirm);
apply/reject/quarantine/revise/update/request-revision →
`skills.proposals.apply/reject/quarantine/revise/update/requestRevision`
(confirm, owner-gated verdicts); events → `skills.proposals.events.list`;
curator pin/unpin/restore → `skills.curator.pin/unpin/restore` (confirm).
Disabled-with-reason: upload, automated evaluation, history-scan, remote
security verdicts (policy-refused backend operations, exact prerequisite shown).

### `/admin/mcp-calls` — McpCallsPage.tsx (3 bridge rows + 2 disabled)
Call tool → `mcp.app.callTool` (confirm; server + tool + args inputs); list
resources → `mcp.app.listResources`; read resource →
`mcp.app.readResource` (URI input). Disabled-with-reason: resource templates
and model-context updates (policy-refused, exact prerequisite shown).

### `/admin/users` — UsersAdminPage.tsx (11 bridge rows, single-operator)
List/self/mentionable → `users.list/self/mentionable` (read-only loopback
operator identity); prefs get + `PUT prefs` → `users.prefs.get/set` (PUT is a
direct write — font/theme store, e-stop gated, no confirm round-trip);
auth-links / model-accounts → `users.listAuthLinks/listModelAccounts`; select /
link / unlink → `users.selectModelAccount/linkAuthProfile/unlinkAuthProfile`
(confirm); connect catalog → `users.authConnect.catalog`; canonical-profile
resolution with the `qubit auth login` manual handoff. Disabled-with-reason:
display name, role, avatar, email, auth-start (no multi-user module).

## 3b. Second-wave family pages (5) — per-button backend maps + honest gates

All 5 pages call the 5 second-wave family API clients, which call `GET/POST
/api/qubit/<family>/<action>`; the bridge serves the +23-row allowlist verbatim
(131 rows over 17 families; webchat adds 0 rows — reuse-only) and delegates to
the already-registered gateway method behind each row. Reads execute
immediately; mutations are e-stop gated then confirm-gated (409 + staged id,
re-POST executes exactly once); unknown family/action is a typed 404.

### `/canvas` — CanvasPage.tsx (9 bridge rows, all honest gates)
No canvas file host or node-presentation backend is registered: list/get/live
→ typed `CANVAS_HOST_UNWIRED`; add/remove → same gate (mutations gate 403
before any confirm is staged); present/hide/navigate/snapshot → typed
`CANVAS_PRESENT_UNWIRED` (the node roster reads supply identity; no
presentation backend exists). Client confines names path-side (no traversal/
absolute/odd segments) and builds preview URLs as pure strings until the
follow-up confined static host (`GET /__qubit__/canvas/*`, plugin-asset
precedent) lands. No button fakes a success.

### `/qubit-tasks` — TasksPage.tsx (4 bridge rows + 1 never-wired refusal)
List → `tasks.list` (shared ledger `task_ledger.list_tasks`, state filter);
get → `tasks.get` (`task_ledger.show`, `lookup` aliases to `taskId`);
cancel → `tasks.cancel` (confirm; by receipt or live chat session — never a
bare ledger id); retry → `tasks.retry` (confirm; exact-args receipt retry or
last chat turn). `tasks.dismiss` has no path by design
(`TASKS_DISMISS_OPERATOR_REQUIRED`) — the page renders the verbatim
`TASKS_DISMISS_REFUSAL`, never wired.

### `/qubit-taskflows` — TaskflowsPage.tsx (5 bridge rows + 11 cron-reuse rows)
Durable flows are cron-backed (`schedules.*` via the existing `/api/cron/jobs`
routes: list/get/runs/create/update/delete/pause/resume/trigger/blueprints/
blueprint_instantiate — reused, no bridge rows): recurring schedules render as
standing orders, `run_at` as one-shots; enabled-toggle re-sends the full
mutation (enabled-only, confirm). Needs-bridge rows: hooks → `hooks.status`
(real catalog); hook_enable/hook_disable/cancel/programs → honest gates
(`FLOWS_HOOKS_UNWIRED` / `FLOWS_CANCEL_UNWIRED` / `FLOWS_PROGRAMS_UNWIRED`)
naming the real surface (cron pause/delete). The ephemeral-suggestion registry
is not here by design (lives on `/qubit-task-inbox`).

### `/qubit-memory` — MemoryBackendsPage.tsx (5 bridge rows, status-only)
The `/api/qubit/memory/*` namespace has no backend: status/provider/
provider-config/probe → typed `MEMORY_BRIDGE_UNWIRED` (live reads go to the
canonical `GET /api/memory`, `GET /api/memory/providers/<id>[/config]` —
qubit-local, the only provider); provider-set → verbatim `MEMORY_LOCAL_ONLY`
(the gate persists qubit-local only). Only `qubit-local` is selectable;
lancedb/wiki/active-memory render disabled-with-reason (hosted infra/secrets
or no writer — never a fake connect button).

### `/webchat` — WebChatPage.tsx (0 bridge rows, reuse-only)
Minimal SSE chat on the same `voicebridge.chat_transport` both chats use:
sessions list/create/history → `api.listChatSessions`/`createChatSession`/
`getChatSessionMessages`; streaming turns → `streamChatTurn`
(`POST /api/qubit/chat/stream`); non-streaming turn → `POST /api/qubit/chat`
via shared `fetchJSON` (no `api.ts` wrapper exists for it). Server-backed
sessions (reload-safe), no voice controls, no terminal/PTY, explicitly not a
duplex socket.

## 4. CLI appendix — `qubit agent` top-level families (brief)

(`qubit agent --help`; subcommands omitted — families only. The native `qubit`
top-level lanes — `run/once/mic-test/task`, `voice/brain/audio`, `gateway/msg`,
`skills/kanban/project`, `sessions/checkpoints`, `update/product`, and ~100
total native lanes — are the voicebridge surface and are not enumerated here.)
Chat + sessions: `chat`, `sessions`. Models + routing: `model`, `moa`,
`fallback`, `tools`. Runtimes: `codex-runtime`, `computer-use`, `lsp`, `proxy`.
Gateway + messaging: `gateway`, `whatsapp`, `whatsapp-cloud`, `slack`, `send`,
`peer`, `webhook`. Scheduling + boards: `cron`, `kanban`, `project`, `sync`.
Credentials + access: `auth`, `secrets`, `egress`, `logout`, `pairing`, `vault`.
System + data: `status`, `doctor`, `config`, `migrate`, `setup`, `update`,
`backup`, `import`, `import-agent`, `checkpoints`, `worktree`, `browser`.
Skills + plugins + review: `skills`, `bundles`, `plugins`, `curator`,
`approvals`, `mcp`. Diagnostics + misc: `verify`, `security`, `dump`, `debug`,
`console`, `skin`, `pets`, `journey`, `memory`, `insights`, `usage`,
`monitoring`, `claw` (migration helpers), `acp`, `profile`, `completion`,
`dashboard`, `serve`, `desktop`, `logs`, `prompt-size`.

## 5. Totals (verified by command)

| What | Count | Command |
|---|---|---|
| Non-test page modules | 40 | `ls -1 frontend/qubit-web/src/pages/*.tsx \| grep -v '\.test\.tsx' \| wc -l` → **40** (35 first wave + 5 second wave) |
| Route-table lines in `App.tsx` | 39 | `grep -cE '^ +"/' frontend/qubit-web/src/App.tsx` → **39** (`/` redirect + 38 pages; `/chat` + `/qubit-chat` mount conditionally on top) |
| Addressable dashboard routes | 44+ | 39 table + 2 chat + 2 bundled plugin tabs (`/kanban`, `/achievements` per `fleet.json`) + 1 overlay tab (`/qubit-boards` per its `manifest.json`) + 2 hidden plugin routes |
| `<Button` controls in pages | 304 | `grep -o '<Button' frontend/qubit-web/src/pages/*.tsx \| grep -v test \| wc -l` → **304** |
| Dashboard `api.*` call sites | 150 | `grep -oE 'api\.[a-zA-Z0-9_]+\(' frontend/qubit-web/src/pages/*.tsx \| wc -l` → **150** (second-wave pages call ocFamily clients, not `api.*`) |
| Family-page labelled controls | 257 | `aria-label` tally: 203 over the 13 first-wave pages (concurrent lanes added controls since the 155 count) + 54 over the 5 second-wave pages → **257** |
| Family API clients | 10 | `ls frontend/qubit-web/src/lib/ocFamily*.ts` → 10 API libs (+1 test): Agents, Sessions, Board, Talk, Admin + Canvas, Tasks, Flows, Memory, Webchat |
| Family bridge rows | 131 over 17 families | `grep -c '"family":' voicebridge/qubit_compat.py` → **131**; agent-workspace 3, nodes 9, devices 10, session-ops 20, worktrees 4, checkpoints 2, board 11, task 4, talk 8, scheduler 9, skills-review 14, mcp-calls 3, users 11, canvas 9, tasks 4, flows 5, memory 5 (webchat 0, reuse-only) |
| Compat route registrations | 387 | `grep -c '_r(' voicebridge/qubit_compat.py` → **387** |
| Parity inventory | 1389 rows: 1280 PORTED / 109 GATED / 0 MISSING | `docs/generated/parity_inventory.json` `counts` (routes 277, CLI 388, controls 603, tools 121; compat ROUTES 460, GATED 56) |
| Voice registry | 514 intents + 18 guards | `voice_commands.REGISTRY` 514, `VOICE_GUARDS` 18 (per inventory `qubit_surface_summary`) |
| Honest family gates | 2 named + generic | `DEVICE_IDENTITY_UNAVAILABLE`, `CHECKPOINTS_JOURNAL_VERIFY_ONLY`, plus `FAMILY_UNKNOWN_ACTION` (404), `FAMILY_METHOD_NOT_REGISTERED` (500), `EMERGENCY_STOP` (403), confirm-required 409s |

## 6. Notes (required references, not product surfaces)

- Legal attribution is the one required exception to zero-brand policy and
  lives in `LICENSE` and `THIRD_PARTY_NOTICES.md` — retained, not inventoried here.
- Frozen records (`docs/archive/2026-09-21/`, pre-reset docs, and the frozen
  contract surface `docs/differential/INTERFACE.md`) are preserved as-is and
  referenced only as evidence anchors, never edited.
- This doc uses "the reference dashboard" / "the reference" solely where the
  parity source is load-bearing, per brand policy for internal docs.
