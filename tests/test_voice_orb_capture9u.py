"""Deterministic RENDER CAPTURE harness for the Qubit voice orb.

The live orb paints offscreen-unfriendly on X11, but its PAINT is fully
deterministic: ``qt_app._paint_mark`` is pure in ``(t, state, family)``. This
module renders representative frames offscreen (no window, no X server) and
makes the look machine-inspectable instead of owner-only:

  * it WRITES each frame as a PNG under ``output/orb_capture/`` (gitignored)
    so a human can open the exact frame, plus a ``digest.txt`` that names the
    frame's hash and coverage;
  * it ASSERTS the owner-2026-10-19 layers are actually on screen — volume
    particles, accretion stream, photon ring, infall streaks and screen-break
    bodies reaching past the orb radius — by stubbing each field layer off and
    requiring the render to change, so a refactor that quietly drops a layer
    fails rather than shipping an owner-only look;
  * it ASSERTS the retired math-glyph layer draws NO glyphs (only the binary
    ``1``/``0`` fragments may ever reach ``drawText``);
  * it PINS determinism: the same clock renders byte-identical pixels.

Hermetic and fast: ``QT_QPA_PLATFORM=offscreen``, Qt-less env is skipped, and
the field functions are stubbed through their public module attribute (no
product code is touched).
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtGui", reason="Qt-less env")

#: Roomier than the 160px perf frame so the screen-break bodies have space to
#: visibly exit the orb radius (R) without also being clipped by the widget.
SIZE = 300
CX = CY = SIZE / 2.0
R = 70.0
BG = (11, 13, 18)

#: A small, non-degenerate set of clocks: 0 is the unturned still mark, the
#: others land mid-tumble / mid-beat so the live layers are exercised.
CLOCK_TIMES = (0.0, 1.3, 4.7)
STATES = ("idle", "listening", "speaking")

#: The field layers the owner asked to be made inspectable. Each is stubbed off
#: (through ``mark_field``) to prove it contributes pixels.
LAYER_NAMES = (
    "volume_particles",
    "accretion_stream",
    "photon_ring",
    "infall_streaks",
    "screen_break",
)

OUT_DIR = REPO_ROOT / "output" / "orb_capture"
DIGEST_PATH = OUT_DIR / "digest.txt"

#: Only the binary data-fragment family may reach ``drawText``. The retired
#: math/checkmark glyphs must never appear.
ALLOWED_GLYPHS = {"0", "1"}


def _make_app():
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.instance() or QGuiApplication([])


class _Recorder:
    """Forwarding QPainter proxy that records the glyphs a frame draws."""

    def __init__(self, painter) -> None:
        object.__setattr__(self, "_p", painter)
        object.__setattr__(self, "chars", [])

    def drawText(self, *args):
        text = args[-1]
        object.__setattr__(self, "chars", self.chars + [str(text)])
        self._p.drawText(*args)

    def __getattr__(self, name):
        return getattr(self._p, name)


def _bg_pixel_int() -> int:
    """The background as a native 32-bit ``ARGB32_Premultiplied`` word."""
    from PySide6.QtGui import QColor, QImage
    probe = QImage(1, 1, QImage.Format_ARGB32_Premultiplied)
    probe.fill(QColor(*BG))
    mv = memoryview(bytes(probe.constBits())).cast("I")
    return int(mv[0])


def _render_raw(state: str, t: float):
    """Offscreen-paint one frame; return ``(QImage, Recorder)``."""
    from PySide6.QtGui import QColor, QImage, QPainter

    from voicebridge.orb import ember as E
    from voicebridge.orb import qt_app as Q

    _make_app()
    fam = E.family_from_core(E.state_core(state))
    img = QImage(SIZE, SIZE, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor(*BG))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, True)
    rec = _Recorder(painter)
    Q._paint_mark(rec, CX, CY, R, t, state, 1.0, fam)
    painter.end()
    return img, rec


def _raw_words(img) -> memoryview:
    return memoryview(bytes(img.constBits())).cast("I")


def _sha(words) -> str:
    return hashlib.sha256(bytes(words)).hexdigest()


def _different_pixels(a: memoryview, b: memoryview) -> int:
    return sum(1 for x, y in zip(a, b) if x != y)


def _outside_R_pixels(a: memoryview, b: memoryview) -> int:
    """Pixels that differ between ``a`` and ``b`` AND sit further than R out."""
    r2 = R * R
    count = 0
    width = SIZE
    for i, (x, y) in enumerate(zip(a, b)):
        if x == y:
            continue
        px = float(i % width)
        py = float(i // width)
        dx = px - CX
        dy = py - CY
        if dx * dx + dy * dy > r2:
            count += 1
    return count


def _foreground_pixels(words, bg: int) -> int:
    return sum(1 for w in words if w != bg)


class _StubLayer:
    """Context manager that makes ``mark_field.<name>`` return an empty list."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._orig = None

    def __enter__(self):
        from voicebridge.orb import mark_field as F
        self._orig = getattr(F, self.name)
        setattr(F, self.name, lambda t, R: [])  # noqa: E731 - test double
        return self

    def __exit__(self, *exc):
        from voicebridge.orb import mark_field as F
        if self._orig is not None:
            setattr(F, self.name, self._orig)
        return False


