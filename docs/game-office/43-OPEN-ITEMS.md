# 43 — Open items: the single honest register

**Status: ACCURATE** (this file, at the time of writing). It is the one place
that says what is actually OUTSTANDING in the Qubit Office effort. Every entry
carries its SOURCE (which report found it), its EVIDENCE (a command that
reproduces it RIGHT NOW), and what would CLOSE it. An entry whose evidence no
longer reproduces is marked **CLOSED**, with the passing output shown — because
a register that keeps a fixed defect is a list of ghosts, and forgetting to
re-run is exactly how that happens.

This file is a **register**, not a plan. Nothing here fixes anything.

---

## How to read this register

* **OPEN** — the evidence command reproduces the problem on the current worktree.
* **CLOSED** — the evidence command was RUN and no longer reproduces the
  problem. The passing output is shown inline. A CLOSED entry stays in this
  file (it is the record that it *was* real), it just no longer counts.
* **UNVERIFIABLE-BY-MACHINE** — the item is real but its evidence is prose, a
  judgement, or a claim about behaviour the code does not encode as a constant.
  These will rot; see the last section.

All commands are run from the repo root with the project venv
(`.venv/bin/python`), and the tree was quiet for the suite runs.

---

## A. Verify list from the task brief

### A1 — `room_layout` is referenced by no module in the write path

**OPEN.** *Source:* `41-MODULE-CONTRACT.md` §1.2 + §Part 2 N5; `33-ROOM-LAYOUT.md`.

**Evidence (run):**
```sh
$ grep -rn "room_layout" voicebridge/ frontend/ --include=*.py --include=*.js
(no output — every match is a test file or a .pyc)
$ grep -rln "room_layout" --include=*.py --include=*.js .
./tests/test_office_seating_plan.py
./tests/test_office_room_layout.py
./tests/test_office_cross_module.py
./tests/test_office_seat_binding.py
```
`room_layout.py` is imported by **tests only**. No production module — not
`tower.py`, not `org.py`, not `cli.py`, not `seating_plan.py` — imports or calls
it. Its `programme()` / `check()` are therefore inert at runtime.

