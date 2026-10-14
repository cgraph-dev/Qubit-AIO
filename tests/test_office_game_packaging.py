"""F04 — the packaging spike: one entry, one React runtime, a clean lifecycle.

`docs/game-office/frontend-game/60-IMPLEMENTATION-PLAN.md` F04 asks for a spike
that proves the packaging path before the game is built on it, and names its
own proof: "one functioning scene/input surface, one React runtime, no
duplicate registration; cold reload/unmount clean."

What that means concretely, and what this file checks:

  1. **It builds at all, deterministically.** Two builds of unchanged source
     are byte-identical — the same guarantee `build_office_bundle.py --check`
     makes for the hand-stitched bundle, so a rebuild is a no-op.
  2. **React is NOT bundled.** The artifact must not contain a second React
     runtime. This is the silent failure the config's `external` list exists to
     prevent, and a text check is the right tool because the property IS about
     what bytes shipped.
  3. **ONE entry, ONE registration.** The plugin loader injects exactly one
     `<script>` per manifest entry.
  4. **The lifecycle is clean.** The scene host binds on mount, releases every
     listener on destroy, and `destroy()` is idempotent — which is what makes
     React StrictMode's mount/unmount/remount safe instead of leaking a second
     engine onto one canvas.

Engine-agnostic on purpose: these assertions hold for the Canvas host, and
would hold for a Phaser host, because the contract lives in `SceneHost.ts`
rather than in whichever engine answers it. That is what keeps the
Phaser-vs-Pixi decision (10-DECISIONS.md § Renderer comparison) reversible.

Hermetic: builds into a temp dir, reads the artifact, drives the host through
`node`. No browser, no server, no network, no provider call.
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
PLUGIN_DIR = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
GAME_DIR = PLUGIN_DIR / "game"
CONFIG = WEB / "vite.office-game.config.ts"

NODE = shutil.which("node")
VITE = WEB / "node_modules" / "vite" / "bin" / "vite.js"


def _have_build_toolchain() -> bool:
    return bool(NODE) and VITE.is_file()


pytestmark = pytest.mark.skipif(
    not _have_build_toolchain(),
    reason="node + the web node_modules (vite) are required for the F04 spike")


def _build(outdir: Path) -> subprocess.CompletedProcess:
    """Build the spike into `outdir` with no publicDir copy."""
    env = {"PATH": f"{Path(NODE).parent}:{'/usr/bin:/bin'}"}
    proc = subprocess.run(
        [NODE, str(VITE), "build", "--config", str(CONFIG),
         "--outDir", str(outdir), "--emptyOutDir"],
        capture_output=True, text=True, timeout=300, cwd=str(WEB), env=env)
    return proc


# ======================================================================
# 1 — the spike source exists and is engine-agnostic
# ======================================================================
def test_the_spike_files_exist():
    for name in ("entry.ts", "SceneHost.ts", "CanvasSceneHost.ts",
                 "OfficeGameShell.tsx"):
        assert (GAME_DIR / name).is_file(), f"missing game/{name}"
    assert CONFIG.is_file(), f"missing {CONFIG.name}"


def test_the_host_reaches_engines_only_through_the_factory_seam():
    """The engine choice must stay a ONE-ARGUMENT swap.

    An earlier version of this test asserted the host named no engine at all.
    That was the right guard while the decision was open; now that the owner has
    ruled, naming an engine is expected. What must STILL hold is that the host
    talks to the `SceneHost` interface and takes the engine as a factory — so
    the Canvas fallback remains one argument away, and the engine is never
    used directly in the React layer.
    """
    raw = (GAME_DIR / "OfficeGameShell.tsx").read_text()
    shell = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    shell = re.sub(r"//[^\n]*", "", shell)
    assert "from \"./SceneHost\"" in shell, (
        "the host no longer imports the engine-agnostic contract")
    assert "createScene" in shell, (
        "the engine factory is not injectable, so the engine choice is baked in")
    # A default is fine; a DIRECT use is not.
    assert "new Phaser" not in shell, (
        "the React host constructs Phaser directly instead of via the factory — "
        "the Canvas fallback is no longer a one-argument swap")
    imports = re.findall(r'import\s*\{([^}]*)\}\s*from\s*"([^"]+)"', shell)
    for names, src in imports:
        if src in ("react", "./SceneHost", "./CanvasSceneHost",
                   "./PhaserSceneHost", "./input", "./navigation"):
            continue
        assert "phaser" not in names, (
            "the React layer imports Phaser outside the host module")


def test_both_engines_satisfy_one_contract():
    """The fallback is only real if it answers the same interface."""
    for name in ("CanvasSceneHost.ts", "PhaserSceneHost.ts"):
        src = (GAME_DIR / name).read_text()
        assert "SceneHost" in src, f"{name} does not use the SceneHost contract"
    canvas = (GAME_DIR / "CanvasSceneHost.ts").read_text()
    phaser = (GAME_DIR / "PhaserSceneHost.ts").read_text()
    for method in ("mount", "step", "destroy", "camera", "listenerCount"):
        assert f"{method}(" in canvas, f"the Canvas host lacks {method}()"
        assert f"{method}(" in phaser, f"the Phaser host lacks {method}()"


def test_the_host_does_not_own_a_timer():
    """C7: a gesture redraws on its own event. Nothing here may tick."""
    code = (GAME_DIR / "CanvasSceneHost.ts").read_text()
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    for banned in ("setInterval", "setTimeout", "requestAnimationFrame"):
        assert banned not in code, (
            f"the scene host uses {banned}: the rendering must be driven by "
            f"data change and gestures, never by a clock")


def test_the_host_issues_no_request():
    """A camera gesture is presentation, never a network action (C4/C7)."""
    code = (GAME_DIR / "CanvasSceneHost.ts").read_text()
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)
    for banned in ("fetch(", "XMLHttpRequest", "sendBeacon", "axios"):
        assert banned not in code, (
            f"the scene host calls {banned}: a gesture must not fetch")


# ======================================================================
# 2 — it builds, deterministically, and ships no React
# ======================================================================
def test_it_builds(tmp_path):
    out = tmp_path / "dist"
    proc = _build(out)
    assert proc.returncode == 0, (
        f"the spike failed to build\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}")
    art = out / "qubit-office.game.js"
    assert art.is_file(), f"the build produced no artifact: {list(out.iterdir())}"
    assert art.stat().st_size > 500, "the artifact is implausibly small"


def test_two_builds_of_unchanged_source_are_byte_identical(tmp_path):
    """A rebuild with no source change must be a no-op — the guarantee the
    hand-stitched bundle already makes."""
    a, b = tmp_path / "a", tmp_path / "b"
    assert _build(a).returncode == 0
    assert _build(b).returncode == 0
    assert (a / "qubit-office.game.js").read_bytes() == \
           (b / "qubit-office.game.js").read_bytes(), (
        "the build is not deterministic; a rebuild would churn the artifact")


def test_react_is_not_bundled(tmp_path):
    """THE silent failure this config exists to prevent: a second React copy
    splits the hook runtime, so session and theme never reach the engine."""
    out = tmp_path / "dist"
    assert _build(out).returncode == 0
    src = (out / "qubit-office.game.js").read_text()
    # A bundled React carries its own internals; an externalized one leaves the
    # bare import as a reference to the host global.
    for marker in ("react.production", "ReactCurrentDispatcher",
                   "__CLIENT_INTERNALS_DO_NOT_USE"):
        assert marker not in src, (
            f"the artifact contains React internals ({marker}): React was "
            f"bundled instead of externalized")
    assert "React" in src, (
        "the artifact does not reference the host's React global at all")


def test_the_config_externalizes_every_react_entrypoint():
    cfg = CONFIG.read_text()
    for name in ("react", "react-dom", "react/jsx-runtime",
                 "react/jsx-dev-runtime"):
        assert name in cfg, (
            f"{name} is not externalized; a jsx-runtime pulled from a second "
            f"React is the same split-runtime bug")


def test_the_config_never_empties_a_shared_served_directory():
    """An `emptyOutDir: true` pointed at a served directory is a directory
    delete inside a tree that holds unrelated assets."""
    cfg = CONFIG.read_text()
    assert "emptyOutDir: false" in cfg, (
        "emptyOutDir is not pinned false: the build could delete sibling assets")
    assert "publicDir: false" in cfg, (
        "publicDir is not disabled: the build would copy the whole SPA into "
        "the output directory")


def test_the_manifest_entry_exists_on_disk_and_is_the_promoted_game():
    """The entry is now the PROMOTED game bundle.

    The original guard ("the manifest still names the old bundle") existed to
    stop an unproven spike being promoted by accident. That promotion is now an
    explicit owner decision, so the guard becomes the thing that actually
    matters: the entry must EXIST on disk and be the built game — a manifest
    naming a file that is not there is a broken tab, and `frontend/qubit-dist`
    is gitignored so the artifact cannot live there (a clean clone would omit
    it; tests/test_clean_clone_wave5.py enforces that).
    """
    manifest = json.loads((PLUGIN_DIR / "manifest.json").read_text())
    assert manifest["entry"] == "qubit-office.game.js", (
        f"the served entry is {manifest['entry']!r}; the promoted game bundle "
        f"is qubit-office.game.js")
    entry = PLUGIN_DIR / manifest["entry"]
    assert entry.is_file(), (
        f"the manifest names {manifest['entry']!r} but it does not exist in the "
        f"served plugin directory — the tab would 404")
    # It must be the BUILT game (engine included), not a stray empty file.
    assert entry.stat().st_size > 1_000_000, (
        f"the served entry is {entry.stat().st_size} bytes; the built game "
        f"bundle carries the engine and is megabytes")
    # And it must live in the COMMITTED served directory, not the gitignored
    # build area, or a fresh clone serves a manifest pointing at nothing.
    assert PLUGIN_DIR in entry.parents
    assert "qubit-dist" not in str(entry), (
        "the entry lives under frontend/qubit-dist, which is gitignored — a "
        "fresh clone would serve a manifest naming a missing file")
    # The old hand-stitched bundle stays as a reference/rollback.
    assert (PLUGIN_DIR / "qubit-office.bundle.js").is_file(), (
        "the previous hand-stitched bundle was deleted; it is the rollback path")


# ======================================================================
# 2b — the engine: Phaser 4.2.1, pinned, MIT, and honestly sized
# ======================================================================
def test_phaser_is_pinned_exactly_not_by_range():
    """A caret range on a game engine means a rebuild can silently change the
    engine. The owner ruled on 4.2.1 specifically, so the manifest must say
    exactly that — no `^`, no `~`."""
    manifest = json.loads((WEB / "package.json").read_text())
    deps = {**manifest.get("dependencies", {}),
            **manifest.get("devDependencies", {})}
    assert "phaser" in deps, "phaser is not declared in the web manifest"
    version = deps["phaser"]
    assert version == "4.2.1", (
        f"phaser is pinned at {version!r}; the owner's ruling was exactly "
        f"4.2.1 and a range would let a rebuild change the engine")
    for bad in ("^", "~", "*", ">", "<"):
        assert bad not in version, f"phaser version {version!r} is a range"


def test_the_installed_phaser_is_the_pinned_version_and_is_mit():
    """AGENTS.md requires provenance before reusing external source. MIT is
    compatible; anything else would be a licence review, not a dependency."""
    pkg = WEB / "node_modules" / "phaser" / "package.json"
    if not pkg.is_file():
        pytest.skip("phaser is not installed in this checkout")
    meta = json.loads(pkg.read_text())
    assert meta["version"] == "4.2.1", (
        f"installed phaser is {meta['version']}, not the pinned 4.2.1")
    assert meta["license"] == "MIT", (
        f"phaser is {meta['license']}: a non-MIT licence needs a licence "
        f"review (docs/archive/2026-09-21/LICENSING.md), not an import")
    lic = (WEB / "node_modules" / "phaser" / "LICENSE.md")
    if lic.is_file():
        head = lic.read_text()[:400]
        assert "MIT" in head and "Richard Davey" in head, (
            "the Phaser licence notice is not the expected MIT text — "
            "re-check provenance before shipping it")


def test_the_phaser_host_implements_the_same_contract_as_the_canvas_host():
    """Both engines answer ONE interface, which is what makes the engine
    choice reversible. If the Phaser host drifted from the contract, the
    fallback would silently stop being a fallback."""
    src = (GAME_DIR / "PhaserSceneHost.ts").read_text()
    for method in ("mount", "step", "destroy", "camera", "listenerCount"):
        assert f"{method}(" in src, (
            f"the Phaser host does not implement {method}() — it has drifted "
            f"from SceneHost")
    assert "implements SceneHost" in src, (
        "the Phaser host does not declare the SceneHost contract")


def test_the_shell_gives_the_engine_an_empty_stage():
    """ONE visible canvas, not two.

    Phaser creates its own canvas. If the shell hands it a container that
    already holds the measuring canvas, the page gets two — and the engine's
    paints over the top of a hidden one while the DOM keeps both. The live fix
    was to render a hidden measuring canvas AND an empty stage div.
    """
    raw = (GAME_DIR / "OfficeGameShell.tsx").read_text()
    src = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    assert "stageRef" in src, (
        "the shell has no dedicated stage container for the engine")
    assert '"office-game-stage"' in src, "the stage container is not marked"
    # the measuring canvas must be hidden, never the drawing surface
    assert "aria-hidden" in src or 'display: "none"' in src, (
        "the measuring canvas is not hidden; the page would show a dead canvas")
    # and the mount must use the stage, not the canvas's parent
    assert "scene.mount(stage" in src or "mount(stage," in src, (
        "the engine is not mounted into the dedicated stage container — it "
        "would be handed a parent that already contains a canvas")
    assert "mount(cv.parentElement" not in src, (
        "the engine mounts into the canvas's parent, which may hold other nodes")


def test_the_plugin_registers_a_component_not_an_object():
    """`registry.ts::registerPlugin(name, component)` stores the SECOND argument
    as the component type. Registering `{Office, React, hooks}` produced
    'Element type is invalid ... got: object' in the live page."""
    src = (GAME_DIR / "entry.ts").read_text()
    assert "createOfficeComponent" in src, (
        "the entry does not build a component from the host React")
    call = src[src.find("register("):]
    call = call[:call.find(");") + 2] if ");" in call else call[:300]
    assert "{ Office" not in call and "Office:" not in call, (
        "the entry registers an OBJECT where the registry expects a component")


def test_the_shell_takes_react_from_the_host_never_an_import():
    """A `react` import compiles to a bare global reference; the dashboard
    defines none. That was a live 'React is not defined' page error."""
    raw = (GAME_DIR / "OfficeGameShell.tsx").read_text()
    src = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    assert 'from "react"' not in src, (
        "the shell imports react directly; the host supplies it via the plugin "
        "SDK and the build externalizes the module")
    assert "props.hooks" in src, "the hooks are not taken from the host"
    assert "createElement" in src, (
        "the shell does not build elements from the host's createElement — JSX "
        "would emit a jsx-runtime import the dashboard does not provide")


def test_the_phaser_host_binds_and_releases_its_own_gestures():
    """The engine has its own input system; Qubit keeps ONE gesture
    vocabulary so the C4/C7 checks hold over one implementation, not two."""
    src = (GAME_DIR / "PhaserSceneHost.ts").read_text()
    assert "addEventListener" in src and "removeEventListener" in src, (
        "the Phaser host binds gestures without releasing them — a StrictMode "
        "remount would stack handlers on the container")
    for gesture in ('"pointerdown"', '"pointermove"', '"pointerup"',
                    '"wheel"'):
        assert gesture in src, f"the Phaser host does not handle {gesture}"


def test_the_phaser_host_does_not_render_from_its_own_clock():
    """C7: Phaser HAS a render loop — that is what an engine is, and the
    amendment permits presentational motion. What it must never do is advance
    DOMAIN state. The host must paint only revisions it was handed.

    Comments stripped: the module header explains Phaser's own loop, and a naive
    check fails on that explanation.
    """
    raw = (GAME_DIR / "PhaserSceneHost.ts").read_text()
    src = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    src = re.sub(r"//[^\n]*", "", src)
    assert "requestAnimationFrame" not in src, (
        "the host drives its own animation frame; Phaser owns the loop, and a "
        "second one here would be a second clock")
    assert "update(" not in src, (
        "the host defines a Phaser update() tick — that is a per-frame hook "
        "advancing state, which is exactly what C7 forbids")
    # and it must not invent a scenario step
    for banned in ("office_event", "agent_id", "fetch("):
        assert banned not in src, (
            f"the Phaser host references {banned}")


def test_phaser_is_alias_resolved_because_the_source_is_outside_the_package():
    """The plugin's source lives outside qubit-web/, so a bare `phaser`
    specifier cannot resolve from where the file is. An earlier build failed
    with UNRESOLVED_IMPORT for exactly this reason."""
    cfg = CONFIG.read_text()
    assert "resolve" in cfg and "phaser" in cfg, (
        "the build config does not alias phaser; the plugin source is outside "
        "the package directory and the import will not resolve")


def test_the_built_artifact_actually_wires_the_keyboard_and_navigation(tmp_path):
    """Modules can be present in a bundle and STILL be dead — imported but
    never bound. That is the F02 bug in its general form, and only the built
    artifact can answer it.

    The binding is checked by its SHAPE, not by the presence of the string
    `keydown`: a source that only *removes* a listener still contains that
    string, so a naive check passes on code that binds nothing. Falsified by
    deleting the `addEventListener("keydown", ...)` call — the assertion must
    fail.
    """
    out = tmp_path / "dist"
    assert _build(out).returncode == 0
    src = (out / "qubit-office.game.js").read_text()
    add = re.search(r'addEventListener\(\s*"keydown"\s*,', src)
    assert add, (
        "the built artifact never ADDS a keydown listener: the input router and "
        "the navigation reducer are bundled but never connected to a keyboard")
    rem = re.search(r'removeEventListener\(\s*"keydown"\s*,', src)
    assert rem, (
        "the artifact adds a keydown listener but never removes it — the "
        "StrictMode remount would stack handlers")
    for marker in ("passThrough", "handledBy"):
        assert marker in src, (
            f"the artifact does not contain the input router's {marker} — the "
            f"router was tree-shaken out or never imported")
    for marker in ("viewpoint", "refused"):
        assert marker in src, (
            f"the artifact does not contain the navigation reducer's {marker}")


# ======================================================================
# 3 — the lifecycle, driven for real
# ======================================================================
_DRIVER = r"""
/* Drive the built CanvasSceneHost through the SceneHost contract with a
 * minimal DOM, then report what it did. This is the lifecycle proof: bind on
 * mount, release on destroy, idempotent destroy. */
