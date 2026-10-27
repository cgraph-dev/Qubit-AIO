# Rebrand prose cleanup — P8 D17/D18 (2026-10-27)

**Status:** scoped prose cleanup only. **Not** a functional rename, not `VERIFIED`, not a
completion claim. No identifiers, env names, executable paths, contracts, error strings, or
tests were changed.

- Worktree: `/tmp/opencode/wt-p8-d1718`
- Branch: `feat/p8-d1718`
- Base: `ddc4056ab` (`fix(cli): make agent roles/run and connectors status reachable from 'qubit'`)
- Scope authority: `docs/PHASE8_INVENTORY.md` D17/D18, `docs/PHASE8-SIGNOFF-PACK.md`,
  `docs/BRAND.md` §1–§3.

> `docs/PHASE8-SIGNOFF-PACK.md` records D17/D18 as **Keep / clean only where the prose is
> factually wrong** and says "not this lane". This lane implements only that narrow option:
> stale *canonical-home* prose is corrected, and legacy-fallback prose is left verbatim.
> Nothing was deleted, renamed, or re-ported.

## What was changed

### D17 — production comments/docstrings

Scope: `qubit_runtime/**` and `voicebridge/**`, excluding `qubit_runtime/tests/**`,
`qubit_runtime/website/**`, `docs/archive/**`, generated/vendor bundles, migration skills,
`evals/**`, `scripts/**`, and `apps/**`.

Method: a span-aware scan (`ast` docstrings + `tokenize` comments) rewrote only occurrences
inside comments/docstrings; a verifier then asserted every changed `.py` line is a comment or
docstring (`non-comment/docstring changed lines: 0`). A curated second pass corrected
unambiguous product-name prose (`Hermes` — the old product name — → `Qubit`) only where the
line was not a literal path, identifier, model name, attribution, or upstream reference.

- **189** prose occurrences of `~/.hermes` → `~/.qubit` (canonical home), across **146** `.py`
  files.
- **84** curated product-prose occurrences (`Hermes` → `Qubit`), e.g. `Hermes Agent CLI`,
  `Update Hermes Agent`, `Hermes home`, `Hermes core`, `Hermes process`.
- **0** `voicebridge/**` lines changed: every hit there describes the operator's **external**
  `~/.hermes` (read-only legacy source) and is factually correct.

Measured scoped totals (all code+prose, `${GLOBS}`-style `git grep`):

```bash
git grep -oF '~/.hermes' HEAD -- \
  'qubit_runtime/*.py' 'qubit_runtime/**/*.py' 'voicebridge/*.py' 'voicebridge/**/*.py' \
  ':!qubit_runtime/tests/**' ':!qubit_runtime/website/**' | wc -l
# before: 406
# after:  217   (working tree)
```

### D18 — bundled skill prose

Scope: `qubit_runtime/skills/**` and `skills/**`, non-migration prose only.

- `qubit_runtime/skills/productivity/box/references/oauth-setup.md`: example environment
  `hermes-box-oauth` → `qubit-box-oauth`.
- `qubit_runtime/skills/productivity/google-workspace/SKILL.md`: example filename
  `~/Downloads/hermes-google-client-secret.json` → `qubit-google-client-secret.json`.

No migration source under `qubit_runtime/optional-skills/migration/**` was touched.

## Preserved exceptions (deliberately unchanged)

