"""FINDING #2 — reversing an unmetered row must not manufacture metered money.

A reversal must be the *exact inverse* of what the original did to the metered
fold (D16). An ``unmetered=1`` row is invisible to every metered aggregate, so
its compensating row must be invisible too — otherwise undoing a phantom mints
(``transfer`` = +N) or destroys (``transfer`` = -N) real, spendable balance.

Hermetic: tmp XDG state, real SQLite, no network, no provider call.
"""
from __future__ import annotations


def _cfg(tmp_path, monkeypatch, name="unmetered"):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s" / name))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / f"voice-{name}.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seat(tmp_path, monkeypatch, name="unmetered"):
    from voicebridge.office import org as O
    from voicebridge.office import store

    cfg = _cfg(tmp_path, monkeypatch, name=name)
    store.init(cfg)
    ceo = O.hire(cfg, "ceo", seat_type="ceo", confirmed=True)["id"]
    mgr = O.hire(cfg, "mgr", seat_type="manager", supervisor_id=ceo,
                 confirmed=True)["id"]
    return cfg, mgr


def test_reversing_unmetered_inference_creates_no_metered_money(
        tmp_path, monkeypatch):
    """The reproduction: a phantom turn, undone, must stay worthless.

    Before the fix: balance 0 -> 777 metered cents.
    """
    from voicebridge.office import ledger as L

    cfg, seat = _seat(tmp_path, monkeypatch, name="unmetered_rev")

    assert L.balance(cfg, seat)["balance_cents"] == 0

    row = L.record(cfg, "inference", 777, agent_id=seat, unmetered=True,
                   note="subscription-backed turn")
    assert L.balance(cfg, seat)["balance_cents"] == 0, "D16 already broken"

    rev = L.reverse(cfg, row["id"])
    # The compensating row is itself unmetered...
    assert rev["unmetered"] is True
    # ... so the seat's metered balance is unmoved.
    assert L.balance(cfg, seat)["balance_cents"] == 0
    assert L.sum_metered(cfg) == 0

    # And it still names and offsets the row it corrects (C1).
    rows = {r["id"]: r for r in L.rows(cfg)["rows"]}
    assert rows[rev["id"]]["offsets_id"] == row["id"]
    assert rows[rev["id"]]["unmetered"] == 1


def test_reversing_unmetered_revenue_destroys_no_metered_money(
        tmp_path, monkeypatch):
    """The destructive direction: a phantom sale, undone, must not go negative."""
    from voicebridge.office import ledger as L

    cfg, seat = _seat(tmp_path, monkeypatch, name="unmetered_rev2")

    row = L.record(cfg, "revenue", 5000, agent_id=seat, unmetered=True,
                   note="unmetered revenue")
    assert L.balance(cfg, seat)["balance_cents"] == 0

    rev = L.reverse(cfg, row["id"])
    assert rev["unmetered"] is True
    assert L.balance(cfg, seat)["balance_cents"] == 0


def test_metered_reversal_stays_metered_and_neutral(tmp_path, monkeypatch):
    """The fix must not over-reach: a metered reversal is still metered.

    ``sum_metered`` is *gross cost* (inference+fees+capex), and a reversal is a
    ``transfer``, a signed kind — so the gross cost legitimately stays on the
    record and only the **net** is corrected (a real credit note). The reversal
    must therefore be metered and net-neutral, not scrubbed from gross cost.
    """
    from voicebridge.office import ledger as L

    cfg, seat = _seat(tmp_path, monkeypatch, name="metered_rev")

    inf = L.record(cfg, "inference", 250, agent_id=seat)
    assert L.pnl(cfg, scope="agent", id=seat)["net_cents"] == -250

    rev = L.reverse(cfg, inf["id"])
    assert rev["unmetered"] is False
    # net is corrected to zero; gross metered cost stays as recorded
    assert L.pnl(cfg, scope="agent", id=seat)["net_cents"] == 0
    assert L.balance(cfg, seat)["balance_cents"] == 0
    assert L.sum_metered(cfg) == 250


def test_assert_balances_stays_ok_and_the_ledger_stays_healthy(
        tmp_path, monkeypatch):
    """The gate is green for a healthy book, and the unmetered correction
    sequence does not introduce a residual."""
    from voicebridge.office import balance_sheet as BS
    from voicebridge.office import ledger as L

    cfg, seat = _seat(tmp_path, monkeypatch, name="gate")

    # A genuinely healthy metered book.
    L.record(cfg, "revenue", 8000, agent_id=seat)
    L.record(cfg, "inference", 2000, agent_id=seat)
    gate = BS.assert_balances(cfg)
    assert gate["ok"] is True
    assert gate["residual"] == 0
    assert L.health(cfg)["healthy"] is True

    # Now the unmetered correction sequence, which must be neutral.
    phantom = L.record(cfg, "inference", 777, agent_id=seat, unmetered=True)
    before = L.balance(cfg, seat)["balance_cents"]
    L.reverse(cfg, phantom["id"])
    assert L.balance(cfg, seat)["balance_cents"] == before

    gate = BS.assert_balances(cfg)
    assert gate["ok"] is True
    assert gate["residual"] == 0
    assert L.health(cfg)["healthy"] is True
