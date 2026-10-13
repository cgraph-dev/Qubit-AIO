"""Pixel-CONTENT assertions for the live Qubit Office page.

WHY THIS FILE EXISTS (and how it differs from test_office_render_smoke.py)
--------------------------------------------------------------------------
``tests/test_office_render_smoke.py`` proves the page is **not empty**: it
reaches ``data-state="ready"``, more than 5% of the largest canvas is not
background, the PNG is real, and the count is stable across two loads. All of
that can be true of a picture that is wrong.

This file asserts what is IN the pixels — that the drawing is COHERENT:

  1. the page did NOT fall back to the bare renderer. This is the single check
     that would have caught a real bug this week: the manifest was updated
     while the *served* ``frontend/qubit-dist/plugins/fleet.json`` still pointed
     at the old entry, so the browser loaded the bare renderer for three render
     cycles and the page silently degraded. The renderer NAMES a missing module
     on the page (``"Office modules not loaded: ..."``), so the visible text is
     the evidence.
  2. the building is drawn, not just the chrome — the non-background fraction is
     ABOVE a floor an empty canvas (or a bare HUD strip) cannot reach.
  3. the palette is varied — a single flat rectangle cannot fill many colour
     buckets, a drawn building can.
  4. the minimap (the second canvas, 640x260) has real drawn content — a
     "reconciled minimap" was one of the named defects.
  5. determinism stronger than a count: the top colour BUCKETS from two separate
     renders must be identical (same colours, same counts), which a
     timer-driven or randomised draw cannot satisfy.

Every number below is measured from the live page, not guessed; the comments
record the measurement each threshold is derived from.

SKIP DISCIPLINE (matches test_office_render_smoke.py)
-----------------------------------------------------
SKIP — never fail — when the dashboard is unreachable, the token is missing, or
headless Chrome is not installed. But when the dashboard IS up, a broken page is
a FAILURE. A renderer mid-edit must turn this file red.

Marked ``dashboard`` so it stays out of the hermetic default run (pytest.ini).

Run:  .venv/bin/python -m pytest tests/test_office_render_pixels.py -m dashboard -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import shutil
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "scripts" / "render_office_screenshot.py"

pytestmark = pytest.mark.dashboard

# --- thresholds, each justified from a live measurement ------------------------
#
# 15% non-background on the largest canvas. Measured live: 24.637%
# (108,527 of 440,500 px) — the populated office. An empty canvas measures
# ~0.0-0.2%; the smoke test's own history records 5.4% for a *sparse* office
# before the module integration landed. The floor sits at 15%: well above any
# empty canvas, and above a bare HUD strip (a few text rows + control buttons is
# a small fraction of 881x500), while still clearing the real 24.6% with ~9
# points of headroom so a slightly sparser-but-real office does not flap. It is
# deliberately higher than the smoke test's 5% because this file's claim is
# "the building is drawn", not merely "something is drawn".
MIN_NON_BG_PCT = 15.0

# Palette variety. Measured live: 10 buckets reported (the tool's cap), of which
# the top 8 carry >=757 px each and the top 4 carry >14,800 px each. A blank
# canvas is ONE bucket (the background); a single flat rectangle is ONE bucket
# (its fill). We require at least 4 distinct buckets each with at least 500
# pixels — a drawn building (floors, rooms, walls, agents, labels) clears this
# by a wide margin, while a flat fill cannot.
MIN_DISTINCT_BUCKETS = 4
MIN_BUCKET_COUNT = 500

# The minimap canvas is 640x260 with its OWN background fill #141a23 (see
# ``drawMinimap`` in qubit-office.js: it does ``fillStyle = "#141a23"`` then
# ``fillRect`` the whole canvas). The screenshot tool's global background rule
# (all channels < 30) does NOT classify #141a23 as background — its blue channel
# is 0x23 == 35 >= 30 — so reading the minimap with the tool's rule reports a
# useless 100%. We therefore measure the minimap against ITS OWN documented
# background colour and count pixels that differ from it. Measured live: 59,616
# of 166,400 px (35.8%) differ from #141a23 — rooms and agent markers. A minimap
# that never drew would be pure #141a23 (0%). Floor: 5%.
MINIMAP_BG_RGB = (0x14, 0x1A, 0x23)
MINIMAP_BG_TOLERANCE = 6  # per-channel; absorbs antialiasing at room edges
MIN_MINIMAP_DRAWN_PCT = 5.0

# The marker the renderer prints when a module failed to load. Case-insensitive
# substring test so it survives the renderer re-casing or prefixing the string.
FALLBACK_MARKER = "modules not loaded"


# --------------------------------------------------------------------------- #
# reachability / environment guards (same convention as the smoke test)        #
# --------------------------------------------------------------------------- #
def _base_url() -> str:
    """The Office route to probe, from the same config the bridge reads."""
    try:
        sys.path.insert(0, str(REPO))
        from voicebridge.config import Config  # noqa: PLC0415

        cfg = Config.load()
        port = cfg.get("webui", "port", default=8890)
    except Exception:
        port = 8890
    return f"http://127.0.0.1:{port}/qubit-office"


def _http_status(url: str, timeout: float = 3.0) -> int | None:
    """HTTP status for ``url``, or None if it is unreachable."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.getcode()
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return None


