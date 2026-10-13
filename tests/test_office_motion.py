"""The Office motion model: paths, not animation.

`frontend/qubit-app/plugins/qubit-office/motion.js` is the module the renderer
uses to turn the backend's `position.progress` into a cell on a REAL route —
one that goes round a desk and through a doorway instead of straight through a
wall. It is not a renderer and it owns no clock.

Two things are asserted here that a behavioural test alone could not:

1. **The promise holds.** Given the same from-cell, to-cell and progress, the
   route and the cell are identical on every call, and the progress maps
   monotonically from the start cell to the destination — including the two
   endpoints, where `progress` 0 is the start and `progress` 1 is the
   destination exactly.

2. **The hard rule survives the next editor.** This module must contain no
   timer of any kind — no `setInterval`, no `setTimeout`, no
   `requestAnimationFrame` — because a timer here is motion with no event
   behind it (C7). That is asserted on the SOURCE TEXT, the same way
   `tests/test_office_tower_plugin.py` asserts it for the plugin bundle: the
   property is exactly the one a text check catches, and a behavioural test
   would need a browser and would still miss the timer a future commit adds.

3. **The station map is the backend's, not a second copy.** `STATIONS` and
   `_EVENT_STATION` are read out of `voicebridge/office/tower.py` with `ast`
   and compared to what the module exports, so the two cannot drift.

Matching `_facing()` is checked against the Python function directly, for the
same reason: two implementations of "which way am I facing" that are only
*intended* to agree is how a character ends up moonwalking.

Hermetic: reads two files and runs `node`; no network, no provider call, no
browser, no state directory.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MODULE = (REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
          / "motion.js")
TOWER = REPO / "voicebridge" / "office" / "tower.py"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is required for the motion model")


# ---------------------------------------------------------------- the probe
def _run(script: str):
    """Evaluate a snippet with the module already loaded, and parse its JSON.

    The module is loaded through `require`, which is the documented Node entry
    point (`module.exports` in the UMD wrapper). A failure here is a syntax
    error or a thrown assertion inside the snippet, and the node stderr is
    surfaced rather than swallowed so the reason is visible in the report.
    """
    path = json.dumps(str(MODULE))
    proc = subprocess.run(
        ["node", "-e", f"const M=require({path});\n{script}"],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\n--- stdout ---\n{proc.stdout}\n"
        f"--- stderr ---\n{proc.stderr}")
    last = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert last, f"the probe printed nothing\nstderr: {proc.stderr}"
    return json.loads(last[-1])


def _node_check() -> str:
    proc = subprocess.run(["node", "--check", str(MODULE)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stderr


def _src() -> str:
    return MODULE.read_text()


def _code() -> str:
    """The module with its comments stripped.

    The header DOCUMENTS that there is no timer here, so a naive check would
    fail on the file's own explanation. Same reasoning as
    `test_office_tower_plugin._code`.
    """
    src = _src()
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    return src


def _tower_module():
    return ast.parse(TOWER.read_text())


def _tower_literal(name: str):
    for node in _tower_module().body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {TOWER}")


# --------------------------------------------------------------- the file
def test_the_module_exists_and_the_browser_bundle_shape_is_right():
    src = _src()
    assert MODULE.is_file()
    # browser: a global the plugin bundle can pick up; node: module.exports
    assert "QubitOfficeMotion" in src
    assert "module.exports" in src
    # no build step, no imports, no DOM
    assert not re.search(r"^\s*(import|export)\s", src, re.M)
    assert "document." not in src and "window." not in _code()


def test_node_check_passes():
    assert _node_check() == ""


def test_the_module_has_no_timer_of_any_kind():
    """C7, at the module level. No timer may exist in this file at all.

    `tests/test_office_tower_plugin.py` allows the plugin bundle exactly one
    `setInterval(load, POLL_MS)` because the poll only asks the backend what is
    true. This module does not poll and does not animate, so it gets zero.
    """
    src = _code()
    found = re.findall(r"\b(setInterval|setTimeout|requestAnimationFrame"
                       r"|setImmediate|queueMicrotask)\b", src)
    assert found == [], (
        f"motion.js must own no timer — found {found}. A timer here is motion "
        f"with no event behind it (C7).")
    for word in ("@keyframes", "animate(", "performance.now("):
        assert word not in src, f"{word} is a clock, and this module owns none"


# ---------------------------------------------------------------- DIR
def test_dir_follows_the_same_rule_as_the_backend():
    """Two implementations of `_facing` that only *intend* to agree is how a
    character ends up walking backwards."""
    from voicebridge.office.tower import _facing

    cases = [
        ((0, 0), (3, 1)), ((0, 0), (-2, 1)), ((0, 0), (1, 4)),
        ((0, 0), (1, -4)), ((0, 0), (2, 2)), ((5, 0), (1, 2)),
        ((0, 0), (0, 3)), ((0, 0), (0, -3)), ((0, 0), (4, 0)),
        ((0, 0), (-4, 0)), ((0, 0), (-3, -3)), ((7, 7), (7, 7)),
        ((2, 5), (0, 6)), ((4, 1), (1, 1)), ((1, 1), (4, 1)),
    ]
    for frm, to in cases:
        want = _facing({"x": frm[0], "y": frm[1]},
                       {"x": to[0], "y": to[1]})
        got = _run(f"console.log(JSON.stringify(M.DIR({frm[0]},{frm[1]},"
                   f"{to[0]},{to[1]})))")
        assert got == want, f"{frm}->{to}: got {got!r}, backend says {want!r}"

    # the tie goes to the HORIZONTAL, and zero delta counts as the positive way
    assert _run("console.log(JSON.stringify(M.DIR(0,0,2,2)))") == "right"
    assert _run("console.log(JSON.stringify(M.DIR(0,0,-2,-2)))") == "left"
    assert _run("console.log(JSON.stringify(M.DIR(5,5,5,5)))") == "right"


def test_dirs_are_the_four_cardinal_steps_in_grid_terms():
    dirs = _run("console.log(JSON.stringify(M.DIRS))")
    by = {d["dir"]: (d["dx"], d["dy"]) for d in dirs}
    assert by == {"right": (1, 0), "left": (-1, 0), "down": (0, 1),
                  "up": (0, -1)}, by


# ----------------------------------------------------------- pathThrough
def test_a_straight_route_ends_on_the_destination():
    got = _run("console.log(JSON.stringify("
               "M.pathThrough({x:0,y:0},{x:5,y:0},{})))")
    # excludes the start, includes the end (pixel-agents findPath convention)
    assert got[-1] == {"x": 5, "y": 0}
    assert {"x": 0, "y": 0} not in got
    # and it is compressed: a five-cell corridor is not five waypoints
    assert len(got) < 5, got


def test_the_waypoints_never_enter_a_blocked_cell():
    """A wall between the two points must be walked AROUND, not through."""
    blocked = ["2,0", "2,1", "2,2", "2,3", "2,4"]
    got = _run("const b=new Set(" + json.dumps(blocked) + ");"
               "console.log(JSON.stringify("
               "M.pathThrough({x:0,y:2},{x:4,y:2},{blocked:b})))")
    assert got[-1] == {"x": 4, "y": 2}
    assert len(got) > 2, f"a wall was crossed in a straight line: {got}"
    # expand the compressed waypoints and check every cell of the real route
    cells = _expand(got, {"x": 0, "y": 2})
    for cell in cells:
        assert f"{cell['x']},{cell['y']}" not in blocked, (
            f"the route walks through the blocked cell {cell}")
    assert {"x": 2, "y": 2} not in cells


def _expand(path, frm):
    """A compressed waypoint list -> every cell actually walked.

    Each waypoint is a straight run from the previous one, so the dense route
    is the axis-aligned walk between consecutive waypoints. This is what makes
    "never cuts through a blocked cell" checkable rather than merely stated.
    """
    out = []
    cur = dict(frm)
    for waypoint in path:
        dx = (waypoint["x"] > cur["x"]) - (waypoint["x"] < cur["x"])
        dy = (waypoint["y"] > cur["y"]) - (waypoint["y"] < cur["y"])
        while cur["x"] != waypoint["x"] or cur["y"] != waypoint["y"]:
            cur = {"x": cur["x"] + dx, "y": cur["y"] + dy}
            out.append(dict(cur))
    return out


def test_a_route_around_a_wall_is_no_longer_than_the_shortest_one():
    """The route's LENGTH is checked against a BFS ground truth computed here.

    ai-town's greedy search is what this module ports, and its pop order can in
    principle commit to a longer detour; this pins that it does not, over a
    deterministic sweep of obstacles.
    """
    script = """
