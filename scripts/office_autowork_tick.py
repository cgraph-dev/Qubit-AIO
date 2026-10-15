#!/usr/bin/env python3
"""Run one autonomous office tick, on a schedule, without an agent turn.

WHY THIS EXISTS
---------------
`voicebridge.office.autowork` is a complete driver -- it picks the eligible
seats, runs one real attributed turn per seat, settles the ledger and writes
`office_event` rows -- and NOTHING ever called it. `office autowork --now` only
fires when a human types it, and the office showed every agent "Waiting for
instructions" forever, which reads as "the office is broken" when in fact the
engine was simply never started.

The module's own docs name the intended trigger ("the CLI or the cron
surface"), so this is the missing binding rather than new machinery.

WHY A SCRIPT AND NOT `cron add`
-------------------------------
`qubit cron add` takes a task TEXT, which is dispatched to an agent: every tick
would burn a model call just to decide to run the tick. The vendored store
already supports script jobs (`no_agent: true`, as `auto-push.sh` does), so the
scheduler runs this file directly and no model is consulted to start work.

WHAT IT DOES NOT DO
-------------------
* It does not enable the driver. `office.autowork.enabled` remains the owner's
  switch; a tick is refused while it is off, and this script reports that
  refusal instead of overriding it. Spending money stays an explicit decision.
* It does not force a tick that is not due. `AWA.run_now` is the "run one now"
  entry; `due()` is what the cadence means. The script asks for a tick only when
  the cadence says one is owed, so running it every 5 minutes does not produce
  twelve ticks an hour.
* It never raises on a refusal -- a disabled driver, a spent envelope or a
  missing model are all expected states, and a cron job that exits non-zero
  forever trains its reader to ignore it. Exit 0 always; the STATUS LINE is the
  signal, and `--json` gives the full record.

USAGE
-----
    python3 scripts/office_autowork_tick.py              # tick if due
    python3 scripts/office_autowork_tick.py --force      # tick regardless
    python3 scripts/office_autowork_tick.py --status     # report, never run
    python3 scripts/office_autowork_tick.py --json       # machine-readable
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

REPO = Path(__file__).resolve().parent.parent
VENV_PY = REPO / ".venv" / "bin" / "python"


def _python() -> str:
    """The interpreter that can import voicebridge.

    Prefer the checkout's venv: the system python3 has no voicebridge deps (and
    no pytest either, which is the same trap in a different costume).
    """
    if VENV_PY.exists():
        return str(VENV_PY)
    return sys.executable


# The tick is run in-process rather than through `python -m voicebridge.cli`:
# the CLI verb is confirm-gated for a human at a terminal, and threading a
# confirmation through cron would either be a fake flag or an unattended
# approval. Calling the driver directly keeps the scheduler's authority narrow:
# it may run a tick, and it may not change the switch that permits one.
_TICK = r"""
import json, sys
from voicebridge import config as C
from voicebridge.office import autowork as AWA

mode = sys.argv[1] if len(sys.argv) > 1 else "due"
cfg = C.Config.load()

if mode == "status":
    print(json.dumps({"ran": False, "reason": "status-only",
                      "status": AWA.status(cfg), "assess": AWA.assess(cfg)},
                     default=str))
    raise SystemExit(0)

if mode == "force":
    # run_now holds the flag AND the cadence open for one call. That is the
    # owner's manual trigger; a scheduler asking for it explicitly is an
    # intentional override, so --force maps here and only here.
    out = AWA.run_now(cfg, model=None, requested_by="cron:office-autowork")
    print(json.dumps({"ran": bool(out.get("ran") or out.get("approved")),
                      "forced": True, "result": out}, default=str))
    raise SystemExit(0)

# The scheduled path: plain tick(). It enforces BOTH gates itself -- the
# office.autowork.enabled flag (E-stop first, then the flag) and the cadence
# via _due(). Running this every few minutes therefore produces a tick only
# when one is actually owed, and never overrides the owner's switch.
out = AWA.tick(cfg, model=None, requested_by="cron:office-autowork")
reason = out.get("skipped") or (
    "disabled" if not out.get("enabled", True) else None)
print(json.dumps({
    "ran": bool(out.get("ran") or out.get("approved")),
    "reason": reason, "result": out}, default=str))
"""


def _run(args: argparse.Namespace) -> Dict[str, Any]:
    mode = "status" if args.status else ("force" if args.force else "due")
    proc = subprocess.run(
        [_python(), "-c", _TICK, mode],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=args.timeout,
        # The tick loads config and talks to the office store; it must not
        # inherit a stray TMPDIR that would relocate the store mid-run.
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    payload: Dict[str, Any] = {"mode": mode, "exit": proc.returncode}
    out = (proc.stdout or "").strip()
    if out:
        try:
            payload.update(json.loads(out.splitlines()[-1]))
        except ValueError:
            payload["raw"] = out[-2000:]
    err = (proc.stderr or "").strip()
    if err:
        # Keep the tail: a traceback's last frames are the useful part, and a
        # cron log that drops them is a cron log nobody can act on.
        payload["stderr"] = err[-1200:]
    return payload


def _line(rec: Dict[str, Any]) -> str:
    """One human-readable status line -- the actual signal for a cron log."""
    st = rec.get("status") or {}
    res = rec.get("result") or {}
    if rec.get("reason") == "disabled" or res.get("enabled") is False:
        return ("office-autowork: OFF (office.autowork.enabled=false) "
                "-- no tick, nothing read or written")
    if rec.get("reason") == "not_due":
        return (f"office-autowork: not due (every {res.get('every_seconds')}s, "
                f"last_tick_at={res.get('last_tick_at')})")
    if rec.get("reason") == "status-only":
        return (f"office-autowork: enabled={st.get('enabled')} "
                f"every={st.get('every_seconds')}s "
                f"last_tick_at={st.get('last_tick_at')}")
    if rec.get("ran"):
        ran = res.get("ran")
        n = len(ran) if isinstance(ran, list) else res.get("ran")
        return f"office-autowork: tick ran ({n if n is not None else '?'} seat(s))"
    if res.get("state") == "HALTED":
        return "office-autowork: HALTED -- the emergency stop is latched"
    return f"office-autowork: no tick (exit {rec.get('exit')})"


def main(argv: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="run a tick even if the cadence says one is not due")
    ap.add_argument("--status", action="store_true",
                    help="report only; never run a tick")
    ap.add_argument("--json", action="store_true",
                    help="emit the full record instead of one status line")
    ap.add_argument("--timeout", type=int, default=900,
                    help="seconds to allow one tick (default 900)")
    args = ap.parse_args(argv)

    rec = _run(args)
    if args.json:
        print(json.dumps(rec, indent=2, default=str))
    else:
        print(_line(rec))
        # A refusal is not a failure of the JOB. Exit 0 so the scheduler keeps
        # firing; the line above is what a reader acts on.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
