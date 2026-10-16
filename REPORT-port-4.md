# REPORT — port chunk 4 (evals/ + cron/)

| | |
|---|---|
| Worktree / branch | `/tmp/port-4` on `port/chunk-4` |
| Base commit | `9c2f782e1` |
| Upstream ref read | `origin/main` = `53bec2a311979ad6f77580ba491eca261ab988e0` |
| Chunk | `/tmp/port-4/FILELIST.txt` — 9 files |
| Method | `PORT-METHOD.md` (landed on `port/chunk-5` mid-task; followed) |

## Outcome

| Status | Count |
|---|---|
| `ported` | 7 |
| `dep-missing` | 2 |
| `shim-skipped` | 0 |
| imported clean (verified) | 7 / 9 |

Nothing is stubbed and nothing is `try/except`-papered. The two blocked files are blocked on a
**top-level** import of a module that is in the 299 but not in this chunk; they are reported, not
ported as dead code.

## The finding that shapes this chunk: facade + siblings, already inline in our tree

Upstream moved code out of our monolithic files. `cron/jobs.py` (ours) **defines
`_validate_job_mode_invariants` and `NO_AGENT_WITHOUT_SCRIPT_ERROR` inline at line 1740/473**;
upstream's `cron/jobs.py` line 34 is `from cron.jobs_invariants import _validate_job_mode_invariants`.
Same pattern for `cron/scheduler_bookkeeping.py`: we have `_classify_delivery_outcome` (2958),
`_FireOwnership` (3033), `_RunDelivery` (3088), `_save_compose_deliver` (3106) inline in
`cron/scheduler.py`; upstream extracted the terminal-bookkeeping half into its own module.

So these are **genuine extracted modules, not re-export shims** — but porting the module alone
leaves a duplicate definition until the facade (`cron/jobs.py`, `cron/scheduler.py`) is rewired to
import from it. Rewiring the facade is *that file's* port, not this chunk's, and doing it here would
be an unreviewed behavioural change to a live scheduler. **Action item for whoever ports
`cron/jobs.py` / `cron/scheduler.py`:** make them import from the ported siblings and delete the
inline copies. Until then the ported modules are standalone-importable and harmless.

## Brand rule — collisions found (reported, not papered over)

The task says: use `QUBIT_*` for a new file, **then grep the whole tree for an `HERMES_` spelling and
report a collision rather than adding a third inconsistency.** Two collisions, both resolved by
reading *what the writers set*, per `docs/REBRAND-SWEEP-PLAN.md` hazard 3.

### Collision 1 — `HERMES_TUI_GATEWAY_URL` (left as `HERMES_`)

`cron/scheduler_gateway_gate.py` reads this to detect an explicit remote gateway. Our **production
writes and reads the `HERMES_` spelling**:

- `qubit_cli/shared_session_attach.py:91,95` — reads and writes `env["HERMES_TUI_GATEWAY_URL"]`
- `qubit_cli/web_server_chat.py:509` — writes `env["HERMES_TUI_GATEWAY_URL"]`
- only one *test* reads the `QUBIT_` spelling (`tests/hermes_cli/test_web_server.py:5117`)

Writing `QUBIT_*` here would make a fresh file disagree with the process that sets the var — the
exact "rename has two halves" failure. Kept `HERMES_TUI_GATEWAY_URL` with an inline `NOTE(port)`
explaining why; the sweep (Phase 1/2, dual-read) owns the migration. **`HERMES_TUI_GATEWAY_URL` is a
boundary symbol: writer and reader are different processes.**

### Collision 2 — `HERMES_HOME` → `QUBIT_HOME` (renamed; target confirmed)

`HERMES_HOME` appears in the ported eval harnesses. Counted in our tree: **809 production refs to
`QUBIT_HOME` vs 34 to `HERMES_HOME`**, and `qubit_constants.get_qubit_home()` reads *only*
`QUBIT_HOME`. `QUBIT_HOME` is the canonical target — renamed in all ported files. The 34 remaining
`HERMES_HOME` sites are the sweep's problem (one is a deliberate legacy dual-read in
`qubit_cli/update_lock.py`).

### Kept verbatim — wire protocol, NOT brand (PORT-METHOD.md §3d)

`electron-main.ts` / `electron-main-multiplex.ts` carry `'hermes-gateway-v1'` and
`'hermes-gateway-ticket.'`. Our production **writes those subprotocol ids**:

- `qubit_cli/web_server_chat.py:221` `_GATEWAY_WS_PROTOCOL = "hermes-gateway-v1"`
- `qubit_cli/web_server_chat.py:222` `_GATEWAY_WS_TICKET_PROTOCOL_PREFIX = "hermes-gateway-ticket."`

Renaming them in the fixture would break the handshake against the real gateway. Left verbatim.

### Module renames applied (§3a)

`hermes_constants`→`qubit_constants` (`display_hermes_home`→`display_qubit_home`,
`get_hermes_home`→`get_qubit_home`); `hermes_cli.*`→`qubit_cli.*`; `hermes_state`→`qubit_state`;
`hermes_state_runtime`→`qubit_state_runtime`; `HERMES_PROFILE`→`QUBIT_PROFILE`; `~/.hermes`→`~/.qubit`.

