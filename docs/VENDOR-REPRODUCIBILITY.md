# Vendor reproducibility — can the vendored tree be re-produced? (A9)

Investigated 2026-10-15. HEAD `d45107af0`. This settles the OPEN item **A9** from
`docs/OFFICE-ACCEPTANCE-AND-RISK.md`.

## Answer: NO, not to a bit-identical tree from the checkout on this machine

A re-vendor is **possible in principle but not reproducible here**, and the reason is
concrete rather than mysterious.

## Evidence

**The vendored tree** (`frontend/qubit-app/plugins/qubit-office/habblaud/PROVENANCE.json`)
records upstream commit **`4ccce2537472fe5a8d432f06974494d2e778ff15`**, 77 files.

**The checkout on disk** is `/home/trick/Projects/habblaud`:

```
remote: origin  https://github.com/marmottajr/habblaud.git  [blob:none]
HEAD:   86c0379b18cd1be35dd3661f98f87662633db3ef
git rev-list --count HEAD  ->  1
git cat-file -t 4ccce2537472...  ->  (nothing: not present)
```

Two independent blockers:

1. **The vendored commit is not in the checkout.** It is a **partial clone**
   (`[blob:none]`) truncated to **one commit** (`rev-list --count HEAD` = 1). The
   vendored revision is unreachable locally, so the exact input cannot be rebuilt
   without fetching it (`git fetch origin 4ccce25…`, or a full clone).
2. **The checkout's HEAD is a DIFFERENT commit** (`86c0379b…` ≠ `4ccce25…`). Even a
   successful build from what is on disk produces **a different artifact** than the one
   being served. That is not a failure of the vendoring script — it is a stale checkout.

`dist/client/index.html` **is** present and `node_modules` exists (vite and friends are
installed), so `--skip-build` with this checkout would run — it would just publish a tree
built from a different upstream revision, silently changing what the page loads.

## What this means

- `scripts/vendor_habblaud.py --check` remains the trustworthy integrity gate. It passes
  today (`ok: 77 files, 2147631 bytes, commit 4ccce2537472`, exit 0) and is what pins the
  served tree to its record.
- **The served tree is intact and consistent with its own record.** The gap is purely
  upstream: there is no local source that reproduces it.
- A future full `--src` re-vendor **must first fetch the pinned commit**, or it will
  publish a different build under the old commit's provenance — which would be a false
  record, worse than no re-vendor.

## To actually close A9

```sh
cd /home/trick/Projects/habblaud
git fetch --unshallow origin            # or: git fetch origin 4ccce25…
git checkout 4ccce2537472fe5a8d432f06974494d2e778ff15
cd /home/trick/Projects/Repos/qubit-voicebridge
.venv/bin/python scripts/vendor_habblaud.py --src /home/trick/Projects/habblaud --check
```

Then compare file count and per-file content against the committed tree. Note the byte
total legitimately differs across rebuilds of the same source only via the transforms, so
assert **file count + `--check` exit code**, not the byte total — a concurrent agent has
already been caught by exactly that (see `docs/OFFICE-ACCEPTANCE-AND-RISK.md` Part 5).

Not done here: fetching the pinned commit is a network operation that changes the checkout
another agent may be using, and it does not improve the served page. Recorded instead.

## Status

**A9: OPEN — characterized.** Not a defect in the vendoring; a stale upstream checkout.
The served tree's integrity is verified; its *reproducibility* requires a fetch that was
deliberately not performed.
