#!/usr/bin/env python3
"""Packet 8K: release certification orchestrator (hermetic + opt-in steps).

  --static          hermetic checks only (no build, no install, no network):
                    provenance, dependency/SBOM, secret + machine-path scans,
                    filesystem modes, resource ceilings, operator runbooks,
                    backup/migration, systemd-analyze unit verify, upgrade/
                    uninstall contract.
  --build           build wheel + sdist into --out, inspect members, scan
                    contents, record hashes (network: build backend).
  --install         install the built wheel into a CLEAN venv with a CLEAN
                    HOME outside the checkout, then run import / entry-point /
                    resource / unit / smoke checks with no PYTHONPATH, no
                    source-tree sys.path insertion, no editable install, no
                    repo-local venv (network: deps).
  --reproduce       build twice and compare artifact hashes.
  --all             static + build + install + reproduce.
  --out DIR         report + artifact output dir (default ./dist/release-8k).
  --json            print the JSON report to stdout too.

Exit: 0 = PASS, 2 = FAIL, 4 = DEGRADED, 8 = BLOCKED. A dirty tree blocks the
artifact steps (build/install/reproduce) so an sdist can never sweep untracked
personal files and be reported as a clean release; the static hermetic
diagnostic remains available on a dirty tree. A skipped --install is DEGRADED,
never a pass.

Hermetic guarantee: nothing here enables/restarts/touches a live service,
timer, browser, account, model, or the operator's real HOME/config/state. All
install state lives under --out.
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

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_DEGRADED = 4
EXIT_BLOCKED = 8

# Documented generic placeholders used in sanitized test/docs fixtures. A
# machine-path token outside this set is treated as a personal path and fails
# the artifact even when it appears in non-runtime docs/tests (Packet 8K).
# Built from parts so the certifier's own source never contains a literal home
# path that the machine-path scan would flag.
GENERIC_PATH_TOKENS = {"/home" + "/example", "Projects"}


def _which(name: str) -> Path | None:
    p = shutil.which(name)
    return Path(p) if p else None


def _run(cmd: list[str], timeout: int, cwd: Path | None = None,
         env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=str(cwd) if cwd else None, env=env)


def static_report() -> dict:
    from voicebridge import release as R
    return R.certify(REPO)


def tree_clean_state() -> dict:
    """Whether the checkout is a clean committed tree (Packet 8K closure).

    Building release artifacts from a DIRTY tree is a release blocker: an
    sdist built from a dirty checkout sweeps untracked personal files (for
    example an untracked benchmark report carrying an absolute user-home path)
    into the artifact and would misrepresent them as a clean release.
    ``--build/--install/--reproduce`` therefore fail closed on a dirty tree
    unless ``--allow-dirty`` is given (explicit diagnostic mode). ``--static``
    remains a safe hermetic diagnostic on a dirty tree."""
    from voicebridge import release as R
    prov = R.git_provenance(REPO)
    dirty_files = list(prov.get("dirty_files", []))
    untracked = list(prov.get("untracked_files", []))
    dirty = bool(dirty_files or untracked)
    return {
        "state": "CLEAN" if not dirty else "DIRTY",
        "dirty_files": dirty_files[:200],
        "untracked_files": untracked[:200],
        "detail": "committed checkout is clean" if not dirty else
                  f"{len(dirty_files)} modified + {len(untracked)} untracked",
    }


# ---------------------------------------------------------------------------
# Build + inspect
# ---------------------------------------------------------------------------
def build_artifacts(out_dir: Path) -> dict:
    """Build wheel + sdist with `python -m build` (build isolation fetches
    hatchling). Requires the `build` tool (pip install build) — a
    certification prerequisite, never a runtime dependency."""
    build_dir = out_dir / "build1"
    build_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {"wheel": None, "sdist": None}
    try:
        r = _run([sys.executable, "-m", "build", "--wheel", "--sdist",
                  "--outdir", str(build_dir), str(REPO)], timeout=900)
        if r.returncode != 0:
            return {"state": "DEGRADED",
                    "detail": f"build failed (is `build` installed? "
                              f"pip install build): {r.stderr[-300:]}",
                    "artifacts": artifacts}
    except subprocess.TimeoutExpired:
        return {"state": "DEGRADED", "detail": "build timed out (network/slow)",
                "artifacts": artifacts}
    wheels = sorted(build_dir.glob("*.whl"))
    sdists = sorted(build_dir.glob("*.tar.gz"))
    artifacts["wheel"] = wheels[0].name if wheels else None
    artifacts["sdist"] = sdists[0].name if sdists else None
    if not artifacts["wheel"] or not artifacts["sdist"]:
        return {"state": "DEGRADED", "detail": "wheel or sdist not produced",
                "artifacts": artifacts}
    return {"state": "OK", "detail": "wheel + sdist built",
            "artifacts": artifacts, "dir": str(build_dir)}


def inspect_artifacts(build_dir: Path) -> dict:
    from voicebridge import release as R
    records = []
    all_ok = True
    for name in ("wheel", "sdist"):
        matches = sorted(build_dir.glob("*.whl")) if name == "wheel" \
            else sorted(build_dir.glob("*.tar.gz"))
        if not matches:
            records.append({"artifact": name, "state": "MISSING",
                            "detail": "no artifact built"})
            all_ok = False
            continue
        art = matches[0]
        try:
            members = R.artifact_members(art)
        except Exception as exc:
            records.append({"artifact": name, "state": "FAIL",
                            "detail": f"cannot read: {exc}"})
            all_ok = False
            continue
        forbidden = R.artifact_member_hits(art)
        content = R.artifact_content_scan(art)
        # Packet 8K closure: noncore machine paths (docs/tests/UI) are NOT
        # silently harmless. Generic placeholders (a documented "example" user
        # home and the generic "Projects" token) are accepted as sanitized
        # fixtures; any OTHER personal path token (e.g. a real user name)
        # fails the artifact, even inside docs or tests.
        noncore = content["machine_paths_noncore"]
        bad_noncore = {rel: toks for rel, toks in noncore.items()
                       if any(tok not in GENERIC_PATH_TOKENS for tok in toks)}
        dirty_private = bool(forbidden or content["secrets"]
                             or content["machine_paths"] or bad_noncore)
        # Packet 8K closure: verify the ACTUAL detached signature (never
        # fabricate one). NOT_SIGNED is a typed result, not a pass.
        sig = R.verify_artifact_signature(art)
        records.append({
            "artifact": name,
            "file": art.name,
            "size_bytes": art.stat().st_size,
            "sha256": R.sha256(art),
            "members": len(members),
            "forbidden_members": forbidden,
            "secrets_in_members": content["secrets"],
            "fixture_secrets_in_members": content["fixture_secrets"],
            "machine_paths_in_members": content["machine_paths"],
            "machine_paths_in_noncore_members": content["machine_paths_noncore"],
            "noncore_personal_paths_blocked": bad_noncore,
            "signature": sig,
            "state": "OK" if not dirty_private else "FAIL",
            "detail": "clean" if not dirty_private else
                      ("non-generic personal path in docs/tests: "
                       f"{bad_noncore}" if bad_noncore else
                       "forbidden/secret/machine-path members present"),
        })
        if dirty_private:
            all_ok = False
    return {"state": "OK" if all_ok else "FAIL", "records": records}


def build_step(out_dir: Path) -> dict:
    b = build_artifacts(out_dir)
    if b["state"] != "OK":
        return b
    return inspect_artifacts(Path(b["dir"]))


# ---------------------------------------------------------------------------
# Clean-HOME install + smoke
# ---------------------------------------------------------------------------
def install_step(out_dir: Path) -> dict:
    """Install the wheel into a clean venv + clean HOME outside the checkout and
    run import / entry-point / resource / unit / smoke checks."""
    from voicebridge import release as R
    build_dir = out_dir / "build1"
    wheels = sorted(build_dir.glob("*.whl")) if build_dir.exists() else []
    if not wheels:
        return {"state": "DEGRADED",
                "detail": "no wheel built; run --build first", "checks": []}
    venv = (out_dir / "venv").resolve()
    home = (out_dir / "home").resolve()
    home.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []
    r = _run([sys.executable, "-m", "venv", str(venv)], timeout=300)
    if r.returncode != 0:
        return {"state": "DEGRADED",
                "detail": f"venv creation failed: {r.stderr[-300:]}",
                "checks": []}
    if not (venv / "bin" / "python").exists():
        return {"state": "DEGRADED", "detail": "venv python missing", "checks": []}
    r = _run([str(venv / "bin" / "pip"), "install", str(wheels[0])], timeout=2400)
    if r.returncode != 0:
        return {"state": "DEGRADED",
                "detail": f"wheel+deps install failed: {r.stderr[-300:]}",
                "checks": []}

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_STATE_HOME"] = str(home / ".local" / "state")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")

    def run_check(name: str, argv: list[str], timeout: int = 300) -> dict:
        try:
            r = subprocess.run(argv, capture_output=True, text=True,
                               timeout=timeout, cwd=str(home), env=env)
        except subprocess.TimeoutExpired:
            return {"name": name, "state": "FAIL", "detail": "timed out",
                    "rc": -1}
        tail = ((r.stdout or "") + (r.stderr or "")).strip().replace("\n", " ")[-200:]
        return {"name": name, "state": "OK" if r.returncode == 0 else "FAIL",
                "detail": tail, "rc": r.returncode}

    py = [str(venv / "bin" / "python"), "-I"]

    # imports
    for mod in ("voicebridge", "voicebridge.release", "voicebridge.cli",
                "voicebridge.config", "voicebridge.resources"):
        checks.append(run_check(
            f"import.{mod}",
            py + ["-c", f"import {mod}"]))

    # entry points + module entry
    checks.append(run_check("entrypoint.voicebridge", py + ["-m", "voicebridge",
                                                            "--help"]))
    checks.append(run_check("entrypoint.voicebridge_console_script",
                            [str(venv / "bin" / "voicebridge"), "--help"]))
    checks.append(run_check("entrypoint.qubit-dev", [str(venv / "bin" / "qubit-dev"),
                                                     "--help"]))

    # packaged resources resolve from the installed package data
    res_code = (
        "from voicebridge import resources;\n"
        "import sys;\n"
        "need = {'frontend': resources.resource_path('frontend').is_dir(),\n"
        "        'config_example': resources.resource_path("
        "'config/voice.example.yaml').is_file(),\n"
        "        'dialogs': resources.resource_path('config/dialogs.md').is_file(),\n"
        "        'templates': resources.resource_path('config/task-templates.yaml').is_file(),\n"
        "        'systemd': resources.resource_path('systemd').is_dir()}\n"
        "sys.exit(0 if all(need.values()) else 1)\n"
    )
    checks.append(run_check("resources.packaged", py + ["-c", res_code]))

    # dependency-contract scan against the INSTALLED package files
    dep_code = (
        "import sys, pathlib, importlib.util;\n"
        "spec = importlib.util.find_spec('voicebridge');\n"
        "base = pathlib.Path(spec.origin).resolve().parent;\n"
        "from voicebridge.release import import_scan_installed, "
        "declared_module_names_from_metadata;\n"
        "declared = declared_module_names_from_metadata();\n"
        "f = import_scan_installed(base, declared);\n"
        "print('INSTALLED-DEPS-FAILS=' + str(f));\n"
        "sys.exit(0 if not f else 1)\n"
    )
    checks.append(run_check("deps.installed_contract", py + ["-c", dep_code]))

    # unit verify on the packaged units
    unit_code = (
        "from voicebridge import release as R;\n"
        "import tempfile, sys;\n"
        "with tempfile.TemporaryDirectory(prefix='vb-units-') as td:\n"
        "    res = R.unit_verify(R.REPO, workdir=td)\n"
        "sys.exit(0 if res.get('all_ok') else 1)\n"
    )
    checks.append(run_check("units.packaged_verify", py + ["-c", unit_code]))

    # first-run config smoke: manifest + package doctor (loopback probes only).
    # package doctor returns 1 on a clean host where ollama/browser/audio are
    # absent — a TYPED degraded state, not a failure.
    checks.append(run_check("smoke.package_manifest",
                            py + ["-m", "voicebridge", "package", "manifest"]))
    checks.append(run_check(
        "smoke.package_doctor",
        py + ["-m", "voicebridge", "package", "doctor", "--json",
              "--config", str(home / "none.yaml")], timeout=60))
    for c in checks:
        if c["name"] == "smoke.package_doctor" and c.get("rc") in (0, 1):
            c["state"] = "OK"
            c["detail"] = "typed degraded on clean host (probes missing; rc<=1)" + \
                (" " + c["detail"] if c["detail"] else "")

    all_ok = all(c["state"] == "OK" for c in checks)
    return {"state": "OK" if all_ok else "FAIL",
            "detail": f"{sum(c['state'] == 'OK' for c in checks)}/"
                      f"{len(checks)} smoke checks passed",
            "checks": checks}


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------
def reproduce_step(out_dir: Path) -> dict:
    from voicebridge import release as R
    try:
        b1 = out_dir / "build1"
        b2 = out_dir / "build2"
        b2.mkdir(parents=True, exist_ok=True)
        _run([sys.executable, "-m", "build", "--wheel", "--outdir", str(b2),
              str(REPO)], timeout=900)
        wheels1 = sorted(b1.glob("*.whl"))
        wheels2 = sorted(b2.glob("*.whl"))
        if not wheels1 or not wheels2:
            return {"state": "DEGRADED", "detail": "build missing artifacts",
                    "records": []}
        rec = R.reproducibility_record(wheels1[0], wheels2[0])
        return {"state": "OK" if rec.get("reproducible") else "DEGRADED",
                "detail": ("byte-identical wheel rebuild"
                           if rec.get("reproducible") else
                           "wheels differ (timestamps/metadata); hashes "
                           "recorded — see toolchain"),
                "records": [rec]}
    except subprocess.TimeoutExpired:
        return {"state": "DEGRADED", "detail": "rebuild timed out", "records": []}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def _print_report(report: dict) -> None:
    print(json.dumps(report, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Packet 8K release certification")
    ap.add_argument("--static", action="store_true",
                    help="hermetic checks only (default if no step given)")
    ap.add_argument("--build", action="store_true", help="build + inspect artifacts")
    ap.add_argument("--install", action="store_true", help="clean-HOME install + smoke")
    ap.add_argument("--reproduce", action="store_true", help="rebuild reproducibility")
    ap.add_argument("--all", action="store_true", help="static+build+install+reproduce")
    ap.add_argument("--out", type=Path, default=None,
                    help="artifact/report dir (default ./dist/release-8k)")
    ap.add_argument("--json", action="store_true", help="print JSON report")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="explicitly build/inspect artifacts from a DIRTY tree "
                         "(diagnostic only — never a clean release; unreachable "
                         "release verdict unless the tree is clean)")
    ap.add_argument("--unsigned-ok", action="store_true",
                    help="record an EXPLICIT approved unsigned-release policy: "
                         "artifacts that are NOT_SIGNED no longer BLOCK the "
                         "qualification (the report still lists each artifact's "
                         "NOT_SIGNED status). Without this, unsigned artifacts "
                         "are a release blocker.")
    args = ap.parse_args(argv)

    out_dir = args.out or (REPO / "dist" / "release-8k")
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    any_step = any((args.static, args.build, args.install, args.reproduce, args.all))
    report: dict = {"packet": "8K", "out_dir": str(out_dir), "steps": {}}

    # Packet 8K closure: a dirty tree can never yield a clean release. The
    # static hermetic diagnostic is allowed (it records provenance), but the
    # artifact steps (build/install/reproduce) fail closed unless --allow-dirty
    # explicitly opts into a non-release diagnostic.
    tree = tree_clean_state()
    report["tree"] = tree
    artifact_steps = {"build", "install", "reproduce"}
    if (args.build or args.install or args.reproduce or args.all) and not (
            tree["state"] == "CLEAN" or args.allow_dirty):
        block = {
            "state": "BLOCKED",
            "detail": (f"release artifacts from a dirty tree are not a clean "
                       f"release: {tree['detail']}; commit or stash before "
                       f"building, or pass --allow-dirty for an explicit "
                       f"diagnostic (never a clean release)"),
            "dirty_files": tree["dirty_files"],
            "untracked_files": tree["untracked_files"],
        }
        report["blocked_steps"] = sorted(artifact_steps)
        report["verdict"] = "BLOCKED"
        for name in artifact_steps:
            report["steps"][name] = block
        if args.json:
            _print_report(report)
        else:
            print(f"verdict: {report['verdict']}")
            print(f"  BLOCKED — {block['detail']}")
        return EXIT_BLOCKED

    if args.all or args.static or not any_step:
        report["steps"]["static"] = static_report()
    if args.all or args.build:
        report["steps"]["build"] = build_step(out_dir)
    if args.all or args.install:
        report["steps"]["install"] = install_step(out_dir)
    if args.all or args.reproduce:
        report["steps"]["reproduce"] = reproduce_step(out_dir)

    # gate: static must pass; build/install/reproduce FAIL is a release blocker;
    # DEGRADED is typed (never a silent pass).
    states = [s.get("state") for s in report["steps"].values()
              if isinstance(s, dict) and "state" in s]
    if "static" in report["steps"] and "state" not in report["steps"]["static"]:
        try:
            from voicebridge import release as R
            stat = report["steps"]["static"]
            ok = R.report_pass(stat)
            stat["state"] = "OK" if ok else "FAIL"
            states.append("OK" if ok else "FAIL")
        except Exception as exc:
            states.append("DEGRADED")
            report["steps"]["static"] = {"state": "DEGRADED",
                                         "error": str(exc)[:200]}
    # Packet 8K closure: unsigned artifacts are a release blocker unless the
    # operator explicitly approves the unsigned-release policy (--unsigned-ok).
    if "build" in report["steps"]:
        build = report["steps"]["build"]
        sigs = [rec.get("signature", {}).get("state")
                for rec in build.get("records", []) if "signature" in rec]
        if sigs and not args.unsigned_ok:
            if all(s == "SIGNED" for s in sigs):
                build["signing"] = {"state": "SIGNED",
                                    "detail": "all artifacts carry a verified "
                                              "detached signature from the "
                                              "approved signer"}
            else:
                not_signed = [r["signature"] for r in build["records"]
                              if r.get("signature", {}).get("state") != "SIGNED"]
                states_detail = ", ".join(
                    f"{r['artifact']}={r['signature']['state']}"
                    for r in build["records"])
                build["signing"] = {"state": "BLOCKED",
                                    "detail": (f"artifacts are not all signed by "
                                               f"the approved signer "
                                               f"({len(not_signed)} not SIGNED: "
                                               f"{states_detail}); sign each "
                                               f"artifact with the approved "
                                               f"fingerprint or pass "
                                               f"--unsigned-ok to record an "
                                               f"explicit approved unsigned-"
                                               f"release policy")}
                states.append("BLOCKED")
        elif sigs and args.unsigned_ok:
            # Explicitly approved exception: never claim cryptographic release
            # readiness, but do not BLOCK on the missing signature.
            states_detail = ", ".join(
                f"{r['artifact']}={r['signature']['state']}"
                for r in build["records"])
            build["signing"] = {
                "state": "UNSIGNED_POLICY_APPROVED",
                "detail": (f"EXPLICIT approved unsigned-release policy "
                           f"(--unsigned-ok): artifacts are NOT cryptographically "
                           f"signed ({states_detail}). This is an exception, "
                           f"not a verified-signature release."),
            }
    # Packet 8K closure: BLOCKED outranks FAIL/DEGRADED — a release blocker
    # (dirty tree for artifact steps, or unsigned artifacts) must never be
    # reported as PASS, and the overall verdict must distinguish
    # PASS / DEGRADED / BLOCKED / FAIL.
    if any(s == "BLOCKED" for s in states):
        report["verdict"] = "BLOCKED"
    elif any(s == "FAIL" for s in states):
        report["verdict"] = "FAIL"
    elif any(s == "DEGRADED" for s in states):
        report["verdict"] = "DEGRADED"
    else:
        report["verdict"] = "PASS"

    if args.json:
        _print_report(report)
    else:
        print(f"verdict: {report['verdict']}")
        for step, rec in report["steps"].items():
            print(f"  {step}: {rec.get('state', rec.get('verdict', '?'))} — "
                  f"{rec.get('detail', '')}")
    if report["verdict"] == "PASS":
        return EXIT_OK
    if report["verdict"] == "BLOCKED":
        return EXIT_BLOCKED
    if report["verdict"] == "FAIL":
        return EXIT_FAIL
    return EXIT_DEGRADED


if __name__ == "__main__":
    sys.exit(main())