const listeners = [];
function FakeCanvas() {
  this.style = {};
  this.width = 880; this.height = 500;
  this.parentElement = null;
  this.getContext = () => ({
    save(){}, restore(){}, fillRect(){}, clearRect(){}, beginPath(){},
    moveTo(){}, lineTo(){}, stroke(){}, fill(){}, arc(){}, rect(){},
    setTransform(){}, scale(){}, translate(){}, closePath(){},
    drawImage(){}, measureText(){ return {width: 1}; },
    get fillStyle(){ return "#000"; }, set fillStyle(v){},
    get strokeStyle(){ return "#000"; }, set strokeStyle(v){},
    get lineWidth(){ return 1; }, set lineWidth(v){},
    get font(){ return ""; }, set font(v){},
    get textAlign(){ return ""; }, set textAlign(v){},
    get globalAlpha(){ return 1; }, set globalAlpha(v){},
  });
  this.addEventListener = (t, fn) => { listeners.push([this, t, fn]); };
  this.removeEventListener = (t, fn) => {
    const i = listeners.findIndex(([e,tt,f]) => e===this && tt===t && f===fn);
    if (i >= 0) listeners.splice(i, 1);
  };
  this.getBoundingClientRect = () => ({left:0, top:0, width:880, height:500});
  this.setPointerCapture = () => {};
}
globalThis.window = globalThis.window || {};
const cv = new FakeCanvas();
const host = createCanvasHost();
const drawn = [];
host.mount(cv, cv);
const afterMount = host.listenerCount();
host.step({revision: 1, draw: () => drawn.push(1)}, {cx:0, cy:0, zoom:1});
host.destroy();
const afterDestroy = host.listenerCount();
host.destroy();  /* idempotent: must not throw or double-release */
const afterDouble = host.listenerCount();
const out = {afterMount, afterDestroy, afterDouble, drawn: drawn.length,
             liveListeners: listeners.length};
