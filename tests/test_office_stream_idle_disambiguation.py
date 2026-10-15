"""The two kinds of "nothing is happening" must not look the same.

An agent that ran ten minutes ago and an agent that has NEVER produced an event
row are both reported by the tower as `animation: "idle"` -- that is C7, and it
is correct: `activity_from_events` has no branch that renders motion from a
stale row. But the SERVER used to hand the client the same payload for both, so
the office displayed:

  * "idle 6 min ago"       for an agent that had just done real work, and
  * "idle 20741 d ago"     for one that had never run at all

with 20741 days being `now - undefined` rendered as a number. On screen an
agent that had just finished a turn was indistinguishable from one that had
never started, which reads as "the office is dead" or "it is not connected".

These tests pin the disambiguator, not the implementation: after the fix the
snapshot must let a consumer tell the three states apart --
never-ran, ran-then-went-quiet, and running-now.
"""
import time

import pytest

from voicebridge import office_stream as S
from voicebridge.office import tower


def _character(**over):
    """One tower character, as `tower.state()` emits it.

    `position` must name a room that exists in the snapshot: `to_snapshot`
    SKIPS any character it cannot place in a declared room (a character drawn
    in the wrong room looks like a fact), so a fixture with a null position
    would silently produce an empty agent list and every assertion below would
    be vacuous.
    """
    ch = {
        "agent_id": 1,
        "slug": "mira",
        "seat_type": "worker",
        "department_id": 1,
        "room_id": "f2r2",
        "seated": True,
        "position": {"floor": 2, "room_id": 2, "x": 0.0, "y": 0.0},
        "facing": "down",
        "walking": False,
        "at": "desk",
        "animation": "idle",
        "last_event": None,
        "stalled": False,
        "supervisor_id": None,
        "memory": {"count": 0, "known": False},
        "dossier": {"known": False, "beliefs": [], "dimensions": []},
    }
    ch.update(over)
    return ch


def _state(characters):
    """A tower payload with the nesting `to_snapshot` actually reads.

    The rooms live under `floorplan.floors[].rooms[]` (not a top-level `floors`
    key), and the agent->room join is by the STRING id `f<floor>r<room id>`.
    Getting this wrong yields an empty agents list rather than an error, so the
    assertions below would pass vacuously.
    """
    return {
        "now": time.time(),
        "floorplan": {
            "floors": [{
                "number": 2,
                "role": "work",
                "rooms": [{
                    "id": 2,
                    "slug": "desk-a",
                    "seats": [{"agent_id": 1}, {"agent_id": 2}],
                }],
            }],
        },
        "characters": characters,
        "hud": {},
        "ledger_rows": 0,
        "activity": [],
        "history": {},
        "empty": False,
    }


def test_never_ran_agent_is_flagged_and_has_no_age():
    """No event row at all -> hasHistory False and no fabricated age.

    A fabricated age is what produced the 20741-day reading: the client
    subtracts an absent timestamp. The server must say plainly that there is
    nothing to measure.
    """
    snap = S.to_snapshot(_state([_character(last_event=None)]), 1, time.time())
    agent = snap["agents"][0]
    assert agent["hasHistory"] is False
    assert agent["lastEventAgeS"] is None


def test_agent_that_ran_then_went_quiet_reports_its_real_age():
    """A stale row still yields a truthful, finite age.

    This is the case the bug hid: the tower says `idle` (correct), but the
    payload must still carry *how long* ago the work happened, or a just-finished
    turn is indistinguishable from one that never happened.
    """
    ch = _character(last_event={"kind": "run_settled", "age_s": 400.0})
    snap = S.to_snapshot(_state([ch]), 1, time.time())
    agent = snap["agents"][0]
    assert agent["hasHistory"] is True
    assert agent["lastEventAgeS"] == 400.0
    # and the tower's own verdict is untouched -- still idle, by design
    assert agent["status"] == "idle"


def test_the_two_idle_states_are_distinguishable():
    """The whole point: equal `status`, different `hasHistory`.

    If this ever passes with both agents identical, the disambiguator is gone
    and the office has regressed to showing every quiet agent the same way.
    """
    never = _character(agent_id=1, slug="mira", last_event=None)
    quiet = _character(agent_id=2, slug="wren",
                       last_event={"kind": "tool_call", "age_s": 900.0})
    snap = S.to_snapshot(_state([never, quiet]), 1, time.time())
    by_name = {a["name"]: a for a in snap["agents"]}

    # same rendered status ...
    assert by_name["mira"]["status"] == by_name["wren"]["status"] == "idle"
    # ... but not the same story
    assert by_name["mira"]["hasHistory"] is False
    assert by_name["wren"]["hasHistory"] is True
    assert by_name["wren"]["lastEventAgeS"] == 900.0


def test_a_running_agent_is_not_reported_as_history_only():
    """Fresh work must still light up as work, not as an age reading."""
    now = time.time()
    rows = [{"id": 1, "kind": "run_started", "ts": now - 5.0, "agent_id": 1}]
    act = tower.activity_from_events(rows, now=now)
    assert act[1]["animation"] == "working"
    assert act[1]["fresh"] is True

    ch = _character(last_event={"kind": "run_started", "age_s": 5.0},
                    animation="working")
    snap = S.to_snapshot(_state([ch]), 1, now)
    agent = snap["agents"][0]
    assert agent["status"] == "working"
    assert agent["hasHistory"] is True
    assert agent["lastEventAgeS"] == 5.0


def test_age_is_carried_through_even_when_the_window_has_passed():
    """`age_s` survives the freshness gate instead of being zeroed with it.

    The freshness gate decides ANIMATION. It must not decide whether the age is
    worth reporting -- that conflation is the defect.
    """
    now = time.time()
    stale = now - (tower.ACTIVE_WINDOW_S + 60.0)
    rows = [{"id": 1, "kind": "tool_call", "ts": stale, "agent_id": 7}]
    act = tower.activity_from_events(rows, now=now)
    assert act[7]["fresh"] is False
    assert act[7]["animation"] == "idle"
    # the age is still there, and still truthful
    assert act[7]["age_s"] == pytest.approx(tower.ACTIVE_WINDOW_S + 60.0, abs=1.0)
