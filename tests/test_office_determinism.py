"""Office renderer — the DRAWING is a pure function of the payload.

Why this file exists
--------------------
`tests/test_office_tower_plugin.py` asserts on the renderer's *text*: exactly one
timer, no `Math.random`. `tests/test_office_tower_visual.py` asserts on the
pixels of one draw. Neither proves the property that actually matters for a
renderer that is redrawn on every poll: **the same payload always produces the
same draw calls.** A renderer that reads a clock, calls `Math.random`, or walks
an object in insertion order still passes both of those suites, and still draws
a different picture every frame.

That property is not decoration here. Two things went wrong in this project and
both are the same defect class:

  * a plugin module holding a live `setInterval` made a bare `node -e` harness
    HANG FOREVER — proof that the timer-free rule is load-bearing, not style;
  * a suite failed 12/12 while another process was non-atomically rewriting a
    file it read — proof that "read a file, trust its bytes" is unsafe.

The renderer must be a pure function of its payload. This file executes the real
drawing code twice (or once per variant) and compares the recorded call
sequences op by op, argument by argument.

How it runs
-----------
The fake-canvas harness is the one from `tests/test_office_tower_visual.py`: the
plugin is evaluated in `node`, its final `register(...)` line is rewritten to
expose the drawing helpers, and every drawing call is made against a context
that records each `fillRect` / `arc` / `ellipse` / `moveTo` / `lineTo` /
`fillText` / `fill` / `stroke` plus the `fillStyle`/`globalAlpha` in force. The
harness is *imported from that file*, not re-implemented, so there is exactly one
definition of "what a draw call is" in the suite and the two files cannot drift.
That file is never modified.

Every assertion below is capable of failing; two of them were proven to fail
against a mutated SCRATCH copy of the renderer (a `Math.random()` jitter in the
draw path, and a `.reverse()` on the paint order). See the module report.
"""

from __future__ import annotations

import importlib.util
import json
import random
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ENTRY = (REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
         / "qubit-office.js")

