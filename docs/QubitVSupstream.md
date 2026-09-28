# Qubit vs. upstream Hermes — living audit

> **Generated** by `scripts/forensic_audit/run_audit.py`. Do not hand-edit; edit the ledger or the triage overlay and re-run. The deep one-shot forensic pass is preserved at `docs/generated/audit/FORENSIC_BASELINE_2026-09-25.md`.

- Ledger run: **9**
- Upstream reference: `https://github.com/NousResearch/hermes-agent`
- Qubit remote: `https://github.com/cgraph-dev/openclaw-voice-bridge.git`
- **Verdict: `READY_FOR_REPAIR`** (229 open, 15 fixed/tracked-resolved, 0 triaged)

## How to re-run

```bash
# deterministic checks; fail on NEW/REGRESSED HIGH|CRITICAL
.venv/bin/python scripts/forensic_audit/run_audit.py --check

# update the ledger + regenerate this report
.venv/bin/python scripts/forensic_audit/run_audit.py

# fast subset (skips slow generator/import checks)
.venv/bin/python scripts/forensic_audit/run_audit.py --check --fast
```

## Coverage map (Tier 0 checks)

| Check | Slow | What it verifies |
|---|---|---|
| `home_fallback` | no | reference-home (~/.hermes) default/fallback candidates |
| `credential_home_message` | no | user-facing credential guidance naming ~/.hermes |
| `version_skew` | no | product/runtime/pin/desktop version skew |
| `cli_exit_code_contract` | no | oneshot interrupted/failed exit-code contract |
| `mcp_redaction_contract` | no | qubit mcp test credential redaction |
| `model_capability_contract` | no | /api/model/info derives real capabilities |
| `default_tool_bundle` | yes | default CLI bundle contains registered tools |
| `installer_checksums` | no | installer download integrity verification |
| `dep_pinning` | no | Qubit-owned dependency pinning |
| `generated_staleness` | yes | generated artifacts match current source |

## Summary

- By severity: `{'CRITICAL': 1, 'HIGH': 42, 'INFO': 8, 'LOW': 61, 'MEDIUM': 132}`
- By status: `{'FIXED': 15, 'OPEN': 229}`

## Open findings (severity-ranked)

