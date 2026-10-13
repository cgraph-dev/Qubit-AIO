"""Balance-sheet reconciliation: the book must prove itself, or refuse.

Contract proofs for ``voicebridge/office/balance_sheet.py``:

* a trial balance totals equal debit and credit for a known set of rows, with
  the direction taken from each kind's sign rule;
* ``explain()`` bridges opening to closing exactly, residual 0;
* the NEGATIVE case — a genuine discrepancy (an ``unmetered=1`` row outside
  the metered fold, D16) is *named*, never folded in, and an incomplete ledger
  (an open run receipt) makes ``assert_balances`` refuse with a reason naming
  the incomplete ledger and a residual that is UNKNOWN, not 0;
* a compensating ``transfer`` is provably neutral;
* ``KINDS_DIRECTION`` agrees with ``ledger.py`` by parsing the module source.

Hermetic: tmp XDG state only; no network, no live services, no provider call.
"""
from __future__ import annotations

import sqlite3

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seated_cfg(tmp_path, monkeypatch):
    """A real org with a real seat, so agent-scoped rows have a target."""
    from voicebridge.office import org as O, store

    cfg = _cfg(tmp_path, monkeypatch)
    store.init(cfg)
    seat = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)
    return cfg, seat["id"]


# ------------------------------------------------------------ 1. trial balance
def test_trial_balance_totals_equal_for_a_known_book(tmp_path, monkeypatch):
    """revenue 1000, inference 20, adjustment +/-100, a transfer reversal.

    Rows (all metered):
      revenue      +1000  (direction +, adds to net)
      inference      -20  (direction -, subtracts)
      adjustment    +100  (signed, to a seat)
      adjustment    -100  (signed, from the treasury)
      transfer      -100  (the reversal of the +100 capital adjustment)

    Measured net = 1000 - 20 + 100 - 100 - 100 = 880.
    """
    from voicebridge.office import balance_sheet as BS, ledger
    from voicebridge.office import store

    cfg, seat = _seated_cfg(tmp_path, monkeypatch)
    ledger.record(cfg, "revenue", 1000, agent_id=seat, note="invoice")
    ledger.record(cfg, "inference", 20, agent_id=seat, note="one turn")
    # An adjustment is signed; +100 to a seat, -100 from the treasury.
    capital = ledger.record(cfg, "adjustment", 100, agent_id=seat,
                            note="capital allocation to seat")
    ledger.record(cfg, "adjustment", -100, note="capital from treasury")
    # A transfer MUST name the row it offsets (C1) — use the real path.
    ledger.reverse(cfg, capital["id"], note="undo the allocation")

    tb = BS.trial_balance(cfg)
    assert tb["ok"] is True
    assert tb["balanced"] is True

    by_kind = {ln["kind"]: ln for ln in tb["lines"]}
    # Direction is applied from the kind's sign rule, not invented.
    assert by_kind["revenue"]["net_cents"] == 1000
    assert by_kind["revenue"]["debit_cents"] == 1000
    assert by_kind["inference"]["net_cents"] == -20
    assert by_kind["inference"]["credit_cents"] == 20
    # adjustment line is the NET of +100 and -100 = 0, not +100.
    assert by_kind["adjustment"]["net_cents"] == 0
    # The reversal transfer (-100) nets the +100 capital back to zero.
    assert by_kind["transfer"]["net_cents"] == -100

    # Debit == credit by construction: revenue 1000 debit; credit side is
    # inference 20 + transfer 100 + the period's balancing line 880 = 1000.
    assert tb["total_debit"] == tb["total_credit"] == 1000
    assert tb["net_cents"] == 880
    assert tb["residual_cents"] == 0
    assert tb["balanced"] is True
    # 6 ledger kinds + the balancing line.
    assert len([ln for ln in tb["lines"] if ln["kind"] != "balancing"]) == \
        len(store.KINDS)

    # explain() must bridge to the same closing net.
    ex = BS.explain(cfg)
    assert ex["opening"] == 0
    assert ex["revenue"] == 1000
    assert ex["costs"] == 20
    assert ex["adjustments"] == -100  # +100 capital -100 treasury -100 reversal
    assert ex["closing"] == 880
    assert ex["residual"] == 0
    assert ex["explained"] is True

    # The gate agrees: complete, no residual.
    gate = BS.assert_balances(cfg)
    assert gate["ok"] is True
    assert gate["residual"] == 0
    assert gate["complete"] is True
    assert gate["violations"] == []