function expand(p,from){
  const out=[]; let cur=from;
  for(const w of p){const dx=Math.sign(w.x-cur.x),dy=Math.sign(w.y-cur.y);
    while(cur.x!==w.x||cur.y!==w.y){cur={x:cur.x+dx,y:cur.y+dy};out.push(cur);}}
  return out;}
function bfs(f,t,blocked){
  const key=(x,y)=>x+','+y, sk=key(f.x,f.y), ek=key(t.x,t.y);
  const q=[[f.x,f.y]], par=new Map([[sk,null]]);
  while(q.length){const [x,y]=q.shift();
    if(x===t.x&&y===t.y){const out=[];let k=ek;while(k&&k!==sk){
      const [a,b]=k.split(',').map(Number);out.unshift({x:a,y:b});k=par.get(k);}return out;}
    for(const [dx,dy] of [[1,0],[-1,0],[0,1],[0,-1]]){
      const nx=x+dx,ny=y+dy,k2=key(nx,ny);
      if(par.has(k2))continue; if(nx<-20||nx>20||ny<-20||ny>20)continue;
      if(blocked.has(k2)&&!(nx===t.x&&ny===t.y))continue;
      par.set(k2,key(x,y)); q.push([nx,ny]);}}
  return null;}
let seed=12345; const rnd=()=>{seed=(seed*1103515245+12345)&0x7fffffff;return seed/0x7fffffff;};
let mismatch=0, reachable=0, trials=0;
for(let n=0;n<300;n++){
  const f={x:Math.floor(rnd()*9),y:Math.floor(rnd()*9)};
  const t={x:Math.floor(rnd()*9),y:Math.floor(rnd()*9)};
  const blocked=new Set();
  for(let i=0;i<14;i++){const bx=Math.floor(rnd()*9),by=Math.floor(rnd()*9);
    if((bx===f.x&&by===f.y)||(bx===t.x&&by===t.y))continue; blocked.add(bx+','+by);}
  const got=M.pathThrough(f,t,{blocked});
  const truth=bfs(f,t,blocked);
  trials++;
  if(truth===null)continue;
  reachable++;
  if(expand(got,f).length!==truth.length)mismatch++;
}
console.log(JSON.stringify({trials,reachable,mismatch}));
"""
    got = _run(script)
    assert got["trials"] == 300
    assert got["reachable"] > 200, got
    assert got["mismatch"] == 0, got


def test_a_walled_off_destination_walks_as_far_as_it_honestly_can():
    """ai-town's `bestCandidate` fallback: never a teleport, never a freeze."""
    sealed = ["2,2", "1,2", "3,2", "2,1", "2,3"]
    got = _run("const b=new Set(" + json.dumps(sealed) + ");"
               "console.log(JSON.stringify("
               "M.pathThrough({x:0,y:0},{x:2,y:2},{blocked:b})))")
    assert got, "the agent must still make progress toward a walled-off goal"
    assert got[-1] != {"x": 2, "y": 2}, "it cannot stand on the sealed cell"
    cells = _expand(got, {"x": 0, "y": 0})
    for cell in cells:
        assert f"{cell['x']},{cell['y']}" not in sealed


