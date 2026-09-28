# Qubit Migration Apply Report — 2026-09-21

**Status:** NOT VERIFIED — this is an **applied code-side migration**, not an
independent verification, not behavioral parity, and not a release claim. The
final matrix has not run and no independent read-only verifier has reproduced
this evidence. Commit/push remains forbidden (active brief §18).

**Workstream:** operator-home migration apply (sole owner).
**HEAD:** `f984911c81e119fe6fe1220510b8ac324c1224a4` (branch `main`).
**Source (read-only):** `/home/trick/.hermes`
**Destination (Qubit-owned):** `/home/trick/.local/state/qubit-voicebridge`
(runtime home `/home/trick/.local/state/qubit-voicebridge/runtime`)
**Config:** `/home/trick/.config/qubit-voicebridge/voice.yaml`
**Runner:** `.venv/bin/python` with
`VOICE_BRIDGE_CONFIG=/home/trick/.config/qubit-voicebridge/voice.yaml`.

## 1. Dry run (`plan`/`apply(confirm=False)`)

`state = DRY_RUN`; no writes. Source counts match the measured context.

```json
{
  "state": "DRY_RUN",
  "home": "/home/trick/.hermes",
  "destination": "/home/trick/.local/state/qubit-voicebridge/runtime/state.db",
  "source_sessions": 122,
  "source_messages": 22047,
  "existing_dest_sessions": 55,
  "catalog": {
    "source_home": "/home/trick/.hermes",
    "destination_home": "/home/trick/.local/state/qubit-voicebridge/runtime",
    "skills":  {"source": 162, "destination": 60},
    "plugins": {"source": 1,   "destination": 0},
    "mcp":     {"source": 2,   "destination": 0}
  }
}
```

## 2. Apply (`apply(cfg, confirm=True)`) — first run

`state = OK`, no errors. Only Qubit-owned storage was written; `~/.hermes` was
opened `mode=ro` throughout.

| Field | Value |
|---|---|
| `imported_sessions` | **122** |
| `skipped_sessions` | **0** |
| `imported_messages` | **22047** |
| `skipped_roles` | **0** |
| `errors` | `[]` |
| `added_files` (count) | **969** (skills 963, plugins 3, cron 3) |
| `backup_dir` | `/home/trick/.local/state/qubit-voicebridge/hermes-migration/20260921-232324` |
| `destination` | `/home/trick/.local/state/qubit-voicebridge/runtime/state.db` |
| `model_profile` | `qubit-model` state `OK`, key_ref `env:OPENCODE_GO_API_KEY`, context_tokens 0; 13 auxiliary sections enumerated |
| `cron` | +2 jobs (`b8d60fe64add`, `3e396bb6e6a5`) |
| `catalog` (post) | skills 162→**203** (143 new SKILL.md; dest had 60 pre-existing), plugins 1→**1**, mcp 2→**2** |

**MCP report (first run):** source_count 2, imported 2
(`cgraph-graft`, `openclaw-voice-bridge-graft`), skipped 0, conflicts 0,
unsupported 0. Only `env:`/reference values were written — no secret values.

**Created files (journal):**

```
/home/trick/.local/state/qubit-voicebridge/mcp_servers.json
/home/trick/.local/state/qubit-voicebridge/runtime/shared-state.db
/home/trick/.local/state/qubit-voicebridge/runtime/cron/executions.db
/home/trick/.local/state/qubit-voicebridge/runtime/cron/jobs.json
```

**Aux DBs snapshotted (WAL-safe, created only because absent):**
`runtime/shared-state.db`, `runtime/cron/executions.db`.

**Conflicts / unsupported items (first run):**

- MCP conflicts: **none**.
- Plugin enabled-state: **UNSUPPORTED**, code `PLUGIN_STATE_NO_PUBLIC_API`.
  Source plugin `hermes-achievements` read as state `UNKNOWN`; no safe public
  API exists to record a plugin enable/disable state, so nothing was fabricated.
- Credentials: **SAFE_EQUIVALENT**, code `CREDENTIALS_NOT_MIGRATED` — source
  `auth.json` holds provider token material; Qubit migrates references, not
  secrets. Counterpart: `qubit auth login --provider`.
- Skill disabled-state: **NOOP** — source has no disabled skills.
- Dropped MCP env values without a resolvable reference: none.

**Backup dir:** `/home/trick/.local/state/qubit-voicebridge/hermes-migration/20260921-232324`
(contains `source/state.db` (247 MB, SQLite-backup snapshot), `source/` auxiliary
DBs, `destination/state.db`, `destination/asset__*` pre-write copies of
`config.yaml`/`profiles.json`, and `journal.json`).

**Rollback command (recorded in `journal.json`):**

