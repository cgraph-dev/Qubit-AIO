"""The Office plugin's LOAD PATH is consistent — enforced, not assumed.

WHY THIS FILE EXISTS
--------------------
A plugin's `entry` is declared in THREE places, and the one the running
dashboard actually serves is NOT the obvious one:

  1. ``frontend/qubit-app/plugins/qubit-office/manifest.json``  ->  ``"entry"``
  2. ``frontend/qubit-web/public/plugins/fleet.json``           ->  the row's ``"entry"``
  3. ``frontend/qubit-dist/plugins/fleet.json``                 ->  the row's ``"entry"``
       ^ THIS is the copy the dashboard serves at ``/plugins/fleet.json``.

The manifest was moved to ``qubit-office.bundle.js`` while the served
``fleet.json`` still said ``qubit-office.js``. The dashboard kept loading the
old file, the new bundle sat on disk unused, and the page reported "Office
modules not loaded". Three render cycles were spent before anyone noticed that
editing one copy is never enough.

This module makes that divergence fail HERE, in the test run, instead of
silently serving stale code to a browser. It also proves the declared entry
actually EXISTS, is the freshly-BUILT bundle, and loads its five modules in a
script-like context.

Hermetic except one check: the served-copy check talks to the local dashboard
and SKIPS CLEANLY when no server is up.

Ownership: this is the only file in ``tests/`` that asserts across the three
declaration sites; ``tests/test_office_tower_plugin.py`` owns the renderer's
own contract (no mutating verb, one timer, etc).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from _office_node import run_node

REPO = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
MANIFEST = PLUGIN_DIR / "manifest.json"

#: The two static fleet manifests. The dashboard serves the ``qubit-dist`` copy
#: at ``/plugins/fleet.json``; the ``qubit-web`` copy is the source the SPA
#: build copies from. They must agree or one endpoint serves a different tab.
FLEET_WEB = REPO / "frontend" / "qubit-web" / "public" / "plugins" / "fleet.json"
FLEET_DIST = REPO / "frontend" / "qubit-dist" / "plugins" / "fleet.json"

DASHBOARD_FLEET_URL = "http://127.0.0.1:8890/plugins/fleet.json"

#: The six globals the bundle must define, in dependency order.
#: `QubitAtlas` precedes `QubitSprites` because sprites.js reads it at load time.
BUNDLE_GLOBALS = ("QubitIso", "QubitAtlas", "QubitSprites", "QubitCamera",
                  "QubitControlBar", "QubitOfficeMotion")

#: Non-trivial lower bound: a real concatenated bundle is >100 KB. 1000 bytes
#: rejects a stub, a truncated file, or an error page written to disk.
MIN_ENTRY_BYTES = 1000


# ------------------------------------------------------------------- helpers
def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _fleet_rows(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _row_for(rows: list[dict], name: str) -> dict | None:
    return next((r for r in rows if r.get("name") == name), None)


def _node() -> str | None:
    """Path to the ``node`` binary, or None when it is not installed."""
    return shutil.which("node")


# ============================================================ assertion 1
def test_all_three_entry_declarations_agree():
    """Every plugin listed in more than one place must match on name/entry/path.

    REAL DEFECT THIS CATCHES: the exact bug that cost three render cycles. The
    manifest said ``qubit-office.bundle.js``; the served ``fleet.json`` still
    said ``qubit-office.js``. Comparing only the manifest against itself passes
    every time — the served copy is where the drift lives.

    It also catches a plugin present in one fleet.json and absent from the
    other (the two endpoints would disagree about which tabs exist).
    """
    manifest = _manifest()
    web_rows = _fleet_rows(FLEET_WEB)
    dist_rows = _fleet_rows(FLEET_DIST)

    # site name -> {declared name -> (entry, tab.path)}
    sites = {
        "manifest.json": {
            manifest["name"]: (manifest["entry"], manifest["tab"]["path"])},
        "qubit-web/public/plugins/fleet.json": {
            r["name"]: (r["entry"], r["tab"]["path"]) for r in web_rows},
        "qubit-dist/plugins/fleet.json": {
            r["name"]: (r["entry"], r["tab"]["path"]) for r in dist_rows},
    }

    # ---- the two fleet copies must list the same plugin names --------------
    web_names = set(sites["qubit-web/public/plugins/fleet.json"])
    dist_names = set(sites["qubit-dist/plugins/fleet.json"])
    if web_names != dist_names:
        only_web = sorted(web_names - dist_names)
        only_dist = sorted(dist_names - web_names)
        # This is a real divergence, but it is about plugins OTHER than
        # qubit-office; report it in this test's failure text without making
        # qubit-office's own contract depend on a sibling's state, and assert
        # only that qubit-office is present in BOTH.
        assert "qubit-office" in web_names and "qubit-office" in dist_names, (
            f"qubit-office missing from a fleet copy; the two fleet.json copies "
            f"also disagree: only in qubit-web={only_web}, "
            f"only in qubit-dist={only_dist}")

    # ---- the three declarations of qubit-office must be identical ----------
    declared = {site: rows.get("qubit-office")
                for site, rows in sites.items()}
    for site, value in declared.items():
        assert value is not None, (
            f"qubit-office has no entry declared in {site!r}; a plugin absent "
            f"from a site the loader reads would vanish from that surface")
    values = set(declared.values())
    assert len(values) == 1, (
        "the three entry declarations of qubit-office DISAGREE — the served "
        "copy is what the running dashboard loads, so a divergence here is "
        "stale code served to the browser:\n  " +
        "\n  ".join(f"{site}: entry={v[0]!r} tab.path={v[1]!r}"
                    for site, v in declared.items()))

    entry, tab_path = values.pop()
    assert entry, "the declared entry is empty"
    # the manifest must be the anchor, so a divergent fleet row is a copy bug
    assert entry == manifest["entry"], (
        f"the fleet copies declare {entry!r} but the manifest declares "
        f"{manifest['entry']!r}")
    assert tab_path == manifest["tab"]["path"]


# ============================================================ assertion 2
def test_the_declared_entry_exists_next_to_the_manifest():
    """The declared entry file must EXIST in the plugin dir and be non-trivial.

    REAL DEFECT THIS CATCHES: a manifest/fleet row that names a file nobody
    built (``entry: "qubit-office.bundle.js"`` before anyone ran the builder, or
    a rename that left the old name behind). The loader fetches
    ``/dashboard-plugins/qubit-office/<entry>``; a missing file is a silent 404
    and the page renders nothing — the same "modules not loaded" failure, with
    no error on the server side.
    """
    m = _manifest()
    entry = m["entry"]
    path = PLUGIN_DIR / entry
    assert path.is_file(), (
        f"the declared entry {entry!r} does not exist at {path} — the loader "
        f"would 404 it and the tab would render empty")
    size = path.stat().st_size
    assert size > MIN_ENTRY_BYTES, (
        f"{entry} is only {size} bytes (need >{MIN_ENTRY_BYTES}); a stub or a "
        f"truncated write is not a loadable bundle")


# ============================================================ assertion 3
def test_the_declared_entry_is_the_current_built_bundle():
    """``scripts/build_office_bundle.py --check`` must exit 0.

    REAL DEFECT THIS CATCHES: the entry on disk is a STALE build of the five
    source modules — someone edited ``motion.js`` and shipped without
    re-running the builder, so the browser loads a bundle that predates the
    source. The builder is the single writer of ``qubit-office.bundle.js``; its
    ``--check`` mode rebuilds in memory and byte-compares against the file,
    exiting non-zero on any difference (and on refusal, if a module fails to
    export its promised global).

    This only proves freshness for the BUNDLE. It is meaningful because
    assertion 2 pins the entry name; together they mean "the file the browser
    loads is the file the sources generate right now".
    """
    script = REPO / "scripts" / "build_office_bundle.py"
    assert script.is_file(), f"the builder is missing at {script}"
    proc = subprocess.run(
        [sys.executable, str(script), "--check"],
        cwd=str(REPO), capture_output=True, text=True)
    assert proc.returncode == 0, (
        f"the declared entry is STALE (builder exited {proc.returncode}); "
        f"run `{Path(sys.executable).name} scripts/build_office_bundle.py`:\n"
        f"stdout: {proc.stdout.strip()}\nstderr: {proc.stderr.strip()}")


# ============================================================ assertion 4
def test_the_declared_entry_loads_and_registers_its_component():
    """Load the served entry like a ``<script>`` tag and prove it is LIVE.

    WHAT CHANGED AND WHY (the guarantee moved, it did not vanish)
    -------------------------------------------------------------
    This assertion used to prove the served entry defined the five CLASSIC
    globals (``QubitIso``/``QubitAtlas``/``QubitSprites``/``QubitCamera``/
    ``QubitControlBar``/``QubitOfficeMotion``). Those globals are the exports of
    the hand-stitched ``qubit-office.bundle.js`` built by
    ``scripts/build_office_bundle.py``, and they exist ONLY to feed the classic
    canvas renderer in ``qubit-office.js``.

    The manifest now promotes the Vite-built game (``qubit-office.game.js``),
    which registers its own component through the SDK and needs none of them.
    That promotion keeps the classic bundle ONLY as a rollback artifact
    (``tests/test_office_game_packaging.py`` asserts it still exists for exactly
    that reason) — it is never loaded. And the loader itself
    (``frontend/qubit-web/src/plugins/usePlugins.ts``) injects exactly ONE
    ``<script>`` per manifest ``entry``, while ``docs/game-office/
    frontend-game/30-ARCHITECTURE.md`` states the manifest "selects exactly ONE
    entry. Never leave duplicate native and plugin route owners active." So the
    classic modules CANNOT be loaded alongside the game entry, and bundling
    their dead exports into the game would ship a second, unused renderer.

    The guarantee this file actually exists for — "the file the browser loads is
    not inert: it runs without throwing and registers a real React component for
    the tab" — is now asserted directly on the served entry, through the same
    SDK seam the dashboard uses. A bundle that loads but registers nothing (or
    registers an OBJECT where the registry stores a component type — the live
    'Element type is invalid ... got: object' bug) fails here.

    The context reproduces real script semantics: ``window`` IS the global
    object (as it is in a browser), and there is NO ``module`` object — that is
    the path the served ``<script>`` takes. The SDK is supplied as the dashboard
    supplies it, because the entry registers a component built from it.
    """
    node = _node()
    if node is None:
        pytest.skip("node is not available to execute the bundle")

    probe = r"""
