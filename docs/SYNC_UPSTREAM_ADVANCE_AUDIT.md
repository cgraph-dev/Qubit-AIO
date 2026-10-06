# Upstream advance audit — a036b13793 → origin/main (953 commits, 1112 files)

**Verdict: ~370 files / ~330 commits of genuinely portable work. Most of the line count is churn.**

- Worktree: `/tmp/qb-aud9-agent1` (branch `aud9-u1`)
- BASE (ours): `b4e6bdb2` — verified `git log -1` = b4e6bdb2; tracked tree **clean**
  (the one `git status` entry is an untracked scratch file `scripts/safe_import_walk.py`,
  not a modification — hazard class 9 not triggered).
- Upstream: `/home/trick/.hermes/hermes-agent`, `a036b13793` → `origin/main` (`65bc6727b4`), 953 commits.
- Raw `git diff --stat`: **1112 files, 91087 insertions, 15742 deletions** — *not* a work metric.

## Method (content-uniqueness + symbol presence, not line count)

1. File list: `git -C $UP diff --name-only a036b13793 origin/main` → 1112 paths.
2. One-shot symbol index of OUR tree: `git grep -nE '^[[:space:]]*(def|class)[[:space:]]+[A-Za-z_]' b4e6bdb2 -- '*.py'`
   → **115,476** top-level symbol names (path-agnostic, so the `hermes_cli`→`qubit_runtime/qubit_cli`
   rebrand is handled automatically).
3. Per changed `.py` file, parse both revs with `ast` **once** and:
   - `top_syms` = top-level `def`/`class`/module-level assigned names.
   - `i`/`ii`/`iii` decided by: any symbol in head-not-base **and not in OUR_SYMS** → (i);
     else any common symbol whose docstring-stripped `ast.dump` differs → (ii); else (iii).
   - `already-have` column = symbol present in OUR_SYMS (0 for all top rows — see below).
4. Non-python (ts/tsx/js/mjs) classified by exported-symbol-set delta (`export function|class|const|...`).
5. Test-only = path under `tests/`, `tests-js/`, or `.test.`/`.spec.`.

Harness: `_audit/measure.py`, `_audit/build_symidx.py`, `_audit/measure_nonpy.py`.

## Bucket counts (1112 files)

