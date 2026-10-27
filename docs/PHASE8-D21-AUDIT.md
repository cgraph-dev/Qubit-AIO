# PHASE 8 — D21 verification: write-only `HERMES_*` env names

**Status:** read-only verification lane. **No product code changed, no writer removed, no
deletion, no rename, no service restart, no network, no push.** This document is evidence, not
an owner decision.

- Worktree: `/tmp/opencode/wt-p8-d21`
- Branch: `feat/p8-d21`
- Base HEAD at measurement: `ddc4056ab` (`fix(cli): make agent roles/run and connectors status
  reachable from 'qubit'`)
- Inventory base re-checked: `6bc665780` (the commit named by `docs/PHASE8_INVENTORY.md`)
- Inputs: `docs/PHASE8_INVENTORY.md` §2 (2.2, 2.6) and D21; `docs/PHASE8-SIGNOFF-PACK.md` D21
- All commands are `git grep` over **tracked** files only, run with `nice -n 10`.

## 1. Method (exact)

### 1.1 Production surface

The same globs and exclusions as the inventory:

```
*.py *.ts *.tsx *.js *.mjs *.cjs *.sh *.bash *.zsh *.yaml *.yml *.toml *.service *.env .env.example
:!**/tests/** :!tests/** :!**/tests-js/** :!**/test_*.py :!**/*.test.ts :!**/*.test.tsx
:!**/__tests__/** :!qubit_runtime/website/** :!docs/archive/**
```

```bash
git -C /tmp/opencode/wt-p8-d21 grep -nH -E '\bHERMES_[A-Z0-9_]+' HEAD -- \
  '*.py' '*.ts' '*.tsx' '*.js' '*.mjs' '*.cjs' '*.sh' '*.bash' '*.zsh' \
  '*.yaml' '*.yml' '*.toml' '*.service' '*.env' '.env.example' \
  ':!**/tests/**' ':!tests/**' ':!**/tests-js/**' ':!**/test_*.py' \
  ':!**/*.test.ts' ':!**/*.test.tsx' ':!**/__tests__/**' \
  ':!qubit_runtime/website/**' ':!docs/archive/**'
```

### 1.2 Line classification

Each matching line is classified as a **writer**, a **reader**, or neither. This is line-level,
as the inventory said; §1.4 adds the dataflow pass needed to answer D21 properly.

| Kind | Patterns counted |
|---|---|
| writer | `os.environ["N"] =`, `environ["N"] =`, `env["N"] =`; `environ.setdefault("N"`, `putenv("N=`; `.update(... N=...)`; shell `export N=`, `N=`; systemd `Environment=N=`; `process.env.N =`; a `"N":` dict key in an env/spawn/overrides literal |
| reader | `getenv("N")`, `environ.get("N")`, `env.get("N")`, `process.env.N`, `process.env["N"]`, `environ.pop("N")`, `"N" in environ`, `environ["N"]` (not on the left of `=`), `$N` / `${N}`, `printenv N`, and any `*env*(... "N" ...)` helper (`env_var_enabled`, `_env_get`, `_float_env`, `get_session_env`, `routed_sessions_setting`, …) |

Line-level result at both `HEAD` and `6bc665780`:

| Measure | Count |
|---|---:|
| Distinct `HERMES_*` names classified in production | 421 |
| Names with a write line | 118 |
| Names with a read line | 402 |
| Names both read and written | 99 |
| **Names written but not read (line-level)** | **19** |
| Names read but not written | 303 |

The inventory recorded **22**. I could not reproduce 22 exactly: a narrower first pass (only the
inventory-listed reader forms, no generic `*env*` helper match) gave **26**. The exact 22-name
list was never published, so the delta cannot be itemised name-for-name. What can be proven is
below: **three of the five D21 examples have real in-tree readers**, and the line-level set is
**19**, not 22.

### 1.3 Whole-tree reader search

For every write-only name:

```bash
git -C /tmp/opencode/wt-p8-d21 grep -nH -E '\b<NAME>\b' HEAD      # every tracked file
```

