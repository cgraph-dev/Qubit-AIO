# Operations — Packet 8R supervised OpenCode coding adapter

Status: implementation (hermetic). No live coding run is authorized by this
packet. Every command below is read-only or synthetic unless it says
**live**.

## 1. Enablement (two-step, owner only)

1. Packaged master switch: `intent.opencode.enabled: true` in the effective
   config (default `false`). Check with `voicebridge opencode status`.
2. User flag: `voicebridge opencode enable` (writes 0600
   `<state_dir>/opencode_adapter.json`). Disable any time with
   `voicebridge opencode disable`; latch an immediate stop with
   `voicebridge opencode emergency-stop` (cleared by `opencode reset`).

Both must be true, with no emergency-stop latch, or every run returns typed
`ADAPTER_DISABLED` and no subprocess is spawned.

## 2. One bounded run (CLI)

```sh
# 2a. Grant the capability in the shared 7E store (dry-run unless --confirm)
voicebridge opencode grant act.opencode.run --scope code --confirm

# 2b. Preview (spawns nothing) and keep the action hash
voicebridge opencode preview --project /path/to/checkout \
  --task "Add a hello function"

# 2c. Execute: disposable worktree by default (project checkout untouched)
voicebridge opencode execute --project /path/to/checkout \
  --task "Add a hello function" --confirm

# 2d. In-project writes: preview hash is mandatory
voicebridge opencode execute --project /path/to/checkout --in-project \
  --task "Add a hello function" --confirm --expected-hash <hash-from-2b>

# 2e. Receipts / cancel
voicebridge opencode receipts --limit 20
voicebridge opencode cancel <receipt_id>
```

Prompts containing shell metacharacters are rejected at the shared 8F
boundary — put them in a file and use `--task-file` instead. Active runs are
cancelled idempotently; timeouts SIGKILL the whole process group.

## 3. Typed states

`OK` · `preview` · `denied` (`NOT_GRANTED` / `CONFIRMATION_REQUIRED` /
`CONFIRMATION_SOURCE_FORBIDDEN`) · `failed` (`RUN_REJECTED` /
`BINARY_MISSING` / `MISSING_CREDENTIALS` / `DISPOSABLE_UNAVAILABLE` /
`TIMEOUT` / `EXIT_<n>` / `EMPTY_OUTPUT` / `PROVIDER_OUTAGE` / `PATCH_MALFORMED`
/ `RUN_CRASH` / `SERIALIZATION_FAILED`) · `stale_target` (preview hash
changed) · `idempotent_replay` · `cancelled` · `ADAPTER_DISABLED`
(`CONFIG_DISABLED` / `NOT_ENABLED` / `EMERGENCY_STOP`).

A failed or partial run is never reported complete. Code changes are
uncommitted working-tree edits only (`rollback: impossible` by contract —
review the diff, `git revert` when needed). Nothing ever auto-commits,
pushes, publishes, installs dependencies, or touches services/permissions.

## 4. Live qualification (separately authorized only)

- Requires: explicit operator authorization, a disposable worktree
  (`--in-project` forbidden for first qualification), pinned binary
  `1.18.29` (`status` shows installed vs pinned).
- The child inherits only `PATH`/`LANG`/`LC_ALL` plus configured `pass_env`
  (secret/session names refused). Provider credentials must be supplied
  explicitly; absence fails closed as `MISSING_CREDENTIALS`.
- Dashboard parity is read-only: `GET /api/opencode` (status/list/check).
  Mutations stay on the authenticated CLI behind the same runner/broker.

## 5. Rollback

- Adapter: `voicebridge opencode disable` (or `reset` to clear latches).
- Code: `git revert <8R-commit>` — no migration, no state format change;
  deleting `<state_dir>/opencode_adapter.json` returns to disabled.
- Worktrees: disposable bases live under `$TMPDIR/vb-opencode-<run_id>`
  (0700) and are removed with `git worktree remove --force` after each run;
  orphans are safe to delete — they contain no credentials.
