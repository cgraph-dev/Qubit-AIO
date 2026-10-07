# Batch `deleg_a549f486` — results and corrections

## Independent verification (task 5) — 6 claims tested

| # | claim | verdict |
|---|---|---|
| 1 | four gates pass | **CONFIRMED** (raw: `VERDICT: PASS -- all blocking checks green`) |
| 2 | both clobbers fixed | **CONFIRMED** (grep 1 / wc 199 / `get_active_provider` 2; runtime import OK) |
| 3 | **voice work intact after merge** | **CONFIRMED** — 31/31 distinctive added symbols present. **No voice work lost.** |
| 4 | `QubitConsoleEngine` resolves | **REFUTED for that tree** — correct there; the alias exists on the branch but the worktree predated the commit |
| 5 | completeness audit trustworthy | **REFUTED** — see below |
| 6 | real test sample passes | **CONFIRMED**, with a path correction |

**Claim 3 was the one worth falsifying, and it held.** The merge did not lose the voice
work: `65d72246` is an ancestor, all 31 distinctive added symbols survive, and in the 2
files changed afterwards every removed line was replaced by an improved version.

## Genuine defect found and fixed: the completeness audit graded the wrong tree

`scripts/audit_missing_symbols.py` line 5 hardcoded
`REPO = Path("/home/trick/Projects/Repos/qubit-voicebridge")`. Run from a worktree it
reported **16 "GENUINELY ABSENT" symbols and all 16 were false positives** — each existed
as a real `def` in the flagged file. A porter acting on that list creates duplicate
objects (hazard #1).

Fixed to `Path(__file__).resolve().parent.parent`, the same repair already applied to
`sync_driver.py:29`. This was **hazard #10 still live** despite being documented.

**Lesson:** the audit that generates the work list must be proven correct before anyone
acts on it. Two agents this campaign were sent to "fix" symbols that were never missing.

## The test tree — the "1,446 errors" figure was wrong by ~100x

Full collection across `qubit_runtime/tests` on the clean base:

**63,240 collected, 15 errored, 78 deselected, 135.5s.**

There is no `qubit_env` fixture storm. The prior disproval holds; the earlier 1,446 number
was measurement noise.

Root causes of the 15: `missing_symbol`=12, `import_mismatch`=2 (basename collisions),
`name_error`=1. Top missing symbols: `_stored_values`=5, `parse_model_flags`=3,
`use_host_node_as_pm_node`=2, `QubitConsoleEngine`=1, `reset_warning_latch_for_tests`=1,
plus `ENTRY_POINTS['qubit']` KeyError.

Three are the familiar rebrand-blindness:
- `parse_model_flags` — `qubit_cli/model_switch.py` only defines `parse_model_flags_detailed`;
  3 tests expect the legacy tuple API
- `QubitConsoleEngine` — `console_engine.py:378` still declares `HermesConsoleEngine`
- `ENTRY_POINTS` in `_launchers.py:93` still keyed `"hermes"`

## Stray `tests/hermes_cli/` tree — the prior audit was WRONG

The earlier recommendation ("every filename duplicated under `tests/qubit_cli/`, 120
byte-identical, safe to remove") is **refuted**:

- **98 of 303 files are UNIQUE** — not duplicated
- only **1 of 205** twins is byte-identical (not 120); **204 differ**
- the stray tree is the **newer, half-rebranded** upstream revision; `tests/qubit_cli/` is the older
- nothing imports `tests.hermes_cli`

**Do not delete it.** Port the 98 unique files rebranded, close the revision gap, then
retire it. Deleting now would lose newer test coverage. (`_stored_values` and
`use_host_node_as_pm_node` exist ONLY there — that is why tests want them.)

## State of the main checkout

The checkout is on branch **`pre-sync-worktree-snapshot`**, not `main`. This is fine:
`main` (`f984911c`, Sep 16) is an **ancestor**, and the sync branch is **376 commits
ahead**. Nothing is stranded. `main` is simply an old snapshot.

**779 uncommitted files** remain in the working tree (the voice/orb work).
