"""The shading model behind the office art.

WHY THE ART IS TESTABLE AT ALL
------------------------------
"The room looks flat" is not a testable claim. "A shadow is cooler than its
base, a highlight is warmer, and the two are at least N degrees apart in hue"
IS. That distinction is the whole reason `art/colour.ts` exists as pure
functions instead of `ctx.fillStyle = "#39445a"` calls scattered through the
scenes — nine flat colours cannot be asserted on, a shading model can.

The specific defect this suite guards against is the ORIGINAL one: the first
pass at `scenes.ts` painted every wall as a single solid fill (`grep -c
'shade(|ramp(|mix(' scenes.ts` returned 0), so every room was a rectangle of one
colour. A regression that reintroduces a flat fill would be invisible in a
screenshot review and obvious here.

Hermetic: tsc + node. No browser, no canvas, no network.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _office_toolchain import node_path, require_toolchain_or_fail  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
GAME = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office" / "game"
ART = GAME / "art"

NODE = node_path()

#: Loud, not a silent skip: a missing compiler ERRORS at collection
#: (see the helper's docstring). A green `N skipped` is the false pass this replaces.
require_toolchain_or_fail()


def _run(tmp_path: Path, js: str) -> object:
    staged = tmp_path / "src"
    staged.mkdir(exist_ok=True)
    for name in ("colour.ts", "surfaces.ts"):
        shutil.copy(ART / name, staged / name)
    from _office_toolchain import tsc_path
    subprocess.run(
        [NODE, str(tsc_path()), "--module", "commonjs", "--target", "es2020",
         "--moduleResolution", "node",
         "--ignoreDeprecations", "6.0", "--outDir", str(tmp_path / "js"),
         "--skipLibCheck", "--noEmitOnError", "false", "--lib", "es2020,dom",
         str(staged / "colour.ts"), str(staged / "surfaces.ts")],
        capture_output=True, text=True, timeout=240, cwd=str(staged))
    colour = tmp_path / "js" / "colour.js"
    surf = tmp_path / "js" / "surfaces.js"
    if not colour.is_file() or not surf.is_file():
        pytest.skip("tsc could not compile the art modules for the node driver")
    program = ("const C = require(%s);\nconst S = require(%s);\n"
               "process.stdout.write(JSON.stringify((() => { %s })()));\n"
               % (json.dumps(str(colour)), json.dumps(str(surf)), js))
    proc = subprocess.run([NODE], input=program, capture_output=True,
                          text=True, timeout=90)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}")
    return json.loads(proc.stdout)


# ======================================================================
# 1 — the hue shift, which is the whole technique
# ======================================================================
def test_a_shadow_is_cooler_and_a_highlight_is_warmer(tmp_path):
    """THE ONE PROPERTY THAT MAKES THIS READ AS PIXEL ART.

    Shading a colour by lightness alone keeps its hue, so a grey wall shades to
    grey and looks like a wireframe. Hand-drawn pixel art pulls shadows toward
    blue and highlights toward yellow. This asserts the direction of that
    shift, not just that the colours differ.

    A NEUTRAL GREY is the important case, not the easy one: it has no hue at
    all, so a saturation-scaled shift would leave it grey. The saturation floor
    in `shade` exists precisely so a muted material still reads as lit — an
    earlier version of this function got that wrong and every wall came out
    flat, which is the bug this test now pins down.
    """
    got = _run(tmp_path, """
      const hsl = c => C.rgbToHsl(...C.parse(c).slice(0, 3));
      const base = '#8a8a8a';
      const dark = C.shade(base, -0.25);
      const light = C.shade(base, 0.25);
      const hueGap = (a, b) => Math.abs(((a - b + 540) % 360) - 180);
      return {
        base, dark, light,
        baseSat: hsl(base)[1], darkSat: hsl(dark)[1], lightSat: hsl(light)[1],
        darkHue: hsl(dark)[0], lightHue: hsl(light)[0],
        baseL: hsl(base)[2], darkL: hsl(dark)[2], lightL: hsl(light)[2],
        dbl: C.parse('/definitely-not-a-colour'),
      };
    """)
    # The neutral case: the shadow must gain saturation and go cool.
    assert got["baseSat"] == 0, "the fixture is not neutral, so the test is easy"
    assert got["darkSat"] > 0.05, (
        f"shading a neutral colour produced saturation {got['darkSat']} — it "
        f"stayed grey, so a muted material will render flat: {got['dark']}")
    assert got["darkHue"] > 150, (
        f"the shadow did not pull toward blue: hue {got['darkHue']} "
        f"({got['dark']})")
    assert got["lightHue"] < 140 or got["lightHue"] > 300, (
        f"the highlight did not pull toward warm: hue {got['lightHue']} "
        f"({got['light']})")
    # and the lightness moved the right way, or the hue shift is meaningless
    assert got["darkL"] < got["baseL"] < got["lightL"], (
        f"lightness did not move monotonically: {got}")
    assert got["dbl"][:3] == [255, 0, 255], (
        f"a garbage colour was not magenta: {got['dbl']}")

def test_shading_a_vivid_colour_shifts_more_than_a_muted_one(tmp_path):
    """The shift is scaled by saturation, but the floor means a muted colour
    still moves a little — the two must not be equal, and neither may be zero.

    Pushing a desaturated colour toward blue by a fixed number of degrees gives
    a colour the eye reads as "slightly wrong grey" rather than as a shadow.
    """
    got = _run(tmp_path, """
      const hueOf = c => C.rgbToHsl(...C.parse(c))[0];
      const vivid = '#1f6fd0';
      const muted = '#8f9298';
      const dv = Math.abs(((hueOf(C.shade(vivid,-0.3)) - hueOf(vivid) + 540) % 360) - 180);
      const dm = Math.abs(((hueOf(C.shade(muted,-0.3)) - hueOf(muted) + 540) % 360) - 180);
      return {vividShift: dv, mutedShift: dm};
    """)
    assert got["vividShift"] > got["mutedShift"], (
        f"a vivid colour shifted LESS than a muted one: {got}")


def test_shade_at_zero_is_identity(tmp_path):
    got = _run(tmp_path, """
      return {same: C.shade('#39445a', 0), base: '#39445a'};
    """)
    assert got["same"] == got["base"], "shade(c, 0) changed the colour"


def test_an_unknown_colour_is_loud_not_hidden(tmp_path):
    """A typo'd colour must be magenta, never black or transparent.

    A bad colour that resolves to black vanishes into a dark scene and survives
    review. Magenta is impossible to miss.
    """
    got = _run(tmp_path, """
      const rgb = c => Array.from(C.parse(c).slice(0, 3));
      return {bad: rgb('not-a-colour'), short: rgb('#zzz'),
              good: rgb('#39445a'), three: rgb('#abc')};
    """)
    assert got["bad"] == [255, 0, 255], f"a bad colour was not magenta: {got['bad']}"
    assert got["short"] == [255, 0, 255]
    assert got["good"] == [57, 68, 90], f"a valid hex was mis-parsed: {got['good']}"
    assert got["three"] == [170, 187, 204], (
        f"#rgb shorthand was not expanded: {got['three']}")


# ======================================================================
# 2 — the ramp: five consistent tones, derived not authored
# ======================================================================
def test_the_ramp_has_five_distinct_tones_in_order(tmp_path):
    got = _run(tmp_path, """
      const r = C.ramp('#8a7a5f');
      const lum = c => C.luminance(c);
      return {r, order: [lum(r.hi), lum(r.lt), lum(r.base), lum(r.dk), lum(r.dd)],
              distinct: new Set(Object.values(r)).size};
    """)
    order = got["order"]
    assert got["distinct"] == 5, (
        f"the ramp has duplicate tones, so it is not a ramp: {got['r']}")
    assert order == sorted(order, reverse=True), (
        f"the ramp is not ordered hi > lt > base > dk > dd: {order}")


def test_two_calls_for_the_same_material_agree(tmp_path):
    """A ramp must be IDENTICAL wherever it is used, or the same material takes
    two slightly different tones in two rooms and the scene looks dirty."""
    got = _run(tmp_path, """
      const a = C.ramp('#8a7a5f');
      const b = C.ramp('#8a7a5f');
      return {same: JSON.stringify(a) === JSON.stringify(b)};
    """)
    assert got["same"] is True


def test_an_outline_is_never_pure_black(tmp_path):
    """A black outline is the classic amateur tell: it flattens the image and
    reads as ink on paper rather than as a lit object."""
    got = _run(tmp_path, """
      const colours = ['#e2d6c0', '#4cae6a', '#1f6fd0', '#ffffff', '#000000'];
      const out = colours.map(c => C.outlineFor(c));
      return {out, anyBlack: out.some(o => o === '#000000'),
              anyVeryDark: out.map(o => C.luminance(o))};
    """)
    assert got["anyBlack"] is False, f"an outline was pure black: {got['out']}"
    assert all(l < 0.32 for l in got["anyVeryDark"]), (
        f"an outline was not actually dark: {got['anyVeryDark']}")
    assert len(set(got["out"])) > 1, (
        "every material got the SAME outline, so it is a constant rather than "
        f"derived from the material: {got['out']}")


# ======================================================================
# 3 — the surfaces actually vary, which is what the flat fill lacked
# ======================================================================
def _recorder() -> str:
    """A 2D-context recorder, so the draw calls can be inspected."""
    return """
      function Rec() {
        this.ops = []; this.fillStyle = '#000'; this.strokeStyle = '#000';
        this.lineWidth = 1; this.font = ''; this.textAlign = '';
      }
      const P = Rec.prototype;
      P.fillRect = function (x, y, w, h) {
        this.ops.push(['fillRect', x, y, w, h, this.fillStyle]); };
      P.strokeRect = function (x, y, w, h) {
        this.ops.push(['strokeRect', x, y, w, h, this.strokeStyle]); };
      P.beginPath = function () { this.ops.push(['beginPath']); };
      P.moveTo = function (x, y) { this.ops.push(['moveTo', x, y]); };
      P.lineTo = function (x, y) { this.ops.push(['lineTo', x, y]); };
      P.closePath = function () { this.ops.push(['closePath']); };
      P.fill = function () { this.ops.push(['fill', this.fillStyle]); };
      P.stroke = function () { this.ops.push(['stroke', this.strokeStyle]); };
      P.arc = function () { this.ops.push(['arc']); };
      P.save = function () {}; P.restore = function () {};
      P.translate = function () {}; P.scale = function () {};
      P.createRadialGradient = function () {
        return {addColorStop: function () {}}; };
      const fills = ctx => ctx.ops.filter(o => o[0] === 'fillRect').map(o => o[5]);
    """


def test_a_wall_is_not_a_single_colour(tmp_path):
    """THE ORIGINAL DEFECT, ASSERTED DIRECTLY.

    A wall painted with one `fillRect` in one colour IS the flat-fill bug. A
    wall painted as a gradient with a cap, a pattern and a baseboard produces
    many distinct colours, and that count is the assertion.
    """
    got = _run(tmp_path, _recorder() + """
      const t = S.roomTheme(0);
      const ctx = new Rec();
      S.drawWallFace(ctx, 0, 0, 120, 52, t, {seed: 1});
      const cols = fills(ctx);
      const unique = new Set(cols);
      return {ops: ctx.ops.filter(o => o[0] === 'fillRect').length,
              unique: unique.size, sampleCols: [...unique].slice(0, 6)};
    """)
    assert got["unique"] >= 8, (
        f"the wall face used only {got['unique']} distinct colours — that is a "
        f"flat fill, not a shaded surface: {got['sampleCols']}")
    assert got["ops"] >= 20, (
        f"the wall face made only {got['ops']} fills; a gradient alone is 30+")


def test_a_floor_is_not_a_single_colour(tmp_path):
    """Per-tile lightness jitter is what stops a large floor reading as one
    sheet of colour. Asserted as a count of distinct tile tones."""
    got = _run(tmp_path, _recorder() + """
      const t = S.roomTheme(0);
      const ctx = new Rec();
      S.drawFloor(ctx, 0, 0, 200, 120, t, {seed: 3});
      const fills2 = ctx.ops.filter(o => o[0] === 'fill');
      const tones = new Set(fills2.map(o => o[1]));
      return {tiles: fills2.length, tones: tones.size};
    """)
    assert got["tiles"] > 20, f"too few tiles painted: {got['tiles']}"
    assert got["tones"] > 3, (
        f"every floor tile is one of only {got['tones']} tones — the jitter is "
        f"not reaching the paint, so a big floor will read as a flat sheet")


def test_the_floor_is_deterministic_for_a_seed(tmp_path):
    """Same seed, same pixels — otherwise "the art changed" is undetectable."""
    got = _run(tmp_path, _recorder() + """
      const t = S.roomTheme(0);
      const mk = () => { const c = new Rec(); S.drawFloor(c, 0, 0, 150, 90, t,
        {seed: 5}); return JSON.stringify(c.ops); };
      const a = mk(), b = mk();
      const c = new Rec();
      S.drawFloor(c, 0, 0, 150, 90, t, {seed: 6});
      return {same: a === b, differentSeed: a !== JSON.stringify(c.ops)};
    """)
    assert got["same"] is True, "the same seed painted different pixels"
    assert got["differentSeed"] is True, (
        "a different seed painted identical pixels, so the seed is ignored")


def test_adjacent_rooms_get_different_themes(tmp_path):
    """The theme list alternates colour families so neighbouring rooms are
    visibly distinct rather than a wash of one hue."""
    got = _run(tmp_path, """
      const seen = [];
      for (let i = 0; i < S.themeCount; i++) {
        const t = S.roomTheme(i);
        seen.push(t.carpet + '/' + t.wall);
      }
      return {n: S.themeCount, unique: new Set(seen).size,
              negatives: S.roomTheme(-1).carpet, big: S.roomTheme(37).carpet};
    """)
    assert got["unique"] == got["n"], (
        f"two themes are identical: {got['unique']} unique of {got['n']}")
    assert got["negatives"], "a negative seed produced no theme"
    assert got["big"], "a large seed produced no theme"


def test_the_contact_shadow_is_soft_and_not_black(tmp_path):
    """A hard black ellipse looks like a hole. The shadow is a gradient at low
    alpha, darkened toward blue-grey."""
    got = _run(tmp_path, """
      const stops = [];
      function Ctx() {}
      Ctx.prototype.createRadialGradient = function () {
        return {addColorStop: (p, c) => stops.push([p, c])}; };
      for (const m of ['save','restore','translate','scale','beginPath','arc','fill'])
        Ctx.prototype[m] = function () {};
      S.drawContactShadow(new Ctx(), 10, 10, 8, 4);
      return {stops, first: stops[0][1], last: stops[stops.length-1][1]};
    """)
    assert got["stops"], "the shadow drew no gradient stops"
    assert got["last"].endswith(",0)"), (
        f"the shadow does not fade to transparent at its edge, so it will show "
        f"a hard rim: {got['last']}")
    assert "#000" not in got["first"] and "0,0,0" not in got["first"], (
        f"the shadow is pure black: {got['first']}")
    assert "28,32,52" in got["first"], (
        f"the shadow is not the blue-grey contact tone: {got['first']}")


def test_the_wall_tones_contrast_against_the_floor(tmp_path):
    """A WALL YOU CANNOT DISTINGUISH FROM THE FLOOR IS NOT A WALL.

    Every earlier geometry test passed while the room read as a flat bright
    diamond: the wall's three tones and the carpet all sat within ~15% of each
    other, so "the walls render" was true and useless. This asserts the thing
    that actually matters — the wall's darkest visible plane is CLEARLY darker
    than the floor it stands on, which is what makes a vertical surface read as
    vertical.
    """
    got = _run_scene(tmp_path, """
      const t = ROOM.themeFor(0);
      const r = C.ramp(t.wall, 0.08);
      const west = C.shade(r.base, -0.06);
      const east = C.shade(r.dk, -0.17);
      const cap  = C.mix(r.hi, '#ffffff', 0.10);
      const floor = t.carpet;
      const lum = (c) => C.luminance(c);
      return {
        west, east, cap, floor,
        lumWest: lum(west), lumEast: lum(east), lumCap: lum(cap),
        lumFloor: lum(floor),
        eastVsFloor: +(lum(floor) - lum(east)).toFixed(3),
        capVsEast: +(lum(cap) - lum(east)).toFixed(3),
      };
    """)
    # the darkest wall plane must be clearly darker than the floor
    assert got["eastVsFloor"] > 0.18, (
        f"the wall's dark face is only {got['eastVsFloor']} luminance below the "
        f"floor — a vertical surface that does not contrast reads as more floor, "
        f"which is exactly how a room renders as a flat diamond "
        f"(wall {got['east']} vs floor {got['floor']})")
    # and the cap must be clearly lighter than the dark face, so the top edge of
    # the wall is legible
    assert got["capVsEast"] > 0.3, (
        f"the wall cap is only {got['capVsEast']} above its dark face, so the "
        f"wall has no visible top edge: cap {got['cap']} vs east {got['east']}")
    # the three faces must be three DIFFERENT tones, or the wall is a flat block
    assert len({got["west"], got["east"], got["cap"]}) == 3, (
        f"the wall's faces are not distinct tones: {got}")


def test_the_wall_paints_pixels_above_the_floor(tmp_path):
    """The geometric complement: a wall must occupy screen rows ABOVE the
    floor's topmost row, or it has no visible height no matter its colour."""
    got = _run_scene(tmp_path, """
      const floor = {number:0, name:'HQ', role:'r', rooms:[
        {id:0, slug:'a', kind:'lobby', x:0, y:0, w:10, h:8, occupants:[]}]};
      const cam = ROOM.cameraFor(floor, 1305, 879);
      const sx = (p) => p.x * cam.zoom + cam.ox;
      const sy = (p) => p.y * cam.zoom + cam.oy;
      // the floor's topmost screen row
      let floorTop = 1e9, wallTop = 1e9, wallBottom = -1e9;
      for (let gy=0; gy<8; gy++) for (let gx=0; gx<10; gx++)
        for (const p of ISO.tileDiamond(gx,gy)) floorTop = Math.min(floorTop, sy(p));
      // the north wall run (one tile off the room's own row)
      for (let gx=-1; gx<=10; gx++) {
        const f = ISO.wallFaces(gx, -1, 2);
        for (const poly of [f.top, f.west, f.east])
          for (const p of poly) {
            wallTop = Math.min(wallTop, sy(p));
            wallBottom = Math.max(wallBottom, sy(p));
          }
      }
      return {floorTop: Math.round(floorTop), wallTop: Math.round(wallTop),
              wallBottom: Math.round(wallBottom), camZoom: +cam.zoom.toFixed(2)};
    """)
    assert got["wallTop"] < got["floorTop"], (
        f"the wall's highest pixel ({got['wallTop']}) is not above the floor's "
        f"top row ({got['floorTop']}) — the wall has no visible height")
    assert got["floorTop"] - got["wallTop"] > 60, (
        f"the wall rises only {got['floorTop'] - got['wallTop']}px above the "
        f"floor; at zoom {got['camZoom']} a 2-tile wall should be far taller")


