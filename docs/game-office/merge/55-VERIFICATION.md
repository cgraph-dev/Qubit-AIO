# 55 — Verification (per-milestone proofs + regression gates)

**Status: OWNER-APPROVED 2026-10-13.** Companion to `90-AGENT-CONTRACT.md`. Every command here
was run in the merge session unless marked `NEW` (which depends on a file that does not yet
exist). Copy-paste them; do not paraphrase them into something else.

Run everything from the repo root `/home/trick/Projects/Repos/qubit-voicebridge`. Use
`.venv/bin/python` — **system `python3` has no pytest.**

---

## 0. The two regression anchors (measured, not asserted)

| anchor | value today | how measured |
|---|---|---|
| Office test suite | **885 passed, 3 skipped, 9 deselected** (888 collected) | `.venv/bin/python -m pytest tests/test_office_*.py -q -p no:cacheprovider` |
| Render baseline | **2 canvases, 0 console errors, `non_background_pct = 27.188`** (largest canvas 881×500) | `.venv/bin/python scripts/render_office_screenshot.py --json` |

**Both must hold or improve after every milestone.** A milestone that lowers the passing count
(for a reason that is not an intentional, documented contract move) or drops the render below
its own previous value has failed its gate.

---

## 1. G0 — the pre-flight gate (run before any agent starts)

```bash
# 1. the rollback point exists
git log --oneline -1 276aa3e14        # -> checkpoint: the Office as it stands before the /refs merge
# 2. the baseline suite is green
.venv/bin/python -m pytest tests/test_office_*.py -q -p no:cacheprovider  # -> 885 passed, 3 skipped, 9 deselected
# 3. the baseline render is up
.venv/bin/python scripts/render_office_screenshot.py --json | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['canvas_count'],len(d['console_errors']),d['pixels_largest_canvas']['non_background_pct'])"
#    -> 2 0 27.188
# 4. the served bundle is current
.venv/bin/python scripts/build_office_bundle.py --check   # -> OK: ... is up to date (..., 4113 lines)
```

If any line is off, **stop** — do not start the merge on a moved baseline.

---

## 2. Per-milestone proofs

Every milestone has **one behavioral proof**: a command whose output is the evidence. Prose is
not a proof.

### A1 — Asset vendor (M2)

```bash
# vendored tree is byte-fresh w.r.t. its manifest (NEW script)
.venv/bin/python scripts/vendor_office_assets.py --check && echo FRESH
# manifest integrity test (NEW)
.venv/bin/python -m pytest tests/test_office_assets_vendored.py -q          # -> N passed
# the served path actually serves an allowlisted asset (live)
curl -s -o /dev/null -w "%{http_code} %{size_download}\n" \
  http://127.0.0.1:8890/dashboard-plugins/qubit-office/assets/furniture/chair.png
#   -> 200 <bytes>     (a 404 here means the asset is not under the plugin dir, or the suffix is not allowlisted)
# size cap (R7): the script prints the real total; must be <= 100 MB
.venv/bin/python scripts/vendor_office_assets.py --check --print-total
```
**Fails loudly if:** the manifest hash mismatches, a file under `assets/` is absent from the
manifest, `.gitignore` excludes `assets/`, or the total exceeds 100 MB.

### A7 — audio/font serving + bundle ORDER (M9)

```bash
# the .wav line is present in the file the daemon ACTUALLY imports (verified: the local checkout)
.venv/bin/python -c "
from voicebridge import qubit_runtime as qr; qr.ensure_path()
import qubit_cli.web_routers.dashboard_ui as m
print('wav' , '.wav' in m._PLUGIN_ASSET_CONTENT_TYPES, m.__file__)"
#   -> wav True /home/trick/.../qubit_runtime/qubit_cli/web_routers/dashboard_ui.py
# a sound actually serves (after a daemon RESTART — this is a Python change, Trap 7)
curl -s -o /dev/null -w "%{http_code} %{content_type}\n" \
  http://127.0.0.1:8890/dashboard-plugins/qubit-office/assets/sfx/<one>.wav   # -> 200 audio/wav
# a NON-allowlisted suffix is still refused (do not widen the allowlist by accident).
# The office plugin dir holds only .js/.json today, so this 404s for EITHER reason
# (absent OR refused-suffix) -- the invariant "a .py is never served" holds either way,
# via the suffix filter at dashboard_ui.py:414. Assert only the status, not the cause.
curl -s -o /dev/null -w "%{http_code}\n" \
  http://127.0.0.1:8890/dashboard-plugins/qubit-office/plugin_api.py          # -> 404
# vendor manifest stays clean (the file is manifest-tracked)
.venv/bin/python -m pytest tests/test_qubit_runtime_vendor8u.py -q           # -> N passed
```
**Restart required:** a `voicebridge/*.py` / `qubit_runtime/*.py` change needs the daemon
restarted before the page shows it (`60-WIRING-TRAPS.md` Trap 7; `scripts/check_stale_daemons.py`).