```
qubit product legacy-rollback --backup-dir /home/trick/.local/state/qubit-voicebridge/hermes-migration/20260921-232324 --apply
```

## 3. Idempotency (second + third apply)

Second `apply(cfg, confirm=True)`:

```json
{
  "state": "OK",
  "imported_sessions": 0,
  "skipped_sessions": 122,
  "imported_messages": 0,
  "skipped_roles": 0,
  "added_files": 0,
  "errors": [],
  "mcp": {"imported": [], "skipped": ["cgraph-graft", "openclaw-voice-bridge-graft"],
          "conflicts": [2 entries: "destination record exists; never overwritten"],
          "unsupported": []},
  "cron": {"added": 0, "ids": []},
  "skill_state": {"state": "NOOP"},
  "catalog": {"skills": {"source": 162, "destination": 203},
              "plugins": {"source": 1, "destination": 1},
              "mcp": {"source": 2, "destination": 2}}
}
```

Destination snapshot (sha256 + size + mtime) of the migration-touched files
(`runtime/state.db`, `runtime/config.yaml`, `runtime/cron/jobs.json`,
`state/profiles.json`, `state/mcp_servers.json`) was taken immediately before
and after the second apply, with a 15 s control interval proving the live
destination was otherwise quiet.

- **First idempotency run (before the fix):** all hashes identical; only
  `profiles.json` **mtime** moved — `upsert_model_profile` rewrote the registry
  file even though the content was byte-identical (`sha256 88ba96ff…`, size 826).
- **Fix (in `voicebridge/qubit_migration.py`, this workstream's file):**
  `_import_model_profile` now skips the upsert when an identical destination
  record already exists and reports `state: "EXISTS"`. No resolver/API change.
- **After the fix (third apply):** snapshot diff **IDENTICAL** (content **and**
  mtime stable); `imported_sessions == 0`, `skipped_sessions == 122`.

Each re-run still writes a fresh timestamped backup directory under the
Qubit-owned state dir (e.g. `20260921-232440`, `20260921-232513`); the rollback
command for the actual import remains the **first** backup dir above.

## 4. Source `~/.hermes` unchanged

`stat` + `find` listing (path/size/mtime) captured before and after the apply
runs. Required items — `state.db`, `config.yaml`, `.env`, `skills/`,
`plugins/` — show **no diff** (state.db mtime to the nanosecond unchanged).

The only source churn observed is attributable to the independent, live Hermes
daemon that holds the source home open (not to the migration, which opens every
source DB `mode=ro`):

- `state.db-wal` / `state.db-shm`: same size, mtime moved (live daemon holding
  the DB open; the migration never writes them).
- `cron/ticker_heartbeat`, `cron/ticker_last_success`, `cron/.tick.lock`: the
  Hermes cron ticker heartbeat advances every ~2 minutes.

No file under `/home/trick/.hermes` was created, modified, moved, or deleted by
the migration.

## 5. Focused tests

```
.venv/bin/python -m pytest tests/test_migration_apply9u.py -q
# 2 passed in 3.96s
.venv/bin/python -m pytest tests/test_qubit_migration_b04m.py tests/test_catalog_sync9u.py -q
# 13 passed in 4.42s
```

`tests/test_migration_apply9u.py` (new, hermetic — synthetic source only, never
the real home) proves:

1. **destination-only writes** — the synthetic source tree is byte-identical
   after apply, the backup lives under Qubit state, and a dry run writes
   nothing;
2. **backup + journal** — `backup_dir/{source,destination}` and `journal.json`
   exist with the recorded `rollback` command;
3. **rollback restores** — a pre-seeded destination session survives while the
   imported session is removed and the created `profiles.json` is deleted;
4. **second apply is a no-op** — `imported_sessions == 0`, `skipped == source`,
   and the destination fingerprint (sha256 + size + mtime) is unchanged.

## 6. Files changed by this workstream

- `voicebridge/qubit_migration.py` — idempotency guard in
  `_import_model_profile` (skip identical existing profile; no resolver edit).
- `tests/test_migration_apply9u.py` — new hermetic test (2 tests).
- `docs/QUBIT_MIGRATION_APPLY_REPORT_2026-09-21.md` — this report.

No other file was touched. No `git commit`/`push`/`add`/`reset`/`checkout` was
run. No mutating HTTP request was sent to any running server.

## 7. Rollback instructions

To undo the applied import (restores the destination store to its pre-import
state and removes files the import created):

```
qubit product legacy-rollback \
  --backup-dir /home/trick/.local/state/qubit-voicebridge/hermes-migration/20260921-232324 \
  --apply
```

Dry-run first (omit `--apply`) to preview. The command is also written verbatim
in that backup's `journal.json` (`rollback` field). The `~/.hermes` source is
never touched by rollback.
