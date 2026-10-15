# Vendor publish concurrency — measured evidence

Companion to `docs/VENDOR-ATOMIC-PUBLISH-FIX.md` (the fix) and
`docs/FLAKY-TEST-FINDINGS.md` (the root cause). That fix shipped with a one-off
probe and an **unreproduced** residual claim ("~1 in 600k reads"). This document
is the evidence from a **repeatable harness** that (a) proves the fixed publish
is consistent, (b) proves the OLD algorithm is broken under the SAME readers, and
(c) pins the residual to a mechanism and a measured rate.

Harness: `tests/tools/test_office_vendor_publish_concurrency.py`
Soak runner: `python tests/tools/test_office_vendor_publish_concurrency.py --seconds 60 --readers 6 --dwell 1.0`
(Hermetic. Writes ONLY to a scratch destination; never to the served tree
`frontend/qubit-app/plugins/qubit-office/habblaud/`.)

## What the harness does

1. **Fake upstream.** Reconstructs a `--src` checkout from the CURRENT vendored
   tree (`<src>/dist/client/{bundle,index.html}`, `<src>/client/public/*`,
   `<src>/LICENSE`) — the layout `vendor(..., skip_build=True)` reads. Faithful
   because the string transforms are idempotent, so re-vendoring the vendored
   tree reproduces it byte-for-byte; asserted by
   `test_fake_upstream_round_trips`.
2. **Race.** N reader threads against a writer re-vendoring a scratch
   destination. Each read performs the exact `--check` contract: *every file
   named in `PROVENANCE.json` exists with its recorded sha256.*
   - `OK` — record present, whole tree matched it;
   - `BAD` — record present, tree did NOT match (reason recorded);
   - `SKIPPED` — no record on disk at that instant (the documented contract: a
     reader "can skip").
3. **Falsify.** The EXACT OLD algorithm (copied from `ca03150e8^`) is run against
   the SAME readers. `test_old_algorithm_measures_as_broken_tight_loop` fails if
   OLD is ever reported consistent — a green harness that cannot see the defect
   would be worthless.

Because `SKIPPED` is a correct outcome, the contract metric is the **decided
rate**: `OK / (OK + BAD)` — of the reads that found a record, how many found a
consistent tree?

## Result 1 — realistic cadence (dwell = 1.0s, 6 readers, 60s)

A real re-vendor is occasional, so the served tree sits published between vends.

| Algorithm | reads | writes | OK | BAD | SKIPPED | decided-OK | BAD reasons |
|---|---|---|---|---|---|---|---|
| **OLD** (`rmtree` + record mid-assembly) | 4,876,952 | 8 | 1453 | 42 | 4,875,457 | **97.19 %** | `missing` 32, `unreadable` 10 |
| **FIXED** (record published last) | 4,891,072 | 8 | 1474 | 7 | 4,889,591 | **99.53 %** | `mismatch` 7 |

The two algorithms differ in **kind**, not just rate:

- OLD's failures are **whole-tree**: a reader holding generation N's record finds
  recorded files *absent* (`missing` — 32×) or the record itself *unreadable*
  (`unreadable` — 10×, the non-atomic `write_text` truncate-then-write). This is
  the original flake — a *correct* detection of an inconsistent tree, not a test
  defect.
- FIXED's failures are **one file at a time** and only ever `mismatch` — the
  residual below. FIXED **never** produced `missing` or `unreadable`
  (asserted in `test_fixed_algorithm_never_tears_the_tree_down`; the published
  barrier holds).

## Result 2 — tight loop (dwell = 0, 6 readers, 45s)

The writer re-vends as fast as it can — the harshest stress, and the regime the
fix's original probe measured.

| Algorithm | reads | OK | BAD | SKIPPED | decided-OK | BAD reasons |
|---|---|---|---|---|---|---|
| **OLD** | 4,065,325 | **0** | 35 | 4,065,290 | **0.00 %** | `missing` 30, `unreadable` 5 |
| **FIXED** | 4,056,534 | 21 | 10 | 4,056,503 | 67.74 % | `mismatch` 10 |

**OLD = 0 of 35 decided reads consistent** — the same finding as the fix's probe
("0 of 90,818 reads"), reproduced on demand. Why 0: with no dwell the writer
`rmtree`s the destination immediately after each publish (`unlink` of the record
is the first step of `vendor()`), so the record is present only ~0.15 % of the
time (measured: one `vendor()` cycle is ~0.42s) and every read that *does* catch
a record catches it mid-tear-down.

