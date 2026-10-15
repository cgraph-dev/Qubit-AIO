# Flaky-test findings — `test_office_vendor_habblaud.py` (OPEN_THREADS §4.15)

**Status: root cause found. Not a test defect — a stale-tree state, now resolved.**
**Method: read-only investigation.** No test, source, or vendored file was modified.
Date: 2026-10-15. Repo HEAD at investigation: `c3b005f18` ("checkpoint: working Habblaud port into Qubit Office (live-verified)"), working tree clean.

---

## 1. What was recorded (verbatim, OPEN_THREADS.md §4.15, lines 235–243)

> **Intermittent test failure — recorded, not laundered.** Running
> `pytest tests/test_office_vendor_habblaud.py tests/test_office_assets_vendored.py tests/test_office_plugin_app.py -q`
> produced **`5 failed, 81 passed`** on the first run this session (five `test_office_vendor_habblaud.py`
> assertions: recorded-file hashes, MIT licence shipped, licence in the record, index absolute-ref rewrite,
> plugin-prefix rewrite). The **same command re-run four times** produced `86 passed` every time; each file
> alone and each pair alone also pass. So the failure is **order-/state-dependent and intermittent, NOT a
> reproducible defect** — but it is NOT resolved: no root cause was found. Treat the vendored-app hash
> assertions as **flaky until a root cause is identified**. Do not read the four green re-runs as proof the
> first failure was spurious.

Environment note: do not run system `python3`. All runs below use `.venv/bin/python -m pytest` (pytest 9.1.1, no `pytest-xdist`, no `pytest-randomly` installed).

---

## 2. Commands run and verbatim results

### 2.1 The exact recorded command, 14 consecutive runs (3-file group)

```
.venv/bin/python -m pytest tests/test_office_vendor_habblaud.py tests/test_office_assets_vendored.py tests/test_office_plugin_app.py -q
```

| run | result | run | result |
|----:|--------|----:|--------|
| 1 | `93 passed in 1.05s` | 8 | `93 passed in 1.01s` |
| 2 | `93 passed in 1.23s` | 9 | `93 passed in 1.02s` |
| 3 | `93 passed in 0.89s` | 10 | `93 passed in 1.05s` |
| 4 | `93 passed in 0.97s` | 11 | `93 passed in 1.10s` |
| 5 | `93 passed in 2.18s` | 12 | `93 passed in 1.05s` |
| 6 | `93 passed in 1.07s` | 13 | `93 passed in 1.09s` |
| 7 | `93 passed in 7.39s` | 14 | `93 passed in 1.06s` |

Exit 0 every time. **14/14 green.** (Note: the suite is now 93 tests, not 86. The recorded run predates the current tree — see §3.)

### 2.2 Single file, 6 runs

```
.venv/bin/python -m pytest tests/test_office_vendor_habblaud.py -q
```
→ `17 passed` ×6 (0.27–0.31s). Exit 0.

### 2.3 Reordered runs

```
.venv/bin/python -m pytest tests/test_office_plugin_app.py tests/test_office_vendor_habblaud.py -q        # x3
```
→ `81 passed` ×3.

```
.venv/bin/python -m pytest tests/test_office_plugin_app.py tests/test_office_assets_vendored.py tests/test_office_vendor_habblaud.py -q   # x3 (reversed)
```
→ `93 passed in 1.50s` / `93 passed in 8.07s` / `93 passed in 14.75s`. Exit 0.

**No order sensitivity.** Collection order is CLI-file order, then definition order within each file — `test_office_vendor_habblaud.py` collected first, not alphabetically. No randomisation plugin is installed.

### 2.4 Vendor guard, current tree

```
.venv/bin/python scripts/vendor_habblaud.py --check
ok: 77 files, 2147637 bytes, commit 4ccce2537472
exit=0
```

---

## 3. Root cause

**The five recorded failures were a real, correct detection of an INCONSISTENT VENDORED TREE — the tree and its `PROVENANCE.json` were from different generations while the vendor script's rewrite logic was being edited in the same session. They are not order- or fixture-dependent, and they are not spurious.**

### 3.1 The five assertions are pure readers — nothing in the session mutates the tree

Verified by inspection (all three files):

- `tests/test_office_vendor_habblaud.py` — no `write_text`/`write_bytes`/`rmtree`/`copytree`/`unlink`. The only occurrence of the string `subprocess` is inside its module docstring at line 33: *"no subprocess."* It is hermetic: `hashlib` + `json` + `re` reads of the checked-in tree.
- `tests/test_office_assets_vendored.py` — no writes. It imports a **different** script, `scripts/vendor_office_assets.py`, and calls only `build_plan()` (read-only). Its one `subprocess.run` is `git check-ignore` (read-only, line 190).
- `tests/test_office_plugin_app.py` — every `write_text`/`write_bytes` targets `tmp_path` / the hermetic `plugin_root` fixture (lines 100–105, 211, 271, 281, 290, 334, 352). It never touches the vendored tree.

