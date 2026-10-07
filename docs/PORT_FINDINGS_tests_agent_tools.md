# Product bugs exposed by the tests/agent + tests/tools port (branch n3)

Base: `f818a75e` (this worktree's HEAD; sibling of the `5a734b66` the task named — both are
children of the same n3 lineage, the named SHA is not an ancestor here).
Measure rev: `ee8dd6c886`. Scope: the 51 short-port files under `tests/agent/` + `tests/tools/`.

## Outcome
- 51 test files ported (were entirely absent — the audit's "symbols" are whole missing files).
- 30/51 now fully GREEN: **953 passing tests**, 0 failing.
- 21 remain red, classified below.

## Source-of-truth correction (important)
The correct port source is **`qubit_runtime/tests/`** (the in-tree vendored copy: already
hermes->qubit renamed AND tracking a NEWER upstream than the milestone), NOT
`git show ee8dd6c886:<path>`. Copy vendored files **verbatim** — re-running the rename
transformer over them corrupts legitimate `hermes_` tokens (e.g. the codex wire-alias
prefix is intentionally `hermes_`). The milestone body is only correct for a handful of
modules where our production happens to match it (context_compressor, reasoning_only_stop,
chat_completions_reasoning_details_replay, run_agent_codex_responses, tool_result_storage,
web_managed_routing, web_free_fast_search, auxiliary_relay, code_execution, modal_bulk_upload).

## PROVEN PRODUCT BUGS (do not fix here; other agents own product)

1. **`tools/bot_mode_dm.py:221` — half-finished rebrand, NameError-class (handoff §3a).**
   Calls `record_feature_used("bot_mode", hermes_home=home)` but the callee signature is now
   `record_feature_used(feature, *, qubit_home=None)`
   (`qubit_cli/observability/shared_metrics_signals.py:129`). → `TypeError` at runtime,
   swallowed into `_err("Bot Mode gate check failed")`. Bot Mode gate check is dead.
   Exposed by `tests/tools/test_bot_relay.py` (4 failures).

2. **`agent/transports/codex.py:88` — wire alias prefix never rebranded.**
   `_RESERVED_TOOL_ALIAS_PREFIX = "hermes_"` but the current in-tree test expects `qubit_`
   aliases (`tests/agent/test_auxiliary_client.py` at the milestone revision expects
   `qubit_{tool}`). NOTE the vendored/current test expects `hermes_`, so this is a
   rebrand-asymmetry: the constant and the newest test disagree. Needs a decision
   (rebrand alias vs keep `hermes_`), not a blind rename.

3. **`gateway/session_context.py:41-47` — session env NAMES still `HERMES_SESSION_*`.**
   `_VAR_MAP` keys are `HERMES_SESSION_KEY/ID/PROFILE`, `HERMES_UI_SESSION_ID`, etc., while the
   runtime/test env vars are `QUBIT_SESSION_*`. Consequence: `tools/bot_relay.py`'s
   `delivery_env()` strips the WRONG names and fails to strip the real `QUBIT_SESSION_KEY`, so a
   delivery child inherits the parent's session identity (the exact "nested recipient never
   resumes" failure the code comment warns about). Silent — string names, so no ImportError.
   Exposed by `tests/tools/test_bot_relay.py::...delivery_env_*`.

4. **`tools/mcp_liveness.py:160,171` — references `decl.required_gpu`, which does not exist.**
   `qubit_platform/declaration.py`'s `Declaration`/`RequiresSpec` have only `app`/`min_version`
   (no `gpu`), yet `mcp_liveness` has an `unsupported_gpu` state and `gpu_label` calls. A
   partially-ported feature → `AttributeError` in the unavailable-details path.
   Exposed by `tests/tools/test_mcp_liveness.py` (4 failures).

5. **`agent/relay_runtime.py:324-325` — stale `Hermes` strings in the FOREIGN-conflict path.**
   "outside Hermes native ownership; ... disabling Hermes-managed Relay middleware" — the
   sibling `_configured_plugin_inputs` path was rebranded but this one was not.

6. **`agent/auxiliary_client.py:3019-3021` — reads `HERMES_CODEX_BASE_URL`; test sets `QUBIT_CODEX_BASE_URL`.**
   `_profile_codex_base_url()` → `_scoped_key_env("HERMES_CODEX_BASE_URL")`. Rebrand asymmetry
   (env-var compat surface). Exposed by `tests/agent/test_auxiliary_client.py` (2 failures at
   lines 724/741): the override is ignored and the real endpoint is used.

## INFRASTRUCTURE-BLOCKED (not product; needs a shared-conftest graft)
- `tests/tools/test_local_env_blocklist.py` (31 errors): the fixture chain needs
  `tests/tools/_child_env_fixtures.py`, which reads `tools.env_passthrough._allowed_env_vars_var`
  (a ContextVar). Our `tools/env_passthrough.py:32` intentionally replaced it with a plain
  `_allowed_env_vars` set (see its comment) — so this is an intentional divergence; the upstream
  fixture is stale. The remaining 3 assertion failures are PYTHONPATH-strip layout issues in a
  `/tmp` worktree.
- `tests/tools/test_kanban_tools.py` (32 errors): kanban tests need upstream's
  `tests/conftest.py` `_hermetic_environment` fixture (sets `HERMES_KANBAN_HOME`). Our repo's
  `tests/conftest.py` is a different, Qubit-specific file. `sqlite3.OperationalError: unable to
  open database file` (fenced RO connect to a not-yet-created DB).
- `tests/agent/test_auxiliary_relay.py`: imports `hermes_cli` (rename miss in the MILESTONE body;
  the vendored body is clean but needs a fixture).

## Marker infrastructure added (test tree, kept)
`tests/conftest.py` now registers `platforms`, `require_symlinks`, `live_system_guard_bypass`
and applies host-OS gating via `tests/_fixtures/platform_gating.py` (ported from upstream). This
alone unblocked 15 files that were erroring on `filterwarnings=error`.