## Result 3 — the residual hole, characterised

**Mechanism.** `vendor()` still writes files **in place**: `shutil.copytree(...,
dirs_exist_ok=True)` for `bundle/` and `client/public`, and `write_text()` in the
CSS/JS rewrite loops. When a file's NAME survives a rebuild (hash-named fonts are
byte-identical across idempotent re-vendors, so their names are stable), a reader
that loaded generation N's record can have the writer begin generation N+1 —
which **unlinks the record and overwrites that same file in place** — before the
reader finishes hashing it. The reader then reads a file mid-overwrite and sees a
hash that does not match the record it holds. `os.replace` on the record and the
`LICENSE` copy closed the *record* hole; they do not close the *in-place file
overwrite* hole.

**Rate (measured, 60s cadence soak, FIXED):**

- **7 BAD of 4,891,072 reads → 1 in ~698,700 reads.** This matches the fix's
  prose estimate ("~1 in 600k") — the fix's number is per **all** reads.
- Per **decided** read (record found): 7 of 1481 → **1 in ~212**.

Repeated 10-second pytest runs agree: FIXED is 98.8–99.6 % decided-consistent
across runs, and BAD is always `mismatch` on a single name-stable file
(`pixelify-sans-*.woff/woff2`, `daystats-*.js`, `ptypane-*.css`).

**Why the doc's 1-in-600k and this harness's 1-in-212-decided both hold:** the
denominator differs. Continuous re-vendoring here means ~all reads land in the
(unlinked) `SKIPPED` window, diluting the BAD rate per all-reads; the residual
shows up cleanly against *decided* reads. Both are honest; the all-reads figure
is the one comparable to the fix's prose.

**Judgement: still not closed, for the same reason the fix recorded.** Closing it
needs every write to be temp-file + `os.replace`, which for a 77-file tree means
replacing `copytree` wholesale with a per-file copy-to-temp-then-rename. The
original algorithm failed on *nearly every decided read* and destroyed the whole
tree; the fix reduces that to a single file at ~1 in 700k reads. The honest state
is measured here, not smoothed over.

## Falsifiability

`test_office_vendor_publish_concurrency.py` **fails** if:

- OLD is ever reported consistent in the tight loop (`test_old_algorithm_measures_as_broken_tight_loop`);
- OLD's failures are not whole-tree at realistic cadence
  (`test_old_algorithm_tears_the_tree_down_at_realistic_cadence`);
- FIXED produces any `missing`/`unreadable` failure, i.e. the barrier breaks
  (`test_fixed_algorithm_never_tears_the_tree_down`);
- the reader itself cannot fail — a corrupted file must be caught
  (`test_reader_contract_detects_a_tampered_tree`);
- the fake upstream no longer round-trips to the shipped tree
  (`test_fake_upstream_round_trips`).

## Commands run

```text
.venv/bin/python -m pytest tests/tools/test_office_vendor_publish_concurrency.py -q -s -p no:randomly
  → 6 passed in ~50s   (repeated 3×, all green)

.venv/bin/python tests/tools/test_office_vendor_publish_concurrency.py --seconds 60 --readers 6 --dwell 1.0
  → [old]   reads=4876952 OK=1453 BAD=42  SKIPPED=4875457 decided=1495 (97.19% ok)  reasons={'missing':32,'unreadable':10}
  → [fixed] reads=4891072 OK=1474 BAD=7   SKIPPED=4889591 decided=1481 (99.53% ok)  reasons={'mismatch':7}

.venv/bin/python tests/tools/test_office_vendor_publish_concurrency.py --seconds 45 --readers 6 --dwell 0.0
  → [old]   reads=4065325 OK=0    BAD=35  SKIPPED=4065290 decided=35   (0.00% ok)  reasons={'missing':30,'unreadable':5}
  → [fixed] reads=4056534 OK=21   BAD=10  SKIPPED=4056503 decided=31  (67.74% ok)  reasons={'mismatch':10}

git status --porcelain frontend/qubit-app/plugins/qubit-office/habblaud/
  → (empty — the served tree is untouched)
```

Host: 6 reader threads; GIL-bound (readers dominate wall clock), so per-run
decided counts vary with scheduling — the assertions use rates and failure
KIND, not raw counts, and pass across repeated runs.