### A2 — Sprite renderer (M3, the headline)

```bash
# sprite module contract (bake cache key, feet anchor, silhouette) under node
.venv/bin/python -m pytest tests/test_office_sprites.py tests/test_office_iso_engine.py -q  # -> N passed
# the bundle still builds and the new module exports its global
.venv/bin/python scripts/build_office_bundle.py --check        # -> OK ... (a REFUSED here names the module that failed to export)
# real pixels: sprites must RAISE the non-background share above 27.188
.venv/bin/python scripts/render_office_screenshot.py --json | python3 -c "import sys,json;d=json.load(sys.stdin);print(d['pixels_largest_canvas']['non_background_pct'], len(d['console_errors']))"
#   -> a value > 27.188 and 0 console errors
```
**The pixel delta is the proof.** A sprite pass that does not move 27.188 has not rendered.

### A3 — World renderer (M4, M7, M8, M10, M12)

```bash
.venv/bin/python -m pytest tests/test_office_render_pixels.py tests/test_office_room_layout_applied.py \
    tests/test_office_iso_engine.py tests/test_office_cutaway_visibility.py -q              # -> N passed
# the depth seam did not move: a sprite and a vector figure at the same (x,y,z) sort identically
.venv/bin/python -m pytest tests/test_office_wall_volume.py tests/test_office_label_clipping.py -q
# render again: higher than A2's %, still 0 console errors
.venv/bin/python scripts/render_office_screenshot.py --out /tmp/m4.png --json
# single-script-per-manifest-entry still true: the bundle is the only injected script
.venv/bin/python -m pytest tests/test_office_plugin_wiring.py tests/test_office_bundle.py -q
```
**Occlusion proof (M12):** a figure at `(0,0)` and a chair at `(1,1)` — the chair's fill covers
the figure; reversed for `(1,-1)`. `26-OCCLUSION-AND-DEPTH.md` is authoritative.

### A5 — Memory & linkage (M11 + emissions)

```bash
# room_layout now has a PRODUCTION consumer (M11: layout IS capability)
grep -rn "room_layout" voicebridge/office/tower.py        # -> a real call site, not only a test import
.venv/bin/python -m pytest tests/test_office_room_layout.py tests/test_office_room_layout_applied.py -q
# the cross-module disagreement is resolved and the xfail is gone
.venv/bin/python -m pytest tests/test_office_cross_module.py -q -rxX     # -> N passed, 0 xfailed
# the tower payload contract held (SCHEMA + keys)
.venv/bin/python -m pytest tests/test_office_tower_stream.py tests/test_office_hud_panels.py \
    tests/test_office_visual_scenes.py -q                               # -> N passed
# the 8 mapped-but-unemitted kinds are now reachable
.venv/bin/python -m pytest tests/test_office_tower_no_phantom_motion.py tests/test_office_determinism.py -q
```
**A5 gate:** `test_office_tower_stream.py::test_the_section_names_mirror_the_real_state_schema`
must stay green — it parses `tower.state()` source for the literal section keys.

### A4 — Agent engine / autowork (the loop)

```bash
# the loop is a function of events, never a background timer (C7)
#   (guard the file: a grep on a NEW file that doesn't exist yet exits 2, not 0)
[ -f voicebridge/office/autowork.py ] && grep -c "setInterval\|setTimeout\|threading.Timer\|while True" voicebridge/office/autowork.py || echo "no such timer at all (file absent -> NEW)"
#   -> 0 (no always-on loop)
# the loop consumes A5's memory, does not own a store
grep -n "agent_memory" voicebridge/office/autowork.py        # -> a call, not a store
# the engine's own suite
.venv/bin/python -m pytest tests/test_office_work_loop.py tests/test_office_done_signal.py \
    tests/test_office_agent_work_e2e.py tests/test_office_runner.py -q          # -> N passed
# no second memory authority exists anywhere
grep -c "def .*channel\|MessageBus\|websocket" voicebridge/office/*.py           # -> 0
```
**A4 gate:** the loop wakes on the cadence of real events; with no event in the window the office
stays `idle` (`ACTIVE_WINDOW_S`, C7).

### A6 — Surfaces & panels (M6, M13)

```bash
.venv/bin/python -m pytest tests/test_office_escalation.py tests/test_office_business.py -q   # -> N passed
# no second messaging authority was added
grep -c "def .*channel\|MessageBus\|websocket" voicebridge/office/*.py                        # -> 0
# the office handlers in qubit_compat still answer
curl -s -o /dev/null -w "%{http_code}\n" -H "X-Qubit-Session-Token: <token>" \
  http://127.0.0.1:8890/agent/api/office/tower                                                # -> 200
```

### A7 — control bar / font (M10) + `ORDER` registration

```bash
.venv/bin/python -m pytest tests/test_office_control_bar.py tests/test_office_bundle.py -q
# the bundle contains every module in ORDER, in order
.venv/bin/python scripts/build_office_bundle.py --check
```

