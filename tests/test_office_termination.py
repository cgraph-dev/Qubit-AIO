"""M2 — survival, the daily tick, reports, and termination (D24/D34/D38).

The milestone's proof: force a seat's runway below the floor and the tick
escalates it to **its manager** — found via `supervisor_id` — instead of letting
it die quietly; and termination is never deletion.

Hermetic: tmp XDG state only; no network, no provider call.
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


def _chain(cfg):
    from voicebridge.office import org as O

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    d = O.hire(cfg, "dir", seat_type="director_portfolio", supervisor_id=ceo,
               confirmed=True)["id"]
    dept = O.department_create(cfg, "shop", director_id=d,
                               kill_criteria="two quarters negative net",
                               confirmed=True)["id"]
    mgr = O.hire(cfg, "mgr", seat_type="manager", supervisor_id=d,
                 department_id=dept, confirmed=True)["id"]
    wk = O.hire(cfg, "wk", seat_type="worker", supervisor_id=mgr,
                department_id=dept, confirmed=True)["id"]
    return {"ceo": ceo, "director": d, "department": dept, "manager": mgr,
            "worker": wk}


def _burn(cfg, agent_id, *, cents, days=14.0):
    """Book an inference spend aged across the burn window."""
    from voicebridge.office import ledger, store

    now = store.now()
    ledger.record(cfg, "inference", cents, agent_id=agent_id,
                  ts=now - days * 86400.0 / 2)


# --- a seat that is not spending has no runway limit, and we say that rather
# --- than claiming "infinite days"
def test_a_seat_that_is_not_spending_has_no_runway_limit(tmp_path, monkeypatch):
    from voicebridge.office import ledger, survival

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    ledger.record(cfg, "revenue", 5_000, agent_id=ids["worker"])
    info = survival.runway_days(cfg, ids["worker"])
    assert info["days"] is None and info["burn_cents_per_day"] == 0.0
    assert info["at_risk"] is False
    assert "not spending" in info["meaning"]


# --- the milestone's proof: below the floor, the MANAGER is told
def test_a_seat_below_the_floor_escalates_to_its_own_manager(tmp_path,
                                                             monkeypatch):
    from voicebridge.office import ledger, survival

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    ledger.record(cfg, "revenue", 2_000, agent_id=ids["worker"])
    _burn(cfg, ids["worker"], cents=1_400)          # 100c/day
    info = survival.runway_days(cfg, ids["worker"])
    assert info["days"] == 6.0 and info["at_risk"] is True

    tick = survival.daily_tick(cfg)
    raised = [r for r in tick["raised"] if r["agent_id"] == ids["worker"]]
    assert len(raised) == 1
    # to its MANAGER — found through supervisor_id, not by convention
    assert raised[0]["supervisor_id"] == ids["manager"]
    assert raised[0]["confirmation_id"]

    # and it is alive, not quietly dead
    from voicebridge.office import survival as S
    roster = {s["id"]: s for s in S.roster(cfg)["seats"]}
    assert roster[ids["worker"]]["status"] != "terminated"


# --- told once, not once a day: a manager told five times has been told nothing
def test_the_tick_raises_once_not_every_day(tmp_path, monkeypatch):
    from voicebridge.office import ledger, survival

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    ledger.record(cfg, "revenue", 2_000, agent_id=ids["worker"])
    _burn(cfg, ids["worker"], cents=1_400)

    first = survival.daily_tick(cfg)
    assert len([r for r in first["raised"] if r["agent_id"] == ids["worker"]]) == 1

    second = survival.daily_tick(cfg)
    assert not [r for r in second["raised"] if r["agent_id"] == ids["worker"]]
    skip = [s for s in second["skipped"] if s["agent_id"] == ids["worker"]]
    assert skip and "already owns this" in skip[0]["why"]


# --- the CEO has no manager, so the tick says so instead of escalating nowhere
def test_the_tick_skips_the_ceo_with_a_typed_reason(tmp_path, monkeypatch):
    from voicebridge.office import ledger, survival

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    ledger.record(cfg, "revenue", 1_000, agent_id=ids["ceo"])
    _burn(cfg, ids["ceo"], cents=1_400)

    tick = survival.daily_tick(cfg)
    skip = [s for s in tick["skipped"] if s["agent_id"] == ids["ceo"]]
    assert skip and "owner, not an agent" in skip[0]["why"]
    assert not [r for r in tick["raised"] if r["agent_id"] == ids["ceo"]]


# --- reflection is a paid turn: with none configured, that is stated, not faked
def test_the_tick_says_reflection_is_not_configured_rather_than_pretending(
        tmp_path, monkeypatch):
    from voicebridge.office import survival

    cfg = _cfg(tmp_path, monkeypatch)
    _chain(cfg)
    tick = survival.daily_tick(cfg)
    assert tick["reflection"]["state"] == "NOT_CONFIGURED"
    assert tick["reflection"]["reflected"] == 0
    # the survival pass still ran, and is complete
    assert tick["state"] == "OK" and tick["seats"] == 4   # dept is not a seat

    # with a reflector injected it is used, and a failing seat does not stop it
    def boom(cfg_, agent_id, key):
        raise RuntimeError("model exploded")

    tick2 = survival.daily_tick(cfg, reflector=boom)
    assert tick2["reflection"]["state"] == "OK"
    assert tick2["reflection"]["reflected"] == 4
    assert all(r["state"] == "ERROR" for r in tick2["reflection"]["results"])


# --- a report whose numbers contradict the ledger is kept WITH the contradiction
def test_a_report_whose_numbers_are_wrong_keeps_the_disagreement(tmp_path,
                                                                monkeypatch):
    from voicebridge.office import ledger, survival

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    ledger.record(cfg, "revenue", 4_000, agent_id=ids["worker"])

    honest = survival.file_report(
        cfg, agent_id=ids["worker"], kind="weekly", period="2026-10",
        body="b", recommendation="keep going", reasoning="r",
        claims={"agent:%d.net_cents" % ids["worker"]: 4_000}, confirmed=True)
    assert honest["trustworthy"] is True and honest["ledger_diff"] == "clean"

    lying = survival.file_report(
        cfg, agent_id=ids["worker"], kind="weekly", period="2026-10",
        body="b", recommendation="hire ten", reasoning="we are rich",
        claims={"agent:%d.net_cents" % ids["worker"]: 400_000}, confirmed=True)
    # stored — a report that can be refused is one an agent rewrites until it
    # passes — but the disagreement travels with it
    assert lying["trustworthy"] is False
    assert "400000" in lying["ledger_diff"] and "4000" in lying["ledger_diff"]
    row = survival.reports(cfg)["reports"][0]
    assert row["id"] == lying["id"] and row["ledger_diff"] != "clean"


# --- an uncheckable claim is reported as unchecked, never as verified
def test_a_claim_the_ledger_cannot_check_is_not_reported_as_verified(
        tmp_path, monkeypatch):
    from voicebridge.office import survival

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    out = survival.file_report(
        cfg, agent_id=ids["worker"], kind="weekly", period="2026-10",
        claims={"vibes": 10, "agent:%d.nonsense" % ids["worker"]: 1},
        confirmed=True)
    assert out["trustworthy"] is True          # nothing contradicted
    assert len(out["unchecked"]) == 2          # ...but nothing was verified


# --- termination is not deletion
def test_termination_frees_the_seat_and_keeps_every_ledger_row(tmp_path,
                                                               monkeypatch):
    from voicebridge.office import ledger, survival

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    ledger.record(cfg, "revenue", 5_000, agent_id=ids["worker"])
    ledger.record(cfg, "inference", 1_200, agent_id=ids["worker"])
    before = ledger.pnl(cfg, scope="agent", id=ids["worker"])
    assert before["net_cents"] == 3_800

    out = survival.terminate(cfg, ids["worker"], reason="burned out",
                             by_agent=ids["manager"], confirmed=True)
    assert out["status"] == "terminated"

    after = ledger.pnl(cfg, scope="agent", id=ids["worker"])
    assert after["net_cents"] == 3_800          # the loss is still the org's
    assert after["cost_cents"] == 1_200
    # gone from the live roster, but still on the record when asked for
    assert ids["worker"] not in [s["id"] for s in survival.roster(cfg)["seats"]]
    all_seats = survival.roster(cfg, include_terminated=True)["seats"]
    gone = [s for s in all_seats if s["id"] == ids["worker"]][0]
    assert gone["status"] == "terminated" and gone["terminated_at"]


# --- a seat that still supervises live seats cannot be terminated
def test_a_manager_with_live_reports_cannot_be_terminated(tmp_path,
                                                          monkeypatch):
    from voicebridge.office import survival
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    with pytest.raises(OfficeError) as exc:
        survival.terminate(cfg, ids["manager"], reason="restructure",
                           confirmed=True)
    assert exc.value.code == "OFFICE_BAD_STATE"
    assert "hole in it" in exc.value.detail


# --- and a termination needs a reason that is a reason
def test_termination_needs_a_reason_and_a_confirmation(tmp_path, monkeypatch):
    from voicebridge.office import survival, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    with pytest.raises(OfficeError) as exc:
        survival.terminate(cfg, ids["worker"], reason="", confirmed=True)
    assert exc.value.code == "OFFICE_BAD_INPUT"
    with pytest.raises(OfficeError) as exc:
        survival.terminate(cfg, ids["worker"], reason="why not")
    assert exc.value.code == store.CONFIRM_CODE


# --- a terminated seat cannot escalate (it is not a seat any more)
def test_a_terminated_seat_cannot_escalate(tmp_path, monkeypatch):
    from voicebridge.office import escalation as E, survival
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    survival.terminate(cfg, ids["worker"], reason="done", confirmed=True)
    with pytest.raises(OfficeError) as exc:
        E.raise_to_supervisor(cfg, agent_id=ids["worker"], kind="blocked",
                              summary="x")
    assert exc.value.code == "OFFICE_BAD_STATE"


# --- the org facts a CEO writes its weekly review from are ledger-derived
def test_org_facts_are_derived_from_the_ledger_not_stored(tmp_path, monkeypatch):
    from voicebridge.office import ledger, survival

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _chain(cfg)
    ledger.record(cfg, "revenue", 9_000, agent_id=ids["worker"])
    ledger.record(cfg, "inference", 2_000, agent_id=ids["worker"])

    facts = survival.org_facts(cfg)
    assert facts["org"]["revenue_cents"] == 9_000
    assert facts["org"]["cost_cents"] == 2_000
    assert facts["org"]["net_cents"] == 7_000
    dept = [d for d in facts["departments"]
            if d["department_id"] == ids["department"]][0]
    assert dept["cost_cents"] == 2_000     # resolved from the seat, not the call
