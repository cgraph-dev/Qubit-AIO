"""M4 — the plugin's side of C4 and C7, enforced rather than intended.

C4's proof in the contract is a `grep`:

```sh
grep -rn 'fetchJSON' frontend/qubit-app/plugins/qubit-office/*.js \\
  | grep -E 'POST|PUT|DELETE'    # each must target /api/ops/* (confirm path)
```

A grep in a document is a habit. Here it is a test, so the day someone adds a
quick `POST` to the tower it fails instead of shipping. The same goes for the
renderer's half of C7: the tower may poll, and it may not own any other timer or
any CSS animation that would let a character look busy with no event behind it.

These are text assertions on purpose. There is no JS toolchain in this repo's
test path, and the properties that matter here (`no mutating verb`, `no
keyframes`, `exactly one timer`) are exactly the properties a text check
catches — a behavioural test would need a browser and would still miss the
spinner that a future commit adds.

Hermetic: reads files; no network, no browser, no provider call.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PLUGIN_DIR = (Path(__file__).resolve().parent.parent
              / "frontend" / "qubit-app" / "plugins" / "qubit-office")
MANIFEST = PLUGIN_DIR / "manifest.json"
#: What the manifest declares and the browser loads: the generated bundle.
#: Read FROM the manifest rather than hardcoded — a hardcoded filename here is
#: what made a correct manifest change look like a regression, and the same
#: staleness is exactly what cost three render cycles in the dashboard (the
#: entry is declared in the manifest AND in two fleet.json copies, and only one
#: of them is served).
MANIFEST_ENTRY = json.loads(MANIFEST.read_text()).get("entry", "")

#: The renderer SOURCE. Several tests below assert on the renderer's own code
#: (its contract: one timer, no write verbs, the provenance header). Those
#: questions are about the source file, not about what is served, so they keep
#: reading it directly. They must NOT be pointed at the bundle: the bundle is
#: generated, and asserting on generated output would hide a source regression
#: until someone happened to rebuild.
ENTRY = PLUGIN_DIR / "qubit-office.js"

#: The file the manifest actually names, resolved next to the manifest.
SERVED_ENTRY = PLUGIN_DIR / MANIFEST_ENTRY if MANIFEST_ENTRY else ENTRY


def _src() -> str:
    """The whole file, comments included. Use for 'does it say what it is'."""
    return ENTRY.read_text()


def _code() -> str:
    """The file with JS comments stripped.

    Assertions about what the code *does* must not be satisfied or tripped by a
    comment explaining what the code deliberately does not do — this file's
    header says "no spinner" and "no innerHTML", and a naive check would fail on
    its own documentation.
    """
    src = _src()
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    return src


# --------------------------------------------------------------- existence
def test_the_plugin_dir_has_a_manifest_and_an_entry():
    assert MANIFEST.is_file() and ENTRY.is_file()


def test_the_manifest_matches_the_overlay_shape():
    """The same shape as qubit-boards / qubit-ops / qubit-memory."""
    m = json.loads(MANIFEST.read_text())
    assert m["name"] == "qubit-office"
    assert m["source"] == "qubit-overlay"
    # The manifest must name a file that EXISTS beside it. Asserting an exact
    # filename made this test a trap: it failed when the entry was correctly
    # changed to the bundle. What actually matters is that the declaration
    # resolves and the declared artifact is the built one.
    assert m["entry"], "the manifest must declare an entry"
    assert (PLUGIN_DIR / m["entry"]).is_file(), (
        f"the manifest declares {m['entry']!r} but no such file exists in "
        f"{PLUGIN_DIR.name}/ -- the loader would 404 and the tab would render "
        f"nothing")
    assert (PLUGIN_DIR / m["entry"]).stat().st_size > 1000, (
        "the declared entry is a stub")
    assert m["tab"]["path"].startswith("/")
    # `end` or an anchor that can resolve — see
    # test_the_position_anchor_can_actually_resolve for why a bare
    # `after:<sibling-plugin>` is not a valid hint for this tab.
    assert m["tab"]["position"] == "end" or m["tab"]["position"].startswith(
        ("after:", "before:"))
    assert m["has_api"] is True, "the tab reads a real backend"


def test_the_manifest_does_not_reference_a_file_that_does_not_exist():
    """A `css` entry pointing at a missing file is a broken tab, not a style."""
    m = json.loads(MANIFEST.read_text())
    css = m.get("css")
    if css:
        assert (PLUGIN_DIR / css).is_file(), f"{css} is referenced but missing"
    # documented deviation from 55-DASHBOARD-PAGE.md's illustrative example,
    # which shows a css file: the tower styles in JS and needs no build step.
    assert css in (None, ""), (
        "if a css file is added, keep this assertion honest by pointing it at "
        "a file that exists — and update 55-DASHBOARD-PAGE.md")


# ------------------------------------------------- the tab actually appears
# These three assertions exist because the tab shipped INVISIBLE: the manifest
# was correct, the bundle was served, and the sidebar still had no Office entry.
# A plugin tab is reachable only if all three of these hold, and none of them is
# checkable from the plugin directory alone.


def test_the_icon_is_a_real_icon_in_the_client_map():
    """`resolveIcon` falls back to Puzzle, so a wrong icon fails silently."""
    app = (PLUGIN_DIR.parents[3] / "frontend" / "qubit-web" / "src"
           / "App.tsx")
    assert app.is_file(), f"cannot find the client App.tsx at {app}"
    src = app.read_text()
    block = src[src.index("const ICON_MAP"):]
    block = block[:block.index("};")]
    icons = set(re.findall(r"^\s*([A-Za-z0-9_]+),", block, re.M))
    assert icons, "the ICON_MAP could not be parsed"
    icon = json.loads(MANIFEST.read_text())["icon"]
    assert icon in icons, (
        f"icon {icon!r} is not in ICON_MAP ({sorted(icons)}) — the tab would "
        f"render the generic Puzzle icon")


def test_the_position_anchor_can_actually_resolve():
    """A `after:<x>` hint only works if `/x` is already in the nav list.

    `buildNavItems` (App.tsx) inserts a plugin tab after an existing nav item,
    and appends it when the anchor is not found. The static fleet is merged
    BEFORE the API list, so a fleet-delivered tab cannot anchor to another
    overlay plugin — that tab has not been added yet, the lookup misses, and the
    tab silently lands at the end.

    So the position must be `end` (explicit) or anchor to a BUILT-IN route,
    which is always present by the time plugins are processed.
    """
    app = (PLUGIN_DIR.parents[3] / "frontend" / "qubit-web" / "src"
           / "App.tsx")
    src = app.read_text()
    block = src[src.index("const BUILTIN_NAV_REST"):]
    block = block[:block.index("];")]
    builtin = set(re.findall(r'path:\s*"([^"]+)"', block))
    assert len(builtin) > 5, f"could not parse BUILTIN_NAV_REST: {builtin}"

    pos = json.loads(MANIFEST.read_text())["tab"]["position"]
    if pos == "end":
        return
    assert pos.startswith(("after:", "before:")), pos
    target = "/" + pos.split(":", 1)[1]
    assert target in builtin, (
        f"position {pos!r} anchors to {target}, which is not a built-in route "
        f"({sorted(builtin)}) — a fleet-delivered tab would be appended last "
        f"instead, silently")


def test_the_tab_is_registered_in_the_served_fleet_manifest():
    """The sidebar is built from the SPA's static fleet plus the API list.

    The static fleet is what the served SPA can always read from disk, so an
    overlay tab that is only in the API list is one API/cache problem away from
    vanishing — which is exactly how this tab shipped invisible. Both copies of
    the file must list it, and they must agree.
    """
    repo = PLUGIN_DIR.parents[3]
    copies = [repo / "frontend" / "qubit-dist" / "plugins" / "fleet.json",
              repo / "frontend" / "qubit-web" / "public" / "plugins"
              / "fleet.json"]
    seen = []
    for path in copies:
        assert path.is_file(), f"missing {path}"
        rows = json.loads(path.read_text())
        row = [r for r in rows if r.get("name") == "qubit-office"]
        assert row, f"qubit-office is absent from {path.name} at {path.parent}"
        seen.append(row[0])
    assert seen[0] == seen[1], (
        "the two fleet.json copies disagree — one would serve a different tab")
    manifest = json.loads(MANIFEST.read_text())
    assert seen[0]["tab"]["path"] == manifest["tab"]["path"]
    assert seen[0]["entry"] == manifest["entry"]
    # no assetBase means it loads from /dashboard-plugins/<name>, which is where
    # the backend overlay root serves an overlay plugin's bundle
    assert "assetBase" not in seen[0] or seen[0]["assetBase"] == (
        f"/dashboard-plugins/{manifest['name']}")


def test_the_tab_path_is_a_shell_route_on_the_server():
    """The server must render the SPA shell for the tab's path.

    `SPA_ROUTE_PATHS` is described in webui.py as the single source of truth for
    which public paths render the shell; a plugin path missing from it is a tab
    that 404s the moment somebody clicks it.
    """
    repo = PLUGIN_DIR.parents[3]
    src = (repo / "voicebridge" / "webui.py").read_text()
    m = re.search(r"SPA_ROUTE_PATHS\s*=\s*\((.*?)\n\)", src, re.S)
    assert m, "SPA_ROUTE_PATHS could not be parsed from webui.py"
    paths = set(re.findall(r'"([^"]+)"', m.group(1)))
    assert len(paths) > 5, f"parsed too few routes: {sorted(paths)}"
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["tab"]["path"] in paths, (
        f"{manifest['tab']['path']} is not in SPA_ROUTE_PATHS {sorted(paths)}")


def test_the_entry_registers_through_the_plugin_sdk():
    src = _src()
    assert "window.__QUBIT_PLUGIN_SDK__" in src
    assert 'window.__QUBIT_PLUGINS__.register("qubit-office"' in src


# ------------------------------------------------------------------- C4
def test_the_tower_issues_no_mutating_request():
    """C4: the view never writes org state."""
    src = _code()
    for verb in ("POST", "PUT", "PATCH", "DELETE"):
        assert f'"{verb}"' not in src, f"the tower must not issue {verb}"
        assert f"method: \"{verb}\"" not in src
    # and the only path it touches is the read route
    paths = set(re.findall(r'"(/api/[a-z0-9/_-]+)"', src))
    assert paths == {"/api/office/tower"}, paths


def test_every_fetch_call_is_the_read_route():
    src = _code()
    for line in src.splitlines():
        if "fetchJSON(" in line:
            assert "/api/office/tower" in line, line.strip()


# ------------------------------------------------------------------- C7
def test_the_only_timer_is_the_poll():
    """No timer may imply work. Exactly one interval, and it only re-reads."""
    src = _code()
    timers = re.findall(r"\b(setInterval|setTimeout|requestAnimationFrame)\b",
                        src)
    assert timers.count("setInterval") == 1, timers
    assert "setTimeout" not in timers, "a one-shot timer here is a fake activity tick"
    assert "requestAnimationFrame" not in timers, (
        "a frame loop would let the renderer animate without an event")
    # the interval's callback is the loader, not an animation
    assert re.search(r"setInterval\(load,\s*POLL_MS\)", src)
    # and it is cleared on unmount
    assert "clearInterval(id)" in src


def test_no_css_animation_can_run_on_its_own():
    """A keyframe animation is motion with nothing behind it."""
    src = _code()
    assert "@keyframes" not in src
    assert "animation:" not in src.replace("animationName", "")
    assert "animate(" not in src


def test_there_is_no_busy_state_or_spinner():
    src = _code()
    lowered = src.lower()
    for word in ("spinner", "loading-spin", "pulse", "shimmer", "skeleton"):
        assert word not in lowered, f"'{word}' implies work the payload cannot prove"
    # the loading state is a sentence, not motion
    assert "Reading the office" in src


def test_the_renderer_draws_from_the_payload_field_only():
    """The characters' state comes from `animation`, not from a local default."""
    src = _code()
    assert "cs.animation" in src
    # an unknown state must be grey, never an active colour
    assert 'ANIM_COLOR[cs.animation] ||' in src
    # and the colour map has no default-to-working fallback
    assert "|| ANIM_COLOR.working" not in src
    assert "? ANIM_COLOR" not in src


