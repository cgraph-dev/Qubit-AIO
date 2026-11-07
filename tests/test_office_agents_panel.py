"""The Office agents panel: real seats, real divisions, no invented fields.

WHAT THIS PINS
--------------
The panel the Office front-end renders lists every SEAT the building plan
places, grouped by the division recorded on the seat. Three things can go wrong
here and each is asserted separately:

  1. **The route is unreachable or shadowed.** ``/api/office/habblaud/agents/
     panel`` must resolve to its own handler, not be swallowed by the
     ``agents/<id>`` read (which would answer "no such agent" with a 404), and
     it must be reachable from the embedded office document's literal
     ``/api/agents/panel`` front door.
  2. **The counts drift from the module.** Every number the panel serves is
     checked against ``agency_org`` itself — the roster on disk and the plan —
     not against a literal written from memory.
  3. **A field is invented.** Every key on a seat row must come from the plan's
     seat record; a key the plan does not have (a persona "title", a "level", a
     "bio") must not exist.
"""
from __future__ import annotations

import pytest

from voicebridge import qubit_compat as HC
from voicebridge.office import agency_org as A
from voicebridge.office import agent_panel as PANEL

pytestmark = pytest.mark.unit

ROUTE = "/api/office/habblaud/agents/panel"


# ======================================================================
# 1 — the route resolves, and resolves to THIS handler
# ======================================================================
def test_the_route_is_registered_before_the_agent_id_read():
    """``agents/panel`` must not be read as an agent id."""
    route, _params = HC.match("GET", ROUTE)
    assert route is not None, f"no compat route matches {ROUTE}"
    assert route["handler"] == "h_office_habblaud_agents_panel", (
        f"{ROUTE} is answered by {route['handler']!r}; the agents/<id> read "
        f"has swallowed it")
    # The by-id read still resolves a real id.
    by_id, params = HC.match("GET", "/api/office/habblaud/agents/7")
    assert by_id is not None and params.get("id") == "7"


def test_the_route_answers_on_the_real_dispatch():
    code, out = HC.dispatch(None, "GET", ROUTE)
    assert code == 200, (code, out.get("detail") if isinstance(out, dict) else out)
    assert out["ok"] is True
    assert out["schema"] == PANEL.SCHEMA


def test_the_route_is_read_only():
    """No write verb reaches the PANEL handler.

    The office family binds a catch-all POST that answers a typed refusal; what
    matters here is that no write verb is answered by the panel handler (which
    would imply the panel is writable).
    """
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        route, _ = HC.match(method, ROUTE)
        assert route is None or route["handler"] != "h_office_habblaud_agents_panel", (
            f"{method} {ROUTE} is answered by the panel handler")


# ======================================================================
# 2 — every number is the module's, measured, not remembered
# ======================================================================
def test_counts_are_the_modules_own_numbers():
    roster = A.load()
    plan = A.plan_building()
    counts = PANEL.panel()["counts"]

    assert counts["headcount"] == len(plan["seats"]), (
        "the panel's headcount is not the plan's seat count")
    assert counts["headcount"] == len(roster["personas"]), (
        "the panel lost or invented a persona: one persona is one seat")
    assert counts["roster_divisions"] == len(roster["divisions"])
    assert counts["rooms"] == plan["rooms_needed"]
    assert counts["floors"] == len(plan["floors"])
    # The seat-type tally is the plan's, keyed identically.
    expected_types = {t: sum(1 for s in plan["seats"] if s["seat_type"] == t)
                      for t in {s["seat_type"] for s in plan["seats"]}}
    assert counts["seat_types"] == dict(sorted(expected_types.items()))


def test_the_per_division_tally_is_the_seats_own_division_field():
    plan = A.plan_building()
    counts = PANEL.panel()["counts"]
    expected: dict[str, int] = {}
    for seat in plan["seats"]:
        expected[seat["division"]] = expected.get(seat["division"], 0) + 1
    assert counts["per_division"] == dict(sorted(expected.items())), (
        "the panel's per-division counts do not match the seats' own "
        "division field")


def test_the_empty_slug_bucket_is_the_boardroom_and_is_not_a_division():
    """The executive chain sits above every division: its seats carry an empty
    division slug. That bucket must be shown, named as the boardroom, and must
    not be counted as a nineteenth division."""
    out = PANEL.panel()
    groups = out["divisions"]
    board = [g for g in groups if g["slug"] == ""]
    assert len(board) == 1, "the boardroom bucket is missing or duplicated"
    assert board[0]["label"] == PANEL.BOARDROOM_LABEL
    assert board[0]["headcount"] == len(board[0]["seats"]) > 0
    assert out["counts"]["divisions"] == len([g for g in groups if g["slug"]])
    assert out["counts"]["divisions"] == out["counts"]["roster_divisions"]
    assert groups[-1]["slug"] == "", "the boardroom tier is not listed last"
    # And every division group IS a division the roster has.
    roster = set(A.load()["divisions"])
    assert {g["slug"] for g in groups if g["slug"]} == roster


