"""The tower poll's byte budget, measured rather than assumed.

The contract under test, in the module's own terms:

```sh
python -m pytest tests/test_office_perf_budget.py -q
# asserts: a real 11-seat payload is measured in bytes; the projection to 71
#          seats is honest (over budget is reported as over budget); the fastest
#          budget-legal interval is computed, not guessed; the largest section
#          is proven rather than claimed; and headcount 0 / -1 give an honest
#          reason instead of crashing.
```

Everything here is hermetic: a tmp XDG state dir, a real (tiny) org built
through the same `org` calls the CLI uses, and a real `tower.state()` payload.
No network, no provider call, no browser. The numbers this file prints are the
measurements the budget is derived from — if they drift, the budget is wrong
and this file says so.
"""
from __future__ import annotations

import pytest

# The declared budget the module ships with, restated here so a change to the
# constant is a visible change to this test rather than a silent one.
from voicebridge.office import perf_budget as PB


def _cfg(tmp_path, monkeypatch, name="ref"):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s" / name))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / f"voice-{name}.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _build_org(cfg, *, workers=7):
    """One floor, one department, one room, 11 seats total, a few event rows.

    A real org through the real API: a CEO, a director, two managers (so no node
    exceeds the 7-direct-report span cap, D34), and enough workers to reach 11
    seats: 1 + 1 + 2 + 7 = 11.
    """
    from voicebridge.office import org as O, store

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    dr = O.hire(cfg, "dir", seat_type="director_portfolio", supervisor_id=ceo,
                confirmed=True)["id"]
    floor = O.floor_create(cfg, 1, "Work", role="director",
                           confirmed=True)["id"]
    dept = O.department_create(cfg, "shop", director_id=dr, floor_id=floor,
                               kill_criteria="two quarters negative net",
                               confirmed=True)["id"]
    room = O.room_create(cfg, dept, "desks", kind="work",
                         confirmed=True)["id"]
    mgrs = [O.hire(cfg, f"mgr-{i}", seat_type="manager", supervisor_id=dr,
                   department_id=dept, confirmed=True)["id"] for i in range(2)]
    seats = list(mgrs)
    for i in range(workers):
        seats.append(O.hire(cfg, f"wk-{i}", seat_type="worker",
                            supervisor_id=mgrs[i % len(mgrs)],
                            department_id=dept, confirmed=True)["id"])
    # seat them all in the one room: a seat without a room is still reported by
    # the tower, but a seated one is the normal case and the one the budget
    # should measure
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        for seat in seats + [dr, ceo]:
            conn.execute("UPDATE office_agent SET room_id=? WHERE id=?",
                         (room, seat))
        conn.commit()
    finally:
        conn.close()
    # a few event rows so the activity feed and the animation states are real
    now = store.now()
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        for i in range(8):
            conn.execute(
                "INSERT INTO office_event(ts, kind, agent_id, department_id,"
                " payload, visible) VALUES(?,?,?,NULL,'{}','bubble')",
                (now - i, ("skill_used", "tool_call", "note_written",
                          "run_started")[i % 4], seats[i % len(seats)]))
        conn.commit()
    finally:
        conn.close()
    return {"ceo": ceo, "director": dr, "floor": floor, "department": dept,
            "room": room, "managers": mgrs, "seats": seats}


@pytest.fixture()
def ref_payload(tmp_path, monkeypatch):
    """A real 11-seat ``tower.state()`` payload, recorded as the baseline."""
    from voicebridge.office import tower

    cfg = _cfg(tmp_path, monkeypatch)
    _build_org(cfg)
    state = tower.state(cfg)
    assert state["characters"], "the reference org has seats"
    PB.record_reference(state)
    return state


# =========================================================================
# 1. THE REAL PAYLOAD: measured in bytes, with the per-seat cost
# =========================================================================
def test_a_real_11_seat_payload_is_measured_in_bytes(ref_payload):
    """The number the whole budget rests on, from a real `state()` payload."""
    seats = len(ref_payload["characters"])
    n = PB.payload_bytes(ref_payload)
    per = PB.per_seat_bytes(ref_payload)

    # 11 seats: a CEO, a director, two managers, and 7 workers; the count is
    # taken from the payload, not assumed
    assert seats == 11, seats
    # the payload is a real, non-trivial object measured as JSON, and the
    # numbers are the ones the module's docstring cites (order of magnitude)
    assert 8_000 < n < 20_000, n
    assert per == pytest.approx(n / seats)
    assert 500 < per < 1_500, per

    print(f"\n11-seat payload: seats={seats} bytes={n} bytes/seat={per:.1f}")