# Reuse the established harness rather than duplicating it. Loading the sibling
# test module by path (it is a test module, not an importable package member)
# gives us `_draw`, `_js_result`, `make_payload`, `make_floor`, `make_room`,
# `make_character` and the `texts`/`fills` classifiers, all proven by that file.
def _load_harness():
    path = REPO / "tests" / "test_office_tower_visual.py"
    spec = importlib.util.spec_from_file_location("_office_visual_harness", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = _load_harness()

# The harness reads the renderer path from ITS `ENTRY` and caches the file text
# on first use. Point both at the mutated copy when the mutation prover sets
# this var — the real file is never written to. No-op in a normal run.
import os as _os
_OVERRIDE = _os.environ.get("OFFICE_ENTRY_OVERRIDE")
if _OVERRIDE:
    ENTRY = Path(_OVERRIDE)
    H.ENTRY = Path(_OVERRIDE)
    H._CACHE.clear()


# --------------------------------------------------------------- payload shapes
# One definition of the payload here; `make_*` come from the harness so the two
# files cannot disagree about the shape.

def characters_payload(slugs: list[str], positions: bool = True) -> dict:
    """A single-floor office holding one character per slug.

    `positions=True` gives each character a distinct (x, y) so the depth sort
    has real work to do and the `(slug, x, y)` set is not degenerate; with
    distinct positions a shuffled array must still sort to the same near->far
    order.
    """
    rooms = H.make_room("open", 0, 0, 12, 12, slugs)
    floors = [H.make_floor(1, 0, "operations", [rooms])]
    chars = []
    for i, s in enumerate(slugs):
        if positions:
            pos = {"x": 1.5 + (i % 6), "y": 2.5 + (i // 6)}
        else:
            pos = {"x": 1.5, "y": 1.5}
        chars.append(H.make_character(s, 1, "idle", position=pos))
    return H.make_payload(floors, chars)


def _reverse_keys(value):
    """Rebuild the SAME logical payload with every object's keys reversed.

    A JSON round trip through a different serializer (or a Python dict with a
    different insertion order) presents the same data with a different key
    order. If the renderer iterates an object with `for..in` / `Object.keys`
    anywhere in the draw path, the picture changes with the serializer — a
    real defect, because two clients on the same payload would render two
    different offices.
    """
    if isinstance(value, dict):
        return {k: _reverse_keys(value[k]) for k in reversed(list(value.keys()))}
    if isinstance(value, list):
        return [_reverse_keys(v) for v in value]
    return value


def _record_ops(payload) -> dict:
    """Draw `payload` with the real `drawTower` and record every call.

    Returns `{n, ops, agents}`: the op count, the JSON of each recorded call in
    order, and the `(slug, x, y)` of every agent the renderer actually placed.
    """
    return H._draw(payload, (
        "var rec = c.ops.map(function (o) { return JSON.stringify(o); });"
        "__RESULT = {n: c.ops.length, ops: rec,"
        "  agents: out.probe.agents.map(function (a) {"
        "    return a.cs.slug + '@' + a.x.toFixed(6) + ',' + a.y.toFixed(6);"
        "  })};"))


def _first_divergence(a: list, b: list):
    """The first index where two op sequences differ, or None if equal."""
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        if x != y:
            return (i, x, y)
    return None


# ============================================================= 1. same payload
def test_the_same_payload_draws_the_identical_call_sequence():
    """Render one payload twice; the two recorded call sequences are identical.

    Same number of ops, same op in every position, same arguments on every op.
    This is the base determinism property: a redraw of an unchanged payload
    must not differ from the frame before it, or the office shimmers.
    """
    payload = characters_payload(["alpha", "beta", "gamma", "delta", "epsilon"])
    first = _record_ops(payload)
    second = _record_ops(payload)

    assert first["n"] > 0, (
        "the renderer drew nothing, so 'the same payload draws the same calls' "
        "is vacuously true: %r" % first["n"])
    assert first["n"] == second["n"], (
        "two renders of one payload drew a different NUMBER of calls: %d vs %d"
        % (first["n"], second["n"]))
    div = _first_divergence(first["ops"], second["ops"])
    assert div is None, (
        "two renders of the SAME payload diverged at call #%d:\n  first:  %s\n"
        "  second: %s" % div)
    # Report the count so a shrinking render is visible in the test output.
    print("determinism: %d recorded calls, identical across both renders"
          % first["n"])


# ============================================================ 2. key ordering
def test_reversing_every_object_key_does_not_change_the_draw_calls():
    """Rebuild the payload with reversed object keys; the calls still match.

    `_reverse_keys` reverses the insertion order of every object at every depth
    — the floorplan, each floor, each room, each seat, each character, each
    character's `position`, the `hud`. If the draw path reads any of those with
    `for..in` / `Object.keys` instead of by name, the two sequences differ and
    the renderer is iterating a dict, not reading fields.
    """
    payload = characters_payload(["one", "two", "three", "four"])
    forward = _record_ops(payload)
    reversed_keys = _record_ops(_reverse_keys(payload))

    assert forward["n"] == reversed_keys["n"], (
        "reversing the object keys changed the NUMBER of draw calls (%d -> %d): "
        "the draw path iterates an object in insertion order"
        % (forward["n"], reversed_keys["n"]))
    div = _first_divergence(forward["ops"], reversed_keys["ops"])
    assert div is None, (
        "key order changed the picture: the call sequences diverged at #%d\n"
        "  forward keys: %s\n  reversed keys: %s"
        % (div[0], div[1], div[2]))
    print("key order: %d calls, identical under a full recursive key reversal"
          % forward["n"])


# ============================================== 3. array order where it matters
def test_shuffling_the_characters_array_leaves_the_same_drawn_agents():
    """A shuffled `characters` array draws the same SET of agents.

    The renderer flattens everything into one list and sorts by a depth key; a
    depth sort exists precisely so the payload's array order does not decide the
    painter's order. So shuffling `characters` may reorder nothing (the sort is
    total), and must reorder nothing that matters: the set of `(slug, x, y)`
    drawn is identical, and every agent is drawn exactly once, on the same spot.

    If the sort is not total, a shuffle changes which of two same-key items
    paints first and the drawing smears — a real finding. The set comparison is
    the invariant; the op-order report below makes a non-total sort visible
    without failing on a legitimate reorder.
    """
    payload = characters_payload(
        ["c%02d" % i for i in range(8)])          # 8 distinct positions
    base = _record_ops(payload)

    # Deterministic shuffle (seeded) so a failure is reproducible.
    chars = list(payload["characters"])
    random.Random(20261013).shuffle(chars)
    shuffled_payload = dict(payload)
    shuffled_payload["characters"] = chars
    shuffled = _record_ops(shuffled_payload)

    assert len(base["agents"]) == 8, (
        "the reference render did not place 8 agents: %r" % base["agents"])
    assert sorted(base["agents"]) == sorted(shuffled["agents"]), (
        "shuffling the characters array changed WHICH agents are drawn (or "
        "where): the depth sort is not invariant to payload order.\n"
        "  base:     %r\n  shuffled: %r"
        % (sorted(base["agents"]), sorted(shuffled["agents"])))
    # No agent drawn twice and none lost by the reorder.
    assert len(set(shuffled["agents"])) == len(shuffled["agents"]) == 8, (
        "shuffling produced a duplicate or a missing agent: %r"
        % sorted(shuffled["agents"]))

    div = _first_divergence(base["ops"], shuffled["ops"])
    if div is not None:
        # Not a failure — a legitimate depth reorder is allowed to change op
        # order as long as the drawn-agent SET is identical (asserted above).
        # Reported so a non-total sort that only reorders ties is visible.
        print("array order: agent set identical; op order differs at #%d "
              "(tolerated, set is invariant)" % div[0])
    else:
        print("array order: %d calls, byte-identical after shuffling "
              "characters (the depth sort is total)" % base["n"])


def test_a_total_depth_sort_is_invariant_to_array_order_even_on_key_ties():
    """Two agents on the SAME cell (same depth key) keep their relative order.

    This is the total-order case test 3's set comparison cannot see. Two seats
    at one point tie on the depth key, so the sort needs a deterministic
    tiebreak (the renderer uses the item `id`) or the painter's order falls
    back to the payload array — and reversing the array reverses the drawing.
    A `(slug, x, y)` SET is identical either way, so only an OP-ORDER comparison
    catches it. With the id tiebreak in place the op sequence is byte-identical
    across a shuffle; with the tiebreak dropped (`return 0`, relying on V8's
    stable sort) the array order leaks straight through.
    """
    payload = H.make_payload(
        [H.make_floor(1, 0, "operations",
                      [H.make_room("open", 0, 0, 6, 6, ["aa", "bb"])])],
        [H.make_character("aa", 1, "idle", position={"x": 3, "y": 3}),
         H.make_character("bb", 1, "idle", position={"x": 3, "y": 3})])
    forward = _record_ops(payload)

    chars = list(payload["characters"])
    chars.reverse()
    rev_payload = dict(payload)
    rev_payload["characters"] = chars
    reversed_arr = _record_ops(rev_payload)

    assert len(forward["agents"]) == 2, forward["agents"]
    div = _first_divergence(forward["ops"], reversed_arr["ops"])
    assert div is None, (
        "two agents on the SAME depth key swapped paint order when the payload "
        "array was reversed — the depth sort is not TOTAL (no deterministic "
        "tiebreak), so the picture follows the array.\n  divergence at #%d:\n"
        "  forward: %s\n  reversed: %s" % (div[0], div[1], div[2]))
    print("key ties: 2 agents on one cell, %d calls byte-identical after "
          "reversing the array (the depth sort is total)" % forward["n"])


def test_array_order_does_not_decide_the_paint_order_of_floors():
    """Reversing the floors array does not change which deck paints first.

    The companion case to the character shuffle, on `floorplan.floors`: the
    deck labels must appear in DEPTH order regardless of the array the payload
    was serialised from.
    """
    labels_js = ("__RESULT = {labels: texts(c).filter(function (t) "
                 "{ return /^FLOOR /.test(t); })};")
    asc = H.make_payload(
        [H.make_floor(1, 0, "operations", [H.make_room("low", 0, 0, 4, 4, [])]),
         H.make_floor(2, 1, "executive", [H.make_room("high", 0, 0, 4, 4, [])])],
        [])
    desc = H.make_payload(
        [H.make_floor(2, 1, "executive", [H.make_room("high", 0, 0, 4, 4, [])]),
         H.make_floor(1, 0, "operations", [H.make_room("low", 0, 0, 4, 4, [])])],
        [])
    a = H._draw(asc, labels_js)["labels"]
    b = H._draw(desc, labels_js)["labels"]
    assert a == b, (
        "the paint order followed the floors ARRAY instead of the depth key: "
        "reversing the array changed it. %r vs %r" % (a, b))
    print("floors array reversed: paint order unchanged (%r)" % (a,))


# ================================================================ 4. the clock
# The check has to survive the bug it is guarding against: a previous stripper
# in this repo removed the very STRING LITERALS it meant to inspect, so the
# scan passed on a file full of the forbidden text. This stripper removes
# comments only and KEEPS every string literal.
_JS_STRINGS_AND_COMMENTS = re.compile(
    r"""
    (?P<block> /\*.*?\*/ )          # /* block comment */
  | (?P<line>  //[^\n]* )           # // line comment
  | (?P<squote> '(?:\\.|[^'\\])*' ) # 'single quoted string'  KEPT
  | (?P<dquote> "(?:\\.|[^"\\])*" ) # "double quoted string"  KEPT
  | (?P<tick>   `(?:\\.|[^`\\])*` ) # `template literal`       KEPT
    """,
    re.DOTALL | re.VERBOSE,
)

CLOCK_AND_RANDOM = ("Date.now", "performance.now", "new Date", "Math.random")


def _strip_comments_keep_strings(src: str) -> str:
    """Blank out comments, preserve string literals and every newline.

    A string literal may contain `//` or `/*` (a URL, a regex-looking label);
    a comment may contain a quote. Scanning line by line with `//` would delete
    the string and let a `Date.now` in a string (or hiding just after one)
    survive or vanish wrongly. So we tokenise: comments are replaced with
    spaces and strings are copied through untouched.

    Newlines inside a comment are KEPT (as `\\n`), so the line number a hit is
    reported at is the line number in the real file. A stripper that collapses
    a multi-line block comment shifts every line after it and reports the wrong
    location — a second way to make a scanner useless.
    """
    def repl(m: re.Match) -> str:
        text = m.group()
        if m.lastgroup in ("block", "line"):
            # erase the comment but preserve its newlines so line numbers hold
            return "".join("\n" if ch == "\n" else " " for ch in text)
        return text                         # KEEP the string literal
    return _JS_STRINGS_AND_COMMENTS.sub(repl, src)


def test_the_stripper_keeps_strings_and_removes_comments():
    """Guard the guard: the scanner must not repeat the deleted-strings bug.

    A string literal containing `//` and a `Date.now` inside a comment are the
    two shapes that break a naive stripper. Assert the string survives and the
    comment does not, before trusting the real scan.
    """
    sample = (
        'var url = "http://example/Date.now"; // Date.now here\n'
        '/* Math.random\n   in a block */\n'
        'var s = \'Math.random\';\n'
        'var t = `Date.now ${x}`;\n'
    )
    out = _strip_comments_keep_strings(sample)
    # strings KEPT (the bug that deleted them must not recur)
    assert "http://example/Date.now" in out, (
        "the scanner deleted a string literal it was meant to inspect: %r" % out)
    assert "'Math.random'" in out, "a single-quoted string was deleted: %r" % out
    assert "`Date.now ${x}`" in out, "a template literal was deleted: %r" % out
    # comments REMOVED (so a commented-out call is not a false positive)
    assert "// Date.now here" not in out, "a line comment survived: %r" % out
    assert "Math.random\n   in a block" not in out, (
        "a block comment survived: %r" % out)


def test_the_renderer_source_has_no_clock_and_no_randomness():
    """No wall clock, no PRNG in the DRAWING layer of the file.

    The drawing is the region of the file from the geometry helpers through
    `drawTower` / `drawMinimap` / `drawPnl` — everything before the React
    component `Office()` at line ~1002. Comments are stripped and strings KEPT,
    and each forbidden token is reported with its real line numbers (the
    stripper preserves newlines, so the numbers are true).

    `setInterval` is deliberately NOT scanned here — `test_office_tower_plugin.py`
    owns the one-timer rule; this test owns 'the drawing cannot read a clock or
    roll a die'. A timer that only polls the backend does not make the DRAW
    impure; that is what the other file pins.
    """
    src = ENTRY.read_text()
    lines = src.splitlines()

    # The drawing layer ends where the React component begins. Found by name so
    # a line-number shift does not silently move the boundary.
    office_at = next(i for i, ln in enumerate(lines)
                     if ln.strip().startswith("function Office()"))
    draw_layer = "\n".join(lines[:office_at])
    stripped = _strip_comments_keep_strings(draw_layer)
    stripped_lines = stripped.splitlines()

    hits: dict[str, list[int]] = {}
    for token in CLOCK_AND_RANDOM:
        found = [i + 1 for i, line in enumerate(stripped_lines)
                 if token in line]
        if found:
            hits[token] = found
    assert not hits, (
        "the office DRAWING layer reads a clock or a RNG, so its drawing is not "
        "a pure function of the payload:\n%s" % json.dumps(hits, indent=2))

    # The whole file is scanned too, so a clock anywhere in the renderer is at
    # least NAMED. A hit ABOVE `Office()` already failed above; a hit below it
    # is in the React DOM and is reported rather than asserted, because it does
    # not touch the canvas — but it is a finding, not a clean bill of health.
    whole = _strip_comments_keep_strings(src)
    whole_lines = whole.splitlines()
    dom_hits = {t: [i + 1 for i, ln in enumerate(whole_lines) if t in ln]
                for t in CLOCK_AND_RANDOM}
    dom_hits = {t: v for t, v in dom_hits.items() if v}

    # `drawTower` itself (the payload entry point this file mostly exercises) is
    # asserted clean by name, independent of the region boundary above.
    tower = "\n".join(lines[661:805])            # drawTower..(before drawMinimap)
    assert "function drawTower" in tower, (
        "the drawTower slice is stale — update the span (the source moved): %r"
        % tower[:80])
    tower_clean = _strip_comments_keep_strings(tower)
    for token in CLOCK_AND_RANDOM:
        assert token not in tower_clean, (
            "drawTower contains %r; the payload entry point is not a pure "
            "function of its input" % token)

    # Positive control: the same scanner DOES find a planted token, so the
    # green result above is a real absence and not a scanner matching nothing.
    planted = _strip_comments_keep_strings("var x = Date.now(); // Math.random")
    assert "Date.now" in planted, (
        "the scanner failed to see a planted Date.now — the negative result "
        "above is meaningless")
    assert "Math.random" not in planted, (
        "the scanner counted a token inside a comment: %r" % planted)

    print("clock/random scan: 0 hits in the drawing layer (%d lines, %d before "
          "Office()); file-wide hits outside the draw path: %s"
          % (len(stripped_lines), office_at, json.dumps(dom_hits) or "none"))


def test_a_clock_in_the_dom_panel_is_reported_not_hidden():
    """The file-wide scan must SEE the one real `new Date` and place it.

    The renderer reads a wall clock exactly once, in the React activity feed
    (`new Date(row.ts * 1000).toLocaleTimeString()`), to format a timestamp for
    a text row. That is BELOW `Office()` — it never runs inside `drawTower`,
    `drawMinimap`, `drawPnl` or any helper, so it cannot make the drawing
    nondeterministic; but `toLocaleTimeString()` is locale/timezone dependent,
    so it is a genuine (if cosmetic) nondeterminism in the UI text and must be
    reported rather than swept up. This test pins that it stays OUT of the draw
    path: if a future commit moves a clock into `drawTower` and this file's
    region scan is ever weakened, this assertion still fails.
    """
    src = ENTRY.read_text()
    stripped_lines = _strip_comments_keep_strings(src).splitlines()
    hits = [i + 1 for i, ln in enumerate(stripped_lines)
            if "new Date" in ln or "Date.now" in ln or "Math.random" in ln]
    office_at = next(i for i, ln in enumerate(src.splitlines())
                     if ln.strip().startswith("function Office()")) + 1
    assert all(n > office_at for n in hits), (
        "a clock/RNG token sits in the DRAWING layer (line %s; the drawing "
        "layer ends at Office() on line %d) — the drawing is not a pure "
        "function of the payload" % (hits, office_at))
    # And the draw entry points, checked directly, contain none.
    draw_fns_src = "\n".join(src.splitlines()[661:1001])
    dc = _strip_comments_keep_strings(draw_fns_src)
    for token in CLOCK_AND_RANDOM:
        assert token not in dc, (
            "%r appears inside drawTower/drawMinimap/drawPnl — the drawing can "
            "read a clock or roll a die" % token)
    print("file-wide clock/RNG tokens: %r, all in the React DOM below Office() "
          "(line %d); draw entry points clean" % (hits, office_at))


# ============================================================ 5. no collisions
def test_thirty_near_identical_slugs_get_thirty_distinct_colours():
    """30 slugs differing by one character draw as 30 distinct colours.

    This is the `seatColour` fix proven at scale rather than at 12: adjacent
    slugs ("slug-00", "slug-01", …) differ only in their last character, and a
    weak hash (`abs(h) % PALETTE.length`) collapses them onto a handful of
    palette entries — a room of 30 people drawing as a few blobs. The hash is
    `h * 31 + c` over a large modulus so a one-character difference spreads
    across the wheel; the count here is the evidence.
    """
    slugs = ["slug-%02d" % i for i in range(30)]
    r = H._js_result(
        "__RESULT = {c: %s.map(function (s) "
        "{ return X.seatColour(s, 'worker'); })};" % json.dumps(slugs))
    colours = r["c"]
    assert len(colours) == 30
    distinct = len(set(colours))
    assert distinct == 30, (
        "%d slugs differing by one character produced only %d distinct "
        "colours: %r — two agents would draw the same colour and read as one"
        % (len(slugs), distinct, sorted(set(colours))))

    # And the colour is a pure function of the slug: a second call is identical.
    again = H._js_result(
        "__RESULT = {c: %s.map(function (s) "
        "{ return X.seatColour(s, 'worker'); })};" % json.dumps(slugs))["c"]
    assert colours == again, (
        "seatColour is not a pure function of the slug: two calls disagreed "
        "for some slug")
    print("seat colour: 30/30 distinct for adjacent slugs, stable across calls")
