"""The ghost mark is the real pixelfx sprite, wired through QUBIT_ORB_STYLE."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.orb import ghost_atlas  # noqa: E402
from voicebridge.orb import qt_app  # noqa: E402


def test_style_accepts_ghost_and_falls_back_on_junk() -> None:
    """A typo must never leave the orb painting nothing."""
    assert qt_app._orb_style()  # default
    for value in ("ghost", "GHOST", " ghost "):
        import os

        os.environ["QUBIT_ORB_STYLE"] = value
        try:
            assert qt_app._orb_style() == "ghost"
        finally:
            os.environ.pop("QUBIT_ORB_STYLE", None)
    for value in ("nonsense", "", "ghostt"):
        import os

        os.environ["QUBIT_ORB_STYLE"] = value
        try:
            assert qt_app._orb_style() == "mark"
        finally:
            os.environ.pop("QUBIT_ORB_STYLE", None)


def test_atlas_is_the_real_pixelfx_effect() -> None:
    """The atlas must be the exported upstream effect, not a stand-in."""
    header = ghost_atlas.header()
    if not header:
        pytest.skip("ghost_atlas.bin not generated (run export_ghost.mjs)")
    assert header["name"] == "Ghost"
    assert header["fps"] == 30
    assert header["duration"] == 2.4
    assert header["frames"] == 72
    assert "pixelfx" in header["source"]
    assert header["loop"] is True


def test_frame_index_wraps_and_is_stable() -> None:
    """The loop must tile: t and t+duration resolve to the same frame."""
    if not ghost_atlas.header():
        pytest.skip("ghost_atlas.bin not generated")
    duration = ghost_atlas.header()["duration"]
    for t in (0.0, 0.37, 1.2, 2.39):
        assert ghost_atlas.frame_index(t) == ghost_atlas.frame_index(t + duration)
    assert ghost_atlas.frame_index(0.0) == 0
    # Non-finite clocks must not raise (the field guards do the same).
    assert ghost_atlas.frame_index(float("nan")) == 0
    assert ghost_atlas.frame_index(None) == 0


def test_atlas_body_matches_its_header() -> None:
    """Every declared frame must be present at the declared size."""
    if not ghost_atlas.header():
        pytest.skip("ghost_atlas.bin not generated")
    atlas = ghost_atlas._atlas()
    assert atlas is not None
    assert len(atlas.body) == atlas.w * atlas.h * 4 * atlas.frames
    frame = atlas.rgba(0)
    assert len(frame) == atlas.w * atlas.h * 4
    # A real effect paints something: not every pixel is transparent.
    assert any(frame[i] for i in range(3, len(frame), 4))


def test_paint_ghost_reports_failure_instead_of_raising() -> None:
    """Without Qt (or an atlas) the painter must decline, not crash."""

    class _Boom:
        def __getattr__(self, name):  # noqa: D105
            raise RuntimeError("no painter here")

    assert qt_app._paint_ghost(_Boom(), 0.0, 0.0, 100.0, 0.5, 1.0) is False
