"""F06 step 4 — input contexts: typing never moves the viewpoint or closes the app.

This is the SECOND half of F06's proof, which the reducer tests did not cover:
F06 says "navigation reducer round-trips every enter/exit/floor/room path;
**editing text never moves viewpoint or closes the app**". The reducer half was
proven in `test_office_game_navigation.py`; this file proves the input half.

The property under test is structural, not a list of exceptions: if any
text-bearing context is active, the world is not allowed to interpret the key.
That is what makes "typing `w` does not walk" true without enumerating every
letter, and it is the assertion most worth falsifying.

Hermetic: tsc + node. No browser, no server, no network.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "frontend" / "qubit-web"
GAME_DIR = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office" / "game"

NODE = shutil.which("node")
TSC = WEB / "node_modules" / "typescript" / "bin" / "tsc"


def _toolchain() -> bool:
    return bool(NODE) and TSC.is_file()


pytestmark = pytest.mark.skipif(
    not _toolchain(), reason="node + the web node_modules (typescript) required")


def _run(tmp_path: Path, js: str) -> object:
    staged = tmp_path / "src"
    staged.mkdir(exist_ok=True)
    for name in ("navigation.ts", "input.ts"):
        shutil.copy(GAME_DIR / name, staged / name)
    subprocess.run(
        [NODE, str(TSC), "--module", "commonjs", "--target", "es2020",
         "--moduleResolution", "node", "--outDir", str(tmp_path / "js"),
         "--skipLibCheck", "--noEmitOnError", "false",
         "--lib", "es2020,dom", str(staged / "input.ts")],
        capture_output=True, text=True, timeout=180, cwd=str(staged))
    compiled = tmp_path / "js" / "input.js"
    if not compiled.is_file():
        pytest.skip("tsc could not compile input.ts for the node driver")
    program = (
        'const I = require(%s);\n'
        '(async () => {\n'
        '  const out = await (async () => { %s })();\n'
        '  process.stdout.write(JSON.stringify(out));\n'
        '})().catch(e => { console.error(String(e && e.stack || e));'
        ' process.exit(1); });\n'
    ) % (json.dumps(str(compiled)), js)
    proc = subprocess.run([NODE, "-e", program], capture_output=True,
                          text=True, timeout=90)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}")
    return json.loads(proc.stdout)


# ======================================================================
# 1 — THE headline property: typing cannot navigate
# ======================================================================
def test_typing_any_key_never_produces_a_nav_command(tmp_path):
    """The whole point. With a text field focused, EVERY key the world would
    otherwise claim must pass through untouched."""
    got = _run(tmp_path, """
      const state = {active: ['world', 'panel', 'text']};
      const worldKeys = ['w','a','s','d','ArrowUp','ArrowDown','ArrowLeft',
                         'ArrowRight','Enter','Escape','-','=','Tab',' '];
      const leaked = [];
      const passed = [];
      for (const key of worldKeys) {
        const r = I.routeInput(state, {key, modified: false, repeat: false});
        if (r.nav !== null) leaked.push({key, nav: r.nav});
        if (r.passThrough) passed.push(key);
      }
      return {leaked, passedCount: passed.length, total: worldKeys.length};
    """)
    assert got["leaked"] == [], (
        f"typing produced navigation commands: {got['leaked']} — this is the "
        f"exact failure F06's proof forbids")
    assert got["passedCount"] == got["total"], (
        "not every key reached the field; a swallowed key is a broken field")


def test_escape_in_a_text_field_does_not_leave_the_building(tmp_path):
    """Called out separately because Escape is the one key a modal DOES own,
    and it is the classic way a form closes the app by accident."""
    got = _run(tmp_path, """
      const text = I.routeInput({active:['world','text']},
                                 {key:'Escape', modified:false, repeat:false});
      const editor = I.routeInput({active:['world','editor']},
                                  {key:'Escape', modified:false, repeat:false});
      return {textNav: text.nav, textPass: text.passThrough,
              editorNav: editor.nav, editorPass: editor.passThrough};
    """)
    assert got["textNav"] is None, "Escape in a text field navigated back"
    assert got["editorNav"] is None, "Escape in the editor navigated back"
    assert got["textPass"] is True and got["editorPass"] is True


def test_a_character_typed_into_a_field_never_reaches_the_world(tmp_path):
    """A letter that is ALSO a movement key (`w`, `a`, `s`, `d`) is the concrete
    case: type your name and the viewpoint walks."""
    got = _run(tmp_path, """
      const out = [];
      for (const key of ['w','a','s','d']) {
        const r = I.routeInput({active:['world','text']},
                               {key, modified:false, repeat:false});
        out.push({key, nav: r.nav, handledBy: r.handledBy});
      }
      return out;
    """)
    for row in got:
        assert row["nav"] is None, (
            f"typing {row['key']!r} moved the viewpoint")


def test_a_held_movement_key_does_not_repeat_navigation(tmp_path):
    got = _run(tmp_path, """
      const world = I.routeInput({active:['world']},
        {key:'w', modified:false, repeat:true});
      const text = I.routeInput({active:['world','text']},
        {key:'w', modified:false, repeat:true});
      return {worldNav: world.nav, textNav: text.nav,
              textPass: text.passThrough};
    """)
    assert got["worldNav"] is None, "a held key repeated navigation"
    assert got["textNav"] is None
    assert got["textPass"] is True, (
        "a held key stopped reaching the field — holding backspace in a name "
        "field must still delete characters")


def test_a_keyboard_chord_is_never_stolen(tmp_path):
    """Ctrl/Cmd+A is select-all, not 'move left'."""
    got = _run(tmp_path, """
      const mods = ['a','w','Enter','Escape','Tab'];
      const stolen = [];
      for (const key of mods) {
        const r = I.routeInput({active:['world']},
                               {key, modified:true, repeat:false});
        if (r.nav !== null) stolen.push({key, nav: r.nav});
      }
      return {stolen};
    """)
    assert got["stolen"] == [], (
        f"a modified key produced navigation: {got['stolen']}")


# ======================================================================
# 2 — the world DOES navigate when nothing else wants the key
# ======================================================================
def test_the_world_navigates_when_it_is_the_innermost_context(tmp_path):
    got = _run(tmp_path, """
      const w = k => I.routeInput({active:['world']},
                                  {key:k, modified:false, repeat:false});
      return {up: w('w'), down: w('s'), left: w('a'), right: w('d'),
              enter: w('Enter'), esc: w('Escape'), tab: w('Tab'),
              zoomIn: w('='), zoomOut: w('-')};
    """)
    assert got["up"]["nav"] == {"kind": "move", "dx": 0, "dy": -1}
    assert got["down"]["nav"] == {"kind": "move", "dx": 0, "dy": 1}
    assert got["left"]["nav"] == {"kind": "move", "dx": -1, "dy": 0}
    assert got["right"]["nav"] == {"kind": "move", "dx": 1, "dy": 0}
    assert got["enter"]["nav"] == {"kind": "travel", "direction": "enter"}
    assert got["esc"]["nav"] == {"kind": "back"}
    assert got["tab"]["nav"] == {"kind": "select", "delta": 1}
    assert got["zoomIn"]["nav"]["kind"] == "zoom"
    assert got["zoomOut"]["nav"]["factor"] < 1
    for k in ("up", "down", "left", "right", "enter", "esc", "tab"):
        assert got[k]["handledBy"] == "world"
        assert got[k]["passThrough"] is False


def test_an_unclaimed_key_is_nobodys_business(tmp_path):
    got = _run(tmp_path, """
      const r = I.routeInput({active:['world']},
                             {key:'q', modified:false, repeat:false});
      return {nav: r.nav, handledBy: r.handledBy, passThrough: r.passThrough};
    """)
    assert got["nav"] is None
    assert got["handledBy"] is None


# ======================================================================
# 3 — panels and modals swallow movement
# ======================================================================
def test_a_panel_swallows_movement_so_it_does_not_walk_the_world(tmp_path):
    got = _run(tmp_path, """
      const p = I.routeInput({active:['world','panel']},
                             {key:'w', modified:false, repeat:false});
      const esc = I.routeInput({active:['world','panel']},
                               {key:'Escape', modified:false, repeat:false});
      return {panelNav: p.nav, panelHandled: p.handledBy, escNav: esc.nav,
              escHandled: esc.handledBy};
    """)
    assert got["panelNav"] is None, (
        "movement navigated the world while a panel was open")
    assert got["panelHandled"] == "panel"
    assert got["escNav"] == {"kind": "back"}, "Escape did not close the panel"
    assert got["escHandled"] == "panel"


def test_a_modal_owns_escape_and_swallows_everything_else(tmp_path):
    got = _run(tmp_path, """
      const m = k => I.routeInput({active:['world','panel','modal']},
                                  {key:k, modified:false, repeat:false});
      return {w: m('w'), enter: m('Enter'), esc: m('Escape')};
    """)
    assert got["w"]["nav"] is None, "movement leaked through a modal"
    assert got["w"]["handledBy"] == "modal"
    assert got["enter"]["nav"] is None, (
        "Enter navigated the world while a modal was open")
    assert got["esc"]["nav"] == {"kind": "back"}
    assert got["esc"]["handledBy"] == "modal", (
        "the modal did not claim its own Escape")


def test_a_modal_containing_a_field_still_lets_you_type(tmp_path):
    got = _run(tmp_path, """
      const t = k => I.routeInput({active:['world','panel','modal','text']},
                                  {key:k, modified:false, repeat:false});
      return {w: t('w'), esc: t('Escape')};
    """)
    assert got["w"]["nav"] is None
    assert got["w"]["passThrough"] is True, (
        "a field inside a modal could not receive input")
    assert got["esc"]["nav"] is None, (
        "Escape inside a modal's text field closed the modal instead of "
        "reaching the field")


def test_with_no_active_context_nothing_is_claimed(tmp_path):
    got = _run(tmp_path, """
      const r = I.routeInput({active:[]},
                             {key:'w', modified:false, repeat:false});
      return {nav: r.nav, handledBy: r.handledBy};
    """)
    assert got["nav"] is None
    assert got["handledBy"] is None


# ======================================================================
# 4 — context containment and lifecycle
# ======================================================================
def test_containment_order_is_the_declared_chain(tmp_path):
    got = _run(tmp_path, """
      return {order: I.CONTEXT_ORDER,
              worldInPanel: I.isWithin('world','panel'),
              textInModal: I.isWithin('text','modal'),
              editorInText: I.isWithin('editor','text'),
              panelInText: I.isWithin('panel','text'),
              modalInModal: I.isWithin('modal','modal')};
    """)
    assert got["order"] == ["world", "panel", "modal", "text", "editor"]
    assert got["worldInPanel"] is False, (
        "world is not inside panel — the containment order is inverted")
    assert got["textInModal"] is True
    assert got["editorInText"] is True, "editor is not nested under text"
    assert got["panelInText"] is False
    assert got["modalInModal"] is True


def test_push_and_pop_are_idempotent_and_remove_exactly_one(tmp_path):
    got = _run(tmp_path, """
      let s = {active: ['world']};
      s = I.pushContext(s, 'modal');
      s = I.pushContext(s, 'modal');      // idempotent
      const afterPush = [...s.active];
      s = I.popContext(s, 'modal');
      const afterPop = [...s.active];
      return {afterPush, afterPop};
    """)
    assert got["afterPush"] == ["world", "modal"], (
        "pushContext stacked a duplicate — a re-render would do this too")
    assert got["afterPop"] == ["world"]


def test_detect_context_reads_real_elements(tmp_path):
    got = _run(tmp_path, """
      const el = (tag, extra={}) => Object.assign({
        tagName: tag, getAttribute: () => null}, extra);
      return {
        textarea: I.detectContext(el('TEXTAREA')),
        input: I.detectContext(el('INPUT')),
        button: I.detectContext(el('BUTTON')),
        div: I.detectContext(el('DIV')),
        contenteditable: I.detectContext(el('DIV', {isContentEditable: true})),
        nothing: I.detectContext(null),
      };
    """)
    assert got["textarea"] == "text"
    assert got["input"] == "text"
    assert got["button"] == "panel"
    assert got["div"] == "world"
    assert got["contenteditable"] == "text"
    assert got["nothing"] == "world", (
        "a missing target must not be treated as the panel context")


# ======================================================================
# 5 — the module stays presentation-only and offline
# ======================================================================
def test_the_input_router_does_not_navigate_by_itself(tmp_path):
    """It returns a COMMAND. Applying it is the reducer's job, so this file can
    be tested without a world and cannot move agents.

    Comments are stripped first: the module's header describes the very bug it
    prevents, and a naive check fails on that documentation.
    """
    raw = (GAME_DIR / "input.ts").read_text()
    src = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    for banned in ("agent_id", "character", "office_event", "fetch(",
                   "XMLHttpRequest", "setInterval", "setTimeout"):
        assert banned not in src, (
            f"input.ts references {banned}: the router returns commands and "
            f"owns no side effects")


def test_the_router_is_pure(tmp_path):
    got = _run(tmp_path, """
      const s = {active: ['world']};
      const before = JSON.stringify(s);
      I.routeInput(s, {key: 'w', modified: false, repeat: false});
      I.pushContext(s, 'modal');
      return {unchanged: before === JSON.stringify(s)};
    """)
    assert got["unchanged"] is True, (
        "routeInput or pushContext mutated the state it was given")
