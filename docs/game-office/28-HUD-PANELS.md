# 28 — The HUD panels and the control bar

> **Status 2026-10-13 — design (no renderer code in this file).**
> The tower page (`frontend/qubit-app/plugins/qubit-office/qubit-office.js`) already draws
> the building, the characters, a name/state panel on click, a minimap, an activity list and
> a P&L chart. The **right-hand rail is bare**: the owner's verdict is that the reference
> project `rsanandres/aphae` has a rich HUD the page does not. This document is the spec for
> that HUD — every panel, every readout, and **the exact `/api/office/tower` field each one
> reads**, plus the four control-bar buttons the read-only contract permits.
>
> This is a **design deliverable**. It defines what the renderer must compute and from which
> field. It contains no renderer code, and no panel here requires a write.

## The rule that shapes every choice (C4 / C7)

The tower plugin is **read-only** and **event-driven**:

- **C4 — the view never writes org state.** No `POST`/`PUT`/`PATCH`/`DELETE`, and the only
  path fetched is `/api/office/tower` (the literal string; a query string may be appended).
  Enforced by `tests/test_office_tower_plugin.py::test_the_tower_issues_no_mutating_request`.
- **C7 — the tower renders only from events.** Exactly one timer, and it is the poll. No
  `setTimeout`, no `requestAnimationFrame`, no CSS keyframes. A readout may only show a number
  that traces to a payload field (`test_office_tower_no_phantom_motion.py`).

**Consequence for this spec, and it is the whole point:** a panel that would need a field the
payload does not carry is **not** specified as if the field existed. It is specified as
`needs a backend field` and named, so the backend change can be argued on its own merits. No
value is ever synthesised, defaulted to "working", or interpolated from a timer.

## The payload fields this spec reads

Verified against `voicebridge/office/tower.py` (`state()`, `hud()`, `floorplan()`) and by
calling the module against a live populated store, not from the brief's prose. The placement
of each field:

| Field | Where it lives | Notes |
|---|---|---|
| `now` | top level | epoch seconds; the clock the whole payload is computed against |
| `active_window_s` | top level | `ACTIVE_WINDOW_S` = 120.0, the freshness window |
| `walk_s` | top level | `WALK_S` = 6.0, how long a walk takes |
| `empty`, `empty_reason` | top level | `empty` is true only with **no seats, no floors and no ledger rows** |
| `preview`, `preview_legend` | top level | `preview` true only when `?preview=1` **and** `empty` |
| `motion_rule` | top level | the literal C7 sentence; the page already renders it |
| `read_only` | top level | always `true` |
| `schema` | top level | `"qubit-office-tower/1"` |
| `hud.headcount` | `hud` | count of agents where status != terminated |
| `hud.revenue_cents` / `cost_cents` / `net_cents` | `hud` | the org fold, same view `qubit office pnl` reads |
| `hud.burn_cents_per_day` | `hud` | metered inference per day (float, can be sub-cent) |
| `hud.runway_days` | `hud` | **nullable** — `null` when burn is 0 |
| `hud.runway_is_derived` | `hud` | boolean; false exactly when `runway_days` is null |
| `hud.survival.floor_days` | `hud.survival` | the survival floor, e.g. 7.0 |
| `hud.floors[]` | `hud.floors` | per-floor `{floor_id, number, role, departments, revenue_cents, cost_cents, net_cents}` |
| `hud.pnl_source` | `hud` | the provenance sentence |
| `floorplan.grid.{cols,rows,tile}` | `floorplan.grid` | 21×10×1 for the standard grid |
| `floorplan.floors[]` | `floorplan.floors` | `{id, number, name, role, theme, z, rooms[]}`; each room `{id, slug, kind, department_id, x, y, w, h, seats[]}` |
| `characters[]` | top level | `{agent_id, slug, seat_type, department_id, supervisor_id, seated, position, facing, walking, at, animation, last_event, stalled}` |
| `characters[].position` | each character | `{floor, floor_role, room, room_id, slot, x, y}`; **`progress` IS carried: `walk_for()` computes it and `state()` assigns `position["progress"]` while walking (N11 closed)** |
| `characters[].last_event` | each character | `{kind, age_s}` — **nullable** (`null` when the seat has no event on record) |
| `characters[].animation` | each character | one of `idle, working, walking, carrying, glow, settling, done, stopped, paused, blocked, meeting, walking_to_manager` |
| `characters[].stalled` | each character | true when a `run_started` event has aged past the window and never settled |
| `characters[].at` | each character | `desk` \| `station` \| `walking` |
| `activity[]` | top level | `{id, ts, kind, agent_id, agent_slug, text}` — newest first, limit 60 |
| `history[]` | top level | `{day, date, revenue_cents, cost_cents, net_cents}` — 30 days, zero days kept |
| `history_totals.{revenue_cents,cost_cents,net_cents}` | top level | the window totals |
| `grid.{cols,rows,tile}` | top level | same object as `floorplan.grid`; the renderer already reads it for `clampPan` |