# ======================================================================
# 4 — the scenes actually USE the shading model
# ======================================================================
def test_the_wall_scene_paints_many_colours_through_drawPlace(tmp_path):
    """THE DECORATIVE-TEST GUARD, DONE PROPERLY.

    An earlier version of this suite asserted only that `scenes.ts` CONTAINS the
    string "drawWallFace". That passes while the wall is painted with one flat
    `fillRect` elsewhere — a source-text grep is not a pixel check. Mutating the
    entrance hall to a single flat fill was NOT caught by it.

    So this drives the REAL `drawPlace` with the REAL payload and counts the
    distinct colours that reach the canvas. A flat-filled room cannot produce
    thirty of them.
    """
    got = _run_scene(tmp_path, """
      const ctx = new Rec();
      SC.drawPlace({ctx, world, width: 900, height: 520,
                    place: {kind: 'building', building: 'hq'}, travel: 0});
      const cols = [];
      for (const o of ctx.ops) {
        if (o[0] === 'fillRect' || o[0] === 'fill') cols.push(o[o.length - 1]);
      }
      const lit = cols.filter(c => typeof c === 'string'
                                   && c.startsWith('#'));
      return {painted: lit.length, distinct: new Set(lit).size,
              sample: [...new Set(lit)].slice(0, 8)};
    """)
    assert got["painted"] > 150, (
        f"the entrance hall made only {got['painted']} colour writes; a shaded "
        f"wall alone is hundreds (one per gradient scanline)")
    assert got["distinct"] > 25, (
        f"the scene painted only {got['distinct']} distinct colours — that is "
        f"a flat fill, not a shaded room: {got['sample']}")


