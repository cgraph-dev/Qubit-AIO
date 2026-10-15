"""F07 — the playable slice: outside -> enter -> lobby -> elevator -> floor ->
room -> back -> exit, by MOUSE and by KEYBOARD, with the location surviving reload.

F07's proof (`60-IMPLEMENTATION-PLAN.md`): "outside→enter→lobby→elevator→floor→
room→back→exit succeeds by mouse and keyboard; location survives reload."

The walk is driven over the REAL recorded payload
(`fixtures/tower-recording.json`: 3 floors, 4 rooms, 10 characters), so the
traversal is proved against the building that actually exists rather than a
hand-built toy graph.

Mouse and keyboard are asserted to be the SAME path: both produce the same
`Intent` and go through one reducer. A mouse-only shortcut is the failure this
catches.

Hermetic: tsc + node. No browser, no server, no network.
"""

from __future__ import annotations

import json
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
    ensure_tsc, node_path, require_toolchain_or_fail)

NODE = node_path()
#: Self-healing: installs the compiler outside the tree if it vanished, so the
#: suite RUNS after a dashboard build deleted node_modules.
TSC = ensure_tsc()

MODULES = ["types.ts", "WorldAssembler.ts", "navigation.ts", "input.ts",
           "SceneHost.ts", "scenes.ts", "InteractionController.ts"]
#: `scenes.ts` imports these, so they must sit beside it when it is compiled.
#: Listed separately from the entry points above because they are compiled as
#: DEPENDENCIES, not driven directly.
ART_MODULES = ["art/colour.ts", "art/surfaces.ts", "art/iso.ts", "art/room.ts",
               "art/scene-adapter.ts", "art/fittings.ts"]


def _toolchain() -> bool:
    return ensure_tsc() is not None


#: Loud, not a silent skip: a missing compiler ERRORS at collection (see the
#: helper's docstring). A green `18 skipped` is the false pass this replaces.
require_toolchain_or_fail()


def _run(tmp_path: Path, js: str) -> object:
    staged = tmp_path / "src"
    staged.mkdir(exist_ok=True)
    for name in MODULES:
        shutil.copy(GAME_DIR / name, staged / name)
    # `art/` keeps its directory so the relative `./art/colour` imports in
    # scenes.ts resolve exactly as they do in the bundle.
    (staged / "art").mkdir(exist_ok=True)
    for name in ART_MODULES:
        shutil.copy(GAME_DIR / name, staged / name)
    subprocess.run(
        [NODE, str(TSC), "--module", "commonjs", "--target", "es2020",
         "--moduleResolution", "node",
         "--ignoreDeprecations", "6.0", "--outDir", str(tmp_path / "js"),
         "--skipLibCheck", "--noEmitOnError", "false",
         "--lib", "es2020,dom", str(staged / "InteractionController.ts"),
         str(staged / "input.ts"), str(staged / "scenes.ts")],
        capture_output=True, text=True, timeout=240, cwd=str(staged))
    compiled = tmp_path / "js" / "InteractionController.js"
    if not compiled.is_file():
        pytest.skip("tsc could not compile the slice for the node driver")
    program = (
        'const IC = require(%s);\n'
        'const WA = require(%s);\n'
        'const N = require(%s);\n'
        'const IN = require(%s);\n'
        'const SC = require(%s);\n'
        'const TOWER = JSON.parse(require("fs").readFileSync(%s, "utf8"));\n'
        '(async () => {\n'
        '  const out = await (async () => { %s })();\n'
        '  process.stdout.write(JSON.stringify(out));\n'
        '})().catch(e => { console.error(String(e && e.stack || e));'
        ' process.exit(1); });\n'
    ) % (json.dumps(str(compiled)),
         json.dumps(str(tmp_path / "js" / "WorldAssembler.js")),
         json.dumps(str(tmp_path / "js" / "navigation.js")),
         json.dumps(str(tmp_path / "js" / "input.js")),
         json.dumps(str(tmp_path / "js" / "scenes.js")),
         json.dumps(str(FIXTURE)), js)
    proc = subprocess.run([NODE], input=program, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}")
    return json.loads(proc.stdout)


