# SHELL-TEST-RECONCILIATION

**Scope:** the three failing tests in `tests/test_office_game_shell.py` (section 6,
"the renderer switch"). The office game was deliberately rewritten; those tests
still encoded the PRE-rewrite contract and failed against the new, intended one.
The code is authoritative — the tests were reconciled to it, without weakening
what they actually guarantee.

Owned files touched: `tests/test_office_game_shell.py` (only),
`docs/SHELL-TEST-RECONCILIATION.md`. No source file was edited. No commit.

## Why they failed

```
.venv/bin/python -m pytest tests/test_office_game_*.py -q
-> 3 failed, 209 passed   (before)
```

The three failures and their root cause:

1. `test_the_default_renderer_is_the_app_and_the_shell_is_opt_in`
   asserted `'has("app")' not in code`. `entry.ts:81` is now
   `const wantShell = params.has("shell") && !params.has("app");` and the entry's
   own header (lines 55–78) documents `?app=1` as a DELIBERATE backward-compat
   alias for bookmarks from the first Habblaud preview, with the vendored app as
   the unconditional default. The code is intentional; the test's contract was
   stale.
2. `test_the_stream_is_bound_and_the_writes_are_gated_not_missing`
   read `perms.gated` / `msgs.gated` off `A.ROUTES.find(...)` results. The route
   table was rewritten; the per-route object is no longer keyed the way the test
   assumed (`messages/:id` does not exist as a client id, and `gated` lives on the
   route entry under client `"messages"`). Node threw
   `TypeError: Cannot read properties of undefined (reading 'gated')`.
3. `test_the_binding_summary_counts_the_new_truth`
   asserted `s.startswith("3 of 5 routes bound")`. The real current output is
   `"8 bound · 5 unavailable · 2 confirmation-gated"` — an IMPROVEMENT (more
   routes actually bound), so the assertion was measuring the old, worse state.

## The reconciliation — per test

### Test 1 — `test_the_default_renderer_is_the_app_and_the_shell_is_opt_in`

- **Old assertion:** `assert 'has("app")' not in code` (forbids a token).
- **New assertion:** lift the REAL `const wantShell = <expr>;` out of the
  comment-stripped source, evaluate that exact expression under `node` for the
  four query-param cases, and require the behaviour:
  - no params → `wantShell === False` (the vendored app is the default)
  - `?shell=1` → `True` (the fallback shell is reachable)
  - `?app=1` → `False` (the compatibility alias resolves to the app)
  - `?shell=1&app=1` → `False` (the app alias wins; a stale bookmark cannot
    resurrect the fallback)
  The structural checks that the branch is
  `wantShell ? createOfficeComponent(...) : createOfficeApp(...)` and that both
  factories are referenced are kept unchanged.
- **Property protected:** a plain `/qubit-office` mounts the vendored app; `?shell`
  is the ONLY thing that selects the hand-built fallback. Asserted as the
  behaviour of the shipping expression, not by forbidding a token — so `?app`
  existing as an alias is fine, and the test still fails if the default is
  inverted or the shell becomes unreachable.

### Test 2 — `test_the_stream_is_bound_and_the_writes_are_gated_not_missing`

- **Old assertion:** `permsGated`/`msgsGated` read off per-client objects, plus
  `set(got["missing"]) == set()` — the flattening the rewrite removed.
- **New assertion:** use the current `bindingVerdict()` / `ROUTES` API and assert
  the PARTITION:
  - `stream` route names `/api/stream` and is in `bound`;
  - the verdict buckets are exhaustive (`len(bound ∪ gated ∪ missing) == len(ROUTES)`);
  - `set(gated) == {"permissions/:id", "messages"}` and neither appears in
    `missing` (org mutations are confirmed refusals, not unwired gaps);
  - `set(missing)` is exactly the genuinely-unavailable capabilities
    (`terminal/pty`, `spawn`, `renameRoom`, `deliveries`, `recording/replay`);
  - read-only routes (`agents/:id`, `access`, `sessions/recent`) are `bound` with
    the right Qubit paths; `live`, `canRender`, `viewOnly` are as declared.
- **Property protected:** the read-only routes are BOUND and the mutation routes
  are GATED (present as refusals), not missing — the distinction the old table
  collapsed into "0 of 5". The partition check means a route cannot silently fall
  out of every bucket.

### Test 3 — `test_the_binding_summary_counts_the_new_truth`

- **Old assertion:** `s.startswith("3 of 5 routes bound")` (hardcoded old counts).
- **New assertion:** assert the summary string contains
  `bindingVerdict()`'s OWN numbers — `v.bound.length`, `v.missing.length`,
  `v.gated.length` — names all three buckets, does not report a non-empty bucket
  as `0`, AND cross-checks `bindingVerdict`'s counts against the route list
  derived directly from `ROUTES`. No `"8"` is hardcoded.
- **Property protected:** the human-readable summary reports the true counts and
  cannot drift from the data again — it must agree with both the verdict and the
  route list, so it cannot claim more (or less) is bound than really is.

## Falsifiability evidence

Each property was deliberately broken in a SCRATCH copy only (the real tree was
never mutated; `entry.ts:81` and `office-api.ts` were confirmed intact after). A
driver copied the real modules aside, applied one mutation, pointed the test
module at the scratch copies, and ran the REAL test functions.

Mutations applied:

| # | File | Mutation | Result |
|---|------|----------|--------|
| M1 | `entry.ts` | `&& !params.has("app")` → `\|\| params.has("app")` | **RED** |
| M2 | `office-api.ts` | drop `gated: true` from `permissions/:id` | **RED** |
| M3 | `office-api.ts` | summary emits `${v.bound.length + 1} bound` | **RED** |

Red output (first line of each failure):

```
RED    M1 test_the_default_renderer_is_the_app_and_the_shell_is_opt_in
       `?app=1` (the compatibility alias) does not resolve to the vendored app.
       expr='params.has("shell") || params.has("app")'
RED    M2 test_the_stream_is_bound_and_the_writes_are_gated_not_missing
       the gated set is wrong: ['messages'] — the org mutations must be flagged
       gated by the confirmation authority (C4)
RED    M3 test_the_binding_summary_counts_the_new_truth
       the summary does not report the bound count (8):
       '9 bound · 5 unavailable · 2 confirmation-gated'
GREEN  SANITY (unmutated) test1 / test2 / test3
       (unmutated scratch copies stay green)
```

So all three tests pass BEFORE the mutation and fail AFTER it: none is
decoration. Driver: `…/runtime/cache/scratch/shellfalsify/falsify.py` (scratch,
not committed).

## Final verification (green)

```
.venv/bin/python -m pytest tests/test_office_game_*.py -q   -> 212 passed in ~88s
.venv/bin/python -m pytest tests/test_office_game_shell.py -q
    run 1: 30 passed   run 2: 30 passed   run 3: 30 passed
    run 4: 30 passed   run 5: 30 passed   run 6: 30 passed    (stable)
.venv/bin/python -m pytest tests/test_office_plugin_wiring.py -q -> 8 passed
.venv/bin/python scripts/vendor_habblaud.py --check
    -> ok: 77 files, 2147637 bytes, commit 4ccce2537472
```

No source file under `frontend/` or `voicebridge/` was modified. The only diff
outside the new doc is `tests/test_office_game_shell.py`.
