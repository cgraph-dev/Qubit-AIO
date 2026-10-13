# 35 — Completion Gates

**What this is.** `scripts/office_done.sh` is the machine-checkable definition of
*"the Office work is done."* An agent does not get to say a thing is finished;
this script does, and it prints the evidence for each claim. This document is the
honest record of what each gate proves, **what it cannot prove**, how to run it,
and the rule for adding a new one.

---

## Why this exists

Two real failures in this project, and the gate is the answer to both:

1. **A subagent reported `status=completed` having edited NOTHING.** Its output
   was about an unrelated fintech exercise. Claimed done; produced zero change.
2. **`CHAR_FLOOR_ZOOM` was added and reported as a fix, but never bound** — the
   computed fit was always above it, so the constant never took effect. Claimed
   fixed; behaviour unchanged.

Both are the same defect: *a claim of completion that execution never checked.*
A gate fixes that only if it is run, only if it can fail, and only if a failure
is impossible to mistake for a pass. Those three requirements are why the
assertions in `tests/test_office_done_script.py` exist — they test the gate
itself, because **a broken gate is worse than no gate: it makes a false claim
with authority.**

---

## How to run it

```bash
bash scripts/office_done.sh          # human output, exit non-zero if any gate fails
bash scripts/office_done.sh --json   # one JSON object per gate, for machines
```

Exit codes are part of the contract:

| Exit | Meaning |
|------|---------|
| `0`  | Every gate passed. The success line is printed. |
| `1`  | At least one gate failed. `failing gates:` names them. **NOT DONE.** |
| `2`  | The harness itself could not run (no `.venv`). **This is NOT a pass.** |

> **Exit 2 is the most important exit code here.** A gate that passes because it
> could not check anything is the exact failure the script exists to prevent,
> one level up. A missing interpreter must never read as success.

**Practical note.** The suite gates glob `tests/test_office*.py`. If another
process is *writing* one of those files during a run, the run includes a
half-written file and fails for a reason unrelated to the code. The `tree-quiet`
gate (below) was added for exactly this: it names the race instead of reporting a
lie. Run the full gate when the tree is quiet.

---

## The eleven gates, and what each one proves

Numbered by their order in the script.

| # | Gate | Proves | Cannot prove |
|---|------|--------|--------------|
| 1 | `bundle-current` | The served bundle is byte-identical to what its sources would build right now. A source edited without a rebuild is **STALE**. | That the bundle *runs* in a browser — that is gate 8. |
| 2 | `tree-quiet` | No `tests/test_office*.py` was modified in the last 60 s, so a suite run is evidence about the code and not about another writer mid-file. | That the suite passes — it only licenses trusting the next two gates. |
| 3 | `suite-pass-1` | The whole office test suite is green once. | That it is green *repeatably* — order-dependence is gate 4. |
| 4 | `suite-pass-2` | The suite is green **a second time**. A single green run is not evidence: an order-dependent test passed alone and failed in-suite earlier in this project. | That the tests are good tests. Only that they agree with themselves. |
| 5 | `plugin-wiring` | The entry point is declared consistently in the manifest and **both** `fleet.json` copies, and the *served* copy matches disk. | That the plugin loads at runtime; gate 8 covers the served page. |
| 6 | `geometry-clear` | The desk row pitch clears the 15-unit figure with the required margin (`+10.5`). The legacy 2-tile pitch is refused with a stated shortfall. | That the *drawing* looks right — only that the geometry is not self-occluding by the documented measure. |
| 7 | `geometry-live` | `tower.py`'s **own constants** are the recommended ones (`12×7`, pitch `4,3`, offset `1`), not merely that the planner computes them. | That those are the *best* constants — only that source and planner agree. |
| 8 | `desks-distinct` | No two seated desks land on the same grid cell, so a room cannot silently over-fill. | That distinct cells *look* separated on screen (see the projection note in the tests). |
| 9 | `progress-reaches` | `state()` carries `position.progress` while walking — asserting the **drop site**, not the producer. Deleting that one line passed every producer-level test. | That the bob is *visible* — gate 10 covers "not empty"; "looks good" is unprovable. |
| 10 | `render-live` | A real headless browser loads the page, reaches `data-state=ready`, does **not** fall back to "modules not loaded", and paints >15% non-background pixels. | That the page is pleasant, legible, or correct in composition. Only that it is not blank and not fallen back. |
| 11 | `provenance` | No ported file cites the unlicensed repo `rsanandres/aphae` except as an explicit "not used / no licence" statement. | That every source IS licensed — only that the one known-unlicensed repo is not silently borrowed. |

