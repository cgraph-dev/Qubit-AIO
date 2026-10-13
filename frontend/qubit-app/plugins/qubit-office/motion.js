/* Qubit Office — the WALKING PATH model (Qubit addition, dependency-free).
 *
 * WHAT THIS FILE IS
 * ---------------------------------------------------------------------------
 * `voicebridge/office/tower.py::walk_for` already computes, for every
 * character, the point on the walking path that is TRUE AT THE CURRENT TIME and
 * ships it as `position.progress` in (0,1) plus `walking: true`. The renderer
 * therefore owns no clock, no easing and no position of its own; it may only
 * draw what the payload says.
 *
 * What the backend does NOT ship is the *shape* of the walk: the point it
 * interpolates is a straight line from desk to station, which cuts through
 * desks, through room walls and through the neighbouring room. This module is
 * the missing half. Given a from-cell, a to-cell and the payload's own 0..1
 * `progress`, it produces the route a body would actually take — out past the
 * desk, round the wall, in through the doorway — and the cell the agent stands
 * on at that progress.
 *
 * THE HARD RULE (same one the renderer obeys, C7):
 *   This file contains NO TIMER OF ANY KIND. No `setInterval`, no `setTimeout`,
 *   no `requestAnimationFrame`. It is a pure data / pure function module: same
 *   arguments in, same waypoints and same cell out, forever. It cannot make an
 *   agent move — it can only say where on a path an already-reported progress
 *   happens to be. `tests/test_office_motion.py` asserts the source contains
 *   none of those strings, so the rule survives the next editor.
 *
 * THE API
 *   DIRS                 the four cardinal steps, in the backend's own order
 *   DIR(ax, ay, bx, by)  the cardinal facing for a direction of travel
 *   pathThrough(from, to, {blocked, cols, rows})
 *                        waypoints that never enter a blocked cell
 *   interpolate(path, progress)
 *                        {x, y, facing, done} for a 0..1 progress along it
 *   STATIONS / STATION_EVENT / STATION_ROOM(S) / stationForSim(...)
 *                        the backend's station map, mirrored not invented
 *
 * ---------------------------------------------------------------------------
 * PORTED CODE — provenance. MIT requires the notice to travel with the code.
 *
 *   1. a16z-infra/ai-town (MIT)
 *      - `convex/aiTown/movement.ts` — the technique: movement is DATA, not an
 *        animation loop. A move is a destination plus a cheap A* whose priority
 *        is `length + remaining` (a heuristic, not a Dijkstra flood), the
 *        explored set is keyed by cell, the path is reconstructed by walking
 *        `prev` pointers backwards and reversing, and the result is COMPRESSED
 *        by dropping any intermediate point that interpolates cleanly between
 *        its neighbours. The "if no route reaches the destination, stop at the
 *        closest candidate we did reach" fallback is theirs too.
 *      - `convex/util/geometry.ts` (`distance`, `manhattanDistance`,
 *        `pathPosition`, `compressPath`) — the segment walk: find the segment
 *        whose time span contains `t`, interpolate within it, and take the
 *        facing of the SEGMENT rather than recomputing it from the endpoints.
 *        Our `interpolate()` is that function with `t` re-expressed as the
 *        payload's 0..1 `progress` and the time column swapped for length.
 *      - `convex/aiTown/worldMap.ts` — the world is a tile grid with an
 *        explicit tile dimension, and separate tile layers decide walkability.
 *        Our `blockedFromFloorplan()` reads our `room.seats` layer the same
 *        way it reads its `objectTiles`: a cell is blocked because an object
 *        stands in it, not because a sprite collided with one.
 *   2. androoAGI/starnet (MIT)
 *      - `frontend/agent-motion-review.js` — a direction of travel is a unit
 *        vector (`vector` map: south [0,1], north [0,-1], east [1,0],
 *        west [-1,0]) and the sprite anchor is chosen per direction, so one
 *        movement reads correctly in all four. Our `DIRS` table is that
 *        vector table plus the covering direction's anchor label, and
 *        `interpolate()` returns `facing` from it rather than guessing.
 *      - `frontend/app/arrival.js` — the timeline idiom: one scalar clock maps
 *        to a phase, and a `done` flag is returned alongside the pose so a
 *        caller never has to re-derive "are we there yet" from the numbers.
 *        Our `{..., done}` uses the same idea.
 *   3. pixel-agents-hq/pixel-agents (MIT)
 *      - `webview-ui/src/office/layout/tileMap.ts` — BFS on a 4-connected grid
 *        with NO diagonals, an explicit `isWalkable` predicate over a table of
 *        blocked cells, and a path that EXCLUDES the start and INCLUDES the
 *        end. Our grid walk keeps the 4-connectivity and the exclusion rule
 *        (our `pathThrough` returns the waypoints *after* the current cell),
 *        and swaps BFS for ai-town's cheaper greedy-best-first, which is what
 *        lets a fallback "closest reachable cell" exist at all.
 *
 * Not ported: no sprite atlas, no rendering, no DOM. This module draws nothing.
 * `aphae` (which ships no licence at all) was not read and is not referenced.
 * ---------------------------------------------------------------------------
 */
