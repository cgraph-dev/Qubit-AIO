"""Bounded performance smoke test for the Qt Qubit-mark orb painter.

Renders 120 frames offscreen (no window, no X server) and asserts two things:

  1. the MEAN per-frame cost of the whole batch stays under a generous cap
     (25 ms/frame on this host) — the batch is timed as a whole, never
     per-frame, so a single scheduler hiccup cannot flake the test;
  2. per-frame paint work is bounded: the number of primitives emitted each
     frame never exceeds ``PRIM_CAP`` and never drifts upward across the
     batch, so an effect that allocates more and more every frame is caught.

This is a smoke bound, not a benchmark: it must stay green on a busy CI box.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtGui", reason="Qt-less env")

CX = CY = 80.0
R = 70.0
SIZE = 160
BG = (11, 13, 18)

FRAMES = 120
#: Generous mean cap: the mark paints a few dozen anti-aliased primitives into
#: a 160x160 ARGB image. Real cost is well under 2 ms/frame; 25 ms leaves room
#: for a heavily loaded host while still failing on an accidental O(n^2) blowup.
MEAN_FRAME_CAP_S = 0.025
#: Fixed upper bound on primitives per frame. The pre-FX mark is ~16; the full
#: FX stack (trails + sparkles + three satellite shells + the binary field)
#: measures 195..239 at R=70 with three shells orbiting. The RGB streak field
#: adds at most one stroke per body (~80), so 420 still leaves headroom while
#: remaining a ceiling only an unbounded per-frame allocation can cross.
PRIM_CAP = 420
#: How much the per-frame primitive count may OSCILLATE. Density is periodic —
#: the 7 s comet sweep adds a 12-sample tail and glints pulse in and out — so a
#: "second half must not out-allocate the first" comparison is a phase lottery,
#: not an invariant. This band is real drift detection: an unbounded per-frame
#: allocation widens the spread or crosses PRIM_CAP.
PRIM_SPREAD_CAP = 120


class _Counter:
    """Forwarding QPainter proxy that counts emitted primitives per frame."""

    def __init__(self, painter) -> None:
        object.__setattr__(self, "_p", painter)
        object.__setattr__(self, "count", 0)

    def _tick(self):
        object.__setattr__(self, "count", self.count + 1)

    def drawEllipse(self, *args):
        self._tick()
        self._p.drawEllipse(*args)

    def drawPath(self, path):
        self._tick()
        self._p.drawPath(path)

    def drawLine(self, *args):
        self._tick()
        self._p.drawLine(*args)

    def __getattr__(self, name):
        return getattr(self._p, name)


def _run_batch() -> tuple:
    from PySide6.QtGui import QGuiApplication, QColor, QImage, QPainter

    from voicebridge.orb import ember as E
    from voicebridge.orb import qt_app as Q

    app = QGuiApplication.instance() or QGuiApplication([])
    del app
    fam = E.family_from_core(E.state_core("idle"))
    img = QImage(SIZE, SIZE, QImage.Format_ARGB32_Premultiplied)

    def paint_one(t: float) -> int:
        img.fill(QColor(*BG))
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing, True)
        counter = _Counter(painter)
        Q._paint_mark(counter, CX, CY, R, t, "idle", 1.0, fam)
        painter.end()
        return counter.count

    # Warm up so first-call lazy work is not charged to the timed batch.
    for i in range(5):
        paint_one(i * 0.1)

    counts = []
    start = time.perf_counter()
    for i in range(FRAMES):
        counts.append(paint_one(i / 30.0))
    elapsed = time.perf_counter() - start
    return elapsed, counts


def test_qt_mark_batch_mean_cost_and_bounded_primitives():
    elapsed, counts = _run_batch()
    mean = elapsed / float(FRAMES)
    assert counts, "no frames painted"
    assert mean < MEAN_FRAME_CAP_S, (
        f"mean Qt mark frame cost {mean * 1000:.2f}ms exceeds cap "
        f"{MEAN_FRAME_CAP_S * 1000:.0f}ms")

    top = max(counts)
    assert top <= PRIM_CAP, f"per-frame primitives {top} exceed cap {PRIM_CAP}"

    # Bounded, not drifting: the spread stays inside one fixed band. (The
    # earlier "second half <= first half" check was a phase lottery — the
    # comet's periodic sweep makes the batch max land in either half.)
    spread = max(counts) - min(counts)
    assert spread <= PRIM_SPREAD_CAP, (
        f"per-frame primitives swing by {spread} (>{PRIM_SPREAD_CAP}): "
        "unbounded or leaking per-frame work")
