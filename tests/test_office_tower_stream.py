"""M5 — the 400 ms poll sends only what changed, and the cache stays honest.

``state()`` returns the whole renderable office. At a 400 ms poll that is the
world ~2.5 times a second, and the response carries no way to tell an uneventful
poll from a real change, so the renderer redraws the identical picture. The fix
is an incremental view in ``voicebridge.office.tower_stream``: a fingerprint and
a delta, computed purely, that never touch ``state()`` or the full-payload
contract the other two tower test files pin.

These tests build states as tiny literal dicts that MIRROR the real ``state()``
schema (read from ``tower.py``): the top-level keys, the character dict, the
``position`` sub-dict, the ``hud`` figures, the ``floorplan`` floors/rooms/seats
shape, and the ``activity`` rows ``{id, ts, kind, agent_id, agent_slug, text}``.
Mirroring the schema by hand is the point: if ``state()``'s shape moves out from
under these tests, the literals stop matching what the module consumes and the
round-trip test fails — which is the signal we want, not a second copy of the
state builder.

Hermetic: pure dicts, no config, no store, no network, no provider call.
"""
from __future__ import annotations

import copy

import pytest

from voicebridge.office import tower_stream as TS


# --------------------------------------------------------------- fixtures
def _character(aid, *, animation="idle", x=5, y=3, facing="down",
               walking=False, at="desk", stalled=False, seated=True,
               floor=1, room="desks", room_id=10, slot=0,
               slug=None, last_event=None):
    """One seat, shaped exactly like ``tower.state()['characters'][i]``."""
    return {
        "agent_id": aid,
        "slug": slug or f"agent-{aid}",
        "seat_type": "worker",
        "department_id": 1,
        "supervisor_id": None,
        "seated": seated,
        "position": {"floor": floor, "floor_role": "operations",
                     "room": room, "room_id": room_id,
                     "x": x, "y": y, "slot": slot},
        "facing": facing,
        "walking": walking,
        "at": at,
        "animation": animation,
        "last_event": last_event,
        "stalled": stalled,
    }


def _activity_row(aid, ts=1000.0, kind="skill_used", ident=1):
    """One row, shaped exactly like ``state()['activity'][i]``."""
    return {"id": ident, "ts": ts, "kind": kind, "agent_id": aid,
            "agent_slug": f"agent-{aid}", "text": f"agent-{aid} used a skill"}


def _state(*, characters=None, net_cents=10_000, floors=1, rooms=1, seats=3,
           activity=None, empty=False, now=1000.0, headcount=None):
    """A small but schema-faithful tower state. Literal, so it cannot drift."""
    chars = characters if characters is not None else [
        _character(1), _character(2), _character(3)]
    feed = activity if activity is not None else [_activity_row(1, ident=1)]

    plan_floors = []
    for f in range(1, floors + 1):
        plan_rooms = []
        for r in range(1, rooms + 1):
            slots = [{"slot": n, "agent_id": 100 + n, "slug": f"a{100 + n}",
                      "x": 1 + n, "y": 1} for n in range(seats)]
            plan_rooms.append({
                "id": f * 10 + r, "slug": f"room-{f}-{r}", "kind": "work",
                "department_id": 1, "x": 0, "y": 0, "w": 6, "h": 4,
                "seats": slots})
        plan_floors.append({"id": f, "number": f, "name": f"Floor {f}",
                            "role": "operations", "theme": "", "z": f * 3,
                            "rooms": plan_rooms})

    out = {
        "schema": "qubit-office-tower/1",
        "now": now,
        "active_window_s": 120.0,
        "walk_s": 6.0,
        "empty": empty,
        "empty_reason": "no seats, no floors and no ledger rows: this org has "
                        "not started" if empty else "",
        "hud": {
            "headcount": headcount if headcount is not None else len(chars),
            "revenue_cents": 20_000,
            "cost_cents": 10_000,
            "net_cents": net_cents,
            "burn_cents_per_day": 500.0,
            "runway_days": 20.0,
            "runway_is_derived": True,
            "floors": [{"floor_id": f, "number": f, "role": "operations",
                        "departments": 1, "revenue_cents": 20_000,
                        "cost_cents": 10_000, "net_cents": 10_000}
                       for f in range(1, floors + 1)],
            "survival": {"floor_days": 30},
            "pnl_source": "office_v_org_pnl (the same view `qubit office pnl` "
                          "reads)",
        },
        "floorplan": {
            "schema": "qubit-office-floorplan/1",
            "grid": {"cols": 21, "rows": 10, "tile": 1},
            "floors": plan_floors,
        },
        "characters": chars,
        "activity": feed,
        "history": [{"day": 1, "date": "2026-10-13", "revenue_cents": 20_000,
                     "cost_cents": 10_000, "net_cents": 10_000}],
        "history_totals": {"revenue_cents": 20_000, "cost_cents": 10_000,
                           "net_cents": 10_000},
        "motion_rule": "animation is derived from office_event rows only; no "
                       "event within active_window_s means `idle` (C7)",
        "read_only": True,
        "preview": False,
    }
    # ``state()`` sets ``out["grid"] = plan["grid"]`` — the SAME object, so a
    # floorplan grid change moves the top-level grid too. Mirror that identity.
    out["grid"] = out["floorplan"]["grid"]
    return out