## Import proof (venv python, `PYTHONPATH=/tmp/port-4/qubit_runtime`)

```
cron.jobs_invariants            CLEAN
cron.scheduler_authority        CLEAN
cron.scheduler_bookkeeping      CLEAN
cron.scheduler_gateway_gate     CLEAN
evals.hosted_attachment_transfer.bench      CLEAN
evals.prefix_rediet.run                     CLEAN
evals.prefix_rediet.worker                  CLEAN  (import-clean; body runs sys.argv)
```

AST check confirms the 4 cron modules have **no top-level import of a missing module** — the only
top-level imports are `asyncio/hashlib/json/uuid/typing/logging/os`. Every unresolved name in them
(`qubit_cli.gateway_client`, `qubit_cli.gateway_runtime`, `gateway.session_cron`,
`cron.delivery_outcome`, `cron.jobs._hermes_now`, `cron.scheduler._apply_agent_failure_marker`) is
imported *inside a function*, so the module imports and the dep is orderable.

### Two blocked files (top-level imports — cannot import until the dep lands)

| File | Blocking top-level import | Where it lives |
|---|---|---|
| `evals/desktop_gateway_ticket_native/run.py` | `from qubit_cli.gateway_runtime_discovery import query_identify` | `hermes_cli/gateway_runtime_discovery.py` — **in the 299**, 213 lines, not this chunk |
| `evals/desktop_gateway_ticket_native/run_multiplex.py` | `from evals.desktop_gateway_ticket_native.run import stop_owned` **and** the same discovery import | transitively blocked |

`gateway_runtime_discovery.py` is a real 213-line module (bounded local-control discovery), not a
shim. **Port it and these two files import, unchanged.**

The method (§5) says port the file anyway when only the *function-internal* imports are missing;
for a top-level missing import it says "say so plainly". Both are committed as-is with the blocker
recorded — importing clean is impossible until the dep lands, and their content is identical to
upstream modulo the brand renames that *do* apply.

## Companion data files ported (PORT-METHOD.md §5)

`build.mjs` is **invoked at runtime** by `run.py`/`run_multiplex.py`
(`subprocess.run([node, build.mjs, bundle])`), so it is a dependency, not decoration:

```
evals/desktop_gateway_ticket_native/{build.mjs, electron-main.ts, electron-main-multiplex.ts,
                                     README.md, workflow.yml, .gitignore}
evals/prefix_rediet/README.md
evals/hosted_attachment_transfer/.gitignore
```

`build.mjs` resolves `'../../apps/desktop/package.json'` and `../../node_modules` relative to its own
directory, which is why it must keep the same *relative* depth under `qubit_runtime/` — it does.

## Not runnable standalone here (honest)

| Harness | Why not |
|---|---|
| `bench.py` | needs the hosted-room stack (`session_hosted_transport`, `hosted_room_attachments`, `run_runtime`, `runtime_ownership`, `session_authorities`, `session_hosted_service`, `qubit_state_runtime`) and a real `GatewayControlServer` on a private socket |
| `desktop_gateway_ticket_native/run*.py` | do not import (dep-missing); would also need node + a native Electron binary + a live gateway |
| `prefix_rediet/run.py` | orchestrates `worker.py`; needs `PRD_BASE_TREE`/`PRD_PR_TREE` checkouts and model inference |
| `prefix_rediet/worker.py` | needs `PRD_*` env, an OpenAI-compatible endpoint, and the browser/vault stubs it installs |

Proof used instead of a misleading import failure (§6): `py_compile` on all five (passes),
`bench.py --help` (passes), `prefix_rediet/run.py --report` (runs, empty results dir).

## Commits on `port/chunk-4`

| Commit | Unit |
|---|---|
| `171cbda98` | 4 cron modules |
| `d987e614d` | 5 eval harnesses |
| `6ea7e58ad` | eval companion data |
| (final) | `PORTED.json` + this report |

## Dependencies this chunk names for ordering

In the 299, not yet ported — **port these and the blocked/unrunnable set shrinks**:

`hermes_cli/gateway_runtime_discovery.py` (unblocks 2 files), `hermes_cli/gateway_client.py`,
`hermes_cli/gateway_runtime.py`, `gateway/session_cron.py`, `gateway/session_hosted_transport.py`,
`gateway/hosted_room_attachments.py`, `gateway/run_runtime.py`, `gateway/runtime_ownership.py`,
`gateway/session_authorities.py`, `gateway/session_hosted_service.py`, `cron/delivery_outcome.py`,
`hermes_state_runtime.py`.

Also needed but **not** in the 299: `cron.jobs._hermes_now` and
`cron.scheduler._apply_agent_failure_marker` — upstream symbols our `cron/jobs.py` /
`cron/scheduler.py` never grew (ours uses `qubit_time.now`). The facade port must supply them or the
receipt-recovery path stays inert.
