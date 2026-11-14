"""Shared fixtures for the P5-02 paper-trading readiness tests.

Hermetic: an injected synthetic feed and trusted clock. No exchange API, no
credential, no network and no live order is touched.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from voicebridge.business import trading as T


class Clock:
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)


def default_strategy(**overrides) -> T.StrategySpec:
    values = {
        "strategy_id": "paper-sma",
        "hypothesis": "A short SMA cross above a long SMA continues.",
        "universe": ("BTCUSD",),
        "timeframe": "1d",
        "entry": "close_above_sma",
        "exit": "close_below_sma",
        "fees_bps": 10,
        "slippage_bps": 5,
        "risk": {"max_leverage": "1"},
        "version": "1.0.0",
        "status": "paper",
        "params": {"short_window": 2, "long_window": 3},
    }
    values.update(overrides)
    return T.StrategySpec(**values)


def default_policy(**overrides) -> T.RiskPolicy:
    values = {
        "policy_version": "paper-test-v1",
        "max_order_notional_minor": 500_000,
        "max_leverage": Decimal("1"),
        "max_concentration_pct": 50,
        "min_liquidity_multiple": 2,
        "max_stale_seconds": 5,
        "max_drawdown_pct": Decimal("20"),
        "max_daily_loss_minor": 200_000,
        "allowed_symbols": ("BTCUSD",),
        "allow_short": False,
    }
    values.update(overrides)
    return T.RiskPolicy(**values)


def make_snapshot(
    clock: Clock,
    *,
    mark: str = "100",
    taken_at: float | None = None,
    source: str = "synthetic-paper-feed",
    liquidity: int = 100_000_000,
    bid: str | None = None,
    ask: str | None = None,
) -> T.MarketSnapshot:
    value = Decimal(mark)
    return T.MarketSnapshot(
        taken_at=clock.t if taken_at is None else taken_at,
        source=source,
        marks={"BTCUSD": value},
        bids={"BTCUSD": Decimal(bid) if bid is not None else value - Decimal("0.1")},
        asks={"BTCUSD": Decimal(ask) if ask is not None else value + Decimal("0.1")},
        liquidity_notional_minor={"BTCUSD": liquidity},
    )


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def snapshot_factory(clock):
    def make(**kwargs):
        return make_snapshot(clock, **kwargs)

    return make


@pytest.fixture
def market_factory(clock):
    def make(*, fixed: T.MarketSnapshot | None = None, max_stale: float = 5.0, **kwargs):
        if fixed is not None:
            feed = lambda: fixed  # noqa: E731
        else:
            feed = lambda: make_snapshot(clock, **kwargs)  # noqa: E731
        return T.MarketDataService(feed, clock, max_stale_seconds=max_stale)

    return make


@pytest.fixture
def strategy() -> T.StrategySpec:
    return default_strategy()


@pytest.fixture
def policy() -> T.RiskPolicy:
    return default_policy()


@pytest.fixture
def system_factory(clock, market_factory):
    def make(
        *,
        spec: T.StrategySpec | None = None,
        risk_policy: T.RiskPolicy | None = None,
        market: T.MarketDataService | None = None,
        broker: T.PaperBroker | None = None,
        starting_cash_minor: int = 10_000_000,
        mode: T.TradingMode = T.TradingMode.PAPER,
        signoff: T.OwnerRiskPolicySignoff | None = None,
        exchange_keys=(),
        kill_switch: T.KillSwitch | None = None,
    ) -> T.TradingSystem:
        return T.TradingSystem(
            mode=mode,
            clock=clock,
            market=market or market_factory(),
            strategies=T.StrategyRegistry([spec or default_strategy()]),
            risk_policy=risk_policy or default_policy(),
            broker=broker
            or T.PaperBroker(T.PaperBrokerConfig(fee_bps=10, slippage_bps=5)),
            starting_cash_minor=starting_cash_minor,
            signoff=signoff,
            exchange_keys=exchange_keys,
            kill_switch=kill_switch,
        )

    return make


@pytest.fixture
def execution() -> T.Actor:
    return T.Actor("desk-execution", T.Role.EXECUTION)


@pytest.fixture
def risk_manager() -> T.Actor:
    return T.Actor("risk-manager", T.Role.RISK_MANAGER)


@pytest.fixture
def auditor() -> T.Actor:
    return T.Actor("independent-auditor", T.Role.AUDITOR)


@pytest.fixture
def owner() -> T.Actor:
    return T.Actor("owner", T.Role.OWNER)


def order(
    client_order_id: str,
    *,
    side: str = "buy",
    quantity: str = "2",
    symbol: str = "BTCUSD",
    strategy_id: str = "paper-sma",
    order_type: str = "market",
    limit_price: str | None = None,
) -> T.OrderRequest:
    return T.OrderRequest(
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,
        quantity=Decimal(quantity),
        strategy_id=strategy_id,
        order_type=order_type,
        limit_price=None if limit_price is None else Decimal(limit_price),
    )
