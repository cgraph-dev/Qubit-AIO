"""The walk is a pure function of the event and the clock.

`walk_for` places a character on the path from its desk to the station its
event names. These tests pin that placement: no event means the desk, age 0
has not left the desk, a finished walk stands on the station, a mid-walk
reports progress strictly between 0 and 1, and the same inputs always return
the same point.

Hermetic: a hand-built floorplan, tmp XDG state only; no network, no provider
call, no live service, no real state directory.
"""
from __future__ import annotations

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


SEAT_ID = 7
ROOM_ID = 1
DESK = (5, 0)
ROOM_ORIGIN = (0, 0)
TS = 1_000.0


def _plan():
    """The smallest floorplan `walk_for` will accept. Not a store read."""
    return {
        "floors": [{
            "number": 0,
            "rooms": [{
                "id": ROOM_ID,
                "x": ROOM_ORIGIN[0],
                "y": ROOM_ORIGIN[1],
                "seats": [{
                    "agent_id": SEAT_ID,
                    "x": DESK[0],
                    "y": DESK[1],
                }],
            }],
        }],
    }


def _station_xy(kind):
    from voicebridge.office.tower import STATIONS, _EVENT_STATION

    name = _EVENT_STATION[kind]
    dx, dy = STATIONS[name]
    return ROOM_ORIGIN[0] + dx, ROOM_ORIGIN[1] + dy


def _walk(kind, *, age, target=None):
    from voicebridge.office import tower as T

    return T.walk_for(
        SEAT_ID, plan=_plan(), room_id=ROOM_ID, event_kind=kind,
        ts=TS, now=TS + age, escalation_target=target)


# ---------------------------------------------------------------- no event
def test_no_event_stays_at_the_desk(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch)
    got = _walk("", age=0.0)
    assert got["walking"] is False
    assert got["at"] == "desk"
    assert (got["x"], got["y"]) == DESK
    assert "progress" not in got


# ------------------------------------------------------- age 0 is the desk
def test_age_zero_has_not_left_the_desk(tmp_path, monkeypatch):
    """An event that names a station has not moved the character at age 0."""
    _cfg(tmp_path, monkeypatch)
    from voicebridge.office.tower import _EVENT_STATION

    kind = "tool_call"
    assert kind in _EVENT_STATION
    station = _station_xy(kind)
    assert station != DESK

    got = _walk(kind, age=0.0)
    assert (got["x"], got["y"]) == DESK
    assert (got["x"], got["y"]) != station


# ------------------------------------------------------- the walk finishes
def test_at_walk_s_the_character_stands_on_the_station(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch)
    from voicebridge.office import tower as T

    kind = "tool_call"
    station = _station_xy(kind)
    for age in (T.WALK_S, T.WALK_S + 30.0):
        got = _walk(kind, age=age)
        assert got["walking"] is False
        assert got["at"] == "station"
        assert (got["x"], got["y"]) == station
        assert "progress" not in got


# ------------------------------------------------------------- mid-walk
def test_mid_walk_reports_progress_between_zero_and_one(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch)
    from voicebridge.office import tower as T

    for age in (1.0, T.WALK_S / 2):
        assert 0 < age < T.WALK_S
        got = _walk("ledger_write", age=age)
        assert got["walking"] is True
        assert got["at"] == "walking"
        assert "progress" in got
        assert 0 < got["progress"] < 1
        assert got["progress"] == round(age / T.WALK_S, 3)


# ------------------------------------------------- pure function of the clock
def test_the_same_event_and_clock_always_return_the_same_point(
        tmp_path, monkeypatch):
    """No hidden state: two calls with identical arguments are identical."""
    _cfg(tmp_path, monkeypatch)
    from voicebridge.office import tower as T

    plan = _plan()
    now = TS + T.WALK_S / 2
    first = T.walk_for(
        SEAT_ID, plan=plan, room_id=ROOM_ID, event_kind="mcp_call",
        ts=TS, now=now)
    second = T.walk_for(
        SEAT_ID, plan=plan, room_id=ROOM_ID, event_kind="mcp_call",
        ts=TS, now=now)
    assert first == second
    assert first["walking"] is True
    assert plan == _plan()


