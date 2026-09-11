# Qubit gap ledger

Machine-generated from `docs/QUBIT_GAP_LEDGER.*.json`. This merged report is the authoritative view; the input JSON sections are never modified.

> **COMPLETION GATE:** NOT COMPLETE while any row has status != IMPLEMENTED.

## Provenance pins

- `docs/QUBIT_GAP_LEDGER.hermes.json` — schema `qubit-gap-ledger-section/1`, source `hermes`, 418 rows
  - counts: {"by_area": {"analytics-config": 4, "audio-voice": 6, "chat-websocket": 5, "cli": 72, "cli-subcommand": 50, "config-env": 15, "cron": 13, "dashboard-auth": 11, "dashboard-page": 20, "dashboard-ui": 15, "files-fs": 18, "gateway-actions": 6, "git": 21, "local-models": 16, "mcp": 12, "memory": 5, "messaging": 11, "models": 7, "ops": 28, "profiles": 20, "provider-oauth": 6, "sessions": 15, "skills": 1
  - generated: 2026-09-12
  - method: Reconciled docs/AUDIT_HERMES_2026-09-11.md, docs/PARITY_HERMES.json, docs/HERMES_SURFACE.json, docs/INVENTORY_HERMES.json, docs/HERMES_FRONTEND_MAP.json against the CURRENT Qubit tables (voicebridge/hermes_compat.py runtime ROUTES+GATED, voicebridge/cli.py, voicebridge/voice_commands.py, voicebridge/dashboard_ops.py, frontend/hermes-web) and the pinned upstream qubit_hermes/ source. Runtime match 
  - sources_reconciled: ["docs/AUDIT_HERMES_2026-09-11.md", "docs/PARITY_HERMES.json", "docs/HERMES_SURFACE.json", "docs/INVENTORY_HERMES.json", "docs/HERMES_FRONTEND_MAP.json", "voicebridge/hermes_compat.py", "voicebridge/cli.py", "voicebridge/voice_commands.py", "voicebridge/dashboard_ops.py", "frontend/hermes-web", "qubit_hermes/hermes_cli/web_routers", "qubit_hermes/hermes_cli/web_server*.py", "qubit_hermes/hermes_cl
  - upstream_pin: 2237be355906fbe6065ce1815711eee52b2d646e
- `docs/QUBIT_GAP_LEDGER.openclaw.json` — schema `qubit-gap-ledger-section/1`, source `openclaw`, 104 rows
  - counts: {"by_area": {"automation": 10, "boards": 6, "browser": 5, "dashboard": 4, "desktop": 5, "gateway": 10, "ide": 7, "messaging": 11, "policy": 2, "reports": 6, "runtime": 9, "sessions": 10, "skills": 7, "web_media": 9, "webhooks": 3}, "by_status": {"GATED": 8, "IMPLEMENTED": 35, "MISSING": 19, "PARTIAL": 42}, "total": 104}
  - generated: 2026-09-12
  - method: Reconciled docs/AUDIT_OPENCLAW_2026-09-11.md (72 rows @v2026.9.2) and docs/AUDIT_OPENCLAW_V2026_9_4_DELTA.md (32 rows @v2026.9.4) against the CURRENT Qubit modules/tests (voicebridge/*, tests/*) and the pinned upstream tree at 3a9d69db306cd7f081e06254cb89c4bcc14a7107 (read via git show, shallow clone, no checkout). Wave-3 Qubit modules (node_host, task_ledger, media_gen, local_models, plugin_runti
  - qubit_tree: /home/trick/Projects/Repos/qubit-voicebridge @ ee7e6e78
  - sources_reconciled: ["docs/AUDIT_OPENCLAW_2026-09-11.md", "docs/AUDIT_OPENCLAW_V2026_9_4_DELTA.md", "voicebridge/*", "tests/*", "/tmp/opencode/pinned/openclaw@3a9d69db"]
  - upstream_pin: 3a9d69db306cd7f081e06254cb89c4bcc14a7107
  - upstream_tag: v2026.9.4
  - upstream_tree: /tmp/opencode/pinned/openclaw (read via git show 3a9d69db, shallow clone)
- `docs/QUBIT_GAP_LEDGER.qubit.json` — schema `qubit-gap-ledger-section/1`, source `qubit-surface`, 554 rows
  - generated_from: cli_families=81, dashboard_ops.EXTRA_OPS=34, dashboard_ops.catalog=454, hermes_compat.GATED=126, hermes_compat.ROUTES=300, voice_commands.CLI_COVERAGE=81, voice_commands.REGISTRY=420

## Counts

- Total rows: **1076**
- Open rows (status != IMPLEMENTED): **588**

### By status

- DASHBOARD_ONLY: 15
- GATED: 230
- IMPLEMENTED: 488
- MISSING: 70
- PARTIAL: 174
- UNVERIFIED: 99

### By source

- hermes: 418
- openclaw: 104
- qubit: 554

### By area

- acp: 3
- acp_client: 7
- actions: 11
- active_memory: 6
- agent: 1
- analytics-config: 4
- approvals: 1
- audio: 1
- audio-voice: 6
- audio_hw: 6
- auth_login: 1
- auth_logout: 1
- auth_status: 1
- auth_verify: 1
- automation: 10
- backup: 5
- boards: 6
- brain: 17
- branding: 3
- browser: 8
- browser_tabs: 12
- capability: 2
- channels: 7
- chat-websocket: 5
- checkpoints: 2
- cli: 72
- cli-subcommand: 50
- commands: 1
- completions: 1
- computer: 5
- config-env: 15
- config_profiles: 8
- console_guidance: 1
- cron: 13
- curator: 1
- dashboard: 4
- dashboard-auth: 11
- dashboard-page: 20
- dashboard-ui: 15
- dashboard_plugin: 10
- desktop: 5
- device: 1
- device_enroll: 1
- diagnostics: 1
- doc_extract: 3
- doctor: 1
- editor: 3
- experiments: 12
- file_transfer: 5
- files: 4
- files-fs: 18
- flags: 1
- frontier: 1
- frontier_chat: 1
- frontier_show: 1
- gateway: 17
- gateway-actions: 6
- ghost: 20
- git: 25
- hermes_gated: 71
- hermes_web_page: 19
- hooks: 9
- ide: 9
- import_agent: 2
- insights: 1
- kanban: 44
- lessons: 3
- local-models: 16
- local_models: 11
- logs: 1
- loop: 1
- lsp: 3
- mcp: 13
- media_gen: 3
- memory: 8
- messaging: 22
- mic_guidance: 1
- migrate_status: 1
- models: 7
- monitor: 1
- msg: 14
- node_host: 7
- opencode: 14
- ops: 28
- package: 2
- pairing: 4
- pause: 1
- peer: 7
- permissions: 1
- plugin_runtime: 7
- plugins_install: 1
- plugins_remote: 1
- policy: 2
- power_status: 1
- product: 7
- profiles: 20
- projects: 13
- prompt_budget: 1
- provider-oauth: 6
- proxy: 4
- question_prompt: 3
- reasoning: 1
- reports: 7
- resume: 1
- runtime: 16
- schedules: 8
- secrets: 1
- security: 1
- session_share: 5
- sessions: 32
- setup_check: 1
- skills: 27
- state: 2
- status: 1
- status-curator: 17
- sync: 5
- task_ledger: 4
- task_run: 2
- themes: 7
- tools: 13
- tui_guidance: 1
- update_status: 1
- verified: 6
- verify_release: 1
- vision_describe: 1
- vision_status: 1
- voice_unsupported: 18
- wake: 1
- web_media: 9
- webhook: 5
- webhooks: 4
- websites: 4
- webui: 1
- worker: 1
- worktree: 1

## Open rows (status != IMPLEMENTED)

### source: hermes (260 open)

#### hermes / analytics-config (2)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/config/raw` | GATED | none | GET /api/config/raw | tests/test_hermes_routes_parity8u.py |
| `api:PUT /api/config/raw` | GATED | none | PUT /api/config/raw | tests/test_hermes_routes_parity8u.py |

#### hermes / audio-voice (3)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/audio/elevenlabs/voices` | GATED | hardware | GET /api/audio/elevenlabs/voices | tests/test_hermes_readsurfaces8u.py |
| `api:POST /api/audio/tts-lease` | GATED | hardware | POST /api/audio/tts-lease | tests/test_hermes_readsurfaces8u.py |
| `api:WEBSOCKET /api/audio/speak-stream` | GATED | hardware | WEBSOCKET /api/audio/speak-stream | tests/test_hermes_readsurfaces8u.py |

#### hermes / chat-websocket (5)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:WEBSOCKET /api/console` | GATED | none | WEBSOCKET /api/console | tests/test_hermes_read_routes8u.py |
| `api:WEBSOCKET /api/events` | PARTIAL | none | WEBSOCKET /api/events | tests/test_hermes_read_routes8u.py |
| `api:WEBSOCKET /api/pty` | GATED | none | WEBSOCKET /api/pty | tests/test_hermes_read_routes8u.py |
| `api:WEBSOCKET /api/pub` | MISSING | owner | WEBSOCKET /api/pub | tests/test_hermes_read_routes8u.py |
| `api:WEBSOCKET /api/ws` | GATED | none | WEBSOCKET /api/ws | tests/test_hermes_read_routes8u.py |

#### hermes / cli (45)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `cli:backup` | PARTIAL | none | hermes backup | tests/test_parity_hermes.py |
| `cli:bundles` | MISSING | owner | hermes bundles | tests/test_parity_hermes.py |
| `cli:chat` | PARTIAL | none | hermes chat | tests/test_parity_hermes.py |
| `cli:claw` | MISSING | owner | hermes claw | tests/test_parity_hermes.py |
| `cli:config` | PARTIAL | none | hermes config | tests/test_parity_hermes.py |
| `cli:console` | MISSING | owner | hermes console | tests/test_parity_hermes.py |
| `cli:cron` | PARTIAL | none | hermes cron | tests/test_parity_hermes.py |
| `cli:curator` | PARTIAL | none | hermes curator | tests/test_parity_hermes.py |
| `cli:dashboard` | PARTIAL | none | hermes dashboard | tests/test_parity_hermes.py |
| `cli:debug` | MISSING | owner | hermes debug | tests/test_parity_hermes.py |
| `cli:desktop` | MISSING | owner | hermes desktop | tests/test_parity_hermes.py |
| `cli:doctor` | PARTIAL | none | hermes doctor | tests/test_parity_hermes.py |
| `cli:dump` | MISSING | owner | hermes dump | tests/test_parity_hermes.py |
| `cli:egress` | MISSING | owner | hermes egress | tests/test_parity_hermes.py |
| `cli:fallback` | PARTIAL | none | hermes fallback | tests/test_parity_hermes.py |
| `cli:gui` | MISSING | owner | hermes gui | tests/test_parity_hermes.py |
| `cli:insights` | MISSING | owner | hermes insights | tests/test_parity_hermes.py |
| `cli:journey` | MISSING | owner | hermes journey | tests/test_parity_hermes.py |
| `cli:learning` | PARTIAL | none | hermes learning | tests/test_parity_hermes.py |
| `cli:logs` | MISSING | owner | hermes logs | tests/test_parity_hermes.py |
| `cli:mcp` | GATED | none | hermes mcp | tests/test_parity_hermes.py |
| `cli:memory` | PARTIAL | none | hermes memory | tests/test_parity_hermes.py |
| `cli:memory-graph` | MISSING | owner | hermes memory-graph | tests/test_parity_hermes.py |
| `cli:moa` | MISSING | owner | hermes moa | tests/test_parity_hermes.py |
| `cli:model` | PARTIAL | none | hermes model | tests/test_parity_hermes.py |
| `cli:monitoring` | PARTIAL | none | hermes monitoring | tests/test_parity_hermes.py |
| `cli:pause` | MISSING | owner | hermes pause | tests/test_parity_hermes.py |
| `cli:pets` | MISSING | owner | hermes pets | tests/test_parity_hermes.py |
| `cli:plugins` | PARTIAL | none | hermes plugins | tests/test_parity_hermes.py |
| `cli:portal` | MISSING | owner | hermes portal | tests/test_parity_hermes.py |
| `cli:prompt-size` | MISSING | owner | hermes prompt-size | tests/test_parity_hermes.py |
| `cli:resume` | MISSING | owner | hermes resume | tests/test_parity_hermes.py |
| `cli:secrets` | PARTIAL | creds | hermes secrets | tests/test_parity_hermes.py |
| `cli:security` | PARTIAL | creds | hermes security | tests/test_parity_hermes.py |
| `cli:send` | PARTIAL | none | hermes send | tests/test_parity_hermes.py |
| `cli:serve` | PARTIAL | none | hermes serve | tests/test_parity_hermes.py |
| `cli:sessions` | GATED | none | hermes sessions | tests/test_parity_hermes.py |
| `cli:skills` | GATED | none | hermes skills | tests/test_parity_hermes.py |
| `cli:slack` | PARTIAL | none | hermes slack | tests/test_parity_hermes.py |
| `cli:tools` | MISSING | owner | hermes tools | tests/test_parity_hermes.py |
| `cli:uninstall` | PARTIAL | none | hermes uninstall | tests/test_parity_hermes.py |
| `cli:update` | PARTIAL | none | hermes update | tests/test_parity_hermes.py |
| `cli:whatsapp` | PARTIAL | none | hermes whatsapp | tests/test_parity_hermes.py |
| `cli:whatsapp-cloud` | PARTIAL | none | hermes whatsapp-cloud | tests/test_parity_hermes.py |
| `cli:worktree` | MISSING | owner | hermes worktree | tests/test_parity_hermes.py |

#### hermes / cli-subcommand (44)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `cli:config check` | MISSING | owner | hermes config check | tests/test_parity_hermes.py |
| `cli:config edit` | MISSING | owner | hermes config edit | tests/test_parity_hermes.py |
| `cli:config env-path` | MISSING | owner | hermes config env-path | tests/test_parity_hermes.py |
| `cli:config get` | MISSING | owner | hermes config get | tests/test_parity_hermes.py |
| `cli:config path` | MISSING | owner | hermes config path | tests/test_parity_hermes.py |
| `cli:config set` | GATED | creds | hermes config set | tests/test_parity_hermes.py |
| `cli:config show` | MISSING | owner | hermes config show | tests/test_parity_hermes.py |
| `cli:config unset` | GATED | creds | hermes config unset | tests/test_parity_hermes.py |
| `cli:cron doctor` | MISSING | owner | hermes cron doctor | tests/test_parity_hermes.py |
| `cli:cron edit` | PARTIAL | creds | hermes cron edit | tests/test_parity_hermes.py |
| `cli:cron run` | PARTIAL | creds | hermes cron run | tests/test_parity_hermes.py |
| `cli:cron status` | PARTIAL | creds | hermes cron status | tests/test_parity_hermes.py |
| `cli:cron tick` | MISSING | owner | hermes cron tick | tests/test_parity_hermes.py |
| `cli:mcp add` | GATED | creds | hermes mcp add | tests/test_parity_hermes.py |
| `cli:mcp catalog` | PARTIAL | creds | hermes mcp catalog | tests/test_parity_hermes.py |
| `cli:mcp configure` | GATED | creds | hermes mcp configure | tests/test_parity_hermes.py |
| `cli:mcp remove` | GATED | creds | hermes mcp remove | tests/test_parity_hermes.py |
| `cli:mcp test` | PARTIAL | creds | hermes mcp test | tests/test_parity_hermes.py |
| `cli:plugins capabilities` | MISSING | owner | hermes plugins capabilities | tests/test_parity_hermes.py |
| `cli:plugins doctor` | PARTIAL | creds | hermes plugins doctor | tests/test_parity_hermes.py |
| `cli:plugins enable` | PARTIAL | creds | hermes plugins enable | tests/test_parity_hermes.py |
| `cli:plugins install` | GATED | creds | hermes plugins install | tests/test_parity_hermes.py |
| `cli:plugins remove` | PARTIAL | creds | hermes plugins remove | tests/test_parity_hermes.py |
| `cli:plugins update` | GATED | creds | hermes plugins update | tests/test_parity_hermes.py |
| `cli:sessions clean-markers` | MISSING | owner | hermes sessions clean-markers | tests/test_parity_hermes.py |
| `cli:sessions delete` | GATED | creds | hermes sessions delete | tests/test_parity_hermes.py |
| `cli:sessions export` | MISSING | owner | hermes sessions export | tests/test_parity_hermes.py |
| `cli:sessions optimize-storage` | MISSING | owner | hermes sessions optimize-storage | tests/test_parity_hermes.py |
| `cli:sessions pin` | MISSING | owner | hermes sessions pin | tests/test_parity_hermes.py |
| `cli:sessions prune` | GATED | creds | hermes sessions prune | tests/test_parity_hermes.py |
| `cli:sessions rename` | GATED | creds | hermes sessions rename | tests/test_parity_hermes.py |
| `cli:skills audit` | GATED | creds | hermes skills audit | tests/test_parity_hermes.py |
| `cli:skills install` | GATED | creds | hermes skills install | tests/test_parity_hermes.py |
| `cli:skills publish` | GATED | creds | hermes skills publish | tests/test_parity_hermes.py |
| `cli:skills tap` | GATED | creds | hermes skills tap | tests/test_parity_hermes.py |
| `cli:skills update` | GATED | creds | hermes skills update | tests/test_parity_hermes.py |
| `cli:tools disable` | MISSING | owner | hermes tools disable | tests/test_parity_hermes.py |
| `cli:tools enable` | MISSING | owner | hermes tools enable | tests/test_parity_hermes.py |
| `cli:tools list` | MISSING | owner | hermes tools list | tests/test_parity_hermes.py |
| `cli:tools post-setup` | MISSING | owner | hermes tools post-setup | tests/test_parity_hermes.py |
| `cli:worktree add` | MISSING | owner | hermes worktree add | tests/test_parity_hermes.py |
| `cli:worktree list` | MISSING | owner | hermes worktree list | tests/test_parity_hermes.py |
| `cli:worktree remove` | MISSING | owner | hermes worktree remove | tests/test_parity_hermes.py |
| `cli:worktree switch` | MISSING | owner | hermes worktree switch | tests/test_parity_hermes.py |

#### hermes / config-env (9)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:DELETE /api/env` | GATED | none | DELETE /api/env | tests/test_hermes_routes_parity8u.py |
| `api:DELETE /api/providers/custom-endpoints/{endpoint_id}` | GATED | none | DELETE /api/providers/custom-endpoints/{endpoint_id} | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/env/reveal` | GATED | none | POST /api/env/reveal | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/providers/custom-endpoints` | GATED | none | POST /api/providers/custom-endpoints | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/providers/custom-endpoints/validate` | GATED | none | POST /api/providers/custom-endpoints/validate | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/providers/custom-endpoints/{endpoint_id}/activate` | GATED | none | POST /api/providers/custom-endpoints/{endpoint_id}/activate | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/providers/validate` | GATED | none | POST /api/providers/validate | tests/test_hermes_routes_parity8u.py |
| `api:PUT /api/config` | GATED | none | PUT /api/config | tests/test_hermes_routes_parity8u.py |
| `api:PUT /api/env` | GATED | none | PUT /api/env | tests/test_hermes_routes_parity8u.py |

#### hermes / cron (3)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:POST /api/cron/blueprints/instantiate` | GATED | none | POST /api/cron/blueprints/instantiate | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/cron/fire` | GATED | none | POST /api/cron/fire | tests/test_hermes_routes_parity8u.py |
| `api:PUT /api/cron/jobs/{job_id}` | GATED | none | PUT /api/cron/jobs/{job_id} | tests/test_hermes_routes_parity8u.py |

#### hermes / dashboard-auth (10)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/auth/providers` | PARTIAL | owner | GET /api/auth/providers | — |
| `api:GET /auth/callback` | MISSING | owner | GET /auth/callback | — |
| `api:GET /auth/login` | MISSING | owner | GET /auth/login | — |
| `api:GET /auth/native/authorize` | MISSING | owner | GET /auth/native/authorize | — |
| `api:GET /login` | MISSING | owner | GET /login | — |
| `api:POST /api/auth/ws-ticket` | GATED | owner | POST /api/auth/ws-ticket | — |
| `api:POST /auth/logout` | MISSING | owner | POST /auth/logout | — |
| `api:POST /auth/native/refresh` | MISSING | owner | POST /auth/native/refresh | — |
| `api:POST /auth/native/token` | MISSING | owner | POST /auth/native/token | — |
| `api:POST /auth/password-login` | MISSING | owner | POST /auth/password-login | — |

#### hermes / dashboard-page (14)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `dashboard:/analytics` | PARTIAL | owner | hermes dashboard /analytics (AnalyticsPage) | tests/test_hermes_frontend_map8u.py |
| `dashboard:/channels` | PARTIAL | owner | hermes dashboard /channels (ChannelsPage) | tests/test_hermes_frontend_map8u.py |
| `dashboard:/chat` | PARTIAL | owner | hermes dashboard /chat (ChatPage) | tests/test_hermes_frontend_map8u.py |
| `dashboard:/config` | PARTIAL | owner | hermes dashboard /config (ConfigPage) | tests/test_hermes_frontend_map8u.py |
| `dashboard:/cron` | PARTIAL | owner | hermes dashboard /cron (CronPage) | tests/test_hermes_frontend_map8u.py |
| `dashboard:/env` | PARTIAL | owner | hermes dashboard /env (EnvPage) | tests/test_hermes_frontend_map8u.py |
| `dashboard:/files` | PARTIAL | owner | hermes dashboard /files (FilesPage) | tests/test_hermes_frontend_map8u.py |
| `dashboard:/mcp` | PARTIAL | owner | hermes dashboard /mcp (McpPage) | tests/test_hermes_frontend_map8u.py |
| `dashboard:/models` | PARTIAL | owner | hermes dashboard /models (ModelsPage) | tests/test_hermes_frontend_map8u.py |
| `dashboard:/plugins` | PARTIAL | owner | hermes dashboard /plugins (PluginsPage) | tests/test_hermes_frontend_map8u.py |
| `dashboard:/profiles` | PARTIAL | owner | hermes dashboard /profiles (ProfilesPage) | tests/test_hermes_frontend_map8u.py |
| `dashboard:/profiles/new` | PARTIAL | owner | hermes dashboard /profiles/new (ProfileBuilderPage) | tests/test_hermes_frontend_map8u.py |
| `dashboard:/skills` | PARTIAL | owner | hermes dashboard /skills (SkillsPage) | tests/test_hermes_frontend_map8u.py |
| `dashboard:/system` | PARTIAL | owner | hermes dashboard /system (SystemPage) | tests/test_hermes_frontend_map8u.py |

#### hermes / dashboard-ui (5)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:POST /api/dashboard/agent-plugins/install` | GATED | owner | POST /api/dashboard/agent-plugins/install | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/dashboard/agent-plugins/{name:path}/disable` | GATED | owner | POST /api/dashboard/agent-plugins/{name:path}/disable | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/dashboard/agent-plugins/{name:path}/enable` | GATED | owner | POST /api/dashboard/agent-plugins/{name:path}/enable | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/dashboard/agent-plugins/{name:path}/update` | GATED | owner | POST /api/dashboard/agent-plugins/{name:path}/update | tests/test_hermes_routes_parity8u.py |
| `api:PUT /api/dashboard/plugin-providers` | GATED | owner | PUT /api/dashboard/plugin-providers | tests/test_hermes_routes_parity8u.py |

#### hermes / files-fs (11)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:DELETE /api/files` | GATED | none | DELETE /api/files | tests/test_hermes_readsurfaces8u.py |
| `api:GET /api/files/download` | GATED | none | GET /api/files/download | tests/test_hermes_readsurfaces8u.py |
| `api:GET /api/files/stream` | GATED | none | GET /api/files/stream | tests/test_hermes_readsurfaces8u.py |
| `api:GET /api/fs/download` | GATED | none | GET /api/fs/download | tests/test_hermes_readsurfaces8u.py |
| `api:GET /api/media` | GATED | none | GET /api/media | tests/test_hermes_readsurfaces8u.py |
| `api:HEAD /api/files/stream` | GATED | none | HEAD /api/files/stream | tests/test_hermes_readsurfaces8u.py |
| `api:POST /api/chat/image-upload` | GATED | none | POST /api/chat/image-upload | tests/test_hermes_readsurfaces8u.py |
| `api:POST /api/files/mkdir` | GATED | none | POST /api/files/mkdir | tests/test_hermes_readsurfaces8u.py |
| `api:POST /api/files/upload` | GATED | none | POST /api/files/upload | tests/test_hermes_readsurfaces8u.py |
| `api:POST /api/files/upload-stream` | GATED | none | POST /api/files/upload-stream | tests/test_hermes_readsurfaces8u.py |
| `api:POST /api/fs/write-text` | GATED | none | POST /api/fs/write-text | tests/test_hermes_readsurfaces8u.py |

#### hermes / gateway-actions (5)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/hermes/update/check` | GATED | owner | GET /api/hermes/update/check | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/hermes/update/receipt` | GATED | owner | GET /api/hermes/update/receipt | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/gateway/drain` | MISSING | owner | POST /api/gateway/drain | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/gateway/restart` | GATED | owner | POST /api/gateway/restart | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/hermes/update` | GATED | owner | POST /api/hermes/update | tests/test_hermes_routes_parity8u.py |

#### hermes / git (12)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/git/gh-auth` | GATED | none | GET /api/git/gh-auth | tests/test_hermes_readsurfaces8u.py |
| `api:GET /api/git/review/ship-info` | GATED | none | GET /api/git/review/ship-info | tests/test_hermes_readsurfaces8u.py |
| `api:POST /api/git/branch/switch` | GATED | none | POST /api/git/branch/switch | tests/test_hermes_readsurfaces8u.py |
| `api:POST /api/git/review/commit` | GATED | none | POST /api/git/review/commit | tests/test_hermes_readsurfaces8u.py |
| `api:POST /api/git/review/create-pr` | GATED | none | POST /api/git/review/create-pr | tests/test_hermes_readsurfaces8u.py |
| `api:POST /api/git/review/pr-list` | GATED | none | POST /api/git/review/pr-list | tests/test_hermes_readsurfaces8u.py |
| `api:POST /api/git/review/push` | GATED | none | POST /api/git/review/push | tests/test_hermes_readsurfaces8u.py |
| `api:POST /api/git/review/revert` | GATED | none | POST /api/git/review/revert | tests/test_hermes_readsurfaces8u.py |
| `api:POST /api/git/review/stage` | GATED | none | POST /api/git/review/stage | tests/test_hermes_readsurfaces8u.py |
| `api:POST /api/git/review/unstage` | GATED | none | POST /api/git/review/unstage | tests/test_hermes_readsurfaces8u.py |
| `api:POST /api/git/worktree/add` | GATED | none | POST /api/git/worktree/add | tests/test_hermes_readsurfaces8u.py |
| `api:POST /api/git/worktree/remove` | GATED | none | POST /api/git/worktree/remove | tests/test_hermes_readsurfaces8u.py |

#### hermes / local-models (11)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:DELETE /api/local-models/models/{model_id}` | GATED | hardware | DELETE /api/local-models/models/{model_id} | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/local-models/search` | GATED | hardware | GET /api/local-models/search | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/local-models/search/files` | GATED | hardware | GET /api/local-models/search/files | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/local-models/activate` | GATED | hardware | POST /api/local-models/activate | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/local-models/download` | GATED | hardware | POST /api/local-models/download | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/local-models/download-browsed` | GATED | hardware | POST /api/local-models/download-browsed | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/local-models/eject` | GATED | hardware | POST /api/local-models/eject | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/local-models/quickstart` | GATED | hardware | POST /api/local-models/quickstart | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/local-models/runtime/install` | GATED | hardware | POST /api/local-models/runtime/install | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/local-models/server` | GATED | hardware | POST /api/local-models/server | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/local-models/sideload` | GATED | hardware | POST /api/local-models/sideload | tests/test_hermes_routes_parity8u.py |

#### hermes / mcp (10)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:DELETE /api/mcp/oauth/flows/{flow_id}` | GATED | owner | DELETE /api/mcp/oauth/flows/{flow_id} | tests/test_hermes_routes_parity8u.py |
| `api:DELETE /api/mcp/servers/{name}` | GATED | owner | DELETE /api/mcp/servers/{name} | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/mcp/catalog` | PARTIAL | owner | GET /api/mcp/catalog | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/mcp/oauth/callback/{server_name:path}` | GATED | owner | GET /api/mcp/oauth/callback/{server_name:path} | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/mcp/oauth/flows/{flow_id}` | GATED | owner | GET /api/mcp/oauth/flows/{flow_id} | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/mcp/servers` | PARTIAL | owner | GET /api/mcp/servers | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/mcp/catalog/install` | GATED | owner | POST /api/mcp/catalog/install | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/mcp/servers` | GATED | owner | POST /api/mcp/servers | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/mcp/servers/{name}/auth` | GATED | owner | POST /api/mcp/servers/{name}/auth | tests/test_hermes_routes_parity8u.py |
| `api:PUT /api/mcp/servers` | GATED | owner | PUT /api/mcp/servers | tests/test_hermes_routes_parity8u.py |

#### hermes / memory (4)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/memory/providers/{name}/config` | PARTIAL | none | GET /api/memory/providers/{name}/config | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/memory/providers/{name}/setup` | GATED | none | POST /api/memory/providers/{name}/setup | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/memory/providers/{provider}/oauth/start` | GATED | none | POST /api/memory/providers/{provider}/oauth/start | tests/test_hermes_routes_parity8u.py |
| `api:PUT /api/memory/providers/{name}/config` | GATED | none | PUT /api/memory/providers/{name}/config | tests/test_hermes_routes_parity8u.py |

#### hermes / messaging (8)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:DELETE /api/messaging/telegram/onboarding/{pairing_id}` | GATED | creds | DELETE /api/messaging/telegram/onboarding/{pairing_id} | tests/test_hermes_routes_parity8u.py |
| `api:DELETE /api/messaging/whatsapp/onboarding/{pairing_id}` | GATED | creds | DELETE /api/messaging/whatsapp/onboarding/{pairing_id} | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/messaging/telegram/onboarding/{pairing_id}` | GATED | creds | GET /api/messaging/telegram/onboarding/{pairing_id} | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/messaging/whatsapp/onboarding/{pairing_id}` | GATED | creds | GET /api/messaging/whatsapp/onboarding/{pairing_id} | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/messaging/telegram/onboarding/start` | GATED | creds | POST /api/messaging/telegram/onboarding/start | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/messaging/telegram/onboarding/{pairing_id}/apply` | GATED | creds | POST /api/messaging/telegram/onboarding/{pairing_id}/apply | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/messaging/whatsapp/onboarding/start` | GATED | creds | POST /api/messaging/whatsapp/onboarding/start | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/messaging/whatsapp/onboarding/{pairing_id}/apply` | GATED | creds | POST /api/messaging/whatsapp/onboarding/{pairing_id}/apply | tests/test_hermes_routes_parity8u.py |

#### hermes / models (3)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/model/auxiliary` | PARTIAL | none | GET /api/model/auxiliary | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/model/moa` | PARTIAL | none | GET /api/model/moa | tests/test_hermes_routes_parity8u.py |
| `api:PUT /api/model/moa` | GATED | none | PUT /api/model/moa | tests/test_hermes_routes_parity8u.py |

#### hermes / ops (11)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:DELETE /api/credentials/pool/{provider}/{index}` | GATED | none | DELETE /api/credentials/pool/{provider}/{index} | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/credentials/pool` | GATED | none | GET /api/credentials/pool | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/memory` | PARTIAL | none | GET /api/memory | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/ops/backup/download` | GATED | none | GET /api/ops/backup/download | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/credentials/pool` | GATED | none | POST /api/credentials/pool | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/gateway/start` | GATED | none | POST /api/gateway/start | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/gateway/stop` | GATED | none | POST /api/gateway/stop | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/ops/backup` | PARTIAL | none | POST /api/ops/backup | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/ops/import` | PARTIAL | none | POST /api/ops/import | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/ops/import-upload` | GATED | none | POST /api/ops/import-upload | tests/test_hermes_routes_parity8u.py |
| `api:PUT /api/memory/provider` | PARTIAL | none | PUT /api/memory/provider | tests/test_hermes_routes_parity8u.py |

#### hermes / profiles (9)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/profiles/{name}/desktop-overlay` | GATED | none | GET /api/profiles/{name}/desktop-overlay | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/profiles/{name}/setup-command` | PARTIAL | none | GET /api/profiles/{name}/setup-command | tests/test_hermes_routes_parity8u.py |
| `api:PATCH /api/profiles/{name}` | GATED | none | PATCH /api/profiles/{name} | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/profiles` | GATED | none | POST /api/profiles | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/profiles/import` | GATED | none | POST /api/profiles/import | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/profiles/sessions/pull-requests` | GATED | none | POST /api/profiles/sessions/pull-requests | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/profiles/{name}/describe-auto` | GATED | none | POST /api/profiles/{name}/describe-auto | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/profiles/{name}/export` | GATED | none | POST /api/profiles/{name}/export | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/profiles/{name}/open-terminal` | GATED | none | POST /api/profiles/{name}/open-terminal | tests/test_hermes_routes_parity8u.py |

#### hermes / provider-oauth (6)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:DELETE /api/providers/oauth/sessions/{session_id}` | GATED | creds | DELETE /api/providers/oauth/sessions/{session_id} | tests/test_hermes_routes_parity8u.py |
| `api:DELETE /api/providers/oauth/{provider_id}` | GATED | creds | DELETE /api/providers/oauth/{provider_id} | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/providers/oauth` | GATED | creds | GET /api/providers/oauth | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/providers/oauth/{provider_id}/poll/{session_id}` | GATED | creds | GET /api/providers/oauth/{provider_id}/poll/{session_id} | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/providers/oauth/{provider_id}/start` | GATED | creds | POST /api/providers/oauth/{provider_id}/start | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/providers/oauth/{provider_id}/submit` | GATED | creds | POST /api/providers/oauth/{provider_id}/submit | tests/test_hermes_routes_parity8u.py |

#### hermes / sessions (4)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:POST /api/sessions/bulk-delete` | GATED | none | POST /api/sessions/bulk-delete | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/sessions/import` | GATED | none | POST /api/sessions/import | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/sessions/owner-backfill` | GATED | none | POST /api/sessions/owner-backfill | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/sessions/prune` | GATED | none | POST /api/sessions/prune | tests/test_hermes_routes_parity8u.py |

#### hermes / skills (11)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/skills/hub/official` | GATED | none | GET /api/skills/hub/official | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/skills/hub/preview` | GATED | none | GET /api/skills/hub/preview | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/skills/hub/scan` | GATED | none | GET /api/skills/hub/scan | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/skills/hub/search` | GATED | none | GET /api/skills/hub/search | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/skills/hub/sources` | GATED | none | GET /api/skills/hub/sources | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/skills` | GATED | none | POST /api/skills | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/skills/hub/install` | GATED | none | POST /api/skills/hub/install | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/skills/hub/uninstall` | GATED | none | POST /api/skills/hub/uninstall | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/skills/hub/update` | GATED | none | POST /api/skills/hub/update | tests/test_hermes_routes_parity8u.py |
| `api:PUT /api/skills/content` | GATED | none | PUT /api/skills/content | tests/test_hermes_routes_parity8u.py |
| `api:PUT /api/skills/toggle` | GATED | none | PUT /api/skills/toggle | tests/test_hermes_routes_parity8u.py |

#### hermes / status-curator (6)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:DELETE /api/learning/node` | GATED | none | DELETE /api/learning/node | tests/test_hermes_read_routes8u.py |
| `api:GET /api/portal` | GATED | none | GET /api/portal | tests/test_hermes_read_routes8u.py |
| `api:GET /api/ssh/ownership` | GATED | none | GET /api/ssh/ownership | tests/test_hermes_read_routes8u.py |
| `api:POST /api/curator/run` | GATED | none | POST /api/curator/run | tests/test_hermes_read_routes8u.py |
| `api:PUT /api/curator/paused` | GATED | none | PUT /api/curator/paused | tests/test_hermes_read_routes8u.py |
| `api:PUT /api/learning/node` | GATED | none | PUT /api/learning/node | tests/test_hermes_read_routes8u.py |

#### hermes / tools (9)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/tools/toolsets/{name}/config` | PARTIAL | none | GET /api/tools/toolsets/{name}/config | tests/test_hermes_routes_parity8u.py |
| `api:GET /api/tools/toolsets/{name}/models` | PARTIAL | none | GET /api/tools/toolsets/{name}/models | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/tools/computer-use/permissions/grant` | GATED | none | POST /api/tools/computer-use/permissions/grant | tests/test_hermes_routes_parity8u.py |
| `api:POST /api/tools/toolsets/{name}/post-setup` | GATED | none | POST /api/tools/toolsets/{name}/post-setup | tests/test_hermes_routes_parity8u.py |
| `api:PUT /api/tools/terminal/backend` | GATED | none | PUT /api/tools/terminal/backend | tests/test_hermes_routes_parity8u.py |
| `api:PUT /api/tools/toolsets/{name}` | GATED | none | PUT /api/tools/toolsets/{name} | tests/test_hermes_routes_parity8u.py |
| `api:PUT /api/tools/toolsets/{name}/env` | GATED | none | PUT /api/tools/toolsets/{name}/env | tests/test_hermes_routes_parity8u.py |
| `api:PUT /api/tools/toolsets/{name}/model` | GATED | none | PUT /api/tools/toolsets/{name}/model | tests/test_hermes_routes_parity8u.py |
| `api:PUT /api/tools/toolsets/{name}/provider` | GATED | none | PUT /api/tools/toolsets/{name}/provider | tests/test_hermes_routes_parity8u.py |

### source: openclaw (69 open)

#### openclaw / automation (5)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-27` | MISSING | none | Autonomous scheduler (fires in-process, catch-up) | — |
| `OC-29` | PARTIAL | none | Hooks registry (declare/test/fire/doctor) | tests/test_hooks8tf.py |
| `OC-31` | MISSING | none | Auto-reply / flows / standing orders / heartbeat | — |
| `OC-D05` | MISSING | none | QA Lab / E2E automation stack (operator flow, scenarios, reporting) | — |
| `OC-D23` | PARTIAL | none | Hooks event types, bundled hooks, authoring config | tests/test_hooks8tf.py |

#### openclaw / boards (3)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-33` | MISSING | none | Project git clone/checkout | — |
| `OC-36` | PARTIAL | none | Control-UI session boards + tabs + sandboxed HTML/SVG/plugin widgets + show_widget | tests/test_board_canvas8u.py |
| `OC-37` | GATED | none | Workboard dispatch + worker runs | tests/test_kanban8tf.py |

#### openclaw / browser (3)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-10` | GATED | none | Browser profiles + dedicated browser lifecycle + close-profile | tests/test_browser8tf.py |
| `OC-12` | MISSING | hardware | Remote CDP via node host / Chrome extension attach | — |
| `OC-D27` | PARTIAL | hardware | Browser live panel: screencast streaming with screenshot fallback; Annotate/Inspect | tests/test_browser_screenshot8u.py |

#### openclaw / dashboard (3)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-69` | PARTIAL | none | Dashboard compat routes + operations console with real forms/confirmations | tests/test_dashboard_ops8tf.py |
| `OC-70` | PARTIAL | none | Control-UI multi-session dashboards, board tabs, plugin widgets | tests/test_board_canvas8u.py |
| `OC-71` | PARTIAL | none | Control-UI panels: terminal / browser / desktop / side chat | tests/test_hermes_routes_parity8u.py |

#### openclaw / desktop (5)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-13` | GATED | hardware | Real desktop control (Peekaboo/CUA) | tests/test_computer_use8tf.py |
| `OC-14` | GATED | hardware | Supervised ghost action framework (screenshot/key/cursor/window) | tests/test_desktop_parity8u.py |
| `OC-15` | MISSING | none | `screen` tool (arrange Control UI) | — |
| `OC-16` | MISSING | hardware | Screen snapshot work journal (Logbook) | — |
| `OC-17` | MISSING | hardware | CUA browser family (get_browser_state, etc.) | — |

#### openclaw / gateway (7)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-01` | MISSING | owner | Gateway protocol server + RPC v4 | — |
| `OC-03` | PARTIAL | creds | Gateway/peer records + link state | tests/test_gateway_peers8tf.py |
| `OC-04` | GATED | none | Gateway process supervision | tests/test_gateway_peers8tf.py |
| `OC-05` | MISSING | owner | Device pairing + device-scoped tokens (remote) | — |
| `OC-06` | PARTIAL | hardware | Node pairing (remote node host) | tests/test_node_host8u.py |
| `OC-08` | MISSING | none | Device setup code / QR / join URL / channel DM pairing | — |
| `OC-D09` | PARTIAL | none | New gateway RPC server methods (environments, task history, update report, workspace files, plugin m | tests/test_gateway_frames8u.py |

#### openclaw / ide (3)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-58` | PARTIAL | hardware | ACP external harnesses (Claude Code/Cursor/Gemini/Codex/Droid) | tests/test_acp_client8u.py |
| `OC-59` | GATED | none | LSP layer (clients, lifecycle) | tests/test_acp_lsp8tf.py |
| `OC-D20` | PARTIAL | none | PDF + transcripts tools | tests/test_doc_extract8u.py |

#### openclaw / messaging (9)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-19` | PARTIAL | creds | Broad channel set (~29 plugins) | tests/test_messaging_breadth8u.py |
| `OC-20` | PARTIAL | creds | Live inbound channel ingress/listeners | tests/test_channels_inbound8u.py |
| `OC-21` | PARTIAL | creds | Channel account pairing / DM access control | tests/test_pairing8tf.py |
| `OC-D12` | PARTIAL | creds | Discord voice channels, voice-follow, transcripts, rich messages | tests/test_messaging_breadth8u.py |
| `OC-D13` | PARTIAL | creds | Telegram mini-app + photo albums + rich messages | tests/test_messaging_breadth8u.py |
| `OC-D14` | PARTIAL | creds | Slack Enterprise Grid, transports, rich messages | tests/test_messaging8tf.py |
| `OC-D15` | PARTIAL | creds | iMessage private API, media, rich messages | tests/test_messaging_breadth8u.py |
| `OC-D16` | MISSING | creds | Feishu dynamic agents, advanced configuration | — |
| `OC-D17` | PARTIAL | creds | Matrix encryption + multi-account homeservers | tests/test_messaging_breadth8u.py |

#### openclaw / policy (2)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-D02` | PARTIAL | none | Policy plugin (workspace-conformance doctor, attestation, rules, findings) | — |
| `OC-D08` | GATED | none | Secrets egress proxy (outbound proxy, stream substitution, secret registry) | tests/test_proxy_status8u.py |

#### openclaw / reports (5)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-50` | PARTIAL | none | Analytics / usage | tests/test_webui_reports.py |
| `OC-51` | MISSING | hardware | Logbook screen work-journal | — |
| `OC-52` | PARTIAL | none | Immutable execution-identity receipts | tests/test_packet8f.py |
| `OC-D01` | PARTIAL | creds | Team Reports plugin (GitHub+Discord activity reports, history, summaries, scheduler, HTTP) | tests/test_report_bundles.py |
| `OC-D32` | MISSING | none | OpenTelemetry (traces, spans/events, model-call metrics, trace-context) | — |

#### openclaw / runtime (8)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-54` | MISSING | owner | Containerized runtime / fleet multi-tenant cells / cloud workers | — |
| `OC-55` | PARTIAL | none | Device capability inventory (browser/desktop/IDE/vision/runtime) | tests/test_runtime_tiles.py |
| `OC-56` | PARTIAL | hardware | Paired node host (screen/audio/camera/media playback/presence) | tests/test_node_host8u.py |
| `OC-D03` | PARTIAL | none | File Transfer plugin (node-host dir list/fetch/stream, resumable) | tests/test_file_transfer8u.py |
| `OC-D06` | PARTIAL | creds | Cloud workers / worker environments (prepared builds, admission, warm images) | tests/test_runtime_images8u.py |
| `OC-D07` | PARTIAL | none | Node runtime update / recovery (respawn, native hook relay) | tests/test_runtime_images8u.py |
| `OC-D30` | PARTIAL | none | Update rollback / repair / recovery (package/service rollback, update history) | tests/test_runtime_images8u.py |
| `OC-D31` | PARTIAL | none | Plugin marketplace / gateway discovery CLI docs expand | tests/test_native_equivalents8u.py |

#### openclaw / sessions (4)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-44` | PARTIAL | none | Session create/resume/archive/delete/rename/search/subagents/steering | tests/test_owner_session8tf.py |
| `OC-48` | MISSING | creds | Markdown workspace memory / dreaming / external providers (LanceDB/Wiki/Honcho) | — |
| `OC-D25` | MISSING | none | Subagent yield handoff (sessions_yield continuation context/tool) | — |
| `OC-D26` | PARTIAL | none | Session repository / managed worktrees (cloud artifacts, workspace access, lifecycle) | tests/test_opencode_voice8u.py |

#### openclaw / skills (5)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-39` | GATED | none | Remote plugin/skill install (ClawHub/npm/git/archive) | tests/test_plugins8tf.py |
| `OC-40` | PARTIAL | none | Plugin runtime extensibility (tools/channels/providers/hooks) | tests/test_plugin_runtime8u.py |
| `OC-41` | PARTIAL | none | Skill workshop / authoring / self-learning | tests/test_packet8p.py |
| `OC-D10` | PARTIAL | none | MCP hosted apps (loopback MCP HTTP server, attach grants, OAuth callback, standalone host) | tests/test_plugin_runtime8u.py |
| `OC-D29` | PARTIAL | none | Skill Workshop agent-owned collection (backup/restore/rollback, curator, contracts) | tests/test_packet8p.py |

#### openclaw / web_media (6)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-63` | PARTIAL | creds | `web_search` / `x_search` / `web_fetch` | tests/test_web_tool8u.py |
| `OC-64` | PARTIAL | creds | Image / music / video generation | tests/test_media_gen8u.py |
| `OC-65` | PARTIAL | creds | Media understanding beyond single-image vision | tests/test_vision8tf.py |
| `OC-67` | MISSING | creds | Realtime transcription / voice call / meeting bot | — |
| `OC-D18` | PARTIAL | creds | Media generation tools (image/music/video) + GPT Image 2.5 | tests/test_media_gen8u.py |
| `OC-D28` | PARTIAL | creds | Provider account controls + model fallback picker + custom providers | tests/test_local_models8u.py |

#### openclaw / webhooks (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-25` | MISSING | creds | Gmail Pub/Sub transport | — |

### source: qubit (259 open)

#### qubit / acp (2)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `acp_accept_hooks_refusal` | PARTIAL | none | ACP bypass flags | tests/test_acp_lsp8tf.py::test_acp_check_readonly_shape |
| `acp_serve_refusal` | PARTIAL | none | ACP serve | tests/test_acp_lsp8tf.py::test_acp_check_readonly_shape |

#### qubit / actions (4)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `actions_prepare` | UNVERIFIED | none | Prepare action | — |
| `actions_preview` | PARTIAL | none | Preview action | tests/test_dashboard_ops8tf.py::test_actions_ghost_preview_extras_execute |
| `actions_recover` | UNVERIFIED | none | Recover action | — |
| `actions_retry` | UNVERIFIED | none | Retry action | — |

#### qubit / agent (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `import_agent_import` | PARTIAL | none | Import-agent import | tests/test_dashboard_ops8tf.py::test_extra_refusals_are_typed |

#### qubit / audio_hw (6)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `asr_test` | UNVERIFIED | hardware | Transcription test | — |
| `enroll_voice` | UNVERIFIED | hardware | Enroll my voice | — |
| `mic_test` | UNVERIFIED | hardware | Microphone test | — |
| `roundtrip_test` | UNVERIFIED | hardware | Voice self-test | — |
| `tts_test` | UNVERIFIED | hardware | Speech test | — |
| `verify_voice` | UNVERIFIED | hardware | Verify my voice | — |

#### qubit / auth_logout (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `auth_logout` | UNVERIFIED | creds | Logout check | — |

#### qubit / backup (5)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `backup_create` | DASHBOARD_ONLY | none | Backup create | tests/test_dashboard_ops8tf.py::test_backup_roundtrip_stays_encrypted |
| `backup_info` | PARTIAL | none | Backup guidance | tests/test_dashboard_ops8tf.py::test_dashboard_matches_direct_backend_for_every_read_op |
| `backup_restore` | DASHBOARD_ONLY | none | Backup restore | tests/test_export_voice8u.py::test_corrupt_bundle_typed |
| `export_state_voice` | PARTIAL | none | Export state (voice refusal) | tests/test_export_voice8u.py::test_voice_export_import_refused_with_guidance |
| `import_state_voice` | PARTIAL | none | Import state (voice refusal) | tests/test_export_voice8u.py::test_voice_export_import_refused_with_guidance |

#### qubit / brain (5)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `brain_export` | DASHBOARD_ONLY | none | Brain profile export | tests/test_brain8tf.py::test_profile_provenance_forget_sweep_export |
| `brain_onboard_apply` | DASHBOARD_ONLY | none | Brain onboarding apply | tests/test_brain8tf.py::test_onboarding_offline_preview_and_apply |
| `brain_onboard_plan` | PARTIAL | none | Brain setup plan | tests/test_brain8tf.py::test_onboarding_offline_preview_and_apply |
| `brain_sweep` | DASHBOARD_ONLY | none | Brain expiry sweep | tests/test_brain8tf.py::test_profile_provenance_forget_sweep_export |
| `brain_vault_remove` | UNVERIFIED | none | Remove vault reference | — |

#### qubit / browser (2)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `browser_close` | PARTIAL | hardware | Browser close-profile | tests/test_browser8tf.py::test_close_profile_redacts_name |
| `browser_close_refusal` | PARTIAL | hardware | Close browser profile | tests/test_browser8tf.py::test_close_profile_redacts_name |

#### qubit / commands (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `commands_list` | UNVERIFIED | none | What can you do | — |

#### qubit / computer (4)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `computer_doctor` | PARTIAL | hardware | Desktop health | tests/test_computer_use8tf.py::test_doctor_checklist_typed |
| `computer_install` | DASHBOARD_ONLY | hardware | Computer-use install | tests/test_computer_use8tf.py::test_install_refused |
| `computer_install_refusal` | PARTIAL | hardware | Desktop driver install | tests/test_computer_use8tf.py::test_doctor_checklist_typed |
| `computer_permissions` | PARTIAL | hardware | Desktop permissions | tests/test_computer_use8tf.py::test_permissions_record |

#### qubit / console_guidance (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `console_help` | PARTIAL | none | Terminal help | tests/test_dashboard_ops8tf.py::test_dashboard_matches_direct_backend_for_every_read_op |

#### qubit / curator (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `curator` | PARTIAL | none | Learning summary | tests/test_dashboard_ops8tf.py::test_dashboard_matches_direct_backend_for_every_read_op |

#### qubit / dashboard_plugin (10)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `plugin_qubit-approvals` | UNVERIFIED | none | Qubit Approvals | — |
| `plugin_qubit-boards` | UNVERIFIED | none | Boards | — |
| `plugin_qubit-brand` | UNVERIFIED | none | Qubit Brand | — |
| `plugin_qubit-chat` | UNVERIFIED | none | Qubit Chat | — |
| `plugin_qubit-devices` | UNVERIFIED | none | Devices | — |
| `plugin_qubit-gateway` | UNVERIFIED | none | Gateway | — |
| `plugin_qubit-memory` | UNVERIFIED | none | Memory | — |
| `plugin_qubit-ops` | UNVERIFIED | none | Operations | — |
| `plugin_qubit-reports` | UNVERIFIED | none | Reports | — |
| `plugin_qubit-runtime` | UNVERIFIED | none | Runtime | — |

#### qubit / diagnostics (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `diagnostics` | PARTIAL | none | Diagnostic bundle | tests/test_dashboard_ops8tf.py::test_dashboard_matches_direct_backend_for_every_read_op |

#### qubit / doctor (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `doctor` | PARTIAL | none | Diagnostics | tests/test_dashboard_ops8tf.py::test_dashboard_matches_direct_backend_for_every_read_op |

#### qubit / editor (3)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `acp_accept_hooks` | DASHBOARD_ONLY | none | ACP accept-hooks | tests/test_acp_lsp8tf.py::test_acp_serve_and_hooks_refused |
| `acp_serve` | DASHBOARD_ONLY | none | ACP serve | tests/test_acp_lsp8tf.py::test_acp_serve_and_hooks_refused |
| `lsp_install` | PARTIAL | none | LSP install | tests/test_acp_lsp8tf.py::test_lsp_restart_and_install |

#### qubit / experiments (12)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `experiments_abort` | UNVERIFIED | none | Abort experiment | — |
| `experiments_evaluate` | UNVERIFIED | none | Evaluate experiment | — |
| `experiments_finish` | UNVERIFIED | none | Finish trial | — |
| `experiments_list` | UNVERIFIED | none | List experiments | — |
| `experiments_promote` | UNVERIFIED | none | Promote experiment | — |
| `experiments_register` | UNVERIFIED | none | Register candidate | — |
| `experiments_show` | UNVERIFIED | none | Show experiment | — |
| `experiments_signal` | UNVERIFIED | none | Record signal | — |
| `experiments_snapshot` | UNVERIFIED | none | Snapshot experiments | — |
| `experiments_start` | UNVERIFIED | none | Start experiment | — |
| `experiments_status` | UNVERIFIED | none | Experiment status | — |
| `experiments_trial` | UNVERIFIED | none | Start trial | — |

#### qubit / frontier_show (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `frontier_show` | UNVERIFIED | creds | Show model entry | — |

#### qubit / gateway (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `gateway_supervision` | DASHBOARD_ONLY | creds | Gateway supervision | tests/test_dashboard_ops8tf.py::test_extra_refusals_are_typed |

#### qubit / ghost (7)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `ghost_action_preview` | PARTIAL | none | Preview Ghost action | tests/test_desktop_parity8u.py::test_cancel_in_flight_and_crash_recovery |
| `ghost_action_recover` | PARTIAL | none | Recover Ghost action | tests/test_desktop_parity8u.py::test_cancel_in_flight_and_crash_recovery |
| `ghost_action_retry` | PARTIAL | none | Retry Ghost action | tests/test_desktop_parity8u.py::test_cancel_in_flight_and_crash_recovery |
| `ghost_click` | PARTIAL | none | Click this button | tests/test_desktop_parity8u.py::test_cancel_in_flight_and_crash_recovery |
| `ghost_preview` | PARTIAL | none | Preview Ghost action | tests/test_dashboard_ops8tf.py::test_actions_ghost_preview_extras_execute |
| `ghost_read_tab` | PARTIAL | none | Read the browser tab | tests/test_desktop_parity8u.py::test_cancel_in_flight_and_crash_recovery |
| `ghost_type` | PARTIAL | none | Fill this form | tests/test_desktop_parity8u.py::test_cancel_in_flight_and_crash_recovery |

#### qubit / hermes_gated (71)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `hermes_gate_audio_lease_desktop` | GATED | owner | Gated upstream route: AUDIO_LEASE_DESKTOP | — |
| `hermes_gate_audio_stream_local` | GATED | owner | Gated upstream route: AUDIO_STREAM_LOCAL | — |
| `hermes_gate_backup_passphrase` | GATED | owner | Gated upstream route: BACKUP_PASSPHRASE | — |
| `hermes_gate_blocked_remote_source` | GATED | owner | Gated upstream route: BLOCKED_REMOTE_SOURCE | — |
| `hermes_gate_blueprint_staged` | GATED | owner | Gated upstream route: BLUEPRINT_STAGED | — |
| `hermes_gate_chat_image_overlay` | GATED | owner | Gated upstream route: CHAT_IMAGE_OVERLAY | — |
| `hermes_gate_computer_use_grant_staged` | GATED | owner | Gated upstream route: COMPUTER_USE_GRANT_STAGED | — |
| `hermes_gate_cron_fire_external` | GATED | owner | Gated upstream route: CRON_FIRE_EXTERNAL | — |
| `hermes_gate_curator_on_demand` | GATED | owner | Gated upstream route: CURATOR_ON_DEMAND | — |
| `hermes_gate_desktop_overlay_only` | GATED | owner | Gated upstream route: DESKTOP_OVERLAY_ONLY | — |
| `hermes_gate_export_brokered` | GATED | owner | Gated upstream route: EXPORT_BROKERED | — |
| `hermes_gate_files_binary_stream` | GATED | owner | Gated upstream route: FILES_BINARY_STREAM | — |
| `hermes_gate_gateway_no_supervision` | GATED | owner | Gated upstream route: GATEWAY_NO_SUPERVISION | — |
| `hermes_gate_gh_auth_operator` | GATED | owner | Gated upstream route: GH_AUTH_OPERATOR | — |
| `hermes_gate_gh_pr_external` | GATED | owner | Gated upstream route: GH_PR_EXTERNAL | — |
| `hermes_gate_git_ship_gh_external` | GATED | owner | Gated upstream route: GIT_SHIP_GH_EXTERNAL | — |
| `hermes_gate_git_write_brokered` | GATED | owner | Gated upstream route: GIT_WRITE_BROKERED | — |
| `hermes_gate_media_not_served` | GATED | owner | Gated upstream route: MEDIA_NOT_SERVED | — |
| `hermes_gate_network_probe_blocked` | GATED | owner | Gated upstream route: NETWORK_PROBE_BLOCKED | — |
| `hermes_gate_oauth_manual_handoff` | GATED | owner | Gated upstream route: OAUTH_MANUAL_HANDOFF | — |
| `hermes_gate_onboarding_guided` | GATED | owner | Gated upstream route: ONBOARDING_GUIDED | — |
| `hermes_gate_plugin_providers_qubit` | GATED | owner | Gated upstream route: PLUGIN_PROVIDERS_QUBIT | — |
| `hermes_gate_profile_import_review` | GATED | owner | Gated upstream route: PROFILE_IMPORT_REVIEW | — |
| `hermes_gate_provider_endpoint_staged` | GATED | owner | Gated upstream route: PROVIDER_ENDPOINT_STAGED | — |
| `hermes_gate_provider_external` | GATED | owner | Gated upstream route: PROVIDER_EXTERNAL | — |
| `hermes_gate_pty_no_shell` | GATED | owner | Gated upstream route: PTY_NO_SHELL | — |
| `hermes_gate_secret_never_served` | GATED | owner | Gated upstream route: SECRET_NEVER_SERVED | — |
| `hermes_gate_sessions_receipt_store` | GATED | owner | Gated upstream route: SESSIONS_RECEIPT_STORE | — |
| `hermes_gate_ssh_ownership_operator` | GATED | owner | Gated upstream route: SSH_OWNERSHIP_OPERATOR | — |
| `hermes_gate_stub_actions_read` | GATED | owner | Gated upstream route: STUB_ACTIONS_READ | — |
| `hermes_gate_stub_agent_disable` | GATED | owner | Gated upstream route: STUB_AGENT_DISABLE | — |
| `hermes_gate_stub_agent_enable` | GATED | owner | Gated upstream route: STUB_AGENT_ENABLE | — |
| `hermes_gate_stub_agent_install` | GATED | owner | Gated upstream route: STUB_AGENT_INSTALL | — |
| `hermes_gate_stub_agent_remove` | GATED | owner | Gated upstream route: STUB_AGENT_REMOVE | — |
| `hermes_gate_stub_agent_update` | GATED | owner | Gated upstream route: STUB_AGENT_UPDATE | — |
| `hermes_gate_stub_blueprints` | GATED | owner | Gated upstream route: STUB_BLUEPRINTS | — |
| `hermes_gate_stub_ckpt` | GATED | owner | Gated upstream route: STUB_CKPT | — |
| `hermes_gate_stub_ckpt_prune` | GATED | owner | Gated upstream route: STUB_CKPT_PRUNE | — |
| `hermes_gate_stub_config_save` | GATED | owner | Gated upstream route: STUB_CONFIG_SAVE | — |
| `hermes_gate_stub_debug_share` | GATED | owner | Gated upstream route: STUB_DEBUG_SHARE | — |
| `hermes_gate_stub_delivery` | GATED | owner | Gated upstream route: STUB_DELIVERY | — |
| `hermes_gate_stub_env_write` | GATED | owner | Gated upstream route: STUB_ENV_WRITE | — |
| `hermes_gate_stub_font` | GATED | owner | Gated upstream route: STUB_FONT | — |
| `hermes_gate_stub_font_set` | GATED | owner | Gated upstream route: STUB_FONT_SET | — |
| `hermes_gate_stub_h_session_delete` | GATED | owner | Gated upstream route: STUB_H_SESSION_DELETE | — |
| `hermes_gate_stub_h_session_patch` | GATED | owner | Gated upstream route: STUB_H_SESSION_PATCH | — |
| `hermes_gate_stub_h_session_resume` | GATED | owner | Gated upstream route: STUB_H_SESSION_RESUME | — |
| `hermes_gate_stub_import_upload` | GATED | owner | Gated upstream route: STUB_IMPORT_UPLOAD | — |
| `hermes_gate_stub_mcp_create` | GATED | owner | Gated upstream route: STUB_MCP_CREATE | — |
| `hermes_gate_stub_mcp_enabled` | GATED | owner | Gated upstream route: STUB_MCP_ENABLED | — |
| `hermes_gate_stub_mcp_test` | GATED | owner | Gated upstream route: STUB_MCP_TEST | — |
| `hermes_gate_stub_mem_config` | GATED | owner | Gated upstream route: STUB_MEM_CONFIG | — |
| `hermes_gate_stub_mem_config_write` | GATED | owner | Gated upstream route: STUB_MEM_CONFIG_WRITE | — |
| `hermes_gate_stub_mem_prov` | GATED | owner | Gated upstream route: STUB_MEM_PROV | — |
| `hermes_gate_stub_mem_provider_set` | GATED | owner | Gated upstream route: STUB_MEM_PROVIDER_SET | — |
| `hermes_gate_stub_mem_reset` | GATED | owner | Gated upstream route: STUB_MEM_RESET | — |
| `hermes_gate_stub_msg_test` | GATED | owner | Gated upstream route: STUB_MSG_TEST | — |
| `hermes_gate_stub_oauth_delete` | GATED | owner | Gated upstream route: STUB_OAUTH_DELETE | — |
| `hermes_gate_stub_plugin_visibility` | GATED | owner | Gated upstream route: STUB_PLUGIN_VISIBILITY | — |
| `hermes_gate_stub_plugins_hub` | GATED | owner | Gated upstream route: STUB_PLUGINS_HUB | — |
| `hermes_gate_stub_plugins_rescan` | GATED | owner | Gated upstream route: STUB_PLUGINS_RESCAN | — |
| `hermes_gate_stub_sess_bulk` | GATED | owner | Gated upstream route: STUB_SESS_BULK | — |
| `hermes_gate_stub_sess_empty` | GATED | owner | Gated upstream route: STUB_SESS_EMPTY | — |
| `hermes_gate_stub_sess_import` | GATED | owner | Gated upstream route: STUB_SESS_IMPORT | — |
| `hermes_gate_stub_sess_prune` | GATED | owner | Gated upstream route: STUB_SESS_PRUNE | — |
| `hermes_gate_stub_toolset_config` | GATED | owner | Gated upstream route: STUB_TOOLSET_CONFIG | — |
| `hermes_gate_stub_toolset_write` | GATED | owner | Gated upstream route: STUB_TOOLSET_WRITE | — |
| `hermes_gate_terminal_backend_staged` | GATED | owner | Gated upstream route: TERMINAL_BACKEND_STAGED | — |
| `hermes_gate_update_operator_owned` | GATED | owner | Gated upstream route: UPDATE_OPERATOR_OWNED | — |
| `hermes_gate_write_brokered` | GATED | owner | Gated upstream route: WRITE_BROKERED | — |
| `hermes_gate_ws_single_path` | GATED | owner | Gated upstream route: WS_SINGLE_PATH | — |

#### qubit / hermes_web_page (19)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `page_analyticspage` | UNVERIFIED | none | Hermes web page AnalyticsPage | — |
| `page_channelspage` | UNVERIFIED | none | Hermes web page ChannelsPage | — |
| `page_chatpage` | DASHBOARD_ONLY | none | Hermes web page ChatPage | frontend/hermes-web/src/pages/ChatPage.test.tsx |
| `page_configpage` | UNVERIFIED | none | Hermes web page ConfigPage | — |
| `page_cronpage` | UNVERIFIED | none | Hermes web page CronPage | — |
| `page_docspage` | UNVERIFIED | none | Hermes web page DocsPage | — |
| `page_envpage` | UNVERIFIED | none | Hermes web page EnvPage | — |
| `page_filespage` | UNVERIFIED | none | Hermes web page FilesPage | — |
| `page_logspage` | UNVERIFIED | none | Hermes web page LogsPage | — |
| `page_mcppage` | UNVERIFIED | none | Hermes web page McpPage | — |
| `page_modelspage` | UNVERIFIED | none | Hermes web page ModelsPage | — |
| `page_pairingpage` | UNVERIFIED | none | Hermes web page PairingPage | — |
| `page_pluginspage` | UNVERIFIED | none | Hermes web page PluginsPage | — |
| `page_profilebuilderpage` | UNVERIFIED | none | Hermes web page ProfileBuilderPage | — |
| `page_profilespage` | UNVERIFIED | none | Hermes web page ProfilesPage | — |
| `page_sessionspage` | DASHBOARD_ONLY | none | Hermes web page SessionsPage | frontend/hermes-web/src/pages/SessionsPage.test.tsx |
| `page_skillspage` | UNVERIFIED | none | Hermes web page SkillsPage | — |
| `page_systempage` | UNVERIFIED | none | Hermes web page SystemPage | — |
| `page_webhookspage` | UNVERIFIED | none | Hermes web page WebhooksPage | — |

#### qubit / hooks (3)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `hooks_remove` | UNVERIFIED | none | Remove hook | — |
| `hooks_revoke` | UNVERIFIED | none | Revoke hook | — |
| `hooks_show` | UNVERIFIED | none | Show hook | — |

#### qubit / import_agent (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `import_refusal` | PARTIAL | none | Import foreign setup | tests/test_import_agent8tf.py::test_import_refused_and_redacted |

#### qubit / insights (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `insights` | PARTIAL | none | Activity summary | tests/test_dashboard_ops8tf.py::test_dashboard_matches_direct_backend_for_every_read_op |

#### qubit / kanban (15)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `kanban_assign` | UNVERIFIED | none | Assign task | — |
| `kanban_block` | UNVERIFIED | none | Block task | — |
| `kanban_comment` | UNVERIFIED | none | Comment on task | — |
| `kanban_complete` | UNVERIFIED | none | Complete task | — |
| `kanban_delegated` | DASHBOARD_ONLY | none | Kanban delegated op | tests/test_dashboard_ops8tf.py::test_extra_refusals_are_typed |
| `kanban_edit` | UNVERIFIED | none | Edit task | — |
| `kanban_import` | UNVERIFIED | none | Kanban import | — |
| `kanban_reassign` | UNVERIFIED | none | Reassign task | — |
| `kanban_reclaim` | UNVERIFIED | none | Reclaim task | — |
| `kanban_role_grant` | PARTIAL | none | Kanban role grant | tests/test_board_canvas8u.py::test_cli_dashboard_voice_parity |
| `kanban_role_revoke` | PARTIAL | none | Kanban role revoke | tests/test_board_canvas8u.py::test_cli_dashboard_voice_parity |
| `kanban_snapshot_restore` | PARTIAL | none | Kanban snapshot restore | tests/test_board_canvas8u.py::test_canvas_estop_refuses_mutations |
| `kanban_unblock` | UNVERIFIED | none | Unblock task | — |
| `kanban_widget_move` | PARTIAL | none | Kanban widget move | tests/test_board_canvas8u.py::test_cli_dashboard_voice_parity |
| `kanban_widget_move_voice` | UNVERIFIED | none | Move canvas widget | — |

#### qubit / logs (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `logs` | PARTIAL | none | Recent logs | tests/test_dashboard_ops8tf.py::test_dashboard_matches_direct_backend_for_every_read_op |

#### qubit / loop (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `loop_start` | UNVERIFIED | none | Start the voice loop | — |

#### qubit / lsp (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `lsp_install_refusal` | PARTIAL | none | Install language server | tests/test_acp_lsp8tf.py::test_lsp_inventory_never_executes |

#### qubit / memory (2)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `memory_forget` | UNVERIFIED | none | Forget memory | — |
| `memory_remember` | UNVERIFIED | none | Remember | — |

#### qubit / mic_guidance (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `mic_help` | PARTIAL | none | Microphone help | tests/test_dashboard_ops8tf.py::test_dashboard_matches_direct_backend_for_every_read_op |

#### qubit / msg (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `msg_account_show` | UNVERIFIED | creds | Show messaging account | — |

#### qubit / opencode (2)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `opencode_prepare` | PARTIAL | none | OpenCode prepare | tests/test_opencode_voice8u.py::test_cancel_retry_recover_receipts |
| `opencode_run_confirmed` | DASHBOARD_ONLY | none | OpenCode execute (hash-bound) | tests/test_opencode_voice8u.py::test_cancel_retry_recover_receipts |

#### qubit / pause (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `pause` | PARTIAL | none | Pause all work | tests/test_packet8o.py::test_automation_monitor_observes_durable_emergency_stop |

#### qubit / projects (11)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `project_add_folder` | UNVERIFIED | none | Add project folder | — |
| `project_bind_board` | UNVERIFIED | none | Bind board | — |
| `project_remove_folder` | UNVERIFIED | none | Remove project folder | — |
| `project_rename` | UNVERIFIED | none | Rename project | — |
| `project_restore` | UNVERIFIED | none | Restore project | — |
| `project_rollback` | UNVERIFIED | none | Rollback projects | — |
| `project_set_primary` | UNVERIFIED | none | Set primary folder | — |
| `projects_archive` | UNVERIFIED | none | Archive project | — |
| `projects_create` | UNVERIFIED | none | Create project | — |
| `projects_show` | UNVERIFIED | none | Show project | — |
| `projects_use` | UNVERIFIED | none | Switch project | — |

#### qubit / prompt_budget (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `prompt_budget` | PARTIAL | none | Prompt budget | tests/test_dashboard_ops8tf.py::test_dashboard_matches_direct_backend_for_every_read_op |

#### qubit / proxy (2)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `proxy_start` | PARTIAL | none | Proxy start | tests/test_dashboard_ops8tf.py::test_extra_refusals_are_typed |
| `proxy_start_refusal` | PARTIAL | none | Start proxy | tests/test_proxy8tf.py::test_no_network_or_listeners |

#### qubit / reasoning (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `reasoning` | PARTIAL | none | Reasoning budgets | tests/test_dashboard_ops8tf.py::test_dashboard_matches_direct_backend_for_every_read_op |

#### qubit / reports (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `reports_list` | PARTIAL | none | Show my reports | tests/test_required_utterances8tf.py::test_dashboard_tier1_runs_new_ops |

#### qubit / resume (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `resume` | PARTIAL | none | Resume work | tests/test_packet8o.py::test_automation_monitor_observes_durable_emergency_stop |

#### qubit / secrets (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `secrets` | PARTIAL | creds | Secret status | tests/test_dashboard_ops8tf.py::test_dashboard_matches_direct_backend_for_every_read_op |

#### qubit / security (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `security` | PARTIAL | none | Security status | tests/test_dashboard_ops8tf.py::test_dashboard_matches_direct_backend_for_every_read_op |

#### qubit / sessions (6)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `session_archive` | UNVERIFIED | none | Archive a session | — |
| `session_delete` | UNVERIFIED | none | Delete a session | — |
| `session_rename` | UNVERIFIED | none | Rename a session | — |
| `session_resume` | UNVERIFIED | none | Resume a session | — |
| `session_show` | UNVERIFIED | none | Show a session | — |
| `sessions_search` | UNVERIFIED | none | Search sessions | — |

#### qubit / skills (6)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `plugins_remote` | DASHBOARD_ONLY | none | Remote plugin install | tests/test_dashboard_ops8tf.py::test_extra_refusals_are_typed |
| `skills_disable` | UNVERIFIED | none | Disable skill | — |
| `skills_enable` | UNVERIFIED | none | Enable skill | — |
| `skills_remove` | UNVERIFIED | none | Remove skill | — |
| `skills_rollback` | UNVERIFIED | none | Rollback skill | — |
| `skills_show` | UNVERIFIED | none | Show skill | — |

#### qubit / state (2)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `export_state` | PARTIAL | none | Export portable state | tests/test_dashboard_ops8tf.py::test_export_extra_never_persists_passphrase |
| `import_state` | PARTIAL | none | Import portable state | tests/test_dashboard_ops8tf.py::test_import_extra_is_preview_only |

#### qubit / sync (4)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `sync_accept` | UNVERIFIED | creds | Sync accept | — |
| `sync_offer` | PARTIAL | creds | Sync offer | tests/test_dashboard_ops8tf.py::test_passphrase_never_persisted_in_journal |
| `sync_revoke` | PARTIAL | creds | Sync revoke | tests/test_dashboard_ops8tf.py::test_estop_refuses_mutations_but_not_reads |
| `sync_revoke_voice` | UNVERIFIED | creds | Revoke sync offer | — |

#### qubit / themes (5)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `pets_downloads` | DASHBOARD_ONLY | none | Pet gallery downloads | tests/test_dashboard_ops8tf.py::test_extra_refusals_are_typed |
| `themes_reset` | PARTIAL | none | Reset theme | tests/test_themes8tf.py::test_set_reset_roundtrip_with_receipt |
| `themes_set` | PARTIAL | none | Set theme | tests/test_themes8tf.py::test_corrupt_state_falls_back_to_default |
| `themes_tweak_clear` | PARTIAL | none | Clear theme tweak | tests/test_themes8tf.py::test_corrupt_state_falls_back_to_default |
| `themes_tweak_set` | PARTIAL | none | Tweak theme color | tests/test_themes8tf.py::test_corrupt_state_falls_back_to_default |

#### qubit / tools (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `tools` | PARTIAL | none | Tool inventory | tests/test_dashboard_ops8tf.py::test_dashboard_matches_direct_backend_for_every_read_op |

#### qubit / update_status (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `update_status` | PARTIAL | none | Update status | tests/test_dashboard_ops8tf.py::test_dashboard_matches_direct_backend_for_every_read_op |

#### qubit / verified (6)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `verified_list` | UNVERIFIED | none | List verified skills | — |
| `verified_pin` | UNVERIFIED | none | Pin verified skill | — |
| `verified_recall` | UNVERIFIED | none | Recall verified skills | — |
| `verified_show` | UNVERIFIED | none | Show verified skill | — |
| `verified_unpin` | UNVERIFIED | none | Unpin verified skill | — |
| `verified_verdict` | UNVERIFIED | none | Judge verified skill | — |

#### qubit / voice_unsupported (18)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `guard_actions_prepare` | PARTIAL | none | Actions Prepare (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |
| `guard_actions_preview` | PARTIAL | none | Actions Preview (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |
| `guard_actions_recover` | PARTIAL | none | Actions Recover (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |
| `guard_actions_retry` | PARTIAL | none | Actions Retry (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |
| `guard_ask_answer` | PARTIAL | none | Ask Answer (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |
| `guard_backup_create` | PARTIAL | none | Backup Create (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |
| `guard_backup_restore` | PARTIAL | none | Backup Restore (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |
| `guard_brain_onboard_apply` | PARTIAL | none | Brain Onboard Apply (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |
| `guard_file_patch` | PARTIAL | none | File Patch (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |
| `guard_ghost_preview` | PARTIAL | none | Ghost Preview (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |
| `guard_kanban_import` | PARTIAL | none | Kanban Import (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |
| `guard_opencode_prepare` | PARTIAL | none | Opencode Prepare (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |
| `guard_opencode_run_confirmed` | PARTIAL | none | Opencode Run Confirmed (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |
| `guard_shares_resolve` | PARTIAL | none | Shares Resolve (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |
| `guard_sync_accept` | PARTIAL | none | Sync Accept (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |
| `guard_sync_offer` | PARTIAL | none | Sync Offer (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |
| `guard_transfers_fetch_raw` | PARTIAL | none | Transfers Fetch Raw (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |
| `guard_webhook_verify` | PARTIAL | none | Webhook Verify (voice guard) | tests/test_voice_surface_closure8u.py::test_every_catalog_op_has_a_voice_surface |

#### qubit / webhooks (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `webhook_verify` | PARTIAL | creds | Webhook verify | tests/test_webhooks8tf.py::test_verify_auth_missing_and_test_plan |

#### qubit / worktree (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `worktree` | PARTIAL | none | Worktree cleanup | tests/test_dashboard_ops8tf.py::test_dashboard_matches_direct_backend_for_every_read_op |
