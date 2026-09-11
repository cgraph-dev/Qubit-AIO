# OpenClaw capability inventory vs Qubit VoiceBridge — audit

Read-only audit. No repo file was modified. Report written to
`/tmp/opencode/audit_openclaw.md`.

- Qubit repo: `/home/trick/Projects/Repos/qubit-voicebridge`
- Pinned OpenClaw source (read-only): `/tmp/opencode/pinned/openclaw`
  at revision `3928bad9badfcb6c7d140530435e806fb8092190`
  (verified with `git rev-parse HEAD`)
- Qubit interpreter used for probes: `.venv/bin/python`

## Status legend (honest, capability-level — not tiles/names/routes)

- **IMPLEMENTED** — a real backend code path exists, is wired to at least one
  of CLI/voice/dashboard, and has a proving test that exercises behavior
  (not just catalog presence).
- **PARTIAL** — a real sub-part exists and works, but the upstream capability
  is materially incomplete on the Qubit side (missing breadth, missing
  automatic/daemon behavior, or missing real side effects).
- **GATED / REFUSED** — Qubit deliberately refuses the upstream behavior with
  a typed code and has a test; there is no live path. This is a *safe*
  divergence, not a gap to be "closed" blindly.
- **MISSING** — no Qubit backend path at all (may be deliberate policy).

Counts below are capability rows, not routes, tiles, or registry names.

---

## 1. Method and probes run

Read-only commands and probes (all against the working checkout; no writes):

- `git -C /tmp/opencode/pinned/openclaw rev-parse HEAD` → pinned rev confirmed.
- Enumerated the OpenClaw CLI, docs tree, `src/`, `packages/`, and `extensions/`
  with `ls`/`rg`, and read `docs/cli/*`, `docs/tools/*`, `docs/gateway/*`,
  `docs/channels/*`, `docs/automation/*`, `docs/nodes/*`, `docs/web/*`.
- Enumerated Qubit CLI families by importing `voicebridge.cli.build_parser()`
  and walking the argparse tree.
- Enumerated the Qubit voice registry/guards from
  `voicebridge/voice_commands.py` (`REGISTRY`, `VOICE_GUARDS`).
- Enumerated Qubit dashboard routes from `voicebridge/hermes_compat.py`
  (`ROUTES`) and dashboard ops from `voicebridge/dashboard_ops.py`
  (`EXTRA_OPS`), then read the handlers.
- Read the Qubit backends listed in the task and their proving tests.
- Ran the capability-focused, hermetic test subset (single run):
  `test_browser_tabs8u, test_gateway_peers8tf, test_schedules8u, test_webhooks8tf,
  test_pairing8tf, test_messaging8tf, test_runtime_images8u, test_computer_use8tf,
  test_acp_lsp8tf, test_kanban8tf, test_board_canvas8u, test_projects8tf,
  test_opencode_voice8u, test_voice_commands8tf, test_dashboard_ops8tf,
  test_capability_catalog, test_native_equivalents8u, test_files_ide8u,
  test_vision8tf, test_gateway_frames8u`
  → **249 passed, 1 skipped** (111.9s). The 1 skip is the expected live
  `--dry-run`-dependent skip.
- `pytest --collect-only` → **2932 of 2980 tests collected** (48 deselected by
  `addopts = -m "not live and not dashboard and not monitor and not ui"`).

Qubit surface sizes (from the live objects, not docs):

| Surface | Count |
|---|---|
| CLI top-level families | 69 |
| Voice registry entries (`REGISTRY`) | 333 |
| Voice guards (`VOICE_GUARDS`) | 14 |
| Dashboard compat routes (`ROUTES`) | 92 |
| Dashboard Tier-2 extra ops (`EXTRA_OPS`) | 34 |
| Dashboard overlay plugins | 10 (`qubit-approvals, -boards, -brand, -chat, -devices, -gateway, -memory, -ops, -reports, -runtime`) |

---

## 2. Upstream OpenClaw capability inventory (with citations)

At rev `3928bad`, OpenClaw is a gateway-centric agent platform. Evidence, by
domain:

### 2.1 Gateway, protocol, nodes, devices, pairing
- Gateway protocol v4 and min-version matrix:
  `packages/gateway-protocol/src/version.ts:2-8`.
- Full Gateway server/auth surface (1379 files under `src/gateway/`), including
  device auth (`src/gateway/device-auth.ts`), device-pairing join
  (`src/gateway/device-pairing-join-http.ts`), node pairing auto-approve
  (`src/gateway/node-pairing-auto-approve.ts:88`), operator scopes
  (`docs/gateway/operator-scopes.md`), remote/Cloudflare/tailscale
  (`docs/gateway/remote.md`, `docs/gateway/stable-https-url.md`).
- Pairing store and setup codes: `src/pairing/pairing-store.ts:33`,
  `src/pairing/setup-code.ts:402`, `src/pairing/join-code.ts:6`;
  device-pair plugin `extensions/device-pair/openclaw.plugin.json`.
- CLI: `docs/cli/gateway.md`, `docs/cli/devices.md`, `docs/cli/pairing.md`.
- Cloud workers / node host: `docs/cli/worker.md`, `docs/gateway/cloud-workers.md`,
  `src/node-host/` (incl. `computer-command.ts`, `desktop-stream-command.ts`,
  `invoke-device-apps.ts`), `src/worker/`.

### 2.2 Browser
- Browser plugin with profiles and lifecycle:
  `extensions/browser/openclaw.plugin.json`, `extensions/browser/browser-profiles.ts`,
  `extensions/browser/browser-maintenance.ts`, `extensions/browser/chrome-extension/`.
- Tool/CLI: `docs/tools/browser.md`, `docs/cli/browser.md`, `docs/tools/browser-login.md`,
  `docs/tools/browser-control.md`, `docs/tools/chrome-extension.md`.
- Remote CDP via node host and WSL2 guidance in the same docs.