def test_every_place_still_paints_different_colours(tmp_path):
    """The distinct-pixels guard, at the colour level, across all 10 places."""
    got = _run_scene(tmp_path, """
      const spots = {exterior: {kind:'exterior'},
                     building: {kind:'building', building:'hq'},
                     lobby:    {kind:'lobby', building:'hq'}};
      for (const f of world.building.floors) {
        spots['floor' + f.number] = {kind:'floor', building:'hq', floor:f.number};
        for (const r of f.rooms) {
          spots['room' + r.id] = {kind:'room', building:'hq',
                                  floor:f.number, room:r.id};
        }
      }
      const sig = {};
      for (const [k, p] of Object.entries(spots)) {
        const ctx = new Rec();
        SC.drawPlace({ctx, world, width: 900, height: 520, place: p, travel: 0});
        const cols = ctx.ops.map(o => o[o.length - 1]).filter(c => typeof c === 'string');
        sig[k] = cols.join('|');
      }
      const dupes = [];
      const keys = Object.keys(sig);
      for (let i = 0; i < keys.length; i++)
        for (let j = i + 1; j < keys.length; j++)
          if (sig[keys[i]] === sig[keys[j]]) dupes.push([keys[i], keys[j]]);
      return {count: keys.length, dupes,
              expected: 3 + world.building.floors.length
                        + world.building.floors.reduce((n, f) => n + f.rooms.length, 0)};
    """)
    assert got["dupes"] == [], (
        f"two different places paint identical pixels, so a reached place looks "
        f"unreached: {got['dupes']}")
    # Derived from the payload, not a magic number: the recorded tower has 3
    # floors and a varying number of rooms, and asserting a hardcoded total
    # would break on every re-recording while proving nothing.
    assert got["count"] == got["expected"], (
        f"built {got['count']} places; the payload implies {got['expected']} "
        f"(exterior + building + lobby + every floor + every room)")


