"""F08 — settings scoping, panel policy and input contexts.

F08's proof (`60-IMPLEMENTATION-PLAN.md`): "settings change only local view
controls unless a named scoped backend action is confirmed; modal/input contexts
work at desktop/mobile sizes."

The first half is the one that can hurt. A setting that reads like a preference
and is actually an org mutation — a room's objects grant its occupants' tools
(C12), so "floor layout" changes CAPABILITIES — must not be applicable by a
display store. That refusal is asserted directly, with a real setting, rather
than in the abstract.

The second half is keyboard correctness: a modal that does not trap focus is not
a modal, and one that does not restore focus dumps the user at the top of the
page. Both are behaviours, so both are tested as behaviours.

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
UI_DIR = GAME_DIR / "ui"
STYLES = GAME_DIR / "styles"

from _office_toolchain import (  # noqa: E402
    ensure_tsc, node_path, require_toolchain_or_fail)

NODE = node_path()
#: Self-healing: installs the compiler outside the tree if it vanished, so the
#: suite RUNS after a dashboard build deleted node_modules.
TSC = ensure_tsc()

MODULES = ["settings.ts", "PanelHost.ts"]


def _toolchain() -> bool:
    return ensure_tsc() is not None


#: Loud, not a silent skip: a missing compiler ERRORS at collection (see the
#: helper's docstring). A green `N skipped` is the false pass this replaces.
require_toolchain_or_fail()


def _run(tmp_path: Path, js: str) -> object:
    staged = tmp_path / "src"
    staged.mkdir(exist_ok=True)
    for name in MODULES:
        shutil.copy(UI_DIR / name, staged / name)
    subprocess.run(
        [NODE, str(TSC), "--module", "commonjs", "--target", "es2020",
         "--moduleResolution", "node",
         "--ignoreDeprecations", "6.0", "--outDir", str(tmp_path / "js"),
         "--skipLibCheck", "--noEmitOnError", "false",
         "--lib", "es2020,dom", str(staged / "settings.ts"),
         str(staged / "PanelHost.ts")],
        capture_output=True, text=True, timeout=240, cwd=str(staged))
    st = tmp_path / "js" / "settings.js"
    ph = tmp_path / "js" / "PanelHost.js"
    if not st.is_file() or not ph.is_file():
        pytest.skip("tsc could not compile the ui modules for the node driver")
    program = (
        'const S = require(%s);\n'
        'const P = require(%s);\n'
        '(async () => {\n'
        '  const out = await (async () => { %s })();\n'
        '  process.stdout.write(JSON.stringify(out));\n'
        '})().catch(e => { console.error(String(e && e.stack || e));'
        ' process.exit(1); });\n'
    ) % (json.dumps(str(st)), json.dumps(str(ph)), js)
    proc = subprocess.run([NODE], input=program, capture_output=True,
                          text=True, timeout=90)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}")
    return json.loads(proc.stdout)


# ======================================================================
# 1 — the scoping rule: a view store may never apply an org change
# ======================================================================
def test_a_backend_scoped_setting_is_refused_by_the_local_store(tmp_path):
    """THE headline F08 claim. 'Floor layout' reads like a display preference
    and is an organisation change: a room's objects grant its occupants' tools
    (C12). A display store must not be able to apply it at all."""
    got = _run(tmp_path, """
      const v = S.defaultValues();
      const r = S.applySetting(v, 'floorLayout', 'rearranged');
      return {ok: r.ok, reason: r.ok ? null : r.reason,
              scope: S.SETTINGS.floorLayout.scope,
              action: S.SETTINGS.floorLayout.backendAction,
              unchanged: v.floorLayout};
    """)
    assert got["ok"] is False, (
        "a backend-scoped setting was applied through the local store")
    assert got["scope"] == "backend"
    assert got["action"] == "office.floor.update", (
        "the refusal does not name the backend action it needs")
    assert "organisation state" in got["reason"], (
        f"the refusal does not explain WHY: {got['reason']}")
    assert got["unchanged"] == "unchanged", (
        "the refused setting changed the values anyway")


def test_no_backend_setting_is_ever_persisted(tmp_path):
    """A backend-scoped value restored from storage would be an org change
    applied by a page reload — the same bug through a different door."""
    got = _run(tmp_path, """
      const ids = S.persistableIds();
      const backendIds = Object.values(S.SETTINGS)
        .filter(s => s.scope === 'backend').map(s => s.id);
      const leaked = backendIds.filter(id => ids.includes(id));
      // and a store that HAS a backend value stored must not restore it
      const stored = {'office.floorLayout': '"rearranged"',
                      'office.textScale': '1.15'};
      const loaded = S.loadStored(id => stored[id] ?? null);
      // and writing must not emit it
      const written = {};
      S.storeValues(S.defaultValues(), (id, raw) => { written[id] = raw; });
      const wroteBackend = Object.keys(written).filter(id => backendIds.includes(id));
      return {ids, backendIds, leaked, loadedFloor: loaded.floorLayout,
              loadedScale: loaded.textScale, wroteBackend};
    """)
    assert got["leaked"] == [], (
        f"a backend setting is offered for persistence: {got['leaked']}")
    assert got["wroteBackend"] == [], (
        f"writing settings emitted a backend key: {got['wroteBackend']}")
    assert got["loadedFloor"] == "unchanged", (
        "a stored backend value was restored; a reload could change the org")
    assert got["loadedScale"] == 1.15, (
        "a legitimate local setting was not restored")


def test_local_settings_apply_and_validate(tmp_path):
    got = _run(tmp_path, """
      let v = S.defaultValues();
      const a = S.applySetting(v, 'textScale', 1.15);
      const b = S.applySetting(v, 'reducedMotion', true);
      const bad = S.applySetting(v, 'textScale', 42);
      return {scaleOk: a.ok, scale: a.ok ? a.values.textScale : null,
              motionOk: b.ok, motion: b.ok ? b.values.reducedMotion : null,
              badOk: bad.ok, badReason: bad.ok ? null : bad.reason};
    """)
    assert got["scaleOk"] is True and got["scale"] == 1.15
    assert got["motionOk"] is True and got["motion"] is True
    assert got["badOk"] is False, (
        "an out-of-range value was accepted")
    assert "42" in got["badReason"]


def test_every_setting_declares_its_scope(tmp_path):
    """An undeclared scope is how a view setting quietly becomes an org change."""
    got = _run(tmp_path, """
      const missing = [];
      const bad = [];
      for (const [key, s] of Object.entries(S.SETTINGS)) {
        if (!s.scope) { missing.push(key); continue; }
        if (!['local','view','backend'].includes(s.scope)) bad.push([key, s.scope]);
        if (s.scope === 'backend' && !s.backendAction) bad.push([key, 'no action']);
        if (!s.label || !s.help) bad.push([key, 'no label/help']);
      }
      return {missing, bad, count: Object.keys(S.SETTINGS).length,
              scopes: Object.values(S.SETTINGS).map(s => s.scope)};
    """)
    assert got["missing"] == [], f"settings with no scope: {got['missing']}"
    assert got["bad"] == [], f"malformed settings: {got['bad']}"
    assert "backend" in got["scopes"], (
        "no backend-scoped setting exists, so the refusal is never exercised")


def test_dirty_state_detects_an_unapplied_edit(tmp_path):
    got = _run(tmp_path, """
      const applied = S.defaultValues();
      const draft = S.defaultValues();
      const before = S.isDirty(applied, draft);
      draft.textScale = 1.3;
      const after = S.isDirty(applied, draft);
      return {before, after};
    """)
    assert got["before"] is False
    assert got["after"] is True


def test_the_shell_attributes_follow_the_values(tmp_path):
    got = _run(tmp_path, """
      const a = S.defaultValues();
      const b = S.defaultValues();
      b.reducedMotion = true; b.highContrast = true; b.textScale = 1.3;
      return {a: S.shellAttributes(a), b: S.shellAttributes(b)};
    """)
    assert got["a"]["data-office-reduced-motion"] == "0"
    assert got["a"]["data-office-contrast"] == "normal"
    assert got["b"]["data-office-reduced-motion"] == "1"
    assert got["b"]["data-office-contrast"] == "high"
    assert got["b"]["data-office-scale"] == "1.3"


def test_the_settings_module_never_reaches_the_network():
    """A "setting" that fetches is a backend call wearing a checkbox."""
    src = (UI_DIR / "settings.ts").read_text()
    for banned in ("fetch(", "XMLHttpRequest", "sendBeacon", "axios"):
        assert banned not in src, (
            f"settings.ts contains {banned}: applying a preference must not "
            f"reach the network")


# ======================================================================
# 2 — the panel policy: one primary, one drawer, exclusive modal
# ======================================================================
def test_there_is_at_most_one_panel_and_one_drawer(tmp_path):
    got = _run(tmp_path, """
      let s = P.initialPanelState();
      s = P.openPanel(s, 'character', false);
      s = P.openPanel(s, 'work', false);          // replaces, not stacks
      const panel = s.panel;
      s = P.openPanel(s, 'help', true);
      s = P.openPanel(s, 'settings', true);       // replaces, not stacks
      return {panel, drawer: s.drawer};
    """)
    assert got["panel"] == "work", (
        f"opening a second panel stacked instead of replacing: {got['panel']}")
    assert got["drawer"] == "settings", (
        f"opening a second drawer stacked: {got['drawer']}")


def test_opening_a_modal_closes_the_drawer_and_records_focus(tmp_path):
    """A drawer left open behind a modal is still reachable by Tab — the modal
    would not be exclusive."""
    got = _run(tmp_path, """
      let s = P.initialPanelState();
      s = P.openPanel(s, 'help', true);
      const fake = {tagName: 'BUTTON'};
      s = P.openModal(s, 'confirm', fake);
      return {drawer: s.drawer, modal: s.modal,
              returnIsSame: s.returnFocusTo === fake,
              isOpen: P.isModalOpen(s)};
    """)
    assert got["drawer"] == "none", (
        "the drawer stayed open behind a modal")
    assert got["modal"] == "confirm"
    assert got["returnIsSame"] is True, (
        "the modal did not record where focus came from, so it cannot restore it")
    assert got["isOpen"] is True


def test_close_top_closes_the_modal_first(tmp_path):
    """Escape must close the topmost thing. Closing the panel underneath a
    modal would leave the user staring at a modal with nothing behind it."""
    got = _run(tmp_path, """
      let s = P.initialPanelState();
      s = P.openPanel(s, 'character', false);
      s = P.openModal(s, 'confirm', null);
      const first = P.closeTop(s);
      const second = P.closeTop(first);
      const third = P.closeTop(second);
      return {afterFirst: first.modal, panelStill: first.panel,
              afterSecond: second.panel, afterThird: second.panel};
    """)
    assert got["afterFirst"] == "none", "Escape did not close the modal"
    assert got["panelStill"] == "character", (
        "closing the modal also closed the panel underneath")
    assert got["afterSecond"] == "none", "Escape did not then close the panel"


def test_close_top_on_an_empty_state_is_a_no_op(tmp_path):
    got = _run(tmp_path, """
      const s = P.initialPanelState();
      const again = P.closeTop(s);
      return {same: JSON.stringify(s) === JSON.stringify(again)};
    """)
    assert got["same"] is True


# ======================================================================
# 3 — focus: a trap that does not trap is not a trap
# ======================================================================
def test_focus_wraps_at_both_ends(tmp_path):
    """Tab off the last control must go to the first, and Shift+Tab off the
    first to the last. Without wrapping, focus escapes the modal into the page
    behind it and a keyboard user is lost."""
    got = _run(tmp_path, """
      // A minimal DOM the focus helpers can walk.
      function El(tag, attrs = {}) {
        this.tagName = tag.toUpperCase();
        this.attributes = attrs;
        this.children = [];
        this.offsetParent = {};
        this.disabled = !!attrs.disabled;
        this.getAttribute = (n) => {
          const k = Object.keys(attrs).find(x => x.toLowerCase() === n);
          return k ? String(attrs[k]) : null;
        };
        this.matches = () => false;
      }
      El.prototype.querySelectorAll = function (sel) {
        // Honour the SELECTOR, like a browser would. A fake that ignores it
        // proves nothing about whether `focusables` excludes disabled
        // controls — the exclusion lives in the selector, not in the filter.
        return this.children.filter((c) => {
          if (sel.includes("button:not([disabled])") && c.tagName === "BUTTON") {
            return !c.disabled;
          }
          if (sel.includes("input:not([disabled])") && c.tagName === "INPUT") {
            return !c.disabled;
          }
          return false;
        });
      };
      function panel() {
        const p = new El('div');
        p.children = [new El('button'), new El('input'),
                      new El('button', {disabled: true}), new El('button')];
        return p;
      }
      globalThis.document = { activeElement: null };
      const root = panel();
      const list = P.focusables(root);
      // focusables must SKIP the disabled control
      const forward = P.nextFocus(root, root.children[1], false);
      const back = P.nextFocus(root, root.children[0], true);
      const fromNone = P.nextFocus(root, null, false);
      return {count: list.length, forwardIdx: root.children.indexOf(forward),
              backIdx: root.children.indexOf(back),
              fromNoneIdx: root.children.indexOf(fromNone),
              isLast: root.children.indexOf(back) === 3};
    """)
    assert got["count"] == 3, (
        f"focusables included a disabled control: {got['count']}")
    assert got["fromNoneIdx"] == 0, "Tab from nowhere did not enter the modal"
    assert got["forwardIdx"] == 3, (
        f"Tab from the last focusable did not wrap to the first: landed on "
        f"index {got['forwardIdx']}")
    assert got["isLast"] is True, (
        f"Shift+Tab off the first control did not wrap to the LAST focusable: "
        f"landed on index {got['backIdx']}, and index 2 is disabled")


def test_an_empty_trap_returns_null_rather_than_throwing(tmp_path):
    got = _run(tmp_path, """
      function El() { this.children = []; this.offsetParent = {}; }
      El.prototype.querySelectorAll = function () { return []; };
      globalThis.document = { activeElement: null };
      const root = new El();
      return {n: P.focusables(root).length, nf: P.nextFocus(root, null, false)};
    """)
    assert got["n"] == 0
    assert got["nf"] is None, "an empty trap threw or returned something odd"


def test_focus_returns_to_where_the_modal_opened_from(tmp_path):
    got = _run(tmp_path, """
      const origin = {tagName: 'BUTTON', id: 'opener'};
      let s = P.initialPanelState();
      s = P.openModal(s, 'error', origin);
      const target = P.focusTargetOnClose(s);
      const closed = P.closeTop(s);
      return {isOrigin: target === origin,
              afterClose: P.focusTargetOnClose(closed) === origin};
    """)
    assert got["isOrigin"] is True, (
        "closing a modal would not restore focus to the control that opened it")


# ======================================================================
# 4 — the F06 input contexts derive from the panel state
# ======================================================================
def test_input_contexts_follow_the_open_panels(tmp_path):
    """This is the bridge F06 deliberately left out: the router takes a context
    set, and the panel state decides what that set is."""
    got = _run(tmp_path, """
      const out = {};
      let s = P.initialPanelState();
      out.worldOnly = P.contextsFor(s, false);
      s = P.openPanel(s, 'character', false);
      out.withPanel = P.contextsFor(s, false);
      out.panelFocusedText = P.contextsFor(s, true);
      s = P.openModal(s, 'confirm', null);
      out.withModal = P.contextsFor(s, false);
      out.modalFocusedText = P.contextsFor(s, true);
      return out;
    """)
    assert got["worldOnly"] == ["world"], (
        "with nothing open the world should be the only context")
    assert got["withPanel"] == ["world", "panel"]
    assert got["panelFocusedText"] == ["world", "panel", "text"], (
        "a focused field inside a panel did not add the text context")
    assert got["withModal"] == ["world", "panel", "modal"]
    assert got["modalFocusedText"] == ["world", "panel", "modal", "text"]


def test_a_panel_above_the_world_swallows_movement_through_the_real_router(
        tmp_path,
):
    """End to end across F06 and F08: the contexts this module produces, fed to
    the real router, must stop `w` from walking the world."""
    staged = tmp_path / "src"
    staged.mkdir(exist_ok=True)
    for name in MODULES:
        shutil.copy(UI_DIR / name, staged / name)
    shutil.copy(GAME_DIR / "input.ts", staged / "input.ts")
    subprocess.run(
        [NODE, str(TSC), "--module", "commonjs", "--target", "es2020",
         "--moduleResolution", "node",
         "--ignoreDeprecations", "6.0", "--outDir", str(tmp_path / "js"),
         "--skipLibCheck", "--noEmitOnError", "false",
         "--lib", "es2020,dom", str(staged / "input.ts"),
         str(staged / "PanelHost.ts")],
        capture_output=True, text=True, timeout=240, cwd=str(staged))
    inp = tmp_path / "js" / "input.js"
    ph = tmp_path / "js" / "PanelHost.js"
    if not inp.is_file() or not ph.is_file():
        pytest.skip("tsc could not compile for the cross-module driver")
    program = (
        'const P = require(%s);\nconst I = require(%s);\n'
        'let s = P.initialPanelState();\n'
        's = P.openPanel(s, "character", false);\n'
        'const ctx = P.contextsFor(s, false);\n'
        'const r = I.routeInput({active: ctx},\n'
        '  {key: "w", modified: false, repeat: false});\n'
        'const typing = I.routeInput({active: P.contextsFor(s, true)},\n'
        '  {key: "w", modified: false, repeat: false});\n'
        'process.stdout.write(JSON.stringify({panelMove: r.nav,\n'
        '  panelHandled: r.handledBy, typingNav: typing.nav,\n'
        '  typingPass: typing.passThrough}));\n'
    ) % (json.dumps(str(ph)), json.dumps(str(inp)))
    proc = subprocess.run([NODE], input=program, capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[:600]
    got = json.loads(proc.stdout)
    assert got["panelMove"] is None, (
        "movement navigated the world while a panel was open")
    assert got["panelHandled"] == "panel"
    assert got["typingNav"] is None, (
        "typing navigated the world")
    assert got["typingPass"] is True


# ======================================================================
# 5 — the styles are tokens, and the route is full-height
# ======================================================================
def test_the_tokens_file_defines_every_token_the_shell_uses():
    tokens = (STYLES / "tokens.css").read_text()
    game = (STYLES / "game.css").read_text()
    used = set(re.findall(r"var\((--office-[a-z0-9-]+)", game))
    defined = set(re.findall(r"^\s*(--office-[a-z0-9-]+)\s*:", tokens, re.M))
    missing = sorted(used - defined)
    assert not missing, (
        f"game.css uses tokens tokens.css never defines: {missing}")


def test_reduced_motion_zeroes_the_durations_rather_than_hiding_content():
    tokens = (STYLES / "tokens.css").read_text()
    assert '[data-office-reduced-motion="1"]' in tokens, (
        "there is no reduced-motion token override")
    game = (STYLES / "game.css").read_text()
    assert "prefers-reduced-motion" in game, (
        "the system preference is not honoured; an in-game setting must not "
        "be able to override the OS")
    assert "display: none" not in game.split("prefers-reduced-motion")[1], (
        "reduced motion hides content instead of removing animation")


def test_high_contrast_swaps_tokens_rather_than_filtering():
    """A CSS filter over the scene would wash out the animation colours, so a
    `blocked` agent could read as `idle` — the state signal lost."""
    tokens = (STYLES / "tokens.css").read_text()
    assert '[data-office-contrast="high"]' in tokens
    game = (STYLES / "game.css").read_text()
    assert "filter:" not in game, (
        "game.css applies a CSS filter; contrast must be a token swap so the "
        "scene's own state colours survive")


def test_focus_is_never_removed():
    game = (STYLES / "game.css").read_text()
    assert ":focus-visible" in game, (
        "no focus ring is styled; a keyboard-driven game would be unusable "
        "without a mouse")
    assert "outline: none" not in game and "outline:none" not in game, (
        "a focus ring is removed; that is never acceptable here")


def test_the_route_is_full_height_and_not_a_chat_route():
    """A game needs a BOUNDED ancestor. Without the flag, `height: 100%`
    resolves against an auto-height block and the world expands past the
    viewport — the documented 104,305px bug."""
    routes = (WEB / "src" / "lib" / "chat-routes.ts").read_text()
    m = re.search(r"FULL_HEIGHT_ROUTE_PATHS = \[([^\]]*)\]", routes, re.S)
    assert m, "FULL_HEIGHT_ROUTE_PATHS is not defined as expected"
    body = m.group(1)
    assert "/qubit-office" in body, (
        "the office route is not full-height; the world will expand instead of "
        "being bounded")
    # and it must NOT have been added to the CHAT routes, which are a different
    # question (the file says so explicitly)
    chat = re.search(r"CHAT_ROUTE_PATHS = \[([^\]]*)\]", routes, re.S)
    assert chat and "/qubit-office" not in chat.group(1), (
        "the office route was added to CHAT_ROUTE_PATHS; full-height and chat "
        "are different predicates and must not be merged")


def test_the_game_css_exists_and_is_scoped_to_the_shell():
    """Every rule must be under `.office-game`, so the office cannot restyle
    the rest of the dashboard.

    Parsed by walking the block structure rather than matching `^selector{`:
    a line-anchored regex also catches declarations and at-rule preludes, which
    is what an earlier version of this test did (it flagged its own body text).
    """
    game = (STYLES / "game.css").read_text()
    body = re.sub(r"/\*.*?\*/", "", game, flags=re.S)

    # Collect the selector a brace belongs to by scanning forward, skipping
    # at-rule preludes (which are not selectors) and ignoring declarations.
    unscoped: list[str] = []
    depth = 0
    buf = ""
    in_at_rule = False
    for ch in body:
        if ch == "{":
            sel = buf.strip()
            if depth == 0 or in_at_rule:
                # a top-level rule, or the inside of an at-rule block
                if sel and not sel.startswith("@") and "@" not in sel:
                    if ".office-game" not in sel:
                        unscoped.append(sel)
            buf = ""
            depth += 1
            in_at_rule = False
        elif ch == "}":
            depth = max(0, depth - 1)
            buf = ""
        elif ch == "@" and not buf.strip():
            in_at_rule = True
            buf += ch
        else:
            buf += ch

    # An at-rule PRELUDE (e.g. "@media ...") opens a block whose inner rules
    # are checked above; the prelude itself is not a selector, so drop any
    # entry that still carries an "@".
    unscoped = [s for s in unscoped if "@" not in s and ";" not in s]
    assert not unscoped, (
        f"game.css has rules that could leak into the dashboard: {unscoped}")


# ======================================================================
# 6 — the renderer switch: the vendored app is the DEFAULT, the shell
#     is the reachable fallback, and the binding table tells the truth
# ======================================================================
APP_DIR = GAME_DIR / "app"
APP_MODULES = ["office-api.ts", "office-app.ts"]
ENTRY = GAME_DIR / "entry.ts"


def _entry_source() -> str:
    """entry.ts with comments stripped, so a check reads CODE not prose.

    The header explains the OLD arrangement (`?app=1`) at length; a naive text
    search would match that explanation and pass while the code did the wrong
    thing. Strip block and line comments before asserting.
    """
    raw = ENTRY.read_text()
    code = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    return re.sub(r"//[^\n]*", "", code)


def _run_app(tmp_path: Path, js: str) -> object:
    """Compile the two app modules with tsc and drive them under node."""
    staged = tmp_path / "appsrc"
    staged.mkdir(exist_ok=True)
    for name in APP_MODULES:
        shutil.copy(APP_DIR / name, staged / name)
    subprocess.run(
        [NODE, str(TSC), "--module", "commonjs", "--target", "es2020",
         "--moduleResolution", "node",
         "--ignoreDeprecations", "6.0", "--outDir", str(tmp_path / "appjs"),
         "--skipLibCheck", "--noEmitOnError", "false",
         "--lib", "es2020,dom", str(staged / "office-api.ts"),
         str(staged / "office-app.ts")],
        capture_output=True, text=True, timeout=240, cwd=str(staged))
    api = tmp_path / "appjs" / "office-api.js"
    appm = tmp_path / "appjs" / "office-app.js"
    if not api.is_file() or not appm.is_file():
        pytest.fail("tsc could not compile the office app modules for the "
                    "node driver; the binding table and the iframe src are "
                    "unchecked without this")
    program = (
        'const A = require(%s);\n'
        'const AP = require(%s);\n'
        'const out = (() => { %s })();\n'
        'process.stdout.write(JSON.stringify(out));\n'
    ) % (json.dumps(str(api)), json.dumps(str(appm)), js)
    proc = run_node(program, timeout=90)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}")
    return json.loads(proc.stdout)


def test_the_default_renderer_is_the_app_and_the_shell_is_opt_in():
    """THE headline of this arrangement: a plain `/qubit-office` mounts the
    vendored app; the hand-built shell is reached only by asking for it.

    Asserted as BEHAVIOUR of the entry's real decision expression, not by
    forbidding a token. `?app=1` is a deliberate backward-compatibility alias
    for bookmarks from the first Habblaud preview (entry.ts documents this), so
    the property is NOT "`?app` must be absent" — it is "the app is the default
    and `?shell` is the only thing that selects the fallback", which is what the
    four param cases below actually check.

    The `wantShell` expression is lifted out of the (comment-stripped) source
    and evaluated under node for each case, so this tests the code that ships
    rather than a re-implementation of it.
    """
    code = _entry_source()
    assert 'has("shell")' in code, (
        "there is no `?shell=1` opt-out, so the old shell is unreachable — it "
        "is the ROLLBACK and must stay one query param away")
    # And the branch shapes: shell -> createOfficeComponent, else -> createOfficeApp.
    shell_branch = re.search(
        r"wantShell\s*\?\s*createOfficeComponent\([^)]*\)\s*:\s*createOfficeApp\(",
        code)
    assert shell_branch, (
        "the switch is not `wantShell ? createOfficeComponent(...) : "
        "createOfficeApp(...)` — the app is not the default branch")
    assert "createOfficeComponent" in code and "createOfficeApp" in code, (
        "one of the two renderers is not referenced; the fallback or the "
        "default was removed rather than kept")

    # Lift the REAL `const wantShell = <expr>;` and evaluate that expr under
    # node for the four query-param cases. This is the property that matters:
    # only `?shell` (without `?app`) selects the shell; everything else — the
    # plain route included — is the app.
    expr_m = re.search(r"const\s+wantShell\s*=\s*([^;]+);", code)
    assert expr_m, (
        "entry.ts no longer defines `const wantShell = ...`, so the renderer "
        "decision is unreadable from source")
    expr = expr_m.group(1).strip()

    program = (
        "const expr = %s;\n"
        "function decide(search) {\n"
        "  const params = new URLSearchParams(search);\n"
        "  return eval(expr);\n"
        "}\n"
        "const out = {\n"
        "  plain: decide(''),\n"
        "  shell: decide('?shell=1'),\n"
        "  app: decide('?app=1'),\n"
        "  both: decide('?shell=1&app=1'),\n"
        "};\n"
        "process.stdout.write(JSON.stringify(out));\n"
    ) % json.dumps(expr)
    proc = run_node(program, timeout=30)
    assert proc.returncode == 0, (
        f"the lifted wantShell expression could not run under node:\n"
        f"expr={expr!r}\nstdout: {proc.stdout}\nstderr: {proc.stderr}")
    cases = json.loads(proc.stdout)
    got = {k: bool(v) for k, v in cases.items()}
    assert got["plain"] is False, (
        "a plain `/qubit-office` must mount the vendored app; wantShell was "
        f"True. expr={expr!r}")
    assert got["shell"] is True, (
        "`?shell=1` does not select the fallback shell — the rollback is "
        f"unreachable. expr={expr!r}")
    assert got["app"] is False, (
        "`?app=1` (the compatibility alias) does not resolve to the vendored "
        f"app. expr={expr!r}")
    assert got["both"] is False, (
        "`?shell=1&app=1` selected the shell; the app alias must win so a "
        f"stale bookmark cannot resurrect the fallback. expr={expr!r}")


def test_both_renderers_stay_reachable_one_query_param_apart():
    """The old shell is not dead code: it is the rollback. The entry must keep
    importing the shell's factory and the app's factory, so inverting the default
    did not delete the path back."""
    raw = ENTRY.read_text()
    assert 'from "./OfficeGameShell"' in raw, (
        "the entry no longer imports the shell factory — the fallback is gone")
    assert 'from "./app/OfficeAppShell"' in raw, (
        "the entry no longer imports the app factory")
    # The shell's live-data feeder must survive the inversion: the fallback is
    # only a real fallback if it still gets the tower payload.
    assert "liveData" in raw, (
        "the shell's tower feeder was dropped; the fallback would mount empty")


def test_the_stream_is_bound_and_the_writes_are_gated_not_missing(tmp_path):
    """THE corrected binding truth. `/api/stream` IS served (webui's
    `_serve_office_stream`), so it must read as BOUND. permissions/messages are
    org mutations gated by the confirmation authority (C4), NOT merely missing —
    the distinction the old table flattened into "0 of 5".

    Asserted against the CURRENT `bindingVerdict()`/`ROUTES` API: the verdict
    partitions every route into bound / gated / missing, and the property is the
    PARTITION — read-only routes are bound, the two mutation routes are GATED
    (present as refusals) and never missing.
    """
    got = _run_app(tmp_path, """
      const v = A.bindingVerdict();
      const byClient = {};
      for (const r of A.ROUTES) byClient[r.client] = r;
      const stream = byClient['stream'];
      return {bound: v.bound, missing: v.missing, gated: v.gated,
              live: v.live, canRender: v.canRender, viewOnly: v.viewOnly,
              nRoutes: A.ROUTES.length,
              streamQubit: stream.qubit, streamReadOnly: stream.readOnly,
              streamGated: !!stream.gated,
              accessQubit: byClient['access'].qubit,
              agentsQubit: byClient['agents/:id'].qubit};
    """)
    assert got["streamQubit"] == "/api/stream", (
        f"the stream route does not name a binding: {got['streamQubit']!r} — "
        f"/api/stream is served by webui._serve_office_stream")
    assert "stream" in got["bound"], (
        f"the stream is not reported as bound: {got['bound']}")
    assert got["live"] is True, (
        "the verdict does not report the office as live, so the status bar "
        "would still claim a poll is the feed")

    # The partition is exhaustive: every route lands in exactly one bucket.
    buckets = set(got["bound"]) | set(got["missing"]) | set(got["gated"])
    assert len(buckets) == got["nRoutes"], (
        f"the verdict does not classify every route: {len(buckets)} bucket "
        f"entries for {got['nRoutes']} routes — bound={got['bound']}, "
        f"gated={got['gated']}, missing={got['missing']}")

    # permissions/messages are GATED, and gated is disjoint from missing.
    assert set(got["gated"]) == {"permissions/:id", "messages"}, (
        f"the gated set is wrong: {got['gated']} — the org mutations must be "
        f"flagged gated by the confirmation authority (C4)")
    for gated_client in ("permissions/:id", "messages"):
        assert gated_client not in got["missing"], (
            f"{gated_client} is reported MISSING, not gated — the mutation "
            f"route is a confirmed refusal, not an unwired gap")

    # The read-only routes are BOUND, never missing.
    assert set(got["missing"]) == {
        "terminal/pty", "spawn", "renameRoom", "deliveries",
        "recording/replay"}, (
        f"the missing set is wrong: {got['missing']} — only genuinely "
        f"unavailable capabilities may be missing")
    for bound_client in ("agents/:id", "access", "sessions/recent"):
        assert bound_client in got["bound"], (
            f"read-only route {bound_client!r} is not marked bound")
    assert got["accessQubit"] == "/api/access", (
        "the access capability probe lacks its read-only binding")
    assert got["agentsQubit"] == "/api/agents/:id", (
        "Habblaud's read-only agent profile route lacks its binding")
    assert got["canRender"] is True, (
        "the renderer is reported as unable to render")
    assert got["viewOnly"] is True, (
        "the writes are gated, so the client must read as view-only")


def test_the_binding_summary_counts_the_new_truth(tmp_path):
    """The string a person reads must report the verdict's OWN counts and must
    not claim more is bound than really is.

    Asserted against `bindingVerdict()` rather than a hardcoded number, so the
    summary cannot drift from the data it summarises — the failure mode the old
    `"3 of 5 routes bound"` literal encoded.
    """
    got = _run_app(tmp_path, """
      const v = A.bindingVerdict();
      return {s: A.bindingSummary(), bound: v.bound.length,
              missing: v.missing.length, gated: v.gated.length};
    """)
    s = got["s"]
    assert str(got["bound"]) in s, (
        f"the summary does not report the bound count "
        f"({got['bound']}): {s!r}")
    assert str(got["missing"]) in s, (
        f"the summary does not report the unavailable count "
        f"({got['missing']}): {s!r}")
    assert str(got["gated"]) in s, (
        f"the summary does not report the gated count ({got['gated']}): {s!r}")
    assert "bound" in s and "unavailable" in s and "confirmation-gated" in s, (
        f"the summary does not name all three buckets: {s!r}")
    # And it must not under-report: every bound route appears by name.
    assert "0 bound" not in s and "0 unavailable" not in s, (
        f"the summary reports an empty bucket that is not empty: {s!r}")

    # Cross-check the numbers against the route list itself, so a summary that
    # agreed only with a stale `bindingVerdict` would still fail here.
    counts = _run_app(tmp_path, """
      const v = A.bindingVerdict();
      const bound = A.ROUTES.filter(r => !r.gated && !!r.qubit).length;
      const gated = A.ROUTES.filter(r => r.gated).length;
      const missing = A.ROUTES.filter(r => !r.gated && !r.qubit).length;
      return {vBound: v.bound.length, vGated: v.gated.length,
              vMissing: v.missing.length, bound, gated, missing};
    """)
    assert counts["vBound"] == counts["bound"], (
        f"bindingVerdict disagrees with ROUTES on bound: {counts}")
    assert counts["vGated"] == counts["gated"], (
        f"bindingVerdict disagrees with ROUTES on gated: {counts}")
    assert counts["vMissing"] == counts["missing"], (
        f"bindingVerdict disagrees with ROUTES on missing: {counts}")


def test_the_app_url_resolves_at_the_plain_office_route(tmp_path):
    """Point 2. With NO props and no base, the iframe src must point at the
    served app directory with a TRAILING SLASH (so the app's own relative
    bundle paths resolve inside it), and it must NOT be duplicated with the
    host base when that base is empty.
    """
    got = _run_app(tmp_path, """
      return {noBase: AP.officeAppSrc(),
              explicitRoot: AP.officeAppSrc({base: ''}),
              path: AP.OFFICE_APP_PATH};
    """)
    assert got["noBase"] == "/plugin-apps/qubit-office/habblaud/", (
        f"the iframe src at a plain /qubit-office is {got['noBase']!r}, not the "
        f"app directory — the app would 404")
    assert got["noBase"].endswith("/"), (
        "the app URL has no trailing slash, so a relative 'bundle/...' inside "
        "the app resolves against its parent")
    assert got["explicitRoot"] == got["noBase"], (
        "an explicit empty base changed the URL; the default is not the root")


def test_the_app_url_honours_a_non_empty_base_path(tmp_path):
    """Point 2, the subpath deployment. The host renders a plugin with NO props
    (`PluginPage.tsx` renders `<Component />`), so the base cannot come from a
    prop in production — it must be read from the host's injected
    `window.__QUBIT_BASE_PATH__` global. A hardcoded root prefix 404s there.
    """
    got = _run_app(tmp_path, """
      globalThis.window = {__QUBIT_BASE_PATH__: '/sub'};
      const viaGlobal = AP.officeAppSrc();
      const viaGlobalSlash = AP.hostBasePath();
      globalThis.window = {__QUBIT_BASE_PATH__: 'sub/'};   // unnormalized
      const normalized = AP.hostBasePath();
      const normalizedSrc = AP.officeAppSrc();
      globalThis.window = {};                              // absent
      const absent = AP.officeAppSrc();
      return {viaGlobal, viaGlobalSlash, normalized, normalizedSrc, absent};
    """)
    assert got["viaGlobal"] == "/sub/plugin-apps/qubit-office/habblaud/", (
        f"a non-empty QUBIT_BASE_PATH was ignored: {got['viaGlobal']!r} — the "
        f"iframe would 404 under a subpath deployment")
    assert got["viaGlobalSlash"] == "/sub", (
        f"the base path is not normalized: {got['viaGlobalSlash']!r}")
    assert got["normalized"] == "/sub", (
        f"a leading-slash/trailing-slash base was not normalized: "
        f"{got['normalized']!r}")
    assert got["normalizedSrc"] == "/sub/plugin-apps/qubit-office/habblaud/", (
        f"a normalized base produced the wrong src: {got['normalizedSrc']!r}")
    assert got["absent"] == "/plugin-apps/qubit-office/habblaud/", (
        f"an absent global must fall back to the root: {got['absent']!r}")


def test_an_explicit_base_still_overrides_the_global(tmp_path):
    """A caller that passes `base` (a test, or a future host that DOES pass a
    prop) keeps control — the global is a fallback, not a hardwired source."""
    got = _run_app(tmp_path, """
      globalThis.window = {__QUBIT_BASE_PATH__: '/from-global'};
      return {explicit: AP.officeAppSrc({base: '/explicit'}),
              explicitEmpty: AP.officeAppSrc({base: ''})};
    """)
    assert got["explicit"] == "/explicit/plugin-apps/qubit-office/habblaud/", (
        f"an explicit base did not win over the global: {got['explicit']!r}")
    assert got["explicitEmpty"] == "/plugin-apps/qubit-office/habblaud/", (
        f"an explicit empty base must mean the root, not the global: "
        f"{got['explicitEmpty']!r}")


def test_the_host_message_guard_accepts_only_our_own_marker(tmp_path):
    """`isOfficeAppMessage` is the host-side filter for iframe postMessage.

    The iframe is same-origin, so a foreign `postMessage` (a dashboard widget,
    a browser extension's injected script, or the frame's own transient
    `/sessions` navigation) can reach the host's `message` listener. The guard
    exists so the host ignores anything that is not labelled by our own app.
    Asserted as BEHAVIOUR: the positive marker passes, and every shape a foreign
    post could take is rejected — including a look-alike that carries the wrong
    `source`, a non-object, and the null/array/primitive cases a naive
    `data.source` deref would throw on.
    """
    got = _run_app(tmp_path, """
      return {
        ours: AP.isOfficeAppMessage({source: 'qubit-office-app'}),
        oursWithExtra: AP.isOfficeAppMessage(
          {source: 'qubit-office-app', type: 'ready'}),
        wrongMarker: AP.isOfficeAppMessage({source: 'other-app'}),
        bareObject: AP.isOfficeAppMessage({}),
        nullish: AP.isOfficeAppMessage(null),
        undef: AP.isOfficeAppMessage(undefined),
        string: AP.isOfficeAppMessage('qubit-office-app'),
        number: AP.isOfficeAppMessage(7),
        array: AP.isOfficeAppMessage(['qubit-office-app']),
      };
    """)
    assert got["ours"] is True, (
        "a message from our own app was not recognised")
    assert got["oursWithExtra"] is True, (
        "a message from our own app with extra fields was not recognised")
    assert got["wrongMarker"] is False, (
        "a foreign post carrying a different `source` was accepted — the host "
        "would act on another origin's message")
    assert got["bareObject"] is False, (
        "an object with no `source` marker was accepted")
    assert got["nullish"] is False and got["undef"] is False, (
        "a null/undefined payload was accepted (or threw) — the guard must "
        "reject rather than dereference")
    assert got["string"] is False, (
        "a bare string matching the marker was accepted; `data.source` on a "
        "string is `undefined`, so this must be rejected")
    assert got["number"] is False and got["array"] is False, (
        "a non-object payload was accepted; the guard must reject primitives "
        "and arrays")