### 2.3 Desktop / computer-use
- `docs/nodes/computer-use.md`: capability-gated `computer.act` +
  `screen.snapshot`, Peekaboo (macOS) and CUA providers, coordinate frames,
  window/element family, browser family.
- Code: `src/worker/computer-runtime.ts`, `src/worker/node-computer-protocol.ts`,
  `src/node-host/computer-command.ts`, `extensions/cua-computer/`,
  `src/agents/tools/computer-tool.ts`, `src/agents/tools/screen-tool.ts`.
- Work-journal of screen snapshots: `extensions/logbook/openclaw.plugin.json`.

### 2.4 Messaging / channels
- Channel framework: `src/channels/` (allowlists, routing, inbound/outbound,
  draft preview, direct-DM guard).
- ~29 channel plugins under `extensions/` (discord, slack, telegram, whatsapp,
  signal, imessage, sms, matrix, msteams, feishu, googlechat, irc, line,
  mattermost, nextcloud-talk, nostr, tlon, twitch, zalo, zalouser,
  synology-chat, clickclack, buzz, raft, reef …). Catalog:
  `docs/channels/index.md`.
- Voice telephony plugin: `extensions/voice-call/`, `docs/plugins/voice-call.md`.

### 2.5 Webhooks / hooks / automation
- Webhook plugin: `extensions/webhooks/`, ingress guards in
  `src/plugin-sdk/webhook-ingress.test.ts`, `src/plugin-sdk/webhook-memory-guards.ts`.
- Gmail Pub/Sub: `docs/cli/webhooks.md`; internal hooks `src/hooks/`
  (`gmail.ts`, `gmail-watcher.ts`), `docs/automation/hooks.md`.
- Scheduler: `src/cron/` (job store, service, isolated-agent runs, delivery),
  `docs/automation/cron-jobs.md`.
- Background task ledger: `src/tasks/`, `docs/automation/tasks.md`.
- Standing orders / auto-reply: `src/auto-reply/`, `docs/automation/standing-orders.md`,
  `docs/concepts/standing-intents.md`.

### 2.6 Boards / projects / workboard (control-ui)
- Board store/layout/tabs/widgets: `src/boards/board-store.ts:21-65`,
  `src/boards/board-layout.ts:17,30,39`, `src/boards/sqlite-board-store.ts:377`,
  `src/boards/board-capabilities.ts` (grants, netOrigins, tools).
- Workboard plugin (cards + worker dispatch): `extensions/workboard/openclaw.plugin.json`,
  `packages/workboard-contract/src/index.ts`, `docs/cli/workboard.md`.
- Project registry + clone: `src/projects/project-registry.ts:281`,
  `src/projects/project-clone.ts`.
- Control-UI tools: `src/agents/tools/dashboard-tool.ts`,
  `src/agents/tools/screen-tool.ts`, `src/canvas/widget-tool.ts:263,302`
  (`show_widget`), `docs/tools/show-widget.md`, `docs/tools/screen.md`,
  `docs/web/control-ui.md`, `docs/web/dashboards.md`.
- Control UI is a Vite + Lit SPA served by the Gateway
  (`docs/web/control-ui.md`, `src/infra/control-ui-assets.fs.runtime.ts`).

### 2.7 Skills / plugins
- Skill runtime/library/workshop: `src/skills/` (subdirs `library`, `lifecycle`,
  `loading`, `security`, `workshop`), `src/skills/library/service.ts:75,370`.
- Plugin SDK + install sources: `src/plugins/`, `src/plugin-sdk/`,
  `packages/plugin-sdk/`, `docs/plugins/plugin-inventory.md`,
  `docs/tools/plugin.md`, `docs/plugins/manage-plugins.md`.
- ~162 extensions total in `extensions/`; `skills/` ships 48 skill packs
  (e.g. `skills/control-ui`, `skills/clawhub`, `skills/coding-agent`).

### 2.8 Sessions / memory / context
- Sessions store + events + search: `src/sessions/` (`session-id.ts:3`,
  `session-state-events.ts`), `docs/cli/sessions.md`, `docs/concepts/session.md`,
  `docs/concepts/session-search.md`, `docs/concepts/session-tool.md`.
- Subagents / multi-agent / swarm: `docs/tools/subagents.md`, `docs/tools/swarm.md`,
  `src/agents/tools/agents-list-tool.ts`, `agents-wait-tool.ts`.
- Memory: markdown workspace (`USER.md`, `MEMORY.md`, daily notes) in
  `docs/concepts/memory.md`; providers `extensions/memory-core`,
  `extensions/memory-lancedb`, `extensions/memory-wiki`; `docs/concepts/memory-*.md`.
- Context engine/compaction: `src/context-engine/`, `docs/concepts/context-engine.md`.

### 2.9 Reports / audit
- Audit ledger: `src/audit/` (event store, writer, identity, decision receipts),
  `docs/cli/audit.md`.
- Logbook screen journal: `extensions/logbook/`.
- Diagnostics/telemetry: `extensions/diagnostics-otel`,
  `extensions/diagnostics-prometheus`, `docs/gateway/telemetry.md`.

### 2.10 Runtime / device / fleet
- Container fleet cells: `src/fleet/` (registry, containers, service, backup),
  `docs/cli/fleet.md`.
- Worker runtime (cloud/paired node): `src/worker/`, `docs/cli/worker.md`.
- Node host capabilities (audio, camera, images, location, media playback,
  presence, talk, voice wake): `docs/nodes/*`, `src/node-host/`.

### 2.11 IDE / coding / media / web
- ACP external harnesses: `src/acp/`, `extensions/acpx`, `extensions/codex`,
  `extensions/opencode`, `docs/tools/acp-agents.md`.
- Runtime tools: `exec`, `process`, `terminal`, `code_execution`
  (`docs/tools/exec.md`, `docs/tools/code-execution.md`).
- Web: `src/web-search/`, `src/web-fetch/`, `docs/tools/web.md`; many search
  provider extensions (`brave`, `exa`, `tavily`, `duckduckgo`, `searxng`, …).
