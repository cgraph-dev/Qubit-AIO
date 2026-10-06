# SYNC SLICE VERIFICATION — `qubit_cli` upstream sync slice

**Diagnosis only. Nothing was fixed.** Read-only verification with real command execution.

- Repo: `/home/trick/Projects/Repos/qubit-voicebridge`
- HEAD (pre-slice baseline commit): `9a6432b2241679f10fe2493ab71313831e8c442b` (`9a6432b2`)
- Interpreter: `/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python` — Python 3.12.3
- Runtime import path: `PYTHONPATH=qubit_runtime:.` (see `voicebridge/qubit_runtime.py`, `scripts/probe_pty_lease_shim.py`)
- Slice scope (verified via `git status --porcelain`): **the slice modified only `qubit_runtime/qubit_cli/**`** (239 files). The 12 other modified files are the earlier voice/orb work, not this slice. **No provider module outside `qubit_cli/` was touched by the slice** — this is the root cause of every import breakage below.
- Date of run: 2026-10-05 (UTC).

---

## Verdict summary

| Gate | What | Result | Verdict |
|---|---|---|---|
| 1 | `py_compile` every `qubit_cli/**.py` | 638 files, **0 fails** (1 `SyntaxWarning`) | **PASS** |
| 2 | Import key + all modules (`importlib`) | 630 modules, **27 fails** (26 new vs pre-slice) | **FAIL** |
| 3 | `test_pty_lease_shim.py` + `test_status_storage_root.py` | 17/17 pass **with `PYTHONPATH=qubit_runtime:.`**; 4 pty fails without it (pre-existing env requirement, not a slice regression) | **PASS** (with correct env) |
| 4 | `qubit_cli`-focused tests (subset) | 155/155 pass | **PASS** |
| 4b | End-to-end `qubit agent -z "…"` smoke | **FAIL** — `ImportError: cannot import name 'looks_like_openrouter_key' from 'qubit_cli.auth'` | **FAIL** |
| 5 | Merge-marker residue (`<<<<<<<`, `=======`, `>>>>>>>`) in `qubit_cli/` | **0 hits** | **PASS** |
| 6 | Untranslated identifiers (`hermes_cli`, `HERMES_*`) | `hermes_cli`: **0 in source `.py`** (stale cp311 `.pyc` only). `HERMES_*`: 797 occurrences — **pre-existing/deliberate** (777 pre-slice, 140 files) | **PASS** (see caveats) |

**Overall: the slice does NOT import cleanly.** Syntax and merge-marker gates pass, but 26 modules fail to import and the documented end-to-end smoke command is broken. The slice is **not internally consistent**.

---

## Gate 1 — Syntax gate (`py_compile`)

**Command**
```bash
cd /home/trick/Projects/Repos/qubit-voicebridge && .venv/bin/python - <<'PY'
import pathlib, py_compile
root = pathlib.Path('qubit_runtime/qubit_cli')
files = sorted(root.rglob('*.py'))
fails = []
for f in files:
    try: py_compile.compile(str(f), doraise=True)
    except py_compile.PyCompileError as e: fails.append((str(f), str(e)))
print('TOTAL', len(files)); print('FAILS', len(fails))
for f,e in fails: print('FAIL:', e)
PY
```

**Real output**
```
qubit_runtime/qubit_cli/uninstall.py:343: SyntaxWarning: invalid escape sequence '\c'
TOTAL 638
FAILS 0
```

**Verdict: PASS.** All 638 `.py` files under `qubit_cli/` compile. The single `SyntaxWarning` in `uninstall.py:343` is a docstring escape-sequence warning, not an error — it does not block compilation or import. **No `SyntaxError` anywhere.**

---

## Gate 2 — Import gate

**Module discovery:** the key modules named in the task exist as `qubit_cli.main`, `qubit_cli.config`, `qubit_cli.commands`, `qubit_cli.web_server`, `qubit_cli.gateway`. `qubit_cli.arg_parsing` **does not exist** (the real parser builder is `qubit_cli._parser`); its absence is expected, not a break.

**Command (full-package sweep over 630 modules)**
```bash
PYTHONPATH=qubit_runtime:. .venv/bin/python import_sweep.py   # importlib.import_module over every *.py
```

**Real output (post-slice / live tree):**
```
TOTAL 630 FAILS 27
```

**Real output (pre-slice baseline, `git worktree add --detach … 9a6432b2`):**
```
TOTAL 595 FAILS 1
FAIL qubit_cli.psutil_android :: SystemExit : 1
```

The pre-slice tree had **1** failure (`psutil_android` raises `SystemExit(1)` — a platform gate, pre-existing). Post-slice has **27**, i.e. **26 modules newly fail to import**.