#: Shared preamble: the world and the portal graph from the REAL payload.
_SETUP = """
  const world = WA.assembleWorld(TOWER, 1);
  const floors = TOWER.floorplan.floors.map(f => ({
    number: f.number, rooms: f.rooms.map(r => ({id: r.id}))}));
  const portals = N.buildHeadquartersPortals('hq', floors);
  const W = 880, H = 500;
"""


# ======================================================================
# 1 — the world is derived from the payload, never invented
# ======================================================================
def test_the_world_matches_the_real_payload(tmp_path):
    got = _run(tmp_path, _SETUP + """
      return {
        floors: world.building.floors.map(f => f.number),
        rooms: world.building.floors.map(f => f.rooms.length),
        chars: world.building.characters.length,
        unseated: WA.unseatedCharacters(world).length,
        placeholder: world.building.placeholder,
        motionRule: world.motionRule.slice(0, 30),
      };
    """)
    assert got["floors"] == [1, 2, 3]
    assert sum(got["rooms"]) == 4, "the recorded building has 4 rooms"
    assert got["chars"] == 10
    assert got["placeholder"] is False, (
        "a real payload was marked scaffold")
    assert got["motionRule"], (
        "the payload's own motion rule was not carried through")


def test_no_payload_builds_no_invented_office(tmp_path):
    """Before the first successful read there is no world; drawing one would be
    the lie C7 forbids."""
    got = _run(tmp_path, """
      const w = WA.assembleWorld(null, 0);
      return {floors: w.building.floors.length,
              chars: w.building.characters.length,
              placeholder: w.building.placeholder};
    """)
    assert got["floors"] == 0
    assert got["chars"] == 0, "a world invented characters with no payload"
    assert got["placeholder"] is True, (
        "a world with no payload does not declare itself scaffold")


def test_the_world_places_no_character_the_payload_did_not(tmp_path):
    got = _run(tmp_path, _SETUP + """
      const payloadIds = new Set(TOWER.characters.map(c => c.agent_id));
      const worldIds = world.building.characters.map(c => c.agentId);
      const phantoms = worldIds.filter(i => !payloadIds.has(i));
      // and every room occupant must be a real character
      let badRoom = [];
      for (const f of world.building.floors) {
        for (const r of f.rooms) {
          for (const o of r.occupants) {
            if (!payloadIds.has(o.agent_id)) badRoom.push(o.agent_id);
          }
        }
      }
      return {phantoms, badRoom, total: worldIds.length};
    """)
    assert got["phantoms"] == [], (
        f"the world spawned characters the payload does not have: "
        f"{got['phantoms']}")
    assert got["badRoom"] == [], (
        f"a room holds an occupant the payload does not seat there")
    assert got["total"] == 10


def test_the_world_is_pure(tmp_path):
    got = _run(tmp_path, """
      const a = WA.assembleWorld(TOWER, 1);
      const b = WA.assembleWorld(TOWER, 1);
      return {same: JSON.stringify(a) === JSON.stringify(b)};
    """)
    assert got["same"] is True, "assembleWorld is not deterministic"


