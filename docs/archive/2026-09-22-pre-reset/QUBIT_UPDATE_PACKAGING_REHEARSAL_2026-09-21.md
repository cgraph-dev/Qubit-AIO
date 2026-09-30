# Qubit Update & Packaging Rehearsal — 2026-09-21

**Status:** working note, **not** a status authority and **not** a verification
claim. It records what was implemented and what was actually executed in the
update/packaging workstream. Read completion status from the active brief and
the current ledgers, not from this file.

Scope of the workstream (sole-owner files only):

- `voicebridge/update_manager.py` (hardened, additive)
- `scripts/clean_install_rehearsal.py` (new)
- `tests/test_update_packaging9u.py` (new)
- this document (new)

No other product files were changed. No commit/push was performed.

---

## 1. Update path contract

The update path is a checkout-scoped, **non-destructive** operation. The
dashboard "Update Qubit" card drives it through:

- `GET /api/qubit/update/check` → `h_qubit_update_check` →
  `voicebridge.update_manager.check` (read-only), and
- `POST /api/qubit/update` → `h_qubit_update_apply` →
  `voicebridge.update_manager.apply` (mutating, confirmed).

`rollback` is **an operator command that is described, never executed** by the
manager. That is the whole point of the safety contract: the manager runs
exactly one update command, `git pull --ff-only <remote> <branch>`, and never
`reset`/`checkout`/`stash`/`clean`/`--force`/`--hard`.

### Gates (order matters)

`apply` refuses, in order, before any mutating command:

1. **confirmation** — `confirm` must be true (`CONFIRM_REQUIRED`);
2. **e-stop** — the single e-stop authority must be clear (`UPDATE_ESTOP`);
3. **fail-closed test/operator guard** — no injected executor and `under_test()`
   or `QUBIT_UPDATE_DISABLED=1` (`UPDATE_UNAVAILABLE`);
4. **checkout** — not a git checkout → `UPDATE_OPERATOR_OWNED` naming
   `scripts/install.sh`;
5. **dirty worktree** — `git status --porcelain` non-empty → `WORKTREE_DIRTY`
   and nothing is touched.

Only then does it read the pre revision, pull the **configured**
`update.remote`/`update.branch` explicitly, re-read the post revision, and
verify the post revision equals the configured remote head.

## 2. What was hardened (additive)

The module already refused dirty trees, honored confirmation and e-stop, and
pulled the configured remote/branch. Three truthfulness gaps were found and
fixed; no existing behavior was removed.

1. **Missing branch was misreported as unreachable.** `git ls-remote` returns
   exit 0 with empty output when the remote answers but has no such branch.
   That was collapsed into the same "couldn't reach the update source" message
   as a real network failure — false. `_remote_head` now returns a three-way
   result (`ok` / `branch_missing` / `unreachable` / `unresolved`), and `check`
   reports a distinct, truthful message and `remote_reachable` /
   `remote_branch_found` fields.
2. **`can_apply` could be true with an unresolvable update source.** A clean
   checkout whose configured remote/branch could not be resolved reported
   `can_apply=true`. It now requires a resolvable remote head, so the dashboard
   never offers an update it cannot fetch.
3. **Post-update verification was silent when it could not run.** When the
   remote head could not be re-read after a successful pull, the receipt still
   looked like a verified match. `apply` now returns `remote_verified` and, when
   it is false, a `warning` plus `remote_check_detail` naming exactly what could
   not be confirmed. A post revision that *does* resolve and disagrees with the
   remote head remains a typed `UPDATE_FAILED`.

The receipt also now carries a structured, non-executed `rollback` object
(`{kind: "operator", command, revision, executed: false, note}`) alongside the
existing human-readable `rollback_plan` string.

## 3. Tests

Command:

```
python -m pytest tests/test_update_packaging9u.py -q
```

Result observed 2026-09-22 in this worktree: **17 passed** (2.0 s). The combined
update/packaging focused set observed **47 passed** (5.3 s):

```
python -m pytest tests/test_update_manager9u.py \
  tests/test_update_apply_rehearsal9u.py \
  tests/test_update_qualification9u.py \
  tests/test_update_packaging9u.py -q
```

`tests/test_update_packaging9u.py` builds a real temp git repo + local bare
remote (no network) and covers:

- `check` reports update available and resolves the configured remote/branch;
- configured branch selection (`origin/release`) is honored by both `check` and
  `apply`;
- a **missing branch** is diagnosed as `branch_missing` (reachable, branch
  absent) and **never** as "couldn't reach";
- an unreachable remote is typed `DEGRADED` with `can_apply=false` and no
  fabricated "up to date";
- the confirmation and e-stop gates run **no** git command;
- a dirty worktree is refused and left byte-for-byte untouched;
- a real fast-forward moves HEAD and content, and
  `post_revision == bare remote head` with `remote_verified=true`;
- a post revision that disagrees with the remote head is `UPDATE_FAILED`
  (`remote_verified=false`), driven by an injected deterministic executor;
- the receipt's rollback is a non-executed operator command; executing the
  **literal plan string** with `shlex.split` in the temp repo (the operator's
  action, not the manager's) restores the pre revision while the bare remote is
  unchanged;
- a scan of every executed argv proves no destructive verb/flag was passed.

The second half covers the packaging rehearsal script: the hermetic `--check`
preflight, the isolation planner (rejects a `~/.hermes` or real-HOME work root),
typed `FAIL` for missing entry points, typed `DEGRADED` for a missing dashboard
bundle, `run()` refusing before it builds when there is no dashboard, and the
`--check` CLI staying inside a throwaway temp root under a redirected HOME.

## 4. Clean-install / packaging rehearsal

`scripts/clean_install_rehearsal.py` rehearses the shipped artifact:

```
python scripts/clean_install_rehearsal.py --check   # hermetic, no network, no writes
python scripts/clean_install_rehearsal.py --run     # real build + install + start
```

Typed exit codes: `0` PASS, `2` FAIL, `4` DEGRADED. A skipped prerequisite is a
`DEGRADED`, never a silent pass.

- **`--check`** parses `pyproject.toml` for the canonical entry points, confirms
  the built dashboard bundle is present, and proves the isolation plan. It
  writes nothing (it does not even create the work dir).
- **`--run`** builds the wheel into a throwaway `--out-dir`, creates a throwaway
  venv, installs the wheel with declared dependencies, runs every declared
  entry point's `--help`, then starts the **installed** dashboard out of the
  source tree and probes `/` (302 → `/sessions`), the dashboard asset, and the
  vendored runtime. It snapshots `git status --porcelain` before/after and fails
  if the product worktree's tracked state changed.

Isolation: `HOME`, all four `XDG_*` roots, `QUBIT_HOME`, `UV_CACHE_DIR`,
`PIP_CACHE_DIR`, the venv, the dist dir, and the state dir are all under a unique
temp root; `PYTHONPATH`/`PYTHONHOME`/`VIRTUAL_ENV` are cleared;
`QUBIT_UPDATE_DISABLED=1`. `isolation_findings` rejects any plan path that
escapes the work root, targets the real HOME, or contains a `.hermes` component.
The product worktree is read to build the wheel but is never written; the
git-ignored dashboard bundle must already exist, so the script refuses
(`DEGRADED`) rather than building it into the checkout. `--run` removes its
throwaway root unless `--keep` is passed.

## 5. Verification status and uncertainty

- **Executed here:** the focused pytest set above (`47 passed`) and
  `scripts/clean_install_rehearsal.py --check` (exit 0, `state: OK`).
- **NOT executed here:** the full `--run` packaging rehearsal (it builds a
  multi-hundred-MB wheel, including the vendored runtime and docs, and installs
  the declared dependency set; that is an operator/CI action). Therefore the
  build/install/start path of the script is **implemented and unexercised in
  this session**, not verified.
- **NOT executed here:** the frozen final matrix, full backend/frontend suites,
  differential parity, or any independent verification. This note is a working
  record — **not** VERIFIED, not CODE_READY, not 100%.
- The `--run` worktree-unchanged guard relies on `git status --porcelain`; a
  build that creates *tracked* files would be caught, while git-ignored build
  caches would not. This is intentional (ignored artifacts are not product
  changes) but is a limitation to note.
- The manager still describes rollback rather than performing it by design;
  automated destructive rollback is out of contract and was deliberately not
  added.
