#!/usr/bin/env python3
"""Release artifact-install E2E gate.

Installs a *built* wheel (or builds one) into a throwaway venv with a throwaway
HOME/prefix, then proves the installed artifact — never the checkout — exposes
the console entry points, reports ``qubit --version``, serves the dashboard
assets, answers ``GET /api/health``, and imports its vendored runtime. The live
operator ``QUBIT_HOME``/``HERMES_HOME`` is snapshotted before/after and the
installed probe carries an audit hook that raises if anything reads or writes
it.

The build/install/probe engine is ``scripts/clean_install_rehearsal.py`` (the
same isolation plan and typed PASS/FAIL/DEGRADED contract used by
``scripts/clean_install_check.sh`` and ``scripts/release_qualify.py``); this
script is the named, report-writing gate around it.

Usage::

    # build from the checkout and install the resulting wheel
    python scripts/artifact_install_e2e.py --build --json

    # install an already-built release wheel (no build)
    python scripts/artifact_install_e2e.py --wheel dist/qubit_voicebridge-*.whl

    # minimal install: no dashboard assets, no model writes, named profile
    python scripts/artifact_install_e2e.py --wheel ... \\
        --without-dashboard --without-models --profile minimal

    # release/CI: require the dependency install to succeed (network)
    python scripts/artifact_install_e2e.py --build --require-network \\
        --require-clean --report dist/artifact-install-e2e.json

Typed exit codes (never a silent pass):
  0 PASS      the installed artifact passed every probe
  2 FAIL      a real artifact / install / startup / isolation defect
  4 DEGRADED  a prerequisite (build backend, dashboard bundle, network) is
              unavailable, so the gate cannot honestly run

The live service, the operator's real HOME/config/state, the running unit, and
``~/.hermes`` are never touched, enabled, or restarted.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts import clean_install_rehearsal as R  # noqa: E402

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_DEGRADED = 4

SCHEMA = "qubit.artifact-install-e2e.v1"


def _tree_state(repo: Path) -> dict:
    status = R._git_tracked_status(repo)
    if status is None:
        return {"state": "UNKNOWN", "detail": "git unavailable"}
    clean = not status.strip()
    return {
        "state": "CLEAN" if clean else "DIRTY",
        "detail": status.strip()[:400],
    }


def _redact_live_homes(result: dict) -> None:
    """Keep the isolation evidence but do not publish operator home paths.

    The report can be uploaded as a CI artifact; absolute live-home paths and
    their directory listings are not needed to prove the gate held.
    """
    iso = result.get("isolation")
    if not isinstance(iso, dict):
        return
    for key in ("live_homes", "live_homes_after"):
        homes = iso.get(key)
        if not isinstance(homes, dict):
            continue
        redacted = {}
        for path, snap in homes.items():
            label = Path(path).name or "home"
            entry = dict(snap) if isinstance(snap, dict) else {"value": snap}
            entry["path"] = f"<live-home:{label}>"
            entry.pop("entries", None)
            redacted[f"<live-home:{label}>"] = entry
        iso[key] = redacted


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Release artifact-install E2E gate",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="hermetic preflight only (default)")
    parser.add_argument("--build", action="store_true",
                        help="build the wheel from --repo (default when no --wheel)")
    parser.add_argument("--wheel", type=Path, default=None,
                        help="install an already-built .whl instead of building")
    parser.add_argument("--repo", type=Path, default=REPO,
                        help="checkout to build/read (default: this repo)")
    parser.add_argument("--work-root", type=Path, default=None,
                        help="throwaway work dir (default: a fresh mktemp dir)")
    parser.add_argument("--profile", default=None,
                        help="root QUBIT_HOME at <work>/state/profiles/<name>")
    parser.add_argument("--without-models", action="store_true",
                        help="assert no model artifact is downloaded or written")
    parser.add_argument("--without-dashboard", action="store_true",
                        help="skip dashboard bundle/asset checks (health still probed)")
    parser.add_argument("--require-network", action="store_true",
                        help="treat a failed dependency install as FAIL, not DEGRADED")
    parser.add_argument("--require-clean", action="store_true",
                        help="refuse to build from a dirty checkout")
    parser.add_argument("--report", type=Path, default=None,
                        help="write the JSON report here (default: <repo>/dist/"
                             "artifact-install-e2e.json)")
    parser.add_argument("--keep", action="store_true",
                        help="keep the throwaway work dir")
    parser.add_argument("--json", action="store_true",
                        help="print the JSON report to stdout")
    args = parser.parse_args(argv)

    repo = args.repo.expanduser().resolve()
    explicit_root = args.work_root is not None
    work_root = args.work_root.expanduser().resolve() if explicit_root else None
    started = time.time()

    kwargs = dict(
        work_root=work_root,
        wheel=args.wheel.expanduser().resolve() if args.wheel else None,
        profile=args.profile,
        without_models=args.without_models,
        without_dashboard=args.without_dashboard,
    )
    if args.check:
        result = R.check(repo, **kwargs)
    else:
        result = R.run(repo, require_network=args.require_network,
                       require_clean=args.require_clean, **kwargs)

    result["schema"] = SCHEMA
    result["gate"] = "artifact-install-e2e"
    result["elapsed_s"] = round(time.time() - started, 3)
    result.setdefault("tree", _tree_state(repo))
    wheel_value = result.get("wheel")
    if isinstance(wheel_value, dict) and wheel_value.get("path"):
        # --check reports the inspected wheel as a structured record.
        artifact = dict(wheel_value)
        artifact.setdefault("source", "provided")
        result["artifact"] = artifact
    elif isinstance(wheel_value, dict):
        result["artifact"] = {"source": None, "wheel": None}
    else:
        result["artifact"] = {
            "wheel": wheel_value,
            "path": result.get("wheel_path"),
            "sha256": result.get("wheel_sha256"),
            "source": result.get("wheel_source"),
            "dashboard": result.get("artifact_dashboard"),
        }
    _redact_live_homes(result)

    report_path = (args.report.expanduser().resolve() if args.report else
                   repo / "dist" / "artifact-install-e2e.json")
    result["report"] = str(report_path)
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        result.setdefault("warnings", []).append(
            f"could not write report {report_path}: {exc}")

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"artifact-install-e2e: {result.get('verdict', result['state'])}")
        print(f"  wheel  : {result.get('wheel') or '(none)'}"
              f" [{result.get('wheel_source') or 'n/a'}]")
        if result.get("version"):
            print(f"  version: {result['version']}")
        print(f"  report : {result.get('report', '(not written)')}")
        for reason in result.get("reasons", []):
            print(f"  - {reason}")

    if not args.check and not explicit_root and not args.keep:
        shutil.rmtree(result["plan"]["work_root"], ignore_errors=True)

    return R._exit_for(result["state"])


if __name__ == "__main__":
    raise SystemExit(main())
