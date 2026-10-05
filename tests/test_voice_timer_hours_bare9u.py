"""Lane 4 (2026-10-03) — timer gaps: bare-set, hours, bare "in X", tens words.

RED-first audit findings in ``voicebridge/local/actions.py``:
(a) the set-branch pattern requires a literal space after "timer", so a bare
"set a timer" matches NOTHING (the start-branch has no such space, which is
why "start a timer" worked); (b) hours were never a unit — "for 2 hours"
fell into the bare prefix and silently became 5 minutes; (c) a bare
"in 5 minutes" matched nothing. All hermetic: no mic, no model, no service.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _minutes(text: str) -> tuple:
    from voicebridge.local import actions as LA
    m = LA.match(text)
    assert m is not None and m.name == "timer", (text, m)
    return int(m.params.get("minutes"))


def test_timer_bare_set_variants_default_five():
    for text in ("set a timer", "set the timer", "set my timer",
                 "set an timer", "set timer"):
        assert _minutes(text) == 5, text


def test_timer_hours_convert_to_minutes():
    assert _minutes("set a timer for 2 hours") == 120
    assert _minutes("set a timer for one hour") == 60
    assert _minutes("set a timer for an hour") == 60
    assert _minutes("remind me in 2 hours") == 120
    assert _minutes("in 2 hours") == 120
    assert _minutes("timer for 3 hours") == 180  # clamp, not 180+


def test_timer_bare_in_minutes():
    assert _minutes("in 5 minutes") == 5
    assert _minutes("in one minute") == 1


def test_timer_half_hour():
    assert _minutes("half an hour") == 30
    assert _minutes("set a timer for half an hour") == 30


def test_timer_tens_words():
    assert _minutes("set a timer for forty minutes") == 40
    assert _minutes("set a timer for forty two minutes") == 42
    assert _minutes("set a timer for fifty minutes") == 50


def test_timer_existing_shapes_unchanged():
    assert _minutes("set a timer for one minute") == 1
    assert _minutes("set a timer for 5 minutes") == 5
    assert _minutes("set a timer for twenty one minutes") == 21
    assert _minutes("set a timer for 30 seconds") == 1
    assert _minutes("set a timer for ten seconds") == 1
    assert _minutes("remind me in 5 minutes") == 5
    assert _minutes("start a timer") == 5
    assert _minutes("start the timer") == 5
