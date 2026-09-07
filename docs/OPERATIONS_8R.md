# Operations — Packet 8R supervised OpenCode coding adapter (repair 2026-09-07)

Status: implementation (hermetic) + verification-driven repair. No live
coding run is authorized by this packet. Every command below is read-only or
synthetic unless it says **live**.

Architecture (plan §6.2 gate): the local WRAP is `voicebridge/opencode/`
— focused modules, each under 300 lines, no new runtime dependency:
`policy` (pin/budgets/validation), `transport` (env/argv/probe/confined
spawn/parsing), `workdir` (worktrees/validation/sweep/diffs), `specs` (8F
contracts), `runs` (spawn-safe executors), `state` (enablement/config),
`runner` (broker-bound runner + shared entry). Same public surface as the
former monolith (`voicebridge.opencode.*` imports unchanged).

Containment boundary: every live child runs inside Bubblewrap (ro /usr +
merged-usr symlinks, /proc, /dev, private /tmp, private empty HOME, ro
network prerequisites, pinned binary single-file ro-bind, approved workdir
read-write). The original checkout, other checkouts, real HOME, credentials,
host /tmp, and system dirs do not exist inside. The network namespace is
deliberately retained (provider API calls); writes the child makes outside
its workdir land in discarded sandbox storage. If Bubblewrap/user-namespaces
are unavailable, live execution is disabled as CONFINEMENT_UNAVAILABLE —
never an unconfined run. A checkout tripwire additionally fails any run that
dirties the project checkout outside its disposable worktree
(`PROJECT_TOUCHED`).

Channel parity (honest contract): CLI has FULL parity (grant, preview,
prepare, execute, cancel, receipts, enable/disable); the authenticated
dashboard has READ-ONLY parity (`GET /api/opencode`: status/list/check on the
same runner/broker). Voice and tray execution are INTENTIONALLY NOT WIRED:
voice coding requests stay on the existing OpenClaw/Hermes dispatch and no
tray execution surface exists. Any future wiring must go through
`request_coding_run` — direct subprocess, shell, and MCP paths stay
forbidden.

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

# 2d. In-project writes: preview hash is MANDATORY (mechanically enforced —
refused as PREVIEW_REQUIRED without --expected-hash)
voicebridge opencode execute --project /path/to/checkout --in-project \
  --task "Add a hello function" --confirm --expected-hash <hash-from-2b>

# 2e. Receipts / cancel
voicebridge opencode receipts --limit 20
voicebridge opencode cancel <receipt_id>

# 2f. Orphan recovery (crash/cancel can leave a disposable worktree behind;
# each new disposable run also sweeps bases older than 2h automatically)
# Safe manual cleanup: delete /tmp/vb-opencode-* and run, for each affected
# project root: git worktree prune
```

Prompts containing shell metacharacters are rejected at the shared 8F
boundary — put task text in a file INSIDE the project and use `--task-file`
instead (paths outside the approved root are refused, symlinks are resolved
and escapes refused, secret-named files are refused). Active runs are
cancelled idempotently; timeouts SIGKILL the whole process group.

## 3. Typed states

`OK` · `preview` · `denied` (`NOT_GRANTED` / `CONFIRMATION_REQUIRED` /
`CONFIRMATION_SOURCE_FORBIDDEN`) · `failed` (`RUN_REJECTED` /
`BINARY_MISSING` / `VERSION_MISMATCH` / `VERSION_UNKNOWN` /
`MISSING_CREDENTIALS` / `DISPOSABLE_UNAVAILABLE` / `TIMEOUT` / `EXIT_<n>` /
`EMPTY_OUTPUT` / `MALFORMED_OUTPUT` / `PATCH_MALFORMED` /
`TRANSPORT_MALFORMED` / `LIMIT_EXCEEDED` / `SYMLINK_ESCAPE` /
`PROJECT_TOUCHED` / `VALIDATION_CRASH` / `PROVIDER_OUTAGE` / `RUN_CRASH` /
`SPAWN_FAILED` / `SERIALIZATION_FAILED`) · `stale_target` (preview hash
changed, or `PREVIEW_REQUIRED` for in-project writes without a hash) ·
`idempotent_replay` · `cancelled` · `replayed` (single-use confirmation
re-use refused) · `ADAPTER_DISABLED``
(`CONFIG_DISABLED` / `NOT_ENABLED` / `EMERGENCY_STOP`).

A failed or partial run is never reported complete. Code changes are
uncommitted working-tree edits only (`rollback: impossible` by contract —
review the diff, `git revert` when needed). Nothing ever auto-commits,
pushes, publishes, installs dependencies, or touches services/permissions.

## 4. Live qualification (separately authorized only)

- Requires: explicit operator authorization, a disposable worktree
  (`--in-project` forbidden for first qualification), pinned binary
  `1.18.29`. A differing installed version fails closed as
  `VERSION_MISMATCH` (operator-gated: re-qualify or update the pin); an
  undeterminable version fails as `VERSION_UNKNOWN`. `status` shows
  installed vs pinned but spawns the probe ONLY while effectively enabled.
- The child inherits ONLY `PATH`/`LANG`/`LC_ALL`/`LC_MESSAGES`/`TZ`. There
  is no environment pass-through knob: HOME, PYTHONPATH, LD_PRELOAD,
  shell/XDG/session variables, Git overrides, credentials, keys, tokens,
  and cookies are rejected by construction. Provider credentials must be
  supplied to OpenCode through its own explicit operator-managed login;
  provider-side auth failures surface as typed `MISSING_CREDENTIALS`.
- The child is confined with setrlimit ceilings (CPU = budget + 60s,
  4 GiB address space, 512 MiB files, 128 processes, 256 fds) plus its own
  process group for kill/reap. Generated worktrees are validated before
  success is reported (≤1000 files, ≤100 MiB, no outside symlinks, bounded
  diff); violations fail closed as `LIMIT_EXCEEDED`/`SYMLINK_ESCAPE` with
  partial results marked honestly.
- Dashboard parity is read-only: `GET /api/opencode` (status/list/check).
  Mutations stay on the authenticated CLI behind the same runner/broker.

## 5. Rollback

- Adapter: `voicebridge opencode disable` (or `reset` to clear latches).
- Code: `git revert <8R-commit>` — no migration, no state format change;
  deleting `<state_dir>/opencode_adapter.json` returns to disabled.
- Worktrees: disposable bases live under `$TMPDIR/vb-opencode-<run_id>`
  (0700) and are removed with `git worktree remove --force` after each run;
  orphans are safe to delete — they contain no credentials.
