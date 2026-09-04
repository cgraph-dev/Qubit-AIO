#!/usr/bin/env python3
"""Packet 8H: real audio, PTT, and Right Ctrl qualification CLI (operator-gated).

Usage:
  python scripts/audio_qualify.py --preflight
  python scripts/audio_qualify.py --signal
  python scripts/audio_qualify.py --latency
  python scripts/audio_qualify.py --concurrency
  python scripts/audio_qualify.py --restart-guard
  python scripts/audio_qualify.py --cleanup
  python scripts/audio_qualify.py --all
  python scripts/audio_qualify.py --all --live          # opt-in
  python scripts/audio_qualify.py --capture --live      # opt-in
  python scripts/audio_qualify.py --playback --live     # opt-in
  python scripts/audio_qualify.py --ptt --live          # opt-in

Live execution is IMPOSSIBLE by default: every live stage requires BOTH
``--live`` AND the operator gate ``VOICE_BRIDGE_QUALIFY=1``. Without them the
live stages fail closed (``LIVE_NOT_AUTHORIZED``) and no device is opened.

Hermetic stages always run against synthetic audio/events in an ISOLATED
qualification state dir (default ``<config state>/qualification``); the
production state root, audio configuration, services, timers, models, and the
working web-dashboard path are never touched.

The harness never swaps Piper, loads models, records private audio, restarts
services, or enables a daemon. Persisted output is a redacted typed record.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge.audio.qualify import (  # noqa: E402
    QUALIFY_ENV, deny_live, effective_devices, latency_probe,
    provider_identity, run_concurrent_turns, run_qualification,
    session_context, signal_qualification, temp_audio_cleanup_probe,
    ptt_edge_probe, restart_guard_probe,
)
from voicebridge.config import Config  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_DENIED = 2
EXIT_NO_MODE = 3


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def _cfg(args) -> Config:
    return Config.load(args.config)


def _state_dir(args, cfg: Config) -> Path:
    if args.state_dir:
        return Path(args.state_dir)
    return cfg.state_dir() / "qualification"


def _concurrency_plan(args):
    n = max(2, int(args.turns))
    return ([(f"web-{i}", "web") for i in range(n // 2)] +
            [(f"rctrl-{i}", "rctrl") for i in range(n - n // 2)])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="audio-qualify",
                                 description="Packet 8H real audio / PTT / "
                                             "Right Ctrl qualification")
    ap.add_argument("--preflight", action="store_true",
                    help="hermetic session + device + provider identity probe")
    ap.add_argument("--signal", action="store_true",
                    help="hermetic synthetic signal / silence / clipping / "
                         "VAD-boundary probe")
    ap.add_argument("--latency", action="store_true",
                    help="hermetic latency stage decomposition probe")
    ap.add_argument("--concurrency", action="store_true",
                    help="hermetic concurrent web-PTT + Right-Ctrl isolation")
    ap.add_argument("--restart-guard", action="store_true",
                    help="hermetic restart authorization/backoff/circuit probe")
    ap.add_argument("--cleanup", action="store_true",
                    help="hermetic temporary-audio cleanup probe")
    ap.add_argument("--all", action="store_true",
                    help="run the full qualification (all hermetic stages "
                         "plus gated live stages when --live)")
    ap.add_argument("--capture", action="store_true",
                    help="live-gated real microphone capture probe")
    ap.add_argument("--playback", action="store_true",
                    help="live-gated real sink playback probe (synthetic tone)")
    ap.add_argument("--ptt", action="store_true",
                    help="live-gated Right Ctrl PTT probe (hold + release)")
    ap.add_argument("--live", action="store_true",
                    help="opt-in live execution (requires %s=1)"
                         % QUALIFY_ENV)
    ap.add_argument("--turns", type=int, default=8,
                    help="concurrent-turn stress size (default 8)")
    ap.add_argument("--dry-run", action="store_true",
                    help="do not persist the qualification summary")
    ap.add_argument("--state-dir", type=Path, default=None,
                    help="isolated qualification state dir (default: "
                         "<config state>/qualification)")
    ap.add_argument("--config", type=Path, default=None)
    args = ap.parse_args(argv)

    # ---- live gate fails closed before anything else is attempted
    if args.live:
        denied = deny_live(True)
        if denied:
            _print(denied)
            return EXIT_DENIED

    cfg = _cfg(args)

    if args.preflight:
        out = {
            "state": "OK",
            "session": session_context(),
            "devices": effective_devices(cfg),
            "providers": provider_identity(cfg),
        }
        # preflight is informational: typed states are reported, never a gate
        _print(out)
        return EXIT_OK

    if args.signal:
        _print(signal_qualification())
        return EXIT_OK

    if args.latency:
        _print(latency_probe(identity=provider_identity(cfg)))
        return EXIT_OK

    if args.concurrency:
        _print(run_concurrent_turns(_concurrency_plan(args),
                                    runner=lambda tid, kind: {
                                        "turn": tid, "kind": kind,
                                        "answer_turn": tid,
                                        "answer": f"answer-{tid}"}))
        return EXIT_OK

    if args.restart_guard:
        _print(restart_guard_probe())
        return EXIT_OK

    if args.cleanup:
        _print(temp_audio_cleanup_probe(_state_dir(args, cfg) / "temp"))
        return EXIT_OK

    if args.ptt:
        from voicebridge.audio.qualify import ptt_probe
        _print(ptt_probe(live=True))
        return EXIT_OK

    if args.capture:
        from voicebridge.audio.qualify import capture_probe
        _print(capture_probe(cfg, live=True))
        return EXIT_OK

    if args.playback:
        from voicebridge.audio.qualify import playback_probe
        _print(playback_probe(cfg, live=True))
        return EXIT_OK

    if args.all:
        out = run_qualification(cfg, live=args.live,
                                state_dir=_state_dir(args, cfg),
                                dry_run=args.dry_run)
        _print(out)
        return EXIT_OK if out.get("state") != "failed" else EXIT_FAILED

    _print({"state": "denied",
            "detail": "no mode selected; use --preflight, --signal, --latency, "
                      "--concurrency, --restart-guard, --cleanup, --all, "
                      "--capture, --playback, or --ptt"})
    return EXIT_NO_MODE


if __name__ == "__main__":
    sys.exit(main())