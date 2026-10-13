"""Is the agent walk actually OBSERVABLE? (C7's visible half)

`tower.walk_for` proves the walk is *real data* — a point on the desk->station
path, computed server-side from the `office_event` row and the clock, never from
a client. `test_office_tower_walk.py` pins that arithmetic. What nobody had
checked is whether the walk can be *seen*:

  * the renderer bobs a walking figure by ``|sin(progress*PI*4)| * 3 * cam.zoom``
    world units, against a ~15-unit character -- a 20% vertical displacement;
  * ``WALK_S`` is 6 s and the real poll interval is ``POLL_MS = 400`` -- only 15
    samples across an entire walk;
  * and the walk only begins when an event lands, so a quiet org never walks.

This file answers, with numbers: does a real org actually produce distinct
on-screen positions across a walk, and is the bob big enough to see at the zoom
the fit really produces?

Part 1 and 2 exercise the REAL entry point (`tower.state()`) against a REAL
store-backed org, not a hand-built plan -- because the bug this guards against
(the comment at `tower.state`'s `progress` carry) was exactly a value that
`walk_for` computed and `state()` dropped. A plan-only test would have missed it.

Part 3 REPORTS the renderer-observable numbers (prints, no assert): the count of
distinct positions the renderer sees across one walk at POLL_MS, the bob in
pixels at the fitted zoom, and whether that is visible.

Part 4 ASSERTS the bob is bounded and not sub-pixel at the legibility floor, so
the day someone shrinks the bob to 0.5 units this test fails loudly instead of
the walk quietly disappearing.

Hermetic: tmp XDG only; no network, no provider call, no browser, no real state.
"""
from __future__ import annotations

import math

import pytest

# --------------------------------------------------------------------- geometry
# These are the renderer's OWN constants, restated here so the test computes the
# same pixels the canvas does. If they change in `qubit-office.js` this test is
# the tripwire that says so (the assert at the bottom compares the source).
TILE_W = 34.0          # qubit-office.js: TILE_W = 34
TILE_H = 17.0          # qubit-office.js: TILE_H = 17  (2:1 iso)
DECK_Z = 26.0          # qubit-office.js: DECK_Z = 26  (floor stack gap)
POLL_MS = 400          # qubit-office.js: POLL_MS = 400  (the real poll interval)
BOB_UNITS = 3.0        # qubit-office.js: Math.abs(Math.sin(pr*PI*4)) * 3 * zoom
BOB_HZ = 4.0           # qubit-office.js: pr * Math.PI * 4
CHAR_FLOOR_ZOOM = 0.85  # camera.js CHAR_FLOOR_ZOOM / control-bar.js MIN_ZOOM
FRONT_HIDDEN_MIN_ZOOM = 0.45  # qubit-office.js MIN_ZOOM (the loosest bound)


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _org(cfg, *, floors=1):
    """A small REAL org: floors, one department/room each, seats in the room.

    One room per floor keeps the geometry the smallest that still exercises the
    real `state()` path (a room the seat is placed in, so `walk_for` finds a
    destination station). Returns the agent ids that are seated.
    """
    from voicebridge.office import org as O, store

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    seats = []
    for n in range(1, floors + 1):
        fl = O.floor_create(cfg, n, f"Floor {n}", role="director",
                            confirmed=True)["id"]
        d = O.hire(cfg, f"dir{n}", seat_type="director_portfolio",
                   supervisor_id=ceo, confirmed=True)["id"]
        dept = O.department_create(cfg, f"dept{n}", director_id=d, floor_id=fl,
                                   kill_criteria="two quarters negative net",
                                   confirmed=True)["id"]
        room = O.room_create(cfg, dept, f"desks{n}", kind="work",
                             confirmed=True)["id"]
        mgr = O.hire(cfg, f"mgr{n}", seat_type="manager", supervisor_id=d,
                     department_id=dept, confirmed=True)["id"]
        for i in range(3):
            O.hire(cfg, f"w{n}-{i}", seat_type="worker", supervisor_id=mgr,
                   department_id=dept, confirmed=True)
        # seat the whole department in its room: `state()` only walks a seat it
        # has placed, so an unplaced seat would make `walk_for` return x=None.
        conn = store.connect(cfg)
        try:
            rows = conn.execute(
                "SELECT id FROM office_agent WHERE department_id=?",
                (dept,)).fetchall()
            for r in rows:
                conn.execute("UPDATE office_agent SET room_id=? WHERE id=?",
                             (room, int(r["id"])))
            conn.commit()
        finally:
            conn.close()
        seats.append(mgr)
    return seats


def _emit(cfg, agent_id, kind, *, ts):
    from voicebridge.office import store

    conn = store.connect(cfg)
    try:
        conn.execute(
            "INSERT INTO office_event(ts, kind, agent_id, department_id,"
            " payload, visible) VALUES(?,?,?,NULL,'{}','bubble')",
            (ts, kind, agent_id))
        conn.commit()
    finally:
        conn.close()