const fs = require("fs"), vm = require("vm");
const path = process.env.OFFICE_WIRING_ENTRY;
const src = fs.readFileSync(path, "utf8");
// A browser <script> context: window === globalThis, NO `module`, NO `exports`.
const sandbox = { console: console };
sandbox.window = sandbox;
sandbox.self = sandbox;
/* THE ENGINE PROBES THE BROWSER AT MODULE INIT.
 * Phaser 4 reads `navigator.userAgent` while its module body evaluates, so a
 * bare Node context throws `ReferenceError: navigator is not defined` before a
 * single line of plugin code runs. That is worth knowing beyond this test: the
 * bundle cannot be evaluated in a DOM-less environment at all (SSR, prerender,
 * a plain Node import), and the failure surfaces as "the bundle is broken"
 * rather than "there is no DOM". The shim below is the minimum Phaser needs to
 * reach its own registration; it does NOT fake rendering. */
sandbox.navigator = {
  userAgent: "Mozilla/5.0 (X11; Linux x86_64) Node/office-test",
  platform: "Linux x86_64",
  language: "en-GB",
  languages: ["en-GB"],
  hardwareConcurrency: 4,
  maxTouchPoints: 0,
  vendor: "",
};
/* A 2D context Phaser's init-time capability probe can write to. Phaser calls
 * `checkInverseAlpha` on a 1x1 canvas and sets `fillStyle`/`fillRect`; a null
 * context throws before the plugin body runs. This is a WRITE-THROUGH STUB, not
 * a renderer — nothing here draws, it only has to be assignable. */