def test_the_rule_is_stated_in_the_renderer_for_the_next_reader():
    src = _src()
    assert "C7" in src
    assert "no event" in src.lower() or "office_event" in src


# ------------------------------------------------- the read route, end to end
# The tab also shipped BROKEN once: the nav was fixed, the bundle loaded, and the
# page rendered "The office could not be read — NOT_FOUND: no compat route for
# GET /api/office/tower". The route table is loaded when the server process
# starts, so these three assertions are what a restarted server must satisfy.


def test_the_tower_read_route_is_registered():
    import voicebridge.qubit_compat as Q

    blob = repr(getattr(Q, "ROUTES"))
    assert "/api/office/tower" in blob, (
        "the tower route is not in the compat ROUTES table — the page would "
        "render its error state with NOT_FOUND")


def test_the_server_serves_the_tower_from_the_qubit_boundary():
    """Served by the Qubit compat handler, not proxied to a vendored store.

    Same reasoning as /api/curator: the page must read the store `qubit office`
    writes, or it renders a different org than the CLI reports.
    """
    from voicebridge import webui

    assert webui.qubit_read_authority("/api/office/tower") is True
    assert "/qubit-office" in webui.SPA_ROUTE_SET


def test_the_route_answers_with_the_payload_the_tab_expects(tmp_path,
                                                            monkeypatch):
    """The route returns the same shape `tower.state()` does, over HTTP."""
    import voicebridge.qubit_compat as Q
    from voicebridge import config as C
    from voicebridge.office import tower

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    y = tmp_path / "voice.yaml"
    y.write_text("logging:\n  jsonl: false\n")
    cfg = C.Config.load(y)

    code, body = Q.h_office_tower(cfg, {}, {}, {})
    assert code == 200 and body["ok"] is True
    for key in ("empty", "empty_reason", "hud", "floorplan", "characters",
                "motion_rule", "read_only"):
        assert key in body, f"the tab reads {key} and the route omits it"
    assert body["read_only"] is True and body["empty"] is True

    # ?motion=1 is the smoke-check shape, and ?now pins the clock
    code, motion = Q.h_office_tower(cfg, {"motion": "1"}, {}, {})
    assert code == 200 and motion["moving"] == 0
    code, pinned = Q.h_office_tower(cfg, {"now": "1700000000"}, {}, {})
    assert code == 200 and pinned["now"] == 1700000000.0
    code, bad = Q.h_office_tower(cfg, {"now": "not-a-number"}, {}, {})
    assert code == 400 and bad["code"] == "BAD_INPUT"

    # and the payload is exactly what the module computes, not a second shape
    # (`_ok()` adds the envelope's own "ok": True)
    assert set(tower.state(cfg)) | {"ok"} == set(body)