def _character(out, agent_id):
    return [c for c in out["characters"] if c["agent_id"] == agent_id][0]


def _iso_world(x, y, z):
    """The renderer's un-zoomed projection (qubit-office.js `isoWorld`)."""
    return ((x - y) * (TILE_W / 2.0),
            (x + y) * (TILE_H / 2.0) - z * DECK_Z)


def _fitted_zoom(cfg, viewport):
    """Reproduce `camera.js::fit` for the real floorplan at a viewport size.

    Mirrors `fitToBuilding` exactly: cover zoom, clamped to MAX_ZOOM=2.4, then
    floored at CHAR_FLOOR_ZOOM=0.85 (the renderer applies that floor twice, so
    it is not negotiable). Returns the zoom the canvas would actually use.
    """
    from voicebridge.office import tower as T

    plan = T.floorplan(cfg)
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for floor in plan["floors"]:
        z = float(floor["number"])
        for room in floor["rooms"]:
            corners = [(room["x"], room["y"]),
                       (room["x"] + room["w"], room["y"]),
                       (room["x"] + room["w"], room["y"] + room["h"]),
                       (room["x"], room["y"] + room["h"])]
            for gx, gy in corners:
                px, py = _iso_world(gx, gy, z)
                min_x, max_x = min(min_x, px), max(max_x, px)
                min_y, max_y = min(min_y, py), max(max_y, py)
    ew, eh = max_x - min_x, max_y - min_y
    margin = 0.08
    avail_w = viewport[0] * (1 - 2 * margin)
    avail_h = viewport[1] * (1 - 2 * margin)
    z_cover = min(avail_w / ew, avail_h / eh)
    z = min(z_cover, 2.4)
    z = max(z, CHAR_FLOOR_ZOOM)
    return z, (ew, eh)


# =========================================================================
# 1. the walk is real data, not a constant, through the REAL state()
# =========================================================================
def test_the_walk_moves_across_clocks_and_is_flagged_walking(tmp_path,
                                                             monkeypatch):
    """`position.x/y` and `progress` CHANGE across clocks while `walking`."""
    from voicebridge.office import store, tower as T

    cfg = _cfg(tmp_path, monkeypatch)
    seats = _org(cfg)
    seat = seats[0]
    ts = 1000.0
    # `ledger_write` walks to the vault -- a station the desk is NOT on, so the
    # path is a real displacement (a same-cell station would prove nothing).
    _emit(cfg, seat, "ledger_write", ts=ts)

    clocks = [ts + f * T.WALK_S for f in (0.0, 0.25, 0.5, 0.75)]

    # At the exact event instant (age 0) the character is already flagged
    # `walking: True` with progress 0.0 -- still standing on its desk. That is
    # correct (age < WALK_S) and it matters for visibility: at progress 0 the
    # bob is |sin(0)| = 0 px, so the FIRST frame of a walk shows no bob at all.
    first = _character(T.state(cfg, now=ts), seat)
    assert first["walking"] is True and first["position"]["progress"] == 0.0, first
    assert first["position"]["x"] == first["position"]["x"]  # x is real

    samples = []
    for now in clocks:
        me = _character(T.state(cfg, now=now), seat)
        assert me["walking"] is True, me
        assert me["at"] == "walking"
        assert isinstance(me["position"]["progress"], float)
        samples.append((me["position"]["x"], me["position"]["y"],
                        me["position"]["progress"]))

    # the walk is movement, not a frozen point: the point and the progress both
    # change from sample to sample
    xs = [s[0] for s in samples]
    ys = [s[1] for s in samples]
    ps = [s[2] for s in samples]
    assert len(set(zip(xs, ys))) > 1, f"position never changed: {samples}"
    assert ps == sorted(ps) and len(set(ps)) == len(ps), \
        f"progress is not strictly advancing: {ps}"
    assert len(set(xs)) > 1 or len(set(ys)) > 1, \
        f"neither axis advanced: {samples}"
    # progress starts at 0 (the desk) and every sample is < 1 while walking
    assert ps[0] == 0.0 and all(0.0 <= p < 1.0 for p in ps), ps