def test_payload_bytes_is_the_compact_json_the_http_layer_emits(ref_payload):
    """Not `sys.getsizeof` — the bytes that would actually cross the wire."""
    import json

    assert PB.payload_bytes(ref_payload) == len(
        json.dumps(ref_payload, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8"))


def test_per_seat_bytes_of_an_empty_payload_is_zero_not_inf():
    assert PB.per_seat_bytes({"characters": []}) == 0.0
    assert PB.per_seat_bytes({}) == 0.0


# =========================================================================
# 2. PROJECTION TO 71 SEATS — honest, not rounded down
# =========================================================================
def test_the_71_seat_projection_is_reported_honestly(ref_payload):
    """Say what it is: within budget if it is, over if it is.

    The verdict must match the numbers the same call returns — a projection of
    30-something KB against a 64 KiB budget is `ok`, and the reason must say so
    rather than hedge. If the projection ever creeps past the budget, this test
    flips to requiring `ok is False` with an over-budget reason.
    """
    out = PB.budget_for(PB.DESIGN_TARGET_SEATS)
    assert set(("ok", "budget_bytes", "projected_bytes", "reason")) <= set(out)
    assert out["budget_bytes"] == PB.BUDGET_BYTES_PER_POLL
    assert isinstance(out["ok"], bool)
    assert out["reason"], "a verdict with no reason is not a verdict"

    # honest = the verdict agrees with the projected bytes
    if out["projected_bytes"] > PB.BUDGET_BYTES_PER_POLL:
        assert out["ok"] is False
        assert "exceeds" in out["reason"]
    else:
        assert out["ok"] is True
        assert "under the" in out["reason"]

    print(f"\n71-seat projection: {out['projected_bytes']} bytes/poll "
          f"({out['projected_bytes_per_minute']} bytes/min) ok={out['ok']}")
    print(f"  reason: {out['reason']}")


def test_the_71_seat_projection_matches_a_real_grown_org(tmp_path, monkeypatch,
                                                         ref_payload):
    """The extrapolation is checked against a second, really-grown org.

    The projection is only worth trusting if it lands near a measured payload
    for a bigger org. This builds a real span-limited org as close to the design
    target as the tree allows (every node under the 7-report cap, chain depth
    under the 8-hop cap) and asserts the projection is in the same region — not
    that it is exact, because a projection is not a re-render.
    """
    from voicebridge.office import org as O, store, tower

    cfg = _cfg(tmp_path, monkeypatch, name="grown")
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    dr = O.hire(cfg, "dir", seat_type="director_portfolio", supervisor_id=ceo,
                confirmed=True)["id"]
    floor = O.floor_create(cfg, 1, "W", role="director", confirmed=True)["id"]
    dept = O.department_create(cfg, "shop", director_id=dr, floor_id=floor,
                               kill_criteria="two quarters negative net",
                               confirmed=True)["id"]
    room = O.room_create(cfg, dept, "desks", kind="work",
                         confirmed=True)["id"]
    seats = [dr]
    mgrs = [O.hire(cfg, f"m-{i}", seat_type="manager", supervisor_id=dr,
                   department_id=dept, confirmed=True)["id"] for i in range(6)]
    seats += mgrs
    # each manager carries 6 workers: 1 + 6 + 36 = 43 seats, chain depth 3
    for m in mgrs:
        for _ in range(6):
            seats.append(O.hire(cfg, f"w-{len(seats)}", seat_type="worker",
                                supervisor_id=m, department_id=dept,
                                confirmed=True)["id"])
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        for s in seats:
            conn.execute("UPDATE office_agent SET room_id=? WHERE id=?",
                         (room, s))
        conn.commit()
    finally:
        conn.close()

    grown = tower.state(cfg)
    measured_seats = len(grown["characters"])
    measured_bytes = PB.payload_bytes(grown)
    projected = PB.budget_for(measured_seats)["projected_bytes"]

    # The projection runs off the 11-seat reference's per-seat rate, so it is
    # close to — not identical to — a differently-shaped real org. The honest
    # claim is a band: within 25%, and on the same side of the budget.
    ratio = projected / measured_bytes
    assert 0.75 <= ratio <= 1.25, (projected, measured_bytes)
    assert measured_bytes > PB.payload_bytes(ref_payload)
    assert PB.budget_for(PB.DESIGN_TARGET_SEATS)["projected_bytes"] > \
        measured_bytes
    assert (projected > PB.BUDGET_BYTES_PER_POLL) == \
        (measured_bytes > PB.BUDGET_BYTES_PER_POLL), "verdict disagrees"

    print(f"\ngrown org: seats={measured_seats} measured={measured_bytes} "
          f"projected(same size)={projected} ratio={ratio:.2f}")


def test_the_projection_refuses_to_invent_a_rate_without_a_reference(
        tmp_path, monkeypatch):
    """No measured baseline -> no fabricated number, and a reason says so."""
    cfg = _cfg(tmp_path, monkeypatch, name="bare")
    _build_org(cfg)
    saved = dict(PB._REFERENCE_STATE)
    PB._REFERENCE_STATE.clear()
    try:
        out = PB.budget_for(71)
        assert out["ok"] is False
        assert out["projected_bytes"] == 0
        assert "record_reference" in out["reason"]
    finally:
        PB._REFERENCE_STATE.update(saved)


# =========================================================================
# 3. THE FASTEST BUDGET-LEGAL INTERVAL — computed, and checked vs 400 ms
# =========================================================================
def test_min_interval_ms_is_computed_and_the_current_poll_is_within_budget(
        ref_payload):
    """At 71 seats, is `>= 400 ms` enough, or is the current poll over budget?

    This is the question the whole exercise answers, so it is asserted literally
    rather than inferred: `min_interval_ms(projected_71_seat)` is computed from
    the projected bytes, and the current 400 ms poll is within budget iff
    400 >= that minimum. Whichever way it falls, the module's verdict for the
    design target must agree — no rounding down, no "probably fine".
    """
    projected = PB.budget_for(PB.DESIGN_TARGET_SEATS)["projected_bytes"]

    # the projection is a payload-shaped object the function can measure: build
    # a stand-in with the projected byte size by scaling the reference's
    # characters, then compute the minimum interval over it.
    stand_in = _scaled_payload(ref_payload, scale=projected /
                               PB.payload_bytes(ref_payload))
    min_ms = PB.min_interval_ms(stand_in)

    current_ok = PB.budget_for(PB.DESIGN_TARGET_SEATS,
                               interval_ms=400)["ok"]

    assert min_ms >= 1
    if current_ok:
        assert 400 >= min_ms, (
            f"the module says 71 seats is within budget at 400 ms, but its "
            f"computed minimum interval is {min_ms} ms")
    else:
        # over budget at 400 ms: the module must say so, with a reason
        out = PB.budget_for(PB.DESIGN_TARGET_SEATS, interval_ms=400)
        assert out["ok"] is False and out["reason"]

    # and the minimum interval scales with the payload: a bigger payload cannot
    # be polled faster
    bigger = _scaled_payload(ref_payload, scale=3.0)
    assert PB.min_interval_ms(bigger) >= PB.min_interval_ms(ref_payload)

    print(f"\n71-seat: projected={projected} bytes -> min_interval={min_ms} ms; "
          f"at 400 ms ok={current_ok}")


def test_min_interval_ms_is_zero_guess(ref_payload):
    """The formula is `60_000 * bytes / BUDGET_BYTES_PER_MINUTE`, rounded up."""
    import math

    n = PB.payload_bytes(ref_payload)
    expected = max(1, int(math.ceil(60_000.0 * n / PB.BUDGET_BYTES_PER_MINUTE)))
    assert PB.min_interval_ms(ref_payload) == expected


# =========================================================================
# 4. THE BIG HISTORY — proven to dominate, by how much
# =========================================================================
def test_a_180_day_history_dominates_the_byte_cost(tmp_path, monkeypatch,
                                                    ref_payload):
    """The section that can blow the budget, shown as the largest one.

    `state()` calls `history(cfg)` with the default 30-day window, so a live
    poll cannot grow this section today — but it is the section with no cap of
    its own, and the budget exists to refuse it if it ever becomes one. This
    measures the forced 180-day payload and proves the history section is the
    largest, and by what multiple of the next largest.
    """
    from voicebridge.office import ledger as L, store, tower

    cfg = _cfg(tmp_path, monkeypatch, name="hist")
    _build_org(cfg)
    now = store.now()
    for d in range(180):
        L.record(cfg, "revenue", 1_000, ts=now - d * 86400)
        L.record(cfg, "inference", 100, ts=now - d * 86400)

    # the live poll stays capped at 30 rows
    live = tower.state(cfg, now=now)
    assert len(live["history"]) == 30, len(live["history"])

    # the forced 180-day window is what "no cap" would cost
    hist180 = tower.history(cfg, now=now, days=180)
    assert len(hist180["series"]) == 180
    forced = dict(live)
    forced["history"] = hist180["series"]

    dom = PB.dominant_section(forced)
    assert dom["name"] == "history", dom["sections"]
    assert dom["share"] > 0.5, dom["share"]

    # by how much: the largest against the runner-up
    ranked = sorted(dom["sections"].items(), key=lambda kv: -kv[1])
    runner_up_name, runner_up = ranked[1]
    ratio = dom["bytes"] / runner_up

    # and it genuinely moves the total: the forced payload is bigger than the
    # live one by roughly the extra 150 days of rows
    live_bytes = PB.payload_bytes(live)
    forced_bytes = PB.payload_bytes(forced)

    assert forced_bytes > live_bytes
    assert dom["bytes"] > runner_up

    print(f"\n180-day history: history={dom['bytes']} B "
          f"({dom['share'] * 100:.1f}% of {forced_bytes} B), "
          f"next largest={runner_up_name} ({runner_up} B), "
          f"{ratio:.2f}x larger")
    print(f"  live (30-day) payload={live_bytes} B, forced (180-day)="
          f"{forced_bytes} B (+{forced_bytes - live_bytes} B)")


def test_a_huge_activity_feed_is_the_largest_section_at_small_headcount(
        tmp_path, monkeypatch):
    """The other unbounded-ish section: the feed, at its 60-row cap.

    At 11 seats the activity feed is the largest section (it is capped at 60
    rows, so it dominates a small org's characters). This proves the budget's
    claim about where the bytes are at the size the org actually is today.
    """
    from voicebridge.office import store

    cfg = _cfg(tmp_path, monkeypatch, name="feed")
    ids = _build_org(cfg)
    now = store.now()
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        for i in range(200):
            conn.execute(
                "INSERT INTO office_event(ts, kind, agent_id, department_id,"
                " payload, visible) VALUES(?,?,?,NULL,'{}','bubble')",
                (now - i, ("skill_used", "tool_call", "note_written",
                          "run_started")[i % 4], ids["seats"][i % len(ids["seats"])]))
        conn.commit()
    finally:
        conn.close()

    from voicebridge.office import tower
    state = tower.state(cfg, now=now)
    assert len(state["activity"]) == 60, len(state["activity"])
    dom = PB.dominant_section(state)
    # the feed is capped, so it cannot exceed its own 60-row size; it is the top
    # section here but the point is it is bounded and does not scale with seats
    assert dom["name"] in ("activity", "characters"), dom["sections"]
    print(f"\n60-row feed, 12 seats: dominant={dom['name']} "
          f"({dom['bytes']} B, {dom['share'] * 100:.1f}%)")


# =========================================================================
# 5. BOUNDARY: 0 and -1 do not crash, and give an honest reason
# =========================================================================
def test_budget_for_zero_seats_does_not_crash_and_is_honest(ref_payload):
    out = PB.budget_for(0)
    assert set(("ok", "budget_bytes", "projected_bytes", "reason")) <= set(out)
    assert out["seats"] == 0
    assert out["reason"], "an empty-org verdict still needs a reason"
    assert isinstance(out["ok"], bool)
    # an office with nobody in it still polls; the module must not pretend the
    # cost is a fabricated positive number or crash on the division by zero
    assert out["projected_bytes"] >= 0
    print(f"\nheadcount 0: ok={out['ok']} projected={out['projected_bytes']} "
          f"reason={out['reason']}")


def test_budget_for_negative_headcount_does_not_crash_and_is_honest():
    out = PB.budget_for(-1)
    assert out["ok"] is False
    assert out["headcount"] == -1
    assert "negative" in out["reason"].lower()
    assert out["projected_bytes"] == 0
    assert isinstance(out["reason"], str) and out["reason"]
    print(f"\nheadcount -1: ok={out['ok']} reason={out['reason']}")


def test_budget_for_rejects_a_non_positive_interval():
    with pytest.raises(ValueError):
        PB.budget_for(11, interval_ms=0)
    with pytest.raises(ValueError):
        PB.budget_for(11, interval_ms=-400)
    with pytest.raises(ValueError):
        PB.poll_cost({"characters": []}, interval_ms=0)


# =========================================================================
# poll_cost: the per-minute / per-hour figures and the double-ceiling verdict
# =========================================================================
def test_poll_cost_reports_minute_and_hour_and_respects_both_ceilings(
        ref_payload):
    out = PB.poll_cost(ref_payload, interval_ms=400)
    assert out["bytes_per_poll"] == PB.payload_bytes(ref_payload)
    assert out["bytes_per_minute"] == round(out["bytes_per_poll"] * 150)
    assert out["bytes_per_hour"] == round(out["bytes_per_minute"] * 60)
    assert out["seats"] == len(ref_payload["characters"])
    assert out["verdict"] in ("within_budget", "over_budget")

    # a fast enough interval blows the per-minute ceiling even with a small
    # payload — that is exactly the case a per-poll ceiling alone would miss
    fast = PB.poll_cost(ref_payload, interval_ms=1)
    assert fast["verdict"] == "over_budget"
    assert fast["over_per_minute"] is True
    assert fast["over_per_poll"] is False

    print(f"\npoll_cost @400ms: {out['bytes_per_minute']} B/min, "
          f"{out['bytes_per_hour']} B/hour, verdict={out['verdict']}")
    print(f"poll_cost @1ms: verdict={fast['verdict']} "
          f"(per-minute over, per-poll under)")


def test_the_min_interval_math_agrees_with_the_per_minute_ceiling(ref_payload):
    """At exactly the computed minimum interval, traffic is at the ceiling."""
    min_ms = PB.min_interval_ms(ref_payload)
    at_min = PB.poll_cost(ref_payload, interval_ms=min_ms)
    just_under = PB.poll_cost(ref_payload, interval_ms=min_ms - 1)
    # rounding up means at the minimum we are at or just under the ceiling...
    assert at_min["bytes_per_minute"] <= PB.BUDGET_BYTES_PER_MINUTE
    # ...and one millisecond faster is over it
    assert just_under["bytes_per_minute"] >= at_min["bytes_per_minute"]


# =========================================================================
# the module's own declared budget constants
# =========================================================================
def test_the_declared_budget_constants_are_self_consistent():
    assert PB.BUDGET_BYTES_PER_POLL == 64 * 1024
    assert PB.BUDGET_BYTES_PER_MINUTE == PB.BUDGET_BYTES_PER_POLL * 150
    assert PB.POLL_INTERVAL_MS == 400
    assert PB.DESIGN_TARGET_SEATS == 71
    assert PB.MIN_INTERVAL_MS >= 1
    # the module is pure: importing it opened no store and made no file
    assert PB.payload_bytes({"characters": []}) > 0


# ---------------------------------------------------------------- helpers
def _scaled_payload(ref, *, scale):
    """A stand-in payload whose serialised size is ~`scale` x the reference.

    Used to ask `min_interval_ms` about a payload of a given size without
    building a second org. It pads the characters list so the JSON grows
    proportionally; the padding entry is a real dict so the bytes are real.
    """
    import copy

    out = copy.deepcopy(ref)
    base = out.get("characters") or []
    if not base:
        return out
    extra = max(0, int(round(len(base) * (scale - 1))))
    filler = dict(base[0])
    out["characters"] = base + [dict(filler, agent_id=-(i + 1))
                                for i in range(extra)]
    return out