Fields the payload does **not** carry (each one blocks a panel below): a per-character display
name, a per-character task or feature title, per-character spend, the organisation's model
tier, a supervisor's name, a total active/awaiting count, a build cost, and any per-agent
whole-day output. See §5.

---

## 1. The panels

### P1 — Floor stack board (already present; keep)

**What it shows.** One row per floor from `hud.floors[]` — `number`, `role`, headcount is **not**
here (there is no per-floor headcount field), `departments`, and the floor's `revenue_cents` /
`cost_cents` / `net_cents`. The net cell takes the sign colour (green ≥ 0, red < 0).

**Exact fields.** `hud.floors[].number`, `.role`, `.departments`, `.revenue_cents`,
`.cost_cents`, `.net_cents`. (Names come from `floorplan.floors[].name` if the board prefers the
floor's display name; match on `number`.)

**Renderer note.** This is the same fold as the P&L stat, so the two cannot disagree. A floor with
`departments === 0` renders as an empty floor, not a zero row that is hidden — an empty floor is a
real, reportable state.

### P2 — Floor focus bar (already present; keep)

**What it shows.** `Floor · [all] [F1 · executive] [F2 · …] …` as pressed-state buttons; setting
`floor` re-frames the camera and filters the minimap.

**Exact fields.** `floorplan.floors[].number`, `.role` (the label); the selection drives
`floorplan.floors[].rooms[]` and `characters[].position.floor`.

**Renderer note.** The re-frame is keyed on `number + ':' + rooms.length` so a poll does not yank
a pan/zoom the user chose. No new field needed.

### P3 — Stat strip (already present; keep)

**What it shows.** Headcount, Revenue, Cost, Net, Burn/day, Runway.

**Exact fields.** `hud.headcount`, `hud.revenue_cents`, `hud.cost_cents`, `hud.net_cents`,
`hud.burn_cents_per_day`, `hud.runway_days`, and `hud.survival.floor_days` for the runway hint.

**Renderer note.** `runway_days` is nullable by design. When it is `null` the tile reads
`unknown` with the hint `nothing is burning yet` — never `0` and never `∞`. `burn_cents_per_day`
is a float and must render as a rate (`$0.14/day`), not through the integer-cents formatter that
would print `$0.00.4286`.

### P4 — Agent needs meters (**aphae-derived design**, see §2)

**What it shows.** For the selected character, three labelled bars read **directly off the
payload** — not off decaying client state, which C7 forbids. These are the honest translation of
aphae's "needs" idea onto our fields:

| Meter label | Bar fill | Field read |
|---|---|---|
| **Freshness** | `clamp((active_window_s - last_event.age_s) / active_window_s, 0, 1)` | `active_window_s`, `characters[].last_event.age_s` |
| **Progress** | `position.progress` (already 0..1) — **currently absent; see N11** | `characters[].position.progress` (**CARRIED by `state()` (N11 closed: `tower.py` assigns `position["progress"]`) today**) |
| **Attention** | full red bar if `stalled`, else a flat "ok" bar | `characters[].stalled` |

**Exact fields.** `active_window_s`; `characters[].last_event.age_s`; `characters[].last_event`
(treat as nullable); `characters[].position.progress`; `characters[].stalled`;
`characters[].animation`.

**Renderer note (precise).**
- **Freshness fill** = `(active_window_s - last_event.age_s) / active_window_s`, clamped to
  `[0, 1]`. At `age_s = 0` the bar is full (an event just happened); as `age_s → active_window_s`
  it empties; past the window it is 0. This is the *only* correct reading of the decay idea: the
  number is baked by the backend, the renderer does no clock arithmetic beyond the subtraction the
  field already implies.
- **When `last_event` is `null`, the bar is rendered grey and empty — never a made-up value.**
  A seat with no event on record has no freshness to show, and drawing it as "fresh" would be
  inventing work (exactly what the tower must not do).
- **Progress** is drawn only when `position.progress` is present. **It is not present today:**
  `walk_for()` computes `progress` on its mid-walk branch, but `state()` copies only `walk["x"]`
  and `walk["y"]` into `position` — the field is produced and then dropped before the payload
  ships (verified against a live walking character: `walk_for` returned `progress: 0.25` while
  `characters[0].position` had CARRIED by `state()` (N11 closed: `tower.py` assigns `position["progress"]`)). So this meter is a **backend gap (N11)**, not a
  renderer task. When the field lands it is read verbatim and never animated client-side; until
  then the renderer must render **no progress bar**, because a bar it cannot fill is a bar it would
  have to invent.
- **Attention** is binary and comes from `stalled`. It is not derived from `age_s` client-side —
  `stalled` is already the backend's verdict, and re-deriving it would be a second definition.

### P5 — Selected-agent inspector (extends the existing click panel)

**What it shows.** The complete per-seat readout, all of it from the character object:

| Readout | Field |
|---|---|
| Name | `characters[].slug` |
| Role | `characters[].seat_type` |
| Department | `characters[].department_id` |
| State | `characters[].animation` (+ `" (walking)"` when `characters[].walking`) |
| Location | `characters[].position.floor`, `.position.room`, `.at` |
| **Stalled line** | when `characters[].stalled`: "STALLED — no event within `active_window_s`s" |
| Last event | `characters[].last_event.kind` + `age_s` |

**Exact fields.** `characters[].slug`, `.seat_type`, `.department_id`, `.animation`, `.walking`,
`.position.floor`, `.position.room`, `.at`, `.stalled`, `.last_event.kind`, `.last_event.age_s`,
and `active_window_s`.

**Renderer note.** `department_id` may be `null` (unassigned) — render the role alone, not
`dept null`. `position.floor` may be `null` for a seat with no desk (`seated:false`); that seat
renders in the "not at a desk" strip (already present), never with a fabricated location.

### P6 — Character name labels over agents (already present; keep — see §2)

**What it shows.** A short label under/over each figure on the canvas.

**Exact fields.** `characters[].slug`, `characters[].seat_type`, `characters[].animation` (for the
status band colour).

**Renderer note (honest limitation).** The label text **must be `slug`**. Tower sends no
per-character display name, so the label cannot say "Alice" — `slug` is the only identity field in
the payload. Surfacing a real name is `needs a backend field` (see §5, N1) if the owner wants
human names rather than seat slugs.

### P7 — Activity feed (already present; keep — see §2)

**What it shows.** The event stream, newest first, each row `[HH:MM:SS] text`.

**Exact fields.** `activity[].id` (React key), `activity[].ts` (formatted time), `activity[].text`,
`activity[].kind`, `activity[].agent_slug`.

**Renderer note.** `text` is the backend's own sentence (`"<slug> <verb>"`, e.g.
`"worker-seat started a turn"`); the renderer prepends only the timestamp, never re-composes the
sentence. Empty feed ⇒ the existing empty sentence, not a fabricated row.

