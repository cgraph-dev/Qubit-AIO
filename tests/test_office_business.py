"""M3 — the business lifecycle and its gates (D33/C22/C23).

The proof the milestone names: a business goes `proposed → building → live`,
sells, and its P&L is positive in the ledger with no hand-entered number. Plus
the two gates that make `live` mean something, and the one gate that is the
owner's alone.

Hermetic: tmp XDG state only; no network, no payment processor, no provider call.
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


def _dept(cfg):
    from voicebridge.office import org as O

    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    d = O.hire(cfg, "dir", seat_type="director_portfolio", supervisor_id=ceo,
               confirmed=True)["id"]
    dept = O.department_create(cfg, "shop", director_id=d,
                               kill_criteria="two quarters negative net",
                               confirmed=True)["id"]
    return {"ceo": ceo, "director": d, "department": dept}


def _live(cfg, ids, *, exposure="tax registration and 14-day refunds"):
    from voicebridge.office import business as B

    b = B.propose(cfg, "gadget", department_id=ids["department"],
                  thesis="people already pay for this, badly",
                  offer="a gadget, 5 dollars, direct", price_cents=500,
                  confirmed=True)["id"]
    B.set_terms(cfg, b, kill_criteria="no sale in 60 days",
                exposure=exposure, confirmed=True)
    B.advance(cfg, b, "building", confirmed=True)
    B.advance(cfg, b, "live", confirmed=True)
    return b


# --- a business must state its thesis and offer before it exists
def test_a_business_must_state_a_thesis_and_an_offer(tmp_path, monkeypatch):
    from voicebridge.office import business as B
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _dept(cfg)
    for kwargs in ({"thesis": "  ", "offer": "a thing"},
                   {"thesis": "a reason", "offer": ""}):
        with pytest.raises(OfficeError) as exc:
            B.propose(cfg, "x", department_id=ids["department"],
                      confirmed=True, **kwargs)
        assert exc.value.code == "OFFICE_BAD_INPUT"


# --- THE GATE: no kill criteria and no exposure means no live
def test_a_business_cannot_go_live_without_kill_criteria_and_exposure(
        tmp_path, monkeypatch):
    from voicebridge.office import business as B
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _dept(cfg)
    b = B.propose(cfg, "gadget", department_id=ids["department"],
                  thesis="t", offer="o", confirmed=True)["id"]
    B.advance(cfg, b, "building", confirmed=True)

    with pytest.raises(OfficeError) as exc:
        B.advance(cfg, b, "live", confirmed=True)
    assert exc.value.code == "OFFICE_BUSINESS_GATE"
    assert "kill_criteria" in exc.value.detail
    assert "exposure" in exc.value.detail
    # and the seat is told what the gate is *for*, not just that it fired
    assert "no declared way to die" in exc.value.detail
    assert "legal risk" in exc.value.detail

    # exposure alone is not enough
    B.set_terms(cfg, b, exposure="tax registration", confirmed=True)
    with pytest.raises(OfficeError) as exc:
        B.advance(cfg, b, "live", confirmed=True)
    assert "kill_criteria" in exc.value.detail

    B.set_terms(cfg, b, kill_criteria="no sale in 60 days", confirmed=True)
    assert B.advance(cfg, b, "live", confirmed=True)["status"] == "live"


# --- no skipping the ladder, and no resurrecting a dead business
def test_the_lifecycle_only_moves_forward(tmp_path, monkeypatch):
    from voicebridge.office import business as B
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _dept(cfg)
    b = B.propose(cfg, "gadget", department_id=ids["department"],
                  thesis="t", offer="o", confirmed=True)["id"]

    with pytest.raises(OfficeError) as exc:
        B.advance(cfg, b, "profitable", confirmed=True)   # skips two steps
    assert exc.value.code == "OFFICE_BAD_STATE"
    assert "Allowed from proposed: building, dead" in exc.value.detail

    B.advance(cfg, b, "dead", reason="thesis did not survive contact", confirmed=True)
    with pytest.raises(OfficeError) as exc:
        B.advance(cfg, b, "live", confirmed=True)
    assert "nothing — it is dead" in exc.value.detail


# --- a kill needs a reason (C27), and the terms freeze with the corpse
def test_a_kill_needs_a_reason_and_then_the_terms_are_frozen(tmp_path,
                                                            monkeypatch):
    from voicebridge.office import business as B
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _dept(cfg)
    b = B.propose(cfg, "gadget", department_id=ids["department"],
                  thesis="t", offer="o", confirmed=True)["id"]
    with pytest.raises(OfficeError) as exc:
        B.advance(cfg, b, "dead", reason="  ", confirmed=True)
    assert exc.value.code == "OFFICE_BAD_INPUT"

    B.advance(cfg, b, "dead", reason="no demand at any price", confirmed=True)
    with pytest.raises(OfficeError) as exc:
        B.set_terms(cfg, b, kill_criteria="something else", confirmed=True)
    assert "post-mortem" in exc.value.detail


# --- THE OWNER GATE: paper first, and no agent-facing call can fund it
def test_the_owner_funds_and_no_agent_function_can(tmp_path, monkeypatch):
    from voicebridge.office import business as B
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _dept(cfg)
    b = _live(cfg, ids)
    assert B.businesses(cfg)["businesses"][0]["funded"] == 0

    with pytest.raises(OfficeError) as exc:
        B.record_revenue(cfg, b, 500, source="stripe", external_ref="pi_1")
    assert exc.value.code == "OFFICE_BUSINESS_GATE"
    assert "not owner-funded" in exc.value.detail
    assert "only the owner can" in exc.value.detail

    # a one-word funding is a switch, not a record of who threw it
    with pytest.raises(OfficeError) as exc:
        B.owner_fund(cfg, b, owner_words="yes", confirmed=True)
    assert exc.value.code == "OFFICE_BAD_INPUT"
    assert "own words" in exc.value.detail

    out = B.owner_fund(cfg, b, owner_words="yes, put real money behind the gadget",
                       confirmed=True)
    assert out["funded"] is True

    # no exposed agent-facing function takes the funding parameter at all
    import inspect
    for fn in (B.propose, B.set_terms, B.advance, B.record_revenue):
        params = inspect.signature(fn).parameters
        assert "funded" not in params, fn.__name__
        assert "owner_words" not in params, fn.__name__


# --- revenue is idempotent on the processor's own id: a replay is not a sale
def test_a_replayed_payment_is_one_row_not_two(tmp_path, monkeypatch):
    from voicebridge.office import business as B

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _dept(cfg)
    b = _live(cfg, ids)
    B.owner_fund(cfg, b, owner_words="funding it for real, go and sell it",
                 confirmed=True)

    first = B.record_revenue(cfg, b, 500, source="stripe", external_ref="pi_abc")
    second = B.record_revenue(cfg, b, 500, source="stripe", external_ref="pi_abc")
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert first["ledger_id"] == second["ledger_id"]
    assert B.pnl(cfg, b)["revenue_cents"] == 500     # one sale, not 1,000


# --- revenue must name the processor's reference, or it is an assertion
def test_revenue_without_a_processor_reference_is_refused(tmp_path, monkeypatch):
    from voicebridge.office import business as B
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _dept(cfg)
    b = _live(cfg, ids)
    B.owner_fund(cfg, b, owner_words="real money, go and sell the gadget",
                 confirmed=True)
    with pytest.raises(OfficeError) as exc:
        B.record_revenue(cfg, b, 500, source="stripe", external_ref="  ")
    assert exc.value.code == "OFFICE_BAD_INPUT"
    assert "assertion rather than a sale" in exc.value.detail


# --- a non-live business cannot book revenue, funded or not
def test_only_a_live_business_sells(tmp_path, monkeypatch):
    from voicebridge.office import business as B
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _dept(cfg)
    b = B.propose(cfg, "gadget", department_id=ids["department"],
                  thesis="t", offer="o", confirmed=True)["id"]
    B.set_terms(cfg, b, kill_criteria="k", exposure="e", confirmed=True)
    B.owner_fund(cfg, b, owner_words="funding this before it is even built",
                 confirmed=True)
    with pytest.raises(OfficeError) as exc:
        B.record_revenue(cfg, b, 500, source="stripe", external_ref="pi_1")
    assert exc.value.code == "OFFICE_BUSINESS_GATE"
    assert "Only a live business sells" in exc.value.detail


# --- THE MILESTONE PROOF: the P&L turns positive with no hand-entered number
def test_the_business_pnl_is_derived_and_turns_positive(tmp_path, monkeypatch):
    from voicebridge.office import business as B, ledger as L

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _dept(cfg)
    b = _live(cfg, ids)
    B.owner_fund(cfg, b, owner_words="real money behind the gadget business",
                 confirmed=True)

    # it spends first: inference to build, then a processor fee
    L.record(cfg, "inference", 120, agent_id=ids["director"],
             business_id=b, department_id=ids["department"])
    L.record(cfg, "fees", 45, agent_id=ids["director"], business_id=b,
             department_id=ids["department"], note="processor fee")
    assert B.pnl(cfg, b)["net_cents"] == -165
    assert B.pnl(cfg, b)["break_even"] is False

    B.record_revenue(cfg, b, 500, source="stripe", external_ref="pi_1")
    p = B.pnl(cfg, b)
    assert p["revenue_cents"] == 500 and p["cost_cents"] == 165
    assert p["net_cents"] == 335 and p["break_even"] is True
    assert p["entries"] == 3          # every figure is a GROUP BY (C2)

    # the business rollup feeds the department's, which feeds the org's
    assert L.pnl(cfg, scope="department", id=ids["department"])["revenue_cents"] == 500
    assert L.pnl(cfg, scope="org")["net_cents"] == 335


# --- the P&L explains the chain it sits in, not just its own numbers
def test_the_business_pnl_names_its_chain(tmp_path, monkeypatch):
    from voicebridge.office import business as B

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _dept(cfg)
    b = _live(cfg, ids)
    p = B.pnl(cfg, b)
    assert p["chain"]["department_id"] == ids["department"]
    assert p["chain"]["director_slug"] == "dir"
    assert p["kill_criteria"] and p["exposure"]


# --- a business with gates missing says which ones, without being asked
def test_the_business_list_reports_missing_gates(tmp_path, monkeypatch):
    from voicebridge.office import business as B

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _dept(cfg)
    B.propose(cfg, "half-planned", department_id=ids["department"],
              thesis="t", offer="o", confirmed=True)
    row = B.businesses(cfg)["businesses"][0]
    assert sorted(row["missing_gates"]) == ["exposure", "kill_criteria"]


# --- every mutating call needs --confirm, with no third path
def test_the_business_surface_is_confirm_gated(tmp_path, monkeypatch):
    from voicebridge.office import business as B, store
    from voicebridge.office.errors import OfficeError

    cfg = _cfg(tmp_path, monkeypatch)
    ids = _dept(cfg)
    with pytest.raises(OfficeError) as exc:
        B.propose(cfg, "gadget", department_id=ids["department"],
                  thesis="t", offer="o")
    assert exc.value.code == store.CONFIRM_CODE
    assert B.businesses(cfg)["count"] == 0