---

## 3. The doc-consistency gate (Trap 9 / R6)

```bash
# existing consistency test must stay green
.venv/bin/python -m pytest tests/test_office_doc_consistency.py -q        # -> 16 passed
# NEW: every asset the renderer loads is in the vendoring manifest
.venv/bin/python -m pytest tests/test_office_merge_doc_consistency.py -q  # -> N passed
```

---

## 4. Regression gates (the whole-suite + render gate)

Run at every merge boundary **and** before declaring any milestone done:

```bash
# GATE 1 — the full office suite: must be >= the 885 baseline
.venv/bin/python -m pytest tests/test_office_*.py -q -p no:cacheprovider
#   -> 885+N passed, ... — a LOWER passed count than the baseline fails the gate
#   (3 skipped is normal: node-less/browser-less skips)

# GATE 2 — the live render did not regress
.venv/bin/python scripts/render_office_screenshot.py --json | python3 -c "
import sys,json; d=json.load(sys.stdin)
p=d['pixels_largest_canvas']['non_background_pct']; e=len(d['console_errors']); c=d['canvas_count']
print('canvases',c,'errors',e,'pct',p)
assert c==2, 'canvas count changed'
assert e==0, 'console errors appeared'
assert d['data_state']=='ready', 'page not ready'"
#   -> canvases 2 errors 0 pct <value>   (pct must be >= the previous milestone's value)

# GATE 3 — nothing stale is being served
.venv/bin/python scripts/build_office_bundle.py --check        # -> OK: ... is up to date
.venv/bin/python scripts/check_stale_daemons.py                # -> 0 stale

# GATE 4 — the exact fail-closed check for a new bundle module
.venv/bin/python -m pytest tests/test_office_bundle.py tests/test_office_plugin_wiring.py -q -rxX
#   EXPECTED to fail on the FIRST run after adding a module to ORDER, because both
#   files hardcode the 5-module list (test_office_bundle.py:43-46,151;
#   test_office_plugin_wiring.py:57,229). V updates them with a NEW test, never a
#   builder edit. If they pass while MODULES count changed, the test was loosened -- reject.
```

---

## 5. How to prove "no regression against the two anchors"

**Against 885:** GATE 1 prints `885+N passed`. Count must be `>= 885`; the 3 skips are
environment skips and 9 deselects are the collector's default. If `passed` is `< 885`, the
merge moved a contract — identify which test moved and why before continuing.

**Against 27.188%:** GATE 2 prints the live `non_background_pct`. It must be **`>= 27.188`**.
- A2/M3 must raise it materially (sprites are more pixels than rectangle stacks).
- A3/M4 must raise it again (tiles + furniture + labels).
- If any milestone **lowers** it, the render regressed — roll back to `276aa3e14` and fix.
- The number is deterministic for a fixed payload; a swing that is *not* explained by the merge
  means the payload changed shape, which is A5's seam (`90-AGENT-CONTRACT.md` §2.3).

**Cheap pre-check while iterating (A2/A3 only — JS/asset changes need no daemon restart):**

```bash
# no full suite; just the render + bundle freshness
.venv/bin/python scripts/build_office_bundle.py && \
.venv/bin/python scripts/render_office_screenshot.py --json | python3 -c "import sys,json;print(json.load(sys.stdin)['pixels_largest_canvas']['non_background_pct'])"
```
For a **Python** change (`voicebridge/**`, `qubit_runtime/**`): restart the daemon
(`python -m voicebridge run`) before trusting the page (`60-WIRING-TRAPS.md` Trap 7).

---

## 6. The final gate (independent verification)

```bash
# 1. full suite green at or above baseline
.venv/bin/python -m pytest tests/test_office_*.py -q -p no:cacheprovider     # -> >=885 passed
# 2. render ready, no errors, pixel share >= the last milestone
.venv/bin/python scripts/render_office_screenshot.py --json                 # -> data_state ready, 0 errors
# 3. bundle current, no stale daemons
.venv/bin/python scripts/build_office_bundle.py --check && .venv/bin/python scripts/check_stale_daemons.py
# 4. the contract's "never touch" invariants hold
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8890/agent/api/office/tower   # -> 401 (still gated)
diff <(python3 -c "import json;print(json.dumps([p for p in json.load(open('frontend/qubit-web/public/plugins/fleet.json')) if 'office' in json.dumps(p)],sort_keys=True))") \
     <(python3 -c "import json;print(json.dumps([p for p in json.load(open('frontend/qubit-dist/plugins/fleet.json')) if 'office' in json.dumps(p)],sort_keys=True))") \
  && echo "FLEET_IDENTICAL"
```

**Do not claim VERIFIED / COMPLETE / 100% without these four outputs in hand**
(`AGENTS.md` release-gate rule). The pixel number and the passed count are the evidence; the
prose is not.
