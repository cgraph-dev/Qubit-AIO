# Qubit gap ledger

Machine-generated from `docs/QUBIT_GAP_LEDGER.*.json`. This merged report is the authoritative view; the input JSON sections are never modified.

> **COMPLETION GATE:** NOT COMPLETE while any row has status != IMPLEMENTED.

## Provenance pins

- `docs/QUBIT_GAP_LEDGER.hermes.json` — schema `qubit-gap-ledger-section/1`, source `hermes`, 573 rows
  - counts: {"by_area": {"analytics-config": 4, "audio-voice": 6, "chat-websocket": 5, "cli": 72, "cli-subcommand": 200, "config-env": 15, "cron": 13, "dashboard-auth": 11, "dashboard-page": 20, "dashboard-ui": 15, "dashboard_plugin": 5, "files-fs": 18, "gateway-actions": 6, "git": 21, "local-models": 16, "mcp": 12, "memory": 5, "messaging": 11, "models": 7, "ops": 28, "profiles": 20, "provider-oauth": 6, "se
  - generated: 2026-09-12
  - method: Evidence-based regeneration. API rows re-extract the pinned upstream route decorators and classify them from voicebridge.hermes_compat.match disposition plus the handler's real behaviour (a handler whose normal exits are typed gates/refusals is GATED; a real read/write is IMPLEMENTED; a wire-shape stub is PARTIAL). IMPLEMENTED additionally requires an on-disk tests/ function that dispatches the ro
  - plugin_fleet: [{"api": "plugins/hermes-achievements/dashboard/plugin_api.py", "has_dashboard": true, "manifest": "plugins/hermes-achievements/dashboard/manifest.json", "name": "hermes-achievements", "source": "vendored"}, {"api": "plugins/kanban/dashboard/plugin_api.py", "has_dashboard": true, "manifest": "plugins/kanban/dashboard/manifest.json", "name": "kanban", "source": "vendored"}, {"has_dashboard": false,
  - sources_reconciled: ["voicebridge/hermes_compat.py", "voicebridge/hermes_plugins.py", "voicebridge/cli.py", "voicebridge/cli_hermes_forward.py", "tests/ (AST caller evidence)", "tests/test_hermes_plugins.py (plugin-fleet proof)", "qubit_hermes/hermes_cli/web_routers", "qubit_hermes/hermes_cli/web_server*.py", "qubit_hermes/hermes_cli/memory_oauth.py", "qubit_hermes/hermes_cli/dashboard_auth/routes.py", "qubit_hermes/
  - upstream_pin: 2237be355906fbe6065ce1815711eee52b2d646e
- `docs/QUBIT_GAP_LEDGER.openclaw.json` — schema `qubit-gap-ledger-section/1`, source `openclaw`, 104 rows
  - counts: {"by_area": {"automation": 10, "boards": 6, "browser": 5, "dashboard": 4, "desktop": 5, "gateway": 10, "ide": 7, "messaging": 11, "policy": 2, "reports": 6, "runtime": 9, "sessions": 10, "skills": 7, "web_media": 9, "webhooks": 3}, "by_status": {"GATED": 4, "IMPLEMENTED": 94, "PARTIAL": 6}, "total": 104}
  - extension_map: {"by_disposition": {"distinct": 3, "gate": 123, "parent": 28}, "by_needs": {"creds": 115, "hardware": 8, "none": 27, "owner": 4}, "covered": true, "total": 154, "unexplained": []}
  - extension_wave5: {"by_disposition": {"distinct": 2, "gate": 24, "parent": 4}, "by_needs": {"creds": 23, "hardware": 1, "none": 6, "owner": 0}, "covered": true, "extensions": [{"disposition": "gate", "form": "voicebridge/channels_inbound.py normalize_event", "gate": "CHANNEL_CREDENTIAL_REQUIRED", "name": "a2a", "needs": "creds", "parent": "OC-19", "proving": "tests/test_openclaw_extensions_wave5.py"}, {"disposition
  - generated: 2026-09-12
  - method: Evidence-based regeneration. The audited capability inventory (72 rows @v2026.9.2 + 32 delta rows @v2026.9.4) is embedded as a baseline; a documented ROW_EVIDENCE table records the rows closed or upgraded by real Qubit modules + behavioural tests. Every IMPLEMENTED row is mechanically re-verified against the code and an on-disk proving test that references the cited backend; a row upgraded from an
  - sources_reconciled: ["docs/AUDIT_OPENCLAW_2026-09-11.md", "docs/AUDIT_OPENCLAW_V2026_9_4_DELTA.md", "voicebridge/automation.py", "voicebridge/schedules.py", "tests/test_automation_agent6_*.py", "tests/test_openclaw_e5_directives.py", "tests/test_oc_*_agent[5-9]*.py (Agent 5-10 witness tests)", "tests/ (proving-test evidence)"]
  - upstream_pin: 3a9d69db306cd7f081e06254cb89c4bcc14a7107
  - upstream_tag: v2026.9.4
  - upstream_tree: /tmp/opencode/pinned/openclaw (read via git show 3a9d69db306cd7f081e06254cb89c4bcc14a7107, shallow clone)
- `docs/QUBIT_GAP_LEDGER.qubit.json` — schema `qubit-gap-ledger-section/1`, source `qubit-surface`, 740 rows
  - generated_from: cli_families=106, dashboard_ops.EXTRA_OPS=34, dashboard_ops.catalog=510, hermes_compat.GATED=102, hermes_compat.ROUTES=361, source_inventory.dispositioned=141, source_inventory.dispositioned_gated=25, source_inventory.dispositioned_implemented=116, source_inventory.distinct_uncovered=1, source_inventory.items=6776, source_inventory.parented_covered=1054, source_inventory.parented_uncovered=26, source_inventory.surface_items=1819, voice_commands.CLI_COVERAGE=106, voice_commands.REGISTRY=476
  - evidence: production backend + declared interfaces + live CLI tree + voice registry + linked tests/ AST proofs + direct source inventory
  - generator: scripts/rebuild_ledger_qubit.py
  - backend_flags: []
  - generated: 2026-09-12
  - inventory: {"by_category": {"hermes_backend_route": 1, "hermes_cli_option": 23, "hermes_cli_positional": 3}, "by_parent": {"cli:computer-use install": 1, "cli:config set": 1, "cli:gateway install": 3, "cli:gateway run": 2, "cli:mcp add": 7, "cli:plugins install": 6, "cli:skills install": 3, "cli:skills publish": 3}, "covered": 1651, "covered_by_parent": {"OC-02": 5, "OC-16": 1, "OC-19": 5, "OC-30": 1, "OC-34
  - method: Evidence-based regeneration. Catalog rows derive their status from the live production backend (voice_commands.REGISTRY / _BACKENDS + dashboard_ops.EXTRA_OPS), the declared interfaces (catalog), the live CLI parser tree, the registry voice surface, and an AST scan of tests/ for a proving caller. A plugin/page row declares only the dashboard surface (no CLI/voice by design); it is IMPLEMENTED when
  - sources_reconciled: ["voicebridge/voice_commands.py (REGISTRY/_BACKENDS/CLI_COVERAGE)", "voicebridge/dashboard_ops.py (catalog/EXTRA_OPS)", "voicebridge/cli.py (build_parser)", "voicebridge/hermes_compat.py (ROUTES/GATED)", "voicebridge/hermes_plugins.py (vendored plugin fleet wrapper)", "qubit_hermes/toolsets.py + tools/registry.py (fork tool runtime)", "qubit_hermes/skills + optional-skills (fork skill runtime)", "

## Counts

- Total rows: **1417**
- Open rows (status != IMPLEMENTED): **213**

### By status

- GATED: 174
- IMPLEMENTED: 1204
- MISSING: 28
- PARTIAL: 11

### By source

- hermes: 573
- openclaw: 104
- qubit: 740

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
- cli-subcommand: 200
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
- hermes_cli_option: 23
- hermes_cli_positional: 3
- hermes_gated: 30
- hermes_plugin: 9
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
- plugins: 1
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
- skills: 28
- state: 2
- status: 1
- status-curator: 17
- sync: 5
- task_ledger: 4
- task_run: 2
- telemetry: 1
- themes: 7
- tools: 14
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
- Unexplained omissions: **27**
- `qubit`: present=True, items=6776, matched=1792, unmatched=27, unexplained=27

## Open rows (status != IMPLEMENTED)

### source: hermes (116 open)

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

#### hermes / cli-subcommand (21)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `cli:computer-use install` | GATED | creds | hermes computer-use install | tests/test_cli_agent1_wiring.py::test_computer_use_screen_arrangement |
| `cli:computer-use permissions grant` | GATED | creds | hermes computer-use permissions grant | tests/test_cli_agent1_wiring.py::test_computer_use_screen_arrangement |
| `cli:config set` | GATED | creds | hermes config set | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:config unset` | GATED | creds | hermes config unset | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:gateway install` | GATED | creds | hermes gateway install | tests/test_cli_agent1_wiring.py::test_gateway_supervision_and_discovery |
| `cli:gateway restart` | GATED | creds | hermes gateway restart | tests/test_cli_agent1_wiring.py::test_gateway_supervision_and_discovery |
| `cli:gateway run` | GATED | creds | hermes gateway run | tests/test_cli_agent1_wiring.py::test_gateway_supervision_and_discovery |
| `cli:gateway start` | GATED | creds | hermes gateway start | tests/test_cli_agent1_wiring.py::test_gateway_supervision_and_discovery |
| `cli:gateway stop` | GATED | creds | hermes gateway stop | tests/test_cli_agent1_wiring.py::test_gateway_supervision_and_discovery |
| `cli:mcp add` | GATED | creds | hermes mcp add | tests/test_cli_agent1_forward.py::test_translate_maps_family_and_preserves_trailing_args |
| `cli:mcp configure` | GATED | creds | hermes mcp configure | tests/test_cli_agent1_forward.py::test_translate_maps_family_and_preserves_trailing_args |
| `cli:mcp remove` | GATED | creds | hermes mcp remove | tests/test_cli_agent1_forward.py::test_translate_maps_family_and_preserves_trailing_args |
| `cli:plugins install` | GATED | creds | hermes plugins install | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:plugins update` | GATED | creds | hermes plugins update | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:sessions archive` | GATED | creds | hermes sessions archive | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:sessions delete` | GATED | creds | hermes sessions delete | tests/test_session_lifecycle8u.py::test_cli_sessions_subcommands_roundtrip |
| `cli:sessions prune` | GATED | creds | hermes sessions prune | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
| `cli:sessions rename` | GATED | creds | hermes sessions rename | tests/test_session_lifecycle8u.py::test_cli_sessions_subcommands_roundtrip |
| `cli:skills install` | GATED | creds | hermes skills install | tests/test_cli_agent1_forward.py::test_translate_ignores_unmapped_and_short_calls |
| `cli:skills publish` | GATED | creds | hermes skills publish | tests/test_cli_agent1_families.py::test_forward_mapped_families_reach_the_fork_argv |
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

### source: qubit (87 open)

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

#### qubit / hermes_cli_option (23)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:hermes_computer-use_install_--upgrade` | MISSING | hardware | --upgrade | — |
| `inv:hermes_config_set_--force` | MISSING | owner | --force | — |
| `inv:hermes_gateway_install_--no-start-now` | MISSING | owner | --no-start-now | — |
| `inv:hermes_gateway_install_--no-start-on-login` | MISSING | owner | --no-start-on-login | — |
| `inv:hermes_gateway_install_--run-as-user` | MISSING | owner | --run-as-user | — |
| `inv:hermes_gateway_run_--verbose` | MISSING | owner | --verbose | — |
| `inv:hermes_gateway_run_-v` | MISSING | owner | -v | — |
| `inv:hermes_mcp_add_--args` | MISSING | owner | --args | — |
| `inv:hermes_mcp_add_--auth` | MISSING | creds | --auth | — |
| `inv:hermes_mcp_add_--command` | MISSING | owner | --command | — |
| `inv:hermes_mcp_add_--connect-timeout` | MISSING | owner | --connect-timeout | — |
| `inv:hermes_mcp_add_--env` | MISSING | owner | --env | — |
| `inv:hermes_mcp_add_--preset` | MISSING | owner | --preset | — |
| `inv:hermes_mcp_add_--url` | MISSING | owner | --url | — |
| `inv:hermes_plugins_install_--enable` | MISSING | owner | --enable | — |
| `inv:hermes_plugins_install_--force` | MISSING | owner | --force | — |
| `inv:hermes_plugins_install_--no-enable` | MISSING | owner | --no-enable | — |
| `inv:hermes_plugins_install_--ref` | MISSING | owner | --ref | — |
| `inv:hermes_plugins_install_-f` | MISSING | owner | -f | — |
| `inv:hermes_skills_install_--category` | MISSING | owner | --category | — |
| `inv:hermes_skills_install_--name` | MISSING | owner | --name | — |
| `inv:hermes_skills_publish_--repo` | MISSING | owner | --repo | — |
| `inv:hermes_skills_publish_--to` | MISSING | owner | --to | — |

#### qubit / hermes_cli_positional (3)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:hermes_plugins_install_identifier` | MISSING | owner | identifier | — |
| `inv:hermes_skills_install_identifier` | MISSING | owner | identifier | — |
| `inv:hermes_skills_publish_skill_path` | MISSING | owner | skill_path | — |

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

#### qubit / hermes_skill (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:skill:skills/index-cache` | GATED | owner | index-cache | — |

#### qubit / openclaw_extension (24)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:openclaw-ext:a2a` | GATED | creds | a2a | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:buzz` | GATED | creds | buzz | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:clickclack` | GATED | creds | clickclack | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:cua-computer` | GATED | hardware | cua-computer | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:googlechat` | GATED | creds | googlechat | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:image-generation-core` | GATED | creds | image-generation-core | tests/test_openclaw_closure_wave2_webmedia.py |
| `inv:openclaw-ext:imap` | GATED | creds | imap | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:irc` | GATED | creds | irc | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:line` | GATED | creds | line | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:mattermost` | GATED | creds | mattermost | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:msteams` | GATED | creds | msteams | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:nextcloud-talk` | GATED | creds | nextcloud-talk | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:nostr` | GATED | creds | nostr | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:qa-channel` | GATED | creds | qa-channel | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:raft` | GATED | creds | raft | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:reef` | GATED | creds | reef | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:sms` | GATED | creds | sms | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:synology-chat` | GATED | creds | synology-chat | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:test-support` | GATED | none | test-support | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:tlon` | GATED | creds | tlon | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:twitch` | GATED | creds | twitch | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:visitor-access` | GATED | creds | visitor-access | tests/test_openclaw_parity_wave1.py |
| `inv:openclaw-ext:zalo` | GATED | creds | zalo | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:zalouser` | GATED | creds | zalouser | tests/test_openclaw_extensions_wave5.py |