### P8 — Building / minimap (already present; keep)

**What it shows.** Floors and rooms as rects, characters as markers on the plan extent.

**Exact fields.** `floorplan.floors[].number`, `.rooms[].{x,y,w,h}`, `.rooms[].seats[]`;
`characters[].position.{x,y,floor}`, `.stalled`, `.animation`; `floorplan.grid.{cols,rows}`.

**Renderer note.** A stalled character's marker is red; otherwise the marker uses the animation
colour map, and an **unknown animation falls back to grey** (never to an "active" colour — the
plugin test asserts this for the flat view too).

### P9 — P&L by day (already present; keep)

**What it shows.** Green revenue bars, red cost bars, blue net line, one column per day.

**Exact fields.** `history[].{revenue_cents, cost_cents, net_cents}`; header uses
`history.length` and `history_totals.net_cents`.

**Renderer note.** `history` keeps zero days (backend groups by day and back-fills), so the chart
must not skip them. Scale is `max(revenue, cost, net)` over the window, with the smallest `net`
(usually ≤ 0) as the floor.

### P10 — Preview banner (already present; keep)

**What it shows.** `preview_legend` when `preview` is true.

**Exact fields.** `preview`, `preview_legend`.

**Renderer note.** Only ever true for an **empty** org with `?preview=1`; the renderer must not
show it otherwise. The empty state (`empty && !preview`) keeps its own sentence from
`empty_reason`.

### P11 — Honesty / motion strip (already present; keep, extend)