**Closes with:** either wire `room_layout.check` into the room-creation path
(`tower.floorplan` / `org.hire`), or delete the module and move its live rules
into the module that does run. The contract says its authority ("which rooms,
what kind, who sits in them") must have a consumer, not only a test.

**Related (same root):** `41-MODULE-CONTRACT.md` Finding A — `room_layout`
describes a *different building* than `seating_plan` (a 50-seat org: 6 rooms /
1 floor vs 8 rooms / 2 floors). Recorded as `xfail` in
`tests/test_office_cross_module.py`. It is OPEN (still xfails):

```sh
$ .venv/bin/python -m pytest tests/test_office_cross_module.py -q -rxX
XFAIL tests/test_office_cross_module.py::test_room_layout_programme_floors_do_not_exceed_the_desk_planners
      FINDING: ... at h=50 room_layout needs 8 rooms = 2 floors ... seating_plan
      fits 50 in 6 rooms on 1 floor ...
26 passed, 1 xfailed, 1 xpassed in 10.77s
```

---

### A2 — Nothing generates proposals; the org cannot notice work for itself

**OPEN.** *Source:* `39-TASK-PROPOSAL.md` (whole doc); "What is deliberately NOT
built" table, entry *CLI wiring*.

**Evidence (run):**
```sh
$ grep -rn "TP\.propose\|TP\.approve\|TP\.proposals" voicebridge/ --include=*.py
voicebridge/cli.py:7173:  out = TP.propose(cfg, proposer_id=args.proposer_id,
voicebridge/cli.py:7179:  out = TP.proposals(cfg, status=args.status,
voicebridge/cli.py:7186:  out = TP.approve(cfg, args.proposal_id,

$ grep -rn "proposal" voicebridge/office/runner.py voicebridge/office/dispatch.py \
      voicebridge/office/work.py voicebridge/office/survival.py
(no output)
```
Every call site of the proposal layer is a `qubit office …` CLI verb — a human
or script invoking it. **No autonomous loop** (`runner`, `dispatch`, `work`,
`survival`, the turn) calls `propose()`. The org can execute work but cannot
*notice* work for itself; the ratchet's autonomous half is unbuilt.

**Closes with:** a turn-time or tick-time path where a seat (a manager, a
director) files a proposal from its own context, plus a test that a real turn
produces a `proposed` row with no owner invocation. `39-TASK-PROPOSAL.md`
explicitly defers this ("CLI wiring — deliberate"), so this is by-design open,
not a bug.

---

### A3 — `room_layout` not applied → work rooms have no manager's desk

**OPEN.** *Source:* `33-ROOM-LAYOUT.md:166,188`; `36-SEAT-BINDING.md` row B5.

**Evidence (run):**
```sh
$ sed -n '166,173p' docs/game-office/33-ROOM-LAYOUT.md
2. **The two `work` rooms hold leaves only — no manager's desk.** The live
   `desk-a`/`desk-b` seat 7 leaves between them, and neither records a desk for
   the manager who runs it. ... The programme puts **one manager desk in each
   work room** ...

$ grep -rn "manager_desk\|manager's desk" voicebridge/office/tower.py
voicebridge/office/tower.py:707:  """Where an `escalation` event sends this character: its manager's desk."""
```
`tower.floorplan()` assigns seats to slots by `seats_by_room[room_id]` order
(`tower.py:122-186`) — desk 0 is simply "the first seat", with no reserved
manager desk and no `room_layout.check`. Doc `36-SEAT-BINDING.md` B5 records
the enforcement as **"nothing"**:
```sh
$ sed -n '60p' docs/game-office/36-SEAT-BINDING.md
| B5 | manager's desk is in the room it runs | **nothing** | TEST (partial) | ...
```
The rule exists in `room_layout.check` (`room_layout.py:428,444` refuses a work
room without exactly one supervisor) but nothing calls `check` on a live plan —
this is A1's concrete consequence.

**Closes with:** the same wiring as A1 — the plan is built (or validated) by
`room_layout`, so a work room without its manager's desk is refused, and a test
asserts a manager and its leaves share a room with the manager on a distinct
cell.

---

### A4 — The `24-FLOORPLAN-SCALE.md` collision claim: refuted by algebra — is it asserted anywhere as truth?

**OPEN (in a form the check misses).** *Source:* `40-DOC-CONSISTENCY.md` §"The
refuted collision claim"; enforced by `tests/test_office_doc_consistency.py`
check 4.

**Evidence (run):**
```sh
$ .venv/bin/python -m pytest tests/test_office_doc_consistency.py -q
..................     [100%]
18 passed in 0.33s
```
The check is **green**, and the algebra is sound (two distinct integer cells
cannot collide under a 2:1 projection: `x1−y1 == x2−y2` and `x1+y1 == x2+y2`
⟹ `x1 == x2` and `y1 == y2`; distinct cells share only the *depth key*, a
paint-order tie). **But the false claim survives as prose the check does not
see**, in two spellings:

1. **Split across lines — the check is line-local.** Lines 141-144:
   ```sh
   $ sed -n '141,144p' docs/game-office/24-FLOORPLAN-SCALE.md
   2. **The desk glyph is drawn after the figure that should occlude the row
      behind it**, and the
      depth sort is by `x + y`, which is *ambiguous for a `(x+1, y-1)` pair* —
      both sum to the same
      value. In a room-local grid with a uniform 2-tile column pitch,
      `(x, y+2)` and `(x+2, y)` also
      collide in the sort. **Staggering the rows by an odd offset removes every
      tie.**
   ```
   The trigger word and the depth-sum term are on **different physical lines**,
   so check 4's per-line regex never matches the claim and never flags it. The
   test is green only because of this wrapper-induced blind spot. (This very
   paragraph must avoid putting those two tokens on one line, or it would trip
   the same check — which is itself the evidence that the check is line-local.)

2. **Different wording, false content.** Line 297:
   ```sh
   $ sed -n '297p' docs/game-office/24-FLOORPLAN-SCALE.md
   | 2 | depth sort tie on diagonals | `(x, y+2)` and `(x+2, y)` both have
     `x+y` equal; a `(x+1, y-1)` pair differs by exactly `TILE_W = 34` with
     `Δy = 0` (the 2:1 iso trap) | **stagger alternating rows by 1 tile** → no
     two desks share a sort key |
   ```
   This asserts an "iso trap" and a "depth sort tie on diagonals" as the
   *reason* the fix is needed. It avoids the words *collide*/"overlap", so
   check 4 (which requires those) does not see it. (Line 236 repeats it in the
   recommendation table.)

Neither form is marked REFUTED, and neither is caught. So the answer to the
brief's question is **yes** — the refuted collision claim still appears as an
*assertion*, in two forms the check cannot reach.

**Closes with:** (a) correcting `24-FLOORPLAN-SCALE.md:141-144, 236, 297` to
say the shared `x + y` is a *paint-order tie* on the depth key, not a
collision/overlap/trap; and (b) hardening check 4 so it scans a *window* of
lines (not one line) and matches the sibling spellings (*iso trap*, *depth sort
tie*, *same sort key*) — otherwise the next split-across-lines claim passes
green.

---

### A5 — `30-VISUAL-DEFECT-LEDGER.md` statuses are prose no test pins

**OPEN (confirmed), and the statuses have demonstrably rotted.** *Source:*
`40-DOC-CONSISTENCY.md` §4, §5.

**Evidence (run):**
```sh
$ grep -rn "30-VISUAL\|VISUAL-DEFECT-LEDGER" tests/
(no output — nothing in the test suite reads the ledger)
```
The ledger's own stated ground truth (`30-VISUAL-DEFECT-LEDGER.md:50-56`) was
`2 failed, 74 passed`; re-running it now:
```sh
$ .venv/bin/python -m pytest tests/test_office_tower_visual.py \
      tests/test_office_tower_plugin.py tests/test_office_bundle.py -q
76 passed in 11.57s
```
The two failures it cites (the D2 `seatColour` collision) are gone:
```sh
$ .venv/bin/python -m pytest \
    "tests/test_office_tower_visual.py::test_two_different_slugs_get_two_different_colours" -q
1 passed in 0.18s
```
And D2 is still marked **OPEN** (`30-VISUAL-DEFECT-LEDGER.md:126`). D1 is also
marked **OPEN** with "the renderer calls neither" (`:93`), but the renderer now
defines and calls a depth sort:
```sh
$ grep -n "paintOrder\|\.sort(" frontend/qubit-app/plugins/qubit-office/qubit-office.js
653:  function paintOrder(items) {
666:    return items.slice().sort(function (a, b) {
829:    paintOrder(items).forEach(function (it) { it.draw(); });
```
So at least D1 and D2 are stale-OPEN, and the ledger cannot fail a build to say
so.

**Closes with:** either a test that reads the ledger's OPEN/FIXED column and
fails when a FIXED's evidence command no longer passes (turning prose into a
gate), or deleting the status column and pointing at the tests that own each
defect. Per `40-DOC-CONSISTENCY.md`, the ledger is *deliberately*
non-machine-checkable — so this stays open until that decision is revisited.

