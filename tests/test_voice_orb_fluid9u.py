"""Fluid orb: Oklab color cross-fade + spectrum-energy voice pump.

Owner 2026-10-06: distinct hue per action AND a smooth cross-fade
between them (no hard switches), plus Qubit's own voice deforming the
orb from the TTS spectrum energy.
"""
from __future__ import annotations


def test_blend_core_endpoints_and_vivid_midpoint():
    from voicebridge.orb import ember as E
    a, b = (34, 211, 238), (192, 132, 252)  # cyan -> violet
    assert tuple(E.blend_core(a, b, 0.0)) == a
    assert tuple(E.blend_core(a, b, 1.0)) == b
    mid = E.blend_core(a, b, 0.5)
    # Perceptually between the two, and NOT a muddy grey: the channel
    # spread stays wide (hue-swept LCh interpolation, not raw blend).
    assert max(mid) - min(mid) > 60, mid
    # Opposite hues (idle orange -> listening cyan) must also stay vivid
    # at the midpoint instead of washing to beige.
    omid = E.blend_core((255, 84, 0), (34, 211, 238), 0.5)
    assert max(omid) - min(omid) > 90, omid


def test_oklab_roundtrip_reasonable():
    from voicebridge.orb import ember as E
    for rgb in ((255, 84, 0), (34, 211, 238), (99, 102, 241),
                (251, 191, 36), (192, 132, 252), (255, 255, 255), (0, 0, 0)):
        back = E.oklab_to_rgb(E.rgb_to_oklab(rgb))
        assert all(abs(x - y) <= 3 for x, y in zip(back, rgb)), (rgb, back)


def test_color_transition_fades_then_lands():
    from voicebridge.orb import ember as E
    tr = E.ColorTransition("idle", now=0.0, duration=0.4)
    assert tr.core == E.STATE_CORES["idle"]
    tr.update("listening", 0.0)
    mid = tr.update("listening", 0.2)["core"]
    assert mid != E.STATE_CORES["idle"] and mid != E.STATE_CORES["listening"], mid
    land = tr.update("listening", 0.5)["core"]
    assert land == E.STATE_CORES["listening"]


def test_color_transition_reroutes_midfade_without_snap():
    from voicebridge.orb import ember as E
    tr = E.ColorTransition("idle", now=0.0, duration=0.4)
    tr.update("listening", 0.2)
    before = tr.core
    tr.update("speaking", 0.2)   # switch mid-fade
    assert tr.core == before     # continues from current color, no snap
    done = tr.update("speaking", 1.0)["core"]
    assert done == E.STATE_CORES["speaking"]


def test_spectrum_energy_mapping():
    from voicebridge.orb import ember as E
    assert E.spectrum_energy(None) is None
    assert E.spectrum_energy([]) is None
    assert E.spectrum_energy([0.0] * 12) == 0.0
    loud = E.spectrum_energy([0.9] * 12)
    quiet = E.spectrum_energy([0.1] * 12)
    assert loud > quiet and loud > 0.7


def test_render_colors_override_uses_family():
    from voicebridge.orb import ember as E
    fam = E.family_from_core(E.STATE_CORES["speaking"])
    stats = E.render(None, 48, 48, 46, 1.0, "idle", colors=fam)
    bright = E.resolve_params("idle")["brightness"]
    core_hex = E._hex(E._mix(fam["core"], E.NEAR_BLACK,
                             min(1.0, 0.55 + 0.35 * bright)))
    assert core_hex in stats["fills"]


def test_speaking_pump_from_spectrum_energy():
    from voicebridge.orb import ember as E

    def brightest(stats):
        best = 0
        for f in stats["fills"]:
            r, g, b = (int(f[i:i + 2], 16) for i in (1, 3, 5))
            best = max(best, r + g + b)
        return best

    loud = E.render(None, 48, 48, 46, 2.0, "speaking", spectrum=[0.9] * 12)
    quiet = E.render(None, 48, 48, 46, 2.0, "speaking", spectrum=[0.05] * 12)
    assert loud["bars"] is True and quiet["bars"] is True
    assert brightest(loud) >= brightest(quiet)


def test_qt_helpers_accept_family_override():
    from voicebridge.orb import ember as E
    from voicebridge.orb import qt_app as qa
    fam = E.family_from_core(E.STATE_CORES["speaking"])
    rgb, _a, _w = qa.band_pen(0, "idle", family=fam)
    assert tuple(rgb) == tuple(E.bands_from_family(fam)[0])
    stops = qa.core_stops("idle", family=fam)
    assert tuple(stops[1][1][:3]) == tuple(fam["core"])
    # brightness_scale modulates any state (speaking pump path)
    dim = qa.band_pen(0, "speaking", brightness_scale=0.2)
    full = qa.band_pen(0, "speaking", brightness_scale=1.0)
    assert dim[1] < full[1]
