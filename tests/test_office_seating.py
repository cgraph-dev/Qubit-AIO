"""seat-move and hire --room: the only supported way to put a seat in a room.

Why this file exists: a seat's `room_id` was settable ONLY at hire time, and the
CLI never exposed it, so an org whose seats were hired before its rooms existed
could never be drawn in the tower — the tower correctly reported `floors: 0` and
tray-listed every seat as "not at a desk". `seat_move` and `hire --room` close
that gap. Both shipped before they had coverage; this is that coverage.

The rules that matter: a placement is not a re-hire (the seat keeps its id, its
supervisor, its ledger history and its task trail); a room that does not exist is
refused rather than silently seating the agent nowhere; and a seat in a room is
counted in that room's department, because a seat sitting on one floor while
counted in another division is a P&L that does not match the floor plan.

Correct signatures (checked against the modules, not assumed):
``org.hire(cfg, slug, *, seat_type, department_id, room_id, supervisor_id, ...)``,
``survival.roster(cfg, *, status, include_terminated)``, and there is no
``confirmed`` parameter on ``ledger.record`` (the ledger is already append-only).

Hermetic: tmp XDG_* only; no network, no provider call, no live service.
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


def _building(cfg, *, floors=1):
    """A floor per number, each with one department and one room."""
    from voicebridge.office import org as O

    roles = ["executive", "operations", "intelligence"]
    kinds = ["meta", "product", "intelligence"]
    out = {"floors": [], "departments": [], "rooms": []}
    for n in range(1, floors + 1):
        f = O.floor_create(cfg, n, name=f"F{n}", role=roles[(n - 1) % 3],
                           confirmed=True)["id"]
        d = O.department_create(
            cfg, f"dept{n}", floor_id=f, division_kind=kinds[(n - 1) % 3],
            kill_criteria="closes if it produces nothing for 30 days",
            confirmed=True)["id"]
        r = O.room_create(cfg, d, f"room{n}", confirmed=True)["id"]
        out["floors"].append(f)
        out["departments"].append(d)
        out["rooms"].append(r)
    return out


def _seats(cfg):
    from voicebridge.office import survival as S

    return {s["id"]: s for s in
            S.roster(cfg, include_terminated=True)["seats"]}


# ============================================================ hire with a room
def test_hire_can_seat_a_seat_in_a_room(tmp_path, monkeypatch):
    """`hire` returns the new seat's id, so seating is asserted on the roster."""
    from voicebridge.office import org as O

    cfg = _cfg(tmp_path, monkeypatch)
    b = _building(cfg)
    out = O.hire(cfg, "ceo", seat_type="ceo", room_id=b["rooms"][0],
                 confirmed=True)
    assert set(out) >= {"state", "id", "slug"}
    row = _seats(cfg)[out["id"]]
    assert row["room_id"] == b["rooms"][0]
    # the department comes from the room, or the floor plan and the P&L disagree
    assert row["department_id"] == b["departments"][0]


def test_hire_refuses_a_room_that_does_not_exist(tmp_path, monkeypatch):
    """The model must not seat an agent nowhere and call it placed."""
    from voicebridge.office import org as O
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    b = _building(cfg)
    with pytest.raises(OfficeError) as exc:
        O.hire(cfg, "fresh-seat", seat_type="ceo", room_id=999, confirmed=True)
    assert exc.value.code == "OFFICE_NOT_FOUND"


def test_hire_refuses_a_duplicate_slug(tmp_path, monkeypatch):
    """A duplicate is refused as a duplicate.

    Note the seat type: a second CEO is refused by the one-CEO rule (D2) before
    the slug is ever examined, so a worker under the first CEO is what makes the
    slug collision the thing under test.
    """
    from voicebridge.office import org as O
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    b = _building(cfg)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", room_id=b["rooms"][0],
                 confirmed=True)["id"]
    ws = O.hire(cfg, "ws", seat_type="worker", supervisor_id=ceo,
                room_id=b["rooms"][0], confirmed=True)["id"]
    assert ws != ceo
    with pytest.raises(OfficeError) as dup:
        O.hire(cfg, "ws", seat_type="worker", supervisor_id=ceo,
               room_id=b["rooms"][0], confirmed=True)
    assert dup.value.code == "OFFICE_EXISTS"


def test_hire_refuses_a_room_from_another_department(tmp_path, monkeypatch):
    """A desk in one division while counted in another is a P&L that lies."""
    from voicebridge.office import org as O
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    b = _building(cfg, floors=2)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    with pytest.raises(OfficeError) as exc:
        O.hire(cfg, "ws", seat_type="worker", supervisor_id=ceo,
               department_id=b["departments"][0],      # dept 1
               room_id=b["rooms"][1],                  # a room in dept 2
               confirmed=True)
    assert "P&L that does not match the floor" in exc.value.detail


def test_seat_move_places_a_worker_and_takes_its_department(tmp_path,
                                                           monkeypatch):
    from voicebridge.office import org as O, survival as S

    cfg = _cfg(tmp_path, monkeypatch)
    b = _building(cfg)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    ws = O.hire(cfg, "ws", seat_type="worker", supervisor_id=ceo,
                confirmed=True)["id"]

    out = O.seat_move(cfg, ws, room_id=b["rooms"][0], confirmed=True)
    assert out["room_id"] == b["rooms"][0]
    assert out["department_id"] == b["departments"][0]

    row = _seats(cfg)[ws]
    assert row["room_id"] == b["rooms"][0]
    assert row["department_id"] == b["departments"][0]
    # a move does not touch the chain
    assert row["supervisor_id"] == ceo
    assert S.roster(cfg)["count"] == 2


