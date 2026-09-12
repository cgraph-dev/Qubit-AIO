# Qubit gap ledger

Machine-generated from `docs/QUBIT_GAP_LEDGER.*.json`. This merged report is the authoritative view; the input JSON sections are never modified.

> **COMPLETION GATE:** NOT COMPLETE while any row has status != IMPLEMENTED.

## Provenance pins

- `docs/QUBIT_GAP_LEDGER.hermes.json` — schema `qubit-gap-ledger-section/1`, source `hermes`, 433 rows
  - counts: {"by_area": {"analytics-config": 4, "audio-voice": 6, "chat-websocket": 5, "cli": 72, "cli-subcommand": 60, "config-env": 15, "cron": 13, "dashboard-auth": 11, "dashboard-page": 20, "dashboard-ui": 15, "dashboard_plugin": 5, "files-fs": 18, "gateway-actions": 6, "git": 21, "local-models": 16, "mcp": 12, "memory": 5, "messaging": 11, "models": 7, "ops": 28, "profiles": 20, "provider-oauth": 6, "ses
  - generated: 2026-09-12
  - method: Evidence-based regeneration. API rows re-extract the pinned upstream route decorators and classify them from voicebridge.hermes_compat.match disposition plus the handler's real behaviour (a handler whose normal exits are typed gates/refusals is GATED; a real read/write is IMPLEMENTED; a wire-shape stub is PARTIAL). IMPLEMENTED additionally requires an on-disk tests/ function that dispatches the ro
  - plugin_fleet: [{"api": "plugins/hermes-achievements/dashboard/plugin_api.py", "has_dashboard": true, "manifest": "plugins/hermes-achievements/dashboard/manifest.json", "name": "hermes-achievements", "source": "vendored"}, {"api": "plugins/kanban/dashboard/plugin_api.py", "has_dashboard": true, "manifest": "plugins/kanban/dashboard/manifest.json", "name": "kanban", "source": "vendored"}, {"has_dashboard": false,
  - sources_reconciled: ["voicebridge/hermes_compat.py", "voicebridge/hermes_plugins.py", "voicebridge/cli.py", "voicebridge/cli_hermes_forward.py", "tests/ (AST caller evidence)", "tests/test_hermes_plugins.py (plugin-fleet proof)", "qubit_hermes/hermes_cli/web_routers", "qubit_hermes/hermes_cli/web_server*.py", "qubit_hermes/hermes_cli/memory_oauth.py", "qubit_hermes/hermes_cli/dashboard_auth/routes.py", "qubit_hermes/
  - upstream_pin: 2237be355906fbe6065ce1815711eee52b2d646e
- `docs/QUBIT_GAP_LEDGER.openclaw.json` — schema `qubit-gap-ledger-section/1`, source `openclaw`, 104 rows
  - counts: {"by_area": {"automation": 10, "boards": 6, "browser": 5, "dashboard": 4, "desktop": 5, "gateway": 10, "ide": 7, "messaging": 11, "policy": 2, "reports": 6, "runtime": 9, "sessions": 10, "skills": 7, "web_media": 9, "webhooks": 3}, "by_status": {"GATED": 4, "IMPLEMENTED": 94, "PARTIAL": 6}, "total": 104}
  - extension_map: {"by_disposition": {"distinct": 3, "gate": 123, "parent": 28}, "by_needs": {"creds": 115, "hardware": 8, "none": 27, "owner": 4}, "covered": true, "total": 154, "unexplained": []}
  - generated: 2026-09-12
  - method: Evidence-based regeneration. The audited capability inventory (72 rows @v2026.9.2 + 32 delta rows @v2026.9.4) is embedded as a baseline; a documented ROW_EVIDENCE table records the rows closed or upgraded by real Qubit modules + behavioural tests. Every IMPLEMENTED row is mechanically re-verified against the code and an on-disk proving test that references the cited backend; a row upgraded from an
  - sources_reconciled: ["docs/AUDIT_OPENCLAW_2026-09-11.md", "docs/AUDIT_OPENCLAW_V2026_9_4_DELTA.md", "voicebridge/automation.py", "voicebridge/schedules.py", "tests/test_automation_agent6_*.py", "tests/test_openclaw_e5_directives.py", "tests/test_oc_*_agent[5-9]*.py (Agent 5-10 witness tests)", "tests/ (proving-test evidence)"]
  - upstream_pin: 3a9d69db306cd7f081e06254cb89c4bcc14a7107
  - upstream_tag: v2026.9.4
  - upstream_tree: /tmp/opencode/pinned/openclaw (read via git show 3a9d69db306cd7f081e06254cb89c4bcc14a7107, shallow clone)
- `docs/QUBIT_GAP_LEDGER.qubit.json` — schema `qubit-gap-ledger-section/1`, source `qubit-surface`, 907 rows
  - generated_from: cli_families=106, dashboard_ops.EXTRA_OPS=34, dashboard_ops.catalog=510, hermes_compat.GATED=102, hermes_compat.ROUTES=361, source_inventory.distinct_uncovered=271, source_inventory.items=6776, source_inventory.parented_covered=1013, source_inventory.parented_uncovered=67, source_inventory.surface_items=1819, voice_commands.CLI_COVERAGE=106, voice_commands.REGISTRY=476
  - evidence: production backend + declared interfaces + live CLI tree + voice registry + linked tests/ AST proofs + direct source inventory
  - generator: scripts/rebuild_ledger_qubit.py
  - backend_flags: []
  - generated: 2026-09-12
  - inventory: {"by_category": {"hermes_backend_route": 1, "hermes_cli_option": 37, "hermes_cli_positional": 8, "hermes_cli_subcommand": 150, "hermes_plugin": 10, "hermes_skill": 29, "hermes_tool": 73, "openclaw_extension": 30}, "by_parent": {"OC-19": 19, "cli:config set": 1, "cli:mcp": 6, "cli:mcp add": 7, "cli:mcp configure": 1, "cli:mcp remove": 1, "cli:plugins install": 6, "cli:sessions": 11, "cli:skills": 9
  - method: Evidence-based regeneration. Catalog rows derive their status from the live production backend (voice_commands.REGISTRY / _BACKENDS + dashboard_ops.EXTRA_OPS), the declared interfaces (catalog), the live CLI parser tree, the registry voice surface, and an AST scan of tests/ for a proving caller. A plugin/page row declares only the dashboard surface (no CLI/voice by design); it is IMPLEMENTED when
  - sources_reconciled: ["voicebridge/voice_commands.py (REGISTRY/_BACKENDS/CLI_COVERAGE)", "voicebridge/dashboard_ops.py (catalog/EXTRA_OPS)", "voicebridge/cli.py (build_parser)", "voicebridge/hermes_compat.py (ROUTES/GATED)", "tests/ (AST caller evidence)", "frontend/hermes-qubit/plugins/*/manifest.json", "frontend/hermes-web/src/pages/*.tsx", "docs/QUBIT_SOURCE_INVENTORY.json (direct source inventory, agent A)"]

## Counts

- Total rows: **1444**
- Open rows (status != IMPLEMENTED): **498**

### By status

- GATED: 142
- IMPLEMENTED: 946
- MISSING: 345
- PARTIAL: 11

### By source

- hermes: 433
- openclaw: 104
- qubit: 907

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
- automation: 27
- backup: 5
- boards: 6
- brain: 17
- branding: 3
- browser: 8
- browser_profiles: 5
- browser_tabs: 12
- capability: 2
- channels: 7
- chat-websocket: 5
- checkpoints: 2
- cli: 72
- cli-subcommand: 60
- commands: 1
- completions: 1
- computer: 5
- computer_screen: 3
- config-env: 15
- config_profiles: 8
- console_guidance: 1
- cron: 13
- curator: 1
- dashboard: 4
- dashboard-auth: 11
- dashboard-page: 20
- dashboard-ui: 15
- dashboard_plugin: 15
- desktop: 5
- device: 1
- device_enroll: 1
- diagnostics: 1
- doc_extract: 3
- doctor: 1
- editor: 3
- egress: 3
- experiments: 12
- file_transfer: 5
- files: 4
- files-fs: 18
- flags: 1
- frontier: 1
- frontier_chat: 1
- frontier_show: 1
- gateway: 20
- gateway-actions: 6
- ghost: 20
- git: 25
- hermes_backend_route: 1
- hermes_cli_option: 37
- hermes_cli_positional: 8
- hermes_cli_subcommand: 150
- hermes_gated: 30
- hermes_plugin: 10
- hermes_skill: 29
- hermes_tool: 73
- hermes_web_page: 19
- hooks: 10
- ide: 9
- import_agent: 2
- insights: 1
- kanban: 44
- lessons: 3
- local-models: 16
- local_models: 13
- logbook: 2
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
- openclaw_extension: 30
- opencode: 14
- ops: 28
- package: 2
- pairing: 6
- pause: 1
- peer: 7
- permissions: 1
- plugin_runtime: 10
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
- receipts_ledger: 1
- reports: 7
- resume: 1
- runtime: 22
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
- telemetry: 1
- themes: 7
- tools: 13
- tui_guidance: 1
- update_status: 1
- usage: 1
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
- workshop: 6
- worktree: 1

## Source-inventory reconciliation

- Inventory present: **True**
- Unexplained omissions: **338**
- `qubit`: present=True, items=6776, matched=1481, unmatched=338, unexplained=338

## Open rows (status != IMPLEMENTED)

### source: hermes (115 open)

#### hermes / analytics-config (2)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/config/raw` | GATED | creds | GET /api/config/raw | — |
| `api:PUT /api/config/raw` | GATED | creds | PUT /api/config/raw | — |

#### hermes / audio-voice (3)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/audio/elevenlabs/voices` | GATED | creds | GET /api/audio/elevenlabs/voices | — |
| `api:POST /api/audio/tts-lease` | GATED | hardware | POST /api/audio/tts-lease | — |
| `api:WEBSOCKET /api/audio/speak-stream` | GATED | hardware | WEBSOCKET /api/audio/speak-stream | — |

#### hermes / chat-websocket (5)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:WEBSOCKET /api/console` | GATED | owner | WEBSOCKET /api/console | — |
| `api:WEBSOCKET /api/events` | GATED | owner | WEBSOCKET /api/events | tests/test_hermes_vendor8tf.py::test_compat_read_endpoints_smoke |
| `api:WEBSOCKET /api/pty` | GATED | owner | WEBSOCKET /api/pty | — |
| `api:WEBSOCKET /api/pub` | GATED | owner | WEBSOCKET /api/pub | tests/test_hermes_api_parity_wave1.py::test_event_publish_appends_to_single_journal |
| `api:WEBSOCKET /api/ws` | GATED | owner | WEBSOCKET /api/ws | — |

#### hermes / cli (3)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `cli:mcp` | GATED | owner | hermes mcp | tests/test_cli_agent1_forward.py::test_translate_maps_family_and_preserves_trailing_args |
| `cli:sessions` | GATED | owner | hermes sessions | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:skills` | GATED | owner | hermes skills | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |

#### hermes / cli-subcommand (20)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `cli:approvals suggest` | MISSING | owner | hermes approvals suggest | — |
| `cli:approvals test` | MISSING | owner | hermes approvals test | — |
| `cli:config set` | GATED | creds | hermes config set | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:config unset` | GATED | creds | hermes config unset | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:gateway uninstall` | MISSING | owner | hermes gateway uninstall | — |
| `cli:mcp add` | GATED | creds | hermes mcp add | tests/test_cli_agent1_forward.py::test_translate_maps_family_and_preserves_trailing_args |
| `cli:mcp configure` | GATED | creds | hermes mcp configure | tests/test_cli_agent1_forward.py::test_translate_maps_family_and_preserves_trailing_args |
| `cli:mcp remove` | GATED | creds | hermes mcp remove | tests/test_cli_agent1_forward.py::test_translate_maps_family_and_preserves_trailing_args |
| `cli:plugins install` | GATED | creds | hermes plugins install | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:plugins update` | GATED | creds | hermes plugins update | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:profile update` | MISSING | owner | hermes profile update | — |
| `cli:sessions delete` | GATED | creds | hermes sessions delete | tests/test_session_lifecycle8u.py::test_cli_sessions_subcommands_roundtrip |
| `cli:sessions prune` | GATED | creds | hermes sessions prune | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:sessions rename` | GATED | creds | hermes sessions rename | tests/test_session_lifecycle8u.py::test_cli_sessions_subcommands_roundtrip |
| `cli:skills config` | MISSING | owner | hermes skills config | — |
| `cli:skills install` | GATED | creds | hermes skills install | tests/test_cli_agent1_forward.py::test_translate_ignores_unmapped_and_short_calls |
| `cli:skills publish` | GATED | creds | hermes skills publish | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:skills tap` | GATED | creds | hermes skills tap | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:skills update` | GATED | creds | hermes skills update | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:slack manifest` | MISSING | owner | hermes slack manifest | — |

#### hermes / config-env (3)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:POST /api/env/reveal` | GATED | creds | POST /api/env/reveal | — |
| `api:POST /api/providers/custom-endpoints/validate` | GATED | creds | POST /api/providers/custom-endpoints/validate | — |
| `api:POST /api/providers/validate` | GATED | creds | POST /api/providers/validate | — |

#### hermes / cron (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:POST /api/cron/fire` | GATED | owner | POST /api/cron/fire | — |

#### hermes / dashboard-auth (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:POST /api/auth/ws-ticket` | GATED | owner | POST /api/auth/ws-ticket | — |

#### hermes / dashboard-ui (2)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:POST /api/dashboard/agent-plugins/install` | GATED | owner | POST /api/dashboard/agent-plugins/install | — |
| `api:POST /api/dashboard/agent-plugins/{name:path}/update` | GATED | owner | POST /api/dashboard/agent-plugins/{name:path}/update | — |

#### hermes / dashboard_plugin (2)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `plugin:kanban-extra-reads` | MISSING | owner | hermes dashboard plugin kanban auxiliary reads (routes pending) | — |
| `plugin:kanban-write-gate` | GATED | owner | hermes dashboard plugin kanban write surface (typed gate) | tests/test_hermes_plugins.py::test_kanban_writes_stay_on_policy_gate |

#### hermes / files-fs (7)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:DELETE /api/files` | GATED | owner | DELETE /api/files | — |
| `api:GET /api/files/download` | GATED | hardware | GET /api/files/download | — |
| `api:GET /api/files/stream` | GATED | hardware | GET /api/files/stream | — |
| `api:GET /api/fs/download` | GATED | hardware | GET /api/fs/download | — |
| `api:GET /api/media` | GATED | hardware | GET /api/media | — |
| `api:HEAD /api/files/stream` | GATED | hardware | HEAD /api/files/stream | — |
| `api:POST /api/chat/image-upload` | GATED | owner | POST /api/chat/image-upload | — |

#### hermes / gateway-actions (4)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/hermes/update/check` | GATED | owner | GET /api/hermes/update/check | — |
| `api:GET /api/hermes/update/receipt` | GATED | owner | GET /api/hermes/update/receipt | — |
| `api:POST /api/gateway/restart` | GATED | owner | POST /api/gateway/restart | — |
| `api:POST /api/hermes/update` | GATED | owner | POST /api/hermes/update | — |

#### hermes / git (5)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/git/gh-auth` | GATED | creds | GET /api/git/gh-auth | — |
| `api:GET /api/git/review/ship-info` | GATED | creds | GET /api/git/review/ship-info | — |
| `api:POST /api/git/review/create-pr` | GATED | owner | POST /api/git/review/create-pr | — |
| `api:POST /api/git/review/pr-list` | GATED | owner | POST /api/git/review/pr-list | — |
| `api:POST /api/git/review/push` | GATED | owner | POST /api/git/review/push | — |

#### hermes / local-models (11)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:DELETE /api/local-models/models/{model_id}` | GATED | hardware | DELETE /api/local-models/models/{model_id} | — |
| `api:GET /api/local-models/search` | GATED | hardware | GET /api/local-models/search | — |
| `api:GET /api/local-models/search/files` | GATED | hardware | GET /api/local-models/search/files | — |
| `api:POST /api/local-models/activate` | GATED | hardware | POST /api/local-models/activate | tests/test_local_models8u.py::test_every_write_is_a_typed_gate_and_writes_nothing |
| `api:POST /api/local-models/download` | GATED | hardware | POST /api/local-models/download | tests/test_local_models8u.py::test_confirmation_gate_precedes_broker |
| `api:POST /api/local-models/download-browsed` | GATED | hardware | POST /api/local-models/download-browsed | — |
| `api:POST /api/local-models/eject` | GATED | hardware | POST /api/local-models/eject | tests/test_local_models8u.py::test_every_write_is_a_typed_gate_and_writes_nothing |
| `api:POST /api/local-models/quickstart` | GATED | hardware | POST /api/local-models/quickstart | tests/test_local_models8u.py::test_confirmation_gate_precedes_broker |
| `api:POST /api/local-models/runtime/install` | GATED | hardware | POST /api/local-models/runtime/install | tests/test_local_models8u.py::test_confirmation_gate_precedes_broker |
| `api:POST /api/local-models/server` | GATED | hardware | POST /api/local-models/server | tests/test_local_models8u.py::test_estop_latches_writes |
| `api:POST /api/local-models/sideload` | GATED | hardware | POST /api/local-models/sideload | tests/test_local_models8u.py::test_every_write_is_a_typed_gate_and_writes_nothing |

#### hermes / mcp (9)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:DELETE /api/mcp/oauth/flows/{flow_id}` | GATED | creds | DELETE /api/mcp/oauth/flows/{flow_id} | — |
| `api:DELETE /api/mcp/servers/{name}` | GATED | owner | DELETE /api/mcp/servers/{name} | — |
| `api:GET /api/mcp/oauth/callback/{server_name:path}` | GATED | creds | GET /api/mcp/oauth/callback/{server_name:path} | — |
| `api:GET /api/mcp/oauth/flows/{flow_id}` | GATED | creds | GET /api/mcp/oauth/flows/{flow_id} | — |
| `api:POST /api/mcp/catalog/install` | GATED | owner | POST /api/mcp/catalog/install | — |
| `api:POST /api/mcp/servers` | GATED | owner | POST /api/mcp/servers | tests/test_hermes_api_parity_wave1.py::test_mcp_inventory_reads_are_real_and_never_connect |
| `api:POST /api/mcp/servers/{name}/auth` | GATED | creds | POST /api/mcp/servers/{name}/auth | — |
| `api:PUT /api/mcp/servers` | GATED | owner | PUT /api/mcp/servers | — |
| `api:PUT /api/mcp/servers/{name}/enabled` | GATED | owner | PUT /api/mcp/servers/{name}/enabled | — |

#### hermes / memory (2)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/memory/providers/{provider}/oauth/status` | GATED | creds | GET /api/memory/providers/{provider}/oauth/status | — |
| `api:POST /api/memory/providers/{provider}/oauth/start` | GATED | creds | POST /api/memory/providers/{provider}/oauth/start | — |

#### hermes / messaging (8)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:DELETE /api/messaging/telegram/onboarding/{pairing_id}` | GATED | creds | DELETE /api/messaging/telegram/onboarding/{pairing_id} | — |
| `api:DELETE /api/messaging/whatsapp/onboarding/{pairing_id}` | GATED | creds | DELETE /api/messaging/whatsapp/onboarding/{pairing_id} | — |
| `api:GET /api/messaging/telegram/onboarding/{pairing_id}` | GATED | creds | GET /api/messaging/telegram/onboarding/{pairing_id} | — |
| `api:GET /api/messaging/whatsapp/onboarding/{pairing_id}` | GATED | creds | GET /api/messaging/whatsapp/onboarding/{pairing_id} | — |
| `api:POST /api/messaging/telegram/onboarding/start` | GATED | creds | POST /api/messaging/telegram/onboarding/start | — |
| `api:POST /api/messaging/telegram/onboarding/{pairing_id}/apply` | GATED | creds | POST /api/messaging/telegram/onboarding/{pairing_id}/apply | — |
| `api:POST /api/messaging/whatsapp/onboarding/start` | GATED | creds | POST /api/messaging/whatsapp/onboarding/start | — |
| `api:POST /api/messaging/whatsapp/onboarding/{pairing_id}/apply` | GATED | creds | POST /api/messaging/whatsapp/onboarding/{pairing_id}/apply | — |

#### hermes / models (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:PUT /api/model/moa` | GATED | owner | PUT /api/model/moa | — |

#### hermes / ops (6)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:DELETE /api/credentials/pool/{provider}/{index}` | GATED | creds | DELETE /api/credentials/pool/{provider}/{index} | — |
| `api:GET /api/credentials/pool` | GATED | creds | GET /api/credentials/pool | — |
| `api:GET /api/ops/backup/download` | GATED | creds | GET /api/ops/backup/download | — |
| `api:POST /api/credentials/pool` | GATED | creds | POST /api/credentials/pool | — |
| `api:POST /api/ops/checkpoints/prune` | GATED | owner | POST /api/ops/checkpoints/prune | — |
| `api:POST /api/ops/import-upload` | GATED | owner | POST /api/ops/import-upload | — |

#### hermes / profiles (6)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/profiles/{name}/desktop-overlay` | GATED | owner | GET /api/profiles/{name}/desktop-overlay | — |
| `api:PATCH /api/profiles/{name}` | GATED | owner | PATCH /api/profiles/{name} | tests/test_hermes_api_parity_wave1.py::test_wave1_dispatch_non_refusal_success |
| `api:POST /api/profiles/import` | GATED | owner | POST /api/profiles/import | — |
| `api:POST /api/profiles/sessions/pull-requests` | GATED | creds | POST /api/profiles/sessions/pull-requests | — |
| `api:POST /api/profiles/{name}/describe-auto` | GATED | owner | POST /api/profiles/{name}/describe-auto | — |
| `api:POST /api/profiles/{name}/open-terminal` | GATED | owner | POST /api/profiles/{name}/open-terminal | — |

#### hermes / provider-oauth (6)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:DELETE /api/providers/oauth/sessions/{session_id}` | GATED | creds | DELETE /api/providers/oauth/sessions/{session_id} | — |
| `api:DELETE /api/providers/oauth/{provider_id}` | GATED | creds | DELETE /api/providers/oauth/{provider_id} | — |
| `api:GET /api/providers/oauth` | GATED | creds | GET /api/providers/oauth | — |
| `api:GET /api/providers/oauth/{provider_id}/poll/{session_id}` | GATED | creds | GET /api/providers/oauth/{provider_id}/poll/{session_id} | — |
| `api:POST /api/providers/oauth/{provider_id}/start` | GATED | creds | POST /api/providers/oauth/{provider_id}/start | — |
| `api:POST /api/providers/oauth/{provider_id}/submit` | GATED | creds | POST /api/providers/oauth/{provider_id}/submit | — |

#### hermes / sessions (4)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:POST /api/sessions/bulk-delete` | GATED | owner | POST /api/sessions/bulk-delete | — |
| `api:POST /api/sessions/import` | GATED | owner | POST /api/sessions/import | — |
| `api:POST /api/sessions/owner-backfill` | GATED | owner | POST /api/sessions/owner-backfill | — |
| `api:POST /api/sessions/prune` | GATED | owner | POST /api/sessions/prune | — |

#### hermes / status-curator (4)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/portal` | GATED | owner | GET /api/portal | — |
| `api:GET /api/ssh/ownership` | GATED | owner | GET /api/ssh/ownership | — |
| `api:POST /api/curator/run` | GATED | owner | POST /api/curator/run | — |
| `api:PUT /api/curator/paused` | GATED | owner | PUT /api/curator/paused | — |

### source: openclaw (10 open)

#### openclaw / boards (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-37` | GATED | owner | Workboard dispatch + worker runs | tests/test_kanban8tf.py |

#### openclaw / desktop (2)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-13` | GATED | hardware | Real desktop control (Peekaboo/CUA) | tests/test_computer_use8tf.py |
| `OC-14` | GATED | hardware | Supervised ghost action framework (screenshot/key/cursor/window) | tests/test_desktop_parity8u.py |

#### openclaw / gateway (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-01` | GATED | owner | Gateway protocol server + RPC v4 | tests/test_openclaw_parity_wave1.py |

#### openclaw / messaging (3)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-19` | PARTIAL | creds | Broad channel set (~29 plugins) | tests/test_openclaw_closure_wave2_messaging.py |
| `OC-D15` | PARTIAL | creds | iMessage private API, media, rich messages | tests/test_openclaw_closure_wave3.py |
| `OC-D16` | PARTIAL | creds | Feishu dynamic agents, advanced configuration | tests/test_openclaw_closure_wave3.py |

#### openclaw / reports (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-51` | PARTIAL | hardware | Logbook screen work-journal | tests/test_openclaw_closure_wave2_webmedia.py |

#### openclaw / runtime (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-56` | PARTIAL | hardware | Paired node host (screen/audio/camera/media playback/presence) | tests/test_openclaw_closure_wave2_runtime.py |

#### openclaw / sessions (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-48` | PARTIAL | creds | Markdown workspace memory / dreaming / external providers (LanceDB/Wiki/Honcho) | tests/test_openclaw_closure_wave2_webmedia.py |

### source: qubit (373 open)

#### qubit / browser (2)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `browser_close` | PARTIAL | hardware | Browser close-profile | tests/test_browser8tf.py::test_close_profile_redacts_name |
| `browser_close_refusal` | PARTIAL | hardware | Close browser profile | tests/test_browser8tf.py::test_close_profile_redacts_name |

#### qubit / computer (3)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `computer_doctor` | PARTIAL | hardware | Desktop health | tests/test_computer_use8tf.py::test_doctor_checklist_typed |
| `computer_install_refusal` | PARTIAL | hardware | Desktop driver install | tests/test_computer_use8tf.py::test_doctor_checklist_typed |
| `computer_permissions` | PARTIAL | hardware | Desktop permissions | tests/test_computer_use8tf.py::test_permissions_record |

#### qubit / hermes_backend_route (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:api:GET_/assets/{filename}.css` | MISSING | owner | GET /assets/{filename}.css | — |

#### qubit / hermes_cli_option (37)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:hermes_config_set_--force` | MISSING | owner | --force | — |
| `inv:hermes_mcp_add_--args` | MISSING | owner | --args | — |
| `inv:hermes_mcp_add_--auth` | MISSING | creds | --auth | — |
| `inv:hermes_mcp_add_--command` | MISSING | owner | --command | — |
| `inv:hermes_mcp_add_--connect-timeout` | MISSING | owner | --connect-timeout | — |
| `inv:hermes_mcp_add_--env` | MISSING | owner | --env | — |
| `inv:hermes_mcp_add_--preset` | MISSING | owner | --preset | — |
| `inv:hermes_mcp_add_--url` | MISSING | owner | --url | — |
| `inv:hermes_mcp_login_--flow` | MISSING | owner | --flow | — |
| `inv:hermes_mcp_reauth_--all` | MISSING | creds | --all | — |
| `inv:hermes_mcp_serve_--verbose` | MISSING | owner | --verbose | — |
| `inv:hermes_mcp_serve_-v` | MISSING | owner | -v | — |
| `inv:hermes_plugins_install_--enable` | MISSING | owner | --enable | — |
| `inv:hermes_plugins_install_--force` | MISSING | owner | --force | — |
| `inv:hermes_plugins_install_--no-enable` | MISSING | owner | --no-enable | — |
| `inv:hermes_plugins_install_--ref` | MISSING | owner | --ref | — |
| `inv:hermes_plugins_install_-f` | MISSING | owner | -f | — |
| `inv:hermes_sessions_browse_--limit` | MISSING | owner | --limit | — |
| `inv:hermes_sessions_browse_--source` | MISSING | owner | --source | — |
| `inv:hermes_sessions_import_--from` | MISSING | owner | --from | — |
| `inv:hermes_sessions_recover_--chunk-size` | MISSING | owner | --chunk-size | — |
| `inv:hermes_sessions_recover_--output` | MISSING | owner | --output | — |
| `inv:hermes_sessions_recover_--report` | MISSING | owner | --report | — |
| `inv:hermes_sessions_recover_--source` | MISSING | owner | --source | — |
| `inv:hermes_sessions_recover_--work-dir` | MISSING | owner | --work-dir | — |
| `inv:hermes_sessions_repair-routing_--max-gap-seconds` | MISSING | owner | --max-gap-seconds | — |
| `inv:hermes_sessions_retitle-skills_--limit` | MISSING | owner | --limit | — |
| `inv:hermes_skills_browse_--page` | MISSING | owner | --page | — |
| `inv:hermes_skills_browse_--size` | MISSING | owner | --size | — |
| `inv:hermes_skills_browse_--source` | MISSING | owner | --source | — |
| `inv:hermes_skills_install_--category` | MISSING | owner | --category | — |
| `inv:hermes_skills_install_--name` | MISSING | owner | --name | — |
| `inv:hermes_skills_list_--source` | MISSING | owner | --source | — |
| `inv:hermes_skills_publish_--repo` | MISSING | owner | --repo | — |
| `inv:hermes_skills_publish_--to` | MISSING | owner | --to | — |
| `inv:hermes_skills_search_--limit` | MISSING | owner | --limit | — |
| `inv:hermes_skills_search_--source` | MISSING | owner | --source | — |

#### qubit / hermes_cli_positional (8)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:hermes_mcp_install_identifier` | MISSING | owner | identifier | — |
| `inv:hermes_plugins_install_identifier` | MISSING | owner | identifier | — |
| `inv:hermes_sessions_unpin_session_ids` | MISSING | owner | session_ids | — |
| `inv:hermes_skills_inspect_identifier` | MISSING | owner | identifier | — |
| `inv:hermes_skills_install_identifier` | MISSING | owner | identifier | — |
| `inv:hermes_skills_publish_skill_path` | MISSING | owner | skill_path | — |
| `inv:hermes_skills_search_query` | MISSING | owner | query | — |
| `inv:hermes_skills_snapshot_import_input` | MISSING | owner | input | — |

#### qubit / hermes_cli_subcommand (150)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:hermes_approvals_suggest` | MISSING | owner | hermes approvals suggest | — |
| `inv:hermes_approvals_test` | MISSING | owner | hermes approvals test | — |
| `inv:hermes_auth_add` | MISSING | creds | hermes auth add | — |
| `inv:hermes_auth_list` | MISSING | creds | hermes auth list | — |
| `inv:hermes_auth_logout` | MISSING | creds | hermes auth logout | — |
| `inv:hermes_auth_priority` | MISSING | creds | hermes auth priority | — |
| `inv:hermes_auth_refresh` | MISSING | creds | hermes auth refresh | — |
| `inv:hermes_auth_remove` | MISSING | creds | hermes auth remove | — |
| `inv:hermes_auth_reset` | MISSING | creds | hermes auth reset | — |
| `inv:hermes_auth_spotify` | MISSING | creds | hermes auth spotify | — |
| `inv:hermes_auth_status` | MISSING | creds | hermes auth status | — |
| `inv:hermes_browser_close-profile` | MISSING | hardware | hermes browser close-profile | — |
| `inv:hermes_claw_cleanup` | MISSING | owner | hermes claw cleanup | — |
| `inv:hermes_claw_migrate` | MISSING | owner | hermes claw migrate | — |
| `inv:hermes_computer-use_doctor` | MISSING | hardware | hermes computer-use doctor | — |
| `inv:hermes_computer-use_install` | MISSING | hardware | hermes computer-use install | — |
| `inv:hermes_computer-use_permissions` | MISSING | hardware | hermes computer-use permissions | — |
| `inv:hermes_computer-use_permissions_grant` | MISSING | hardware | hermes computer-use permissions grant | — |
| `inv:hermes_computer-use_permissions_status` | MISSING | hardware | hermes computer-use permissions status | — |
| `inv:hermes_computer-use_status` | MISSING | hardware | hermes computer-use status | — |
| `inv:hermes_cron_create` | MISSING | owner | hermes cron create | — |
| `inv:hermes_cron_incidents` | MISSING | owner | hermes cron incidents | — |
| `inv:hermes_cron_notepad` | MISSING | owner | hermes cron notepad | — |
| `inv:hermes_cron_remove` | MISSING | owner | hermes cron remove | — |
| `inv:hermes_cron_runs` | MISSING | owner | hermes cron runs | — |
| `inv:hermes_dashboard_register` | MISSING | owner | hermes dashboard register | — |
| `inv:hermes_debug_delete` | MISSING | owner | hermes debug delete | — |
| `inv:hermes_debug_share` | MISSING | owner | hermes debug share | — |
| `inv:hermes_fallback_add` | MISSING | owner | hermes fallback add | — |
| `inv:hermes_fallback_clear` | MISSING | owner | hermes fallback clear | — |
| `inv:hermes_fallback_list` | MISSING | owner | hermes fallback list | — |
| `inv:hermes_fallback_remove` | MISSING | owner | hermes fallback remove | — |
| `inv:hermes_gateway_enroll` | MISSING | owner | hermes gateway enroll | — |
| `inv:hermes_gateway_install` | MISSING | owner | hermes gateway install | — |
| `inv:hermes_gateway_list` | MISSING | owner | hermes gateway list | — |
| `inv:hermes_gateway_migrate-legacy` | MISSING | owner | hermes gateway migrate-legacy | — |
| `inv:hermes_gateway_restart` | MISSING | owner | hermes gateway restart | — |
| `inv:hermes_gateway_run` | MISSING | owner | hermes gateway run | — |
| `inv:hermes_gateway_setup` | MISSING | owner | hermes gateway setup | — |
| `inv:hermes_gateway_start` | MISSING | owner | hermes gateway start | — |
| `inv:hermes_gateway_status` | MISSING | owner | hermes gateway status | — |
| `inv:hermes_gateway_stop` | MISSING | owner | hermes gateway stop | — |
| `inv:hermes_gateway_uninstall` | MISSING | owner | hermes gateway uninstall | — |
| `inv:hermes_hooks_doctor` | MISSING | owner | hermes hooks doctor | — |
| `inv:hermes_hooks_list` | MISSING | owner | hermes hooks list | — |
| `inv:hermes_hooks_revoke` | MISSING | owner | hermes hooks revoke | — |
| `inv:hermes_hooks_test` | MISSING | owner | hermes hooks test | — |
| `inv:hermes_mcp_config` | MISSING | owner | hermes mcp config | — |
| `inv:hermes_mcp_install` | MISSING | owner | hermes mcp install | — |
| `inv:hermes_mcp_list` | MISSING | owner | hermes mcp list | — |
| `inv:hermes_mcp_login` | MISSING | owner | hermes mcp login | — |
| `inv:hermes_mcp_ls` | MISSING | owner | hermes mcp ls | — |
| `inv:hermes_mcp_picker` | MISSING | owner | hermes mcp picker | — |
| `inv:hermes_mcp_reauth` | MISSING | creds | hermes mcp reauth | — |
| `inv:hermes_mcp_rm` | MISSING | owner | hermes mcp rm | — |
| `inv:hermes_mcp_serve` | MISSING | owner | hermes mcp serve | — |
| `inv:hermes_memory_off` | MISSING | owner | hermes memory off | — |
| `inv:hermes_memory_reset` | MISSING | owner | hermes memory reset | — |
| `inv:hermes_memory_setup` | MISSING | owner | hermes memory setup | — |
| `inv:hermes_memory_status` | MISSING | owner | hermes memory status | — |
| `inv:hermes_migrate_xai` | MISSING | owner | hermes migrate xai | — |
| `inv:hermes_moa_configure` | MISSING | owner | hermes moa configure | — |
| `inv:hermes_moa_delete` | MISSING | owner | hermes moa delete | — |
| `inv:hermes_moa_list` | MISSING | owner | hermes moa list | — |
| `inv:hermes_monitoring_status` | MISSING | owner | hermes monitoring status | — |
| `inv:hermes_pairing_approve` | MISSING | hardware | hermes pairing approve | — |
| `inv:hermes_pairing_clear-pending` | MISSING | hardware | hermes pairing clear-pending | — |
| `inv:hermes_pairing_list` | MISSING | hardware | hermes pairing list | — |
| `inv:hermes_pairing_revoke` | MISSING | hardware | hermes pairing revoke | — |
| `inv:hermes_peer_add` | MISSING | owner | hermes peer add | — |
| `inv:hermes_peer_list` | MISSING | owner | hermes peer list | — |
| `inv:hermes_peer_remove` | MISSING | owner | hermes peer remove | — |
| `inv:hermes_plugins_compat` | MISSING | owner | hermes plugins compat | — |
| `inv:hermes_plugins_disable` | MISSING | owner | hermes plugins disable | — |
| `inv:hermes_plugins_list` | MISSING | owner | hermes plugins list | — |
| `inv:hermes_plugins_pack` | MISSING | owner | hermes plugins pack | — |
| `inv:hermes_plugins_pack_export` | MISSING | owner | hermes plugins pack export | — |
| `inv:hermes_plugins_pack_install` | MISSING | owner | hermes plugins pack install | — |
| `inv:hermes_plugins_pack_show` | MISSING | owner | hermes plugins pack show | — |
| `inv:hermes_plugins_search` | MISSING | owner | hermes plugins search | — |
| `inv:hermes_plugins_show` | MISSING | owner | hermes plugins show | — |
| `inv:hermes_profile_alias` | MISSING | owner | hermes profile alias | — |
| `inv:hermes_profile_create` | MISSING | owner | hermes profile create | — |
| `inv:hermes_profile_delete` | MISSING | owner | hermes profile delete | — |
| `inv:hermes_profile_describe` | MISSING | owner | hermes profile describe | — |
| `inv:hermes_profile_export` | MISSING | owner | hermes profile export | — |
| `inv:hermes_profile_import` | MISSING | owner | hermes profile import | — |
| `inv:hermes_profile_info` | MISSING | owner | hermes profile info | — |
| `inv:hermes_profile_install` | MISSING | owner | hermes profile install | — |
| `inv:hermes_profile_list` | MISSING | owner | hermes profile list | — |
| `inv:hermes_profile_rename` | MISSING | owner | hermes profile rename | — |
| `inv:hermes_profile_show` | MISSING | owner | hermes profile show | — |
| `inv:hermes_profile_update` | MISSING | owner | hermes profile update | — |
| `inv:hermes_profile_use` | MISSING | owner | hermes profile use | — |
| `inv:hermes_project_create` | MISSING | owner | hermes project create | — |
| `inv:hermes_project_list` | MISSING | owner | hermes project list | — |
| `inv:hermes_project_use` | MISSING | owner | hermes project use | — |
| `inv:hermes_proxy_providers` | MISSING | creds | hermes proxy providers | — |
| `inv:hermes_proxy_start` | MISSING | owner | hermes proxy start | — |
| `inv:hermes_proxy_status` | MISSING | owner | hermes proxy status | — |
| `inv:hermes_secrets_bitwarden` | MISSING | creds | hermes secrets bitwarden | — |
| `inv:hermes_secrets_onepassword` | MISSING | creds | hermes secrets onepassword | — |
| `inv:hermes_security_audit` | MISSING | owner | hermes security audit | — |
| `inv:hermes_sessions_archive` | MISSING | owner | hermes sessions archive | — |
| `inv:hermes_sessions_browse` | MISSING | owner | hermes sessions browse | — |
| `inv:hermes_sessions_import` | MISSING | owner | hermes sessions import | — |
| `inv:hermes_sessions_pinned` | MISSING | owner | hermes sessions pinned | — |
| `inv:hermes_sessions_recover` | MISSING | owner | hermes sessions recover | — |
| `inv:hermes_sessions_repair` | MISSING | owner | hermes sessions repair | — |
| `inv:hermes_sessions_repair-routing` | MISSING | owner | hermes sessions repair-routing | — |
| `inv:hermes_sessions_retitle-skills` | MISSING | owner | hermes sessions retitle-skills | — |
| `inv:hermes_sessions_unpin` | MISSING | owner | hermes sessions unpin | — |
| `inv:hermes_skills_browse` | MISSING | owner | hermes skills browse | — |
| `inv:hermes_skills_check` | MISSING | owner | hermes skills check | — |
| `inv:hermes_skills_config` | MISSING | owner | hermes skills config | — |
| `inv:hermes_skills_diff` | MISSING | owner | hermes skills diff | — |
| `inv:hermes_skills_inspect` | MISSING | owner | hermes skills inspect | — |
| `inv:hermes_skills_list` | MISSING | owner | hermes skills list | — |
| `inv:hermes_skills_list-modified` | MISSING | owner | hermes skills list-modified | — |
| `inv:hermes_skills_opt-in` | MISSING | owner | hermes skills opt-in | — |
| `inv:hermes_skills_opt-out` | MISSING | owner | hermes skills opt-out | — |
| `inv:hermes_skills_repair-official` | MISSING | owner | hermes skills repair-official | — |
| `inv:hermes_skills_reset` | MISSING | owner | hermes skills reset | — |
| `inv:hermes_skills_search` | MISSING | owner | hermes skills search | — |
| `inv:hermes_skills_snapshot` | MISSING | owner | hermes skills snapshot | — |
| `inv:hermes_skills_snapshot_export` | MISSING | owner | hermes skills snapshot export | — |
| `inv:hermes_skills_snapshot_import` | MISSING | owner | hermes skills snapshot import | — |
| `inv:hermes_skills_tap_add` | MISSING | owner | hermes skills tap add | — |
| `inv:hermes_skills_tap_list` | MISSING | owner | hermes skills tap list | — |
| `inv:hermes_skills_tap_remove` | MISSING | owner | hermes skills tap remove | — |
| `inv:hermes_skills_trust` | MISSING | owner | hermes skills trust | — |
| `inv:hermes_skills_uninstall` | MISSING | owner | hermes skills uninstall | — |
| `inv:hermes_skills_untrust` | MISSING | owner | hermes skills untrust | — |
| `inv:hermes_skin_list` | MISSING | owner | hermes skin list | — |
| `inv:hermes_skin_set` | MISSING | owner | hermes skin set | — |
| `inv:hermes_skin_use` | MISSING | owner | hermes skin use | — |
| `inv:hermes_slack_manifest` | MISSING | creds | hermes slack manifest | — |
| `inv:hermes_sync_device` | MISSING | hardware | hermes sync device | — |
| `inv:hermes_sync_disable` | MISSING | owner | hermes sync disable | — |
| `inv:hermes_sync_enable` | MISSING | owner | hermes sync enable | — |
| `inv:hermes_sync_now` | MISSING | owner | hermes sync now | — |
| `inv:hermes_sync_propose` | MISSING | owner | hermes sync propose | — |
| `inv:hermes_sync_pull` | MISSING | owner | hermes sync pull | — |
| `inv:hermes_sync_push` | MISSING | owner | hermes sync push | — |
| `inv:hermes_sync_status` | MISSING | owner | hermes sync status | — |
| `inv:hermes_webhook_list` | MISSING | creds | hermes webhook list | — |
| `inv:hermes_webhook_remove` | MISSING | creds | hermes webhook remove | — |
| `inv:hermes_webhook_subscribe` | MISSING | creds | hermes webhook subscribe | — |
| `inv:hermes_webhook_test` | MISSING | creds | hermes webhook test | — |
| `inv:hermes_worktree_prune` | MISSING | owner | hermes worktree prune | — |

#### qubit / hermes_gated (30)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `hermes_gate_audio_lease_desktop` | GATED | owner | Gated upstream route: AUDIO_LEASE_DESKTOP | — |
| `hermes_gate_audio_stream_local` | GATED | owner | Gated upstream route: AUDIO_STREAM_LOCAL | — |
| `hermes_gate_backup_passphrase` | GATED | owner | Gated upstream route: BACKUP_PASSPHRASE | — |
| `hermes_gate_blocked_remote_source` | GATED | owner | Gated upstream route: BLOCKED_REMOTE_SOURCE | — |
| `hermes_gate_chat_image_overlay` | GATED | owner | Gated upstream route: CHAT_IMAGE_OVERLAY | — |
| `hermes_gate_checkpoint_confirm_required` | GATED | owner | Gated upstream route: CHECKPOINT_CONFIRM_REQUIRED | — |
| `hermes_gate_cron_fire_external` | GATED | owner | Gated upstream route: CRON_FIRE_EXTERNAL | — |
| `hermes_gate_curator_on_demand` | GATED | owner | Gated upstream route: CURATOR_ON_DEMAND | — |
| `hermes_gate_desktop_overlay_only` | GATED | owner | Gated upstream route: DESKTOP_OVERLAY_ONLY | — |
| `hermes_gate_files_binary_stream` | GATED | owner | Gated upstream route: FILES_BINARY_STREAM | — |
| `hermes_gate_gateway_no_supervision` | GATED | owner | Gated upstream route: GATEWAY_NO_SUPERVISION | — |
| `hermes_gate_gh_auth_operator` | GATED | owner | Gated upstream route: GH_AUTH_OPERATOR | — |
| `hermes_gate_gh_pr_external` | GATED | owner | Gated upstream route: GH_PR_EXTERNAL | — |
| `hermes_gate_git_ship_gh_external` | GATED | owner | Gated upstream route: GIT_SHIP_GH_EXTERNAL | — |
| `hermes_gate_git_write_brokered` | GATED | owner | Gated upstream route: GIT_WRITE_BROKERED | — |
| `hermes_gate_kanban_write_brokered` | GATED | owner | Gated upstream route: KANBAN_WRITE_BROKERED | — |
| `hermes_gate_mcp_no_mount` | GATED | owner | Gated upstream route: MCP_NO_MOUNT | — |
| `hermes_gate_media_not_served` | GATED | owner | Gated upstream route: MEDIA_NOT_SERVED | — |
| `hermes_gate_network_probe_blocked` | GATED | owner | Gated upstream route: NETWORK_PROBE_BLOCKED | — |
| `hermes_gate_oauth_manual_handoff` | GATED | owner | Gated upstream route: OAUTH_MANUAL_HANDOFF | — |
| `hermes_gate_onboarding_guided` | GATED | owner | Gated upstream route: ONBOARDING_GUIDED | — |
| `hermes_gate_plugin_providers_qubit` | GATED | owner | Gated upstream route: PLUGIN_PROVIDERS_QUBIT | — |
| `hermes_gate_profile_import_review` | GATED | owner | Gated upstream route: PROFILE_IMPORT_REVIEW | — |
| `hermes_gate_provider_external` | GATED | owner | Gated upstream route: PROVIDER_EXTERNAL | — |
| `hermes_gate_pty_no_shell` | GATED | owner | Gated upstream route: PTY_NO_SHELL | — |
| `hermes_gate_secret_never_served` | GATED | owner | Gated upstream route: SECRET_NEVER_SERVED | — |
| `hermes_gate_sessions_receipt_store` | GATED | owner | Gated upstream route: SESSIONS_RECEIPT_STORE | — |
| `hermes_gate_ssh_ownership_operator` | GATED | owner | Gated upstream route: SSH_OWNERSHIP_OPERATOR | — |
| `hermes_gate_update_operator_owned` | GATED | owner | Gated upstream route: UPDATE_OPERATOR_OWNED | — |
| `hermes_gate_ws_single_path` | GATED | owner | Gated upstream route: WS_SINGLE_PATH | — |

#### qubit / hermes_plugin (10)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:plugin:context_engine` | MISSING | owner | context_engine | — |
| `inv:plugin:cron_providers` | MISSING | creds | cron_providers | — |
| `inv:plugin:disk-cleanup` | MISSING | owner | disk-cleanup | — |
| `inv:plugin:google_meet` | MISSING | owner | google_meet | — |
| `inv:plugin:image_gen` | MISSING | owner | image_gen | — |
| `inv:plugin:observability` | MISSING | owner | observability | — |
| `inv:plugin:security-guidance` | MISSING | owner | security-guidance | — |
| `inv:plugin:spotify` | MISSING | owner | spotify | — |
| `inv:plugin:teams_pipeline` | MISSING | owner | teams_pipeline | — |
| `inv:plugin:video_gen` | MISSING | owner | video_gen | — |

#### qubit / hermes_skill (29)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:skill:optional-skills/autonomous-ai-agents` | MISSING | owner | autonomous-ai-agents | — |
| `inv:skill:optional-skills/blockchain` | MISSING | owner | blockchain | — |
| `inv:skill:optional-skills/communication` | MISSING | owner | communication | — |
| `inv:skill:optional-skills/creative` | MISSING | owner | creative | — |
| `inv:skill:optional-skills/data-science` | MISSING | owner | data-science | — |
| `inv:skill:optional-skills/devops` | MISSING | owner | devops | — |
| `inv:skill:optional-skills/dogfood` | MISSING | owner | dogfood | — |
| `inv:skill:optional-skills/email` | MISSING | creds | email | — |
| `inv:skill:optional-skills/finance` | MISSING | owner | finance | — |
| `inv:skill:optional-skills/gaming` | MISSING | owner | gaming | — |
| `inv:skill:optional-skills/mlops` | MISSING | owner | mlops | — |
| `inv:skill:optional-skills/payments` | MISSING | owner | payments | — |
| `inv:skill:optional-skills/productivity` | MISSING | owner | productivity | — |
| `inv:skill:optional-skills/research` | MISSING | owner | research | — |
| `inv:skill:optional-skills/smart-home` | MISSING | owner | smart-home | — |
| `inv:skill:optional-skills/software-development` | MISSING | owner | software-development | — |
| `inv:skill:optional-skills/web-development` | MISSING | owner | web-development | — |
| `inv:skill:optional-skills/yuanbao` | MISSING | owner | yuanbao | — |
| `inv:skill:skills/apple` | MISSING | owner | apple | — |
| `inv:skill:skills/autonomous-ai-agents` | MISSING | owner | autonomous-ai-agents | — |
| `inv:skill:skills/creative` | MISSING | owner | creative | — |
| `inv:skill:skills/devops` | MISSING | owner | devops | — |
| `inv:skill:skills/email` | MISSING | creds | email | — |
| `inv:skill:skills/index-cache` | MISSING | owner | index-cache | — |
| `inv:skill:skills/note-taking` | MISSING | owner | note-taking | — |
| `inv:skill:skills/productivity` | MISSING | owner | productivity | — |
| `inv:skill:skills/research` | MISSING | owner | research | — |
| `inv:skill:skills/social-media` | MISSING | hardware | social-media | — |
| `inv:skill:skills/software-development` | MISSING | owner | software-development | — |

#### qubit / hermes_tool (73)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:tool:annotate_preview` | MISSING | owner | annotate_preview | — |
| `inv:tool:apply_layout` | MISSING | owner | apply_layout | — |
| `inv:tool:browser_back` | MISSING | hardware | browser_back | — |
| `inv:tool:browser_cdp` | MISSING | hardware | browser_cdp | — |
| `inv:tool:browser_console` | MISSING | hardware | browser_console | — |
| `inv:tool:browser_dialog` | MISSING | hardware | browser_dialog | — |
| `inv:tool:browser_exec` | MISSING | hardware | browser_exec | — |
| `inv:tool:browser_get_images` | MISSING | hardware | browser_get_images | — |
| `inv:tool:browser_press` | MISSING | hardware | browser_press | — |
| `inv:tool:browser_scroll` | MISSING | hardware | browser_scroll | — |
| `inv:tool:browser_snapshot` | MISSING | hardware | browser_snapshot | — |
| `inv:tool:browser_type` | MISSING | hardware | browser_type | — |
| `inv:tool:browser_vision` | MISSING | hardware | browser_vision | — |
| `inv:tool:clarify` | MISSING | owner | clarify | — |
| `inv:tool:close_terminal` | MISSING | owner | close_terminal | — |
| `inv:tool:cronjob_manage` | MISSING | owner | cronjob_manage | — |
| `inv:tool:delegate_task` | MISSING | owner | delegate_task | — |
| `inv:tool:desktop_preview` | MISSING | hardware | desktop_preview | — |
| `inv:tool:desktop_project` | MISSING | hardware | desktop_project | — |
| `inv:tool:discord_admin` | MISSING | creds | discord_admin | — |
| `inv:tool:drive_preview` | MISSING | owner | drive_preview | — |
| `inv:tool:execute_code` | MISSING | owner | execute_code | — |
| `inv:tool:feishu_doc_read` | MISSING | owner | feishu_doc_read | — |
| `inv:tool:feishu_drive_add_comment` | MISSING | owner | feishu_drive_add_comment | — |
| `inv:tool:feishu_drive_list_comment_replies` | MISSING | owner | feishu_drive_list_comment_replies | — |
| `inv:tool:feishu_drive_list_comments` | MISSING | owner | feishu_drive_list_comments | — |
| `inv:tool:feishu_drive_reply_comment` | MISSING | owner | feishu_drive_reply_comment | — |
| `inv:tool:focus_pane` | MISSING | owner | focus_pane | — |
| `inv:tool:gui_tour` | MISSING | owner | gui_tour | — |
| `inv:tool:ha_call_service` | MISSING | owner | ha_call_service | — |
| `inv:tool:ha_get_state` | MISSING | owner | ha_get_state | — |
| `inv:tool:ha_list_entities` | MISSING | owner | ha_list_entities | — |
| `inv:tool:ha_list_services` | MISSING | owner | ha_list_services | — |
| `inv:tool:image_gen` | MISSING | owner | image_gen | — |
| `inv:tool:image_generate` | MISSING | owner | image_generate | — |
| `inv:tool:kanban_attach` | MISSING | owner | kanban_attach | — |
| `inv:tool:kanban_attach_url` | MISSING | owner | kanban_attach_url | — |
| `inv:tool:kanban_attachments` | MISSING | owner | kanban_attachments | — |
| `inv:tool:kanban_heartbeat` | MISSING | owner | kanban_heartbeat | — |
| `inv:tool:kanban_link` | MISSING | owner | kanban_link | — |
| `inv:tool:kanban_request_changes` | MISSING | owner | kanban_request_changes | — |
| `inv:tool:kanban_request_review` | MISSING | owner | kanban_request_review | — |
| `inv:tool:process_manage` | MISSING | owner | process_manage | — |
| `inv:tool:react_to_message` | MISSING | owner | react_to_message | — |
| `inv:tool:read_terminal` | MISSING | owner | read_terminal | — |
| `inv:tool:read_window_below` | MISSING | owner | read_window_below | — |
| `inv:tool:search_files` | MISSING | owner | search_files | — |
| `inv:tool:session_search` | MISSING | owner | session_search | — |
| `inv:tool:setup_mcp` | MISSING | owner | setup_mcp | — |
| `inv:tool:show_tip` | MISSING | owner | show_tip | — |
| `inv:tool:skill_manage` | MISSING | owner | skill_manage | — |
| `inv:tool:skill_view` | MISSING | owner | skill_view | — |
| `inv:tool:spotify_albums` | MISSING | owner | spotify_albums | — |
| `inv:tool:spotify_devices` | MISSING | hardware | spotify_devices | — |
| `inv:tool:spotify_library` | MISSING | owner | spotify_library | — |
| `inv:tool:spotify_playback` | MISSING | owner | spotify_playback | — |
| `inv:tool:spotify_playlists` | MISSING | owner | spotify_playlists | — |
| `inv:tool:spotify_queue` | MISSING | owner | spotify_queue | — |
| `inv:tool:spotify_search` | MISSING | owner | spotify_search | — |
| `inv:tool:text_to_speech` | MISSING | owner | text_to_speech | — |
| `inv:tool:todo_list` | MISSING | owner | todo_list | — |
| `inv:tool:video_analyze` | MISSING | owner | video_analyze | — |
| `inv:tool:video_generate` | MISSING | owner | video_generate | — |
| `inv:tool:vision_analyze` | MISSING | hardware | vision_analyze | — |
| `inv:tool:web_extract` | MISSING | owner | web_extract | — |
| `inv:tool:write_file` | MISSING | owner | write_file | — |
| `inv:tool:xai_video_edit` | MISSING | owner | xai_video_edit | — |
| `inv:tool:xai_video_extend` | MISSING | owner | xai_video_extend | — |
| `inv:tool:yb_query_group_info` | MISSING | owner | yb_query_group_info | — |
| `inv:tool:yb_query_group_members` | MISSING | owner | yb_query_group_members | — |
| `inv:tool:yb_search_sticker` | MISSING | owner | yb_search_sticker | — |
| `inv:tool:yb_send_dm` | MISSING | owner | yb_send_dm | — |
| `inv:tool:yb_send_sticker` | MISSING | owner | yb_send_sticker | — |

#### qubit / openclaw_extension (30)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:openclaw-ext:a2a` | MISSING | owner | a2a | — |
| `inv:openclaw-ext:buzz` | MISSING | owner | buzz | — |
| `inv:openclaw-ext:clickclack` | MISSING | owner | clickclack | — |
| `inv:openclaw-ext:cua-computer` | MISSING | hardware | cua-computer | — |
| `inv:openclaw-ext:device-pair` | MISSING | hardware | device-pair | — |
| `inv:openclaw-ext:diffs` | MISSING | owner | diffs | — |
| `inv:openclaw-ext:geolocation` | MISSING | owner | geolocation | — |
| `inv:openclaw-ext:googlechat` | MISSING | owner | googlechat | — |
| `inv:openclaw-ext:image-generation-core` | MISSING | owner | image-generation-core | — |
| `inv:openclaw-ext:imap` | MISSING | creds | imap | — |
| `inv:openclaw-ext:irc` | MISSING | owner | irc | — |
| `inv:openclaw-ext:line` | MISSING | owner | line | — |
| `inv:openclaw-ext:llm-task` | MISSING | owner | llm-task | — |
| `inv:openclaw-ext:lobster` | MISSING | owner | lobster | — |
| `inv:openclaw-ext:mattermost` | MISSING | owner | mattermost | — |
| `inv:openclaw-ext:msteams` | MISSING | owner | msteams | — |
| `inv:openclaw-ext:nextcloud-talk` | MISSING | owner | nextcloud-talk | — |
| `inv:openclaw-ext:nostr` | MISSING | owner | nostr | — |
| `inv:openclaw-ext:oc-path` | MISSING | owner | oc-path | — |
| `inv:openclaw-ext:qa-channel` | MISSING | creds | qa-channel | — |
| `inv:openclaw-ext:raft` | MISSING | owner | raft | — |
| `inv:openclaw-ext:reef` | MISSING | owner | reef | — |
| `inv:openclaw-ext:sms` | MISSING | owner | sms | — |
| `inv:openclaw-ext:synology-chat` | MISSING | owner | synology-chat | — |
| `inv:openclaw-ext:test-support` | MISSING | owner | test-support | — |
| `inv:openclaw-ext:tlon` | MISSING | owner | tlon | — |
| `inv:openclaw-ext:twitch` | MISSING | owner | twitch | — |
| `inv:openclaw-ext:visitor-access` | MISSING | owner | visitor-access | — |
| `inv:openclaw-ext:zalo` | MISSING | owner | zalo | — |
| `inv:openclaw-ext:zalouser` | MISSING | owner | zalouser | — |
