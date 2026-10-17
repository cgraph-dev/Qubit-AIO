"""Qt orb mark painter: hermetic (offscreen QImage, no window, no X server).

Pins that the desktop orb's default look is the product mark, that the mark
actually paints pixels for every state, and that the ember look stays reachable
by env without ever leaving the orb blank.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtGui", reason="Qt-less env")


def _qt_app():
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.instance() or QGuiApplication([])


def _paint(state: str):
    _qt_app()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    from voicebridge.orb import ember as E
    from voicebridge.orb import qt_app as Q

    img = QImage(192, 192, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(11, 13, 18))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    Q._paint_mark(p, 96.0, 96.0, 84.0, 1.2, state, 1.0,
                  E.family_from_core(E.state_core(state)))
    p.end()
    return img


def _painted_pixels(img) -> int:
    """Pixels that differ from the near-black background."""
    from PySide6.QtGui import QColor
    bg = QColor(11, 13, 18).rgb()
    count = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            if img.pixel(x, y) != bg:
                count += 1
    return count


def test_style_defaults_to_mark_and_ember_stays_reachable(monkeypatch):
    from voicebridge.orb import qt_app as Q

    monkeypatch.delenv("QUBIT_ORB_STYLE", raising=False)
    assert Q._orb_style() == "mark"
    monkeypatch.setenv("QUBIT_ORB_STYLE", "ember")
    assert Q._orb_style() == "ember"


def test_mark_paints_for_every_state():
    for state in ("idle", "listening", "thinking", "working", "speaking", "error"):
        img = _paint(state)
        assert _painted_pixels(img) > 50, state


def test_state_hue_changes_the_painted_mark():
    idle = _paint("idle")
    listening = _paint("listening")
    assert idle != listening


def test_mark_paint_never_raises_on_bad_input():
    _qt_app()
    from PySide6.QtGui import QImage, QPainter

    from voicebridge.orb import qt_app as Q

    img = QImage(64, 64, QImage.Format_ARGB32_Premultiplied)
    p = QPainter(img)
    try:
        Q._paint_mark(p, 32.0, 32.0, 28.0, "nonsense", "not-a-state", 0.0, {})
    finally:
        p.end()
