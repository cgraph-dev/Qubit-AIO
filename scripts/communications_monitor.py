#!/usr/bin/env python3
"""Communications monitor (Packets 8B/8D).

Usage:
  python scripts/communications_monitor.py --once --dry-run --fixture \
      tests/fixtures/communications/new_items.json
  python scripts/communications_monitor.py --once --live --canary   # opt-in
  python scripts/communications_monitor.py --hourly                # 8D oneshot
  python scripts/communications_monitor.py --health                # typed health
  python scripts/communications_monitor.py --install [--enable]    # render/install
  python scripts/communications_monitor.py --rollback              # disable timer

- ``--once``: a single bounded scan; never schedules anything.
- ``--dry-run``: no persistence; prints the would-be result (redacted metadata).
- ``--fixture <json>``: hermetic run against synthetic rows (no browser).
- ``--live --canary``: OPT-IN redacted live canary; requires
  ``VOICE_BRIDGE_CANARY=1`` and attaches to the user's already-running real
  Chromium on loopback CDP. Never prints message bodies.
- ``--hourly``: the 8D authorized hourly pass (lease, bundles with real
  screenshot bytes, deduplicated notifications, retention).

INSTALL CONTRACT (never implicit):

  ``--install`` alone is a DRY-RUN: renders both templates and prints the diff,
  writes NOTHING and enables NOTHING.

  Live installation requires BOTH of these together:

      VOICE_BRIDGE_MONITOR_ENABLE=1 \
      python scripts/communications_monitor.py --install --enable

  ``--enable`` without ``--install`` is rejected, and ``--install --enable``
  without ``VOICE_BRIDGE_MONITOR_ENABLE=1`` is denied. Only the TIMER is
  enabled; the service unit is installed but never enabled.

Output is redacted metadata only: identities, read/evidence state, counts,
typed health/status. No senders, subjects, or message text.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge.config import Config  # noqa: E402
from voicebridge.communications.adapters.registry import run_scan  # noqa: E402
from voicebridge.communications.monitor import (  # noqa: E402
    install_monitor, monitor_health, optional_launch,
    rollback_monitor, run_hourly,
)

ENABLE_ENV = "VOICE_BRIDGE_MONITOR_ENABLE"


def _cfg(args) -> Config:
    return Config.load(args.config)


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="communications-monitor",
                                 description="read-only inbox monitor")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--hourly", action="store_true")
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--enable", action="store_true",
                    help="live activation: enable the hourly timer (requires "
                         "--install AND " + ENABLE_ENV + "=1)")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--launch", action="store_true",
                    help="optional approved Chromium launch (cdp_launch must be on)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixture", type=Path, default=None)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--canary", action="store_true")
    ap.add_argument("--notify", action="store_true", default=True,
                    help="send desktop/dashboard notifications (default)")
    ap.add_argument("--no-notify", action="store_true",
                    help="suppress notifications")
    ap.add_argument("--platform", choices=["gmail", "instagram", "messenger"],
                    default="gmail")
    ap.add_argument("--account", default="default")
    ap.add_argument("--config", type=Path, default=None)
    args = ap.parse_args(argv)

    if args.enable and not args.install:
        _print({"state": "denied",
                "detail": "--enable requires --install (installation dry-run "
                          "must be reviewed first)"})
        return 2

    if args.install:
        authorized = os.environ.get(ENABLE_ENV) == "1"
        live = args.enable and authorized
        cfg = _cfg(args)
        out = install_monitor(cfg, dry_run=not live, enable=live)
        _print(out)
        if args.enable and not authorized:
            print(f"\n# denied: live installation requires BOTH --enable AND "
                  f"{ENABLE_ENV}=1. Nothing was written or enabled.",
                  file=sys.stderr)
        elif not args.enable:
            print("\n# dry-run: nothing installed/enabled. To install AND "
                  "enable the timer (live activation), run exactly:\n#   "
                  f"{ENABLE_ENV}=1 "
                  "python scripts/communications_monitor.py --install --enable",
                  file=sys.stderr)
        return 0 if not (args.enable and not authorized) else 1

    if args.hourly:
        cfg = _cfg(args)
        out = run_hourly(cfg, source="live", dry_run=args.dry_run,
                         notify=not args.no_notify, canary=args.canary)
        _print(out)
        return 0 if out.get("state") in ("OK", "preview") else 1

    if args.health:
        _print(monitor_health(_cfg(args)))
        return 0

    if args.rollback:
        _print(rollback_monitor(_cfg(args)))
        return 0

    if args.launch:
        _print(optional_launch(_cfg(args)))
        return 0

    if not args.once:
        print(json.dumps({"state": "denied",
                          "detail": "manual scans only (--once / --hourly); "
                                    "scheduling is not enabled by this packet"}))
        return 2
    if args.live:
        if not args.canary or os.environ.get("VOICE_BRIDGE_CANARY") != "1":
            print(json.dumps({"state": "denied",
                              "detail": "live canary requires --canary AND "
                                        "VOICE_BRIDGE_CANARY=1 (opt-in)"}))
            return 1
        source = "live"
    elif args.fixture is not None:
        source = "fixture"
    else:
        print("usage: --once --dry-run --fixture <json> | "
              "--once --live --canary | --hourly | --health | --install "
              "[--enable] | --rollback | --launch")
        return 2

    cfg = _cfg(args)
    out = run_scan(cfg, args.platform, account_scope=args.account,
                   source=source, fixture=args.fixture, live=args.live,
                   dry_run=args.dry_run)
    _print(out)
    if out.get("state") in ("failed", "denied"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())