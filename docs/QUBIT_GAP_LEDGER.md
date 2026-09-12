# Qubit gap ledger

Machine-generated from `docs/QUBIT_GAP_LEDGER.*.json`. This merged report is the authoritative view; the input JSON sections are never modified.

> **COMPLETION GATE:** NOT COMPLETE while any row has status != IMPLEMENTED.

## Provenance pins

- `docs/QUBIT_GAP_LEDGER.hermes.json` — schema `qubit-gap-ledger-section/1`, source `hermes`, 421 rows
  - counts: {"by_area": {"analytics-config": 4, "audio-voice": 6, "chat-websocket": 5, "cli": 72, "cli-subcommand": 50, "config-env": 15, "cron": 13, "dashboard-auth": 11, "dashboard-page": 20, "dashboard-ui": 15, "dashboard_plugin": 3, "files-fs": 18, "gateway-actions": 6, "git": 21, "local-models": 16, "mcp": 12, "memory": 5, "messaging": 11, "models": 7, "ops": 28, "profiles": 20, "provider-oauth": 6, "ses
  - generated: 2026-09-12
  - method: Evidence-based regeneration. API rows re-extract the pinned upstream route decorators and classify them from voicebridge.hermes_compat.match disposition plus the handler's real behaviour (a handler whose normal exits are typed gates/refusals is GATED; a real read/write is IMPLEMENTED; a wire-shape stub is PARTIAL). IMPLEMENTED additionally requires an on-disk tests/ function that dispatches the ro
  - sources_reconciled: ["voicebridge/hermes_compat.py", "voicebridge/hermes_plugins.py", "voicebridge/cli.py", "voicebridge/cli_hermes_forward.py", "tests/ (AST caller evidence)", "tests/test_hermes_plugins.py (plugin-fleet proof)", "qubit_hermes/hermes_cli/web_routers", "qubit_hermes/hermes_cli/web_server*.py", "qubit_hermes/hermes_cli/memory_oauth.py", "qubit_hermes/hermes_cli/dashboard_auth/routes.py", "qubit_hermes/
  - upstream_pin: 2237be355906fbe6065ce1815711eee52b2d646e
- `docs/QUBIT_GAP_LEDGER.openclaw.json` — schema `qubit-gap-ledger-section/1`, source `openclaw`, 104 rows
  - counts: {"by_area": {"automation": 10, "boards": 6, "browser": 5, "dashboard": 4, "desktop": 5, "gateway": 10, "ide": 7, "messaging": 11, "policy": 2, "reports": 6, "runtime": 9, "sessions": 10, "skills": 7, "web_media": 9, "webhooks": 3}, "by_status": {"GATED": 4, "IMPLEMENTED": 94, "PARTIAL": 6}, "total": 104}
  - generated: 2026-09-12
  - method: Evidence-based regeneration. The audited capability inventory (72 rows @v2026.9.2 + 32 delta rows @v2026.9.4) is embedded as a baseline; a documented ROW_EVIDENCE table records the rows closed or upgraded by real Qubit modules + behavioural tests. Every IMPLEMENTED row is mechanically re-verified against the code and an on-disk proving test that references the cited backend; a row upgraded from an
  - sources_reconciled: ["docs/AUDIT_OPENCLAW_2026-09-11.md", "docs/AUDIT_OPENCLAW_V2026_9_4_DELTA.md", "voicebridge/automation.py", "voicebridge/schedules.py", "tests/test_automation_agent6_*.py", "tests/test_openclaw_e5_directives.py", "tests/test_oc_*_agent[5-9]*.py (Agent 5-10 witness tests)", "tests/ (proving-test evidence)"]
  - upstream_pin: 3a9d69db306cd7f081e06254cb89c4bcc14a7107
  - upstream_tag: v2026.9.4
  - upstream_tree: /tmp/opencode/pinned/openclaw (read via git show 3a9d69db306cd7f081e06254cb89c4bcc14a7107, shallow clone)
- `docs/QUBIT_GAP_LEDGER.qubit.json` — schema `qubit-gap-ledger-section/1`, source `qubit-surface`, 1788 rows
  - generated_from: cli_families=106, dashboard_ops.EXTRA_OPS=34, dashboard_ops.catalog=510, hermes_compat.GATED=108, hermes_compat.ROUTES=361, source_inventory.items=6776, source_inventory.surface_items=1819, voice_commands.CLI_COVERAGE=106, voice_commands.REGISTRY=476
  - evidence: production backend + declared interfaces + live CLI tree + voice registry + linked tests/ AST proofs + direct source inventory
  - generator: scripts/rebuild_ledger_qubit.py
  - backend_flags: []
  - generated: 2026-09-12
  - inventory: {"by_category": {"hermes_backend_route": 1, "hermes_cli_option": 351, "hermes_cli_positional": 34, "hermes_cli_subcommand": 179, "hermes_dashboard_component": 14, "hermes_dashboard_dialog": 10, "hermes_dashboard_form": 2, "hermes_dashboard_nav": 1, "hermes_dashboard_plugin": 1, "hermes_model": 211, "hermes_plugin": 11, "hermes_plugin_api": 1, "hermes_plugin_asset": 4, "hermes_plugin_test": 1, "her
  - method: Evidence-based regeneration. Catalog rows derive their status from the live production backend (voice_commands.REGISTRY / _BACKENDS + dashboard_ops.EXTRA_OPS), the declared interfaces (catalog), the live CLI parser tree, the registry voice surface, and an AST scan of tests/ for a proving caller. A plugin/page row declares only the dashboard surface (no CLI/voice by design); it is IMPLEMENTED when
  - sources_reconciled: ["voicebridge/voice_commands.py (REGISTRY/_BACKENDS/CLI_COVERAGE)", "voicebridge/dashboard_ops.py (catalog/EXTRA_OPS)", "voicebridge/cli.py (build_parser)", "voicebridge/hermes_compat.py (ROUTES/GATED)", "tests/ (AST caller evidence)", "frontend/hermes-qubit/plugins/*/manifest.json", "frontend/hermes-web/src/pages/*.tsx", "docs/QUBIT_SOURCE_INVENTORY.json (direct source inventory, agent A)"]

## Counts

- Total rows: **2313**
- Open rows (status != IMPLEMENTED): **1374**

### By status

- GATED: 155
- IMPLEMENTED: 939
- MISSING: 1207
- PARTIAL: 12

### By source

- hermes: 421
- openclaw: 104
- qubit: 1788

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
- browser: 31
- browser_profiles: 5
- browser_tabs: 12
- capability: 2
- channels: 7
- chat-websocket: 5
- checkpoints: 2
- cli: 72
- cli-subcommand: 50
- coding: 6
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
- dashboard_plugin: 13
- desktop: 8
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
- hermes_cli_option: 351
- hermes_cli_positional: 34
- hermes_cli_subcommand: 179
- hermes_dashboard_component: 14
- hermes_dashboard_dialog: 10
- hermes_dashboard_form: 2
- hermes_dashboard_nav: 1
- hermes_dashboard_plugin: 1
- hermes_gated: 42
- hermes_model: 211
- hermes_plugin: 11
- hermes_plugin_api: 1
- hermes_plugin_asset: 4
- hermes_plugin_test: 1
- hermes_provider: 38
- hermes_skill: 29
- hermes_tool: 83
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
- memory: 30
- messaging: 30
- mic_guidance: 1
- migrate_status: 1
- models: 7
- monitor: 1
- msg: 14
- node_host: 7
- openclaw_extension: 132
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
- reports: 8
- resume: 1
- runtime: 41
- schedules: 30
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
- Unexplained omissions: **1207**
- `qubit`: present=True, items=6776, matched=612, unmatched=1207, unexplained=1207

## Open rows (status != IMPLEMENTED)

### source: hermes (110 open)

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

#### hermes / cli-subcommand (14)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `cli:config set` | GATED | creds | hermes config set | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:config unset` | GATED | creds | hermes config unset | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:mcp add` | GATED | creds | hermes mcp add | tests/test_cli_agent1_forward.py::test_translate_maps_family_and_preserves_trailing_args |
| `cli:mcp configure` | GATED | creds | hermes mcp configure | tests/test_cli_agent1_forward.py::test_translate_maps_family_and_preserves_trailing_args |
| `cli:mcp remove` | GATED | creds | hermes mcp remove | tests/test_cli_agent1_forward.py::test_translate_maps_family_and_preserves_trailing_args |
| `cli:plugins install` | GATED | creds | hermes plugins install | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:plugins update` | GATED | creds | hermes plugins update | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:sessions delete` | GATED | creds | hermes sessions delete | tests/test_session_lifecycle8u.py::test_cli_sessions_subcommands_roundtrip |
| `cli:sessions prune` | GATED | creds | hermes sessions prune | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:sessions rename` | GATED | creds | hermes sessions rename | tests/test_session_lifecycle8u.py::test_cli_sessions_subcommands_roundtrip |
| `cli:skills install` | GATED | creds | hermes skills install | tests/test_cli_agent1_forward.py::test_translate_ignores_unmapped_and_short_calls |
| `cli:skills publish` | GATED | creds | hermes skills publish | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:skills tap` | GATED | creds | hermes skills tap | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:skills update` | GATED | creds | hermes skills update | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |

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

#### hermes / dashboard-ui (4)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:POST /api/dashboard/agent-plugins/install` | GATED | owner | POST /api/dashboard/agent-plugins/install | — |
| `api:POST /api/dashboard/agent-plugins/{name:path}/disable` | GATED | owner | POST /api/dashboard/agent-plugins/{name:path}/disable | — |
| `api:POST /api/dashboard/agent-plugins/{name:path}/enable` | GATED | owner | POST /api/dashboard/agent-plugins/{name:path}/enable | — |
| `api:POST /api/dashboard/agent-plugins/{name:path}/update` | GATED | owner | POST /api/dashboard/agent-plugins/{name:path}/update | — |

#### hermes / dashboard_plugin (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `plugin:kanban` | PARTIAL | owner | hermes dashboard plugin kanban (read API) | tests/test_hermes_plugins.py::test_kanban_read_routes_serve_real_board |

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
| `api:PUT /api/mcp/servers` | GATED | owner | PUT /api/mcp/servers | tests/test_hermes_api_parity_wave1.py::test_mcp_inventory_reads_are_real_and_never_connect |
| `api:PUT /api/mcp/servers/{name}/enabled` | GATED | owner | PUT /api/mcp/servers/{name}/enabled | tests/test_hermes_routes_parity8u.py::test_wrong_verb_rows_now_typed_not_404 |

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
| `api:POST /api/ops/import-upload` | GATED | owner | POST /api/ops/import-upload | tests/test_dashboard_ops8tf.py::test_ops_http_end_to_end |

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

### source: qubit (1254 open)

#### qubit / browser (25)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `browser_close` | PARTIAL | hardware | Browser close-profile | tests/test_browser8tf.py::test_close_profile_redacts_name |
| `browser_close_refusal` | PARTIAL | hardware | Close browser profile | tests/test_browser8tf.py::test_close_profile_redacts_name |
| `inv:workflow:browser:hermes_cli/browser_connect.py` | MISSING | hardware | hermes_cli/browser_connect.py | — |
| `inv:workflow:browser:tools/browser_camofox.py` | MISSING | hardware | tools/browser_camofox.py | — |
| `inv:workflow:browser:tools/browser_camofox_state.py` | MISSING | hardware | tools/browser_camofox_state.py | — |
| `inv:workflow:browser:tools/browser_cdp_tool.py` | MISSING | hardware | tools/browser_cdp_tool.py | — |
| `inv:workflow:browser:tools/browser_dialog_tool.py` | MISSING | hardware | tools/browser_dialog_tool.py | — |
| `inv:workflow:browser:tools/browser_extension_router.py` | MISSING | hardware | tools/browser_extension_router.py | — |
| `inv:workflow:browser:tools/browser_lightpanda.py` | MISSING | hardware | tools/browser_lightpanda.py | — |
| `inv:workflow:browser:tools/browser_supervisor.py` | MISSING | hardware | tools/browser_supervisor.py | — |
| `inv:workflow:browser:tools/browser_supervisor_dialogs.py` | MISSING | hardware | tools/browser_supervisor_dialogs.py | — |
| `inv:workflow:browser:tools/browser_supervisor_frames.py` | MISSING | hardware | tools/browser_supervisor_frames.py | — |
| `inv:workflow:browser:tools/browser_tool.py` | MISSING | hardware | tools/browser_tool.py | — |
| `inv:workflow:browser:tools/browser_tool_cdp.py` | MISSING | hardware | tools/browser_tool_cdp.py | — |
| `inv:workflow:browser:tools/browser_tool_cloud.py` | MISSING | hardware | tools/browser_tool_cloud.py | — |
| `inv:workflow:browser:tools/browser_tool_eval_policy.py` | MISSING | hardware | tools/browser_tool_eval_policy.py | — |
| `inv:workflow:browser:tools/browser_tool_install.py` | MISSING | hardware | tools/browser_tool_install.py | — |
| `inv:workflow:browser:tools/browser_tool_lifecycle.py` | MISSING | hardware | tools/browser_tool_lifecycle.py | — |
| `inv:workflow:browser:tools/browser_tool_lightpanda_fallback.py` | MISSING | hardware | tools/browser_tool_lightpanda_fallback.py | — |
| `inv:workflow:browser:tools/browser_tool_origin.py` | MISSING | hardware | tools/browser_tool_origin.py | — |
| `inv:workflow:browser:tools/browser_tool_real_profile.py` | MISSING | hardware | tools/browser_tool_real_profile.py | — |
| `inv:workflow:browser:tools/browser_tool_session.py` | MISSING | hardware | tools/browser_tool_session.py | — |
| `inv:workflow:browser:tools/browser_tool_snapshot.py` | MISSING | hardware | tools/browser_tool_snapshot.py | — |
| `inv:workflow:browser:tools/browser_tool_vision.py` | MISSING | hardware | tools/browser_tool_vision.py | — |
| `inv:workflow:browser:tools/browser_use_cli.py` | MISSING | hardware | tools/browser_use_cli.py | — |

#### qubit / coding (6)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:workflow:coding:hermes_cli/claw.py` | MISSING | owner | hermes_cli/claw.py | — |
| `inv:workflow:coding:tools/code_execution_env.py` | MISSING | owner | tools/code_execution_env.py | — |
| `inv:workflow:coding:tools/code_execution_rpc.py` | MISSING | owner | tools/code_execution_rpc.py | — |
| `inv:workflow:coding:tools/code_execution_tool.py` | MISSING | owner | tools/code_execution_tool.py | — |
| `inv:workflow:coding:tools/code_kernel.py` | MISSING | owner | tools/code_kernel.py | — |
| `inv:workflow:coding:tools/code_kernel_remote.py` | MISSING | owner | tools/code_kernel_remote.py | — |

#### qubit / computer (3)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `computer_doctor` | PARTIAL | hardware | Desktop health | tests/test_computer_use8tf.py::test_doctor_checklist_typed |
| `computer_install_refusal` | PARTIAL | hardware | Desktop driver install | tests/test_computer_use8tf.py::test_doctor_checklist_typed |
| `computer_permissions` | PARTIAL | hardware | Desktop permissions | tests/test_computer_use8tf.py::test_permissions_record |

#### qubit / desktop (3)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:workflow:desktop:hermes_cli/main_desktop.py` | MISSING | hardware | hermes_cli/main_desktop.py | — |
| `inv:workflow:desktop:tools/computer_use_tool.py` | MISSING | hardware | tools/computer_use_tool.py | — |
| `inv:workflow:desktop:tools/desktop_ui.py` | MISSING | hardware | tools/desktop_ui.py | — |

#### qubit / hermes_backend_route (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:api:GET_/assets/{filename}.css` | MISSING | owner | GET /assets/{filename}.css | — |

#### qubit / hermes_cli_option (351)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:hermes_acp_--check` | MISSING | owner | --check | — |
| `inv:hermes_acp_--setup` | MISSING | owner | --setup | — |
| `inv:hermes_acp_--setup-browser` | MISSING | hardware | --setup-browser | — |
| `inv:hermes_acp_--version` | MISSING | owner | --version | — |
| `inv:hermes_acp_--yes` | MISSING | owner | --yes | — |
| `inv:hermes_acp_-y` | MISSING | owner | -y | — |
| `inv:hermes_approvals_suggest_--days` | MISSING | owner | --days | — |
| `inv:hermes_approvals_suggest_--db` | MISSING | owner | --db | — |
| `inv:hermes_approvals_suggest_--limit` | MISSING | owner | --limit | — |
| `inv:hermes_approvals_suggest_--min-count` | MISSING | owner | --min-count | — |
| `inv:hermes_approvals_test_--env-type` | MISSING | owner | --env-type | — |
| `inv:hermes_auth_add_--api-key` | MISSING | creds | --api-key | — |
| `inv:hermes_auth_add_--ca-bundle` | MISSING | creds | --ca-bundle | — |
| `inv:hermes_auth_add_--client-id` | MISSING | creds | --client-id | — |
| `inv:hermes_auth_add_--inference-url` | MISSING | creds | --inference-url | — |
| `inv:hermes_auth_add_--insecure` | MISSING | creds | --insecure | — |
| `inv:hermes_auth_add_--label` | MISSING | creds | --label | — |
| `inv:hermes_auth_add_--no-browser` | MISSING | hardware | --no-browser | — |
| `inv:hermes_auth_add_--portal-url` | MISSING | creds | --portal-url | — |
| `inv:hermes_auth_add_--priority` | MISSING | creds | --priority | — |
| `inv:hermes_auth_add_--scope` | MISSING | creds | --scope | — |
| `inv:hermes_auth_add_--timeout` | MISSING | creds | --timeout | — |
| `inv:hermes_auth_add_--type` | MISSING | creds | --type | — |
| `inv:hermes_auth_spotify_--client-id` | MISSING | creds | --client-id | — |
| `inv:hermes_auth_spotify_--no-browser` | MISSING | hardware | --no-browser | — |
| `inv:hermes_auth_spotify_--redirect-uri` | MISSING | creds | --redirect-uri | — |
| `inv:hermes_auth_spotify_--scope` | MISSING | creds | --scope | — |
| `inv:hermes_auth_spotify_--timeout` | MISSING | creds | --timeout | — |
| `inv:hermes_backup_--label` | MISSING | owner | --label | — |
| `inv:hermes_backup_--output` | MISSING | owner | --output | — |
| `inv:hermes_backup_--quick` | MISSING | owner | --quick | — |
| `inv:hermes_backup_-l` | MISSING | owner | -l | — |
| `inv:hermes_backup_-o` | MISSING | owner | -o | — |
| `inv:hermes_backup_-q` | MISSING | owner | -q | — |
| `inv:hermes_browser_close-profile_--browser` | MISSING | hardware | --browser | — |
| `inv:hermes_chat_--checkpoints` | MISSING | owner | --checkpoints | — |
| `inv:hermes_chat_--cli` | MISSING | owner | --cli | — |
| `inv:hermes_chat_--continue` | MISSING | owner | --continue | — |
| `inv:hermes_chat_--create-if-missing` | MISSING | owner | --create-if-missing | — |
| `inv:hermes_chat_--dev` | MISSING | owner | --dev | — |
| `inv:hermes_chat_--in` | MISSING | owner | --in | — |
| `inv:hermes_chat_--max-turns` | MISSING | owner | --max-turns | — |
| `inv:hermes_chat_--model` | MISSING | creds | --model | — |
| `inv:hermes_chat_--no-restore-cwd` | MISSING | owner | --no-restore-cwd | — |
| `inv:hermes_chat_--oneshot` | MISSING | owner | --oneshot | — |
| `inv:hermes_chat_--pass-session-id` | MISSING | owner | --pass-session-id | — |
| `inv:hermes_chat_--provider` | MISSING | creds | --provider | — |
| `inv:hermes_chat_--query` | MISSING | owner | --query | — |
| `inv:hermes_chat_--query-file` | MISSING | owner | --query-file | — |
| `inv:hermes_chat_--quiet` | MISSING | owner | --quiet | — |
| `inv:hermes_chat_--resume` | MISSING | owner | --resume | — |
| `inv:hermes_chat_--run-budget` | MISSING | owner | --run-budget | — |
| `inv:hermes_chat_--safe-mode` | MISSING | owner | --safe-mode | — |
| `inv:hermes_chat_--skills` | MISSING | owner | --skills | — |
| `inv:hermes_chat_--source` | MISSING | owner | --source | — |
| `inv:hermes_chat_--toolsets` | MISSING | owner | --toolsets | — |
| `inv:hermes_chat_--verbose` | MISSING | owner | --verbose | — |
| `inv:hermes_chat_-Q` | MISSING | owner | -Q | — |
| `inv:hermes_chat_-c` | MISSING | owner | -c | — |
| `inv:hermes_chat_-m` | MISSING | owner | -m | — |
| `inv:hermes_chat_-q` | MISSING | owner | -q | — |
| `inv:hermes_chat_-r` | MISSING | owner | -r | — |
| `inv:hermes_chat_-s` | MISSING | owner | -s | — |
| `inv:hermes_chat_-t` | MISSING | owner | -t | — |
| `inv:hermes_chat_-v` | MISSING | owner | -v | — |
| `inv:hermes_chat_-w` | MISSING | owner | -w | — |
| `inv:hermes_claw_cleanup_--dry-run` | MISSING | owner | --dry-run | — |
| `inv:hermes_claw_cleanup_--source` | MISSING | owner | --source | — |
| `inv:hermes_claw_migrate_--dry-run` | MISSING | owner | --dry-run | — |
| `inv:hermes_claw_migrate_--migrate-secrets` | MISSING | creds | --migrate-secrets | — |
| `inv:hermes_claw_migrate_--no-backup` | MISSING | owner | --no-backup | — |
| `inv:hermes_claw_migrate_--overwrite` | MISSING | owner | --overwrite | — |
| `inv:hermes_claw_migrate_--preset` | MISSING | owner | --preset | — |
| `inv:hermes_claw_migrate_--skill-conflict` | MISSING | owner | --skill-conflict | — |
| `inv:hermes_claw_migrate_--source` | MISSING | owner | --source | — |
| `inv:hermes_claw_migrate_--workspace-target` | MISSING | owner | --workspace-target | — |
| `inv:hermes_computer-use_doctor_--include` | MISSING | hardware | --include | — |
| `inv:hermes_computer-use_doctor_--skip` | MISSING | hardware | --skip | — |
| `inv:hermes_computer-use_install_--upgrade` | MISSING | hardware | --upgrade | — |
| `inv:hermes_config_set_--force` | MISSING | owner | --force | — |
| `inv:hermes_cron_create_--continuity` | MISSING | owner | --continuity | — |
| `inv:hermes_cron_create_--deliver` | MISSING | owner | --deliver | — |
| `inv:hermes_cron_create_--failure-deliver` | MISSING | owner | --failure-deliver | — |
| `inv:hermes_cron_create_--model` | MISSING | creds | --model | — |
| `inv:hermes_cron_create_--monitor-script` | MISSING | owner | --monitor-script | — |
| `inv:hermes_cron_create_--monitor-url` | MISSING | owner | --monitor-url | — |
| `inv:hermes_cron_create_--name` | MISSING | owner | --name | — |
| `inv:hermes_cron_create_--paused-reason` | MISSING | owner | --paused-reason | — |
| `inv:hermes_cron_create_--provider` | MISSING | creds | --provider | — |
| `inv:hermes_cron_create_--reasoning-effort` | MISSING | owner | --reasoning-effort | — |
| `inv:hermes_cron_create_--repeat` | MISSING | owner | --repeat | — |
| `inv:hermes_cron_create_--script` | MISSING | owner | --script | — |
| `inv:hermes_cron_create_--skill` | MISSING | owner | --skill | — |
| `inv:hermes_cron_create_--workdir` | MISSING | owner | --workdir | — |
| `inv:hermes_cron_edit_--add-skill` | MISSING | owner | --add-skill | — |
| `inv:hermes_cron_edit_--agent` | MISSING | owner | --agent | — |
| `inv:hermes_cron_edit_--continuity` | MISSING | owner | --continuity | — |
| `inv:hermes_cron_edit_--deliver` | MISSING | owner | --deliver | — |
| `inv:hermes_cron_edit_--failure-deliver` | MISSING | owner | --failure-deliver | — |
| `inv:hermes_cron_edit_--model` | MISSING | creds | --model | — |
| `inv:hermes_cron_edit_--monitor-script` | MISSING | owner | --monitor-script | — |
| `inv:hermes_cron_edit_--monitor-url` | MISSING | owner | --monitor-url | — |
| `inv:hermes_cron_edit_--name` | MISSING | owner | --name | — |
| `inv:hermes_cron_edit_--no-agent` | MISSING | owner | --no-agent | — |
| `inv:hermes_cron_edit_--no-continuity` | MISSING | owner | --no-continuity | — |
| `inv:hermes_cron_edit_--prompt` | MISSING | owner | --prompt | — |
| `inv:hermes_cron_edit_--provider` | MISSING | creds | --provider | — |
| `inv:hermes_cron_edit_--reasoning-effort` | MISSING | owner | --reasoning-effort | — |
| `inv:hermes_cron_edit_--remove-skill` | MISSING | owner | --remove-skill | — |
| `inv:hermes_cron_edit_--repeat` | MISSING | owner | --repeat | — |
| `inv:hermes_cron_edit_--schedule` | MISSING | owner | --schedule | — |
| `inv:hermes_cron_edit_--script` | MISSING | owner | --script | — |
| `inv:hermes_cron_edit_--skill` | MISSING | owner | --skill | — |
| `inv:hermes_cron_edit_--workdir` | MISSING | owner | --workdir | — |
| `inv:hermes_cron_incidents_--state` | MISSING | owner | --state | — |
| `inv:hermes_cron_resume_--at` | MISSING | owner | --at | — |
| `inv:hermes_cron_runs_--limit` | MISSING | owner | --limit | — |
| `inv:hermes_dashboard_--no-open` | MISSING | owner | --no-open | — |
| `inv:hermes_dashboard_register_--name` | MISSING | owner | --name | — |
| `inv:hermes_dashboard_register_--portal-url` | MISSING | owner | --portal-url | — |
| `inv:hermes_dashboard_register_--redirect-uri` | MISSING | owner | --redirect-uri | — |
| `inv:hermes_debug_share_--expire` | MISSING | owner | --expire | — |
| `inv:hermes_debug_share_--lines` | MISSING | owner | --lines | — |
| `inv:hermes_debug_share_--local` | MISSING | owner | --local | — |
| `inv:hermes_debug_share_--no-redact` | MISSING | owner | --no-redact | — |
| `inv:hermes_debug_share_--nous` | MISSING | owner | --nous | — |
| `inv:hermes_debug_share_--yes` | MISSING | owner | --yes | — |
| `inv:hermes_debug_share_-y` | MISSING | owner | -y | — |
| `inv:hermes_desktop_--build-only` | MISSING | hardware | --build-only | — |
| `inv:hermes_desktop_--cwd` | MISSING | hardware | --cwd | — |
| `inv:hermes_desktop_--fake-boot` | MISSING | hardware | --fake-boot | — |
| `inv:hermes_desktop_--force-build` | MISSING | hardware | --force-build | — |
| `inv:hermes_desktop_--hermes-root` | MISSING | hardware | --hermes-root | — |
| `inv:hermes_desktop_--identity` | MISSING | hardware | --identity | — |
| `inv:hermes_desktop_--ignore-existing` | MISSING | hardware | --ignore-existing | — |
| `inv:hermes_desktop_--local` | MISSING | hardware | --local | — |
| `inv:hermes_desktop_--setup-tcc-identity` | MISSING | hardware | --setup-tcc-identity | — |
| `inv:hermes_desktop_--skip-build` | MISSING | hardware | --skip-build | — |
| `inv:hermes_desktop_--source` | MISSING | hardware | --source | — |
| `inv:hermes_doctor_--ack` | MISSING | owner | --ack | — |
| `inv:hermes_doctor_--fix` | MISSING | owner | --fix | — |
| `inv:hermes_doctor_--live` | MISSING | owner | --live | — |
| `inv:hermes_dump_--show-keys` | MISSING | owner | --show-keys | — |
| `inv:hermes_gateway_enroll_--connector-url` | MISSING | owner | --connector-url | — |
| `inv:hermes_gateway_enroll_--gateway-id` | MISSING | owner | --gateway-id | — |
| `inv:hermes_gateway_enroll_--token` | MISSING | creds | --token | — |
| `inv:hermes_gateway_enroll_--wake-url` | MISSING | hardware | --wake-url | — |
| `inv:hermes_gateway_install_--no-start-now` | MISSING | owner | --no-start-now | — |
| `inv:hermes_gateway_install_--no-start-on-login` | MISSING | owner | --no-start-on-login | — |
| `inv:hermes_gateway_install_--run-as-user` | MISSING | owner | --run-as-user | — |
| `inv:hermes_gateway_run_--verbose` | MISSING | owner | --verbose | — |
| `inv:hermes_gateway_run_-v` | MISSING | owner | -v | — |
| `inv:hermes_hooks_test_--for-tool` | MISSING | owner | --for-tool | — |
| `inv:hermes_hooks_test_--payload-file` | MISSING | owner | --payload-file | — |
| `inv:hermes_import-agent_--dry-run` | MISSING | owner | --dry-run | — |
| `inv:hermes_import-agent_--overwrite` | MISSING | owner | --overwrite | — |
| `inv:hermes_import-agent_--source` | MISSING | owner | --source | — |
| `inv:hermes_import_--force` | MISSING | owner | --force | — |
| `inv:hermes_import_-f` | MISSING | owner | -f | — |
| `inv:hermes_insights_--days` | MISSING | owner | --days | — |
| `inv:hermes_insights_--source` | MISSING | owner | --source | — |
| `inv:hermes_kanban_--board` | MISSING | owner | --board | — |
| `inv:hermes_login_--ca-bundle` | MISSING | owner | --ca-bundle | — |
| `inv:hermes_login_--client-id` | MISSING | owner | --client-id | — |
| `inv:hermes_login_--inference-url` | MISSING | owner | --inference-url | — |
| `inv:hermes_login_--insecure` | MISSING | owner | --insecure | — |
| `inv:hermes_login_--no-browser` | MISSING | hardware | --no-browser | — |
| `inv:hermes_login_--portal-url` | MISSING | owner | --portal-url | — |
| `inv:hermes_login_--provider` | MISSING | creds | --provider | — |
| `inv:hermes_login_--scope` | MISSING | owner | --scope | — |
| `inv:hermes_login_--timeout` | MISSING | owner | --timeout | — |
| `inv:hermes_logout_--provider` | MISSING | creds | --provider | — |
| `inv:hermes_logs_--component` | MISSING | owner | --component | — |
| `inv:hermes_logs_--follow` | MISSING | owner | --follow | — |
| `inv:hermes_logs_--level` | MISSING | owner | --level | — |
| `inv:hermes_logs_--lines` | MISSING | owner | --lines | — |
| `inv:hermes_logs_--session` | MISSING | owner | --session | — |
| `inv:hermes_logs_--since` | MISSING | owner | --since | — |
| `inv:hermes_logs_-f` | MISSING | owner | -f | — |
| `inv:hermes_logs_-n` | MISSING | owner | -n | — |
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
| `inv:hermes_memory_reset_--target` | MISSING | owner | --target | — |
| `inv:hermes_migrate_xai_--no-backup` | MISSING | owner | --no-backup | — |
| `inv:hermes_model_--ca-bundle` | MISSING | creds | --ca-bundle | — |
| `inv:hermes_model_--client-id` | MISSING | creds | --client-id | — |
| `inv:hermes_model_--inference-url` | MISSING | creds | --inference-url | — |
| `inv:hermes_model_--insecure` | MISSING | creds | --insecure | — |
| `inv:hermes_model_--no-browser` | MISSING | hardware | --no-browser | — |
| `inv:hermes_model_--portal-url` | MISSING | creds | --portal-url | — |
| `inv:hermes_model_--refresh` | MISSING | creds | --refresh | — |
| `inv:hermes_model_--scope` | MISSING | creds | --scope | — |
| `inv:hermes_model_--timeout` | MISSING | creds | --timeout | — |
| `inv:hermes_pause_--reason` | MISSING | owner | --reason | — |
| `inv:hermes_peer_add_--key` | MISSING | owner | --key | — |
| `inv:hermes_peer_add_--note` | MISSING | owner | --note | — |
| `inv:hermes_peer_add_--url` | MISSING | owner | --url | — |
| `inv:hermes_plugins_compat_--json` | MISSING | owner | --json | — |
| `inv:hermes_plugins_doctor_--ci` | MISSING | owner | --ci | — |
| `inv:hermes_plugins_enable_--allow-tool-override` | MISSING | owner | --allow-tool-override | — |
| `inv:hermes_plugins_enable_--no-allow-tool-override` | MISSING | owner | --no-allow-tool-override | — |
| `inv:hermes_plugins_install_--enable` | MISSING | owner | --enable | — |
| `inv:hermes_plugins_install_--force` | MISSING | owner | --force | — |
| `inv:hermes_plugins_install_--no-enable` | MISSING | owner | --no-enable | — |
| `inv:hermes_plugins_install_--ref` | MISSING | owner | --ref | — |
| `inv:hermes_plugins_install_-f` | MISSING | owner | -f | — |
| `inv:hermes_plugins_list_--enabled` | MISSING | owner | --enabled | — |
| `inv:hermes_plugins_list_--no-bundled` | MISSING | owner | --no-bundled | — |
| `inv:hermes_plugins_list_--plain` | MISSING | owner | --plain | — |
| `inv:hermes_plugins_list_--user` | MISSING | owner | --user | — |
| `inv:hermes_plugins_pack_export_--enabled-only` | MISSING | owner | --enabled-only | — |
| `inv:hermes_plugins_pack_export_--name` | MISSING | owner | --name | — |
| `inv:hermes_plugins_pack_install_--force` | MISSING | owner | --force | — |
| `inv:hermes_plugins_pack_install_-f` | MISSING | owner | -f | — |
| `inv:hermes_plugins_search_--capability` | MISSING | owner | --capability | — |
| `inv:hermes_plugins_search_--refresh` | MISSING | owner | --refresh | — |
| `inv:hermes_profile_alias_--name` | MISSING | owner | --name | — |
| `inv:hermes_profile_alias_--remove` | MISSING | owner | --remove | — |
| `inv:hermes_profile_create_--clone` | MISSING | owner | --clone | — |
| `inv:hermes_profile_create_--clone-all` | MISSING | owner | --clone-all | — |
| `inv:hermes_profile_create_--clone-from` | MISSING | owner | --clone-from | — |
| `inv:hermes_profile_create_--description` | MISSING | owner | --description | — |
| `inv:hermes_profile_create_--no-alias` | MISSING | owner | --no-alias | — |
| `inv:hermes_profile_create_--no-skills` | MISSING | owner | --no-skills | — |
| `inv:hermes_profile_delete_--yes` | MISSING | owner | --yes | — |
| `inv:hermes_profile_delete_-y` | MISSING | owner | -y | — |
| `inv:hermes_profile_describe_--all` | MISSING | owner | --all | — |
| `inv:hermes_profile_describe_--auto` | MISSING | owner | --auto | — |
| `inv:hermes_profile_describe_--overwrite` | MISSING | owner | --overwrite | — |
| `inv:hermes_profile_describe_--text` | MISSING | owner | --text | — |
| `inv:hermes_profile_export_--output` | MISSING | owner | --output | — |
| `inv:hermes_profile_export_-o` | MISSING | owner | -o | — |
| `inv:hermes_profile_import_--name` | MISSING | owner | --name | — |
| `inv:hermes_profile_install_--alias` | MISSING | owner | --alias | — |
| `inv:hermes_profile_install_--force` | MISSING | owner | --force | — |
| `inv:hermes_profile_install_--name` | MISSING | owner | --name | — |
| `inv:hermes_profile_install_--yes` | MISSING | owner | --yes | — |
| `inv:hermes_profile_install_-y` | MISSING | owner | -y | — |
| `inv:hermes_profile_update_--force-config` | MISSING | owner | --force-config | — |
| `inv:hermes_profile_update_--yes` | MISSING | owner | --yes | — |
| `inv:hermes_profile_update_-y` | MISSING | owner | -y | — |
| `inv:hermes_project_create_--board` | MISSING | owner | --board | — |
| `inv:hermes_project_create_--primary` | MISSING | owner | --primary | — |
| `inv:hermes_project_create_--slug` | MISSING | owner | --slug | — |
| `inv:hermes_project_create_--use` | MISSING | owner | --use | — |
| `inv:hermes_project_list_--all` | MISSING | owner | --all | — |
| `inv:hermes_prompt-size_--platform` | MISSING | owner | --platform | — |
| `inv:hermes_proxy_start_--host` | MISSING | owner | --host | — |
| `inv:hermes_proxy_start_--port` | MISSING | owner | --port | — |
| `inv:hermes_proxy_start_--provider` | MISSING | creds | --provider | — |
| `inv:hermes_security_audit_--fail-on` | MISSING | owner | --fail-on | — |
| `inv:hermes_security_audit_--skip-mcp` | MISSING | owner | --skip-mcp | — |
| `inv:hermes_security_audit_--skip-plugins` | MISSING | owner | --skip-plugins | — |
| `inv:hermes_security_audit_--skip-venv` | MISSING | owner | --skip-venv | — |
| `inv:hermes_sessions_browse_--limit` | MISSING | owner | --limit | — |
| `inv:hermes_sessions_browse_--source` | MISSING | owner | --source | — |
| `inv:hermes_sessions_export_--format` | MISSING | owner | --format | — |
| `inv:hermes_sessions_export_--lineage` | MISSING | owner | --lineage | — |
| `inv:hermes_sessions_export_--only` | MISSING | owner | --only | — |
| `inv:hermes_sessions_export_--session-id` | MISSING | owner | --session-id | — |
| `inv:hermes_sessions_import_--from` | MISSING | owner | --from | — |
| `inv:hermes_sessions_list_--limit` | MISSING | owner | --limit | — |
| `inv:hermes_sessions_list_--source` | MISSING | owner | --source | — |
| `inv:hermes_sessions_list_--workspace` | MISSING | owner | --workspace | — |
| `inv:hermes_sessions_recover_--chunk-size` | MISSING | owner | --chunk-size | — |
| `inv:hermes_sessions_recover_--output` | MISSING | owner | --output | — |
| `inv:hermes_sessions_recover_--report` | MISSING | owner | --report | — |
| `inv:hermes_sessions_recover_--source` | MISSING | owner | --source | — |
| `inv:hermes_sessions_recover_--work-dir` | MISSING | owner | --work-dir | — |
| `inv:hermes_sessions_repair-routing_--max-gap-seconds` | MISSING | owner | --max-gap-seconds | — |
| `inv:hermes_sessions_retitle-skills_--limit` | MISSING | owner | --limit | — |
| `inv:hermes_setup_--non-interactive` | MISSING | owner | --non-interactive | — |
| `inv:hermes_setup_--portal` | MISSING | owner | --portal | — |
| `inv:hermes_setup_--quick` | MISSING | owner | --quick | — |
| `inv:hermes_setup_--reconfigure` | MISSING | owner | --reconfigure | — |
| `inv:hermes_setup_--reset` | MISSING | owner | --reset | — |
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
| `inv:hermes_skin_set_--skin` | MISSING | owner | --skin | — |
| `inv:hermes_slack_manifest_--agent-view` | MISSING | creds | --agent-view | — |
| `inv:hermes_slack_manifest_--description` | MISSING | creds | --description | — |
| `inv:hermes_slack_manifest_--long-description` | MISSING | creds | --long-description | — |
| `inv:hermes_slack_manifest_--long-description-file` | MISSING | creds | --long-description-file | — |
| `inv:hermes_slack_manifest_--name` | MISSING | creds | --name | — |
| `inv:hermes_slack_manifest_--no-assistant` | MISSING | creds | --no-assistant | — |
| `inv:hermes_slack_manifest_--slashes-only` | MISSING | creds | --slashes-only | — |
| `inv:hermes_slack_manifest_--write` | MISSING | creds | --write | — |
| `inv:hermes_status_--all` | MISSING | owner | --all | — |
| `inv:hermes_status_--deep` | MISSING | owner | --deep | — |
| `inv:hermes_sync_device_--name` | MISSING | hardware | --name | — |
| `inv:hermes_sync_propose_--message` | MISSING | owner | --message | — |
| `inv:hermes_sync_propose_-m` | MISSING | owner | -m | — |
| `inv:hermes_tools_--summary` | MISSING | owner | --summary | — |
| `inv:hermes_tools_disable_--platform` | MISSING | owner | --platform | — |
| `inv:hermes_tools_enable_--platform` | MISSING | owner | --platform | — |
| `inv:hermes_tools_list_--platform` | MISSING | owner | --platform | — |
| `inv:hermes_uninstall_--dry-run` | MISSING | owner | --dry-run | — |
| `inv:hermes_uninstall_--full` | MISSING | owner | --full | — |
| `inv:hermes_uninstall_--gui` | MISSING | owner | --gui | — |
| `inv:hermes_uninstall_--gui-summary` | MISSING | owner | --gui-summary | — |
| `inv:hermes_update_--backup` | MISSING | owner | --backup | — |
| `inv:hermes_update_--branch` | MISSING | owner | --branch | — |
| `inv:hermes_update_--check` | MISSING | owner | --check | — |
| `inv:hermes_update_--force` | MISSING | owner | --force | — |
| `inv:hermes_update_--force-venv` | MISSING | owner | --force-venv | — |
| `inv:hermes_update_--gateway` | MISSING | owner | --gateway | — |
| `inv:hermes_update_--keep-stash` | MISSING | owner | --keep-stash | — |
| `inv:hermes_update_--no-backup` | MISSING | owner | --no-backup | — |
| `inv:hermes_update_--plan` | MISSING | owner | --plan | — |
| `inv:hermes_update_--switch-branch` | MISSING | owner | --switch-branch | — |
| `inv:hermes_update_--yes` | MISSING | owner | --yes | — |
| `inv:hermes_update_-y` | MISSING | owner | -y | — |
| `inv:hermes_verify_--detect-only` | MISSING | owner | --detect-only | — |
| `inv:hermes_verify_--phase` | MISSING | owner | --phase | — |
| `inv:hermes_verify_--port` | MISSING | owner | --port | — |
| `inv:hermes_verify_--ready-timeout` | MISSING | owner | --ready-timeout | — |
| `inv:hermes_verify_--save` | MISSING | owner | --save | — |
| `inv:hermes_verify_--skip-start` | MISSING | owner | --skip-start | — |
| `inv:hermes_verify_--timeout` | MISSING | owner | --timeout | — |
| `inv:hermes_webhook_subscribe_--deliver` | MISSING | creds | --deliver | — |
| `inv:hermes_webhook_subscribe_--deliver-chat-id` | MISSING | creds | --deliver-chat-id | — |
| `inv:hermes_webhook_subscribe_--deliver-only` | MISSING | creds | --deliver-only | — |
| `inv:hermes_webhook_subscribe_--description` | MISSING | creds | --description | — |
| `inv:hermes_webhook_subscribe_--events` | MISSING | creds | --events | — |
| `inv:hermes_webhook_subscribe_--prompt` | MISSING | creds | --prompt | — |
| `inv:hermes_webhook_subscribe_--script` | MISSING | creds | --script | — |
| `inv:hermes_webhook_subscribe_--secret` | MISSING | creds | --secret | — |
| `inv:hermes_webhook_subscribe_--skills` | MISSING | creds | --skills | — |
| `inv:hermes_webhook_test_--payload` | MISSING | creds | --payload | — |
| `inv:hermes_worktree_list_--repo` | MISSING | owner | --repo | — |
| `inv:hermes_worktree_prune_--branches-only` | MISSING | owner | --branches-only | — |
| `inv:hermes_worktree_prune_--dry-run` | MISSING | owner | --dry-run | — |
| `inv:hermes_worktree_prune_--repo` | MISSING | owner | --repo | — |
| `inv:hermes_worktree_prune_--trees-only` | MISSING | owner | --trees-only | — |

#### qubit / hermes_cli_positional (34)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:hermes_approvals_test_command_words` | MISSING | owner | command_words | — |
| `inv:hermes_auth_priority_priority` | MISSING | creds | priority | — |
| `inv:hermes_auth_spotify_spotify_action` | MISSING | creds | spotify_action | — |
| `inv:hermes_cron_incidents_incident_action` | MISSING | owner | incident_action | — |
| `inv:hermes_cron_incidents_incident_id` | MISSING | owner | incident_id | — |
| `inv:hermes_cron_notepad_notepad_action` | MISSING | owner | notepad_action | — |
| `inv:hermes_debug_delete_urls` | MISSING | owner | urls | — |
| `inv:hermes_import_zipfile` | MISSING | owner | zipfile | — |
| `inv:hermes_logs_log_name` | MISSING | owner | log_name | — |
| `inv:hermes_mcp_install_identifier` | MISSING | owner | identifier | — |
| `inv:hermes_pairing_revoke_user_id` | MISSING | hardware | user_id | — |
| `inv:hermes_plugins_install_identifier` | MISSING | owner | identifier | — |
| `inv:hermes_plugins_search_term` | MISSING | owner | term | — |
| `inv:hermes_profile_alias_profile_name` | MISSING | owner | profile_name | — |
| `inv:hermes_profile_create_profile_name` | MISSING | owner | profile_name | — |
| `inv:hermes_profile_delete_profile_name` | MISSING | owner | profile_name | — |
| `inv:hermes_profile_describe_profile_name` | MISSING | owner | profile_name | — |
| `inv:hermes_profile_export_profile_name` | MISSING | owner | profile_name | — |
| `inv:hermes_profile_info_profile_name` | MISSING | owner | profile_name | — |
| `inv:hermes_profile_rename_new_name` | MISSING | owner | new_name | — |
| `inv:hermes_profile_rename_old_name` | MISSING | owner | old_name | — |
| `inv:hermes_profile_show_profile_name` | MISSING | owner | profile_name | — |
| `inv:hermes_profile_update_profile_name` | MISSING | owner | profile_name | — |
| `inv:hermes_profile_use_profile_name` | MISSING | owner | profile_name | — |
| `inv:hermes_project_create_folders` | MISSING | owner | folders | — |
| `inv:hermes_sessions_pin_session_ids` | MISSING | owner | session_ids | — |
| `inv:hermes_sessions_unpin_session_ids` | MISSING | owner | session_ids | — |
| `inv:hermes_setup_section` | MISSING | owner | section | — |
| `inv:hermes_skills_inspect_identifier` | MISSING | owner | identifier | — |
| `inv:hermes_skills_install_identifier` | MISSING | owner | identifier | — |
| `inv:hermes_skills_publish_skill_path` | MISSING | owner | skill_path | — |
| `inv:hermes_skills_search_query` | MISSING | owner | query | — |
| `inv:hermes_skills_snapshot_import_input` | MISSING | owner | input | — |
| `inv:hermes_tools_post-setup_post_setup_key` | MISSING | owner | post_setup_key | — |

#### qubit / hermes_cli_subcommand (179)

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
| `inv:hermes_claw_clean` | MISSING | owner | hermes claw clean | — |
| `inv:hermes_claw_cleanup` | MISSING | owner | hermes claw cleanup | — |
| `inv:hermes_claw_migrate` | MISSING | owner | hermes claw migrate | — |
| `inv:hermes_computer-use_doctor` | MISSING | hardware | hermes computer-use doctor | — |
| `inv:hermes_computer-use_install` | MISSING | hardware | hermes computer-use install | — |
| `inv:hermes_computer-use_permissions` | MISSING | hardware | hermes computer-use permissions | — |
| `inv:hermes_computer-use_permissions_grant` | MISSING | hardware | hermes computer-use permissions grant | — |
| `inv:hermes_computer-use_permissions_status` | MISSING | hardware | hermes computer-use permissions status | — |
| `inv:hermes_computer-use_status` | MISSING | hardware | hermes computer-use status | — |
| `inv:hermes_cron_add` | MISSING | owner | hermes cron add | — |
| `inv:hermes_cron_create` | MISSING | owner | hermes cron create | — |
| `inv:hermes_cron_delete` | MISSING | owner | hermes cron delete | — |
| `inv:hermes_cron_history` | MISSING | owner | hermes cron history | — |
| `inv:hermes_cron_incidents` | MISSING | owner | hermes cron incidents | — |
| `inv:hermes_cron_notepad` | MISSING | owner | hermes cron notepad | — |
| `inv:hermes_cron_remove` | MISSING | owner | hermes cron remove | — |
| `inv:hermes_cron_rm` | MISSING | owner | hermes cron rm | — |
| `inv:hermes_cron_runs` | MISSING | owner | hermes cron runs | — |
| `inv:hermes_dashboard_register` | MISSING | owner | hermes dashboard register | — |
| `inv:hermes_debug_delete` | MISSING | owner | hermes debug delete | — |
| `inv:hermes_debug_share` | MISSING | owner | hermes debug share | — |
| `inv:hermes_fallback_add` | MISSING | owner | hermes fallback add | — |
| `inv:hermes_fallback_clear` | MISSING | owner | hermes fallback clear | — |
| `inv:hermes_fallback_list` | MISSING | owner | hermes fallback list | — |
| `inv:hermes_fallback_ls` | MISSING | owner | hermes fallback ls | — |
| `inv:hermes_fallback_remove` | MISSING | owner | hermes fallback remove | — |
| `inv:hermes_fallback_rm` | MISSING | owner | hermes fallback rm | — |
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
| `inv:hermes_hooks_ls` | MISSING | owner | hermes hooks ls | — |
| `inv:hermes_hooks_remove` | MISSING | owner | hermes hooks remove | — |
| `inv:hermes_hooks_revoke` | MISSING | owner | hermes hooks revoke | — |
| `inv:hermes_hooks_rm` | MISSING | owner | hermes hooks rm | — |
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
| `inv:hermes_moa_config` | MISSING | owner | hermes moa config | — |
| `inv:hermes_moa_configure` | MISSING | owner | hermes moa configure | — |
| `inv:hermes_moa_delete` | MISSING | owner | hermes moa delete | — |
| `inv:hermes_moa_list` | MISSING | owner | hermes moa list | — |
| `inv:hermes_moa_ls` | MISSING | owner | hermes moa ls | — |
| `inv:hermes_moa_rm` | MISSING | owner | hermes moa rm | — |
| `inv:hermes_monitoring_status` | MISSING | owner | hermes monitoring status | — |
| `inv:hermes_pairing_approve` | MISSING | hardware | hermes pairing approve | — |
| `inv:hermes_pairing_clear-pending` | MISSING | hardware | hermes pairing clear-pending | — |
| `inv:hermes_pairing_list` | MISSING | hardware | hermes pairing list | — |
| `inv:hermes_pairing_revoke` | MISSING | hardware | hermes pairing revoke | — |
| `inv:hermes_peer_add` | MISSING | owner | hermes peer add | — |
| `inv:hermes_peer_list` | MISSING | owner | hermes peer list | — |
| `inv:hermes_peer_ls` | MISSING | owner | hermes peer ls | — |
| `inv:hermes_peer_remove` | MISSING | owner | hermes peer remove | — |
| `inv:hermes_peer_rm` | MISSING | owner | hermes peer rm | — |
| `inv:hermes_peer_set` | MISSING | owner | hermes peer set | — |
| `inv:hermes_plugins_compat` | MISSING | owner | hermes plugins compat | — |
| `inv:hermes_plugins_disable` | MISSING | owner | hermes plugins disable | — |
| `inv:hermes_plugins_info` | MISSING | owner | hermes plugins info | — |
| `inv:hermes_plugins_list` | MISSING | owner | hermes plugins list | — |
| `inv:hermes_plugins_ls` | MISSING | owner | hermes plugins ls | — |
| `inv:hermes_plugins_pack` | MISSING | owner | hermes plugins pack | — |
| `inv:hermes_plugins_pack_export` | MISSING | owner | hermes plugins pack export | — |
| `inv:hermes_plugins_pack_install` | MISSING | owner | hermes plugins pack install | — |
| `inv:hermes_plugins_pack_show` | MISSING | owner | hermes plugins pack show | — |
| `inv:hermes_plugins_rm` | MISSING | owner | hermes plugins rm | — |
| `inv:hermes_plugins_search` | MISSING | owner | hermes plugins search | — |
| `inv:hermes_plugins_show` | MISSING | owner | hermes plugins show | — |
| `inv:hermes_plugins_uninstall` | MISSING | owner | hermes plugins uninstall | — |
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
| `inv:hermes_project_ls` | MISSING | owner | hermes project ls | — |
| `inv:hermes_project_use` | MISSING | owner | hermes project use | — |
| `inv:hermes_proxy_providers` | MISSING | creds | hermes proxy providers | — |
| `inv:hermes_proxy_start` | MISSING | owner | hermes proxy start | — |
| `inv:hermes_proxy_status` | MISSING | owner | hermes proxy status | — |
| `inv:hermes_secrets_1password` | MISSING | creds | hermes secrets 1password | — |
| `inv:hermes_secrets_bitwarden` | MISSING | creds | hermes secrets bitwarden | — |
| `inv:hermes_secrets_bw` | MISSING | creds | hermes secrets bw | — |
| `inv:hermes_secrets_onepassword` | MISSING | creds | hermes secrets onepassword | — |
| `inv:hermes_secrets_op` | MISSING | creds | hermes secrets op | — |
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
| `inv:hermes_webhook_add` | MISSING | creds | hermes webhook add | — |
| `inv:hermes_webhook_list` | MISSING | creds | hermes webhook list | — |
| `inv:hermes_webhook_ls` | MISSING | creds | hermes webhook ls | — |
| `inv:hermes_webhook_remove` | MISSING | creds | hermes webhook remove | — |
| `inv:hermes_webhook_rm` | MISSING | creds | hermes webhook rm | — |
| `inv:hermes_webhook_subscribe` | MISSING | creds | hermes webhook subscribe | — |
| `inv:hermes_webhook_test` | MISSING | creds | hermes webhook test | — |
| `inv:hermes_worktree_audit` | MISSING | owner | hermes worktree audit | — |
| `inv:hermes_worktree_ls` | MISSING | owner | hermes worktree ls | — |
| `inv:hermes_worktree_prune` | MISSING | owner | hermes worktree prune | — |

#### qubit / hermes_dashboard_component (14)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:component:AuthWidget` | MISSING | creds | AuthWidget | — |
| `inv:component:AutomationBlueprints` | MISSING | owner | AutomationBlueprints | — |
| `inv:component:ChatSessionList` | MISSING | owner | ChatSessionList | — |
| `inv:component:ChatSidebar` | MISSING | owner | ChatSidebar | — |
| `inv:component:LanguageSwitcher` | MISSING | owner | LanguageSwitcher | — |
| `inv:component:MemoryPressureBanner` | MISSING | owner | MemoryPressureBanner | — |
| `inv:component:ModelInfoCard` | MISSING | creds | ModelInfoCard | — |
| `inv:component:OAuthProvidersCard` | MISSING | creds | OAuthProvidersCard | — |
| `inv:component:PlatformsCard` | MISSING | owner | PlatformsCard | — |
| `inv:component:ProfileScopeBanner` | MISSING | owner | ProfileScopeBanner | — |
| `inv:component:ProfileSwitcher` | MISSING | owner | ProfileSwitcher | — |
| `inv:component:SidebarFooter` | MISSING | owner | SidebarFooter | — |
| `inv:component:SidebarStatusStrip` | MISSING | owner | SidebarStatusStrip | — |
| `inv:component:ThemeSwitcher` | MISSING | owner | ThemeSwitcher | — |

#### qubit / hermes_dashboard_dialog (10)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:component:ConfirmDialog` | MISSING | owner | ConfirmDialog | — |
| `inv:component:DeleteConfirmDialog` | MISSING | owner | DeleteConfirmDialog | — |
| `inv:component:HermesConsoleModal` | MISSING | owner | HermesConsoleModal | — |
| `inv:component:ModelPickerDialog` | MISSING | creds | ModelPickerDialog | — |
| `inv:component:ModelReloadConfirm` | MISSING | creds | ModelReloadConfirm | — |
| `inv:component:OAuthLoginModal` | MISSING | creds | OAuthLoginModal | — |
| `inv:component:ReasoningPicker` | MISSING | owner | ReasoningPicker | — |
| `inv:component:SkillEditorDialog` | MISSING | owner | SkillEditorDialog | — |
| `inv:component:SlashPopover` | MISSING | owner | SlashPopover | — |
| `inv:component:ToolsetConfigDrawer` | MISSING | owner | ToolsetConfigDrawer | — |

#### qubit / hermes_dashboard_form (2)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:component:AutoField` | MISSING | owner | AutoField | — |
| `inv:component:ScheduleBuilder` | MISSING | owner | ScheduleBuilder | — |

#### qubit / hermes_dashboard_nav (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:nav:/docs` | MISSING | owner | Documentation | — |

#### qubit / hermes_dashboard_plugin (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:plugin-manifest:hermes-achievements` | MISSING | owner | hermes-achievements | — |

#### qubit / hermes_gated (42)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `hermes_gate_audio_lease_desktop` | GATED | owner | Gated upstream route: AUDIO_LEASE_DESKTOP | — |
| `hermes_gate_audio_stream_local` | GATED | owner | Gated upstream route: AUDIO_STREAM_LOCAL | — |
| `hermes_gate_backup_passphrase` | GATED | owner | Gated upstream route: BACKUP_PASSPHRASE | — |
| `hermes_gate_blocked_remote_source` | GATED | owner | Gated upstream route: BLOCKED_REMOTE_SOURCE | — |
| `hermes_gate_chat_image_overlay` | GATED | owner | Gated upstream route: CHAT_IMAGE_OVERLAY | — |
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
| `hermes_gate_stub_agent_disable` | GATED | owner | Gated upstream route: STUB_AGENT_DISABLE | — |
| `hermes_gate_stub_agent_enable` | GATED | owner | Gated upstream route: STUB_AGENT_ENABLE | — |
| `hermes_gate_stub_agent_install` | GATED | owner | Gated upstream route: STUB_AGENT_INSTALL | — |
| `hermes_gate_stub_agent_update` | GATED | owner | Gated upstream route: STUB_AGENT_UPDATE | — |
| `hermes_gate_stub_ckpt_prune` | GATED | owner | Gated upstream route: STUB_CKPT_PRUNE | — |
| `hermes_gate_stub_h_session_resume` | GATED | owner | Gated upstream route: STUB_H_SESSION_RESUME | — |
| `hermes_gate_stub_import_upload` | GATED | owner | Gated upstream route: STUB_IMPORT_UPLOAD | — |
| `hermes_gate_stub_mcp_create` | GATED | owner | Gated upstream route: STUB_MCP_CREATE | — |
| `hermes_gate_stub_mcp_enabled` | GATED | owner | Gated upstream route: STUB_MCP_ENABLED | — |
| `hermes_gate_stub_mem_prov` | GATED | owner | Gated upstream route: STUB_MEM_PROV | — |
| `hermes_gate_stub_oauth_delete` | GATED | owner | Gated upstream route: STUB_OAUTH_DELETE | — |
| `hermes_gate_stub_sess_bulk` | GATED | owner | Gated upstream route: STUB_SESS_BULK | — |
| `hermes_gate_stub_sess_import` | GATED | owner | Gated upstream route: STUB_SESS_IMPORT | — |
| `hermes_gate_stub_sess_prune` | GATED | owner | Gated upstream route: STUB_SESS_PRUNE | — |
| `hermes_gate_update_operator_owned` | GATED | owner | Gated upstream route: UPDATE_OPERATOR_OWNED | — |
| `hermes_gate_ws_single_path` | GATED | owner | Gated upstream route: WS_SINGLE_PATH | — |

#### qubit / hermes_model (211)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:model:MiniMax-M2.7` | MISSING | creds | MiniMax-M2.7 | — |
| `inv:model:MiniMax-M2.7-highspeed` | MISSING | creds | MiniMax-M2.7-highspeed | — |
| `inv:model:MiniMax-M3` | MISSING | creds | MiniMax-M3 | — |
| `inv:model:MiniMaxAI/MiniMax-M2.5` | MISSING | creds | MiniMaxAI/MiniMax-M2.5 | — |
| `inv:model:Qwen/Qwen3.5-35B-A3B` | MISSING | creds | Qwen/Qwen3.5-35B-A3B | — |
| `inv:model:Qwen/Qwen3.5-397B-A17B` | MISSING | creds | Qwen/Qwen3.5-397B-A17B | — |
| `inv:model:XiaomiMiMo/MiMo-V2-Flash` | MISSING | creds | XiaomiMiMo/MiMo-V2-Flash | — |
| `inv:model:anthropic/claude-fable-5` | MISSING | creds | anthropic/claude-fable-5 | — |
| `inv:model:anthropic/claude-fable-5.1` | MISSING | creds | anthropic/claude-fable-5.1 | — |
| `inv:model:anthropic/claude-haiku-4.5` | MISSING | creds | anthropic/claude-haiku-4.5 | — |
| `inv:model:anthropic/claude-opus-4.6` | MISSING | creds | anthropic/claude-opus-4.6 | — |
| `inv:model:anthropic/claude-opus-4.8` | MISSING | creds | anthropic/claude-opus-4.8 | — |
| `inv:model:anthropic/claude-opus-4.8-fast` | MISSING | creds | anthropic/claude-opus-4.8-fast | — |
| `inv:model:anthropic/claude-opus-5` | MISSING | creds | anthropic/claude-opus-5 | — |
| `inv:model:anthropic/claude-opus-5-fast` | MISSING | creds | anthropic/claude-opus-5-fast | — |
| `inv:model:anthropic/claude-sonnet-4.6` | MISSING | creds | anthropic/claude-sonnet-4.6 | — |
| `inv:model:anthropic/claude-sonnet-5` | MISSING | creds | anthropic/claude-sonnet-5 | — |
| `inv:model:big-pickle` | MISSING | creds | big-pickle | — |
| `inv:model:claude-fable-5` | MISSING | creds | claude-fable-5 | — |
| `inv:model:claude-haiku-4-5` | MISSING | creds | claude-haiku-4-5 | — |
| `inv:model:claude-haiku-4-5-20251001` | MISSING | creds | claude-haiku-4-5-20251001 | — |
| `inv:model:claude-opus-4-20250514` | MISSING | creds | claude-opus-4-20250514 | — |
| `inv:model:claude-opus-4-5` | MISSING | creds | claude-opus-4-5 | — |
| `inv:model:claude-opus-4-5-20251101` | MISSING | creds | claude-opus-4-5-20251101 | — |
| `inv:model:claude-opus-4-6` | MISSING | creds | claude-opus-4-6 | — |
| `inv:model:claude-opus-4-7` | MISSING | creds | claude-opus-4-7 | — |
| `inv:model:claude-opus-4-8` | MISSING | creds | claude-opus-4-8 | — |
| `inv:model:claude-opus-5` | MISSING | creds | claude-opus-5 | — |
| `inv:model:claude-sonnet-4` | MISSING | creds | claude-sonnet-4 | — |
| `inv:model:claude-sonnet-4-20250514` | MISSING | creds | claude-sonnet-4-20250514 | — |
| `inv:model:claude-sonnet-4-5` | MISSING | creds | claude-sonnet-4-5 | — |
| `inv:model:claude-sonnet-4-5-20250929` | MISSING | creds | claude-sonnet-4-5-20250929 | — |
| `inv:model:claude-sonnet-4-6` | MISSING | creds | claude-sonnet-4-6 | — |
| `inv:model:claude-sonnet-5` | MISSING | creds | claude-sonnet-5 | — |
| `inv:model:copilot-acp` | MISSING | creds | copilot-acp | — |
| `inv:model:deepseek-ai/DeepSeek-V3.2` | MISSING | creds | deepseek-ai/DeepSeek-V3.2 | — |
| `inv:model:deepseek-v4-flash` | MISSING | creds | deepseek-v4-flash | — |
| `inv:model:deepseek-v4-flash-free` | MISSING | creds | deepseek-v4-flash-free | — |
| `inv:model:deepseek-v4-pro` | MISSING | creds | deepseek-v4-pro | — |
| `inv:model:deepseek.v3.2` | MISSING | creds | deepseek.v3.2 | — |
| `inv:model:deepseek/deepseek-r1-0528` | MISSING | creds | deepseek/deepseek-r1-0528 | — |
| `inv:model:deepseek/deepseek-v3-0324` | MISSING | creds | deepseek/deepseek-v3-0324 | — |
| `inv:model:deepseek/deepseek-v4-flash-0731` | MISSING | creds | deepseek/deepseek-v4-flash-0731 | — |
| `inv:model:deepseek/deepseek-v4-pro` | MISSING | creds | deepseek/deepseek-v4-pro | — |
| `inv:model:deepseek/deepseek-v4-pro-0813` | MISSING | creds | deepseek/deepseek-v4-pro-0813 | — |
| `inv:model:gemini-3-flash` | MISSING | creds | gemini-3-flash | — |
| `inv:model:gemini-3-pro-preview` | MISSING | creds | gemini-3-pro-preview | — |
| `inv:model:gemini-3.1-flash-lite-preview` | MISSING | creds | gemini-3.1-flash-lite-preview | — |
| `inv:model:gemini-3.1-pro` | MISSING | creds | gemini-3.1-pro | — |
| `inv:model:gemini-3.1-pro-preview` | MISSING | creds | gemini-3.1-pro-preview | — |
| `inv:model:gemini-3.5-flash` | MISSING | creds | gemini-3.5-flash | — |
| `inv:model:gemini-3.5-flash-lite` | MISSING | creds | gemini-3.5-flash-lite | — |
| `inv:model:gemini-3.6-flash` | MISSING | creds | gemini-3.6-flash | — |
| `inv:model:gemini-3.7-flash` | MISSING | creds | gemini-3.7-flash | — |
| `inv:model:gemini-3.8-flash` | MISSING | creds | gemini-3.8-flash | — |
| `inv:model:glm-4.5` | MISSING | creds | glm-4.5 | — |
| `inv:model:glm-4.5-flash` | MISSING | creds | glm-4.5-flash | — |
| `inv:model:glm-4.7` | MISSING | creds | glm-4.7 | — |
| `inv:model:glm-5` | MISSING | creds | glm-5 | — |
| `inv:model:glm-5-turbo` | MISSING | creds | glm-5-turbo | — |
| `inv:model:glm-5.1` | MISSING | creds | glm-5.1 | — |
| `inv:model:glm-5.2` | MISSING | creds | glm-5.2 | — |
| `inv:model:glm-5.3` | MISSING | creds | glm-5.3 | — |
| `inv:model:glm-5.3-flash` | MISSING | creds | glm-5.3-flash | — |
| `inv:model:glm-5v-turbo` | MISSING | creds | glm-5v-turbo | — |
| `inv:model:google/gemini-3-flash-preview` | MISSING | creds | google/gemini-3-flash-preview | — |
| `inv:model:google/gemini-3-pro-preview` | MISSING | creds | google/gemini-3-pro-preview | — |
| `inv:model:google/gemini-3.1-flash-lite` | MISSING | creds | google/gemini-3.1-flash-lite | — |
| `inv:model:google/gemini-3.1-flash-lite-preview` | MISSING | creds | google/gemini-3.1-flash-lite-preview | — |
| `inv:model:google/gemini-3.1-pro-preview` | MISSING | creds | google/gemini-3.1-pro-preview | — |
| `inv:model:google/gemini-3.5-flash` | MISSING | creds | google/gemini-3.5-flash | — |
| `inv:model:google/gemini-3.5-flash-lite` | MISSING | creds | google/gemini-3.5-flash-lite | — |
| `inv:model:google/gemini-3.6-flash` | MISSING | creds | google/gemini-3.6-flash | — |
| `inv:model:google/gemini-3.7-flash` | MISSING | creds | google/gemini-3.7-flash | — |
| `inv:model:google/gemini-3.8-flash` | MISSING | creds | google/gemini-3.8-flash | — |
| `inv:model:gpt-4.1` | MISSING | creds | gpt-4.1 | — |
| `inv:model:gpt-4o` | MISSING | creds | gpt-4o | — |
| `inv:model:gpt-4o-mini` | MISSING | creds | gpt-4o-mini | — |
| `inv:model:gpt-5` | MISSING | creds | gpt-5 | — |
| `inv:model:gpt-5-codex` | MISSING | creds | gpt-5-codex | — |
| `inv:model:gpt-5-mini` | MISSING | creds | gpt-5-mini | — |
| `inv:model:gpt-5-nano` | MISSING | creds | gpt-5-nano | — |
| `inv:model:gpt-5.1` | MISSING | creds | gpt-5.1 | — |
| `inv:model:gpt-5.1-codex` | MISSING | creds | gpt-5.1-codex | — |
| `inv:model:gpt-5.1-codex-max` | MISSING | creds | gpt-5.1-codex-max | — |
| `inv:model:gpt-5.1-codex-mini` | MISSING | creds | gpt-5.1-codex-mini | — |
| `inv:model:gpt-5.2` | MISSING | creds | gpt-5.2 | — |
| `inv:model:gpt-5.2-codex` | MISSING | creds | gpt-5.2-codex | — |
| `inv:model:gpt-5.3-codex` | MISSING | creds | gpt-5.3-codex | — |
| `inv:model:gpt-5.3-codex-spark` | MISSING | creds | gpt-5.3-codex-spark | — |
| `inv:model:gpt-5.4` | MISSING | creds | gpt-5.4 | — |
| `inv:model:gpt-5.4-mini` | MISSING | creds | gpt-5.4-mini | — |
| `inv:model:gpt-5.4-nano` | MISSING | creds | gpt-5.4-nano | — |
| `inv:model:gpt-5.4-pro` | MISSING | creds | gpt-5.4-pro | — |
| `inv:model:gpt-5.5` | MISSING | creds | gpt-5.5 | — |
| `inv:model:gpt-5.5-pro` | MISSING | creds | gpt-5.5-pro | — |
| `inv:model:gpt-5.6-luna` | MISSING | creds | gpt-5.6-luna | — |
| `inv:model:gpt-5.6-luna-pro` | MISSING | creds | gpt-5.6-luna-pro | — |
| `inv:model:gpt-5.6-sol` | MISSING | creds | gpt-5.6-sol | — |
| `inv:model:gpt-5.6-sol-pro` | MISSING | creds | gpt-5.6-sol-pro | — |
| `inv:model:gpt-5.6-terra` | MISSING | creds | gpt-5.6-terra | — |
| `inv:model:gpt-5.6-terra-pro` | MISSING | creds | gpt-5.6-terra-pro | — |
| `inv:model:grok-4.5` | MISSING | creds | grok-4.5 | — |
| `inv:model:grok-4.6` | MISSING | creds | grok-4.6 | — |
| `inv:model:grok-build-0.1` | MISSING | creds | grok-build-0.1 | — |
| `inv:model:hy3` | MISSING | creds | hy3 | — |
| `inv:model:hy3-free` | MISSING | creds | hy3-free | — |
| `inv:model:hy3-preview` | MISSING | creds | hy3-preview | — |
| `inv:model:kimi-for-coding` | MISSING | creds | kimi-for-coding | — |
| `inv:model:kimi-for-coding-highspeed` | MISSING | creds | kimi-for-coding-highspeed | — |
| `inv:model:kimi-k2-0905-preview` | MISSING | creds | kimi-k2-0905-preview | — |
| `inv:model:kimi-k2-thinking` | MISSING | creds | kimi-k2-thinking | — |
| `inv:model:kimi-k2-thinking-turbo` | MISSING | creds | kimi-k2-thinking-turbo | — |
| `inv:model:kimi-k2-turbo-preview` | MISSING | creds | kimi-k2-turbo-preview | — |
| `inv:model:kimi-k2.5` | MISSING | creds | kimi-k2.5 | — |
| `inv:model:kimi-k2.6` | MISSING | creds | kimi-k2.6 | — |
| `inv:model:kimi-k2.7-code` | MISSING | creds | kimi-k2.7-code | — |
| `inv:model:kimi-k2.7-code-highspeed` | MISSING | creds | kimi-k2.7-code-highspeed | — |
| `inv:model:kimi-k3` | MISSING | creds | kimi-k3 | — |
| `inv:model:laguna-s-2.1-free` | MISSING | creds | laguna-s-2.1-free | — |
| `inv:model:meta/muse-spark-1.2` | MISSING | creds | meta/muse-spark-1.2 | — |
| `inv:model:meta/muse-spark-1.2-contributor` | MISSING | creds | meta/muse-spark-1.2-contributor | — |
| `inv:model:meta/muse-spark-1.3` | MISSING | creds | meta/muse-spark-1.3 | — |
| `inv:model:meta/muse-spark-1.3-contributor` | MISSING | creds | meta/muse-spark-1.3-contributor | — |
| `inv:model:mimo-v2-flash` | MISSING | creds | mimo-v2-flash | — |
| `inv:model:mimo-v2-omni` | MISSING | creds | mimo-v2-omni | — |
| `inv:model:mimo-v2-pro` | MISSING | creds | mimo-v2-pro | — |
| `inv:model:mimo-v2.5` | MISSING | creds | mimo-v2.5 | — |
| `inv:model:mimo-v2.5-free` | MISSING | creds | mimo-v2.5-free | — |
| `inv:model:mimo-v2.5-pro` | MISSING | creds | mimo-v2.5-pro | — |
| `inv:model:minimax-m2.5` | MISSING | creds | minimax-m2.5 | — |
| `inv:model:minimax-m2.7` | MISSING | creds | minimax-m2.7 | — |
| `inv:model:minimax-m3` | MISSING | creds | minimax-m3 | — |
| `inv:model:minimax/minimax-m2.7` | MISSING | creds | minimax/minimax-m2.7 | — |
| `inv:model:minimax/minimax-m3` | MISSING | creds | minimax/minimax-m3 | — |
| `inv:model:minimaxai/minimax-m3` | MISSING | creds | minimaxai/minimax-m3 | — |
| `inv:model:moonshotai/Kimi-K2-Thinking` | MISSING | creds | moonshotai/Kimi-K2-Thinking | — |
| `inv:model:moonshotai/Kimi-K2.5` | MISSING | creds | moonshotai/Kimi-K2.5 | — |
| `inv:model:moonshotai/Kimi-K2.6` | MISSING | creds | moonshotai/Kimi-K2.6 | — |
| `inv:model:moonshotai/kimi-k2.5` | MISSING | creds | moonshotai/kimi-k2.5 | — |
| `inv:model:moonshotai/kimi-k2.6` | MISSING | creds | moonshotai/kimi-k2.6 | — |
| `inv:model:moonshotai/kimi-k3` | MISSING | creds | moonshotai/kimi-k3 | — |
| `inv:model:muse-spark-1.2` | MISSING | creds | muse-spark-1.2 | — |
| `inv:model:muse-spark-1.2-contributor` | MISSING | creds | muse-spark-1.2-contributor | — |
| `inv:model:muse-spark-1.2-contributor-free` | MISSING | creds | muse-spark-1.2-contributor-free | — |
| `inv:model:muse-spark-1.3-contributor` | MISSING | creds | muse-spark-1.3-contributor | — |
| `inv:model:muse-spark-1.3-contributor-free` | MISSING | creds | muse-spark-1.3-contributor-free | — |
| `inv:model:nemotron-3-ultra-free` | MISSING | creds | nemotron-3-ultra-free | — |
| `inv:model:nemotron-3.5-lightning-free` | MISSING | creds | nemotron-3.5-lightning-free | — |
| `inv:model:nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | MISSING | creds | nvidia/nemotron-3-nano-omni-30b-a3b-reasoning | — |
| `inv:model:nvidia/nemotron-3-super-120b-a12b` | MISSING | creds | nvidia/nemotron-3-super-120b-a12b | — |
| `inv:model:nvidia/nemotron-3-ultra-550b-a55b` | MISSING | creds | nvidia/nemotron-3-ultra-550b-a55b | — |
| `inv:model:nvidia/nemotron-3.5-lightning-30b-a3b` | MISSING | creds | nvidia/nemotron-3.5-lightning-30b-a3b | — |
| `inv:model:openai.gpt-5.5` | MISSING | creds | openai.gpt-5.5 | — |
| `inv:model:openai.gpt-5.6-luna` | MISSING | creds | openai.gpt-5.6-luna | — |
| `inv:model:openai.gpt-5.6-sol` | MISSING | creds | openai.gpt-5.6-sol | — |
| `inv:model:openai.gpt-5.6-terra` | MISSING | creds | openai.gpt-5.6-terra | — |
| `inv:model:openai/gpt-5.4` | MISSING | creds | openai/gpt-5.4 | — |
| `inv:model:openai/gpt-5.4-mini` | MISSING | creds | openai/gpt-5.4-mini | — |
| `inv:model:openai/gpt-5.5` | MISSING | creds | openai/gpt-5.5 | — |
| `inv:model:openai/gpt-5.5-pro` | MISSING | creds | openai/gpt-5.5-pro | — |
| `inv:model:openai/gpt-5.6-luna` | MISSING | creds | openai/gpt-5.6-luna | — |
| `inv:model:openai/gpt-5.6-luna-pro` | MISSING | creds | openai/gpt-5.6-luna-pro | — |
| `inv:model:openai/gpt-5.6-sol` | MISSING | creds | openai/gpt-5.6-sol | — |
| `inv:model:openai/gpt-5.6-sol-pro` | MISSING | creds | openai/gpt-5.6-sol-pro | — |
| `inv:model:openai/gpt-5.6-terra` | MISSING | creds | openai/gpt-5.6-terra | — |
| `inv:model:openai/gpt-5.6-terra-pro` | MISSING | creds | openai/gpt-5.6-terra-pro | — |
| `inv:model:openai/gpt-6-astra` | MISSING | creds | openai/gpt-6-astra | — |
| `inv:model:openai/gpt-6-astra-fast` | MISSING | creds | openai/gpt-6-astra-fast | — |
| `inv:model:openai/gpt-6-astra-flex` | MISSING | creds | openai/gpt-6-astra-flex | — |
| `inv:model:openai/gpt-6-astra-pro` | MISSING | creds | openai/gpt-6-astra-pro | — |
| `inv:model:openai/gpt-6-astra-pro-fast` | MISSING | creds | openai/gpt-6-astra-pro-fast | — |
| `inv:model:openai/gpt-6-astra-pro-flex` | MISSING | creds | openai/gpt-6-astra-pro-flex | — |
| `inv:model:openrouter/pareto-code` | MISSING | creds | openrouter/pareto-code | — |
| `inv:model:ox-alpha-free` | MISSING | creds | ox-alpha-free | — |
| `inv:model:qwen/qwen3-235b-a22b-fp8` | MISSING | creds | qwen/qwen3-235b-a22b-fp8 | — |
| `inv:model:qwen/qwen3.8-flash` | MISSING | creds | qwen/qwen3.8-flash | — |
| `inv:model:qwen/qwen3.8-max-0902` | MISSING | creds | qwen/qwen3.8-max-0902 | — |
| `inv:model:qwen3.5-plus` | MISSING | creds | qwen3.5-plus | — |
| `inv:model:qwen3.6-plus` | MISSING | creds | qwen3.6-plus | — |
| `inv:model:qwen3.7-max` | MISSING | creds | qwen3.7-max | — |
| `inv:model:qwen3.7-plus` | MISSING | creds | qwen3.7-plus | — |
| `inv:model:qwen3.8-max` | MISSING | creds | qwen3.8-max | — |
| `inv:model:sakana/fugu-ultra` | MISSING | creds | sakana/fugu-ultra | — |
| `inv:model:step-3.5-flash` | MISSING | creds | step-3.5-flash | — |
| `inv:model:step-3.5-flash-2603` | MISSING | creds | step-3.5-flash-2603 | — |
| `inv:model:stepfun/step-3.7-flash` | MISSING | creds | stepfun/step-3.7-flash | — |
| `inv:model:tencent/hy3` | MISSING | creds | tencent/hy3 | — |
| `inv:model:tencent/hy4-preview` | MISSING | creds | tencent/hy4-preview | — |
| `inv:model:trinity-large-preview` | MISSING | creds | trinity-large-preview | — |
| `inv:model:trinity-large-thinking` | MISSING | creds | trinity-large-thinking | — |
| `inv:model:trinity-mini` | MISSING | creds | trinity-mini | — |
| `inv:model:us.amazon.nova-lite-v1:0` | MISSING | creds | us.amazon.nova-lite-v1:0 | — |
| `inv:model:us.amazon.nova-micro-v1:0` | MISSING | hardware | us.amazon.nova-micro-v1:0 | — |
| `inv:model:us.amazon.nova-pro-v1:0` | MISSING | creds | us.amazon.nova-pro-v1:0 | — |
| `inv:model:us.anthropic.claude-haiku-4-5-20251001-v1:0` | MISSING | creds | us.anthropic.claude-haiku-4-5-20251001-v1:0 | — |
| `inv:model:us.anthropic.claude-opus-4-6-v1` | MISSING | creds | us.anthropic.claude-opus-4-6-v1 | — |
| `inv:model:us.anthropic.claude-sonnet-4-5-20250929-v1:0` | MISSING | creds | us.anthropic.claude-sonnet-4-5-20250929-v1:0 | — |
| `inv:model:us.anthropic.claude-sonnet-4-6` | MISSING | creds | us.anthropic.claude-sonnet-4-6 | — |
| `inv:model:us.anthropic.claude-sonnet-5` | MISSING | creds | us.anthropic.claude-sonnet-5 | — |
| `inv:model:us.meta.llama4-maverick-17b-instruct-v1:0` | MISSING | creds | us.meta.llama4-maverick-17b-instruct-v1:0 | — |
| `inv:model:us.meta.llama4-scout-17b-instruct-v1:0` | MISSING | creds | us.meta.llama4-scout-17b-instruct-v1:0 | — |
| `inv:model:x-ai/grok-4.6` | MISSING | creds | x-ai/grok-4.6 | — |
| `inv:model:x-preview-f-free` | MISSING | creds | x-preview-f-free | — |
| `inv:model:xiaomi/mimo-v2.5-pro` | MISSING | creds | xiaomi/mimo-v2.5-pro | — |
| `inv:model:z-ai/glm-5.2` | MISSING | creds | z-ai/glm-5.2 | — |
| `inv:model:z-ai/glm-5.3` | MISSING | creds | z-ai/glm-5.3 | — |
| `inv:model:z-ai/glm-5.3-flash` | MISSING | creds | z-ai/glm-5.3-flash | — |
| `inv:model:zai-org/GLM-5` | MISSING | creds | zai-org/GLM-5 | — |
| `inv:model:zai-org/GLM-5.1-FP8` | MISSING | creds | zai-org/GLM-5.1-FP8 | — |
| `inv:model:zai-org/glm-5` | MISSING | creds | zai-org/glm-5 | — |

#### qubit / hermes_plugin (11)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:plugin:context_engine` | MISSING | owner | context_engine | — |
| `inv:plugin:cron_providers` | MISSING | creds | cron_providers | — |
| `inv:plugin:disk-cleanup` | MISSING | owner | disk-cleanup | — |
| `inv:plugin:google_meet` | MISSING | owner | google_meet | — |
| `inv:plugin:hermes-achievements` | MISSING | owner | hermes-achievements | — |
| `inv:plugin:image_gen` | MISSING | owner | image_gen | — |
| `inv:plugin:observability` | MISSING | owner | observability | — |
| `inv:plugin:security-guidance` | MISSING | owner | security-guidance | — |
| `inv:plugin:spotify` | MISSING | owner | spotify | — |
| `inv:plugin:teams_pipeline` | MISSING | owner | teams_pipeline | — |
| `inv:plugin:video_gen` | MISSING | owner | video_gen | — |

#### qubit / hermes_plugin_api (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:plugin-api:hermes-achievements` | MISSING | owner | hermes-achievements | — |

#### qubit / hermes_plugin_asset (4)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:plugin-asset:hermes-achievements:index.js` | MISSING | owner | index.js | — |
| `inv:plugin-asset:hermes-achievements:style.css` | MISSING | owner | style.css | — |
| `inv:plugin-asset:kanban:index.js` | MISSING | owner | index.js | — |
| `inv:plugin-asset:kanban:style.css` | MISSING | owner | style.css | — |

#### qubit / hermes_plugin_test (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:plugin-test:hermes-achievements:test_achievement_engine.py` | MISSING | owner | test_achievement_engine.py | — |

#### qubit / hermes_provider (38)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:provider:ai-gateway` | MISSING | creds | ai-gateway | — |
| `inv:provider:alibaba` | MISSING | creds | alibaba | — |
| `inv:provider:anthropic` | MISSING | creds | anthropic | — |
| `inv:provider:arcee` | MISSING | creds | arcee | — |
| `inv:provider:azure-foundry` | MISSING | creds | azure-foundry | — |
| `inv:provider:bedrock` | MISSING | creds | bedrock | — |
| `inv:provider:copilot` | MISSING | creds | copilot | — |
| `inv:provider:copilot-acp` | MISSING | creds | copilot-acp | — |
| `inv:provider:deepseek` | MISSING | creds | deepseek | — |
| `inv:provider:fireworks` | MISSING | creds | fireworks | — |
| `inv:provider:gemini` | MISSING | creds | gemini | — |
| `inv:provider:gmi` | MISSING | creds | gmi | — |
| `inv:provider:huggingface` | MISSING | creds | huggingface | — |
| `inv:provider:kilocode` | MISSING | creds | kilocode | — |
| `inv:provider:kimi-coding` | MISSING | creds | kimi-coding | — |
| `inv:provider:kimi-coding-cn` | MISSING | creds | kimi-coding-cn | — |
| `inv:provider:lmstudio` | MISSING | creds | lmstudio | — |
| `inv:provider:minimax` | MISSING | creds | minimax | — |
| `inv:provider:minimax-cn` | MISSING | creds | minimax-cn | — |
| `inv:provider:minimax-oauth` | MISSING | creds | minimax-oauth | — |
| `inv:provider:nous` | MISSING | creds | nous | — |
| `inv:provider:novita` | MISSING | creds | novita | — |
| `inv:provider:nvidia` | MISSING | creds | nvidia | — |
| `inv:provider:ollama-cloud` | MISSING | creds | ollama-cloud | — |
| `inv:provider:openai-api` | MISSING | creds | openai-api | — |
| `inv:provider:openai-codex` | MISSING | creds | openai-codex | — |
| `inv:provider:opencode-go` | MISSING | creds | opencode-go | — |
| `inv:provider:opencode-zen` | MISSING | creds | opencode-zen | — |
| `inv:provider:openrouter` | MISSING | creds | openrouter | — |
| `inv:provider:qwen-oauth` | MISSING | creds | qwen-oauth | — |
| `inv:provider:stepfun` | MISSING | creds | stepfun | — |
| `inv:provider:tencent-tokenhub` | MISSING | creds | tencent-tokenhub | — |
| `inv:provider:tencent-tokenplan` | MISSING | creds | tencent-tokenplan | — |
| `inv:provider:vertex` | MISSING | creds | vertex | — |
| `inv:provider:xai` | MISSING | creds | xai | — |
| `inv:provider:xai-oauth` | MISSING | creds | xai-oauth | — |
| `inv:provider:xiaomi` | MISSING | creds | xiaomi | — |
| `inv:provider:zai` | MISSING | creds | zai | — |

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

#### qubit / hermes_tool (83)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:tool:annotate_preview` | MISSING | owner | annotate_preview | — |
| `inv:tool:apply_layout` | MISSING | owner | apply_layout | — |
| `inv:tool:browser_back` | MISSING | hardware | browser_back | — |
| `inv:tool:browser_cdp` | MISSING | hardware | browser_cdp | — |
| `inv:tool:browser_click` | MISSING | hardware | browser_click | — |
| `inv:tool:browser_console` | MISSING | hardware | browser_console | — |
| `inv:tool:browser_dialog` | MISSING | hardware | browser_dialog | — |
| `inv:tool:browser_exec` | MISSING | hardware | browser_exec | — |
| `inv:tool:browser_get_images` | MISSING | hardware | browser_get_images | — |
| `inv:tool:browser_navigate` | MISSING | hardware | browser_navigate | — |
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
| `inv:tool:kanban_block` | MISSING | owner | kanban_block | — |
| `inv:tool:kanban_comment` | MISSING | owner | kanban_comment | — |
| `inv:tool:kanban_complete` | MISSING | owner | kanban_complete | — |
| `inv:tool:kanban_create` | MISSING | owner | kanban_create | — |
| `inv:tool:kanban_heartbeat` | MISSING | owner | kanban_heartbeat | — |
| `inv:tool:kanban_link` | MISSING | owner | kanban_link | — |
| `inv:tool:kanban_list` | MISSING | owner | kanban_list | — |
| `inv:tool:kanban_request_changes` | MISSING | owner | kanban_request_changes | — |
| `inv:tool:kanban_request_review` | MISSING | owner | kanban_request_review | — |
| `inv:tool:kanban_show` | MISSING | owner | kanban_show | — |
| `inv:tool:kanban_unblock` | MISSING | owner | kanban_unblock | — |
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
| `inv:tool:skills_list` | MISSING | owner | skills_list | — |
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

#### qubit / memory (22)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:workflow:memory:hermes_cli/memory_setup.py` | MISSING | owner | hermes_cli/memory_setup.py | — |
| `inv:workflow:memory:hermes_state_common.py` | MISSING | owner | hermes_state_common.py | — |
| `inv:workflow:memory:hermes_state_compression.py` | MISSING | owner | hermes_state_compression.py | — |
| `inv:workflow:memory:hermes_state_dbfile.py` | MISSING | owner | hermes_state_dbfile.py | — |
| `inv:workflow:memory:hermes_state_errors.py` | MISSING | owner | hermes_state_errors.py | — |
| `inv:workflow:memory:hermes_state_fts.py` | MISSING | owner | hermes_state_fts.py | — |
| `inv:workflow:memory:hermes_state_gateway.py` | MISSING | owner | hermes_state_gateway.py | — |
| `inv:workflow:memory:hermes_state_guard.py` | MISSING | owner | hermes_state_guard.py | — |
| `inv:workflow:memory:hermes_state_holders.py` | MISSING | owner | hermes_state_holders.py | — |
| `inv:workflow:memory:hermes_state_maintenance.py` | MISSING | owner | hermes_state_maintenance.py | — |
| `inv:workflow:memory:hermes_state_messages.py` | MISSING | owner | hermes_state_messages.py | — |
| `inv:workflow:memory:hermes_state_portability.py` | MISSING | owner | hermes_state_portability.py | — |
| `inv:workflow:memory:hermes_state_readpool.py` | MISSING | owner | hermes_state_readpool.py | — |
| `inv:workflow:memory:hermes_state_registry.py` | MISSING | owner | hermes_state_registry.py | — |
| `inv:workflow:memory:hermes_state_repair.py` | MISSING | owner | hermes_state_repair.py | — |
| `inv:workflow:memory:hermes_state_schema.py` | MISSING | owner | hermes_state_schema.py | — |
| `inv:workflow:memory:hermes_state_search.py` | MISSING | owner | hermes_state_search.py | — |
| `inv:workflow:memory:hermes_state_sessions.py` | MISSING | owner | hermes_state_sessions.py | — |
| `inv:workflow:memory:hermes_state_telegram.py` | MISSING | creds | hermes_state_telegram.py | — |
| `inv:workflow:memory:hermes_state_titles.py` | MISSING | owner | hermes_state_titles.py | — |
| `inv:workflow:memory:hermes_state_usage.py` | MISSING | owner | hermes_state_usage.py | — |
| `inv:workflow:memory:hermes_state_wal.py` | MISSING | owner | hermes_state_wal.py | — |

#### qubit / messaging (8)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:workflow:messaging:hermes_cli/setup_whatsapp_cloud.py` | MISSING | creds | hermes_cli/setup_whatsapp_cloud.py | — |
| `inv:workflow:messaging:hermes_cli/slack_cli.py` | MISSING | creds | hermes_cli/slack_cli.py | — |
| `inv:workflow:messaging:hermes_cli/telegram_managed_bot.py` | MISSING | creds | hermes_cli/telegram_managed_bot.py | — |
| `inv:workflow:messaging:hermes_cli/web_server_messaging.py` | MISSING | creds | hermes_cli/web_server_messaging.py | — |
| `inv:workflow:messaging:tools/discord_tool.py` | MISSING | creds | tools/discord_tool.py | — |
| `inv:workflow:messaging:tools/feishu_doc_tool.py` | MISSING | creds | tools/feishu_doc_tool.py | — |
| `inv:workflow:messaging:tools/feishu_drive_tool.py` | MISSING | creds | tools/feishu_drive_tool.py | — |
| `inv:workflow:messaging:tools/feishu_lark.py` | MISSING | creds | tools/feishu_lark.py | — |

#### qubit / openclaw_extension (132)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:openclaw-ext:a2a` | MISSING | owner | a2a | — |
| `inv:openclaw-ext:acpx` | MISSING | owner | acpx | — |
| `inv:openclaw-ext:admin-http-rpc` | MISSING | owner | admin-http-rpc | — |
| `inv:openclaw-ext:alibaba` | MISSING | owner | alibaba | — |
| `inv:openclaw-ext:amazon-bedrock` | MISSING | owner | amazon-bedrock | — |
| `inv:openclaw-ext:amazon-bedrock-mantle` | MISSING | owner | amazon-bedrock-mantle | — |
| `inv:openclaw-ext:anthropic` | MISSING | owner | anthropic | — |
| `inv:openclaw-ext:anthropic-vertex` | MISSING | owner | anthropic-vertex | — |
| `inv:openclaw-ext:arcee` | MISSING | owner | arcee | — |
| `inv:openclaw-ext:azure-speech` | MISSING | owner | azure-speech | — |
| `inv:openclaw-ext:baseten` | MISSING | owner | baseten | — |
| `inv:openclaw-ext:beam` | MISSING | owner | beam | — |
| `inv:openclaw-ext:bonjour` | MISSING | owner | bonjour | — |
| `inv:openclaw-ext:brave` | MISSING | owner | brave | — |
| `inv:openclaw-ext:buzz` | MISSING | owner | buzz | — |
| `inv:openclaw-ext:byteplus` | MISSING | owner | byteplus | — |
| `inv:openclaw-ext:cerebras` | MISSING | owner | cerebras | — |
| `inv:openclaw-ext:chutes` | MISSING | owner | chutes | — |
| `inv:openclaw-ext:clawrouter` | MISSING | owner | clawrouter | — |
| `inv:openclaw-ext:clickclack` | MISSING | owner | clickclack | — |
| `inv:openclaw-ext:cloudflare-ai-gateway` | MISSING | owner | cloudflare-ai-gateway | — |
| `inv:openclaw-ext:codex` | MISSING | owner | codex | — |
| `inv:openclaw-ext:cohere` | MISSING | owner | cohere | — |
| `inv:openclaw-ext:comfy` | MISSING | owner | comfy | — |
| `inv:openclaw-ext:copilot` | MISSING | owner | copilot | — |
| `inv:openclaw-ext:copilot-proxy` | MISSING | owner | copilot-proxy | — |
| `inv:openclaw-ext:crabbox` | MISSING | owner | crabbox | — |
| `inv:openclaw-ext:cua-computer` | MISSING | hardware | cua-computer | — |
| `inv:openclaw-ext:deepgram` | MISSING | owner | deepgram | — |
| `inv:openclaw-ext:deepinfra` | MISSING | owner | deepinfra | — |
| `inv:openclaw-ext:deepseek` | MISSING | owner | deepseek | — |
| `inv:openclaw-ext:device-pair` | MISSING | hardware | device-pair | — |
| `inv:openclaw-ext:diagnostics-otel` | MISSING | owner | diagnostics-otel | — |
| `inv:openclaw-ext:diagnostics-prometheus` | MISSING | owner | diagnostics-prometheus | — |
| `inv:openclaw-ext:diffs` | MISSING | owner | diffs | — |
| `inv:openclaw-ext:diffs-language-pack` | MISSING | owner | diffs-language-pack | — |
| `inv:openclaw-ext:document-extract` | MISSING | owner | document-extract | — |
| `inv:openclaw-ext:duckduckgo` | MISSING | owner | duckduckgo | — |
| `inv:openclaw-ext:exa` | MISSING | owner | exa | — |
| `inv:openclaw-ext:fal` | MISSING | owner | fal | — |
| `inv:openclaw-ext:featherless` | MISSING | owner | featherless | — |
| `inv:openclaw-ext:firecrawl` | MISSING | owner | firecrawl | — |
| `inv:openclaw-ext:fireworks` | MISSING | owner | fireworks | — |
| `inv:openclaw-ext:fish-audio-speech` | MISSING | hardware | fish-audio-speech | — |
| `inv:openclaw-ext:geolocation` | MISSING | owner | geolocation | — |
| `inv:openclaw-ext:github-copilot` | MISSING | owner | github-copilot | — |
| `inv:openclaw-ext:gmi` | MISSING | owner | gmi | — |
| `inv:openclaw-ext:google` | MISSING | owner | google | — |
| `inv:openclaw-ext:google-meet` | MISSING | owner | google-meet | — |
| `inv:openclaw-ext:googlechat` | MISSING | owner | googlechat | — |
| `inv:openclaw-ext:gradium` | MISSING | owner | gradium | — |
| `inv:openclaw-ext:groq` | MISSING | owner | groq | — |
| `inv:openclaw-ext:huggingface` | MISSING | owner | huggingface | — |
| `inv:openclaw-ext:image-generation-core` | MISSING | owner | image-generation-core | — |
| `inv:openclaw-ext:imap` | MISSING | creds | imap | — |
| `inv:openclaw-ext:inworld` | MISSING | owner | inworld | — |
| `inv:openclaw-ext:irc` | MISSING | owner | irc | — |
| `inv:openclaw-ext:kilocode` | MISSING | owner | kilocode | — |
| `inv:openclaw-ext:kimi-coding` | MISSING | owner | kimi-coding | — |
| `inv:openclaw-ext:line` | MISSING | owner | line | — |
| `inv:openclaw-ext:linux-node` | MISSING | owner | linux-node | — |
| `inv:openclaw-ext:litellm` | MISSING | owner | litellm | — |
| `inv:openclaw-ext:llama-cpp` | MISSING | owner | llama-cpp | — |
| `inv:openclaw-ext:llm-task` | MISSING | owner | llm-task | — |
| `inv:openclaw-ext:lmstudio` | MISSING | owner | lmstudio | — |
| `inv:openclaw-ext:lobster` | MISSING | owner | lobster | — |
| `inv:openclaw-ext:longcat` | MISSING | owner | longcat | — |
| `inv:openclaw-ext:mattermost` | MISSING | owner | mattermost | — |
| `inv:openclaw-ext:memory-core` | MISSING | owner | memory-core | — |
| `inv:openclaw-ext:memory-lancedb` | MISSING | owner | memory-lancedb | — |
| `inv:openclaw-ext:memory-wiki` | MISSING | owner | memory-wiki | — |
| `inv:openclaw-ext:meta` | MISSING | owner | meta | — |
| `inv:openclaw-ext:microsoft` | MISSING | hardware | microsoft | — |
| `inv:openclaw-ext:microsoft-foundry` | MISSING | hardware | microsoft-foundry | — |
| `inv:openclaw-ext:migrate-claude` | MISSING | owner | migrate-claude | — |
| `inv:openclaw-ext:migrate-hermes` | MISSING | owner | migrate-hermes | — |
| `inv:openclaw-ext:minimax` | MISSING | owner | minimax | — |
| `inv:openclaw-ext:mistral` | MISSING | owner | mistral | — |
| `inv:openclaw-ext:moonshot` | MISSING | owner | moonshot | — |
| `inv:openclaw-ext:msteams` | MISSING | owner | msteams | — |
| `inv:openclaw-ext:mxc` | MISSING | owner | mxc | — |
| `inv:openclaw-ext:nextcloud-talk` | MISSING | owner | nextcloud-talk | — |
| `inv:openclaw-ext:nostr` | MISSING | owner | nostr | — |
| `inv:openclaw-ext:novita` | MISSING | owner | novita | — |
| `inv:openclaw-ext:nvidia` | MISSING | owner | nvidia | — |
| `inv:openclaw-ext:oc-path` | MISSING | owner | oc-path | — |
| `inv:openclaw-ext:ollama` | MISSING | owner | ollama | — |
| `inv:openclaw-ext:onepassword` | MISSING | owner | onepassword | — |
| `inv:openclaw-ext:openai` | MISSING | owner | openai | — |
| `inv:openclaw-ext:opencode-go` | MISSING | owner | opencode-go | — |
| `inv:openclaw-ext:openrouter` | MISSING | owner | openrouter | — |
| `inv:openclaw-ext:openshell` | MISSING | owner | openshell | — |
| `inv:openclaw-ext:perplexity` | MISSING | owner | perplexity | — |
| `inv:openclaw-ext:pixverse` | MISSING | owner | pixverse | — |
| `inv:openclaw-ext:qa-channel` | MISSING | creds | qa-channel | — |
| `inv:openclaw-ext:qa-lab` | MISSING | owner | qa-lab | — |
| `inv:openclaw-ext:qianfan` | MISSING | owner | qianfan | — |
| `inv:openclaw-ext:qwen` | MISSING | owner | qwen | — |
| `inv:openclaw-ext:raft` | MISSING | owner | raft | — |
| `inv:openclaw-ext:reef` | MISSING | owner | reef | — |
| `inv:openclaw-ext:runway` | MISSING | owner | runway | — |
| `inv:openclaw-ext:searxng` | MISSING | owner | searxng | — |
| `inv:openclaw-ext:senseaudio` | MISSING | hardware | senseaudio | — |
| `inv:openclaw-ext:sglang` | MISSING | owner | sglang | — |
| `inv:openclaw-ext:sms` | MISSING | owner | sms | — |
| `inv:openclaw-ext:stepfun` | MISSING | owner | stepfun | — |
| `inv:openclaw-ext:synology-chat` | MISSING | owner | synology-chat | — |
| `inv:openclaw-ext:synthetic` | MISSING | owner | synthetic | — |
| `inv:openclaw-ext:talk-voice` | MISSING | owner | talk-voice | — |
| `inv:openclaw-ext:tavily` | MISSING | owner | tavily | — |
| `inv:openclaw-ext:teams-meetings` | MISSING | owner | teams-meetings | — |
| `inv:openclaw-ext:tencent` | MISSING | owner | tencent | — |
| `inv:openclaw-ext:test-support` | MISSING | owner | test-support | — |
| `inv:openclaw-ext:tlon` | MISSING | owner | tlon | — |
| `inv:openclaw-ext:together` | MISSING | owner | together | — |
| `inv:openclaw-ext:tokenjuice` | MISSING | creds | tokenjuice | — |
| `inv:openclaw-ext:tts-local-cli` | MISSING | owner | tts-local-cli | — |
| `inv:openclaw-ext:twitch` | MISSING | owner | twitch | — |
| `inv:openclaw-ext:venice` | MISSING | owner | venice | — |
| `inv:openclaw-ext:vercel-ai-gateway` | MISSING | owner | vercel-ai-gateway | — |
| `inv:openclaw-ext:visitor-access` | MISSING | owner | visitor-access | — |
| `inv:openclaw-ext:vllm` | MISSING | owner | vllm | — |
| `inv:openclaw-ext:volcengine` | MISSING | owner | volcengine | — |
| `inv:openclaw-ext:voyage` | MISSING | owner | voyage | — |
| `inv:openclaw-ext:vydra` | MISSING | owner | vydra | — |
| `inv:openclaw-ext:web-readability` | MISSING | owner | web-readability | — |
| `inv:openclaw-ext:xai` | MISSING | owner | xai | — |
| `inv:openclaw-ext:xiaomi` | MISSING | owner | xiaomi | — |
| `inv:openclaw-ext:zai` | MISSING | owner | zai | — |
| `inv:openclaw-ext:zalo` | MISSING | owner | zalo | — |
| `inv:openclaw-ext:zalouser` | MISSING | owner | zalouser | — |
| `inv:openclaw-ext:zoom-meetings` | MISSING | owner | zoom-meetings | — |

#### qubit / reports (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:workflow:reports:hermes_cli/doctor_report.py` | MISSING | owner | hermes_cli/doctor_report.py | — |

#### qubit / runtime (19)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:workflow:runtime:hermes_cli/local_runtime/__init__.py` | MISSING | owner | hermes_cli/local_runtime/__init__.py | — |
| `inv:workflow:runtime:hermes_cli/local_runtime/binaries.py` | MISSING | owner | hermes_cli/local_runtime/binaries.py | — |
| `inv:workflow:runtime:hermes_cli/local_runtime/bootstrap.py` | MISSING | owner | hermes_cli/local_runtime/bootstrap.py | — |
| `inv:workflow:runtime:hermes_cli/local_runtime/capabilities.py` | MISSING | owner | hermes_cli/local_runtime/capabilities.py | — |
| `inv:workflow:runtime:hermes_cli/local_runtime/catalog.py` | MISSING | owner | hermes_cli/local_runtime/catalog.py | — |
| `inv:workflow:runtime:hermes_cli/local_runtime/context_policy.py` | MISSING | owner | hermes_cli/local_runtime/context_policy.py | — |
| `inv:workflow:runtime:hermes_cli/local_runtime/detect.py` | MISSING | owner | hermes_cli/local_runtime/detect.py | — |
| `inv:workflow:runtime:hermes_cli/local_runtime/endpoint.py` | MISSING | owner | hermes_cli/local_runtime/endpoint.py | — |
| `inv:workflow:runtime:hermes_cli/local_runtime/estimator.py` | MISSING | owner | hermes_cli/local_runtime/estimator.py | — |
| `inv:workflow:runtime:hermes_cli/local_runtime/gguf.py` | MISSING | owner | hermes_cli/local_runtime/gguf.py | — |
| `inv:workflow:runtime:hermes_cli/local_runtime/growth.py` | MISSING | owner | hermes_cli/local_runtime/growth.py | — |
| `inv:workflow:runtime:hermes_cli/local_runtime/hardware.py` | MISSING | hardware | hermes_cli/local_runtime/hardware.py | — |
| `inv:workflow:runtime:hermes_cli/local_runtime/hf_browse.py` | MISSING | owner | hermes_cli/local_runtime/hf_browse.py | — |
| `inv:workflow:runtime:hermes_cli/local_runtime/load_progress.py` | MISSING | owner | hermes_cli/local_runtime/load_progress.py | — |
| `inv:workflow:runtime:hermes_cli/local_runtime/presets.py` | MISSING | owner | hermes_cli/local_runtime/presets.py | — |
| `inv:workflow:runtime:hermes_cli/local_runtime/supervisor.py` | MISSING | owner | hermes_cli/local_runtime/supervisor.py | — |
| `inv:workflow:runtime:hermes_cli/runtime_provider.py` | MISSING | creds | hermes_cli/runtime_provider.py | — |
| `inv:workflow:runtime:hermes_cli/runtime_provider_backends.py` | MISSING | creds | hermes_cli/runtime_provider_backends.py | — |
| `inv:workflow:runtime:hermes_cli/runtime_provider_custom.py` | MISSING | creds | hermes_cli/runtime_provider_custom.py | — |

#### qubit / schedules (22)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:workflow:schedules:cron/__init__.py` | MISSING | owner | cron/__init__.py | — |
| `inv:workflow:schedules:cron/blueprint_catalog.py` | MISSING | owner | cron/blueprint_catalog.py | — |
| `inv:workflow:schedules:cron/delivery_queue.py` | MISSING | owner | cron/delivery_queue.py | — |
| `inv:workflow:schedules:cron/executions.py` | MISSING | owner | cron/executions.py | — |
| `inv:workflow:schedules:cron/incidents.py` | MISSING | owner | cron/incidents.py | — |
| `inv:workflow:schedules:cron/jobs.py` | MISSING | owner | cron/jobs.py | — |
| `inv:workflow:schedules:cron/ledger.py` | MISSING | owner | cron/ledger.py | — |
| `inv:workflow:schedules:cron/lifecycle_guard.py` | MISSING | owner | cron/lifecycle_guard.py | — |
| `inv:workflow:schedules:cron/monitor.py` | MISSING | owner | cron/monitor.py | — |
| `inv:workflow:schedules:cron/notepad.py` | MISSING | owner | cron/notepad.py | — |
| `inv:workflow:schedules:cron/occurrences.py` | MISSING | owner | cron/occurrences.py | — |
| `inv:workflow:schedules:cron/scheduler.py` | MISSING | owner | cron/scheduler.py | — |
| `inv:workflow:schedules:cron/scheduler_delivery.py` | MISSING | owner | cron/scheduler_delivery.py | — |
| `inv:workflow:schedules:cron/scheduler_detached_worker.py` | MISSING | owner | cron/scheduler_detached_worker.py | — |
| `inv:workflow:schedules:cron/scheduler_diagnostics.py` | MISSING | owner | cron/scheduler_diagnostics.py | — |
| `inv:workflow:schedules:cron/scheduler_preflight.py` | MISSING | owner | cron/scheduler_preflight.py | — |
| `inv:workflow:schedules:cron/scheduler_prompt.py` | MISSING | owner | cron/scheduler_prompt.py | — |
| `inv:workflow:schedules:cron/scheduler_provider.py` | MISSING | creds | cron/scheduler_provider.py | — |
| `inv:workflow:schedules:cron/scheduler_script.py` | MISSING | owner | cron/scheduler_script.py | — |
| `inv:workflow:schedules:cron/suggestion_catalog.py` | MISSING | owner | cron/suggestion_catalog.py | — |
| `inv:workflow:schedules:cron/suggestions.py` | MISSING | owner | cron/suggestions.py | — |
| `inv:workflow:schedules:hermes_cli/cron.py` | MISSING | owner | hermes_cli/cron.py | — |
