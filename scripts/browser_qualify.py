#!/usr/bin/env python3
"""Packet 8G: real Chromium/CDP qualification CLI (operator-gated).

Usage:
  python scripts/browser_qualify.py --preflight
  python scripts/browser_qualify.py --targets --live          # opt-in
  python scripts/browser_qualify.py --scan gmail instagram messenger --fixture F
  python scripts/browser_qualify.py --all --live              # opt-in
  python scripts/browser_qualify.py --all --live --external EXTERNAL_FIXTURE

Live execution is IMPOSSIBLE by default: it requires BOTH ``--live`` AND the
operator gate ``VOICE_BRIDGE_QUALIFY=1``. Without them the live stages fail
closed (``LIVE_NOT_AUTHORIZED``) and nothing is touched.

Hermetic stages always run against synthetic fixtures in an ISOLATED
qualification state dir (default ``<config state>/qualification``); the
production monitor store, report root, browser, services, timers, and user
config are never touched.

Persisted output is bounded redacted evidence (clipped screenshot bytes) and
typed health only. Senders, subjects, message bodies, cookies, tokens, and
secrets are never stored or printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge.config import Config  # noqa: E402
from voicebridge.communications.adapters.registry import PLATFORM_NAMES  # noqa: E402
from voicebridge.communications.qualify import (  # noqa: E402
    QUALIFY_ENV, _PLATFORMS, deny_live, qualify_fixture,
    qualify_live_scan, qualify_preflight, qualify_targets,
    run_qualification,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_DENIED = 2


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def _cfg(args) -> Config:
    return Config.load(args.config)


def _platforms(args, *, all_platforms: bool = False) -> list:
    if args.all or all_platforms:
        return list(_PLATFORMS)
    if args.scan:
        return list(args.scan)
    if args.platform:
        return list(args.platform)
    return list(_PLATFORMS)


def _state_dir(args, cfg: Config) -> Path:
    if args.state_dir:
        return Path(args.state_dir)
    return cfg.state_dir() / "qualification"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="browser-qualify",
                                 description="Packet 8G real Chromium/CDP "
                                             "qualification (read-only)")
    ap.add_argument("--preflight", action="store_true",
                    help="hermetic CDP precondition + target metadata")
    ap.add_argument("--targets", action="store_true",
                    help="read-only target discovery (live scans still need "
                         "--live + the gate)")
    ap.add_argument("--scan", nargs="*", default=None, metavar="PLATFORM",
                    help="qualification scans for the given platforms "
                         "(none = all; --platform also works)")
    ap.add_argument("--all", action="store_true",
                    help="run the full qualification (preflight+targets+scans)")
    ap.add_argument("--platform", action="append", default=None,
                    choices=sorted(PLATFORM_NAMES.keys()))
    ap.add_argument("--fixture", type=Path, default=None,
                    help="synthetic fixture for hermetic baseline/new/no-change "
                         "qualification (applied to every selected platform)")
    ap.add_argument("--external", type=Path, default=None,
                    help="synthetic fixture with baseline rows PLUS one new item "
                         "(external-item simulation)")
    ap.add_argument("--live", action="store_true",
                    help="opt-in live execution (requires %s=1)"
                         % QUALIFY_ENV)
    ap.add_argument("--dry-run", action="store_true",
                    help="do not persist qualification records/evidence")
    ap.add_argument("--state-dir", type=Path, default=None,
                    help="isolated qualification state dir (default: "
                         "<config state>/qualification)")
    ap.add_argument("--account", default="qualify")
    ap.add_argument("--config", type=Path, default=None)
    args = ap.parse_args(argv)

    # ---- live gate fails closed before anything else is attempted
    denied = deny_live(args.live)
    if denied:
        _print(denied)
        return EXIT_DENIED

    cfg = _cfg(args)

    if args.preflight:
        _print(qualify_preflight(cfg))
        return EXIT_OK

    if args.targets:
        _print(qualify_targets(cfg, _platforms(args), live=args.live))
        return EXIT_OK

    if args.all:
        platforms = _platforms(args, all_platforms=True)
        state_dir = _state_dir(args, cfg)
        fixtures = ({p: args.fixture for p in platforms}
                    if args.fixture else None)
        externals = ({p: args.external for p in platforms}
                     if args.external else None)
        out = run_qualification(cfg, platforms, live=args.live,
                                state_dir=state_dir, fixtures=fixtures,
                                external_fixtures=externals,
                                dry_run=args.dry_run)
        _print(out)
        return EXIT_OK if out.get("state") != "failed" else EXIT_FAILED

    if args.scan is not None or args.fixture is not None:
        platforms = _platforms(args)
        state_dir = _state_dir(args, cfg)
        fixture = args.fixture
        external = args.external
        results = {}
        for platform in platforms:
            rec: dict = {"platform": platform}
            if fixture is not None:
                rec["fixture"] = qualify_fixture(
                    cfg, platform, fixture=fixture, external_fixture=external,
                    state_dir=state_dir / "fixtures", account_scope=args.account)
            if args.live:
                rec["live_scan"] = qualify_live_scan(
                    cfg, platform, state_dir=state_dir,
                    account_scope=args.account, live=True,
                    persist_evidence=not args.dry_run)
            results[platform] = rec
        _print(results)
        failed = any(
            r.get("fixture", {}).get("state") == "failed" or
            r.get("live_scan", {}).get("state") in ("failed", "unavailable",
                                                    "hidden")
            for r in results.values())
        return EXIT_FAILED if failed else EXIT_OK

    _print({"state": "denied",
            "detail": "no mode selected; use --preflight, --targets, --scan, "
                      "or --all"})
    return EXIT_DENIED


if __name__ == "__main__":
    sys.exit(main())