Every hit was re-classified with the same rules. This includes tests, shell, CI, scripts, and
docs. A reader found here is still evidence (a test or script breaks if the writer is removed),
even when it is not a production reader.

### 1.4 Dataflow / constant pass (why line-level alone is not enough)

A literal name that reaches `os.environ.get(...)` through a module constant is a real reader but
invisible on the writer's line. Examples found here:

- `_TRUST_RECENT_SECONDS_ENV = "HERMES_MEDIA_TRUST_RECENT_SECONDS"` → read at
  `qubit_runtime/gateway/media_policy.py:70`.
- `STATUS_FILE_ENV = "HERMES_UPDATE_STATUS_FILE"` → read at
  `qubit_runtime/qubit_cli/update_stage.py:46`.
- `HANDOFF_PID_ENV = "HERMES_UPDATE_HANDOFF_PID"` → read at
  `qubit_runtime/qubit_cli/update_lock.py:332`.
- `routed_sessions_setting(key, env_var)` → reads `os.environ.get(env_var)` at
  `qubit_runtime/qubit_state_common.py:52`, reached from
  `qubit_runtime/qubit_state_fts.py:108` and `qubit_runtime/qubit_state_search.py:29`.
- Electron reads `env.HERMES_DESKTOP_DISABLE_GPU` / `env.HERMES_DESKTOP_PASSWORD_STORE` from a
  destructured `process.env`, not via `process.env.NAME`
  (`qubit_runtime/apps/desktop/electron/bootstrap-platform.ts:63,127`).
- `_timeout_from_env_or_config("HERMES_CRON_SCRIPT_TIMEOUT", …)` at
  `qubit_runtime/cron/scheduler_script.py:79`.

This pass is why the list shrinks from 19 to 10 names with no in-tree reader at all.

### 1.5 Inheritance test

For each writer I recorded whether the value reaches a child:

- a write to `os.environ[...]` / `environ.update(...)` is **process-global** and is inherited by
  every child that does not pass an explicit `env=` or clear the environment;
- a write to a local `env` dict is recorded as **explicit child env** when that dict is passed to
  `subprocess`/`Popen`/Electron/`ShellExecuteW`/a worker launcher;
- an `export` in `posix.sh` is inherited by the updater children it starts.

Because almost every candidate is either process-global or an explicit child env, **the
inheritance bar in the task (“no reader AND no child inherits”) is not met by any name**.

## 2. Table — 19 line-level write-only names

Reader column is whole-tree. “none” means no read pattern exists anywhere in tracked files;
tests that merely `monkeypatch.setenv` are writers, not readers.

