"""Packet 8U release/packaging/rebrand gates.

Machine-checked gates over the release surface, hermetic (no network, no
build, no install):

1. pyproject ships the built dashboard (frontend/hermes-dist) in both the
   wheel and the sdist artifacts, force-includes the frontend tree, and the
   dashboard source (frontend/hermes-web) is present;
2. the dashboard source declares the build/lint/test toolchain it actually
   imports in eslint.config.js and needs for vitest — every tool is a
   declared devDependency (never an undeclared ``--no-save`` install);
3. the reproducible build script and the isolated-install verification script
   exist, are executable, and carry the root-base ``--base=/`` / ``--outDir``
   build contract;
4. the Qubit overlay plugin manifests are complete, their entry bundles
   exist, and their labels carry no upstream product identity;
5. the served ``/chat`` dashboard is Qubit-branded and carries the injected
   session token + API prefix;
6. the generated shell completions carry Qubit identity only.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

WEB = REPO / "frontend" / "hermes-web"
DIST = REPO / "frontend" / "hermes-dist"
COMPLETIONS = REPO / "voicebridge" / "_data" / "completions"

needs_dist = pytest.mark.skipif(
    not (DIST / "index.html").is_file(),
    reason="hermes-dist not built (run scripts/build_dashboard.sh)")

# Tools the dashboard lint/test toolchain actually needs. ``eslint/config`` is
# part of the ``eslint`` package; ``jsdom`` is the environment requested by the
# ``@vitest-environment jsdom`` suites under src/.
REQUIRED_TOOLS = [
    "jsdom", "eslint", "@eslint/js", "globals",
    "eslint-plugin-react-hooks", "typescript-eslint",
]

# Node builtins are imported by the vite/vitest configs but are not npm
# dependencies; never require them to be declared.
NODE_BUILTINS = {
    "assert", "buffer", "child_process", "crypto", "events", "fs", "http",
    "https", "net", "os", "path", "process", "stream", "url", "util", "zlib",
}


def _pyproject() -> dict:
    return tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))


def _package_json() -> dict:
    return json.loads((WEB / "package.json").read_text(encoding="utf-8"))


def _declared_names(pkg: dict) -> set:
    return set(pkg.get("dependencies", {})) | set(pkg.get("devDependencies", {}))


def _import_specifiers(path: Path) -> list:
    """Bare npm specifiers imported by a JS/TS config (subpaths collapsed)."""
    text = path.read_text(encoding="utf-8")
    # strip comments so prose like "from 'warn'" is never read as an import
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    out = []
    for m in re.finditer(r"""(?:from\s+|import\s*)['"]([^'"]+)['"]""", text):
        raw = m.group(1)
        if raw.startswith((".", "/", "node:")):
            continue
        parts = raw.split("/")
        name = "/".join(parts[:2]) if raw.startswith("@") else parts[0]
        if name not in NODE_BUILTINS:
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# 1. pyproject packaging declarations + dashboard source tree
# ---------------------------------------------------------------------------

def test_pyproject_ships_dashboard_in_wheel_and_sdist():
    data = _pyproject()
    wheel = data["tool"]["hatch"]["build"]["targets"]["wheel"]
    sdist = data["tool"]["hatch"]["build"]["targets"]["sdist"]
    assert "frontend/hermes-dist" in wheel.get("artifacts", []), wheel
    assert "frontend/hermes-dist" in sdist.get("artifacts", []), sdist
    # The frontend tree is force-included under voicebridge/_data/frontend,
    # either as a direct directory mapping or via the filtered custom build hook
    # (scripts/hatch_build_qubit.py) that prunes the git-ignored dev trees
    # (node_modules) a raw directory mapping would sweep into the wheel.
    force = wheel.get("force-include", {})
    if "frontend" in force:
        assert force["frontend"].endswith("_data/frontend"), force
    else:
        hook = wheel.get("hooks", {}).get("custom", {})
        hook_path = str(hook.get("path", ""))
        assert hook_path and (REPO / hook_path).is_file(), wheel
        text = (REPO / hook_path).read_text(encoding="utf-8",
                                            errors="replace")
        assert "voicebridge/_data/frontend/" in text, text


def test_dashboard_source_tree_present():
    for rel in ("package.json", "vite.config.ts", "eslint.config.js",
                "vitest.config.ts", "src/App.tsx"):
        assert (WEB / rel).is_file(), f"missing dashboard source {rel}"


# ---------------------------------------------------------------------------
# 2. declared dashboard toolchain (no --no-save hack)
# ---------------------------------------------------------------------------

def test_dashboard_toolchain_declared_and_imported():
    pkg = _package_json()
    dev = pkg.get("devDependencies", {})
    declared = _declared_names(pkg)
    for tool in REQUIRED_TOOLS:
        assert tool in dev, f"{tool} must be a devDependency of hermes-web"
        assert tool in declared, tool
    # every bare import in the lint/build/test configs must be declared
    for cfg in ("eslint.config.js", "vite.config.ts", "vitest.config.ts"):
        for spec in _import_specifiers(WEB / cfg):
            assert spec in declared, f"{cfg} imports undeclared package {spec!r}"


def test_dashboard_tools_are_locked_not_no_save_installs():
    lock_path = WEB / "package-lock.json"
    if not lock_path.is_file():
        pytest.skip("package-lock.json absent; devDependency check is authoritative")
    packages = json.loads(lock_path.read_text(encoding="utf-8")).get("packages", {})
    for tool in REQUIRED_TOOLS:
        assert f"node_modules/{tool}" in packages, (
            f"{tool} is installed only via a --no-save hack "
            f"(absent from package-lock.json)")


# ---------------------------------------------------------------------------
# 3. build + isolated-install verification scripts
# ---------------------------------------------------------------------------

def test_build_and_verify_scripts_present_executable():
    build = REPO / "scripts" / "build_dashboard.sh"
    verify = REPO / "scripts" / "verify_installed_dashboard.sh"
    assert build.is_file(), build
    assert os.access(build, os.X_OK), f"{build} is not executable"
    assert verify.is_file(), verify
    assert os.access(verify, os.X_OK), f"{verify} is not executable"


def test_build_script_carries_build_contract():
    text = (REPO / "scripts" / "build_dashboard.sh").read_text(encoding="utf-8")
    assert "vite build --base=/" in text, text
    assert "--outDir" in text, text


# ---------------------------------------------------------------------------
# 4. overlay plugin manifests — complete and debranded
# ---------------------------------------------------------------------------

def test_overlay_manifests_complete_and_debranded():
    from voicebridge import hermes_compat as HC
    manifests = HC._overlay_manifests()
    assert manifests, "no overlay plugin manifests found"
    plugdir = HC.OVERLAY_DIR / "plugins"
    for m in manifests:
        for field in ("name", "label", "entry"):
            assert m.get(field), (m.get("name"), field)
        assert (m.get("tab") or {}).get("path"), m
        entry = plugdir / str(m["name"]) / str(m["entry"])
        assert entry.is_file(), f"plugin entry missing: {entry}"
        low = str(m["label"]).lower()
        for bad in ("hermes", "openclaw"):
            assert bad not in low, f"{m['name']} label leaks upstream {bad!r}"


# ---------------------------------------------------------------------------
# 5. served dashboard product text (real WebUI boot)
# ---------------------------------------------------------------------------

def _boot(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\n"
                 "webui:\n  token: test-token\n", encoding="utf-8")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui, ui._server.server_address[1]


def _get(port, path, token="test-token"):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path), method="GET")
    if token:
        req.add_header("X-Api-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@needs_dist
def test_served_dashboard_is_qubit_branded_with_injection(tmp_path):
    ui, port = _boot(tmp_path)
    try:
        status, body = _get(port, "/chat")
        assert status == 200, status
        text = body.decode("utf-8", "replace")
        title = re.search(r"<title>(.*?)</title>", text, flags=re.S)
        assert title is not None, "served dashboard has no <title>"
        assert "Qubit" in title.group(1), title.group(1)
        assert "__HERMES_SESSION_TOKEN__" in text, "session token not injected"
        assert "test-token" in text, "loopback token value not injected"
        assert "__HERMES_BASE_PATH__" in text, "base path not injected"
        assert "__QUBIT_API_PREFIX__" in text, "api prefix not injected"
        assert "/agent" in text, "api prefix value not injected"
    finally:
        ui.stop()


# ---------------------------------------------------------------------------
# 6. generated shell completions — Qubit identity only
# ---------------------------------------------------------------------------

def test_completions_are_qubit_branded_only():
    assert COMPLETIONS.is_dir(), COMPLETIONS
    files = sorted(p for p in COMPLETIONS.iterdir() if p.is_file())
    assert files, "no completion files found"
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "Qubit Agent" in text, f"{path.name}: missing Qubit header"
        low = text.lower()
        assert "hermes" not in low and "openclaw" not in low, (
            f"{path.name}: upstream identity in generated completions")
