# Migration Coverage Audit — `voicebridge/qubit_migration.py`

**Scope.** Read-only audit of `voicebridge/qubit_migration.py` (1917 lines, commit
`1b7b3c8`) plus the runtime-home resolution in `voicebridge/qubit_runtime.py`.
Every claim below is cited to `file:line` and was established by reading the
**body** of the function, not its name.

**Method note.** The deliverable is the distinction between:
- **MIGRATED** — the item's *content* is copied or converted into the Qubit home;
- **STATE-ONLY** — only a flag/enablement/list membership is recorded; the
  underlying files are **not** copied;
- **NOT HANDLED** — nothing touches it (neither content nor state).

**Dead-code check (requested).** `_copy_missing()` is **NOT dead code**. It is
defined at `qubit_migration.py:529` and called from **four** sites, all inside
`_import_assets()`:
- `qubit_migration.py:1052` — skills, plugins, hooks, projects, attachments
- `qubit_migration.py:1059` — `_SAFE_STATE_DIRS` (memories, checkpoints)
- `qubit_migration.py:1102` — receipts / audit stores
- `qubit_migration.py:1126` — `cron/output`

A repo-wide grep (`grep -rn '_copy_missing' --include='*.py'`) returns only these
five lines (definition + four calls). It is live.

---

## 1. Destination home resolution

`_import_assets()` writes into the **Qubit runtime home**, not `~/.hermes`:

- `qubit_migration.py:1025` — `dest_home = _qubit_home(cfg)`
- `qubit_migration.py:382-386` — `_qubit_home()` → `qubit_runtime.runtime_home_for_cfg(cfg)`
- `qubit_runtime.py:254-291` — `runtime_home_for_cfg()` resolves the home as
  `<cfg.state_dir()>/runtime`, else `default_home()` (`qubit_runtime.py:185-195`),
  i.e. `${XDG_STATE_HOME:-~/.local/state}/qubit-voicebridge/runtime`. An explicit
  non-external `QUBIT_HOME` wins (`qubit_runtime.py:270-277`). The external
  `~/.hermes` is **never** the destination (`qubit_runtime.py:216-223`).

**Source home** is `~/.hermes` by default, overridable by
`QUBIT_HERMES_IMPORT_HOME` or `migration.hermes_home`
(`qubit_migration.py:76-88`).

---

## 2. Coverage table

Status legend: **MIGRATED** = content copied/converted · **STATE-ONLY** = flag/list
only · **NOT HANDLED** = untouched · **EXCLUDED** = deliberately not migrated,
recorded as a typed disposition.