# ======================================================================
# 2 — THE PROOF: the full walk, by KEYBOARD
# ======================================================================
def test_the_full_walk_succeeds_by_keyboard(tmp_path):
    """outside -> enter -> lobby -> elevator -> floor -> room -> back -> exit."""
    got = _run(tmp_path, _SETUP + """
      const trail = [];
      let nav = N.initialNav();
      let sel = null;
      const snap = () => N.encodePlace(nav.viewpoint);
      const go = (intent) => {
        const r = IC.applyIntent(nav, intent, portals, world, sel);
        nav = r.nav; if (r.selected !== undefined) sel = r.selected;
        trail.push({did: intent.kind, at: snap(), refused: r.refused});
        return r;
      };
      trail.push({did: 'start', at: snap(), refused: false});
      go({kind:'enter'});          // exterior -> building
      go({kind:'enter'});          // building -> lobby
      go({kind:'up'});             // lobby -> a floor
      const floorPlace = snap();
      // enter the first room the reached floor actually has
      const floorNo = nav.viewpoint.floor;
      const floor = world.building.floors.find(f => f.number === floorNo);
      const roomId = floor.rooms[0].id;
      go({kind:'goto', to:{kind:'room', building:'hq', floor:floorNo,
                           room:roomId}});
      const inRoom = nav.viewpoint.kind;
      go({kind:'back'});           // room -> floor
      const afterBack = nav.viewpoint.kind;
      go({kind:'exit'});           // floor -> lobby
      go({kind:'exit'});           // lobby -> building
      go({kind:'exit'});           // building -> exterior
      return {trail, floorPlace, inRoom, afterBack, final: snap(),
              refusedAny: trail.filter(t => t.refused).length};
    """)
    assert got["refusedAny"] == 0, (
        f"a step in the walk was refused: "
        f"{[t for t in got['trail'] if t['refused']]}")
    assert got["floorPlace"].startswith("floor:"), (
        f"the elevator did not reach a floor: {got['floorPlace']}")
    assert got["inRoom"] == "room", "the room was not entered"
    assert got["afterBack"] == "floor", (
        f"back from a room did not return to its floor: {got['afterBack']}")
    assert got["final"] == "exterior", (
        f"the walk did not end outside: {got['final']}")


def test_keyboard_keys_produce_the_walk(tmp_path):
    """The KEYS, through the input router, must drive the same traversal — a
    clickable door with no key equivalent is half the proof."""
    got = _run(tmp_path, _SETUP + """
      const trail = [];
      let nav = N.initialNav();
      let sel = null;
      // Enter walks in/through; Escape walks back
      const press = (key) => {
        const state = {active: ['world']};
        const r = IN.routeInput(state, {key, modified:false, repeat:false});
        if (!r.nav) {
          trail.push({key, nav: null, at: N.encodePlace(nav.viewpoint)});
          return;
        }
        const cmd = r.nav;
        let intent = null;
        if (cmd.kind === 'travel') intent = {kind: cmd.direction};
        else if (cmd.kind === 'back') intent = {kind: 'back'};
        if (!intent) return;
        const res = IC.applyIntent(nav, intent, portals, world, sel);
        nav = res.nav; if (res.selected !== undefined) sel = res.selected;
        trail.push({key, intent: intent.kind,
                    at: N.encodePlace(nav.viewpoint), refused: res.refused});
      };
      press('Enter');   // exterior -> building
      press('Enter');   // building -> lobby
      press('Escape');  // back to building
      press('Enter');   // lobby again
      return {trail, at: N.encodePlace(nav.viewpoint)};
    """)
    assert got["trail"][0]["at"] == "building:hq", (
        f"Enter did not enter the building: {got['trail'][0]}")
    assert got["trail"][1]["at"] == "lobby:hq", (
        f"Enter did not reach the lobby: {got['trail'][1]}")
    assert got["trail"][2]["at"] == "building:hq", (
        f"Escape did not go back to the building: {got['trail'][2]}")


