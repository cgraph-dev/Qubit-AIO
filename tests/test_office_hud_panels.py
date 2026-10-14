"""M4 — the HUD panel spec, verified against the real payload in BOTH directions.

`docs/game-office/28-HUD-PANELS.md` and its machine-readable twin
`docs/game-office/hud-panels.json` name **12 panels** and the **87 payload field
paths** they read. Nothing checked that the renderer actually reads those fields,
or that every listed path EXISTS in `/api/office/tower`. A spec that cites a field
nobody sends is the exact `position.progress` failure: the spec was right, the
backend dropped the field, and the renderer drew nothing for hours.

This file closes the loop on five claims, each capable of failing:

1. **Every cited path resolves against a REAL payload.** A real org is built in a
   tmp XDG store (seats, a room, an event, a ledger row), `tower.state()` is
   called, and every path in `hud-panels.json` is resolved. A miss is classified
   honestly: (a) absent because the org is too small for it, (b) absent because
   it is conditional, (c) WRONG — and only (c) is a defect. The classification is
   proven, not asserted empty-handed: the test also builds the payload that
   SHOULD carry each (a)/(b) path and shows it appears.

2. **The 11 documented gaps are genuinely absent.** For each of N1..N11 the spec
   says `needs a backend field`; the field it names must NOT be anywhere in the
   payload. A gap that quietly filled in is a spec error and fails loudly.
   N11 (`position.progress`) is the deliberate exception and is split out into
   assertion 5 — see its note.

3. **The renderer reads the fields P3 (stat strip) and P5 (inspector) claim.**
   The real `Office()` component is executed with a stub SDK that feeds it a
   chosen view; the *drawn element tree* is captured. Two payloads differing ONLY
   in the fields a panel claims must produce different drawn output — a panel
   that renders a constant is a dead panel. P3 and P5 are each checked this way;
   P4's meter inputs and no-faked-bar rule are checked on the same harness.

4. **The stat strip numbers come from the LEDGER, not the drawing.** The HUD
   values in the payload must equal what `ledger.pnl` / `ledger.health` report for
   the same store, so the HUD cannot drift from the books.

5. **`position.progress` resolves when a character is walking** (it was gap N11,
   now fixed). A real mid-walk character must arrive with the field present and
   in (0, 1) — the ONLY permitted source of the walk bob (C7).

Hermetic: tmp XDG state only; `node` for the renderer half; no network, no
browser, no provider call, no live service.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest  # noqa: E402

from _office_node import run_node  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SPEC = REPO / "docs" / "game-office" / "hud-panels.json"
ENTRY = (REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
         / "qubit-office.js")

NODE = shutil.which("node")


# ===================================================================== #
#  a real org, built in a tmp XDG store                                 #
# ===================================================================== #
def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _real_org(cfg):
    """A real org with seats, a room, a seated + walking character, and money.

    Rich enough that every spec path either resolves here or is legitimately
    conditional: the walking `wk-0` carries `position.progress` and
    `last_event`; the seated `mgr` carries `position.floor_role`; the ledger
    carries revenue + inference so the HUD is not all-zero.
    """
    from voicebridge.office import org as O, store, ledger as L

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    d = O.hire(cfg, "dir", seat_type="director_portfolio",
               supervisor_id=ceo, confirmed=True)["id"]
    floor = O.floor_create(cfg, 1, "Work", role="director",
                           confirmed=True)["id"]
    dept = O.department_create(cfg, "shop", director_id=d, floor_id=floor,
                               kill_criteria="two quarters negative net",
                               confirmed=True)["id"]
    room = O.room_create(cfg, dept, "desks", kind="work",
                         confirmed=True)["id"]
    mgr = O.hire(cfg, "mgr", seat_type="manager", supervisor_id=d,
                 department_id=dept, room_id=room, confirmed=True)["id"]
    wk = O.hire(cfg, "wk-0", seat_type="worker", supervisor_id=mgr,
                department_id=dept, room_id=room, confirmed=True)["id"]

    L.record(cfg, "revenue", 12_345, agent_id=d, department_id=dept)
    L.record(cfg, "inference", 2_345, agent_id=d, department_id=dept)

    now = store.now()
    # a `tool_call` event 3s ago (WALK_S == 6.0) => wk-0 is MID-WALK, so it
    # carries `position.progress` in (0,1) and a `last_event` with age_s.
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        conn.execute(
            "INSERT INTO office_event(ts, kind, agent_id, department_id,"
            " payload) VALUES(?,?,?,?,?)",
            (now - 3.0, "tool_call", wk, None, "{}"))
        conn.commit()
    finally:
        conn.close()
    return {"ceo": ceo, "director": d, "floor": floor, "department": dept,
            "room": room, "manager": mgr, "worker": wk, "now": now}


# ===================================================================== #
#  path resolution                                                      #
# ===================================================================== #
def _resolve(payload, path):
    """Resolve a spec path like `characters[].position.floor` against payload.

    Returns `(found, values)`. `found` is True iff the path resolves against AT
    LEAST ONE element of every `[]` collection it crosses. "At least one" is the
    honest reading of a panel's "I read this field" claim for a documented
    NULLABLE / conditionally-present field: `position.progress` is absent on a
    non-walking character by design (the spec's `nullable_fields` says so), and
    `position.floor_role` is absent on a seat with no desk. A path that resolves
    on NO element is the `wrong` case — a field the backend never sends.
    """
    tokens = [t for t in re.split(r"\.(?![^\[]*\])", path) if t]

    def step(node, i):
        if i == len(tokens):
            return True
        tok = tokens[i]
        many = tok.endswith("[]")
        key = tok[:-2] if many else tok
        if not isinstance(node, dict) or key not in node:
            return False                     # missing key -> this branch fails
        val = node[key]
        if many:
            if not isinstance(val, list):
                return False
            if not val:
                return False                 # nothing to fan out over
            return any(step(el, i + 1) for el in val)
        if i + 1 == len(tokens):
            return True                      # the final key is present
        return step(val, i + 1)

    return step(payload, 0), None


def _spec_paths():
    return json.loads(SPEC.read_text())["payload_fields_read"]


# ===================================================================== #
#  ASSERTION 1 — every cited path resolves                              #
# ===================================================================== #
def test_the_spec_file_lists_the_expected_paths():
    """A guard on the SPEC itself: the count is what the report claims."""
    paths = _spec_paths()
    assert len(paths) == 87, (
        "hud-panels.json no longer lists 87 payload paths (%d) — the spec "
        "changed and this verification must be re-read" % len(paths))
    assert "characters[].position.progress" in paths


def test_every_cited_path_resolves_against_a_real_payload(tmp_path, monkeypatch):
    """The core direction: every path the spec cites exists in a real payload.

    Misses are NOT tolerated silently. Each unresolved path is classified:
      (a) the org is too small — proven by building the payload that DOES carry
          it (a walking char for `progress`, a seated char for `floor_role`, ...)
      (b) conditional — proven by the code path that emits it (`preview_legend`
          only on `empty && ?preview=1`)
      (c) WRONG — the spec cites a field the backend never sends
    Only (c) fails. `hud.floors[].floor_id` etc. are all present on a populated
    org; the classification below is explicit so a silent (c) cannot hide.
    """
    from voicebridge.office import tower

    cfg = _cfg(tmp_path, monkeypatch)
    _real_org(cfg)
    payload = tower.state(cfg, now=tower.store.now())

    unresolved = []
    for path in _spec_paths():
        found, _ = _resolve(payload, path)
        if not found:
            unresolved.append(path)

    # the only acceptable misses, each with the reason it is not a defect
    conditional = {"preview_legend"}          # needs `?preview=1` AND empty
    small_org = set()                          # none: the org carries all of it
    wrong = [p for p in unresolved
             if p not in conditional and p not in small_org]
    assert wrong == [], (
        "the spec cites field paths the real payload does NOT carry — the "
        "position.progress failure in a new place:\n  " + "\n  ".join(wrong))
    assert set(unresolved) <= (conditional | small_org), unresolved


def test_the_org_small_and_conditional_misses_are_proven_not_assumed(
        tmp_path, monkeypatch):
    """Each non-resolving path is shown to be conditional, not wrong.

    `preview_legend` is the one path the standard populated org lacks. The
    spec's own rule is that it appears only for an empty org with `?preview=1`,
    so — per the spec — this test proves the miss is (b) by building exactly
    that payload and seeing the field arrive.
    """
    from voicebridge.office import tower

    cfg = _cfg(tmp_path, monkeypatch)
    _real_org(cfg)
    populated = tower.state(cfg, now=tower.store.now())
    assert not _resolve(populated, "preview_legend")[0], (
        "preview_legend leaked into a live org — it must never be mixed with "
        "real rows (D14)")

    # the empty + preview org: the field appears
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir(parents=True, exist_ok=True)
    empty_cfg = _cfg(empty_dir, monkeypatch)
    preview = tower.state(empty_cfg, preview=True)
    assert _resolve(preview, "preview_legend")[0], (
        "preview_legend did not resolve even under preview=True")
    assert preview["preview"] is True and "PREVIEW" in preview["preview_legend"]
    # the OTHER two conditional paths resolve on the right entities even though
    # the spec marks them nullable — proven, not assumed:
    assert _resolve(populated, "characters[].position.floor_role")[0], (
        "position.floor_role is absent from a payload with a SEATED character")
    assert _resolve(populated, "characters[].position.progress")[0], (
        "position.progress is absent from a payload with a WALKING character")


# ===================================================================== #
#  ASSERTION 2 — the 11 documented gaps are genuinely absent            #
# ===================================================================== #
# For each gap, the concrete field name(s) the spec says do NOT exist, and a
# recursive search that proves each is nowhere in the payload. N11 is the
# deliberate exception: it was a DROP (the field was computed and discarded),
# now fixed — it is verified present-and-correct by assertion 5, not here.
GAP_FIELDS = {
    "N1": ["display_name", "person_name", "employee_name"],
    # N2 — PARTIALLY FILLED (A5, L2). `memory`/`memories` are now REAL payload
    # fields (tower.state()'s per-character `memory` block + `dossier`), so they
    # are removed from the absent list and asserted PRESENT below. The rest of
    # N2 stays absent on purpose: there is still no authored `personality`,
    # `secret`, `goal` or `relationship` field — the dossier is derived, and a
    # derived belief is not a stored trait.
    "N2": ["relationship", "relationships", "goal",
           "goals", "personality", "secret", "secrets"],
    "N3": ["task", "task_id", "current_task", "task_title", "feature"],
    "N4": ["spend", "spend_cents", "balance", "agent_spend", "seat_spend"],
    "N5": ["tier", "model_tier", "tier_name"],
    "N6": ["supervisor_name", "supervisor_slug", "manager_name"],
    "N7": ["category"],
    "N8": ["active_count", "awaiting_count","active_agents", "awaiting"],
    "N9": ["capex", "capex_cents", "build_cost", "build_cost_cents"],
    "N10": ["daily_output", "output_cents", "tasks_done", "per_agent_daily"],
    # N11 (position.progress) intentionally omitted: it is now FIXED and is
    # asserted PRESENT in test_position_progress_resolves_... below.
}


def _all_keys(obj, acc=None):
    """Every dict key anywhere in the payload, recursively."""
    if acc is None:
        acc = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.add(str(k))
            _all_keys(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _all_keys(v, acc)
    return acc


def test_no_documented_gap_field_is_present_in_the_payload(
        tmp_path, monkeypatch):
    """N1..N10: the spec says these fields do not exist. If one appears, the
    spec is wrong and this fails loudly.

    N6 is the subtle one the spec itself half-concedes: `supervisor_id` IS
    present (a number), but no resolvable *label* exists. This test asserts the
    label is absent WHILE the id is present, which is exactly the spec's
    'partially present' claim — so a future full gap-fill (a real
    `supervisor_name`) is caught as a spec change rather than passing quietly.
    """
    from voicebridge.office import tower

    cfg = _cfg(tmp_path, monkeypatch)
    _real_org(cfg)
    payload = tower.state(cfg, now=tower.store.now())
    keys = _all_keys(payload)

    offenders = []
    for gap, names in GAP_FIELDS.items():
        for name in names:
            if name in keys:
                offenders.append("%s: %r IS present" % (gap, name))
    assert offenders == [], (
        "the spec lists these as absent backend fields, but the payload now "
        "carries them — update 28-HUD-PANELS.md §5 and hud-panels.json:\n  "
        + "\n  ".join(offenders))

    # N6's half-truth: the numeric id is present, its label is not.
    assert "supervisor_id" in keys, (
        "N6 says supervisor_id is present (as a number) — it is gone")
    assert "supervisor_name" not in keys and "supervisor_slug" not in keys

    # N2's now-filled half (A5, L2): the per-character memory/personality block
    # EXISTS. This is the positive half of the gap — the spec said "no memory
    # field"; it now does. Asserted PRESENT so a future removal fails loudly.
    assert "memory" in keys and "dossier" in keys, (
        "A5 landed the per-seat memory + dossier fields; they are gone")


# ===================================================================== #
#  ASSERTION 3 — the renderer reads the fields P3 and P5 claim          #
# ===================================================================== #
# The renderer is an IIFE ending in a register() call; the tail is rewritten to
# export the component + formatters. Only the SDK STUB differs from the browser:
# `createElement` keeps type/props/children so the drawn element tree can be
# walked, and `useState` yields the view we choose instead of the initial one.
REGISTER_LINE = 'window.__QUBIT_PLUGINS__.register("qubit-office", Office);'

RENDER_PROGRAM = r"""
var __src = %(src)s;
var __use = %(use)s;          // [view, floor, pick, showChart, barTick]
var captured = {};
globalThis.window = {
  __QUBIT_PLUGIN_SDK__: {
    React: {createElement: function (t, p) {
      return {type: t, props: p || {},
              children: Array.prototype.slice.call(arguments, 2)};
    }},
    hooks: {
      _i: 0,
      useState: function (init) {
        var i = this._i++;
        return [i < __use.length ? __use[i] : init, function () {}];
      },
      useEffect: function () {}, useRef: function (v) { return {current: v}; },
      useCallback: function (f) { return f; }
    },
    fetchJSON: function () { return Promise.resolve({}); }
  },
  __QUBIT_PLUGINS__: {register: function (n, f) { captured.n = n; captured.f = f; }},
  location: {search: ""}
};
(0, eval)(__src);
var Office = captured.f;
var tree = Office();
/* Flatten to the leaves the renderer actually put on the page: every string
 * and number handed to createElement, in draw order. Props that are objects
 * (styles) carry no readout and are skipped. */
var leaves = [];
(function walk(n) {
  if (n === null || n === undefined || n === true || n === false) return;
  if (Array.isArray(n)) { n.forEach(walk); return; }
  if (typeof n === "object" && n.type !== undefined) {
    walk(n.children); return;
  }
  leaves.push(typeof n === "object" ? JSON.stringify(n) : n);
})(tree);
process.stdout.write(JSON.stringify({n: captured.n, leaves: leaves}));
"""


def _render(src_text, view, pick=None, floor=None):
    """Execute the real `Office()` against `view`; return its drawn leaves."""
    assert NODE is not None, "node is required for the renderer assertions"
    program = RENDER_PROGRAM % {
        "src": json.dumps(src_text),
        "use": json.dumps([view, floor, pick, False, 0]),
    }
    proc = subprocess.run([NODE], input=program, capture_output=True,
                          text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node failed:\nSTDOUT:\n%s\nSTDERR:\n%s"
                             % (proc.stdout, proc.stderr))
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(NODE is None, reason="node is not on PATH")
def test_the_render_harness_reaches_the_real_component():
    """If this fails, every renderer assertion below is meaningless."""
    src = ENTRY.read_text()
    assert REGISTER_LINE in src, (
        "the harness rewrites this register line; it is gone from %s" % ENTRY)
    out = _render(src, {"loading": True, "error": None, "data": None})
    assert out["n"] == "qubit-office", out
    assert any("Reading the office" in str(x) for x in out["leaves"]), out


def _payload(hud=None, chars=None, extra=None):
    """A payload built to EXERCISE the stat strip / inspector, not the store."""
    base = {
        "schema": "qubit-office-tower/1", "now": 1.0, "active_window_s": 120.0,
        "walk_s": 6.0, "empty": False, "empty_reason": "",
        "hud": {
            "headcount": 1, "revenue_cents": 0, "cost_cents": 0,
            "net_cents": 0, "burn_cents_per_day": 0.0, "runway_days": None,
            "runway_is_derived": False, "floors": [],
            "survival": {"floor_days": 7.0}, "pnl_source": "x",
        },
        "floorplan": {"schema": "qubit-office-floorplan/1",
                      "grid": {"cols": 21, "rows": 10, "tile": 1},
                      "floors": []},
        "characters": [], "activity": [], "history": [],
        "history_totals": {"revenue_cents": 0, "cost_cents": 0, "net_cents": 0},
        "motion_rule": "RULE", "read_only": True, "preview": False,
    }
    if hud:
        base["hud"].update(hud)
    if chars is not None:
        base["characters"] = chars
    if extra:
        base.update(extra)
    return base


@pytest.mark.skipif(NODE is None, reason="node is not on PATH")
def test_p3_stat_strip_renders_from_hud_not_from_a_constant():
    """THE dead-panel check for P3.

    Two payloads differ ONLY in the `hud` money fields. The rendered text must
    change accordingly — the headcount tile, the revenue string, the burn RATE
    and the runway tile. A stat strip that renders a constant would draw the
    same leaves for both and fail here.

    It also pins the two documented edge rules on the SAME fields: the burn is
    a RATE (`$0.14/day`, never `$0.00.4286`) and a null runway reads `unknown`
    with the `nothing is burning yet` hint.
    """
    src = ENTRY.read_text()

    a = _payload(hud={"headcount": 3, "revenue_cents": 12_345,
                      "cost_cents": 2_345, "net_cents": 10_000,
                      "burn_cents_per_day": 14.286, "runway_days": 700.0})
    b = _payload(hud={"headcount": 9, "revenue_cents": 1, "cost_cents": 2,
                      "net_cents": -1, "burn_cents_per_day": 0.0,
                      "runway_days": None})

    la = _render(src, {"loading": False, "error": None, "data": a})["leaves"]
    lb = _render(src, {"loading": False, "error": None, "data": b})["leaves"]

    # the fields a stat strip claims MUST have moved the drawn output
    assert 3 in la and 9 in lb, (
        "P3 headcount did not follow hud.headcount: %r vs %r" % (la, lb))
    assert "$123.45" in la, "P3 revenue did not follow hud.revenue_cents: %r" % la
    assert "$0.01" in lb, "P3 revenue did not follow hud.revenue_cents: %r" % lb
    assert "$0.14/day" in la, (
        "P3 burn is not the rate moneyRate() renders: %r" % la)
    assert "$0.00/day" in lb, "P3 burn did not follow the payload: %r" % lb
    # the dead-panel discriminator: the two drawings differ
    assert la != lb, "the stat strip rendered identically for two hud payloads"

    # the two documented edges, on the same fields
    assert "unknown" in lb and "nothing is burning yet" in lb, (
        "a null runway_days must read 'unknown' + the hint, never 0/Infinity")
    assert "700" in " ".join(str(x) for x in la), (
        "a derived runway did not follow hud.runway_days: %r" % la)
    # no MONEY figure grew a second decimal point (the burn is the risk case)
    for leaf in la + lb:
        s = str(leaf)
        if s.startswith("$"):
            assert s.count(".") == 1, (
                "a HUD money figure grew a second decimal point: %r" % leaf)


@pytest.mark.skipif(NODE is None, reason="node is not on PATH")
def test_p5_inspector_renders_from_the_selected_character():
    """THE dead-panel check for P5.

    The inspector is fed two different `pick` characters and must draw their
    fields: slug, seat_type, animation (+ walking suffix), the stalled line and
    the last-event line. A constant inspector fails the difference check.

    The renderer's selection contract is an **agent id**, not the character
    object: `qubit-office.js` documents it (`pick` holds an id) so the inspector
    derives from the CURRENT snapshot — a stored object pinned the panel to the
    revision it was clicked in. The harness therefore has to do what a real
    click does: put the character in `view.data.characters` and pass its
    `agent_id` as `pick`. Passing the object itself (the pre-`0c26cb2c6`
    contract) renders "That agent is no longer in the tower." and asserts
    against the empty-selection branch, not the inspector.
    """
    src = ENTRY.read_text()

    one = {"agent_id": 1, "slug": "wk-0", "seat_type": "worker",
           "department_id": None, "animation": "working", "walking": False,
           "at": "desk", "stalled": False,
           "position": {"floor": 1, "room": "desks", "room_id": 1, "slot": 0,
                        "x": 1, "y": 2},
           "last_event": {"kind": "skill_used", "age_s": 12.4}}
    two = {"agent_id": 2, "slug": "mgr", "seat_type": "manager",
           "department_id": 4, "animation": "walking", "walking": True,
           "at": "walking", "stalled": True,
           "position": {"floor": 2, "room": "ops", "room_id": 2, "slot": 1,
                        "x": 3, "y": 4},
           "last_event": {"kind": "run_started", "age_s": 130.0}}

    view_one = {"loading": False, "error": None,
                "data": _payload(chars=[one])}
    view_two = {"loading": False, "error": None,
                "data": _payload(chars=[two])}
    l1 = _render(src, view_one, pick=one["agent_id"])["leaves"]
    l2 = _render(src, view_two, pick=two["agent_id"])["leaves"]

    assert "wk-0" in l1 and "mgr" in l2, (
        "P5 inspector did not follow the selected slug: %r vs %r" % (l1, l2))
    assert any("worker" in str(x) for x in l1), (
        "P5 role did not follow seat_type: %r" % l1)
    # the '(walking)' suffix is driven by .walking, not the animation
    assert any("(walking)" in str(x) for x in l2), (
        "P5 did not append '(walking)' from characters[].walking: %r" % l2)
    assert not any("(walking)" in str(x) for x in l1), (
        "P5 appended '(walking)' for a character with walking:false: %r" % l1)
    # the stalled line + last-event line come from the payload
    assert any("STALLED" in str(x) for x in l2), (
        "P5 did not draw the stalled line from characters[].stalled: %r" % l2)
    assert not any("STALLED" in str(x) for x in l1), (
        "P5 drew a stalled line for a character with stalled:false: %r" % l1)
    assert any("skill_used" in str(x) for x in l1), (
        "P5 last-event line did not follow last_event.kind: %r" % l1)
    assert any("run_started" in str(x) for x in l2), (
        "P5 last-event line did not follow last_event.kind: %r" % l2)
    assert l1 != l2, "the inspector rendered identically for two characters"


@pytest.mark.skipif(NODE is None, reason="node is not on PATH")
def test_p4_meter_inputs_are_payload_fields_and_no_bar_is_faked():
    """P4's meters read payload fields, never a client clock (C7).

    The spec's P4 is three bars. The renderer today draws **no** freshness or
    progress bar — `active_window_s` reaches the DOM only through the inspector's
    STALLED line, and the walk bob (in `drawAgent`) is the only consumer of
    `position.progress`. So the honest assertions are:

      * the fields P4 names ARE in the payload the page is fed
        (`active_window_s`, `last_event.age_s`, `position.progress`, `stalled`);
      * the inspector draws a `120`s reference ONLY when `stalled` is true
        (proving the active_window_s read is payload-driven, not a constant);
      * no bar is fabricated for a null `last_event` or an absent `progress`.

    If a future renderer adds real meter bars, the last two rows flip and this
    test must be updated with the spec — which is the point of pinning it.

    Like P5, the harness selects by **agent id** (the renderer's contract since
    the selection moved to an id resolved against the live snapshot): the drawn
    character is in `view.data.characters` and `pick` is its `agent_id`.
    """
    src = ENTRY.read_text()

    def leaves_for(ch):
        """Leaves for the inspector with `ch` selected the way a click does."""
        view = {"loading": False, "error": None, "data": _payload(chars=[ch])}
        return " ".join(str(x) for x in
                        _render(src, view, pick=ch["agent_id"])["leaves"])

    base = {"agent_id": 1, "slug": "wk-0", "seat_type": "worker",
            "department_id": None, "animation": "working", "walking": False,
            "at": "desk", "stalled": False,
            "position": {"floor": 1, "room": "desks", "room_id": 1, "slot": 0,
                         "x": 1, "y": 2},
            "last_event": {"kind": "skill_used", "age_s": 0.0}}

    # (1) the P4 freshness inputs reach the DOM through the payload
    assert "120" in leaves_for({**base, "stalled": True}), (
        "active_window_s did not reach the inspector even when a character is "
        "stalled — the freshness window is not read from the payload")
    assert "120" not in leaves_for(base), (
        "active_window_s was drawn for a NON-stalled character — the metric "
        "does not track `stalled`, so it is a constant, not the payload")

    # (2) a null last_event draws the explicit 'no event on record' line, and
    #     NO invented freshness value
    no_event = {**base, "last_event": None}
    joined = leaves_for(no_event)
    assert "no event on record" in joined, (
        "a null last_event must render the empty line, never a made-up value")
    assert "age_s" not in joined and "null" not in joined.lower()

    # (3) the progress meter renders NOTHING today: with progress present in the
    #     payload the inspector still draws no progress bar (the spec says so).
    with_prog = {**base,
                 "position": {**base["position"], "progress": 0.5}}
    assert "progress" not in leaves_for(with_prog).lower(), (
        "a progress bar appeared in the inspector — the spec says P4's progress "
        "meter renders nothing; if it now renders, update 28-HUD-PANELS.md and "
        "this test deliberately")


# ===================================================================== #
#  ASSERTION 4 — the HUD numbers come from the ledger                   #
# ===================================================================== #
def test_the_hud_values_equal_the_ledger_for_the_same_store(
        tmp_path, monkeypatch):
    """P3's numbers are the LEDGER's fold, to the cent.

    The stat strip reads the payload's `hud`; the payload's `hud` must equal
    what `ledger.pnl` / `ledger.health` report over the same store. If the HUD
    ever drifted to a separately computed number, this fails — the HUD cannot
    disagree with the books.
    """
    from voicebridge.office import ledger as L, tower

    cfg = _cfg(tmp_path, monkeypatch)
    _real_org(cfg)

    hud = tower.state(cfg, now=tower.store.now())["hud"]
    org = L.pnl(cfg, scope="org")
    health = L.health(cfg)

    assert hud["revenue_cents"] == org["revenue_cents"] == 12_345
    assert hud["cost_cents"] == org["cost_cents"] == 2_345
    assert hud["net_cents"] == org["net_cents"] == 10_000

    # health() reports the SAME net over the same store (C2: derived, no rollup)
    assert health["net_cents"] == hud["net_cents"], (
        "the HUD net disagrees with ledger.health: %r vs %r"
        % (hud["net_cents"], health["net_cents"]))

    # the per-floor rows add back up to the org, so P1 and P3 cannot disagree
    assert sum(f["revenue_cents"] for f in hud["floors"]) == hud["revenue_cents"]
    assert sum(f["cost_cents"] for f in hud["floors"]) == hud["cost_cents"]
    assert sum(f["net_cents"] for f in hud["floors"]) == hud["net_cents"]

    # headcount is the store's own count, and equals the characters drawn
    chars = tower.state(cfg, now=tower.store.now())["characters"]
    assert hud["headcount"] == len(chars)


def test_the_hud_burn_and_runway_agree_with_the_survival_window(
        tmp_path, monkeypatch):
    """The burn is the ledger's inference over the survival window, and the
    runway is derived from it — the same is_derived rule the spec states."""
    from voicebridge.office import ledger as L, survival as SV, tower

    cfg = _cfg(tmp_path, monkeypatch)
    _real_org(cfg)

    hud = tower.state(cfg, now=tower.store.now())["hud"]
    # burn = metered inference in the window / window days; one 2345c row now
    assert hud["burn_cents_per_day"] == round(2345 / SV.BURN_WINDOW_DAYS, 4), (
        "hud.burn_cents_per_day does not trace to the ledger's inference rows")
    assert hud["runway_is_derived"] is (hud["runway_days"] is not None), (
        "runway_is_derived must be true exactly when runway_days is not null")
    # no unmetered leakage into the burn (D16)
    L.record(cfg, "inference", 9_999, unmetered=True)
    hud2 = tower.state(cfg, now=tower.store.now())["hud"]
    assert hud2["burn_cents_per_day"] == hud["burn_cents_per_day"], (
        "an unmetered row leaked into the HUD burn (D16)")


# ===================================================================== #
#  ASSERTION 5 — position.progress resolves for a walking character     #
# ===================================================================== #
def test_position_progress_resolves_when_a_character_is_mid_walk(
        tmp_path, monkeypatch):
    """Gap N11, now fixed: `state()` carries the walk's `progress` through.

    A real mid-walk character (an event NOW whose kind maps to a station, aged
    halfway through `WALK_S`) must arrive with `characters[].position.progress`
    present and strictly in (0, 1). This is the ONLY permitted source of the
    renderer's walk bob (C7), so its absence is the `position.progress` failure
    the whole spec is about.
    """
    from voicebridge.office import tower

    cfg = _cfg(tmp_path, monkeypatch)
    org = _real_org(cfg)
    st = tower.state(cfg, now=org["now"])

    walker = next(c for c in st["characters"] if c["agent_id"] == org["worker"])
    pos = walker["position"]
    assert "progress" in pos, (
        "state() dropped `position.progress` again — the walk bob reads it and "
        "drew nothing while every producer-level test stayed green")
    assert isinstance(pos["progress"], (int, float)), pos["progress"]
    assert 0.0 < pos["progress"] < 1.0, (
        "a mid-walk progress must be strictly between 0 and 1: %r"
        % pos["progress"])
    assert walker["animation"] == "walking" and walker["walking"] is True

    # the field resolves through the SPEC PATH against a REAL payload
    assert _resolve(st, "characters[].position.progress")[0], (
        "characters[].position.progress does not resolve against a real "
        "mid-walk payload")


def test_progress_is_absent_on_a_character_that_is_not_walking(
        tmp_path, monkeypatch):
    """The converse, so a stale value cannot masquerade as a live walk."""
    from voicebridge.office import tower

    cfg = _cfg(tmp_path, monkeypatch)
    org = _real_org(cfg)
    st = tower.state(cfg, now=org["now"])
    mgr = next(c for c in st["characters"] if c["agent_id"] == org["manager"])
    assert mgr["walking"] is False
    assert "progress" not in mgr["position"], (
        "a non-walking character carried a `progress` — a leftover value "
        "pretending to be live")