| # | User-state item | Function (file:line) | Status | Evidence / exact behaviour |
|---|---|---|---|---|
| 1 | **Sessions** (`state.db:sessions`) | `_source_sessions` `:293-319`; applied at `apply` `:1309-1337` via `st.import_session` `:1324` | **MIGRATED** | Reads every column `select * from sessions` `:299`, carries ids/timestamps, imports through `QubitSessionStore.import_session(..., overwrite=False)` `:1324`. Preserves session id, insertion order. |
| 2 | **Messages** (`state.db:messages`) | `_source_messages` `:322-367`; applied `:1310`, `:1316-1325` | **MIGRATED** | Selects `_MESSAGE_COLUMNS` present in source schema `:328`, keeps all roles (user/assistant/system/tool) `:343`, carries `message_id` `:364-365`. Rows with unknown role or empty non-tool content counted in `skipped_roles` `:344/:351`. |
| 3 | **System prompts** (referenced `system_prompts` rows) | `_source_system_prompts` `:276-290`; applied `:1319-1321` | **MIGRATED** | Copies the prompt rows whose `hash` is referenced by a migrated session so a mirrored hash stays valid. |
| 4 | **Cron jobs** (`cron/jobs.json`) | `_merge_cron` `:551-588`; called `:1148` | **MIGRATED** (merge by id) | Parses source `cron/jobs.json` `:557`, appends jobs whose `id` is absent from destination `:572-582`, atomic write `:585-587`. Additive only; existing dest jobs never overwritten. |
| 5 | **Cron executions** (`cron/executions.db`) | `:1115-1125` (`_snapshot_sqlite`) | **MIGRATED** (snapshot) | WAL-safe SQLite snapshot when destination absent `:1117-1125`. |
| 6 | **Cron output** (`cron/output/`) | `_copy_missing` `:1126` | **MIGRATED** (files) | Skip-existing file copy. |
| 7 | **Model profile / credentials** (`config.yaml` `model`, `fallback_providers`, `auxiliary`, `stt/tts/voice/speech/realtime`) | `_import_model_profile` `:407-526`; called `:1047` (skipped in `safe` `:1042-1045`) | **MIGRATED as refs** | Reads `config.yaml` `:415`, builds `R.ModelProfile`s for model + fallbacks + auxiliary + speech sections `:471-486`, upserts via `R.upsert_model_profile` `:508`. **Only `env:NAME` key references** (`_key_ref` `:441-448`, value matched against `.env` `:433-436`); the raw `api_key` is never written. |
| 8 | **Provider credential store** (`auth.json`) | `:1131-1142` | **EXCLUDED** (typed) | Never copied. Emits `CREDENTIALS_NOT_MIGRATED` with re-enrollment pointer `:1132-1139`. |
| 9 | **Skills — the skill FILES** (`skills/**` incl. `SKILL.md`) | `_copy_missing` `:1052` (`home/"skills"` → `dest_home/"skills"`) | **MIGRATED** (files) | Whole `skills/` tree copied, skip-existing, excluding `__pycache__`/`.git`/`*.lock`/`*.tmp`/`*.pyc` (`_copy_missing` `:534-547`). The skill **content** is ported. |
| 10 | **Skills — enabled/disabled state** (`config.yaml` `skills.disabled` + `skills.platform_disabled`) | `_import_skill_state` `:658-764`; helpers `_source_skill_state` `:633-655`, `_normalize_skill_names` `:594-619`, `_platform_disabled_names` `:622-630`; called `:1071` | **STATE-ONLY** | Reads source `skills.disabled`/`platform_disabled` `:651-653` and **unions the disabled NAMES** into the destination `<runtime_home>/config.yaml` `skills.disabled` `:752-759`. It records which skills are **disabled** — it copies **no** skill files (that is row 9). It never enables; strictly additive union `:743-752`. |
| 11 | **Plugins — the plugin DIRECTORIES** (`plugins/**`) | `_copy_missing` `:1052` (`home/"plugins"` → `dest_home/"plugins"`) | **MIGRATED** (files) | Same skip-existing tree copy as skills. Plugin **content** is ported. |
| 12 | **Plugins — enabled/disabled state** (`plugins/<name>/state.json`) | `_source_plugin_states` `:767-797`; `_import_plugin_state` `:800-821`; called `:1075` | **STATE-ONLY → actually NOT RECORDED** | `_source_plugin_states` **reads** the source `state.json` `:783-795` but the importer returns `state="UNSUPPORTED"`, `recorded: []` `:811-821`. The source state is read into the report only; **nothing** is written to any destination plugin registry (no public API exists — see §3.2). |
| 13 | **Hooks** (`hooks/**`) | `_copy_missing` `:1052` | **MIGRATED** (files) | Skip-existing tree copy. |
| 14 | **Projects** (`projects/**` dir) | `_copy_missing` `:1052` | **MIGRATED** (files) | Skip-existing tree copy of the directory. |
| 15 | **Projects DB** (`projects.db`) | `:1115-1125` | **MIGRATED** (snapshot) | WAL-safe snapshot when dest absent. |
| 16 | **Attachments** (`attachments/**`) | `_copy_missing` `:1052` | **MIGRATED** (files) | Skip-existing tree copy. |
| 17 | **Shared state DB** (`shared-state.db`) | `_SOURCE_DBS` `:43`; `_snapshot_sqlite` at `apply` `:1281-1283` (backup only), `:1115-1125` (dest snapshot) | **MIGRATED** (snapshot) | Included in `_SOURCE_DBS` and snapshotted to destination `:1115-1125`. |
| 18 | **Kanban — DB** (`kanban.db`) | `:1115-1125` | **MIGRATED** (snapshot) | Snapshot when destination absent. Rows counted read-only in `_state_item_report` `:147-159`. |
| 19 | **Kanban — dir** (`kanban/`) | — | **NOT HANDLED** | `kanban/` directory is not in any copy list; only `kanban.db` is snapshotted. (The live dir currently holds only locks, but nothing copies it.) |
| 20 | **Memory** (`memories/`, e.g. `MEMORY.md`, `USER.md`) | `_copy_missing` `:1058-1059` via `_SAFE_STATE_DIRS` `:52` | **MIGRATED** (files) | `_SAFE_STATE_DIRS = ("memories", "checkpoints")` `:52`. Skip-existing copy. |
| 21 | **Checkpoints** (`checkpoints/`) | `_copy_missing` `:1058-1059` via `_SAFE_STATE_DIRS` `:52` | **MIGRATED** (files) | Same as memories. |
| 22 | **Channel directory** (`channel_directory.json`) | `:1060-1066` via `_SAFE_STATE_FILES` `:53` | **MIGRATED** (file) | Single non-secret file, copied if absent `:1063-1066`. |
| 23 | **MCP server config** (`config.yaml:mcp_servers` + `mcp_servers.json`) | `_source_mcp_servers` `:824-861`; `_import_mcp_servers` `:887-970`; called `:1091` (skipped in `safe` `:1083-1089`) | **MIGRATED as refs** | Reads both sources `:835-861`, converts env values to **references only** via `_env_ref_for` `:864-884`, upserts into canonical registry `MS.upsert`/`MS.set_enabled` `:955-958`. Unresolvable env values are **dropped**, never copied `:950-951`, `:963-967`. |
| 24 | **`.env` secrets** | `_read_env_map` `:389-404` (reference matching only); `inspect` `:218-232` | **NOT COPIED** (by design) | `.env` values are read into memory only to *match* them to `env:NAME` refs (`:433-436`, `:445-447`, `:881-883`). The file itself is never copied; `inspect` reports **names** only `:232`. |
| 25 | **`config.yaml` keys (general)** | Only specific keys consumed: `model`/`fallback_providers`/`auxiliary`/`stt`/`tts`/`voice`/`speech`/`realtime` (`:471-486`), `skills.disabled` `:651`, `mcp_servers` `:843`. | **PARTIAL / NOT HANDLED** | The source `config.yaml` **file is not copied**. All other top-level keys (`agent`, `approvals`, `tools`, `telegram`, `browser`, `security`, `logging`, `goals`, `privacy`, etc.) are read only for the read-only inventory (`inspect` `:211-215` reports `top_level_keys[:60]`) and otherwise ignored. |
| 26 | **CLI history** (`.hermes_history`) | `import_cli_history_sanitized` `:1707-1862`; `sanitize_cli_history` `:1633-1663`; separate CLI command `history-import` (cli.py:5199) | **MIGRATED (sanitized)** — but **NOT** part of `apply()` | Secret-shaped values redacted `:1596-1630`; fail-closed gate `:1807-1813`; idempotent de-dup `:1789-1794`. **Not invoked by `apply()`/`_import_assets()`** — must be run as its own command. `_STATE_ITEM_FILES` lists `.hermes_history` for inventory `:58`; the comment `:49-51` explicitly excludes it from the bulk copy. |
| 27 | **Receipts / audit** (`receipts/`, `audit/`, `spawn-ledger.json`, `receipts.json`, `audit.jsonl`) | `:1097-1109` | **MIGRATED** (files, when present) | Skip-existing copy; if absent it records that Qubit-owned `actions.json` was not fabricated `:1152-1158`. |
| 28 | **Pairing** (`pairing/`) | inventoried `_STATE_ITEM_DIRS` `:56`; `_state_item_report` `:133-135` | **EXCLUDED** | Counted read-only; comment `:49-51` states pairing is Qubit-owned live state and is never imported. No copy path exists. |
| 29 | **Gateway state** (`gateway_state.json`) | inventoried `_STATE_ITEM_FILES` `:57` | **EXCLUDED** | Counted read-only; same rationale `:49-51`. |
| 30 | **Request dumps** (`sessions/request_dump_*.json`) | — | **EXCLUDED / NOT HANDLED** | Comment `:49-51` excludes request dumps (raw prompts). No handler. `sessions/state.db` is in `_SOURCE_DBS` `:43` but the dumps are not. |

