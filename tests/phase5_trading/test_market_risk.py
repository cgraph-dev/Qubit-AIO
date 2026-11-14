"""P5-02 — trusted-clock market data and pre-trade/portfolio risk vetoes."""
from __future__ import annotations

from decimal import Decimal

import pytest

from voicebridge.business import trading as T


def _order(
    *,
    symbol: str = "BTCUSD",
    side: str = "buy",
    quantity: str = "2",
    client_order_id: str = "risk-1",
) -> T.OrderRequest:
    return T.OrderRequest(
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,
        quantity=Decimal(quantity),
        strategy_id="paper-sma",
    )


def _snapshot(
    clock, *, mark: str = "100", symbols: tuple[str, ...] = ("BTCUSD",),
    liquidity: int = 100_000_000
) -> T.MarketSnapshot:
    value = Decimal(mark)
    return T.MarketSnapshot(
        taken_at=clock.t,
        source="synthetic",
        marks={symbol: value for symbol in symbols},
        bids={symbol: value - Decimal("0.1") for symbol in symbols},
        asks={symbol: value + Decimal("0.1") for symbol in symbols},
        liquidity_notional_minor={
            symbol: liquidity for symbol in symbols
        },
    )


def test_stale_snapshot_is_refused(clock, snapshot_factory, market_factory):
    frozen = snapshot_factory()
    market = market_factory(fixed=frozen, max_stale=5)
    clock.advance(6)
    with pytest.raises(T.TradingError) as exc:
        market.snapshot(["BTCUSD"])
    assert exc.value.code == T.STALE_DATA


def test_future_snapshot_is_refused(clock, snapshot_factory, market_factory):
    future = snapshot_factory(taken_at=clock.t + 60)
    market = market_factory(fixed=future)
    with pytest.raises(T.TradingError) as exc:
        market.snapshot()
    assert exc.value.code == T.STALE_DATA


def test_missing_symbol_is_refused(clock, snapshot_factory, market_factory):
    market = market_factory(fixed=snapshot_factory())
    with pytest.raises(T.TradingError) as exc:
        market.snapshot(["ETHUSD"])
    assert exc.value.code == T.STALE_DATA


def test_veto_notional_concentration_and_liquidity(
    clock, system_factory, snapshot_factory, market_factory, execution, policy
):
    system = system_factory()
    too_big = system.submit_order(execution, _order(quantity="100000"))
    assert too_big.risk is not None and not too_big.risk.allowed
    assert "max_order_notional" in too_big.risk.reasons
    assert too_big.order.status == "rejected"
    assert not too_big.fills

    thin = T.TradingSystem(
        clock=clock,
        market=market_factory(fixed=snapshot_factory(liquidity=100)),
        strategies=T.StrategyRegistry(
            [
                T.StrategySpec(
                    strategy_id="paper-sma",
                    hypothesis="test",
                    universe=("BTCUSD",),
                    timeframe="1d",
                    entry="close_above_sma",
                    exit="close_below_sma",
                    fees_bps=10,
                    slippage_bps=5,
                    risk={},
                    version="1.0.0",
                    status="paper",
                )
            ]
        ),
        risk_policy=policy,
        broker=T.PaperBroker(T.PaperBrokerConfig(fee_bps=10)),
    )
    thin_report = thin.submit_order(execution, _order(client_order_id="thin"))
    assert thin_report.risk is not None
    assert "liquidity" in thin_report.risk.reasons
    assert not thin_report.fills

    concentrated = system.submit_order(
        execution, _order(client_order_id="big", quantity="30000")
    )
    assert concentrated.risk is not None and not concentrated.risk.allowed
    assert {"max_order_notional", "concentration"} <= set(
        concentrated.risk.reasons
    )


def test_veto_symbol_allowlist_and_short(
    clock, system_factory, policy, execution
):
    system = system_factory(
        risk_policy=T.RiskPolicy(
            policy_version="narrow",
            max_order_notional_minor=500_000,
            max_leverage=Decimal("2"),
            max_concentration_pct=50,
            min_liquidity_multiple=1,
            max_stale_seconds=5,
            max_drawdown_pct=Decimal("50"),
            max_daily_loss_minor=500_000,
            allowed_symbols=("BTCUSD",),
        )
    )
    # Selling more than the (zero) holding is refused by the no-short rule.
    short = system.submit_order(
        execution, _order(client_order_id="short", side="sell", quantity="1")
    )
    assert short.risk is not None
    assert "no_short" in short.risk.reasons
    assert not short.fills


