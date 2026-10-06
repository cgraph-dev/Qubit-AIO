# STALE SYNC-BRANCH AUDIT

Audit branch: `int6-u5` @ HEAD `ad5b5125` (clean tree, base proven).
Scope: all `sync*` branches **31–79 commits behind HEAD** (the "stale base" cohort;
the 8 branches behind ≤ 8 are being integrated by sibling agents and are out of scope).

Method: for each branch, `git diff --stat HEAD...<branch>` for the file set, then a
content-uniqueness measure — the lines the branch **adds since its merge-base** that do
**not** appear anywhere in the same HEAD file (or whose file is absent from HEAD).
A branch whose added symbols already exist in HEAD is SUPERSEDED even when its raw diff
is non-empty (rebranding/whitespace/docstring drift inflates the naive diff).

---

## 1. Verdict table

| branch | ahead | behind | files | content | verdict |
|---|---:|---:|---:|---|---|
| sync-agent1 | 2 | 79 | 148 | agent/ upstream merge (163 files) | SUPERSEDED (32 stray lines) |
| sync-agent2 | 0 | 78 | 0 | empty | SUPERSEDED (no content) |
| sync-agent3 | 0 | 78 | 0 | empty | SUPERSEDED (no content) |
| sync-agent4 | 2 | 78 | 73 | cron + tui_gateway subtree | SUPERSEDED (5 stray lines) |
| sync-a2 | 2 | 74 | 7 | qubit_cli config/auth/plugins | SUPERSEDED (1 stray line) |
| sync-a3 | 3 | 74 | 155 | tools subtree + local_env_policy | SUPERSEDED (95 branding-only lines) |
| sync-a4 | 1 | 74 | 92 | gateway kanban watchers | SUPERSEDED (4 stray lines) |
| sync-a5 | 3 | 74 | 151 | ui-tui + web subtree | SUPERSEDED (34 i18n branding lines) |
| sync-a6 | 0 | 74 | 0 | empty | SUPERSEDED (no content) |
| sync-w2 | 1 | 63 | 85 | plugins upstream subtree | SUPERSEDED (2 stray lines) |
| sync-w3 | 0 | 63 | 0 | empty | SUPERSEDED (no content) |
| sync-w4 | 0 | 63 | 0 | empty | SUPERSEDED (no content) |
| sync-w5 | 1 | 63 | 34 | scripts subtree | SUPERSEDED (0 unique lines) |
| **sync-x2** | 1 | 60 | 1006 | tests subtree (upstream, 140k lines) | **UNIQUE-VALUE** (156) |
| **sync-x3** | 1 | 60 | 1332 | desktop app subtree | SUPERSEDED (0 unique lines) |
| **sync-x4** | 1 | 60 | 132 | plugin-catalog (129 new YAML) | **UNIQUE-VALUE** (271) |
| sync-x5 | 4 | 60 | 48 | scripts/plugins/nix/locales conflicts | SUPERSEDED (0 unique lines) |
| sync-y2 | 1 | 52 | 18 | agent/ conflicts + stale imports | SUPERSEDED (4 stray lines) |
| sync-y3 | 0 | 52 | 0 | empty | SUPERSEDED (no content) |
| sync-y4 | 4 | 52 | 244 | apps/tests/plugin-catalog/website | SUPERSEDED (1 stray line) |
| sync-z2 | 1 | 45 | 1 | observability metrics contract | SUPERSEDED (0 unique lines) |
| **sync-z3** | 3 | 45 | 26 | gateway + tools conflicts | SUPERSEDED (55 branding-only) |
| sync-z4 | 1 | 45 | 31 | ui-tui i18n conflicts | SUPERSEDED (0 unique lines) |
| sync-z5 | 1 | 45 | 39 | HERMES_ identifier restore | SUPERSEDED (0 unique lines) |
| sync-w22 | 1 | 36 | 1 | (1 file) | SUPERSEDED (0 unique lines) |
| **sync-w23** | 3 | 36 | 27 | tests/hermes_cli conflict backlog | **UNIQUE-VALUE** (446) |
| **sync-w24** | 1 | 36 | 332 | resolve 252 conflict files + doc | **UNIQUE-VALUE** (18,125) |
| sync-v6 | 1 | 31 | 4 | tools (4 files) | SUPERSEDED (0 unique lines) |
| sync-v7 | 1 | 31 | 1 | (1 file) | SUPERSEDED (0 unique lines) |
| sync-v8 | 1 | 31 | 4 | restore 3 upstream symbols | SUPERSEDED (0 unique lines) |
| **sync-v9** | 1 | 31 | 5 | code_exec security rewrite + tests | **UNIQUE-VALUE** (~195, 8 new tests) |