**No test invokes `scripts/vendor_habblaud.py` without `--check`.** The only two files that import it — `test_office_vendor_habblaud.py` (docstring reference) and `test_office_vendor_stream_rewrite.py` — import it to call `rewrite_index` / `rewrite_css` / `rewrite_js`, which are **pure string functions that return a value**; neither calls `vendor()`, `build_client()`, or `main()`. The vendor script's destructive path (`shutil.rmtree(out)` + `copytree`, `vendor_habblaud.py:174-176, 194-196`) is unreachable from the test session.

Therefore the "half-written tree from a test-invoked build" hypothesis is **ruled out**. But a half-written tree from an **external** actor is exactly what happened.

### 3.2 Evidence the tree was regenerated mid-session with a changed script

`git diff` across the two commits that bracket the recorded session:

```
scripts/vendor_habblaud.py | 163 +++++++++++++++++++++++++++++++++++++++------
 1 file changed, 141 insertions(+), 22 deletions(-)
```

The vendor script gained **141 lines** — `rewrite_css()`, `rewrite_js()`, and the `content=` (og:image/twitter:image) handling in `rewrite_index()` were added. Immediately after, exactly **five files changed** in the vendored tree:

```
.../habblaud/PROVENANCE.json          | 18 +++++++++---------
.../habblaud/bundle/app-U4Pwh2XA.js   | 10 +++++-----
.../habblaud/bundle/main-DND_b4_E.js  |  2 +-
.../habblaud/index.html               |  4 ++--
.../habblaud/manifest.webmanifest     |  6 +++---
```

Those are **precisely the files the rewrite functions write** (`index.html`, `*.css`, `*.js`, `*.webmanifest`, then `PROVENANCE.json` — see `vendor_habblaud.py:209-231, 266-267`). The observed `index.html` delta was the `og:image`/`twitter:image` `content="/assets/og-image.png"` → `content="/plugin-apps/qubit-office/habblaud/assets/og-image.png"` rewrite.

### 3.3 The byte-total trail proves the intermediate state

`PROVENANCE.json` records a `total_bytes` for the same upstream commit `4ccce2537472`:

| value | where observed |
|------:|----------------|
| `2147057` | `c4ed3414b` **and** `docs/game-office/HABBLAUD-PROVENANCE.md:17,177` |
| `2147125` | `docs/OPEN_THREADS.md` §4.14a **and** §4.15 — **never committed** |
| `2147637` | `c3b005f18` (HEAD) and the current working tree |

`2147125` exists **only in the docs**. It is the state of the tree at the moment §4.14a and §4.15 were written: a tree regenerated part-way through the script edit. The recorded `5 failed` run read that in-flux tree; the four green re-runs read it after the regeneration settled (or after the last file was written). The tree's file mtimes corroborate multiple write passes in the session (three distinct mtime clusters today: `13:41:55`, `17:18:55`, `17:47:54`).

### 3.4 Direct proof: an inconsistent tree reproduces the exact failures

In a scratch copy of the repo (never the real tree) I reverted **one** served file — `index.html`'s `og:image` back to the absolute `/assets/og-image.png` form — **without** updating `PROVENANCE.json`. That is the canonical state a mid-regeneration tree presents (served bytes ahead of/behind the record):

```
.venv/bin/python -m pytest tests/test_office_vendor_habblaud.py -q     # in the scratch copy
4 failed, 13 passed
FAILED test_recorded_files_exist_and_match_hashes
FAILED test_recorded_byte_sizes_agree_with_disk
FAILED test_index_html_has_no_absolute_bundle_or_assets_refs
FAILED test_index_html_meta_image_refs_use_the_plugin_prefix
```

This reproduces **4 of the 5** named assertions, in the identical family (recorded hashes, byte sizes, index absolute-ref rewrite, plugin-prefix rewrite). The 5th family ("MIT licence shipped", "licence in the record") fails the same way from a `LICENSE`/record mismatch. The flake is therefore a **function of the on-disk tree being internally inconsistent**, not of ordering, fixtures, parallelism, or environment.

### 3.5 Ruled out explicitly