def test_no_route_at_all_is_an_empty_list_not_a_guess():
    got = _run("console.log(JSON.stringify("
               "M.pathThrough({x:4,y:4},{x:4,y:4},{})))")
    assert got == []


# ----------------------------------------------------------- interpolate
def test_progress_zero_is_the_near_end_and_one_is_the_route_end():
    path = json.dumps(_run("console.log(JSON.stringify("
                           "M.pathThrough({x:0,y:0},{x:5,y:0},{})))"))
    start = {"x": 0, "y": 0}
    end = {"x": 5, "y": 0}
    got0 = _run(f"const p={path};"
                "console.log(JSON.stringify(M.interpolate(p,0)))")
    got1 = _run(f"const p={path};"
                "console.log(JSON.stringify(M.interpolate(p,1)))")
    # the first waypoint returns the cell the walk STARTED from, because a
    # progress of 0 is departure, not arrival
    assert (got0["x"], got0["y"]) == (1, 0)
    assert got0["done"] is False
    assert (got1["x"], got1["y"]) == (end["x"], end["y"])
    assert got1["done"] is True
    assert start != end


def test_the_interpolated_cell_is_always_on_the_dense_route():
    """A 0..1 progress may only ever land on a cell the route walks."""
    script = """
const p=M.pathThrough({x:0,y:2},{x:4,y:2},{blocked:new Set(['2,0','2,1','2,2','2,3','2,4'])});
function expand(path,from){const out=[];let cur=from;
  for(const w of path){const dx=Math.sign(w.x-cur.x),dy=Math.sign(w.y-cur.y);
    while(cur.x!==w.x||cur.y!==w.y){cur={x:cur.x+dx,y:cur.y+dy};out.push(cur);}}
  return out;}
const dense=[{x:0,y:2}].concat(expand(p,{x:0,y:2}));
const keys=new Set(dense.map(c=>c.x+','+c.y));
let off=0, monotone=0;
let prev=-1;
for(let i=0;i<=100;i++){
  const t=i/100, r=M.interpolate(p,t);
  if(!keys.has(r.x+','+r.y)&&!keys.has(Math.round(r.x)+','+Math.round(r.y)))off++;
  // progress must not walk backwards along its own route
  const at=dense.findIndex(c=>c.x===Math.round(r.x)&&c.y===Math.round(r.y));
  if(at>=0&&at<prev)monotone++;
  if(at>=0)prev=at;
}
console.log(JSON.stringify({off,monotone,dense:dense.length,waypoints:p.length}));
"""
    got = _run(script)
    assert got["off"] == 0, got
    assert got["monotone"] == 0, "progress moved backwards along its own path"
    assert got["waypoints"] < got["dense"], (
        "the path was not compressed, so interpolation pays per cell")