---

### A6 — Docs that contradict the code: three cross-checked

All three were picked as the most likely to have drifted. All three HAVE drifted.

**(a) `37-WALL-VOLUME.md` — `FACE_H` says 20, the code says 36.** *Source:*
`37-WALL-VOLUME.md` §6-§7.

```sh
$ grep -n "FACE_H" docs/game-office/37-WALL-VOLUME.md   # (doc)
 7:> ... the current `FACE_H = 20` is **not wrong — it is stale.** ...
26:| `FACE_H` | **20** | `qubit-office.js:137` |
206:-  var FACE_H = 20, FACE_W = 4;      /* the proposed edit, "-" line */
$ grep -n "var FACE_H" frontend/qubit-app/plugins/qubit-office/qubit-office.js  # (code)
137:  var FACE_H = 36, FACE_W = 4;
$ grep -n "var FACE_H" frontend/qubit-app/plugins/qubit-office/qubit-office.bundle.js
2715:  var FACE_H = 36, FACE_W = 4;
```
The document says repeatedly that it "**does not apply**" the change (§7: "for
the renderer worker — **NOT applied here**"). The change **has been applied**:
`FACE_H = 36` in both the served renderer and the bundle. The doc's measurement
and recommendation were correct; its *status* line ("does not apply") is now
false. **Doc says `FACE_H = 20`; code says `36`.**

**(b) `39-TASK-PROPOSAL.md` line 3 — "Not wired to the CLI", but it is.**
*Source:* the doc's own line 3 vs its §CLI (line 228).

```sh
$ sed -n '3,4p' docs/game-office/39-TASK-PROPOSAL.md
**Status:** specified and implemented. **Not wired to the CLI** — that is a separate,
deliberate step.
$ sed -n '228,230p' docs/game-office/39-TASK-PROPOSAL.md
## CLI (wired)
The layer is reachable from `qubit office`:
$ .venv/bin/python -m voicebridge.cli office propose --help
usage: qubit office propose [-h] [--config CONFIG] ... --by PROPOSER_ID --title TITLE
                            [--brief BRIEF] [--exit-check EXIT_CHECK] [--rationale RATIONALE] [--confirm]
```
Line 3 and line 228 of the **same document** contradict each other, and the
code sides with line 228: the verb is registered (`cli.py:1799`), dispatched
(`cli.py:7172`), and runs. **Doc (line 3) says "not wired"; code says wired.**
This is a stale header on a document that was later finished.

**(c) `24-FLOORPLAN-SCALE.md` — "current constants" block still prints `6, 4`.**
*Source:* `24-FLOORPLAN-SCALE.md:363` vs `tower.py:110`.

```sh
$ sed -n '363p' docs/game-office/24-FLOORPLAN-SCALE.md
ROOM_W, ROOM_H = 6, 4            # tower.py:99
$ grep -n "^ROOM_W, ROOM_H" voicebridge/office/tower.py
110:ROOM_W, ROOM_H = 12, 7
```
The doc's *constant table* (line 16) was corrected to `12, 7`, but the
"current values" listing in §10 (lines 360-366) and the recommendation table
(line 233) still print `6, 4` as the **current** value. **Doc says `6, 4`;
code says `12, 7`.** (Line 338's `6, 4 → 12, 7` is a *description of a change*
and is correct; lines 233 and 363 are the stale ones.)

**These close with:** the owning agent editing the three documents against the
values shown. The doc-consistency test pins the room size (check 3) and the
`FACE_H` table row (check 3), but it does **not** pin the `24-FLOORPLAN` §10
prose block, `37-WALL-VOLUME`'s "not applied" status, or `39`'s header — which
is why all three drifted green.

---

### A7 — `README.md`'s index table omits newer documents

**OPEN.** *Source:* `README.md` "Read in this order" table.

**Evidence (run):**
```sh
$ python3 - <<'EOF'   # for each *.md in docs/game-office, is it named in README?
... 46 docs total, in index: 30, missing: 16
EOF
MISSING FROM INDEX (16):
  24-FLOORPLAN-SCALE.md
  27-WORK-LOOP.md
  28-HUD-PANELS.md
  29-REPO-AUDIT.md
  30-VISUAL-DEFECT-LEDGER.md
  31-PORT-VERIFICATION.md
  32-LABELS-AND-LEGIBILITY.md
  33-ROOM-LAYOUT.md
  34-WALK-VERIFICATION.md
  35-COMPLETION-GATES.md
  36-SEAT-BINDING.md
  37-WALL-VOLUME.md
  39-TASK-PROPOSAL.md
  40-DOC-CONSISTENCY.md
  41-MODULE-CONTRACT.md
  README.md          (the index does not list itself — not an omission)
```
**15 real documents are absent** from the index (the 16th is `README.md`
itself). Every document numbered 24 and 27–41 which was added in the recent
waves is missing, so a reader who trusts the index will never learn that the
proposal layer (`39`), the module contract (`41`), or the defect ledger (`30`)
exist. `README.md` also still carries metrics from an earlier state — "16
modules / 5,288 lines, 65 subcommands, **221 tests passing**" — while the office
suite is now **860 passed** and `voicebridge/office/` is **26 modules**:
```sh
$ ls voicebridge/office/*.py | wc -l
26
$ .venv/bin/python -m pytest tests/test_office*.py -q
860 passed, 9 deselected, 1 xfailed, 1 xpassed in 389.10s
```

**Closes with:** adding the omitted documents to the index table, and refreshing
the status block (module count, test count) — or removing the counts so they
cannot rot.

---

### A8 — Concepts with NO single authority (`41-MODULE-CONTRACT.md` Part 2)

Each below was verified to still have **no single authority** (i.e. the copies
still exist and nothing links them).

**OPEN — N1: room geometry constants live in four modules.**
```sh
$ grep -n "^ROOM_W\|^PITCH_COL\|^ODD_ROW_OFFSET" voicebridge/office/*.py
floorplan_fit.py:84:ROOM_W, ROOM_H = 12, 7
floorplan_fit.py:86:PITCH_COL, PITCH_ROW = 4, 3
floorplan_fit.py:88:ODD_ROW_OFFSET = 1
seating_plan.py:76:ROOM_W, ROOM_H = 12, 7
seating_plan.py:77:PITCH_COL, PITCH_ROW = 4, 3
seating_plan.py:80:ODD_ROW_OFFSET = 1
tower.py:110:ROOM_W, ROOM_H = 12, 7
tower.py:117:PITCH_COL, PITCH_ROW = 4, 3
tower.py:118:ODD_ROW_OFFSET = 1
```
Three typed copies that agree only by hand. *Closes with:* one module
(`geometry.py`, or `seating_plan`) owning the numbers and the others importing
them.

**OPEN — N2: the span limit is stated twice.**
```sh
$ grep -rn "MAX_SPAN *=" voicebridge/office/*.py
room_layout.py:125:MAX_SPAN = 7
store.py:40:MAX_SPAN = 7          # D34: a manager carries at most 7 direct reports
```
`room_layout.MAX_ROOM_SEATS` is derived from its own copy, not from
`store.MAX_SPAN`. *Closes with:* `room_layout` importing `store.MAX_SPAN`.

**OPEN — N3: the cost-kind tuple is a bare literal in seven modules.**
```sh
$ grep -rc "inference','fees','capex\|\"inference\",\"fees\",\"capex\"" voicebridge/office/*.py | grep -v ":0"
audit.py:2  balance_sheet.py:4  envelope.py:1  ledger.py:1
receipts.py:2  store.py:31  tower.py:1
```
43 inline occurrences across 7 modules; only `balance_sheet` derives it from
`ledger.MAGNITUDE_KINDS`. The authority exists and is not read. *Closes with:*
the copies reading `ledger.MAGNITUDE_KINDS`.

**OPEN — N4: `motion.js`'s `STATION_ROOMS`/`STATION_ANIMATION` have no Python
counterpart.**
```sh
$ grep -n "STATION_ROOMS\|STATION_ANIMATION" frontend/qubit-app/plugins/qubit-office/motion.js | head -3
506:  var STATION_ANIMATION = {
517:  var STATION_ROOMS = {
597:    STATION_ANIMATION: STATION_ANIMATION,
$ grep -rn "STATION_ROOMS\|STATION_ANIMATION" voicebridge/
(no output)
```
Declared twice in JS, zero times in Python. `41-MODULE-CONTRACT.md` §N4 notes
the cross-module test can only assert it is "not pure fiction". *Closes with:*
the backend declaring the slug vocabulary, or the tables moving server-side.

**OPEN — N5: the "work room" shape is asserted in two modules from different
data.** `room_layout` decides it from the programme dict; `tower.floorplan`
builds rooms from `office_room.kind` and validates with nothing. This is A1/A3
seen from the contract's side. *Closes with:* A1's wiring.

---

## B. Items CLOSED because their evidence no longer reproduces

These were reported as open by earlier documents. Each evidence command was RUN;
the problem **no longer reproduces**, so the item is CLOSED. The stale report is
named so it can itself be corrected.

### B1 — CLOSED: the "no depth sort" claim (D1's premise / check 3)

*Reported by:* `29-REPO-AUDIT.md` §2, `30-VISUAL-DEFECT-LEDGER.md` D1.

```sh
$ grep -n "paintOrder\|\.sort(" frontend/qubit-app/plugins/qubit-office/qubit-office.js
653:  function paintOrder(items) {
666:    return items.slice().sort(function (a, b) {
829:    paintOrder(items).forEach(function (it) { it.draw(); });
$ .venv/bin/python -m pytest tests/test_office_doc_consistency.py -q
18 passed in 0.33s
```
The renderer now defines and **calls** a depth sort; check 3 (which forbids a
"no sort" claim in `29-REPO-AUDIT.md`) passes. **CLOSED.** The ledger D1 entry
still saying "OPEN … the renderer calls neither" is stale prose (A5).

### B2 — CLOSED: the D2 `seatColour` collision

*Reported by:* `30-VISUAL-DEFECT-LEDGER.md` D2 (both cited tests failing).

```sh
$ .venv/bin/python -m pytest \
    "tests/test_office_tower_visual.py::test_two_different_slugs_get_two_different_colours" -q
1 passed in 0.18s
$ .venv/bin/python -m pytest tests/test_office_tower_visual.py \
      tests/test_office_tower_plugin.py tests/test_office_bundle.py -q
76 passed in 11.57s
```
The ledger's stated ground truth ("2 failed, 74 passed") is now **76 passed**.
**CLOSED.** D2's "Current status: OPEN" (`:126`) is stale.

### B3 — CLOSED: the renderer calls none of the five modules

*Reported by:* `30-VISUAL-DEFECT-LEDGER.md` intro box; the premise of D4, D6,
D8, D9.

```sh
$ grep -n "QubitIso\|QubitCamera\|QubitSprites\|QubitControlBar\|QubitOfficeMotion" \
      frontend/qubit-app/plugins/qubit-office/qubit-office.js
111:  var ISO  = window.QubitIso || null;
112:  var SPRITES = window.QubitSprites || null;
113:  var CAM  = window.QubitCamera || null;
114:  var BAR  = window.QubitControlBar || null;
115:  var MOT  = window.QubitOfficeMotion || null;
```
The renderer now resolves all five module globals, and `drawMinimap` uses
`CAM.worldExtent`/`CAM.minimapTransform` when present
(`qubit-office.js:861`). The blanket claim "the live renderer calls **none** of
them" **no longer reproduces**; it is CLOSED as written. (Some local duplicates
— `iso()` at `:339`, `extent()` at `:383`, `drawAgent()` at `:548`,
`drawMinimap()` at `:841` — still exist, and whether each is still *used* rather
than shadowed is a per-defect question; it is not the doc's "calls none" claim.
The ledger's D4/D6/D8/D9 entries need to be re-derived against the wired
renderer, which is A5.)