# ---------------------------------------------------------------- 2. the bridge
def test_explain_bridges_opening_to_closing_exactly(tmp_path, monkeypatch):
    """opening + revenue - costs + adjustments == closing, residual 0."""
    from voicebridge.office import balance_sheet as BS, ledger

    cfg, seat = _seated_cfg(tmp_path, monkeypatch)
    ledger.record(cfg, "revenue", 5000, agent_id=seat)
    ledger.record(cfg, "inference", 300, agent_id=seat)
    ledger.record(cfg, "fees", 50, agent_id=seat)
    ledger.record(cfg, "capex", 150, agent_id=seat)
    ledger.record(cfg, "adjustment", 25, agent_id=seat)   # signed

    ex = BS.explain(cfg)
    lhs = ex["opening"] + ex["revenue"] - ex["costs"] + ex["adjustments"]
    assert lhs == ex["closing"]
    assert ex["opening"] == 0
    assert ex["revenue"] == 5000
    assert ex["costs"] == 500          # 300 + 50 + 150
    assert ex["adjustments"] == 25
    assert ex["closing"] == 4525
    assert ex["residual"] == 0
    assert ex["explained"] is True
    assert ex["cost_detail"] == {"inference": 300, "fees": 50, "capex": 150}


# ------------------------------------------------------ 3. the negative cases
def test_unmetered_row_is_named_and_never_folded_in(tmp_path, monkeypatch):
    """D16: a recorded-but-unmetered row is outside the metered fold.

    It is *real*, but it must not appear in the metered net and must not be
    silently absorbed. ``assert_balances`` surfaces it as ``unmetered_cents``
    and keeps it out of ``residual``.
    """
    from voicebridge.office import balance_sheet as BS, ledger

    cfg, seat = _seated_cfg(tmp_path, monkeypatch)
    ledger.record(cfg, "revenue", 1000, agent_id=seat)
    ledger.record(cfg, "inference", 20, agent_id=seat)
    # A subscription-backed turn: recorded, metered fold must never see it.
    ledger.record(cfg, "inference", 777, agent_id=seat, note="subscription",
                  unmetered=True)

    ex = BS.explain(cfg)
    # The unmetered 777 is NOT in costs or closing.
    assert ex["costs"] == 20
    assert ex["closing"] == 980

    gate = BS.assert_balances(cfg)
    assert gate["unmetered_cents"] == 777
    # It is not in residual — it is outside the metered fold, not a drift.
    assert gate["residual"] == 0
    assert gate["ok"] is True

    tb = BS.trial_balance(cfg)
    by_kind = {ln["kind"]: ln for ln in tb["lines"]}
    assert by_kind["inference"]["net_cents"] == -20      # 777 not counted
    assert tb["unmetered_cents"] == 777                  # but named
    assert "unmetered" in tb["reason"]


def test_a_genuine_residual_is_named_and_refused(tmp_path, monkeypatch):
    """A non-zero residual -> ok False, and the reason names the cents.

    We inject a phantom net into the fold the module computes so the rows no
    longer account for the view's net. This is the drift the audit exists to
    catch: recorded activity that does not explain the net. The gate must
    refuse and name the cents — never round them away.
    """
    from voicebridge.office import balance_sheet as BS, ledger

    cfg, seat = _seated_cfg(tmp_path, monkeypatch)
    ledger.record(cfg, "revenue", 1000, agent_id=seat)

    assert BS.assert_balances(cfg)["ok"] is True     # clean to start

    real = BS._kind_folds

    def _drifting(conn, period):
        folds, un = real(conn, period)
        folds["inference"] = {"entries": 1, "magnitude_cents": 250,
                              "net_cents": -250}       # 250 cents unaccounted
        return folds, un

    monkeypatch.setattr(BS, "_kind_folds", _drifting)
    gate = BS.assert_balances(cfg)

    assert gate["ok"] is False
    assert gate["residual"] == -250
    assert "residual" in gate["violations"]
    assert "250" in gate["reason"]
    assert gate["reason"].startswith("residual:")
    # An unexplained residual must never be reported as balanced.
    assert gate["reason"] != "balanced: no unexplained residual"