def test_seat_move_keeps_the_seats_ledger_history(tmp_path, monkeypatch):
    """A placement is not a re-hire: what the seat already earned still stands."""
    from voicebridge.office import ledger, org as O

    cfg = _cfg(tmp_path, monkeypatch)
    b = _building(cfg)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    ws = O.hire(cfg, "ws", seat_type="worker", supervisor_id=ceo,
                confirmed=True)["id"]
    ledger.record(cfg, "revenue", 500, agent_id=ws)

    before = ledger.balance(cfg, ws)["balance_cents"]
    assert before == 500
    O.seat_move(cfg, ws, room_id=b["rooms"][0], confirmed=True)
    assert ledger.balance(cfg, ws)["balance_cents"] == before
    assert _seats(cfg)[ws]["slug"] == "ws"
    assert _seats(cfg)[ws]["id"] == ws


def test_seat_move_can_unseat_a_seat(tmp_path, monkeypatch):
    """Taking a seat off a desk is a real operation, not an error."""
    from voicebridge.office import org as O

    cfg = _cfg(tmp_path, monkeypatch)
    b = _building(cfg)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    O.seat_move(cfg, ceo, room_id=b["rooms"][0], confirmed=True)
    out = O.seat_move(cfg, ceo, room_id=None, confirmed=True)
    assert out["room_id"] is None
    assert _seats(cfg)[ceo]["room_id"] is None


def test_seat_move_refuses_an_unknown_room(tmp_path, monkeypatch):
    from voicebridge.office import org as O
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    _building(cfg)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    with pytest.raises(OfficeError) as exc:
        O.seat_move(cfg, ceo, room_id=4242, confirmed=True)
    assert "no room" in exc.value.detail


def test_seat_move_refuses_an_unknown_seat(tmp_path, monkeypatch):
    from voicebridge.office import org as O
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    b = _building(cfg)
    with pytest.raises(OfficeError) as exc:
        O.seat_move(cfg, 999, room_id=b["rooms"][0], confirmed=True)
    assert "no seat" in exc.value.detail


def test_seat_move_needs_a_confirmation(tmp_path, monkeypatch):
    from voicebridge.office import org as O, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    b = _building(cfg)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    with pytest.raises(OfficeError) as exc:
        O.seat_move(cfg, ceo, room_id=b["rooms"][0])
    assert exc.value.code == store.CONFIRM_CODE


# ========================================================== seated => drawn
def test_a_seated_agent_appears_on_the_floor_plan_with_coordinates(
        tmp_path, monkeypatch):
    """The point of all of it: a seated seat has a desk the tower can draw."""
    from voicebridge.office import org as O, tower as TWR

    cfg = _cfg(tmp_path, monkeypatch)
    b = _building(cfg)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    O.seat_move(cfg, ceo, room_id=b["rooms"][0], confirmed=True)

    plan = TWR.floorplan(cfg)
    room = plan["floors"][0]["rooms"][0]
    assert len(room["seats"]) == 1
    slot = room["seats"][0]
    assert slot["agent_id"] == ceo
    assert isinstance(slot["x"], int) and isinstance(slot["y"], int)

    chars = {c["agent_id"]: c for c in TWR.state(cfg)["characters"]}
    assert chars[ceo]["seated"] is True
    assert chars[ceo]["position"]["x"] == slot["x"]
    assert chars[ceo]["position"]["y"] == slot["y"]


def test_an_unseated_seat_is_reported_unseated_not_dropped(tmp_path,
                                                          monkeypatch):
    """A seat with no desk is still a seat; the tower says so rather than hiding it."""
    from voicebridge.office import org as O, tower as TWR

    cfg = _cfg(tmp_path, monkeypatch)
    _building(cfg)
    O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    chars = {c["slug"]: c for c in TWR.state(cfg)["characters"]}
    assert chars["ceo"]["seated"] is False
    assert chars["ceo"]["position"]["x"] is None


def test_desks_in_a_room_are_laid_out_in_a_grid_not_one_row(tmp_path,
                                                            monkeypatch):
    """Six seats in one room must occupy more than one row.

    A single row is not a room, it is a shelf — and in isometric projection every
    desk on that shelf overlaps the previous one, so the office drew three desks
    where it claimed six.
    """
    from voicebridge.office import org as O, tower as TWR

    cfg = _cfg(tmp_path, monkeypatch)
    b = _building(cfg)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    ws = [O.hire(cfg, f"w{i}", seat_type="worker", supervisor_id=ceo,
                 confirmed=True)["id"] for i in range(5)]
    for a in [ceo] + ws:
        O.seat_move(cfg, a, room_id=b["rooms"][0], confirmed=True)

    seats = TWR.floorplan(cfg)["floors"][0]["rooms"][0]["seats"]
    assert len(seats) == 6
    xs = sorted({s["x"] for s in seats})
    ys = sorted({s["y"] for s in seats})
    assert len(xs) >= 3, f"desks collapsed into too few columns: {xs}"
    assert len(ys) >= 2, f"every desk is on one row: {ys}"
    cells = {(s["x"], s["y"]) for s in seats}
    assert len(cells) == len(seats), f"desks overlap: {sorted(cells)}"

