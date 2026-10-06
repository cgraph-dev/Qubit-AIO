"""Amber-orb polish tests: speed_scale, palette overrides, living pulse.

Owner 2026-10-06: make the orb render more alive/organic/beautiful while
keeping every existing invariant. These tests pin the new, purely
additive surface (hermetic: no Tk, no display, no network):

- ``render(..., speed_scale=...)`` is deterministic and actually changes
  the frame; clamps to [0.2, 3.0].
- ``state_core(state, overrides=...)`` / ``ColorTransition(...,
  overrides=...)`` honour a per-state palette override and fall back to
  the single-source table otherwise (malformed never raises).
- Voice energy (loud vs quiet spectrum / level) changes the rendered
  fills, deterministically (a pure function of its inputs).
- ``render`` called twice with identical args is byte-identical.
"""
from __future__ import annotations

from voicebridge.orb import ember as E


class FakeCanvas:
    """Canvas-like recorder; no window, no Tk."""

    def __init__(self) -> None:
        self.ovals = []
        self.lines = []

    def create_oval(self, x0, y0, x1, y1, **kw):
        self.ovals.append((x0, y0, x1, y1, kw))
        return len(self.ovals)

    def create_line(self, *pts, **kw):
        self.lines.append((pts, kw))
        return len(self.lines)


# -- 1. speed_scale ----------------------------------------------------------

def test_speed_scale_deterministic_and_changes_motion():
    base_a = E.render(None, 48, 48, 46, 2.5, "speaking")
    base_b = E.render(None, 48, 48, 46, 2.5, "speaking")
    assert base_a == base_b  # purity

    fast_a = E.render(None, 48, 48, 46, 2.5, "speaking", speed_scale=2.0)
    fast_b = E.render(None, 48, 48, 46, 2.5, "speaking", speed_scale=2.0)
    assert fast_a == fast_b
    # A different clock multiplier must produce a different live frame.
    assert fast_a != base_a


def test_speed_scale_clamped_to_range():
    lo = E.render(None, 48, 48, 46, 1.0, "idle", speed_scale=0.0)
    lo_edge = E.render(None, 48, 48, 46, 1.0, "idle",
                       speed_scale=E.SPEED_SCALE_MIN)
    assert lo == lo_edge

    hi = E.render(None, 48, 48, 46, 1.0, "idle", speed_scale=99.0)
    hi_edge = E.render(None, 48, 48, 46, 1.0, "idle",
                       speed_scale=E.SPEED_SCALE_MAX)
    assert hi == hi_edge

    # A bad multiplier degrades to the neutral 1.0 clock.
    bad = E.render(None, 48, 48, 46, 1.0, "idle", speed_scale="fast")
    neutral = E.render(None, 48, 48, 46, 1.0, "idle", speed_scale=1.0)
    assert bad == neutral


def test_speed_scale_default_is_identity():
    default = E.render(None, 48, 48, 46, 3.3, "thinking")
    explicit_one = E.render(None, 48, 48, 46, 3.3, "thinking",
                            speed_scale=1.0)
    assert default == explicit_one


# -- 2. palette overrides ----------------------------------------------------

def test_state_core_overrides():
    ov = {"idle": (10, 20, 30), "listening": (1, 2, 3)}
    assert E.state_core("idle", overrides=ov) == (10, 20, 30)
    assert E.state_core("LISTENING", overrides=ov) == (1, 2, 3)
    # A state absent from the override map reads the single-source table.
    assert E.state_core("speaking", overrides=ov) == E.STATE_CORES["speaking"]
    # No overrides -> unchanged behaviour.
    assert E.state_core("idle") == E.STATE_CORES["idle"]
    assert E.state_core("idle", overrides={}) == E.STATE_CORES["idle"]
    # Malformed override never raises; it falls back to the table.
    assert E.state_core("idle", overrides={"idle": "nope"}) == \
        E.STATE_CORES["idle"]
    assert E.state_core("idle", overrides={"idle": (1, 2)}) == \
        E.STATE_CORES["idle"]
    # Out-of-range values are clamped, not rejected.
    assert E.state_core("speaking", overrides={"speaking": (999, -5, 100)}) == \
        (255, 0, 100)


