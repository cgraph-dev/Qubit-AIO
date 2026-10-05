#!/usr/bin/env python3
"""Detached work runner — survive a `voicebridge` restart.

WHY THIS EXISTS
---------------
`qubit-voicebridge.service` runs `voicebridge run`, which spawns the ui-tui
node process, which spawns `tui_gateway.entry` — the process hosting the
interactive agent session. All three share one systemd cgroup. A
`systemctl --user restart qubit-voicebridge` therefore tears down the agent
session AND any `delegate_task` children attached to its turn, so long work
dies mid-flight and only resumes when the owner types `continue`.

This script is the escape hatch: it is launched with `start_new_session=True`
(setsid), which puts it in its OWN session and process group, OUTSIDE the
service cgroup. A service restart does not signal it. It runs the given
command to completion and records a durable result file the next session (or
the owner) can read.

USAGE
-----
    python scripts/detached_run.py --label <name> [--timeout S] -- <command...>

Writes to ~/.local/state/qubit-voicebridge/runtime/detached/<label>/:
    result.json   {label, pid, argv, started_at, finished_at, exit_code,
                   status, stdout_tail, stderr_tail, log}
    out.log       full combined stdout/stderr
result.json is written atomically (tmp + rename) so a reader never sees a
half-written file. `status` is one of: running -> ok | failed | timeout.

READ RESULTS
------------
    python scripts/detached_run.py --list
    python scripts/detached_run.py --show <label>

Never restarts or signals the service — that is the caller's decision.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TAIL_BYTES = 4000


def _state_dir() -> Path:
    """The runtime dir, honouring the same env the bridge uses.

    ``QUBIT_HOME`` here points AT the runtime dir (``.../qubit-voicebridge/
    runtime``), so ``/runtime`` must not be appended to it — doing so produced
    ``.../runtime/runtime/detached`` and the reader looked in the wrong place.
    """
    env = os.environ.get("QUBIT_RUNTIME_DIR") or os.environ.get("HERMES_RUNTIME_DIR")
    if env:
        return Path(env).expanduser() / "detached"
    home = os.environ.get("QUBIT_HOME") or os.environ.get("HERMES_HOME")
    if home:
        base = Path(home).expanduser()
        if base.name != "runtime":
            base = base / "runtime"
        return base / "detached"
    return (Path.home() / ".local" / "state" / "qubit-voicebridge"
            / "runtime" / "detached")


def _write_atomic(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _tail(text: str) -> str:
    return text[-TAIL_BYTES:] if len(text) > TAIL_BYTES else text


def run(label: str, argv: List[str], timeout: Optional[float]) -> int:
    d = _state_dir() / label
    d.mkdir(parents=True, exist_ok=True)
    result_path = d / "result.json"
    log_path = d / "out.log"

    meta: Dict[str, Any] = {
        "label": label,
        "pid": os.getpid(),
        "argv": argv,
        "started_at": time.time(),
        "finished_at": None,
        "exit_code": None,
        "status": "running",
        "stdout_tail": "",
        "stderr_tail": "",
        "log": str(log_path),
    }
    _write_atomic(result_path, meta)

    # Detach from the controlling terminal so a service restart (or the
    # owner's shell closing) cannot SIGHUP us mid-run.
    try:
        os.setsid()
    except OSError:
        pass  # already a session leader — fine

    # Ignore SIGHUP/SIGINT: the whole point is to outlive the parent turn.
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, signal.SIG_IGN)
        except (ValueError, OSError):
            pass

    try:
        with open(log_path, "w", encoding="utf-8") as log:
            proc = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT,
                                  text=True, timeout=timeout)
        rc, status = proc.returncode, ("ok" if proc.returncode == 0 else "failed")
    except subprocess.TimeoutExpired:
        rc, status = 124, "timeout"
    except FileNotFoundError:
        rc, status = 127, "failed"
        log_path.write_text(f"{argv[0]}: not found\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - always record *something*
        rc, status = 1, "failed"
        log_path.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")

    body = ""
    try:
        body = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    meta.update({
        "finished_at": time.time(),
        "exit_code": rc,
        "status": status,
        "stdout_tail": _tail(body),
    })
    _write_atomic(result_path, meta)
    return rc


def _launch_detached(argv: List[str], label: str) -> Tuple[bool, str]:
    """Re-launch this run as a transient systemd unit OUTSIDE the service cgroup.

    ``setsid()`` alone is NOT enough: cgroup membership is inherited at fork,
    so a child started inside ``qubit-voicebridge.service`` stays in that
    cgroup even after it leads its own session — and ``KillMode=control-group``
    (or a plain ``systemctl restart``) then kills it anyway. Measured: a
    ``setsid``-ed worker still reported
    ``0::/user.slice/.../qubit-voicebridge.service`` in ``/proc/<pid>/cgroup``.

    ``systemd-run --user --scope`` puts the process in its OWN transient scope,
    which the bridge service does not own, so a service restart cannot signal
    it. Returns ``(launched, reason)``; ``launched=False`` means the caller
    should fall back to running inline.
    """
    if os.environ.get("_QUBIT_DETACHED_ACTIVE") == "1":
        return False, "already detached"
    if not shutil.which("systemd-run"):
        return False, "systemd-run not available"
    unit = f"qubit-detached-{label}"[:64]
    cmd = [
        "systemd-run", "--user", "--collect", "--no-block",
        f"--unit={unit}",
        "--property=Description=Qubit detached work: " + label,
        "--property=Type=oneshot",
        "--property=TimeoutStopSec=infinity",
        "--setenv=_QUBIT_DETACHED_ACTIVE=1",
        "--setenv=QUBIT_HOME=" + os.environ.get("QUBIT_HOME", ""),
        "--setenv=QUBIT_RUNTIME_DIR=" + os.environ.get("QUBIT_RUNTIME_DIR", ""),
        *argv,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    if r.returncode == 0:
        return True, unit
    err = (r.stderr or r.stdout or "systemd-run failed").strip()
    # A live job already owns this label's unit (and therefore its result
    # directory). Signal it distinctly so the caller refuses instead of
    # falling back inline and clobbering the running job's records.
    if "already loaded" in err or "already exists" in err or "in use" in err:
        return False, "unit-name-collision"
    return False, err[:200]


def cmd_list() -> int:
    root = _state_dir()
    if not root.is_dir():
        print("(no detached runs)")
        return 0
    rows = []
    for d in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        rp = d / "result.json"
        if not rp.is_file():
            continue
        try:
            m = json.loads(rp.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows.append(m)
    if not rows:
        print("(no detached runs)")
        return 0
    print(f"{'LABEL':28s} {'STATUS':8s} {'EXIT':>5s}  {'AGE':>8s}  CMD")
    for m in rows:
        age = time.time() - float(m.get("started_at") or 0)
        cmd = " ".join(m.get("argv") or [])[:60]
        print(f"{str(m.get('label'))[:28]:28s} {str(m.get('status')):8s} "
              f"{str(m.get('exit_code')):>5s}  {age:7.0f}s  {cmd}")
    return 0


def cmd_show(label: str) -> int:
    rp = _state_dir() / label / "result.json"
    if not rp.is_file():
        print(f"no such detached run: {label}", file=sys.stderr)
        return 1
    m = json.loads(rp.read_text(encoding="utf-8"))
    print(json.dumps(m, indent=2, default=str))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="detached_run", add_help=True,
                                 description="Run a command detached from the "
                                             "service cgroup; survives restarts.")
    ap.add_argument("--label")
    ap.add_argument("--timeout", type=float, default=None)
    ap.add_argument("--no-scope", action="store_true",
                    help="run inline instead of a transient systemd scope")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--show", metavar="LABEL")
    ap.add_argument("command", nargs=argparse.REMAINDER,
                    help="-- <command...>")
    ns = ap.parse_args(argv)

    if ns.list:
        return cmd_list()
    if ns.show:
        return cmd_show(ns.show)
    cmd = [c for c in (ns.command or []) if c != "--"]
    if not ns.label or not cmd:
        ap.error("--label and a -- command are required")

    # Prefer a transient systemd scope: it is the only thing that actually
    # leaves the service cgroup, so a restart cannot kill the work.
    if not ns.no_scope:
        me = str(Path(__file__).resolve())
        py = str(Path(sys.executable).resolve())
        # The inner (--no-scope) run must receive the SAME timeout, or the
        # caller's cap is silently dropped and the job runs unbounded.
        inner = [py, me, "--label", ns.label, "--no-scope"]
        if ns.timeout is not None:
            inner += ["--timeout", str(ns.timeout)]
        launched, why = _launch_detached(inner + ["--"] + cmd, ns.label)
        if launched:
            print(json.dumps({"state": "detached", "unit": why,
                              "label": ns.label,
                              "read": f"python scripts/detached_run.py --show {ns.label}"}))
            return 0
        if why == "unit-name-collision":
            # Refuse rather than fall back inline: an inline run would share
            # this label's result directory with the live job and clobber both
            # records. The caller must choose a fresh label.
            print(f"[detached_run] label '{ns.label}' is already in use by a "
                  "live job; pass a distinct --label", file=sys.stderr)
            return 2
        print(f"[detached_run] scope launch unavailable ({why}); "
              "running inline", file=sys.stderr)
    return run(ns.label, cmd, ns.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
