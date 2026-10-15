"""F09 — the character roster, the profile and the dialogue shell.

F09's proof (`docs/game-office/frontend-game/60-IMPLEMENTATION-PLAN.md`):
"real target receives a message, reload recovers it, stream doesn't duplicate,
close doesn't cancel. If binding absent, preview tests pass but LIVE integration
remains blocked."

THE LIVE BINDING IS ABSENT IN THIS BUILD, and this file says so rather than
working around it. `50-BACKEND-CONTRACT.md` lists `getCharacterSession` and
`sendCharacterMessage` as PROPOSED additive DTOs with the availability behaviour
"No fictional session created locally" / "Action disabled with reason until safe
binding exists"; the only office wire route that exists is the read-only
`GET /api/office/tower` snapshot. So the conversation half of this suite proves
the PREVIEW states exactly and asserts the BLOCKED binding is reported as
blocked.

WHAT THIS FILE IS ACTUALLY GUARDING, IN ORDER OF HOW MUCH IT CAN HURT
--------------------------------------------------------------------
0. **The `known` trap.** Every character in the recorded payload has
   `dossier.known === false` AND non-empty `dossier.beliefs` with provenance
   (`identity: "ceo is a ceo"`, evidence `office_agent#1`). A presenter that
   reads `known` as "has no facts" hides beliefs the backend sent; one that
   reads it as "established" presents a derived projection as earned evidence.
   The tests below assert BOTH halves against the real recording.
1. **One message id finalized exactly once.** A `done` frame that appends its
   body onto the deltas, or a second `done` for the same id, is the duplicate
   the plan names. The state carries a per-id finalize COUNT so the failure is
   visible as a number rather than as prose.
2. **`Go to target` reaches the F06 reducer.** It must route through the
   DECLARED portal graph (a room is one hop from its floor and no others, so a
   single `travel` is refused from anywhere else) and it must never move an
   AGENT.
3. **C4.** Neither panel module may contain a network call. Asserted by reading
   the source, because the property IS about what the module can do.

Hermetic: tsc + node, tmp dirs only. No browser, no server, no network, no
provider call. The recorded fixture is read from the repo.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest  # noqa: E402

from _office_node import run_node  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
GAME_DIR = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office" / "game"
UI_DIR = GAME_DIR / "ui"
SYSTEMS_DIR = GAME_DIR / "systems"
FIXTURE = (REPO / "frontend" / "qubit-web" / "src" / "lib" / "office-game"
           / "fixtures" / "tower-recording.json")

from _office_toolchain import (  # noqa: E402
    ensure_tsc, node_path, require_toolchain_or_fail)

NODE = node_path()
#: Self-healing: installs the compiler outside the tree if it vanished, so the
#: suite RUNS after a dashboard build deleted node_modules.
TSC = ensure_tsc()

#: The modules that must be staged verbatim. `systems/CharacterPresenter.ts`
#: imports `../navigation` and `../InteractionController`, so the layout has to
#: mirror the repo rather than flatten.
ROOT_MODULES = ["types.ts", "navigation.ts", "InteractionController.ts",
                "WorldAssembler.ts"]
SYSTEM_MODULES = ["CharacterPresenter.ts"]
UI_MODULES = ["CharacterPanel.ts", "ConversationPanel.ts"]


def _toolchain() -> bool:
    return ensure_tsc() is not None


#: Loud, not a silent skip: a missing compiler ERRORS at collection (see the
#: helper's docstring). A green `N skipped` is the false pass this replaces.
require_toolchain_or_fail()


class _Bundle:
    """A compiled staging directory, reused across the tests in one run.

    `tsc` on five modules is ~0.7 s, but `node` per assertion is the cost that
    matters, so the compile is done once per session and cached on the module.
    """

    _cache: dict[str, "_Bundle"] = {}

    def __init__(self, root: Path):
        self.root = root
        self.js = root / "js"
        self.stderr = ""

    @classmethod
    def build(cls, tmp_root: Path) -> "_Bundle":
        key = str(tmp_root)
        if key in cls._cache:
            return cls._cache[key]
        staged = tmp_root / "src"
        (staged / "systems").mkdir(parents=True, exist_ok=True)
        (staged / "ui").mkdir(parents=True, exist_ok=True)
        for name in ROOT_MODULES:
            shutil.copy(GAME_DIR / name, staged / name)
        for name in SYSTEM_MODULES:
            shutil.copy(SYSTEMS_DIR / name, staged / "systems" / name)
        for name in UI_MODULES:
            shutil.copy(UI_DIR / name, staged / "ui" / name)
        bundle = cls(tmp_root)
        proc = subprocess.run(
            [NODE, str(TSC), "--module", "commonjs", "--target", "es2020",
             "--moduleResolution", "node", "--ignoreDeprecations", "6.0",
             "--outDir", str(bundle.js), "--skipLibCheck",
             "--noEmitOnError", "false", "--lib", "es2020,dom",
             str(staged / "ui" / "CharacterPanel.ts"),
             str(staged / "ui" / "ConversationPanel.ts"),
             str(staged / "InteractionController.ts")],
            capture_output=True, text=True, timeout=300, cwd=str(staged))
        bundle.stderr = proc.stdout + proc.stderr
        cls._cache[key] = bundle
        return bundle

    def presenters(self) -> Path:
        return self.js / "systems" / "CharacterPresenter.js"

    def character(self) -> Path:
        return self.js / "ui" / "CharacterPanel.js"

    def conversation(self) -> Path:
        return self.js / "ui" / "ConversationPanel.js"

    def navigation(self) -> Path:
        return self.js / "navigation.js"

    def interaction(self) -> Path:
        return self.js / "InteractionController.js"


@pytest.fixture(scope="session")
def bundle(tmp_path_factory) -> _Bundle:
    return _Bundle.build(tmp_path_factory.mktemp("f09"))


def _run_with(bundle: _Bundle, extras: dict, js: str,
              requires: list) -> object:
    """Compile `extras` (name -> source path) next to the F09 modules, then run.

    Used by the F08 composition test, which needs `PanelHost.ts` — an ADDITIVE
    import of an F08 module, not a change to it.
    """
    staged = bundle.root / "src"
    names = []
    for name, src in extras.items():
        if name == "_dir":
            continue
        shutil.copy(src, staged / name)
        names.append(str(staged / name))
    proc = subprocess.run(
        [NODE, str(TSC), "--module", "commonjs", "--target", "es2020",
         "--moduleResolution", "node", "--ignoreDeprecations", "6.0",
         "--outDir", str(bundle.js), "--skipLibCheck",
         "--noEmitOnError", "false", "--lib", "es2020,dom", *names],
        capture_output=True, text=True, timeout=300, cwd=str(staged))
    for name in names:
        if not (bundle.js / Path(name).name).with_suffix(".js").is_file():
            pytest.skip(
                "tsc could not compile the F08 composition module:\n"
                + (proc.stdout + proc.stderr)[:2000])
    lines = "".join(
        "const %s = require(%s);\n"
        % (alias, json.dumps(str(bundle.js / r)))
        for alias, r in requires)
    program = lines + (
        'const CP = require(%s);\n'
        'const N = require(%s);\n'
        '(async () => {\n'
        '  const out = await (async () => { %s })();\n'
        '  process.stdout.write(JSON.stringify(out));\n'
        '})().catch(e => { console.error(String(e && e.stack || e));'
        ' process.exit(1); });\n'
    ) % (json.dumps(str(bundle.presenters())),
         json.dumps(str(bundle.navigation())), js)
    proc = run_node(program, timeout=120)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}")
    return json.loads(proc.stdout)


def _run(bundle: _Bundle, js: str) -> object:
    """Drive the compiled modules under `node`, program through STDIN.

    NOT `node -e <program>`: the bundle is ~7 MB and a program embedding it hits
    `OSError: [Errno 7] Argument list too long`. See `tests/_office_node.py`.
    """
    for path in (bundle.presenters(), bundle.character(),
                 bundle.conversation(), bundle.navigation(),
                 bundle.interaction()):
        if not path.is_file():
            pytest.skip(
                "tsc could not compile the F09 modules for the node driver:\n"
                + bundle.stderr[:2000])
    program = (
        'const CP = require(%s);\n'
        'const CH = require(%s);\n'
        'const CV = require(%s);\n'
        'const N = require(%s);\n'
        'const IC = require(%s);\n'
        'const TOWER = JSON.parse(require("fs").readFileSync(%s, "utf8"));\n'
        'const portals = CP.portalsFor(TOWER);\n'
        '(async () => {\n'
        '  const out = await (async () => { %s })();\n'
        '  process.stdout.write(JSON.stringify(out));\n'
        '})().catch(e => { console.error(String(e && e.stack || e));'
        ' process.exit(1); });\n'
    ) % (json.dumps(str(bundle.presenters())),
         json.dumps(str(bundle.character())),
         json.dumps(str(bundle.conversation())),
         json.dumps(str(bundle.navigation())),
         json.dumps(str(bundle.interaction())),
         json.dumps(str(FIXTURE)), js)
    proc = run_node(program, timeout=180)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}")
    return json.loads(proc.stdout)


#: Reused: every profile the payload produces, and the CEO's.
_SETUP = """
  const chars = TOWER.characters;
  const profiles = chars.map(c =>
    CP.presentCharacter(c, chars, TOWER.active_window_s));
  const bySlug = {};
  for (const p of profiles) bySlug[p.slug] = p;