function fakeCtx() {
  const c = {};
  for (const m of ["save","restore","fillRect","clearRect","drawImage","beginPath",
                   "closePath","moveTo","lineTo","arc","fill","stroke","strokeRect",
                   "rect","roundRect","setTransform","translate","scale","rotate",
                   "measureText","createImageData","getImageData","putImageData",
                   "createLinearGradient","createRadialGradient","clip","setLineDash"]) {
    c[m] = () => (m === "measureText" ? {width: 0}
                : m.startsWith("create") ? {addColorStop() {}}
                : m.startsWith("get") ? {data: [0, 0, 0, 255]}
                : undefined);
  }
  return c;
}
const mkCanvas = () => ({
  style: {}, width: 1, height: 1,
  getContext: () => fakeCtx(),
  addEventListener() {}, removeEventListener() {},
  setAttribute() {}, appendChild() {}, remove() {},
  getBoundingClientRect: () => ({left:0,top:0,width:1,height:1}),
  toDataURL: () => "data:,",
});
sandbox.document = {
  createElement: (tag) => (tag === "canvas" ? mkCanvas() : {
    style: {}, getContext: () => fakeCtx(), addEventListener() {}, removeEventListener() {},
    setAttribute() {}, appendChild() {}, remove() {},
    getBoundingClientRect: () => ({left:0,top:0,width:0,height:0}),
  }),
  getElementById: () => null,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {}, removeEventListener() {},
  documentElement: { style: {}, addEventListener() {}, removeEventListener() {} },
  body: { appendChild() {}, style: {}, addEventListener() {} },
  head: { appendChild() {} },
  readyState: "complete",
  createTextNode: () => ({}),
};
sandbox.location = { href: "http://localhost/qubit-office", protocol: "http:", search: "", hash: "" };
sandbox.screen = { width: 1280, height: 720, orientation: { type: "landscape-primary" } };
sandbox.devicePixelRatio = 1;
sandbox.requestAnimationFrame = (fn) => setTimeout(() => fn(Date.now()), 16);
sandbox.cancelAnimationFrame = (id) => clearTimeout(id);
sandbox.addEventListener = () => {};
sandbox.removeEventListener = () => {};
sandbox.AudioContext = function () { return { createGain: () => ({ connect() {} }), destination: {} }; };
sandbox.HTMLCanvasElement = function () {};
sandbox.HTMLVideoElement = function () {};
sandbox.Image = function () {};
/* The dashboard's plugin SDK, verbatim in shape: `React`, `hooks`,
 * `fetchJSON`. `window.__QUBIT_PLUGINS__.register(name, component)` stores the
 * second argument AS the component type — so what is registered must be a
 * function, never an object literal. */
