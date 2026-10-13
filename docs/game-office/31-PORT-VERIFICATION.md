# 31 — Port verification: making the Office port's claims provable

`scripts/verify_office_port.sh` re-derives, by execution, the five claims the
Qubit Office port rests on and prints a `PASS`/`FAIL` line plus the evidence for
each. It exits non-zero if any check fails.

This document exists because this project has been burned by *claims*:

- a subagent reported `status=completed` while having edited **nothing** — its
  output was about an unrelated fintech CSRF exercise;
- the renderer was "verified ready" **twice** while drawing an almost-empty
  canvas;
- a gradient claim ("map too small for the org") was disproved by arithmetic in
  `24-FLOORPLAN-SCALE.md`;
- a test's own comment-stripper **deleted the string literals** its write-verb
  assertions looked for, so "no write verb" passed by erasing its own evidence.

A paragraph is not evidence. A script that runs, prints its evidence, and fails
loudly is. This is that script's contract.

## How to run it

```sh
scripts/verify_office_port.sh            # run every check
scripts/verify_office_port.sh --verbose  # (accepted; reserved for more output)
```

Requirements: `node` on `PATH` (checks 2 loads the bundle the way a browser
does) and the repo's `.venv` (checks 2 and 4 shell out to it; the script falls
back to `python3`). Exit codes:

| code | meaning |
|---|---|
| `0` | every check passed — the port's claims are backed by execution |
| `1` | at least one check FAILED — do **not** edit the script to hide it |
| `2` | the harness itself could not run (no python; provenance doc missing) |

It **reads only**. It writes no file, mutates no database, and never rebuilds
the bundle — `--check` is used in read-only mode on purpose, so running the
verifier can never itself "fix" the thing it is measuring.

## What each check proves

### Check 1 — licence provenance is internally consistent

- Parses the provenance tables in `docs/game-office/25-PORTED-CODE.md`: the
  adopted sources and their licences, and the "read for design, code NOT used"
  table.
- Asserts the unlicensed repo (`rsanandres/aphae`) is present and marked
  read-only (its licence cell reads `none` → all rights reserved).
- Scans every ported module's `PORTED CODE` header for **repo citation** in the
  three canonical forms actually used in this tree — `N. owner/name (MIT)`,
  `upstream repo : owner/name`, and `` (`file`, owner/name, MIT) `` — and
  **fails if a cited repo is absent from the provenance table**. Path fragments
  (`src/engine`, `webview-ui/src`) and prose tokens (`H/2`, `Approve/deny`) are
  deliberately *not* counted as repos; a false positive would be as useless as a
  false negative.
- Asserts **no ported file lists `aphae` as a source** (an attribution entry),
  while allowing it to be named in a "deliberately not used" sentence — which is
  what the renderer header and `sprites.js` do.

Proves: every upstream repo the code draws on is named and licensed in the
provenance table, and the one unlicensed repo is recorded as read-only with no
file claiming to have ported it.

### Check 2 — every registered module is loaded by the bundle

- Runs `scripts/build_office_bundle.py --check` (the builder is not
  reimplemented) and fails if the served bundle is **stale** relative to its
  sources.
- Loads the generated bundle in a Node `vm` context that has `window` (and
  `self`/`globalThis`) but **no `module`** — the exact path the dashboard plugin
  loader takes, and the path where a module whose export hides in a CommonJS
  `else` disappears.
- Asserts every expected global is registered (`QubitIso`, `QubitSprites`,
  `QubitCamera`, `QubitControlBar`, `QubitOfficeMotion`) **and usable**, not
  merely present:
  - `QubitControlBar.buildButtons()` returns exactly the four ids
    `reset/fit/zoom/pnl`;
  - `QubitIso.gridToScreen(2,3,0,…)` returns numeric `x`/`y`;
  - `QubitOfficeMotion.DIR(0,0,1,0)` returns a direction, not `undefined`;
  - `QubitSprites.walkFrame(0.5)` returns a non-negative frame index;
  - `QubitCamera.minimapTransform(…)` returns an object.

Proves: the bundle a browser receives is current, and every module it stitches
loads **and does work**. A module that loads but is inert fails here — the
"almost-empty canvas" failure mode, caught mechanically.

### Check 3 — no file claims work it does not do

- Strips JS comments **while keeping string literals intact** (the exact hazard
  above: a stripper that eats strings lets a write-verb check pass by deleting
  the evidence). Comments are stripped so a header that *says* "no spinner" does
  not itself trip or satisfy the check.
- Asserts the code contains **exactly one** `setInterval` and **zero**
  `setTimeout` / `requestAnimationFrame` — the poll is the tower's only timer
  (C7).
- Asserts **zero** of the write verbs `POST`/`PUT`/`PATCH`/`DELETE` appear as a
  string literal in the code (C4: a read-only surface).
- Asserts the request literal `/api/office/tower` is present — the code polls
  the one endpoint it claims to.

Proves: what the code *does* matches what it *says*, with the string-literal
evidence preserved rather than erased.

### Check 4 — the backend reaches the payload the renderer needs

- Runs `python -m voicebridge office tower` and parses the JSON.
- Asserts `characters[].position.{x,y}` on **every** character;
  `floorplan.grid` as a `{cols,rows,…}` object; and `hud` non-empty.