### B4 — CLOSED: `position.progress` N11

*Reported by:* `28-HUD-PANELS.md` N11; `40-DOC-CONSISTENCY.md` row 4.

```sh
$ .venv/bin/python -m pytest tests/test_office_doc_consistency.py -q
18 passed in 0.33s   # includes check3_position_progress_is_carried_by_state
```
`state()` carries `position["progress"]` when walking, and `28-HUD-PANELS.md` no
longer asserts it is dropped. **CLOSED.**

### B5 — CLOSED: the `agent-monitor` wrong-owner citation

*Reported by:* `40-DOC-CONSISTENCY.md` §1 (`kmoulder/...` vs
`ruiqili2/agent-monitor`).

```sh
$ grep -rn "kmoulder" docs/game-office/*.md
(no output)
$ .venv/bin/python -m pytest tests/test_office_doc_consistency.py -q
18 passed in 0.33s   # includes check1_real_agent_monitor_slug_is_from_the_licence
```
No document names `kmoulder/...` any more; the real slug is read from the
checkout's own remote and `LICENSE`. **CLOSED.**

### B6 — CLOSED: `25-PORTED-CODE.md` omitted the fifth source

*Reported by:* `40-DOC-CONSISTENCY.md` row 2; check 3's source count.

```sh
$ grep -n "ruiqili2/agent-monitor" docs/game-office/25-PORTED-CODE.md
18:| `ruiqili2/agent-monitor` | `main` | `bec78ad8cb6405` | MIT | `src/engine/canvas.ts` | ...
$ .venv/bin/python -m pytest tests/test_office_doc_consistency.py -q
18 passed in 0.33s   # includes check3_ported_source_count_matches_the_table_and_the_header
```
`agent-monitor` is now a row in the provenance table and the counts agree.
**CLOSED.**

