# Rebrand audit — findings and the actionable set

From `deleg_2339c615` agent 5 (verified independently where it mattered).

## Counts (production `.py`, excluding tests/optional-skills)

**5,402 matching lines across 989 files.** Breakdown:

| class | count | action |
|---|---|---|
| (a) COMPAT ALIAS — deliberate, load-bearing | ~16 defs | **KEEP** — imported code calls them |
| (b) ENV VAR `HERMES_*` | ~2,382 occurrences (382 direct `os.environ.get` reads, ~40 files) | case-by-case; only `HERMES_HOME` has a `QUBIT_` twin |
| (c) GENUINE LEFTOVER | ~26 user-visible/persisted + ~17 internal | **ACTIONABLE** |
| (d) COSMETIC (comments/docstrings) | ~2,533 | ignore |

## (a) Compat aliases — these exist for a reason, deleting them breaks call sites

- `cli.py:1805` — `HermesCLI = QubitCLI`
- `qubit_constants.py:199-203, 1500-1503` — 10 aliases: `get_hermes_home`,
  `get_hermes_dir`, `display_hermes_home`, `hermes_home_key`, etc.

## (c) Genuine leftovers — the actionable list

**User-visible / persisted (c1)** — these leak the old brand to users or to disk:
- Windows scheduled task `Hermes_Gateway`
- systemd service base `hermes-gateway`
- dashboard action `hermes-update`
- Telegram setup bot `HermesSetupBot`
- IRC bot `hermes-bot`
- debug User-Agent / multipart boundary
- dot-dirs `.hermes-update-staging`, `.hermes-update-old`
- state key `"hermes_version"`
- `hermes_kernel_` prefix

**Internal (c2)** — ~17, lower priority.

**PascalCase classes with ZERO Qubit twin** (real, not aliased):
- `HermesOverlay` (45 refs), `HermesConsoleEngine` (25), `HermesACPAgent` (6 prod / 35 test),
  `HermesMCPOAuthProvider` (5)

## Already DONE — verified present under Qubit names

`QubitTokenStorage` (125 refs), `QubitIndexSource`, `QubitProviderMixin`,
`QubitOAuthClientProvider`, `QubitArgumentParser`, `QUBIT_HERO`, `QUBIT_AGENT_LOGO`.
The known "~55 bare renames" are mostly handled.

## Two real bugs found and fixed

### 1. `QubitConsoleEngine` did not exist — the test file was UNCOLLECTABLE

`qubit_cli/console_engine.py` defines `HermesConsoleEngine` (line 378) but never got
rebranded, while `tests/qubit_cli/test_console_engine.py` imports `QubitConsoleEngine`:

```
ImportError: cannot import name 'QubitConsoleEngine' from 'qubit_cli.console_engine'
```

**Zero tests in that file could run.** Fixed by aliasing at the definition site
(`QubitConsoleEngine = HermesConsoleEngine`) so exactly one object exists under both
names. Verified: `QubitConsoleEngine is HermesConsoleEngine` -> True. The module now
collects and runs **11 passed / 1 failed** where it previously collected 0.

The remaining failure is the TEST being stale, not the code: the engine correctly prints
`"Qubit Console"`, the test asserts `"Hermes Console"`.

### 2. `HERMES_AGENT_LOGO` still documented but renamed

The symbol became `QUBIT_AGENT_LOGO` in code, but shipped
`website/docs/.../environment-variables.md` still documents the old name.

## The trap NOT to fall into: `.hermes-update-in-progress`

`qubit_cli/update_lock.py` uses `.hermes-update-in-progress` where newer tests expect
`.qubit-update-in-progress`. **Do NOT rename it in Python alone.** It is a cross-language
lock contract shared byte-for-byte by:
- Python `update_lock.py`
- Rust `paths.rs:90`
- Electron `update-marker.ts:34`

All three currently agree on the legacy name. Renaming Python alone lets two updaters run
concurrently and corrupt the install. Any rename must be atomic across all three owners,
with a migration for an in-progress update.

## What a complete rebrand requires

1. Fix class names with zero Qubit twin: `HermesOverlay`, `HermesConsoleEngine`,
   `HermesACPAgent`, `HermesMCPOAuthProvider` — **via aliases or rename-with-call-site-update,
   never a bare rename alone.**
2. Fix the c1 user-visible/persisted strings (service names, task names, dot-dirs).
3. Extend the rebrand transform to emit aliases automatically for PascalCase renames.
4. Update stale tests that assert the OLD name while the code prints the new one
   (found one already: `test_console_engine.py`).
5. Leave the `HERMES_*` env vars alone unless a migration exists — external configs and
   `.hermes/` directories depend on them.

## Method note

Two agents in this batch independently claimed the short-port list was "inflated by
rebrand artifacts" and that specific files had "0 real gaps". I checked both by hand:
**`plugins/plugin_loader.py` has 5 genuinely missing symbols and `cron/scheduler.py` has
at least 1** (`_record_external_cron_worker`) — neither is a rebrand artifact. The
suspicion was healthy; the conclusions were wrong. Verify before applying a correction,
in both directions.
