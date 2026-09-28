# Packet 8J — end-to-end recovery rehearsal (operator runbook)

Status: **IMPLEMENTED** — hermetic recovery rehearsal is safe to run now. Packet
8J is hermetic-only: there are NO live stages. Every stage rehearses crash /
recovery boundaries in an ISOLATED temporary state root and never touches a live
service, account, browser, timer, or the production state. It never runs
destructive account actions, sends real messages, fakes remote rollback, or uses
production state as a test fixture.
Source contract: `docs/UNIFIED_AGENT_PLAN.md` §8J, Packet 8J prompt-pack prompt.

## 1. What this packet rehearses

It rehearses the already-verified browser, audio, timer, notification, report,
cursor, and action contracts against deterministic injected failures, and closes
the known finding #6 (partial rollback must be visible and block resume):

| 8J requirement | Where it is proven |
|---|---|
| **Deterministic SIGKILL at every crash boundary** (child process) | `--crash-kill`: 6 file-write boundaries + 8 store-commit boundaries |
| File write/flush/fsync/rename crash boundaries | `--crash-kill` (`file_*`): temp never served, prior state intact until rename, new state durable after rename |
| Cursor / notification commit crash boundaries | `--crash-kill` (`store_*`): mid-transaction kill rolls back atomically; after_commit is fully durable; no partial cursor/report/notification |
| **fsync failure injection** (real `os.fsync` fails EIO) | `--fsync-failure`: typed failure, prior state intact, no partial serve, retry idempotent |
| **atomic-rename / os.replace failure injection** | `--rename-failure`: temp never exposed, prior valid bundle intact, recovery leaves no partial state |
| **ENOSPC / disk-full, honestly labeled** | `--enospc`: deterministic `ENOSPC` (errno 28, physical disk-full class) **and** kernel-enforced `RLIMIT_FSIZE` `EFBIG` (errno 27, **quota/file-size limit — not physical disk-full**, labeled as such); typed failure, no partial target, reopen recovers after the condition clears |
| **Corrupt SQLite** (truncated / malformed / interrupted migration / corrupt WAL) | `--corrupt-sqlite`: fail closed, corrupt file preserved (never silently replaced by an empty store), valid backup restored with hash validation; **missing backup** and **partial/corrupt backup** also fail closed and block resume |
| **Newer schema** | `--newer-schema`: the 8J recovery guard refuses a newer-schema state, never downgrades/overwrites it, never claims recovery, and a correct-code copy recovers |
| **Resource enforcement + measurements** | `--resources`: real rlimits (CPU/AS-memory/fsize/nofile/nproc/threads) in a child; a **genuinely hanging child is SIGKILLed by the parent and reported as a typed wall-timeout**; actual elapsed wall, peak RSS, CPU time, bytes written, file count, thread count, **measured process count**, and a **network probe executed BEFORE the workload that is verified blocked**; exceeding each limit is a typed failure |
| **Measurable RPO/RTO via the real restore path** | `--rpo-rto`: last-durable ts, newest-recoverable ts, data-loss interval, recovery start/end + duration, RPO/RTO target met; recovery uses `portable.restore_state` (atomic staging) and a store-DB atomic restore (temp+fsync+rename), NOT a bare `write_bytes` replacement |
| Atomic boundary hash/schema validation before resume | `--atomic-boundaries`: store schema + sha256 stable across reopen; receipts hash stable; journal truncation recovered |
| **Partial rollback visible + blocks resume (finding #6)** | `--partial-rollback`: `PartialRollbackError` with exact bounded paths, resume blocked, no success claim, unaffected paths intact, later recovery completes |
| **Repeated recovery x3** — stable cursor, no duplicate report/notification/receipt/audit, no silent loss, no secret leak | `--repeat-recovery` |
| **Hermetic guarantees** | `--hermetic`: private temp permissions (0700/0600), isolated-from-production state dir, network blocked in a child BEFORE the workload, `--live` fails closed |
| **Summary persistence fail-closed** | every run: atomic temp+fsync+rename at `0600`; if write/flush/fsync/rename fails, the run returns a typed failure and never claims `OK` (`_persist_summary_boundaries` tests each boundary) |
| Notification retry / dead-letter bounds, dedupe, restart persistence | `--notify-retry` |
| Report-bundle atomic publish, partial/corrupt bundle, missing evidence typed | `--report-bundle`: atomic publish, corrupt manifest refused, **corrupt evidence hash (sha256/byte-count) refused**, **symlink escape refused** (asset + index integrity), stale stage not served |
| Backup/restore/rollback; wrong passphrase typed; dry-run no-write | `--portable-restore` |
| Stale lease / overlap / renewal / re-acquire after crash | `--lease` |
| Truncated/corrupt JSONL journal tolerated, no secret leak | `--journal` |
| Action receipt crash recovery (running -> interrupted), corrupt-state fail-closed | `--action-receipts` |
| Read-only store write fails typed and recovers | `--disk-state` |
| Concurrent manual/timer/action access isolation; OVERLAP denial | `--concurrent` |
| No live account/browser/audio/timer/service touched | every stage (`isolated: true`, hermetic-only `--live` fails closed) |

## 2. The harness

- `voicebridge/communications/recovery_qualify.py` — staged, bounded, isolated
  recovery rehearsal (parent).
- `voicebridge/communications/recovery_crash.py` — the child-process crash/fault
  worker: real atomic writes, real store commits, fsync/rename/write failure
  injection, `RLIMIT_FSIZE`/CPU/AS/nofile/nproc enforcement, a genuine `hang`
  mode for wall-timeout tests, a pre-workload network probe, and real resource
  measurement (including process count).
- `scripts/recovery_qualify.py` — CLI (`recovery-qualify`).
- `tests/test_recovery_qualify.py` — hermetic focused tests (42).
- `docs/OPERATIONS_8J.md` — this runbook.

Output is machine-readable JSON with **typed** results. Every stage uses a fresh
per-run isolated root under `<state-dir>/runs/<run_id>` (default
`<config state>/qualification-recovery`). Live execution is **impossible by
default**: `--live` always returns `LIVE_NOT_AUTHORIZED` because 8J has no live
stages.

## 3. Preflight (safe, read-only)

```bash
.venv/bin/python scripts/recovery_qualify.py --preflight
```

Reports, in one JSON record:

- `recovery.rpo_seconds` / `recovery.rto_seconds` — operator-declared targets
  (from config `recovery.rpo_seconds` / `recovery.rto_seconds`, defaults
  3600 / 300). The `--rpo-rto` stage measures them and FAILS if not met.
- `resource_ceilings` — asserts every stage runs in isolated temporary state,
  that destructive account actions are forbidden, that every child is bounded
  by wall-clock timeout + cpu/as/fsize/nofile/nproc limits, and that network is
  disabled in the child. **These are hard-enforced** (see `--resources`).
- `journal.jsonl_bounded` — the durable JSONL journal is bounded.

Nothing is written to production state by this stage.

## 4. Stage commands

```bash
.venv/bin/python scripts/recovery_qualify.py --preflight
.venv/bin/python scripts/recovery_qualify.py --crash-kill
.venv/bin/python scripts/recovery_qualify.py --fsync-failure
.venv/bin/python scripts/recovery_qualify.py --rename-failure
.venv/bin/python scripts/recovery_qualify.py --enospc
.venv/bin/python scripts/recovery_qualify.py --corrupt-sqlite
.venv/bin/python scripts/recovery_qualify.py --newer-schema
.venv/bin/python scripts/recovery_qualify.py --resources
.venv/bin/python scripts/recovery_qualify.py --rpo-rto
.venv/bin/python scripts/recovery_qualify.py --atomic-boundaries
.venv/bin/python scripts/recovery_qualify.py --partial-rollback
.venv/bin/python scripts/recovery_qualify.py --repeat-recovery
.venv/bin/python scripts/recovery_qualify.py --hermetic
.venv/bin/python scripts/recovery_qualify.py --crash-commit
.venv/bin/python scripts/recovery_qualify.py --lease
.venv/bin/python scripts/recovery_qualify.py --notify-retry
.venv/bin/python scripts/recovery_qualify.py --report-bundle
.venv/bin/python scripts/recovery_qualify.py --portable-restore
.venv/bin/python scripts/recovery_qualify.py --journal
.venv/bin/python scripts/recovery_qualify.py --action-receipts
.venv/bin/python scripts/recovery_qualify.py --disk-state
.venv/bin/python scripts/recovery_qualify.py --concurrent
.venv/bin/python scripts/recovery_qualify.py --idempotent
.venv/bin/python scripts/recovery_qualify.py --all
```

`--all` runs every stage. Add `--dry-run` to avoid persisting the redacted
summary. Add `--state-dir <path>` to place the isolated rehearsal state
elsewhere (never the production state). `--fixture <json>` overrides the
synthetic scan fixture (default: repository fixtures).

## 5. Hermetic focused tests

```bash
.venv/bin/python -m pytest tests/test_recovery_qualify.py -q -W error
```

Expected: `42 passed`. These cover each stage plus direct contract tests for
`PartialRollbackError` path visibility, `_guard_newer_schema`, the SIGKILL
crash helpers, fault-injection helpers, `_snapshot_state`, secret scanning,
orchestration summary persistence (`0600`, redacted, no raw check dicts,
atomic rename + every persistence-failure boundary fails closed), dry-run
no-persist, network-probe-before-workload, process-count measurement, ENOSPC
honesty (EFBIG labeled quota), and stage filtering.

## 6. Deterministic crash/fault injection details

Every crash/fault runs in a **child process** (`python -m
voicebridge.communications.recovery_crash`):

- **SIGKILL at a boundary**: the child writes a `reached-<boundary>` marker and
  blocks; the parent sees the marker and SIGKILLs the child at exactly that
  point, then inspects the on-disk state.
- **File boundaries** (`before_write`…`after_rename`) drive the REAL atomic
  writer (`portable._write_file_atomic`) with OS-layer hooks.
- **Store boundaries** (`before_commit`…`after_commit`) drive a REAL
  `CommunicationsStore.commit_new` (baseline + new-item + notification) with
  SQLite `execute` hooks. Mid-transaction kills roll back atomically to the
  baseline; `after_commit` is fully durable. This proves no partial cursor /
  report / notification, no cursor jump, no duplicate.
- **fsync / rename / ENOSPC**: the child patches `os.fsync` / `os.replace` /
  `os.fsync(ENOSPC)` to raise at a controlled call while running the real
  writer, then reports the typed failure. Retry without injection is verified
  idempotent.
- **Quota/file-size (not physical disk-full)**: the child applies `RLIMIT_FSIZE`
  so the kernel rejects the write with `EFBIG` (errno 27). The result is
  labeled **quota/file-size limit**, explicitly distinguished from the
  deterministic `ENOSPC` (errno 28) physical-disk-full injection.
- **Genuine wall-timeout**: the `hang` child sleeps forever; the parent waits a
  short window then SIGKILLs it and reports a typed wall-timeout, then verifies
  idempotent recovery on a fresh isolated store.

## 7. Resource enforcement and measurements

Every child stage is bounded by the parent wall-clock timeout
(`CHILD_TIMEOUT_S`). The `--resources` stage applies REAL rlimits and enforces
each one as a typed failure:

- **CPU**: `RLIMIT_CPU` soft<hard + busy loop; the SIGXCPU handler writes a
  typed `CPU_LIMIT_EXCEEDED` result before the hard SIGKILL backstop (the
  parent requires that typed error code, not any nonzero return);
- **address-space/memory**: `RLIMIT_AS` + large allocation → `MemoryError`;
- **file-size/output**: `RLIMIT_FSIZE` + store writes → `EFBIG`/`OperationalError`;
- **open-fd limit**: `RLIMIT_NOFILE` + many open descriptors → `EMFILE`
  (labeled as the descriptor limit it is);
- **created-file count**: `manyfiles_created` creates distinct files until the
  configured ceiling raises a typed `FileCountLimitError` — a REAL count of
  files created, distinct from the open-fd limit;
- **process-count**: `RLIMIT_NPROC` + `fork` → `EAGAIN`;
- **thread-count**: `RLIMIT_NPROC` + thread creation → failure;
- **wall-clock**: a genuinely hanging child is SIGKILLed by the parent → typed
  wall-timeout, then idempotent recovery.

The `resource_ok` run reports ACTUAL measured values:

- elapsed wall seconds (parent clock);
- peak RSS (`getrusage` `ru_maxrss`, KiB);
- CPU time (`ru_utime + ru_stime`);
- bytes written and file count under the isolated state root;
- thread count and **measured process count** (scanned from `/proc`, not
  hardcoded);
- a **network probe executed BEFORE the workload** that attempts a real socket
  connection and is verified blocked — the actual probe result is reported, not
  a hardcoded `network_used: false`.

`resource_over` applies a tighter `RLIMIT_FSIZE` so the store write exceeds it;
the child returns a typed failure and the isolated state is discarded fresh per
stage (recovery is re-run on a clean isolated root).

## 8. Partial-rollback visibility (finding #6)

`restore_state` in `voicebridge/portable.py`:

- performs a per-path pre-restore backup;
- on a restore failure, restores every touched path from that backup;
- if ANY rollback step fails, raises `PartialRollbackError` with the **exact
  bounded paths** that could not be restored;
- never claims "rolled back" when rollback is incomplete.

The rehearsal proves this deterministically (`--partial-rollback`): it seeds a
target memory dir, injects a restore failure after the backup exists, makes the
rollback of that path fail (read-only dir), asserts the typed
`PartialRollbackError` with `failed_paths`, that unaffected paths remain intact,
and that a later recovery can safely complete.

## 8a. RPO/RTO via the real restore path

`--rpo-rto` measures recovery using the REAL backup/atomic-restore machinery:

- a portable-state bundle is exported and `plan_restore` writes the pre-restore
  backup (newest-recoverable snapshot);
- durable state is advanced past the backup (growing the data-loss interval);
- recovery runs through `portable.restore_state` (temp+fsync+rename staging) and
  a store-DB atomic restore via `portable._write_file_atomic` — never a bare
  `db.write_bytes` replacement;
- the stage records last-durable ts, newest-recoverable ts, measured
  data-loss interval, recovery start/end and duration, and FAILS if the
  configured RPO/RTO targets are not met;
- the restored portable bundle is re-exported and its stable memory-fact
  hashes are compared against the recoverable snapshot (volatile
  `created_at`/`updated_at` timestamps are excluded); the store restore also
  validates the restored hash before reopening.

## 8b. Summary persistence fail-closed

Every non-dry-run rehearsal persists a redacted summary at `0600` using
temp-file + `fsync` + `os.replace` (atomic rename) **and** an `fsync` of the
directory entry after the rename. If writing, flushing, fsyncing the file,
fsyncing the directory, or renaming the summary fails, the run returns a typed
failure and never claims `OK`. `_persist_summary_boundaries` exercises each of
the five failure boundaries (write / flush / fsync / dir-fsync / rename) and
asserts fail-closed behavior with no partial file.

## 9. What is never done

- No live service, account, browser, timer, or production state is touched.
- No destructive account actions; no real messages sent.
- No remote rollback is faked; a rollback that cannot fully restore is reported
  as a typed failure and blocks resume.
- No production state is used as a test fixture.
- `--live` has no effect and always fails closed (`LIVE_NOT_AUTHORIZED`).
- Persisted summaries contain no report IDs, URLs, senders, subjects, bodies,
  tokens, cookies, or secrets.

## 10. Rollback of the rehearsal itself

The rehearsal writes only inside its isolated state dir
(`<config state>/qualification-recovery` by default). Removing that directory and
deleting the new files (`voicebridge/communications/recovery_qualify.py`,
`voicebridge/communications/recovery_crash.py`, `scripts/recovery_qualify.py`,
`tests/test_recovery_qualify.py`, `docs/OPERATIONS_8J.md`) fully reverts this
packet. The only production-code change is the `PartialRollbackError` +
rollback-visibility repair in `voicebridge/portable.py`; reverting that block
restores the prior behavior. No live state is changed, so no further rollback is
needed.