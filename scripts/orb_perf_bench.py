#!/usr/bin/env python3
"""Reproducible paint-cost benchmark for the Qubit orb mark.

Measures the WHOLE rasterisation path a real frame takes — a QImage target,
``QPainter`` construction, antialiasing hint, ``_paint_mark`` — and reports
mean / p50 / p95 / max plus the exact pixel bytes so two runs (or two commits)
can be compared for both cost and appearance.

    ./scripts/orb_perf_bench.py                 # default: 120 frames at R=120
    ./scripts/orb_perf_bench.py --frames 240 --radius 120 --json
    ./scripts/orb_perf_bench.py --save-frame /tmp/frame.png

Exit code is always 0 unless the harness itself fails; this is a measurement
tool, not a gate (the gate lives in ``tests/test_voice_orb_fx_perf9u.py``).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BG = (11, 13, 18)


def _quantiles(xs, ) -> dict:
    if not xs:
        return {"count": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    s = sorted(xs)
    n = len(s)

    def q(frac):
        return s[min(n - 1, int(round(frac * (n - 1))))]

    return {"count": n, "mean": sum(s) / n, "p50": q(0.50),
            "p95": q(0.95), "max": s[-1]}


def run(frames: int, radius: float, size: int, state: str,
        save_frame: str | None, run_offset: float) -> dict:
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

    from voicebridge.orb import ember as E
    from voicebridge.orb import qt_app as Q

    app = QGuiApplication.instance() or QGuiApplication([])
    del app
    fam = E.family_from_core(E.state_core(state))
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    cx = cy = size / 2.0

    def paint_one(t: float) -> float:
        img.fill(QColor(*BG))
        t0 = time.perf_counter()
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        Q._paint_mark(p, cx, cy, radius, t, state, 1.0, fam)
        p.end()
        return (time.perf_counter() - t0) * 1000.0

    # Warm up (font cache, lazy imports, first-paint allocations) so the timed
    # batch is steady-state.
    for i in range(8):
        paint_one(run_offset + i * 0.03)

    costs = [paint_one(run_offset + i / 30.0) for i in range(frames)]

    img.fill(QColor(*BG))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    Q._paint_mark(p, cx, cy, radius, run_offset, state, 1.0, fam)
    p.end()
    raw = bytes(img.constBits())
    if save_frame:
        img.save(save_frame)
    return {
        "frames": frames, "radius": radius, "size": size, "state": state,
        "ms": _quantiles(costs),
        "sha256": __import__("hashlib").sha256(raw).hexdigest(),
        "raw_len": len(raw),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frames", type=int, default=120)
    ap.add_argument("--radius", type=float, default=120.0)
    ap.add_argument("--size", type=int, default=336)
    ap.add_argument("--state", default="idle")
    ap.add_argument("--t", type=float, default=0.0,
                    help="start time offset (a fixed phase = comparable pixels)")
    ap.add_argument("--save-frame", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    try:
        res = run(a.frames, a.radius, a.size, a.state, a.save_frame, a.t)
    except ImportError as exc:  # Qt-less host
        print(f"SKIP: {exc}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        m = res["ms"]
        print(f"frames={m['count']} R={res['radius']:.0f} size={res['size']} "
              f"state={res['state']}")
        print(f"  mean={m['mean']:.2f}ms p50={m['p50']:.2f}ms "
              f"p95={m['p95']:.2f}ms max={m['max']:.2f}ms")
        print(f"  frame-sha256={res['sha256'][:16]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
