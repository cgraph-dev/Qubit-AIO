"""F02 — the office renderer's interaction lifecycle, camera state and selection.

`docs/game-office/frontend-game/60-IMPLEMENTATION-PLAN.md` F02 names three defects
that made the retained baseline unusable, all confirmed live in
`frontend-game/evidence/probe.json` (`canvas_event_listeners: []`,
`wheel_changed_pixels: false`, `drag_changed_pixels: false`):

  1. **Listeners never bind.** The effect read `canvasRef.current` at mount while
     the loading branch renders no canvas, returned at the guard, and never ran
     again.
  2. **Fit/Reset are no-ops.** The canonical framing was overwritten with the
     *current* camera on every redraw, and `fit`/`reset` set `_auto` while the
     redraw read `_autoZoom`.
  3. **The inspector goes stale.** `setPick` stored the whole payload object, so
     the panel kept rendering the revision it was clicked in.

This file asserts the SOURCE-level invariants, because the defects are
structural (a dependency array, a flag name, a stored object) and the repo's
test path has `node` but no DOM: a behavioural test of the React effect needs a
renderer this suite does not have. Where behaviour *can* be exercised —
`camera.js`'s pure fit/clamp reducer — it is, through `node -e`, exactly as
`test_office_control_bar.py` does it.

Hermetic: reads source files and shells out to `node`. No browser, no server, no
network, no provider call.
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
RENDERER = PLUGIN_DIR / "qubit-office.js"
CAMERA = PLUGIN_DIR / "camera.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node is required for the camera reducer assertions")


def _src(path: Path) -> str:
    return path.read_text()


def _code(path: Path) -> str:
    """Comments stripped — an assertion about behaviour must not be satisfied or
    tripped by the prose that documents the behaviour."""
    src = _src(path)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    return src


def _run(js: str, module: Path = CAMERA, name: str = "CAM") -> object:
    program = (
        'const %s = require(%s);\n'
        'const out = (() => { %s })();\n'
        'process.stdout.write(JSON.stringify(out));\n'
    ) % (name, json.dumps(str(module)), js)
    proc = subprocess.run([NODE], input=program, capture_output=True, text=True,
                          timeout=30)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}")
    return json.loads(proc.stdout)


# ======================================================================
# 1 — the listener effect is bound to the branch, not to mount
# ======================================================================
def test_the_input_effect_is_not_a_mount_only_effect():
    """The defect: `hooks.useEffect(..., [])` while the canvas exists only in
    the ready branch. A mount-only effect can never see the canvas, so it must
    not be the binding site."""
    code = _code(RENDERER)
    # the input effect is the one that binds pointerdown; find its closing deps
    idx = code.find('addEventListener("pointerdown"')
    assert idx > 0, "the input effect no longer binds pointerdown"
    tail = code[idx:idx + 3000]
    close = tail.find("}, [")
    assert close > 0, "the input effect has no dependency array"
    deps = tail[close + 4:tail.find("]", close)]
    assert deps.strip() != "", (
        "the input effect is a mount-only effect again: its listener bind "
        "reads canvasRef.current, which is null until the ready branch renders")


def test_the_input_effect_depends_on_the_canvas_branch_selector():
    """It must re-run when the canvas appears (loading -> ready) and after a
    remount, which means depending on the branch condition itself."""
    code = _code(RENDERER)
    assert "canvasPresent" in code, (
        "the branch selector the input effect depends on is gone")
    assert re.search(r"\},\s*\[canvasPresent\]", code), (
        "the input effect no longer depends on [canvasPresent], so it cannot "
        "re-bind when the canvas mounts after the loading state")


def test_the_branch_selector_matches_every_branch_that_renders_no_canvas():
    """`canvasPresent` is only correct if it is false on exactly the branches
    that return without a canvas."""
    code = _code(RENDERER)
    m = re.search(r"var canvasPresent = ([^;]+);", code)
    assert m, "canvasPresent is not defined"
    expr = m.group(1)
    for guard in ("view.loading", "view.error"):
        assert guard in expr, (
            f"canvasPresent ignores {guard}, so the effect would bind on a "
            f"branch that renders no canvas")
    assert "v.empty" in expr, "canvasPresent ignores the empty branch"


def test_the_listener_teardown_removes_every_listener_it_adds():
    """A re-binding effect that leaks is worse than a mount-only one: every
    transition back to ready would stack another handler."""
    code = _code(RENDERER)
    idx = code.find('addEventListener("pointerdown"')
    tail = code[idx:idx + 3000]
    added = set(re.findall(r'addEventListener\("(\w+)"', tail))
    removed = set(re.findall(r'removeEventListener\("(\w+)"', tail))
    assert added, "no listeners are added"
    assert added == removed, (
        f"listeners added but never removed: {sorted(added - removed)} — a "
        f"re-binding effect would stack them on every transition")


def test_pointer_cancel_ends_the_drag_not_only_pointerup():
    """A lost pointer or an OS gesture must not leave the camera glued to a
    pointer that no longer exists."""
    code = _code(RENDERER)
    assert '"pointercancel"' in code, (
        "pointercancel is not handled: a cancelled gesture leaves the drag open")


def test_a_drag_suppresses_the_click_it_would_otherwise_fire():
    """Releasing a pan fires `click` on the canvas; without the guard the
    selection jumps to the tile the drag ended on."""
    code = _code(RENDERER)
    assert "suppressClick" in code, (
        "nothing suppresses the click that follows a drag")


# ======================================================================
# 2 — canonical framing vs. the current camera
# ======================================================================
def test_the_canonical_framing_is_saved_from_the_fit_not_the_camera():
    """The defect: `fitRef.current = {cam...}` ran on EVERY redraw, so Reset
    restored whatever the user had just panned to.

    Every write must either sit in a branch that just re-framed, or take the
    control bar's own `next.fit`. A bare assignment reading the live camera is
    the regression.
    """
    code = _code(RENDERER)
    live = re.findall(r"fitRef\.current\s*=\s*\{cx:\s*cam\.cx", code)
    assert live, "fitRef is never saved from the canonical framing"
    # Every live-camera write must sit inside a branch that JUST re-framed.
    # (The control bar's own `fitRef.current = next.fit` is a different shape
    # and needs no such guard.)
    for m in re.finditer(r"fitRef\.current\s*=\s*\{cx:\s*cam\.cx", code):
        window = code[max(0, m.start() - 900):m.start()]
        assert "fitToBuilding" in window, (
            "fitRef.current is assigned from `cam` outside a fitToBuilding "
            "branch — Reset would restore the user's last pan")


def test_fit_and_reset_set_the_flag_the_redraw_actually_reads():
    """The defect: `onBar` wrote `_auto`; `redraw()` read `_autoZoom`."""
    code = _code(RENDERER)
    bar = code[code.find("function onBar"):]
    bar = bar[:bar.find("\n    }")] if "\n    }" in bar else bar[:2000]
    assert "_autoZoom = true" in bar, (
        "fit/reset do not set _autoZoom, the flag redraw() reads — Fit cannot "
        "re-frame after Zoom")
    assert not re.search(r"_auto\s*=\s*true", bar), (
        "fit/reset still write `_auto`, a name nothing reads")


def test_the_redraw_reads_the_same_flag_the_bar_writes():
    """Both halves of the flag must name the same property."""
    code = _code(RENDERER)
    redraw = code[code.find("function redraw"):]
    redraw = redraw[:3000]
    assert "_autoZoom" in redraw, (
        "redraw no longer consults _autoZoom, so the bar's flag is dead again")


# ======================================================================
# 3 — selection by identity, not by payload object
# ======================================================================
def test_selection_stores_an_agent_id_not_the_character_object():
    code = _code(RENDERER)
    idx = code.find("setPick(")
    assert idx > 0, "setPick is never called"
    call = code[idx:code.find(")", idx) + 1]
    assert "agent_id" in call, (
        "setPick stores the payload object, which pins the inspector to the "
        "revision it was clicked in")


def test_the_inspector_derives_from_the_current_snapshot():
    """The panel must resolve the stored id against the snapshot being
    rendered, so a poll updates it."""
    code = _code(RENDERER)
    assert "var picked" in code, "the inspector is not derived from the snapshot"
    window = code[code.find("var picked"):]
    window = window[:600]
    assert "v.characters" in window and "agent_id" in window, (
        "picked is not resolved against the current snapshot's characters")


def test_the_panel_renders_the_derived_value_not_the_stored_one():
    code = _code(RENDERER)
    panel = code[code.find("styles.side"):]
    panel = panel[:2500]
    assert "picked." in panel, "the panel does not render the derived character"
    assert not re.search(r"\bpick\.(slug|animation|walking|stalled)\b", panel), (
        "the panel still reads fields off the stored selection object")


# ======================================================================
# 4 — the camera reducer itself (behavioural, through node)
# ======================================================================
def test_camera_module_is_present_and_reachable():
    assert CAMERA.is_file(), f"missing {CAMERA}"
    got = _run("return typeof CAM.fit;")
    assert got == "function", "camera.js no longer exports fit()"


def test_a_fit_is_reproducible_from_the_same_inputs():
    """The canonical framing must be a function of the building, not of when it
    was computed — that is what makes Reset meaningful."""
    js = """
    const vp = {w: 880, h: 500};
    const ex = {cx: 100, cy: 60, w: 39, h: 16};
    const a = CAM.fit(vp, ex);
    const b = CAM.fit(vp, ex);
    return {a, b, same: JSON.stringify(a) === JSON.stringify(b)};
    """
    got = _run(js)
    assert got["same"] is True, "fit() is not deterministic across calls"
    assert got["a"]["zoom"] > 0, "fit() produced a non-positive zoom"


# ======================================================================
# 5 — C7 / T3 — a camera gesture is presentation, not a request
# ======================================================================
def test_a_camera_gesture_issues_no_request():
    """The amendment's T3. Pan and wheel are local view operations; if either
    fetched, a camera gesture would become a network action and the C4/C7
    boundary would be gone."""
    code = _code(RENDERER)
    idx = code.find('addEventListener("pointerdown"')
    end = code.find("}, [canvasPresent])", idx)
    assert idx > 0 and end > idx, "the input effect could not be isolated"
    effect = code[idx:end]
    for banned in ("fetchJSON", "fetch(", "XMLHttpRequest", "sendBeacon"):
        assert banned not in effect, (
            f"the input effect calls {banned}: a camera gesture must not "
            f"issue a request")


def test_a_camera_gesture_starts_no_agent_run():
    """The other half of T3: no gesture may reach a dispatch path."""
    code = _code(RENDERER)
    idx = code.find('addEventListener("pointerdown"')
    end = code.find("}, [canvasPresent])", idx)
    effect = code[idx:end]
    for banned in ("/api/ops", "/api/office/run", "dispatch"):
        assert banned not in effect, (
            f"the input effect touches {banned}: a camera gesture must not "
            f"start work")


def test_the_input_effect_owns_no_timer():
    """C7's renderer half, kept for the engine: a pan is drawn on the event
    that caused it, so nothing here needs to tick."""
    code = _code(RENDERER)
    idx = code.find('addEventListener("pointerdown"')
    end = code.find("}, [canvasPresent])", idx)
    effect = code[idx:end]
    for banned in ("setInterval", "setTimeout", "requestAnimationFrame"):
        assert banned not in effect, (
            f"the input effect uses {banned}: a gesture is drawn on its own "
            f"event, not on a clock")


def test_clamp_keeps_a_panned_camera_inside_the_building_extent():
    """A pan must not be able to lose the building entirely."""
    js = """
    const vp = {w: 880, h: 500};
    const box = {minX: 0, minY: 0, maxX: 39, maxY: 16, w: 39, h: 16};
    const cam = {cx: 99999, cy: 99999, zoom: 1, w: 880, h: 500};
    CAM.clamp(vp, box, cam, 0.30);
    return {cx: cam.cx, cy: cam.cy};
    """
    got = _run(js)
    assert got["cx"] < 99999 and got["cy"] < 99999, (
        "clamp() left the camera outside the building extent")
