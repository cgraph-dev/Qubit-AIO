# Sync hazard register — silent-failure classes found while syncing upstream

Every entry here is a bug that looked fine and was quietly wrong. They are listed
because each one cost real debugging time and each will recur on the next slice.

Baseline for this campaign: pin `69948c0057` → `a036b13793` (5,441 commits / 5,142 files).

## 1. A dropped symbol leaves the call site behind

Upstream renames or deletes a symbol; the merge carries the *caller* to the new
tree while the definition stays behind (or vice versa). Import-time checks miss it
when the import is function-local.

Real instances:
- `tui_gateway/server.py` did not import `marker_writer_state` that
  `session_auto_continue.py` calls bare → `NameError` on every resume of a
  crash-interrupted session.
- `qubit_cli/plugins.py` lacked `publish_gateway_message_host` /
  `clear_published_gateway_message_host` / `_attach_published_gateway_host` that
  `gateway/run_inbound.py` imports → `ImportError` killed the gateway daemon;
  systemd hit its restart limit.
- `qubit_cli/kanban_db.py` lacked `pin_first_board_resolution` that the gateway
  kanban watchers call → failure every 5s, forever.

**Fix pattern:** define or alias at the *definition* site, never at the call site,
so exactly one object exists under both names. A second object (e.g. a duplicated
frozenset blocklist) drifts silently.

## 2. A signature gains a parameter; the definition does not

Upstream added `profile=` to `_session_home_scope` for a session-less Desktop
draft. The merged caller passed it; the definition (in a file outside that slice)
still had two args → `_session_home_scope() got an unexpected keyword argument
'profile'` on **every** `/slash` command.

**Guard:** after a merge, diff call-site kwargs against definition signatures for
every cross-file helper touched.

## 3. Upstream DELETED a layer our tree still needs

`qubit_cli/plugin_compat.py` was replaced by upstream's 3-stub version
(**16,586 → 1,945 bytes**). Upstream removed the plugin-compat layer on
2026-09-14; our tree deliberately keeps it and **15 files** import `warn_once`
from it. The merge treated a deliberate deletion as an edit.

**Rule (already in the plan, now with a corpse): never auto-apply upstream
deletions.** Check each against `docs/PARITY.md`.

Counter-example, so the rule is not over-applied: `qubit_cli/tips.py` genuinely
shrank 40,622 → 2,857 B because upstream moved the tip text into the i18n catalog
(`tips.tNNN` keys). That is a real refactor and must be KEPT. Size delta alone
does not tell you which case you are in — read the file.

## 4. A measurement harness that silently measures the wrong thing

`sync_import_gate.py` fell back to `sys.executable` when it found no `.venv`. A git
worktree has no `.venv`, so grading a worktree ran under the **system python**,
which cannot import the app: **8 OK / 21 FAIL** (all phantom `No module named
'ruamel'`) for a tree that measures **29 OK / 0 FAIL** under the real interpreter.
Five agents nearly reported ~21 phantom failures each.

**Fixed:** the gate now searches the repo *and its parents*, then raises
`SystemExit` rather than grading with an interpreter that cannot import the code.
A wrong number is worse than no number.

## 5. A label used as a filesystem path

`__root__` was passed to git as a pathspec (`-- __root__`), which matches nothing.
It reported **0 files**, and a run would have merged nothing while reporting
success — silently skipping the 49-file primitive layer whose absence caused
defect class #1 three times.

## 6. A stale baseline

`BASE_SHA` was `bd0affe5`, **1,313 commits** short of current upstream. Every
measurement understated the gap by ~5x (346 missing symbols reported vs 1,833 real).

## 7. The slice list omitted whole subsystems

`SUBSYSTEMS` in `sync_driver.py` had no `hermes_cli` (377 files — the largest) and
no `hermes_platform`, and no `tests` (1,319). Those files could never be scheduled,
so every symbol they define read as permanently missing.

## 8. A routing rule that does not fire under a prefix

For `tests/hermes_cli/...`, `route()` leaves the path unchanged because the
`hermes_cli` prefix rule never matches once upstream prefixes it with `tests/`.
362 planned files would land in `qubit_runtime/tests/hermes_cli/` — a
`hermes_cli`-**named** tree — instead of `qubit_runtime/tests/qubit_cli/`. A stray
303-file tree from a previous run already exists, so this re-infects pytest
collection. Caught by a pre-apply routing check before anything was written.

## The throughline

Six of the eight are one shape: **a fallback or a default that produces a
plausible wrong answer instead of failing.** Prefer a loud refusal. When a tool
can measure the wrong thing, make it stop rather than guess.