| Exception | Where / why |
|---|---|
| `_LEGACY_HOME_DIR = ".hermes"` and the read-old resolver | `qubit_runtime/qubit_constants.py`; legacy installs must keep resolving. Prose such as "pre-rebrand … accepted read-old" is a literal fact and was kept. |
| `/root/.hermes` container bind-mount target | BRAND §2: host and image-side mount paths must agree. **0 changes.** |
| `${HERMES_SKILL_DIR}`, `${HERMES_SESSION_ID}`, `HERMES_*` env names | Cross-process contracts. Only the surrounding prose path changed; every `HERMES_*` token is intact. |
| `metadata.hermes.*`, `_meta.hermes.*`, `ai.hermes.*` | Wire/launchd/systemd contracts. **0 changes.** |
| `.hermes-update-*`, `.hermes-bootstrap-complete`, `.hermes-package.json`, `.hermes-catalog.json`, `hermes-update.lock`, `hermes-gateway*` | Marker/unit identifiers. **0 changes.** |
| `requires_hermes`, `hermes-agent`, `${HERMES_HOME...}` | Plugin/upstream contracts. **0 changes.** |
| Upstream attribution, `NousResearch`, `Nous Research`, model family `Hermes 3` / `Hermes 4`, pins/URLs | Legal/provenance exception, BRAND §2. **0 changes.** |
| Sandbox/remote-home mapping (`/root/.hermes`, `~/.hermes` on remote backends) | Behavioural contract of the SSH/Docker/Modal backends. |
| Project-local `<repo>/.hermes/skills` and `./.hermes/plugins` | `PROJECT_SKILLS_SUBDIRS` still literally uses `.hermes`; the comments are correct. |
| Literal UI/path/binary strings (`Hermes.app`, `Hermes.exe`, `Application Support/Hermes`, `X-Hermes-Session-*`, `Hermes TUI Status`, a quoted desktop message) | Literal examples/error strings. Kept verbatim; not product prose. |
| React Native's "Hermes" JS engine, `PAL_HERMES`, `inspecting-hermes-desktop-dom` skill name, `hermes@agent.ai` contact | Unrelated external names / identifiers. |
| Migration scripts and legacy install-path fallbacks (`LOCALAPPDATA "hermes"`, `.hermes_history`) | Migration/history. Never rewritten. |

## Ambiguous hits left (deliberate)

These were considered and **not** changed because the path is a real legacy/install contract or
an upstream issue example; each is listed rather than silently rewritten:

- `qubit_runtime/qubit_state_holders.py:196` — root store `~/.hermes/state.db` in an `#92401`
  issue example.
- `qubit_runtime/qubit_state_holders.py:289` — install-root layout for a custom `QUBIT_HOME`.
- `qubit_runtime/tools/approval_detection.py:530,594` — the matcher really folds `QUBIT_HOME`
  to the `~/.hermes/` spelling so the legacy regexes match.
- `qubit_runtime/qubit_cli/_startup_fast.py:64,67` — a quoted `Path("~/.hermes")` example
  illustrating the tilde-expansion bug.
- `qubit_runtime/plugins/platforms/whatsapp/adapter.py:527` — describes the external bridge
  hard-coding `~/.hermes/..._cache` (the reason the override exists).
- `qubit_runtime/qubit_cli/uninstall.py:1069` — "Only Hermes-named entries are touched";
  those filesystem entries are literally Hermes-named.
- `qubit_runtime/qubit_cli/linux_desktop_entry.py:779` — legacy desktop-entry alias.
- `qubit_runtime/gateway/status.py:622` — "Atomic Hermes' bundled desktop runner" names a
  specific bundler.
- `qubit_runtime/gateway/platforms/webhook_filters.py:26` — documents the actual mapping of a
  legacy `~/.hermes` user path onto the active profile home.
- `qubit_runtime/tools/skills_guard.py:287` and `plugin_guard.py:71` — examples of external
  README/security false positives that quote `~/.hermes`.
- Remaining `~/.hermes` prose in `voicebridge/**` (55 lines) is the operator's external,
  read-only legacy home by design.

## Verification

```bash
python3 -m compileall -q <146 changed .py files>        # COMPILE_OK
python3 scripts/check_brand_half_rename.py              # OK (46 global name(s) consistent), exit 0
git diff --check                                        # clean
```

Focused brand tests (worktree, main venv):

```bash
PYTHONPATH=/tmp/opencode/wt-p8-d1718:/tmp/opencode/wt-p8-d1718/qubit_runtime \
  python -m pytest -q tests/test_branding_leaks9u.py tests/test_cli_branding_leaks9u.py \
  tests/test_rebrand_identifier_map.py
# 20 passed, 1 failed
```

The single failure is pre-existing and unrelated:

```
tests/test_cli_branding_leaks9u.py::test_update_and_tools_prints_are_debranded
assert '_UPSTREAM_ADD_CMD' not in update_cmd_git.py
```

`qubit_runtime/qubit_cli/update_cmd_git.py` and all test files are untouched by this lane
(`git diff --name-only` contains neither), so the assertion fails identically on the base.

```bash
cd qubit_runtime && PYTHONPATH=$PWD python -m pytest -q tests/test_rebrand_symbols9u.py
# 31 passed
```

No test was weakened, skipped, or xfailed. No service was restarted. Nothing was pushed.

## Deviations / honest limits

1. `docs/PHASE8-SIGNOFF-PACK.md` says D17/D18 are "not this lane"; the task explicitly
   authorizes this cleanup, so only the pack's narrow "clean where factually wrong" option was
   taken.
