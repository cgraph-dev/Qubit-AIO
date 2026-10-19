#!/usr/bin/env python3
"""Clean-install / packaging rehearsal for Qubit (isolated venv, temp prefix).

Rehearses the shipped artifact the way an operator would consume it: build the
wheel from the checkout, install it (with its declared dependencies) into a
throwaway venv under the system temp dir, start the installed entry points, and
serve the installed dashboard — never the source tree.

Two modes:

* ``--check`` (default, hermetic, no network): statically verify the packaging
  preconditions and the *isolation plan*. It parses ``pyproject.toml`` for the
  console entry points, confirms the built dashboard bundle is present, and
  proves every rehearsal path (HOME, XDG, QUBIT_HOME, venv, dist, state) is
  rooted under the throwaway work dir — never the operator's real HOME and never
  ``~/.hermes``. It writes nothing.
* ``--run`` (opt-in, slow, needs a build backend; network for deps): perform the
  rehearsal for real. Every artifact and every byte of state lands under the
  throwaway work dir. The product worktree is only *read* to build the wheel;
  its tracked status is snapshotted before/after and a change is a typed FAIL
  (build-only artifacts that git already ignores are not tracked changes).

Typed exit codes (never a silent pass):
  0 PASS      the requested rehearsal passed
  2 FAIL      a real packaging / install / startup / isolation defect
  4 DEGRADED  a prerequisite (dashboard bundle, build backend, network) is
              unavailable, so the rehearsal cannot honestly run

The live service, the operator's real HOME/config/state, the running unit, and
``~/.hermes`` are never touched, enabled, or restarted.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_DEGRADED = 4

REPO = Path(__file__).resolve().parents[1]

# The canonical public entry points the wheel must expose. ``qubit-hermes`` is a
# hidden compatibility alias and is verified only when present.
EXPECTED_ENTRY_POINTS = ("qubit", "voicebridge", "qubit-dev", "qubit-agent")
OPTIONAL_ENTRY_POINTS = ("qubit-hermes",)

# Any planned path that lands under one of these is an isolation failure.
FORBIDDEN_PATH_PARTS = frozenset({".hermes"})


def _load_pyproject(repo: Path) -> Dict[str, Any]:
    data = (repo / "pyproject.toml").read_bytes()
    try:
        import tomllib

        return tomllib.loads(data.decode("utf-8"))
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        import tomli  # type: ignore

        return tomli.loads(data.decode("utf-8"))


def entry_points(repo: Optional[Path] = None) -> Dict[str, str]:
    """The declared ``[project.scripts]`` console entry points."""
    repo = Path(repo or REPO)
    return dict(_load_pyproject(repo).get("project", {}).get("scripts", {}))


def build_plan(repo: Optional[Path] = None,
               work_root: Optional[Path] = None) -> Dict[str, str]:
    """Resolve the throwaway paths the rehearsal will use.

    ``work_root`` defaults to a fresh ``mktemp -d`` under the system temp dir; a
    caller may pass an explicit root (used by tests) but it is still validated
    by :func:`isolation_findings`.
    """
    repo = Path(repo or REPO).resolve()
    if work_root is None:
        # A unique, not-yet-created path: ``--check`` must not write anything.
        work_root = (Path(tempfile.gettempdir())
                     / f"qubit-packaging-rehearsal-{os.getpid()}-{os.urandom(4).hex()}")
    work_root = Path(work_root).resolve()
    home = work_root / "home"
    return {
        "repo": str(repo),
        "work_root": str(work_root),
        "home": str(home),
        "xdg_config": str(home / ".config"),
        "xdg_data": str(home / ".local/share"),
        "xdg_state": str(home / ".local/state"),
        "xdg_cache": str(home / ".cache"),
        "venv": str(work_root / "venv"),
        "dist": str(work_root / "dist"),
        "state": str(work_root / "state"),
        "dashboard_bundle": str(repo / "frontend/qubit-dist/index.html"),
    }


def isolation_findings(plan: Dict[str, str]) -> List[str]:
    """Every rehearsal path must stay under ``work_root`` and off ``~/.hermes``.

    Returns a list of human-readable violations (empty means isolated).
    """
    findings: List[str] = []
    work_root = Path(plan["work_root"]).resolve()
    real_home = Path(os.path.expanduser("~")).resolve()

    for key in ("home", "xdg_config", "xdg_data", "xdg_state", "xdg_cache",
                "venv", "dist", "state"):
        raw = plan.get(key)
        if not raw:
            findings.append(f"{key}: missing from plan")
            continue
        path = Path(raw).resolve()
        if path != work_root and work_root not in path.parents:
            findings.append(f"{key} escapes work_root: {path}")
        if path == real_home or real_home in path.parents:
            findings.append(f"{key} targets the real HOME: {path}")
        if any(part in FORBIDDEN_PATH_PARTS for part in path.parts):
            findings.append(f"{key} targets a forbidden .hermes path: {path}")
    return findings


def check(repo: Optional[Path] = None,
          work_root: Optional[Path] = None) -> Dict[str, Any]:
    """Hermetic rehearsal preflight. Never builds, installs, or writes."""
    repo = Path(repo or REPO).resolve()
    plan = build_plan(repo, work_root)
    scripts = entry_points(repo)
    missing = [name for name in EXPECTED_ENTRY_POINTS if name not in scripts]
    optional_seen = [name for name in OPTIONAL_ENTRY_POINTS if name in scripts]
    bundle = Path(plan["dashboard_bundle"])
    bundle_present = bundle.is_file()
    findings = isolation_findings(plan)

    reasons: List[str] = []
    state = "OK"
    if missing:
        state = "FAIL"
        reasons.append(f"pyproject.toml is missing entry points: {missing}")
    if findings:
        state = "FAIL"
        reasons.append(f"rehearsal is not isolated: {findings}")
    if not bundle_present:
        # A dashboard-less wheel is not a faithful rehearsal; ask the operator to
        # build the (git-ignored) bundle first rather than mutating the worktree.
        if state == "OK":
            state = "DEGRADED"
        reasons.append(
            f"dashboard bundle {bundle} is absent; build it out of band with "
            "scripts/build_dashboard.sh before an honest rehearsal")

    return {
        "state": state,
        "mode": "check",
        "plan": plan,
        "entry_points": scripts,
        "missing_entry_points": missing,
        "optional_entry_points_present": optional_seen,
        "dashboard_bundle_present": bundle_present,
        "isolation_findings": findings,
        "reasons": reasons,
    }


def _exit_for(state: str) -> int:
    return {"OK": EXIT_OK, "FAIL": EXIT_FAIL, "DEGRADED": EXIT_DEGRADED}[state]


def _toolchain() -> Optional[Dict[str, Any]]:
    """Pick a wheel builder + venv creator, or None when unavailable."""
    if shutil.which("uv"):
        return {
            "name": "uv",
            "build": ["uv", "build", "--wheel", "--out-dir", "{dist}"],
            "venv": ["uv", "venv", "--quiet", "{venv}"],
            "install": ["uv", "pip", "install", "--python", "{venv}/bin/python"],
        }
    if _has_module("build") and _has_module("venv"):
        py = sys.executable
        return {
            "name": "python -m build",
            "build": [py, "-m", "build", "--wheel", "--out-dir", "{dist}"],
            "venv": [py, "-m", "venv", "{venv}"],
            "install": ["{venv}/bin/python", "-m", "pip", "install"],
        }
    return None


def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:  # noqa: BLE001
        return False


def _git_tracked_status(repo: Path) -> Optional[str]:
    """``git status --porcelain`` for the repo, or None when git is absent."""
    if shutil.which("git") is None:
        return None
    proc = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                          capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def _rehearsal_env(plan: Dict[str, str]) -> Dict[str, str]:
    env = dict(os.environ)
    for stale in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(stale, None)
    env.update({
        "HOME": plan["home"],
        "XDG_CONFIG_HOME": plan["xdg_config"],
        "XDG_DATA_HOME": plan["xdg_data"],
        "XDG_STATE_HOME": plan["xdg_state"],
        "XDG_CACHE_HOME": plan["xdg_cache"],
        "QUBIT_HOME": plan["state"],
        # Keep build/install caches inside the throwaway root too, so a rehearsal
        # never reads or writes the operator's real package caches.
        "UV_CACHE_DIR": str(Path(plan["work_root"]) / "uv-cache"),
        "PIP_CACHE_DIR": str(Path(plan["work_root"]) / "pip-cache"),
        # The installed product must never self-update the throwaway checkout.
        "QUBIT_UPDATE_DISABLED": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


def _run(cmd: List[str], *, cwd: Optional[Path], env: Dict[str, str],
         timeout: float = 1800.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env,
                          capture_output=True, text=True, timeout=timeout)


_PROBE = r'''
import http.client, json, os, pathlib, re, sys, tempfile, time
venv = pathlib.Path(os.environ["VB_INSTALL_VENV"]).resolve()
import voicebridge
pkg = pathlib.Path(voicebridge.__file__).resolve()
assert venv in pkg.parents, f"voicebridge not imported from the install: {pkg}"
from voicebridge.config import Config
from voicebridge.webui import WebUI
import yaml
state = pathlib.Path(tempfile.mkdtemp(dir=os.environ["VB_REHEARSAL_STATE"]))
p = state / "voice.yaml"
p.write_text(yaml.safe_dump({"logging": {"jsonl_path": str(state)},
                             "webui": {"token": "rehearsal-token"}}))
cfg = Config.load(p)
class _F:
    _route = "local"; _web_active = 0
    task = type("T", (), {"active": False})()
    def __init__(self): self.cfg = cfg; self._t0 = time.time()
ui = WebUI(_F(), host="127.0.0.1", port=0, state_dir=state, asr_factory=lambda: None)
ui.start()
port = ui._server.server_address[1]
try:
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", "/"); r = c.getresponse(); body = r.read()
    assert (r.status, r.getheader("Location")) == (302, "/sessions"), (r.status, r.getheader("Location"))
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", "/sessions"); r = c.getresponse(); body = r.read()
    assert r.status == 200, r.status
    assert b"__QUBIT_BASE_PATH__" in body
    asset = re.search(rb"/(assets/[^\"]+\.js)", body)
    assert asset, "no built dashboard asset referenced"
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", "/" + asset.group(1).decode()); r = c.getresponse(); ab = r.read()
    assert r.status == 200 and len(ab) > 1000, (r.status, len(ab))
finally:
    ui.stop()
print("OK dashboard served from", pkg)
'''


def run(repo: Optional[Path] = None, *, work_root: Optional[Path] = None,
        require_network: bool = False) -> Dict[str, Any]:
    """Perform the real build -> install -> start rehearsal."""
    repo = Path(repo or REPO).resolve()
    plan = build_plan(repo, work_root)
    result: Dict[str, Any] = {"state": "OK", "mode": "run", "plan": plan,
                              "steps": [], "reasons": []}

    findings = isolation_findings(plan)
    if findings:
        result.update(state="FAIL", reasons=[f"rehearsal not isolated: {findings}"])
        return result
    if not Path(plan["dashboard_bundle"]).is_file():
        result.update(state="DEGRADED", reasons=[
            f"dashboard bundle {plan['dashboard_bundle']} is absent; build it "
            "out of band with scripts/build_dashboard.sh first"])
        return result

    toolchain = _toolchain()
    if toolchain is None:
        result.update(state="DEGRADED", reasons=[
            "no wheel builder available (install 'uv' or the 'build' module)"])
        return result
    result["toolchain"] = toolchain["name"]

    before = _git_tracked_status(repo)
    work = Path(plan["work_root"])
    for sub in ("home", "dist", "state"):
        (work / sub).mkdir(parents=True, exist_ok=True)
    env = _rehearsal_env(plan)

    # 1) build the wheel into the throwaway dist dir (reads the checkout only).
    try:
        built = _run([c.format(dist=plan["dist"]) for c in toolchain["build"]],
                     cwd=repo, env=env)
    except subprocess.TimeoutExpired:
        result.update(state="FAIL", reasons=["wheel build timed out"])
        return result
    if built.returncode != 0:
        result.update(state="FAIL", reasons=[
            "wheel build failed", (built.stderr or built.stdout)[-400:]])
        return result
    wheels = sorted(Path(plan["dist"]).glob("*.whl"))
    if not wheels:
        result.update(state="FAIL", reasons=["build produced no wheel"])
        return result
    wheel = wheels[0]
    result["wheel"] = wheel.name
    result["steps"].append("build")

    # 2) throwaway venv + install with declared deps.
    try:
        made = _run([c.format(venv=plan["venv"]) for c in toolchain["venv"]],
                    cwd=work, env=env)
    except subprocess.TimeoutExpired:
        result.update(state="FAIL", reasons=["venv creation timed out"])
        return result
    if made.returncode != 0:
        result.update(state="FAIL", reasons=[
            "venv creation failed", (made.stderr or made.stdout)[-400:]])
        return result
    install = [c.format(venv=plan["venv"]) for c in toolchain["install"]] + [str(wheel)]
    try:
        installed = _run(install, cwd=work, env=env)
    except subprocess.TimeoutExpired:
        result.update(state="FAIL", reasons=["wheel install timed out"])
        return result
    if installed.returncode != 0:
        detail = (installed.stderr or installed.stdout)[-400:]
        state = "FAIL" if require_network else "DEGRADED"
        result.update(state=state, reasons=[
            f"wheel install failed (network/build backend?): {detail}"])
        return result
    result["steps"].append("install")

    # 3) every declared entry point resolves and starts its help.
    bin_dir = Path(plan["venv"]) / "bin"
    for name in EXPECTED_ENTRY_POINTS:
        exe = bin_dir / name
        if not exe.exists():
            result.update(state="FAIL", reasons=[f"entry point missing: {exe}"])
            return result
        helptext = _run([str(exe), "--help"], cwd=work, env=env, timeout=120)
        if helptext.returncode != 0:
            result.update(state="FAIL", reasons=[
                f"entry point '{name} --help' failed",
                (helptext.stderr or helptext.stdout)[-300:]])
            return result
    result["steps"].append("entry-points")

    # 4) start the installed dashboard and probe it (out of the source tree).
    probe = work / "probe.py"
    probe.write_text(_PROBE)
    penv = dict(env)
    penv["VB_INSTALL_VENV"] = plan["venv"]
    penv["VB_REHEARSAL_STATE"] = plan["state"]
    try:
        probed = _run([str(bin_dir / "python"), "-I", str(probe)], cwd=work,
                      env=penv, timeout=300)
    except subprocess.TimeoutExpired:
        result.update(state="FAIL", reasons=["installed start probe timed out"])
        return result
    if probed.returncode != 0:
        result.update(state="FAIL", reasons=[
            "installed start probe failed",
            (probed.stderr or probed.stdout)[-400:]])
        return result
    result["steps"].append("start")

    # 5) the product checkout must not have changed (tracked files).
    after = _git_tracked_status(repo)
    if before is not None and after is not None and before != after:
        result.update(state="FAIL", reasons=[
            "the rehearsal changed the product worktree (tracked files)",
            f"before={before!r} after={after!r}"])
        return result

    result["detail"] = (probed.stdout or "").strip()[-300:]
    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="hermetic preflight only (default)")
    mode.add_argument("--run", action="store_true",
                      help="perform the real build/install/start rehearsal")
    parser.add_argument("--repo", default=str(REPO),
                        help="checkout to rehearse (default: this script's repo)")
    parser.add_argument("--work-root", default=None,
                        help="throwaway work dir (default: a fresh mktemp dir)")
    parser.add_argument("--require-network", action="store_true",
                        help="treat a failed dependency install as FAIL, not DEGRADED")
    parser.add_argument("--keep", action="store_true",
                        help="keep the throwaway work dir (default: remove it)")
    args = parser.parse_args(argv)

    explicit_root = args.work_root is not None
    if args.run:
        result = run(Path(args.repo), work_root=Path(args.work_root) if explicit_root else None,
                     require_network=args.require_network)
    else:
        result = check(Path(args.repo),
                       work_root=Path(args.work_root) if explicit_root else None)
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.run and not explicit_root and not args.keep:
        shutil.rmtree(result["plan"]["work_root"], ignore_errors=True)
    return _exit_for(result["state"])


if __name__ == "__main__":
    raise SystemExit(main())