---

## What the gate CANNOT prove (be honest about this)

The gate is **deliberately narrow.** It checks only claims that are cheap to
verify and that this project has actually been wrong about. It is *not* a proof
that the Office is good. Specifically, a green run does **not** establish any of
the following:

- **It does not prove the drawing looks good.** No gate here judges aesthetics,
  legibility, colour, or composition. `render-live` proves the canvas is not
  blank, not that it is pleasant. Rendering a scene of one giant red rectangle
  would pass every gate.
- **It does not prove the org is useful.** Nothing checks whether departments,
  seats, events, or the work loop produce value. The gates prove structural
  invariants, not that the structure earns its keep.
- **It does not prove a claim outside these 11 checks.** If you claim something
  none of these gates tests, the green summary is silent about it. "Every gate
  passed" means *these eleven things*, and nothing more. Do not read a green run
  as a general warrant.
- **It does not prove the tests it runs are meaningful.** `suite-pass-1/2` prove
  the suite agrees with itself, twice. A suite full of vacuous assertions would
  pass.
- **It does not prove licensing.** `provenance` proves one unlicensed repo is not
  cited without disclaimer. It is not a licence audit.
- **It is not a definition of "VERIFIED" or "COMPLETE".** Per `AGENTS.md`, those
  verdicts need the behavioral evidence in `docs/PLAN.md` and the archived
  release gate. This script is one input, not the verdict.

A future `CHAR_FLOOR_ZOOM`-style bug — a fix that is *present* but never *bound*
in a way these gates do not measure — would pass. That is the permanent gap, and
the reason each gate names its own subject rather than asserting a vibe.

---

## The rule for adding a new gate

A gate earns its place only if it satisfies **both** conditions:

1. **It must be able to fail.** Before merging a gate, break its subject in a
   scratch copy and confirm the gate goes red and names the failure. A gate that
   cannot fail is decoration — worse, it launders a lie. Every gate in this
   script has a corresponding failure test in
   `tests/test_office_done_script.py`; add one for a new gate.
2. **It must name its subject.** The failure output must identify *what* is
   wrong — the file, the room, the constant, the missing interpreter — not just
   exit non-zero. `STALE: qubit-office.bundle.js differs from its sources`,
   `the back row's desk plate and state band are buried`,
   `(4, 2)` — the specific thing, stated. A gate that fails silently or
   generically trains the reader to ignore it.

Two corollaries:

- **The gate checks; it does not fix.** It must never mutate the repo. A gate
  that repairs its own subject proves nothing — it would go green by editing the
  defect away. `tests/test_office_done_script.py::test_the_gate_does_not_mutate_the_repo`
  hashes `git status --short` before and after a run and asserts it is unchanged.
- **A gate that cannot check is not a pass.** If the harness cannot run, exit 2.
  Never coerce "could not check" into "checked and fine."

---

## How the gate is itself tested

`tests/test_office_done_script.py` tests the gate the way a gate must be tested:

- passes on the current, known-good tree (marked `monitor` — run it on purpose);
- **fails** when a gate's subject is broken, proven for the stale-bundle gate, a
  geometry gate, `geometry-live`, `desks-distinct`, and `provenance`;
- exit 2 is not a pass (both a mirror harness and the real script);
- `--json` names match the human gate names;
- the gate does not mutate the repo.

Two of those are tested at the **logic level** rather than by a full run — the
`tree-quiet` guard and the `--json` name set — because the full run globs
`tests/test_office*.py`, is ~4 minutes long, and gets reaped under load while
other writers are active. That limitation is recorded in the test file, not
papered over. The one full-run assertion is `-m monitor`, deliberately opt-in:

```bash
.venv/bin/python -m pytest tests/test_office_done_script.py -m monitor
```

---

## See also

- `scripts/office_done.sh` — the gate itself.
- `tests/test_office_done_script.py` — the gate's own tests.
- `docs/game-office/90-MILESTONES.md`, `docs/game-office/99-HANDOFF.md` — where a
  green gate does and does not take the project.