def test_every_seat_is_grouped_exactly_once():
    out = PANEL.panel()
    grouped = [s["slug"] for g in out["divisions"] for s in g["seats"]]
    assert len(grouped) == len(out["seats"]) == out["counts"]["headcount"]
    assert sorted(grouped) == sorted(s["slug"] for s in out["seats"])


# ======================================================================
# 3 — no invented field: the row's keys are the plan's keys
# ======================================================================
def test_a_seat_row_carries_only_fields_the_plan_has():
    plan = A.plan_building()
    # The plan's seat record, and the two names `agent_panel` re-keys the
    # persona emoji/colour under. `persona_slug` is the roster key for a
    # promoted seat (the plan suffixes `-N` onto the SEAT slug).
    plan_keys = set(plan["seats"][0])
    renamed = {"persona_emoji": "emoji", "persona_color": "color"}
    allowed = (plan_keys - set(renamed)) | set(renamed.values()) | {"persona_slug"}
    for row in PANEL.panel()["seats"]:
        extra = set(row) - allowed
        assert not extra, (
            f"seat {row.get('slug')!r} carries keys {sorted(extra)} that the "
            f"plan's seat record does not have")
        # The values are copied, not defaulted.
        source = next(s for s in plan["seats"] if s["slug"] == row["slug"])
        assert row["name"] == source["name"]
        assert row["seat_type"] == source["seat_type"]
        assert row["supervisor"] == source["supervisor"]
        assert row["room"] == source["room"]
        assert row["emoji"] == (source.get("persona_emoji") or "")
        assert row["color"] == (source.get("persona_color") or "")
        if source["slug"] == row["persona_slug"]:
            assert row["persona_slug"] == source["slug"]


def test_a_division_group_labels_come_from_the_roster():
    roster = A.load()["divisions"]
    for group in PANEL.panel()["divisions"]:
        if group["slug"] == "":
            continue
        assert group["label"] == roster[group["slug"]]["label"], (
            f"division {group['slug']!r} is labelled {group['label']!r}; the "
            f"roster says {roster[group['slug']]['label']!r}")
        assert group["kind"] == roster[group["slug"]]["kind"]
        assert group["headcount"] == len(group["seats"])


def test_the_persona_names_are_the_rosters_names():
    """A seat's name is the roster's name for its persona — including the
    promoted executive seats, whose SEAT slug carries a `-N` suffix."""
    roster = {p["slug"]: p["name"] for p in A.load()["personas"]}
    promoted = 0
    for row in PANEL.panel()["seats"]:
        assert row["persona_slug"] in roster, (
            f"seat {row['slug']!r} names persona {row['persona_slug']!r}, "
            f"which is not in the roster")
        assert row["name"] == roster[row["persona_slug"]], (
            f"seat {row['slug']!r} is named {row['name']!r}; the roster says "
            f"{roster[row['persona_slug']]!r}")
        if row["persona_slug"] != row["slug"]:
            promoted += 1
    assert promoted >= 1, (
        "no promoted seat is in the plan, so the -N slug handling is untested")


# ======================================================================
# 4 — the panel RENDERS, in a real DOM, against the real route
# ======================================================================
def test_the_panel_renders_every_seat_from_the_real_route():
    """THE END-TO-END PROOF: a real HTTP server, the real compat dispatch, the
    real React component, and the text a reader would see.

    ``tests/_probe_agents_panel_dom.py`` boots the handler on an ephemeral
    loopback port with the production token gate (a tokenless read is a 401, so
    the panel's own header is exercised), transpiles the panel with the repo's
    tsc, mounts it with real React over that server, and asserts 283 seat rows
    in 19 buckets with every persona name on screen.

    A probe defect (no toolchain, a compile error) is a FAILURE here, never a
    skip: this is the assertion the panel exists to satisfy.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    web = repo / "frontend" / "qubit-web"
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _office_toolchain import node_path  # noqa: E402

    if not node_path() or not (web / "node_modules" / "jsdom").is_dir():
        pytest.fail("no node/jsdom toolchain: the panel's DOM proof cannot run")
    run = subprocess.run(
        [sys.executable, str(repo / "tests" / "_probe_agents_panel_dom.py")],
        cwd=str(repo), text=True, capture_output=True, timeout=600,
        env={**os.environ, "PYTHONPATH": str(repo)})
    assert run.returncode == 0, (
        f"the panel DOM check failed\nstdout:\n{run.stdout[-4000:]}\n"
        f"stderr:\n{run.stderr[-2000:]}")
    out = run.stdout
    assert "ALL CHECKS PASS" in out, out[-3000:]
    assert "283 rows for 283 seats" in out, (
        f"the panel did not list every seat:\n{out[-3000:]}")
    assert "the tokenless request is refused (HTTP 401)" in out, (
        f"the probe did not exercise the session-token gate:\n{out[-2000:]}")