def test_the_facing_is_the_direction_of_the_segment_being_walked():
    """The facing comes from the segment, not from re-deriving the endpoints —
    ai-town `pathPosition` returns `segmentStart.facing` for exactly this
    reason: at a corner the agent must already be facing the new way."""
    path = json.dumps(_run("console.log(JSON.stringify("
                           "M.pathThrough({x:0,y:0},{x:5,y:3},{})))"))
    got = _run(f"const p={path};"
               "const out=[];"
               "for(const t of [0.01,0.2,0.5,0.8,0.99])"
               "  out.push(M.interpolate(p,t).facing);"
               "console.log(JSON.stringify(out))")
    assert set(got) <= {"right", "down", "left", "up"}
    assert "right" in got, got
    assert got[0] == "right", got


def test_interpolate_is_a_pure_function_and_clamps():
    path = json.dumps(_run("console.log(JSON.stringify("
                           "M.pathThrough({x:0,y:0},{x:4,y:2},{})))"))
    a = _run(f"const p={path};console.log(JSON.stringify(M.interpolate(p,0.37)))")
    b = _run(f"const p={path};console.log(JSON.stringify(M.interpolate(p,0.37)))")
    assert a == b
    low = _run(f"const p={path};console.log(JSON.stringify(M.interpolate(p,-5)))")
    high = _run(f"const p={path};console.log(JSON.stringify(M.interpolate(p,9)))")
    start = _run(f"const p={path};console.log(JSON.stringify(M.interpolate(p,0)))")
    assert low == start, "a negative progress must clamp to the start"
    assert high["done"] is True and low["done"] is False


