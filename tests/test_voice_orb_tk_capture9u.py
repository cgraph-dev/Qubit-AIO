"""Deterministic RENDER CAPTURE harness for the Tk fallback orb painter.

The Qt capture harness (``tests/test_voice_orb_capture9u.py``) makes the LIVE
look machine-inspectable by rendering offscreen PNGs. The Tk fallback
(:mod:`voicebridge.orb.mark`) had no equivalent, so its look was owner-only.
This module gives it one:

  * it renders representative frames (idle / listening / speaking at a few
    fixed clocks) through the Tk painter and WRITES, to
    ``output/orb_capture/tk/`` (gitignored):

      - a readable primitive DUMP + a per-frame sha256 for every frame (always;
        this is the authoritative, hermetic, deterministic artifact), and
      - a real-pixel PNG when the host can drive a Tk display and ``gs``
        exists (best effort — ``Tk -> postscript -> gs png16m``);

  * it ASSERTS the owner-2026-10-19 layers are actually drawn — volume
    particles, accretion stream, photon ring, infall streaks and screen-break
    bodies reaching past the orb radius — by stubbing each layer off at the
    renderer's seam and requiring the drawn primitive stream to change, so a
    refactor that quietly drops a layer fails instead of shipping an
    owner-only look;

  * it ASSERTS the retired math-glyph layer draws NO glyphs (only the binary
    ``1``/``0`` fragments may ever reach ``create_text``);

  * it PINS determinism: the same clock renders an identical primitive stream
    (and an identical PNG when real pixels are captured).

Hermetic and fast: the core capture needs no Tk at all (``mark.render`` takes
any canvas-like object), so this runs on a headless host; the real-pixel path
is display-gated and skips cleanly. No product code is touched — the layer
seams are the ``mark._field_*`` module attributes that ``mark`` binds from
:mod:`voicebridge.orb.mark_field` at import time.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from voicebridge.orb import mark as M  # noqa: E402
from voicebridge.orb import mark_field as F  # noqa: E402

#: Roomier than the 160px perf frame so the screen-break bodies have space to
#: visibly exit the orb radius (R) without also being clipped by the widget.
SIZE = 300
CX = CY = SIZE / 2.0
R = 70.0
BG = "#0b0d12"

#: A small, non-degenerate set of clocks: 0 is the unturned still mark, the
#: others land mid-tumble / mid-beat so the live layers are exercised.
CLOCK_TIMES = (0.0, 1.3, 4.7)
STATES = ("idle", "listening", "speaking")

#: The field layers the owner asked to be made inspectable, mapped to the
#: renderer seam that draws each. ``mark`` imports every one of these from
#: ``mark_field`` at module import as ``_field_<name>``; stubbing the
#: ``mark_field`` attribute after import would NOT affect the painter (the
#: binding is already resolved), so the renderer seam is the ``mark`` attribute.
LAYERS = (
    ("volume_particles", "_field_volume_particles"),
    ("accretion_stream", "_field_accretion_stream"),
    ("photon_ring", "_field_photon_ring"),
    ("infall_streaks", "_field_infall_streaks"),
    ("screen_break", "_field_screen_break"),
)
LAYER_NAMES = tuple(name for name, _attr in LAYERS)

OUT_DIR = REPO_ROOT / "output" / "orb_capture" / "tk"
DIGEST_PATH = OUT_DIR / "digest.txt"

#: Only the binary data-fragment family may reach ``create_text``. The retired
#: math/checkmark glyphs must never appear.
ALLOWED_GLYPHS = {"0", "1"}


class FakeCanvas:
    """Records the Tk primitive stream a frame draws.

    ``mark.render`` calls ``create_oval`` / ``create_line`` / ``create_text``;
    this keeps every call so the drawn output is inspectable and hashable
    without a display.
    """

    def __init__(self) -> None:
        self.ovals = []
        self.lines = []
        self.texts = []

    def create_oval(self, x0, y0, x1, y1, **kw):
        self.ovals.append(((x0, y0, x1, y1), dict(kw)))
        return len(self.ovals)

    def create_line(self, *pts, **kw):
        self.lines.append((tuple(pts), dict(kw)))
        return len(self.lines)

    def create_text(self, x, y, **kw):
        self.texts.append(((x, y), dict(kw)))
        return len(self.texts)


def _r(v) -> int:
    """Round a coordinate to a stable integer-ish key for digests."""
    try:
        return int(round(float(v) * 10000.0))
    except (TypeError, ValueError):
        return 0


def _render(t: float, state: str) -> FakeCanvas:
    c = FakeCanvas()
    M.render(c, CX, CY, R, t, state)
    return c


def _canonical(canvas: FakeCanvas):
    """Ordered, hashable description of the primitive stream.

    Ovals / lines / texts are canonicalised separately (interleaving across
    primitive KINDS is not meaningful for change detection); the ordering
    within a kind is preserved, which is what makes two frames' draws
    comparable and deterministic.
    """
    recs = []
    for (x0, y0, x1, y1), kw in canvas.ovals:
        recs.append(("O", _r(x0), _r(y0), _r(x1), _r(y1),
                     str(kw.get("fill", "")), str(kw.get("outline", ""))))
    for pts, kw in canvas.lines:
        recs.append(("L", tuple(_r(p) for p in pts),
                     str(kw.get("fill", "")), int(kw.get("width", 1))))
    for (x, y), kw in canvas.texts:
        recs.append(("T", _r(x), _r(y), str(kw.get("text", "")),
                     str(kw.get("fill", ""))))
    return recs


def _sha(canvas: FakeCanvas) -> str:
    return hashlib.sha256(repr(_canonical(canvas)).encode("utf-8")).hexdigest()


def _counts(canvas: FakeCanvas):
    return (len(canvas.ovals), len(canvas.lines), len(canvas.texts))


def _max_reach(canvas: FakeCanvas) -> float:
    """Furthest drawn primitive centre / endpoint from the mark centre."""
    out = 0.0
    import math

    for (x0, y0, x1, y1), _kw in canvas.ovals:
        out = max(out, math.hypot((x0 + x1) / 2.0 - CX, (y0 + y1) / 2.0 - CY))
    for pts, _kw in canvas.lines:
        for i in range(0, len(pts) - 1, 2):
            out = max(out, math.hypot(pts[i] - CX, pts[i + 1] - CY))
    return out


def _glyphs(canvas: FakeCanvas):
    return sorted({str(kw.get("text")) for _xy, kw in canvas.texts})


def _frame_stem(state: str, t: float) -> str:
    return f"{state}_t{str(t).replace('.', 'p')}"


def _dump_path(state: str, t: float) -> Path:
    return OUT_DIR / f"{_frame_stem(state, t)}.dump.txt"


def _png_path(state: str, t: float) -> Path:
    return OUT_DIR / f"{_frame_stem(state, t)}.png"


def _color_histogram(canvas: FakeCanvas):
    from collections import Counter

    hist = Counter()
    for _o, kw in canvas.ovals:
        if kw.get("fill"):
            hist[str(kw["fill"])] += 1
    for _pts, kw in canvas.lines:
        if kw.get("fill"):
            hist[str(kw["fill"])] += 1
    return hist


# --------------------------------------------------------------------------- #
# real-pixel capture (Tk -> postscript -> gs), best effort and display-gated
# --------------------------------------------------------------------------- #
def _real_pixels_available() -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    if shutil.which("gs") is None:
        return False
    try:
        import tkinter  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _postscript(t: float, state: str) -> str:
    """Render one frame to Tk PostScript, with the nondeterministic headers
    (creation date, user, window title) stripped so the output is reproducible."""
    import tkinter as tk

    root = tk.Tk()
    try:
        root.withdraw()
        canvas = tk.Canvas(root, width=SIZE, height=SIZE, bg=BG,
                           highlightthickness=0)
        M.render(canvas, CX, CY, R, t, state)
        canvas.update_idletasks()
        ps = canvas.postscript(colormode="color", x=0, y=0,
                               width=SIZE, height=SIZE)
    finally:
        root.destroy()
    return "\n".join(
        line for line in ps.splitlines()
        if not line.startswith("%%CreationDate")
        and not line.startswith("%%For")
        and not line.startswith("%%Title"))


def _rasterize(ps: str, out_png: Path) -> bool:
    """PostScript -> PNG via ghostscript, on the orb's dark background.

    Tk's postscript does not emit the canvas background, so rendering it as
    ``png16m`` yields a white page. Render with alpha instead and composite
    the (transparent) page onto ``BG`` with PIL, so the artifact is a faithful
    dark-background frame; without PIL, keep the raw raster.
    """
    ps_path = None
    tmp_png = out_png.with_name(out_png.stem + ".raw.png")
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".ps", delete=False) as fh:
            fh.write(ps)
            ps_path = fh.name
        proc = subprocess.run(
            ["gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE",
             "-sDEVICE=pngalpha", "-dEPSCrop", "-r72",
             f"-sOutputFile={tmp_png}", ps_path],
            capture_output=True)
        if proc.returncode != 0 or not tmp_png.is_file():
            return False
        try:
            from PIL import Image
            src = Image.open(tmp_png).convert("RGBA")
            bg = Image.new("RGB", src.size, BG)
            bg.paste(src, mask=src.split()[3])
            bg.save(out_png)
        except Exception:  # noqa: BLE001 - no PIL: keep the raw raster
            os.replace(tmp_png, out_png)
        return out_png.is_file()
    except Exception:  # noqa: BLE001 - a capture fault must not fail the suite
        return False
    finally:
        for p in (ps_path, tmp_png):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


class _StubLayer:
    """Context manager that stubs a ``mark`` layer seam off (``None`` = absent,
    which is exactly how the renderer treats a field revision that lacks it)."""

    def __init__(self, attr: str) -> None:
        self.attr = attr
        self._orig = None

    def __enter__(self):
        self._orig = getattr(M, self.attr)
        setattr(M, self.attr, None)
        return self

    def __exit__(self, *exc):
        setattr(M, self.attr, self._orig)
        return False


@pytest.fixture(scope="module", autouse=True)
def _headless_display():
    """Give the real-pixel path an X display on a headless host.

    Tk cannot build a canvas without a display, so the PNG capture used to be
    skipped entirely on CI/headless runs. When no DISPLAY/WAYLAND_DISPLAY is
    set and ``Xvfb`` exists, start a private virtual display for this module
    and tear it down after; the primitive-stream capture still runs either way.
    """
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        yield
        return
    xvfb = shutil.which("Xvfb")
    if not xvfb:
        yield
        return
    disp = None
    for n in range(120, 220):
        if not Path(f"/tmp/.X11-unix/X{n}").exists():
            disp = n
            break
    if disp is None:
        yield
        return
    proc = subprocess.Popen(
        [xvfb, f":{disp}", "-screen", "0", "1024x1024x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    prev = os.environ.get("DISPLAY")
    os.environ["DISPLAY"] = f":{disp}"
    try:
        for _ in range(100):
            if Path(f"/tmp/.X11-unix/X{disp}").exists():
                break
            time.sleep(0.05)
        yield
    finally:
        if prev is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = prev
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - best effort teardown
            proc.kill()


@pytest.fixture(scope="module")
def captured(_headless_display):
    """Render every frame once and cache the per-frame evidence."""
    # The capture is about the LIVE look: a stray reduced-motion env from the
    # host must not silently blank every moving layer. ``mark.render`` takes
    # ``reduced_motion`` explicitly, but keep the env clean to match the Qt
    # harness and restore it when the module's frames are done.
    prior_reduced = os.environ.pop("QUBIT_REDUCED_MOTION", None)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    real_pixels = _real_pixels_available()

    frames = []
    lines = [
        "tk orb render capture — state x clock, live layers (owner 2026-10-19)",
        f"canvas={SIZE}x{SIZE} cx=cy={CX:.0f} R={R:.0f} out={OUT_DIR}",
        f"real_pixels={'yes' if real_pixels else 'no (primitive stream)'}",
        "",
    ]
    pngs = 0
    for state in STATES:
        for t in CLOCK_TIMES:
            canvas = _render(t, state)
            sha = _sha(canvas)
            ov, ln, tx = _counts(canvas)
            glyphs = _glyphs(canvas)
            reach = _max_reach(canvas)

            changed = {}
            for name, attr in LAYERS:
                with _StubLayer(attr):
                    stub_canvas = _render(t, state)
                sov, sln, stx = _counts(stub_canvas)
                changed[name] = {
                    "sha_differs": _sha(stub_canvas) != sha,
                    "ovals": ov - sov,
                    "lines": ln - sln,
                    "texts": tx - stx,
                }

            png_ok = False
            png_sha = ""
            if real_pixels:
                path = _png_path(state, t)
                if _rasterize(_postscript(t, state), path):
                    png_ok = True
                    pngs += 1
                    png_sha = hashlib.sha256(path.read_bytes()).hexdigest()

            dump = [
                f"frame {state} t={t}",
                f"canvas {SIZE}x{SIZE} cx=cy={CX:.0f} R={R:.0f}",
                f"primitives ovals={ov} lines={ln} texts={tx} total={ov + ln + tx}",
                f"sha256 {sha}",
                f"max_reach {reach:.2f}px ({reach / R:.3f} R)",
                f"glyphs {glyphs}",
                f"png {'written sha256=' + png_sha if png_ok else 'not captured'}",
                "layers (stub -> primitives removed):",
            ]
            hist = _color_histogram(canvas)
            dump.append("  top colours: " + ", ".join(
                f"{c}x{n}" for c, n in hist.most_common(6)))
            for name, _attr in LAYERS:
                d = changed[name]
                dump.append(
                    f"  {name:>18}: ovals={d['ovals']:>4} lines={d['lines']:>4}"
                    f" texts={d['texts']:>3} sha_differs={d['sha_differs']}")
            _dump_path(state, t).write_text("\n".join(dump) + "\n",
                                            encoding="utf-8")

            frame = {
                "state": state, "t": t, "sha": sha, "reach": reach,
                "counts": (ov, ln, tx), "glyphs": glyphs,
                "changed": changed, "png": png_ok, "png_sha": png_sha,
            }
            frames.append(frame)
            lines.append(
                f"{state:>9} t={t:>4.1f} sha={sha[:16]} "
                f"ovals={ov:>4} lines={ln:>4} reach={reach / R:.2f}R "
                f"glyphs={glyphs} "
                + " ".join(f"{n.split('_')[0]}={changed[n]['ovals']}"
                           for n in LAYER_NAMES))

    # Screen-break reach: how far past R the field declares any body (aggregate).
    max_reach_frac = 0.0
    for t in CLOCK_TIMES:
        for body in F.screen_break(t, R):
            try:
                import math
                max_reach_frac = max(max_reach_frac,
                                     math.hypot(float(body["x"]),
                                                float(body["y"])) / R)
            except (KeyError, TypeError, ValueError):
                continue
    drawn_reach = max((f["reach"] for f in frames), default=0.0)

    lines += [
        "",
        f"screen_break max field radius = {max_reach_frac:.3f} R "
        f"(must exceed 1.0)",
        f"max DRAWN primitive reach = {drawn_reach:.2f}px = "
        f"{drawn_reach / R:.3f} R",
        f"real-pixel PNGs written = {pngs}/{len(frames)}",
        "",
        "math_state (retired) = " + repr(getattr(F, "math_state", lambda *a: [])(
            0.0, R)),
    ]
    DIGEST_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))
    yield {
        "frames": frames,
        "max_reach_frac": max_reach_frac,
        "drawn_reach": drawn_reach,
        "real_pixels": real_pixels,
    }

    if prior_reduced is not None:
        os.environ["QUBIT_REDUCED_MOTION"] = prior_reduced


def test_capture_writes_artifacts(captured):
    """Every frame is written to the stable, gitignored output dir."""
    frames = captured["frames"]
    assert len(frames) == len(STATES) * len(CLOCK_TIMES)
    for frame in frames:
        dump = _dump_path(frame["state"], frame["t"])
        assert dump.is_file() and dump.stat().st_size > 0, dump
    assert DIGEST_PATH.is_file() and DIGEST_PATH.stat().st_size > 0
    # A PNG is written iff real pixels are available on this host.
    for frame in frames:
        png = _png_path(frame["state"], frame["t"])
        if captured["real_pixels"]:
            assert frame["png"], f"real pixels available but {png} not written"
            assert png.is_file() and png.stat().st_size > 0
        else:
            assert not frame["png"]


def test_new_layers_are_painted(captured):
    """Each owner-2026-10-19 layer contributes primitives to every frame."""
    for frame in captured["frames"]:
        ov, ln, tx = frame["counts"]
        assert ov + ln + tx > 0, f"{frame['state']} t={frame['t']} drew nothing"
        for name in LAYER_NAMES:
            d = frame["changed"][name]
            assert d["sha_differs"], (
                f"{name} drew nothing in {frame['state']} t={frame['t']} "
                "— the layer is missing from the frame")
            removed = max(d["ovals"], 0) + max(d["lines"], 0) + max(d["texts"], 0)
            assert removed > 0, (
                f"{name} changed the digest without removing a primitive "
                f"({frame['state']} t={frame['t']})")


def test_screen_break_reaches_past_orb_radius(captured):
    """Screen-break bodies reach beyond R in the field and are drawn out there."""
    assert captured["max_reach_frac"] > 1.0, (
        "screen_break field never declares a radius beyond R")
    assert captured["drawn_reach"] > R, (
        f"no primitive was ever drawn beyond the orb radius R "
        f"(max {captured['drawn_reach']:.1f}px <= {R:.0f}px)")


def test_no_retired_math_glyphs(captured):
    """Only binary 1/0 fragments reach create_text; math glyphs are retired."""
    math_state = getattr(F, "math_state", None)
    if math_state is not None:
        for t in CLOCK_TIMES:
            assert math_state(t, R) == [], "retired math_state layer is not empty"
    for frame in captured["frames"]:
        assert frame["glyphs"], (
            f"{frame['state']} t={frame['t']} expected binary glyphs")
        bad = sorted(set(frame["glyphs"]) - ALLOWED_GLYPHS)
        assert not bad, (
            f"{frame['state']} t={frame['t']} drew forbidden glyphs {bad} "
            "(retired math/checkmark layer) via create_text")


def test_primitive_stream_is_deterministic(captured):
    """The same clock and state give an identical primitive stream (a real pin)."""
    frame = captured["frames"][0]
    a = _render(frame["t"], frame["state"])
    b = _render(frame["t"], frame["state"])
    assert _sha(a) == _sha(b)
    assert _sha(a) == frame["sha"], "capture sha drifted between renders"
    # Every frame, not just the first: the whole capture is reproducible.
    for f in captured["frames"]:
        assert _sha(_render(f["t"], f["state"])) == f["sha"]


def test_real_pixels_are_deterministic(captured):
    """When a Tk display exists, the rasterised PNG is byte-deterministic."""
    if not captured["real_pixels"]:
        pytest.skip("no Tk display / ghostscript on this host")
    frame = captured["frames"][0]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "again.png"
        assert _rasterize(_postscript(frame["state"], frame["t"]), out)
        again = hashlib.sha256(out.read_bytes()).hexdigest()
    assert again == frame["png_sha"], "rasterised PNG drifted between renders"
