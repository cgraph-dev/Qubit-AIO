"""M4 — the tower never shows work that is not happening (C7).

The contract names this file:

```sh
python -m pytest tests/test_office_tower_no_phantom_motion.py -q
# asserts: with no new office_event rows for N seconds, every character's
#          animation state is `idle` and no position changes.
```

That assertion is only meaningful because the animation state is computed in
Python from the event rows, as a pure function of (rows, clock). If it lived in
a browser, this test could not exist and C7 would be a comment.

Also here: the milestone's other proofs that can be checked without a browser —
a stalled turn shows as stalled rather than busy, the HUD matches `office pnl`
to the cent, and an org with no rows renders the empty state.

Hermetic: tmp XDG state only; no network, no provider call, no browser.
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


def _office(cfg, *, slots=3):
    """A small real org: a floor, a department, a room, seats in it."""
    from voicebridge.office import org as O

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    d = O.hire(cfg, "dir", seat_type="director_portfolio", supervisor_id=ceo,
               confirmed=True)["id"]
    floor = O.floor_create(cfg, 1, "Work", role="director", confirmed=True)["id"]
    dept = O.department_create(cfg, "shop", director_id=d, floor_id=floor,
                               kill_criteria="two quarters negative net",
                               confirmed=True)["id"]
    room = O.room_create(cfg, dept, "desks", kind="work", confirmed=True)["id"]
    mgr = O.hire(cfg, "mgr", seat_type="manager", supervisor_id=d,
                 department_id=dept, confirmed=True)["id"]
    seats = [mgr]
    for i in range(slots):
        seats.append(O.hire(cfg, f"wk-{i}", seat_type="worker",
                            supervisor_id=mgr, department_id=dept,
                            confirmed=True)["id"])
    # seat them: the tower places a seat in its room or not at all
    from voicebridge.office import store
    conn = store.connect(cfg)
    try:
        for seat in seats:
            conn.execute("UPDATE office_agent SET room_id=? WHERE id=?",
                         (room, seat))
        conn.commit()
    finally:
        conn.close()
    return {"ceo": ceo, "director": d, "floor": floor, "department": dept,
            "room": room, "manager": mgr, "seats": seats}


def _emit(cfg, agent_id, kind, *, ts=None):
    from voicebridge.office import store
    conn = store.connect(cfg)
    try:
        conn.execute("INSERT INTO office_event(ts, kind, agent_id, department_id,"
                     " payload, visible) VALUES(?,?,?,NULL,'{}','bubble')",
                     (ts if ts is not None else store.now(), kind, agent_id))
        conn.commit()
    finally:
        conn.close()


# =========================================================================
# THE PROOF: freeze the events, advance the clock, everything goes still
# =========================================================================
def test_with_no_new_events_every_character_is_idle(tmp_path, monkeypatch):
    from voicebridge.office import store, tower

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _office(cfg)
    now = store.now()

    # every seat is mid-work: one fresh event each
    for i, seat in enumerate(ids["seats"]):
        _emit(cfg, seat, "skill_used", ts=now - i)

    live = tower.motion_states(cfg, now=now)
    assert live["moving"] == len(ids["seats"]), live["animations"]

    # --- the event stream freezes. Time passes. ---
    frozen = tower.motion_states(cfg, now=now + tower.ACTIVE_WINDOW_S + 1)

    assert frozen["moving"] == 0, frozen["animations"]
    assert set(frozen["animations"].values()) == {"idle"}


def test_no_position_changes_when_the_event_stream_is_frozen(tmp_path,
                                                             monkeypatch):
    from voicebridge.office import store, tower

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _office(cfg)
    now = store.now()
    for i, seat in enumerate(ids["seats"]):
        _emit(cfg, seat, "working_placeholder_unknown_kind", ts=now - i)

    a = tower.state(cfg, now=now)
    b = tower.state(cfg, now=now + 3600)
    pos_a = {c["slug"]: c["position"] for c in a["characters"]}
    pos_b = {c["slug"]: c["position"] for c in b["characters"]}
    assert pos_a == pos_b, "a character moved without an event moving it"
    # and the floorplan is deterministic too, so the building does not shuffle
    assert a["floorplan"] == b["floorplan"]


def test_motion_is_derived_from_events_and_never_from_the_clock(tmp_path,
                                                               monkeypatch):
    """The strongest form: motion can only appear when a *row* appears."""
    from voicebridge.office import store, tower

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _office(cfg)
    seat = ids["seats"][0]
    now = store.now()

    # no rows at all -> nothing moves, however much time passes
    for offset in (0, 60, 600, 86400):
        assert tower.motion_states(cfg, now=now + offset)["moving"] == 0

    # one row -> exactly one character moves, and only inside the window
    _emit(cfg, seat, "run_started", ts=now)
    assert tower.motion_states(cfg, now=now)["moving"] == 1
    assert tower.motion_states(cfg, now=now + tower.ACTIVE_WINDOW_S)["moving"] == 1
    assert tower.motion_states(cfg, now=now + tower.ACTIVE_WINDOW_S + 0.1)["moving"] == 0


def test_a_seat_with_no_events_is_idle_not_working(tmp_path, monkeypatch):
    from voicebridge.office import tower

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _office(cfg)
    out = tower.state(cfg)
    assert out["characters"], "the org has seats"
    assert all(c["animation"] == "idle" for c in out["characters"])
    assert all(c["last_event"] is None for c in out["characters"])
    assert all(c["stalled"] is False for c in out["characters"])


def test_an_unknown_event_kind_still_shows_something(tmp_path, monkeypatch):
    """A row that exists must be visible; only a *missing* row means nothing."""
    from voicebridge.office import store, tower

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _office(cfg)
    now = store.now()
    _emit(cfg, ids["seats"][0], "some_future_capability", ts=now)
    out = tower.state(cfg, now=now)
    me = [c for c in out["characters"] if c["agent_id"] == ids["seats"][0]][0]
    assert me["animation"] == "working"
    assert me["last_event"]["kind"] == "some_future_capability"


# =========================================================================
# The kill-mid-turn proof: a dead turn looks stalled, not busy
# =========================================================================
def test_a_killed_turn_stops_animating_and_reports_itself_stalled(
        tmp_path, monkeypatch):
    from voicebridge.office import store, tower

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _office(cfg)
    seat = ids["seats"][0]
    now = store.now()

    # the turn starts, and then the session dies: no settle event follows
    _emit(cfg, seat, "run_started", ts=now)
    me = [c for c in tower.state(cfg, now=now)["characters"]
          if c["agent_id"] == seat][0]
    assert me["animation"] == "working" and me["stalled"] is False

    later = tower.state(cfg, now=now + tower.ACTIVE_WINDOW_S + 1)
    me = [c for c in later["characters"] if c["agent_id"] == seat][0]
    # it stopped animating — the tower never shows work that is not happening
    assert me["animation"] == "idle"
    # ...and the dead turn is still a visible FACT, not a silent disappearance
    assert me["stalled"] is True


def test_a_settled_run_is_not_reported_as_stalled(tmp_path, monkeypatch):
    from voicebridge.office import store, tower

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _office(cfg)
    seat = ids["seats"][0]
    now = store.now()
    _emit(cfg, seat, "run_started", ts=now)
    _emit(cfg, seat, "run_settled", ts=now + 1)

    out = tower.state(cfg, now=now + 3600)
    me = [c for c in out["characters"] if c["agent_id"] == seat][0]
    assert me["animation"] == "idle"
    assert me["stalled"] is False, "a settled run is finished, not stalled"
    assert me["last_event"]["kind"] == "run_settled"


def test_the_newest_event_wins(tmp_path, monkeypatch):
    from voicebridge.office import tower

    rows = [{"agent_id": 7, "kind": "skill_used", "ts": 100.0},
            {"agent_id": 7, "kind": "task_done", "ts": 130.0},
            {"agent_id": 7, "kind": "skill_used", "ts": 110.0}]
    out = tower.activity_from_events(rows, now=131.0)
    assert out[7]["kind"] == "task_done"
    assert out[7]["animation"] == "done"


# =========================================================================
# The HUD matches the CLI to the cent
# =========================================================================
def test_the_hud_matches_office_pnl_to_the_cent(tmp_path, monkeypatch):
    from voicebridge.office import ledger as L, tower

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _office(cfg)
    L.record(cfg, "revenue", 12_345, agent_id=ids["director"],
             department_id=ids["department"])
    L.record(cfg, "inference", 2_345, agent_id=ids["director"],
             department_id=ids["department"])
    L.record(cfg, "inference", 999, agent_id=ids["director"],
             department_id=ids["department"], unmetered=True)

    cli = L.pnl(cfg, scope="org")
    h = tower.hud(cfg)["headcount"], tower.hud(cfg)
    head, h = h
    assert h["revenue_cents"] == cli["revenue_cents"] == 12_345
    assert h["cost_cents"] == cli["cost_cents"] == 2_345   # unmetered excluded (D16)
    assert h["net_cents"] == cli["net_cents"] == 10_000
    assert head == len(ids["seats"]) + 2          # CEO + director + the seats

    # the per-floor rows add back up to the org, so the HUD cannot disagree
    # with itself
    assert sum(f["revenue_cents"] for f in h["floors"]) == 12_345
    assert sum(f["cost_cents"] for f in h["floors"]) == 2_345


def test_the_hud_says_unknown_rather_than_infinity_when_nothing_burns(
        tmp_path, monkeypatch):
    from voicebridge.office import ledger as L, tower

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _office(cfg)
    L.record(cfg, "revenue", 50_000, agent_id=ids["director"],
             department_id=ids["department"])
    h = tower.hud(cfg)
    assert h["burn_cents_per_day"] == 0.0
    # a number the ledger cannot support is not a number
    assert h["runway_days"] is None and h["runway_is_derived"] is False


# =========================================================================
# The empty state: never a demo office
# =========================================================================
def test_an_org_with_no_rows_renders_empty_never_a_demo_office(
        tmp_path, monkeypatch):
    from voicebridge.office import tower

    cfg = _cfg(tmp_path, monkeypatch)
    out = tower.state(cfg)
    assert out["empty"] is True
    assert out["characters"] == []
    assert out["floorplan"]["floors"] == []
    assert out["hud"]["headcount"] == 0
    assert out["hud"]["revenue_cents"] == 0
    assert out["hud"]["runway_days"] is None
    assert "has not started" in out["empty_reason"]


def test_one_seat_is_an_org_not_an_empty_state(tmp_path, monkeypatch):
    from voicebridge.office import org as O, tower

    cfg = _cfg(tmp_path, monkeypatch)
    O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    out = tower.state(cfg)
    assert out["empty"] is False
    assert len(out["characters"]) == 1
    # no room was created, so the seat is unplaced rather than misplaced
    assert out["characters"][0]["seated"] is False
    assert out["characters"][0]["position"]["floor"] is None


# =========================================================================
# Nobody is silently missing from the tower
# =========================================================================
def test_a_seat_with_no_room_is_still_reported_not_dropped(tmp_path, monkeypatch):
    """An unseated agent is surfaced, because a tower that omits the CEO
    because the executive floor has no rooms lies by omission."""
    from voicebridge.office import org as O, tower

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _office(cfg)
    # the CEO has no room anywhere: `hire` never places anyone
    out = tower.state(cfg)
    slugs = {c["slug"] for c in out["characters"]}
    assert "ceo" in slugs, "the CEO vanished from the tower"
    assert len(out["characters"]) == out["hud"]["headcount"]

    by_slug = {c["slug"]: c for c in out["characters"]}
    assert by_slug["ceo"]["seated"] is False
    assert by_slug["ceo"]["position"]["floor"] is None
    assert by_slug["mgr"]["seated"] is True
    # and the unseated ones are not drawn at a fabricated desk
    placed = [c for c in out["characters"] if c["seated"]]
    assert all(c["position"]["x"] is not None for c in placed)
    assert all(c["position"]["x"] is None
               for c in out["characters"] if not c["seated"])


def test_the_plugin_renders_the_unseated_rather_than_dropping_them():
    """The renderer's half of the same rule."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "frontend" / "qubit-app"
           / "plugins" / "qubit-office" / "qubit-office.js").read_text()
    assert "unseated" in src
    assert "Not at a desk" in src
    assert "!c.seated" in src