- Asserts that **when any character has `walking: true`, `position.progress` is
  present**. `progress` is carried by `tower.py::state()` *only* while a walk is
  in progress (a stale `progress` on a stopped character would be a leftover
  pretending to be live).
- When no character is walking at that instant (common when the event stream is
  idle), the check does not shrug: it runs the **producer directly** —
  `tower.py::walk_for(...)` at a clock two seconds into a walk — and asserts it
  returns `walking: true` with `progress` in `(0,1)`. That is the exact function
  `state()` calls, so the field a silent schema drop would remove is proven to
  exist and carry a value.

Proves: the backend ships every field the renderer reads — including the
conditional `progress` — so the "silent schema drop" bug class cannot recur
unnoticed. (`progress` once *did* get computed and then dropped, so the
renderer's bob keyed on a field that never arrived. This check is the guard.)

### Check 5 — provenance header travels with the renderer

- Asserts a `PORTED CODE` header block exists in
  `frontend/qubit-app/plugins/qubit-office/qubit-office.js`.
- Asserts the header **names every upstream repo the port draws on** — each
  MIT source in the provenance table, plus any extra MIT source the other ported
  modules cite — and notes `aphae` as deliberately unused, not as a source.

Proves: MIT's notice travels with the code. A reader opening the renderer alone
can see which upstream repos inform it, without cross-referencing a document.

## What this CANNOT prove — read this before trusting a `PASS`

- **It cannot see pixels.** Every check above proves the *pipeline* is intact
  and the *claims* are internally consistent. None of them proves the drawing
  **looks right**: that characters land on their desks, that the isometric
  projection is not skewed, that colours are correct, that nothing overlaps. A
  bundle can load, be timer-free, poll the right endpoint, and still paint an
  ugly or empty scene. Visual correctness needs a rendered screenshot compared
  against a human, or the visual tests, not this script.
- **It cannot prove the licence file of `aphae` is still absent** upstream, only
  that this repo records it as read-only and ports none of it. Confirming a
  repository's licence is a network read (see `29-REPO-AUDIT.md` §1), not a
  local check.
- **It cannot prove the commit SHA in the provenance table is real.** It checks
  internal consistency (a cited repo is listed), not that the SHA resolves
  against the upstream repo.
- **It cannot prove the payload is *meaningful*.** Check 4 asserts fields are
  present and typed; it does not assert the numbers are right — that a
  character's position corresponds to an actual event. That is the backend's
  tests, not this.
- **It cannot prove absence.** Check 3 asserts zero occurrences of a small set
  of literal tokens; a write verb constructed at runtime
  (`["PO","ST"].join("")`) would evade it, as would any timer reached through an
  alias. It also looks for the verbs as `"…"`/`'…'` string literals, so a verb
  inside a **backtick template literal** would not be seen. It catches the
  ordinary case a future commit introduces, which is the case that has actually
  bitten this repo; it is not a sandbox and does not pretend to be.
- **Check 2 depends on a shared bundle.** If another writer edits a source
  module without rebuilding, `--check` correctly reports STALE and check 2
  fails — even though the *loading* is fine. That is a real failure (the served
  bundle no longer matches its sources), not a false one, but it means the PASS
  state of check 2 requires the workspace to be settled.

## Current result and the two failures it exposes

At the time of writing the script reports `3/5` and exits `1`. Both failures are
genuine, not artefacts:

1. **Check 1 — `ruiqili2/agent-monitor` is ported but not in the provenance
   table.** `iso-engine.js`, `sprites.js` and `camera.js` each name it in a
   numbered `(MIT)` attribution entry, and `29-REPO-AUDIT.md` lists it as one of
   the five MIT references — but `25-PORTED-CODE.md` lists only four adopted
   sources and never names it. The document's own first line says "four
   third-party projects". The fix is a documentation fix (add the agent-monitor
   row to `25-PORTED-CODE.md`); it is deliberately **not** done here, because
   the point of the script is to surface the gap rather than paper over it.
2. **Check 2 — the served bundle is stale.** A concurrent edit to
   `qubit-office.js` left `qubit-office.bundle.js` behind its sources;
   `build_office_bundle.py --check` reports `STALE`. Re-running
   `scripts/build_office_bundle.py` clears it.

Checks 3, 4 and 5 pass. Do not edit the script to make check 1 pass; add the
missing provenance row.

## Appendix — real output

Run `.venv`-less environments may differ in the `characters[10]` count; the
structure is what matters.

```
[FAIL] check 1: licence provenance is internally consistent
        doc: docs/game-office/25-PORTED-CODE.md
        MIT sources in provenance table (4): FulAppiOS/Agent-Quest,
            a16z-infra/ai-town, androoAGI/starnet, pixel-agents-hq/pixel-agents
        read-only / unlicensed rows: rsanandres/aphae
        read-only provenance confirmed for: rsanandres/aphae
        UPSTREAM CITED BUT NOT IN THE PROVENANCE TABLE: ruiqili2/agent-monitor
          - iso-engine.js cites ruiqili2/agent-monitor in its header
          - sprites.js cites ruiqili2/agent-monitor in its header
          - camera.js cites ruiqili2/agent-monitor in its header
        no ported file lists aphae as a source (only 'not used' prose)
...
RESULT: 3/5 checks passed — FAILED: check 1, check 2
```

The live output is the authority; this table is a snapshot. Re-run the script
rather than trusting this paste.