def test_color_transition_honours_overrides():
    ov = {"listening": (7, 8, 9)}
    tr = E.ColorTransition("idle", now=0.0, duration=0.4, overrides=ov)
    assert tr.core == E.STATE_CORES["idle"]
    tr.update("listening", 0.0)
    mid = tr.update("listening", 0.2)["core"]
    assert mid not in (E.STATE_CORES["idle"], (7, 8, 9))
    land = tr.update("listening", 0.5)["core"]
    assert tuple(land) == (7, 8, 9)
    # A state with no override still cross-fades to the table colour.
    tr.update("speaking", 0.5)
    done = tr.update("speaking", 1.0)["core"]
    assert tuple(done) == E.STATE_CORES["speaking"]


def test_family_from_core_unchanged_public_contract():
    # The required additions must not disturb the existing ramp builders.
    fam = E.family_from_core((7, 8, 9))
    assert fam["core"] == (7, 8, 9)
    assert E.bands_from_family(fam)[0] == (7, 8, 9)


# -- 3. energy drives the render (deterministically) -------------------------

def test_energy_changes_render_and_is_deterministic():
    loud = [0.95] * 12
    quiet = [0.02] * 12
    a = E.render(None, 48, 48, 46, 2.5, "speaking", spectrum=loud)
    b = E.render(None, 48, 48, 46, 2.5, "speaking", spectrum=loud)
    c = E.render(None, 48, 48, 46, 2.5, "speaking", spectrum=quiet)
    assert a == b                       # deterministic
    assert a["bars"] is True and c["bars"] is True
    assert a != c                       # loud vs quiet differ
    assert a["fills"] != c["fills"]     # the visible fills differ


def test_listening_level_drives_render_deterministically():
    hi = E.render(None, 48, 48, 46, 2.5, "listening", level=0.95)
    hi2 = E.render(None, 48, 48, 46, 2.5, "listening", level=0.95)
    lo = E.render(None, 48, 48, 46, 2.5, "listening", level=0.05)
    assert hi == hi2
    assert hi != lo


def test_non_voice_states_ignore_spectrum_and_energy():
    # Invariant guard: the polish must not let energy leak into states
    # with no audio to analyse.
    for st in ("thinking", "working", "idle", "error"):
        plain = E.render(None, 48, 48, 46, 2.5, st)
        spec = E.render(None, 48, 48, 46, 2.5, st, spectrum=[0.9] * 12)
        lvl = E.render(None, 48, 48, 46, 2.5, st, level=0.9)
        assert plain == spec == lvl, st


# -- 4. purity across the new surface ----------------------------------------

def test_render_purity_identical_args():
    args = (None, 48, 48, 46, 3.1, "speaking")
    kwargs = {"spectrum": [0.4] * 12, "speed_scale": 1.7}
    assert E.render(*args, **kwargs) == E.render(*args, **kwargs)


def test_render_purity_reduced_motion_static_with_speed_scale():
    c1, c2 = FakeCanvas(), FakeCanvas()
    s1 = E.render(c1, 48, 48, 46, 5.0, "listening", reduced_motion=True,
                  spectrum=[0.8] * 12, speed_scale=2.0)
    s2 = E.render(c2, 48, 48, 46, 50.0, "listening", reduced_motion=True,
                  spectrum=[0.8] * 12, speed_scale=2.0)
    assert (c1.lines, s1["points"]) == (c2.lines, s2["points"])


def test_render_never_raises_with_new_params():
    for bad in (None, "x", [1], object()):
        stats = E.render(None, 48, 48, 46, 1.0, "listening",
                         speed_scale=bad)
        assert stats["lines"] == 5 and stats["points"] > 0