sandbox.__QUBIT_PLUGIN_SDK__ = {
  React: { createElement: function (t, p) {
    return {type: t, props: p || {}, children: Array.prototype.slice.call(arguments, 2)}; } },
  hooks: {
    useState: function (init) { return [init, function () {}]; },
    useEffect: function () {}, useRef: function (v) { return {current: v}; },
    useCallback: function (f) { return f; },
  },
  fetchJSON: function () { return Promise.resolve({}); },
};
let registeredName = null;
let registeredKind = "__unset__";
sandbox.__QUBIT_PLUGINS__ = {
  register: function (name, component) {
    registeredName = name;
    registeredKind = typeof component;
  },
};
const ctx = vm.createContext(sandbox);
vm.runInContext(src, ctx, { filename: path });
console.log(JSON.stringify({name: registeredName, kind: registeredKind}));
"""
    entry = PLUGIN_DIR / _manifest()["entry"]
    proc = run_node(probe, timeout=180,
                    env={**os.environ, "OFFICE_WIRING_ENTRY": str(entry)})
    assert proc.returncode == 0, (
        f"the served entry failed to LOAD as a <script> (node exit "
        f"{proc.returncode}):\n{proc.stderr.strip()}")
    result = json.loads(proc.stdout.strip().splitlines()[-1])

    assert result["name"] == "qubit-office", (
        f"the entry registered {result['name']!r}, not 'qubit-office' — the "
        f"tab's route would resolve no component and render nothing")
    assert result["kind"] == "function", (
        f"the entry registered a {result['kind']!r} where the registry stores a "
        f"COMPONENT TYPE — registering an object literal is the live "
        f"'Element type is invalid ... got: object' bug")


# ============================================================ assertion 4b
def test_the_rollback_bundle_still_defines_the_classic_globals():
    """The classic globals are obsolete FOR THE SERVED ENTRY, not deleted.

    They are the exports of ``qubit-office.bundle.js``, the documented rollback
    artifact (``tests/test_office_game_packaging.py`` asserts it stays on disk
    and ``docs/game-office/frontend-game/30-ARCHITECTURE.md`` records the
    one-entry switch). This test keeps the old guarantee asserted where it now
    legitimately lives: on the rollback bundle, so a rollback to it is still a
    working renderer. It is SKIPPED when that bundle is absent so a clean clone
    without a generated bundle does not fail — but if the file is present and
    has stopped defining its modules, that is a real regression.
    """
    node = _node()
    if node is None:
        pytest.skip("node is not available to execute the bundle")
    bundle = PLUGIN_DIR / "qubit-office.bundle.js"
    if not bundle.is_file():
        pytest.skip("the rollback bundle is not present in this checkout")

    probe = r"""