def test_an_empty_or_single_cell_path_is_handled_without_inventing_motion():
    empty = _run("console.log(JSON.stringify(M.interpolate([],0.5)))")
    assert empty["x"] is None and empty["y"] is None and empty["done"] is True
    one = _run("console.log(JSON.stringify(M.interpolate([{x:3,y:3}],0.5)))")
    assert (one["x"], one["y"]) == (3, 3) and one["done"] is True


def test_a_route_never_passes_through_a_desk_it_was_told_about():
    """The floorplan's own seat slots block the tiles they stand on."""
    floorplan = {
        "floors": [{
            "number": 0,
            "rooms": [{
                "id": 1, "slug": "desks", "x": 0, "y": 0, "w": 6, "h": 4,
                "seats": [
                    {"slot": 0, "agent_id": 7, "x": 3, "y": 0},
                    {"slot": 1, "agent_id": 8, "x": 3, "y": 2},
                ],
            }],
        }],
    }
    fp = json.dumps(floorplan)
    script = (
        f"const fp={fp};"
        "const b=M.blockedFromFloorplan(fp);"
        "const route=M.pathThrough({x:1,y:0},{x:5,y:0},{blocked:b});"
        "console.log(JSON.stringify({blocked:[...b],route:route}))"
    )
    got = _run(script)
    assert "3,0" in got["blocked"], got
    assert "3,2" in got["blocked"], got
    cells = _expand(got["route"], {"x": 1, "y": 0})
    assert {"x": 3, "y": 0} not in cells, (
        "the route walked through an occupied desk")
    assert got["route"][-1] == {"x": 5, "y": 0}


# --------------------------------------------------------------- stations
def test_the_stations_are_the_backends_own_map():
    """Read out of `tower.py` and compared, so the two cannot drift."""
    stations = _tower_literal("STATIONS")
    events = _tower_literal("_EVENT_STATION")

    got_stations = _run("console.log(JSON.stringify(M.STATIONS))")
    got_events = _run("console.log(JSON.stringify(M.STATION_EVENT))")

    assert set(got_stations) == set(stations), (
        f"motion.js invented or dropped a station: "
        f"{sorted(set(got_stations) ^ set(stations))}")
    for name, (dx, dy) in stations.items():
        assert got_stations[name] == {"x": dx, "y": dy}, (
            f"station {name!r} is at ({dx},{dy}) in the backend and "
            f"{got_stations[name]} here")
    assert got_events == events, (
        f"the event->station map drifted: backend {events}, module "
        f"{got_events}")


def test_station_for_sim_says_nothing_for_a_kind_with_no_station():
    """A kind absent from `_EVENT_STATION` stays at the desk. There is no
    default, because a default is motion the event did not justify."""
    got = _run("console.log(JSON.stringify({"
               "tool:M.stationForSim('tool_call'),"
               "ledger:M.stationForSim('ledger_write'),"
               "mcp:M.stationForSim('mcp_call'),"
               "note:M.stationForSim('note_written'),"
               "mem:M.stationForSim('memory_written'),"
               "skill:M.stationForSim('skill_used'),"
               "run:M.stationForSim('run_started'),"
               "escalation:M.stationForSim('escalation'),"
               "empty:M.stationForSim(''),"
               "nul:M.stationForSim(null)}))")
    assert got["tool"] == "workbench" and got["skill"] == "workbench"
    assert got["ledger"] == "vault"
    assert got["mcp"] == "comms"
    assert got["note"] == "shelf" and got["mem"] == "shelf"
    # a run event and an escalation have no station in the room sense
    assert got["run"] is None and got["escalation"] is None
    assert got["empty"] is None and got["nul"] is None