---

## 3. GAPS — what a real `~/.hermes` contains that migration does NOT handle

Each gap names the function that *would* have to have done it and shows it does
not exist or does not do it. Evidence is from the live `~/.hermes` tree (43
entries) and the code.

### 3.1 Plugin enabled/disabled state is read but never recorded
`_source_plugin_states` (`:767-797`) reads `plugins/<name>/state.json` and
derives an `ENABLED`/`DISABLED`/`UNKNOWN` string, but `_import_plugin_state`
(`:800-821`) hard-returns `state="UNSUPPORTED"` with `"recorded": []`. **No write
happens.** There is no function that persists a plugin name + enabled flag to the
destination. The docstring `:801-809` states the reason: `plugin_runtime` exposes
no public enable/disable API and inventing a second store is forbidden. So a
hermes install with a disabled plugin migrates the plugin *files* (row 11) but
**loses the fact it was disabled**.

### 3.2 Skill enable/disable is asymmetric — only *disables* are carried
`_import_skill_state` (`:658-764`) unions only names found in source
`skills.disabled`/`platform_disabled` (`:651-653`, `:694`). An **explicitly
enabled** skill (or a source with an empty disabled-list) conveys no positive
enablement state. There is no function reading an "enabled" list, because the
hermes config model has none — but the migration also does not reconcile the
destination's *existing* disabled-list against the source, so a skill disabled
in Qubit but enabled in hermes stays disabled. `added` is a pure union `:743-752`;
nothing removes a stale disabled entry.

