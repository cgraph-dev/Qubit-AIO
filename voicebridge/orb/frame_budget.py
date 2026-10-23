"""Shared per-frame drawing budget: the ONE place a frame's LOD is decided.

Both renderers (Qt ``_paint_mark`` and Tk ``mark.render``) consume the same
:class:`FrameStats`, so an optimisation can never make the two disagree about
what the mark looks like. The rule set is deliberately tiny and geometric:

- :meth:`FrameStats.draw_px` — the smallest on-screen dimension (diameter) a
  primitive gets credit for when deciding whether it is worth rasterising.
- :meth:`FrameStats.visible` — an element survives only if it is at least
  :data:`MIN_DIM_PX` across AND its peak alpha is at least
  :data:`MIN_ALPHA`. Both thresholds were chosen from measurement, not taste:
  below 1.2 px an anti-aliased dot covers under a third of one pixel, and at
  alpha 0.06 over a near-black background it moves a channel by <= 15/255 —
  i.e. it is not resolvable on screen at the orb's real size.

The thresholds are a function of :data:`ORB_PX`-scaled geometry because every
element's size already arrives scaled by ``R``; nothing else needs to know how
big the orb is.

Nothing here allocates per call and nothing depends on the clock, so a reduced
motion frame and an animated frame take exactly the same decisions for the
same inputs.

Written to be import-safe without Qt or Tk (both hosts import it).
"""
from __future__ import annotations

from typing import Any, Tuple

#: Smallest drawn dimension, in device pixels, that is still resolvable — used
#: for FILLED BODIES. An anti-aliased disc smaller than this covers less than a
#: third of a pixel and reads as a one-pixel smudge no different from the
#: neighbouring dust, so its whole contribution is below one 8-bit step.
MIN_DIM_PX = 1.2

#: Smallest peak alpha still resolvable over the orb's near-black background.
#: 0.06 * a lifted channel (~200) = ~12/255 = below the perceptual step of the
#: overlay's own dithering, and it is what the faintest of the field's lives
#: bottom out at.
MIN_ALPHA = 0.06

#: Alias kept for the dot rule: layers whose elements are a FILLED BODY (dust
#: motes, blink dots, binary specks, glint cores, volume/orbiter particles).
MIN_DOT_PX = MIN_DIM_PX

#: A STROKED path is gated on alpha only: a 200 px circle stroked at 1.1 px is
#: perfectly visible, so its extent must never be a cull criterion.
MIN_STROKE_PX = 0.0

#: The geometry module's default orb diameter, used only to express the
#: thresholds relative to a real on-screen radius in docs and tests.
REFERENCE_ORB_PX = 168


class FrameStats(object):
    """Per-frame ledger of what a renderer decided to draw.

    ``__slots__`` keeps it allocation-cheap (two per frame: far + near half):
    the classes are used inside the hot loop, and the shared thresholds must
    not become the new cost.

    ``skip_dots`` / ``skip_rings`` count the primitives LOD culled so a test
    can assert the cull FIRES at small radii and is EMPTY at the default one,
    rather than trusting that it does.
    """

    __slots__ = ("radius", "scale", "dots", "rings", "skip_dots", "skip_rings",
                 "prims")

    def __init__(self, radius: Any = REFERENCE_ORB_PX / 2.0) -> None:
        try:
            r = float(radius)
        except (TypeError, ValueError):
            r = REFERENCE_ORB_PX / 2.0
        if not (r > 0.0):
            r = REFERENCE_ORB_PX / 2.0
        self.radius = r
        self.scale = r / (REFERENCE_ORB_PX / 2.0)
        self.dots = 0
        self.rings = 0
        self.skip_dots = 0
        self.skip_rings = 0
        self.prims = 0

    # -- LOD ---------------------------------------------------------------
    def draw_px(self, size_px: Any, width_px: Any = 0.0) -> float:
        """The dimension a primitive is judged by.

        ``width_px`` is the stroke width for a stroked path and ``0`` for a
        filled body (a dot is judged by its diameter alone). A stroked path is
        judged by the SMALLER of its extent and its stroke: a hairline that is
        a pixel long is not resolvable however wide the pen is.
        """
        try:
            s = float(size_px)
        except (TypeError, ValueError):
            return 0.0
        if width_px is None:
            return s
        try:
            w = float(width_px)
        except (TypeError, ValueError):
            return s
        if w <= 0.0:
            return s
        return s if s < w else w

    def visible(self, size_px: Any, alpha: Any, width_px: Any = 0.0,
                min_dim: float = MIN_DOT_PX,
                min_alpha: float = MIN_ALPHA) -> bool:
        """True when the element is resolvable on screen at this frame's R.

        ``size_px`` is the element's drawn diameter for a filled body, and its
        stroked extent for a path. ``width_px`` is 0 (or omitted) for a filled
        body; passing a pen width judges a path by the SMALLER of extent and
        stroke, which is only meaningful for something drawn as a short dash.
        A plain stroked circle passes ``min_dim=MIN_STROKE_PX`` instead.
        """
        try:
            a = float(alpha)
        except (TypeError, ValueError):
            return False
        if not (a >= min_alpha):
            return False
        return self.draw_px(size_px, width_px) >= min_dim

    # -- accounting --------------------------------------------------------
    def dot(self, drawn: bool) -> bool:
        """Record one dot decision; returns ``drawn`` so it can be inlined."""
        if drawn:
            self.dots += 1
            self.prims += 1
        else:
            self.skip_dots += 1
        return drawn

    def ring(self, drawn: bool) -> bool:
        """Record one stroked circle decision; returns ``drawn``."""
        if drawn:
            self.rings += 1
            self.prims += 1
        else:
            self.skip_rings += 1
        return drawn

    def as_dict(self) -> dict:
        """Hermetic snapshot (tests / benchmark output). Never raises."""
        return {"radius": self.radius, "scale": self.scale, "dots": self.dots,
                "rings": self.rings, "skip_dots": self.skip_dots,
                "skip_rings": self.skip_rings, "prims": self.prims}


def merge(*stats: FrameStats) -> FrameStats:
    """Sum several halves' ledgers into one frame ledger (never mixes radii)."""
    out = FrameStats(stats[0].radius if stats else REFERENCE_ORB_PX / 2.0)
    for s in stats:
        if s is None:
            continue
        out.dots += s.dots
        out.rings += s.rings
        out.skip_dots += s.skip_dots
        out.skip_rings += s.skip_rings
        out.prims += s.prims
    return out


def lod_thresholds(radius: Any) -> Tuple[float, float]:
    """``(min_dim_px, min_alpha)`` in force at ``radius`` — for tests/docs."""
    st = FrameStats(radius)
    return (MIN_DIM_PX, MIN_ALPHA) if st.scale > 0 else (MIN_DIM_PX, MIN_ALPHA)


__all__ = ["FrameStats", "MIN_DIM_PX", "MIN_DOT_PX", "MIN_STROKE_PX",
           "MIN_ALPHA", "REFERENCE_ORB_PX", "merge", "lod_thresholds"]