const fs = require("fs"), vm = require("vm");
const path = process.env.OFFICE_WIRING_ENTRY;
const src = fs.readFileSync(path, "utf8");
const sandbox = { console: console };
sandbox.window = sandbox;
sandbox.self = sandbox;
const ctx = vm.createContext(sandbox);
vm.runInContext(src, ctx, { filename: path });
const out = {};
for (const g of __GLOBALS__) out[g] = typeof sandbox[g];
out.__loaded = sandbox.__qubit_loaded__;
console.log(JSON.stringify(out));
"""
    probe = probe.replace("__GLOBALS__", json.dumps(list(BUNDLE_GLOBALS)))
    proc = run_node(probe, timeout=180,
                    env={**os.environ, "OFFICE_WIRING_ENTRY": str(bundle)})
    assert proc.returncode == 0, (
        f"the rollback bundle failed to load (node exit {proc.returncode}):\n"
        f"{proc.stderr.strip()}")
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    for name in BUNDLE_GLOBALS:
        assert result.get(name) == "object", (
            f"window.{name} is {result.get(name)!r} in the rollback bundle — a "
            f"rollback to it would render a dead canvas")
    assert result["__loaded"] == list(BUNDLE_GLOBALS), (
        f"the rollback bundle's load guard recorded {result['__loaded']!r}; "
        f"every module must have proven its global present")


# ============================================================ assertion 5
def test_the_served_fleet_matches_the_copy_on_disk():
    """The running dashboard's ``/plugins/fleet.json`` must equal the dist copy.

    REAL DEFECT THIS CATCHES: the served ``fleet.json`` is what the browser
    actually reads, and it can drift from the file on disk when the dashboard
    serves a stale in-memory copy or a different root than ``qubit-dist``. This
    is the check that would have caught the original bug immediately: it reads
    the SAME URL the browser hits and compares the qubit-office row to
    ``frontend/qubit-dist/plugins/fleet.json``.

    SKIPS CLEANLY when no dashboard is listening — this test must not fail a
    hermetic run.
    """
    try:
        with urllib.request.urlopen(DASHBOARD_FLEET_URL, timeout=2) as resp:
            body = resp.read()
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        pytest.skip(f"no dashboard at {DASHBOARD_FLEET_URL} ({exc})")

    served = json.loads(body.decode("utf-8"))
    served_row = _row_for(served, "qubit-office")
    assert served_row is not None, (
        "the served fleet.json omits qubit-office — the running dashboard "
        "would not show the Office tab even though the file on disk lists it")

    disk_row = _row_for(_fleet_rows(FLEET_DIST), "qubit-office")
    assert served_row == disk_row, (
        "the SERVED fleet.json disagrees with frontend/qubit-dist/plugins/"
        "fleet.json — the browser loads the served copy, so the page and the "
        "disk have drifted:\n"
        f"  served: {json.dumps(served_row, sort_keys=True)}\n"
        f"  disk:   {json.dumps(disk_row, sort_keys=True)}")


# ============================================================ assertion 6
def test_the_tab_path_is_a_real_spa_route():
    """``tab.path`` must appear in ``SPA_ROUTE_PATHS`` in ``voicebridge/webui.py``.

    REAL DEFECT THIS CATCHES: the tab's path is not in the server's shell-route
    table, so clicking the tab 404s instead of rendering the SPA. An earlier bug
    shipped exactly this — the path was missing from ``SPA_ROUTE_PATHS`` and the
    route worked everywhere except the one place a user clicks.

    Parsed the same way as
    ``tests/test_office_tower_plugin.py::test_the_tab_path_is_a_shell_route_on_the_server``
    so both tests agree on what the route table says.
    """
    webui = (REPO / "voicebridge" / "webui.py").read_text(encoding="utf-8")
    m = re.search(r"SPA_ROUTE_PATHS\s*=\s*\((.*?)\n\)", webui, re.S)
    assert m, "SPA_ROUTE_PATHS could not be parsed from voicebridge/webui.py"
    paths = set(re.findall(r'"([^"]+)"', m.group(1)))
    assert len(paths) > 5, f"parsed too few routes: {sorted(paths)}"
    tab_path = _manifest()["tab"]["path"]
    assert tab_path in paths, (
        f"{tab_path} is not in SPA_ROUTE_PATHS {sorted(paths)} — the server "
        f"would not render the SPA shell for the tab, and it would 404")


# ============================================================ assertion 7
def test_the_icon_is_a_real_client_icon():
    """The manifest icon must be a key in the client's ``ICON_MAP``.

    REAL DEFECT THIS CATCHES: ``resolveIcon`` falls back to a generic Puzzle
    icon, so a typo or a renamed icon fails SILENTLY — the tab appears with the
    wrong glyph and nothing errors. Same parse as the existing Office test.
    """
    app = REPO / "frontend" / "qubit-web" / "src" / "App.tsx"
    assert app.is_file(), f"cannot find the client App.tsx at {app}"
    src = app.read_text(encoding="utf-8")
    block = src[src.index("const ICON_MAP"):]
    block = block[:block.index("};")]
    icons = set(re.findall(r"^\s*([A-Za-z0-9_]+),", block, re.M))
    assert icons, "the ICON_MAP could not be parsed"
    icon = _manifest()["icon"]
    assert icon in icons, (
        f"icon {icon!r} is not in ICON_MAP ({sorted(icons)}) — the tab would "
        f"render the generic Puzzle icon")