**Verdict: FAIL.**

### The 26 newly-broken imports

| # | Module(s) | Error | Missing symbol | Provider (callee) | Caller slice status |
|---|---|---|---|---|---|
| 1 | `qubit_cli.backup`, `qubit_cli.backup_restore` | `ImportError` | `mkstemp_beside` from `utils` | `qubit_runtime/utils.py` (unmodified) | callers `M` |
| 2 | `qubit_cli.claw`, `gateway`, `gateway_setup_wizard`, `setup`, `setup_summary`, `setup_terminal` (6 modules) | `ImportError` | `DEFAULT_SANDBOX_IMAGE` from `qubit_cli.config_defaults` | `config_defaults.py` (unmodified) | callers `M` |
| 3 | `qubit_cli.local_runtime.catalog`, `web_routers.local_models`, `web_server` (3) | `ImportError` | `is_nvidia_n1x_pci_id` from `qubit_platform.host.products` | `qubit_platform/host/products.py` (outside slice, unmodified) | callers `M` |
| 4 | `qubit_cli.observability.shared_metrics_{desktop,events,fields,model,signals}` (5) | `AttributeError` | `MEMORY_OP_METRIC` on `shared_metrics_contract` | `observability/shared_metrics_contract.py` | callers `M` |
| 5 | `qubit_cli.observability.shared_metrics_snapshot`, `web_routers.cron` (2) | `ImportError` | `get_hermes_home` from `qubit_constants` | `qubit_runtime/qubit_constants.py` (outside slice, unmodified) | callers `M` |
| 6 | `qubit_cli.runtime_provider`, `status`, `status_auth` (3) | `ImportError` | `looks_like_openrouter_key` from `qubit_cli.auth` | `qubit_cli/auth.py` (unmodified) | callers `M` |
| 7 | `qubit_cli.sessions_cmd`, `sessions_cmd_browse`, `web_routers.sessions` (3) | `ImportError` | `SessionActiveWriteGuardError` from `qubit_state_errors` | `qubit_runtime/qubit_state_errors.py` (outside slice, unmodified) | callers `M` |
| 8 | `qubit_cli.plugins_cmd` | `ImportError` | `cmd_compat` from `qubit_cli.plugins_cmd_listing` | `plugins_cmd_listing.py` (**`M`** — merged too, symbol still absent) | caller `M` |
| 9 | `qubit_cli.plugin_validate_locales` | `ModuleNotFoundError` | `agent.i18n_layers` | module absent | — |

(26 distinct modules; `psutil_android` is the 27th and is pre-existing.)

### Root cause (uniform across all 26)

Every missing symbol is imported by a file **the slice modified (`M`)**, and every provider file is either **outside the slice entirely** (`qubit_platform/`, `utils.py`, `qubit_constants.py`, `qubit_state_errors.py`) or **unmodified within it** (`config_defaults.py`, `auth.py`). The slice was applied to `qubit_runtime/qubit_cli/**` only, so the upstream callers now demand symbols whose providers still sit at the old pin. This is a **cross-package sync gap**, not a merge-marker or translation failure.

Independent corroboration: `docs/SYNC_CONFLICT_PLAN.md` (untracked, pre-existing) already lists 6 of these symbols and states "the slice as applied is **NOT internally consistent**." This verification finds the same pattern and expands the set from 6 to 9 distinct missing symbols / 26 broken import sites.

---

## Gate 3 — The two known-good test files

**Command**
```bash
PYTHONPATH=qubit_runtime:. .venv/bin/python -m pytest \
    tests/test_pty_lease_shim.py tests/test_status_storage_root.py -q
```

**Real output (correct env, live tree):**
```
.................                                                        [100%]
17 passed in 1.53s
```

**Critical finding — the pty shim tests require `PYTHONPATH=qubit_runtime`.** Run with the repo's default pytest invocation (which does NOT put `qubit_runtime` on the path):
```
4 failed, 6 passed
```
`voicebridge/pty_lease_shim.py` lazily does `from qubit_cli.active_sessions import _read_entries, _state_path`; when `qubit_cli` is not importable it raises `ModuleNotFoundError`, which the shim **swallows** (`log.debug`) and returns 0 → lease never released → the 4 release-path tests fail.

**Proof this is pre-existing, not a slice regression** — the identical 4-failure result occurs in the **pre-slice worktree at `9a6432b2`** when `PYTHONPATH` is unset (`env -u PYTHONPATH`):
```
# pre-slice, no PYTHONPATH:   4 failed, 6 passed
# pre-slice, PYTHONPATH=qubit_runtime:. :  10 passed
```
Both trees behave identically. The pass/fail is a function of `PYTHONPATH`, not of the slice. `tests/test_status_storage_root.py` passes either way (7 passed) because it does not take the lazy-import path.