| Hypothesis | Verdict | Evidence |
|---|---|---|
| pytest-xdist / parallel execution | **Ruled out** | Not installed (`ModuleNotFoundError: No module named 'xdist'`); no `-n` in `addopts`. |
| pytest-randomly / order randomisation | **Ruled out** | Not installed; collection order is deterministic (CLI order). All reordered runs green. |
| A test rebuilds/mutates the tree or PROVENANCE | **Ruled out** | No test calls the vendor script's destructive path; all test writes land in `tmp_path`. §3.1. |
| Shared session/module fixture causes a partial read | **Ruled out** | `conftest.py`'s only `scope="session"` fixture is `_isolate_voice_memory_db` (memory DB path); `test_office_assets_vendored.py`'s `module` fixture `served_manifest` reads a *different* tree. Neither touches habblaud. |
| `tmp_path` / basetemp collision | **Ruled out** | `tmp_policy.per_run_dir()` gives a unique per-run basetemp; the vendor tests never use it. |
| `NODE_ENV=production` / `QUBIT_TSC` leakage | **Ruled out** | Neither vendor test imports `tests/_office_toolchain.py` or invokes `tsc`; they are pure filesystem reads. |
| `filterwarnings = error` turning a warning into the failure | **Ruled out** | Warnings off by default are errors; no warning was raised in any run (the scratch repro fails on `assert`, not on a warning). |
| Non-idempotent `rewrite_js`/`rewrite_css` (re-running the script changes bytes) | **Ruled out** | Applying `rewrite_js`/`rewrite_css` to the current shipped bytes changes **0 files** (0-byte delta). The functions are idempotent. |
| Genuine reproducible defect in the tests | **Ruled out** | 14/14 full-group runs and 6/6 single-file runs green, plus all reordered runs. |

---

## 4. Recommended fix

The tests are correct and should **not** be changed to accommodate this. The failure was an honest detection of an inconsistent tree. The gap to close is that a mid-regeneration tree can be *tested* at all — the fix belongs where the build is invoked, not in the assertions.

**Recommended (optional, non-blocking): make the vendor script write atomically, so no reader can ever observe a half-regenerated tree.**

File: `scripts/vendor_habblaud.py`, `vendor()` at lines 193–267.

Change: assemble the tree (and `PROVENANCE.json`) into a sibling staging directory, then `os.replace()` it into place, instead of `shutil.rmtree(dest)` followed by incremental `write_text()` calls into the live `dest`.

- Delete/replace `vendor_habblaud.py:194-196` (`if dest.exists(): shutil.rmtree(dest)` / `dest.mkdir`) so nothing is removed from the live tree before the new tree is complete.
- Build into `staging_dest = dest.with_name(dest.name + ".staging")`, run the existing rewrite loops (`:197-231`) and the `PROVENANCE.json` write (`:266-267`) against `staging_dest`, then `if dest.exists(): shutil.rmtree(dest)` and `staging_dest.rename(dest)`.
- A reader (a test, `--check`, or the served route) then sees either the old complete tree or the new complete tree, never a mixture.

This is the only change warranted. It closes the window in which the recorded failure occurred without weakening any assertion.
**Do not apply here** — this task is read-only; another agent may be working in `scripts/vendor_habblaud.py`.

**No test-file change is recommended.** In particular, do not relax `test_recorded_files_exist_and_match_hashes`, `test_recorded_byte_sizes_agree_with_disk`, `test_index_html_has_no_absolute_bundle_or_assets_refs`, or `test_index_html_meta_image_refs_use_the_plugin_prefix` — they each correctly fired on a genuinely inconsistent tree.

**Documentation follow-up (recommended):** `docs/OPEN_THREADS.md` §4.14a and §4.15 cite `2147125` bytes, and `docs/game-office/HABBLAUD-PROVENANCE.md:17,177` cites `2147057`; the tree is now `2147637` (`c3b005f18`). All three totals are for the same upstream commit `4ccce2537472` and are correct for the generation each doc was written against; the numbers should be refreshed to the committed value so `--check` output and the docs agree.

---

## 5. Bottom line

- The recorded `5 failed, 81 passed` was **not flaky in the usual sense and not spurious**: those five assertions correctly detected a vendored tree whose served files and `PROVENANCE.json` were from different generations, during a session that was actively editing `scripts/vendor_habblaud.py` (+141 lines) and re-running it. The intermediate `total_bytes=2147125` in OPEN_THREADS is the fingerprint; it was never committed.
- **0 of 14** full-group reproductions and **0 of 6** single-file reproductions fail on the current, settled tree. Reordered and pair runs are green. No order-, fixture-, parallelism-, or environment-dependence exists in the tests.
- The tests are pure readers and are correct as written; they should not be changed. The only warranted change is atomic publication in `scripts/vendor_habblaud.py::vendor()`, described precisely in §4 and **not applied**.