def _with_char(s, index, **kw):
    """Return a copy of ``s`` with character ``index`` patched."""
    out = copy.deepcopy(s)
    out["characters"][index].update(kw)
    return out


# =========================================================================
# 1. the fingerprint is stable, and moves for every rendered value
# =========================================================================
def test_fingerprint_is_stable_on_equal_states():
    a = _state()
    b = _state()
    assert TS.fingerprint(a) == TS.fingerprint(b)
    # and it is stable across repeated calls on the same object
    assert TS.fingerprint(a) == TS.fingerprint(a)


def test_fingerprint_is_a_hex_digest_not_a_python_hash():
    """A salted ``hash()`` would churn across processes — it must be hashlib."""
    fp = TS.fingerprint(_state())
    assert isinstance(fp, str)
    assert len(fp) == 32
    int(fp, 16)  # hex-decodable; would raise on a repr of an int


def test_animation_change_moves_the_fingerprint():
    base = _state()
    moved = _with_char(base, 0, animation="walking")
    assert TS.fingerprint(base) != TS.fingerprint(moved)


def test_position_x_change_moves_the_fingerprint():
    base = _state()
    moved = copy.deepcopy(base)
    moved["characters"][0]["position"]["x"] = base["characters"][0][
        "position"]["x"] + 1
    assert TS.fingerprint(base) != TS.fingerprint(moved)


def test_hud_net_cents_change_moves_the_fingerprint():
    base = _state(net_cents=10_000)
    moved = _state(net_cents=10_001)
    assert TS.fingerprint(base) != TS.fingerprint(moved)


def test_floorplan_room_count_change_moves_the_fingerprint():
    base = _state(rooms=1)
    moved = _state(rooms=2)
    assert TS.fingerprint(base) != TS.fingerprint(moved)


def test_a_new_activity_row_moves_the_fingerprint():
    base = _state(activity=[_activity_row(1, ident=1)])
    moved = _state(activity=[_activity_row(1, ident=1),
                             _activity_row(1, ident=2)])
    assert TS.fingerprint(base) != TS.fingerprint(moved)


def test_the_walking_and_facing_flags_alone_move_the_fingerprint():
    """The spec calls out the animation/position/walking/facing set — each."""
    base = _state()
    assert TS.fingerprint(base) != TS.fingerprint(
        _with_char(base, 0, walking=True))
    assert TS.fingerprint(base) != TS.fingerprint(
        _with_char(base, 0, facing="left"))
    assert TS.fingerprint(base) != TS.fingerprint(
        _with_char(base, 0, stalled=True))
    assert TS.fingerprint(base) != TS.fingerprint(
        _with_char(base, 0, at="station"))


def test_the_clock_alone_does_not_move_the_fingerprint():
    """``now`` advances on EVERY poll; hashing it would defeat the module."""
    base = _state(now=1000.0)
    later = _state(now=1400.0)
    assert TS.fingerprint(base) == TS.fingerprint(later)


def test_a_character_age_s_alone_does_not_move_the_fingerprint():
    """``last_event.age_s`` is ``now - ts``: it churns with the clock, not the
    office. The fingerprint keys on the event's *kind*, not its age."""
    base = _state()
    base["characters"][0]["last_event"] = {"kind": "tool_call", "age_s": 1.0}
    older = copy.deepcopy(base)
    older["characters"][0]["last_event"]["age_s"] = 999.0
    assert TS.fingerprint(base) == TS.fingerprint(older)
    # but a different event KIND is a real change
    changed = copy.deepcopy(base)
    changed["characters"][0]["last_event"] = {"kind": "run_failed",
                                             "age_s": 1.0}
    assert TS.fingerprint(base) != TS.fingerprint(changed)


