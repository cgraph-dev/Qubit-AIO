"""P5-02 — double-entry portfolio ledger, cost basis, fees and funding."""
from __future__ import annotations

from decimal import Decimal

import pytest

from voicebridge.business import trading as T


def _order(client_order_id: str, *, side: str, quantity: str) -> T.OrderRequest:
    return T.OrderRequest(
        client_order_id=client_order_id,
        symbol="BTCUSD",
        side=side,
        quantity=Decimal(quantity),
        strategy_id="paper-sma",
    )


def test_buy_sell_round_trip_is_balanced(system_factory, execution):
    system = system_factory()
    buy = system.submit_order(execution, _order("buy", side="buy", quantity="2"))
    buy_fee = buy.fills[0].fee_minor
    cost_basis = system.portfolio.position("BTCUSD").cost_basis_minor
    sell = system.submit_order(
        execution, _order("sell", side="sell", quantity="2")
    )
    sell_fee = sell.fills[0].fee_minor

    assert system.portfolio.position("BTCUSD").quantity == 0
    assert system.portfolio.position("BTCUSD").cost_basis_minor == 0
    expected_realized = sell.fills[0].notional_minor - cost_basis
    assert system.portfolio.realized_pnl_minor == expected_realized
    assert system.portfolio.fees_minor == buy_fee + sell_fee
    assert system.portfolio.cash_minor == system.ledger.balance("cash")
    assert system.ledger.is_balanced()
    assert system.ledger.total_minor() == 0
    for journal in system.ledger.journals():
        assert journal.debit_minor == journal.credit_minor
    assert system.ledger.balance("capital") == -10_000_000
    assert system.audit()["ok"] is True


def test_funding_is_recorded_in_both_directions(clock):
    ledger = T.DoubleEntryLedger()
    portfolio = T.Portfolio(ledger)
    portfolio.fund(1_000_000, at=clock.t)
    portfolio.apply_funding(
        symbol="BTCUSD", amount_minor=5_000, at=clock.t, memo="paid"
    )
    assert portfolio.cash_minor == 995_000
    assert portfolio.funding_minor == 5_000
    assert ledger.balance("funding") == 5_000

    portfolio.apply_funding(
        symbol="BTCUSD", amount_minor=-2_000, at=clock.t, memo="received"
    )
    assert portfolio.cash_minor == 997_000
    assert portfolio.funding_minor == 3_000
    assert ledger.balance("funding") == 3_000
    assert ledger.is_balanced()


def test_partial_sale_releases_proportional_cost(clock):
    ledger = T.DoubleEntryLedger()
    portfolio = T.Portfolio(ledger)
    portfolio.fund(1_000_000, at=clock.t)
    portfolio.apply_fill(
        symbol="BTCUSD",
        side="buy",
        quantity=Decimal("10"),
        price=Decimal("100"),
        fee_minor=0,
        order_id="ord",
        fill_id="fill-buy",
        at=clock.t,
        event_seq=None,
    )
    portfolio.apply_fill(
        symbol="BTCUSD",
        side="sell",
        quantity=Decimal("4"),
        price=Decimal("110"),
        fee_minor=0,
        order_id="ord",
        fill_id="fill-sell",
        at=clock.t,
        event_seq=None,
    )
    position = portfolio.position("BTCUSD")
    assert position.quantity == Decimal("6")
    assert position.cost_basis_minor == 60_000
    assert position.realized_pnl_minor == 4_000
    assert ledger.is_balanced()


def test_oversell_is_refused(clock):
    ledger = T.DoubleEntryLedger()
    portfolio = T.Portfolio(ledger)
    portfolio.fund(1_000_000, at=clock.t)
    portfolio.apply_fill(
        symbol="BTCUSD",
        side="buy",
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee_minor=0,
        order_id="ord",
        fill_id="fill",
        at=clock.t,
        event_seq=None,
    )
    with pytest.raises(T.TradingError) as exc:
        portfolio.apply_fill(
            symbol="BTCUSD",
            side="sell",
            quantity=Decimal("2"),
            price=Decimal("100"),
            fee_minor=0,
            order_id="ord",
            fill_id="fill-2",
            at=clock.t,
            event_seq=None,
        )
    assert exc.value.code == T.BAD_INPUT


def test_unbalanced_journal_is_refused():
    ledger = T.DoubleEntryLedger()
    with pytest.raises(T.TradingError) as exc:
        ledger.post(
            kind="bad",
            memo="unbalanced",
            postings=(
                T.Posting("cash", "debit", 100),
                T.Posting("capital", "credit", 90),
            ),
            at=1_700_000_000.0,
        )
    assert exc.value.code == T.BAD_INPUT
    assert ledger.journals() == ()