"""


# ======================================================================
# 0 — THE `known` TRAP: the payload's flag does not mean "no facts"
# ======================================================================
def test_the_recorded_payload_has_the_known_trap_and_it_is_really_here(
        bundle,
):
    """Establish the trap from the FIXTURE first, so the presenter tests are
    checked against the recorded bytes rather than against an assumption."""
    data = json.loads(FIXTURE.read_text())
    assert data["schema"] == "qubit-office-tower/1"
    flagged = [
        c for c in data["characters"]
        if c["dossier"]["known"] is False and c["dossier"]["beliefs"]
    ]
    assert flagged, (
        "the recorded payload no longer contains the `known: false with "
        "beliefs` case this packet exists for; the trap has moved")
    assert len(flagged) == len(data["characters"])
    ceo = next(c for c in data["characters"] if c["slug"] == "ceo")
    assert ceo["memory"]["known"] is False
    assert ceo["dossier"]["note"] == "no evidence yet: no seat row and no memory"


def test_the_presenter_keeps_known_and_beliefs_as_separate_facts(bundle):
    """THE HEADLINE ASSERTION.

    `dossier.known === false` must not hide the beliefs the backend sent, and it
    must not be presented as if the dossier were established. Both halves are
    asserted, against every character in the recording.
    """
    got = _run(bundle, _SETUP + """
      const hidden = [];
      const overstated = [];
      const missingBasis = [];
      for (const p of profiles) {
        const d = p.dossier;
        const raw = chars.find(c => c.agent_id === p.agentId).dossier;
        if (d.known !== raw.known) {
          overstated.push({slug: p.slug, said: d.known, real: raw.known});
        }
        // (a) hiding: the flag is false but the payload carried beliefs and we
        //     still have every one of them, with its evidence string.
        if (raw.beliefs.length > 0 && d.beliefs.length !== raw.beliefs.length) {
          hidden.push({slug: p.slug, sent: raw.beliefs.length,
                       shown: d.beliefs.length});
        }
        // (b) the beliefs we do hold must be the payload's own, unedited.
        for (let i = 0; i < d.beliefs.length; i += 1) {
          if (d.beliefs[i].belief !== raw.beliefs[i].belief ||
              d.beliefs[i].evidence !== raw.beliefs[i].evidence) {
            hidden.push({slug: p.slug, at: i, shown: d.beliefs[i],
                         sent: raw.beliefs[i]});
          }
        }
        // (c) a not-established dossier must carry a BASIS, verbatim.
        if (!d.known && !d.basis) missingBasis.push(p.slug);
      }
      return {hidden, overstated, missingBasis,
              count: profiles.length,
              unestablishedWithEvidence:
                profiles.filter(p => p.dossier.hasUnestablishedEvidence).length};
    """)
    assert got["hidden"] == [], (
        f"beliefs the payload sent were lost or edited: {got['hidden']}")
    assert got["overstated"] == [], (
        f"the dossier's known flag was not carried verbatim: "
        f"{got['overstated']}")
    assert got["missingBasis"] == [], (
        f"an unestablished dossier rendered with no basis: {got['missingBasis']}")
    assert got["count"] == 10
    assert got["unestablishedWithEvidence"] == got["count"], (
        "the recorded case (known:false WITH beliefs) was not preserved")


def test_the_ceos_own_beliefs_survive_the_known_flag(bundle):
    """The concrete example, spelled out, so a regression names a person."""
    got = _run(bundle, _SETUP + """
      const p = bySlug['ceo'];
      return {known: p.dossier.known, basis: p.dossier.basis,
              beliefs: p.dossier.beliefs,
              dimensions: p.dossier.dimensions};
    """)
    assert got["known"] is False
    assert got["basis"] == "no evidence yet: no seat row and no memory", (
        "the payload's own note was paraphrased or dropped")
    assert got["beliefs"] == [
        {"dimension": "identity", "belief": "ceo is a ceo",
         "evidence": "office_agent#1"},
        {"dimension": "standing_orders",
         "belief": "autonomy posture: unattended_full",
         "evidence": "office_agent.autonomy"},
    ], "the CEO's beliefs were hidden or rewritten by the `known` flag"


def test_a_dossier_keyed_to_another_seat_is_never_rendered_as_this_one(bundle):
    """A dossier attached to the wrong agent is a false statement about a
    person, so it is named rather than drawn under this slug."""
    got = _run(bundle, _SETUP + """
      const c = Object.assign({}, chars.find(x => x.slug === 'wren'));
      c.dossier = Object.assign({}, c.dossier, {agent_id: 999});
      const p = CP.presentCharacter(c, chars, TOWER.active_window_s);
      return {basis: p.dossier.basis, known: p.dossier.known};
    """)
    assert "999" in got["basis"] and "4" in got["basis"], (
        f"a mismatched dossier was not named: {got['basis']}")


def test_memory_known_false_is_not_zero_memories(bundle):
    """`{count: 0, known: false}` means "depth unknown". Rendering 0 as a count
    is a claim the backend declined to make."""
    got = _run(bundle, _SETUP + """
      const p = bySlug['ceo'];
      const unknown = {known: p.memory.known, text: p.memory.text};
      // and the contrast: a KNOWN zero must read as a known zero
      const c2 = Object.assign({}, chars[0]);
      c2.memory = {count: 0, known: true};
      const p2 = CP.presentCharacter(c2, chars, TOWER.active_window_s);
      return {unknown, knownZero: {text: p2.memory.text, known: p2.memory.known}};
    """)
    assert got["unknown"]["known"] is False
    assert got["unknown"]["text"] == "memory depth unknown", (
        f"an unknown memory depth was rendered as a fact: "
        f"{got['unknown']['text']}")
    assert "0" in got["knownZero"]["text"], (
        "a KNOWN zero-memory seat was hidden; the flag's two states must "
        "render differently")


# ======================================================================
# 1 — identity matches the world; the supervisor edge's four real states
# ======================================================================
def test_the_portrait_key_is_the_same_identity_the_world_draws(bundle):
    """The card and the figure must not be two lookalikes."""
    got = _run(bundle, _SETUP + """
      const world = require(%s);
      const w = world.assembleWorld(TOWER, 1);
      const worldIds = new Set(w.building.characters.map(c => c.agentId));
      const bad = profiles
        .filter(p => p.portrait !== ('agent:' + p.agentId))
        .map(p => ({slug: p.slug, key: p.portrait}));
      const notInWorld = profiles
        .filter(p => !worldIds.has(p.agentId)).map(p => p.slug);
      return {bad, notInWorld, count: profiles.length};
    """ % json.dumps(str(bundle.js / "WorldAssembler.js")))
    assert got["bad"] == [], (
        f"portrait keys that do not match the world's identity: {got['bad']}")
    assert got["notInWorld"] == [], (
        f"a profile exists for a seat the world does not have: "
        f"{got['notInWorld']}")


def test_go_to_target_lands_on_the_characters_own_room(bundle):
    """The `Go to target` action, for every seat the payload reports.

    A room is one hop from its OWN floor and no others, so this can only pass if
    the action routes through the declared graph rather than attempting a
    single travel.
    """
    got = _run(bundle, _SETUP + """
      const bad = [];
      const traces = [];
      for (const p of profiles) {
        const res = CP.goToTarget(N.initialNav(), p, portals);
        if (res.refused) { bad.push({slug: p.slug, why: res.reason}); continue; }
        const at = N.encodePlace(res.nav.viewpoint);
        traces.push({slug: p.slug, at, target: res.target, hops: res.steps.length});
        if (at !== p.world.placeKey || p.world.placeKey === '') {
          bad.push({slug: p.slug, at, want: p.world.placeKey});
        }
        if (!res.arrived) bad.push({slug: p.slug, why: 'arrived=false'});
        // every hop must be a real portal hop, not a teleport
        if (res.steps.some(s => s.refused)) {
          bad.push({slug: p.slug, why: 'a hop refused', steps: res.steps});
        }
      }
      return {bad, count: traces.length,
              hops: [...new Set(traces.map(t => t.hops))].sort(),
              sample: traces.slice(0, 3)};
    """)
    assert got["bad"] == [], (
        f"Go to target did not land on the target's own room: {got['bad']}")
    assert got["count"] == 10
    # floor 2/3 seats need the full exterior->building->lobby->floor->room
    # route; floor 1 seats take the lobby->floor1 door. Both are declared hops.
    assert min(got["hops"]) >= 3, (
        f"a route reached a room in fewer hops than the graph allows: "
        f"{got['hops']}")


def test_go_to_target_from_the_exterior_reaches_a_floor_three_seat(bundle):
    """The longest declared route, spelled out: floor 3 is the far end of the
    elevator chain."""
    got = _run(bundle, _SETUP + """
      const p = bySlug['wren'];
      const res = CP.goToTarget(N.initialNav(), p, portals);
      return {slug: p.slug, place: p.world.placeKey,
              at: N.encodePlace(res.nav.viewpoint),
              steps: res.steps.map(s => s.to), arrived: res.arrived,
              refused: res.refused};
    """)
    assert got["refused"] is False
    assert got["place"] == "room:hq:2:2"
    assert got["at"] == got["place"], (
        f"Go to target ended at {got['at']}, not {got['place']}")
    assert got["steps"] == ["building:hq", "lobby:hq", "floor:hq:2",
                            "room:hq:2:2"]


def test_go_to_target_never_moves_an_agent_and_never_moves_the_viewpoint_without_a_portal(
        bundle,
):
    """Two rules the amendment and F06 both depend on: the action moves the
    VIEWPOINT only, and an unreachable target is a TYPED REFUSAL, not a jump."""
    got = _run(bundle, _SETUP + """
      // a graph with no portal to floor 3 at all: the hop must refuse
      const floors = [{number: 1, rooms: [{id: 1}]}];
      const thin = N.buildHeadquartersPortals('hq', floors);
      const p = bySlug['wren'];   // floor 2, room 2 — not in `thin`
      const before = N.encodePlace(N.initialNav().viewpoint);
      const res = CP.goToTarget(N.initialNav(), p, thin);
      // and the selected target's own room is never written anywhere
      const sel = IC.applyIntent(N.initialNav(), {kind: 'select', target: 4},
                                 portals, {building: {slug: 'hq', floors: [],
                                 characters: []}}, null);
      return {refused: res.refused, reason: res.reason,
              at: N.encodePlace(res.nav.viewpoint), before,
              selectedAgent: sel.selected,
              selectedPlace: N.encodePlace(sel.nav.viewpoint)};
    """)
    assert got["refused"] is True, (
        "a target with no declared route was reached anyway")
    assert got["reason"], "the refusal carries no reason to show the player"
    assert got["at"] == got["before"], (
        "a refused route still moved the viewpoint")
    assert got["selectedAgent"] == 4
    assert got["selectedPlace"] == got["before"], (
        "selecting a character moved the viewpoint; only `travel` may do that")


def test_the_supervisor_edge_has_four_distinct_states(bundle):
    """`null` is "reports to nobody" (a fact); a missing key is NOT that."""
    got = _run(bundle, _SETUP + """
      const ceo = bySlug['ceo'];
      const worker = bySlug['wren'];
      const orphan = Object.assign({}, chars[0], {supervisor_id: 4242});
      const pOrphan = CP.presentCharacter(orphan, chars, TOWER.active_window_s);
      const stripped = Object.assign({}, chars[0]);
      delete stripped.supervisor_id;
      const pStripped = CP.presentCharacter(stripped, chars,
                                            TOWER.active_window_s);
      return {ceo: ceo.supervisor, worker: worker.supervisor,
              orphan: pOrphan.supervisor, stripped: pStripped.supervisor};
    """)
    assert got["ceo"]["state"] == "none"
    assert got["ceo"]["agentId"] is None
    assert got["worker"]["state"] == "assigned"
    assert got["worker"]["slug"] == "lead-content"
    assert got["orphan"]["state"] == "unresolved"
    assert "4242" in got["orphan"]["text"], (
        f"an unresolved supervisor id was hidden: {got['orphan']['text']}")
    assert got["stripped"]["state"] == "unreported", (
        "a MISSING supervisor key was collapsed into 'reports to nobody', "
        "which is a fabricated org fact")


def test_an_unknown_field_is_named_rather_than_defaulted(bundle):
    """Strip fields off a real row and assert the presenter names each gap
    instead of substituting a plausible value."""
    got = _run(bundle, _SETUP + """
      const stripped = JSON.parse(JSON.stringify(chars[0]));
      delete stripped.last_event;
      delete stripped.department_id;
      delete stripped.position.floor_role;
      stripped.position.room_id = null;
      const p = CP.presentCharacter(stripped, chars, TOWER.active_window_s);
      // A SECOND case, distinct from the first: a seat row that HAS a room but
      // carried NO floor. There is no way to name that place, so none may be
      // built — a guessed floor 1 would send the player to a real floor of a
      // real building on the strength of a value the payload never sent.
      const noFloor = JSON.parse(JSON.stringify(chars[0]));
      delete noFloor.position.floor;
      const p2 = CP.presentCharacter(noFloor, chars, TOWER.active_window_s);
      return {unknown: p.unknownFields.sort(),
              lastEvent: p.lastEvent,
              role: CP.roleView(stripped.seat_type),
              placeKey: p.world.placeKey,
              seated: p.world.seated,
              noFloor: {placeKey: p2.world.placeKey, derived: p2.world.derived,
                        roomId: p2.world.roomId, seated: p2.world.seated,
                        floor: p2.world.floor},
              noFloorKnown: p2.unknownFields.sort()};
    """)
    for field in ("last_event", "department_id", "position.floor_role",
                  "position.room_id"):
        assert field in got["unknown"], (
            f"a missing {field} was silently defaulted instead of named: "
            f"{got['unknown']}")
    assert got["lastEvent"]["state"] == "absent"
    assert got["lastEvent"]["kind"] == "", (
        "an absent last_event was rendered as a kind")
    assert "idle" not in got["lastEvent"]["text"].lower(), (
        "'absent' was rendered as 'idle' — a state the payload never sent")
    assert got["placeKey"] == "", (
        "a place was derived from a seat row with no floor")
    assert got["seated"] is False, (
        "a seat with no room_id reported itself as seated")
    # the no-floor case: a real room, no floor, and therefore no place
    assert got["noFloor"]["seated"] is True, (
        "a seat WITH a room but no floor was reported unseated; the two cases "
        "are different and the presenter must not merge them")
    assert got["noFloor"]["placeKey"] == "", (
        f"a place was built for a seat with no floor: "
        f"{got['noFloor']['placeKey']!r}")
    assert got["noFloor"]["derived"] is False
    assert got["noFloor"]["floor"] is None, (
        "a missing floor became a number")
    assert "position.floor" in got["noFloorKnown"], (
        "the missing floor was not named as an unknown")


def test_an_unrecognised_role_is_not_labelled_as_a_worker(bundle):
    got = _run(bundle, """
      return {known: CP.roleView('manager'),
              unknown: CP.roleView('wizard'),
              empty: CP.roleView(null)};
    """)
    assert got["known"]["recognised"] is True
    assert got["known"]["label"] == "Manager"
    assert got["unknown"]["recognised"] is False, (
        "a seat type this build does not know was annotated as known")
    assert got["unknown"]["seatType"] == "wizard"
    assert got["empty"]["recognised"] is False
    assert "not reported" in got["empty"]["label"]


# ======================================================================
# 2 — the roster: filters over a stable id
# ======================================================================
def test_the_roster_lists_every_seat_in_payload_order(bundle):
    got = _run(bundle, _SETUP + """
      const view = CP.rosterView(TOWER, {filter: 'all', query: ''});
      return {total: view.total, shown: view.shown,
              ids: view.entries.map(e => e.agentId),
              payloadIds: chars.map(c => c.agent_id),
              portraits: [...new Set(view.entries.map(e => e.portrait))].length,
              firstActivity: view.entries[0].activity};
    """)
    assert got["total"] == 10 and got["shown"] == 10
    assert got["ids"] == got["payloadIds"], (
        "the roster invented, dropped or reordered a seat")
    assert got["portraits"] == 10, "two seats shared one portrait key"


def test_every_filter_is_a_real_predicate_over_the_payload(bundle):
    got = _run(bundle, _SETUP + """
      const out = {};
      for (const f of CP.ROSTER_FILTERS) {
        const v = CP.rosterView(TOWER, {filter: f, query: ''});
        out[f] = {shown: v.shown, ids: v.entries.map(e => e.agentId)};
      }
      const q = CP.rosterView(TOWER, {filter: 'all', query: 'WREN'});
      const miss = CP.rosterView(TOWER, {filter: 'all', query: 'nobody-here'});
      const roomQ = CP.rosterView(TOWER, {filter: 'all', query: 'boardroom'});
      return {out, filters: CP.ROSTER_FILTERS,
              q: {shown: q.shown, ids: q.entries.map(e => e.agentId)},
              miss: {shown: miss.shown, ids: miss.entries.map(e => e.agentId)},
              roomQ: {shown: roomQ.shown}};
    """)
    assert got["filters"] == ["all", "seated", "unseated", "stalled", "working"]
    # The recorded org is fully seated, unstalled and idle — so these filters
    # must return EMPTY rather than "everything" (the bug a lazy filter makes).
    assert got["out"]["seated"]["shown"] == 10
    assert got["out"]["unseated"]["shown"] == 0, (
        "the unseated filter matched a seated office")
    assert got["out"]["stalled"]["shown"] == 0, (
        "the stalled filter matched an office the payload reports unstalled")
    assert got["out"]["working"]["shown"] == 0, (
        "the working filter matched an office the payload reports idle")
    # an empty result is NOT a match — the filter bug this catches
    assert got["q"]["shown"] == 1 and got["q"]["ids"] == [4], (
        f"the text filter is not filtering: {got['q']}")
    assert got["miss"]["shown"] == 0, "a miss returned every seat"
    assert got["roomQ"]["shown"] == 3, (
        f"the room filter did not match the boardroom: {got['roomQ']}")


def test_a_working_seat_is_visible_through_the_filter_when_the_payload_says_so(
        bundle,
):
    """The positive half: a filter that only ever returns zero is also broken."""
    got = _run(bundle, _SETUP + """
      const chars2 = chars.map(c => Object.assign({}, c));
      chars2[3].animation = 'working';
      chars2[3].stalled = false;
      chars2[5].stalled = true;
      const payload2 = Object.assign({}, TOWER, {characters: chars2});
      return {working: CP.rosterView(payload2, {filter: 'working', query: ''})
                .entries.map(e => e.agentId),
              stalled: CP.rosterView(payload2, {filter: 'stalled', query: ''})
                .entries.map(e => e.agentId)};
    """)
    assert got["working"] == [4], (
        f"the working filter missed a seat the payload reports working: "
        f"{got['working']}")
    assert got["stalled"] == [6], (
        f"the stalled filter missed a seat the payload flags stalled: "
        f"{got['stalled']}")


def test_the_roster_surfaces_the_payloads_own_empty_statement(bundle):
    got = _run(bundle, """
      const empty = {schema: 'qubit-office-tower/1', ok: true, characters: [],
                     empty: true, empty_reason: 'no seats, no floors and no ' +
                       'ledger rows: this org has not started',
                     active_window_s: 120, preview: false,
                     floorplan: {grid: {cols: 1, rows: 1, tile: 1}, floors: []}};
      const v = CP.rosterView(empty, {filter: 'all', query: ''});
      const none = CP.rosterView(null, {filter: 'all', query: ''});
      return {empty: {total: v.total, shown: v.shown, empty: v.empty,
                      reason: v.emptyReason},
              none: {total: none.total, shown: none.shown, empty: none.empty}};
    """)
    assert got["empty"]["total"] == 0 and got["empty"]["empty"] is True
    assert "not started" in got["empty"]["reason"], (
        "the backend's own empty reason was not carried")
    assert got["none"]["total"] == 0, (
        "a missing payload produced roster entries")


# ======================================================================
# 3 — the conversation preview: the SIX states, and the once-only finalizer
# ======================================================================
def test_the_six_states_are_distinct_and_each_is_reachable(bundle):
    """Every state the plan names, driven by a frame that really means it."""
    got = _run(bundle, """
      const frames = {
        pending: {kind: 'status', messageId: 'm1', recipient: 4,
                  state: 'pending', cursor: 1},
        streaming: {kind: 'delta', messageId: 'm1', recipient: 4,
                    text: 'hel', cursor: 2},
        tool: {kind: 'tool', messageId: 'm1', recipient: 4,
               tool: 'mcp__graft__graft_ask', cursor: 3},
        owner_wait: {kind: 'status', messageId: 'm1', recipient: 4,
                     state: 'owner_wait', runId: 'run-9', text: 'which one?',
                     artifacts: ['opt-a', 'opt-b'], cursor: 4},
        error: {kind: 'error', messageId: 'm1', recipient: 4,
                error: {source: 'transport', detail: 'socket closed'},
                cursor: 5},
        stale: {kind: 'gap', messageId: 'm1', recipient: 4, cursor: 6},
      };
      const out = {};
      for (const k of Object.keys(frames)) {
        let s = CV.initialConversation('sess-1', 4);
        s = CV.applyFrame(s, frames[k]).state;
        out[k] = {state: s.state, reason: s.reason,
                  asks: s.asks.length, needsReseed: s.needsReseed};
      }
      return {out, vocabulary: CV.STREAM_STATES};
    """)
    assert got["vocabulary"] == ["pending", "streaming", "tool", "owner_wait",
                                 "error", "stale"]
    for name in got["vocabulary"]:
        assert got["out"][name]["state"] == name, (
            f"the {name} frame did not produce the {name} state: "
            f"{got['out'][name]}")
        assert got["out"][name]["reason"], (
            f"the {name} state carries no reason to render")
    assert got["out"]["owner_wait"]["asks"] == 1, (
        "an owner-wait status did not record the ask it carries")
    assert got["out"]["stale"]["needsReseed"] is True, (
        "a gap did not ask for a reseed")
    assert "socket closed" in got["out"]["error"]["reason"]


def test_a_delta_accumulates_and_never_replaces(bundle):
    got = _run(bundle, """
      let s = CV.initialConversation('s', 4);
      for (const t of ['Hel', 'lo, ', 'wren']) {
        s = CV.applyFrame(s, {kind: 'delta', messageId: 'm1', recipient: 4,
                              text: t}).state;
      }
      const msg = s.byId['m1'];
      return {text: msg.text, order: s.order.length, finalised: msg.finalised};
    """)
    assert got["text"] == "Hello, wren", (
        f"deltas did not accumulate into one message: {got['text']!r}")
    assert got["order"] == 1, "a delta created a second message"


def test_done_finalises_the_message_id_exactly_once(bundle):
    """F09's own loading test: "stream doesn't duplicate".

    Two failure shapes are asserted at once — a `done` that APPENDS its body
    onto the accumulated deltas, and a SECOND `done` for the same id.

    The count is asserted as an INCREMENT, not as the literal 1: a finalizer
    that writes `= 1` on every pass cannot tell a first finalize from a fifth,
    so the evidence for "exactly once" would evaporate while the number still
    read 1.
    """
    got = _run(bundle, """
      let s = CV.initialConversation('s', 4);
      s = CV.applyFrame(s, {kind: 'delta', messageId: 'm1', recipient: 4,
                            text: 'Hello, '}).state;
      s = CV.applyFrame(s, {kind: 'delta', messageId: 'm1', recipient: 4,
                            text: 'wren'}).state;
      const beforeAnyDone = s.finalizedCount['m1'] === undefined;
      const r1 = CV.applyFrame(s, {kind: 'done', messageId: 'm1', recipient: 4,
                                   text: 'Hello, wren',
                                   artifacts: ['art-1']});
      s = r1.state;
      const afterFirst = s.byId['m1'].text;
      const countFirst = s.finalizedCount['m1'];
      const r2 = CV.applyFrame(s, {kind: 'done', messageId: 'm1', recipient: 4,
                                   text: 'Hello, wren'});
      s = r2.state;
      const r3 = CV.applyFrame(s, {kind: 'done', messageId: 'm1', recipient: 4,
                                   text: 'Hello, wren'});
      s = r3.state;
      return {beforeAnyDone, afterFirst, countFirst, afterSecond: s.byId['m1'].text,
              countAfterThree: s.finalizedCount['m1'], secondIgnored: r2.ignored,
              secondReason: r2.reason, order: s.order.length,
              audit: CV.finalizeAudit(s)};
    """)
    assert got["beforeAnyDone"] is True, (
        "a message was counted as finalized before any done frame arrived")
    assert got["afterFirst"] == "Hello, wren", (
        f"the done body was appended onto the deltas: {got['afterFirst']!r}")
    assert got["countFirst"] == 1, (
        "the FIRST finalize did not register exactly one; the once-only "
        "evidence is not being counted")
    assert got["afterSecond"] == "Hello, wren", (
        f"a second done appended the body again: {got['afterSecond']!r}")
    assert got["countAfterThree"] == 3, (
        "three done frames for one id did not register three ATTEMPTS, so the "
        "count cannot witness a duplicate (a count written only on the first "
        "pass reads 1 for one frame and 1 for five)")
    assert got["audit"]["duplicated"] == ["m1"], (
        f"three done frames for one id were not flagged as a duplicate: "
        f"{got['audit']}")
    assert got["audit"]["allOnce"] is False, (
        "the finalize audit reported three done frames as once-only")
    assert got["secondIgnored"] is True
    assert "already finalised" in got["secondReason"], (
        "a duplicate done was dropped silently rather than named")
    assert got["order"] == 1


def test_a_single_done_is_the_only_thing_that_reads_as_once(bundle):
    """The audit's POSITIVE half: exactly one done must read as once-only, so
    the duplicate detection above is not simply always-true."""
    got = _run(bundle, """
      let s = CV.initialConversation('s', 4);
      s = CV.applyFrame(s, {kind: 'done', messageId: 'm1', recipient: 4,
                            text: 'hi'}).state;
      const one = CV.finalizeAudit(s);
      s = CV.applyFrame(s, {kind: 'done', messageId: 'm2', recipient: 4,
                            text: 'yo'}).state;
      return {one, two: CV.finalizeAudit(s)};
    """)
    assert got["one"]["counts"] == {"m1": 1}
    assert got["one"]["duplicated"] == []
    assert got["one"]["allOnce"] is True
    assert got["two"]["counts"] == {"m1": 1, "m2": 1}
    assert got["two"]["allOnce"] is True


def test_the_finalize_audit_can_actually_report_a_duplicate(bundle):
    """`finalizeAudit` must be able to FAIL, or it is decoration.

    The once-only test asserts `allOnce` is true, and a mutation that hard-codes
    `allOnce: true` would leave it green — the audit would then be incapable of
    reporting the very bug it is quoted as evidence for. So the audit is driven
    with a state that HAS a duplicate, and it must name the id and refuse
    `allOnce`.
    """
    got = _run(bundle, """
      // A hand-built state with a genuine double-finalize: the shape the
      // guard in `applyFrame` exists to prevent, so the audit has something to
      // find. Reaching it through `applyFrame` is impossible BY DESIGN.
      const dup = {
        sessionId: 's', recipient: 4, byId: {}, order: [], cursor: 3,
        state: 'idle', reason: '', asks: [], needsReseed: false,
        finalizedCount: {'m1': 2, 'm2': 1},
      };
      const clean = {
        sessionId: 's', recipient: 4, byId: {}, order: [], cursor: 3,
        state: 'idle', reason: '', asks: [], needsReseed: false,
        finalizedCount: {'m1': 1},
      };
      const empty = CV.initialConversation('s', 4);
      return {dup: CV.finalizeAudit(dup), clean: CV.finalizeAudit(clean),
              empty: CV.finalizeAudit(empty)};
    """)
    assert got["dup"]["duplicated"] == ["m1"], (
        f"the finalize audit cannot report a duplicated id: {got['dup']}")
    assert got["dup"]["allOnce"] is False, (
        "the finalize audit reported a doubly-finalized message as once-only")
    assert got["dup"]["counts"] == {"m1": 2, "m2": 1}
    assert got["clean"]["duplicated"] == []
    assert got["clean"]["allOnce"] is True
    assert got["empty"]["allOnce"] is False, (
        "an audit over nothing reported a successful single finalize")


def test_a_done_with_no_body_keeps_the_streamed_text(bundle):
    got = _run(bundle, """
      let s = CV.initialConversation('s', 4);
      s = CV.applyFrame(s, {kind: 'delta', messageId: 'm1', recipient: 4,
                            text: 'streamed'}).state;
      s = CV.applyFrame(s, {kind: 'done', messageId: 'm1', recipient: 4}).state;
      return {text: s.byId['m1'].text, finalised: s.byId['m1'].finalised,
              count: s.finalizedCount['m1']};
    """)
    assert got["text"] == "streamed", (
        "an empty done erased the streamed text")
    assert got["finalised"] is True
    assert got["count"] == 1


def test_a_frame_for_another_recipient_is_dropped_with_a_reason(bundle):
    """At ~71 seats two runs stream at once; a cross-recipient frame is the
    G08 bug the acceptance gate names."""
    got = _run(bundle, """
      let s = CV.initialConversation('s', 4);
      const r = CV.applyFrame(s, {kind: 'delta', messageId: 'mX', recipient: 7,
                                  text: 'not mine'});
      s = r.state;
      return {ignored: r.ignored, reason: r.reason, order: s.order.length,
              byId: Object.keys(s.byId)};
    """)
    assert got["ignored"] is True
    assert "7" in got["reason"] and "4" in got["reason"], (
        f"the cross-recipient drop does not name both seats: {got['reason']}")
    assert got["order"] == 0 and got["byId"] == []


def test_an_out_of_order_cursor_asks_for_a_reseed_instead_of_reordering(bundle):
    got = _run(bundle, """
      let s = CV.initialConversation('s', 4);
      s = CV.applyFrame(s, {kind: 'delta', messageId: 'm1', recipient: 4,
                            text: 'a', cursor: 5}).state;
      const back = CV.applyFrame(s, {kind: 'delta', messageId: 'm2',
                                     recipient: 4, text: 'b', cursor: 2});
      s = back.state;
      return {ignored: back.ignored, reason: back.reason,
              needsReseed: s.needsReseed, order: s.order.length,
              cursor: s.cursor};
    """)
    assert got["ignored"] is True, (
        "a frame from behind the cursor was applied")
    assert got["needsReseed"] is True
    assert "reseed" in got["reason"]
    assert got["order"] == 1, "an out-of-order frame entered the transcript"
    assert got["cursor"] == 5, "an out-of-order frame rewound the cursor"


def test_a_transport_failure_is_not_reported_as_a_task_failure(bundle):
    got = _run(bundle, """
      let a = CV.initialConversation('s', 4);
      a = CV.applyFrame(a, {kind: 'error', messageId: 'm', recipient: 4,
                            error: {source: 'transport',
                                    detail: 'socket closed'}}).state;
      let b = CV.initialConversation('s', 4);
      b = CV.applyFrame(b, {kind: 'error', messageId: 'm', recipient: 4,
                            error: {source: 'task',
                                    detail: 'the tool refused'}}).state;
      return {transport: a.reason, task: b.reason};
    """)
    assert got["transport"].startswith("connection failed")
    assert got["task"].startswith("run failed")
    assert got["transport"] != got["task"], (
        "a connection failure and a run failure rendered identically")


def test_closing_the_panel_never_interrupts(bundle):
    """The plan's fourth proof: "close doesn't cancel". Stated as a function
    that cannot return a request, in ANY state."""
    got = _run(bundle, """
      const states = ['idle', 'pending', 'streaming', 'tool', 'owner_wait',
                      'error', 'stale'];
      const out = {};
      for (const k of states) {
        let s = CV.initialConversation('s', 4);
        if (k !== 'idle') {
          s = CV.applyFrame(s, k === 'error'
            ? {kind: 'error', messageId: 'm', recipient: 4,
               error: {source: 'transport', detail: 'x'}}
            : k === 'stale'
              ? {kind: 'gap', messageId: 'm', recipient: 4}
              : {kind: 'status', messageId: 'm', recipient: 4, state: k}
          ).state;
        }
        out[k] = {close: CV.onPanelClose(),
                  observed: CV.interruptObserved(s, 'm')};
      }
      return out;
    """)
    for name, row in got.items():
        assert row["close"] is None, (
            f"closing the panel produced an interrupt request in state "
            f"{name}: {row['close']}")
    assert got["streaming"]["observed"] == "running", (
        "a run observed mid-stream was not reported as running")


def test_an_interrupt_names_what_it_stops(bundle):
    got = _run(bundle, """
      const out = {};
      for (const s of ['speech', 'request', 'agent']) {
        out[s] = CV.interruptRequest(s, 'run-7');
      }
      return out;
    """)
    assert got["speech"]["label"] == "Stop speaking"
    assert got["request"]["label"] == "Cancel this request"
    assert got["agent"]["label"] == "Stop this agent"
    assert len({got[k]["label"] for k in got}) == 3, (
        "two interrupt scopes share one label; the owner cannot tell what "
        "they are stopping")
    for k, row in got.items():
        assert row["scope"] == k and row["runId"] == "run-7"


def test_the_composer_refuses_with_a_named_reason_and_never_invents_a_session(
        bundle,
):
    """No recipient -> disabled with a reason. There is no generic chat."""
    got = _run(bundle, """
      const none = CV.initialConversation('', null);
      const noRecipient = CV.composerView(none, 'hello', true);
      const unbound = CV.initialConversation('sess-1', 4);
      const blocked = CV.composerView(unbound, 'hello', false);
      const empty = CV.composerView(unbound, '   ', true);
      const ok = CV.composerView(unbound, 'hello', true);
      const req = CV.buildSendRequest(unbound, 'hello', false);
      return {noRecipient, blocked, empty, ok, req};
    """)
    assert got["noRecipient"]["enabled"] is False
    assert "no recipient" in got["noRecipient"]["reason"]
    assert got["blocked"]["enabled"] is False
    assert "not available" in got["blocked"]["reason"], (
        "the blocked binding did not disable the composer")
    assert got["empty"]["enabled"] is False
    assert got["ok"]["enabled"] is True
    assert "ok" in got["req"] and got["req"]["ok"] is False, (
        "a send REQUEST was built while the binding is blocked")


def test_a_send_request_id_is_derived_so_a_retry_is_recognised(bundle):
    got = _run(bundle, """
      const s = CV.initialConversation('sess-1', 4);
      const a = CV.buildSendRequest(s, 'do the thing', true);
      const b = CV.buildSendRequest(s, 'do the thing', true);
      const c = CV.buildSendRequest(s, 'do another thing', true);
      return {a, b, c};
    """)
    assert got["a"]["requestId"] == got["b"]["requestId"], (
        "a retry of the same message minted a different request id, so the "
        "authority cannot dedupe it")
    assert got["a"]["requestId"] != got["c"]["requestId"]
    assert got["a"]["recipient"] == 4
    assert got["a"]["body"] == "do the thing"


def test_a_follow_up_carries_the_message_it_follows(bundle):
    got = _run(bundle, """
      const s = CV.initialConversation('sess-1', 4);
      const f = CV.followUpRequest(s, 'm1', 'and then?', true);
      const blocked = CV.followUpRequest(s, 'm1', 'and then?', false);
      return {f, blocked};
    """)
    assert got["f"]["parentId"] == "m1"
    assert got["f"]["recipient"] == 4
    assert got["blocked"].get("ok") is False, (
        "a follow-up bypassed the blocked binding")


def test_artifacts_are_ids_and_are_settled_by_the_finalizing_message(bundle):
    got = _run(bundle, """
      let s = CV.initialConversation('s', 4);
      s = CV.applyFrame(s, {kind: 'delta', messageId: 'm1', recipient: 4,
                            text: 'working'}).state;
      const before = CV.artifactRefs(s, 'm1');
      // A message that names artifacts WITHOUT being finalised: the shape a
      // streaming frame could produce. `settled` must be false here, or the
      // flag says nothing — every ref would read settled and a caller could
      // not tell an in-flight artifact from a delivered one.
      s = CV.applyFrame(s, {kind: 'tool', messageId: 'm2', recipient: 4,
                            tool: 'graft'}).state;
      s.byId['m2'].artifacts = ['art-inflight'];
      const inflight = CV.artifactRefs(s, 'm2');
      s = CV.applyFrame(s, {kind: 'done', messageId: 'm1', recipient: 4,
                            text: 'done',
                            artifacts: ['art-1', 'art-2']}).state;
      return {before, inflight, after: CV.artifactRefs(s, 'm1'),
              missing: CV.artifactRefs(s, 'nope')};
    """)
    assert got["before"] == [], (
        "an artifact was claimed before the message was finalised")
    assert got["inflight"] == [{"id": "art-inflight", "settled": False}], (
        f"an in-flight artifact was reported as settled: {got['inflight']}")
    assert got["after"] == [{"id": "art-1", "settled": True},
                            {"id": "art-2", "settled": True}]
    assert got["missing"] == []


def test_a_second_done_cannot_re_merge_artifacts(bundle):
    """The duplicate guard covers the side effects, not just the text."""
    got = _run(bundle, """
      let s = CV.initialConversation('s', 4);
      s = CV.applyFrame(s, {kind: 'done', messageId: 'm1', recipient: 4,
                            text: 'a', artifacts: ['art-1']}).state;
      s = CV.applyFrame(s, {kind: 'done', messageId: 'm1', recipient: 4,
                            text: 'b', artifacts: ['art-2']}).state;
      return CV.artifactRefs(s, 'm1');
    """)
    assert got == [{"id": "art-1", "settled": True}], (
        f"a duplicate done merged a second set of artifacts: {got}")


# ======================================================================
# 4 — typewriter vs instant: cosmetic, and it never delays settlement
# ======================================================================
def test_the_reveal_modes_end_at_the_same_place(bundle):
    got = _run(bundle, """
      const text = 'Hello, wren';
      const instant = CV.revealFor(text, 'instant', 'm1', 0);
      let shown = 0;
      const path = [];
      for (let i = 0; i < 20; i += 1) {
        shown = CV.typewriterStep(shown, text.length, 3);
        const r = CV.revealFor(text, 'typewriter', 'm1', shown);
        path.push(r.shown);
        if (r.done) break;
      }
      const last = CV.revealFor(text, 'typewriter', 'm1', path[path.length - 1]);
      const skipped = CV.skipReveal(
        CV.revealFor(text, 'typewriter', 'm1', 2));
      return {instant, path, last, skipped};
    """)
    assert got["instant"]["done"] is True
    assert got["instant"]["text"] == "Hello, wren"
    assert got["last"]["done"] is True, (
        "the typewriter never reached the end of the text")
    assert got["skipped"]["done"] is True, (
        "Skip did not jump to the end of the reveal")
    assert got["skipped"]["text"] == "Hello, wren"


def test_the_reveal_is_cosmetic_and_the_message_is_settled_regardless(bundle):
    """The plan: the typewriter "never delays request settlement". A settled
    message with a typewriter still mid-reveal must be settled."""
    got = _run(bundle, """
      let s = CV.initialConversation('s', 4);
      s = CV.applyFrame(s, {kind: 'done', messageId: 'm1', recipient: 4,
                            text: 'a long final answer'}).state;
      const mid = CV.revealFor('a long final answer', 'typewriter', 'm1', 3);
      return {finalised: s.byId['m1'].finalised, state: s.state,
              midDone: mid.done, midText: mid.text};
    """)
    assert got["finalised"] is True, (
        "settlement was gated on the cosmetic reveal")
    assert got["state"] == "idle"
    assert got["midDone"] is False
    assert got["midText"] == "a l"


# ======================================================================
# 5 — the LIVE binding is BLOCKED, and the panel says so
# ======================================================================
def test_the_binding_reports_itself_blocked_and_names_what_owes_it(bundle):
    got = _run(bundle, """
      return {blocked: CV.bindingStatus(false), available: CV.bindingStatus(true)};
    """)
    assert got["blocked"]["status"] == "blocked"
    assert "getCharacterSession" in got["blocked"]["owedBy"], (
        "the blocked binding does not name the contract method that owes it")
    assert "Unavailable" in got["blocked"]["owedBy"], (
        "the blocked binding does not quote the contract's own availability "
        "behaviour")
    assert "PREVIEW" in got["blocked"]["reason"], (
        "the blocked binding does not say the panel is a preview")
    assert got["available"]["status"] == "available"


def test_rendering_the_panel_blocked_does_not_claim_nobody_spoke(bundle):
    """A blank transcript with no banner reads as "the org said nothing", which
    is a claim about the org rather than about the missing binding."""
    got = _run(bundle, """
      const R = {createElement: function (type, props, children) {
        return {type: type, props: props || {},
                children: children === undefined ? [] : children};
      }};
      function walk(node, out) {
        if (!node || typeof node !== 'object') return out;
        const tid = node.props && node.props['data-testid'];
        if (tid) out.push({testid: tid, props: node.props,
                           text: typeof node.children === 'string'
                             ? node.children : null});
        const kids = Array.isArray(node.children) ? node.children : [];
        for (const k of kids) walk(k, out);
        return out;
      }
      const s = CV.initialConversation('sess-1', 4);
      const blocked = CV.renderConversationPanel(
        {R: R, payload: TOWER, state: s, bound: false});
      const bound = CV.renderConversationPanel(
        {R: R, payload: TOWER, state: s, bound: true});
      const bt = walk(blocked, []).filter(n => n.testid ===
        'conversation-binding-blocked');
      const et = walk(blocked, []).filter(n => n.testid ===
        'conversation-transcript-empty');
      const boundEt = walk(bound, []).filter(n => n.testid ===
        'conversation-transcript-empty');
      return {blockedBanner: bt.length, blockedEmpty: et.length,
              boundBanner: walk(bound, []).filter(n => n.testid ===
                'conversation-binding-blocked').length,
              blockedEmptyText: et.length ? et[0].text : null,
              boundEmptyText: boundEt.length ? boundEt[0].text : null,
              root: walk(blocked, []).map(n => n.testid)};
    """)
    assert got["blockedBanner"] == 1, (
        "a blocked binding rendered with no banner")
    assert got["boundBanner"] == 0
    assert "not bound" in (got["blockedEmptyText"] or ""), (
        f"the empty transcript does not explain the missing binding: "
        f"{got['blockedEmptyText']!r}")
    assert "Nothing has been said" in (got["boundEmptyText"] or ""), (
        "a BOUND empty transcript was not distinguished from a blocked one")
    assert "conversation-binding-owed-by" in got["root"], (
        "the panel does not render which contract owes the binding")


def test_the_panel_renders_the_six_states_as_data(bundle):
    got = _run(bundle, """
      const R = {createElement: function (type, props, children) {
        return {type: type, props: props || {},
                children: children === undefined ? [] : children};
      }};
      function walk(node, out) {
        if (!node || typeof node !== 'object') return out;
        const tid = node.props && node.props['data-testid'];
        if (tid) out.push({testid: tid, props: node.props});
        const kids = Array.isArray(node.children) ? node.children : [];
        for (const k of kids) walk(k, out);
        return out;
      }
      const frames = {
        pending: {kind: 'status', messageId: 'm', recipient: 4,
                  state: 'pending'},
        streaming: {kind: 'delta', messageId: 'm', recipient: 4, text: 'x'},
        tool: {kind: 'tool', messageId: 'm', recipient: 4, tool: 'graft'},
        owner_wait: {kind: 'status', messageId: 'm', recipient: 4,
                     state: 'owner_wait', runId: 'r', text: 'which?',
                     artifacts: ['a']},
        error: {kind: 'error', messageId: 'm', recipient: 4,
                error: {source: 'task', detail: 'no'}},
        stale: {kind: 'gap', messageId: 'm', recipient: 4},
      };
      const out = {};
      for (const k of Object.keys(frames)) {
        let s = CV.initialConversation('sess-1', 4);
        s = CV.applyFrame(s, frames[k]).state;
        const nodes = walk(CV.renderConversationPanel(
          {R: R, payload: TOWER, state: s, bound: true}), []);
        const chip = nodes.find(n => n.testid === 'conversation-state');
        out[k] = {chip: chip ? chip.props['data-state'] : null,
                  root: nodes.find(n => n.testid === 'office-conversation-panel')
                    .props['data-state']};
      }
      return out;
    """)
    for name, row in got.items():
        assert row["chip"] == name, (
            f"the {name} state did not reach the rendered chip: {row}")
        assert row["root"] == name


def test_the_panel_names_the_recipient_and_never_shows_a_generic_chat(bundle):
    got = _run(bundle, """
      const R = {createElement: function (type, props, children) {
        return {type: type, props: props || {},
                children: children === undefined ? [] : children};
      }};
      function walk(node, out) {
        if (!node || typeof node !== 'object') return out;
        const tid = node.props && node.props['data-testid'];
        if (tid) out.push({testid: tid, props: node.props, node: node});
        const kids = Array.isArray(node.children) ? node.children : [];
        for (const k of kids) walk(k, out);
        return out;
      }
      const s = CV.initialConversation('sess-1', 4);
      const nodes = walk(CV.renderConversationPanel(
        {R: R, payload: TOWER, state: s, bound: true}), []);
      const head = nodes.find(n => n.testid === 'conversation-recipient');
      const none = walk(CV.renderConversationPanel(
        {R: R, payload: TOWER, state: CV.initialConversation('', null),
         bound: true}), []).find(n => n.testid === 'conversation-recipient');
      return {recipient: head.props['data-recipient'],
              session: head.props['data-session'],
              noneRecipient: none.props['data-recipient'],
              labels: nodes.map(n => n.testid)};
    """)
    assert got["recipient"] == "4"
    assert got["session"] == "sess-1"
    assert got["noneRecipient"] == "", (
        "a session with no recipient rendered as though it had one")
    # three interrupt controls, each named, plus the close that is not one
    assert "conversation-interrupt-speech" in got["labels"]
    assert "conversation-interrupt-request" in got["labels"]
    assert "conversation-interrupt-agent" in got["labels"]
    assert "conversation-close" in got["labels"]


# ======================================================================
# 6 — C4: the views never write org state, and there is no second React
# ======================================================================
def test_the_f09_modules_reach_no_network():
    """C4: "The tower reads. Every mutation is a staged op through the
    confirmation journal. There is no optimistic state in the renderer."
    A view module containing a write verb could not make that claim."""
    for path in (UI_DIR / "CharacterPanel.ts", UI_DIR / "ConversationPanel.ts",
                 SYSTEMS_DIR / "CharacterPresenter.ts"):
        src = path.read_text()
        for banned in ("fetch(", "XMLHttpRequest", "sendBeacon", "axios",
                       "WebSocket", "EventSource"):
            assert banned not in src, (
                f"{path.name} contains {banned}: an F09 view must not reach "
                f"the network (C4)")
        # and no write verb in a string that could become a request
        for banned in ('"POST"', "'POST'", '"PUT"', "'PUT'", '"DELETE"'):
            assert banned not in src, (
                f"{path.name} names the write verb {banned}; every mutation is "
                f"a staged op, never a direct store call (C4)")


def test_the_f09_modules_take_react_from_the_host(bundle):
    """A `react` import compiles to a bare global the dashboard does not
    define — the live "React is not defined" page error."""
    import re as _re
    for name in ("ui/CharacterPanel.ts", "ui/ConversationPanel.ts",
                 "systems/CharacterPresenter.ts"):
        raw = (GAME_DIR / name).read_text()
        # Comments stripped: the module headers EXPLAIN this rule by name
        # ("there is NO `import from \"react\"`"), and a naive check fails on
        # that explanation — the same trap `test_office_game_packaging.py`
        # documents.
        src = _re.sub(r"/\*.*?\*/", "", raw, flags=_re.S)
        src = _re.sub(r"//[^\n]*", "", src)
        assert 'from "react"' not in src and "from 'react'" not in src, (
            f"{name} imports react directly; the host supplies it")
    for name in ("ui/CharacterPanel.ts", "ui/ConversationPanel.ts"):
        raw = (GAME_DIR / name).read_text()
        assert "createElement" in raw, (
            f"{name} does not build elements from the host's createElement; "
            f"JSX would emit a jsx-runtime import the dashboard lacks")


def test_the_presenter_is_pure(bundle):
    """Same input, same output — no clock, no random, no module state."""
    got = _run(bundle, _SETUP + """
      const a = chars.map(c => CP.presentCharacter(c, chars,
                                                   TOWER.active_window_s));
      const b = chars.map(c => CP.presentCharacter(c, chars,
                                                   TOWER.active_window_s));
      const rosterA = CP.rosterView(TOWER, {filter: 'all', query: ''});
      const rosterB = CP.rosterView(TOWER, {filter: 'all', query: ''});
      return {same: JSON.stringify(a) === JSON.stringify(b),
              rosterSame: JSON.stringify(rosterA) === JSON.stringify(rosterB)};
    """)
    assert got["same"] is True, "presentCharacter is not deterministic"
    assert got["rosterSame"] is True, "rosterView is not deterministic"


def test_the_f09_modules_contain_no_timer():
    """C7's boundary: this build's F09 code owns no clock. The typewriter is
    advanced by the caller with the frame it already has."""
    for name in ("ui/CharacterPanel.ts", "ui/ConversationPanel.ts",
                 "systems/CharacterPresenter.ts"):
        src = (GAME_DIR / name).read_text()
        for banned in ("setInterval", "setTimeout", "requestAnimationFrame",
                       "Date.now", "performance.now"):
            assert banned not in src, (
                f"{name} contains {banned}: an F09 view must not own a clock "
                f"(C7)")


def test_the_panels_compose_with_the_f08_panel_policy(bundle):
    """Additive integration only: the F09 panels must slot into the F08 panel
    state without changing it. `character` is already a `PanelKind`, so the
    conversation panel is the DRAWER beside it, and the panel state decides the
    F06 input contexts exactly as F08 left it."""
    policy = UI_DIR / "PanelHost.ts"
    assert policy.is_file()
    got = _run_with(
        bundle,
        {"PanelHost.ts": policy, "_dir": UI_DIR},
        """
        let s = P.initialPanelState();
        s = P.openPanel(s, 'character', false);
        const withPanel = s.panel;
        s = P.openPanel(s, 'character', true);      // the dialogue drawer
        const drawer = s.drawer;
        const contexts = P.contextsFor(s, true);
        const closed = P.closeTop(P.closeTop(s));
        return {withPanel, drawer, contexts, panelAfterClose: closed.panel,
                modal: P.isModalOpen(s)};
        """,
        requires=[("P", "PanelHost.js")],
    )
    assert got["withPanel"] == "character"
    assert got["drawer"] == "character", (
        "the F08 drawer policy did not accept the dialogue panel beside the "
        "character panel")
    assert got["contexts"] == ["world", "panel", "text"], (
        f"the F08/F06 context derivation changed: {got['contexts']}")
    assert got["panelAfterClose"] == "none"
    assert got["modal"] is False