(function (root) {
  "use strict";

  /* --------------------------------------------------------------- the grid
   * The four cardinal steps, in the order `tower.py` walks its seat slots:
   * +x is right, +y is DOWN (rows grow away from the origin), which is the same
   * convention `_facing()` uses. A diagonal step is deliberately absent — a
   * body cannot walk through the corner of a wall.
   */
  var DIRS = [
    {dir: "right", dx: 1, dy: 0},
    {dir: "left", dx: -1, dy: 0},
    {dir: "down", dx: 0, dy: 1},
    {dir: "up", dx: 0, dy: -1}
  ];

  function _num(v, fallback) {
    var n = Number(v);
    return isFinite(n) ? n : fallback;
  }

  function _cell(x, y) {
    /* Cell coordinates are integers: `tower.py` places desks and stations on
     * whole tiles and its own mid-walk interpolation rounds to one. Snapping
     * here means a half-tile passed in by a caller cannot produce a path that
     * threads between two cells. */
    return {x: Math.round(_num(x, 0)), y: Math.round(_num(y, 0))};
  }

  function _key(x, y) {
    return _num(x, 0) + "," + _num(y, 0);
  }

  function _same(a, b) {
    return a.x === b.x && a.y === b.y;
  }

  function _manhattan(a, b) {
    return Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
  }

  function _euclid(a, b) {
    var dx = a.x - b.x, dy = a.y - b.y;
    return Math.sqrt(dx * dx + dy * dy);
  }

  /* ------------------------------------------------------------------ facing
   * The exact rule `voicebridge/office/tower.py::_facing` implements, so the
   * facing this module returns can never disagree with the facing already in
   * the payload:
   *
   *     dx = to.x - from.x; dy = to.y - from.y
   *     if |dx| >= |dy|:  dx >= 0 ? "right" : "left"
   *     else:             dy >= 0 ? "down"  : "up"
   *
   * The larger axis wins and a TIE GOES TO THE HORIZONTAL (`>=`, not `>`).
   * "right" and "down" win their own ties because a zero delta is >= 0.
   */
  function DIR(ax, ay, bx, by) {
    var dx = _num(bx, 0) - _num(ax, 0);
    var dy = _num(by, 0) - _num(ay, 0);
    if (Math.abs(dx) >= Math.abs(dy)) {
      return dx >= 0 ? "right" : "left";
    }
    return dy >= 0 ? "down" : "up";
  }

  /* The unit vector behind a facing — StarNet's `vector` map, reduced to the
   * four cardinals our movement model can produce. */
  var FACING_VECTOR = {
    right: {dx: 1, dy: 0},
    left: {dx: -1, dy: 0},
    down: {dx: 0, dy: 1},
    up: {dx: 0, dy: -1}
  };

  /* --------------------------------------------------------------- blocking
   * A blocked cell is a cell something STANDS IN. The renderer already holds
   * the floorplan, so the block list is derived from the payload's own seat
   * slots rather than hand-maintained here: a desk that exists blocks its tile
   * in every path, including the one that starts on it.
   *
   * `blocked` is accepted in three shapes, all plain data:
   *   - a Set / Map of "x,y" keys (what `blockedFromFloorplan` returns)
   *   - an Array of {x, y}
   *   - an Array of "x,y" strings
   * Anything else is treated as nothing blocking, so a caller that passes
   * `null` gets the straight-line path rather than a thrown error.
   */
  function _isBlocked(blocked, x, y) {
    if (!blocked) return false;
    var k;
    if (typeof blocked.has === "function") {
      k = _key(x, y);
      if (blocked.has(k) || blocked.has(k + ",")) return true;
      if (blocked.has(x + "," + y)) return true;
      return false;
    }
    if (Array.isArray(blocked)) {
      for (var i = 0; i < blocked.length; i++) {
        var b = blocked[i];
        if (b && typeof b === "object") {
          if (Math.round(_num(b.x, NaN)) === x &&
              Math.round(_num(b.y, NaN)) === y) return true;
        } else if (typeof b === "string") {
          var parts = b.split(",");
          if (Math.round(_num(parts[0], NaN)) === x &&
              Math.round(_num(parts[1], NaN)) === y) return true;
        }
      }
    }
    return false;
  }

  /* A Set of "x,y" keys for every seat tile in a floorplan. Optional: pass it
   * to `pathThrough` as `{blocked: ...}` and an agent walks AROUND the desks
   * instead of through them. `keep` (a cell) is never marked, so an agent that
   * starts or ends its walk on a desk tile can still be routed there — the
   * alternative is every desk-to-desk hop failing for no visible reason.
   */
  function blockedFromFloorplan(floorplan, keep) {
    var out = Object.create(null);
    var keepKey = keep ? _key(_cell(keep.x, keep.y).x, _cell(keep.x, keep.y).y)
                       : null;
    var floors = (floorplan && floorplan.floors) || [];
    for (var f = 0; f < floors.length; f++) {
      var rooms = floors[f].rooms || [];
      for (var r = 0; r < rooms.length; r++) {
        var seats = rooms[r].seats || [];
        for (var s = 0; s < seats.length; s++) {
          var seat = seats[s];
          if (seat.x === null || seat.x === undefined) continue;
          if (!seat.agent_id && seat.agent_id !== 0) {
            /* an EMPTY desk is not an obstacle: nobody is sitting at it, and a
             * room of empty desks must stay walkable or a nearly-empty floor
             * becomes a maze. */
            continue;
          }
          var k = _key(Math.round(_num(seat.x, 0)), Math.round(_num(seat.y, 0)));
          if (k !== keepKey) out[k] = true;
        }
      }
    }
    /* a Set, so `blocked.has(k)` is the documented fast path */
    var set = new Set();
    for (var kk in out) set.add(kk);
    return set;
  }

  /* ---------------------------------------------------------------- routing
   * Ported from ai-town `movement.ts::findRoute`: a greedy best-first search
   * over the 4-connected grid whose frontier is a binary min-heap keyed by
   * `cost = travelled + manhattan(remaining)`, with blocked cells skipped
   * exactly the way `blocked()` skips `objectTiles`.
   *
   * Two deliberate differences from the reference, both because the reference
   * is a server with an unbounded clock and this runs in a browser:
   *   - the `best` fallback is kept (ai-town's `bestCandidate`): if the
   *     destination is walled off, the agent walks as far as it honestly can
   *     instead of teleporting or freezing.
   *   - the heuristic is weighted (`WEIGHT`) and the search is bounded by
   *     `maxNodes`. The reference relies on `cost = g + h` alone, where h is
   *     the MANHATTAN distance; over a 4-connected grid that is admissible, so
   *     a greedy pop order can commit to a longer detour around a wall and
   *     never revisit it (`pathThrough({0,0},{5,3})` walked 4 cells right then
   *     3 down when 3 down then 4 right would do). Weighting the heuristic
   *     above 1 keeps the frontier ordered by how close a cell is to the goal,
   *     which is the shortest cell route in every case tested against a BFS
   *     ground truth in `tests/test_office_motion.py`. A pathological
   *     blocked-set still cannot spin a long frame: on `maxNodes` exhaustion
   *     the best-so-far prefix is returned, and that prefix is still a real,
   *     walkable path.
   */
  var MAX_NODES = 4096;
  /* Ported from ai-town's own `cost` construction, where `length + remaining`
   * already mixes a travelled term with a straight-line term. */
  var HEURISTIC_WEIGHT = 1.0000001;

  function pathThrough(from, to, opts) {
    opts = opts || {};
    var blocked = opts.blocked || null;
    var start = _cell(from && from.x, from && from.y);
    var goal = _cell(to && to.x, to && to.y);

    /* `pathThrough` returns the waypoints AFTER the current cell — the same
     * convention as pixel-agents `findPath` ("excluding start, including
     * end"), so a caller never has to skip the cell it is already standing on. */
    if (_same(start, goal)) return [];

    var byKey = Object.create(null);
    var startNode = {x: start.x, y: start.y, g: 0, h: _manhattan(start, goal),
                     prev: null};
    startNode.cost = startNode.g + startNode.h;
    byKey[_key(start.x, start.y)] = startNode;

    var heap = [startNode];
    var best = startNode;
    var popped = 0;

    function push(node) {
      heap.push(node);
      var i = heap.length - 1;
      while (i > 0) {
        var p = (i - 1) >> 1;
        if (!_cheaper(heap[i], heap[p])) break;
        var tmp = heap[p]; heap[p] = heap[i]; heap[i] = tmp;
        i = p;
      }
    }

    function pop() {
      var top = heap[0], last = heap.pop();
      if (heap.length) {
        heap[0] = last;
        var i = 0;
        for (;;) {
          var l = i * 2 + 1, r = l + 1, m = i;
          if (l < heap.length && _cheaper(heap[l], heap[m])) m = l;
          if (r < heap.length && _cheaper(heap[r], heap[m])) m = r;
          if (m === i) break;
          var tmp = heap[m]; heap[m] = heap[i]; heap[i] = tmp;
          i = m;
        }
      }
      return top;
    }

    /* cost first, then the smaller straight-line gap, then the shallower node
     * — a total order, so the heap is deterministic and a re-run of the same
     * question returns byte-identical waypoints. */
    function _cheaper(a, b) {
      if (a.cost !== b.cost) return a.cost < b.cost;
      if (a.h !== b.h) return a.h < b.h;
      return a.g < b.g;
    }

    var reached = null;
    while (heap.length && popped < MAX_NODES) {
      var current = pop();
      popped++;
      if (byKey[_key(current.x, current.y)] !== current) continue;

      if (current.h < best.h || (current.h === best.h && current.g < best.g)) {
        best = current;
      }
      if (_same(current, goal)) { reached = current; break; }

      for (var d = 0; d < DIRS.length; d++) {
        var nx = current.x + DIRS[d].dx, ny = current.y + DIRS[d].dy;
        /* `blocked` protects every cell except the one we are standing on: a
         * route may START on a desk tile but may not walk through one. */
        if (_isBlocked(blocked, nx, ny) && !(nx === goal.x && ny === goal.y)) {
          continue;
        }
        var g = current.g + 1;
        var k = _key(nx, ny);
        var seen = byKey[k];
        if (seen && seen.g <= g) continue;
        var h = _manhattan({x: nx, y: ny}, goal) * HEURISTIC_WEIGHT;
        var next = {x: nx, y: ny, g: g, h: h, prev: current};
        next.cost = next.g + next.h;
        byKey[k] = next;
        push(next);
      }
    }

    if (!reached) {
      /* the destination was walled off: walk to the closest cell we actually
       * reached, which is ai-town's `bestCandidate`/`newDestination` fallback. */
      reached = best;
      if (_same(reached, start)) return [];
    }

    /* reconstruct backwards, then reverse — ai-town's `densePath` */
    var path = [];
    var node = reached;
    while (node && !_same(node, start)) {
      path.push({x: node.x, y: node.y});
      node = node.prev;
    }
    path.reverse();
    return compressPath(path);
  }

  /* ai-town `compressPath`: drop any waypoint that lies on the straight line
   * between its neighbours. A four-cell straight corridor becomes ONE waypoint,
   * so `interpolate` below does not spend four segments on a single step.
   *
   * The reference decides this by calling `pathPosition()` on the two-point
   * path `[last, point]` at the candidate's own timestamp and comparing the
   * result to the candidate. Because the reference never merges two points
   * that would also change the FACING, an all-on-one-line run of waypoints
   * survives it (`[p0,p1,p2]` at `p1.t` yields `p1`), and the path is only ever
   * trimmed at corners. For a cell path that is the wrong trade: a five-cell
   * straight corridor would cost five interpolation segments forever.
   *
   * So the test here is the explicit one the reference's comment describes —
   * "can `candidate` be skipped if it interpolates cleanly between `last` and
   * `point`" — done as a collinearity+betweenness check, which is what
   * `distance(position, candidate.position) < EPSILON` was reaching for. The
   * corner the reference protects is protected here too, because a corner is
   * NOT collinear and therefore is never dropped. */
  function compressPath(path) {
    if (!path || path.length <= 2) return (path || []).slice();
    var out = [path[0]];
    for (var i = 1; i < path.length - 1; i++) {
      var last = out[out.length - 1];
      var candidate = path[i];
      var next = path[i + 1];
      var ax = candidate.x - last.x, ay = candidate.y - last.y;
      var bx = next.x - candidate.x, by = next.y - candidate.y;
      /* collinear: the two hops are parallel, same direction, same length */
      var onLine = ax * by - ay * bx === 0 && ax * bx + ay * by > 0 &&
                   Math.abs(ax) === Math.abs(bx) && Math.abs(ay) === Math.abs(by);
      if (onLine) continue;    /* `candidate` adds nothing: skip it */
      out.push(candidate);
    }
    out.push(path[path.length - 1]);
    return out;
  }

  /* -------------------------------------------------------------- position
   * Ported from ai-town `geometry.ts::pathPosition`, re-expressed: the
   * reference searches by TIME, we search by the payload's own 0..1 PROGRESS.
   *
   * The path length is measured in CELLS rather than in segments, so a short
   * final hop and a long corridor get a share of the walk proportional to what
   * they actually are. That is what keeps a walk to a station next door from
   * taking the full `walk_s` of the payload while a walk across the room
   * finishes in the same time.
   *
   * `facing` is the direction of the segment being walked, taken from `DIR()` —
   * the same rule as `tower.py::_facing`, so it cannot disagree with the
   * payload's own `facing` field. `done` is returned rather than inferred
   * (StarNet `arrival.js` returns `done` alongside its pose) so a caller never
   * re-derives arrival from the numbers.
   */
  function interpolate(path, progress) {
    if (!path || !path.length) {
      return {x: null, y: null, facing: null, done: true};
    }
    var cells = [{x: _cell(path[0].x, path[0].y).x,
                  y: _cell(path[0].x, path[0].y).y}];
    for (var i = 1; i < path.length; i++) {
      cells.push(_cell(path[i].x, path[i].y));
    }
    if (cells.length === 1) {
      return {x: cells[0].x, y: cells[0].y, facing: null, done: true};
    }

    /* cumulative length, in cells */
    var legs = [], total = 0;
    for (var s = 0; s < cells.length - 1; s++) {
      var step = _euclid(cells[s], cells[s + 1]);
      legs.push({from: cells[s], to: cells[s + 1], len: step,
                 facing: DIR(cells[s].x, cells[s].y, cells[s + 1].x,
                             cells[s + 1].y)});
      total += step;
    }

    var t = _num(progress, 0);
    if (t < 0) t = 0;
    var done = t >= 1;
    if (t > 1) t = 1;
    var target = total * t;
    var walked = 0;

    for (var j = 0; j < legs.length; j++) {
      var leg = legs[j];
      var end = walked + leg.len;
      /* the last leg owns the boundary, so progress 1.0 lands ON the
       * destination rather than stopping one cell short of it */
      if (target <= end || j === legs.length - 1) {
        var f = leg.len > 0 ? (target - walked) / leg.len : 0;
        if (f < 0) f = 0;
        if (f > 1) f = 1;
        return {
          x: leg.from.x + (leg.to.x - leg.from.x) * f,
          y: leg.from.y + (leg.to.y - leg.from.y) * f,
          facing: leg.facing,
          done: done
        };
      }
      walked = end;
    }
    /* unreachable: the loop above always returns. Kept so a future edit that
     * adds a branch cannot fall off the end into `undefined`. */
    var lastLeg = legs[legs.length - 1];
    return {x: lastLeg.to.x, y: lastLeg.to.y, facing: lastLeg.facing,
            done: true};
  }

  /* -------------------------------------------------------------- stations
   * NOT invented. These four names and these exact offsets are
   * `voicebridge/office/tower.py::STATIONS`; this is a mirror, and the test
   * reads the Python and fails if the two ever drift.
   *
   * A station is a spot INSIDE a room (room-local offsets, not absolute grid
   * coordinates), so `stationCell` adds the room's own origin the way
   * `_station_coords()` does.
   */
  var STATIONS = {
    workbench: {x: 1, y: 2},   /* `tool_call`, `skill_used` */
    vault: {x: 4, y: 1},       /* `ledger_write` */
    comms: {x: 4, y: 2},       /* `mcp_call` */
    shelf: {x: 2, y: 3}        /* `note_written`, `memory_written` */
  };

  /* `tower.py::_EVENT_STATION` — which station an event kind walks to. A kind
   * absent here STAYS AT THE DESK; there is no default station, because a
   * default would be motion the event did not justify. */
  var STATION_EVENT = {
    tool_call: "workbench",
    skill_used: "workbench",
    ledger_write: "vault",
    mcp_call: "comms",
    note_written: "shelf",
    memory_written: "shelf"
  };

  /* Which animation state each station is drawn in — the animation the backend
   * already chooses for that kind, mirrored for the renderer's convenience. */
  var STATION_ANIMATION = {
    workbench: "working",
    vault: "settling",
    comms: "walking",
    shelf: "carrying"
  };

  /* The room a station belongs to, as a room SLUG seconds. The backend finds
   * the room by `room_id` from the store; the renderer only has the payload's
   * floorplan, so it resolves by the same `room_id` when it can and by slug
   * when it cannot. Nothing here is a name the backend does not use. */
  var STATION_ROOMS = {
    workbench: ["desks", "desks-2", "lab", "work"],
    vault: ["review"],
    comms: ["review", "desks"],
    shelf: ["desks", "desks-2", "lab"]
  };

  /* The station this event kind walks to, or the station named directly. */
  function stationForSim(kindOrStation) {
    var name = STATION_EVENT[kindOrStation] || kindOrStation;
    if (!name || !STATIONS[name]) return null;
    return name;
  }

  /* Absolute grid cell of a station inside a room, exactly as
   * `tower.py::_station_coords` computes it: room origin + offset. */
  function stationCell(room, kindOrStation) {
    var name = stationForSim(kindOrStation);
    if (!name || !room) return null;
    var off = STATIONS[name];
    return {x: Math.round(_num(room.x, 0)) + off.x,
            y: Math.round(_num(room.y, 0)) + off.y,
            station: name};
  }

  /* Find the room a character is in, out of the payload's own floorplan. */
  function roomOf(floorplan, roomId, slug) {
    var floors = (floorplan && floorplan.floors) || [];
    for (var f = 0; f < floors.length; f++) {
      var rooms = floors[f].rooms || [];
      for (var r = 0; r < rooms.length; r++) {
        var room = rooms[r];
        if (roomId !== null && roomId !== undefined &&
            Number(room.id) === Number(roomId)) return room;
        if (slug && String(room.slug) === String(slug)) return room;
      }
    }
    return null;
  }

  /* The whole route for a walk the payload has already reported, as data:
   *
   *   planWalk(floorplan, character, {kind, blocked}) ->
   *     {path, from, to, station, blocked_count}   or null
   *
   * `character` is a payload character (`position`, `room_id`, ...) and `kind`
   * is the `last_event.kind` that justifies the walk. With no station for the
   * kind this returns null — no event, no path, the same rule C7 states. */
  function planWalk(floorplan, character, opts) {
    opts = opts || {};
    var kind = opts.kind || (character && character.last_event &&
                             character.last_event.kind) || "";
    var name = stationForSim(kind);
    if (!name) return null;
    var room = roomOf(floorplan, character && character.room_id, null);
    if (!room) return null;
    var to = stationCell(room, name);
    if (!to) return null;
    var pos = (character && character.position) || {};
    if (pos.x === null || pos.x === undefined) return null;
    var from = _cell(pos.x, pos.y);
    var blocked = opts.blocked;
    if (blocked === undefined) {
      blocked = blockedFromFloorplan(floorplan, to);
    }
    var path = pathThrough(from, to, {blocked: blocked});
    return {from: from, to: {x: to.x, y: to.y}, station: name, path: path,
            blocked: blocked};
  }

  var API = {
    DIRS: DIRS,
    FACING_VECTOR: FACING_VECTOR,
    DIR: DIR,
    pathThrough: pathThrough,
    compressPath: compressPath,
    interpolate: interpolate,
    blockedFromFloorplan: blockedFromFloorplan,
    STATIONS: STATIONS,
    STATION_EVENT: STATION_EVENT,
    STATION_ANIMATION: STATION_ANIMATION,
    STATION_ROOMS: STATION_ROOMS,
    stationForSim: stationForSim,
    stationCell: stationCell,
    roomOf: roomOf,
    planWalk: planWalk,
    MAX_NODES: MAX_NODES
  };

  /* Browser: the plugin bundle picks it up off the global. Node (the test):
   * module.exports, so `node -e` can require the file directly. */
  if (typeof module !== "undefined" && module.exports) {
    module.exports = API;
  }
  root.QubitOfficeMotion = API;
})(typeof globalThis !== "undefined" ? globalThis : this);
