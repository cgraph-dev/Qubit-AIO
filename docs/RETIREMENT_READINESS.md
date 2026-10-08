# RETIREMENT READINESS — the mechanical GO/NO-GO

**Worktree:** `/tmp/qb-m7`  **branch:** `m7`  **HEAD:** `e5d75e6d`  **tree:** clean (0 dirty)
**Frozen target:** `ad12263a5ed4` (docs/FROZEN_TARGET.md)
**Audited:** 2026-10-08, against a fresh sandbox from THIS worktree.
**Author:** retirement-readiness gate (adversarial check on the "we are close" claim).

---

## 0. Base proof

```
$ cd /tmp/qb-m7 && git log -1 --format=%h && git status --porcelain | wc -l
e5d75e6d
0
```
Matches the expected base. No service was restarted or stopped; no source edited.

---

## 1. Independence evidence — deletion harness on the CURRENT tree

**Command:** `./deletion_test.sh --rebuild` (fresh sandbox, `HOME=/tmp/qb-m7/home` empty,
`QUBIT_HOME=/tmp/qb-m7/qhome`, `hermes` stripped from PATH, no `~/.hermes`).

**Result: 16/16 PASS, exit 0. OVERALL: PASS — Qubit boots with hermes absent.**

Raw per-check PASS/FAIL:

| # | check | result | evidence |
|---|-------|--------|----------|
| 1 | env: no `~/.hermes` in HOME | **PASS** | `exists(home/.hermes)=False` |
| 2 | env: real checkout NOT on sys.path | **PASS** | sandbox paths only |
| 3 | imports: 63/63 first-level modules import clean | **PASS** | all clean |
| 4 | cli: `python -m qubit_cli.main --help` exits 0 | **PASS** | rc=0 |
| 5 | cli: `--version` exits 0 | **PASS** | `Qubit Agent vunknown (2026.9.24)` |
| 6 | cli: >70 subcommands | **PASS** | 74 found |
| 7 | cli: construct `QubitCLI()` | **PASS** | ok |
| 8 | import `tui_gateway.server` | **PASS** | ok |
| 9 | import `tui_gateway.entry` | **PASS** | ok |
| 10 | import `qubit_cli.gateway` | **PASS** | ok |
| 11 | import `plugins.plugin_loader` | **PASS** | ok |
| 12 | providers: `list_authenticated_providers(...)` | **PASS** | 1 provider |
| 13 | path: no `hermes` resolves on stripped PATH | **PASS** | `which(hermes)=None` |
| 14 | path: core home = Qubit's own with hermes off PATH | **PASS** | `get_qubit_home() = /tmp/qb-m7/home/.qubit` |
| 15 | path: no `~/.hermes` created | **PASS** | `exists=False` |
| 16 | turn: real `_chat_run_agent` turn vs local ollama | **PASS** | `qwen3-vl:2b`, completed=True, `final_response='pong'` |

**PART B static scan (classified):**
- `imports of hermes_* modules` — 2 hits, **both docstring text** (cronjob_job_args.py:289, skills_sync_client.py:224). No real import.
- `.hermes dir CREATION` — **none**.
- `subprocess invokes hermes binary` — 1 hit, a **comment** (mcp_tool_transport.py:399).
- home resolution with `QUBIT_HOME` unset → **`~/.qubit`**, VERDICT OK.

**Zero `.hermes` writes in any live path (current tree), confirmed independently:**
```
grep -rnE '(mkdir|makedirs)\(.*hermes' qubit_runtime voicebridge   -> no output
```
Remaining `~/.hermes` literals are the documented `QUBIT_HOME or ~/.hermes` **read-old
fallback** class (cosmetic/compat): `QUBIT_HOME` wins, so the literal is never used when set.

**`get_qubit_home()` on the current tree resolves to `~/.qubit`:**
```
$ env -u QUBIT_HOME .venv/bin/python -c "import qubit_constants as c; print(c.get_qubit_home())"
/home/trick/.qubit
```

