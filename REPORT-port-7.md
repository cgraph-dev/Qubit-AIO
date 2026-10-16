# REPORT-port-7 — gateway blocker-unblocking chunk

Upstream ref read: `origin/main` = **`53bec2a311979ad6f77580ba491eca261ab988e0`** (2026-10-16, re-read
at start; same HEAD as chunk-5's pin).

Worktree `/tmp/port-7`, branch `port/chunk-7`. Mapping `upstream root == qubit_runtime/`.
Method followed verbatim per `PORT-METHOD.md`.

## Result: 5/5 ported, 5/5 imports-clean

| upstream path | ours | upstream lines | status | imports |
|---|---|---|---|---|
| `gateway/hosted_room_work_records.py` | `qubit_runtime/gateway/hosted_room_work_records.py` | 416 | ported | clean |
| `gateway/hosted_room_work_storage.py` | `qubit_runtime/gateway/hosted_room_work_storage.py` | 247 | ported | clean |
| `gateway/run_runtime.py` | `qubit_runtime/gateway/run_runtime.py` | 500 | ported | clean |
| `gateway/run_runtime_init.py` | `qubit_runtime/gateway/run_runtime_init.py` | 237 | ported | clean |
| `gateway/runtime_ownership.py` | `qubit_runtime/gateway/runtime_ownership.py` | 243 | ported | clean |

Commit: see `git log` on `port/chunk-7` (subject `port(gateway): chunk-7 …`).

## §4 shim check

None of the five is a facade re-export stub. All are substantive (237–500 lines) with real
implementations. No `shim-skipped`.

## §5 import resolution — dependencies-missing (all named; all in the 299-list)

Every missing dependency below is a **deferred (function-local) import**, so each of my five
modules imports cleanly *today*; the dep only binds when its code path runs. No top-level import
of a missing module was left unresolved.

- `hosted_room_work_records.py` → **`gateway/hosted_room_work_lineage.py`** (absent, list line 53).
  Imported lazily inside `capture_locked` only on the v2 (authority_epoch > 1) path.
- `run_runtime.py` → **12 absent gateway/state modules**: `gateway/session_authority.py` (95),
  `gateway/runtime_bootstrap.py` (81), `gateway/session_authorities.py` (94),
  `gateway/session_runtime_workers.py` (154), `gateway/session_cron.py` (110),
  `gateway/session_bot.py` (97), `gateway/session_hosted_service.py` (121),
  `gateway/session_local_recovery.py` (137), `gateway/platforms/webhook_ingress.py` (67),
  `gateway/run_api.py` (69), `gateway/session_acp_lifecycle.py` (87), and
  `hermes_state_runtime.py` (256, module-renames to `qubit_state_runtime.py`).
  All are function-local imports inside the runtime-lifecycle coroutines.
- `hosted_room_work_storage.py`, `run_runtime_init.py`, `runtime_ownership.py` → **zero** missing
  deps. `run_runtime_init.py`'s `gateway.run`, `tools.process_registry`, `gateway.session_db_recovery`,
  `gateway.run_profile_reconcile`, `gateway.pairing`, `gateway.hooks` all exist; `runtime_ownership.py`'s
  `gateway.status` exists.

No genuine (non-299) gaps found; nothing needed escalation.

### Ordering for the next wave
`run_runtime.py` unblocks no one until `gateway/session_authorities.py` +
`gateway/session_authority.py` + `gateway/session_runtime_workers.py` + `gateway/session_bot.py` +
`gateway/session_hosted_service.py` land (the boot/hot-serve/recovery entry points live there).
`hosted_room_work_records.py` is complete for v1 evidence; it needs
`gateway/hosted_room_work_lineage.py` only for v2 (post-authority-transition) capture.

## §6 proof

`PYTHONPATH=/tmp/port-7/qubit_runtime <venv python> -c 'import gateway.<mod>'` → **OK for all five**
(`runtime_ownership`, `hosted_room_work_records`, `hosted_room_work_storage`, `run_runtime`,
`run_runtime_init`). `py_compile` also clean on all five.

Exercised beyond bare import:
- `runtime_ownership`: `ProfileOwnership.reserve([a,b])` takes both locks; a second owner on a held
  home raises `OwnershipConflict`; all-or-nothing verified (a conflict on `b` releases the freshly-taken
  `a`); `canonical_home` normalizes `/x/.`; `unreadable_lock_recovery` returns `None` for a
  non-permission failure. `exclusive_maintenance` context runs (its local-owner semantics are
  upstream-correct: `process_ownership` is not the maintenance owner).
- `hosted_room_work_records`/`_storage`: `initialize` delegates to storage; table constants, bounds
  (`MAX_STORE_BYTES=4194304`, `MAX_STORE_ROWS=512`), `retained_fields`/`row_size_sql` delegation across
  the source/pending/invalid tables, `encode`/`digest` (64-hex) deterministic, and `validate({...})`
  rejects malformed records with `WorkRecordError`.

## §3 brand decisions (the two-halves trap engaged — reported, not swept)

1. **`runtime_ownership.py:166` lock record key.** Upstream writes `'hermes_home': str(home)` into the
   `gateway.lock` JSON. Our `gateway/status.py` already establishes the canonical direction: it defines
   `_HOME_KEY = "qubit_home"` / `_LEGACY_HOME_KEY = "hermes_home"` and `record_home()` reads
   **`qubit_home` first, `hermes_home` as legacy fallback** — a **read-old / write-new** contract, and
   `_build_pid_record()` alongside it already writes `qubit_home`. A new file must write the canonical
   key, so the port writes **`'qubit_home'`**. This is *not* a place where `"hermes_home"` is protected
   (§3d protects the sweep's deliberate `_BARE_HOME_RE` preservation of *existing* serialized keys);
   writing the legacy key here would be, in this codebase's own terms, a stale writer and a third
   inconsistency. **Verified**: the emitted `gateway.lock` keys are
   `['argv','kind','pid','qubit_home','start_time']` — no `hermes_home`.
2. `run_runtime.py`: `from hermes_constants import get_hermes_home` → `from qubit_constants import get_qubit_home`
   (both exist; also `get_qubit_home().resolve()`). `hermes_state_runtime` → `qubit_state_runtime`
   (§3a mechanical rename; target absent, named above).
3. `run_runtime_init.py`: prose only — `NFS HERMES_HOME` → `NFS QUBIT_HOME`; `hermes update` → `qubit update`.
   No identifier or wire token touched.
4. `hosted_room_work_records.py` / `hosted_room_work_storage.py`: **zero brand tokens** — verbatim.

No `QUBIT_*` env-var name was introduced by this chunk, so no writer/reader collision to reconcile.

## AGENTS.md engagement

- **Profile scope (unbound read = silent default-profile leak).** `run_runtime.py` is a primary
  participant: `reserved_profile_homes` builds the launch-home-first reservation set;
  `_build_profile_authority` resolves each profile's `state.db` **under `_profile_runtime_scope(home,
  hydrate_secrets=False)`** and refuses a db whose path does not belong to the reserved home;
  `release_unknown_turn_markers` / `recover_gateway_native_sessions` / `_retire_profile_authority` all
  bracket authority work with `owner_scope`. This file is the "resolve the owning home from the
  reservation, never from `os.environ`" rule in code — the port preserves it exactly.
- **Streaming-delivery contract (4 invariants).** Not engaged — no egress door, stream consumer, or
  `send()`/`edit_message` path in these files.
- **Two message guards / approval bypass.** Not engaged — no inbound-command dispatch here; this is
  runtime ownership + lifecycle, not message admission.
- **Multiplex env reads fail closed.** `run_runtime.py`'s `hydrate_secrets=False` scope and
  `getattr(runner.config, '_runtime_profile_homes', …)` reads are config/reservation, not `os.environ`
  borrow — consistent with the rule.

## Not done / left for the next lane
- No `port(data)` companion files needed — no `.json`/README runtime dependency among these five
  (verified: no `__file__`-relative data load).
- `hosted_room_work_lineage.py` + the 12 `run_runtime` gateway/state deps are the ordering constraints.