**What it shows.** `motion_rule`, plus the count of characters whose `animation` is a motion state
(`working, walking, carrying, glow, settling, meeting, walking_to_manager`), the count of
`stalled`, and a `read-only` pill.

**Exact fields.** `motion_rule`, `characters[].animation`, `characters[].stalled`, `read_only`.

**Renderer note.** The motion count is computed by *membership in the motion set*, matching the
backend's `MOTION` tuple in `tower.py` — not by "not idle", because `blocked`/`done`/`stopped`/
`paused` are non-motion states and must not be counted as work.

### P12 — Character roster (already present; keep)

**What it shows.** The clickable list of seats.

**Exact fields.** `characters[].agent_id` (key), `.slug`, `.seat_type`, `.animation`, `.stalled`.

**Renderer note.** Selecting a row populates P5 and the meters in P4. Roster is sorted by
`agent_id` (the store's `ORDER BY id`) so it does not reshuffle between polls.

---

## 2. The aphae-derived design ideas — adopted, and rejected

`rsanandres/aphae` ships **no licence file**, which means all rights reserved. It was **read for
design only**. **No aphae code, in any language, was copied, adapted, transliterated or pasted
into this spec or anything downstream of it.** The ideas below are described in our own words and
re-expressed against *our* payload; where an aphae feature had no counterpart in our data it was
dropped rather than faked.

### Adopted

1. **Needs meters over raw existence flags (→ P4).** aphae's inspector draws a labelled bar per
   need, and the bar is the readable part while the number is for the curious. Adopted as the
   freshness/progress/attention meters: a bar per fact the payload already carries, so the owner
   reads state at a glance instead of parsing text. *Our* bars are computed from `age_s`,
   `progress` and `stalled`; they do **not** decay on a client timer — that would be C7 violation
   by a different name. **No aphae code used.**

2. **A timestamped event log with tabs (→ P7, and the tab idea as a §5 backend gap).** aphae's
   log prefixes every entry with a day/time stamp and splits entries across tabs (Events / Talk /
   You) so one noisy stream does not drown the others. Adopted the **timestamp prefix** on our
   `activity[]` rows (we already have `ts`). The **tab split** is adopted as a design *only as far
   as our data allows*: our payload carries one activity stream, so today it is one tab. A second
   tab needs a second backend field (§5, N7). **No aphae code used.**

3. **A control bar with speed/pause and panel toggles (→ §4).** aphae's bottom bar groups the
   time controls (pause, 1×/2×/3×), a "live" toggle, and lit panel buttons. Adopted the **shape**:
   one row of small, pressed-state buttons at the top of the page. Adopted only the **four** verbs
   our read-only contract permits (§4). The speed/pause controls were **rejected** as
   non-functional here (§3 of Rejected). **No aphae code used.**

4. **Name labels over agents (→ P6).** aphae prints a title above each figure so the crowd is
   legible without clicking. Adopted — our `drawMarker`/name label already carries `slug` over the
   figure. Adopted the *idea*; the label content is our `slug` field, and no aphae drawing code
   was used. **No aphae code used.**

5. **Pressed/lit panel buttons (→ §4).** aphae keeps a panel's button lit while the panel is open
   (`set_pressed_no_signal(panel.visible)`). Adopted as `aria-pressed` on our control-bar buttons
   so the bar states what is currently on. **No aphae code used.**

### Rejected, and why

1. **Speed controls (1×/2×/3×) and a pause toggle.** aphae can pause a *local game clock* it owns.
   Our tower has **no clock of its own**: every figure is computed server-side against `now`, and
   the payload is polled. There is nothing client-side to pause or speed up — faking a pause would
   either stop the poll (and freeze the page against reality) or lie about it. **Rejected: no
   backing field and against C7.** (A real "pause the org" is a *write* and is C4-forbidden for
   this plugin.)
2. **A "god mode" / authoring toolbar.** aphae has a Tab toolbar that *places objects*. Every
   placement is a write. **Rejected: C4.**
3. **Memories / relationships / secrets / goals / personality readouts.** These are aphae's richest
   panel content; our payload carries **none** of them for a character. Rather than invent a
   "memory" bar, they are listed as backend gaps (§5, N1/N2) and left out of the renderer.
4. **Broadcast chrome (LIVE bug, viewer ratings, episode counter, drama level).** aphae's HUD is
   built around a fictional TV show; there is no viewer/drama/episode data in our org, and it is
   not this product. A "drama level" derived client-side would be theatre. **Rejected: not our
   data, and would be a fabricated readout.**
5. **A toasts/modals system (away digest, dilemma modal, achievement toast).** Modals imply a user
   decision that then writes somewhere. **Rejected: no write path; and the tower's loud surfaces
   are already specced in `55-DASHBOARD-PAGE.md`.**

---

## 3. Per-panel implementation notes (renderer computes / reads)

Every entry states **what the renderer computes locally** versus **what it reads verbatim**, and
calls out the null/absent case. Rule of thumb: the renderer may format and lay out; it may not
synthesise a value the payload omits.

| Panel | Renderer reads (verbatim) | Renderer computes locally | Absent/edge case |
|---|---|---|---|
| P1 floor board | `hud.floors[].{number,role,departments,revenue_cents,cost_cents,net_cents}` | sign → colour; `money()` on cents | floor with `departments:0` → "empty floor", still shown |
| P2 floor bar | `floorplan.floors[].{number,role}` | pressed state from local `floor` | no floors → bar renders only `all` |
| P3 stat strip | `hud.*` money fields, `runway_days`, `survival.floor_days` | `money()` / `moneyRate()` | `runway_days` `null` → `unknown` + "nothing is burning yet" |
| P4 freshness meter | `active_window_s`, `last_event.age_s` | `clamp((window - age)/window, 0, 1)` | `last_event` `null` → **grey, empty bar** |
| P4 progress meter | `position.progress` — **CARRIED by `state()` (N11 closed: `tower.py` assigns `position["progress"]`) (N11)** | bar width only | field never present today → **no bar** (gap, not renderer work) |
| P4 attention meter | `stalled` | colour only | `stalled:false` → flat ok bar |
| P5 inspector | all `characters[]` scalars listed | string assembly | `department_id` `null`; `position.floor` `null` |
| P6 name label | `slug`, `animation` | label placement, status-band colour | — (slug always present) |
| P7 activity | `activity[].{id,ts,text}` | `new Date(ts*1000)` → time string | empty array → empty sentence |
| P8 minimap | `floorplan.*`, `characters[].position.*` | scale to canvas, marker colour | unknown animation → grey |
| P9 P&L | `history[].*`, `history_totals.net_cents` | scale, bar geometry | all-zero history → flat zero line, not hidden |
| P10 preview | `preview`, `preview_legend` | show/hide | `preview:false` → banner absent |
| P11 motion strip | `motion_rule`, `animation`, `stalled`, `read_only` | count membership in motion set | — |
| P12 roster | `characters[].{agent_id,slug,seat_type,animation,stalled}` | sort by `agent_id` | — |

**Grid / camera note.** `grid.cols`/`grid.rows` are read for `clampPan` and `fitToBuilding`; they
are *not* a HUD readout and must not be displayed as one — they describe the world extent, not the
org.

---

## 4. The control bar

Placed once at the top of the page (the plugin's existing `styles.bar` row). Four buttons, all of
which are **pure view operations** — no request is issued by any of them, so C4 holds by
construction.

| Button | Verb | What the renderer changes | Field(s) it reads | Why it is read-only |
|---|---|---|---|---|
| **Reset** | pan reset | sets `camRef.cx/cy` back to `(0,0)` then `clampPan` | `grid.cols`, `grid.rows` | moves the camera only |
| **Fit** | fit to building | calls `fitToBuilding` over the floors currently shown | `floorplan.floors[].rooms[].{x,y,w,h}`, `.z` | moves the camera only |
| **Zoom − / +** | zoom | multiplies `camRef.zoom`, clamped to `[CHAR_FLOOR_ZOOM, MAX_ZOOM]` | none (local camera state) | local view state |
| **P&L** | toggle chart | flips local `showChart`; shows/hides the P9 panel | `history[]`, `history_totals.net_cents` | a local visibility toggle |

**Notes.**
- Reset and Fit are the same two camera operations the drag/wheel handlers already perform; wiring
  them to buttons changes nothing about what is fetched.
- Zoom's lower bound is `CHAR_FLOOR_ZOOM` (0.85): below it the 15-unit characters are unreadable,
  and an office whose people are invisible is not a drawing of an office.
- None of the four buttons is a "refresh" button. The poll (`setInterval(load, POLL_MS)`) is the
  only thing that re-reads, and it stays the only timer.

### Control-bar features we **cannot** support (they require a write)

These are named explicitly so nobody adds them later thinking they were an oversight. Each is
C4-forbidden for this plugin, and would have to go through the shared confirmation journal (the
staged-op path), not the tower page.

| Feature | Why it cannot live here |
|---|---|
| **Publish** (publish an artifact/business) | commits state; a mutation, not a read. Its only allowed home is `dashboard_ops` → `409 + confirmation_id` → the confirm path |
| **Edit** (edit a room, seat, or floorplan; the `75-EDITOR.md` floorplan editor) | every edit is a write to `office_room`/`office_agent`; violates C4 outright |
| **Delete** (delete a room/seat/task) | a destructive write; explicitly runs through the confirmation journal, never a direct call |
| **Hire / fire / move seat** | write (§C4), even though `55-DASHBOARD-PAGE.md` lists them as staged ops — they are *staged*, i.e. not this plugin's |
| **Approve / deny a staged review or escalation** | settles a `confirmation_id`; that is a write on the confirmation path |
| **Speed / pause the org** | there is no client-side clock to affect (see §2 Rejected 1); a real org pause is a server-side write |

**Enforcement.** `tests/test_office_tower_plugin.py` already refuses `POST`/`PUT`/`PATCH`/`DELETE`
anywhere in the bundle and pins the only fetched path to `"/api/office/tower"`. Nothing in this
control bar may break that, and the test is the reason it will not.

---

## 5. Where a needed field does **not** exist (stated, not invented)

Each row is a desired readout whose data is **absent from `/api/office/tower`**. They are marked
`needs a backend field` and are deliberately **not** rendered by this spec.

| # | Wanted readout | Would need | Status |
|---|---|---|---|
| **N1** | Character **display name** (e.g. "Alice") over a figure / in the inspector | a name on the character or seat object; today only `slug` and `seat_type` exist | needs a backend field |
| **N2** | Per-character **memory / relationship / goal** lines (aphae's richest panel) | any per-agent memory, relationship or goal data in the payload | needs a backend field |
| **N3** | Per-character **task** or current feature | the character's current `office_task` id/title | needs a backend field |
| **N4** | Per-character **spend** / balance | metered spend attributed to the seat | needs a backend field (the brief's README notes provider spend is cap-attested, not measured, so this may not be derivable at all) |
| **N5** | Org **model tier** (best/cheap per seat, D3) | a tier field on the character or `hud` | needs a backend field |
| **N6** | **Supervisor name** (the escalation target) | `supervisor_id` exists; the supervisor's *slug* needs a lookup, and there is no name either | partially present: `supervisor_id` is a number; a resolvable label is `needs a backend field` |
| **N7** | Activity **tab split** (e.g. `run_*` lifecycle vs capability/tool events) | either a `category` on `activity[]` or a server-side filter param | needs a backend field (the `kind` field exists and *could* drive client-side grouping — that is a **possible** design, not a payload field today) |
| **N8** | **Active / awaiting count** roll-up (how many seats are mid-turn) | a counter in `hud` | needs a backend field (derivable from `characters[].animation` by the renderer — listed here only so nobody adds a redundant `hud` field by mistake) |
| **N9** | **Build cost** / capex readout | a capex total in `hud` | needs a backend field (the ledger has `capex` rows; the payload exposes only the folds shown in §fields, not a capex line) |
| **N10** | Per-agent **whole-day output** (tasks done, revenue) | per-agent daily rollup | needs a backend field |
| **N11** | A walking character's **walk progress** (`position.progress`) | nothing new is needed in the data model — `walk_for()` already computes `progress` (verified: returned `0.25` mid-walk) but `state()` copies only `walk["x"]`/`walk["y"]` into `position` and CARRIED by `state()` (N11 closed: `tower.py` assigns `position["progress"]`) | **needs a back end fix, not a new field** — a one-line change in `state()` to carry `progress` through. Until then the P4 progress meter renders nothing |

**How these were checked.** Field names were read from `voicebridge/office/tower.py` and confirmed
by calling `tower.state()` against a live populated store: an empty org returns `hud` with exactly
`{burn_cents_per_day, cost_cents, floors, headcount, net_cents, pnl_source, revenue_cents,
runway_days, runway_is_derived, survival}` and a populated run returns `characters[]` with exactly
`{agent_id, slug, seat_type, department_id, supervisor_id, seated, position, facing, walking, at,
animation, last_event, stalled}`. No field in N1–N10 appears in either set.

---

## 6. Machine-readable form

`docs/game-office/hud-panels.json` carries the same information as data: every panel, its status
(`present` or `backend_gap`), the exact payload field paths it reads, the aphae design idea it
derives from (if any), and the control-bar buttons including the ones that are write-blocked. That
file is the source a future renderer or a test should read — this prose explains it.