2. "Do not change paths" was read as **do not change executable path constants, regexes,
   identifiers, or contract literals**. Prose that asserted the *canonical* home was corrected;
   every legacy-fallback/container/remote path stayed literal.
3. The generic word `Hermes` still appears in many production comments where it is an
   identifier fragment (`HermesCLI`, `HermesACPAgent`), a model name, a literal path/binary, or
   documentation of upstream behaviour. Those were deliberately conserved rather than
   mass-replaced.

## Files changed (148, excluding this report)

```text
qubit_runtime/acp_adapter/entry.py
qubit_runtime/acp_adapter/session.py
qubit_runtime/agent/agent_runtime_helpers.py
qubit_runtime/agent/anthropic_adapter.py
qubit_runtime/agent/anthropic_credentials.py
qubit_runtime/agent/auxiliary_call_outcome.py
qubit_runtime/agent/auxiliary_client.py
qubit_runtime/agent/auxiliary_local_runtime.py
qubit_runtime/agent/browser_provider.py
qubit_runtime/agent/client_lifecycle.py
qubit_runtime/agent/context_references.py
qubit_runtime/agent/conversation_loop.py
qubit_runtime/agent/credential_pool.py
qubit_runtime/agent/credential_sources.py
qubit_runtime/agent/curator.py
qubit_runtime/agent/curator_backup.py
qubit_runtime/agent/i18n.py
qubit_runtime/agent/image_gen_provider.py
qubit_runtime/agent/image_token_cost.py
qubit_runtime/agent/lsp/install.py
qubit_runtime/agent/lsp/servers.py
qubit_runtime/agent/lsp/workspace.py
qubit_runtime/agent/memory_provider.py
qubit_runtime/agent/message_metadata.py
qubit_runtime/agent/models_dev.py
qubit_runtime/agent/runtime_cwd.py
qubit_runtime/agent/secret_sources/__init__.py
qubit_runtime/agent/secret_sources/_cache.py
qubit_runtime/agent/secret_sources/base.py
qubit_runtime/agent/secret_sources/bitwarden.py
qubit_runtime/agent/secret_sources/registry.py
qubit_runtime/agent/skill_commands.py
qubit_runtime/agent/system_prompt.py
qubit_runtime/agent/tool_dispatch_helpers.py
qubit_runtime/agent/transports/codex.py
qubit_runtime/agent/transports/codex_app_server_session.py
qubit_runtime/agent/transports/codex_event_projector.py
qubit_runtime/agent/video_gen_provider.py
qubit_runtime/agent/web_search_provider.py
qubit_runtime/cli.py
qubit_runtime/gateway/cgroup_cleanup.py
qubit_runtime/gateway/host_attach.py
qubit_runtime/gateway/platforms/api_server.py
qubit_runtime/gateway/platforms/api_server_openai_routes.py
qubit_runtime/gateway/run_turn_persistence.py
qubit_runtime/gateway/session_ingress_media.py
qubit_runtime/gateway/session_managed_worker.py
qubit_runtime/gateway/session_results.py
qubit_runtime/gateway/session_shell.py
qubit_runtime/gateway/status.py
qubit_runtime/plugins/dashboard_auth/self_hosted/__init__.py
qubit_runtime/plugins/model-providers/copilot-acp/__init__.py
qubit_runtime/plugins/platforms/feishu/feishu_comment_rules.py
qubit_runtime/plugins/platforms/google_chat/adapter.py
qubit_runtime/plugins/platforms/google_chat/oauth.py
qubit_runtime/plugins/platforms/line/adapter.py
qubit_runtime/plugins/platforms/photon/auth.py
qubit_runtime/plugins/platforms/photon/cli.py
qubit_runtime/plugins/platforms/simplex/adapter.py
qubit_runtime/plugins/platforms/telegram/adapter.py
qubit_runtime/pm/paths.py
qubit_runtime/providers/base.py
qubit_runtime/qubit_cli/auth.py
qubit_runtime/qubit_cli/auth_codex.py
qubit_runtime/qubit_cli/auth_minimax.py
qubit_runtime/qubit_cli/auth_oauth_grants.py
qubit_runtime/qubit_cli/auth_spotify.py
qubit_runtime/qubit_cli/backup.py
qubit_runtime/qubit_cli/browser_connect.py
qubit_runtime/qubit_cli/cli_commands_mixin.py
qubit_runtime/qubit_cli/cli_config_load.py
qubit_runtime/qubit_cli/cli_info_mixin.py
qubit_runtime/qubit_cli/cli_terminal_mixin.py
qubit_runtime/qubit_cli/cli_tui_mixin.py
qubit_runtime/qubit_cli/config.py
qubit_runtime/qubit_cli/config_defaults.py
qubit_runtime/qubit_cli/doctor.py
qubit_runtime/qubit_cli/doctor_config.py
qubit_runtime/qubit_cli/dump.py
qubit_runtime/qubit_cli/env_loader.py
qubit_runtime/qubit_cli/gateway_runtime.py
qubit_runtime/qubit_cli/gateway_runtime_service.py
qubit_runtime/qubit_cli/gateway_status_notes.py
qubit_runtime/qubit_cli/main.py
qubit_runtime/qubit_cli/main_install_repair.py
qubit_runtime/qubit_cli/main_provider_setup.py
qubit_runtime/qubit_cli/main_tui_launch.py
qubit_runtime/qubit_cli/mcp_catalog.py
qubit_runtime/qubit_cli/mcp_config.py
qubit_runtime/qubit_cli/model_catalog.py
qubit_runtime/qubit_cli/models.py
qubit_runtime/qubit_cli/models_cache_lock.py
qubit_runtime/qubit_cli/onboarding_run.py
qubit_runtime/qubit_cli/plugin_host.py
qubit_runtime/qubit_cli/plugin_isolation.py
qubit_runtime/qubit_cli/plugin_isolation_audit.py
qubit_runtime/qubit_cli/plugin_validate_core_override.py
qubit_runtime/qubit_cli/plugins.py
qubit_runtime/qubit_cli/plugins_cmd.py
qubit_runtime/qubit_cli/plugins_cmd_remove.py
qubit_runtime/qubit_cli/plugins_cmd_update.py
qubit_runtime/qubit_cli/plugins_discovery.py
qubit_runtime/qubit_cli/profile_model_seed.py
qubit_runtime/qubit_cli/profiles.py
qubit_runtime/qubit_cli/security_audit.py
qubit_runtime/qubit_cli/sessions_cmd.py
qubit_runtime/qubit_cli/setup.py
qubit_runtime/qubit_cli/setup_terminal.py
qubit_runtime/qubit_cli/skills_hub.py
qubit_runtime/qubit_cli/subcommands/peer.py
qubit_runtime/qubit_cli/tools_config_post_setup.py
qubit_runtime/qubit_cli/uninstall.py
qubit_runtime/qubit_cli/update_cmd_maint.py
qubit_runtime/qubit_cli/update_stage.py
qubit_runtime/qubit_cli/web_routers/dashboard_ui.py
qubit_runtime/qubit_cli/web_routers/models.py
qubit_runtime/qubit_cli/web_routers/oauth.py
qubit_runtime/qubit_cli/web_server_cron.py
qubit_runtime/qubit_cli/web_server_dashboard.py
qubit_runtime/qubit_cli/web_server_gateway.py
qubit_runtime/qubit_cli/web_server_oauth.py
qubit_runtime/qubit_time.py
qubit_runtime/skills/productivity/box/references/oauth-setup.md
qubit_runtime/skills/productivity/google-workspace/SKILL.md
qubit_runtime/tools/approval_detection.py
qubit_runtime/tools/browser_camofox.py
qubit_runtime/tools/checkpoint_manager.py
qubit_runtime/tools/delegate_tool_child_run.py
qubit_runtime/tools/file_operations_search.py
qubit_runtime/tools/mcp_tool.py
qubit_runtime/tools/mcp_tool_transport.py
qubit_runtime/tools/process_registry_checkpoint.py
qubit_runtime/tools/skill_ledger.py
qubit_runtime/tools/skill_manager_tool.py
qubit_runtime/tools/skill_usage.py
qubit_runtime/tools/skills_hub_official.py
qubit_runtime/tools/skills_sync.py
qubit_runtime/tools/skills_sync_client.py
qubit_runtime/tools/skills_sync_client_org.py
qubit_runtime/tools/skills_sync_client_wire.py
qubit_runtime/tools/skills_tool.py
qubit_runtime/tools/spill_safety.py
qubit_runtime/tools/website_policy.py
qubit_runtime/tools/xai_http.py
qubit_runtime/tui_gateway/methods_bot_relay.py
qubit_runtime/tui_gateway/methods_complete.py
qubit_runtime/tui_gateway/methods_tools.py
qubit_runtime/tui_gateway/slash_worker.py
```