def test_a_sliding_history_window_alone_does_not_move_the_fingerprint():
    """``history()`` derives its window from ``now``: a day passing renumbers
    every row even when the money is identical. That must not churn."""
    day = {"revenue_cents": 100, "cost_cents": 40, "net_cents": 60}
    earlier = _state()
    earlier["history"] = [dict(day, day=10, date="2026-10-01"),
                          dict(day, day=11, date="2026-10-02")]
    later = _state()
    later["history"] = [dict(day, day=11, date="2026-10-02"),
                        dict(day, day=12, date="2026-10-03")]
    assert TS.fingerprint(earlier) == TS.fingerprint(later)


def test_new_money_in_history_does_move_the_fingerprint():
    base = _state()
    base["history"] = [{"day": 10, "date": "2026-10-01",
                        "revenue_cents": 0, "cost_cents": 0, "net_cents": 0}]
    paid = copy.deepcopy(base)
    paid["history"][0]["revenue_cents"] = 5_000
    paid["history"][0]["net_cents"] = 5_000
    assert TS.fingerprint(base) != TS.fingerprint(paid)


def test_a_sliding_history_window_is_not_re_sent_as_a_delta_section():
    """The whole point, on the section level: a slid window is not a change."""
    day = {"revenue_cents": 100, "cost_cents": 40, "net_cents": 60}
    a = _state()
    a["history"] = [dict(day, day=10, date="2026-10-01")]
    b = _state()
    b["history"] = [dict(day, day=11, date="2026-10-02")]
    d = TS.delta(a, b)
    assert "history" not in d["sections"]
    assert "characters" not in d["sections"]


# =========================================================================
# 2. key insertion order must not churn the fingerprint
# =========================================================================
def _shuffled(value):
    """Rebuild every dict in ``value`` with its keys reversed.

    A same-content, different-insertion-order copy — the exact shape a dict
    produced by a different code path (or a JSON client) would have.
    """
    if isinstance(value, dict):
        items = list(value.items())
        return {k: _shuffled(v) for k, v in reversed(items)}
    if isinstance(value, list):
        return [_shuffled(v) for v in value]
    return value


def test_fingerprint_is_not_affected_by_key_insertion_order():
    base = _state()
    same = _shuffled(base)
    assert same == base, "the shuffle must not change the content"
    assert TS.fingerprint(base) == TS.fingerprint(same)


def test_fingerprint_is_not_affected_by_character_dict_key_order():
    base = _state()
    same = copy.deepcopy(base)
    for c in same["characters"]:
        items = list(c.items())
        same["characters"][same["characters"].index(c)] = {
            k: v for k, v in reversed(items)}
    assert same == base
    assert TS.fingerprint(base) == TS.fingerprint(same)


def test_character_list_reorder_does_not_move_the_fingerprint():
    """Order is NOT meaningful here: ``characters`` mirrors seats keyed by
    ``agent_id``, so a same-content reorder is the same office. Keying the
    canonical map on ``agent_id`` (rather than sorting the whole list) is what
    makes this true while still catching a seat being swapped out."""
    base = _state()
    flipped = _state(characters=list(reversed(base["characters"])))
    assert sorted(c["agent_id"] for c in flipped["characters"]) == \
           sorted(c["agent_id"] for c in base["characters"])
    assert TS.fingerprint(base) == TS.fingerprint(flipped)


# =========================================================================
# 3. the round-trip: cache + delta rebuilds the server's exact state
# =========================================================================
def test_apply_delta_round_trips_to_the_exact_next_state():
    first = _state(now=1000.0)
    second = _with_char(_state(now=1400.0, net_cents=10_500), 1,
                        animation="walking")

    d = TS.delta(first, second)
    rebuilt = TS.apply_delta(TS.seed_payload(first), d)
    assert rebuilt == second, "the client's cache drifted from the server"


def test_apply_delta_round_trips_a_real_schema_change_in_every_section():
    """A change in several sections at once still round-trips exactly."""
    first = _state(now=1000.0)
    second = _state(now=1000.0, net_cents=11_000, rooms=2,
                    activity=[_activity_row(1, ident=1),
                              _activity_row(2, ident=2)])
    second["characters"][0]["animation"] = "working"
    # in real ``state()``, ``out["grid"]`` IS ``plan["grid"]`` — the same object
    # — so a floorplan grid change moves both, and the delta must report both.
    second["floorplan"]["grid"] = {"cols": 30, "rows": 10, "tile": 1}
    second["grid"] = second["floorplan"]["grid"]
    d = TS.delta(first, second)
    assert set(d["sections"]) == {"hud", "floorplan", "characters",
                                  "activity", "grid"}, d["sections"]
    assert TS.apply_delta(TS.seed_payload(first), d) == second


