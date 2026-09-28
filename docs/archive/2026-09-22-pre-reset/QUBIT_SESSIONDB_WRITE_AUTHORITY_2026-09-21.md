# Qubit SessionDB Write Authority Audit — 2026-09-21

**Status:** read-only audit + additive implementation record for the current
worktree. **NOT** completion evidence and **NOT** a verification claim. Read
alongside
[QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md](QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md)
and
[QUBIT_VOICE_ACTIONABILITY_CONTRACT_2026-09-21.md](QUBIT_VOICE_ACTIONABILITY_CONTRACT_2026-09-21.md).

**Question answered:** do the `/chat` terminal/TUI path, or any
`voicebridge/` module, write the canonical chat `state.db` (or open
`qubit_state.SessionDB`) in a way that bypasses the canonical adapter's
contract (a second writer, missing audit, session-id drift)?

**Method:** exhaustive `graft` + `grep` over `voicebridge/` for
`state.db` / `SessionDB` / `sqlite3`, then direct reads of every hit's enclosing
symbol and path construction. No prior report was trusted. No vendored file was
modified. No live server was contacted.

---

## 1. The one authority

The single writable chat-transcript store is the vendored
`qubit_state.SessionDB` (SQLite `state.db`) under the Qubit-owned runtime home
(`qubit_runtime.runtime_home_for_cfg(cfg)` = `<state_dir>/runtime`). Every native
surface reaches it through
`voicebridge.qubit_session_store.QubitSessionStore` (`voicebridge/chat_transport.py:369-379`).
The `QubitSessionStore` adapter is the **only** Qubit-side writer path; the
terminal/TUI/agent is the vendored runtime's **own** single writer of the same
file.

## 2. Audit table

