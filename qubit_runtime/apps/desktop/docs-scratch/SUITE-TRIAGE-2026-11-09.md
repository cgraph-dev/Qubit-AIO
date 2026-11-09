# Suite triage — measured after cdd8e2b0d (+ the lark-filter repair)

Measured on 2026-11-09, main @ cdd8e2b0d + the `pkg_resources(\\.|$)` repair.
Runner: `bash scripts/run_tests.sh <tree>` (per-file subprocess isolation, 16 workers).

## Real summary lines

| Tree | Summary |
|---|---|
| root `tests/` (before filter repair) | `1279 files, 15119 tests passed, 667 failed, 389 errors, 419 skipped; FAILED: 405 of 1279 files` in 2509.8s |
| root `tests/` at cdd8e2b0d, same interpreter | `1279 files, 14990 tests passed, 653 failed, 399 errors, 548 skipped; FAILED: 400 of 1279 files` in 2941.4s |
| vendored `qubit_runtime/tests/` | `7289 files, 74879 tests passed, 80 failed, 17 errors, 1061 skipped; FAILED: 68 of 7289 files` in 9305.0s |

Pre-existing editorial baseline (earlier session) was 1274 files / 14269 passed / 737 failed / 399 errors.

## What cdd8e2b0d actually unblocked: nothing, on its own

Its four `ignore:` filters wrote the module field as `lark_oapi\..*` / `pkg_resources\..*`.
`pkg_resources\..*` cannot match the module named exactly `pkg_resources`, and
`pkg_resources.declare_namespace` **re-warns recursively** for each parent namespace
(`lark_oapi.ws.pb` → `lark_oapi.ws` → `lark_oapi`) from inside
`pkg_resources/__init__.py:2560`. Those re-emitted copies carry emitting module
`pkg_resources`, so every one escaped the filters.

Measured: **111 files** still died with
`DeprecationWarning: Deprecated call to ... declare_namespace('lark_oapi.ws.pb')`
— the single largest failure class in the root tree.

Fixed by widening the module field to `lark_oapi(\.|$)` / `pkg_resources(\.|$)`.
Re-running exactly those 111 files afterwards:
`111 files, 559 tests passed, 10 failed, 6 skipped; FAILED: 6 of 111 files`.
Of the 6 survivors, all 6 also fail at cdd8e2b0d → pre-existing, not caused by the filter.

Negative control: a `warnings.warn(msg, DeprecationWarning)` of the same shape issued
from our own module still raises under `filterwarnings = error`.

## Root tree classification (405 failing files)

| Count | Class | Proof |
|---|---|---|
| 111 | lark / `pkg_resources` recursion | fixed; 111→6 files, survivors pre-existing |
| 50 | purged `docs/` paths | see below; absent from tree, recoverable from `eafb54e26^` |
| 44 | teardown `unclosed database` / fd ResourceWarning | tests pass, teardown fails; pre-existing |
| ~170 | assertion failures | pre-existing at cdd8e2b0d |
| 19 | "regressions" | **all environment** — see below |

### The 19 apparent regressions are environment, not code

Every one passes (or is skipped) at cdd8e2b0d **in the same `.venv`**; they only fail/pass
in the main worktree:

- ~13 (playwright `BrowserType.launch: Executable doesn't exist`): `~/.cache/ms-playwright/`
  has no `chromium*` directory. Environment.
- `test_agentB_chat_delete`, `test_qbpv2_*`, `test_ops_confirm8tf`, `test_qubit_serve8tf`,
  `test_voice_dashboard8u`: gated on `frontend/qubit-dist/index.html`, which is **gitignored**
  and exists only in the main worktree (built 04:03). They *skip* in a fresh worktree.
- `test_qbpv2_frontend_root_base`: expects `<title>Qubit Agent` but both the source
  (`frontend/qubit-web/index.html`) and the built dist say `Qubit AiO`. The assertion never
  existed in git history for that file. Pre-existing drift, exposed by the dist gate.
- `test_office_hud_drawer`: `tests/_probe_hud_dom.py` mkdirs the hardcoded
  `/tmp/qubit-v2-office-hudfix/tmp` — another agent's dead worktree. Scratch-state.
- `test_slash_worker_import_path9u`: `.venv`'s `_editable_impl_qubit_voicebridge.pth` puts the
  repo root on `sys.path`, so `import tui_gateway` succeeds even in the "must fail" subprocess.
  Only reproduces under the editable-install venv.
- `test_managed_runtime_resolution`: scans `qubit_runtime/.build/webapp-workspace/node_modules/`
  — untracked build artifacts, absent in a fresh worktree.

**Zero real regressions found.**

## Vendored tree (68 failing files)

Retesting each of the 70 real paths in isolation: **40 pass** (16-worker concurrency flakes),
**28 persist**. Running those 28 at cdd8e2b0d: **26 fail identically** (pre-existing), and the
other 2 (`test_authoring_standards`, `test_managed_runtime_resolution`) turn out to be
contention / untracked `.build/` artifacts. **Zero regressions.**

Note: the full vendored run takes **~2h35m** (9305s) — the earlier "killed at 36 minutes"
was a premature kill, not a hang.

## Purged-docs class — the remedy

66 absent doc paths cited by ~45 test files. They are **not lost**: every one used in a test
is recoverable from `eafb54e26^`, and regenerators exist (`scripts/rebuild_ledger_*.py`,
`scripts/gap_ledger.py`, `scripts/parity_ledger.py`). A helper already encodes the honest
pattern: `tests/_purged_ledger.py` (read from git history, fail loudly if never there).

Do NOT hand-author replacement docs (AGENTS.md forbids it) and do NOT guess paths.