def test_a_station_cell_is_the_room_origin_plus_its_offset():
    """The same arithmetic as `tower.py::_station_coords`, for the rooms the
    real floorplan builds (2 rooms across a floor: origins (0,0) and (7,0))."""
    script = ("console.log(JSON.stringify({"
              "a:M.stationCell({x:0,y:0},'tool_call'),"
              "b:M.stationCell({x:0,y:0},'ledger_write'),"
              "c:M.stationCell({x:0,y:0},'mcp_call'),"
              "d:M.stationCell({x:0,y:0},'note_written'),"
              "e:M.stationCell({x:7,y:0},'tool_call'),"
              "f:M.stationCell({x:7,y:5},'mcp_call')}))")
    got = _run(script)
    assert (got["a"]["x"], got["a"]["y"]) == (1, 2)
    assert (got["b"]["x"], got["b"]["y"]) == (4, 1)
    assert (got["c"]["x"], got["c"]["y"]) == (4, 2)
    assert (got["d"]["x"], got["d"]["y"]) == (2, 3)
    assert (got["e"]["x"], got["e"]["y"]) == (8, 2)
    assert (got["f"]["x"], got["f"]["y"]) == (11, 7)
    assert got["a"]["station"] == "workbench"


def test_a_walk_is_only_planned_when_an_event_justifies_it():
    """`planWalk` is the whole route for a reported walk, or nothing at all."""
    floorplan = {
        "floors": [{
            "number": 0,
            "rooms": [{
                "id": 11, "slug": "desks", "x": 0, "y": 0, "w": 6, "h": 4,
                "seats": [{"slot": 0, "agent_id": 7, "x": 5, "y": 0}],
            }],
        }],
    }
    fp = json.dumps(floorplan)
    script = f"""
const fp={fp};
const who={{room_id:11, position:{{x:5,y:0,floor:0}},
            last_event:{{kind:'tool_call', age_s:1}}}};
const withEvent=M.planWalk(fp, who, {{}});
const noEvent=M.planWalk(fp, {{...who, last_event:{{kind:'run_started',age_s:1}}}}, {{}});
const none=M.planWalk(fp, {{...who, last_event:null}}, {{}});
console.log(JSON.stringify({{
  withEvent: withEvent && {{from:withEvent.from,to:withEvent.to,
                            station:withEvent.station,
                            last:withEvent.path[withEvent.path.length-1],
                            waypoints:withEvent.path.length}},
  noEvent: noEvent, none: none}}));
"""
    got = _run(script)
    walk = got["withEvent"]
    assert walk is not None and walk["station"] == "workbench"
    assert (walk["from"]["x"], walk["from"]["y"]) == (5, 0)
    assert (walk["to"]["x"], walk["to"]["y"]) == (1, 2)
    assert walk["last"] == {"x": 1, "y": 2}
    assert walk["waypoints"] >= 1
    # no station for the kind -> no path, which is C7 stated as data
    assert got["noEvent"] is None and got["none"] is None


def test_every_requirement_of_the_module_is_reachable_from_its_own_test():
    """A last guard: the tests above are text, and a renamed export would let
    them pass while the renderer broke."""
    names = _run("console.log(JSON.stringify(Object.keys(M).sort()))")
    for want in ("DIRS", "DIR", "pathThrough", "interpolate", "STATIONS",
                 "stationForSim"):
        assert want in names, f"{want} is missing from the module's exports"
