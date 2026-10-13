# 90 — CORRECTIONS (from the two review agents, verified)

**Read this before implementing.** Two review agents independently audited the merge plan and
found claims that are **wrong against the running code**. Every item below was re-verified by
the parent this session with live commands — the evidence is quoted.

These override the earlier spec files where they conflict.

---

## C1 — 🔴 THE ASSET PIPELINE AS WRITTEN 404s. This is the biggest finding.

**What the plan said:** `40-ASSET-PIPELINE.md` + `60-WIRING-TRAPS.md` Trap 1 claim PNGs serve
today from `frontend/qubit-app/plugins/qubit-office/` via `<img>`, and that the suffix
allowlist governs it.

**What is actually true** (verified live):

```
$ ls frontend/qubit-app/plugins/qubit-office/dashboard/
No such file or directory
$ printf 'x' > frontend/qubit-app/plugins/qubit-office/assets/probe.png   # simulate the merge
$ curl -o /dev/null -w "%{http_code}" .../dashboard-plugins/qubit-office/assets/probe.png
404
$ curl -o /dev/null -w "%{http_code}" .../dashboard-plugins/qubit-office/panels/x/y/z.js
404
```

**The mechanism** (`voicebridge/webui.py:1610-1632`, `_serve_qubit_plugin`):

1. **Overlay fast path** — serves ONLY `.js/.json/.css`, and only at `len(parts) <= 3`. A PNG
   is rejected by suffix; a deeply-nested path is rejected by depth.
2. **Fallback** — `resolve_plugin_asset` reads from `<plugin>/dashboard/`
   (`web_server_dashboard.py:575` sets `_dir` to the dashboard dir). **qubit-office has no
   `dashboard/` directory**, so every request from it 404s.

**So the entire asset half of the merge would have silently produced 22 MB of art the browser
can never load.** Seven agents would have vendored sprites, then watched a blank canvas and
blamed the renderer.

### The fix (verified)

The path that DOES serve PNGs is `_serve_qubit_static` (`webui.py:1586`), which serves from
`_qubit_dist()` = `frontend/qubit-dist/` and **explicitly handles `.png`, `.svg`, `.woff2`,
`.woff`, `.ico`, `.map`** (`:1596-1606`). Live proof that this path works for other plugins:

```
$ curl -o /dev/null -w "%{http_code}" .../plugins/kanban/dist/style.css
200
```

**Therefore assets land in `frontend/qubit-dist/plugins/qubit-office/` and are fetched from
`/plugins/qubit-office/...`.** Concretely:

1. **Two directories matter** — `frontend/qubit-app/plugins/qubit-office/` is what the overlay
   path serves the *entry JS* from; `frontend/qubit-dist/plugins/qubit-office/` is what serves
   *assets*. The vendoring script must get assets into the dist tree (copy directly, or confirm
   the existing build step copies them — whichever other plugins already do; verify).
2. **The manifest gains `assetBase: "/plugins/qubit-office"`** so `pluginAssetRoot()`
   (`fleet.ts:47`) resolves assets through the working route, while the entry JS keeps loading
   via the overlay path. **Corrects Trap 4**, which said "do not add an assetBase" — that was
   right for the bundle JS and **wrong for assets**.
3. **Verify both URLs after the change** — do not assume:
   `curl /dashboard-plugins/qubit-office/qubit-office.bundle.js` → 200 (JS)
   `curl /plugins/qubit-office/assets/furniture/chair.png` → 200 (asset)

**Owner-visible consequence:** if this is wrong, the page shows a blank tower with 0 console
errors (images fail silently). V's first proof must `curl` one real asset path and assert 200.

---

## C2 — The `.wav` line is not "one line in a vendored file"

`_PLUGIN_ASSET_CONTENT_TYPES` is read only by `resolve_plugin_asset` — **the path qubit-office
never reaches** (C1). So adding `.wav` there fixes nothing for this plugin.

Two real options:
1. **Serve sound from `/plugins/`** — `_serve_qubit_static` does not currently handle `.wav`
   (its chain ends at `.map`), so add `elif low.endswith(".wav"): ctype = "audio/wav"` there.
   Still a one-line change, but in `voicebridge/webui.py`, not the vendored file.
2. **Inline the 12 sounds as base64 data-URIs** in the bundle — no server change at all.

