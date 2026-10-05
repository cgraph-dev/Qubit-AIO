"""Per-state colors + de-glitched motion (renderer lane, TDD).

Fails until the renderer lane lands:
- single-source per-state color table (idle/listening/thinking/speaking/error)
- dwell/hysteresis (flap sequence -> stable display)
- wall-clock phase continuity across irregular dts
- unknown -> idle mapping
- frame-time p95 helper bound
"""
from __future__ import annotations


def test_state_color_table_single_source():
    from voicebridge.orb import ember as E
    assert hasattr(E, "STATE_COLORS"), "single-source per-state color table"
    for name in ("idle", "listening", "thinking", "speaking", "error"):
        assert name in E.STATE_COLORS, name
        fam = E.STATE_COLORS[name]
        for key in ("core", "mid", "warm_a", "warm_b", "hi"):
            assert key in fam, (name, key)
            r, g, b = fam[key]
            assert all(0 <= v <= 255 for v in (r, g, b)), (name, key)


def test_state_colors_documented_hues():
    from voicebridge.orb import ember as E
    idle = E.state_colors("idle")
    assert tuple(idle["core"]) == (255, 84, 0)
    # Owner contract 2026-10-05 (supersedes 2026-10-04): GREEN = HEARING
    # YOU (listening), YELLOW = THINKING/ANSWERING (thinking + speaking).
    lst = E.state_colors("listening")
    assert tuple(lst["core"]) == (60, 220, 120)
    r, g, b = lst["core"]
    assert g > r and g > b and r < 120, (r, g, b)
    # speaking renders the voice green (agent talking to you)
    spk = E.state_colors("speaking")
    assert tuple(spk["core"]) == (60, 220, 120)
    r, g, b = spk["core"]
    assert g > r and g > b and r < 120, (r, g, b)
    # thinking: yellow (R dominant).
    thk = E.state_colors("thinking")
    assert tuple(thk["core"]) == (255, 205, 70)
    r, g, b = thk["core"]
    assert r >= g > b and b < 120, (r, g, b)
    # error/not-working: red (R dominant)
    err = E.state_colors("error")
    r, g, b = err["core"]
    assert r > g and r > b and r >= 200, (r, g, b)


def test_band_colors_per_state_no_dup_literals():
    from voicebridge.orb import ember as E
    idle_bands = E.band_colors("idle")
    assert len(idle_bands) == 5
    assert tuple(idle_bands[0]) == (255, 84, 0)
    # listening bands are green-family (agent hears you)
    r, g, b = E.band_colors("listening")[0]
    assert g > r, (r, g, b)
    # speaking bands are the voice green (agent talking to you)
    r, g, b = E.band_colors("speaking")[0]
    assert g > r and g > b, (r, g, b)
    # unknown -> idle
    assert E.band_colors("warp-drive") == E.band_colors("idle")


def test_qt_helpers_read_single_source():
    from voicebridge.orb import ember as E
    from voicebridge.orb import qt_app as qa
    for bi in range(len(E.BAND_SPECS)):
        rgb, _a, _w = qa.band_pen(bi, "speaking")
        assert tuple(rgb) == tuple(E.band_colors("speaking")[bi % 5]), bi
        rgb_l, _a2, _w2 = qa.band_pen(bi, "listening")
        assert tuple(rgb_l) == tuple(E.band_colors("listening")[bi % 5]), bi
    stops = qa.core_stops("speaking")
    core = E.state_colors("speaking")["core"]
    assert tuple(stops[1][1][:3]) == tuple(core)
    stops_l = qa.core_stops("listening")
    assert tuple(stops_l[1][1][:3]) == tuple(E.state_colors("listening")["core"])
    # unknown -> idle in the Qt helpers too
    assert qa.band_pen(0, "nope") == qa.band_pen(0, "idle")


def test_dwell_flap_sequence_stable():
    from voicebridge.orb import state as S
    assert hasattr(S, "DisplayState"), "hysteresis smoother"
    assert hasattr(S, "MIN_DWELL_S")
    d = S.DisplayState(initial="idle", now=100.0)
    # listening onset is instant
    assert d.update("listening", 100.1) == "listening"
    # immediate flap back to idle is held (dwell)
    assert d.update("idle", 100.2) == "listening"
    assert d.update("idle", 100.3) == "listening"
    # after the dwell the idle sticks
    assert d.update("idle", 101.3) == "idle"
    # error is immediate from anywhere
    d2 = S.DisplayState(initial="speaking", now=200.0)
    assert d2.update("error", 200.1) == "error"


def test_phase_continuity_across_irregular_dts():
    from voicebridge.orb import ember as E
    assert hasattr(E, "advance_render_time"), "wall-clock clamped advance"
    # same wall-time (0.2 s) via one step vs many small steps -> same phase
    one = E.advance_render_time(0.0, 0.0, 0.2)
    many = 0.0
    t = 0.0
    for _ in range(6):
        nxt = t + 0.2 / 6
        many = E.advance_render_time(many, t, nxt)
        t = nxt
    assert one == many or abs(one - many) < 1e-9
    # a huge hitch clamps instead of jumping (no teleport judder)
    assert E.advance_render_time(1.0, 1.0, 6.0) <= 1.0 + 0.25 + 1e-9
    # same wall-time -> same wave shape regardless of frame count
    assert E.band_point(24.0, one, E.BAND_SPECS[2], 96.0, 48.0, 48.0) == \
        E.band_point(24.0, many, E.BAND_SPECS[2], 96.0, 48.0, 48.0)


def test_frame_time_p95_helper_bound():
    from voicebridge.orb import ember as E
    assert hasattr(E, "frame_time_stats"), "frame-time distribution helper"
    stats = E.frame_time_stats([33.0] * 100)
    assert stats["count"] == 100
    assert stats["p95"] <= 50.0
    assert stats["p50"] <= 40.0


def test_unknown_state_still_idle():
    from voicebridge.orb import ember as E
    assert E.resolve_params("warp-drive") == E.resolve_params("idle")
    assert E.state_colors("warp-drive") == E.state_colors("idle")