### 3.3 `config.yaml` general keys are not migrated
Only 9 key groups are consumed (see row 25). The live `~/.hermes/config.yaml`
has ~70 top-level keys including `agent`, `approvals`, `tools`, `toolsets`,
`platforms`, `platform_toolsets`, `known_plugin_toolsets`, `telegram`, `slack`,
`matrix`, `mattermost`, `discord`, `browser`, `security`, `privacy`, `logging`,
`goals`, `command_allowlist`, `network`, `checkpoints`, `memory`, `session_reset`,
`update*`, `image_gen`, `bedrock`, `code_execution`, `human_delay`,
`prompt_caching`, `compression`, `context`, `timezone`, `display`, `dashboard`,
`lsp`, `curator`, `delegation`, `gateway`, `secrets`, `onboarding`. No function
copies the file or maps these keys. `_import_assets` only *backs up* the
destination `config.yaml` (`:1039`) and writes the `skills.disabled` union into
it (`:752-759`).

### 3.4 `.env` is never copied (values intentionally dropped)
`_read_env_map` (`:389-404`) loads `.env` into memory purely for reference
matching; there is **no** counterpart that writes an `.env` (partial or whole)
into the Qubit home. The live `~/.hermes/.env` is 24 KB. A destination that relies
on `.env` entries not already referenced by a migrated model/MCP record will not
have them.

### 3.5 Plugin *state payloads* and plugin-data are not migrated
`plugins/<name>/state.json` holds arbitrary per-plugin state (the live
`plugins/hermes-achievements/`) — only the enabled/disabled boolean is inspected
(`:789-795`) and even that is not written. `plugin-data/` and `plugins/**/scan_*.json`
are not in any copy list. `_copy_missing` at `:1052` copies `plugins/` wholesale,
so plugin *files* do move — but the semantic state derived from them does not.

### 3.6 Many hermes directories have no handler at all
No function references these (grep of `qubit_migration.py` for each name returns
no copy path): `desktop/`, `browser_profiles/`, `audio_cache/`, `image_cache/`,
`images/`, `pastes/`, `plans/`, `pending_messages/`, `platforms/`, `sandboxes/`,
`scripts/`, `tools/`, `installs/`, `lsp/`, `node/`, `bin/`, `state/`,
`terminal-sessions/`, `state-snapshots/`, `source-checks/`, `plugin-update-checks/`,
`serve_restart_pending/`, `rankvalut-research/`, `shared/`, `backups/`, `cache/`,
`logs/`, `runtime/`, `hermes-agent/`.
Some are caches (legitimately skippable) but others are real user state — notably
`plans/`, `pending_messages/`, `platforms/`, `tools/`, `state/` — with no
disposition recorded anywhere, not even a typed skip.

### 3.7 `kanban/` directory not copied
Only `kanban.db` is snapshotted (`:1115-1125`). The live `~/.hermes/kanban/` dir
(currently only a lock) has no copy path.

### 3.8 Auxiliary config files not migrated
`SOUL.md`, `.skills_prompt_snapshot.json`, `install_id`, `models_dev_cache.json`,
`provider_models_cache.json`, `processes.json`, `desktop-build-stamp.json`,
`tui-theme-boot.json`, `gateway-starts.log` — none are referenced by the migration
code. `SOUL.md` in particular is user persona state, not a cache.

### 3.9 `hermes-herald.db` (and other stray DBs) not migrated
The live home has `hermes-herald.db` (a real SQLite store). It is not in
`_SOURCE_DBS` (`:43`) nor any snapshot list (`:1115-1125`). No handler exists.

### 3.10 CLI-history import is not wired into `apply()`
`import_cli_history_sanitized` (`:1707`) is a **separate** CLI command
(`history-import`, cli.py:5199) and is never called from `apply()`
(`:1249-1383`) or `_import_assets()` (`:1011-1171`). A user running only
`legacy-migrate --apply` gets no history. The `.hermes_history` file is listed in
`_STATE_ITEM_FILES` (`:58`) for *inventory* only.

### 3.11 `safe=True` mode skips model profile and MCP even when it could convert them
`_import_assets(safe=True)` (`:1041-1045`, `:1083-1089`) skips the model profile
and MCP registry entirely (owner decision #9). This is intentional, but it means
a `safe` run migrates **zero** provider configuration and **zero** MCP servers —
only files plus skill-disabled names.

---

## 4. Summary counts

- **MIGRATED (content):** sessions, messages, system prompts, cron jobs,
  cron executions/output, model profile (refs), MCP servers (refs), skill files,
  plugin files, hooks, projects (+DB), attachments, shared-state DB, kanban DB,
  memories, checkpoints, channel_directory.json, receipts/audit, CLI history
  (separate command only).
- **STATE-ONLY:** skill enabled/disabled (disabled names only, §3.2);
  plugin enabled/disabled (read, never written, §3.1).
- **NOT HANDLED / EXCLUDED:** `.env` values, `auth.json`, general `config.yaml`
  keys, pairing, gateway state, request dumps, kanban dir, and ~30 other
  directories (§3.6), SOUL.md and stray DBs.