# --------------------------------------------------------------- safety
def test_no_html_injection_primitive_is_used():
    """Every payload string renders as text (the same rule qubit-boards keeps)."""
    src = _code()
    assert "dangerouslySetInnerHTML" not in src
    assert "innerHTML" not in src
    assert "document.write" not in src


def test_the_stalled_state_is_surfaced_not_hidden():
    """A dead turn is visible as a fact rather than drawn as work."""
    src = _code()
    assert "stalled" in src and "STALLED" in src


@pytest.mark.parametrize("field", ["headcount", "revenue_cents", "cost_cents",
                                   "net_cents", "burn_cents_per_day",
                                   "runway_days"])
def test_the_hud_fields_the_tab_shows_are_ones_the_backend_sends(
        field, tmp_path, monkeypatch):
    """The renderer cannot show a number the payload does not carry.

    The tab's field names are checked against what `hud()` actually returns, not
    against a second copy of the list — a copy is how a HUD drifts from its
    backend.
    """
    from voicebridge import config as C
    from voicebridge.office import tower

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    y = tmp_path / "voice.yaml"
    y.write_text("logging:\n  jsonl: false\n")

    assert field in _code(), f"the tab claims to show {field}"
    assert field in tower.hud(C.Config.load(y)), (
        f"the tab shows {field} but the backend does not send it")
