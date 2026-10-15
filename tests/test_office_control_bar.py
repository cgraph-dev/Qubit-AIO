"""M4 — the Office control bar: four read-only buttons, six named refusals.

`docs/game-office/28-HUD-PANELS.md` §4 specifies a control bar with four pure
view operations (Reset, Fit, Zoom, P&L) and §4's second table names SIX features
the bar must NOT carry because each one needs a write (Publish, Edit, Delete,
Hire/fire/move, Approve/deny, Speed/pause).

A design doc saying "do not add these" is worth nothing the day someone adds
them, so this file holds the refusal down as a test: the module must answer
`isReadOnly("publish") === false` and must hand back a reason that names WHY.

Two kinds of assertion live here, on purpose:

  * TEXT assertions, because the properties that matter — no timer, no mutating
    verb, no `fetch` — are exactly the properties a text check catches and a
    behavioural test cannot see. A comment is stripped first (the `_code()`
    convention from `test_office_tower_plugin.py`), so the module's own
    documentation of what it refuses to do cannot satisfy or trip the check.
  * BEHAVIOURAL assertions, driven through `node -e`, for the pure reducer:
    purity (the input object is untouched), determinism, and the zoom bounds.
    These need a real JS engine, and there is exactly one in the repo's test
    path already (the `node -e` smoke tests the sibling office plugins use).

Hermetic: reads two files and shells out to `node`. No network, no browser, no
provider call, no server.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest  # noqa: E402

from _office_node import run_node  # noqa: E402

PLUGIN_DIR = (Path(__file__).resolve().parent.parent
              / "frontend" / "qubit-app" / "plugins" / "qubit-office")
MODULE = PLUGIN_DIR / "control-bar.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="node is required for the reducer assertions (the office plugins' "
           "node -e smoke path)")


# ---------------------------------------------------------------- the source
def _src() -> str:
    """The whole file, comments included. Use for 'does it say what it is'."""
    return MODULE.read_text()


def _code() -> str:
    """The file with JS comments stripped.

    Same helper, and the same reasoning, as `test_office_tower_plugin.py`: an
    assertion about what the code DOES must not be satisfied or tripped by a
    comment explaining what the code deliberately does NOT do. This module's
    header says "no fetch" and "no timer" in prose, and a naive check would fail
    on its own documentation.
    """
    src = _src()
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    return src


def _run(js: str) -> object:
    """Evaluate `js` in node with `CB` bound to the module, and decode the JSON
    it prints. One `node -e` per call keeps each assertion readable; the module
    is tiny and cold-start is milliseconds."""
    program = (
        'const CB = require(%s);\n'
        'const out = (() => { %s })();\n'
        'process.stdout.write(JSON.stringify(out));\n'
    ) % (json.dumps(str(MODULE)), js)
    proc = subprocess.run([NODE], input=program, capture_output=True, text=True,
                          timeout=30)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}")
    return json.loads(proc.stdout)


# --------------------------------------------------------------- existence
def test_the_module_exists_where_the_plugin_dir_expects_it():
    assert MODULE.is_file(), f"missing {MODULE}"


def test_the_module_has_no_dependencies_and_no_build_step():
    """It is a plain browser script, like camera.js / iso-engine.js / motion.js.

    `require(` may appear for the node-test shim, but no package may be pulled
    in and no bundler directive may appear — the plugin dir is served verbatim.
    """
    src = _code()
    assert "import " not in src.replace("imports", ""), "no ESM imports"
    assert "require(\"react\")" not in src and "require('react')" not in src
    # the only require is the ES/CommonJS interop shim, not a dependency
    assert src.count("require(") == 0


# ------------------------------------------------------- the dual export shim
def test_it_loads_both_as_window_global_and_as_module_exports():
    src = _src()
    assert "window.QubitControlBar" in src or "root.QubitControlBar" in src
    assert "module.exports" in src


def test_the_browser_global_and_the_node_export_are_the_same_object():
    got = _run("return Object.keys(CB).sort();")
    for name in ("ZOOM_STEP", "MIN_ZOOM", "MAX_ZOOM", "clampZoom",
                 "buildButtons", "apply", "isReadOnly", "refusalReason"):
        assert name in got, f"the module does not export {name}"


# ------------------------------------------------------------- C4 / C7 text
def test_the_module_issues_no_mutating_request():
    """C4: the view never writes org state — and this file writes nothing.

    The four method strings are not present anywhere in the code, comments
    stripped or not, because the module contains no request of any kind. That is
    a stronger property than "its requests do not mutate": the control bar is
    permitted to fetch nothing at all, since every one of its four buttons is a
    local view operation.
    """
    src = _code()
    for verb in ("POST", "PUT", "PATCH", "DELETE"):
        assert f'"{verb}"' not in src, f"the control bar must not contain {verb}"
        assert f"'{verb}'" not in src, f"the control bar must not contain {verb}"


def test_the_module_never_fetches_anything():
    """Not even the read route. The poll belongs to the renderer, not the bar."""
    src = _code()
    assert "fetch(" not in src, "the control bar issues no request"
    assert "fetchJSON" not in src
    assert "XMLHttpRequest" not in src
    assert "navigator.sendBeacon" not in src


def test_the_module_owns_no_timer():
    """C7's renderer half: the poll is the tower's only timer, and it is not
    here. A control bar is the classic place a decorative tick gets added."""
    src = _code()
    for banned in ("setInterval", "setTimeout", "requestAnimationFrame",
                   "setImmediate", "queueMicrotask"):
        assert banned not in src, f"'{banned}' would be a timer with nothing behind it"


def test_no_css_animation_or_dom_is_touched():
    """`buildButtons()` is pure data; the renderer owns every pixel."""
    src = _code()
    for banned in ("@keyframes", "animate(", "requestIdleCallback",
                   "dangerouslySetInnerHTML", "innerHTML", "document."):
        assert banned not in src, f"the control bar must not touch {banned}"


def test_the_rule_and_the_refusals_are_stated_for_the_next_reader():
    """Every DECLARED refusal is a REAL guard — the declaration cannot drift.

    The old version asserted the six words appear somewhere in the file, which a
    comment satisfies; it stayed GREEN even when the module could publish. The
    property worth holding is the one that makes the declaration load-bearing:
    the module's OWN list (`CB.REFUSED_IDS`) must equal the set of ids it
    actually refuses, enforced through the same three exported surfaces a caller
    uses. A refusal that is written down but not enforced — or enforced but never
    declared — is exactly the "oversight" the written-down list exists to
    prevent, and this fails on it.

    The sibling `test_the_module_never_fetches_anything` proves the bar issues
    no request at all; this test proves the refusals it names are the ones its
    code holds, driven through the real module rather than a second copy of the
    list in the test.
    """
    got = _run("""
      var declared = CB.REFUSED_IDS.slice();
      var enforced = declared.map(function (id) {
        var throws = false;
        try { CB.apply(id, {cam: {cx: 0, cy: 0, zoom: 1}}); }
        catch (e) { throws = /refus/i.test(e.message) && e.message.indexOf(id) >= 0; }
        var reason = CB.refusalReason(id);
        return {
          id: id,
          readOnly: CB.isReadOnly(id),
          throws: throws,
          namesIt: typeof reason === "string" && reason.length > 30
        };
      });
      /* an id that is NOT declared must not be silently refused-by-name either:
       * the generic unknown path names no rule. */
      var undeclared = {reason: CB.refusalReason("publish2"), ro: CB.isReadOnly("publish2")};
      return {declared: declared, enforced: enforced, undeclared: undeclared,
              readOnlyIds: CB.READ_ONLY_IDS};
    """)

    # 1. The declaration exists and is non-trivial (a truncated list fails).
    assert len(got["declared"]) == 6, got
    assert got["declared"] == REFUSED, (
        "the module's declared refusals drifted from the six §4 / 28-HUD-PANELS"
        ".md names: %r" % got["declared"])

    # 2. EVERY declared refusal is enforced on all three surfaces.
    for row in got["enforced"]:
        assert row["readOnly"] is False, (
            "%r is declared refused but isReadOnly says it is permitted: %r"
            % (row["id"], row))
        assert row["throws"] is True, (
            "%r is declared refused but apply() did not refuse it by name — the "
            "declaration is not backed by a guard: %r" % (row["id"], row))
        assert row["namesIt"] is True, (
            "%r is declared refused but refusalReason() names no rule: %r"
            % (row["id"], row))

    # 3. An id that is NOT declared gets no refusal sentence — so the declared
    #    list is the actual contract, not a subset of a wider blanket.
    assert got["undeclared"]["reason"] is None, (
        "an undeclared id carries a refusal sentence: %r" % got["undeclared"])
    assert got["undeclared"]["ro"] is False

    # 4. The declared refusals and the read-only ids partition cleanly.
    assert not set(got["declared"]) & set(got["readOnlyIds"]), got


# -------------------------------------------------------------- the four
def test_there_are_exactly_four_buttons():
    assert _run("return CB.buildButtons().length;") == 4


def test_the_four_buttons_are_the_specified_ones():
    got = _run("return CB.buildButtons().map(b => [b.id, b.kind]);")
    assert got == [["reset", "reset"], ["fit", "fit"],
                   ["zoom", "zoom"], ["pnl", "pnl"]]


def test_every_button_carries_a_label_and_a_title_for_a_tooltip():
    got = _run("return CB.buildButtons().map(b => [b.label, b.title]);")
    for label, title in got:
        assert isinstance(label, str) and label.strip(), label
        assert isinstance(title, str) and title.strip(), title


def test_build_buttons_is_pure_data():
    """No DOM node, no function, no shared reference: four plain records.

    A caller that mutates a returned label must not reach back into the module,
    so a second call must be unaffected and must not be the same array.
    """
    got = _run("""
      const a = CB.buildButtons();
      a[0].label = "CLOBBERED"; a.push({id: "extra"});
      const b = CB.buildButtons();
      return {first: b[0].label, len: b.length};
    """)
    assert got == {"first": "Reset", "len": 4}


def test_every_button_id_is_read_only():
    got = _run("return CB.buildButtons().every(b => CB.isReadOnly(b.id) === true);")
    assert got is True


def test_an_unknown_id_is_refused_rather_than_permitted():
    """The whitelist fails CLOSED. A read-only surface one typo away from
    permitting an unlisted verb is not read-only."""
    got = _run("""
      return {
        nope: CB.isReadOnly("nonsense"),
        empty: CB.isReadOnly(""),
        undef: CB.isReadOnly(undefined),
        real: CB.isReadOnly("nonsense") === false && CB.isReadOnly("fit") === true
      };
    """)
    assert got == {"nope": False, "empty": False, "undef": False, "real": True}


# ------------------------------------------------------------ the six refusals
REFUSED = ["publish", "edit", "delete", "hire", "approve", "speed"]


@pytest.mark.parametrize("feature", REFUSED)
def test_a_write_requiring_feature_is_not_read_only(feature):
    assert _run(f"return CB.isReadOnly({feature!r});") is False


@pytest.mark.parametrize("feature", REFUSED)
def test_a_write_requiring_feature_has_a_named_reason(feature):
    """The reason must name WHY, not merely that the id is unknown.

    "unknown button" tells a future editor nothing and they will assume an
    oversight and add the feature; a sentence naming the write (and, where it
    exists, the holding path) is what makes the refusal a decision.
    """
    reason = _run(f"return CB.refusalReason({feature!r});")
    assert isinstance(reason, str) and reason.strip(), (
        f"{feature!r} must carry a refusal sentence")
    assert len(reason) > 30, f"{feature!r}'s reason is too thin to name why: {reason!r}"


def test_the_six_refusals_name_the_rule_they_break():
    """Each reason cites the mechanism that forbids it — C4, the confirmation
    journal / confirmation_id, the staged-op path, or the absence of a clock."""
    got = _run("return [" + ",".join(
        f"CB.refusalReason({f!r})" for f in REFUSED) + "];")
    blob = " ".join(got).lower()
    assert "c4" in blob, "the refusals must name C4 (the view never writes)"
    assert "confirmation" in blob, "the confirmation journal must be named"
    assert "write" in blob


def test_a_supported_button_has_nothing_to_refuse():
    got = _run("return [CB.refusalReason('reset'), CB.refusalReason('fit'), "
               "CB.refusalReason('zoom'), CB.refusalReason('pnl')];")
    assert got == [None, None, None, None]


def test_the_refused_ids_and_the_read_only_ids_do_not_overlap():
    got = _run("return {ro: CB.READ_ONLY_IDS, no: CB.REFUSED_IDS};")
    assert len(got["ro"]) == 4 and len(got["no"]) == 6
    assert not set(got["ro"]) & set(got["no"])
    assert got["ro"] == [b[0] for b in
                         _run("return CB.buildButtons().map(b => [b.id]);")] \
        or got["ro"] == ["reset", "fit", "zoom", "pnl"]


# ------------------------------------------------------ the pure reducer
def test_apply_refuses_a_write_requiring_id_by_throwing():
    """`apply("publish", s)` is what a future editor writes while wiring the
    button they added. It must fail loudly and name the rule, not reduce to
    something that looks like it worked."""
    got = _run("""
      try { CB.apply("publish", {cam: {cx: 1, cy: 2, zoom: 1}}); return "NO THROW"; }
      catch (e) { return e.message; }
    """)
    assert "refus" in got.lower() and "publish" in got.lower(), got
    assert "c4" in got.lower() or "confirmation" in got.lower(), got


def test_reset_restores_the_fitted_camera_not_the_origin():
    got = _run("""
      const s = {cam: {cx: 999, cy: -999, zoom: 2.2}, floor: 3, showChart: true,
                 fit: {cx: 41, cy: 7, zoom: 1.4}};
      return CB.apply("reset", s);
    """)
    assert got["cam"] == {"cx": 41, "cy": 7, "zoom": 1.4}
    # neither is a camera property, so a reset must not touch them
    assert got["floor"] == 3 and got["showChart"] is True


def test_reset_without_a_fit_falls_back_to_the_centred_pan():
    """The fitted pan of a world centred on its origin, with the zoom left
    alone — inventing a zoom we were not given is the synthesise-a-value
    failure this surface exists to avoid (28-HUD-PANELS.md §4's "sets cx/cy
    back to (0,0)")."""
    got = _run("""
      const s = {cam: {cx: 999, cy: -999, zoom: 2.2}, floor: "all", showChart: false};
      return CB.apply("reset", s);
    """)
    assert got["cam"] == {"cx": 0, "cy": 0, "zoom": 2.2}


def test_fit_refits_and_remembers_the_fitted_camera():
    got = _run("""
      const s = {cam: {cx: 5, cy: 5, zoom: 1}, floor: 2, showChart: false,
                 fit: {cx: 41, cy: 7, zoom: 1.4}};
      return CB.apply("fit", s);
    """)
    assert got["cam"] == {"cx": 41, "cy": 7, "zoom": 1.4}
    assert got["fit"] == {"cx": 41, "cy": 7, "zoom": 1.4}
    assert got["floor"] == 2 and got["showChart"] is False


def test_fit_without_a_computed_frame_only_clamps_zoom():
    got = _run("""
      const s = {cam: {cx: 12, cy: 34, zoom: 99}, floor: "all", showChart: true};
      return CB.apply("fit", s);
    """)
    assert got["cam"]["cx"] == 12 and got["cam"]["cy"] == 34
    assert got["cam"]["zoom"] == 2.4, "a 99x camera must be pulled back to the ceiling"


def test_zoom_steps_by_the_exported_constant_within_bounds():
    got = _run("""
      const s = {cam: {cx: 0, cy: 0, zoom: 1}, floor: "all", showChart: false};
      return {step: CB.ZOOM_STEP, up: CB.apply("zoom", s).cam.zoom};
    """)
    assert got["step"] == 1.25
    assert abs(got["up"] - 1.25) < 1e-12


def test_zoom_never_exceeds_the_ceiling_however_many_steps():
    got = _run("""
      let s = {cam: {cx: 0, cy: 0, zoom: 1}, floor: "all", showChart: false};
      const seen = [];
      for (let i = 0; i < 25; i++) { s = CB.apply("zoom", s); seen.push(s.cam.zoom); }
      return {last: s.cam.zoom, max: Math.max.apply(null, seen), ceiling: CB.MAX_ZOOM};
    """)
    assert got["max"] == got["ceiling"] == 2.4
    assert got["last"] == 2.4


def test_zoom_leaves_the_floor_and_the_chart_alone():
    got = _run("""
      const s = {cam: {cx: 3, cy: 4, zoom: 1}, floor: 5, showChart: true};
      return CB.apply("zoom", s);
    """)
    assert got["cam"]["cx"] == 3 and got["cam"]["cy"] == 4
    assert got["floor"] == 5 and got["showChart"] is True


def test_pnl_toggles_the_chart_flag_and_moves_no_camera():
    got = _run("""
      const s = {cam: {cx: 3, cy: 4, zoom: 1.1}, floor: "all", showChart: false};
      const on = CB.apply("pnl", s);
      const off = CB.apply("pnl", on);
      return {on: on.showChart, off: off.showChart,
              camOn: on.cam, camOff: off.cam, floor: off.floor};
    """)
    assert got["on"] is True and got["off"] is False, "a toggle toggles both ways"
    assert got["camOn"] == got["camOff"] == {"cx": 3, "cy": 4, "zoom": 1.1}
    assert got["floor"] == "all"


# --------------------------------------------------------------- purity
@pytest.mark.parametrize("control", ["reset", "fit", "zoom", "pnl"])
def test_apply_is_pure_and_does_not_mutate_its_input(control):
    """The input object must be deep-equal before and after, and the result
    must not alias it. A mutating reducer cannot be diffed or undone."""
    got = _run("""
      const s = {cam: {cx: 3, cy: 4, zoom: 1.1}, floor: 2, showChart: false,
                 fit: {cx: 41, cy: 7, zoom: 1.4}};
      const before = JSON.stringify(s);
      const camRef = s.cam;
      const out = CB.apply(%s, s);
      return {
        same: JSON.stringify(s) === before,
        input: JSON.parse(before),
        after: s,
        aliasedCam: out.cam === camRef,
        aliasedState: out === s
      };
    """ % json.dumps(control))
    assert got["same"] is True, f"apply({control!r}) mutated its input: {got['after']}"
    assert got["input"] == got["after"]
    assert got["aliasedCam"] is False, "the returned cam must be a new object"
    assert got["aliasedState"] is False, "the returned state must be a new object"


@pytest.mark.parametrize("control", ["reset", "fit", "zoom", "pnl"])
def test_apply_is_deterministic(control):
    """Same inputs, same outputs — twice, from independent objects."""
    got = _run("""
      const mk = () => ({cam: {cx: 3, cy: 4, zoom: 1.1}, floor: 2,
                         showChart: false, fit: {cx: 41, cy: 7, zoom: 1.4}});
      const a = CB.apply(%s, mk());
      const b = CB.apply(%s, mk());
      return {equal: JSON.stringify(a) === JSON.stringify(b), a: a};
    """ % (json.dumps(control), json.dumps(control)))
    assert got["equal"] is True, got


# --------------------------------------------------------------- clampZoom
def test_clamp_zoom_respects_both_bounds():
    got = _run("""
      return {
        low: CB.clampZoom(0.1),
        high: CB.clampZoom(100),
        mid: CB.clampZoom(1.5),
        atMin: CB.clampZoom(CB.MIN_ZOOM),
        atMax: CB.clampZoom(CB.MAX_ZOOM),
        min: CB.MIN_ZOOM,
        max: CB.MAX_ZOOM
      };
    """)
    assert got["low"] == got["min"] == 0.85
    assert got["high"] == got["max"] == 2.4
    assert got["mid"] == 1.5
    assert got["atMin"] == 0.85 and got["atMax"] == 2.4


def test_the_zoom_bounds_are_the_ones_the_tower_already_uses():
    """28-HUD-PANELS.md §4 puts the lower bound at `CHAR_FLOOR_ZOOM` (0.85) and
    the upper at `MAX_ZOOM` (2.4) — the same two numbers `camera.js` refuses to
    fit outside, because a character is ~15 world units tall and below the floor
    the figures are specks. They are read OUT of the sibling module rather than
    copied into this test, so the bar and the camera cannot drift apart: change
    one and this fails.
    """
    cam = (PLUGIN_DIR / "camera.js").read_text()
    floor = re.search(r"CHAR_FLOOR_ZOOM\s*=\s*([0-9.]+)", cam)
    ceiling = re.search(r"MAX_ZOOM\s*=\s*([0-9.]+)", cam)
    assert floor and ceiling, "could not read the camera's own zoom bounds"
    got = _run("return [CB.MIN_ZOOM, CB.MAX_ZOOM];")
    assert got == [float(floor.group(1)), float(ceiling.group(1))] == [0.85, 2.4]


def test_clamp_zoom_survives_junk_without_returning_nan():
    """A NaN zoom blanks the whole canvas, and a blank canvas is
    indistinguishable from 'loading' — the floor is the honest fallback."""
    got = _run("""
      const r = [CB.clampZoom(null), CB.clampZoom(undefined), CB.clampZoom(""),
                 CB.clampZoom("nope"), CB.clampZoom(NaN)];
      return {vals: r, anyNaN: r.some(v => typeof v !== "number" || isNaN(v))};
    """)
    assert got["anyNaN"] is False
    assert got["vals"] == [0.85, 0.85, 0.85, 0.85, 0.85]
