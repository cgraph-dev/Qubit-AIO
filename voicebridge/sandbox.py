"""Bounded tool execution — OpenSandbox's provision/run/terminate shape
(STEAL_LIST item 7) applied to local scripts and scrapers.

Every whitelisted tool call gets:
  * an explicit timeout (a hung tool can never wedge the loop),
  * bounded captured output (stdout/stderr capped -> no memory blowup),
  * guaranteed cleanup (the whole process group is killed on timeout).

No Docker/K8s per call — these are trusted local tools; only the failure
modes are sandboxed.
"""
from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from typing import Callable


@dataclass
class RunResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    rc: int | None = None
    timed_out: bool = False
    truncated: bool = False
    cancelled: bool = False


def run_tool(cmd: str | list[str], timeout_s: float = 60.0,
             max_output: int = 200_000, shell: bool | None = None,
             cwd: str | None = None,
             cancel_check: Callable[[], bool] | None = None) -> RunResult:
    """Run a tool with hard timeout + bounded output + process-group cleanup.

    `cmd` is a string (run via shell, like the templates' scripts) or an argv
    list. On timeout the entire process group gets SIGKILL — children included.

    `cancel_check` (Packet 3A): when given, output is collected in short slices
    and a True return triggers the same process-group SIGKILL; the result is
    flagged `cancelled` instead of `timed_out`.
    """
    if shell is None:
        shell = isinstance(cmd, str)
    try:
        proc = subprocess.Popen(
            cmd, shell=shell, cwd=cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True)
    except Exception as exc:
        return RunResult(ok=False, stderr=str(exc)[:500])

    def _kill_group() -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    timed_out = False
    cancelled = False
    if cancel_check is None:
        try:
            out, err = proc.communicate(timeout=max(1.0, float(timeout_s)))
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group()
            try:
                out, err = proc.communicate(timeout=2.0)
            except Exception:
                out, err = "", ""
    else:
        import time as _time
        deadline = _time.monotonic() + max(1.0, float(timeout_s))
        out = err = ""
        while True:
            try:
                out, err = proc.communicate(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                break
            try:
                if cancel_check():
                    cancelled = True
                    _kill_group()
                    try:
                        out, err = proc.communicate(timeout=2.0)
                    except Exception:
                        out, err = "", ""
                    break
            except Exception:
                pass
            if _time.monotonic() >= deadline:
                timed_out = True
                _kill_group()
                try:
                    out, err = proc.communicate(timeout=2.0)
                except Exception:
                    out, err = "", ""
                break
    truncated = False
    out = (out or "").strip()
    err = (err or "").strip()
    if len(out) > max_output:
        out = out[:max_output]
        truncated = True
    if len(err) > 5_000:
        err = err[:5_000]
        truncated = True
    return RunResult(ok=(not timed_out and not cancelled and proc.returncode == 0),
                     stdout=out, stderr=err, rc=proc.returncode,
                     timed_out=timed_out, truncated=truncated,
                     cancelled=cancelled)
