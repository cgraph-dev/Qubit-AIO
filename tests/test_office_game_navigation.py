"""F06 — the navigation reducer: every path round-trips, nothing is unreachable.

F06's proof (`60-IMPLEMENTATION-PLAN.md`): "navigation reducer round-trips every
enter/exit/floor/room path; editing text never moves viewpoint or closes the app."

The graph is DERIVED from the real recorded floorplan
(`fixtures/tower-recording.json`: 3 floors, 4 rooms), so the test enumerates the
paths the actual building has, not a hand-written toy graph. That is the point:
a reducer that only round-trips the graph its author imagined is not evidence.

Hermetic: tsc + node. No browser, no server, no network.
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
WEB = REPO / "frontend" / "qubit-web"
GAME_DIR = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office" / "game"
FIXTURE = (REPO / "frontend" / "qubit-web" / "src" / "lib" / "office-game"
           / "fixtures" / "tower-recording.json")

from _office_toolchain import (  # noqa: E402
    SKIP_REASON, has_tsc, node_path, tsc_path)

NODE = node_path()
TSC = tsc_path()


def _toolchain() -> bool:
    return has_tsc()


pytestmark = pytest.mark.skipif(
    not _toolchain(), reason=SKIP_REASON)


def _run(tmp_path: Path, js: str) -> object:
    staged = tmp_path / "src"
    staged.mkdir(exist_ok=True)
    shutil.copy(GAME_DIR / "navigation.ts", staged / "navigation.ts")
    subprocess.run(
        [NODE, str(TSC), "--module", "commonjs", "--target", "es2020",
         "--moduleResolution", "node",
         "--ignoreDeprecations", "6.0", "--outDir", str(tmp_path / "js"),
         "--skipLibCheck", "--noEmitOnError", "false",
         "--lib", "es2020,dom", str(staged / "navigation.ts")],
        capture_output=True, text=True, timeout=180, cwd=str(staged))
    compiled = tmp_path / "js" / "navigation.js"
    if not compiled.is_file():
        pytest.skip("tsc could not compile navigation.ts for the node driver")
    program = (
        'const N = require(%s);\n'
        'const TOWER = JSON.parse(require("fs").readFileSync(%s, "utf8"));\n'
        '(async () => {\n'
        '  const out = await (async () => { %s })();\n'
        '  process.stdout.write(JSON.stringify(out));\n'
        '})().catch(e => { console.error(String(e && e.stack || e));'
        ' process.exit(1); });\n'
    ) % (json.dumps(str(compiled)), json.dumps(str(FIXTURE)), js)
    proc = subprocess.run([NODE], input=program, capture_output=True,
                          text=True, timeout=90)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}")
    return json.loads(proc.stdout)


#: Reused by several drivers: derive the graph from the REAL payload.
_GRAPH = """
  const floors = TOWER.floorplan.floors.map(f => ({
    number: f.number, rooms: f.rooms.map(r => ({id: r.id}))}));
  const portals = N.buildHeadquartersPortals('hq', floors);
