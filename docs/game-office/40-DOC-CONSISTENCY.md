# 40 — Document consistency: what the docs get wrong, and the test that keeps them honest

**Status: ACCURATE** (this file). It records confirmed drift in the
`docs/game-office/` set, the real identity of one contested upstream, and the
five-check rule enforced by `tests/test_office_doc_consistency.py`.

This file owns one job: make sure a document that has drifted from the code
**fails a test** instead of quietly misleading the next agent. It does not fix
the other documents — those are fixed deliberately, by the agent that owns them,
against the specific failure this test prints.

---

## 1. The real identity of the `agent-monitor` upstream

Two documents disagreed. `31-PORT-VERIFICATION.md` named
`ruiqili2/agent-monitor`; `26-OCCLUSION-AND-DEPTH.md` named
a different owner (`kmoulder/...`). One of them is wrong, and the disagreement is not a
matter of taste — it is a licensing fact, and MIT attribution has to name the
real copyright holder.

**Determined from the checkout itself**, not from any document:

| evidence | value |
|---|---|
| `git remote get-url origin` in `/home/trick/Projects/Repos/refs/agent-monitor` | `https://github.com/ruiqili2/agent-monitor.git` |
| `LICENSE` line 3 | `Copyright (c) 2026 Ruiqi Li` |
| `git rev-parse HEAD` | `bec78ad8cb64054a81df7bf2db56e3012f577c79` |

**The real slug is `ruiqili2/agent-monitor`.** The remote owner (`ruiqili2`) and
the licence copyright holder (`Ruiqi Li`) agree. `26-OCCLUSION-AND-DEPTH.md`'s
a different owner (`kmoulder/...`) is **wrong** and must be corrected.

`tests/test_office_doc_consistency.py::test_check1_real_agent_monitor_slug_is_from_the_licence_not_guesswork`
reads the remote and the licence line and fails while either document keeps the
wrong owner.

---

## 2. Confirmed drift, each one red in the test today

Every row below is a real failure the test prints right now. "Doc says" is the
literal text in the document; "Code says" is read from the source file.

| # | document | doc says | code says | source of truth |
|---|---|---|---|---|
| 1 | `26-OCCLUSION-AND-DEPTH.md` §"provenance" | upstream is a different owner (`kmoulder/...`) | `ruiqili2/agent-monitor` | `refs/agent-monitor` remote + `LICENSE` (`Copyright (c) 2026 Ruiqi Li`) |
| 2 | `25-PORTED-CODE.md` line 3, line 36 | "adapts technique from **four** third-party projects" | the table lists **four**, but the renderer header and check 1 name a **fifth** — `ruiqili2/agent-monitor` — which the table omits | `frontend/qubit-app/plugins/qubit-office/qubit-office.js` header (`src/engine/canvas.ts` (ruiqili2/agent-monitor, MIT)) |
| 3 | `24-FLOORPLAN-SCALE.md` constant table | `ROOM_W, ROOM_H` = `6, 4` | `ROOM_W, ROOM_H = 12, 7` | `voicebridge/office/tower.py:110` |
| 4 | `28-HUD-PANELS.md` §5 / N11 (lines 57, 122, 138-144, 399) | `position.progress` is "dropped by `state()`", "absent from the payload", "no `progress` key" | `state()` carries it: `position["progress"] = walk["progress"]` | `voicebridge/office/tower.py:644-645` |
| 5 | `29-REPO-AUDIT.md` §2 (line ~63) | the renderer has "**no `sort`, no `depth`, no `zY` and no `zIndex`** … **no sort at all**" | the renderer **does** sort: `paintOrder()` delegates to `iso-engine.js`'s `depthKey()`/`sortByDepth()` | `qubit-office.js:642-655`, `iso-engine.js:260-275` |