- Media: `src/image-generation/`, `src/music-generation/`, `src/media-generation/`,
  `src/media-understanding/`, `src/video-generation/`, `src/realtime-transcription/`,
  `src/meeting-bot/`, `src/talk/`, `src/tts/`; `docs/tools/media-overview.md`,
  `docs/nodes/camera.md`, `docs/nodes/talk.md`.

---

## 3. Qubit mapping matrix

Legend: **BE**=backend module:line · **CLI**=family · **Voice**=registry id(s)
· **Dash**=route/plugin. Status counts are tallied in §5.

### 3.1 Gateway / device / pairing

| # | Capability | Upstream evidence | Qubit backend | CLI | Voice | Dash | Status | Proving test | Smallest fix | HW/creds |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Gateway protocol server + RPC v4 | `packages/gateway-protocol/src/version.ts:2`; `src/gateway/` | none (no server) | `gateway` (records) | `gateway` | qubit-gateway | MISSING | — | out of scope by single-event-path policy; typed `GATEWAY_NO_SERVER` already implied | no |
| 2 | Gateway frame guards (validate, don't fail connection) | protocol v4 | `voicebridge/gateway/gateway_ws.py:40` `FrameGuard` | `gateway` | `gateway` | — | IMPLEMENTED | `tests/test_gateway_frames8u.py` | — | no |
| 3 | Gateway/peer records + link state | `src/pairing/*`, `src/gateway/*` | `voicebridge/gateway_peers.py:168` `gateway_add`, `:252` `gateway_link`, `:310` `peer_add` | `gateway`, `peer` | `gateway*`, `peer*` | qubit-gateway | PARTIAL | `tests/test_gateway_peers8tf.py` | live server/link probing: `peer_link` already has `--probe`; add real WS control channel behind operator approval | creds for live peers |
| 4 | Gateway process supervision | `src/cli/daemon-cli`, `docs/cli/daemon.md` | `gateway_peers.py:282` `refuse_supervision` (`GATEWAY_NO_SUPERVISION`) | `gateway start/stop/restart/install` | `gateway_supervision_refusal` | qubit-gateway | GATED | `tests/test_gateway_peers8tf.py` | none (policy) | no |
| 5 | Device pairing + device-scoped tokens (remote) | `src/gateway/device-auth.ts`, `device-pairing-join-http.ts`, `docs/cli/devices.md` | `voicebridge/device.py` local only (`machine_id`, `enroll`) | `device` | `device`, `device_enroll` | qubit-devices | MISSING | — (local enroll has no test file cited) | add remote device token store + approve/rotate to `voicebridge/device.py`; needs gateway server first | signing key |
| 6 | Node pairing (remote node host) | `src/gateway/node-pairing-auto-approve.ts:88`, `docs/nodes/*` | none | — | — | — | MISSING | — | new `voicebridge/node_host.py`; large | node host |
| 7 | Pairing codes (one-time, hashed, scoped grants) | `src/pairing/pairing-store.ts:33`, `setup-code.ts:402` | `voicebridge/pairing.py:166` `create`, `:210` `approve` | `pairing` | `pairing_*` | qubit-gateway | IMPLEMENTED | `tests/test_pairing8tf.py` | — | no |
| 8 | Device setup code / QR / join URL / channel DM pairing | `src/pairing/setup-code.ts`, `src/channels/pairing`, `docs/channels/pairing.md` | `pairing.py` local codes only | — | — | — | MISSING | — | add setup-code encode + `pair accept` to `pairing.py` | no |

### 3.2 Browser

| # | Capability | Upstream evidence | Qubit backend | CLI | Voice | Dash | Status | Proving test | Smallest fix | HW/creds |
|---|---|---|---|---|---|---|---|---|---|---|
| 9 | Tabs/read/find/activate/open/navigate/fill/click/close over loopback CDP | `extensions/browser/`, `docs/tools/browser.md` | `voicebridge/browser_tabs.py:181,357,546` (`cdp.py`) | `browser` | `browser_*` | qubit-devices | IMPLEMENTED | `tests/test_browser_tabs8u.py` | — | running Chromium w/ CDP |
| 10 | Browser profiles + dedicated browser lifecycle + close-profile | `extensions/browser/browser-profiles.ts`, `docs/cli/browser.md` | `voicebridge/browser.py:28` status, `:42` `refuse_close_profile` (`BROWSER_CLOSE_REFUSED`) | `browser close-profile` | `browser_close_refusal` | qubit-devices | GATED | `tests/test_browser8tf.py` | profile list/launch would need a Qubit-owned dedicated profile dir; policy currently refuses to touch user profiles | no |
| 11 | Browser snapshot/screenshot/state emulation (visual refs) | `extensions/browser/`, `docs/tools/browser.md` | `browser_tabs.read/find` return text only | — | — | — | MISSING | — | add bounded screenshot via `cdp.WsSession.call("Page.captureScreenshot")` in new `browser_tabs.screenshot` | no |
| 12 | Remote CDP via node host / Chrome extension attach | `docs/tools/browser-wsl2-windows-remote-cdp...`, `extensions/browser/chrome-extension/` | none | — | — | — | MISSING | — | depends on node host (#6) | remote host |

### 3.3 Desktop / computer-use

| # | Capability | Upstream evidence | Qubit backend | CLI | Voice | Dash | Status | Proving test | Smallest fix | HW/creds |
|---|---|---|---|---|---|---|---|---|---|---|
| 13 | Real desktop control (Peekaboo/CUA) | `docs/nodes/computer-use.md`, `extensions/cua-computer/`, `src/worker/computer-runtime.ts` | `voicebridge/computer_use.py:70` `status`, `:130` `refuse_install` (`COMPUTER_USE_NO_DRIVER`); control `DISABLED` | `computer-use` | `computer_status/doctor/permissions/install_refusal` | qubit-devices | GATED | `tests/test_computer_use8tf.py` | implement 8V accessibility actuator; needs AT-SPI/hardware | desktop session, AT-SPI |
| 14 | Supervised ghost action framework (screenshot/key/cursor/window) | `src/agents/tools/computer-tool.ts` | `voicebridge/ghost.py:71` disabled by default; specs/gate/grants | `ghost` | `ghost_*` | qubit-ops | GATED | `tests/test_desktop_parity8u.py` (fixture executors) | inject a real AT-SPI executor into `GhostRunner`; framework is real, actuator absent | desktop session |
| 15 | `screen` tool (arrange Control UI) | `src/agents/tools/screen-tool.ts`, `docs/tools/screen.md` | none | — | — | — | MISSING | — | N/A (Qubit UI is not a multi-pane control UI) | no |
| 16 | Screen snapshot work journal (Logbook) | `extensions/logbook/` | none | — | — | — | MISSING | — | new periodic snapshot service; needs desktop session + vision model | desktop + vision |
| 17 | CUA browser family (`get_browser_state`, etc.) | `docs/nodes/computer-use.md` | none | — | — | — | MISSING | — | depends on #13 | desktop |

### 3.4 Messaging / channels

| # | Capability | Upstream evidence | Qubit backend | CLI | Voice | Dash | Status | Proving test | Smallest fix | HW/creds |
|---|---|---|---|---|---|---|---|---|---|---|
| 18 | Messaging accounts + draft/approve/send + inbox (wa, wa-cloud, slack) | `whatsapp/whatsapp-cloud/slack` in `docs/channels/` | `voicebridge/messaging.py:53` `PROVIDERS`, `:517` `send` | `msg` | `msg_*` | qubit-gateway | IMPLEMENTED | `tests/test_messaging8tf.py` | — | provider tokens |
| 19 | Broad channel set (~29 plugins) | `docs/channels/index.md`, `extensions/{discord,telegram,signal,imessage,matrix,...}` | none beyond §18 | — | — | — | MISSING | — | add per-channel adapters to `messaging.PROVIDERS` + transports; large | per-channel creds |
| 20 | Live inbound channel ingress/listeners | `src/channels/` | `messaging.record_inbound` (`:637`) is a storage call, no listener | — | — | — | MISSING | — | add a receive loop per channel; needs gateway | creds |
| 21 | Channel account pairing / DM access control | `src/channels/direct-dm*.ts`, `docs/channels/pairing.md` | `pairing.py` + `messaging.account_add` partial | `pairing`, `msg account-*` | `msg_account*` | — | PARTIAL | `tests/test_pairing8tf.py` | bind pairing to a channel account scope end-to-end | creds |
| 22 | Read-only communications monitor (Gmail/IG/Messenger via CDP) | (Qubit-side capability; OpenClaw has channel ingress instead) | `voicebridge/communications/` (adapters, monitor, reports, store) | `monitor` | `monitor` | qubit-reports | IMPLEMENTED | `tests/test_communications_monitor.py` | — | CDP + logged-in profiles |

### 3.5 Webhooks

| # | Capability | Upstream evidence | Qubit backend | CLI | Voice | Dash | Status | Proving test | Smallest fix | HW/creds |
|---|---|---|---|---|---|---|---|---|---|---|
| 23 | Webhook subscription CRUD + HMAC verify/replay/freshness | `extensions/webhooks/`, `src/plugin-sdk/webhook-ingress*` | `voicebridge/webhooks.py:211` `add`, `:364` `verify` | `webhook` | `webhook_add/remove/show/test` | qubit-ops | IMPLEMENTED | `tests/test_webhooks8tf.py` | — | signing secret |
| 24 | Inbound HTTP webhook delivery endpoint | `src/plugin-sdk/webhook-ingress.ts` | no route/handler; `verify` must be called by an external receiver | — | `guard_webhook_verify` (refuses spoken bytes) | — | MISSING | — | mount `POST /hooks/<sub_id>` in `webui.py` calling `webhooks.verify(record_inbox=True)` | secret |
| 25 | Gmail Pub/Sub transport | `docs/cli/webhooks.md`, `src/hooks/gmail-watcher.ts` | none | — | — | — | MISSING | — | operator-run `gog` integration; out of scope for local-only | gcloud creds |

### 3.6 Schedules / automation / hooks

| # | Capability | Upstream evidence | Qubit backend | CLI | Voice | Dash | Status | Proving test | Smallest fix | HW/creds |
|---|---|---|---|---|---|---|---|---|---|---|
| 26 | Scheduled job store CRUD + `run-due` | `src/cron/schedule.ts`, `docs/automation/cron-jobs.md` | `voicebridge/schedules.py:289` `create`, `:472` `run_due` | `schedules` | `schedules_*` | qubit-ops | IMPLEMENTED | `tests/test_schedules8u.py` | — | no |
| 27 | Autonomous scheduler (fires in-process, catch-up) | `src/cron/service.ts`, `docs/automation/cron-jobs.md` | none; docstring `schedules.py:10` says no timer/daemon | — | — | — | MISSING | — | add a bounded loop in the existing `monitor` service that calls `schedules.run_due` | no |
| 28 | Delivery of schedule output to channel/webhook | `src/cron/delivery*.ts` | none | — | — | — | MISSING | — | call `messaging.send`/`webhooks` from `schedules._default_executor` | creds |
| 29 | Hooks registry (declare/test/fire/doctor) | `src/hooks/`, `docs/automation/hooks.md` | `voicebridge/hooks.py:170` `declare`, `:238` `fire` (disarmed; no auto-exec) | `hooks` | `hooks_*` | qubit-ops | PARTIAL | `tests/test_hooks8tf.py` | event emissions from core lifecycle + hook dispatch; shell stays disarmed by policy | no |
| 30 | Background task ledger | `src/tasks/`, `docs/automation/tasks.md` | none (opencode runs + schedule runs hold per-run history) | — | — | — | MISSING | — | add a unified task ledger over `opencode.runs` + `schedules.runs` | no |
| 31 | Auto-reply / flows / standing orders / heartbeat | `src/auto-reply/`, `src/flows/`, `src/cron/heartbeat-task.ts` | none | `kanban heartbeat` delegates/refuses | — | — | MISSING | — | new module; large | no |

### 3.7 Projects / boards / control-ui

| # | Capability | Upstream evidence | Qubit backend | CLI | Voice | Dash | Status | Proving test | Smallest fix | HW/creds |
|---|---|---|---|---|---|---|---|---|---|---|
| 32 | Project registry (named multi-folder workspaces) | `src/projects/project-registry.ts:281` | `voicebridge/projects.py:244` `create` | `project` | `projects_*` | qubit-boards | IMPLEMENTED | `tests/test_projects8tf.py` | — | no |
| 33 | Project git clone/checkout | `src/projects/project-clone.ts` | none | — | — | — | MISSING | — | add `project clone` calling `git` via brokered actions | network |
| 34 | Kanban task lifecycle | `extensions/workboard/`, `packages/workboard-contract/` | `voicebridge/kanban.py:224` `init`, `:316` `create`, `:452` `_transition` | `kanban` | `kanban_*` | qubit-boards | IMPLEMENTED | `tests/test_kanban8tf.py` | — | no |
| 35 | Board canvas: widgets (text kinds), layout, roles, snapshots | `src/boards/board-store.ts:21-65`, `board-layout.ts` | `kanban.py:1003` `board_open`, `:1096` `widget_add`, `:993` `_canvas_guard` | `kanban widget-*`, `layout-*`, `role-*`, `snapshots` | `kanban_widget_*`, `kanban_layout_show` | qubit-boards | IMPLEMENTED | `tests/test_board_canvas8u.py` | — (but see #36) | no |
| 36 | Control-UI session boards + tabs + sandboxed HTML/SVG/plugin widgets + `show_widget` | `src/boards/board-layout.ts:17,30,39`; `src/canvas/widget-tool.ts:263,302`; `docs/tools/show-widget.md` | none (Qubit widgets are 4 text kinds, one global canvas, no tabs, no plugin kinds, no grants/netOrigins) | — | — | — | MISSING | — | extend `kanban` canvas with `tabs` table + content-kind + declared grants, then a dashboard renderer | no |
| 37 | Workboard dispatch + worker runs | `extensions/workboard/`, `docs/cli/workboard.md` | `kanban.py:786` `refuse_delegated` (`KANBAN_DELEGATED`) | `kanban dispatch/swarm/daemon/watch` | — | — | GATED | `tests/test_kanban8tf.py` | none (policy: no swarm/daemon) | no |

### 3.8 Skills / plugins

| # | Capability | Upstream evidence | Qubit backend | CLI | Voice | Dash | Status | Proving test | Smallest fix | HW/creds |
|---|---|---|---|---|---|---|---|---|---|---|
| 38 | Skill manifest install/enable/disable/remove/rollback (local reviewed only) | `src/skills/`, `src/clawhub` | `voicebridge/plugins.py:77-145`; `voicebridge/verified_skills/` | `skills` | `skills_*`, `plugins_install` | qubit-ops | IMPLEMENTED | `tests/test_plugins8tf.py` | — | no |
| 39 | Remote plugin/skill install (ClawHub/npm/git/archive) | `docs/tools/plugin.md`, `docs/plugins/manage-plugins.md` | `plugins.py:108` `refuse_remote` | `skills install` (remote id typed-refused), `plugins_remote` | `plugins_remote_refusal` | qubit-ops | GATED | `tests/test_plugins8tf.py` | none (pinned-source policy) | no |
| 40 | Plugin runtime extensibility (tools/channels/providers/hooks) | `src/plugin-sdk/`, `docs/plugins/architecture.md` | none | — | — | — | MISSING | — | a real plugin host is a large architectural addition | no |
| 41 | Skill workshop / authoring / self-learning | `skills/skill-creator`, `docs/tools/skill-workshop.md` | none | — | — | — | MISSING | — | new authoring workflow; overlaps verified skills | no |
| 42 | Verified-skills learning ledger (Qubit-only) | (no upstream equivalent) | `voicebridge/verified_skills/` | `verified-skills` | `verified_*` | qubit-memory | IMPLEMENTED | `tests/test_packet8p.py` | — | no |

### 3.9 Sessions / memory

| # | Capability | Upstream evidence | Qubit backend | CLI | Voice | Dash | Status | Proving test | Smallest fix | HW/creds |
|---|---|---|---|---|---|---|---|---|---|---|
| 43 | Session list from receipts | `docs/cli/sessions.md` | receipt reader behind `sessions list` | `sessions` | `sessions` | hermes-web SessionsPage | PARTIAL | `tests/test_owner_session8tf.py` | — (list only) | no |
| 44 | Session create/resume/archive/delete/rename/search/subagents/steering | `src/sessions/`, `docs/tools/subagents.md` | none | — | — | partial pages | MISSING | — | add archive/delete/rename to receipt store; subagents are large | no |
| 45 | Journal + named checkpoints | (Qubit-owned) | `voicebridge/memory/journal.py`, checkpoints | `checkpoints` | `checkpoints_*` | qubit-reports | IMPLEMENTED | `tests/test_checkpoints8tf.py` | — | no |
| 46 | Memory store + search + remember/forget | `extensions/memory-core`, `docs/concepts/memory.md` | `voicebridge/memory/store.py:97` `MemoryStore` | `memory` | `memory_search/remember/forget` | qubit-memory | IMPLEMENTED | `tests/test_packet5b.py`, `test_packet8m.py` | — | no |
| 47 | Semantic belief memory + journal chain | (Qubit-owned; upstream has no equivalent) | `voicebridge/memory/semantic.py:530`, `journal.py:113` | `memory belief-*`, `journal-*` | `memory_*` | qubit-memory | IMPLEMENTED | `tests/test_packet5b.py` | — | no |
| 48 | Markdown workspace memory / dreaming / external providers (LanceDB/Wiki/Honcho) | `docs/concepts/memory-builtin.md`, `extensions/memory-*` | none (different model) | — | — | — | MISSING | — | provider adapters would be new; cloud sync refused by policy | provider creds |

### 3.10 Reports / audit

| # | Capability | Upstream evidence | Qubit backend | CLI | Voice | Dash | Status | Proving test | Smallest fix | HW/creds |
|---|---|---|---|---|---|---|---|---|---|---|
| 49 | Activity/audit ledger + report bundles | `src/audit/`, `extensions/logbook/` | `voicebridge/communications/reports.py`, `webui.ActivityFeed` | `monitor`, receipts | `reports_list` | qubit-reports | IMPLEMENTED | `tests/test_report_bundles.py`, `test_webui_reports.py` | — | no |
| 50 | Analytics / usage | `src/analytics` equivalents in `src/audit` | `hermes_compat.h_analytics` | — | — | AnalyticsPage | PARTIAL | `tests/test_webui_reports.py` | back analytics with real token/cost accounting | no |
| 51 | Logbook screen work-journal | `extensions/logbook/` | none | — | — | — | MISSING | — | periodic screen capture + vision summary | desktop+vision |
| 52 | Immutable execution-identity receipts | `src/audit/execution-identity-*.ts` | action receipts + approval journal (partial) | `actions receipts`, `opencode receipts` | `actions_receipts` | qubit-reports | PARTIAL | `tests/test_packet8f.py` | extend receipt schema with run-correlation/identity | no |

### 3.11 Runtime / device / nodes

| # | Capability | Upstream evidence | Qubit backend | CLI | Voice | Dash | Status | Proving test | Smallest fix | HW/creds |
|---|---|---|---|---|---|---|---|---|---|---|
| 53 | Pinned local runtime install/status/rollback (opencode-binary) | `src/fleet/`, `docs/cli/fleet.md` (Qubit reframes as confined runtime) | `voicebridge/runtime_images.py:46` `REGISTRY`, `:147` `images`, `:235` `install` | `runtime` | `runtime_*` | qubit-runtime | IMPLEMENTED | `tests/test_runtime_images8u.py` | — | staged binary |
| 54 | Containerized runtime / fleet multi-tenant cells / cloud workers | `src/fleet/`, `src/worker/`, `docs/cli/worker.md` | none; Docker forbidden by stack contract | — | — | — | MISSING | — | out of scope (no Docker/K8s); document as policy gap | Docker/host |
| 55 | Device capability inventory (browser/desktop/IDE/vision/runtime) | `docs/nodes/` | `voicebridge/runtime_inventory.py:141` | `runtime`, `status` | `runtime_status` | qubit-devices | PARTIAL | `tests/test_runtime_tiles.py` | add node-host-derived capabilities once #6 exists | no |
| 56 | Paired node host (screen/audio/camera/media playback/presence) | `src/node-host/`, `docs/nodes/*` | none | — | — | — | MISSING | — | new node host role; large | device |

### 3.12 IDE / coding

| # | Capability | Upstream evidence | Qubit backend | CLI | Voice | Dash | Status | Proving test | Smallest fix | HW/creds |
|---|---|---|---|---|---|---|---|---|---|---|
| 57 | Supervised coding runs (opencode) preview/hash/execute/receipts | `extensions/opencode`, `docs/tools/code-execution.md` | `voicebridge/opencode/runner.py:31`, `transport.py`, `confinement.py` | `opencode` | `opencode_*` | qubit-ops | IMPLEMENTED | `tests/test_opencode_voice8u.py`, `test_packet8r.py` | — | pinned opencode + sandbox |
| 58 | ACP external harnesses (Claude Code/Cursor/Gemini/Codex/Droid) | `src/acp/`, `extensions/{acpx,codex}`, `docs/tools/acp-agents.md` | `voicebridge/acp_lsp.py:124` `acp_refuse_serve`, `:135` `acp_refuse_accept_hooks`; only prints config snippets | `acp` | `acp`, `acp_*_refusal` | — | GATED | `tests/test_acp_lsp8tf.py` | implement one ACP client behind the broker (e.g. Claude Code) if policy allows | harness binaries |
| 59 | LSP layer (clients, lifecycle) | `src/acp` / tooling | `voicebridge/acp_lsp.py:146` `lsp_list`, `:192` `lsp_restart` (`NO_CLIENTS`), `:200` `lsp_refuse_install` | `lsp` | `lsp`, `lsp_restart`, `lsp_install_refusal` | — | GATED | `tests/test_acp_lsp8tf.py` | surface real diagnostics from within supervised opencode runs | server binaries |
| 60 | IDE launcher (open workspace in editor) | `docs/tools/` (open-only) | `voicebridge/ide.py:168` `open_editor` | `ide` | `ide_status/open` | qubit-devices | IMPLEMENTED | `tests/test_files_ide8u.py` | — | desktop session |
| 61 | Files read/list/inspect (read-only) | `docs/tools/` (`read`) | `voicebridge/files.py:152` `list_dir`, `:188` `read_file`, `:222` `inspect` | `files` | `files_*` | hermes FilesPage | IMPLEMENTED | `tests/test_files_ide8u.py` | — | no |
| 62 | File write / edit / apply_patch | `docs/tools/apply-patch.md` | none (read-only by design; writes flow through brokered actions/opencode) | — | — | — | MISSING | — | expose brokered `apply_patch` action if desired | no |

### 3.13 Web / media / voice

| # | Capability | Upstream evidence | Qubit backend | CLI | Voice | Dash | Status | Proving test | Smallest fix | HW/creds |
|---|---|---|---|---|---|---|---|---|---|---|
| 63 | `web_search` / `x_search` / `web_fetch` | `src/web-search/`, `src/web-fetch/`, `docs/tools/web.md` | none (only `websites.py` integration onboarding/probe, not agent tools) | `websites` | `websites_*` | — | MISSING | — | add a brokered `web_fetch`/search adapter returning bounded text | provider creds/network |
| 64 | Image / music / video generation | `src/image-generation/`, `src/music-generation/`, `src/video-generation/` | none | — | — | — | MISSING | — | provider adapters; new | provider creds |
| 65 | Media understanding beyond single-image vision | `src/media-understanding/`, `docs/nodes/camera.md` | `voicebridge/vision.py:301` `describe` only | `vision` | `vision_describe/status` | qubit-devices | PARTIAL | `tests/test_vision8tf.py` | multi-attachment/audio understanding; new | vision model |
| 66 | Streaming ASR/TTS + barge-in | `src/realtime-transcription/`, `src/talk/`, `docs/nodes/talk.md` | `voicebridge/asr/`, `tts/`, `audio/vad.py` | `asr-test`, `tts-test`, `mic-test` | `asr_test`, `tts_test`, `mic_test` | Talk route | IMPLEMENTED | `tests/test_barge_stream.py`, `test_packet4a.py` | — | mic/speaker |
| 67 | Realtime transcription / voice call / meeting bot | `src/meeting-bot/`, `extensions/voice-call/`, `docs/plugins/voice-call.md` | none | — | — | — | MISSING | — | new telephony/meeting adapters; large | telephony creds |
| 68 | Voice command surface (registry + typed guards) | (Qubit-owned) | `voicebridge/voice_commands.py:2992` `REGISTRY`, `:5218` `VOICE_GUARDS` | `voice` | 333 entries / 14 guards | qubit-ops | IMPLEMENTED | `tests/test_voice_commands8tf.py` | — | no |

### 3.14 Dashboard / control-ui

| # | Capability | Upstream evidence | Qubit backend | CLI | Voice | Dash | Status | Proving test | Smallest fix | HW/creds |
|---|---|---|---|---|---|---|---|---|---|---|
| 69 | Dashboard compat routes + operations console with real forms/confirmations | `docs/web/control-ui.md` | `voicebridge/hermes_compat.py:1214` `ROUTES`, `dashboard_ops.py:132` `catalog` | all families | all | 92 routes + 10 plugins | PARTIAL | `tests/test_dashboard_ops8tf.py`, `test_hermes_parity8tf.py` | broaden beyond the vendored Hermes pages where Qubit has no page (e.g. gateway, devices) | no |
| 70 | Control-UI multi-session dashboards, board tabs, plugin widgets | `docs/web/dashboards.md`, `src/canvas/widget-tool.ts` | none | — | — | — | MISSING | — | see #36 | no |
| 71 | Control-UI panels: terminal / browser / desktop / side chat | `docs/web/control-ui.md` | webui has Talk/Activity/Reports; no terminal/browser/desktop panes | — | — | — | MISSING | — | add panels over existing ops; terminal panel needs the brokered exec path | no |
| 72 | Brand/theme overlay | (Qubit-owned) | `frontend/hermes-qubit/qubit-theme.json`, qubit-brand | — | — | `/api/dashboard/themes` | IMPLEMENTED | `tests/test_themes8tf.py` | — | no |

---

## 4. Integrity / honesty findings

These matter because they can make the repo look more complete than it is.

1. **`capability_catalog.py` treats `MISSING` as `IMPLEMENTED`.**
   `voicebridge/capability_catalog.py:24` defines
   `_IMPLEMENTED_VERDICTS = {"IMPLEMENTED", "REUSE", "ADAPT", "MISSING"}` and
   `_state_of` maps any of those to `"IMPLEMENTED"` (`:27-31`). Today the two
   `MISSING` rows in `docs/MATRIX_8T_F.json` are *Qubit-owned* rows
   (`Q-voice`, `Q-broker`) whose upstream is `none`, so the semantics are
   "not inherited from upstream" rather than "gap". Still, this conflates the
   two meanings: a future genuine gap row entered as `MISSING` would be
   reported as IMPLEMENTED by `qubit capability check`. Recommend splitting the
   verdict (`NOT_UPSTREAM` vs `MISSING`).

2. **The catalog is entirely self-affirming.** `PARITY_HERMES.json` has 68/68
   rows `IMPLEMENTED`; `MATRIX_8T_F.json` has 35 rows all mapped to
   `IMPLEMENTED` by the module. `tests/test_capability_catalog.py` asserts
   "every row is IMPLEMENTED", so the test cannot fail on a real gap.
   `tests/test_native_equivalents8u.py` only checks that each claimed
   CLI path parses, each voice id exists, and each cited test file exists — it
   does not execute the capability. These are schema/presence tests, not
   behavior proofs.

3. **"IMPLEMENTED" in the docs often means "safe equivalent exists", not
   "upstream capability exists".** E.g. `docs/NATIVE_EQUIVALENTS_8U.md`
   N8U-DOCKER marks `IMPLEMENTED` while stating "Docker is forbidden … container
   deployment needs an operator-owned Dockerfile outside this repo";
   N8U-DESKTOP marks `IMPLEMENTED` while stating "Raw desktop control stays
   DISABLED (no actuators by policy)". So the matrix's IMPLEMENTED is not
   comparable to a straightforward feature-parity count. This audit separates
   those into GATED and MISSING/PARTIAL.

4. **Dashboard/route counts are not implementation.** The 92 compat routes and
   10 overlay plugins are real, but many routes are typed gates or reuse
   vendored pages; the ops console is the only genuinely broad Qubit dashboard
   surface. This audit counts routes only as covered by the capability rows.

5. **Everything I marked IMPLEMENTED has a passing behavior test.** The 249
   hermetic tests above passed; the implementations read (not just the docs)
   contain real stores, real HTTP transports (credential-gated), real CDP, real
   SQLite, and real typed refusals. The gap is breadth and automation, not
   fakeness in the implemented subset.

---

## 5. Counts

Capability rows: **72**

| Status | Count | Rows |
|---|---|---|
| IMPLEMENTED | 23 | 2,7,9,18,22,23,26,32,34,35,38,42,45,46,47,49,53,57,60,61,66,68,72 |
| PARTIAL | 9 | 3,21,29,43,50,52,55,65,69 |
| GATED / REFUSED | 8 | 4,10,13,14,37,39,58,59 |
| MISSING | 32 | 1,5,6,8,11,12,15,16,17,19,20,24,25,27,28,30,31,33,36,40,41,44,48,51,54,56,62,63,64,67,70,71 |

Cross-check: 23 + 9 + 8 + 32 = 72.

By the OpenClaw-generic domains the task highlights (the capabilities OpenClaw
has that Hermes does not): most are MISSING or GATED on Qubit — control-UI
boards/tabs/widgets (#36, #70, #71), gateway enrollment/peers (#1, #3, #5,
#6, #8), runtime images/fleet (#53 partial vs #54 missing), browser profiles
(#10), desktop computer-use (#13, #14, #17).

---

## 6. Ranked top-15 gap list

Ranked by (user-visible capability value ÷ smallest honest fix). "HW/creds"
says whether the fix can be completed without hardware/credentials; if a
credential or hardware session is required, a typed-unavailable state is
acceptable meanwhile.

1. **Control-UI session boards + tabs + sandboxed widgets + `show_widget`**
   (MISSING, #36/#70). Highest-visibility OpenClaw-only capability Qubit lacks.
   Fix: `voicebridge/kanban.py` — add a `board_tabs` table and
   `widget_add(content_kind=..., declared_grants=...)`; extend `board_open`
   (`kanban.py:1003`); render in `frontend/hermes-qubit/plugins/qubit-boards`.
   HW/creds: no.
2. **Autonomous scheduler** (MISSING, #27). Qubit jobs never fire by
   themselves; OpenClaw runs in-process with catch-up. Fix: a bounded loop in
   the existing `monitor` service calling `voicebridge/schedules.py:472`
   `run_due`, gated by the same e-stop. HW/creds: no.
3. **Inbound channel breadth (Discord/Telegram/Signal/iMessage/Matrix/Teams…)**
   (MISSING, #19). Fix: extend `voicebridge/messaging.py:53` `PROVIDERS` and
   `_transport` (`:466`) one adapter at a time. HW/creds: per-channel tokens
   (typed unavailable acceptable).
4. **Inbound HTTP webhook delivery endpoint** (MISSING, #24). Qubit can verify
   HMAC but nothing receives deliveries. Fix: mount `POST /hooks/<sub_id>` in
   `voicebridge/webui.py` calling `webhooks.verify(record_inbox=True)`
   (`webhooks.py:364`). HW/creds: signing secret.
5. **Browser screenshots/snapshots** (MISSING, #11). Qubit reads text but has no
   visual evidence. Fix: `voicebridge/browser_tabs.py` new `screenshot` using
   `cdp.WsSession.call("Page.captureScreenshot")`. HW/creds: running Chromium.
6. **Real desktop actuator (accessibility APIs)** (GATED, #13/#14). Framework
   (`ghost.py`) is real; only the executor is simulated. Fix: inject an AT-SPI
   executor into `GhostRunner` (`ghost.py:1337`). HW/creds: graphical session +
   AT-SPI; typed `NO_DESKTOP` meanwhile.
7. **Paired node host** (MISSING, #6/#56). Blocks remote CDP, remote node
   capabilities, device pairing. Fix: new `voicebridge/node_host.py`. HW/creds:
   a second device; large.
8. **Background task ledger** (MISSING, #30). Unifies opencode/schedule/action
   runs. Fix: read-model over `opencode.runs` + `schedules.runs` exposed via
   `dashboard_ops`. HW/creds: no.
9. **Session lifecycle** (MISSING, #44). `sessions` is list-only. Fix: add
   archive/delete/rename to the receipt store behind `sessions` in
   `voicebridge/cli.py`. HW/creds: no.
10. **Schedule delivery to channels/webhooks** (MISSING, #28). Fix: in
    `schedules._default_executor` (`schedules.py:446`) call `messaging.send`
    for successful runs. HW/creds: channel creds.
11. **Web search / fetch agent tools** (MISSING, #63). Qubit has website
    onboarding, not retrieval. Fix: new brokered `web_fetch` reusing
    `websites.probe_page` (`websites.py:423`) with bounded extraction.
    HW/creds: network.
12. **Provider-backed plugin runtime** (MISSING, #40). Fix: this is a large
    architectural addition (tool/channel/provider registration). HW/creds: no.
13. **Media generation** (MISSING, #64). Image/music/video generation absent.
    Fix: provider adapters in a new module behind `actions`/broker.
    HW/creds: provider creds.
14. **ACP external harness client** (GATED, #58). Qubit prints editor
    snippets and refuses serve. Fix: one `voicebridge/acp_lsp.py` client (e.g.
    Claude Code) behind the existing broker, if policy allows. HW/creds:
    harness binary.
15. **File write/apply_patch** (MISSING, #62). Read-only today. Fix: brokered
    `apply_patch` action reusing the opencode confinement path
    (`voicebridge/opencode/confinement.py`). HW/creds: no.

Not in the top 15 but explicit policy gaps worth recording: container
fleet/cloud workers (#54, Docker forbidden), Gmail Pub/Sub (#25), realtime
transcription / voice call / meeting bot (#67), logbook screen journal (#51),
and markdown/dreaming memory (#48).

---

## 7. Bottom line

Qubit's implemented subset is real and tested (249/249 targeted hermetic tests
pass) and its safety posture is deliberate: many upstream behaviors are typed
refusals rather than silent absences. The honest gap is **breadth and
autonomy**: Qubit matches OpenClaw on local browser tabs, messaging for three
providers, kanban, projects, pairing codes, webhook verification, schedules
store, single-runtime pinning, supervised opencode, files read, and voice. It
does **not** match OpenClaw's gateway server/node-host, control-UI boards and
sandboxed widgets, channel breadth, autonomous scheduler, desktop computer-use,
media generation, web retrieval, or fleet/cloud runtime. Treat the repo's
`IMPLEMENTED` catalog labels with the caveats in §4.