console.log(JSON.stringify(out));
"""


def test_the_scene_host_lifecycle_is_clean(tmp_path):
    """Mount binds, destroy releases everything, destroy is idempotent.

    The build artifact is an IIFE for a browser, so the probe compiles the
    SOURCE host with esbuild through vite's own transform instead of executing
    the browser bundle in node.
    """
    out = tmp_path / "dist"
    assert _build(out).returncode == 0

    # Compile just the Canvas host to CJS so node can require it.
    tsc = WEB / "node_modules" / "typescript" / "bin" / "tsc"
    staged = tmp_path / "src"
    staged.mkdir()
    for name in ("SceneHost.ts", "CanvasSceneHost.ts"):
        shutil.copy(GAME_DIR / name, staged / name)
    proc = subprocess.run(
        [NODE, str(tsc), "--module", "commonjs", "--target", "es2020",
         "--moduleResolution", "node", "--outDir", str(tmp_path / "js"),
         "--skipLibCheck", "--noEmitOnError", "false",
         str(staged / "CanvasSceneHost.ts")],
        capture_output=True, text=True, timeout=180, cwd=str(staged))
    compiled = tmp_path / "js" / "CanvasSceneHost.js"
    if not compiled.is_file():
        pytest.skip(f"tsc could not compile the host for the node driver: "
                    f"{proc.stderr[:400]}")

    program = (
        'const {createCanvasHost} = require(%s);\n%s'
        % (json.dumps(str(compiled)), _DRIVER))
    run = subprocess.run([NODE, "-e", program], capture_output=True, text=True,
                         timeout=60)
    assert run.returncode == 0, (
        f"the lifecycle driver failed\nstdout: {run.stdout}\n"
        f"stderr: {run.stderr}")
    got = json.loads(run.stdout)

    assert got["afterMount"] == 6, (
        f"expected 6 listeners after mount "
        f"(pointerdown/move/up/cancel, wheel, click), got {got['afterMount']}")
    assert got["afterDestroy"] == 0, (
        f"destroy() left {got['afterDestroy']} listeners bound — the memory "
        f"and input leak that makes StrictMode remounts stack engines")
    assert got["afterDouble"] == 0, "destroy() is not idempotent"
    assert got["liveListeners"] == 0, (
        f"the fake canvas still holds {got['liveListeners']} listeners: "
        f"removeEventListener was not called with the same function identity")
    assert got["drawn"] == 1, (
        f"step() drew {got['drawn']} times; exactly one draw was requested")


def test_a_mount_after_destroy_rebinds_from_scratch(tmp_path):
    """StrictMode mounts, unmounts, remounts. The second mount must bind the
    full set again, not half of it."""
    out = tmp_path / "dist"
    assert _build(out).returncode == 0
    # The driver above already proves destroy-then-count; this asserts the
    # second mount reaches the same count, which is the remount half.
    driver = _DRIVER.replace(
        "const afterDouble = host.listenerCount();",
        "const afterDouble = host.listenerCount();\n"
        "const h2 = createCanvasHost(); h2.mount(cv, cv);\n"
        "const remount = h2.listenerCount(); h2.destroy();\n"
        "const afterRemountDestroy = h2.listenerCount();")
    driver = driver.replace(
        "liveListeners: listeners.length};",
        "liveListeners: listeners.length, remount, afterRemountDestroy};")
    tsc = WEB / "node_modules" / "typescript" / "bin" / "tsc"
    staged = tmp_path / "src"
    staged.mkdir(exist_ok=True)
    for name in ("SceneHost.ts", "CanvasSceneHost.ts"):
        shutil.copy(GAME_DIR / name, staged / name)
    subprocess.run(
        [NODE, str(tsc), "--module", "commonjs", "--target", "es2020",
         "--moduleResolution", "node", "--outDir", str(tmp_path / "js"),
         "--skipLibCheck", "--noEmitOnError", "false",
         str(staged / "CanvasSceneHost.ts")],
        capture_output=True, text=True, timeout=180, cwd=str(staged))
    compiled = tmp_path / "js" / "CanvasSceneHost.js"
    if not compiled.is_file():
        pytest.skip("tsc could not compile the host for the node driver")
    run = subprocess.run(
        [NODE, "-e",
         'const {createCanvasHost} = require(%s);\n%s'
         % (json.dumps(str(compiled)), driver)],
        capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr[:600]
    got = json.loads(run.stdout)
    assert got["remount"] == 6, (
        f"a remount bound {got['remount']} listeners instead of 6 — StrictMode "
        f"would leave the surface half-wired")
    assert got["afterRemountDestroy"] == 0
