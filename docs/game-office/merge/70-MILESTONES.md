# 70 — Milestones

M0–M6. Each carries **one behavioral proof** — a command whose output is the evidence, in the
style `90-MILESTONES.md` already uses. No milestone is "done" on a prose claim.

Ordering rule (skill): ordering is a claim about risk. Assets precede logic because the asset
pipeline is the prerequisite for every visual proof, and every later milestone's proof renders
a sprite. Deferring assets would mean every visual test is written twice.

---

## M0 — `aphae` dropped, no code change
**Status: DONE 2026-10-13.**
**Proof:**
```
$ ls /home/trick/Projects/Repos/refs
agent-monitor agent-quest ai-town pixel-agents starnet      # aphae gone
$ test ! -e refs/aphae && echo "dropped"
dropped
```
Done in this session: `aphae` deleted from `/refs`.

---

## M1 — Checkpoint the existing working state
**Not started.**
**Blocks:** everything (Trap 8 — no commit exists, so no rollback).

Tasks:
Commit the current untracked Office tree (`docs/game-office/`, `frontend/qubit-app/plugins/
qubit-office/`, office tests, office scripts) as one checkpoint — *before* any borrowed asset
lands, so a bad merge is revertible.

**Proof:**
```
$ git log --oneline -1 -- frontend/qubit-app/plugins/qubit-office/
<sha> checkpoint: the Office as it stands before the merge
$ test -d frontend/qubit-app/plugins/qubit-office && echo checkpointed
checkpointed
```

---

## M2 — Vendor the assets (no wiring yet)
**Not started.**
**Depends:** M1.

Tasks:
1. Write `scripts/vendor_office_assets.py` (see `40-ASSET-PIPELINE.md` §2) — allowlist-driven,
   idempotent, `--check` mode, emits `assets/MANIFEST.json` (path + sha256 + size) and
   `assets/NOTICE.md`.
2. Run it. Vendored tree appears under `frontend/qubit-app/plugins/qubit-office/assets/`.
3. Write `tests/test_office_assets_vendored.py` (manifest integrity, notice completeness,
   `.gitignore` does not exclude `assets/`).

**Proof:**
```
$ .venv/bin/python scripts/vendor_office_assets.py --check && echo FRESH
FRESH
$ .venv/bin/python -m pytest tests/test_office_assets_vendored.py -q
N passed
$ curl -s -o /dev/null -w "%{http_code} %{size_download}\n" \
    http://127.0.0.1:8890/dashboard-plugins/qubit-office/assets/furniture/chair.png
200 <bytes>          # served, allowlisted
```

---

## M3 — Real agent sprites (M6) — **the headline change**
**Not started.**
**Depends:** M2.

Tasks:
1. Extend `sprites.js`: load a sprite atlas per `(set, facing, pose)` from
   `/dashboard-plugins/qubit-office/assets/sprites/…`; keep `bakeAgent`'s feet-anchor and
   `drawShadow` contract **unchanged** so the sort key does not move (M12).
2. Map `_ANIMATION` states → poses: `idle`→`rot_*`, `walking`→`walk_*_{1-4}`,
   `working`→`type_*`, `settling/done`→`sit_*`, `blocked`→`rot_*` + the existing marker.
   **Only reachable poses are wired** (§5.3 weathering rule).
3. Fall back to the existing procedural figure if an atlas 404s, and **name the missing set in
   the page** (never silently blank).

**Proof:**
```
$ .venv/bin/python scripts/render_office_screenshot.py --json | \
    python -c "import sys,json;d=json.load(sys.stdin);print(d['pixels_largest_canvas']['non_background_pct'])"
> 27.2        # measured baseline today; the sprites must raise it materially
$ .venv/bin/python -m pytest tests/test_office_sprites.py tests/test_office_iso_engine.py -q
N passed
```
The pixel-count delta is the proof: **27.2% non-background is today's measured floor** (from
this session's render probe). A sprite pass that does not move it has not rendered.

---

## M4 — Tiles, furniture, labels (M7, M8, M10)
**Not started.**
**Depends:** M3 (shares the atlas loader).

Tasks:
1. Ground tiles under each room (agent-quest `Terrain/`).
2. Room exteriors from `BuildingsCustom/`, keyed to room `kind` — **the building is the
   activity** (M7): `Library` for read, `Forge` for write, `Arena` for exec, etc.
3. Furniture PNGs into the `paintOrder()` array as depth-sorted items (M8) — a chair must occlude
   a figure behind it and be occluded by one in front, on the same tile-bottom key.
4. VT323 for in-canvas labels; keep the dashboard font everywhere else (M10).

**Proof:**
```
$ .venv/bin/python -m pytest tests/test_office_render_pixels.py tests/test_office_room_layout_applied.py -q
N passed
$ .venv/bin/python scripts/render_office_screenshot.py --out /tmp/m4.png --json
# non_background_pct higher than M3's; 0 console_errors
```
Occlusion proof: a test draws a figure at `(0,0)` and a chair at `(1,1)` and asserts the chair's
fill covers the figure — and the reverse for `(1,-1)`.

---

## M5 — Wire the "layout IS state" law (M11)
**Not started.**
**Depends:** M4 (rooms must exist visually before they can grant capabilities).

Tasks:
1. Make `room_layout.py`'s `programme()`/`check()` the **grant source**: a room's objects
   resolve to capabilities fresh every turn. This closes `43-OPEN-ITEMS.md` A1 (module imported
   by tests only) and the `xfail` in `test_office_cross_module.py` (room_layout vs seating_plan
   disagree on floors at h=50).
2. Reconcile the two planners so one building is described, not two.

**Proof:**
```
$ grep -rn "room_layout" voicebridge/ --include=*.py
voicebridge/office/tower.py:...        # a PRODUCTION consumer now exists
$ .venv/bin/python -m pytest tests/test_office_cross_module.py -q -rxX
N passed, 0 xfailed        # the xfail is gone because the disagreement is resolved
```

---

## M6 — Boss chat on the escalation surface (M13)
**Not started.**
**Depends:** nothing (independent of the visual chain — can run in parallel).

Tasks:
1. Give `escalation.py` an owner→agent direction on the **same** journal (not a new channel).
2. Surface it in the HUD panel (`28-HUD-PANELS.md`).

**Proof:**
```
$ .venv/bin/python -m pytest tests/test_office_escalation.py -q
N passed
$ grep -c "def .*channel\|MessageBus\|websocket" voicebridge/office/*.py
0        # no second messaging authority was added
```

---

## Critical path

```
M0 ─▶ M1 ─▶ M2 ─▶ M3 ─▶ M4 ─▶ M5
                    └─▶ (M6 parallel, any time after M1)
```

M0 is done. M1 is the immediate next action — and its first task is a **commit**, because
nothing in this tree is tracked and there is currently no way to undo a bad merge.
