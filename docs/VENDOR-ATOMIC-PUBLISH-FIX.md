# Vendor publish-order fix (scripts/vendor_habblaud.py)

Follow-up to `docs/FLAKY-TEST-FINDINGS.md`. That investigation root-caused the
recorded intermittent failure to a **non-atomic vendoring publish**, and
recommended it be fixed but did not apply the change. This applies it.

## The defect

`scripts/vendor_habblaud.py::vendor()` deleted the destination tree and then
repopulated it file-by-file, writing `PROVENANCE.json` **mid-assembly**:

```python
if dest.exists():
    shutil.rmtree(dest)
dest.mkdir(parents=True)
...                       # files materialise one at a time
(dest / "PROVENANCE.json").write_text(...)   # record written before it is true
```

Any concurrent reader — notably `pytest tests/test_office_vendor_habblaud.py`,
which hashes the tree against that record — could observe a half-generated
directory. Measured, with a reader thread hashing the tree while the writer ran:

| Algorithm | reads | consistent (`OK`) | inconsistent (`BAD`) |
|---|---|---|---|
| original (rmtree then incremental write) | 90,818 | **0** | **19** |
| fixed (record published last) | 299,533 | 1 | 1 |

**Zero consistent reads** on the original is the flake: it is not a test defect,
it is a *correct* detection of an inconsistent tree.

## The fix

Publish order is the consistency barrier. `PROVENANCE.json` is what a reader
checks the tree against, so it must become visible **last**, and every write that
a reader can observe must be atomic:

1. **Unlink the record first**, write all files, write the record last.
2. **Write the record via temp file + `os.replace`.** `write_text` truncates then
   writes, so a reader could catch truncated JSON — measured
   (`BAD:unreadable: Expecting value: line 1 column 1 (char 0)`); gone after.
3. **Copy `LICENSE` via temp file + `os.replace`.** `shutil.copy2` truncates the
   destination first — measured (`BAD:missing:LICENSE`); gone after.

### Why not a directory swap

The obvious approach — build a sibling directory and `os.replace` it in — **is
not available here**: `frontend/qubit-app/plugins/qubit-office/habblaud/` is a
real, git-tracked, **non-empty** directory, and `rename(2)` onto a non-empty
directory fails. The two alternatives were measured and are worse:

- two renames (old aside, new in): 5 of 38 reads saw no `dest` at all;
- emptying `dest` in place then one rename: 49,363 of 49,391 reads saw no record.

## Residual hole — NOT closed

Measured on the fixed path only: **1 of 589,617 reads** was inconsistent
(`BAD:mismatch:bundle/pixelify-sans-latin-ext-700-normal-BadXkHzZ.woff`).

Cause: files written by `copytree(..., dirs_exist_ok=True)` and the CSS/JS
rewrite loops are still written **in place and non-atomically**, so a file whose
name survives a rebuild can be caught mid-overwrite while the (new) record is
already published. Fully closing this needs every write to be temp-file+rename,
which for a 77-file tree would replace `copytree` wholesale.

Judgement: **not fixed here.** The original algorithm produced an inconsistent
read *almost immediately* and constantly; the fix reduces it from "every read" to
~1 in 600k, and the remaining window is a single file rather than the whole tree.
Going further is a larger refactor than this defect justifies, and the honest
state is recorded rather than smoothed over.

## Verification

Repeatable concurrency harness + measured rates:
**`docs/VENDOR-CONCURRENCY-EVIDENCE.md`** (harness:
`tests/tools/test_office_vendor_publish_concurrency.py`). It reproduces the
OLD-vs-FIXED contrast on demand and pins the residual at 1 in ~698,700 reads
(all-reads; the same denominator as this document's ~1-in-600k figure).

- `scripts/vendor_habblaud.py --check` → `ok: 77 files, 2147637 bytes, commit 4ccce2537472`, exit 0.
- The real vendored tree is **untouched**: `git status` shows no changes under
  `frontend/qubit-app/plugins/qubit-office/habblaud/`.
- `pytest tests/test_office_vendor_habblaud.py -q` → passes (17).
- `pytest tests/test_office_vendor_stream_rewrite.py -q` → **2 failed, 3 passed.
  PRE-EXISTING, NOT CAUSED BY THIS CHANGE** — proven by stashing this diff and
  re-running: the same 2 fail on the unmodified script. They are a separate
  doc/code drift (the shipped `main-*.js` was rebuilt with a different Vite
  preload-helper form than the assertion expects). Not fixed here; recorded.