### B7 — CLOSED: `floorplan_fit.assert_matches_tower()` reported a fixed defect as live (Finding B)

*Reported by:* `41-MODULE-CONTRACT.md` Finding B (recorded as `xfail`).

```sh
$ .venv/bin/python -m pytest tests/test_office_cross_module.py -q -rxX
XPASS tests/test_office_cross_module.py::test_assert_matches_tower_reports_the_geometry_tower_carries
      FINDING: floorplan_fit.assert_matches_tower() reports the stride divergence
      as unresolved ... while tower.py's OWN constants already read PITCH_COL=4 ...
26 passed, 1 xfailed, 1 xpassed in 10.77s
```
The finding was recorded as `xfail` ("this comparison is broken"). It now
**XPASSES** — the checker was fixed and agrees with `tower.py`. The defect is
**CLOSED**; the `xfail` marker is now **stale** (an `xfail` that passes is a
registry error waiting to become a silent failure if `strict` is turned on).
**Close the registry entry** by removing the `xfail` from
`test_assert_matches_tower_reports_the_geometry_tower_carries` and letting it
assert normally.

### B8 — CLOSED (as a *task-brief* candidate): the suite is not at "843 passing"

The brief's figure was a snapshot, not a defect. Current, run cleanly:
```sh
$ .venv/bin/python -m pytest tests/test_office*.py -q -p no:cacheprovider
860 passed, 9 deselected, 1 xfailed, 1 xpassed in 389.10s (0:06:29)
```
This is recorded only so no future reader treats 843 as a target.