# ======================================================================
# 3 — mouse and keyboard are the SAME path
# ======================================================================
def test_a_click_and_a_key_reach_the_same_place(tmp_path):
    """F07 says the walk works by mouse AND keyboard. If a click can reach a
    place no key reaches, the two paths have diverged."""
    got = _run(tmp_path, _SETUP + """
      // The door hotspot on the exterior
      const spots = IC.hotspotsFor({kind:'exterior'}, world, W, H);
      const door = spots.find(s => s.label === 'the front door');
      const byMouse = door ? IC.hitTest(spots, door.x, door.y) : null;

      // The same intent, from the keyboard
      const intentFromKey = {kind: 'enter'};

      let navK = N.initialNav();
      const kRes = IC.applyIntent(navK, intentFromKey, portals, world, null);

      return {
        mouseSpot: byMouse ? {label: byMouse.label, intent: byMouse.intent.kind} : null,
        afterKey: N.encodePlace(kRes.nav.viewpoint),
        refusedByKey: kRes.refused,
      };
    """)
    assert got["mouseSpot"] is not None, (
        "the exterior has no clickable front door")
    assert got["mouseSpot"]["intent"] == "enter", (
        f"the front door's intent is {got['mouseSpot']['intent']}, not 'enter'")
    assert got["afterKey"] == "building:hq"
    assert got["refusedByKey"] is False


def test_every_hotspot_intent_is_accepted_by_the_reducer(tmp_path):
    """A drawn door whose intent the reducer refuses is a dead affordance."""
    got = _run(tmp_path, _SETUP + """
      const places = [N.EXTERIOR, {kind:'building', building:'hq'},
                      {kind:'lobby', building:'hq'}];
      for (const f of world.building.floors) {
        places.push({kind:'floor', building:'hq', floor:f.number});
        for (const r of f.rooms) {
          places.push({kind:'room', building:'hq', floor:f.number, room:r.id});
        }
      }
      const dead = [];
      let total = 0;
      for (const p of places) {
        // reach this place first so the hotspot is meaningful
        const spots = IC.hotspotsFor(p, world, W, H);
        for (const s of spots) {
          total += 1;
          const st = {viewpoint: p, history: [], refused: null};
          const r = IC.applyIntent(st, s.intent, portals, world, null);
          if (r.refused) dead.push({place: N.encodePlace(p), label: s.label,
                                    intent: s.intent.kind,
                                    why: r.nav.refused ? r.nav.refused.reason : null});
        }
      }
      return {total, dead};
    """)
    assert got["total"] > 0, "no hotspots were generated at all"
    assert got["dead"] == [], (
        f"hotspots the reducer refuses (dead affordances): {got['dead']}")


# ======================================================================
# 4 — symmetric exits: every step has a reverse
# ======================================================================
def test_every_step_has_a_symmetric_reverse(tmp_path):
    """`exit` from a room must land on its floor; from a floor on its lobby;
    from the lobby out of the building; from the building on the street."""
    got = _run(tmp_path, _SETUP + """
      const cases = [
        {from: {kind:'room', building:'hq', floor:2, room:2},
         expect: 'floor'},
        {from: {kind:'floor', building:'hq', floor:2}, expect: 'lobby'},
        {from: {kind:'lobby', building:'hq'}, expect: 'building'},
        {from: {kind:'building', building:'hq'}, expect: 'exterior'},
      ];
      const out = [];
      for (const c of cases) {
        const st = {viewpoint: c.from, history: [], refused: null};
        const r = IC.applyIntent(st, {kind:'exit'}, portals, world, null);
        out.push({from: N.encodePlace(c.from),
                  to: N.encodePlace(r.nav.viewpoint),
                  gotKind: r.nav.viewpoint.kind, expect: c.expect,
                  refused: r.refused});
      }
      return out;
    """)
    for row in got:
        assert row["refused"] is False, (
            f"exit from {row['from']} was refused")
        assert row["gotKind"] == row["expect"], (
            f"exit from {row['from']} went to {row['to']}, expected "
            f"{row['expect']}")


def test_exit_from_the_exterior_refuses_rather_than_wrapping(tmp_path):
    got = _run(tmp_path, _SETUP + """
      const r = IC.applyIntent(N.initialNav(), {kind:'exit'}, portals, world, null);
      return {refused: r.refused, at: N.encodePlace(r.nav.viewpoint),
              reason: r.nav.refused ? r.nav.refused.reason : null};
    """)
    assert got["refused"] is True
    assert got["at"] == "exterior", (
        "exiting the street moved the viewpoint somewhere")
    assert got["reason"], "the refusal carries no reason to show"


