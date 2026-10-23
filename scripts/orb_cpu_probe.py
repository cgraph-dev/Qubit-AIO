#!/usr/bin/env python3
"""Measure the orb's steady-state CPU% in a REAL window, from /proc.

Spawns a child process that builds the actual ``QtVoiceOrb`` window (offscreen
Qt platform, so it needs no display), lets it settle, then samples
``/proc/<pid>/stat`` utime+stime over a window and reports how much of one core
the orb process used. Also asks the child for its own frame/paint counters so
the CPU number can be tied to what was actually drawn.

    ./scripts/orb_cpu_probe.py                 # 10 s sample, default orb
    ./scripts/orb_cpu_probe.py --seconds 20 --frames-csv /tmp/cpu.csv
    ./scripts/orb_cpu_probe.py --no-orb        # control: the app, no repaint

Nothing here is a gate; it prints numbers and exits 0.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

CHILD = r"""
import json, os, sys, time
sys.path.insert(0, %(root)r)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from voicebridge.orb import qt_app as Q, geometry as G, ember as E
app = QApplication.instance() or QApplication([])
G.set_orb_size(int(%(size)d))
win = Q.QtVoiceOrb(poll=False, enable_tray=False)
win.show()
# Let the first paints, font caches and option loads settle before sampling.
deadline = time.monotonic() + 2.0
while time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.005)
if %(no_orb)r:
    win._frame_timer.stop()
start_frames = win.frames
t_end = time.monotonic() + %(seconds)f
last_report = time.monotonic()
rows = []
while time.monotonic() < t_end:
    app.processEvents()
    time.sleep(0.004)
    now = time.monotonic()
    if now - last_report >= 1.0:
        rows.append([round(now, 3), win.frames])
        last_report = now
print("@@RESULT@@" + json.dumps({
    "frames": win.frames - start_frames,
    "interval_ms": win._frame_timer.interval(),
    "frame_ms_budget": E.FRAME_MS,
    "frames_timeline": rows,
    "paint_stats": Q._paint_mark._frame_stats.as_dict()
        if getattr(Q._paint_mark, "_frame_stats", None) is not None else None,
}))
"""


def _cpu_ticks(pid: int) -> int:
    with open(f"/proc/{pid}/stat", "r") as fh:
        parts = fh.read().rsplit(")", 1)[1].split()
    # utime = field 14, stime = 15 (1-indexed in proc(5)); after the ") " split
    # we are at field 3, so index 11 and 12.
    return int(parts[11]) + int(parts[12])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--size", type=int, default=0, help="orb px (0 = default)")
    ap.add_argument("--no-orb", action="store_true",
                    help="control run: window up, animation loop stopped")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    size = a.size
    if not size:
        sys.path.insert(0, str(REPO_ROOT))
        try:
            from voicebridge.orb import geometry as G
            size = int(G.ORB_PX)
        except Exception:
            size = 168

    hz = os.sysconf("SC_CLK_TCK")
    code = CHILD % {"root": str(REPO_ROOT), "size": size,
                    "seconds": a.seconds, "no_orb": a.no_orb}
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, env=env)
    # Wait for the child's settle phase to finish (it prints only at the end),
    # so sample exactly during its measurement window.
    time.sleep(2.5)
    try:
        t0_ticks = _cpu_ticks(proc.pid)
        t0 = time.monotonic()
        time.sleep(max(0.5, a.seconds - 0.6))
        t1 = time.monotonic()
        t1_ticks = _cpu_ticks(proc.pid)
    except FileNotFoundError:
        proc.wait(timeout=30)
        print("child exited too early", file=sys.stderr)
        return 1
    out = proc.stdout.read() if proc.stdout else ""
    proc.wait(timeout=60)
    payload = {}
    for line in out.splitlines():
        if line.startswith("@@RESULT@@"):
            payload = json.loads(line[len("@@RESULT@@"):])
    dt = max(1e-6, t1 - t0)
    cpu_pct = 100.0 * ((t1_ticks - t0_ticks) / hz) / dt
    frames = payload.get("frames", 0)
    fps = frames / max(1e-6, a.seconds)
    res = {"cpu_pct_of_one_core": round(cpu_pct, 2), "sample_s": round(dt, 2),
           "frames_drawn": frames, "fps": round(fps, 1),
           "frame_ms_budget": payload.get("frame_ms_budget"),
           "timer_interval_ms": payload.get("interval_ms"),
           "orb_px": size, "animation_stopped": a.no_orb}
    if a.json:
        print(json.dumps({**res, "paint_stats": payload.get("paint_stats"),
                          "frames_timeline": payload.get("frames_timeline")},
                         indent=2))
    else:
        print(f"orb_px={size} sample={res['sample_s']}s")
        print(f"  CPU: {res['cpu_pct_of_one_core']}% of one core")
        print(f"  frames drawn in {a.seconds:.0f}s: {frames} "
              f"({res['fps']} fps)  timer interval={res['timer_interval_ms']}ms")
        if payload.get("paint_stats"):
            print(f"  frame ledger: {payload['paint_stats']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