---

## C. HOW THIS REGISTER GOES STALE

Some entries cannot be machine-checked. They will rot, and a future reader
should distrust them until re-run. Named plainly:

1. **`30-VISUAL-DEFECT-LEDGER.md`'s OPEN/FIXED column (A5).** Nothing reads it.
   It was already stale when this register was written (D1, D2, and the
   "calls none" premise are all false). Its per-defect statuses are prose and
   will keep rotting. **Do not trust any status in `30-VISUAL-DEFECT-LEDGER.md`
   without running its own evidence command.**

2. **`37-WALL-VOLUME.md`'s "does not apply it" status (A6a).** The doc asserts
   a *process* state ("the renderer worker will apply this") that no test pins.
   It is already false (`FACE_H = 36` is live). Whether it re-drifts depends on
   someone re-reading it.

3. **`39-TASK-PROPOSAL.md` line 3's "not wired" header (A6b).** Contradicted by
   its own §CLI and by the code; no test pins the header. A stale header is the
   commonest form of doc rot here and the cheapest to reintroduce.

4. **The "what closes it" clause on every entry.** A closing condition is a
   plan, not evidence. Only the *SOURCE* and *EVIDENCE* columns are checkable;
   the closure clause is a judgement about intent. Treat it as a lead, not a
   fact.