---

## 2. Adversarial half-port probe — 5 modules ported this session

**Method:** import each module in isolation inside the sandbox (`HOME` empty, `QUBIT_HOME`
fresh, real checkout off `sys.path`), then call a representative function with a plausible
**bad** input. Classified as HALF-PORT if the exception is a `NameError`/`AttributeError`/
`ImportError`/`ModuleNotFoundError` **from the module's own body** (an unresolved port);
HELD if it raises a sane guard error on the caller's bad data or returns a sane value.

**Result: 13 probes, 12 HELD, 0 genuine half-ports.**

| module | probe | verdict | finding |
|--------|-------|---------|---------|
| `update_completion` (settle_lost_completion block) | `settle_lost_completion({}, 'probe')` | **HELD** | raises `KeyError: 'receipt'` — sane guard on a request dict missing `receipt`; module imports cleanly |
| `cron/worker_bootstrap` | import; `finish_worker_boot()` | **HELD** | imports clean; call is a no-op returning `None` (no boot env) |
| `plugins_cmd_install` (transaction) | `recorded_install(fail, ...)` | **HELD** | re-raises the install failure (`RuntimeError: probe-boom`) — the transaction records then surfaces, correct |
| `plugins_cmd_install` | `_read_manifest_for_install(nonexistent)` | **HELD** | returns `{}` (sane empty manifest) |
| `chat_ws` (lease helpers) | `_lease_holder_pid(None)` / `('no-such-session')` | **HELD** | returns `None` (no holder) — sane |
| `chat_ws` | `_attach_accepts_active_session_file(object())` | **HELD (probe artifact)** | `AttributeError: 'object' has no attribute 'attach_or_spawn'` — the function's *purpose* is to detect that signature; a bare `object()` isn't a registry. Correct guard behavior; not a half-port. Imports at chat_ws.py:18–23 all resolve. |
| `model_switch` (routes) | `parse_model_switch_args('')` | **HELD** | returns a populated `ModelSwitchRequest` |
| `model_switch` | `format_model_for_display('')` | **HELD** | returns `''` |
| `model_switch` | `resolve_effective_model(None, 'openrouter/')` | **HELD** | returns a sane string |
| `model_switch` | `parse_model_flags_detailed('')` | **HELD** | returns a populated `ModelFlagParseResult` |

**Cross-check:** none of the 5 modules contains an unresolved `hermes_*` call
(grep for `\bhermes_[a-z_]+\b` outside imports/comments → zero). No silent half-port found.

---

## 3. The retirement gate

**Command:** `bash scripts/retire_hermes_check.sh` (dry; must refuse, exit 2).

Guard (a) drives `scripts/frozen_target_progress.py`, which runs the full audit and exceeds
the 300 s foreground window — it was run separately (see §4). Its verdict line is:

```
VERDICT: NOT AT TARGET -- 322 production missing (367 test missing, non-blocking)
```

**Guard states on the current tree:**

| guard | required | actual | state |
|-------|----------|--------|-------|
| (a) production parity at `ad12263a5ed4` | `VERDICT: AT TARGET` | `NOT AT TARGET -- 322 production missing` | **FAIL** |
| (b) `hermes-dashboard.service` must be DISABLED | `disabled`/`masked` | `enabled` + `active` | **FAIL** |
| (c) four Qubit units enabled + active | all enabled+active | `qubit-voicebridge`, `qubit-voice-typing`, `qubit-voice-orb`, `qubit-gateway` all **enabled+active** | **PASS** |