**Verdict: PASS with `PYTHONPATH=qubit_runtime:.` (17/17).** The no-PYTHONPATH 4-failure mode is a pre-existing environment requirement of `tests/test_pty_lease_shim.py`, not breakage introduced by the sync slice. (Recommend the file/docstring be updated to state the required `PYTHONPATH`, but that is a fix decision, out of scope here.)

---

## Gate 4 — Other `qubit_cli`-focused tests

294 files under `tests/` reference `qubit_cli`; most are voicebridge-level. A directly `qubit_cli`-facing subset was run:

**Command**
```bash
PYTHONPATH=qubit_runtime:. .venv/bin/python -m pytest \
  tests/test_cli_branding_leaks9u.py tests/test_cli_branding_url_leaks9u.py \
  tests/test_rebase_qubit_cli.py tests/test_qubit_package_runtime8u.py \
  tests/test_cli_parity_wave1.py tests/test_cli_closure_wave5.py -q
```

**Real output**
```
155 passed in 16.06s
```

**Verdict: PASS** (subset). These branding/parity tests do not touch the broken provider paths.

### Gate 4b — End-to-end smoke (`qubit agent -z`)

The task's documented smoke (`docs/STATUS.md`: `qubit agent -z "…"` → `QUBIT_OK`) runs through the Qubit launcher (`voicebridge/cli.py` adds the `agent` subcommand; the vendored `qubit_cli.main` provides `chat`). Real invocation and output:

```bash
cd /home/trick/Projects/Repos/qubit-voicebridge && \
  timeout 180 ./.venv/bin/qubit agent -z 'Reply with exactly the token QUBIT_OK and nothing else.'
```
```
qubit: dependency environment … targets Python 3.14 but this process runs 3.12; keeping this interpreter's own packages
⚠ install out of sync (ffmpeg: not installed or outdated; ripgrep: not installed or outdated) …
qubit -z: agent failed: cannot import name 'looks_like_openrouter_key' from 'qubit_cli.auth' (…/qubit_cli/auth.py)
```