Additionally, `30-VISUAL-DEFECT-LEDGER.md` **predates the integration** and
still marks D1 (depth sort), D2 (`seatColour`) and D4 (duplicate `iso()`) as
**OPEN** although the renderer now contains the sort (#5 above) and the
modules are wired. Its status column is not machine-checkable (it is per-defect
prose), so the test cannot pin it — it is listed under §5 as **STALE** and is
the responsibility of the renderer worker to re-date.

### The refuted collision claim

`24-FLOORPLAN-SCALE.md` asserts (§3, line 143-144 and §8 line 297) that
`(x, y+2)` and `(x+2, y)` "collide in the sort" because they share `x + y`, and
that a `(x+1, y-1)` pair is "the 2:1 iso trap". **This is refuted by algebra.**

Two cells `(x₁, y₁)` and `(x₂, y₂)` share a screen position only if both the
horizontal difference and the sum are equal:

```
x₁ − y₁ == x₂ − y₂   and   x₁ + y₁ == x₂ + y₂
⟹ 2·x₁ == 2·x₂  and  2·y₁ == 2·y₂
⟹ x₁ == x₂  and  y₁ == y₂
```

Two **distinct** integer cells can never collide under a 2:1 projection. They
share only the **depth key** (`x + y`), which is a *paint-order tie* — the two
desks are drawn at different screen positions in an arbitrary relative order,
never on top of each other. A row stagger is still a reasonable way to make the
paint order deterministic, but the *reason* given is false and must be marked so.

`test_check4_refuted_collision_claim_is_marked_refuted_with_the_algebra`
verifies the integer identity exhaustively and flags any surviving
"collide / collision / overlap … `x + y`" claim that is not contextually marked
as refuted (the words *refuted*, *impossible*, *cannot collide*, *share only the
depth key*, *algebra*, or the `x1 − y1` identity nearby).

---

## 3. The five-check rule

`tests/test_office_doc_consistency.py` runs the checks below. It is a
**known-drift gate**: several fail today, on purpose. A check is red until the
document that owns the claim is corrected — never by weakening the check.

| check | what it enforces | how it reads the truth |
|---|---|---|
| **1. Provenance is real** | every repo named in a ported-code claim exists at `/home/trick/Projects/Repos/refs/<slug>`, and where the checkout is a git repo its commit matches the provenance table | the checkout's own `git remote` and `git rev-parse HEAD` — **only `starnet` is a git repo**; the other five are stripped copies |
| **2. The unlicensed repo is never a source** | every mention of `rsanandres/aphae` (no licence ⇒ all rights reserved) in `docs/game-office/*.md` and `plugins/qubit-office/*.js` carries a not-used disclaimer; it never appears in the adopted-code table | line scan for an MIT grant or a source path attached to `aphae` |
| **3. Docs do not contradict the code** | the room size (`tower.py`'s `ROOM_W, ROOM_H`), `FACE_H` (`qubit-office.js`), `position.progress` being carried (`tower.py`), the presence of a depth sort (the renderer), and the source count in `25-PORTED-CODE.md` all match what the code says | parse the document's claim, read the constant, compare |
| **4. The refuted claim stays refuted** | a "cells collide because they share `x + y`" assertion may appear only with the algebra next to it | the integer identity + a contextual refutation scan |
| **5. Every doc is dated or listed** | every `docs/game-office/*.md` carries a `**Status:` line (ACCURATE / STALE / SUPERSEDED) **or** is named in this file's §5 | head-of-file scan; §5 below is the fallback register |

### Why the checks read the checkout, not the documents

A document that asserts a licence, a commit or a constant is only as good as the
thing it describes. So check 1 does not trust the provenance table — it opens
`refs/<slug>`, reads `git remote get-url origin` and the `LICENSE`, and holds
the table to *that*. This is why the `agent-monitor` disagreement resolved: the
checkout, not either document, was the tie-breaker.

### Partial checkouts

The reference checkouts under `refs/` are populated by a partial fetch, so a
tracked file can be present in git and absent from the working tree.
`refs/starnet/LICENSE` is exactly that case: `git ls-files` is empty for it, but
`git show HEAD:LICENSE` returns `MIT License / Copyright (c) 2026 Andrew Sims`.
The test therefore reads the licence from the worktree **or** `git show`, and
only reports "no licence" when **both** are absent. This is not a workaround —
the licence genuinely exists upstream, and the MIT provenance claim for starnet
is correct.

---

## 4. Where the checks cannot see — stated plainly

- **Prose that is not a named constant.** Check 3 only pins values that exist as
  literals in the code (room size, `FACE_H`, `progress`, the sort, the source
  count). A document can still be wrong about a *description* of behaviour the
  code does not encode as a constant. Read the code.
- **Per-defect status tables.** `30-VISUAL-DEFECT-LEDGER.md`'s OPEN/FIXED column
  is prose, one line per defect; no check reads it. It is registered STALE in §5
  instead.
- **The licence of `aphae` upstream.** Check 2 asserts the *checkout* has no
  licence. It cannot prove the upstream repo has not added one since the fetch;
  re-run after any re-fetch, and re-read `refs/aphae` if the check goes green.
- **Documents outside `docs/game-office/`.** The scan is scoped to that
  directory and the office plugin. A ported-code claim in another doc tree is
  not covered.
- **Timing / freshness.** The test proves the documents agree with the code *as
  of the run*. It cannot know a document was true last week.

---

## 5. The honest register: which documents remain unverified

A document nobody has dated is a document nobody can trust. These files carry
**no status line** and have **not** been re-verified against the code in this
pass. They are listed here so check 5 has somewhere to point, and so the gap is
visible rather than assumed. Each needs a deliberate `**Status:** ACCURATE /
STALE / SUPERSEDED` line added by whoever owns it.

**Design / org documents (not code-derived — low drift risk, still undated):**
`00-IDENTITY.md`, `01-THE-MACHINE.md`, `05-AMENDMENT-LOG.md`, `10-DECISIONS.md`,
`11-ORG-CHARTER.md`, `12-DEPARTMENT-CATALOG.md`, `13-PIPELINE.md`,
`14-RND-FLOOR.md`, `15-REVIEW.md`, `17-AUDIT.md`, `19-REVIEWS.md`,
`20-ARCHITECTURE.md`, `27-WORK-LOOP.md`, `45-ECONOMY.md`, `50-INTEGRATION.md`,
`57-BOARDROOM.md`, `58-AUTONOMY.md`, `59-REVIEW.md`, `60-EVOLUTION.md`,
`65-REFERENCES.md`, `75-EDITOR.md`, `80-RISKS.md`, `90-MILESTONES.md`,
`99-HANDOFF.md`, `README.md`.

**Code-derived documents — KNOWN STALE, corrections in §2:**
`25-PORTED-CODE.md` (STALE — missing the fifth source),
`28-HUD-PANELS.md` (STALE — N11 closed by the code),
`29-REPO-AUDIT.md` (STALE — the "no sort" claim),
`30-VISUAL-DEFECT-LEDGER.md` (STALE — pre-integration; D1/D2/D4 fixed),
`24-FLOORPLAN-SCALE.md` (STALE — recommendation applied; `ROOM_W, ROOM_H`
already `12, 7`; the collision claim refuted),
`26-OCCLUSION-AND-DEPTH.md` (STALE — wrong `agent-monitor` owner).

**Code-derived documents — status NOT independently re-verified in this pass**
(each carries a status line, but the line was not re-checked against the code
here): `30-DATA-MODEL.md`, `31-PORT-VERIFICATION.md`,
`32-LABELS-AND-LEGIBILITY.md`, `34-WALK-VERIFICATION.md`,
`35-COMPLETION-GATES.md`, `36-SEAT-BINDING.md`, `37-WALL-VOLUME.md`,
`39-TASK-PROPOSAL.md`, `40-RUNTIME.md`, `55-DASHBOARD-PAGE.md`,
`70-ASSETS.md`, `95-CONTRACT.md`.

---

## 6. How to run it, and what a green run means

```
.venv/bin/python -m pytest tests/test_office_doc_consistency.py -q
```

A **green** run means: every ported-code upstream exists, is licensed, and its
commit matches; the unlicensed repo is never cited as a source; the five pinned
code facts agree with the documents; the refuted collision claim is not asserted
as truth; and every document is dated or listed in §5. It does **not** mean the
documents are correct about anything outside §4's blind spots, and it does not
mean the documents have been read.