| # | Name | Writer file:line | In-tree reader | Child / external inheritance | Recommendation |
|---|---|---|---|---|---|
| 1 | `HERMES_CJK_FTS` | `qubit_runtime/qubit_cli/cli_config_load.py:177` | `qubit_runtime/qubit_state_common.py:52` via `qubit_state_fts.py:108`; test `qubit_runtime/tests/gateway/test_cjk_fts_config_bridge.py:31` | process-global `os.environ` → every child inherits | **KEEP writer** — real reader |
| 2 | `HERMES_CRON_SCRIPT_TIMEOUT` | `qubit_runtime/evals/cron_timeout_fork_race.py:24` | `qubit_runtime/cron/scheduler_script.py:79` (`_timeout_from_env_or_config`) | process-global; probe spawns cron child | **KEEP writer** — real reader |
| 3 | `HERMES_DESKTOP_DISABLE_GPU` | `qubit_runtime/qubit_cli/main_desktop.py:1546` | `qubit_runtime/apps/desktop/electron/bootstrap-platform.ts:63` | explicit env dict → Electron child | **KEEP writer** — real reader |
| 4 | `HERMES_DESKTOP_PASSWORD_STORE` | `qubit_runtime/qubit_cli/main_desktop.py:1563` | `qubit_runtime/apps/desktop/electron/bootstrap-platform.ts:127` | explicit env dict → Electron child | **KEEP writer** — real reader |
| 5 | `HERMES_DISABLE_PLUGINS` | `qubit_runtime/evals/anthropic_proxy_thinking_replay.py:102`; `qubit_runtime/evals/codex_echo/probe.py:15`; `qubit_runtime/evals/gateway_failure_ownership/probe.py:16` | none (tests only set it) | process-global in eval probes; probe children inherit | **KEEP writer** — inert knob, evidence fixture; no reader today |
| 6 | `HERMES_DISABLE_REDACTION` | `qubit_runtime/evals/postmortem/review_probes/context_cap_probe.py:18` | none | process-global in probe; child inherits | **KEEP writer** — evidence fixture |
| 7 | `HERMES_DISABLE_TELEMETRY` | `qubit_runtime/evals/postmortem/review_probes/deadline_probe.py:16` | none | process-global in probe; child inherits | **KEEP writer** — evidence fixture |
| 8 | `HERMES_GATEWAY_ELEVATED_HANDOFF` | `qubit_runtime/qubit_cli/gateway_windows.py:274` | none | written to `os.environ`, then `ShellExecuteW` elevated child inherits (Windows) | **KEEP writer** — target version cannot be proven from source |
| 9 | `HERMES_KANBAN_GOAL_MAX_TURNS` | `qubit_runtime/qubit_cli/kanban_db_dispatch.py:2905` | none (two pass-through allow-lists at `agent/delegation_context.py:24`, `qubit_cli/gateway_runtime_start.py:21`) | explicit env dict → kanban worker child | **KEEP writer** — child may read; not conclusively absent |
| 10 | `HERMES_MAX_TOKENS` | `qubit_runtime/evals/output_caps_local_capture.py:56`; `qubit_runtime/evals/output_caps_surfaces.py:88` | none — documented as **removed**: `qubit_runtime/evals/output_caps_scope.md:20`, `qubit_runtime/website/docs/integrations/providers.md:1269` | process-global in evals; any spawned child inherits | **KEEP writer** — stale eval fixture; intentionally no reader |
| 11 | `HERMES_MEDIA_TRUST_RECENT_SECONDS` | `qubit_runtime/gateway/run.py:2109` | `qubit_runtime/gateway/media_policy.py:70` via `_TRUST_RECENT_SECONDS_ENV`; fixture `tests/fixtures/differential/closure/HERMES_OPEN_CLOSURE.json:22858` | process-global → every child inherits | **KEEP writer** — real reader |
| 12 | `HERMES_OVERLAYS` | **not a writer**: documentation row in `tmp/audit/check_s4_diff.py:24` | n/a | n/a | **NOT A WRITER** — §4 exclusion-list text, remove from the D21 set |
| 13 | `HERMES_SEARCH_SLOW_MS` | `qubit_runtime/qubit_cli/cli_config_load.py:179` | `qubit_runtime/qubit_state_common.py:52` via `qubit_state_search.py:29`; fixture `HERMES_OPEN_CLOSURE.json:22978` | process-global → every child inherits | **KEEP writer** — real reader |
| 14 | `HERMES_SECRET_KEY` | `qubit_runtime/agent/secret_sources/command.py:75` | none in-tree **by design**: the reader is the user's `/bin/sh -c` helper (`command.py:78`) | explicit `env=` on the child; key travels as data | **KEEP writer** — external command contract |
| 15 | `HERMES_TEST_MODE` | `qubit_runtime/evals/postmortem/review_probes/goal_scope_probe.py:14` | none | process-global in probe; child inherits | **KEEP writer** — evidence fixture |
| 16 | `HERMES_UPDATE_HANDOFF_PID` | `qubit_runtime/scripts/desktop-update/posix.sh:759`; also `qubit_runtime/qubit_cli/update_handoff.py:87` via constant | `qubit_runtime/qubit_cli/update_lock.py:332` via `HANDOFF_PID_ENV`; test `qubit_runtime/tests/scripts/desktop_update/test_posix_handoff_pid_export.py:42` | child updater env (export + `env.setdefault`) | **KEEP writer** — real reader |
| 17 | `HERMES_UPDATE_REEXEC` | `qubit_runtime/qubit_cli/update_handoff.py:86` | none in-tree (tests set it; the frozen/old updater is not in-tree) | explicit env dict → post-swap child | **KEEP writer** — updater-generation contract |
| 18 | `HERMES_UPDATE_RESTART_RECOVERY` | `qubit_runtime/qubit_cli/update_abort_recovery.py:99`; also `update_restart_recovery.py:129` | none (tests assert the write) | explicit env dict → recovery child | **KEEP writer** — child marker; forward/backward compatibility |
| 19 | `HERMES_UPDATE_STATUS_FILE` | `qubit_runtime/scripts/desktop-update/posix.sh:835` | `qubit_runtime/qubit_cli/update_stage.py:46` via `STATUS_FILE_ENV` | exported to updater children | **KEEP writer** — real reader |

