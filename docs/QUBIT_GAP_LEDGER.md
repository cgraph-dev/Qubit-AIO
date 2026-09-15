# Qubit gap ledger

Machine-generated from `docs/QUBIT_GAP_LEDGER.*.json`. This merged report is the authoritative view; the input JSON sections are never modified.

> **COMPLETION GATE:** CODE-READY while every row is IMPLEMENTED, SAFE_EQUIVALENT, or OPERATOR_ONLY; OPERATOR_ONLY rows remain manual/operator qualification items and are not counted as 100% until completed and verified.

## Provenance pins

- `docs/QUBIT_GAP_LEDGER.hermes.json` — schema `qubit-gap-ledger-section/1`, source `hermes`, 573 rows
  - counts: {"by_area": {"analytics-config": 4, "audio-voice": 6, "chat-websocket": 5, "cli": 72, "cli-subcommand": 200, "config-env": 15, "cron": 13, "dashboard-auth": 11, "dashboard-page": 20, "dashboard-ui": 15, "dashboard_plugin": 5, "files-fs": 18, "gateway-actions": 6, "git": 21, "local-models": 16, "mcp": 12, "memory": 5, "messaging": 11, "models": 7, "ops": 28, "profiles": 20, "provider-oauth": 6, "se
  - generated: 2026-09-12
  - method: Evidence-based regeneration. API rows re-extract the pinned upstream route decorators and classify them from voicebridge.hermes_compat.match disposition plus the handler's real behaviour (a handler whose normal exits are typed gates/refusals is GATED; a real read/write is IMPLEMENTED; a wire-shape stub is PARTIAL). IMPLEMENTED additionally requires an on-disk tests/ function that dispatches the ro
  - plugin_fleet: [{"api": "plugins/hermes-achievements/dashboard/plugin_api.py", "has_dashboard": true, "manifest": "plugins/hermes-achievements/dashboard/manifest.json", "name": "hermes-achievements", "source": "vendored"}, {"api": "plugins/kanban/dashboard/plugin_api.py", "has_dashboard": true, "manifest": "plugins/kanban/dashboard/manifest.json", "name": "kanban", "source": "vendored"}, {"has_dashboard": false,
  - sources_reconciled: ["voicebridge/hermes_compat.py", "voicebridge/hermes_plugins.py", "voicebridge/cli.py", "voicebridge/cli_hermes_forward.py", "tests/ (AST caller evidence)", "tests/test_hermes_plugins.py (plugin-fleet proof)", "qubit_hermes/hermes_cli/web_routers", "qubit_hermes/hermes_cli/web_server*.py", "qubit_hermes/hermes_cli/memory_oauth.py", "qubit_hermes/hermes_cli/dashboard_auth/routes.py", "qubit_hermes/
  - upstream_pin: 2237be355906fbe6065ce1815711eee52b2d646e
- `docs/QUBIT_GAP_LEDGER.openclaw.json` — schema `qubit-gap-ledger-section/1`, source `openclaw`, 104 rows
  - counts: {"by_area": {"automation": 10, "boards": 6, "browser": 5, "dashboard": 4, "desktop": 5, "gateway": 10, "ide": 7, "messaging": 11, "policy": 2, "reports": 6, "runtime": 9, "sessions": 10, "skills": 7, "web_media": 9, "webhooks": 3}, "by_status": {"IMPLEMENTED": 94, "OPERATOR_ONLY": 8, "SAFE_EQUIVALENT": 2}, "total": 104}
  - extension_map: {"by_disposition": {"distinct": 3, "gate": 123, "parent": 28}, "by_needs": {"creds": 115, "hardware": 8, "none": 27, "owner": 4}, "covered": true, "total": 154, "unexplained": []}
  - extension_wave5: {"by_disposition": {"distinct": 2, "gate": 24, "parent": 4}, "by_needs": {"creds": 23, "hardware": 1, "none": 6, "owner": 0}, "covered": true, "extensions": [{"disposition": "gate", "form": "voicebridge/channels_inbound.py normalize_event", "gate": "CHANNEL_NO_CREDENTIAL", "name": "a2a", "needs": "creds", "parent": "OC-19", "proving": "tests/test_openclaw_extensions_wave5.py"}, {"disposition": "ga
  - generated: 2026-09-12
  - method: Evidence-based regeneration. The audited capability inventory (72 rows @v2026.9.2 + 32 delta rows @v2026.9.4) is embedded as a baseline; a documented ROW_EVIDENCE table records the rows closed or upgraded by real Qubit modules + behavioural tests. Every IMPLEMENTED row is mechanically re-verified against the code and an on-disk proving test that references the cited backend; a row upgraded from an
  - sources_reconciled: ["docs/AUDIT_OPENCLAW_2026-09-11.md", "docs/AUDIT_OPENCLAW_V2026_9_4_DELTA.md", "voicebridge/automation.py", "voicebridge/schedules.py", "tests/test_automation_agent6_*.py", "tests/test_openclaw_e5_directives.py", "tests/test_oc_*_agent[5-9]*.py (Agent 5-10 witness tests)", "tests/ (proving-test evidence)"]
  - upstream_pin: 3a9d69db306cd7f081e06254cb89c4bcc14a7107
  - upstream_tag: v2026.9.4
  - upstream_tree: /tmp/opencode/pinned/openclaw (read via git show 3a9d69db306cd7f081e06254cb89c4bcc14a7107, shallow clone)
- `docs/QUBIT_GAP_LEDGER.qubit.json` — schema `qubit-gap-ledger-section/1`, source `qubit-surface`, 707 rows
  - generated_from: cli_families=106, dashboard_ops.EXTRA_OPS=34, dashboard_ops.catalog=523, hermes_compat.GATED=105, hermes_compat.ROUTES=385, source_inventory.dispositioned=121, source_inventory.dispositioned_gated=4, source_inventory.dispositioned_implemented=117, source_inventory.distinct_uncovered=0, source_inventory.items=6775, source_inventory.parented_covered=1081, source_inventory.parented_uncovered=0, source_inventory.surface_items=1818, voice_commands.CLI_COVERAGE=106, voice_commands.REGISTRY=480
  - evidence: production backend + declared interfaces + live CLI tree + voice registry + linked tests/ AST proofs + direct source inventory
  - generator: scripts/rebuild_ledger_qubit.py
  - backend_flags: []
  - generated: 2026-09-12
  - inventory: {"by_category": {}, "by_parent": {}, "covered": 1697, "covered_by_parent": {"OC-02": 5, "OC-16": 1, "OC-19": 24, "OC-30": 1, "OC-34": 1, "OC-35": 1, "OC-46": 5, "OC-53": 10, "OC-63": 10, "OC-64": 4, "OC-66": 10, "OC-67": 1, "OC-D01": 1, "OC-D03": 1, "OC-D08": 5, "OC-D12": 1, "OC-D15": 1, "OC-D16": 1, "OC-D20": 1, "OC-D28": 58, "browser_status": 24, "channels_status": 8, "cli:acp": 6, "cli:approval
  - method: Evidence-based regeneration. Catalog rows derive their status from the live production backend (voice_commands.REGISTRY / _BACKENDS + dashboard_ops.EXTRA_OPS), the declared interfaces (catalog), the live CLI parser tree, the registry voice surface, and an AST scan of tests/ for a proving caller. A plugin/page row declares only the dashboard surface (no CLI/voice by design); it is IMPLEMENTED when
  - sources_reconciled: ["voicebridge/voice_commands.py (REGISTRY/_BACKENDS/CLI_COVERAGE)", "voicebridge/dashboard_ops.py (catalog/EXTRA_OPS)", "voicebridge/cli.py (build_parser)", "voicebridge/hermes_compat.py (ROUTES/GATED)", "voicebridge/hermes_plugins.py (vendored plugin fleet wrapper)", "qubit_hermes/toolsets.py + tools/registry.py (fork tool runtime)", "qubit_hermes/skills + optional-skills (fork skill runtime)", "

## Counts

- Total rows: **1384**
- Code-side open rows (not terminal): **0**
- SAFE_EQUIVALENT (closed for code readiness): **158**
- OPERATOR_ONLY (manual/operator qualification): **11**
- Code-ready: **True**; fully complete (all manual qualified): **False**

### By status

- IMPLEMENTED: 1215
- OPERATOR_ONLY: 11
- SAFE_EQUIVALENT: 158

### By source

- hermes: 573
- openclaw: 104
- qubit: 707

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
- dashboard_plugin: 16
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
- hermes_gated: 30
- hermes_plugin: 9
- hermes_skill: 28
- hermes_tool: 73
- hermes_web_page: 19
- hooks: 10
- ide: 9
- import_agent: 2
- insights: 1
- kanban: 48
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
- openclaw_extension: 11
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
- profile: 9
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
- Unexplained omissions: **0**
- `qubit`: present=True, items=6775, matched=1818, unmatched=0, unexplained=0

## Code-side open rows (not IMPLEMENTED/SAFE_EQUIVALENT/OPERATOR_ONLY)

No code-side open rows: every row is terminal (IMPLEMENTED / SAFE_EQUIVALENT / OPERATOR_ONLY).

## Terminal gate rows (SAFE_EQUIVALENT / OPERATOR_ONLY)

### SAFE_EQUIVALENT — Qubit-safe equivalents (closed; unsafe raw behavior explicitly refused) (158)

| id | source | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-01` | openclaw | owner | Gateway protocol server + RPC v4 | tests/test_openclaw_parity_wave1.py |
| `OC-37` | openclaw | owner | Workboard dispatch + worker runs | tests/test_kanban8tf.py |
| `api:DELETE /api/credentials/pool/{provider}/{index}` | hermes | creds | DELETE /api/credentials/pool/{provider}/{index} | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:DELETE /api/files` | hermes | owner | DELETE /api/files | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:DELETE /api/local-models/models/{model_id}` | hermes | hardware | DELETE /api/local-models/models/{model_id} | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:DELETE /api/mcp/oauth/flows/{flow_id}` | hermes | creds | DELETE /api/mcp/oauth/flows/{flow_id} | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:DELETE /api/mcp/servers/{name}` | hermes | owner | DELETE /api/mcp/servers/{name} | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:DELETE /api/messaging/telegram/onboarding/{pairing_id}` | hermes | creds | DELETE /api/messaging/telegram/onboarding/{pairing_id} | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:DELETE /api/messaging/whatsapp/onboarding/{pairing_id}` | hermes | creds | DELETE /api/messaging/whatsapp/onboarding/{pairing_id} | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:DELETE /api/providers/oauth/sessions/{session_id}` | hermes | creds | DELETE /api/providers/oauth/sessions/{session_id} | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:DELETE /api/providers/oauth/{provider_id}` | hermes | creds | DELETE /api/providers/oauth/{provider_id} | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:DELETE /api/sessions/empty` | hermes | owner | DELETE /api/sessions/empty | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/audio/elevenlabs/voices` | hermes | creds | GET /api/audio/elevenlabs/voices | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/config/raw` | hermes | creds | GET /api/config/raw | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/credentials/pool` | hermes | creds | GET /api/credentials/pool | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/files/download` | hermes | owner | GET /api/files/download | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/files/stream` | hermes | owner | GET /api/files/stream | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/fs/download` | hermes | owner | GET /api/fs/download | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/git/gh-auth` | hermes | creds | GET /api/git/gh-auth | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/git/review/ship-info` | hermes | creds | GET /api/git/review/ship-info | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/hermes/update/check` | hermes | owner | GET /api/hermes/update/check | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/hermes/update/receipt` | hermes | owner | GET /api/hermes/update/receipt | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/local-models/search` | hermes | hardware | GET /api/local-models/search | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/local-models/search/files` | hermes | hardware | GET /api/local-models/search/files | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/mcp/oauth/callback/{server_name:path}` | hermes | creds | GET /api/mcp/oauth/callback/{server_name:path} | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/mcp/oauth/flows/{flow_id}` | hermes | creds | GET /api/mcp/oauth/flows/{flow_id} | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/media` | hermes | owner | GET /api/media | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/memory/providers/{provider}/oauth/status` | hermes | creds | GET /api/memory/providers/{provider}/oauth/status | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/messaging/telegram/onboarding/{pairing_id}` | hermes | creds | GET /api/messaging/telegram/onboarding/{pairing_id} | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/messaging/whatsapp/onboarding/{pairing_id}` | hermes | creds | GET /api/messaging/whatsapp/onboarding/{pairing_id} | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/ops/backup/download` | hermes | creds | GET /api/ops/backup/download | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/portal` | hermes | owner | GET /api/portal | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/profiles/{name}/desktop-overlay` | hermes | owner | GET /api/profiles/{name}/desktop-overlay | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/providers/oauth` | hermes | creds | GET /api/providers/oauth | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/providers/oauth/{provider_id}/poll/{session_id}` | hermes | creds | GET /api/providers/oauth/{provider_id}/poll/{session_id} | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:GET /api/ssh/ownership` | hermes | owner | GET /api/ssh/ownership | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:HEAD /api/files/stream` | hermes | owner | HEAD /api/files/stream | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:PATCH /api/profiles/{name}` | hermes | owner | PATCH /api/profiles/{name} | tests/test_hermes_api_parity_wave1.py::test_wave1_dispatch_non_refusal_success |
| `api:POST /api/audio/tts-lease` | hermes | hardware | POST /api/audio/tts-lease | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/auth/ws-ticket` | hermes | owner | POST /api/auth/ws-ticket | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/chat/image-upload` | hermes | owner | POST /api/chat/image-upload | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/credentials/pool` | hermes | creds | POST /api/credentials/pool | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/cron/fire` | hermes | owner | POST /api/cron/fire | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/curator/run` | hermes | owner | POST /api/curator/run | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/dashboard/agent-plugins/install` | hermes | owner | POST /api/dashboard/agent-plugins/install | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/dashboard/agent-plugins/{name:path}/update` | hermes | owner | POST /api/dashboard/agent-plugins/{name:path}/update | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/env/reveal` | hermes | creds | POST /api/env/reveal | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/gateway/restart` | hermes | owner | POST /api/gateway/restart | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/git/review/create-pr` | hermes | owner | POST /api/git/review/create-pr | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/git/review/pr-list` | hermes | owner | POST /api/git/review/pr-list | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/git/review/push` | hermes | owner | POST /api/git/review/push | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/hermes/update` | hermes | owner | POST /api/hermes/update | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/local-models/activate` | hermes | hardware | POST /api/local-models/activate | tests/test_local_models8u.py::test_every_write_is_a_typed_gate_and_writes_nothing |
| `api:POST /api/local-models/download` | hermes | hardware | POST /api/local-models/download | tests/test_local_models8u.py::test_confirmation_gate_precedes_broker |
| `api:POST /api/local-models/download-browsed` | hermes | hardware | POST /api/local-models/download-browsed | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/local-models/eject` | hermes | hardware | POST /api/local-models/eject | tests/test_local_models8u.py::test_every_write_is_a_typed_gate_and_writes_nothing |
| `api:POST /api/local-models/quickstart` | hermes | hardware | POST /api/local-models/quickstart | tests/test_local_models8u.py::test_confirmation_gate_precedes_broker |
| `api:POST /api/local-models/runtime/install` | hermes | hardware | POST /api/local-models/runtime/install | tests/test_local_models8u.py::test_confirmation_gate_precedes_broker |
| `api:POST /api/local-models/server` | hermes | hardware | POST /api/local-models/server | tests/test_local_models8u.py::test_estop_latches_writes |
| `api:POST /api/local-models/sideload` | hermes | hardware | POST /api/local-models/sideload | tests/test_local_models8u.py::test_every_write_is_a_typed_gate_and_writes_nothing |
| `api:POST /api/mcp/catalog/install` | hermes | owner | POST /api/mcp/catalog/install | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/mcp/servers` | hermes | owner | POST /api/mcp/servers | tests/test_hermes_api_parity_wave1.py::test_mcp_inventory_reads_are_real_and_never_connect |
| `api:POST /api/mcp/servers/{name}/auth` | hermes | creds | POST /api/mcp/servers/{name}/auth | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/memory/providers/{provider}/oauth/start` | hermes | creds | POST /api/memory/providers/{provider}/oauth/start | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/messaging/telegram/onboarding/start` | hermes | creds | POST /api/messaging/telegram/onboarding/start | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/messaging/telegram/onboarding/{pairing_id}/apply` | hermes | creds | POST /api/messaging/telegram/onboarding/{pairing_id}/apply | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/messaging/whatsapp/onboarding/start` | hermes | creds | POST /api/messaging/whatsapp/onboarding/start | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/messaging/whatsapp/onboarding/{pairing_id}/apply` | hermes | creds | POST /api/messaging/whatsapp/onboarding/{pairing_id}/apply | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/ops/checkpoints/prune` | hermes | owner | POST /api/ops/checkpoints/prune | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/ops/import-upload` | hermes | owner | POST /api/ops/import-upload | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/profiles/import` | hermes | owner | POST /api/profiles/import | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/profiles/sessions/pull-requests` | hermes | creds | POST /api/profiles/sessions/pull-requests | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/profiles/{name}/describe-auto` | hermes | owner | POST /api/profiles/{name}/describe-auto | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/profiles/{name}/open-terminal` | hermes | owner | POST /api/profiles/{name}/open-terminal | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/providers/custom-endpoints/validate` | hermes | creds | POST /api/providers/custom-endpoints/validate | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/providers/oauth/{provider_id}/start` | hermes | creds | POST /api/providers/oauth/{provider_id}/start | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/providers/oauth/{provider_id}/submit` | hermes | creds | POST /api/providers/oauth/{provider_id}/submit | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/providers/validate` | hermes | creds | POST /api/providers/validate | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/sessions/bulk-delete` | hermes | owner | POST /api/sessions/bulk-delete | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/sessions/import` | hermes | owner | POST /api/sessions/import | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/sessions/owner-backfill` | hermes | owner | POST /api/sessions/owner-backfill | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:POST /api/sessions/prune` | hermes | owner | POST /api/sessions/prune | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:PUT /api/config/raw` | hermes | creds | PUT /api/config/raw | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:PUT /api/curator/paused` | hermes | owner | PUT /api/curator/paused | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:PUT /api/mcp/servers` | hermes | owner | PUT /api/mcp/servers | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:PUT /api/mcp/servers/{name}/enabled` | hermes | owner | PUT /api/mcp/servers/{name}/enabled | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:PUT /api/model/moa` | hermes | owner | PUT /api/model/moa | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:WEBSOCKET /api/audio/speak-stream` | hermes | hardware | WEBSOCKET /api/audio/speak-stream | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:WEBSOCKET /api/console` | hermes | owner | WEBSOCKET /api/console | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:WEBSOCKET /api/events` | hermes | owner | WEBSOCKET /api/events | tests/test_hermes_vendor8tf.py::test_compat_read_endpoints_smoke |
| `api:WEBSOCKET /api/pty` | hermes | owner | WEBSOCKET /api/pty | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `api:WEBSOCKET /api/pub` | hermes | owner | WEBSOCKET /api/pub | tests/test_hermes_api_parity_wave1.py::test_event_publish_appends_to_single_journal |
| `api:WEBSOCKET /api/ws` | hermes | owner | WEBSOCKET /api/ws | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
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
| `cli:mcp` | hermes | owner | hermes mcp | tests/test_hermes_routes_parity8u.py::test_remaining_typed_rows_are_typed_and_confirm_cannot_bypass |
| `cli:mcp add` | hermes | owner | hermes mcp add | tests/test_hermes_routes_parity8u.py::test_remaining_typed_rows_are_typed_and_confirm_cannot_bypass |
| `cli:mcp catalog` | hermes | creds | hermes mcp catalog | tests/test_cli_agent1_forward.py::test_forwarded_mcp_catalog_runs_in_fork |
| `cli:mcp configure` | hermes | owner | hermes mcp configure | tests/test_hermes_routes_parity8u.py::test_remaining_typed_rows_are_typed_and_confirm_cannot_bypass |
| `cli:mcp remove` | hermes | owner | hermes mcp remove | tests/test_hermes_routes_parity8u.py::test_remaining_typed_rows_are_typed_and_confirm_cannot_bypass |
| `cli:mcp test` | hermes | creds | hermes mcp test | tests/test_cli_parity_wave1.py::test_inline_confirm_table_argv_evidence |
| `cli:plugins install` | hermes | creds | hermes plugins install | tests/test_hermes_routes_agent2b_wiring.py::test_skills_hub_reads_are_real_local_catalog |
| `cli:plugins update` | hermes | creds | hermes plugins update | tests/test_hermes_routes_agent2b_wiring.py::test_skills_hub_reads_are_real_local_catalog |
| `cli:sessions` | hermes | owner | hermes sessions | tests/test_hermes_routes_parity8u.py::test_remaining_typed_rows_are_typed_and_confirm_cannot_bypass |
| `cli:sessions archive` | hermes | creds | hermes sessions archive | tests/test_session_lifecycle8u.py::test_cli_sessions_subcommands_roundtrip |
| `cli:sessions delete` | hermes | creds | hermes sessions delete | tests/test_session_lifecycle8u.py::test_cli_sessions_subcommands_roundtrip |
| `cli:sessions prune` | hermes | creds | hermes sessions prune | tests/test_hermes_routes_parity8u.py::test_remaining_typed_rows_are_typed_and_confirm_cannot_bypass |
| `cli:sessions rename` | hermes | creds | hermes sessions rename | tests/test_session_lifecycle8u.py::test_cli_sessions_subcommands_roundtrip |
| `cli:skills` | hermes | creds | hermes skills | tests/test_hermes_routes_agent2b_wiring.py::test_skills_hub_reads_are_real_local_catalog |
| `cli:skills audit` | hermes | creds | hermes skills audit | tests/test_hermes_routes_agent2b_wiring.py::test_skills_hub_reads_are_real_local_catalog |
| `cli:skills install` | hermes | creds | hermes skills install | tests/test_cli_agent1_forward.py::test_translate_ignores_unmapped_and_short_calls |
| `cli:skills publish` | hermes | creds | hermes skills publish | tests/test_hermes_routes_agent2b_wiring.py::test_skills_hub_reads_are_real_local_catalog |
| `cli:skills tap` | hermes | creds | hermes skills tap | tests/test_cli_closure_wave5.py::test_wave5_confirm_routes_require_confirm_then_forward |
| `cli:skills update` | hermes | creds | hermes skills update | tests/test_hermes_routes_agent2b_wiring.py::test_skills_hub_reads_are_real_local_catalog |
| `hermes_gate_audio_lease_desktop` | qubit | owner | Gated upstream route: AUDIO_LEASE_DESKTOP | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_audio_stream_local` | qubit | owner | Gated upstream route: AUDIO_STREAM_LOCAL | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_backup_passphrase` | qubit | owner | Gated upstream route: BACKUP_PASSPHRASE | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_blocked_remote_source` | qubit | owner | Gated upstream route: BLOCKED_REMOTE_SOURCE | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_chat_image_overlay` | qubit | owner | Gated upstream route: CHAT_IMAGE_OVERLAY | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_checkpoint_confirm_required` | qubit | owner | Gated upstream route: CHECKPOINT_CONFIRM_REQUIRED | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_cron_fire_external` | qubit | owner | Gated upstream route: CRON_FIRE_EXTERNAL | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_curator_on_demand` | qubit | owner | Gated upstream route: CURATOR_ON_DEMAND | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_desktop_overlay_only` | qubit | owner | Gated upstream route: DESKTOP_OVERLAY_ONLY | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_files_binary_stream` | qubit | owner | Gated upstream route: FILES_BINARY_STREAM | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_gateway_no_supervision` | qubit | owner | Gated upstream route: GATEWAY_NO_SUPERVISION | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_gh_auth_operator` | qubit | owner | Gated upstream route: GH_AUTH_OPERATOR | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_gh_pr_external` | qubit | owner | Gated upstream route: GH_PR_EXTERNAL | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_git_ship_gh_external` | qubit | owner | Gated upstream route: GIT_SHIP_GH_EXTERNAL | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_git_write_brokered` | qubit | owner | Gated upstream route: GIT_WRITE_BROKERED | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_kanban_write_brokered` | qubit | owner | Gated upstream route: KANBAN_WRITE_BROKERED | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_mcp_no_mount` | qubit | owner | Gated upstream route: MCP_NO_MOUNT | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_media_not_served` | qubit | owner | Gated upstream route: MEDIA_NOT_SERVED | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_network_probe_blocked` | qubit | owner | Gated upstream route: NETWORK_PROBE_BLOCKED | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_oauth_manual_handoff` | qubit | owner | Gated upstream route: OAUTH_MANUAL_HANDOFF | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_onboarding_guided` | qubit | owner | Gated upstream route: ONBOARDING_GUIDED | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_plugin_providers_qubit` | qubit | owner | Gated upstream route: PLUGIN_PROVIDERS_QUBIT | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_profile_import_review` | qubit | owner | Gated upstream route: PROFILE_IMPORT_REVIEW | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_provider_external` | qubit | owner | Gated upstream route: PROVIDER_EXTERNAL | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_pty_no_shell` | qubit | owner | Gated upstream route: PTY_NO_SHELL | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_secret_never_served` | qubit | owner | Gated upstream route: SECRET_NEVER_SERVED | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_sessions_receipt_store` | qubit | owner | Gated upstream route: SESSIONS_RECEIPT_STORE | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_ssh_ownership_operator` | qubit | owner | Gated upstream route: SSH_OWNERSHIP_OPERATOR | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_update_operator_owned` | qubit | owner | Gated upstream route: UPDATE_OPERATOR_OWNED | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `hermes_gate_ws_single_path` | qubit | owner | Gated upstream route: WS_SINGLE_PATH | tests/test_hermes_routes_parity8u.py::test_every_gated_route_is_typed |
| `inv:openclaw-ext:cua-computer` | qubit | hardware | cua-computer | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:image-generation-core` | qubit | creds | image-generation-core | tests/test_openclaw_closure_wave2_webmedia.py |
| `inv:openclaw-ext:imap` | qubit | creds | imap | tests/test_openclaw_extensions_wave5.py |
| `inv:openclaw-ext:visitor-access` | qubit | creds | visitor-access | tests/test_openclaw_parity_wave1.py |
| `plugin:kanban-write-gate` | hermes | owner | hermes dashboard plugin kanban write surface (typed gate) | tests/test_hermes_plugins.py::test_kanban_writes_stay_on_policy_gate |

### OPERATOR_ONLY — Operator-only qualification items (code complete and tested; require live prerequisite) (11)

| id | source | needs | capability | proving test |
| --- | --- | --- | --- | --- |
| `OC-13` | openclaw | hardware | Real desktop control (Peekaboo/CUA) | tests/test_computer_use8tf.py |
| `OC-14` | openclaw | hardware | Supervised ghost action framework (screenshot/key/cursor/window) | tests/test_desktop_parity8u.py |
| `OC-19` | openclaw | creds | Broad channel set (~29 plugins) | tests/test_openclaw_closure_wave2_messaging.py |
| `OC-48` | openclaw | creds | Markdown workspace memory / dreaming / external providers (LanceDB/Wiki/Honcho) | tests/test_openclaw_closure_wave2_webmedia.py |
| `OC-51` | openclaw | hardware | Logbook screen work-journal | tests/test_openclaw_closure_wave2_webmedia.py |
| `OC-56` | openclaw | hardware | Paired node host (screen/audio/camera/media playback/presence) | tests/test_openclaw_closure_wave2_runtime.py |
| `OC-D15` | openclaw | creds | iMessage private API, media, rich messages | tests/test_openclaw_closure_wave3.py |
| `OC-D16` | openclaw | creds | Feishu dynamic agents, advanced configuration | tests/test_openclaw_closure_wave3.py |
| `computer_doctor` | qubit | hardware | Desktop health | tests/test_computer_use8tf.py::test_doctor_checklist_typed |
| `computer_install_refusal` | qubit | hardware | Desktop driver install | tests/test_computer_use8tf.py::test_install_refused |
| `computer_permissions` | qubit | hardware | Desktop permissions | tests/test_computer_use8tf.py::test_permissions_record |