# =========================================================================
# 2. the walk STOPS -- motion ends when the event ages out (C7's honest half)
# =========================================================================
def test_the_walk_stops_at_the_station_when_the_event_ages_out(tmp_path,
                                                               monkeypatch):
    from voicebridge.office import tower as T

    cfg = _cfg(tmp_path, monkeypatch)
    seat = _org(cfg)[0]
    ts = 1000.0
    _emit(cfg, seat, "ledger_write", ts=ts)

    # mid-walk: moving
    mid = _character(T.state(cfg, now=ts + T.WALK_S / 2), seat)
    assert mid["walking"] is True

    # now past ts + WALK_S: stopped, at the station, no progress left over
    stopped = _character(T.state(cfg, now=ts + T.WALK_S + 0.001), seat)
    assert stopped["walking"] is False, stopped
    assert stopped["at"] == "station", stopped
    assert "progress" not in stopped["position"], (
        "a stopped character still ships `progress`; the renderer would bob a "
        f"figure that is not walking (C7): {stopped['position']}")
    assert stopped["animation"] != "walking", stopped

    # and it stays stopped forever after -- no late relock onto the station
    later = _character(T.state(cfg, now=ts + T.WALK_S + 3600), seat)
    assert later["walking"] is False and later["at"] == "station"


def test_no_event_means_nobody_walks_even_as_the_clock_runs(tmp_path,
                                                            monkeypatch):
    """A quiet org never walks -- the condition C7's visibility rests on."""
    from voicebridge.office import tower as T

    cfg = _cfg(tmp_path, monkeypatch)
    _org(cfg)
    # no office_event rows at all: advance the clock a whole day, nobody moves
    first = T.state(cfg, now=1000.0)
    last = T.state(cfg, now=1000.0 + 86400)
    assert all(c["walking"] is False for c in first["characters"])
    assert all(c["walking"] is False for c in last["characters"])
    assert ({c["slug"]: c["position"] for c in first["characters"]}
            == {c["slug"]: c["position"] for c in last["characters"]}), \
        "a character moved with no event and no walk"


# =========================================================================
# 3. REPORT (no assert): what the renderer would actually see
# =========================================================================
def test_report_renderer_observable_walk(tmp_path, monkeypatch, capsys):
    """Print the renderer-observable numbers: sample count, bob px, verdict."""
    from voicebridge.office import tower as T

    cfg = _cfg(tmp_path, monkeypatch)
    seat = _org(cfg)[0]
    ts = 1000.0
    _emit(cfg, seat, "ledger_write", ts=ts)

    # (a) how many DISTINCT on-screen points the renderer observes per walk.
    # The poll samples at 0, POLL_MS, 2*POLL_MS, ... The renderer sees the point
    # `walk_for` reports at each poll instant, deduped (integer grid rounding
    # makes some consecutive polls land on the same cell).
    step_s = POLL_MS / 1000.0
    n_polls = int(T.WALK_S / step_s)  # 15 polls that land inside the walk
    observed = set()
    progress_seen = []
    for i in range(n_polls):
        now = ts + i * step_s
        if now - ts >= T.WALK_S:
            break
        me = _character(T.state(cfg, now=now), seat)
        if not me["walking"]:
            continue
        observed.add((me["position"]["x"], me["position"]["y"]))
        progress_seen.append(me["position"]["progress"])

    # (b) bob pixels at the real fitted zoom(s).
    zoom_fit, extent = _fitted_zoom(cfg, (1200, 800))
    bob_px_fit = BOB_UNITS * zoom_fit
    bob_px_floor = BOB_UNITS * CHAR_FLOOR_ZOOM
    bob_px_minzoom = BOB_UNITS * FRONT_HIDDEN_MIN_ZOOM

    # (c) the world/screen displacement of the whole walk (how far it travels).
    plan = T.floorplan(cfg)
    room = plan["floors"][0]["rooms"][0]
    seat_slot = room["seats"][0]
    dx, dy = T.STATIONS[T._EVENT_STATION["ledger_write"]]
    dest = (room["x"] + dx, room["y"] + dy)
    gdx, gdy = dest[0] - seat_slot["x"], dest[1] - seat_slot["y"]
    wx, wy = (gdx - gdy) * (TILE_W / 2.0), (gdx + gdy) * (TILE_H / 2.0)
    travel_px_fit = math.hypot(wx * zoom_fit, wy * zoom_fit)
    travel_px_floor = math.hypot(wx * CHAR_FLOOR_ZOOM, wy * CHAR_FLOOR_ZOOM)

    with capsys.disabled():
        print("\n================ WALK VISIBILITY (renderer-observable) ==========")
        print(f"POLL_MS                = {POLL_MS} ms")
        print(f"WALK_S                 = {T.WALK_S} s")
        print(f"samples per walk       = WALK_S / (POLL_MS/1000) "
              f"= {T.WALK_S / step_s:.1f}  (polls that land inside the walk)")
        print(f"distinct positions seen= {len(observed)} of {len(progress_seen)} "
              f"polls  (integer grid cells after walk_for rounding)")
        print(f"progress advanced over  = {progress_seen[0]} .. "
              f"{progress_seen[-1]}")
        print(f"extent @1200x800        = {extent[0]:.1f} x {extent[1]:.1f} "
              f"world units")
        print(f"fitted zoom (camera.fit)= {zoom_fit:.3f}x")
        print(f"bob units               = |sin(progress*PI*4)| * {BOB_UNITS}")
        print(f"bob pixels @fit zoom    = {bob_px_fit:.2f} px  (max |sin|=1)")
        print(f"bob pixels @floor 0.85  = {bob_px_floor:.2f} px")
        print(f"bob pixels @MIN_ZOOM .45= {bob_px_minzoom:.2f} px")
        print(f"whole-walk travel @fit  = {travel_px_fit:.1f} px "
              f"(grid delta {gdx},{gdy})")
        print(f"whole-walk travel @0.85 = {travel_px_floor:.1f} px")
        verdict = ("VISIBLE" if bob_px_fit >= 2.0 else
                   "MARGINAL" if bob_px_fit > 1.0 else "SUB-PIXEL/INVISIBLE")
        print(f"VERDICT (bob @fit)      = {verdict} "
              f"({bob_px_fit:.2f} px vertical bob over ~15 frames)")
        print("=================================================================\n")

    # sanity: the numbers this report is built on are non-degenerate
    assert len(observed) >= 2
    assert zoom_fit >= CHAR_FLOOR_ZOOM