# =========================================================================
# C4: the tower reads
# =========================================================================
def test_reading_the_tower_writes_nothing(tmp_path, monkeypatch):
    from voicebridge.office import ledger as L, store, tower

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _office(cfg)
    L.record(cfg, "revenue", 1_000, agent_id=ids["director"],
             department_id=ids["department"])

    def snapshot():
        conn = store.connect(cfg)
        try:
            return {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
                    for t in ("office_ledger", "office_event", "office_agent",
                              "office_floor", "office_room", "office_department")}
        finally:
            conn.close()

    before = snapshot()
    for _ in range(5):
        tower.state(cfg)
        tower.hud(cfg)
        tower.floorplan(cfg)
        tower.motion_states(cfg)
    assert snapshot() == before, "the tower wrote org state (C4)"


def test_the_payload_tells_the_renderer_the_rule(tmp_path, monkeypatch):
    """A plugin that animates from a timer is not misreading the payload."""
    from voicebridge.office import tower

    cfg = _cfg(tmp_path, monkeypatch)
    _office(cfg)
    out = tower.state(cfg)
    assert out["read_only"] is True
    assert "office_event rows only" in out["motion_rule"]
    assert out["active_window_s"] > 0


def test_the_floorplan_is_deterministic_and_seats_agents_in_their_own_room(
        tmp_path, monkeypatch):
    from voicebridge.office import tower

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _office(cfg)
    plan = tower.floorplan(cfg)
    assert plan["schema"] == tower.FLOORPLAN_SCHEMA
    assert plan["floors"], "a floor was created, so the plan has one"
    room = plan["floors"][0]["rooms"][0]
    assert room["slug"] == "desks"
    placed = {s["agent_id"] for s in room["seats"]}
    assert set(ids["seats"]) <= placed
    # stable across calls, and every slot distinct
    assert plan == tower.floorplan(cfg)
    coords = [(s["x"], s["y"]) for s in room["seats"]]
    assert len(coords) == len(set(coords)), "two agents share a desk"


