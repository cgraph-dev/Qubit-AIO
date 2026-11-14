"""P5-02 — paper broker fills, OMS idempotency and unknown reconciliation."""
from __future__ import annotations

from decimal import Decimal

import pytest

from voicebridge.business import trading as T


def _order(
    client_order_id: str,
    *,
    side: str = "buy",
    quantity: str = "2",
    symbol: str = "BTCUSD",
    order_type: str = "market",
    limit_price: str | None = None,
    max_slippage_bps: int | None = None,
) -> T.OrderRequest:
    return T.OrderRequest(
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,
        quantity=Decimal(quantity),
        strategy_id="paper-sma",
        order_type=order_type,
        limit_price=None if limit_price is None else Decimal(limit_price),
        max_slippage_bps=max_slippage_bps,
    )


def test_paper_fill_applies_slippage_and_fee(
    system_factory, execution
):
    system = system_factory()
    report = system.submit_order(execution, _order("fill-1"))
    assert report.created is True
    assert report.order.status == "filled"
    assert len(report.fills) == 1
    fill = report.fills[0]
    # ask 100.1 plus 5 bps slippage
    assert fill.price == Decimal("100.1") * Decimal("1.0005")
    assert fill.fee_minor > 0
    assert report.receipt is not None
    assert report.receipt.status == "filled"
    assert system.portfolio.position("BTCUSD").quantity == Decimal("2")


def test_simulated_latency_is_visible_on_the_receipt(clock, system_factory, execution):
    broker = T.PaperBroker(
        T.PaperBrokerConfig(fee_bps=0, latency_ms=250)
    )
    system = system_factory(broker=broker)
    started = clock.t
    report = system.submit_order(execution, _order("latency"))
    assert report.receipt is not None
    assert report.receipt.received_at == started + 0.25
    assert report.order.status == "filled"


def test_partial_fill_leaves_an_open_remainder(
    clock, system_factory, execution
):
    broker = T.PaperBroker(
        T.PaperBrokerConfig(fee_bps=10, fill_ratio=Decimal("0.5"))
    )
    system = system_factory(broker=broker)
    report = system.submit_order(execution, _order("partial", quantity="10"))
    assert report.order.status == "partially_filled"
    assert report.order.filled_quantity == Decimal("5")
    assert report.order.remaining == Decimal("5")
    assert len(report.fills) == 1

    # A second explicit fill completes the remainder through the OMS.
    fill = system.oms.record_fill(
        report.order.order_id,
        quantity=Decimal("5"),
        price=Decimal("100"),
        fee_minor=0,
        actor=execution,
        at=clock.t,
    )
    system.portfolio.apply_fill(
        symbol=fill.symbol,
        side=fill.side,
        quantity=fill.quantity,
        price=fill.price,
        fee_minor=fill.fee_minor,
        order_id=fill.order_id,
        fill_id=fill.fill_id,
        at=fill.at,
        event_seq=fill.event_seq,
    )
    assert system.oms.get(report.order.order_id).status == "filled"


def test_broker_rejection_changes_nothing(
    system_factory, execution
):
    broker = T.PaperBroker(
        T.PaperBrokerConfig(reject_all=True, reject_reason="venue closed")
    )
    system = system_factory(broker=broker)
    report = system.submit_order(execution, _order("reject-1"))
    assert report.order.status == "rejected"
    assert report.order.reject_reason == "venue closed"
    assert not report.fills
    assert system.portfolio.position("BTCUSD").quantity == 0
    assert system.ledger.balance("cash") == 10_000_000


def test_limit_order_that_is_not_marketable_is_rejected(
    system_factory, execution
):
    system = system_factory()
    report = system.submit_order(
        execution,
        _order("limit-1", order_type="limit", limit_price="90"),
    )
    assert report.order.status == "rejected"
    assert "not marketable" in report.order.reject_reason
    assert not report.fills


def test_request_slippage_cap_rejects(system_factory, execution):
    system = system_factory(
        broker=T.PaperBroker(
            T.PaperBrokerConfig(fee_bps=0, slippage_bps=50)
        )
    )
    report = system.submit_order(
        execution, _order("slip-cap", max_slippage_bps=10)
    )
    assert report.order.status == "rejected"
    assert "slippage" in report.order.reject_reason


def test_client_order_id_is_idempotent_and_body_bound(
    system_factory, execution
):
    system = system_factory()
    first = system.submit_order(execution, _order("same"))
    second = system.submit_order(execution, _order("same"))
    assert first.created is True
    assert second.created is False
    assert second.order.order_id == first.order.order_id
    assert len(system.oms.fills_for(first.order.order_id)) == 1
    assert len(system.oms.list()) == 1

    with pytest.raises(T.TradingError) as exc:
        system.submit_order(execution, _order("same", quantity="3"))
    assert exc.value.code == T.IDEMPOTENCY_CONFLICT


def test_unknown_outcome_is_not_completed_until_reconciled(
    clock, system_factory, execution
):
    broker = T.PaperBroker(
        T.PaperBrokerConfig(fee_bps=10, slippage_bps=5, unknown_once=True)
    )
    system = system_factory(broker=broker)
    report = system.submit_order(execution, _order("unknown-1"))
    assert report.order.status == "unknown"
    assert not report.fills
    assert system.portfolio.position("BTCUSD").quantity == 0
    assert system.ledger.balance("cash") == 10_000_000

    def resolver(order: T.Order):
        return {
            "status": "filled",
            "fills": [
                {
                    "quantity": str(order.quantity),
                    "price": "100.25",
                    "fee_minor": 10,
                }
            ],
            "reason": "venue ledger has the fill",
        }

    reconciled = system.reconcile_unknown(
        report.order.order_id, resolver, actor=execution
    )
    assert reconciled.order.status == "filled"
    assert len(reconciled.fills) == 1
    assert system.portfolio.position("BTCUSD").quantity == Decimal("2")
    assert system.ledger.is_balanced()
    assert system.audit()["ok"]
    assert system.events.of_type("order.reconciled")


def test_unknown_resolution_must_be_terminal(system_factory, execution):
    broker = T.PaperBroker(T.PaperBrokerConfig(unknown_once=True))
    system = system_factory(broker=broker)
    report = system.submit_order(execution, _order("unknown-2"))

    def resolver(order: T.Order):
        return {"status": "still_pending", "fills": [], "reason": "nope"}

    with pytest.raises(T.TradingError) as exc:
        system.reconcile_unknown(
            report.order.order_id, resolver, actor=execution
        )
    assert exc.value.code == T.BAD_INPUT
    assert system.oms.get(report.order.order_id).status == "unknown"

    resolved = system.reconcile_unknown(
        report.order.order_id,
        lambda order: {"status": "cancelled", "fills": [], "reason": "gone"},
        actor=T.Actor("risk", T.Role.RISK_MANAGER),
    )
    assert resolved.order.status == "cancelled"
    assert system.portfolio.position("BTCUSD").quantity == 0


def test_filled_order_cannot_be_cancelled(system_factory, execution):
    system = system_factory()
    report = system.submit_order(execution, _order("done"))
    with pytest.raises(T.TradingError) as exc:
        system.cancel_order(execution, report.order.order_id)
    assert exc.value.code == T.ORDER_STATE