**Owner decision (O2): option 2.** No vendored file touched, no manifest refresh, no server
change. The 12 bleeps are short; the added bundle weight is small and well inside the 100 MB cap.

**A6's job is therefore:** read `/refs/starnet/frontend/assets/sfx/*.wav`, base64-encode each,
emit a `sounds.js` module exposing `window.QubitSfx.play(name)`, and add it to the bundle
`ORDER`. **No `dashboard_ui.py` edit. No `webui.py` edit.** This removes the last reason for any
agent to touch a vendored runtime file.

---

## C3 — Sprite inventory was wrong (and good news on size)

Measured, not estimated:

| claim in plan | truth |
|---|---|
| walk cycles "4 frames" | **6 frames** per direction |
| `sit` for 8 directions | **4 directions only** |
| `type` for 8 directions | **north only** (1 direction) |
| 50 sets "size to be measured" / R5 fears "gigabytes" | **22.2 MB total for all 50 sets** |

**The 100 MB cap is never approached.** R5's bloat fear is retired for sprites. The renderer's
pose map must reflect 6-frame walks and single-direction `type`/4-dir `sit`, or it will index
off the end of an atlas.

---

## C4 — Most of the 50 "crew sets" are celebrity likenesses

`elonmusk`, `harrypotter`, `heisenberg`, `xenomorph`, … The plan treated all 50 as
interchangeable agent skins. They are not: they are cosplay characters. **Select a small,
neutral subset** (the `approved_blank_*`, `approved_android`, `approved_astronaut`,
`approved_animegirl` families) for agent figures, or the office looks like a meme collage.
**Owner decision (O1): use only the neutral families.** The vendoring allowlist takes:
- `sprites/approved_blank_*` (the recolourable base crew)
- `sprites/approved_android`
- `sprites/approved_astronaut`
- `sprites/approved_animegirl`

Everything else in `sprites/` is **excluded**. A2 assigns one family per seat (or per department)
so the tower reads as a workforce, not a costume party. If the blank family is recolourable,
seat colour (`seatColour`) keys the figure — check the atlas for a tint-friendly base.

---

## C5 — A5 must not remove any literal `state()` key

`tests/test_office_tower_stream.py:515-527` parses `tower.py`'s **source text** for
`out["<key>"]` literals (`SECTIONS` = `empty … preview_legend`), and the `SCHEMA` string
`"qubit-office-tower/1"` is asserted in 3 tests. A5 **adds** fields; it must not rename or drop
an existing one. This is the one place where the "additive only" rule is mechanically enforced.

---

## C6 — A new JS module silently breaks two existing tests

`tests/test_office_bundle.py:43-46,151` and `tests/test_office_plugin_wiring.py:57,229`
hardcode the **5-module list and their globals**. A2/A3 adding a module must expect these to
fail. **Builders must not edit those tests** — V updates them in a new file, or A3+ agrees the
new test lives under V's ownership.

---

## C7 — The 8 unemitted events (the reason the office looks dead)

`tower.py:46-70` maps 15 event kinds; audio agent A5's audit found **8 are mapped but never
emitted**: `escalation, ledger_write, mcp_call, meeting, memory_written, note_written,
skill_used, task_unverified`. Those poses are unreachable — the animation exists but nothing
ever fires it.

**This confirms the parent's finding: the canvas is empty because nothing happens, not because
it draws badly.** A5's job is to emit these at honest call sites; the "no unreachable pose"
test then guards it.

---

## O1–O3 — owner decisions (2026-10-13, "I go with your recommendations")

| # | decision |
|---|---|
| **O1 — sprites** | **Neutral families only** (C4). No celebrity/meme sets. |
| **O2 — sound** | **Inline the 12 sounds as base64** (C2). No vendored file touched. |
| **O3 — autonomous cadence** | **Off by default.** When enabled: **15-minute tick**, **≤1 turn per seat per tick**, bounded by the existing envelope + turn-cap system. Conservative because it spends real inference money; the owner can dial it up later. |

**O3 detail for A4:** the loop must be a **cron-shaped tick** (the repo already has a cron
scheduler), not a daemon thread — so it survives process restarts the way every other scheduled
job does, and so it can be paused from the existing cron surface. It must emit **real
`office_event` rows** so C7 holds: a tick that does nothing emits nothing and animates nothing.
Default `enabled: false` in config; a single config flag turns it on.