# ---------------------------------------------------------------- facing
def test_facing_follows_the_dominant_axis(tmp_path, monkeypatch):
    _cfg(tmp_path, monkeypatch)
    from voicebridge.office.tower import _facing

    assert _facing({"x": 0, "y": 0}, {"x": 3, "y": 1}) == "right"
    assert _facing({"x": 0, "y": 0}, {"x": -2, "y": 1}) == "left"
    assert _facing({"x": 0, "y": 0}, {"x": 1, "y": 4}) == "down"
    assert _facing({"x": 0, "y": 0}, {"x": 1, "y": -4}) == "up"
    # a tie prefers the horizontal axis
    assert _facing({"x": 0, "y": 0}, {"x": 2, "y": 2}) == "right"
    assert _facing({"x": 5, "y": 0}, {"x": 1, "y": 2}) == "left"


# ===========================================================================
# THE DROP SITE (the hole this file used to have)
#
# Everything above tests `walk_for` -- the PRODUCER of the walk point. But
# `walk_for` is not what the renderer reads. `state()` is: it copies the walk
# into each character's `position`, and it used to copy only x and y and DROP
# `progress`. The renderer's bob keys on `position.progress`, so it drew
# nothing for hours while every producer-level test stayed green.
#
# Deleting that one line in `state()` passed this entire file. These tests
# exercise `state()`, so the fix cannot silently vanish again.
# ===========================================================================

def _org_with_a_seat(cfg):
    """A minimal real org: one floor, one department, one room, one seat.

    `state()` reads the store, so this has to be a real org rather than a
    hand-built dict -- the point is to exercise the code path that dropped the
    field, which a dict would bypass.
    """
    from voicebridge.office import org as O

    f = O.floor_create(cfg, 1, name="F1", role="operations", confirmed=True)["id"]
    d = O.department_create(
        cfg, "ops", floor_id=f, division_kind="product",
        kill_criteria="closes if it produces nothing for 30 days",
        confirmed=True)["id"]
    r = O.room_create(cfg, d, "desk-a", confirmed=True)["id"]
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    ws = O.hire(cfg, "ws", seat_type="worker", supervisor_id=ceo,
                room_id=r, confirmed=True)["id"]
    return {"room": r, "seat": ws}


def _state_char(cfg, agent_id):
    from voicebridge.office import tower as TWR

    st = TWR.state(cfg)
    return next(c for c in st["characters"] if c["agent_id"] == agent_id)


def test_state_carries_progress_while_the_character_is_walking(
        tmp_path, monkeypatch):
    """THE regression test for the dropped field.

    A character whose most recent event puts it mid-walk must arrive at the
    renderer with `position.progress` set, because that value is the ONLY
    permitted source of the bob (C7: no client clock). If someone removes the
    copy in `state()`, this fails -- and nothing else does.
    """
    from voicebridge.office import store

    cfg = _cfg(tmp_path, monkeypatch)
    org = _org_with_a_seat(cfg)

    # an event NOW, whose kind maps to a station, so the walk is in progress
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        conn.execute(
            "INSERT INTO office_event(ts, kind, agent_id, department_id,"
            " payload) VALUES(?,?,?,?,?)",
            (store.now(), "tool_call", org["seat"], None, "{}"))
        conn.commit()
    finally:
        conn.close()

    pos = _state_char(cfg, org["seat"])["position"]
    assert pos.get("progress") is not None, (
        "state() dropped `position.progress`: the renderer's walk bob reads "
        "this field, and without it a walking character is drawn standing still")
    assert 0.0 <= pos["progress"] <= 1.0, pos["progress"]


def test_state_omits_progress_when_the_character_is_not_walking(
        tmp_path, monkeypatch):
    """A stale value must not masquerade as a live walk.

    The converse failure is worse than the missing field: if `progress` were
    carried unconditionally, an idle character would keep the last walk's
    value and bob while standing at its desk.
    """
    from voicebridge.office import store

    cfg = _cfg(tmp_path, monkeypatch)
    org = _org_with_a_seat(cfg)

    # an event far outside the active window: the walk is over
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        conn.execute(
            "INSERT INTO office_event(ts, kind, agent_id, department_id,"
            " payload) VALUES(?,?,?,?,?)",
            (store.now() - 86_400.0, "tool_call", org["seat"], None, "{}"))
        conn.commit()
    finally:
        conn.close()

    cs = _state_char(cfg, org["seat"])
    assert cs["position"].get("progress") is None, (
        "a stale `progress` was carried onto a character that is not walking")
    assert cs["walking"] is False


def test_state_omits_progress_with_no_events_at_all(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    org = _org_with_a_seat(cfg)
    cs = _state_char(cfg, org["seat"])
    assert cs["position"].get("progress") is None
    assert cs["animation"] == "idle"