5. **A1/A3 (room_layout not wired).** Whether a module "is in the write path"
   is checkable by grep **today**, but it becomes UNVERIFIABLE the moment any
   *indirect* call (a registry, a plugin loader, a string-based dispatch) wires
   it. Re-run the grep; do not trust the word "unwired".

6. **A2 (nothing generates proposals).** Checkable now by grepping call sites,
   but "the org cannot notice work for itself" is partly a *design* claim. If a
   future turn obtains a proposal by any path not named `TP.propose`, the grep
   entry goes stale while the design claim may still hold or fail
   independently. Re-derive from the code, not this row.

7. **A4 (the collision claim).** The *algebra* is permanent and re-asserted by
   check 4. What rots is the **location list** (lines 141-144, 236, 297) — line
   numbers move. Re-grep for the claim, do not trust the numbers.

8. **A6 (doc-vs-code cross-checks).** These are point-in-time values
   (`FACE_H = 36`, `ROOM_W, ROOM_H = 12, 7`). A future code change makes the doc
   correct again or makes a *different* line stale. The pinned constants are
   covered by `tests/test_office_doc_consistency.py`; the **unpinned prose**
   (the §10 block, the status headers) is not, and is where the next drift will
   be.

**The rule for this file:** an entry is only as good as the last time its
*EVIDENCE* command was run. Re-run it before acting. If it no longer
reproduces, move the entry to §B with the passing output — do not leave it in
the open list. Forgetting to re-verify is how a register becomes a list of
ghosts.

