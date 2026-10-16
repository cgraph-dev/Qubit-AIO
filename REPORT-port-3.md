# REPORT — port/chunk-3 (agent/ + cron/, 13 files)

Upstream pin `517b5e10febd619ce30bb22580e29b160266eb43` (`origin/main`), base `9c2f782e1`.
Mapping confirmed against existing files: upstream root → `qubit_runtime/`.

## Result

- **12 / 13 ported and importing clean.**
- **1 / 13 blocked on missing dependencies** (`agent/onboarding_text.py`) — ported, committed, honestly marked blocked (not stubbed, not wrapped).
- **0 shims skipped.** All 13 upstream files carry real `def`/`class` bodies (smallest 65 lines); none is a facade re-export stub, so no file was dropped on that ground.

## Per-file

| upstream path | lines | status | missing deps |
|---|---|---|---|
| agent/onboarding_text.py | 392 | **BLOCKED** | `agent.onboarding_first_tasks`, `agent.onboarding_flow`, `tools.set_theme_tool` |
| agent/persisted_turn_receipt.py | 65 | clean | — |
| agent/plugin_live_notes.py | 77 | clean | — |
| agent/reasoning_carriers.py | 147 | clean | — |
| agent/relay_compaction.py | 93 | clean | — |
| agent/runtime_session_compression.py | 151 | clean | `qubit_state_runtime.HANDOFF_MESSAGE_FIELDS` (lazy) |
| agent/runtime_session_lifecycle.py | 90 | clean | — |
| agent/runtime_session_store.py | 473 | clean | `qubit_cli.gateway_client` / `.gateway_runtime` / `.gateway_runtime_discovery` / `tools.async_delegation_worker` (all lazy) |
| agent/safe_worker_policy.py | 80 | clean | — |
| agent/skills_index_delta.py | 136 | clean | — |
| agent/turn_recovery_codex.py | 76 | clean | — |
| cron/bot_chat_legacy.py | 122 | clean | — |
| cron/delivery_outcome.py | 94 | clean | **`cron.executions.settle_delivery_outcome` (function absent — see below)** |

Import proof: `PYTHONPATH=qubit_runtime <venv>/bin/python -c "import <module>"`. 12 CLEAN, 1
`ModuleNotFoundError: No module named 'agent.onboarding_first_tasks'`.

## Dependency findings (named, per instruction)

**In the 300 missing list — another chunk owns them; nothing to do here, but the ports stay
non-functional until those land:**

- `agent/onboarding_first_tasks.py`, `agent/onboarding_flow.py`, `tools/set_theme_tool.py` —
  top-level imports in `onboarding_text.py`, hence the hard block.
- `hermes_state_runtime.py` → `qubit_state_runtime.py`. Needed for `HANDOFF_MESSAGE_FIELDS`.
  Our tree has 30 `qubit_state_*.py` siblings but no `qubit_state_runtime.py`.
- `hermes_cli/gateway_client.py`, `hermes_cli/gateway_runtime.py`,
  `hermes_cli/gateway_runtime_discovery.py` → `qubit_cli.*`; and
  `tools/async_delegation_worker.py`. All four are lazy (function-body) imports in
  `runtime_session_store.py`, so the module imports, but any real worker RPC path will
  `ImportError`.

**Not in the 300 list — we sit behind the pin, and this is the one to flag:**