`qubit_cli.main` itself loads and `--version` works (`Qubit Agent vunknown (2026.9.24)`, Python 3.12.3), but the actual turn path dies at the `looks_like_openrouter_key` import (breakage #6 above).

**Verdict: FAIL.** The end-to-end smoke command is broken by the slice.

---

## Gate 5 — Merge-marker residue

**Command**
```bash
grep -rn '^<<<<<<<\|^=======\|^>>>>>>>' qubit_runtime/qubit_cli/
```

**Real output**
```
COUNT: 0
```

**Verdict: PASS.** No file under `qubit_cli/` contains conflict markers. The 90 CONFLICT files were genuinely left untouched (the merge did not silently write markers).

---

## Gate 6 — Untranslated identifiers

**Command**
```bash
grep -rn --include='*.py' 'hermes_cli' qubit_runtime/qubit_cli/     # expect 0
grep -rn --include='*.py' 'HERMES_'   qubit_runtime/qubit_cli/     # expect 0 if fully translated
```

**Real output — `hermes_cli`:**
```
SOURCE_COUNT: 0
```
The only `hermes_cli` hits in the tree are **103 stale `__pycache__/*.cpython-311.pyc`** bytecode files (from a Python 3.11 build before the rename). **No source `.py` file references `hermes_cli`.** Verdict: PASS.

**Real output — `HERMES_*` in source `.py`:**
```
live:      797 occurrences across 140 files
pre-slice: 777 occurrences across 139 files
```
Popular cases: `HERMES_TUI`, `HERMES_SUPERVISED_CHILD`, `HERMES_ANON_API_SECRET`, `HERMES_GUEST_ONBOARDING`, `HERMES_DESKTOP_READY_FD`, `HERMES_UPDATE_HANDOFF_PID`.

These are **pre-existing and deliberate** — the pre-slice tree already carries 777. The vendored runtime retains upstream `HERMES_*` env-var names for config compatibility (consistent with `docs/STATUS.md` §36: "deferred env slice"). The `hermes.` module/service refs (`ai.hermes.gateway`, `hermes.desktop`, `hermes.service`) are likewise runtime-internal identifiers, present pre-slice.

**Verdict: PASS with caveat.** `hermes_cli` is fully translated in source. The 20-occurrence / 1-file `HERMES_*` delta introduced by the slice follows the runtime's existing (pre-slice) convention and is **not** evidence of a failed rename; it is the same class as the 777 already present. Whether those 20 should also be translated is a policy decision, not a mechanical break — flagged, not failed.

---

## Concrete breakages found (diagnosis only)

**26 modules that no longer import** (all from the sync slice; all caused by upstream callers merged into `qubit_cli/` whose provider symbols live outside the slice):

1. `qubit_cli.backup` — `utils.mkstemp_beside`
2. `qubit_cli.backup_restore` — `utils.mkstemp_beside`
3. `qubit_cli.claw` — `config_defaults.DEFAULT_SANDBOX_IMAGE`
4. `qubit_cli.gateway` — `config_defaults.DEFAULT_SANDBOX_IMAGE`
5. `qubit_cli.gateway_setup_wizard` — `config_defaults.DEFAULT_SANDBOX_IMAGE`
6. `qubit_cli.setup` — `config_defaults.DEFAULT_SANDBOX_IMAGE`
7. `qubit_cli.setup_summary` — `config_defaults.DEFAULT_SANDBOX_IMAGE`
8. `qubit_cli.setup_terminal` — `config_defaults.DEFAULT_SANDBOX_IMAGE`
9. `qubit_cli.local_runtime.catalog` — `qubit_platform.host.products.is_nvidia_n1x_pci_id`
10. `qubit_cli.web_routers.local_models` — `qubit_platform.host.products.is_nvidia_n1x_pci_id`
11. `qubit_cli.web_server` — `qubit_platform.host.products.is_nvidia_n1x_pci_id`
12. `qubit_cli.observability.shared_metrics_desktop` — `shared_metrics_contract.MEMORY_OP_METRIC`
13. `qubit_cli.observability.shared_metrics_events` — `shared_metrics_contract.MEMORY_OP_METRIC`
14. `qubit_cli.observability.shared_metrics_fields` — `shared_metrics_contract.MEMORY_OP_METRIC`
15. `qubit_cli.observability.shared_metrics_model` — `shared_metrics_contract.MEMORY_OP_METRIC`
16. `qubit_cli.observability.shared_metrics_signals` — `shared_metrics_contract.MEMORY_OP_METRIC`
17. `qubit_cli.observability.shared_metrics_snapshot` — `qubit_constants.get_hermes_home`
18. `qubit_cli.web_routers.cron` — `qubit_constants.get_hermes_home`
19. `qubit_cli.runtime_provider` — `qubit_cli.auth.looks_like_openrouter_key`
20. `qubit_cli.status` — `qubit_cli.auth.looks_like_openrouter_key`
21. `qubit_cli.status_auth` — `qubit_cli.auth.looks_like_openrouter_key`
22. `qubit_cli.sessions_cmd` — `qubit_state_errors.SessionActiveWriteGuardError`
23. `qubit_cli.sessions_cmd_browse` — `qubit_state_errors.SessionActiveWriteGuardError`
24. `qubit_cli.web_routers.sessions` — `qubit_state_errors.SessionActiveWriteGuardError`
25. `qubit_cli.plugins_cmd` — `plugins_cmd_listing.cmd_compat`
26. `qubit_cli.plugin_validate_locales` — `ModuleNotFoundError: No module named 'agent.i18n_layers'`

Plus: **end-to-end `qubit agent -z` fails** on breakage #19.

**Behavioral impact:** `web_server` failing to import means the dashboard/web surface cannot start; `gateway`, `setup`, `status`, `sessions_cmd` failing breaks the corresponding CLI families; `runtime_provider` failure breaks the whole agent turn path.

**9 distinct missing symbols, all providers outside the applied slice:**
`utils.mkstemp_beside`, `config_defaults.DEFAULT_SANDBOX_IMAGE`, `qubit_platform.host.products.is_nvidia_n1x_pci_id`, `observability.shared_metrics_contract.MEMORY_OP_METRIC`, `qubit_constants.get_hermes_home`, `qubit_cli.auth.looks_like_openrouter_key`, `qubit_state_errors.SessionActiveWriteGuardError`, `plugins_cmd_listing.cmd_compat`, `agent.i18n_layers`.

## UNDETERMINED

- **`qubit_cli.psutil_android` (`SystemExit: 1`)** — pre-existing (fails identically at `9a6432b2`); whether it is expected on this platform is undetermined and outside the slice.
- Whether the 20-occurrence `HERMES_*` delta introduced by the slice should be translated (policy, not mechanics). **UNDETERMINED by inspection** — needs a rename-policy decision.

## Method notes

- Pre-slice comparison ran in a **detached git worktree** at `9a6432b2` (`git worktree add --detach … 9a6432b2`), never via `git stash` on the live tree. **The live tree was not modified.**
- All import/test gates were run with an explicit `PYTHONPATH` and clean env (`env -u PYTHONPATH` for the negative case) to eliminate leakage from earlier shells.
- **Nothing was fixed. Diagnosis only.**