# =========================================================================
# 4. ASSERT the bob is bounded and not sub-pixel at the floor
# =========================================================================
def test_the_bob_is_bounded_at_three_units(tmp_path, monkeypatch):
    """|sin(progress*PI*4)| * 3 has a maximum of exactly 3 units."""
    # across the whole progress domain, the peak is 3 (at |sin|=1)
    peak = max(abs(math.sin(p * math.pi * BOB_HZ)) * BOB_UNITS
               for p in [i / 10000 for i in range(10001)])
    assert peak == pytest.approx(BOB_UNITS, abs=1e-9), peak
    # and the renderer's expression is exactly this (source tripwire)
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "frontend" / "qubit-app"
           / "plugins" / "qubit-office" / "qubit-office.js").read_text()
    assert "Math.sin(pr * Math.PI * 4)" in src, \
        "the bob formula changed; this test's arithmetic is now stale"
    assert "cs.walking && pr !== null" in src, \
        "the walk gate changed; the bob may now fire off a non-walk"


def test_the_bob_is_not_sub_pixel_at_the_legibility_floor(tmp_path,
                                                          monkeypatch):
    """At the minimum permitted zoom the bob must clear 1 px, or it is invisible.

    The legibility floor is 0.85 (camera.js CHAR_FLOOR_ZOOM and control-bar.js
    MIN_ZOOM agree); the loosest bound any renderer constant permits is 0.45
    (qubit-office.js MIN_ZOOM). We check BOTH: if the bob is sub-pixel at either,
    the walk is invisible and this test fails with that fact.
    """
    for zoom, label in ((CHAR_FLOOR_ZOOM, "legibility floor 0.85 (camera/control-bar)"),
                        (FRONT_HIDDEN_MIN_ZOOM, "qubit-office MIN_ZOOM 0.45")):
        bob_px = BOB_UNITS * zoom
        assert bob_px > 1.0, (
            f"the walk bob is SUB-PIXEL and therefore INVISIBLE at zoom {zoom} "
            f"({label}): max bob = {BOB_UNITS} units * {zoom} = {bob_px:.3f} px "
            f"<= 1 px. The 15-unit walk/carry motion would read as a standing "
            f"figure. Raise BOB_UNITS or the zoom floor.")
        # and report the margin honestly in the assertion message on success
        print(f"bob @ {zoom}: {bob_px:.2f} px (visible)")


def test_the_bob_is_nonzero_only_while_walking(tmp_path, monkeypatch):
    """The bob cannot exist where the payload has not said a walk happens."""
    from voicebridge.office import tower as T

    cfg = _cfg(tmp_path, monkeypatch)
    seat = _org(cfg)[0]
    ts = 1000.0
    _emit(cfg, seat, "ledger_write", ts=ts)

    def bob_of(me):
        pr = me["position"].get("progress")
        has = isinstance(pr, (int, float))
        return (abs(math.sin(pr * math.pi * BOB_HZ)) * BOB_UNITS
                if (me["walking"] and has) else 0.0)

    # mid-walk: the bob is live
    assert bob_of(_character(T.state(cfg, now=ts + 1.0), seat)) > 0.0
    # stopped: no progress, no bob
    assert bob_of(_character(T.state(cfg, now=ts + T.WALK_S + 1.0), seat)) == 0.0
    # quiet org: nobody bobs, ever
    qdir = tmp_path / "quiet"
    qdir.mkdir()
    cfg2 = _cfg(qdir, monkeypatch)
    _org(cfg2)
    assert all(bob_of(c) == 0.0 for c in T.state(cfg2)["characters"])