Classification legend: **canonical-adapter** (the one Qubit-side writer),
**read-only**, **vendored-runtime-owned** (documented; the fork's own writer),
**migration/backup** (explicit copy, not a steady-state writer),
**canonical-seam** (delegates to the adapter), **disjoint-store** (a different
database file with its own authority), **bypass** (writes the canonical chat
`state.db` outside the adapter — none found).

| file:line | access | classification | evidence |
| --- | --- | --- | --- |
| `voicebridge/qubit_session_store.py:151-164` | `_db_for` constructs `SessionDB(db_path=path)`; refuses the external store and claims the single writer slot before constructing | canonical-adapter | the ONE constructor for the canonical chat store; cached per resolved path by `_DBS` |
| `voicebridge/qubit_session_store.py:167-180` | `qubit_state_db_path(cfg)` → `<runtime_home>/state.db` | canonical-adapter | path authority; fail-closed `assert_safe_test_home` |
| `voicebridge/qubit_session_store.py:194-232` | `refuse_external_state_db`, `assert_canonical_state_db` | canonical-adapter (guard) | **added**; refused the external `~/.hermes/state.db` and any divergent `state.db` |
| `voicebridge/qubit_session_store.py:235-282` | `claim_state_db_writer`, `release_state_db_writer`, `registered_state_db_writers`, `session_db_write_authority` | canonical-adapter (guard) | **added**; typed second-writer registry + descriptor |
| `voicebridge/qubit_session_store.py:286-296` | `close_cached_db` | canonical-adapter | closes the cached handle and releases the write claim |
| `voicebridge/qubit_session_store.py` `QubitSessionStore.*` (lifecycle/messages/meta/groups) | `create_session`, `append_message`, `patch_session_model_config`, `_execute_write`, `_write_sql`, `clear_messages`, `rewind_to_message`, `delete_session`, `archive_and_compact` etc. | canonical-adapter | all writes go through the vendored `SessionDB` API on the one `state.db`; group table + column restores share the vendored write transaction |
| `voicebridge/qubit_session_store.py:995-1096` | `analytics_usage` → `sqlite3.connect("file:...?mode=ro")` | read-only | `mode=ro`; absent store returns `None` (never fabricated) |
| `voicebridge/pty_ws.py:263-274` | `terminal_state_home(cfg)` | vendored-runtime-owned (guard) | **added**; resolves the PTY child home via `qubit_runtime.use_qubit_home(cfg)` |
| `voicebridge/pty_ws.py:277-297` | `_assert_terminal_shares_canonical_store` | vendored-runtime-owned (guard) | **added**; refuses a divergent/external terminal home with `TERMINAL_STATE_HOME_DIVERGED` |
| `voicebridge/pty_ws.py:300-339` | `_default_spawn` → `PtyBridge.spawn([qubit, --tui])` | vendored-runtime-owned | the child opens the canonical `state.db` itself; `pty_ws` opens no DB. No `sqlite3`/`SessionDB` symbol exists in this module |
| `voicebridge/webui.py:3597-3601` | `set_home(runtime_home_for_cfg(cfg))` for the in-process ASGI bridge | vendored-runtime-owned | host binding only; no direct DB write (comment L3591-3596 documents one home) |
| `voicebridge/qubit_asgi.py:114,148` | `set_home` / `use_qubit_home()` | vendored-runtime-owned | binds the fork home; no direct DB write |
| `voicebridge/qubit_runtime.py:186-214` | `runtime_home_for_cfg` | vendored-runtime-owned | the ONE home resolver used by adapter, bridge, PTY, CLI |
| `voicebridge/qubit_runtime.py:342-369` | `use_qubit_home` → sets `QUBIT_HOME`; never adopts external `~/.hermes` | vendored-runtime-owned | env binding for the fork child |
| `voicebridge/qubit_runtime.py:217-249` | `real_default_home` / `assert_safe_test_home` | vendored-runtime-owned | fail-closed test isolation |
| `voicebridge/qubit_runtime.py:274-305,308-339` | `_copy_state_file` / `migrate_legacy_homes` | migration/backup | one-shot, copy-only convergence of legacy homes; SQLite online-backup to a temp file then atomic rename; never opens the external store for write |
| `voicebridge/gateway/rpc.py:566-579` | `_sessions_storage_status` | canonical-seam / read-only | `HS.qubit_state_db_path(cfg)` + `stat` only |
| `voicebridge/gateway/rpc.py:581-604` | `_sessions_storage_run` → `HS._db_for(path)` + `SessionDB.optimize_fts_storage` | canonical-seam | confirmation-gated; uses the canonical adapter's own handle |
| `voicebridge/gateway/openclaw_handlers/sessions.py`, `sessions_advanced.py`, `sessions_compaction.py`, `surface_control.py` | session projections / compaction / checkpoints | canonical-seam | module docstrings state they read/write the one `state.db` through the adapter |
| `voicebridge/qubit_compat.py:538-554` | `_chat_session_store` → `CT.store(cfg)` | canonical-seam | fails closed on authority drift; never opens `state.db` directly |
| `voicebridge/qubit_migration.py:66-67,294,1049,1116,1150-1182,1243` | `_connect_ro` (source, `mode=ro`), `_dest_store(cfg)` → canonical adapter, `st.import_session` | migration/backup | source is read-only; destination writes go through `QubitSessionStore` |
| `voicebridge/qubit_migration.py:1071-1088,1127-1142` | `_snapshot_sqlite` + destination snapshot → `sqlite3.connect` to backup dir | migration/backup | writes only under the timestamped backup dir, never the canonical file |
| `voicebridge/task_ledger.py:190-193` | `_sqlite_ro` (`mode=ro`) | read-only | read-only projection |
| `voicebridge/kanban.py:111-130`, `voicebridge/kanban_bridge.py:123-135` | `cfg.state_dir()/kanban.db` via `sqlite3.connect` | disjoint-store | a different file with its own authority (kanban), not the chat transcript store |
| `voicebridge/schedules.py:174-187` | `cfg.state_dir()/schedules.db` via `sqlite3.connect` | disjoint-store | schedule store |
| `voicebridge/memory/store.py:98-107`, `voicebridge/active_memory.py:35,314-363,402-464` | `memory.db` via `sqlite3.connect` | disjoint-store | memory domain |
| `voicebridge/experiments/_persist.py:21-37`, `voicebridge/verified_skills/_persist.py:25-41`, `voicebridge/communications/store.py:273-293` | their own store files via `sqlite3.connect` | disjoint-store | experiment / verified-skill / communications domains |
| `voicebridge/sqlstore.py:13-49` | shared `add_column_if_missing` / `write_txn` helpers over a caller-owned connection | disjoint-store (utility) | no store of its own |
| `voicebridge/communications/recovery_qualify.py:1159-1324,1665` | `sqlite3.connect` on scratch/copy DBs (incl. a `state.db` under a qualification root) | migration/backup (recovery tooling) | operates on copies for corruption qualification; not the live chat authority |
| `voicebridge/communications/recovery_crash.py:161-205` | installs a `sqlite3.connect` factory hook for crash notifications | disjoint-store (hook) | wraps connections to insert notifications; not a chat-store writer |

**Bypass rows: none.**

## 3. Findings

1. **No Qubit module writes the canonical chat `state.db` outside the adapter.**
   Every native write path (`/qubit-chat`, CLI, dashboard, voice, gateway RPC,
   migration destination) reaches the file through `QubitSessionStore` or the
   adapter's own `_db_for` handle.
2. **`/chat` opens no database itself.** `voicebridge/pty_ws.py` has no
   `sqlite3` / `SessionDB` symbol; it spawns the vendored `qubit --tui` child,
   which is the vendored runtime's own single writer of the same `state.db`.
   The only failure mode is the child resolving a *divergent* home (the
   operator's external `~/.hermes`), which was previously prevented only by
   `use_qubit_home` — now also guarded pre-spawn.
3. **Disjoint stores are not chat-store writers.** `kanban.db`, `schedules.db`,
   `memory.db`, the experiment / verified-skill / communications stores, and the
   recovery scratch DBs are different files with their own authorities.
4. **Latent risk (not a write-authority bypass):** `QubitSessionStore` reaches
   into vendored *private* methods (`SessionDB._execute_write`, `_write_sql`,
   `_read_all`, `_read_one`) for the Qubit-owned `qubit_session_groups` table
   and column restores. These share the vendored WAL / locking / transaction, so
   they are **not** a second writer, but they couple Qubit to vendored private
   API and are a vendored-upgrade fragility to watch. Classified here, not
   changed.
5. **Session-id drift:** native sessions use `dashboard-<hex>` ids
   (`qubit_session_store.py` `create`) and preserve migrated ids verbatim
   (`import_session`); the terminal uses the vendored runtime's own id scheme in
   the same table. No Qubit-side file or shadow id authority was found.

## 4. Invariant added (additive, no vendored changes)

`voicebridge/qubit_session_store.py`:

- `SessionDbWriteAuthorityError` — typed, `code =
  "SESSIONDB_SECOND_WRITER_REFUSED"`, with `as_dict()`.
- `refuse_external_state_db(path)` / `assert_canonical_state_db(path, cfg)` —
  refuse the operator's external `~/.hermes/state.db` and any `state.db` that is
  not the path `qubit_state_db_path(cfg)` resolves.
- `claim_state_db_writer` / `release_state_db_writer` /
  `registered_state_db_writers` — one Qubit-side writer owner per canonical
  path; a *different* owner is refused before it opens.
- `session_db_write_authority(cfg)` — typed descriptor for diagnostics.
- `_db_for` now refuses the external store and claims the writer slot before
  constructing the handle; `close_cached_db` releases the claim.

`voicebridge/pty_ws.py`:

- `terminal_state_home(cfg)` — the home the PTY child receives.
- `_assert_terminal_shares_canonical_store(qubit_home, cfg)` — refuses a
  divergent terminal home with typed `TERMINAL_STATE_HOME_DIVERGED`; wired into
  `_default_spawn` before any vendored PTY import/spawn.

## 5. Focused tests

`tests/test_sessiondb_write_authority9u.py` (hermetic, isolated XDG +
`QUBIT_HOME`, no network, no vendored spawn): canonical adapter is the
registered writer; one cached handle per path; a second-writer claim is
detected/refused; release-then-reclaim; divergent store refused; external
`~/.hermes/state.db` refused by `assert_canonical_state_db`, `_db_for`, and
`refuse_external_state_db`; `/chat` terminal home equals the adapter home; the
external home is a typed `TERMINAL_STATE_HOME_DIVERGED`; `_default_spawn`
enforces the guard before spawning.

Command:

```
python -m pytest tests/test_sessiondb_write_authority9u.py -q
```

Result: **8 passed, 0 failed**. Related focused suites re-run clean:
`test_chat_session_authority8u.py`, `test_store_consolidation.py`,
`test_chat_projections8u.py`, `test_chat_voice_e2e9u.py`,
`test_openclaw_family_sessions9u.py` (68 passed, 1 xfailed);
`test_terminal_context_floor.py`, `test_chat_terminal_parity9u.py` (46 passed);
`test_pty_ws8u.py` (12 passed); `test_gateway_rpc_listener8u.py` and the
OpenClaw family session/surface suites (55 passed).

## 6. Uncertainty (UNKNOWN / NOT VERIFIED)

- The invariant is **Qubit-side and per-process**. It cannot prove no external
  *process* writes `state.db`; the vendored TUI legitimately writes
  concurrently in its own process. This is by design (one file, WAL).
- `active_memory.py`, `communications/recovery_*`, and `sqlstore.py` were
  classified from their imports/path construction and enclosing symbols, not by
  exhaustively tracing every call site.
- `qubit_runtime._copy_state_file` can *create* the canonical `state.db` during
  one-shot legacy-home convergence when it is absent. Classified
  migration/backup (copy-only, never overwrites), not a chat-transcript writer.
- This audit is not independent verification. The final matrix has not been run
  and no gate verdict is asserted.