def _frame_path(state: str, t: float) -> Path:
    return OUT_DIR / f"{state}_t{str(t).replace('.', 'p')}.png"


@pytest.fixture(scope="module")
def captured():
    """Render every frame once and cache the per-frame evidence."""
    # The capture is about the LIVE look: make sure a stray reduced-motion env
    # from the host does not silently blank every moving layer. Restore it when
    # the module's frames are done so a full-suite run is not polluted.
    prior_reduced = os.environ.pop("QUBIT_REDUCED_MOTION", None)

    from voicebridge.orb import mark_field as F

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bg = _bg_pixel_int()
    frames = []
    lines = [
        "orb render capture — state x clock, live layers (owner 2026-10-19)",
        f"canvas={SIZE}x{SIZE} cx=cy={CX:.0f} R={R:.0f} out={OUT_DIR}",
        "",
    ]
    for state in STATES:
        for t in CLOCK_TIMES:
            img, rec = _render_raw(state, t)
            words = _raw_words(img)
            path = _frame_path(state, t)
            saved = img.save(str(path), "PNG")

            changed = {}
            outside = {}
            for name in LAYER_NAMES:
                with _StubLayer(name):
                    stub_img, _ = _render_raw(state, t)
                stub_words = _raw_words(stub_img)
                changed[name] = _different_pixels(words, stub_words)
                outside[name] = _outside_R_pixels(words, stub_words)

            frame = {
                "state": state,
                "t": t,
                "path": path,
                "saved": bool(saved),
                "sha": _sha(words),
                "fg": _foreground_pixels(words, bg),
                "chars": rec.chars,
                "changed": changed,
                "outside": outside,
            }
            frames.append(frame)
            lines.append(
                f"{state:>9} t={t:>4.1f} sha={frame['sha'][:16]} "
                f"fg={frame['fg']:>5} glyphs={len(set(rec.chars))} "
                + " ".join(f"{n.split('_')[0]}={changed[n]}" for n in LAYER_NAMES)
            )

    # Screen-break reach: how far past R any body ever paints (aggregate).
    max_outside = max(
        (f["outside"]["screen_break"] for f in frames), default=0)
    max_reach_frac = 0.0
    for t in CLOCK_TIMES:
        for body in F.screen_break(t, R):
            max_reach_frac = max(max_reach_frac, float(body.get("r", 0.0)) / R)

    lines += [
        "",
        f"screen_break max field radius = {max_reach_frac:.3f} R "
        f"(must exceed 1.0; painter pushes it further out)",
        f"screen_break painted pixels beyond R (max over frames) = {max_outside}",
        "",
        "math_state (retired) = " + repr(F.math_state(0.0, R)),
    ]
    DIGEST_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))

    yield {
        "frames": frames,
        "max_outside": max_outside,
        "max_reach_frac": max_reach_frac,
    }

    if prior_reduced is not None:
        os.environ["QUBIT_REDUCED_MOTION"] = prior_reduced


def test_capture_writes_pngs_and_digest(captured):
    """Every frame is written to the stable, gitignored output dir."""
    frames = captured["frames"]
    assert len(frames) == len(STATES) * len(CLOCK_TIMES)
    for frame in frames:
        assert frame["saved"], f"PNG failed to save: {frame['path']}"
        path = frame["path"]
        assert path.is_file() and path.stat().st_size > 0
    assert DIGEST_PATH.is_file() and DIGEST_PATH.stat().st_size > 0


def test_new_layers_are_painted(captured):
    """Each owner-2026-10-19 layer contributes pixels to every frame."""
    for frame in captured["frames"]:
        assert frame["fg"] > 0, f"{frame['state']} t={frame['t']} drew nothing"
        for name in LAYER_NAMES:
            assert frame["changed"][name] > 0, (
                f"{name} drew no pixels in {frame['state']} t={frame['t']} "
                "— the layer is missing from the frame")


def test_screen_break_reaches_past_orb_radius(captured):
    """Screen-break bodies reach beyond R in the field and paint out there."""
    assert captured["max_reach_frac"] > 1.0, (
        "screen_break field never declares a radius beyond R")
    assert captured["max_outside"] > 0, (
        "no screen_break pixel was ever painted beyond the orb radius R")


def test_no_retired_math_glyphs(captured):
    """Only binary 1/0 fragments reach drawText; the math glyphs are retired."""
    from voicebridge.orb import mark_field as F

    for t in CLOCK_TIMES:
        assert F.math_state(t, R) == [], "retired math_state layer is not empty"
    for frame in captured["frames"]:
        assert frame["chars"], (
            f"{frame['state']} t={frame['t']} expected binary glyphs")
        bad = sorted(set(frame["chars"]) - ALLOWED_GLYPHS)
        assert not bad, (
            f"{frame['state']} t={frame['t']} drew forbidden glyphs {bad} "
            "(retired math/checkmark layer) via drawText")


def test_render_is_deterministic(captured):
    """The same clock and state give byte-identical pixels (a real pin)."""
    frame = captured["frames"][0]
    words_a = _raw_words(_render_raw(frame["state"], frame["t"])[0])
    words_b = _raw_words(_render_raw(frame["state"], frame["t"])[0])
    assert _sha(words_a) == _sha(words_b)
    assert _sha(words_a) == frame["sha"], "capture sha drifted between renders"
