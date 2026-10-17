"""Qt mark life effects: heartbeat, depth, trails, sparkles, reduced motion.

Hermetic (offscreen QImage, no window, no X server). Pins the FX contract the
Tk sibling mirrors: the nucleus beats a lub-dub, particles split into far/near
z-layers, sparkles come and go deterministically, and reduced motion is a
byte-identical still frame no matter the clock.
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

BG = (11, 13, 18)


def _qt_app():
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.instance() or QGuiApplication([])


def _paint(t=1.2, state="idle", family=None, brightness=1.0, size=192):
    _qt_app()
    from PySide6.QtGui import QColor, QImage, QPainter

    from voicebridge.orb import ember as E
    from voicebridge.orb import qt_app as Q

    if family is None:
        family = E.family_from_core(E.state_core(state))
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(*BG))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    Q._paint_mark(p, size / 2.0, size / 2.0, size / 2.0 - 6.0, t, state,
                  brightness, family)
    p.end()
    return img


def _bytes(img) -> bytes:
    return bytes(img.constBits())


def _diff(a, b) -> int:
    return sum(1 for x, y in zip(_bytes(a), _bytes(b)) if x != y)


def _central_ink(img) -> int:
    """Non-background pixels in a small box around the centre (nucleus only)."""
    from PySide6.QtGui import QColor

    bg = QColor(*BG).rgb()
    c = img.width() // 2
    n = 0
    for y in range(c - 6, c + 7):
        for x in range(c - 6, c + 7):
            if img.pixel(x, y) != bg:
                n += 1
    return n


# -- (a) heartbeat ----------------------------------------------------------

def test_heartbeat_envelope_double_thump_and_period():
    from voicebridge.orb import qt_app as Q

    assert Q.HEART_PERIOD_S == pytest.approx(0.95)
    # Primary thump at the cycle start, second at 22% in, at 0.62 amplitude.
    assert Q.heartbeat_envelope(0.0) > 0.99
    assert Q.heartbeat_envelope(Q.HEART_SECOND_PHASE * Q.HEART_PERIOD_S) == (
        pytest.approx(Q.HEART_SECOND_AMP, abs=0.02))
    # Quiet between the thumps (0.16s wide, so 0.10 is only partially lit).
    assert Q.heartbeat_envelope(0.40) < 0.05
    # Repeats every period, phase-locked.
    for k in range(1, 5):
        assert Q.heartbeat_envelope(k * Q.HEART_PERIOD_S) == pytest.approx(1.0)
        assert Q.heartbeat_envelope(k * Q.HEART_PERIOD_S + 0.10) == pytest.approx(
            Q.heartbeat_envelope(0.10), abs=1e-9)


def test_heartbeat_changes_nucleus_size_and_alpha_across_phases():
    peak = _paint(0.0)     # beat envelope == 1
    quiet = _paint(0.5)    # beat envelope == 0
    assert _diff(peak, quiet) > 0
    # Bigger and brighter at the beat peak.
    assert _central_ink(peak) > _central_ink(quiet)
    assert peak.pixelColor(96, 96) != quiet.pixelColor(96, 96)


# -- (b) depth / far-near z-order ------------------------------------------

def test_bead_layers_flip_between_far_and_near_halves():
    from voicebridge.orb import qt_app as Q

    a = {i: layer for i, layer, _ang, _d in Q.mark_particle_layers(0.0)}
    b = {i: layer for i, layer, _ang, _d in Q.mark_particle_layers(1.1)}
    assert set(a.values()) == {"far", "near"}
    assert set(b.values()) == {"far", "near"}
    assert any(a[i] != b[i] for i in a), (a, b)


def test_depth_derives_size_alpha_and_parallax_numbers():
    from voicebridge.orb import qt_app as Q

    # depth 1 (nearest) -> 1.30x radius / alpha 1.00; depth 0 -> the floor.
    assert Q.DEPTH_RADIUS_MIN + Q.DEPTH_RADIUS_GAIN == pytest.approx(1.30)
    assert Q.DEPTH_ALPHA_MIN + Q.DEPTH_ALPHA_GAIN == pytest.approx(1.00)
    assert Q.DEPTH_RADIUS_MIN == pytest.approx(0.55)
    assert Q.DEPTH_ALPHA_MIN == pytest.approx(0.35)
    assert Q.DEPTH_LIFT_MIN + Q.DEPTH_LIFT_GAIN * 1.0 == pytest.approx(0.35)
    assert Q.DEPTH_PARALLAX == pytest.approx(0.06)


# -- (c) sparkles -----------------------------------------------------------

def test_sparkles_appear_and_vanish_deterministically():
    from voicebridge.orb import qt_app as Q

    first = Q.mark_sparkles(0.0)
    assert 0 < len(first) <= Q.SPARKLE_COUNT
    for _i, _r, _ang, phase, fade, size, alpha in first:
        assert 0.0 <= phase < Q.SPARKLE_VISIBLE_FRAC
        assert 0.0 <= fade <= 1.0
        assert 0.0 < size <= Q.SPARKLE_SIZE_BASE + Q.SPARKLE_SIZE_PEAK + 1e-9
        assert 0.0 < alpha <= Q.SPARKLE_MAX_ALPHA + 1e-9
    # Reproducible from t alone.
    assert [tuple(s) for s in first] == [tuple(s) for s in Q.mark_sparkles(0.0)]
    # There are phases with glints and phases with none.
    seen = [len(Q.mark_sparkles(k / 100.0)) for k in range(0, 400)]
    assert any(n > 0 for n in seen)
    assert any(n == 0 for n in seen)


def test_sparkles_actually_paint_pixels(monkeypatch):
    from voicebridge.orb import qt_app as Q

    with_spark = _paint(0.0)
    with monkeypatch.context() as m:
        m.setattr(Q, "SPARKLE_COUNT", 0)
        without = _paint(0.0)
    assert _diff(with_spark, without) > 0


# -- (d) reduced motion is a still frame -----------------------------------

def test_reduced_motion_is_byte_identical_across_t(monkeypatch):
    from voicebridge.orb import qt_app as Q

    monkeypatch.setenv("QUBIT_REDUCED_MOTION", "1")
    a = _paint(1.0)
    b = _paint(37.4)
    assert _bytes(a) == _bytes(b)
    assert Q.mark_sparkles(1.0) == []
    layers_a = Q.mark_particle_layers(1.0)
    layers_b = Q.mark_particle_layers(99.0)
    assert layers_a == layers_b


def test_reduced_motion_still_paints_the_mark(monkeypatch):
    monkeypatch.setenv("QUBIT_REDUCED_MOTION", "1")
    img = _paint(5.0)
    # Still has orbit lines + nucleus ink, just no life.
    assert _central_ink(img) > 0
    assert _diff(img, _paint(5.0)) == 0


# -- (e) never raises -------------------------------------------------------

def test_mark_effects_never_raise_on_bad_input():
    _paint(t="nonsense", state="not-a-state", family={}, brightness=0.0)
    _paint(t=float("nan"), state=None, family={"core": (-5, 300, 20)},
           brightness=-3.0)
    _paint(t=1e9, state="listening", family={})
    _paint(t=0.0, state="error", family={"core": "nope"}, brightness=99.0)


def test_mark_effects_never_raise_under_reduced_motion(monkeypatch):
    monkeypatch.setenv("QUBIT_REDUCED_MOTION", "1")
    _paint(t="x", state="???", family={}, brightness=0.0)