def _load_tool():
    """Import the screenshot tool module, or skip if its deps are absent."""
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import render_office_screenshot as tool  # noqa: PLC0415

        return tool
    except ImportError as exc:  # websocket-client / PyYAML absent
        pytest.skip(f"screenshot tool dependencies unavailable: {exc}")


def _skip_unless_renderable() -> str:
    """Skip — not fail — unless the bridge, token and Chrome are all present.

    Identical discipline to ``test_office_render_smoke.py``: an unreachable
    dashboard is an environment problem (skip); anything past this point is a
    real assertion (fail).
    """
    if not TOOL.is_file():
        pytest.skip(f"screenshot tool missing: {TOOL}")

    url = _base_url()
    if _http_status(url) is None:
        pytest.skip(
            "dashboard not running: cannot reach "
            f"{url} (is qubit-voicebridge.service up?)"
        )

    tool = _load_tool()

    try:
        token = tool.read_token(tool.DEFAULT_TOKEN_PATH)
    except Exception as exc:
        pytest.skip(f"dashboard token unavailable: {exc}")
    assert token, "token read but empty"

    if not Path(tool.CHROME).exists():
        pytest.skip(f"headless Chrome not installed at {tool.CHROME}")

    return url


def _render(url: str, out: Path, timeout: float = 60.0) -> dict:
    """Run the screenshot tool once, returning its parsed JSON report.

    ``TMPDIR`` is forced to ``/tmp`` for the subprocess: ``tests/conftest.py``
    repoints ``TMPDIR`` at a long scratch path, which overflows Chrome's
    108-byte ``sun_path`` socket limit and aborts it before CDP comes up. This
    mirrors the smoke test's fix.
    """
    env = dict(os.environ)
    env["TMPDIR"] = "/tmp"  # keep Chrome's socket path under 108 bytes
    proc = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--url", url,
            "--out", str(out),
            "--timeout", str(timeout),
            "--json",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=timeout + 90,
        env=env,
    )
    report = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                report = json.loads(line)
            except json.JSONDecodeError:
                continue
    if report is None:
        pytest.fail(
            "screenshot tool produced no JSON report "
            f"(exit {proc.returncode}).\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return report


# --------------------------------------------------------------------------- #
# a CDP session for the SECOND canvas (the tool only reports the largest)      #
# --------------------------------------------------------------------------- #
# The tool's ``--json`` report carries pixel stats for the LARGEST canvas only
# (``_PIXEL_JS`` hard-picks ``big`` by area). The minimap is the second canvas,
# so we drive the same page with the tool's OWN CDP helpers and probe the
# second-largest canvas. We do NOT modify the tool; we reuse its symbols
# (``CDP``, ``wait_for_endpoint``, ``free_port``, ``_PROBE_JS``, ``TOKEN_KEYS``,
# ``CHROME``) so the navigation + token seeding is byte-for-byte the tool's.
_MINIMAP_PIXEL_JS = r"""
(() => {
  const canvases = Array.from(document.querySelectorAll('canvas'));
  if (canvases.length < 2) return { error: 'fewer than 2 canvases' };
  const ranked = canvases
    .map((c) => ({ c, area: c.width * c.height }))
    .sort((a, b) => b.area - a.area);
  const mini = ranked[1].c;               // second-largest == the minimap
  let ctx;
  try { ctx = mini.getContext('2d'); } catch (e) { return { error: 'no 2d: ' + e }; }
  if (!ctx) return { error: 'no 2d context on second canvas' };
  const w = mini.width, h = mini.height;
  let imageData;
  try { imageData = ctx.getImageData(0, 0, w, h); }
  catch (e) { return { error: 'cannot read pixels: ' + e }; }
  return { width: w, height: h, mode: '2d', data: Array.from(imageData.data) };
})()
"""


def _minimap_report(base: str, timeout: float = 60.0) -> dict:
    """Drive the page with the tool's CDP helpers; return the minimap's stats.

    Returns a dict with ``error`` if the page could not be driven (surfaced as
    an explicit assertion failure by the caller, never invented data).

    ``TMPDIR`` is set to ``/tmp`` for the Chrome subprocess for the same
    sun_path reason as ``_render``.
    """
    tool = _load_tool()
    token = tool.read_token(tool.DEFAULT_TOKEN_PATH)
    origin = "/".join(base.split("/")[:3])

    env = dict(os.environ)
    env["TMPDIR"] = "/tmp"
    port = tool.free_port()
    profile = tempfile.mkdtemp(prefix="qubit-pixel-mini-")
    proc = subprocess.Popen(
        [
            tool.CHROME,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--hide-scrollbars",
            "--window-size=1600,1000",
            "--remote-allow-origins=*",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=env,
    )
    deadline = time.time() + timeout
    cdp = None
    try:
        ws_url = tool.wait_for_endpoint(f"http://127.0.0.1:{port}", proc, deadline)
        cdp = tool.CDP(ws_url)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Log.enable")

        cdp.call("Page.navigate", {"url": origin + "/"})
        while time.time() < deadline:
            try:
                if cdp.eval("document.readyState") == "complete":
                    break
            except Exception:
                pass
            time.sleep(0.1)
        cdp.eval(
            "(() => { const t = %s; for (const k of %s) "
            "localStorage.setItem(k, t); return true; })()"
            % (json.dumps(token), json.dumps(tool.TOKEN_KEYS))
        )
        cdp.call("Page.navigate", {"url": base})

        state = None
        info = {}
        while time.time() < deadline:
            try:
                info = cdp.eval(tool._PROBE_JS)
            except Exception:
                time.sleep(0.2)
                continue
            state = info.get("dataState")
            widths = [c["width"] for c in info.get("canvases", [])]
            if state == "ready" and (max(widths) if widths else 0) > 200:
                break
            time.sleep(0.25)

        canvases = info.get("canvases", [])
        if state != "ready" or len(canvases) < 2:
            return {
                "error": (
                    f"page not drivable for minimap probe: state={state!r}, "
                    f"canvases={canvases}"
                )
            }
        payload = cdp.eval(_MINIMAP_PIXEL_JS)
        return _minimap_stats(payload, canvases)
    except tool.RenderProbeError as exc:
        return {"error": f"CDP probe failed: {exc}"}
    finally:
        if cdp is not None:
            cdp.close()
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        if proc.stderr is not None:
            proc.stderr.close()
        shutil.rmtree(profile, ignore_errors=True)


def _minimap_stats(payload: dict, canvases: list[dict]) -> dict:
    """Count minimap pixels that differ from the minimap's own background.

    The tool's global background rule treats only near-black (<30/channel) as
    background; #141a23 (the minimap fill) has blue 0x23==35 and would read as
    100% non-background. So we compare against the exact documented fill.
    """
    if payload.get("error"):
        return {"error": payload["error"]}
    data = payload.get("data") or []
    w, h = payload["width"], payload["height"]
    bg = MINIMAP_BG_RGB
    tol = MINIMAP_BG_TOLERANCE
    drawn = 0
    exact_bg = 0
    buckets: Counter[str] = Counter()

    def _bucket(rgb):
        r, g, b = ((min(255, v) // 16) * 16 for v in rgb)
        return f"#{r:02x}{g:02x}{b:02x}"

    for i in range(0, len(data) - 3, 4):
        r, g, b = data[i], data[i + 1], data[i + 2]
        if r == bg[0] and g == bg[1] and b == bg[2]:
            exact_bg += 1
            continue
        if abs(r - bg[0]) <= tol and abs(g - bg[1]) <= tol and abs(b - bg[2]) <= tol:
            continue
        drawn += 1
        buckets[_bucket((r, g, b))] += 1
    total = w * h
    return {
        "width": w,
        "height": h,
        "total_pixels": total,
        "drawn_pixels": drawn,
        "drawn_pct": round(100.0 * drawn / total, 3) if total else 0.0,
        "exact_background_pixels": exact_bg,
        "top_buckets": [{"color": c, "count": n} for c, n in buckets.most_common(10)],
        "canvas_sizes": [(c.get("width"), c.get("height")) for c in canvases],
    }


# --------------------------------------------------------------------------- #
# 1. modules loaded, not falling back                                          #
# --------------------------------------------------------------------------- #
def test_office_modules_are_loaded_not_falling_back(tmp_path: Path) -> None:
    """The page must NOT report a module-load fallback in its own text.

    THE bug this file exists to catch. The manifest can name the new modules
    while the *served* ``frontend/qubit-dist/plugins/fleet.json`` still points
    at the old entry, so the browser loads the bare renderer and the page
    silently degrades for several render cycles. The renderer names the gap on
    the page (``"Office modules not loaded: ..."``); the visible text is the
    evidence.

    SKIP if no dashboard; FAIL if reachable and falling back.
    """
    url = _skip_unless_renderable()
    report = _render(url, tmp_path / "fallback_probe.png")

    text = report.get("visible_text") or ""
    assert report.get("data_state") == "ready", (
        f"page never reached ready: state={report.get('data_state')!r}"
    )

    lowered = text.lower()
    assert FALLBACK_MARKER not in lowered, (
        "the Office page is falling back to the bare renderer — it renders "
        f"{FALLBACK_MARKER!r} in its visible text:\n"
        + "\n".join(
            ln for ln in text.splitlines() if FALLBACK_MARKER in ln.lower()
        )
        + "\nThis means a module did not load — most likely the served "
        "frontend/qubit-dist/plugins/fleet.json points at an old entry while "
        "the manifest names a new one."
    )

    print(
        f"\n[fallback] clean: {FALLBACK_MARKER!r} absent from "
        f"{len(text)} chars of visible text"
    )


# --------------------------------------------------------------------------- #
# 2. the building is actually drawn                                            #
# --------------------------------------------------------------------------- #
def test_office_building_is_drawn_not_just_chrome(tmp_path: Path) -> None:
    """Non-background fraction must clear a floor chome alone cannot reach.

    Measured live: 24.637% (108,527 / 440,500). An empty canvas is ~0.0-0.2%; a
    bare HUD strip is far below the 24.6% the building occupies. Floor = 15%,
    which an empty or HUD-only canvas cannot reach and the real building clears
    with headroom. This is stricter than the smoke test's 5% on purpose: this
    file's claim is that a BUILDING is drawn, not merely that something is.
    """
    url = _skip_unless_renderable()
    report = _render(url, tmp_path / "building.png")

    px = report.get("pixels_largest_canvas") or {}
    assert not px.get("error"), f"could not read canvas pixels: {px.get('error')}"

    pct = px.get("non_background_pct", 0.0)
    nonbg = px.get("non_background_pixels", 0)
    total = px.get("total_pixels")
    assert pct > MIN_NON_BG_PCT, (
        f"largest canvas is not a drawn building: {nonbg}/{total} "
        f"non-background pixels ({pct}%), below the {MIN_NON_BG_PCT}% floor. "
        "An empty canvas measures ~0-0.2%; a bare HUD would sit far below the "
        f"{pct}% a real building occupies."
    )

    print(
        f"\n[building] {nonbg}/{total} pixels ({pct}%) non-background; "
        f"floor {MIN_NON_BG_PCT}%"
    )


# --------------------------------------------------------------------------- #
# 3. the palette is varied                                                     #
# --------------------------------------------------------------------------- #
def test_office_palette_is_varied(tmp_path: Path) -> None:
    """The largest canvas must fill >=4 distinct colour buckets of real size.

    A blank canvas reads as ONE bucket (the background); a single flat
    rectangle reads as ONE bucket (its fill). A drawn building — floors, rooms,
    walls, agents, labels — reads as many. Measured live: 10 buckets reported
    (the tool's cap), the top 8 carrying >=757 px each. We require >=4 buckets
    with >=500 px, which a flat fill cannot satisfy.
    """
    url = _skip_unless_renderable()
    report = _render(url, tmp_path / "palette.png")

    px = report.get("pixels_largest_canvas") or {}
    assert not px.get("error"), f"could not read canvas pixels: {px.get('error')}"
    buckets = px.get("top_buckets") or []

    meaningful = [b for b in buckets if b.get("count", 0) >= MIN_BUCKET_COUNT]
    assert len(meaningful) >= MIN_DISTINCT_BUCKETS, (
        f"largest canvas is too flat: only {len(meaningful)} colour bucket(s) "
        f"carry >= {MIN_BUCKET_COUNT} px (need {MIN_DISTINCT_BUCKETS}). "
        f"Buckets observed: {buckets}. A blank or flat-filled canvas reads as "
        "one bucket."
    )

    observed = ", ".join(
        f"{b['color']}={b['count']}" for b in buckets
    )
    print(
        f"\n[palette] {len(meaningful)} buckets >= {MIN_BUCKET_COUNT} px "
        f"(need {MIN_DISTINCT_BUCKETS}); observed: {observed}"
    )


# --------------------------------------------------------------------------- #
# 4. the minimap is a real minimap                                             #
# --------------------------------------------------------------------------- #
def test_office_minimap_has_drawn_content() -> None:
    """The second canvas (640x260) must have real drawn content.

    A reconciled minimap was one of the named defects. The tool reports pixel
    stats for the LARGEST canvas only, so we drive the page with the tool's own
    CDP helpers and probe the second-largest canvas (the minimap) against ITS
    OWN documented background fill #141a23 (see ``drawMinimap``).

    Measured live: 59,616 / 166,400 px (35.8%) differ from #141a23 — rooms and
    agent markers. Floor: 5% (a minimap that never drew is 0%).
    """
    url = _skip_unless_renderable()
    stats = _minimap_report(url)

    assert not stats.get("error"), (
        f"could not probe the minimap canvas: {stats.get('error')}"
    )
    assert (stats.get("width"), stats.get("height")) == (640, 260), (
        "second-largest canvas is not the 640x260 minimap: "
        f"{stats.get('width')}x{stats.get('height')} "
        f"(canvases seen: {stats.get('canvas_sizes')})"
    )

    pct = stats.get("drawn_pct", 0.0)
    drawn = stats.get("drawn_pixels", 0)
    total = stats.get("total_pixels")
    assert pct >= MIN_MINIMAP_DRAWN_PCT, (
        f"minimap is effectively blank: only {drawn}/{total} pixels "
        f"({pct}%) differ from its #141a23 background "
        f"(floor {MIN_MINIMAP_DRAWN_PCT}%). A minimap that never drew would be "
        "pure #141a23."
    )

    print(
        f"\n[minimap] {drawn}/{total} px ({pct}%) drawn vs #141a23 background; "
        f"floor {MIN_MINIMAP_DRAWN_PCT}%; buckets "
        + ", ".join(f"{b['color']}={b['count']}" for b in stats.get("top_buckets", []))
    )


# --------------------------------------------------------------------------- #
# 5. determinism stricter than a count: identical buckets                      #
# --------------------------------------------------------------------------- #
def test_office_top_buckets_are_identical_across_two_loads(tmp_path: Path) -> None:
    """Two renders must produce IDENTICAL top colour buckets (colour AND count).

    The smoke test checks the pixel COUNT is stable within 25%. A timer-driven
    or randomised draw (an animation loop, ``Math.random`` placement, a live
    clock) could still land inside a 25% band on any given pair of loads. This
    asserts something a count check cannot: the exact histogram of colours and
    their exact counts must match between two separate loads. The office is
    drawn from ``office_event`` rows only, so the honest expectation is byte
    equality, and it is what two live renders measured (colour and count
    identical for all 10 buckets).
    """
    url = _skip_unless_renderable()

    first = _render(url, tmp_path / "buckets_a.png")
    second = _render(url, tmp_path / "buckets_b.png")

    a_px = first.get("pixels_largest_canvas") or {}
    b_px = second.get("pixels_largest_canvas") or {}
    assert not a_px.get("error") and not b_px.get("error"), (
        f"could not read canvas pixels: A={a_px.get('error')}, "
        f"B={b_px.get('error')}"
    )

    a = [(x["color"], x["count"]) for x in a_px.get("top_buckets", [])]
    b = [(x["color"], x["count"]) for x in b_px.get("top_buckets", [])]

    assert a and b, f"no colour buckets reported (A={a}, B={b})"
    assert a == b, (
        "top colour buckets differ between two identical loads — the canvas is "
        "not deterministic (timer-driven, randomised, or clock-dependent "
        "drawing):\n"
        f"  run A: {a}\n"
        f"  run B: {b}\n"
        f"  run A non-bg: {a_px.get('non_background_pixels')}\n"
        f"  run B non-bg: {b_px.get('non_background_pixels')}"
    )

    print(
        f"\n[buckets] {len(a)} buckets identical across two loads: "
        + ", ".join(f"{c}={n}" for c, n in a)
    )
