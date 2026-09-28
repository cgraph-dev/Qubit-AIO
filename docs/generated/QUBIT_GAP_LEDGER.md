# Qubit gap ledger

Machine-generated from `docs/generated/QUBIT_GAP_LEDGER.*.json`. This merged report is the authoritative view; the input JSON sections are never modified.

> **COMPLETION GATE:** CODE-READY while every row is IMPLEMENTED, SAFE_EQUIVALENT, or OPERATOR_ONLY_EXTERNAL; OPERATOR_ONLY_EXTERNAL rows remain manual/operator qualification items. OPERATOR_ONLY_ARCHITECTURE rows are OPEN and are not terminal until individually approved (owner amendment 2026-09-22)..

## Provenance pins

- `docs/generated/QUBIT_GAP_LEDGER.hermes.json` — schema `qubit-gap-ledger-section/1`, source `hermes`, 591 rows
  - counts: {"by_area": {"analytics-config": 4, "audio-voice": 8, "chat-websocket": 5, "chat_workspaces": 1, "cli": 72, "cli-subcommand": 200, "config-env": 15, "cron": 13, "dashboard-auth": 11, "dashboard-page": 20, "dashboard-ui": 17, "dashboard_plugin": 5, "display": 1, "files-fs": 18, "gateway-actions": 8, "git": 21, "local-models": 18, "mcp": 15, "memory": 5, "messaging": 11, "models": 7, "ops": 28, "pro
  - generated: 2026-09-12
  - method: Evidence-based regeneration. API rows re-extract the pinned upstream route decorators and classify them from voicebridge.qubit_compat.match disposition plus the handler's real behaviour (a handler whose normal exits are typed gates/refusals is GATED; a real read/write is IMPLEMENTED; a wire-shape stub is PARTIAL). IMPLEMENTED additionally requires an on-disk tests/ function that dispatches the rou
  - plugin_fleet: [{"api": "plugins/qubit-achievements/dashboard/plugin_api.py", "has_dashboard": true, "manifest": "plugins/qubit-achievements/dashboard/manifest.json", "name": "qubit-achievements", "source": "vendored"}, {"api": "plugins/kanban/dashboard/plugin_api.py", "has_dashboard": true, "manifest": "plugins/kanban/dashboard/manifest.json", "name": "kanban", "source": "vendored"}, {"has_dashboard": false, "n
  - sources_reconciled: ["voicebridge/qubit_compat.py", "voicebridge/qubit_plugins.py", "voicebridge/cli.py", "voicebridge/cli_qubit_forward.py", "tests/ (AST caller evidence)", "tests/test_qubit_plugins.py (plugin-fleet proof)", "qubit_runtime/qubit_cli/web_routers", "qubit_runtime/qubit_cli/web_server*.py", "qubit_runtime/qubit_cli/memory_oauth.py", "qubit_runtime/qubit_cli/dashboard_auth/routes.py", "qubit_runtime/plu
  - upstream_pin: 69948c005791d4d0e51670ca015238c2c7663749
- `docs/generated/QUBIT_GAP_LEDGER.openclaw.json` — schema `qubit-gap-ledger-section/1`, source `openclaw`, 104 rows
  - counts: {"by_area": {"automation": 10, "boards": 6, "browser": 5, "dashboard": 4, "desktop": 5, "gateway": 10, "ide": 7, "messaging": 11, "policy": 2, "reports": 6, "runtime": 9, "sessions": 10, "skills": 7, "web_media": 9, "webhooks": 3}, "by_gate_class": {"architecture": 1, "external": 7}, "by_status": {"IMPLEMENTED": 94, "OPERATOR_ONLY": 7, "OPERATOR_ONLY_ARCHITECTURE": 1, "SAFE_EQUIVALENT": 2}, "total
  - extension_map: {"by_disposition": {"distinct": 3, "gate": 123, "parent": 28}, "by_needs": {"creds": 115, "hardware": 8, "none": 27, "owner": 4}, "covered": true, "total": 154, "unexplained": []}
  - extension_wave5: {"by_disposition": {"distinct": 2, "gate": 24, "parent": 4}, "by_needs": {"creds": 23, "hardware": 1, "none": 6, "owner": 0}, "covered": true, "extensions": [{"disposition": "gate", "form": "voicebridge/channels_inbound.py normalize_event", "gate": "CHANNEL_NO_CREDENTIAL", "name": "a2a", "needs": "creds", "parent": "OC-19", "proving": "tests/test_openclaw_extensions_wave5.py"}, {"disposition": "ga
  - generated: 2026-09-12
  - method: Evidence-based regeneration. The audited capability inventory (72 rows @v2026.9.2 + 32 delta rows @v2026.9.4) is embedded as a baseline; a documented ROW_EVIDENCE table records the rows closed or upgraded by real Qubit modules + behavioural tests. Every IMPLEMENTED row is mechanically re-verified against the code and an on-disk proving test that references the cited backend; a row upgraded from an
  - sources_reconciled: ["docs/AUDIT_OPENCLAW_2026-09-11.md", "docs/AUDIT_OPENCLAW_V2026_9_4_DELTA.md", "voicebridge/automation.py", "voicebridge/schedules.py", "tests/test_automation_agent6_*.py", "tests/test_openclaw_e5_directives.py", "tests/test_oc_*_agent[5-9]*.py (Agent 5-10 witness tests)", "tests/ (proving-test evidence)"]
  - upstream_pin: 3a9d69db306cd7f081e06254cb89c4bcc14a7107
  - upstream_tag: v2026.9.4
  - upstream_tree: /tmp/opencode/pinned/openclaw (read via git show 3a9d69db306cd7f081e06254cb89c4bcc14a7107, shallow clone)
- `docs/generated/QUBIT_GAP_LEDGER.qubit.json` — schema `qubit-gap-ledger-section/1`, source `qubit-surface`, 757 rows
  - generated_from: cli_families=114, dashboard_ops.EXTRA_OPS=35, dashboard_ops.catalog=566, qubit_compat.GATED=94, qubit_compat.ROUTES=450, source_inventory.dispositioned=122, source_inventory.dispositioned_gated=4, source_inventory.dispositioned_implemented=118, source_inventory.distinct_uncovered=0, source_inventory.items=6775, source_inventory.parented_covered=1076, source_inventory.parented_uncovered=5, source_inventory.surface_items=1818, voice_commands.CLI_COVERAGE=114, voice_commands.REGISTRY=514
  - evidence: production backend + declared interfaces + live CLI tree + voice registry + linked tests/ AST proofs + direct source inventory
  - generator: scripts/rebuild_ledger_qubit.py
  - backend_flags: []
  - generated: 2026-09-12
  - inventory: {"by_category": {"hermes_dashboard_plugin": 1, "hermes_plugin_api": 1, "hermes_plugin_asset": 2, "hermes_plugin_test": 1}, "by_parent": {"plugin:hermes-achievements": 5}, "covered": 1691, "covered_by_parent": {"OC-02": 5, "OC-16": 1, "OC-19": 24, "OC-30": 1, "OC-34": 1, "OC-35": 1, "OC-46": 5, "OC-53": 10, "OC-63": 10, "OC-64": 4, "OC-66": 10, "OC-67": 1, "OC-D01": 1, "OC-D03": 1, "OC-D08": 5, "OC
  - method: Evidence-based regeneration. Catalog rows derive their status from the live production backend (voice_commands.REGISTRY / _BACKENDS + dashboard_ops.EXTRA_OPS), the declared interfaces (catalog), the live CLI parser tree, the registry voice surface, and an AST scan of tests/ for a proving caller. A plugin/page row declares only the dashboard surface (no CLI/voice by design); it is IMPLEMENTED when
  - sources_reconciled: ["voicebridge/voice_commands.py (REGISTRY/_BACKENDS/CLI_COVERAGE)", "voicebridge/dashboard_ops.py (catalog/EXTRA_OPS)", "voicebridge/cli.py (build_parser)", "voicebridge/qubit_compat.py (ROUTES/GATED)", "voicebridge/qubit_plugins.py (vendored plugin fleet wrapper)", "qubit_runtime/toolsets.py + tools/registry.py (fork tool runtime)", "qubit_runtime/skills + optional-skills (fork skill runtime)", "

## Counts

- Total rows: **1452**
- Code-side open rows (not terminal): **10**
- SAFE_EQUIVALENT (closed for code readiness): **143**
- OPERATOR_ONLY (manual/operator qualification): **10**
- OPERATOR_ONLY_ARCHITECTURE (OPEN, architecture-gated; needs individual owner approval): **1**
- Code-ready: **False**; fully complete (all manual qualified): **False**

### By status

- IMPLEMENTED: 1289
- MISSING: 7
- OPERATOR_ONLY: 10
- OPERATOR_ONLY_ARCHITECTURE: 1
- SAFE_EQUIVALENT: 143
- UNVERIFIED: 2

### By source

- hermes: 591
- openclaw: 104
- qubit: 757

### By area

- acp: 3
- acp_client: 7
- actions: 11
- active_memory: 7
- agent: 1
- analytics-config: 4
- approvals: 1
- audio: 1
- audio-voice: 8
- audio_hw: 6
- auth_login: 1
- auth_logout: 1
- auth_status: 1
- auth_verify: 1
- automation: 27
- autonomy_expire: 1
- autonomy_list: 1
- autonomy_pause: 1
- autonomy_resume: 1
- autonomy_revoke: 1
- autonomy_show: 1
- autonomy_status: 1
- backup: 5
- boards: 6
- brain: 17
- branding: 3
- browser: 8
- browser_profiles: 5
- browser_tabs: 15
- capability: 2
- channels: 7
- chat-websocket: 5
- chat_sessions: 3
- chat_workspaces: 1
- checkpoints: 2
- cli: 72
- cli-subcommand: 200
- commands: 1
- completions: 1
- computer: 10
- computer_screen: 3
- config-env: 15
- config_profiles: 8
- console_guidance: 1
- cron: 13
- curator: 1
- dashboard: 4
- dashboard-auth: 11
- dashboard-page: 20
- dashboard-ui: 17
- dashboard_plugin: 15
- desktop: 5
- device: 1
- device_enroll: 1
- diagnostics: 1
- display: 1
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
- gateway-actions: 8
- ghost: 20
- git: 25
- hermes_dashboard_plugin: 1
- hermes_gated: 30
- hermes_plugin: 10
- hermes_plugin_api: 1
- hermes_plugin_asset: 2
- hermes_plugin_test: 1
- hermes_skill: 28
- hermes_tool: 73
- hermes_web_page: 21
- hooks: 10
- ide: 9
- import_agent: 2
- insights: 1
- kanban: 48
- lessons: 3
- local-models: 18
- local_models: 13
- logbook: 2
- logs: 1
- loop: 1
- lsp: 3
- mcp: 17
- media_gen: 3
- memory: 8
- messaging: 22
- mic_guidance: 1
- migrate_status: 1
- models: 7
- monitor: 1
- msg: 14
- node_host: 7
- openclaw_extension: 11
- opencode: 14
- ops: 28
- package: 2
- pairing: 6
- pause: 1
- peer: 7
- permissions: 1
- plugin_runtime: 12
- plugins: 1
- plugins_install: 1
- plugins_remote: 1
- policy: 2
- power_status: 1
- product: 7
- profile: 17
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
- schedules: 9
- secrets: 1
- security: 1
- session_share: 5
- sessions: 34
- setup_check: 1
- skills: 28
- speech_profile_clear: 1
- speech_profile_clone: 1
- speech_profile_create: 1
- speech_profile_list: 1
- speech_profile_remove: 1
- speech_profile_select: 1
- speech_profile_show: 1
- speech_profile_test: 1
- state: 2
- status: 1
- status-curator: 20
- sync: 5
- task_ledger: 4
- task_run: 2
- telemetry: 1
- themes: 7
- tools: 14
- tui_guidance: 1
- update_apply: 1
- update_check: 1
- update_rollback: 1
- update_status: 1
- usage: 1
- verified: 7
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
- Unexplained omissions: **5**
- `qubit`: present=True, items=6775, matched=1813, unmatched=5, unexplained=5

## Code-side open rows (not IMPLEMENTED/SAFE_EQUIVALENT/OPERATOR_ONLY)

### source: hermes (4 open)

#### hermes / chat_workspaces (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:GET /api/chat/workspaces` | MISSING | owner | GET /api/chat/workspaces | — |

#### hermes / display (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:WEBSOCKET /api/display/ws` | MISSING | owner | WEBSOCKET /api/display/ws | — |

#### hermes / local-models (2)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `api:POST /api/local-models/download/pause` | UNVERIFIED | owner | POST /api/local-models/download/pause | — |
| `api:POST /api/local-models/download/resume` | UNVERIFIED | owner | POST /api/local-models/download/resume | — |

### source: openclaw (1 open)

#### openclaw / sessions (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-48` | OPERATOR_ONLY_ARCHITECTURE | creds | Markdown workspace memory / dreaming / external providers (LanceDB/Wiki/Honcho) | tests/test_openclaw_closure_wave2_webmedia.py |

### source: qubit (5 open)

#### qubit / hermes_dashboard_plugin (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:plugin-manifest:hermes-achievements` | MISSING | owner | hermes-achievements | — |

#### qubit / hermes_plugin_api (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:plugin-api:hermes-achievements` | MISSING | owner | hermes-achievements | — |

#### qubit / hermes_plugin_asset (2)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:plugin-asset:hermes-achievements:index.js` | MISSING | owner | index.js | — |
| `inv:plugin-asset:hermes-achievements:style.css` | MISSING | owner | style.css | — |

#### qubit / hermes_plugin_test (1)

| id | status | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `inv:plugin-test:hermes-achievements:test_achievement_engine.py` | MISSING | owner | test_achievement_engine.py | — |

## Terminal gate rows (SAFE_EQUIVALENT / OPERATOR_ONLY)

### SAFE_EQUIVALENT — Qubit-safe equivalents (closed; unsafe raw behavior explicitly refused) (143)

| id | source | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-01` | openclaw | owner | Gateway protocol server + RPC v4 | tests/test_openclaw_parity_wave1.py |
| `OC-37` | openclaw | owner | Workboard dispatch + worker runs | tests/test_kanban8tf.py |
| `api:DELETE /api/credentials/pool/{provider}/{index}` | hermes | creds | DELETE /api/credentials/pool/{provider}/{index} | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:DELETE /api/local-models/models/{model_id}` | hermes | hardware | DELETE /api/local-models/models/{model_id} | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:DELETE /api/mcp/oauth/flows/{flow_id}` | hermes | creds | DELETE /api/mcp/oauth/flows/{flow_id} | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:DELETE /api/mcp/servers/{name}` | hermes | owner | DELETE /api/mcp/servers/{name} | tests/test_dashboard_control_confirm9u.py::test_mcp_enable_and_delete_stage_and_settle |
| `api:DELETE /api/messaging/telegram/onboarding/{pairing_id}` | hermes | creds | DELETE /api/messaging/telegram/onboarding/{pairing_id} | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:DELETE /api/messaging/whatsapp/onboarding/{pairing_id}` | hermes | creds | DELETE /api/messaging/whatsapp/onboarding/{pairing_id} | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:DELETE /api/providers/oauth/sessions/{session_id}` | hermes | creds | DELETE /api/providers/oauth/sessions/{session_id} | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:DELETE /api/providers/oauth/{provider_id}` | hermes | creds | DELETE /api/providers/oauth/{provider_id} | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/audio/elevenlabs/voices` | hermes | creds | GET /api/audio/elevenlabs/voices | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/config/raw` | hermes | creds | GET /api/config/raw | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/credentials/pool` | hermes | creds | GET /api/credentials/pool | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/files/download` | hermes | owner | GET /api/files/download | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/files/stream` | hermes | owner | GET /api/files/stream | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/fs/download` | hermes | owner | GET /api/fs/download | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/git/gh-auth` | hermes | creds | GET /api/git/gh-auth | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/git/review/ship-info` | hermes | creds | GET /api/git/review/ship-info | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/local-models/search` | hermes | hardware | GET /api/local-models/search | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/local-models/search/files` | hermes | hardware | GET /api/local-models/search/files | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/mcp/oauth/callback/{server_name:path}` | hermes | creds | GET /api/mcp/oauth/callback/{server_name:path} | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/mcp/oauth/flows/{flow_id}` | hermes | creds | GET /api/mcp/oauth/flows/{flow_id} | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/media` | hermes | owner | GET /api/media | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/memory/providers/{provider}/oauth/status` | hermes | creds | GET /api/memory/providers/{provider}/oauth/status | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/messaging/telegram/onboarding/{pairing_id}` | hermes | creds | GET /api/messaging/telegram/onboarding/{pairing_id} | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/messaging/whatsapp/onboarding/{pairing_id}` | hermes | creds | GET /api/messaging/whatsapp/onboarding/{pairing_id} | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/ops/backup/download` | hermes | creds | GET /api/ops/backup/download | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/profiles/{name}/desktop-overlay` | hermes | owner | GET /api/profiles/{name}/desktop-overlay | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/providers/oauth/{provider_id}/poll/{session_id}` | hermes | creds | GET /api/providers/oauth/{provider_id}/poll/{session_id} | tests/test_route_parity_p0_wave13.py::test_oauth_poll_is_manual_handoff_refusal |
| `api:GET /api/qubit/update/receipt` | hermes | owner | GET /api/qubit/update/receipt | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/ssh/ownership` | hermes | owner | GET /api/ssh/ownership | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:HEAD /api/files/stream` | hermes | owner | HEAD /api/files/stream | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/audio/tts-lease` | hermes | hardware | POST /api/audio/tts-lease | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/audio/voice-live/session` | hermes | hardware | POST /api/audio/voice-live/session | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/auth/ws-ticket` | hermes | owner | POST /api/auth/ws-ticket | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/chat/image-upload` | hermes | owner | POST /api/chat/image-upload | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/credentials/pool` | hermes | creds | POST /api/credentials/pool | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/cron/fire` | hermes | owner | POST /api/cron/fire | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/dashboard/agent-plugins/activate` | hermes | owner | POST /api/dashboard/agent-plugins/activate | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/dashboard/agent-plugins/{name:path}/update` | hermes | owner | POST /api/dashboard/agent-plugins/{name:path}/update | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/env/reveal` | hermes | creds | POST /api/env/reveal | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/gateway/migrate` | hermes | owner | POST /api/gateway/migrate | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/git/review/create-pr` | hermes | owner | POST /api/git/review/create-pr | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/git/review/pr-list` | hermes | owner | POST /api/git/review/pr-list | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/git/review/push` | hermes | owner | POST /api/git/review/push | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/health/retirement` | hermes | owner | POST /api/health/retirement | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/local-models/activate` | hermes | hardware | POST /api/local-models/activate | tests/test_local_models8u.py::test_every_write_is_a_typed_gate_and_writes_nothing |
| `api:POST /api/local-models/download` | hermes | hardware | POST /api/local-models/download | tests/test_local_models8u.py::test_confirmation_gate_precedes_broker |
| `api:POST /api/local-models/download-browsed` | hermes | hardware | POST /api/local-models/download-browsed | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/local-models/eject` | hermes | hardware | POST /api/local-models/eject | tests/test_local_models8u.py::test_every_write_is_a_typed_gate_and_writes_nothing |
| `api:POST /api/local-models/quickstart` | hermes | hardware | POST /api/local-models/quickstart | tests/test_local_models8u.py::test_confirmation_gate_precedes_broker |
| `api:POST /api/local-models/runtime/install` | hermes | hardware | POST /api/local-models/runtime/install | tests/test_local_models8u.py::test_confirmation_gate_precedes_broker |
| `api:POST /api/local-models/server` | hermes | hardware | POST /api/local-models/server | tests/test_local_models8u.py::test_estop_latches_writes |
| `api:POST /api/local-models/sideload` | hermes | hardware | POST /api/local-models/sideload | tests/test_local_models8u.py::test_every_write_is_a_typed_gate_and_writes_nothing |
| `api:POST /api/mcp/catalog/install` | hermes | owner | POST /api/mcp/catalog/install | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/memory/providers/{provider}/oauth/start` | hermes | creds | POST /api/memory/providers/{provider}/oauth/start | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/messaging/telegram/onboarding/start` | hermes | creds | POST /api/messaging/telegram/onboarding/start | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/messaging/telegram/onboarding/{pairing_id}/apply` | hermes | creds | POST /api/messaging/telegram/onboarding/{pairing_id}/apply | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/messaging/whatsapp/onboarding/start` | hermes | creds | POST /api/messaging/whatsapp/onboarding/start | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/messaging/whatsapp/onboarding/{pairing_id}/apply` | hermes | creds | POST /api/messaging/whatsapp/onboarding/{pairing_id}/apply | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/ops/checkpoints/prune` | hermes | owner | POST /api/ops/checkpoints/prune | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/ops/import-upload` | hermes | owner | POST /api/ops/import-upload | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/profiles/import` | hermes | owner | POST /api/profiles/import | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/profiles/sessions/pull-requests` | hermes | creds | POST /api/profiles/sessions/pull-requests | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/profiles/{name}/open-terminal` | hermes | owner | POST /api/profiles/{name}/open-terminal | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/providers/custom-endpoints/validate` | hermes | creds | POST /api/providers/custom-endpoints/validate | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/providers/oauth/{provider_id}/start` | hermes | creds | POST /api/providers/oauth/{provider_id}/start | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/providers/oauth/{provider_id}/submit` | hermes | creds | POST /api/providers/oauth/{provider_id}/submit | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/providers/validate` | hermes | creds | POST /api/providers/validate | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/sessions/owner-backfill` | hermes | owner | POST /api/sessions/owner-backfill | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:PUT /api/config/raw` | hermes | creds | PUT /api/config/raw | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:PUT /api/model/moa` | hermes | owner | PUT /api/model/moa | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:WEBSOCKET /api/audio/speak-stream` | hermes | hardware | WEBSOCKET /api/audio/speak-stream | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:WEBSOCKET /api/console` | hermes | owner | WEBSOCKET /api/console | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:WEBSOCKET /api/events` | hermes | owner | WEBSOCKET /api/events | tests/test_dashboard_matrix9u.py::test_read_control_is_backend_verified |
| `api:WEBSOCKET /api/pty` | hermes | owner | WEBSOCKET /api/pty | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:WEBSOCKET /api/pub` | hermes | owner | WEBSOCKET /api/pub | tests/test_dashboard_matrix9u.py::test_safe_mutation_is_backend_verified |
| `api:WEBSOCKET /api/ws` | hermes | owner | WEBSOCKET /api/ws | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `browser_close` | qubit | owner | Browser close-profile | tests/test_browser8tf.py::test_close_profile_refuses_real_user_profiles |
| `browser_close_refusal` | qubit | owner | Close browser profile | tests/test_browser8tf.py::test_close_profile_refuses_real_user_profiles |
| `cli:computer-use install` | hermes | creds | hermes computer-use install | tests/test_computer_use8tf.py::test_cli_wiring |
| `cli:computer-use permissions grant` | hermes | creds | hermes computer-use permissions grant | tests/test_computer_use8tf.py::test_permissions_record |
| `cli:config set` | hermes | creds | hermes config set | tests/test_cli_closure_wave5.py::test_wave5_confirm_routes_require_confirm_then_forward |
| `cli:config unset` | hermes | creds | hermes config unset | tests/test_cli_closure_wave5.py::test_wave5_confirm_routes_require_confirm_then_forward |
| `cli:gateway install` | hermes | creds | hermes gateway install | tests/test_gateway_peers8tf.py::test_gateway_link_and_supervision |
| `cli:gateway restart` | hermes | creds | hermes gateway restart | tests/test_gateway_peers8tf.py::test_gateway_link_and_supervision |
| `cli:gateway run` | hermes | creds | hermes gateway run | tests/test_gateway_peers8tf.py::test_gateway_link_and_supervision |
| `cli:gateway start` | hermes | creds | hermes gateway start | tests/test_gateway_peers8tf.py::test_gateway_link_and_supervision |
| `cli:gateway stop` | hermes | creds | hermes gateway stop | tests/test_gateway_peers8tf.py::test_gateway_link_and_supervision |
| `cli:mcp` | hermes | owner | hermes mcp | tests/test_qubit_routes_parity8u.py::test_remaining_typed_rows_are_typed_and_confirm_cannot_bypass |
| `cli:mcp add` | hermes | owner | hermes mcp add | tests/test_qubit_routes_parity8u.py::test_remaining_typed_rows_are_typed_and_confirm_cannot_bypass |
| `cli:mcp catalog` | hermes | creds | hermes mcp catalog | tests/test_cli_agent1_forward.py::test_forwarded_mcp_catalog_runs_in_fork |
| `cli:mcp configure` | hermes | owner | hermes mcp configure | tests/test_qubit_routes_parity8u.py::test_remaining_typed_rows_are_typed_and_confirm_cannot_bypass |
| `cli:mcp remove` | hermes | owner | hermes mcp remove | tests/test_qubit_routes_parity8u.py::test_remaining_typed_rows_are_typed_and_confirm_cannot_bypass |
| `cli:mcp test` | hermes | creds | hermes mcp test | tests/test_cli_parity_wave1.py::test_inline_confirm_table_argv_evidence |
| `cli:plugins install` | hermes | creds | hermes plugins install | tests/test_qubit_routes_agent2b_wiring.py::test_skills_hub_reads_are_real_local_catalog |
| `cli:plugins update` | hermes | creds | hermes plugins update | tests/test_qubit_routes_agent2b_wiring.py::test_skills_hub_reads_are_real_local_catalog |
| `cli:sessions` | hermes | owner | hermes sessions | tests/test_qubit_routes_parity8u.py::test_remaining_typed_rows_are_typed_and_confirm_cannot_bypass |
| `cli:sessions archive` | hermes | creds | hermes sessions archive | tests/test_session_lifecycle8u.py::test_cli_sessions_subcommands_roundtrip |
| `cli:sessions delete` | hermes | creds | hermes sessions delete | tests/test_session_lifecycle8u.py::test_cli_sessions_subcommands_roundtrip |
| `cli:sessions prune` | hermes | creds | hermes sessions prune | tests/test_qubit_routes_parity8u.py::test_remaining_typed_rows_are_typed_and_confirm_cannot_bypass |
| `cli:sessions rename` | hermes | creds | hermes sessions rename | tests/test_session_lifecycle8u.py::test_cli_sessions_subcommands_roundtrip |
| `cli:skills` | hermes | creds | hermes skills | tests/test_qubit_routes_agent2b_wiring.py::test_skills_hub_reads_are_real_local_catalog |
| `cli:skills audit` | hermes | creds | hermes skills audit | tests/test_qubit_routes_agent2b_wiring.py::test_skills_hub_reads_are_real_local_catalog |
| `cli:skills install` | hermes | creds | hermes skills install | tests/test_cli_agent1_forward.py::test_translate_ignores_unmapped_and_short_calls |
| `cli:skills publish` | hermes | creds | hermes skills publish | tests/test_qubit_routes_agent2b_wiring.py::test_skills_hub_reads_are_real_local_catalog |
| `cli:skills tap` | hermes | creds | hermes skills tap | tests/test_cli_closure_wave5.py::test_wave5_confirm_routes_require_confirm_then_forward |
| `cli:skills update` | hermes | creds | hermes skills update | tests/test_qubit_routes_agent2b_wiring.py::test_skills_hub_reads_are_real_local_catalog |
| `hermes_gate_audio_lease_desktop` | qubit | owner | Gated upstream route: AUDIO_LEASE_DESKTOP | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_audio_stream_local` | qubit | owner | Gated upstream route: AUDIO_STREAM_LOCAL | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_backup_passphrase` | qubit | owner | Gated upstream route: BACKUP_PASSPHRASE | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_blocked_remote_source` | qubit | owner | Gated upstream route: BLOCKED_REMOTE_SOURCE | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_chat_image_overlay` | qubit | owner | Gated upstream route: CHAT_IMAGE_OVERLAY | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_checkpoint_confirm_required` | qubit | owner | Gated upstream route: CHECKPOINT_CONFIRM_REQUIRED | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_cron_fire_external` | qubit | owner | Gated upstream route: CRON_FIRE_EXTERNAL | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_desktop_overlay_only` | qubit | owner | Gated upstream route: DESKTOP_OVERLAY_ONLY | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_files_binary_stream` | qubit | owner | Gated upstream route: FILES_BINARY_STREAM | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_gateway_migrate_operator_owned` | qubit | owner | Gated upstream route: GATEWAY_MIGRATE_OPERATOR_OWNED | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_gh_auth_operator` | qubit | owner | Gated upstream route: GH_AUTH_OPERATOR | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_gh_pr_external` | qubit | owner | Gated upstream route: GH_PR_EXTERNAL | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_git_ship_gh_external` | qubit | owner | Gated upstream route: GIT_SHIP_GH_EXTERNAL | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_git_write_brokered` | qubit | owner | Gated upstream route: GIT_WRITE_BROKERED | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_kanban_write_brokered` | qubit | owner | Gated upstream route: KANBAN_WRITE_BROKERED | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_media_not_served` | qubit | owner | Gated upstream route: MEDIA_NOT_SERVED | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_network_probe_blocked` | qubit | owner | Gated upstream route: NETWORK_PROBE_BLOCKED | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_oauth_manual_handoff` | qubit | owner | Gated upstream route: OAUTH_MANUAL_HANDOFF | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_onboarding_guided` | qubit | owner | Gated upstream route: ONBOARDING_GUIDED | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_plugin_providers_qubit` | qubit | owner | Gated upstream route: PLUGIN_PROVIDERS_QUBIT | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_profile_import_review` | qubit | owner | Gated upstream route: PROFILE_IMPORT_REVIEW | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_provider_external` | qubit | owner | Gated upstream route: PROVIDER_EXTERNAL | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_pty_no_shell` | qubit | owner | Gated upstream route: PTY_NO_SHELL | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_retirement_operator_owned` | qubit | owner | Gated upstream route: RETIREMENT_OPERATOR_OWNED | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_secret_never_served` | qubit | owner | Gated upstream route: SECRET_NEVER_SERVED | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_sessions_receipt_store` | qubit | owner | Gated upstream route: SESSIONS_RECEIPT_STORE | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_ssh_ownership_operator` | qubit | owner | Gated upstream route: SSH_OWNERSHIP_OPERATOR | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_update_operator_owned` | qubit | owner | Gated upstream route: UPDATE_OPERATOR_OWNED | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_voice_live_vendor_direct` | qubit | owner | Gated upstream route: VOICE_LIVE_VENDOR_DIRECT | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_ws_single_path` | qubit | owner | Gated upstream route: WS_SINGLE_PATH | tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed |
| `inv:openclaw-ext:cua-computer` | qubit | hardware | cua-computer | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:image-generation-core` | qubit | creds | image-generation-core | tests/test_openclaw_closure_wave2_webmedia.py |
| `inv:openclaw-ext:imap` | qubit | creds | imap | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:visitor-access` | qubit | creds | visitor-access | tests/test_openclaw_parity_wave1.py |
| `plugin:kanban-write-gate` | hermes | owner | hermes dashboard plugin kanban write surface (typed gate) | tests/test_qubit_plugins.py::test_kanban_writes_stay_on_policy_gate |

### OPERATOR_ONLY — Operator-only qualification items (code complete and tested; require live prerequisite) (10)

| id | source | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-13` | openclaw | hardware | Real desktop control (Peekaboo/CUA) | tests/test_computer_use8tf.py |
| `OC-14` | openclaw | hardware | Supervised ghost action framework (screenshot/key/cursor/window) | tests/test_desktop_parity8u.py |
| `OC-19` | openclaw | creds | Broad channel set (~29 plugins) | tests/test_openclaw_closure_wave2_messaging.py |
| `OC-51` | openclaw | hardware | Logbook screen work-journal | tests/test_openclaw_closure_wave2_webmedia.py |
| `OC-56` | openclaw | hardware | Paired node host (screen/audio/camera/media playback/presence) | tests/test_openclaw_closure_wave2_runtime.py |
| `OC-D15` | openclaw | creds | iMessage private API, media, rich messages | tests/test_openclaw_closure_wave3.py |
| `OC-D16` | openclaw | creds | Feishu dynamic agents, advanced configuration | tests/test_openclaw_closure_wave3.py |
| `computer_doctor` | qubit | hardware | Desktop health | tests/test_computer_use8tf.py::test_doctor_checklist_typed |
| `computer_install_refusal` | qubit | hardware | Desktop driver install | tests/test_computer_use8tf.py::test_install_refused |
| `computer_permissions` | qubit | hardware | Desktop permissions | tests/test_computer_use8tf.py::test_permissions_record |