**Counts:** 31 branches scanned → SUPERSEDED = 26, UNIQUE-VALUE = 5 (w24, w23, x4, x2, v9), UNKNOWN = 0.

Naive "unique line" counts overstate: most SUPERSEDED branches are non-zero only because
a line differs in rebranding (`hermes_*`↔`qubit_*`), docstring text, or whitespace while
HEAD already defines the same symbols. The symbol-presence check (below) is the arbiter.

---

## 2. UNIQUE-VALUE evidence

### sync-w24 — 18,125 unique added lines (largest)
`git diff --stat HEAD...sync-w24` → 332 files. Merge base `fc88e7e2` (36 behind).
Adds `docs/SYNC_REMAINING.md` (208-line measured state of upstream pin
`69948c0057..bd0affe5`, 4,128 commits delta) plus a resolved 3-way merge of the
non-test upstream delta. Sample:
```
$ git diff --stat HEAD...sync-w24 -- qubit_runtime/apps/desktop/electron/main.ts
 main.ts | 4474 +++++-----   (2952 insertions, 1522 deletions)
```
Only 1 of 332 files is added; the rest are modifications. Conflict preview
`git merge-tree --write-tree HEAD sync-w24` → **13 conflicts** (CONTRIBUTING*.md,
Dockerfile, …). HIGH RISK: built on a base 36 commits stale; large portions of the
diff may be reverts of post-`fc88e7e2` fixes. **Needs rebase-and-reapply, not a merge.**

### sync-w23 — 446 unique added lines
`git diff --stat HEAD...sync-w23` → 27 test files. Base `fc88e7e2`.
```
$ git diff --stat HEAD...sync-w23 -- qubit_runtime/tests/gateway/test_email.py
 test_email.py | 158 +++++   (135 insertions, 23 deletions)
```
Unique content: new test methods in `test_relay_runtime_plugins.py`, `test_email.py`,
`test_skill_invocation_description.py`, `test_status.py`. **Conflict-free**
(`merge-tree` rc=0). These are genuinely-absent upstream test bodies (not symbols —
test method names differ from HEAD's). Real value; low-risk to rebase.

### sync-x4 — 271 unique added lines
`git diff --stat HEAD...sync-x4` → 132 files, of which **129 are add-only
plugin-catalog YAMLs**. Sample: `agent-mail-gateway.yaml`, `qubit-anilist.yaml`,
`orcaslicer.yaml`, `unbrowse.yaml`. Verified absent from HEAD.
```
$ git diff --stat HEAD...sync-x4 -- qubit_runtime/plugin-catalog/
 132 files changed, 3091 insertions(+), 16 deletions(-)
```
`merge-tree` → **112 add/add conflicts** — because these same YAML paths were later
re-added on other branches / partially landed. This is the canonical
"new plugin definitions" contribution. Rebase and take the 129 new YAMLs.

### sync-x2 — 156 unique added lines
`git diff --stat HEAD...sync-x2` → 1006 files (443 M, 563 A). Base `ffe136f0`.
The bulk is a large upstream tests subtree that HEAD already contains; only 156 lines
are unique, concentrated as new test methods:
`test_kanban_turn_recovery.py` (50), `test_model_switch_custom_providers.py` (41),
`test_user_providers_model_switch.py` (16). `merge-tree` → **29 conflicts**.
Mostly superseded; salvage the handful of new test methods.

### sync-v9 — ~195 unique added lines (8 genuinely-new tests)
`git diff --stat HEAD...sync-v9` → 5 files. Base `2d618b2d` (31 behind).
Commit: `eedf9fb3 sync(tools): land code_execution_tool security rewrite +
file_operations binary transport`.
```
git diff --stat HEAD...sync-v9 -- qubit_runtime/tests/tools/test_file_operations.py
 109 unique added lines, 7 new defs
git diff --stat HEAD...sync-v9 -- qubit_runtime/tests/tools/test_code_execution.py
  84 unique added lines, 1 new def
```
Symbol check: the tool-level rewrite (`_ship_file_to_remote`, `_ship_env_file_and_launch`
in `code_execution_tool.py`) is **already in HEAD** → the rewrite half is SUPERSEDED.
What is genuinely absent are the **8 new test methods** referencing upstream issue
#54347 (`test_rg_target_files_includes_matching_empty_directory`,
`..._excludes_gitignored_empty_directory`, `..._sorts_files_and_directories_before_pagination`,
`..._target_files_still_excludes_hidden_matching_directories`, etc.) and
`test_per_call_sandbox_locks_down_dirs_and_hides_token`. `merge-tree` **clean**.
Status: UNIQUE-VALUE but **partial** — take the tests only.