- `cron/executions.py` in our tree is 562 lines vs upstream 653. Upstream defines
  `settle_delivery_outcome` (upstream line 389); ours does not. `cron/delivery_outcome.py:82/84`
  imports and calls it. Verified at runtime:
  `cannot import name 'settle_delivery_outcome' from 'cron.executions'`.
  `settle_quietly` swallows the `ImportError`, so nothing crashes on import or on the no-op path —
  **but the write-back never happens**: queued-delivery outcomes would silently stop settling.
  Fix = refresh `cron/executions.py` from the pin (outside this chunk's 13 files).

## Brand (docs/REBRAND-SWEEP-PLAN.md)

- **No env var is introduced by any of the 13 files** — grepped for `QUBIT_*`/`HERMES_*`
  literals across all of them: zero hits. The collision clause is trivially satisfied; there is
  nothing to reconcile because there is no new name.
- Module renames applied follow the tree's existing convention, verified against already-ported
  files (`cron/bot_chat_delivery.py`, `cron/executions.py`, `gateway/run.py`):
  `hermes_cli.*`→`qubit_cli.*`, `hermes_state_*`→`qubit_state_*`, `hermes_constants`→`qubit_constants`.
  No blanket rename; `agent/skills_index_delta.py` and `agent/reasoning_carriers.py` were left
  byte-identical to upstream.
- **Protocol strings deliberately NOT renamed** (same class as the frozen pin — cross-process /
  persisted identifiers):
  - `carrier_record`'s `CARRIER_TYPE = "hermes.native_assistant"` in `reasoning_carriers.py`.
    Grepped our tree: `agent/relay_runtime.py` keeps `hermes.session` / `hermes.turn` scope
    names, `agent/coding_context.py` keeps `logging.getLogger("hermes.coding_context")`. Renaming
    this one string would desync it from every persisted `reasoning_details` row and from the
    chat-completions transport's `.native_assistant` suffix filter.
  - `relay_compaction.py`'s `COMPACTION_DATA_SCHEMA = {"name": "hermes.compaction"}` — a NeMo
    Relay mark name, matched by the Relay host, not by us.
- **User-facing identity renamed** (`onboarding_text.py`): `Hermes`→`Qubit` in `GREETING`, all
  nine `_GREETINGS` locales, `facts_block`, `opening_prompt`, `_REMOTE_LINE`;
  `~/hermes-first-task/`→`~/qubit-first-task/`; `_PAGE_TASKS` `"hermes"`→`"qubit"`. Precedent:
  `agent/initiate_setup_prompt.py:9` already renders `INTRO = "Hi, I'm Qubit..."`.
  **Unverified coupling to state in the report:** the module's own comment says `_GREETINGS` must
  equal the desktop's `firstRun.intro` copy in `apps/desktop/src/i18n/*_first_run.ts`. Those files
  are **not present in our tree**, so the equality could not be checked — the desktop i18n side
  must be rebranded in the same sweep or the two texts diverge.

## cron/ invariants (qubit_runtime/cron/AGENTS.md)

Two cron files landed: `delivery_outcome.py`, `bot_chat_legacy.py`. Read AGENTS.md first; findings:

- **Neither file touches the scheduling core.** Grepped both for `pending_slot`, `next_run_at`,
  `_inflight_key`, `_inflight_home_path`, `_profile_cron_scope`, `.tick.lock`, `catch_up`:
  **zero hits**. They do not advance slots, do not open the store, do not build job env, do not
  register/release running-job claims. So the at-most-once / slot-accounting / per-home-scope
  invariants are not engaged by these ports.
- `delivery_outcome.py` *reads and writes* two job fields — `last_status` and
  `last_delivery_error` — via `cron.jobs._with_job` (which holds `_jobs_lock()` and saves inside
  the callback). It is fenced on the execution id and on `canonical_completions[-1]`, so it is
  idempotent and cannot rewrite a later run's status. This is *settlement bookkeeping*, not slot
  accounting. It does not use `script`, `context_from`, or `no_agent`.
- `bot_chat_legacy.py` only reads/writes files under `<QUBIT_HOME>/cron/bot_chat_pending/`
  (via `get_qubit_home()`); it never touches `jobs.json`. **It respects the scripts confinement
  by not participating in it** — no repo path is resolved, and I did not "fix" the
  `<QUBIT_HOME>/scripts/` confinement anywhere.
- The **`_QUBIT_CRON_WORKER_BOOT` / `cron/__init__.py` → `worker_bootstrap.py`** invariant: not
  engaged. Neither file is imported by `cron/__init__.py`, and neither is on the external-worker
  boot path.
- `runtime_session_store.py` (agent/, not cron/) does reference worker adoption and
  `_worker_process`, but that is the *agent-side* worker persistence facade — it writes no
  `jobs.json` and takes no cron tick lock. Noted because its name collides in search terms with
  the cron external-worker machinery.

## Tests

No test in our tree exercises these 13 modules. Upstream has dedicated coverage that ships with
the same wave and is likewise unported: `tests/agent/test_reasoning_carriers.py`,
`test_runtime_session_store*.py` (4), `test_safe_worker_policy.py`, `test_skills_index_delta.py`,
`test_relay_compaction*.py` (2), `tests/cron/test_delivery_outcome_writeback.py`,
`test_bot_chat_legacy_isolation.py`, `tests/gateway/test_authority_complete_persisted_turn.py`.
Per the implementation-first cadence I did not run broad suites; verification here is the
per-module import proof plus the trio-interlock check (below).

Trio interlock verified — `RuntimeSessionStore` subclasses both mixins:
`issubclass(RuntimeSessionStore, RuntimeSessionCompressionMixin)` and
`issubclass(RuntimeSessionStore, RuntimeSessionLifecycleMixin)` both hold.

## Commits (port/chunk-3)

```
389099d3d port(agent): persisted_turn_receipt, plugin_live_notes, safe_worker_policy
5a6772cbe port(agent): reasoning_carriers, relay_compaction, turn_recovery_codex
42863dea0 port(agent,cron): skills_index_delta, delivery_outcome
7cb0e00b4 port(agent): onboarding_text (BLOCKED import: needs 3 files)
dcf9fa9fb port(agent,cron): runtime_session_lifecycle, bot_chat_legacy
70438ccfd port(agent): runtime_session_compression + runtime_session_store
```

## Follow-ups for the integration lead

1. Land `cron/executions.py` from the pin — without it `delivery_outcome.py` is inert.
2. Land the three `onboarding_*`/`set_theme_tool` files to unblock `onboarding_text.py`.
3. Land `qubit_state_runtime.py` + the four `qubit_cli.gateway_*`/`tools.async_delegation_worker`
   files for the worker RPC path.
4. Rebrand `apps/desktop/src/i18n/*_first_run.ts` in step with `onboarding_text.py`'s
   `_GREETINGS`, or the desktop opening and the backend scripted greeting diverge.
