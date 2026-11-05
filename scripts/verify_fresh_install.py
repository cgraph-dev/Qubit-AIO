#!/usr/bin/env python3
"""Fresh-install verifier: wheel -> dashboard + packaged chat TUI.

This gate answers one narrow question that the broader release qualifier does
not: *can a wheel installed outside any source checkout serve the dashboard and
start the bundled chat TUI without trying to rebuild Node dependencies from a
tree that the wheel intentionally omits?*

It installs a supplied wheel (or builds one) into a throwaway venv under a
throwaway HOME/QUBIT_HOME, starts the dashboard on an ephemeral port (never
8890 and never the running service), and asserts:

* the installed dashboard assets exist and ``/chat`` serves the SPA;
* the packaged ``ui-tui/dist/entry.js`` is selected and runs (no
  ``scripts/build/node-deps.mjs`` rebuild from a missing source tree);
* the installed tree contains no operator-home or source-checkout path leaks;
* ``/api/health`` and the version identity agree with the wheel.

Usage::

    # build from this checkout and verify the resulting wheel
    python scripts/verify_fresh_install.py --build

    # verify an already-built release wheel (no build)
    python scripts/verify_fresh_install.py --wheel dist/qubit_voicebridge-*.whl

    # offline/minimal: no dependency resolution; the venv sees the base
    # interpreter's packages (use only when they already satisfy the wheel)
    python scripts/verify_fresh_install.py --wheel ... --no-deps

    # hermetic preflight only (no build, install, or start)
    python scripts/verify_fresh_install.py --check --wheel ...

Typed exit codes (never a silent pass):
  0 PASS      the installed artifact passed every probe
  2 FAIL      a real install / dashboard / TUI / isolation defect
  4 DEGRADED  a prerequisite (wheel builder, dashboard bundle, network) is
              unavailable, so the gate cannot honestly run

The live service (including port 8890), the operator's real HOME/config/state,
production repositories, and other worktrees are never touched.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import clean_install_rehearsal as R  # noqa: E402

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_DEGRADED = 4

SCHEMA = "qubit.fresh-install.v1"
LIVE_DASHBOARD_PORT = 8890
PROBE_MARKER = "FRESH_INSTALL_PROBE "

# Text-ish files that can carry a path; binaries are skipped so a compiled
# bundle is not scanned byte-for-byte.
_LEAK_SUFFIXES = frozenset({
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json",
    ".yaml", ".yml", ".toml", ".txt", ".md", ".cfg", ".ini", ".sh", ".ps1",
    ".html", ".css", ".map",
})

# Markers in the TUI startup output that mean it attempted a source rebuild or
# could not resolve its bundled entry point.
_SOURCE_BUILD_MARKERS = (
    "node-deps",
    "npm err",
    "scripts/build/node-deps.mjs",
    "scripts/build/tui.mjs",
    "cannot find module",
    "err_module_not_found",
)


def parse_wheel_version(name: str) -> Optional[str]:
    """Return the version segment of a wheel filename, or None.

    ``qubit_voicebridge-0.0.5-py3-none-any.whl`` -> ``0.0.5``.
    """
    stem = Path(str(name)).name
    if not stem.endswith(".whl"):
        return None
    parts = stem[:-4].split("-")
    if len(parts) < 5:
        return None
    return parts[1] or None


def parse_probe_output(text: str) -> Optional[Dict[str, Any]]:
    """Return the last ``FRESH_INSTALL_PROBE <json>`` payload, or None."""
    payload: Optional[Dict[str, Any]] = None
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith(PROBE_MARKER):
            continue
        try:
            value = json.loads(line[len(PROBE_MARKER):])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            payload = value
    return payload


def _check(checks: List[Dict[str, str]], failures: List[str],
           name: str, ok: bool, detail: object = "") -> None:
    checks.append({"name": name, "state": "OK" if ok else "FAIL",
                   "detail": str(detail)[:400]})
    if not ok:
        failures.append(f"{name}: {detail}")


def version_identity(wheel_version: Optional[str],
                     distribution_version: Optional[str],
                     cli_line: str,
                     health: Dict[str, Any]) -> Dict[str, Any]:
    """Compare wheel filename, installed metadata, CLI line and health payload."""
    problems: List[str] = []
    health_version = str((health or {}).get("version") or "")
    if not distribution_version:
        problems.append("installed distribution metadata has no version")
    if wheel_version and distribution_version and wheel_version != distribution_version:
        problems.append(
            f"wheel filename version {wheel_version} != installed {distribution_version}")
    if distribution_version and distribution_version not in (cli_line or ""):
        problems.append(
            f"qubit --version does not name {distribution_version}: {cli_line!r}")
    if distribution_version and distribution_version not in health_version:
        problems.append(
            f"/api/health version does not name {distribution_version}: "
            f"{health_version!r}")
    if (health or {}).get("ok") is not True:
        problems.append(f"/api/health did not report ok=true: {health}")
    if not (health or {}).get("state"):
        problems.append(f"/api/health did not report a state: {health}")
    return {
        "wheel": wheel_version,
        "installed": distribution_version,
        "cli": cli_line,
        "health": health_version,
        "problems": problems,
    }


def leak_scan(root: Path, needles: List[str]) -> List[Dict[str, str]]:
    """Find operator-home or checkout path literals in installed text files."""
    root = Path(root)
    wanted = [("path", needle.encode("utf-8", "surrogateescape"))
              for needle in needles if needle]
    hits: List[Dict[str, str]] = []
    if not root.exists():
        return [{"path": str(root), "needle": "<root>", "reason": "not found"}]
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _LEAK_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 8 * 1024 * 1024:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        for label, needle in wanted:
            if needle in data:
                hits.append({
                    "path": str(path.relative_to(root)),
                    "needle": needle.decode("utf-8", "replace"),
                    "reason": label,
                })
    return hits


def evaluate_probe(probe: Optional[Dict[str, Any]], *,
                   wheel_version: Optional[str],
                   cli_line: str,
                   expected_plugins: Optional[List[str]] = None) -> Dict[str, Any]:
    """Turn a probe payload into named checks, reasons and version identity."""
    checks: List[Dict[str, str]] = []
    failures: List[str] = []
    probe = probe or {}

    if not probe:
        return {
            "checks": checks,
            "reasons": ["installed probe produced no FRESH_INSTALL_PROBE payload"],
            "version_identity": {},
        }

    installed = probe.get("installed") or {}
    distribution_version = str(installed.get("distribution_version") or "")
    _check(checks, failures, "installed.voicebridge",
           bool(installed.get("voicebridge")), installed.get("voicebridge"))
    _check(checks, failures, "installed.runtime_under_venv",
           bool(installed.get("runtime_root")), installed.get("runtime_root"))

    dashboard = probe.get("dashboard") or {}
    port = dashboard.get("port")
    _check(checks, failures, "dashboard.ephemeral_port",
           isinstance(port, int) and port > 0 and port != LIVE_DASHBOARD_PORT,
           f"port={port}")
    _check(checks, failures, "dashboard.root_redirect",
           dashboard.get("redirect") in ("/chat", "/sessions"),
           dashboard.get("redirect"))
    _check(checks, failures, "dashboard.chat_status",
           dashboard.get("chat_status") == 200, dashboard.get("chat_status"))
    _check(checks, failures, "dashboard.spa_shell",
           bool(dashboard.get("spa_shell")), dashboard.get("spa_shell"))
    _check(checks, failures, "dashboard.asset",
           bool(dashboard.get("asset")) and dashboard.get("asset_status") == 200
           and int(dashboard.get("asset_bytes") or 0) > 1000,
           f"{dashboard.get('asset')} status={dashboard.get('asset_status')} "
           f"bytes={dashboard.get('asset_bytes')}")
    plugins = dashboard.get("plugins") or []
    missing = [p for p in (expected_plugins or []) if p not in plugins]
    _check(checks, failures, "dashboard.plugins", not missing,
           f"missing={missing}")

    health = probe.get("health") or {}
    _check(checks, failures, "health.status", health.get("status") == 200,
           health.get("status"))

    tui = probe.get("tui") or {}
    _check(checks, failures, "tui.entry_present",
           bool(tui.get("entry")), tui.get("entry"))
    _check(checks, failures, "tui.entry_under_install",
           bool(tui.get("entry_under_venv")), tui.get("entry"))
    _check(checks, failures, "tui.no_source_checkout",
           tui.get("source_git") is False, tui.get("source_git"))
    _check(checks, failures, "tui.packaged_argv",
           tui.get("packaged_argv") is True, tui.get("argv"))
    _check(checks, failures, "tui.launch",
           tui.get("launch_rc") == 0,
           f"rc={tui.get('launch_rc')} out={(tui.get('launch_output') or '')[-300:]}")
    output = str(tui.get("launch_output") or "")
    lowered = output.lower()
    marker_hits = [m for m in _SOURCE_BUILD_MARKERS if m in lowered]
    _check(checks, failures, "tui.no_source_rebuild",
           not marker_hits, f"markers={marker_hits}")
    _check(checks, failures, "tui.bundle_loaded",
           "no tty" in lowered, output[-300:])

    identity = version_identity(
        wheel_version, distribution_version or None, cli_line, health)
    for problem in identity["problems"]:
        failures.append(f"version: {problem}")
    _check(checks, failures, "version.identity", not identity["problems"],
           "; ".join(identity["problems"]))

    return {"checks": checks, "reasons": failures,
            "version_identity": identity}


def _install_commands(work_root: Path, python: str, *,
                      no_deps: bool) -> Dict[str, Any]:
    """Pick the wheel builder/installer commands for this host."""
    venv = work_root / "venv"
    if shutil.which("uv"):
        venv_cmd = ["uv", "venv", "--quiet", "--python", python, str(venv)]
        if no_deps:
            venv_cmd.append("--system-site-packages")
        install_cmd = ["uv", "pip", "install", "--python", str(venv / "bin/python")]
        if no_deps:
            install_cmd.append("--no-deps")
        return {
            "name": "uv",
            "venv": venv_cmd,
            "install": install_cmd,
            "build": ["uv", "build", "--wheel", "--out-dir", "{}"],
        }
    if R._has_module("build") and R._has_module("venv"):
        venv_cmd = [python, "-m", "venv"]
        if no_deps:
            venv_cmd.append("--system-site-packages")
        venv_cmd.append(str(venv))
        return {
            "name": "python -m build",
            "venv": venv_cmd,
            "install": [str(venv / "bin/python"), "-m", "pip", "install"]
                       + (["--no-deps"] if no_deps else []),
            "build": [python, "-m", "build", "--wheel", "--out-dir", "{}"],
        }
    return {}


def check(repo: Optional[Path] = None,
          work_root: Optional[Path] = None, *,
          wheel: Optional[Path] = None,
          port: int = 0) -> Dict[str, Any]:
    """Hermetic preflight. Builds, installs, starts, and writes nothing."""
    repo = Path(repo or REPO).resolve()
    plan = R.build_plan(repo, work_root)
    findings = R.isolation_findings(plan)
    reasons: List[str] = []
    state = "OK"

    if port == LIVE_DASHBOARD_PORT:
        state = "FAIL"
        reasons.append(f"port {LIVE_DASHBOARD_PORT} is the live dashboard; "
                       "use an ephemeral/separate port")
    if findings:
        state = "FAIL"
        reasons.append(f"verifier is not isolated: {findings}")

    scripts = R.entry_points(repo)
    missing = [name for name in R.EXPECTED_ENTRY_POINTS if name not in scripts]
    if missing:
        state = "FAIL"
        reasons.append(f"pyproject.toml is missing entry points: {missing}")

    wheel_info: Dict[str, Any] = {}
    wheel_present = False
    if wheel is not None:
        wheel_path = Path(wheel).expanduser().resolve()
        wheel_present = wheel_path.is_file() and wheel_path.suffix == ".whl"
        wheel_info = {"path": str(wheel_path), "present": wheel_present}
        if wheel_present:
            try:
                members = R._wheel_members(wheel_path)
            except Exception as exc:  # noqa: BLE001 - readable failure
                wheel_present = False
                wheel_info["error"] = str(exc)[:200]
            else:
                wheel_info["version"] = parse_wheel_version(wheel_path.name)
                wheel_info["dashboard"] = R._wheel_has_dashboard(members)
                wheel_info["node_modules_members"] = sum(
                    1 for m in members if "node_modules/" in m.replace("\\", "/"))
                if not wheel_info["dashboard"]:
                    state = "FAIL"
                    reasons.append("provided wheel is dashboard-less "
                                   "(frontend/qubit-dist/index.html is not shipped)")
                if wheel_info["node_modules_members"]:
                    state = "FAIL"
                    reasons.append(
                        f"provided wheel swept {wheel_info['node_modules_members']} "
                        "build-only node_modules members")
        else:
            state = "FAIL"
            reasons.append(f"provided wheel is not a readable .whl: {wheel_path}")
    else:
        bundle = Path(plan["dashboard_bundle"])
        wheel_info["dashboard_bundle_present"] = bundle.is_file()
        if not bundle.is_file():
            if state == "OK":
                state = "DEGRADED"
            reasons.append(
                f"dashboard bundle {bundle} is absent; build it out of band with "
                "scripts/build_dashboard.sh, or pass --wheel")

    return {
        "state": state,
        "verdict": {"OK": "PASS", "FAIL": "FAIL", "DEGRADED": "DEGRADED"}[state],
        "mode": "check",
        "plan": plan,
        "entry_points": scripts,
        "missing_entry_points": missing,
        "wheel": wheel_info,
        "port": port,
        "isolation_findings": findings,
        "reasons": reasons,
    }


_PROBE = r'''
import http.client, importlib.metadata, json, os, pathlib, re, subprocess, sys, tempfile

# Isolation guard: the installed artifact must never read or write a live
# QUBIT_HOME/HERMES_HOME. The verifier passes those paths in VB_FORBIDDEN_HOMES.
def _under(path, root):
    try:
        p = pathlib.Path(path).expanduser().resolve(strict=False)
        r = pathlib.Path(root).expanduser().resolve(strict=False)
    except OSError:
        return False
    return p == r or r in p.parents

_forbidden = [p for p in (json.loads(os.environ.get("VB_FORBIDDEN_HOMES", "[]") or "[]")) if p]

def _guard(event, args):
    if event in ("open", "os.mkdir", "os.rename", "os.remove", "os.rmdir",
                 "os.symlink", "os.link", "os.truncate"):
        for arg in args:
            if isinstance(arg, (str, bytes, os.PathLike)):
                try:
                    path = os.fsdecode(arg)
                except Exception:
                    continue
                if any(_under(path, root) for root in _forbidden):
                    raise RuntimeError("fresh-install verifier touched a live home: " + path)

sys.addaudithook(_guard)

venv = pathlib.Path(os.environ["VB_INSTALL_VENV"]).resolve()
import voicebridge
pkg = pathlib.Path(voicebridge.__file__).resolve()
assert venv in pkg.parents, f"voicebridge not imported from the install: {pkg}"
distribution_version = importlib.metadata.version("qubit-voicebridge")

from voicebridge.config import Config
from voicebridge.webui import WebUI
import yaml
state = pathlib.Path(tempfile.mkdtemp(dir=os.environ["VB_PROBE_STATE"]))
p = state / "voice.yaml"
p.write_text(yaml.safe_dump({"logging": {"jsonl_path": str(state)},
                             "webui": {"token": "fresh-install-token"}}))
cfg = Config.load(p)
class _F:
    _route = "local"; _web_active = 0
    task = type("T", (), {"active": False})()
    def __init__(self): self.cfg = cfg; self._t0 = __import__("time").time()

requested_port = int(os.environ.get("VB_PORT", "0") or "0")
ui = WebUI(_F(), host="127.0.0.1", port=requested_port, state_dir=state,
           asr_factory=lambda: None)
ui.start()
port = ui._server.server_address[1]


def raw(path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    return r.status, r.getheader("Location"), r.read()


try:
    st_root, loc, _ = raw("/")
    st_health, _, hb = raw("/api/health")
    health = json.loads(hb)
    st_chat, _, body = raw("/chat")
    asset = re.search(rb"/(assets/[^\"]+\.js)", body)
    asset_name = asset.group(1).decode() if asset else ""
    st_asset, _, asset_body = raw("/" + asset_name) if asset_name else (0, None, b"")
    st_plugins, _, pb = raw("/agent/api/dashboard/plugins",
                            {"X-Qubit-Session-Token": "fresh-install-token"})
    try:
        plugin_names = [m["name"] for m in json.loads(pb)]
    except Exception:
        plugin_names = []
finally:
    ui.stop()

# ------------------------------------------------------------------ TUI
from voicebridge import qubit_runtime as hr
hr_status = hr.status()
assert hr_status.get("present") is True and hr_status.get("importable") is True, hr_status
hr.ensure_path()
runtime_root = pathlib.Path(hr.runtime_root()).resolve()
assert venv in runtime_root.parents, (runtime_root, venv)
import qubit_cli.main_tui_launch as launch

tui_dir = runtime_root / "ui-tui"
entry = (tui_dir / "dist" / "entry.js")
assert entry.is_file(), f"packaged TUI entry missing: {entry}"

def _boom(*args, **kwargs):
    raise AssertionError("the packaged TUI tried to rebuild from source")

launch._ensure_tui_workspace = _boom
launch._tui_need_rebuild = _boom
import qubit_cli.source_build as source_build
source_build.prepare_launch_dependencies = _boom
source_build.build_source_tui = _boom

node = os.environ["VB_NODE"]
os.environ["HERMES_NODE"] = node
argv, cwd = launch._make_tui_argv(tui_dir, tui_dev=False)
packaged_argv = bool(argv) and argv[0] == node and argv[-1] == str(entry) and cwd == tui_dir

# The entry exits 0 with "no TTY" when stdin is not a terminal, so this proves
# the bundle loads without starting a gateway or a model turn.
proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True,
                      timeout=120, stdin=subprocess.DEVNULL)
launch_output = ((proc.stdout or "") + (proc.stderr or "")).strip()

print("FRESH_INSTALL_PROBE " + json.dumps({
    "installed": {
        "voicebridge": str(pkg),
        "runtime_root": str(runtime_root),
        "distribution_version": distribution_version,
    },
    "health": {**health, "status": st_health},
    "dashboard": {
        "port": port,
        "redirect": loc,
        "chat_status": st_chat,
        "spa_shell": b"__QUBIT_BASE_PATH__" in body,
        "asset": asset_name,
        "asset_status": st_asset,
        "asset_bytes": len(asset_body),
        "plugins": plugin_names,
        "plugins_status": st_plugins,
    },
    "tui": {
        "entry": str(entry),
        "entry_under_venv": venv in entry.parents,
        "source_git": (runtime_root / ".git").exists(),
        "argv": argv,
        "cwd": str(cwd),
        "packaged_argv": packaged_argv,
        "launch_rc": proc.returncode,
        "launch_output": launch_output[:2000],
    },
}, sort_keys=True), flush=True)
'''


def _python_package_root(venv: Path, env: Optional[Dict[str, str]] = None) -> Optional[Path]:
    probe = ("import pathlib, voicebridge; "
             "print(pathlib.Path(voicebridge.__file__).resolve().parent)")
    py = venv / "bin" / "python"
    if not py.exists():
        return None
    proc = subprocess.run([str(py), "-I", "-c", probe], capture_output=True,
                          text=True, timeout=120, env=env)
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip())


def run(repo: Optional[Path] = None, *, work_root: Optional[Path] = None,
        wheel: Optional[Path] = None, port: int = 0, build: bool = False,
        no_deps: bool = False, python: Optional[str] = None,
        require_network: bool = False,
        require_clean: bool = False) -> Dict[str, Any]:
    """Perform the real wheel -> install -> dashboard/TUI verification."""
    repo = Path(repo or REPO).resolve()
    plan = R.build_plan(repo, work_root)
    result: Dict[str, Any] = {
        "state": "OK", "verdict": "PASS", "mode": "run", "plan": plan,
        "steps": [], "reasons": [], "checks": [], "warnings": [],
        "port": port, "no_deps": bool(no_deps),
        "wheel": None, "wheel_path": None, "wheel_sha256": None,
        "wheel_source": None, "wheel_version": None, "version_identity": {},
        "isolation": {}, "leaks": [], "tui": {}, "dashboard": {},
    }

    def fail(reason: str) -> Dict[str, Any]:
        result["state"] = "FAIL"
        result["reasons"].append(reason)
        return R._seal(result)

    def degrade(reason: str) -> Dict[str, Any]:
        if result["state"] == "OK":
            result["state"] = "DEGRADED"
        result["reasons"].append(reason)
        return R._seal(result)

    def check(name: str, ok: bool, detail: object = "") -> None:
        result["checks"].append(
            {"name": name, "state": "OK" if ok else "FAIL",
             "detail": str(detail)[:400]})
        if not ok:
            result["reasons"].append(f"{name}: {detail}")

    if port == LIVE_DASHBOARD_PORT:
        return fail(f"port {LIVE_DASHBOARD_PORT} is the live dashboard; "
                    "use an ephemeral/separate port")

    findings = R.isolation_findings(plan)
    live = R._live_home_paths(plan)
    before_homes = {str(p): R._home_snapshot(p) for p in live}
    result["isolation"] = {"findings": findings, "live_homes": before_homes}
    if findings:
        return fail(f"verifier not isolated: {findings}")

    wheel_path: Optional[Path] = None
    if wheel is not None:
        candidate = Path(wheel).expanduser().resolve()
        if not candidate.is_file() or candidate.suffix != ".whl":
            return fail(f"provided wheel is not a readable .whl: {candidate}")
        wheel_path = candidate
        result["wheel_source"] = "provided"
    else:
        result["wheel_source"] = "built"

    toolchain = _install_commands(Path(plan["work_root"]),
                                  python or sys.executable, no_deps=no_deps)
    if not toolchain:
        return degrade("no wheel builder available (install 'uv' or the 'build' module)")
    result["toolchain"] = toolchain["name"]

    if wheel_path is None and not Path(plan["dashboard_bundle"]).is_file():
        return degrade(
            f"dashboard bundle {plan['dashboard_bundle']} is absent; build it "
            "out of band with scripts/build_dashboard.sh, or pass --wheel")

    work = Path(plan["work_root"])
    for sub in ("home", "dist", "state", "models", "probe-state"):
        (work / sub).mkdir(parents=True, exist_ok=True)
    env = R._rehearsal_env(plan)

    tree_status = R._git_tracked_status(repo)
    result["tree"] = {
        "state": ("UNKNOWN" if tree_status is None else
                  ("DIRTY" if tree_status.strip() else "CLEAN")),
        "detail": (tree_status or "").strip()[:400],
    }
    if require_clean and tree_status and tree_status.strip():
        return fail("checkout is dirty and --require-clean was requested: "
                    f"{result['tree']['detail'][:200]}")

    # ------------------------------------------------------------- build
    if wheel_path is None:
        build_cmd = [c.format(plan["dist"]) for c in toolchain["build"]]
        try:
            built = R._run(build_cmd, cwd=repo, env=env)
        except subprocess.TimeoutExpired:
            return fail("wheel build timed out")
        if built.returncode != 0:
            return fail("wheel build failed: " + (built.stderr or built.stdout)[-400:])
        wheels = sorted(Path(plan["dist"]).glob("*.whl"))
        if not wheels:
            return fail("build produced no wheel")
        wheel_path = wheels[0]
        result["steps"].append("build")
        check("build.wheel", True, wheel_path.name)

    assert wheel_path is not None
    result["wheel"] = wheel_path.name
    result["wheel_path"] = str(wheel_path)
    result["wheel_sha256"] = R._sha256(wheel_path)
    result["wheel_version"] = parse_wheel_version(wheel_path.name)

    try:
        members = R._wheel_members(wheel_path)
    except Exception as exc:  # noqa: BLE001
        return fail(f"cannot read wheel {wheel_path}: {exc}")
    if not R._wheel_has_dashboard(members):
        return fail("provided wheel is dashboard-less "
                    "(frontend/qubit-dist/index.html is not shipped)")
    leaked_members = sum(1 for m in members if "node_modules/" in m.replace("\\", "/"))
    if leaked_members:
        return fail(f"wheel swept {leaked_members} build-only node_modules members")
    check("artifact.wheel", True, wheel_path.name)

    # ------------------------------------------------------------- install
    try:
        made = R._run(toolchain["venv"], cwd=work, env=env)
    except subprocess.TimeoutExpired:
        return fail("venv creation timed out")
    if made.returncode != 0:
        return fail("venv creation failed: " + (made.stderr or made.stdout)[-400:])

    install = list(toolchain["install"]) + [str(wheel_path)]
    try:
        installed = R._run(install, cwd=work, env=env)
    except subprocess.TimeoutExpired:
        return fail("wheel install timed out")
    if installed.returncode != 0:
        detail = (installed.stderr or installed.stdout)[-400:]
        if no_deps or not require_network:
            return degrade(f"wheel install failed ({'--no-deps' if no_deps else 'dependencies unavailable?'}): "
                           f"{detail}")
        return fail(f"wheel install failed: {detail}")
    result["steps"].append("install")
    check("install.wheel", True,
          "wheel installed with --no-deps" if no_deps else "wheel + declared deps installed")

    bin_dir = Path(plan["venv"]) / "bin"
    version_proc = R._run([str(bin_dir / "qubit"), "--version"], cwd=work,
                          env=env, timeout=120)
    cli_line = ((version_proc.stdout or "") + (version_proc.stderr or "")).strip().splitlines()
    cli_line = cli_line[0][:200] if cli_line else ""
    if version_proc.returncode != 0 or not cli_line.lower().startswith("qubit"):
        return fail(f"'qubit --version' failed: {cli_line[-300:]}")
    result["cli_version"] = cli_line
    check("entrypoint.qubit_version", True, cli_line)

    # -------------------------------------------------------------- probe
    probe_path = work / "probe.py"
    probe_path.write_text(_PROBE, encoding="utf-8")
    penv = dict(env)
    penv.update({
        "VB_INSTALL_VENV": plan["venv"],
        "VB_PROBE_STATE": str(work / "probe-state"),
        "VB_PORT": str(port),
        "VB_NODE": shutil.which("node") or "",
        "VB_FORBIDDEN_HOMES": json.dumps([str(p) for p in live]),
    })
    try:
        probed = R._run([str(bin_dir / "python"), "-I", str(probe_path)],
                        cwd=work, env=penv, timeout=600)
    except subprocess.TimeoutExpired:
        return fail("installed dashboard/TUI probe timed out")
    probe_text = (probed.stdout or "") + (probed.stderr or "")
    payload = parse_probe_output(probe_text)
    if probed.returncode != 0 or payload is None:
        return fail("installed dashboard/TUI probe failed: " + probe_text[-800:])
    result["steps"].append("probe")
    result["probe"] = payload

    evaluation = evaluate_probe(
        payload, wheel_version=result["wheel_version"], cli_line=cli_line,
        expected_plugins=["qubit-ops", "qubit-devices", "qubit-memory",
                          "qubit-reports", "qubit-approvals"])
    result["checks"].extend(evaluation["checks"])
    result["reasons"].extend(evaluation["reasons"])
    result["version_identity"] = evaluation["version_identity"]
    result["tui"] = payload.get("tui") or {}
    result["dashboard"] = payload.get("dashboard") or {}
    if evaluation["reasons"]:
        result["state"] = "FAIL"
        return R._seal(result)

    # --------------------------------------------- source/operator leaks
    package_root = _python_package_root(Path(plan["venv"]), env)
    if package_root is None:
        return fail("could not resolve the installed voicebridge package root")
    needles = [str(repo), os.path.expanduser("~")]
    leaks = leak_scan(package_root, needles)
    result["leaks"] = leaks
    if leaks:
        detail = "; ".join(f"{h['path']} contains {h['needle']!r}" for h in leaks[:5])
        return fail(f"installed package leaks a checkout/home path: {detail}")
    check("isolation.no_source_leaks", True,
          f"{len(needles)} path needle(s) absent from installed package")

    # ------------------------------------- checkout + live-home integrity
    after_status = R._git_tracked_status(repo)
    if tree_status is not None and after_status is not None and \
            tree_status != after_status:
        return fail("the verifier changed the product worktree (tracked files): "
                    f"before={tree_status!r} after={after_status!r}")
    after_homes = {str(p): R._home_snapshot(p) for p in live}
    changed = [path for path in before_homes
               if before_homes[path] != after_homes.get(path)]
    result["isolation"]["live_homes_after"] = after_homes
    result["isolation"]["live_homes_unchanged"] = not changed
    if changed:
        return fail(f"the verifier read/wrote a live QUBIT_HOME/HERMES_HOME: {changed}")
    check("isolation.live_homes", True, f"{len(before_homes)} live home(s) unchanged")

    result["detail"] = (probe_text or "").strip()[-300:]
    return R._seal(result)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fresh-install verifier (wheel -> dashboard + packaged TUI)",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="hermetic preflight only (default; no build/install/start)")
    mode.add_argument("--run", action="store_true",
                      help="perform the real build/install/start verification")
    parser.add_argument("--build", action="store_true",
                      help="build the wheel from --repo when no --wheel is given")
    parser.add_argument("--wheel", type=Path, default=None,
                        help="install an already-built .whl instead of building")
    parser.add_argument("--repo", type=Path, default=REPO,
                        help="checkout to build/read (default: this repo)")
    parser.add_argument("--work-root", type=Path, default=None,
                        help="throwaway work dir (default: a fresh mktemp dir)")
    parser.add_argument("--python", default=sys.executable,
                        help="base interpreter for the throwaway venv")
    parser.add_argument("--port", type=int, default=0,
                        help="dashboard port (default 0 = ephemeral; 8890 is refused)")
    parser.add_argument("--no-deps", action="store_true",
                        help="install the wheel with --no-deps (venv sees the base interpreter's packages)")
    parser.add_argument("--require-network", action="store_true",
                        help="treat a failed dependency install as FAIL, not DEGRADED")
    parser.add_argument("--require-clean", action="store_true",
                        help="refuse to build from a dirty checkout")
    parser.add_argument("--report", type=Path, default=None,
                        help="write the JSON report here (default: <repo>/dist/fresh-install.json)")
    parser.add_argument("--keep", action="store_true",
                        help="keep the throwaway work dir")
    parser.add_argument("--json", action="store_true",
                        help="print the JSON report to stdout")
    args = parser.parse_args(argv)

    repo = args.repo.expanduser().resolve()
    explicit_root = args.work_root is not None
    work_root = args.work_root.expanduser().resolve() if explicit_root else None
    started = time.time()

    common: Dict[str, Any] = dict(work_root=work_root, wheel=args.wheel, port=args.port)
    # --build/--wheel/--no-deps/--require-* imply a real run; plain invocation
    # and explicit --check stay hermetic.
    wants_run = bool(args.run or args.build or args.wheel or args.no_deps
                     or args.require_network or args.require_clean)
    if args.check or not wants_run:
        result = check(repo, **{k: v for k, v in common.items()
                                if k in ("work_root", "wheel", "port")})
    else:
        result = run(repo, build=args.build or args.wheel is None, no_deps=args.no_deps,
                     python=args.python, require_network=args.require_network,
                     require_clean=args.require_clean,
                     **{k: v for k, v in common.items()
                        if k in ("work_root", "wheel", "port")})

    result["schema"] = SCHEMA
    result["gate"] = "fresh-install"
    result["elapsed_s"] = round(time.time() - started, 3)
    result.setdefault("reasons", [])

    report_path = (args.report.expanduser().resolve() if args.report else
                   repo / "dist" / "fresh-install.json")
    result["report"] = str(report_path)
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, indent=2, sort_keys=True),
                               encoding="utf-8")
    except OSError as exc:
        result.setdefault("warnings", []).append(f"could not write report {report_path}: {exc}")

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"fresh-install: {result.get('verdict', result['state'])}")
        print(f"  wheel  : {result.get('wheel_path') or '(not built)'}"
              f" [{result.get('wheel_source') or 'n/a'}]")
        if result.get("cli_version"):
            print(f"  version: {result['cli_version']}")
        if result.get("dashboard"):
            print(f"  port   : {result['dashboard'].get('port')}")
        print(f"  report : {report_path}")
        for reason in result.get("reasons", []):
            print(f"  - {reason}")

    if args.run and not args.check and not explicit_root and not args.keep:
        plan = result.get("plan") or {}
        if plan.get("work_root"):
            shutil.rmtree(plan["work_root"], ignore_errors=True)

    return {"OK": EXIT_OK, "FAIL": EXIT_FAIL, "DEGRADED": EXIT_DEGRADED}[result["state"]]


if __name__ == "__main__":
    raise SystemExit(main())