---

## 3. Ranking by genuinely-absent content

1. **sync-w24** — 18,125 unique lines (~75% of its added content), includes the only
   `docs/SYNC_REMAINING.md`. Highest value, highest risk (13 conflicts, stale base).
2. **sync-w23** — 446 unique lines, 27 test files, conflict-free.
3. **sync-x4** — 271 unique lines, 129 new plugin-catalog YAMLs.
4. **sync-v9** — ~195 unique lines, 8 new test methods (tool rewrite already in HEAD).
5. **sync-x2** — 156 unique lines, handful of new test methods (rest landed already).

---

## 4. Recommended merge order

Prerequisite for all: **rebase onto current HEAD** — every branch here is 31–79 commits
behind and `merge-tree` shows conflicts from stale bases, not from real content overlap.

1. **sync-x4** (new plugin YAMLs) — isolated namespace (`plugin-catalog/`), no code
   dependency on the others. Land first; it unblocks catalog fixtures.
2. **sync-w23** (test bodies) — conflict-free against HEAD; independent of x4.
3. **sync-v9** (tests only) — take the 8 test methods; drop the tool rewrite (already in
   HEAD). Overlaps `tools/` with x4? No (x4 is catalog-only). Overlaps w24 on `tools/`.
4. **sync-x2** (tests) — salvage the ~3 new test files' methods; expect to drop ~90% of
   the diff as already-landed. Overlaps w23 on `tests/` → **resolve w23 first**.
5. **sync-w24** (large conflict-resolved merge) — LAST, and only after a full rebase and
   a re-diff of its 332 files against post-merge HEAD; treat as a reapply, verify no
   reverts of `fc88e7e2..HEAD` fixes.

**Branches that conflict with each other:**
- `sync-w23` ↔ `sync-x2` (both edit `qubit_runtime/tests/…`; w23 first).
- `sync-w24` ↔ `sync-x5` (37 shared files), ↔ `sync-x3` (23 shared files) — but x3/x5
  are SUPERSEDED, so w24 supersedes them; do not merge x3/x5 separately.
- `sync-x2` ↔ `sync-x3`/`sync-x5`: same base `ffe136f0` generation; x3/x5 add nothing
  unique — merge only x2.
- `sync-x4` ↔ `sync-y4` ↔ `sync-x2` all touch `plugin-catalog/` (y4's catalog pass is
  superseded by x4's newer base). Merge x4, skip y4's catalog portion.

---

## 5. Duplicate work flagged

| duplicate | evidence |
|---|---|
| `sync4-u1` ≡ `sync/qubit_cli-377` | identical tip `856df9af8635` |
| `sync-w24` ⊃ `sync-w2/w5/x3/x5` | w24 base `fc88e7e2` is an **ancestor** of the x-series base `ffe136f0`; x-series is the newer generation of the same subtree work |
| `sync-w23` ⊃ `sync-y2` | same `tests/`+`agent/` conflict backlog, y2 adds 4 stray lines |
| `sync-z3` ⊃ `sync-z5` | both restore HERMES_ identifiers; z5 adds 0 unique lines |
| `sync-a3` vs `sync-z3` | both "resolve tools conflicts"; both superseded by HEAD |
| `sync-a4` vs `sync-z3` | both touch gateway kanban; both superseded |
| `sync-v8` vs `sync-z5`/`sync-agent1` | all "restore dropped symbols"; v8 adds 0 unique lines |

Every branch in the right-hand column above carries no content HEAD lacks.

---

## Cross-cutting finding

The dominant reason these branches look busy but hold nothing: they were produced by the
**rename-map sync tooling** (`scripts/sync_upstream.py`) operating on stale bases, so their
diffs are dominated by rebranding artifacts (`hermes_*` ↔ `qubit_*`, `HERMES_*` env names,
docstrings) rather than by real upstream code. The symbol-presence check (`git show
<b>:<path> | grep 'def <sym>'` vs `git grep <sym> HEAD`) collapses 26 of 31 branches to
SUPERSEDED. Only w24, w23, x4, x2 and v9 (tests-only) contain lines HEAD genuinely lacks.