| Sev | Status | Check | Finding | Location |
|---|---|---|---|---|
| CRITICAL | OPEN | `tier1_b2` | 9 imports of qubit_state_health resolve to a nonexistent module -> ImportError on any state/gateway/qubit_cli  | `engine output keeps file hermes_state_health.py but rewrites every reference to qubit_state_health via the embedded `hermes_state` prefix rule` |
| HIGH | OPEN | `tier1_a1` | differs | `qubit_runtime/qubit_cli/oneshot.py (no _oneshot_exit_code, no interrupted handling)` |
| HIGH | OPEN | `tier1_a2` | DEFAULT_CONTEXT_LENGTHS missing 23 upstream keys; live model deepseek-v4.1-flash resolves to 128000 instead of | `qubit_runtime/agent/model_metadata.py:324-403 (100 keys; deepseek-v4.1-flash ABSENT)` |
| HIGH | OPEN | `tier1_a2` | Default CLI bundle (hermes-cli) is missing 6 registered tools that upstream includes | `qubit_runtime/toolsets.py:11-36 _QUBIT_CORE_TOOLS (no browser_vault_* line after browser_dialog; no manage_connections)` |
| HIGH | OPEN | `tier1_a2` | get_model_context_length no longer consults provider-profile declared windows | `qubit_runtime/agent/model_metadata.py:1849-1907 (get_model_context_length; no profile lookup after model=_strip_provider_prefix at :1882)` |
| HIGH | OPEN | `tier1_a3` | A3-01 | `qubit_cli/mcp_config.py:604-605 (mask = resolved[:4]+'***'+resolved[-4:]); :608 ("Connection failed ...: {exc}" printed raw); :583-621 (cmd_mcp_test)` |
| HIGH | OPEN | `tier1_a4` | Reasoning selector in /chat is permanently disabled by A4-01 | `frontend/qubit-web/src/pages/ChatPage.tsx:493` |
| HIGH | OPEN | `tier1_a4` | Qubit /api/model/info fabricates model capabilities (reasoning + max_output hardcoded) | `voicebridge/qubit_compat.py:1273-1275 (h_model_info)` |
| HIGH | OPEN | `tier1_a4` | Vendored fork default home is still ~/.hermes and is materialized at import time | `qubit_runtime/qubit_constants.py:45-51` |
| HIGH | OPEN | `tier1_a4` | Fresh Hermes install from source is non-functional (missing ruamel) | `N/A` |
| HIGH | OPEN | `tier1_a5` | Legacy-source URL guard omits 'openclaw' (the repo's own legacy brand) | `voicebridge/update_channel.py:72 LEGACY_URL_TOKENS=('hermes','nousresearch','nous'); :301 _legacy_token` |
| HIGH | OPEN | `tier1_a5` | Update default repository identity does not match the configured origin (and README clone URL) | `voicebridge/update_channel.py:58 QUBIT_REPOSITORY='cgraph-dev/qubit-voicebridge'; README.md:98 'git clone https://github.com/cgraph-dev/qubit-voicebridge.git'` |
| HIGH | OPEN | `tier1_b1` | node fallback home | `tools/mcp_tool_config.py:134` |
| HIGH | OPEN | `tier1_b1` | plugin manifest version field | `qubit_cli/plugin_validate.py:109, qubit_cli/plugins_manifest.py:36` |
| HIGH | OPEN | `tier1_b1` | holder process executable identity | `qubit_state_holders.py:35 (_QUBIT_EXECUTABLES)` |
| HIGH | OPEN | `tier1_b1` | mcp_serve fallback home | `mcp_serve.py:45` |
| HIGH | OPEN | `tier1_b1` | pip entry-point groups | `providers/__init__.py:258, qubit_cli/plugins_discovery.py:29, plugins/memory/__init__.py:30` |
| HIGH | OPEN | `tier1_b1` | project-plugins env flag | `utils.py:41-56; callers qubit_cli/plugins_discovery.py:185, plugins/memory/__init__.py:48` |
| HIGH | OPEN | `tier1_b1` | watchdog diagnostic home | `qubit_startup_watchdog.py:110` |
| HIGH | OPEN | `tier1_b1` | skill frontmatter metadata key | `agent/skill_utils.py:635, agent/learning_graph.py:46, tools/skills_tool.py:569, tools/skills_hub_models.py:189, tools/skills_hub_official.py:225, tools/skill_linter.py:105, tools/blueprints.py:73` |
| HIGH | OPEN | `tier1_b1` | distribution manifest version key | `qubit_cli/profile_distribution.py:125,136, qubit_cli/profile_cmd.py:500` |
| HIGH | OPEN | `tier1_b1` | gateway home= argv spelling | `qubit_cli/gateway.py:591,600` |
| HIGH | OPEN | `tier1_b1` | gateway-run argv matcher | `gateway/status.py:364` |
| HIGH | OPEN | `tier1_b1` | platform default home | `qubit_constants.py:56` |
| HIGH | OPEN | `tier1_b1` | dotenv default home | `qubit_cli/env_loader.py:319,569` |
| HIGH | OPEN | `tier1_b1` | blueprint publish writer key | `tools/blueprints.py:183` |
| HIGH | OPEN | `tier1_b1` | project plugin directory | `qubit_cli/plugins_discovery.py:179, plugins/memory/__init__.py:53, qubit_cli/web_server_dashboard.py:492, qubit_cli/plugin_dev.py:202` |
| HIGH | OPEN | `tier1_b1` | update command argv resolution | `gateway/run.py:2842` |
| HIGH | OPEN | `tier1_b2` | missing `qubit` launcher -> console/pyproject entrypoint breakage after rebase | `engine leaves qubit_runtime/hermes; repo has qubit_runtime/qubit` |
| HIGH | OPEN | `tier1_b2` | legacy brand + namespace split (qubit_* tree still imports hermes_yaml); importable, so it fails silently to l | `engine output leaves file hermes_yaml.py and 463 `import hermes_yaml` references verbatim` |
| HIGH | OPEN | `tier1_b3` | rebrand epilogue token-slice off-by-one (uses hermes length instead of qubit length) | `repo/voicebridge/qubit_runtime.py:75-84 `_rebrand_epilogue` slices stripped[len('hermes'):] (6) for a 6-char 'qubit ' incl. space, appending prog (5, no space) -> help shows 'qubitchat -q', 'qubit--tui', 'qubit-c', 'qubitsetup', 'qubitgateway install', 'qubit-s', 'qubitlogs', 'qubit<command>'` |
| HIGH | OPEN | `tier1_b3` | W9 vendor sync moved 0.21.4 content but did not bump the vendored subproject version string | `repo/qubit_runtime/pyproject.toml:5 version = '0.21.1'; `qubit agent --version` -> 'Qubit Agent v0.21.1 (2026.9.7)', Install method unknown` |
| HIGH | OPEN | `tier1_b3` | config value rebranded hermes->Qubit without matching the registry key; inert because platform_toolsets is abs | `installed runtime/config.yaml toolsets: ['Qubit-cli','kanban']; toolsets.py keys are 'hermes-cli','kanban',...; get_toolset('Qubit-cli') -> UNKNOWN` |
| HIGH | OPEN | `tier1_b3` | path tokens intentionally excluded from the capitalised-product-word rewrite; stale reference path left in use | `repo/qubit_runtime/qubit_cli/_parser.py:166,264 help still names ~/.hermes/config.yaml (lowercase path not rewritten by _qubit_text which only rewrites 'Hermes')` |
| HIGH | OPEN | `tier1_b4` | Qubit hardcodes capabilities {supports_reasoning:false, supports_vision:false, max_output_tokens:1024, model_f | `voicebridge/qubit_compat.py:1264-1278 (h_model_info)` |
| HIGH | OPEN | `tier1_r-01` | Runtime error tells users to put credentials in ~/.hermes/.env (also the forbidden external home) | `qubit_runtime/qubit_cli/auth.py:1465-1466` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/subcommands/gateway.py:176` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/subcommands/gateway.py:192` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/memory_setup.py:411` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/subcommands/dashboard.py:110` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/subcommands/peer.py:8` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/subcommands/peer.py:179` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/subcommands/peer.py:242` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/subcommands/peer.py:247` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/subcommands/peer.py:432` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/subcommands/peer.py:451` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/config.py:153` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/config.py:1617` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/config.py:2485` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/config.py:2527` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/config.py:2687` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/config.py:2723` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/config.py:2779` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/config.py:2808` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/config.py:2823` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/mcp_catalog.py:470` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/dump.py:17` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/callbacks.py:53` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/plugins.py:1078` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/gateway.py:2933` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/model_setup_flows.py:729` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/runtime_provider.py:909` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/main.py:1018` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/subcommands/secrets.py:13` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/auth_spotify.py:267` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/auth_spotify.py:310` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/gateway_setup_wizard.py:421` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/gateway_setup_wizard.py:731` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/main_provider_setup.py:37` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/doctor_config.py:14` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/doctor_config.py:133` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/setup_tts.py:256` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/mcp_config.py:396` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/agent_import.py:626` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/agent_import.py:660` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/plugins_cmd.py:381` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/auth.py:275` |
| MEDIUM | OPEN | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/auth.py:1526` |
| MEDIUM | OPEN | `dep_pinning` | 19 top-level dependencies are range-pinned | `pyproject.toml` |
| MEDIUM | OPEN | `installer_checksums` | installer downloads artifacts without integrity verification | `scripts/install.sh` |
| MEDIUM | OPEN | `tier1_a1` | missing_in_qubit | `N/A` |
| MEDIUM | OPEN | `tier1_a1` | missing_in_qubit | `qubit_platform/ (absent)` |
| MEDIUM | OPEN | `tier1_a1` | differs | `qubit_runtime/pyproject.toml version="0.21.1", requires-python=">=3.11,<3.14"; repo pyproject version=1.0.0 requires-python=">=3.12"` |
| MEDIUM | OPEN | `tier1_a1` | missing_in_qubit | `product repo pyproject.toml [project.scripts] lacks qubit-acp; .venv/bin has no qubit-acp` |
| MEDIUM | OPEN | `tier1_a1` | differs | `qubit_runtime/pyproject.toml: cryptography==50.0.0; no truststore/tomli-w/pillow-heif/resvg-py/httptools/uvloop/watchfiles/winrt; uvicorn[standard]; pyyaml==6.0.3 extra` |
| MEDIUM | OPEN | `tier1_a2` | requires_apps / skill_matches_apps gating missing | `qubit_runtime/agent/skill_utils.py: no skill_matches_apps and no requires_apps handling` |
| MEDIUM | OPEN | `tier1_a2` | Whole 'setup' toolset missing (manage_catalog surface) | `qubit_runtime/toolsets.py TOOLSETS has no 'setup' entry; 'manage_catalog' is also absent from the registered tool map` |
| MEDIUM | OPEN | `tier1_a2` | Provider registry behind: missing aliases and 0.21.5 home-layer/scoped-provider machinery | `qubit_runtime/providers/__init__.py (441 lines): register_provider:56, get_provider_profile:70, list_providers:86; none of the home-layer functions` |
| MEDIUM | OPEN | `tier1_a2` | Qubit skill metadata reads only metadata.hermes; no metadata.qubit fallback | `qubit_runtime/agent/skill_utils.py:626 _qubit_metadata reads metadata.get('hermes') only` |
| MEDIUM | OPEN | `tier1_a2` | Docs' N29 context mismatch (1,000,000 vs 200,000) is not reproducible from Qubit code/config as written | `qubit_runtime/agent/model_metadata.py:1849-1946; overlay guard voicebridge/providers/resolver.py:1095 terminal_context_guard, :1165-1176 N29 comment` |
| MEDIUM | OPEN | `tier1_a2` | Qubit plugin invoke/catalog runtime is an overlay addition with only 2 in-process handlers | `voicebridge/plugin_runtime.py:1904 invoke(); :491-516 _HANDLERS/_HOST_HANDLERS; voicebridge/plugin_catalog.py:459 local_catalog; voicebridge/qubit_plugins.py` |
| MEDIUM | OPEN | `tier1_a2` | Codex-route and Bedrock context handling diverged | `qubit_runtime/agent/model_metadata.py:1896-1899 inline bedrock check only; no _is_codex_route/_resolve_custom_codex_route_context_length` |
| MEDIUM | OPEN | `tier1_a2` | Vendored config default still names the upstream toolset; live config names an invalid toolset | `qubit_runtime/qubit_cli/config_defaults.py:26 toolsets ['hermes-cli']; live ~/.local/state/qubit-voicebridge/runtime/config.yaml:6-8 toolsets ['Qubit-cli','kanban']; overlay voicebridge/qubit_asgi.py:103-107 registers alias 'qubit-cli' (lowercase)` |
| MEDIUM | OPEN | `tier1_a2` | OpenRouter routing-variant suffix (:nitro/:floor/...) not stripped before resolution | `qubit_runtime/agent/model_metadata.py: _strip_openrouter_routing_variant absent; no call between _strip_provider_prefix (:1882) and endpoint lookup (:1885)` |
| MEDIUM | OPEN | `tier1_a2` | Pinned plugin index serves upstream-brand repo URLs and maintainer NousResearch | `voicebridge/data/plugin_index_pinned.json; served by voicebridge/plugin_catalog.py:283 load_pinned_index, :397 pinned_index_entries` |
| MEDIUM | OPEN | `tier1_a2` | Built-in platform bundles remain hermes-* while plugin-platform helper renamed to qubit-* | `qubit_runtime/toolsets.py:351-355 startswith('qubit-'); but TOOLSETS keys are still hermes-acp/hermes-cli/hermes-telegram/... (get_toolset_names()); qubit_cli/platforms.py:15-36 default_toolset='hermes-*'` |
| MEDIUM | OPEN | `tier1_a2` | Toolset memoization lost per-profile scope key (multiplexed-process cross-profile leak fix) | `qubit_runtime/toolsets.py:325-327 memo key lacks the scope component; call site :361` |
| MEDIUM | OPEN | `tier1_a3` | A3-11 | `qubit_cli/config_home.py:11-13 _directory_links; :15-34 _ensure_directory has no home param and uses `if secure and not links:`` |
| MEDIUM | OPEN | `tier1_a3` | A3-17 | `qubit_cli/webhook.py:58 _get_webhook_base_url; :142,176,204 print flat `{base}/webhooks/{name}`; no _route_url / profile / cron_job / mirror_to_session` |
| MEDIUM | OPEN | `tier1_a3` | A3-18 | `voicebridge/qubit_compat.py:7572-7579 all /api/messaging/*/onboarding* -> ONBOARDING_GUIDED; vendored implementations exist at qubit_cli/web_routers/messaging.py (same file, real) but gated` |
| MEDIUM | OPEN | `tier1_a3` | A3-12 | `agent/secret_scope.py:26-201 (199 lines): no serves_routed_profile/current_secret_scope_home; set_secret_scope(secrets) only (:47); no env-file cache; get_secret() only checks `_MULTIPLEX_ACTIVE` (:135)` |
| MEDIUM | OPEN | `tier1_a3` | A3-02 | `qubit_cli/mcp_config.py:63-71 (`include if include and isinstance(include, list) else None` — empty list collapses to no filter)` |
| MEDIUM | OPEN | `tier1_a3` | A3-05 | `voicebridge/qubit_compat.py:7537-7538, 7648-7649 (GET/DELETE MCP OAuth flows + callback -> _NO_OAUTH / OAUTH_MANUAL_HANDOFF); :7548 (POST /api/mcp/catalog/install -> _NO_REMOTE); vendored implementations exist at qubit_cli/web_routers/mcp.py:404,416,430,548 but are shadowed` |
| MEDIUM | OPEN | `tier1_a3` | A3-08 | `qubit_state_health.py absent; no references in qubit_runtime/qubit_cli/web_routers/sessions.py or qubit_state.py` |
| MEDIUM | OPEN | `tier1_a4` | Fresh Qubit install lacks ASR/TTS/voice-ID models | `fresh install status output` |
| MEDIUM | OPEN | `tier1_a4` | qubit gateway status is host-global, not QUBIT_HOME/profile scoped | `voicebridge/gateway_supervisor.py:326-446 (status)` |
| MEDIUM | OPEN | `tier1_a4` | Reference plugin provenance CLI subcommands absent from Qubit | `qubit_runtime/qubit_cli/subcommands/plugins.py (build_plugins_parser)` |
| MEDIUM | OPEN | `tier1_a4` | Generated parity inventory is stale vs the live route tables | `docs/generated/parity_inventory.json ; scripts/parity_inventory.py` |
| MEDIUM | OPEN | `tier1_a4` | auto_context_length is not auto-derived; Qubit reports 200k where models.dev declares 1M | `voicebridge/qubit_compat.py:1264-1274` |
| MEDIUM | OPEN | `tier1_a4` | Runtime config toolsets references non-existent toolset 'Qubit-cli' | `runtime config.yaml:7 (live) ; qubit_runtime/toolsets.py:194 (only hermes-cli / lowercase qubit-cli alias)` |
| MEDIUM | OPEN | `tier1_a5` | Two Qubit services share one state.db while journal_mode=DELETE with a 2s busy timeout | `systemd/qubit-voicebridge.service (no QUBIT_HOME env) + systemd/qubit-gateway.service:22 Environment=QUBIT_HOME=%h/.local/state/qubit-voicebridge/runtime; connect timeout=2.0 at qubit_runtime/qubit_state_dbfile.py:736; journal_mode=DELETE (STATUS accepted)` |
| MEDIUM | OPEN | `tier1_a5` | Generated evidence artifacts are stale vs current source | `docs/generated/parity_inventory.json, docs/generated/voice_coverage.json, docs/generated/QUBIT_PAGE_COVERAGE_2026-09-21.json, docs/generated/QUBIT_COMPAT_HANDLER_REACHABILITY.json` |
| MEDIUM | OPEN | `tier1_a5` | Installer downloads models/voice assets with no checksum verification | `scripts/install.sh:151-204 (dl() -> curl -sL ...); 7 curl calls, no sha256/shasum/gpg check` |
| MEDIUM | OPEN | `tier1_a5` | Generators hardcode ephemeral /tmp/opencode/pinned sources (not reproducible on a fresh checkout) | `scripts/source_inventory.py:66-67 DEFAULT_HERMES/DEFAULT_OPENCLAW='/tmp/opencode/pinned/...'; scripts/hermes_vendor_manifest.py:1716,1723; scripts/hermes_byte_manifest.py:59-61` |
| MEDIUM | OPEN | `tier1_a5` | Vendored runtime version metadata is stale vs the pinned upstream (0.21.1 vs v0.21.4) | `qubit_runtime/pyproject.toml:5 version='0.21.1'; qubit_runtime/qubit_cli/__init__.py:6 __version__='0.21.1'; pin/upstream tag is v0.21.4 (docs/generated/QUBIT_HERMES_VENDOR_MANIFEST.json upstream_tag='v0.21.4', rev d337b736)` |
| MEDIUM | OPEN | `tier1_a5` | No Windows/macOS installer or desktop packaging in Qubit | `scripts/install.sh only; systemd units only; qubit_runtime/apps/desktop present but not built by any installer` |
| MEDIUM | OPEN | `tier1_a5` | Qubit updater is a partial port of the reference updater (7 vs 30 modules; no abort/restart/serve recovery) | `voicebridge/update_manager.py + update_{channel,config_migration,handoff,lock,receipt,validation}.py (7 modules)` |
| MEDIUM | OPEN | `tier1_a5` | All 1,608 vendored files have git_index_gaps=0 now, but the OpenClaw pin input is not durable | `scripts/hermes_vendor_manifest.py:1723 default /tmp/opencode/pinned/openclaw-<tag>; regeneration output 'openclaw pin not found'` |
| MEDIUM | OPEN | `tier1_a5` | UI controls call routes that are typed refusals (present-but-gated controls) | `frontend/qubit-web/src/components/QubitConsoleModal.tsx:404 (buildWsUrl("/api/console")); voicebridge/qubit_compat.py:7492 (GATED /api/console = PTY_NO_SHELL), :7488 (ws-ticket WS_SINGLE_PATH), :7548 (/api/mcp/catalog/install _NO_REMOTE), :7496 (/api/env/reveal _NO_SECRET), GATED /api/skills/hub/* _NO_REMOTE, /api/messaging/*/onboarding _ONBOARDING_GUIDED` |
| MEDIUM | OPEN | `tier1_a5` | Top-level runtime dependencies are unpinned ranges; no top-level lockfile | `pyproject.toml:21-39 ranges ('sherpa-onnx>=1.13', 'numpy>=1.24', 'piper-tts>=1.2', 'playwright>=1.49', ...)` |
| MEDIUM | OPEN | `tier1_b1` | version delta v0.21.4 vs ref v0.21.5+ for gateway/plugins decompositions | `ref lines in asymmetries` |
| MEDIUM | OPEN | `tier1_b1` | Qubit deliberately names ~/.hermes as the read-only external reference / migration source | `voicebridge/update_channel.py:165, qubit_session_store.py:191, qubit_migration.py:76, qubit_legacy.py:48, voicebridge/qubit_runtime.py:325` |
| MEDIUM | OPEN | `tier1_b1` | wire/dotted/hyphen/quoted identifiers intentionally preserved by the engine | `qubit_runtime+voicebridge` |
| MEDIUM | OPEN | `tier1_b2` | 52 curated path renames must be re-run | `repo has qubit-achievements, qubit-s6-container-supervision, qubit-migration, skills/**/qubit-agent, ui-tui/packages/qubit-ink, QubitConsoleModal.tsx` |
| MEDIUM | OPEN | `tier1_b2` | legacy installer name | `repo has setup-qubit.sh; engine leaves setup-hermes.*` |
| MEDIUM | OPEN | `tier1_b3` | not migrated | `command_allowlist: []` |
| MEDIUM | OPEN | `tier1_b3` | Qubit overlay adds MCP discovery routes; currently shadowed by the compat /api/mcp/servers prefix | `vendored mcp.py adds GET /api/mcp/servers/{name}/{resources,prompts,resource-templates}` |
| MEDIUM | OPEN | `tier1_b3` | version metadata drift (R2), not a missing command | `same upstream command surface via `qubit agent` (grants `login`); Qubit advertises v0.21.1` |
| MEDIUM | OPEN | `tier1_b3` | Qubit SPA base-path choice; breaks external reference/desktop API clients | `server only exposes /agent/api/* (bare /api/health -> 404, /agent/api/health -> 401/route-exists); voicebridge/webui.py:857,1488,2105-2112` |
| MEDIUM | OPEN | `tier1_b3` | Qubit ships 0.21.4 defaults + its own voice choices the reference user overrode | `logging.level=DEBUG, dashboard.theme=default, terminal.timeout=180, updates.pre_update_backup=quick, x_search.model=grok-4.5, tts.provider=edge` |
| MEDIUM | OPEN | `tier1_b3` | SPA mount-prefix rewrite, not a behaviour change of the handlers | `qubit_compat registers the same /api/... patterns internally while the HTTP server exposes them under /agent/api/*` |
| MEDIUM | OPEN | `tier1_b3` | vendored router slice not synced to the pin; overlay supplies the behaviour | `vendored web_routers/{status,audio}.py omit them; behaviour served by qubit_compat instead` |
| MEDIUM | OPEN | `tier1_b3` | Qubit-owned overlay architecture | `two units: qubit-voicebridge.service (`-m voicebridge run`, port 8890, Restart=on-failure, VOICE_BRIDGE_CONFIG) + qubit-gateway.service (`-m voicebridge.gateway_daemon`, QUBIT_HOME=runtime, Restart=always) + per-nav tui_gateway.entry child` |
| MEDIUM | OPEN | `tier1_b3` | migration did not map platform_toolsets | `runtime/config.yaml has no platform_toolsets (null) -> every platform falls back to its default toolset` |
| MEDIUM | OPEN | `tier1_b3` | WAL switch did not apply on this host's cross-VM fs; Qubit splits dashboard and gateway into two processes | `runtime state.db journal_mode=delete despite config database.journal_mode=wal; TWO writers hold it open (voicebridge run pid + gateway_daemon)` |
| MEDIUM | OPEN | `tier1_b3` | config split/layout | `repo/config/* is not the agent config: only voice.example.yaml + voice.local.yaml (dead, referenced only by voicebridge/release.py); installed ~/.config/qubit-voicebridge/voice.yaml (3270 B, port 8890) is the sole voice authority` |
| MEDIUM | OPEN | `tier1_b3` | overlay config choice | `cgraph-graft with explicit capabilities list` |
| MEDIUM | OPEN | `tier1_b3` | reference 0.21.5 feature absent from the 0.21.4 rebrand | `both router files absent from repo/qubit_runtime/qubit_cli/web_routers/; no qubit_compat route; not present in pinned 0.21.4` |
| MEDIUM | OPEN | `tier1_b3` | product-token rename | `repo/qubit_runtime/qubit_cli/web_routers/actions.py uses '/api/qubit/update' + '/api/qubit/update/check'` |
| MEDIUM | OPEN | `tier1_b3` | 0.21.5 additions | `absent from vendored routers and qubit_compat; not in pinned 0.21.4` |
| MEDIUM | OPEN | `tier1_b3` | owner-excluded reference provider from migration; Qubit-owned model config | `model.provider=opencode-go, default=deepseek-v4-flash, base_url=https://opencode.ai/zen/go/v1` |
| MEDIUM | OPEN | `tier1_b4` | The code/README advertise cgraph-dev/qubit-voicebridge as the canonical update+clone source, but the configure | `voicebridge/update_channel.py:64-65 QUBIT_REPOSITORY='cgraph-dev/qubit-voicebridge'` |
| MEDIUM | OPEN | `tier1_b4` | toolset id renamed inconsistently across code/config; capital-Q resolves to 0 tools | `voicebridge/qubit_asgi.py:102-107 (aliases lowercase 'qubit-cli' only) + live config 'Qubit-cli'` |
| MEDIUM | OPEN | `tier1_b4` | MoA fan-out is a permanent stub and auxiliary-task model pins are refused ('the main profile owns routing'). T | `voicebridge/qubit_compat.py:1387-1399 (h_model_moa returns enabled:false, presets:{} always) + :1401+ (h_model_set refuses scope!='main' with AUX_PIN_REFUSED)` |
| MEDIUM | OPEN | `tier1_b4` | resolve_toolset('Qubit-cli') -> 0 tools (invalid, silently ignored); resolve_toolset('qubit-cli') -> 53 (only  | `voicebridge/qubit_asgi.py:102-107 aliases ONLY lowercase 'qubit-cli'; live config sets 'Qubit-cli' (runtime/config.yaml toolsets: [Qubit-cli, kanban])` |
| MEDIUM | OPEN | `tier1_b4` | h_status returns a reduced 'qubit-runtime-status/1' shape: gateway_platforms is hardcoded {}, memory/disk pres | `voicebridge/qubit_compat.py:377-436 (h_status)` |
| MEDIUM | OPEN | `tier1_b4` | Qubit implements the core lifecycle (lock/snapshot/pull/migrate/validate/restart/health/rollback) but not abor | `voicebridge/update_manager.py + update_{channel,config_migration,handoff,lock,receipt,validation}.py (7 modules)` |
| MEDIUM | OPEN | `tier1_b4` | Non-gated GET/HEAD reads are proxied to the vendored v0.21.4 fork; only QUBIT_READ_AUTHORITY paths (sessions,  | `voicebridge/qubit_asgi.py:327-370 (gated_path/safe_to_proxy) + voicebridge/webui.py:879-965 (QUBIT_READ_AUTHORITY)` |
| MEDIUM | OPEN | `tier1_b4` | GET/DELETE /api/mcp/oauth/flows*, callback, and POST /api/mcp/catalog/install answer typed refusals (OAUTH_MAN | `voicebridge/qubit_compat.py:7536-7549 (GATED _NO_OAUTH / _NO_REMOTE) shadow the vendored handlers` |
| MEDIUM | OPEN | `tier1_b4` | The Plugins page renders pinned entries whose names/repos/maintainer carry the legacy brand (github.com/NousRe | `voicebridge/data/plugin_index_pinned.json (5 entries, 20x NousResearch, hermes-* repos) served by voicebridge/plugin_catalog.py:283 load_pinned_index / :338 pinned_index_entries` |
| MEDIUM | OPEN | `tier1_b4` | status() reads the host user systemd unit (qubit-gateway.service) plus a TCP probe of the configured port, ign | `voicebridge/gateway_supervisor.py:326-446 (status) + :344 daemon_service_unit(cfg)` |
| MEDIUM | OPEN | `tier1_b4` | direct vendored imports can create/read the read-only reference home | `qubit_runtime/qubit_constants.py:43-51 still -> ~/.hermes; guarded only by voicebridge/qubit_runtime.py:226 pin_qubit_home()` |
| MEDIUM | OPEN | `tier1_b4` | Qubit refuses to open /chat (TERMINAL_CONTEXT_BELOW_MINIMUM / TERMINAL_PROFILE_NOT_EXECUTABLE) for a profile b | `voicebridge/providers/resolver.py:71-74, :971-980, :1095-1209 (terminal_context_guard, floor terminal.min_context_tokens default 65536)` |
| MEDIUM | OPEN | `tier1_b4` | Qubit sets auto_context_length = config_context_length = effective_context_length = the resolver's declared/ef | `voicebridge/qubit_compat.py:1264-1278 + voicebridge/providers/resolver.py:1160-1176 (N29 comment)` |
| MEDIUM | OPEN | `tier1_b4` | Automatic channel onboarding is replaced by guided manual enrollment; the vendored handlers are shadowed by th | `voicebridge/qubit_compat.py:7573-7581 (all /api/messaging/*/onboarding* -> ONBOARDING_GUIDED)` |
| MEDIUM | OPEN | `tier1_r-02` | Uninstall help advertises the wrong command name 'hermes' | `qubit_runtime/qubit_cli/uninstall.py:459` |
| MEDIUM | OPEN | `tier1_r-03` | Help example uses legacy-named bundled skill | `qubit_runtime/qubit_cli/_parser.py:87` |
| MEDIUM | OPEN | `tier1_r-04` | Auth upgrade help prints 'Reference behavior: hermes auth upgrade ...' | `qubit_runtime/qubit_cli/auth_commands.py:798` |
| MEDIUM | OPEN | `tier1_r-05` | Served bundle ships a legacy-named bundled plugin id/path | `frontend/qubit-dist/plugins/fleet.json:2 (and .../plugins/hermes-achievements/)` |
| MEDIUM | OPEN | `tier1_r-06` | Gateway setup prints '~/.hermes/.env' as the credential store | `qubit_runtime/qubit_cli/gateway.py:5078,5388` |
| LOW | OPEN | `default_tool_bundle` | audit check default_tool_bundle crashed | `` |
| LOW | OPEN | `tier1_a1` | extra_in_qubit | `voicebridge/gateway_daemon.py:41-145 (service_unit_name/preflight/run_gateway/main); voicebridge/gateway_supervisor.py:82-996 (status/control/failure/recover/install_unit)` |
| LOW | OPEN | `tier1_a1` | missing_in_qubit | `N/A` |
| LOW | OPEN | `tier1_a1` | missing_in_qubit | `N/A` |
| LOW | OPEN | `tier1_a1` | differs | `qubit_runtime/gateway/control_socket.py (366 lines: same core plus _legacy_windows_pipe_name, _query_windows_pipe_named; lacks the profile-serve/rescan/reload verbs)` |
| LOW | OPEN | `tier1_a1` | differs | `qubit_runtime/tests 4450 .py vs pin 4899 .py (~449 fewer test modules)` |
| LOW | OPEN | `tier1_a1` | differs | `qubit_runtime/agent/ (same filenames)` |
| LOW | OPEN | `tier1_a1` | differs | `qubit_runtime/qubit_cli/kanban_db.py (no KANBAN_TERMINAL_PROVIDER_EXIT_CODE); no stderr_timestamp.py / update_cmd_windows.py` |
| LOW | OPEN | `tier1_a1` | missing_in_qubit | `qubit_yaml.py (absent)` |
| LOW | OPEN | `tier1_a1` | differs | `qubit_runtime/agent/agent_init.py:939 from agent.ssl_guard import verify_ca_bundle` |
| LOW | OPEN | `tier1_a1` | missing_in_qubit | `N/A` |
| LOW | OPEN | `tier1_a1` | missing_in_qubit | `N/A` |
| LOW | OPEN | `tier1_a1` | differs | `qubit_runtime/agent/ssl_guard.py` |
| LOW | OPEN | `tier1_a1` | extra_in_qubit | `qubit_cli/{dep_ensure,gateway_service_unit,plugin_index,plugin_python_deps,qubit_state_profile_repair,update_cmd_deps}.py; tools/{connections_tool,setup_mcp_tool}.py; agent/ssl_guard.py (retained)` |
| LOW | OPEN | `tier1_a1` | differs | `qubit_runtime/gateway/run.py` |
| LOW | OPEN | `tier1_a1` | differs | `qubit_runtime/pyproject.toml:391-394 qubit=qubit_cli.main:main; qubit-agent=run_agent:main; qubit-acp=acp_adapter.entry:main` |
| LOW | OPEN | `tier1_a1` | missing_in_qubit | `qubit_constants_scratch.py (absent)` |
| LOW | OPEN | `tier1_a1` | missing_in_qubit | `N/A` |
| LOW | OPEN | `tier1_a1` | missing_in_qubit | `qubit_state_health.py (absent)` |
| LOW | OPEN | `tier1_a1` | renamed | `qubit_cli/subcommands/*.py (63 modules)` |
| LOW | OPEN | `tier1_a1` | extra_in_qubit | `voicebridge/gateway_daemon.py, voicebridge/gateway_supervisor.py` |
| LOW | OPEN | `tier1_a1` | missing_in_qubit | `N/A` |
| LOW | OPEN | `tier1_a1` | renamed | `qubit_cli/ etc.` |
| LOW | OPEN | `tier1_a1` | differs | `qubit_runtime/qubit_cli/main.py:2598-2622 _BUILTIN_SUBCOMMANDS omits 'vault'` |
| LOW | OPEN | `tier1_a1` | missing_in_qubit | `N/A` |
| LOW | OPEN | `tier1_a2` | profile_role_toolsets() (role-scoped toolset grant/deny) missing | `qubit_runtime/toolsets.py: no equivalent (file ends earlier)` |
| LOW | OPEN | `tier1_a2` | Bundled skill set drift (renames + additions + 5 upstream skills absent) | `qubit_runtime/skills + optional-skills: 213 SKILL.md` |
| LOW | OPEN | `tier1_a2` | skills_tool lost same-name disambiguation helpers | `qubit_runtime/tools/skills_tool.py: absent (skills_list:237, skill_view:524)` |
| LOW | OPEN | `tier1_a2` | Credential env-ref resolution duplicated in a Qubit overlay; N17 fallback removal verified | `qubit_runtime/qubit_cli/config.py:1574-1600 (same, de-branded); overlay voicebridge/providers/credentials.py:134 resolve_key (env:/file:) with redaction` |
| LOW | OPEN | `tier1_a2` | Vendored plugin fleet still uses hermes-* dir/id (hermes-achievements) and hermes_* env names | `qubit_runtime/plugins/hermes-achievements (same name), _HOST_HANDLERS['builtin.achievements']` |
| LOW | OPEN | `tier1_a3` | A3-13 | `qubit_runtime/qubit_cli/config.py adapters + qubit_cli/web_routers/config_env.py:275-285 set_env_var (no preview guard; uses plain redact_key at :234/:330)` |
| LOW | OPEN | `tier1_a3` | A3-14 | `qubit_cli/config_defaults.py DEFAULT_CONFIG 815 leaf keys / 332 OPTIONAL_ENV_VARS` |
| LOW | OPEN | `tier1_a3` | A3-20 | `absent (qubit_cli/config_read_errors.py, qubit_yaml.py, qubit_platform absent)` |
| LOW | OPEN | `tier1_a3` | A3-04 | `voicebridge/qubit_compat.py:2297-2320 h_mcp_test_local returns out['errors'] verbatim; voicebridge/mcp_servers.py:377-387 _probe returns `f"{exc.code}: {exc.detail}"`` |
| LOW | OPEN | `tier1_a3` | A3-15 | `qubit_constants.py:43-51 _get_platform_default_qubit_home returns ~/.hermes (POSIX) / %LOCALAPPDATA%/hermes (win32); also qubit_runtime/tools/mcp_tool_config.py:134 and tools/bot_mode_dm.py:54 / bot_mode_probe.py:41 fall back to ~/.hermes when QUBIT_HOME is unset` |
| LOW | OPEN | `tier1_a3` | A3-10 | `qubit_time.py (93 lines; no _repair_surrogates/safe_strftime/_env_timezone; reads HERMES_TIMEZONE directly at :30/:36)` |
| LOW | OPEN | `tier1_a3` | A3-09 | `qubit_cli/web_routers/sessions.py absent (no timeline/messages-around); served only by voicebridge/qubit_compat.py:6966-6968 + handlers :13165-13235 over qubit_state_timeline` |
| LOW | OPEN | `tier1_a3` | A3-03 | `qubit_cli/mcp_config.py:583-621 cmd_mcp_test returns None` |
| LOW | OPEN | `tier1_a4` | Reference MCP server openclaw-voice-bridge-graft not present in Qubit | `runtime config.yaml mcp_servers (only cgraph-graft) ; live /agent/api/mcp/servers` |
| LOW | OPEN | `tier1_a4` | /api/status omits provider while /api/model/info reports opencode-go | `voicebridge/qubit_compat.py h_status / h_model_info` |
| LOW | OPEN | `tier1_a4` | browser_cdp missing on both installs | `qubit doctor (browser_cdp MISSING 127.0.0.1:9222)` |
| LOW | OPEN | `tier1_a4` | Legacy wire token names on the served dashboard | `voicebridge/webui.py:1336 ; served index.html window.__HERMES_SESSION_TOKEN__` |
| LOW | OPEN | `tier1_a4` | Vendored package/gateway version metadata stale vs the declared pin | `qubit_runtime/pyproject.toml:6 (version 0.21.1) ; scripts/hermes_vendor_manifest.py:35 PIN=d337b736 (v0.21.4) ; gateway control payload code_version=0.21.1` |
| LOW | OPEN | `tier1_a4` | Config journal_mode=wal while the live state.db is journal_mode=delete | `runtime config.yaml database.journal_mode: wal ; runtime/state.db` |
| LOW | OPEN | `tier1_a5` | Docs say 'the reference Hermes pin' and quote legacy tokens; internal docs are otherwise de-branded | `docs/FINDINGS_2026-09-22.md:43; docs/STATUS.md; docs/generated/* manifests carry upstream_repo/upstream_license attribution` |
| LOW | OPEN | `tier1_a5` | Qubit lacks a 'qubit-acp' console entry point (reference ships hermes-acp) | `pyproject.toml:82-90 scripts are qubit, voicebridge, qubit-dev, qubit-agent (no qubit-acp); qubit_runtime/acp_adapter/entry.py exists and 'acp' is a CLI subcommand` |
| LOW | OPEN | `tier1_a5` | Ignored release artifact dist/release-8k contains a full legacy-named venv tree | `dist/release-8k/venv/lib/python3.12/site-packages/voicebridge/_data/qubit_hermes/plugins/kanban/systemd/hermes-kanban-dispatcher.service (Documentation=https://hermes-agent.nousresearch.com/..., ExecStart=hermes kanban daemon); path segment qubit_hermes/` |
| LOW | OPEN | `tier1_a5` | Dashboard route map deliberately differs: /chat is TerminalChatPage, /qubit-chat is the native ChatPage | `frontend/qubit-web/src/App.tsx:492 (embeddedChat ? {'/chat': TerminalChatPage, '/qubit-chat': ChatRouteSink} : {})` |
| LOW | OPEN | `tier1_a5` | Dashboard API-path surface: every UI /api/* literal resolves to a backend pattern | `frontend/qubit-web/src/lib/api.ts:39 withApiPrefix() rewrites /api/* -> /agent/api/*; voicebridge/qubit_compat.py:6739 ROUTES; voicebridge/webui.py:1488` |
| LOW | OPEN | `tier1_a5` | Built dashboard bundle is consistent with source (no stale bundle) | `frontend/qubit-dist/index.html:1; frontend/qubit-web/vite.config.ts:106 (outDir ../qubit-dist)` |
| LOW | OPEN | `tier1_b2` | brand residue | `present legacy-named/foldered in repo` |
| LOW | OPEN | `tier1_b2` | engine output != repo policy for ~2600 vendored files; noise in residual | `qubit_rebrand.py rewrites these; qubit_debrand.py PROTECTED_PARTS excludes them` |
| LOW | OPEN | `tier1_b4` | The dashboard MCP test JSON body returns probe errors verbatim; a connection error whose message embeds an Aut | `voicebridge/qubit_compat.py:2297-2320 (h_mcp_test_local) + voicebridge/mcp_servers.py:368-388 (_probe -> f'{exc.code}: {exc.detail}')` |
| LOW | OPEN | `tier1_b4` | The overlay fail-closes QUBIT_HOME at the runtime boundary before the fork import, so normal entrypoints never | `voicebridge/qubit_runtime.py:226-246 pin_qubit_home + :324-326 _external_default_home; vendored qubit_runtime/qubit_constants.py:43-51 still defaults to ~/.hermes` |
| LOW | OPEN | `tier1_b4` | Toggling a toolset stages a durable human confirmation and persists a Qubit-owned enabled flag (precedence: pe | `voicebridge/toolsets.py:275-365 (stage/settle on the shared approvals journal, persisted <state_dir>/toolsets.json)` |
| LOW | OPEN | `tier1_b4` | qubit gateway start/stop/restart require --confirm (GATEWAY_CONFIRM_REQUIRED otherwise) and run e-stop -> conf | `voicebridge/gateway_supervisor.py:700-996 (control/failure/recover, confirm-gated) + voicebridge/gateway_daemon.py` |
| LOW | OPEN | `tier1_b4` | The legacy-source URL guard now refuses openclaw-branded URLs (the repo's own old origin). The earlier audit f | `voicebridge/update_channel.py:87 LEGACY_URL_TOKENS=('hermes','nousresearch','nous','openclaw')` |
| LOW | OPEN | `tier1_b4` | Qubit reads one canonical store (state.db) through an adapter and merges receipt-lifecycle rows; `limit` still | `voicebridge/qubit_session_store.py:299 (QubitSessionStore over qubit_state.SessionDB) + chat_transport.py:367-380 + qubit_compat.py:611-706 (merged chat + receipt rows)` |
| LOW | OPEN | `tier1_b4` | Qubit serves its own MCP registry store (so reads match the Qubit writes) and `qubit mcp invoke` only executes | `voicebridge/mcp_servers.py (Qubit registry) + webui.py:898-906 QUBIT_READ_AUTHORITY for /api/mcp/servers,/catalog; voicebridge/plugin_runtime.py:491-516 only builtin.echo/achievements are in-process invocable` |
| LOW | OPEN | `tier1_b4` | Two Qubit processes (webui+PTY+agent, and the gateway daemon + cron dispatcher) open the same canonical state. | `systemd/qubit-voicebridge.service (no QUBIT_HOME env) + systemd/qubit-gateway.service:22 Environment=QUBIT_HOME=...; journal_mode=wal in config vs live state.db journal_mode=delete` |
| LOW | OPEN | `tier1_r-07` | Env value disclosure text references the external home | `qubit_runtime/qubit_cli/proxy_cli.py:160` |
| INFO | OPEN | `tier1_a3` | A3-06 | `qubit_cli/web_routers/mcp.py:256,262,332 (vendored routes) + voicebridge/qubit_compat.py:7060-7064, 2340-2404 (compat routes/handlers); QUBIT_READ_AUTHORITY includes /api/mcp/servers (webui.py:901)` |
| INFO | OPEN | `tier1_a3` | A3-16 | `voicebridge/orchestrator.py:2674-2699 _env_key and voicebridge/tts/openai_tts.py:31-51 _read_key consult ONLY ~/.config/qubit-voicebridge/.env / canonical runtime .env + os.environ` |
| INFO | OPEN | `tier1_a3` | A3-07 | `voicebridge/chat_transport.py:367-380 store() -> QubitSessionStore (voicebridge/qubit_session_store.py:299) over qubit_state.SessionDB; qubit_compat.py:575-591 _chat_session_store enforces the contract; :614 _chat_session_rows; webui.py:891 /api/sessions in QUBIT_READ_AUTHORITY` |
| INFO | OPEN | `tier1_a3` | A3-19 | `voicebridge/qubit_compat.py:7023-7031 cron CRUD, :7469 blueprints/instantiate, :7054-7064 pairing, :7122-7139 ops/hooks, :7089-7091 & :7458-7461 files, :7263-7270 fs reads; all CRUD delegates to the Qubit overlay stores` |
| INFO | OPEN | `tier1_a4` | Qubit repo remote is the fork, not upstream (recorded) | `git remote -v` |
| INFO | OPEN | `tier1_b4` | Qubit's plugin catalog has 72 entries but only builtin.echo and builtin.achievements have in-process handlers; | `voicebridge/plugin_runtime.py:491-516,1904 + voicebridge/plugin_catalog.py:459` |
| INFO | OPEN | `tier1_b4` | Qubit adds its own conservative redactor (keys, bearer, JWT, sk-, URL userinfo/query). It is not used by the v | `voicebridge/redaction.py:1-84 (redact_text/redact_obj) used by the native turn/toolsets/agent paths` |
| INFO | OPEN | `tier1_b4` | Voice/orchestrator turns use a Qubit-native, local-first turn path (redaction/audit/receipts) instead of the r | `voicebridge/agent.py:229-473 (native turn via providers.gateway.run_role, local-first) + voicebridge/orchestrator.py` |


## Fixed / resolved (tracked)

| Sev | Status | Check | Finding | Location |
|---|---|---|---|---|
| MEDIUM | FIXED | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/send_cmd.py:257` |
| MEDIUM | FIXED | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/env_loader.py:397` |
| HIGH | FIXED | `cli_exit_code_contract` | qubit oneshot does not map interrupted/failed turns to exit codes | `qubit_runtime/qubit_cli/oneshot.py` |
| MEDIUM | FIXED | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/gateway_service_unit.py:37` |
| MEDIUM | FIXED | `generated_staleness` | generated artifact is stale: page_coverage | `scripts/page_coverage.py` |
| MEDIUM | FIXED | `generated_staleness` | generated artifact is stale: parity_inventory | `scripts/parity_inventory.py` |
| MEDIUM | FIXED | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/tips.py:326` |
| HIGH | FIXED | `home_fallback` | vendored default home is the reference home (Path.home()/'.hermes') | `qubit_runtime/qubit_constants.py:51` |
| MEDIUM | FIXED | `credential_home_message` | user-facing credential guidance names the reference home | `qubit_runtime/qubit_cli/proxy_cli.py:573` |
| HIGH | FIXED | `mcp_redaction_contract` | qubit mcp test partially discloses credential material | `qubit_runtime/qubit_cli/mcp_config.py` |
| HIGH | FIXED | `home_fallback` | vendored default home is the reference home (QUBIT_HOME default -> .hermes) | `qubit_runtime/qubit_cli/env_loader.py:319` |
| HIGH | FIXED | `home_fallback` | vendored default home is the reference home (QUBIT_HOME default -> .hermes) | `qubit_runtime/tools/mcp_tool_config.py:134` |
| HIGH | FIXED | `model_capability_contract` | /api/model/info does not derive model capabilities from provider metadata | `voicebridge/qubit_compat.py` |
| HIGH | FIXED | `home_fallback` | vendored default home is the reference home (Path.home()/'.hermes') | `qubit_runtime/qubit_cli/env_loader.py:674` |
| MEDIUM | FIXED | `version_skew` | vendored runtime reports 0.21.1 but the pin is v0.21.4 | `qubit_runtime/pyproject.toml` |


## Triaged (owner overlay)

_none_


## Tier 1 — deep agent audit

Tier 0 is deterministic and cheap. To re-derive the full 107-row forensic set (providers, toolsets, skills, plugins, MCP, sessions, rebrand, future-breakage), run the five-agent deep dive described in `scripts/forensic_audit/TIER1_AGENT_PROMPTS.md` and ingest the outputs:

```bash
.venv/bin/python scripts/forensic_audit/ingest_agent_findings.py \
    /tmp/qubit-hermes-forensic-audit-*/reports/A*.json
```
