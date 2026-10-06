"""Orb options store: clamping, persistence, color overrides (TDD)."""
from __future__ import annotations


def test_defaults_and_clamp():
    from voicebridge.orb import options as O
    d = O.OrbOptions()
    assert d.size_px == 168 and d.opacity == 1.0 and d.speed == 1.0
    assert d.locked is False and d.show_tray is True
    c = O.clamp_options(O.OrbOptions(size_px=9999, opacity=-3,
                                     speed=99, locked=1))
    assert c.size_px == O.SIZE_MAX
    assert c.opacity == O.OPACITY_MIN
    assert c.speed == O.SPEED_MAX
    assert c.locked is True
    # garbage types -> defaults, never raises
    assert O.clamp_options(O.OrbOptions(size_px="x", opacity=None)).size_px == 168


def test_roundtrip_atomic(tmp_path):
    from voicebridge.orb import options as O
    p = tmp_path / "opts.json"
    o = O.OrbOptions(size_px=200, opacity=0.8, locked=True, speed=1.5,
                     click_through=True, show_tray=False,
                     colors={"listening": "#22d3ee"})
    O.save_options(o, p)
    back = O.load_options(p)
    assert back.size_px == 200 and back.opacity == 0.8 and back.locked is True
    assert back.speed == 1.5 and back.click_through is True
    assert back.show_tray is False
    assert back.colors == {"listening": "#22D3EE"}


def test_load_missing_and_corrupt_is_default(tmp_path):
    from voicebridge.orb import options as O
    assert O.load_options(tmp_path / "nope.json") == O.OrbOptions()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert O.load_options(bad) == O.OrbOptions()


def test_color_overrides_parsed_and_invalid_dropped():
    from voicebridge.orb import options as O
    o = O.OrbOptions(colors={"listening": "#22D3EE", "speaking": "nope",
                             "bogus": "#FFFFFF"})
    rgb = O.color_overrides(o)
    assert rgb == {"listening": (34, 211, 238)}
    assert O.color_overrides(O.OrbOptions()) == {}


def test_updated_clamps():
    from voicebridge.orb import options as O
    o = O.OrbOptions()
    assert O.updated(o, size_px=1000).size_px == O.SIZE_MAX
    assert O.updated(o, opacity=0.5).opacity == 0.5
