# REPORT — port/chunk-2 (agent/ absent production files)

Branch: `port/chunk-2` · base `9c2f782e1` · worktree `/tmp/port-2`
Upstream pin: `517b5e10febd619ce30bb22580e29b160266eb43` (`origin/main`)
Source root → dest: `agent/X.py` → `qubit_runtime/agent/X.py` (verified against existing tree)

## Result

| status | count |
|---|---|
| ported | 8 |
| dep-missing (ported but unrunnable) | 3 |
| shim-skipped | 0 |
| imports clean (`import <mod>` succeeds) | 10 / 11 |

**No shims in this chunk** — all 11 upstream files are substantive implementation
modules (the smallest, `context_compressor_retry.py`, is 29 lines of real parser
logic, not a re-export stub).

## Commits (6, coherent units)

- `4babf5865` context_compressor_retry / tail / telemetry
- `785288300` credential_pool_timestamps + gemini_long_decode
- `49e0b3689` model_metadata_cache
- `b5f94f373` onboarding_first_tasks + onboarding_flow
- `4d4cdbc91` onboarding_facts + onboarding_record (dep-missing)
- `dff73b0bb` managed_worker (dep-missing)

## Ported, clean (8)

| upstream path | lines | deps |
|---|---|---|
| agent/context_compressor_retry.py | 29 | none |
| agent/context_compressor_tail.py | 85 | agent.context_compressor (present) |
| agent/context_compressor_telemetry.py | 139 | agent.model_metadata (present) |
| agent/credential_pool_timestamps.py | 31 | none |
| agent/gemini_long_decode.py | 102 | none |
| agent/model_metadata_cache.py | 72 | agent.model_metadata, utils, qubit_cli.auth (all present) |
| agent/onboarding_first_tasks.py | 328 | none (pure catalog) |
| agent/onboarding_flow.py | 429 | agent.onboarding_first_tasks (ported this chunk) |

All 8 pass `PYTHONPATH=qubit_runtime python -c "import agent.X"`.

## dep-missing (3) — imported, but blocked on deps in other chunks

Named missing dependencies (all are in the 299-list unless noted):

**agent/onboarding_record.py** (178 lines) — **fails at module import**:
- `agent/onboarding_text.py` (in 299)
- `qubit_cli/onboarding_run.py` (upstream `hermes_cli/onboarding_run.py`, in 299) — absent locally
- `qubit_platform/host/summary.py` (upstream `hermes_platform/host/summary.py`, in 299) — absent locally

**agent/onboarding_facts.py** (35 lines):
- `qubit_platform/host/summary.py` (in 299)
- `qubit_cli/plugin_catalog_presence.py::onboarding_names` — the module EXISTS
  locally but lacks this function (upstream added it)
- imports clean; `read_facts()` / `suggested_name()` raise ImportError when called.

**agent/managed_worker.py** (454 lines) — imports clean at module level, but
`execute()` needs 8 deps still absent from our tree:
`agent/safe_worker_policy.py`, `gateway/session_finite.py`,
`gateway/session_tool_events.py`, `gateway/session_kanban.py`,
`gateway/session_policy.py`, `gateway/session_worker_construct.py`,
`gateway/session_ingress_media.py`, `agent/runtime_session_store.py` (all in 299).

No stubs, no `try/except` papering. Each is a genuine cross-chunk ordering
dependency: port those paths, then these three run.

## agent/ invariants

- **Prompt caching preserved.** None of the 11 files mutates past context, swaps a
  toolset, reloads memory, or rebuilds the system prompt mid-conversation.
  - The three `context_compressor_*` siblings touch only the compression
    middle window — the ONE sanctioned cache break (per `agent/AGENTS.md`).
  - `managed_worker.py` sets `ephemeral_system_prompt` / `enabled_toolsets` /
    `disabled_toolsets` at **construction** inside a fresh per-assignment process
    (`sys.executable -m`), never mid-conversation — no cache hazard. Its
    `disabled_toolsets=WORKER_DISABLED_TOOLSETS=('delegation',)` is a
    construction-time argument, not a mid-loop swap.
  - `onboarding_flow.py` is pure (no I/O, no clock) and has no transcript access;
    `onboarding_record.py` writes the run into `sessions.model_config`, not into
    live messages.
- **Role alternation untouched.** No file appends to `messages` or injects a user
  row; `onboarding_text`'s opening prompt (the module that *does* inject a hidden
  user turn) is upstream's own file, not in this chunk.
- No file in this chunk is a "consciously deferred / cache-aware" no-op.

## Brand rule

Applied `scripts/qubit_debrand.py`'s exact classified rules via its own
`_transform()` (not a blanket rename):

- Package renames: `hermes_cli`→`qubit_cli`, `hermes_platform`→`qubit_platform`,
  `hermes_constants`→`qubit_constants`, `hermes_bootstrap`→`qubit_bootstrap`,
  `get_default_hermes_root`→`get_default_qubit_root`,
  `hermes_state_compression`→`qubit_state_compression`.
- Prose in `onboarding_first_tasks.py`: standalone `Hermes`→`Qubit`
  ("Explain how Qubit works", "watch Qubit work", scene named `Qubit`).
- **Preserved** (per the engine's masks, matching the repo convention):
  quoted id `"hermes"` (`FIXED_ID`, a wire-visible card id), the `hermes-agent`
  skill reference, the `~/hermes-first-task/` output path, and the upstream docs
  URL. These are exactly the classes `_QUOTED_ID` / `_WIRE_HYPHEN` /
  path-literal / URL protection leaves verbatim.

### Env-var collision (reported, not papered over)

`agent/managed_worker.py` writes `HERMES_GATEWAY_SESSION`. Grepping the whole tree,
**5+ production readers still key on the legacy `HERMES_` spelling directly**
(`cron/scheduler.py:3766`, `tui_gateway/server.py:1380`, and 4 more via
`env_var_enabled`), and existing writers (`tui_gateway/server.py`) still write
`HERMES_`. Per `docs/REBRAND-SWEEP-PLAN.md` Phase 1 (writers have not moved yet),
a canonical-only write would have been the exact half-rebrand bug this lane is
warned about. The port therefore **dual-writes**: canonical `QUBIT_GATEWAY_SESSION`
(Phase-2 target) **plus** legacy `HERMES_GATEWAY_SESSION`, with an inline comment
naming the convergence condition. No third spelling introduced.

## Cross-module gap (not a defect in this chunk)

`gemini_long_decode.py` is correct standalone, but its consumer
`agent/gemini_native_adapter.py` is behind the pin (missing `initial_prompt_tokens`,
`initial_cached_tokens`, and hop-resume wiring). Upstream's
`tests/agent/test_gemini_long_decode.py` fails 4/6 against our tree **inside that
adapter**, not in the ported file (the 2 dependency-free tests pass, and the
failing asserts show `merge_hop_usage` produces the right values). Flagging for
whoever owns `gemini_native_adapter.py`.

## Artifacts

- `qubit_runtime/agent/` — 11 files (paths above)
- `PORTED.json` — per-file path, upstream lines, status, dependencies-missing, imports-clean
- `REPORT-port-2.md` — this file
