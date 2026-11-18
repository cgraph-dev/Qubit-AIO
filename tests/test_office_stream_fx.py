"""Office stream FX projection (P7-04 wired to the real office_event log).

The stream's `fx` list is a PRESENTATION projection of the tower's own
`activity` rows. These tests pin the two things that could make it a lie:
it must never invent an effect (unknown kinds/empty rows produce nothing), and
it must be bounded, deterministic and off-switchable.
"""
from __future__ import annotations

import json

from voicebridge import office_stream as S
from voicebridge.office import fx as FX
from voicebridge.office.fx import EVENT_RULES


def tower(activity=None):
    return {
        "now": 1_700_000_000.0,
        "floorplan": {"floors": []},
        "characters": [],
        "hud": {},
        "activity": activity if activity is not None else [
            # newest first, exactly as tower.activity() returns it
            {"id": 4, "kind": "run_failed", "agent_id": 8, "ts": 99.0},
            {"id": 3, "kind": "mystery", "agent_id": 7, "ts": 98.0},
            {"id": 2, "kind": "run_settled", "agent_id": 7, "ts": 97.0},
            {"id": 1, "kind": "run_started", "agent_id": 7, "ts": 96.0},
        ],
    }


def test_fx_is_projected_from_real_activity_rows_chronologically():
    snap = S.to_snapshot(tower(), 1, 1_700_000_000.0)
    fx = snap["fx"]
    assert [e["event_type"] for e in fx] == [
        "office.run_started", "office.run_settled", "office.run_failed"]
    assert [e["agent"] for e in fx] == ["7", "7", "8"]
    assert fx[1]["event_type"] == "office.run_settled"
    assert fx[1]["animation"] == "celebrate"
    assert fx[1]["essential"] is True
    # the unknown kind produced NO effect
    assert all(e["event_type"] != "office.mystery" for e in fx)


def test_fx_is_bounded_to_the_newest_rows():
    # tower.activity() is newest-first; row 59 is the newest.
    rows = [{"id": i, "kind": "tool_call", "agent_id": 1, "ts": float(i)}
            for i in range(59, 0, -1)]
    snap = S.to_snapshot(tower(rows), 1, 0.0)
    assert len(snap["fx"]) == S.FX_SNAPSHOT_LIMIT
    assert [e["event_id"] for e in snap["fx"]] == [
        str(i) for i in range(60 - S.FX_SNAPSHOT_LIMIT, 60)]


def test_fx_can_be_switched_off_without_changing_the_rest():
    on = S.to_snapshot(tower(), 1, 0.0)
    off = S.to_snapshot(tower(), 1, 0.0, fx_mode="off")
    assert on["fx"] and off["fx"] == []
    assert on["rooms"] == off["rooms"] and on["agents"] == off["agents"]


def test_fx_is_deterministic_and_does_not_mutate_the_tower():
    t = tower()
    before = json.dumps(t, sort_keys=True)
    a = S.to_snapshot(t, 1, 0.0)["fx"]
    b = S.to_snapshot(t, 1, 0.0)["fx"]
    assert a == b
    assert json.dumps(t, sort_keys=True) == before


def test_an_empty_or_malformed_feed_is_an_empty_fx_list():
    assert S.to_snapshot(tower([]), 1, 0.0)["fx"] == []
    t = tower([None, "x", {"kind": "run_settled"}, {"id": 9}])
    assert S.to_snapshot(t, 1, 0.0)["fx"] == []


def test_fx_mode_resolution_defaults_on_and_honours_config_and_env(monkeypatch):
    monkeypatch.delenv("QUBIT_OFFICE_FX", raising=False)
    assert S.fx_mode_for({}) == "sakuga"
    assert S.fx_mode_for({"office": {"fx": "off"}}) == "off"
    assert S.fx_mode_for({"office": {"fx": "sakuga"}}) == "sakuga"
    assert S.fx_mode_for({"office": {"fx": "nonsense"}}) == "sakuga"

    class Cfg:
        def get(self, *path, default=None):
            return "off" if path == ("office", "fx") else default

    assert S.fx_mode_for(Cfg()) == "off"
    monkeypatch.setenv("QUBIT_OFFICE_FX", "sakuga")
    assert S.fx_mode_for(Cfg()) == "sakuga"


def test_reduced_motion_drops_nonessential_and_uses_the_still_cue():
    rows = [
        {"id": 1, "kind": "tool_call", "agent_id": 1},
        {"id": 2, "kind": "run_settled", "agent_id": 1},
        {"id": 3, "kind": "consent_denied", "agent_id": 2},
    ]
    mapped = FX.map_office_events(rows, reduced_motion=True)
    assert [e.event_type for e in mapped] == [
        "office.run_settled", "office.consent_denied"]
    assert mapped[0].animation == mapped[0].still
    assert mapped[0].particles is None
    assert all(e.reduced_motion for e in mapped)


def test_fx_carries_the_towers_real_room_for_the_room_centre_fallback():
    t = tower([{"id": 1, "kind": "run_settled", "agent_id": 7}])
    t["characters"] = [{
        "agent_id": 7,
        "position": {"floor": 2, "room_id": 3},
    }]
    snap = S.to_snapshot(t, 1, 0.0)
    assert snap["fx"][0]["room"] == "f2r3"
    assert snap["fx"][0]["agent"] == "7"


def test_business_os_events_are_projected_beside_the_office_feed():
    # A committed BOS task event is presentation input, never new business truth.
    snap = S.to_snapshot(
        tower([]), 1, 1_700_000_000.0,
        business_events=[{
            "event_id": "evt_task_created",
            "type": "task.created",
            "payload": {"agent_id": "7", "room_id": "f2r3"},
            "occurred_at": "2026-11-18T10:00:00Z",
        }],
    )
    assert [e["event_type"] for e in snap["fx"]] == ["task.created"]
    assert snap["fx"][0]["animation"] == "walk-to-desk"
    assert snap["fx"][0]["agent"] == "7"
    assert snap["fx"][0]["room"] == "f2r3"


def test_business_events_are_ordered_with_the_office_feed_and_bounded():
    office = [{"id": 2, "kind": "run_settled", "agent_id": 7, "ts": 100.0}]
    business = [
        {"event_id": "b3", "type": "artifact.created", "payload": {},
         "occurred_at": "1970-01-01T00:02:00Z"},
        {"event_id": "b2", "type": "task.claimed", "payload": {},
         "occurred_at": "1970-01-01T00:01:00Z"},
    ]
    snap = S.to_snapshot(tower(office), 1, 0.0, business_events=business)
    assert [e["event_type"] for e in snap["fx"]] == [
        "task.claimed", "office.run_settled", "artifact.created"]


def test_unknown_business_event_is_a_no_op():
    snap = S.to_snapshot(
        tower([]), 1, 0.0,
        business_events=[{
            "event_id": "evt_unknown", "type": "not.a.rule", "payload": {},
        }],
    )
    assert snap["fx"] == []


def test_office_fx_aliases_keep_one_rule_table():
    assert FX.OFFICE_EVENT_RULES["task_created"] == EVENT_RULES["task.created"]
    assert FX.OFFICE_EVENT_RULES["finance_posting"] == EVENT_RULES["finance.posting.posted"]


def test_business_fx_events_reads_nothing_without_a_store(tmp_path):
    class Cfg:
        state_dir = lambda self: str(tmp_path)  # noqa: E731

    assert S.business_fx_events(Cfg()) == []
    assert not (tmp_path / "business" / "business.db").exists()
