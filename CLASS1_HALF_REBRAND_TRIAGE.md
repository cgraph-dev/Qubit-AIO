# CLASS 1 half-rebrand triage — /tmp/qb-x1 @ eafb54e2

Scope: names LOADED but BOUND NOWHERE (the `qubit_id` half-rebrand class — a rename
moved the DEFINITION to the new spelling but left a USE on the old one, so the name
resolves to nothing at runtime on the path that executes it).

## Tooling

1. **`half_port_sweep_p6.py`** (proven 31-probe/21-call adversarial sweep, vendored from prior campaign)
   VERDICT: `PROBES: 21 SANE: 21 HALF-PORT: 0 -> PASS -- no half-ports`.

2. **`half_port_static_scan_p6.py`** (dyn scan of 10 modules)
   VERDICT: 1 module with undefined global: `_thread` in `qubit_platform.resolver.app`
   -> triaged Bucket-B below.

3. **`check_half_rebrand.py`** (prior-campaign AST scanner, vendored)
   VERDICT against qubit_runtime: **212 raw unbound Name-Load hits** across 8435 files
   (48 bind_module files skipped).

4. **`scripts/check_half_rebrand_ast.py`** (NEW, this campaign — the AST extension)
   Two-pass, scope-aware, seam-structural. VERDICT: **0 REAL, 3087 dynamic/seam,
   116 annotation, 0 classbody across 8483 files.**

## The seam (why 212 raw hits are NOT 212 bugs)

`qubit_runtime/tui_gateway/method_ctx.py:78 bind_module(module_globals, server, skip=())`
publishes a split module's functions onto **server.py's globals**, rebinding each function's
`__globals__` to server's namespace. So a split module's body may reference server globals
**bare** (`_ok`, `_err`, `_session_cwd`, ...); they resolve at call time, not import time.
`skip=("_",)` skips only the literal name `"_"`, NOT `_`-prefixed names.

PROOF the seam delivers: importing `qubit_runtime.tui_gateway.server` and `hasattr(server, name)`
for **all 57 distinct `_`-prefixed hit names verbatim from the 212-hit list -> every one BOUND**
(0 missing).

## Triage table (hit -> verdict -> PROOF)

| hits | file(s) | verdict | proof |
|---|---|---|---|
| 78 | `tui_gateway/server.py` | BUCKET-B (seam host) | server.py defines `register_method` (line 902) + `_profile_scoped` (617); split modules call `bind_module` publishing their `_`-helpers into server globals. Live `hasattr(server, N)` -> all BOUND. |
| 26 | `tui_gateway/methods_groups.py` | BUCKET-B (split module) | module calls `_registry.install(server)` (line 526); docstring line 4 states bodies are rebound. Live check: `_ok`/`_err` BOUND. |
| 26 | `tui_gateway/methods_bot_relay.py` | BUCKET-B (split module) | `_registry.install(server)` (267); docstring line 7: "reference `_ok`/`_err` bare". |
| 17 | `tui_gateway/methods_vault.py` | BUCKET-B (split module) | `_registry.install(server)` (185); docstring line 25 same contract. |
| 28 | `plugins/platforms/feishu/adapter.py` | BUCKET-B (intentional dynamic) | line 79 `globals().update({name: None for _, names in _LARK_SDK_IMPORTS for name in names})` explicitly binds every lark SDK name to None; `_load_lark_oapi` rebinds on connect. Live: `HttpMethod`,`AccessTokenType`,`GetChatRequest`,`CreateFileRequest`,... all BOUND=True. |
| 31 | `tests/compat/old_updater_dependencies.py` | BUCKET-B (frozen fixture) | line 11 `# ruff: noqa: F821 -- globals came from the old process; the test supplies them.` Loaded by `test_old_updater_shims.py:219` via `spec_from_file_location`+`exec_module`; the test injects the namespace. Verbatim historical extraction by design. |
| 5 | `optional-skills/security/godmode/scripts/auto_jailbreak.py` | BUCKET-B (intentional exec seam) | `score_response` def @ `godmode_race.py:185`, `escalate_encoding` def @ `parseltongue.py:507`; auto_jailbreak.py:58-61 `exec(compile(open(<sibling>).read(),...), _caller_globals)` injects them into module globals. |
| 1 | `tests/qubit_cli/test_local_runtime.py:24` | BUCKET-B (annotation) | `def _write_current_process_state(path: Path, ...)`; file has `from __future__ import annotations` (line 10) -> annotations never evaluated. Not a runtime load. |
| 1 | `resolver.app` `_thread` (dyn scan) | BUCKET-B (imported inside function) | dynamic scan's naive `dir(module)` misses a function-local import; the module imports/uses `_thread` in-body. Productive path green (21-probe sweep SANE). |

**REAL (half-rebrand) findings: NONE.** No fix was required; the tree carries no class-1 defect.

## Scanner validation (red -> green, both directions)

- RED: synthetic `x = qubit_id_only_here` in a plain file -> `1 REAL` (caught).
- GREEN: synthetic split module with `_ok` bare -> `0 REAL` (seam excused).
- EDGE: split module + a name bound NOWHERE (`totally_undefined_name_xyz`) -> `1 REAL`
  (a leak is NOT hidden even inside a seam module — the two-pass allowlist only excuses
  names provably bound somewhere in the scanned tree).
- FULL-TREE: real leak injected into a copy of `server.py` -> caught as `2 REAL` among
  3087 correctly-excused seam names. Scanner is not neutered at scale.

## Rename-pair residue scan (the exact qubit_id shape)

For every file holding BOTH a `qubit_*` and its `hermes_*` twin (50 files), classify each
twin as bound/loaded: **0 files load a name whose twin is bound in-file** — the
`qubit_id`/`hermes_id` pattern does not recur. The proven instance itself
(`model_switch_providers.py`) is now correct: `_raw_pool_usable(qubit_id, ...)` param @323
properly bound, call site @845 passes `hermes_id`. HERMES_* env vars / unit names untouched
(wire contracts).

## Gates

- `gate_all.py`: **VERDICT: PASS — all blocking checks green**
  - import gate 29 OK / 0 FAIL
  - entry-point resolution HARD prod=0 tests=0
  - lazy imports 0 unguarded; 1 guarded (silent no-op)
  - capability audit informational (1727 missing — burndown, not defect)
- `check_lazy_imports.py`: **0 UNGUARDED** (exit 0)
- `py_compile` on both scanner scripts: OK
- Tests (owner-facing surfaces): model-switch variant tags 6/6, voice canonical 12/12,
  voice continuity 5/5 (the one `-x` blip was a `hickory_resolver` env flake; full-file run passes).

## Commits (per-file, no `git add -A`)

- `d71fd389` scripts: add scope-aware AST half-rebrand pass
- `9a179e91` scripts: vendor check_half_rebrand.py (prior-campaign scanner)
