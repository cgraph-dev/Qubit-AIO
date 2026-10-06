# Migration vs Adapter — which mechanism handles which content

Two independent mechanisms exist to make Qubit independent of hermes:

- **Migration** (`voicebridge/qubit_migration.py`) — one-shot, copies/ports
  *past* content that already sits on disk under `~/.hermes` into the Qubit
  runtime home (`_import_assets` `qubit_migration.py:1011-1171`).
- **Adapter** (`voicebridge/agent_compat/framework.py` + `adapters/hermes.py`,
  wired into `qubit_runtime/qubit_cli/plugins_discovery.py:43-96`) — runtime
  import/env/symbol aliasing so *future* content authored against hermes naming
  resolves against Qubit's renamed modules.

They solve different problems and **neither is a superset of the other**.

## Decision table

| Content kind | PAST content (on disk under `~/.hermes`) | FUTURE content (published later, hermes-named) | Where neither works |
|---|---|---|---|
| **Plugin** (files) | **Migration** — `_copy_missing(home/"plugins", dest_home/"plugins")` `qubit_migration.py:1052` copies the tree. | **Adapter** — `adapters/hermes.py:43-73` aliases `hermes_cli`→`qubit_cli` etc.; `plugins_discovery.py:58-96` widens entry-point groups so a hermes-named distribution is discovered and its `import hermes_*` resolves. | Plugin **enable/disabled state**: migration reads `plugins/<name>/state.json` but writes nothing (`_import_plugin_state` returns `UNSUPPORTED`, `qubit_migration.py:800-821`); the adapter only aliases *imports*, it does not carry state. A disabled plugin migrates enabled. |
| **Skill** (files) | **Migration** — `_copy_missing(home/"skills", dest_home/"skills")` `qubit_migration.py:1052` copies skill dirs incl. `SKILL.md`. | **Adapter** — the import aliaser applies to any plugin code a future skill ships; but skill *content* needs no alias (it is markdown). Effectively **migration-style copy** at author time, not adapter. | Skill **enabled/disabled state** is asymmetric: migration only unions source `skills.disabled` names (`_import_skill_state` `qubit_migration.py:658-764`); it cannot mark a skill *enabled*, and does not reconcile stale disabled entries (§3.2 of `MIGRATION_COVERAGE.md`). Future skills arriving already-disabled have no path. |
| **MCP server** | **Migration** — `_import_mcp_servers` `qubit_migration.py:887-970` reads `config.yaml:mcp_servers` + `mcp_servers.json` and upserts into the canonical registry with `env:` refs only. | **Neither.** The adapter aliases Python imports and env-var names (`HERMES_HOME`→`QUBIT_HOME`, `adapters/hermes.py:70`); it does **not** ingest an MCP server definition. A future hermes-published MCP server config has no adapter path. | **Future MCP**: fully unhandled. No discovery of `mcp_servers` in a foreign distribution, no import-on-publish. Also `safe=True` migration runs skip MCP entirely (`qubit_migration.py:1083-1089`). |

## Notes on the boundary

- **Past content = migration's job, and only for what it enumerates.** It does
  not copy general `config.yaml` keys, `.env` values, `auth.json`, or ~30 other
  directories (see GAPS in `MIGRATION_COVERAGE.md`). Past content outside that
  list is handled by *neither* mechanism.
- **Future content = adapter's job, and only for import/env/symbol resolution.**
  The adapter is pure name-mapping data (`adapters/hermes.py:1-19`); it carries
  no data migration, no state, and no MCP ingestion. A future plugin whose code
  uses only aliased names loads; one that depends on a foreign config key or a
  foreign data path does not.
- **The two mechanisms do not share a code path.** Migration never calls
  `agent_compat.install()`; the adapter never copies files. There is no single
  entry point that handles both past and future content for a given kind.

## Bottom line

| Kind | Past | Future |
|---|---|---|
| Plugin | Migration copies files (state lost) | Adapter aliases imports (state not carried) |
| Skill | Migration copies files + disables-union | Adapter (imports only) / copy at author time |
| MCP server | Migration imports (unsafe mode only) | **Nothing** |