---

## Appendix — every evidence command run for this register

```
bash scripts/office_done.sh                     # see note below on contention
.venv/bin/python -m pytest tests/test_office*.py -q -p no:cacheprovider
.venv/bin/python -m pytest tests/test_office_doc_consistency.py -q
.venv/bin/python -m pytest tests/test_office_cross_module.py -q -rxX
.venv/bin/python -m pytest tests/test_office_tower_visual.py tests/test_office_tower_plugin.py tests/test_office_bundle.py -q
.venv/bin/python -m pytest "tests/test_office_tower_visual.py::test_two_different_slugs_get_two_different_colours" -q
.venv/bin/python -m voicebridge.cli office propose --help
grep -rn "room_layout" voicebridge/ frontend/ --include=*.py --include=*.js
grep -rln "room_layout" --include=*.py --include=*.js .
grep -rn "TP\.propose\|TP\.approve\|TP\.proposals" voicebridge/ --include=*.py
grep -rn "proposal" voicebridge/office/runner.py voicebridge/office/dispatch.py voicebridge/office/work.py voicebridge/office/survival.py
grep -rn "manager_desk\|manager's desk" voicebridge/office/tower.py
grep -rn "30-VISUAL\|VISUAL-DEFECT-LEDGER" tests/
grep -n "paintOrder\|\.sort(" frontend/qubit-app/plugins/qubit-office/qubit-office.js
grep -n "QubitIso\|QubitCamera\|QubitSprites\|QubitControlBar\|QubitOfficeMotion" frontend/qubit-app/plugins/qubit-office/qubit-office.js
grep -n "var FACE_H" frontend/qubit-app/plugins/qubit-office/qubit-office.js frontend/qubit-app/plugins/qubit-office/qubit-office.bundle.js
grep -n "^ROOM_W, ROOM_H" voicebridge/office/tower.py
grep -rn "MAX_SPAN *=" voicebridge/office/*.py
grep -rc "inference','fees','capex" voicebridge/office/*.py
grep -n "STATION_ROOMS\|STATION_ANIMATION" frontend/qubit-app/plugins/qubit-office/motion.js
grep -rn "kmoulder" docs/game-office/*.md
python3 - # README index cross-check (46 docs, 16 missing)
```

**Note on `office_done.sh` under contention.** At the time of writing, several
other agents were running the gate concurrently (7 `office_done.sh` processes,
11 concurrent `pytest` processes). Under that load the two `suite-pass-*` gates
were **`Killed`** (SIGKILL, `scripts/office_done.sh: line 68`), producing a
`9 passed, 2 failed` result whose two failures were resource kills, not
defects — and `tree-quiet` FAILed on a different run for the same reason (an
active writer). Run with the tree quiet, the suite itself is green
(`860 passed`). The gate's own note (lines 92-95) anticipates the `tree-quiet`
false FAIL; the same contention produces a false `suite-pass-*` FAIL. **Treat a
`Killed` suite gate as "wait and retry", never as a defect.**