def test_apply_delta_round_trips_over_a_chain_of_polls():
    """The cache must stay exact across many incremental polls, not just one."""
    states = []
    base = _state()
    for i in range(6):
        s = copy.deepcopy(base)
        s["now"] = 1000.0 + i * 0.4
        s["hud"]["net_cents"] = 10_000 + i
        s["characters"][0]["position"]["x"] = 5 + i
        s["characters"][2]["animation"] = "idle" if i % 2 else "working"
        s["activity"] = [_activity_row(1, ident=j) for j in range(1, i + 2)]
        states.append(s)

    cache = TS.seed_payload(states[0])
    for prev, nxt in zip(states, states[1:]):
        cache = TS.apply_delta(cache, TS.delta(prev, nxt))
    assert TS.apply_delta(cache, {"changed": False, "sections": [],
                                  "values": {}, "removed": {}}) == states[-1]


def test_apply_delta_accepts_a_plain_state_cache_too():
    """A poller holding a bare state (not a seed envelope) round-trips as well."""
    first = _state()
    second = _with_char(_state(net_cents=9_000), 0, animation="carrying")
    rebuilt = TS.apply_delta(first, TS.delta(first, second))
    assert rebuilt == second


def test_round_trip_is_exact_when_only_the_clock_advanced():
    """Requirement 3 must hold even when nothing but ``now`` moved.

    ``characters`` and ``history`` carry clock-derived content (``age_s``, the
    sliding day window), so the delta has to re-send them when the clock moves
    or the client's cache drifts from the server.
    """
    first = _state(now=2000.0)
    first["characters"][0]["last_event"] = {"kind": "tool_call", "age_s": 5.0}
    second = _state(now=2500.0)
    second["characters"][0]["last_event"] = {"kind": "tool_call", "age_s": 505.0}
    d = TS.delta(first, second)
    assert TS.fingerprint(first) == TS.fingerprint(second), (
        "the clock alone must not change the content fingerprint")
    rebuilt = TS.apply_delta(TS.seed_payload(first), d)
    assert rebuilt == second, "the cache drifted when only the clock moved"


# =========================================================================
# 4. an unchanged poll is cheap: changed False, sections empty
# =========================================================================
def test_an_unchanged_poll_reports_no_change_and_no_sections():
    # same content, but a fresh dict — an equal state, not the same object
    same = _state()
    d = TS.delta(_state(), same)
    assert d["changed"] is False
    assert d["sections"] == []
    assert d["reason"] == "no change since the last poll"


def test_an_unchanged_poll_still_carries_the_fingerprint():
    """The client must be able to confirm the fingerprint it holds is current."""
    s = _state()
    d = TS.delta(s, _state())
    assert d["fingerprint"] == TS.fingerprint(s)


def test_the_clock_advancing_alone_is_not_a_content_change():
    """``now`` moving is not a redraw: the fingerprint is unchanged.

    The delta may still carry the clock-derived sections (``characters`` and
    ``history`` hold ``age_s`` and the sliding day window, which must round-trip
    exactly), but it must NOT re-send the render sections the change-reduced
    fingerprint already proves identical.
    """
    a = _state(now=1000.0)
    b = _state(now=1400.0)
    d = TS.delta(a, b)
    assert TS.fingerprint(a) == TS.fingerprint(b)
    # no render section that the fingerprint covers is re-sent
    for name in ("hud", "floorplan", "activity", "history_totals", "grid",
                 "empty", "empty_reason", "preview"):
        assert name not in d["sections"], name
    assert "now" in d["sections"]


# =========================================================================
# 5. a removed seat is told, not silently kept
# =========================================================================
def test_a_removed_seat_is_listed_in_removed():
    before = _state(characters=[_character(1), _character(2), _character(3)])
    after = _state(characters=[_character(1), _character(3)])
    d = TS.delta(before, after)
    assert d["removed"]["characters"] == [2]
    assert 2 in d["removed"]["characters"]


def test_removed_seat_is_not_a_ghost_in_the_rebuilt_cache():
    """The point of reporting a removal: the client drops it."""
    before = _state(characters=[_character(1), _character(2), _character(3)])
    after = _state(characters=[_character(1), _character(3)])
    rebuilt = TS.apply_delta(TS.seed_payload(before), TS.delta(before, after))
    assert [c["agent_id"] for c in rebuilt["characters"]] == [1, 3]
    assert rebuilt == after


def test_no_removal_reports_an_empty_removed_map():
    d = TS.delta(_state(), _state())
    assert d["removed"] == {}


def test_a_removal_alone_counts_as_a_change():
    before = _state(characters=[_character(1), _character(2)])
    after = _state(characters=[_character(1)])
    d = TS.delta(before, after)
    assert d["changed"] is True
    assert "removed" in d["reason"]


