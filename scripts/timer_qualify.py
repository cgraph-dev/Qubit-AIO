#!/usr/bin/env python3
"""Packet 8I: real timer, notification, and quiet-hours qualification CLI.

Usage:
  python scripts/timer_qualify.py --preflight
  python scripts/timer_qualify.py --units
  python scripts/timer_qualify.py --lease
  python scripts/timer_qualify.py --quiet-hours
  python scripts/timer_qualify.py --notify [--live]      # live popup is opt-in
  python scripts/timer_qualify.py --aggregate
  python scripts/timer_qualify.py --journal [--live]     # live systemd journal
  python scripts/timer_qualify.py --accelerated
  python scripts/timer_qualify.py --all [--live]

Live execution is IMPOSSIBLE by default: it requires BOTH ``--live`` AND the
operator gate ``VOICE_BRIDGE_QUALIFY=1``. Without them the live stages fail
closed (``LIVE_NOT_AUTHORIZED``) and nothing is touched.

Hermetic stages always run against synthetic fixtures in an ISOLATED
qualification state dir (default ``<config state>/qualification-timer``); the
production monitor store, report root, installed units, the live timer, the
browser, and the user config are never touched. The timer is NEVER enabled and
no live unit is altered by this packet.

Persisted output is a redacted typed summary at ``0600``. No report IDs, URLs,
senders, subjects, bodies, tokens, cookies, or secrets are stored or printed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge.config import Config  # noqa: E402
from voicebridge.communications.timer_qualify import (  # noqa: E402
    QUALIFY_ENV, deny_live, qualify_accelerated, qualify_aggregate,
    qualify_journal, qualify_lease, qualify_notification, qualify_preflight,
    qualify_quiet_hours, qualify_units, run_qualification,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_DENIED = 2


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def _cfg(args) -> Config:
    return Config.load(args.config)


def _state_dir(args, cfg: Config) -> Path:
    if args.state_dir:
        return Path(args.state_dir)
    return cfg.state_dir() / "qualification-timer"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="timer-qualify",
        description="Packet 8I timer/notification/quiet-hours qualification "
                    "(hermetic by default)")
    ap.add_argument("--preflight", action="store_true",
                    help="time config + notification session + lease floor "
                         "(hermetic)")
    ap.add_argument("--units", action="store_true",
                    help="exact unit names + template checks incl. no "
                         "hardcoded DISPLAY (hermetic)")
    ap.add_argument("--lease", action="store_true",
                    help="overlap lease acquire/renew/release + monotonic + "
                         "reboot-stale (hermetic, isolated store)")
    ap.add_argument("--quiet-hours", action="store_true",
                    help="strict quiet-hours + IANA timezone validation incl. "
                         "DST fold/gap (hermetic)")
    ap.add_argument("--notify", action="store_true",
                    help="notification path: silence, new item, dedupe, quiet "
                         "hours, ack, retry bounds, redaction (hermetic)")
    ap.add_argument("--aggregate", action="store_true",
                    help="truthful aggregate state/exit code (hermetic)")
    ap.add_argument("--journal", action="store_true",
                    help="typed-event journal evidence (hermetic; --live adds "
                         "a read-only systemd journal excerpt)")
    ap.add_argument("--accelerated", action="store_true",
                    help="temporary isolated accelerated failure-path drill "
                         "(hermetic; NOT a substitute for the hourly schedule)")
    ap.add_argument("--all", action="store_true",
                    help="run every stage (hermetic + optional gated live)")
    ap.add_argument("--live", action="store_true",
                    help="opt-in live execution (requires %s=1)" % QUALIFY_ENV)
    ap.add_argument("--dry-run", action="store_true",
                    help="do not persist the qualification summary")
    ap.add_argument("--state-dir", type=Path, default=None,
                    help="isolated qualification state dir (default: "
                         "<config state>/qualification-timer)")
    ap.add_argument("--fixture", type=Path, default=None,
                    help="synthetic fixture for hermetic notification stages")
    ap.add_argument("--config", type=Path, default=None)
    args = ap.parse_args(argv)

    denied = deny_live(args.live)
    if denied:
        _print(denied)
        return EXIT_DENIED

    cfg = _cfg(args)

    if args.all:
        out = run_qualification(cfg, live=args.live,
                                state_dir=_state_dir(args, cfg),
                                fixture=args.fixture,
                                dry_run=args.dry_run)
        _print(out)
        return EXIT_OK if out.get("state") != "failed" else EXIT_FAILED

    state_dir = _state_dir(args, cfg)
    stage = None
    if args.preflight:
        stage = qualify_preflight(cfg)
    elif args.units:
        stage = qualify_units(cfg)
    elif args.lease:
        stage = qualify_lease(cfg, state_dir / "lease")
    elif args.quiet_hours:
        stage = qualify_quiet_hours(cfg)
    elif args.notify:
        stage = qualify_notification(cfg, state_dir, live=args.live,
                                     fixture=args.fixture)
    elif args.aggregate:
        stage = qualify_aggregate(cfg, state_dir)
    elif args.journal:
        stage = qualify_journal(cfg, state_dir, live=args.live)
    elif args.accelerated:
        stage = qualify_accelerated(cfg, state_dir, fixture=args.fixture)

    if stage is None:
        _print({"state": "denied",
                "detail": "no mode selected; use --preflight, --units, --lease,"
                          " --quiet-hours, --notify, --aggregate, --journal, "
                          "--accelerated, or --all"})
        return EXIT_DENIED
    _print(stage)
    return EXIT_FAILED if stage.get("state") in ("failed", "denied") \
        else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())