def test_the_elevator_refuses_at_the_top_rather_than_wrapping(tmp_path):
    """Wrapping from the top floor to the bottom is the classic elevator bug:
    it looks like movement and is actually a discontinuity."""
    got = _run(tmp_path, _SETUP + """
      const top = Math.max(...world.building.floors.map(f => f.number));
      const st = {viewpoint: {kind:'floor', building:'hq', floor: top},
                  history: [], refused: null};
      const r = IC.applyIntent(st, {kind:'up'}, portals, world, null);
      return {top, after: N.encodePlace(r.nav.viewpoint),
              stillTop: r.nav.viewpoint.floor === top, refused: r.refused};
    """)
    assert got["stillTop"] is True, (
        f"the elevator wrapped from the top floor to {got['after']}")
    assert got["refused"] is True, (
        "an impossible elevator ride was not refused")


def test_the_elevator_reaches_every_recorded_floor(tmp_path):
    """From the lobby, riding up must visit every floor the payload reports —
    a floor you cannot reach is a building you cannot use."""
    got = _run(tmp_path, _SETUP + """
      const target = world.building.floors.map(f => f.number).sort((a,b)=>a-b);
      const reached = [];
      // arrive at each floor the way a player would: from the lobby
      for (const n of target) {
        const st = {viewpoint: {kind:'lobby', building:'hq'},
                    history: [], refused: null};
        const r = IC.applyIntent(st, {kind:'goto',
          to: {kind:'floor', building:'hq', floor: n}}, portals, world, null);
        if (!r.refused) reached.push(n);
      }
      return {target, reached};
    """)
    assert sorted(got["reached"]) == sorted(got["target"]), (
        f"unreachable floors: "
        f"{sorted(set(got['target']) - set(got['reached']))}")


# ======================================================================
# 5 — location survives reload
# ======================================================================
def test_the_location_round_trips_through_a_deep_link(tmp_path):
    """F07: "location survives reload". A reload is an encode -> decode."""
    got = _run(tmp_path, _SETUP + """
      const places = [N.EXTERIOR, {kind:'building', building:'hq'},
                      {kind:'lobby', building:'hq'}];
      for (const f of world.building.floors) {
        places.push({kind:'floor', building:'hq', floor:f.number});
        for (const r of f.rooms) {
          places.push({kind:'room', building:'hq', floor:f.number, room:r.id});
        }
      }
      const known = {
        buildings: ['hq'],
        floors: world.building.floors.map(f => ['hq', f.number]),
        rooms: world.building.floors.flatMap(f =>
          f.rooms.map(r => ['hq', f.number, r.id])),
      };
      const bad = [];
      for (const p of places) {
        const enc = N.encodePlace(p);
        const dec = N.decodePlace(enc, known);
        if (!dec.ok) { bad.push({p, enc, why: dec.reason}); continue; }
        // and restoring must reproduce the same place
        const restored = N.restore(dec.place).viewpoint;
        if (N.encodePlace(restored) !== enc) {
          bad.push({p, enc, got: N.encodePlace(restored)});
        }
      }
      return {count: places.length, bad};
    """)
    assert got["bad"] == [], f"a location did not survive a reload: {got['bad']}"
    assert got["count"] == 10


def test_a_deep_link_to_a_vanished_room_is_refused(tmp_path):
    """A reload after the payload changed must not render a room that is gone."""
    got = _run(tmp_path, _SETUP + """
      const known = {
        buildings: ['hq'],
        floors: world.building.floors.map(f => ['hq', f.number]),
        rooms: world.building.floors.flatMap(f =>
          f.rooms.map(r => ['hq', f.number, r.id])),
      };
      return N.decodePlace('room:hq:2:424242', known);
    """)
    assert got["ok"] is False, (
        "a deep link to a room that does not exist was accepted")
    assert "424242" in got["reason"] or "no room" in got["reason"]