# ------------------------------------------------------- the labelled preview
# A preview is how the page stays explorable with no agents running. The design
# rule it must not break: an empty tower still reports itself empty by default,
# and a preview is never mixed into live rows.
def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_an_empty_tower_is_still_empty_by_default(tmp_path, monkeypatch):
    """The preview must be opt-in, or an unstarted office stops saying so."""
    from voicebridge.office import tower as TWR

    cfg = _cfg(tmp_path, monkeypatch)
    out = TWR.state(cfg)
    assert out["empty"] is True
    assert out["preview"] is False
    assert out["characters"] == []
    assert "preview_legend" not in out


def test_a_requested_preview_is_labelled_and_never_claims_real_money(
        tmp_path, monkeypatch):
    from voicebridge.office import tower as TWR

    cfg = _cfg(tmp_path, monkeypatch)
    out = TWR.state(cfg, preview=True)
    assert out["preview"] is True
    assert "PREVIEW" in out["preview_legend"]
    assert "not live data" in out["preview_legend"]
    assert out["characters"], "a preview with no characters draws nothing"
    # preview seats are negative ids, so they can never collide with a real seat
    assert all(c["agent_id"] < 0 for c in out["characters"])
    # and the HUD is not invented: the org is still empty and still says so
    assert out["empty"] is True
    assert out["hud"]["revenue_cents"] == 0