def test_portfolio_drawdown_and_daily_loss_veto(clock):
    ledger = T.DoubleEntryLedger()
    portfolio = T.Portfolio(ledger)
    now = clock.t
    portfolio.fund(1_000_000, at=now)
    high = T.MarketSnapshot(
        taken_at=now,
        source="synthetic",
        marks={"BTCUSD": Decimal("100")},
        bids={"BTCUSD": Decimal("100")},
        asks={"BTCUSD": Decimal("100")},
        liquidity_notional_minor={"BTCUSD": 100_000_000},
    )
    portfolio.observe_equity(now, high.marks)
    portfolio.apply_fill(
        symbol="BTCUSD",
        side="buy",
        quantity=Decimal("100"),
        price=Decimal("100"),
        fee_minor=0,
        order_id="ord-1",
        fill_id="fill-1",
        at=now,
        event_seq=None,
    )
    low = T.MarketSnapshot(
        taken_at=now,
        source="synthetic",
        marks={"BTCUSD": Decimal("80")},
        bids={"BTCUSD": Decimal("80")},
        asks={"BTCUSD": Decimal("80")},
        liquidity_notional_minor={"BTCUSD": 100_000_000},
    )
    policy = T.RiskPolicy(
        policy_version="tight",
        max_order_notional_minor=500_000,
        max_leverage=Decimal("10"),
        max_concentration_pct=100,
        min_liquidity_multiple=1,
        max_stale_seconds=5,
        max_drawdown_pct=Decimal("5"),
        max_daily_loss_minor=50_000,
    )
    decision = T.RiskEngine(policy).evaluate_order(
        request=_order(quantity="1"),
        snapshot=low,
        portfolio=portfolio,
        now=now,
    )
    assert not decision.allowed
    assert {"drawdown", "daily_loss"} <= set(decision.reasons)


def test_leverage_veto_is_independent_of_notional(clock):
    ledger = T.DoubleEntryLedger()
    portfolio = T.Portfolio(ledger)
    portfolio.fund(1_000_000, at=clock.t)
    snapshot = _snapshot(clock)
    policy = T.RiskPolicy(
        policy_version="half-leverage",
        max_order_notional_minor=1_000_000,
        max_leverage=Decimal("0.5"),
        max_concentration_pct=100,
        min_liquidity_multiple=1,
        max_stale_seconds=5,
        max_drawdown_pct=Decimal("50"),
        max_daily_loss_minor=500_000,
    )
    decision = T.RiskEngine(policy).evaluate_order(
        request=_order(quantity="60"),
        snapshot=snapshot,
        portfolio=portfolio,
        now=clock.t,
    )
    assert not decision.allowed
    assert "max_leverage" in decision.reasons
    assert "max_order_notional" not in decision.reasons


def test_risk_engine_refuses_foreign_symbol(clock):
    snapshot = _snapshot(clock, symbols=("BTCUSD", "ETHUSD"))
    ledger = T.DoubleEntryLedger()
    portfolio = T.Portfolio(ledger)
    portfolio.fund(1_000_000, at=clock.t)
    policy = T.RiskPolicy(
        policy_version="btc-only",
        max_order_notional_minor=500_000,
        max_leverage=Decimal("2"),
        max_concentration_pct=100,
        min_liquidity_multiple=1,
        max_stale_seconds=5,
        max_drawdown_pct=Decimal("50"),
        max_daily_loss_minor=500_000,
        allowed_symbols=("BTCUSD",),
    )
    decision = T.RiskEngine(policy).evaluate_order(
        request=_order(symbol="ETHUSD"),
        snapshot=snapshot,
        portfolio=portfolio,
        now=clock.t,
    )
    assert not decision.allowed
    assert "symbol_allowlist" in decision.reasons
