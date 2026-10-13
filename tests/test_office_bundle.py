"""The Office bundle: stitched correctly, or not served at all.

WHY THIS FILE EXISTS
--------------------
The dashboard plugin loader injects exactly ONE `<script>` per manifest entry
(`usePlugins.ts`: `script.src = assetRoot/entry`). The Office ships five source
modules, so they are concatenated into one served bundle by
`scripts/build_office_bundle.py`.

A bundle fails in ways the individual modules never do, and each one renders a
dead or half-dead canvas:

* **wrong order** — a module that reads a global before it is defined;
* **a module that never assigns its browser global** — two of the five assign
  theirs only inside the `else` of a CommonJS check, so they load differently
  depending on how they are reached;
* **a stale bundle** — the sources changed and nobody rebuilt, so the page
  serves week-old code and every source-level test still passes;
* **a smuggled timer or write verb** — the visual layer must stay timer-free
  (a live `setInterval` in a module makes `node -e` hang forever, which is how
  one of the agents proved the constraint is load-bearing, not stylistic).

These tests run the bundle the way a BROWSER does — in a `vm` context with
`window` but NO `module` — because that is the path the plugin actually takes
and it is the path where the `else`-branch modules behave differently.

Hermetic: node + tmp files only. No network, no dashboard, no provider call.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
BUNDLE = PLUGIN_DIR / "qubit-office.bundle.js"
BUILDER = REPO / "scripts" / "build_office_bundle.py"
MODULES = ["iso-engine.js", "sprites.js", "camera.js", "control-bar.js",
           "motion.js"]
GLOBALS = ["QubitIso", "QubitSprites", "QubitCamera", "QubitControlBar",
           "QubitOfficeMotion"]

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is required to execute the bundle")


def _code(path: Path) -> str:
    """The file with comments stripped.

    Assertions about what a file DOES must not be satisfied by its own
    documentation — the repo's convention (see test_office_tower_plugin._code).
    """
    src = path.read_text(encoding="utf-8")
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src[i] in "\"'`":
            # A string is KEPT, quotes and all, and skipped as a unit. The first
            # version of this function dropped the quotes AND the contents, which
            # silently deleted the very string literals the write-verb
            # assertions look for -- so those assertions were passing partly by
            # erasing their own evidence.
            quote = src[i]
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote:
                    j += 1
                    break
                j += 1
            out.append(src[i:j])
            i = j
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def _run_in_browser(url_or_path: str, script_body: str) -> dict:
    """Run JS against the bundle in a vm context: window yes, module NO."""
    harness = textwrap.dedent("""
        const fs = require("fs"), vm = require("vm");
        const src = fs.readFileSync(process.argv[1], "utf8");
        const sandbox = {};
        sandbox.window = sandbox; sandbox.self = sandbox;
        sandbox.globalThis = sandbox; sandbox.console = console;
        vm.createContext(sandbox);
        const out = {threw: null, loaded: []};
        try {
            vm.runInContext(src, sandbox, {filename: "bundle.js"});
            out.loaded = sandbox.__qubit_loaded__ || [];
        } catch (e) {
            out.threw = String(e && e.message || e);
        }
        const body = process.argv[2];
        try {
            out.probe = vm.runInContext(body, sandbox, {filename: "probe.js"});
        } catch (e) {
            out.probeError = String(e && e.message || e);
        }
        console.log(JSON.stringify(out));
    """)
    proc = subprocess.run(["node", "-e", harness, url_or_path, script_body],
                          capture_output=True, text=True, timeout=90)
    assert proc.returncode == 0, f"node failed: {proc.stderr[-600:]}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


# ============================================================ it exists and parses

def test_the_bundle_is_committed_and_parses():
    if not BUNDLE.is_file():
        pytest.fail("qubit-office.bundle.js is missing; run "
                    "scripts/build_office_bundle.py")
    proc = subprocess.run(["node", "--check", str(BUNDLE)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-600:]


def test_every_source_module_is_present_and_non_trivial():
    for name in MODULES + ["qubit-office.js"]:
        p = PLUGIN_DIR / name
        assert p.is_file(), f"missing source module {name}"
        assert len(p.read_text().splitlines()) > 50, f"{name} looks empty"
        assert name in BUNDLE.read_text(encoding="utf-8"), \
            f"{name} is not in the bundle"


# ================================================ the browser load path is the one
# that matters: `window` and no `module`

def test_the_bundle_loads_with_no_module_in_scope():
    """The plugin is served as a plain <script>: there is no `module`.

    This is the path where an export hidden in a CommonJS `else` disappears.
    """
    res = _run_in_browser(str(BUNDLE), "1")
    assert res["threw"] is None, f"the bundle threw on load: {res['threw']}"
    assert res["loaded"] == GLOBALS, (
        f"modules that registered: {res['loaded']} != expected {GLOBALS}")


def test_every_module_global_is_reachable_after_load():
    res = _run_in_browser(
        str(BUNDLE),
        json.dumps(GLOBALS) + ".map(n => typeof window[n]).join(',')")
    assert res["probe"] == ",".join(["object"] * len(GLOBALS)), \
        f"a module global is not an object: {res['probe']}"


def test_the_modules_are_usable_not_merely_present():
    """Present-but-broken is the failure a typeof check cannot see."""
    res = _run_in_browser(str(BUNDLE), """(() => {
      const ids = window.QubitControlBar.buildButtons().map(b => b.id);
      const pt = window.QubitIso.gridToScreen(2, 3, 0,
        {cx: 0, cy: 0, zoom: 1, w: 800, h: 500});
      const dir = window.QubitOfficeMotion.DIR(0, 0, 1, 0);
      return JSON.stringify({ids, pt, dir});
    })()""")
    assert "probeError" not in res, res.get("probeError")
    probe = json.loads(res["probe"])
    assert probe["ids"] == ["reset", "fit", "zoom", "pnl"]
    assert isinstance(probe["pt"], dict) and "x" in probe["pt"]
    assert probe["dir"] in ("left", "right", "up", "down")


def test_load_order_puts_each_dependency_before_its_user():
    """A module that reads a global before it is defined cannot work."""
    text = BUNDLE.read_text(encoding="utf-8")
    positions = {n: text.index(n) for n in MODULES + ["qubit-office.js"]}
    assert positions["iso-engine.js"] < positions["qubit-office.js"]
    assert positions["sprites.js"] < positions["qubit-office.js"]
    assert positions["camera.js"] < positions["qubit-office.js"]
    assert positions["motion.js"] < positions["qubit-office.js"]


# ==================================================== the served file is current

def test_the_bundle_is_not_stale(tmp_path):
    """If the sources changed and nobody rebuilt, the page serves old code.

    This is the test that keeps `--check` honest: it rebuilds into a tmp file
    and compares, so a forgotten rebuild fails here rather than in production.
    """
    out = tmp_path / "bundle.js"
    proc = subprocess.run(
        [str(REPO / ".venv" / "bin" / "python"),
         str(BUILDER), "--out", str(out)],
        capture_output=True, text=True, timeout=120, cwd=str(REPO))
    assert proc.returncode == 0, proc.stderr[-600:]
    rebuilt = out.read_text(encoding="utf-8")
    assert rebuilt == BUNDLE.read_text(encoding="utf-8"), (
        "qubit-office.bundle.js is STALE: rebuilding from the sources produces "
        "different bytes. Run scripts/build_office_bundle.py")


def test_the_builder_refuses_a_module_that_does_not_export_its_global(tmp_path):
    """The builder must not bundle a module whose global only exists in an else.

    Rather than trusting the real modules, plant one: a copy of the tree with a
    module that assigns its global solely inside the CommonJS branch.
    """
    fake = tmp_path / "plugin"
    fake.mkdir()
    # a module that only exports under CommonJS — the exact hazard
    (fake / "iso-engine.js").write_text(
        '(function () { var api = {}; if (typeof module === "object")'
        ' { module.exports = api; } })();\n', encoding="utf-8")
    src = BUILDER.read_text(encoding="utf-8")
    patched = fake / "builder.py"
    patched.write_text(
        src.replace('PLUGIN_DIR = REPO / "frontend" / "qubit-app" / "plugins" '
                    '/ "qubit-office"',
                    f'PLUGIN_DIR = Path({str(fake)!r})'),
        encoding="utf-8")
    proc = subprocess.run(
        [str(REPO / ".venv" / "bin" / "python"), str(patched)],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode != 0, (
        "the builder accepted a module with no unconditional browser export")
    assert "iso-engine.js" in (proc.stderr + proc.stdout)


# ================================================= the visual layer stays pure

def test_no_bundled_module_owns_a_timer_or_a_write_verb():
    for name in MODULES:
        code = _code(PLUGIN_DIR / name)
        for banned in ("setInterval", "setTimeout", "requestAnimationFrame",
                       "setImmediate", "queueMicrotask"):
            assert banned not in code, f"{name} owns {banned}: {name}"
        for verb in ("POST", "PUT", "PATCH", "DELETE"):
            assert f'"{verb}"' not in code and f"'{verb}'" not in code, \
                f"{name} names the {verb} verb"


def test_the_renderer_is_in_the_bundle_and_still_read_only():
    """The bundle is what a browser runs, so the contract applies to IT."""
    code = _code(BUNDLE)
    assert code.count("setInterval") == 1, "the bundle must poll exactly once"
    assert "setTimeout" not in code
    assert "requestAnimationFrame" not in code
    assert "@keyframes" not in BUNDLE.read_text(encoding="utf-8")
    for verb in ("POST", "PUT", "PATCH", "DELETE"):
        assert f'"{verb}"' not in code
    assert "/api/office/tower" in code
