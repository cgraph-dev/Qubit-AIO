"""M2 — envelopes (C31, D36/D37).

The contract's six proof bullets, in order. The envelope exists to stop one CEO
from being the org's throughput, and the level rule exists to stop review from
becoming mutual back-scratching — so the tests that matter are the two
*refusals*, not the happy path.

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


def _org(cfg):
    """CEO → one portfolio director → one department → one manager."""
    from voicebridge.office import org as O

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    d = O.hire(cfg, "dir-markets", seat_type="director_portfolio",
               supervisor_id=ceo, confirmed=True)["id"]
    dept = O.department_create(cfg, "storefronts", director_id=d,
                               kill_criteria="two quarters under 0 net; "
                                             "then the director proposes a kill",
                               confirmed=True)["id"]
    mgr = O.hire(cfg, "mgr-storefronts", seat_type="manager",
                 supervisor_id=d, department_id=dept, confirmed=True)["id"]
    return {"ceo": ceo, "director": d, "department": dept, "manager": mgr}


# 1 — spend inside the envelope: allowed, ledger row, NO request row
def test_spend_inside_the_envelope_writes_a_ledger_row_and_no_request(
        tmp_path, monkeypatch):
    from voicebridge.office import envelope as E, ledger

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    E.allocate(cfg, scope="org", scope_id=None, cents=100_000,
               by_agent=None, confirmed=True)
    E.allocate(cfg, scope="department", scope_id=ids["department"],
               cents=10_000, by_agent=ids["director"], confirmed=True)
    ledger.record(cfg, "revenue", 5_000, agent_id=ids["manager"])

    out = E.spend(cfg, agent_id=ids["manager"], cents=1_200)
    assert out["booked"] is True and out["within_envelope"] is True
    assert out["envelope"]["scope"] == "department"

    assert E.requests(cfg)["count"] == 0          # nothing had to be asked
    assert ledger.balance(cfg, ids["manager"])["balance_cents"] == 5_000 - 1_200


# 2 — spend above the envelope with no raised request: refused, and unbooked
def test_spend_above_the_envelope_is_refused_when_nothing_was_raised(
        tmp_path, monkeypatch):
    from voicebridge.office import envelope as E, ledger
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    E.allocate(cfg, scope="department", scope_id=ids["department"],
               cents=1_000, by_agent=ids["director"], confirmed=True)
    ledger.record(cfg, "revenue", 50_000, agent_id=ids["manager"])

    check = E.check_spend(cfg, agent_id=ids["manager"], cents=5_000)
    assert check["allowed"] is False and check["needs_raise"] is True
    assert "raise it to the level above" in check["reason"]

    with pytest.raises(OfficeError) as exc:
        E.spend(cfg, agent_id=ids["manager"], cents=5_000)
    assert exc.value.code == "OFFICE_ENVELOPE_EXCEEDED"

    # refused means nothing was written — not even a partial row
    assert ledger.pnl(cfg, scope="agent", id=ids["manager"])["cost_cents"] == 0


# 3 — raise above it: a request exists naming all four required things
def test_a_raise_must_named_amount_thesis_return_and_displacement(
        tmp_path, monkeypatch):
    from voicebridge.office import envelope as E
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    E.allocate(cfg, scope="department", scope_id=ids["department"],
               cents=1_000, by_agent=ids["director"], confirmed=True)

    # a raise that omits the displacement is a wish, and is refused
    with pytest.raises(OfficeError) as exc:
        E.raise_above(cfg, agent_id=ids["manager"], amount_cents=5_000,
                      thesis="ads are working", expected_return="3x",
                      displaces="", confirmed=True)
    assert exc.value.code == "OFFICE_BAD_INPUT"

    out = E.raise_above(cfg, agent_id=ids["manager"], amount_cents=5_000,
                        thesis="the ads channel returns 3x on spend",
                        expected_return="~15_000c in 30 days",
                        displaces="the Q3 hire, which slips a month",
                        confirmed=True)
    assert out["supervisor_id"] == ids["director"]   # raised to the level above
    row = E.requests(cfg)["requests"][0]
    for field in ("amount_cents", "thesis", "expected_return", "displaces"):
        assert str(row[field]).strip(), field


# 4 — a peer of the same level cannot decide the raise (the one-level rule)
def test_only_the_seat_a_raise_was_sent_to_may_decide_it(tmp_path, monkeypatch):
    from voicebridge.office import envelope as E
    from voicebridge.office import org as O
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    E.allocate(cfg, scope="department", scope_id=ids["department"],
               cents=1_000, by_agent=ids["director"], confirmed=True)
    out = E.raise_above(cfg, agent_id=ids["manager"], amount_cents=5_000,
                        thesis="t", expected_return="r", displaces="d",
                        confirmed=True)

    peer = O.hire(cfg, "mgr-other", seat_type="manager",
                  supervisor_id=ids["director"], confirmed=True)["id"]
    with pytest.raises(OfficeError) as exc:
        E.decide_request(cfg, out["id"], "approve", by_agent=peer,
                         confirmed=True)
    assert exc.value.code == "OFFICE_REQUEST_WRONG_LEVEL"

    # the actual supervisor may, and then the spend clears
    E.decide_request(cfg, out["id"], "approve", by_agent=ids["director"],
                     confirmed=True)
    from voicebridge.office import ledger
    ledger.record(cfg, "revenue", 50_000, agent_id=ids["manager"])
    booked = E.spend(cfg, agent_id=ids["manager"], cents=5_000)
    assert booked["within_envelope"] is False and booked["raised"] is True


# 5 — a spent-out envelope refuses the next turn (C8 still applies)
def test_an_exhausted_envelope_refuses_the_next_turn(tmp_path, monkeypatch):
    from voicebridge.office import envelope as E, ledger

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    E.allocate(cfg, scope="department", scope_id=ids["department"],
               cents=100, by_agent=ids["director"], confirmed=True)
    ledger.record(cfg, "revenue", 10_000, agent_id=ids["manager"])

    E.spend(cfg, agent_id=ids["manager"], cents=100)      # exactly exhausted
    after = E.check_spend(cfg, agent_id=ids["manager"], cents=1)
    assert after["allowed"] is False
    assert after["headroom_cents"] == 0

    env = E.envelopes(cfg)["envelopes"]
    dept = [e for e in env if e["scope"] == "department"][0]
    assert dept["spent_cents"] == 100 and dept["utilisation_pct"] == 100.0


# --- the split keeps a reserve, so the fast track exists without re-opening
# --- every envelope (D37)
def test_the_split_keeps_a_reserve_and_sums_back_to_the_total(
        tmp_path, monkeypatch):
    from voicebridge.office import envelope as E, org as O

    cfg = _cfg(tmp_path, monkeypatch)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    for slug, port in (("dir-a", "games"), ("dir-b", "tools"),
                       ("dir-c", "media")):
        O.hire(cfg, slug, seat_type="director_portfolio", supervisor_id=ceo,
               portfolio=port, confirmed=True)
    out = E.allocate_portfolios(cfg, by_agent=ceo, total_cents=100_000,
                                reserve_pct=20, confirmed=True)
    assert out["reserve_cents"] == 20_000
    assert len(out["directors"]) == 3
    assert sum(d["cents"] for d in out["directors"]) == 80_000
    assert out["reserve_cents"] + out["split_cents"] == out["total_cents"]


def test_the_split_says_what_is_missing_rather_than_splitting_nothing(
        tmp_path, monkeypatch):
    from voicebridge.office import envelope as E, org as O
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    with pytest.raises(OfficeError) as exc:
        E.allocate_portfolios(cfg, by_agent=ceo, total_cents=100_000,
                              confirmed=True)
    assert exc.value.code == "OFFICE_BAD_STATE"
    assert "directorate" in exc.value.detail


# --- the CEO raises to the owner, who is not an agent
def test_the_ceo_cannot_raise_because_the_owner_is_not_an_agent(
        tmp_path, monkeypatch):
    from voicebridge.office import envelope as E, org as O
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    with pytest.raises(OfficeError) as exc:
        E.raise_above(cfg, agent_id=ceo, amount_cents=100, thesis="t",
                      expected_return="r", displaces="d", confirmed=True)
    assert exc.value.code == "OFFICE_BAD_STATE"
    assert "memo" in exc.value.detail


# --- the second bound: a raise does not create money the seat does not have
def test_a_raise_cannot_fix_a_seat_that_cannot_afford_it(tmp_path, monkeypatch):
    from voicebridge.office import envelope as E, ledger
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    E.allocate(cfg, scope="department", scope_id=ids["department"],
               cents=10_000_000, by_agent=ids["director"], confirmed=True)
    ledger.record(cfg, "revenue", 5_000, agent_id=ids["manager"])

    # the envelope is enormous, so this is purely a solvency question
    check = E.check_spend(cfg, agent_id=ids["manager"], cents=50_000)
    assert check["allowed"] is False and check["needs_funds"] is True
    assert check["needs_raise"] is False
    assert "cannot spend money it does not have" in check["reason"]

    with pytest.raises(OfficeError) as exc:
        E.spend(cfg, agent_id=ids["manager"], cents=50_000)
    assert exc.value.code == "OFFICE_INSUFFICIENT_BALANCE"
    assert ledger.balance(cfg, ids["manager"])["balance_cents"] == 5_000


# --- and a solvency refusal is never reported as a policy refusal
def test_the_two_refusals_are_never_confused(tmp_path, monkeypatch):
    """A raise clears the envelope but can never clear a missing balance."""
    from voicebridge.office import envelope as E, ledger
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    E.allocate(cfg, scope="department", scope_id=ids["department"],
               cents=1_000, by_agent=ids["director"], confirmed=True)
    ledger.record(cfg, "revenue", 5_000, agent_id=ids["manager"])

    # above the envelope AND above the balance: the policy refusal comes first,
    # because a raise is the action the contract asks for
    with pytest.raises(OfficeError) as exc:
        E.spend(cfg, agent_id=ids["manager"], cents=4_000)
    assert exc.value.code == "OFFICE_ENVELOPE_EXCEEDED"

    E.raise_above(cfg, agent_id=ids["manager"], amount_cents=6_000,
                  thesis="t", expected_return="r", displaces="d",
                  confirmed=True)
    rid = E.requests(cfg, status="open")["requests"][0]["id"]
    E.decide_request(cfg, rid, "approve", by_agent=ids["director"],
                     confirmed=True)
    # the envelope now allows it (the raise cleared it) and the balance does
    # not: the code must change with the reason, not stay on the envelope one
    with pytest.raises(OfficeError) as exc:
        E.spend(cfg, agent_id=ids["manager"], cents=6_000)
    assert exc.value.code == "OFFICE_INSUFFICIENT_BALANCE"


# --- a negative balance reads as "out of money", not as negative days
def test_a_seat_in_the_red_is_reported_as_out_of_money(tmp_path, monkeypatch):
    from voicebridge.office import ledger, survival, store

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    ledger.record(cfg, "revenue", 100, agent_id=ids["manager"])
    ledger.record(cfg, "inference", 1_400, agent_id=ids["manager"],
                  ts=store.now() - 3 * 86400.0)

    info = survival.runway_days(cfg, ids["manager"])
    assert info["balance_cents"] < 0
    assert info["insolvent"] is True and info["at_risk"] is True
    assert "already out of money" in info["meaning"]


# --- no envelope at all is uncapped, and says so rather than implying zero
def test_no_envelope_means_uncapped_not_zero(tmp_path, monkeypatch):
    from voicebridge.office import envelope as E, ledger

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    ledger.record(cfg, "revenue", 1_000_000, agent_id=ids["manager"])
    info = E.headroom_for(cfg, agent_id=ids["manager"])
    assert info["envelope"] is None and info["headroom_cents"] is None
    assert E.check_spend(cfg, agent_id=ids["manager"],
                         cents=999_999)["allowed"]


# --- spend inside the envelope is always reported (D37), never silent
def test_envelope_spend_is_reported_and_derived_from_the_ledger(
        tmp_path, monkeypatch):
    from voicebridge.office import envelope as E, ledger

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _org(cfg)
    E.allocate(cfg, scope="department", scope_id=ids["department"],
               cents=10_000, by_agent=ids["director"], confirmed=True)
    ledger.record(cfg, "revenue", 5_000, agent_id=ids["manager"])
    E.spend(cfg, agent_id=ids["manager"], cents=400)
    E.spend(cfg, agent_id=ids["manager"], cents=600)

    dept = [e for e in E.envelopes(cfg)["envelopes"]
            if e["scope"] == "department"][0]
    assert dept["spent_cents"] == 1_000       # GROUP BY, not a stored total
    assert dept["remaining_cents"] == 9_000
    assert ledger.sum_metered(cfg, scope="department",
                              id=ids["department"]) == 1_000