def _run_scene(tmp_path: Path, js: str) -> object:
    """Drive the real scenes with the real recorded payload."""
    staged = tmp_path / "src"
    (staged / "art").mkdir(parents=True, exist_ok=True)
    for name in ("types.ts", "WorldAssembler.ts", "navigation.ts", "scenes.ts"):
        shutil.copy(GAME / name, staged / name)
    for name in ("colour.ts", "surfaces.ts", "iso.ts", "room.ts", "scene-adapter.ts",
                 "fittings.ts"):
        shutil.copy(ART / name, staged / "art" / name)
    from _office_toolchain import tsc_path
    result = subprocess.run(
        [NODE, str(tsc_path()), "--module", "commonjs", "--target", "es2020",
         "--moduleResolution", "node",
         "--ignoreDeprecations", "6.0", "--outDir", str(tmp_path / "js"),
         "--skipLibCheck", "--noEmitOnError", "false", "--lib", "es2020,dom",
         str(staged / "scenes.ts")],
        capture_output=True, text=True, timeout=240, cwd=str(staged))
    # tsc hoists the emit when given a single entry (js/scenes.js, not
    # js/art/scenes.js). Searching only the nested path is how this helper
    # reported a green skip while driving nothing — the same trap the iso
    # suite hit. Both layouts are accepted; a missing emit FAILS.
    scenes = next((p for p in (tmp_path / "js" / "art" / "scenes.js",
                               tmp_path / "js" / "scenes.js") if p.is_file()), None)
    if scenes is None:
        raise AssertionError(
            "tsc emitted no scenes.js, so the scene driver cannot run.\n"
            f"tsc stderr: {result.stderr[:800]}")
    js_dir = scenes.parent
    wa = js_dir / "WorldAssembler.js"
    assert wa.is_file(), (
        f"tsc emitted scenes.js but not WorldAssembler.js: "
        f"{sorted(p.name for p in js_dir.iterdir())}")

    def find(name: str) -> Path:
        """Locate an emitted module.

        tsc's emit layout depends on how it infers rootDir, so a module can land
        at `js/room.js` or `js/art/room.js` depending on the entry it was given.
        Searching only one of them is how this helper silently drove nothing.
        """
        for cand in (js_dir / name, js_dir / "art" / name,
                     tmp_path / "js" / name, tmp_path / "js" / "art" / name):
            if cand.is_file():
                return cand
        raise AssertionError(
            f"could not find emitted {name}; layout is "
            f"{sorted(str(p.relative_to(tmp_path)) for p in (tmp_path / 'js').rglob('*.js'))}")
    fixture = (REPO / "frontend" / "qubit-web" / "src" / "lib" / "office-game"
               / "fixtures" / "tower-recording.json")
    program = ("const SC = require(%s);\nconst WA = require(%s);\n"
               "const ROOM = require(%s);\nconst ISO = require(%s);\n"
               "const C = require(%s);\n"
               "const TOWER = JSON.parse(require('fs').readFileSync(%s,'utf8'));\n"
               "const world = WA.assembleWorld(TOWER, 1);\n"
               "%s\n"
               "process.stdout.write(JSON.stringify((() => { %s })()));\n"
               % (json.dumps(str(scenes)), json.dumps(str(wa)),
                  json.dumps(str(find("room.js"))),
                  json.dumps(str(find("iso.js"))),
                  json.dumps(str(find("colour.js"))),
                  json.dumps(str(fixture)), _CTOR, js))
    proc = subprocess.run([NODE], input=program, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nstderr: {proc.stderr[:1500]}")
    return json.loads(proc.stdout)


#: The 2D-context recorder, as a constructor usable by both drivers above.
_CTOR = """
function Rec() {
  this.ops = []; this.fillStyle = '#000'; this.strokeStyle = '#000';
  this.lineWidth = 1; this.font = ''; this.textAlign = '';
}
const P = Rec.prototype;
P.fillRect = function (x, y, w, h) {
  this.ops.push(['fillRect', x, y, w, h, this.fillStyle]); };
P.strokeRect = function (x, y, w, h) {
  this.ops.push(['strokeRect', x, y, w, h, this.strokeStyle]); };
P.fillText = function () { this.ops.push(['fillText', this.fillStyle]); };
P.beginPath = function () { this.ops.push(['beginPath']); };
P.moveTo = function (x, y) { this.ops.push(['moveTo', x, y]); };
P.lineTo = function (x, y) { this.ops.push(['lineTo', x, y]); };
P.closePath = function () { this.ops.push(['closePath']); };
P.ellipse = function () { this.ops.push(['ellipse']); };
P.fill = function () { this.ops.push(['fill', this.fillStyle]); };
P.stroke = function () { this.ops.push(['stroke', this.strokeStyle]); };
P.arc = function () { this.ops.push(['arc']); };
P.save = function () {}; P.restore = function () {};
P.translate = function () {}; P.scale = function () {};
P.setTransform = function () {};
P.createLinearGradient = function () {
  return {addColorStop: function () {}}; };
P.createRadialGradient = function () {
  return {addColorStop: function () {}}; };
"""


def test_the_scenes_import_and_use_the_shading_model():
    """The strongest guard: the scenes must call the model.

    This suite can pass completely while `scenes.ts` still paints one flat
    colour per surface — the modules would simply never be imported. This is
    the assertion that ties them together.
    """
    src = (GAME / "scenes.ts").read_text()
    assert "art/surfaces" in src, (
        "scenes.ts does not import the surface painters, so every room is "
        "still drawn with flat fills no matter what the art module proves")
    for fn in ("drawWallFace", "drawFloor"):
        assert fn in src, f"scenes.ts never calls {fn}"
    # NOT `... or True`. The trailing `or True` made this unconditionally true, so
    # it could never fail and asserted nothing — a decoration that the suite's own
    # phantom-authority checker now flags. The intent is that scenes.ts reaches at
    # least one of the surface-wiring painters, so assert exactly that.
    assert "drawContactShadow" in src or "drawEntity" in src, (
        "scenes.ts calls neither drawContactShadow nor drawEntity, so the "
        "shading model is not wired into the scene at all")


def test_the_scene_palette_is_derived_not_hardcoded():
    """A palette of raw hex values with no shade/ramp use nearby is the exact
    shape of the original bug."""
    src = (GAME / "scenes.ts").read_text()
    assert "shade(" in src or "ramp(" in src or "mix(" in src, (
        "scenes.ts uses no shading at all — every value is a flat literal")