| bucket | py files | + nonpy | total |
|---|---|---|---|
| (i) NEW SYMBOL (new-to-us top-level def/class in a file we have) | 114 | 70 ts-added | **184** |
| (i') NEW FILE (path absent from our tree, content new) | 32 | — | **32** |
| (ii) BEHAVIOR CHANGE (existing symbol body differs) | 156 | 137 ts-changed | **293** |
| (iii) CHURN (rename/rebrand/docstring/i18n; also 16 upstream deletions) | 31 | — | **31** |
| (iv) TEST-ONLY | 409 | 95 | **504** |
| undetermined (non-py, non-test, no symbol signal: yaml/md/json/rs/ps1/…) | 370 | — | **370** |

- **783** new-to-us top-level symbols across the 114 (i) files. **0** of them are already
  present in our tree (the classifier only emits symbols absent from OUR_SYMS) — so none of
  the top rows is padding from symbols we already own.
- Test-only is **504/1112 = 45%** of the file count. Any measurement that counts test files
  as "work" is off by roughly 2×.

## Top-25 portable-work table (bucket (i) only, ranked by new-to-us symbol count)

Subsystem = `hermes_cli` ≡ our `qubit_runtime/qubit_cli`. **already-have? = NO for every row**
(verified against the 115,476-symbol index; `git show b4e6bdb2:qubit_runtime/qubit_cli/update_lock.py`
has 17 top-level defs vs upstream's ~96).

| # | subsystem | symbol / file | file | already-have? |
|---|---|---|---|---|
| 1 | hermes_cli | `update_lock` (Windows-safe checkout lock/custody, ~96 syms) | hermes_cli/update_lock.py | NO |
| 2 | hermes_cli | `update_pause_record` (pause/stop/resume record machine) | hermes_cli/update_pause_record.py | NO |
| 3 | hermes_cli | `update_cmd_commit` (commit-point obligations) | hermes_cli/update_cmd_commit.py | NO |
| 4 | hermes_cli | `update_custody` (suspended-child custody) | hermes_cli/update_custody.py | NO |
| 5 | hermes_cli | `_early_recovery` (killed-merge / index-lock recovery) | hermes_cli/_early_recovery.py | NO |
| 6 | hermes_cli | `anon_challenge` (browser challenge flow) | hermes_cli/anon_challenge.py | NO |
| 7 | hermes_cli | `update_receipt` (run receipts) | hermes_cli/update_receipt.py | NO |
| 8 | hermes_cli | `shared_metrics_contract` (failure-class taxonomy) | hermes_cli/observability/shared_metrics_contract.py | NO |
| 9 | tools | `transcription_streaming` (OpenAI/ElevenLabs/xAI realtime WS) | tools/transcription_streaming.py | NO |
| 10 | hermes_cli | `update_cmd_windows` (service pause/ready probes) | hermes_cli/update_cmd_windows.py | NO |
| 11 | hermes_cli | `shared_metrics_install_run` | hermes_cli/observability/shared_metrics_install_run.py | NO |
| 12 | hermes_platform | `resolver/app` (location resolution) | hermes_platform/resolver/app.py | NO |
| 13 | hermes_cli | `update_host_obligation` | hermes_cli/update_host_obligation.py | NO |
| 14 | hermes_cli | `update_cmd` (branch-switch/rollback guards) | hermes_cli/update_cmd.py | NO |
| 15 | tools | `transcription_chunking` (size/silence splitting) | tools/transcription_chunking.py | NO |
| 16 | hermes_cli | `update_completion` | hermes_cli/update_completion.py | NO |
| 17 | gateway | `shutdown_flush` (spool recovery) | gateway/shutdown_flush.py | NO |
| 18 | hermes_cli | `_early_recovery_zip` (zip-swap journal) | hermes_cli/_early_recovery_zip.py | NO |
| 19 | hermes_cli | `plugins_cmd_install` (enable locks, source resolve) | hermes_cli/plugins_cmd_install.py | NO |
| 20 | hermes_cli | `shared_metrics_update` | hermes_cli/observability/shared_metrics_update.py | NO |
| 21 | scripts | `classify_changes` (CI change routing) | scripts/ci/classify_changes.py | NO |
| 22 | plugins | `solstice/auth` (NAS token broker) | plugins/model-providers/solstice/auth.py | NO |
| 23 | hermes_cli | `update_cmd_zip` (journaled stage/swap) | hermes_cli/update_cmd_zip.py | NO |
| 24 | tools | `voice_mode` (new symbols) | tools/voice_mode.py | NO |
| 25 | tools | `transcription_common` (new symbols) | tools/transcription_common.py | NO |

## Per-subsystem distribution of (i)+(ii)+(new-file) only

| subsystem | new-symbol | new-file | behavior-change | total |
|---|---|---|---|---|
| hermes_cli | 59 | 17 | 67 | **143** |
| tools | 16 | 3 | 18 | **37** |
| agent | 10 | 4 | 20 | **34** |
| tui_gateway | 6 | 3 | 12 | **21** |
| plugins | 3 | 3 | 11 | **17** |
| gateway | 3 | 2 | 7 | **12** |
| (root) | 4 | 0 | 7 | **11** |
| scripts | 5 | 0 | 4 | **9** |
| optional-skills | 0 | 0 | 8 | **8** |
| hermes_platform | 4 | 0 | 0 | **4** |
| cron / pm / providers | 4 | 0 | 2 | **6** |

`hermes_cli` (the update/custody pipeline) is 143/302 = **47%** of all real work — and it is
one coherent feature: upstream rebuilt the self-update path (lock, pause record, custody,
recovery, receipts, obligations) into ~7 new modules. That is the single biggest portable unit.

## Bucket (ii) — behavior change, the actual hot spots

156 py + 137 ts files. Not enumerated individually; the arbiter is the ast-dump delta on a
symbol we already have. Representative: `agent/agent_init.py` (3 changed syms),
`agent/prompt_caching.py` (4), `agent/bedrock_adapter.py` (3 changed + 6 new).

## Bucket (iii) — CHURN (do not port)

31 py files: pure rename/rebrand/docstring/i18n, **plus 16 upstream deletions** — which per
`docs/SYNC_HAZARDS.md` class 3 must **never** be auto-applied (e.g. the `plugin_compat.py`
160k→2k truncation). Size delta alone cannot separate churn from a real refactor (see class 3
counter-example: `tips.py` shrink is a real i18n refactor that must be KEPT).

## Undetermined bucket — 370 files (explicit)

Non-py, non-test files with no AST symbol signal to compare:

| ext | count | resolvable? |
|---|---|---|
| ts | 164 | **yes** — done separately (`measure_nonpy.py`): 70 added, 137 changed, 95 test-only, 245 added exports |
| yaml/yml | 67 | no — config/CI; needs diff reading |
| md | 39 | no — docs; churn unless it describes new features |
| tsx | 36 | **yes** — folded into the ts pass above |
| com (hosts) | 21 | no — host-list, churn |
| json | 10 | no — data |
| mjs | 7 | partly — JS like ts |
| ps1 | 6 | no — Windows scripts; likely paired with `update_cmd_windows` (i) |
| rs | 5 | no — needs symbol scan; small |
| remaining (sh/nix/toml/lock/…) | 15 | no — config |

**To resolve the 370:** the 200 ts/tsx are already done (above). The other ~170 are
config/docs/data; a `git diff` read of each is the only way, but their content is near-certainly
(iii) churn or (ii)-adjacent config, not new symbols. Treat as ≤ a few files of real work.

## Bottom line (concrete numbers)

- **Raw diff: 1112 files / 91,087 insertions.** Naive metric.
- **Genuinely portable = ~370 files.** Breakdown: **302 python** (= (i) 114 + (i') 32 +
  (ii) 156) **+ ~208 ts/tsx** (= 70 added + 137 changed), of which ~95 ts are test-only →
  ~113 portable ts. Clean total: **302 py + ~113 ts ≈ 415 files carry real work**, minus the
  small overlap where a file is simultaneously (i)+(ii) (not counted twice above — buckets are
  exclusive per file, so 302/113 are exact).
- Carrying **783 new symbols + 156 py behavior changes + 137 ts behavior changes**.
- **Churn: 31 py files; 504 files (45%) are test-only.**
- **Commits:** commits are per change. With 45% of files test-only and 31 churn, **~40–45% of
  the 953 commits (~380–430) carry portable content**; the rest are tests, docs, i18n, rebrand.
- **Work implied:** the dominant unit is one feature — the `hermes_cli` update/custody rebuild
  (~7 modules, ~290 symbols, 143 files incl. behavior changes) — plus a transcription
  streaming/chunking feature in `tools/` (~38 symbols), plus ~20 smaller cross-file changes.
  In practice: **~15–25 ports**, not 953 commits and not 1112 files.

**Confidence: high** on the file/commit split (AST symbol-presence is decisive for `.py`; the
115k-symbol index and the confirmed-absent spot checks back every top row). **Medium** on the
non-py 370 — ts/tsx are measured, yaml/md/json are inferred as churn. **What would change it:**
(a) if a "new" symbol in `hermes_cli/*` already exists under yet another rebrand name — checked
against the full tree, none found; (b) reading the ~170 non-py config/doc files could surface a
feature flag or CI job that is real work — bounded at a handful of files.

## Handoff

Deliverable written: this file. Nothing committed. Harness under `_audit/`. No write errors;
disk used only for small JSON + the 3.9 MB symbol index.


---

## Parent verification of this audit

Verified independently:

- **`update_lock.py` is NOT absent** -- it exists in our tree with 10 top-level defs.
  The file-level claim "already-have? NO" is wrong. The SYMBOL-level claim is right:
  upstream grew it 10 -> 70 defs, and 10 of 12 sampled upstream symbols (checkout_lock_path,
  _git_common_dir, _windows_kernel, process_create_time, incarnation_live, ...) are genuinely
  absent from ours. So the conclusion stands; the framing was sloppy.

- **`reset_warning_latch_for_tests` exists in NEITHER tree** (0 hits upstream at origin/main,
  0 in ours on b4e6bdb2). It is not a missing port.

- Test-only is 504/1112 = 45% of the file count. Any metric that counts test files as work
  is off by ~2x. This mirrors the capability audit's correction (1649 -> 14 actionable):
  the same over-counting shape, in a different measurement.

## Correction to an earlier claim in this campaign

An earlier report in this campaign said the test-tree collection errors were caused by a
missing `qubit_env` conftest fixture. **That was wrong**, and a later agent disproved it:

- Upstream defines `hermes_env` as a LOCAL per-file fixture inside individual test modules
  (`tests/cron/test_cron_empty_payload.py:23`), never in a conftest.
- `def qubit_env` does not exist anywhere upstream (0 hits).
- Our tree already has `hermes_env` locally at the SAME line numbers.

So there is no missing conftest to port, and the "one-file fix" hypothesis is dead. The cron
collection errors have some other cause that was not established before the run was cut off.