def test_a_preview_never_overrides_a_live_org(tmp_path, monkeypatch):
    """Live data wins. A preview attached to real rows is the lie D14 forbids."""
    from voicebridge.office import org as O, store, tower as TWR

    cfg = _cfg(tmp_path, monkeypatch)
    O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    conn = store.connect(cfg)
    try:
        store.migrate(conn)
        n = conn.execute("SELECT COUNT(*) n FROM office_agent").fetchone()["n"]
    finally:
        conn.close()
    assert n == 1

    out = TWR.state(cfg, preview=True)
    assert out["preview"] is False, "a live org was replaced by a drawing"
    assert out["empty"] is False
    assert [c["slug"] for c in out["characters"]] == ["ceo"]


def test_the_preview_characters_are_deterministic(tmp_path, monkeypatch):
    """Two reloads must draw the same office, or it shimmers between polls."""
    from voicebridge.office import tower as TWR

    cfg = _cfg(tmp_path, monkeypatch)
    a = TWR.state(cfg, preview=True)
    b = TWR.state(cfg, preview=True)
    assert [(c["slug"], c["position"]["x"]) for c in a["characters"]] == \
           [(c["slug"], c["position"]["x"]) for c in b["characters"]]


# ------------------------------------------------- the walk is clock-driven
def test_no_event_means_nobody_is_walking(tmp_path, monkeypatch):
    """C7's core claim: advance the clock without new events and everyone stops."""
    from voicebridge.office import tower as TWR

    cfg = _cfg(tmp_path, monkeypatch)
    out = TWR.state(cfg, preview=True)
    assert all(c["walking"] is False for c in out["characters"])
    assert all(c["animation"] == "idle" for c in out["characters"])
