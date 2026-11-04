"""Orb hot-path caches are value-identical and safe.

The idle orb repaints a dense field (thousands of bodies) from pure-Python
helpers. Two per-body values do not depend on the clock — the palette hue and
the hashed volume home — yet they were recomputed every frame. Caching them
cuts the paint cost while returning exactly the same floats (pixels unchanged).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.orb import mark_field as F


def _reference_palette(index, salt=0.0):
    n = len(F.PALETTE_HUES)
    pick = int(F.hash01(int(F._finite(index)) + 1,
                        7.7 + F._finite(salt)) * n)
    pick = max(0, min(n - 1, pick))
    return F.PALETTE_HUES[pick]


def test_palette_hue_cache_returns_exact_values():
    for index in (0, 1, 7, 42, 999):
        for salt in (0.0, 7.7, 219.3):
            assert F.palette_hue(index, salt) == _reference_palette(index, salt)
            # A second call must hit the cache and stay identical.
            assert F.palette_hue(index, salt) == _reference_palette(index, salt)


def test_volume_axes_cache_returns_exact_values():
    for t in (0.0, 1.25, 19.9):
        for index in (0, 3, 88):
            z0, z20 = F.depth_axes(int(F._finite(index)), 0.0)
            seed = F.hash01(int(F._finite(index)) + 1, 907.3) * 2.0 * math.pi
            z = z0 + F.VOLUME_SWAY_SPAN * math.sin(
                2.0 * math.pi * t / F.VOLUME_SWAY_PERIOD_A + seed)
            z2 = z20 + F.VOLUME_SWAY_SPAN * math.sin(
                2.0 * math.pi * t / F.VOLUME_SWAY_PERIOD_B + seed * 1.37
                + F.VOLUME_SWAY_PHASE)
            expected = (min(1.0, max(0.0, z)), min(1.0, max(0.0, z2)))
            assert F._volume_axes(t, index, 0.0) == expected


def test_finite_fast_paths_preserve_int_and_nan_contract():
    assert F._finite(3) == 3.0
    assert isinstance(F._finite(3), float)
    assert F._finite(float("nan"), 0.5) == 0.5
    assert F._finite(float("inf"), 0.25) == 0.25
    assert F._finite("bad", 2.0) == 2.0