def test_incomplete_ledger_refuses_with_unknown_residual(tmp_path, monkeypatch):
    """An open run receipt -> ok False, residual UNKNOWN, reason names the run."""
    from voicebridge.office import balance_sheet as BS, receipts

    cfg, seat = _seated_cfg(tmp_path, monkeypatch)
    receipts.begin_run(cfg, seat, cap_cents=1000, run_id="run_open_1")

    gate = BS.assert_balances(cfg)
    assert gate["ok"] is False
    assert gate["complete"] is False
    # The residual is UNKNOWN, not 0 — a zero would read as a balanced book.
    assert gate["residual"] is None
    assert "incomplete" in gate["violations"]
    assert "run_open_1" in gate["reason"]
    assert "UNKNOWN" in gate["reason"]
    # The reason must NOT claim a zero residual.
    assert gate["reason"] != "balanced: no unexplained residual"


def test_settled_run_restores_the_gate(tmp_path, monkeypatch):
    """Close the run and the gate opens again — over an honest zero residual."""
    from voicebridge.office import balance_sheet as BS, receipts

    cfg, seat = _seated_cfg(tmp_path, monkeypatch)
    receipts.begin_run(cfg, seat, cap_cents=1000, run_id="run_settle_1")
    assert BS.assert_balances(cfg)["ok"] is False

    receipts.finish_run(cfg, "run_settle_1", settled_cents=40)
    gate = BS.assert_balances(cfg)
    assert gate["ok"] is True
    assert gate["complete"] is True
    assert gate["residual"] == 0
    assert gate["reason"] == "balanced: no unexplained residual"


# ------------------------------------------------- 4. transfer neutrality
def test_a_compensating_transfer_is_provably_neutral(tmp_path, monkeypatch):
    """A reversal (transfer) nets to zero in the trial balance."""
    from voicebridge.office import balance_sheet as BS, ledger

    cfg, seat = _seated_cfg(tmp_path, monkeypatch)
    capital = ledger.record(cfg, "adjustment", 400, agent_id=seat,
                            note="capital")

    ledger.reverse(cfg, capital["id"], note="reversal")
    after = BS.trial_balance(cfg)

    # The reversal added a transfer of -400, cancelling the +400 adjustment.
    by_kind = {ln["kind"]: ln for ln in after["lines"]}
    assert by_kind["adjustment"]["net_cents"] == 400
    assert by_kind["transfer"]["net_cents"] == -400
    # The pair is provably neutral: the signed kinds net to zero, so the
    # reversal leaves the book's net unchanged.
    assert by_kind["adjustment"]["net_cents"] + \
        by_kind["transfer"]["net_cents"] == 0
    assert after["net_cents"] == 0


# ------------------------------------------- 5. the sign rule is guarded
def test_kinds_direction_matches_ledger_source(tmp_path, monkeypatch):
    """A change to ledger.py's sign rule must fail here, not mis-state books."""
    from voicebridge.office import balance_sheet as BS
    from voicebridge.office import ledger, store

    # The runtime map is total over every kind the store allows.
    assert set(BS.KINDS_DIRECTION) == set(store.KINDS)

    # Re-derive the map from the ledger *source* (not the imported constants).
    from_source = BS.direction_map_from_source()
    for kind, direction in from_source.items():
        assert BS.KINDS_DIRECTION[kind] == direction, (
            f"KINDS_DIRECTION[{kind!r}]={BS.KINDS_DIRECTION[kind]} disagrees "
            f"with the sign rule parsed from ledger.py ({direction})")

    # The structural rule, stated explicitly:
    assert BS.KINDS_DIRECTION["revenue"] == +1
    for k in ("inference", "fees", "capex"):
        assert BS.KINDS_DIRECTION[k] == -1
    for k in ("transfer", "adjustment"):
        assert BS.KINDS_DIRECTION[k] == +1

    # The imported constants agree with the parsed source too.
    assert set(BS.MAGNITUDE_KINDS) == set(ledger.MAGNITUDE_KINDS)
    assert set(BS.SIGNED_KINDS) == set(ledger.SIGNED_KINDS)
    assert set(from_source) == set(ledger.MAGNITUDE_KINDS) | \
        set(ledger.SIGNED_KINDS)