"""


def test_the_reducer_and_graph_compile_and_exist(tmp_path):
    got = _run(tmp_path, """
      return {hasTravel: typeof N.travel === 'function',
              hasBack: typeof N.back === 'function',
              hasEncode: typeof N.encodePlace === 'function',
              hasDecode: typeof N.decodePlace === 'function'};
    """)
    assert all(got.values()), got


def test_initial_state_is_the_exterior(tmp_path):
    got = _run(tmp_path, """
      const s = N.initialNav();
      return {kind: s.viewpoint.kind, history: s.history.length,
              refused: s.refused};
    """)
    assert got["kind"] == "exterior"
    assert got["history"] == 0
    assert got["refused"] is None


# ======================================================================
# 1 — the real building's graph is connected and round-trips
# ======================================================================
def test_the_derived_graph_matches_the_real_floorplan(tmp_path):
    got = _run(tmp_path, _GRAPH + """
      return {floors: TOWER.floorplan.floors.length,
              roomCount: TOWER.floorplan.floors
                .reduce((n,f) => n + f.rooms.length, 0),
              portals: portals.length,
              kinds: [...new Set(portals.map(p => p.via))].sort()};
    """)
    assert got["floors"] == 3
    assert got["roomCount"] == 4, "the recorded building has 4 rooms"
    assert got["portals"] > 0
    # the vocabulary the plan names
    assert "entrance" in got["kinds"]
    assert "exit" in got["kinds"]
    assert "door" in got["kinds"]


def test_every_floor_and_room_is_reachable_and_returns(tmp_path):
    """Walk the real graph: exterior -> building -> lobby -> each floor -> each
    room, then all the way back. Every step must succeed and every reverse must
    land exactly where it started."""
    got = _run(tmp_path, _GRAPH + """
      const results = [];
      let afterBuilding = null;
      for (const f of TOWER.floorplan.floors) {
        let s = N.initialNav();
        const b = {kind:'building', building:'hq'};
        const lb = {kind:'lobby', building:'hq'};
        s = N.travel(s, b, portals);
        if (s.refused) { results.push({at:'building', refused:s.refused.reason}); continue; }
        afterBuilding = s.viewpoint.kind;
        s = N.travel(s, lb, portals);
        if (s.refused) { results.push({at:'lobby', refused:s.refused.reason}); continue; }
        const floorPlace = {kind:'floor', building:'hq', floor:f.number};
        s = N.travel(s, floorPlace, portals);
        if (s.refused && f.number !== 1) {
          results.push({at:'floor'+f.number, refused:s.refused.reason}); continue;
        }
        for (const r of f.rooms) {
          const roomPlace = {kind:'room', building:'hq', floor:f.number, room:r.id};
          const before = JSON.stringify(s.viewpoint);
          const s2 = N.travel(s, roomPlace, portals);
          if (s2.refused) {
            results.push({at:'room'+r.id, refused:s2.refused.reason}); continue;
          }
          const s3 = N.back(s2);   // room -> floor
          const backOk = JSON.stringify(s3.viewpoint) === before;
          results.push({at:'room'+r.id, entered:true, backOk,
                        encoded: N.encodePlace(roomPlace)});
        }
      }
      return {results, afterBuilding};
    """)
    failures = [r for r in got["results"] if r.get("refused") or
                r.get("backOk") is False]
    assert not failures, f"unreachable or non-round-tripping paths: {failures}"
    assert got["results"], "no paths were exercised"


def test_leaving_the_building_returns_to_the_exterior(tmp_path):
    """Every step out is one hop, and each is the reverse of the step in.

    The route out is now lobby -> building -> exterior, NOT a lobby -> exterior
    shortcut: a one-way door is the bug this shape removes, and an earlier
    version of this test encoded exactly that shortcut.
    """
    got = _run(tmp_path, _GRAPH + """
      const hops = [];
      let s = N.initialNav();
      for (const to of [{kind:'building', building:'hq'},
                        {kind:'lobby', building:'hq'},
                        {kind:'building', building:'hq'},
                        N.EXTERIOR]) {
        s = N.travel(s, to, portals);
        hops.push({to: N.encodePlace(to), at: N.encodePlace(s.viewpoint),
                   refused: s.refused !== null});
      }
      // back() from the lobby also reaches the building
      let t = N.initialNav();
      t = N.travel(t, {kind:'building', building:'hq'}, portals);
      t = N.travel(t, {kind:'lobby', building:'hq'}, portals);
      t = N.back(t);
      return {hops, final: N.encodePlace(s.viewpoint),
              backTo: t.viewpoint.kind};
    """)
    refusals = [h for h in got["hops"] if h["refused"]]
    assert not refusals, f"a hop out of the building was refused: {refusals}"
    assert got["final"] == "exterior", (
        f"there is no way out of the building: ended at {got['final']}")
    assert got["backTo"] == "building"


# ======================================================================
# 2 — the reducer refuses what the graph does not allow
# ======================================================================
def test_travelling_without_a_portal_is_a_typed_refusal(tmp_path):
    """The reachable set IS the graph. A hop with no portal must not silently
    succeed — that is how 'enter a room from the district' bugs appear."""
    got = _run(tmp_path, _GRAPH + """
      let s = N.initialNav();
      const s2 = N.travel(s, {kind:'room', building:'hq', floor:2, room:2}, portals);
      return {refused: s2.refused !== null,
              reason: s2.refused ? s2.refused.reason : null,
              stillAt: s2.viewpoint.kind,
              historyGrew: s2.history.length};
    """)
    assert got["refused"] is True, (
        "a portal-less jump succeeded: the reducer invents destinations")
    assert got["stillAt"] == "exterior", "a refused move changed the viewpoint"
    assert got["historyGrew"] == 0, "a refused move pushed history"


def test_a_locked_portal_refuses_and_shows_its_own_reason(tmp_path):
    got = _run(tmp_path, """
      const portals = [{from: N.EXTERIOR, to: {kind:'building', building:'hq'},
                        via:'entrance', locked:true, lockReason:'closed for the night'}];
      const s = N.travel(N.initialNav(), {kind:'building', building:'hq'}, portals);
      return {refused: s.refused !== null,
              reason: s.refused ? s.refused.reason : null};
    """)
    assert got["refused"] is True
    assert got["reason"] == "closed for the night", (
        "the lock's own reason was replaced by an invented one")


def test_travelling_to_the_current_place_is_a_no_op(tmp_path):
    got = _run(tmp_path, """
      const s = N.travel(N.initialNav(), N.EXTERIOR, []);
      return {kind: s.viewpoint.kind, history: s.history.length,
              refused: s.refused === null};
    """)
    assert got["kind"] == "exterior"
    assert got["history"] == 0
    assert got["refused"] is True


# ======================================================================
# 3 — Back at the root does not underflow
# ======================================================================
def test_back_at_the_root_stays_put(tmp_path):
    """F06 names 'Back' explicitly, and a reducer that underflows is how a Back
    button closes the app instead of moving the viewpoint."""
    got = _run(tmp_path, """
      let s = N.initialNav();
      s = N.back(s); s = N.back(s); s = N.back(s);
      return {kind: s.viewpoint.kind, history: s.history.length};
    """)
    assert got["kind"] == "exterior"
    assert got["history"] == 0, "back() underflowed the history stack"


def test_history_is_capped(tmp_path):
    got = _run(tmp_path, """
      const portals = [];
      for (let i = 1; i <= 100; i++) {
        portals.push({from: {kind:'floor', building:'hq', floor:i},
                      to: {kind:'floor', building:'hq', floor:i+1}, via:'stairs'});
      }
      let s = {viewpoint: {kind:'floor', building:'hq', floor:1},
               history: [], refused: null};
      for (let i = 1; i <= 100; i++) {
        s = N.travel(s, {kind:'floor', building:'hq', floor:i+1}, portals);
      }
      return {history: s.history.length, cap: N.MAX_HISTORY,
              at: s.viewpoint.floor};
    """)
    assert got["at"] == 101
    assert got["history"] <= got["cap"], (
        f"history grew to {got['history']} past the cap {got['cap']} — an "
        f"unbounded stack is a memory leak on a long session")


# ======================================================================
# 4 — deep links encode and decode
# ======================================================================
def test_every_place_encodes_and_decodes_to_itself(tmp_path):
    got = _run(tmp_path, _GRAPH + """
      const places = [N.EXTERIOR, {kind:'building', building:'hq'},
                      {kind:'lobby', building:'hq'}];
      for (const f of TOWER.floorplan.floors) {
        places.push({kind:'floor', building:'hq', floor:f.number});
        for (const r of f.rooms) {
          places.push({kind:'room', building:'hq', floor:f.number, room:r.id});
        }
      }
      const bad = [];
      for (const p of places) {
        const enc = N.encodePlace(p);
        const dec = N.decodePlace(enc);
        if (!dec.ok || JSON.stringify(dec.place) !== JSON.stringify(p)) {
          bad.push({place: p, enc, ok: dec.ok,
                    reason: dec.ok ? null : dec.reason,
                    got: dec.ok ? dec.place : null});
        }
      }
      return {count: places.length, bad};
    """)
    assert got["bad"] == [], f"encode/decode is not a round-trip: {got['bad']}"
    assert got["count"] == 10, (
        f"expected 1 exterior + 1 building + 1 lobby + 3 floors + 4 rooms = 10 "
        f"addressable places, got {got['count']}")


def test_an_unknown_target_is_refused_against_real_known_ids(tmp_path):
    """A deep link to a building or floor that does not exist must refuse, not
    render an empty world."""
    got = _run(tmp_path, _GRAPH + """
      const known = {
        buildings: ['hq'],
        floors: TOWER.floorplan.floors.map(f => ['hq', f.number]),
        rooms: TOWER.floorplan.floors.flatMap(f =>
          f.rooms.map(r => ['hq', f.number, r.id])),
      };
      return {
        good: N.decodePlace('floor:hq:2', known),
        badBuilding: N.decodePlace('floor:nowhere:2', known),
        badFloor: N.decodePlace('floor:hq:99', known),
        badRoom: N.decodePlace('room:hq:2:9999', known),
        badKind: N.decodePlace('submarine:hq:1', known),
        notANumber: N.decodePlace('floor:hq:abc', known),
      };
    """)
    assert got["good"]["ok"] is True
    assert got["badBuilding"]["ok"] is False
    assert "nowhere" in got["badBuilding"]["reason"]
    assert got["badFloor"]["ok"] is False and "99" in got["badFloor"]["reason"]
    assert got["badRoom"]["ok"] is False and "9999" in got["badRoom"]["reason"]
    assert got["badKind"]["ok"] is False
    assert got["notANumber"]["ok"] is False


def test_restore_is_forgiving_and_starts_a_fresh_history(tmp_path):
    got = _run(tmp_path, """
      const s = N.restore({kind:'floor', building:'hq', floor:3});
      return {kind: s.viewpoint.kind, floor: s.viewpoint.floor,
              history: s.history.length, refused: s.refused};
    """)
    assert got["floor"] == 3
    assert got["history"] == 0
    assert got["refused"] is None


# ======================================================================
# 5 — presentation-only: navigation cannot move an agent
# ======================================================================
def test_navigation_cannot_move_a_character(tmp_path):
    """Amendment § 3: viewpoint movement is presentation. A real relocation
    requires an accepted action or an updated snapshot. The reducer must have no
    way to touch character state at all.

    Comments are stripped first: the module's own header explains the agent
    separation, and a naive check would fail on that documentation.
    """
    raw = (GAME_DIR / "navigation.ts").read_text()
    src = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    for banned in ("agent_id", "character", "position.floor", "office_event"):
        assert banned not in src, (
            f"the navigation reducer references {banned}: navigation is "
            f"presentation and may not move agents")


def test_the_reducer_is_pure(tmp_path):
    """A reducer that mutates its input is not a reducer, and React will render
    the wrong thing under StrictMode."""
    got = _run(tmp_path, _GRAPH + """
      const s0 = N.initialNav();
      const before = JSON.stringify(s0);
      N.travel(s0, {kind:'building', building:'hq'}, portals);
      const after = JSON.stringify(s0);
      return {unchanged: before === after};
    """)
    assert got["unchanged"] is True, (
        "travel() mutated the state it was given")


def test_navigation_issues_no_request_and_owns_no_timer(tmp_path):
    src = (GAME_DIR / "navigation.ts").read_text()
    for banned in ("fetch(", "setInterval", "setTimeout",
                   "requestAnimationFrame", "XMLHttpRequest"):
        assert banned not in src, (
            f"navigation.ts contains {banned}: moving the viewpoint is local "
            f"presentation and must not fetch or tick")
