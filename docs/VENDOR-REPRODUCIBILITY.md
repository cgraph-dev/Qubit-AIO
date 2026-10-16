# Vendor reproducibility — can the vendored tree be re-produced? (A9)

**Revisited 2026-10-16.** The earlier verdict (below, preserved) was
**WRONG**: it concluded "not reproducible" by building the *shared checkout's
HEAD*, which is a different commit from the one `PROVENANCE.json` records. Built
against the **recorded** commit, the re-vendor is **bit-exact**.

## Answer: YES — bit-exact, from the recorded commit

```
$ .venv/bin/python scripts/verify_habblaud_revendor.py
pinned commit : 4ccce2537472fe5a8d432f06974494d2e778ff15
record        : 77 files, 2147631 bytes
toolchain     : node v22.22.3, npm 10.9.8
{
  "ok": true,
  "files_rebuilt": 77,
  "files_recorded": 77,
  "missing_from_rebuild": [],
  "extra_in_rebuild": [],
  "differing_sha256": [],
  "committed_tree_stale_vs_record": []
}
REPRODUCIBLE: 77 files byte-identical to the vendored tree, rebuilt from 4ccce2537472.
```

Exit 0. Evidence: `docs/evidence/a9-revendor-reproduction.json`.

## The exact reproduction

```sh
git clone --filter=blob:none --no-checkout https://github.com/marmottajr/habblaud.git <tmp>
cd <tmp> && git checkout 4ccce2537472fe5a8d432f06974494d2e778ff15
npm ci --include=dev
node node_modules/vite/bin/vite.js build --config vite.config.ts
# then: scripts/vendor_habblaud.py's transforms, applied to dist/client/ + client/public/
```

`scripts/verify_habblaud_revendor.py` runs exactly this and diffs the result
against the committed tree at the sha256 level, both directions.

## Why the earlier verdict was wrong

| Claim (earlier doc) | Measured (2026-10-16) |
|---|---|
| "the vendored commit is not in the checkout" | **The pin IS present as a commit object** in the shared clone (`git cat-file -t 4ccce25…` → `commit`); its *blobs* are absent under the `blob:none` filter, which a plain `git fetch`/`clone` at the pin resolves. |
| "checkout HEAD `86c0379` ≠ `4ccce25`, so a build from disk produces a different artifact → not reproducible" | True as a statement about *that checkout*, but it is the **wrong input**. A9 asks whether the **recorded commit** reproduces the tree. Built at `4ccce25`, the chunk names are `app-U4Pwh2XA.js` / `main-DND_b4_E.js` / `app-BW61bUik.css` — **the vendored names** — not `app-DrspyBzY.js`. |

`86c0379` is a *later* `main` commit (not an ancestor of `4ccce25`; the two
differ across 50 files under `client/`). Building it is expected to produce a
different bundle and says nothing about A9.

## The build is deterministic (measured, not assumed)

Two consecutive builds of the same source tree, compared recursively:

```
$ diff -rq <run1> dist/client   →   (no output)  IDENTICAL
```

`app-DrspyBzY.js` hashed to the same sha256 in both runs. Vite/rolldown's output
is content-hashed and reproducible here, so build non-determinism is **not** a
contributing cause. The only variable that ever mattered was *which commit*.

## What A9 now means, and how it is falsifiable

* `scripts/vendor_habblaud.py --check` — **drift detection**: "these bytes equal
  the record." Passes on any tree, including one built from the wrong revision.
* `scripts/verify_habblaud_revendor.py` — **A9**: "a fresh build of the recorded
  commit reproduces these bytes." Stronger, and it is the one that was missing.

Both are falsifiable, and the failure mode is demonstrated rather than asserted:

* `tests/test_office_vendor_reproducibility.py` flips one byte in a copy of the
  vendored tree and requires the comparison to flag it — measured: the flipped
  `bundle/app-U4Pwh2XA.js` is reported as `differing`. Deleting a file and adding
  an undeclared file are likewise required to fail.
* The end-to-end rebuild is opt-in (`QUBIT_A9_REBUILD=1`) because it needs the
  network; it exits **2 (SKIPPED)** when it cannot be attempted, so an offline run
  can never read as a reproduction success.

## Genuine limits (what remains unverifiable here)

* `npm ci` pins package versions from the upstream `package-lock.json`, but the
  **npm and Node major versions participate in the output**. The verifier records
  both (`node v22.22.3`, `npm 10.9.8`) and reports the toolchain with any
  mismatch, rather than claiming version-independent reproducibility. A different
  Node major is *not* proven to reproduce the same bytes.
* The pinned commit is fetched from the live remote. If upstream force-pushes or
  deletes the commit, reproduction becomes impossible and the verifier exits 2 —
  it does not pretend otherwise.