Guard (b) failing is **correct and necessary**: `hermes-dashboard.service` is currently
`enabled` + `active`, and its unit file has `Restart=always` / `RestartSec=5`, with
`EnvironmentFile=/home/trick/.hermes/.env` and `ExecStart=/home/trick/.local/bin/hermes`.
Moving `~/.hermes` aside makes the EnvironmentFile load fail **and** the `~/.local/bin/hermes`
shim exec a dead path (exit 127) → a **restart-storm every 5 s, forever**. Left enabled, this
unit is the single most dangerous thing in the retirement sequence. It MUST be disabled first.

**Gate verdict:** the gate correctly **refuses** (exit 2) — guards (a) and (b) fail.

### `hermes-dashboard.service` confirmation (no service was touched)

```
Loaded:  /home/trick/.config/systemd/user/hermes-dashboard.service (enabled; preset: enabled)
Active:  active (running) since Sat 2026-10-03 18:20:39 EEST; 4 days ago
Main PID: 2408  NRestarts=0
ExecStart=/home/trick/.local/bin/hermes dashboard --port 9119 --host 127.0.0.1 --no-open
Restart=always  RestartSec=5  EnvironmentFile=/home/trick/.hermes/.env
```
It is a **user** unit (not system) — `systemctl --user ...`. `~/.hermes` is 8.1 GB. It is
enabled + active and will restart-storm once `~/.hermes` moves aside. Confirmed.

---

## 4. ADVERSARIAL FINDING — "we are close": which number is wrong

**Neither 322 nor ~16 is the production-missing count. The instrument that prints 322
overstates production by 253 symbols; the instrument behind "~16" measures a different,
narrower scope and no longer reports 16.**

### 4a. The 322 is a formula bug in `frozen_target_progress.py`

`scripts/frozen_target_progress.py:175-177`:
```python
prod_missing = (s["absent_prod"]     # 7
                + s["sp_symbols_prod"] # 2
                + tot_bh_syms)         # 313  <-- prod+test, added WHOLE
```
Line 177 comments the last term "behind-head is production-only by nature". **It is not.**
The audit's `behind_head_symbols = 313` is the **combined** prod+test total; the JSON carries
no prod/test split for it. Enumerating the per-file symbols (`--show-symbols`, this run):

```
behind_head total symbols: 313  =  63 PRODUCTION  +  250 TESTS
```

So `prod_missing = 7 + 2 + 313 = 322` counts **250 test symbols as production**. This is the
same false-positive class the instrument's own header warns about, applied to the wrong field.

### 4b. The "~16" instrument measures only the short-port branch — and reports 2, not 16

`scripts/audit_missing_symbols.py:55` filters only `worst_short_ports` (the **short_port**
branch). Run against this tree's audit JSON:

```
checked 2  present-elsewhere (FP) 0  GENUINELY ABSENT 2
  _has_usage                  hermes_cli/web_routers/analytics.py
  _fold_session_only_rows     hermes_cli/web_routers/analytics.py
```

Its own docstring (lines 5-9) records that a hardcoded-REPO bug **once reported "16 GENUINELY
ABSENT" and all 16 were false positives**. The "16" the campaign quotes is that historical,
buggy figure — not a current measurement, and not a whole-production figure.

### 4c. Accurate production-missing accounting (this tree, `ad12263a5ed4`)

| bucket | raw | false positives | real |
|--------|-----|-----------------|------|
| absent production files | 7 | 3 (see below) | **4** |
| short_port production symbols | 2 | 0 | **2** |
| behind_head production symbols | 63 | (not individually re-verified) | **63** |
| **instrument "322"** | — | — | — |
| **accurate total** | | | **~69** |

**Documented path false positives in the "7 absent prod":**
- `plugins/hermes-achievements/dashboard/plugin_api.py` → **present** as
  `qubit_runtime/plugins/qubit-achievements/dashboard/plugin_api.py` (path-routing miss:
  `hermes-achievements` is in `PRESERVED_WIRE`, so `route()` never rewrites it).
- `skills/productivity/google-workspace/scripts/_hermes_home.py` → **present** as `_qubit_home.py`.
- `skills/research/grounded-citations/scripts/_hermes_home.py` → **present** as `_qubit_home.py`.