# =========================================================================
# 6. purity: no timers, no I/O, no clock of its own
# =========================================================================
def test_the_module_is_pure_no_clock_no_io_no_timers():
    """The module must contain no clock read, no timer, and no I/O call.

    Checked on the source, comments stripped, because the docstring mentions the
    words it is careful not to use.
    """
    import re
    from pathlib import Path

    src = Path(TS.__file__).read_text()
    src = re.sub(r'""".*?"""', "", src, flags=re.S)   # module + fn docstrings
    code = re.sub(r"#[^\n]*", "", src)                # line comments
    for banned in ("time.time", "time.monotonic", "datetime.now",
                   "setInterval", "setTimeout", "threading", "asyncio",
                   "open(", "requests.", "urllib", "socket",
                   "random."):
        assert banned not in code, f"the module reaches for {banned!r}"


def test_the_module_calls_no_hash_builtin():
    """``hash()`` is salted per process — the fingerprint must use hashlib."""
    import re
    from pathlib import Path

    code = re.sub(r"#[^\n]*", "", Path(TS.__file__).read_text())
    code = re.sub(r'""".*?"""', "", code, flags=re.S)
    assert not re.search(r"(?<!\w)hash\(", code), (
        "hash() is PYTHONHASHSEED-dependent; use hashlib")


def test_delta_is_a_pure_function_of_its_arguments():
    a = _state()
    b = _with_char(_state(net_cents=12_000), 0, animation="working")
    d1 = TS.delta(a, b)
    d2 = TS.delta(a, b)
    assert d1 == d2
    # and the inputs were not mutated
    assert a == _state()
    assert b == _with_char(_state(net_cents=12_000), 0, animation="working")


def test_delta_handles_a_missing_previous_state():
    """The first poll after a reconnect has no cache: everything is 'changed'."""
    s = _state()
    d = TS.delta(None, s)
    assert d["changed"] is True
    assert set(d["sections"]) >= {"hud", "floorplan", "characters"}
    assert TS.fingerprint(s) == d["fingerprint"]


# =========================================================================
# the shell: seed_payload gives the full payload + fingerprint for poll #1
# =========================================================================
def test_seed_payload_is_the_full_state_plus_its_fingerprint():
    s = _state()
    seed = TS.seed_payload(s)
    assert seed["state"] == s, "the seed must carry the payload byte-for-byte"
    assert seed["fingerprint"] == TS.fingerprint(s)
    assert seed["changed"] is True


def test_the_section_names_mirror_the_real_state_schema():
    """The delta's section names must be real ``state()`` keys, not invented."""
    import inspect
    import re

    from voicebridge.office import tower

    src = inspect.getsource(tower.state)
    # every literal key out[...] = ... / "key": in the return dict
    real_keys = set(re.findall(r'out\[\s*"([a-z_]+)"\s*\]', src))
    real_keys |= set(re.findall(r'^\s*"([a-z_]+)":', src, re.M))
    assert set(TS.SECTIONS) <= real_keys, (
        f"invented sections: {set(TS.SECTIONS) - real_keys}")


def test_sections_match_the_keys_of_a_deeply_equal_literal_state():
    """The literal in THIS file must be the same shape the module expects."""
    s = _state()
    # `preview_legend` is omitted from ``state()`` unless preview is on, so the
    # only sections the literal must carry are the ones a live payload does.
    for name in TS.SECTIONS:
        if name == "preview_legend":
            continue
        assert name in s, f"the literal state is missing section {name!r}"
    # and a change to a section is visible to the fingerprint
    assert TS.fingerprint(s) != TS.fingerprint(
        {**s, "characters": [_character(1, animation="meeting")]})


@pytest.mark.parametrize("name", [n for n in TS.SECTIONS
                                  if n != "preview_legend"])
def test_changing_any_section_registers_somewhere(name):
    """No section of the literal is decorative: a change to it is seen."""
    base = _state()
    other = copy.deepcopy(base)
    value = other[name]
    if name == "characters":
        # append a real seat (a bare ``{}`` has no identity and is ignored)
        other[name] = value + [_character(999)]
    elif isinstance(value, bool):
        other[name] = not value
    elif isinstance(value, str):
        other[name] = value + " x"
    elif isinstance(value, list):
        other[name] = value + [{}]
    elif isinstance(value, dict):
        other[name] = {**value, "__probe__": 1}
    else:
        other[name] = 0
    d = TS.delta(base, other)
    assert d["changed"] is True, f"a change to {name!r} went unreported"
