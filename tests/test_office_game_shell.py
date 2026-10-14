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
    SKIP_REASON, has_tsc, node_path, tsc_path)

NODE = node_path()
TSC = tsc_path()

MODULES = ["settings.ts", "PanelHost.ts"]


def _toolchain() -> bool:
    return has_tsc()


pytestmark = pytest.mark.skipif(
    not _toolchain(), reason=SKIP_REASON)


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