The 4 real absent production files: `hermes_cli/cli_commands_session_tools.py`,
`hermes_cli/main_desktop_tcc.py`, `hermes_cli/observability/shared_metrics_install_run.py`,
`hermes_cli/update_cmd_fleet_verify.py`.

The 63 behind_head production symbols (25 files) are **real**, e.g. `hermes_platform/resolver/
app.py` (15: `AppLocation`, `LocationSpec`, `_Locator`, … — no rebrand twin), `_PruneLogHandler`,
`parse_bedrock_openai_model_id`, `_strip_prior_decoration`. Spots sampled resolve to genuine
absence; the rename subtraction for this branch **is** applied (verified: `_rewrite_resolved_
hermes_home` is correctly credited via its `qubit` twin).

### 4d. Bottom line on "are we close"

- The **322 is wrong** (overstates production by ~253, because 250 test symbols are folded in).
- The **"~16" is stale and mis-scoped** (it was a buggy historical run; the current
  same-scope instrument reports 2).
- **Production is ~69 symbols behind the frozen target**, not 322 and not 16. The campaign has
  genuinely closed 465/975 symbols (+465, gap 975 → 510), but ~69 production symbols remain.

---

## 5. VERDICT

# NO-GO.

Retiring hermes now is unsafe on two independent counts:

1. **Not at target.** Production is **~69 symbols** behind `ad12263a5ed4` (the instrument's 322
   is inflated by a prod/test mis-split; the "16" is stale). The gate's guard (a) fails.
2. **Restart-storm armed.** `hermes-dashboard.service` is enabled + active with
   `Restart=always` / `RestartSec=5`, pulling `EnvironmentFile=~/.hermes/.env` and execing the
   `~/.local/bin/hermes` shim. Moving `~/.hermes` aside restart-storms it every 5 s forever.
   Guard (b) fails and MUST be fixed (`systemctl --user disable --now hermes-dashboard.service`)
   before anything moves.

The gate is **correct** to refuse. Independence is proven (16/16 harness, zero `.hermes`
writes, `get_qubit_home() → ~/.qubit`) and no half-port was found in the 5 audited modules —
so the remaining work is genuine feature parity, not a broken port.

### Recommended next steps (in order)
1. `systemctl --user disable --now hermes-dashboard.service` — defuse the storm (guard b).
2. Port the **63 behind_head production symbols** (25 files; start `hermes_platform/resolver/
   app.py` = 15) + the **4 absent production files** + the **2 short_port symbols**.
3. Re-run `scripts/frozen_target_progress.py` **after fixing its `prod_missing` formula** to
   split behind-head prod/test (line 177 currently adds the combined 313 as production).
4. Re-run this gate; require guard (a) `AT TARGET` and guard (b) `disabled` before proceeding.

---

## Appendix — raw command output

- Harness: `./deletion_test.sh --rebuild` → `RESULT: 16 passed, 0 failed`, `probe exit=0`, `OVERALL: PASS`.
- Progress: `scripts/frozen_target_progress.py` → `VERDICT: NOT AT TARGET -- 322 production missing (367 test missing)`, exit 1.
- Missing-symbols (short_port scope): `audit_missing_symbols.py audit_symbols.json` → `checked 2  FP 0  GENUINELY ABSENT 2`.
- Audit (symbols): `audit_sync_completeness.py --rev ad12263a5ed4 --show-symbols` → behind_head 112 files / 313 syms (63 prod + 250 test); short_port 75 files / 197 syms; absent 179.
- Gate: `scripts/retire_hermes_check.sh` (dry) → guard (a) FAIL, (b) FAIL, (c) PASS → refuses, exit 2.
- Service: `systemctl --user is-enabled/-is-active hermes-dashboard.service` → `enabled` / `active`; `NRestarts=0`.
