#!/usr/bin/env python3
"""Clean-install / packaging rehearsal for Qubit (isolated venv, temp prefix).

Rehearses the shipped artifact the way an operator would consume it: build (or
accept) a wheel, install it with its declared dependencies into a throwaway venv
under the system temp dir, start the installed entry points, and serve the
installed dashboard — never the source tree.

This module is the engine behind ``scripts/artifact_install_e2e.py`` (the
release artifact-install E2E gate) and ``scripts/clean_install_check.sh``'s
Python sibling. It also supports the install profiles an operator can request:

* ``wheel=`` accepts an already-built ``.whl`` instead of building the checkout;
* ``profile=`` roots ``QUBIT_HOME`` at ``<work>/state/profiles/<name>``;
* ``without_dashboard`` skips the dashboard bundle/asset checks (the liveness
  probe still runs);
* ``without_models`` keeps every model/cache path under the throwaway root and
  proves no model artifact is downloaded or written.

Two modes:

* ``--check`` (default, hermetic, no network): statically verify the packaging
  preconditions and the *isolation plan*. It parses ``pyproject.toml`` for the
  console entry points, confirms the built dashboard bundle is present (unless
  ``--without-dashboard``), and proves every rehearsal path (HOME, XDG,
  QUBIT_HOME, profile, venv, dist, state, models) is rooted under the throwaway
  work dir — never the operator's real HOME and never ``~/.hermes``. It writes
  nothing.
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

Isolation is asserted, not assumed: the engine snapshots the operator's live
``QUBIT_HOME``/``HERMES_HOME`` (from the environment and ``~/.qubit`` /
``~/.hermes``) before and after the run, and the installed probe installs an
audit hook that raises if anything opens a path under those live homes.

The live service, the operator's real HOME/config/state, the running unit, and
``~/.hermes`` are never touched, enabled, or restarted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_DEGRADED = 4

REPO = Path(__file__).resolve().parents[1]

# The canonical public entry points the wheel must expose. ``qubit-hermes`` is a
# hidden compatibility alias and is verified only when present; the remaining
# optional launchers are exercised when the artifact ships them.
EXPECTED_ENTRY_POINTS = ("qubit", "voicebridge", "qubit-dev", "qubit-agent")
OPTIONAL_ENTRY_POINTS = ("qubit-acp", "qubit-voice-typing", "qubit-hermes")

# Any planned path that lands under one of these is an isolation failure.
FORBIDDEN_PATH_PARTS = frozenset({".hermes"})

#: Environment variables that name a live operator home. The engine must never
#: let the installed artifact read or write either one.
LIVE_HOME_ENV = ("QUBIT_HOME", "HERMES_HOME")

#: Default identity marker for a named profile so ``QUBIT_HOME`` resolves to a
#: real, live profile rather than an empty shell.
_PROFILE_MARKER = "profile.yaml"


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
               work_root: Optional[Path] = None,
               profile: Optional[str] = None) -> Dict[str, str]:
    """Resolve the throwaway paths the rehearsal will use.

    ``work_root`` defaults to a fresh ``mktemp -d`` under the system temp dir; a
    caller may pass an explicit root (used by tests) but it is still validated
    by :func:`isolation_findings`. ``profile`` roots ``QUBIT_HOME`` at
    ``<work>/state/profiles/<name>`` so a named-profile install is exercised
    without touching the operator's real profile store.
    """
    repo = Path(repo or REPO).resolve()
    if work_root is None:
        # A unique, not-yet-created path: ``--check`` must not write anything.
        work_root = (Path(tempfile.gettempdir())
                     / f"qubit-packaging-rehearsal-{os.getpid()}-{os.urandom(4).hex()}")
    work_root = Path(work_root).resolve()
    home = work_root / "home"
    state = work_root / "state"
    plan = {
        "repo": str(repo),
        "work_root": str(work_root),
        "home": str(home),
        "xdg_config": str(home / ".config"),
        "xdg_data": str(home / ".local/share"),
        "xdg_state": str(home / ".local/state"),
        "xdg_cache": str(home / ".cache"),
        "venv": str(work_root / "venv"),
        "dist": str(work_root / "dist"),
        "state": str(state),
        "models": str(work_root / "models"),
        "dashboard_bundle": str(repo / "frontend/qubit-dist/index.html"),
        "profile": profile or "",
        "profile_home": (str(state / "profiles" / profile) if profile else ""),
    }
    return plan


def isolation_findings(plan: Dict[str, str]) -> List[str]:
    """Every rehearsal path must stay under ``work_root`` and off ``~/.hermes``.

    Returns a list of human-readable violations (empty means isolated). The
    live ``QUBIT_HOME``/``HERMES_HOME`` values from the invoking environment are
    also rejected: a plan path that resolves to one of them would run the
    artifact against operator state.
    """
    findings: List[str] = []
    work_root = Path(plan["work_root"]).resolve()
    real_home = Path(os.path.expanduser("~")).resolve()

    live_homes = []
    for name in LIVE_HOME_ENV:
        raw = os.environ.get(name, "").strip()
        if raw:
            live_homes.append((name, Path(raw).expanduser().resolve(strict=False)))

    for key in ("home", "xdg_config", "xdg_data", "xdg_state", "xdg_cache",
                "venv", "dist", "state", "models", "profile_home"):
        raw = plan.get(key)
        if not raw:
            # Optional keys (models/profile_home) may be absent; required keys
            # must not be. Only complain about a missing REQUIRED key.
            if key not in ("models", "profile_home", "profile"):
                findings.append(f"{key}: missing from plan")
            continue
        path = Path(raw).resolve()
        if path != work_root and work_root not in path.parents:
            findings.append(f"{key} escapes work_root: {path}")
        if path == real_home or real_home in path.parents:
            findings.append(f"{key} targets the real HOME: {path}")
        if any(part in FORBIDDEN_PATH_PARTS for part in path.parts):
            findings.append(f"{key} targets a forbidden .hermes path: {path}")
        for env_name, live in live_homes:
            if path == live or live in path.parents:
                findings.append(
                    f"{key} is under the live {env_name}: {path}")
    return findings


def _is_under(path: Path, root: Path) -> bool:
    try:
        p = path.expanduser().resolve(strict=False)
        r = root.expanduser().resolve(strict=False)
    except OSError:
        return False
    return p == r or r in p.parents


def _live_home_paths(plan: Dict[str, str]) -> List[Path]:
    """The operator homes this run must never touch (outside ``work_root``)."""
    work_root = Path(plan["work_root"])
    out: List[Path] = []
    for name in LIVE_HOME_ENV:
        raw = os.environ.get(name, "").strip()
        if raw:
            out.append(Path(raw).expanduser().resolve(strict=False))
    # The platform defaults are read-old candidates even when the env is unset.
    home = Path(os.path.expanduser("~"))
    out.extend([home / ".qubit", home / ".hermes"])
    seen: set[str] = set()
    unique: List[Path] = []
    for path in out:
        key = str(path)
        if key in seen or _is_under(path, work_root):
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _home_snapshot(path: Path) -> Dict[str, Any]:
    """A cheap fingerprint of a live home: entries, dir mtime, marker stats."""
    try:
        if not path.exists():
            return {"path": str(path), "exists": False}
        entries = sorted(p.name for p in path.iterdir())[:200]
        markers: Dict[str, List[int]] = {}
        for name in ("config.yaml", ".env", "state.db", "active_profile",
                     "profile.yaml", "auth.json"):
            f = path / name
            try:
                if f.exists():
                    st = f.stat()
                    markers[name] = [int(st.st_size), int(st.st_mtime_ns)]
            except OSError:
                continue
        return {
            "path": str(path),
            "exists": True,
            "dir_mtime_ns": int(path.stat().st_mtime_ns),
            "entries": entries,
            "markers": markers,
        }
    except OSError as exc:
        return {"path": str(path), "exists": False, "error": str(exc)[:120]}


def _wheel_members(wheel: Path) -> List[str]:
    with zipfile.ZipFile(wheel) as z:
        return z.namelist()


def _wheel_has_dashboard(members: List[str]) -> bool:
    """Use the release qualifier's canonical dashboard check when importable.

    ``scripts/release_qualify.py`` is the certification orchestrator; reusing
    its suffix test keeps the artifact gate and the release gate from drifting.
    The local fallback keeps this module usable when run outside the package.
    """
    try:
        from scripts.release_qualify import _has_dashboard
        return _has_dashboard(members)
    except Exception:  # noqa: BLE001 - standalone/script execution
        suffix = "frontend/qubit-dist/index.html"
        return any(m.replace("\\", "/").endswith(suffix) for m in members)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def check(repo: Optional[Path] = None,
          work_root: Optional[Path] = None,
          *, wheel: Optional[Path] = None,
          profile: Optional[str] = None,
          without_models: bool = False,
          without_dashboard: bool = False) -> Dict[str, Any]:
    """Hermetic rehearsal preflight. Never builds, installs, or writes."""
    repo = Path(repo or REPO).resolve()
    plan = build_plan(repo, work_root, profile=profile)
    scripts = entry_points(repo)
    missing = [name for name in EXPECTED_ENTRY_POINTS if name not in scripts]
    optional_seen = [name for name in OPTIONAL_ENTRY_POINTS if name in scripts]
    bundle = Path(plan["dashboard_bundle"])
    bundle_present = bundle.is_file()
    findings = isolation_findings(plan)

    wheel_info: Dict[str, Any] = {}
    wheel_present = False
    if wheel is not None:
        wheel_path = Path(wheel).expanduser().resolve()
        wheel_present = wheel_path.is_file() and wheel_path.suffix == ".whl"
        wheel_info = {
            "path": str(wheel_path),
            "present": wheel_present,
            "dashboard": False,
            "node_modules_members": 0,
        }
        if wheel_present:
            try:
                members = _wheel_members(wheel_path)
                wheel_info["members"] = len(members)
                wheel_info["dashboard"] = _wheel_has_dashboard(members)
                wheel_info["node_modules_members"] = sum(
                    1 for m in members if "node_modules/" in m.replace("\\", "/"))
            except (OSError, zipfile.BadZipFile) as exc:
                wheel_present = False
                wheel_info["error"] = str(exc)[:160]

    reasons: List[str] = []
    state = "OK"
    if missing:
        state = "FAIL"
        reasons.append(f"pyproject.toml is missing entry points: {missing}")
    if findings:
        state = "FAIL"
        reasons.append(f"rehearsal is not isolated: {findings}")
    if wheel is not None:
        if not wheel_present:
            state = "FAIL"
            reasons.append(f"provided wheel is not a readable .whl: {wheel}")
        else:
            if not without_dashboard and not wheel_info.get("dashboard"):
                state = "FAIL"
                reasons.append(
                    "provided wheel is dashboard-less "
                    "(frontend/qubit-dist/index.html is not shipped)")
            if wheel_info.get("node_modules_members"):
                state = "FAIL"
                reasons.append(
                    "provided wheel swept build-only node_modules members: "
                    f"{wheel_info['node_modules_members']}")
    elif not without_dashboard and not bundle_present:
        # A dashboard-less wheel is not a faithful rehearsal; ask the operator to
        # build the (git-ignored) bundle first rather than mutating the worktree.
        if state == "OK":
            state = "DEGRADED"
        reasons.append(
            f"dashboard bundle {bundle} is absent; build it out of band with "
            "scripts/build_dashboard.sh before an honest rehearsal")

    return {
        "state": state,
        "verdict": {"OK": "PASS", "FAIL": "FAIL", "DEGRADED": "DEGRADED"}[state],
        "mode": "check",
        "plan": plan,
        "entry_points": scripts,
        "missing_entry_points": missing,
        "optional_entry_points_present": optional_seen,
        "dashboard_bundle_present": bundle_present,
        "without_dashboard": bool(without_dashboard),
        "without_models": bool(without_models),
        "profile": profile or None,
        "wheel": wheel_info,
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
            # Pin the throwaway venv to THIS interpreter: uv otherwise picks
            # the newest system Python it finds (3.11 on some hosts), which the
            # product's requires-python (>=3.12,<3.15) rejects.
            "venv": ["uv", "venv", "--quiet", "--python", "{python}", "{venv}"],
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


def _rehearsal_env(plan: Dict[str, str], *, profile: Optional[str] = None,
                   without_models: bool = False) -> Dict[str, str]:
    env = dict(os.environ)
    # HERMES_HOME is the read-old spelling of QUBIT_HOME. Unset it explicitly so
    # the installed artifact cannot inherit the operator's legacy home.
    for stale in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "HERMES_HOME"):
        env.pop(stale, None)
    qubit_home = plan.get("profile_home") if profile else plan["state"]
    env.update({
        "HOME": plan["home"],
        "XDG_CONFIG_HOME": plan["xdg_config"],
        "XDG_DATA_HOME": plan["xdg_data"],
        "XDG_STATE_HOME": plan["xdg_state"],
        "XDG_CACHE_HOME": plan["xdg_cache"],
        "QUBIT_HOME": qubit_home,
        # Keep build/install/model caches inside the throwaway root too, so a
        # rehearsal never reads or writes the operator's real package caches.
        "UV_CACHE_DIR": str(Path(plan["work_root"]) / "uv-cache"),
        "PIP_CACHE_DIR": str(Path(plan["work_root"]) / "pip-cache"),
        "HF_HOME": str(Path(plan["work_root"]) / "hf-cache"),
        "TRANSFORMERS_CACHE": str(Path(plan["work_root"]) / "hf-cache"),
        "QUBIT_MODELS_DIR": plan["models"],
        # The installed product must never self-update the throwaway checkout.
        "QUBIT_UPDATE_DISABLED": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    if without_models:
        # No model download is part of this gate. If a probe tries one it must
        # stay under the throwaway root; the engine also asserts the models dir
        # is still empty at the end.
        env["QUBIT_SKIP_MODEL_DOWNLOAD"] = "1"
    return env


def _run(cmd: List[str], *, cwd: Optional[Path], env: Dict[str, str],
         timeout: float = 1800.0) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env,
                          capture_output=True, text=True, timeout=timeout)


_PROBE = r'''
import http.client, json, os, pathlib, re, sys, tempfile, time

# Isolation guard: the installed artifact must never read or write a live
# QUBIT_HOME/HERMES_HOME. The engine passes those paths in VB_FORBIDDEN_HOMES.
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
                    raise RuntimeError(
                        "artifact-install E2E touched a live home: " + path)

sys.addaudithook(_guard)

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


def raw(path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    return r.status, r.getheader("Location"), r.read()


try:
    st, loc, _ = raw("/")
    assert st == 302 and loc in ("/chat", "/sessions"), (st, loc)

    # Public liveness: the reference-shaped /api/health contract, served from
    # the installed package, no token and no secret material.
    st, _, hb = raw("/api/health")
    health = json.loads(hb)
    assert st == 200 and health.get("ok") is True, (st, health)
    assert str(health.get("version", "")).startswith("qubit"), health
    assert health.get("state"), health

    dashboard = os.environ.get("VB_EXPECT_DASHBOARD", "1") == "1"
    if dashboard:
        st, _, body = raw("/chat")
        assert st == 200 and b"__QUBIT_BASE_PATH__" in body, st
        asset = re.search(rb"/(assets/[^\"]+\.js)", body)
        assert asset, "no built dashboard asset referenced"
        st, _, ab = raw("/" + asset.group(1).decode())
        assert st == 200 and len(ab) > 1000, (st, len(ab))
        st, _, pb = raw("/agent/api/dashboard/plugins",
                        {"X-Qubit-Session-Token": "rehearsal-token"})
        names = [m["name"] for m in json.loads(pb)]
        for want in ("qubit-ops", "qubit-devices", "qubit-memory",
                     "qubit-reports", "qubit-approvals"):
            assert want in names, (want, names)
finally:
    ui.stop()

# The vendored runtime must resolve and import from the installed package data,
# never the checkout.
from voicebridge import qubit_runtime as hr
st = hr.status()
assert st.get("present") is True and st.get("importable") is True, st
root = pathlib.Path(hr.runtime_root()).resolve()
assert venv in root.parents, f"runtime not under the install: {root}"
hr.ensure_path()
import qubit_cli.main as m  # noqa: E402  (forced by ensure_path above)
mod = pathlib.Path(m.__file__).resolve()
assert mod.is_relative_to(root), (mod, root)
print("OK health=%s dashboard=%s runtime=%s" %
      (health.get("state"), dashboard, root))
'''


def _seal(result: Dict[str, Any]) -> Dict[str, Any]:
    result["verdict"] = {"OK": "PASS", "FAIL": "FAIL",
                         "DEGRADED": "DEGRADED"}[result["state"]]
    return result


def run(repo: Optional[Path] = None, *, work_root: Optional[Path] = None,
        require_network: bool = False, wheel: Optional[Path] = None,
        profile: Optional[str] = None, without_models: bool = False,
        without_dashboard: bool = False,
        require_clean: bool = False) -> Dict[str, Any]:
    """Perform the real artifact -> install -> start rehearsal.

    ``wheel`` accepts an already-built artifact (skips the build); otherwise the
    checkout is built. The return value is a typed JSON-serializable report with
    ``state``/``verdict`` in PASS/FAIL/DEGRADED.
    """
    repo = Path(repo or REPO).resolve()
    plan = build_plan(repo, work_root, profile=profile)
    result: Dict[str, Any] = {
        "state": "OK", "verdict": "PASS", "mode": "run", "plan": plan,
        "steps": [], "reasons": [], "checks": [], "warnings": [],
        "profile": profile or None,
        "without_models": bool(without_models),
        "without_dashboard": bool(without_dashboard),
        "wheel": None, "wheel_path": None, "wheel_sha256": None,
        "wheel_source": None, "isolation": {},
    }

    def fail(reason: str) -> Dict[str, Any]:
        result["state"] = "FAIL"
        result["reasons"].append(reason)
        return _seal(result)

    def degrade(reason: str) -> Dict[str, Any]:
        if result["state"] == "OK":
            result["state"] = "DEGRADED"
        result["reasons"].append(reason)
        return _seal(result)

    def check(name: str, ok: bool, detail: str = "") -> None:
        result["checks"].append(
            {"name": name, "state": "OK" if ok else "FAIL",
             "detail": detail[:300]})

    # ------------------------------------------------------------- isolation
    findings = isolation_findings(plan)
    live = _live_home_paths(plan)
    before_homes = {str(p): _home_snapshot(p) for p in live}
    result["isolation"] = {
        "findings": findings,
        "live_homes": before_homes,
    }
    if findings:
        return fail(f"rehearsal not isolated: {findings}")

    # ---------------------------------------------------------- artifact input
    wheel_path: Optional[Path] = None
    if wheel is not None:
        candidate = Path(wheel).expanduser().resolve()
        if not candidate.is_file() or candidate.suffix != ".whl":
            return fail(f"provided wheel is not a readable .whl: {candidate}")
        try:
            members = _wheel_members(candidate)
        except (OSError, zipfile.BadZipFile) as exc:
            return fail(f"cannot read provided wheel {candidate}: {exc}")
        has_dashboard = _wheel_has_dashboard(members)
        leaked = sum(1 for m in members
                     if "node_modules/" in m.replace("\\", "/"))
        if leaked:
            return fail(
                f"provided wheel swept {leaked} build-only node_modules "
                "members")
        if not without_dashboard and not has_dashboard:
            return fail(
                "provided wheel is dashboard-less: "
                "frontend/qubit-dist/index.html is not shipped")
        wheel_path = candidate
        result["wheel"] = candidate.name
        result["wheel_path"] = str(candidate)
        result["wheel_sha256"] = _sha256(candidate)
        result["wheel_source"] = "provided"
        result["artifact_dashboard"] = has_dashboard
        result["steps"].append("artifact")
        check("artifact.wheel", True, candidate.name)

    toolchain = _toolchain()
    if toolchain is None:
        return degrade(
            "no wheel builder available (install 'uv' or the 'build' module)")
    result["toolchain"] = toolchain["name"]

    # Refuse before creating the throwaway tree when there is nothing to ship.
    if wheel_path is None and not without_dashboard and \
            not Path(plan["dashboard_bundle"]).is_file():
        return degrade(
            f"dashboard bundle {plan['dashboard_bundle']} is absent; build "
            "it out of band with scripts/build_dashboard.sh first")

    work = Path(plan["work_root"])
    for sub in ("home", "dist", "state", "models"):
        (work / sub).mkdir(parents=True, exist_ok=True)
    if profile:
        profile_home = Path(plan["profile_home"])
        profile_home.mkdir(parents=True, exist_ok=True)
        marker = profile_home / _PROFILE_MARKER
        if not marker.exists():
            marker.write_text(f"name: {profile}\n", encoding="utf-8")
        result["profile_home"] = str(profile_home)

    env = _rehearsal_env(plan, profile=profile,
                         without_models=without_models)

    # ------------------------------------------------------------- build
    tree_status = _git_tracked_status(repo)
    result["tree"] = {
        "state": ("UNKNOWN" if tree_status is None else
                  ("DIRTY" if tree_status.strip() else "CLEAN")),
        "detail": (tree_status or "").strip()[:400],
    }
    if require_clean and tree_status and tree_status.strip():
        return fail(
            "checkout is dirty and --require-clean was requested: "
            f"{result['tree']['detail'][:200]}")

    if wheel_path is None:
        try:
            built = _run([c.format(dist=plan["dist"])
                          for c in toolchain["build"]], cwd=repo, env=env)
        except subprocess.TimeoutExpired:
            return fail("wheel build timed out")
        if built.returncode != 0:
            return fail("wheel build failed: " +
                        (built.stderr or built.stdout)[-400:])
        wheels = sorted(Path(plan["dist"]).glob("*.whl"))
        if not wheels:
            return fail("build produced no wheel")
        wheel_path = wheels[0]
        result["wheel"] = wheel_path.name
        result["wheel_path"] = str(wheel_path)
        result["wheel_sha256"] = _sha256(wheel_path)
        result["wheel_source"] = "built"
        try:
            result["artifact_dashboard"] = _wheel_has_dashboard(
                _wheel_members(wheel_path))
        except (OSError, zipfile.BadZipFile):
            result["artifact_dashboard"] = None
        result["steps"].append("build")
        check("artifact.wheel", True, wheel_path.name)

    assert wheel_path is not None

    # ------------------------------------------------------------- install
    try:
        made = _run([c.format(venv=plan["venv"], python=sys.executable)
                     for c in toolchain["venv"]], cwd=work, env=env)
    except subprocess.TimeoutExpired:
        return fail("venv creation timed out")
    if made.returncode != 0:
        return fail("venv creation failed: " +
                    (made.stderr or made.stdout)[-400:])
    install = [c.format(venv=plan["venv"])
               for c in toolchain["install"]] + [str(wheel_path)]
    try:
        installed = _run(install, cwd=work, env=env)
    except subprocess.TimeoutExpired:
        return fail("wheel install timed out")
    if installed.returncode != 0:
        detail = (installed.stderr or installed.stdout)[-400:]
        if require_network:
            return fail(f"wheel install failed (network/build backend?): "
                        f"{detail}")
        return degrade(f"wheel install failed (network/build backend?): "
                       f"{detail}")
    result["steps"].append("install")
    check("install.wheel", True, "wheel + declared deps installed")

    # -------------------------------------------------------- entry points
    bin_dir = Path(plan["venv"]) / "bin"
    for name in EXPECTED_ENTRY_POINTS:
        exe = bin_dir / name
        if not exe.exists():
            return fail(f"entry point missing: {exe}")
        helptext = _run([str(exe), "--help"], cwd=work, env=env, timeout=120)
        if helptext.returncode != 0:
            return fail(
                f"entry point '{name} --help' failed: "
                f"{(helptext.stderr or helptext.stdout)[-300:]}")
        check(f"entrypoint.{name}", True, "--help")
    for name in OPTIONAL_ENTRY_POINTS:
        exe = bin_dir / name
        if not exe.exists():
            continue
        helptext = _run([str(exe), "--help"], cwd=work, env=env, timeout=120)
        if helptext.returncode != 0:
            return fail(
                f"optional entry point '{name} --help' failed: "
                f"{(helptext.stderr or helptext.stdout)[-300:]}")
        check(f"entrypoint.{name}", True, "--help")
    result["steps"].append("entry-points")

    version = _run([str(bin_dir / "qubit"), "--version"], cwd=work, env=env,
                   timeout=120)
    version_out = ((version.stdout or "") + (version.stderr or "")).strip()
    if version.returncode != 0 or not version_out.lower().startswith("qubit"):
        return fail(f"'qubit --version' failed: {version_out[-300:]}")
    result["version"] = version_out.splitlines()[0][:120]
    check("entrypoint.qubit_version", True, result["version"])

    # -------------------------------------------------------------- probe
    probe = work / "probe.py"
    probe.write_text(_PROBE, encoding="utf-8")
    penv = dict(env)
    penv["VB_INSTALL_VENV"] = plan["venv"]
    penv["VB_REHEARSAL_STATE"] = plan["state"]
    penv["VB_EXPECT_DASHBOARD"] = "0" if without_dashboard else "1"
    penv["VB_FORBIDDEN_HOMES"] = json.dumps([str(p) for p in live])
    try:
        probed = _run([str(bin_dir / "python"), "-I", str(probe)], cwd=work,
                      env=penv, timeout=300)
    except subprocess.TimeoutExpired:
        return fail("installed start probe timed out")
    if probed.returncode != 0:
        return fail("installed start probe failed: " +
                    (probed.stderr or probed.stdout)[-500:])
    result["steps"].append("probe")
    check("probe.installed", True, (probed.stdout or "").strip()[-200:])

    # ------------------------------------------------------ models profile
    if without_models:
        models_dir = Path(plan["models"])
        model_files = ([p for p in models_dir.rglob("*") if p.is_file()]
                       if models_dir.exists() else [])
        if model_files:
            return fail(
                "without-models requested but model files were written: "
                f"{[str(p) for p in model_files[:5]]}")
        check("models.skipped", True, "no model artifacts written")

    # ------------------------------------- checkout + live-home integrity
    after_status = _git_tracked_status(repo)
    if tree_status is not None and after_status is not None and \
            tree_status != after_status:
        return fail(
            "the rehearsal changed the product worktree (tracked files): "
            f"before={tree_status!r} after={after_status!r}")

    after_homes = {str(p): _home_snapshot(p) for p in live}
    changed = [path for path in before_homes
               if before_homes[path] != after_homes.get(path)]
    result["isolation"]["live_homes_after"] = after_homes
    result["isolation"]["live_homes_unchanged"] = not changed
    if changed:
        return fail(
            "the rehearsal read/wrote a live QUBIT_HOME/HERMES_HOME: "
            f"{changed}")
    check("isolation.live_homes", True,
          f"{len(before_homes)} live home(s) unchanged")

    result["detail"] = (probed.stdout or "").strip()[-300:]
    return _seal(result)


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
    parser.add_argument("--wheel", default=None,
                        help="install an already-built .whl instead of building")
    parser.add_argument("--profile", default=None,
                        help="root QUBIT_HOME at <work>/state/profiles/<name>")
    parser.add_argument("--without-models", action="store_true",
                        help="assert no model artifact is downloaded or written")
    parser.add_argument("--without-dashboard", action="store_true",
                        help="skip the dashboard bundle/asset checks (health still probed)")
    parser.add_argument("--require-network", action="store_true",
                        help="treat a failed dependency install as FAIL, not DEGRADED")
    parser.add_argument("--require-clean", action="store_true",
                        help="refuse to build from a dirty checkout")
    parser.add_argument("--keep", action="store_true",
                        help="keep the throwaway work dir (default: remove it)")
    args = parser.parse_args(argv)

    explicit_root = args.work_root is not None
    kwargs = dict(
        work_root=Path(args.work_root) if explicit_root else None,
        wheel=Path(args.wheel) if args.wheel else None,
        profile=args.profile,
        without_models=args.without_models,
        without_dashboard=args.without_dashboard,
    )
    if args.run:
        result = run(Path(args.repo), require_network=args.require_network,
                     require_clean=args.require_clean, **kwargs)
    else:
        result = check(Path(args.repo), **kwargs)
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.run and not explicit_root and not args.keep:
        shutil.rmtree(result["plan"]["work_root"], ignore_errors=True)
    return _exit_for(result["state"])


if __name__ == "__main__":
    raise SystemExit(main())