Summary of the 19: **8 have a real in-tree reader** (#1,2,3,4,11,13,16,19), **1 is not a writer**
(#12), and **10 have no in-tree reader** (#5,6,7,8,9,10,14,15,17,18). All 10 still reach a child
or external process except the eval-only knobs, which are non-product evidence fixtures.

## 3. The five D21 examples

### 3.1 `HERMES_DISABLE_PLUGINS` — write-only, but only in eval fixtures

Writers: `qubit_runtime/evals/anthropic_proxy_thinking_replay.py:102`,
`qubit_runtime/evals/codex_echo/probe.py:15`,
`qubit_runtime/evals/gateway_failure_ownership/probe.py:16` (env dict).
No read pattern anywhere in production. Tests only call `monkeypatch.setenv` on it
(`qubit_runtime/tests/acp_adapter/test_failed_turn_closure.py:91`,
`qubit_runtime/tests/gateway/test_authority_unknown_resolution.py:170`). There is also no
`QUBIT_DISABLE_PLUGINS` twin. **It is an inert probe knob today**; the eval probes spawn a child
that inherits it, so an out-of-tree/older runtime consumer cannot be ruled out. KEEP.

### 3.2 `HERMES_EXEC_ASK` — NOT write-only; the D21 example is wrong

Production readers exist: `qubit_runtime/tools/approval.py:997`
(`env_var_enabled("HERMES_EXEC_ASK")`), `qubit_runtime/tools/cronjob_tools.py:1159`. Test
readers confirm the contract (`qubit_runtime/tests/tools/test_cli_approval_exec_ask_leak.py:240`).
Writers: `qubit_runtime/gateway/run_bootstrap.py:758` and
`qubit_runtime/tui_gateway/server.py:1419`. This is a live gateway/approval contract; removing
the writer disables ask-mode approval. KEEP.

### 3.3 `HERMES_GATEWAY_SESSION` — NOT write-only; the D21 example is wrong

Production readers: `qubit_runtime/tools/approval_context.py:240`,
`qubit_runtime/qubit_cli/bang_shell.py:54`, `qubit_runtime/tools/cronjob_tools.py:1158`,
`qubit_runtime/tools/skills_tool_setup.py:122`,
`qubit_runtime/tools/terminal_tool_sudo.py:97`. Writers:
`qubit_runtime/agent/managed_worker.py:375` (next to the canonical
`QUBIT_GATEWAY_SESSION` at `:371`) and `qubit_runtime/tui_gateway/server.py:1419`. KEEP.

### 3.4 `HERMES_SECRET_KEY` — genuinely has no in-tree reader, by design

The single writer is `qubit_runtime/agent/secret_sources/command.py:75`. The value is placed in
the `env=` dict of a `/bin/sh -c <user command>` child (`command.py:78`) and the module docstring
says “The key travels as DATA in `HERMES_SECRET_KEY`” (`command.py:64`). The reader is the user's
command, which is out of tree. Removing the writer breaks the `command` secret provider. KEEP.

### 3.5 `HERMES_UPDATE_HANDOFF_PID` — NOT write-only; the D21 example is wrong

Writers: `qubit_runtime/scripts/desktop-update/posix.sh:759` (`export`) and
`qubit_runtime/qubit_cli/update_handoff.py:87` (`env.setdefault(HANDOFF_PID_ENV, …)`). Reader:
`qubit_runtime/qubit_cli/update_lock.py:332` reads `HANDOFF_PID_ENV` and the canonical
`QUBIT_UPDATE_HANDOFF_PID`; test
`qubit_runtime/tests/scripts/desktop_update/test_posix_handoff_pid_export.py:42` asserts the
export. The reader is reached through a constant, which is exactly what the inventory's
line-level pass misses. KEEP.

## 4. Can any writer be removed? No — and none was

**No writer was removed, renamed, or edited in this lane.** No product file was touched; the only
new file is this document.

No name meets the bar “conclusively no reader and no child inherits”:

- 8 of 19 have a direct or constant/dataflow reader in production.
- 1 is not a writer at all (`HERMES_OVERLAYS`).
- The remaining 10 are written into a process-global `os.environ`, an explicit child `env=`, an
  `export` for updater children, or an Electron/elevated-child environment. In every case a
  consumer outside the lines we can classify (user command, worker, installed/older updater,
  elevated Windows child) can read it.
- The eval-only names (`DISABLE_PLUGINS`, `DISABLE_REDACTION`, `DISABLE_TELEMETRY`, `TEST_MODE`,
  `MAX_TOKENS`) are non-product evidence fixtures. Two of them are visibly stale:
  `HERMES_MAX_TOKENS` is documented as removed (`output_caps_scope.md:20`,
  `providers.md:1269`), and `DISABLE_PLUGINS` has no reader. They are candidates for a future
  evidence-fixture cleanup, not for a product writer change in this lane.

### Residual uncertainty

1. **Line-level + constant scanning is not a proof of absence.** A name can be read through a
   table, a `getattr`, a config-to-env bridge, or a helper we did not enumerate. This audit already
   found several such readers the inventory missed; the same class may hide others.
2. **External consumers are not enumerable from the repo**: user-provided `command` secret
   helpers, installed/older updater shims, the Windows elevated child, third-party plugins that
   read `HERMES_*`, and platforms outside the tracked tree.
3. **`os.environ` writes are inherited by default by every child**; this is a real, non-theoretical
   path even when the in-tree reader is absent.
4. **The inventory's 22 was not reproducible.** This lane measured **19** line-level names at both
   `HEAD` (`ddc4056ab`) and the inventory base (`6bc665780`); a narrower first pass measured 26.
   The 22-name list was never published, so its exact composition is unknown. Three of the five
   published examples (`EXEC_ASK`, `GATEWAY_SESSION`, `UPDATE_HANDOFF_PID`) demonstrably have
   readers, so the inventory's line-level count overstates the write-only set.
5. **No live probe**: no service was restarted and no external child was exercised. Removing any
   writer would need an owner-approved change plus a live read-path test, not this document.

## Appendix — one-off checker

A throwaway script (`/tmp/opencode/d21/d21_audit.py`, not committed) applied the regexes in §1.2
to the `git grep -nH` dump above and printed the write-only set and every writer line. The
per-name whole-tree pass used:

```bash
git -C /tmp/opencode/wt-p8-d21 grep -nH -E '\b<NAME>\b' HEAD
```

and the dataflow pass was a manual `git grep` for each name's constant
(`_TRUST_RECENT_SECONDS_ENV`, `STATUS_FILE_ENV`, `HANDOFF_PID_ENV`, `routed_sessions_setting`,
`HERMES_DESKTOP_DISABLE_GPU` in Electron, `_timeout_from_env_or_config`). All quoted `file:line`
values were read on `ddc4056ab`; the same 19 names were produced on `6bc665780`.