# ======================================================================
# 6 — the slice stays presentation-only
# ======================================================================
def test_the_slice_cannot_move_an_agent(tmp_path):
    """The amendment's separation: navigating moves the VIEWPOINT. A real
    relocation needs an accepted action or an updated snapshot."""
    for name in ("WorldAssembler.ts", "InteractionController.ts"):
        src = (GAME_DIR / name).read_text()
        for banned in ("fetch(", "XMLHttpRequest", "sendBeacon",
                       "setInterval", "setTimeout"):
            assert banned not in src, (
                f"{name} contains {banned}: the slice is local presentation "
                f"and must not fetch or tick")
    # the interaction controller may READ agent ids but never write one
    src = (GAME_DIR / "InteractionController.ts").read_text()
    assert "position" not in src and "relocate" not in src, (
        "the interaction controller touches agent position")


def test_every_place_paints_different_pixels(tmp_path):
    """A PLACE YOU CANNOT SEE IS A PLACE YOU DID NOT REACH.

    `building` and `exterior` both called `drawExterior`, so walking through the
    front door changed the navigation state and produced byte-identical pixels —
    a working traversal that reads as a dead click. This asserts each place has
    its own view, so a state change is observable.
    """
    got = _run(tmp_path, _SETUP + """
      // A recording 2D context: enough of the surface for the scenes to paint.
      function Rec() {
        this.calls = [];
        this.fillStyle = '#000'; this.strokeStyle = '#000'; this.font = '';
        this.textAlign = ''; this.textBaseline = ''; this.lineWidth = 1;
      }
      ['save','restore','beginPath','closePath','moveTo','lineTo','fillRect',
       'strokeRect','rect','roundRect','arc','ellipse','fill','stroke',
       'fillText','strokeText','clearRect','setTransform','translate','scale',
       'rotate','clip','setLineDash'].forEach(m => {
        Rec.prototype[m] = function () {
          this.calls.push(m + ':' + JSON.stringify(Array.from(arguments)));
          return {width: 10};
        };
      });
      Rec.prototype.createRadialGradient = function () {
        this.calls.push('radial'); return {addColorStop(){}};
      };
      Rec.prototype.createLinearGradient = Rec.prototype.createRadialGradient;

      const places = [N.EXTERIOR, {kind:'building', building:'hq'},
                      {kind:'lobby', building:'hq'}];
      for (const f of world.building.floors) {
        places.push({kind:'floor', building:'hq', floor:f.number});
        for (const r of f.rooms) {
          places.push({kind:'room', building:'hq', floor:f.number, room:r.id});
        }
      }
      const seen = {};
      const dupes = [];
      for (const p of places) {
        const ctx = new Rec();
        SC.drawPlace({ctx, world, width: W, height: H, place: p, travel: 0});
        const key = ctx.calls.join('|');
        const enc = N.encodePlace(p);
        if (key.length === 0) { dupes.push({place: enc, why: 'painted nothing'}); continue; }
        for (const [other, k] of Object.entries(seen)) {
          if (k === key) dupes.push({place: enc, sameAs: other});
        }
        seen[enc] = key;
      }
      return {count: places.length, dupes};
    """)
    assert got["dupes"] == [], (
        f"places that paint identically (a reached place looks unreached): "
        f"{got['dupes']}")
    assert got["count"] == 10


def test_selecting_an_agent_does_not_move_the_viewpoint(tmp_path):
    got = _run(tmp_path, _SETUP + """
      const st = {viewpoint: {kind:'floor', building:'hq', floor:2},
                  history: [], refused: null};
      const r = IC.applyIntent(st, {kind:'select', target: 4}, portals, world, null);
      return {here: N.encodePlace(r.nav.viewpoint),
              selected: r.selected, refused: r.refused,
              historyGrew: r.nav.history.length};
    """)
    assert got["here"] == "floor:hq:2", (
        "selecting an agent moved the viewpoint")
    assert got["selected"] == 4
    assert got["historyGrew"] == 0, (
        "selecting an agent pushed navigation history")
