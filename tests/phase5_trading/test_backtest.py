"""P5-02 — deterministic, no-look-ahead backtesting with costs and regimes."""
from __future__ import annotations

from decimal import Decimal

import pytest

from voicebridge.business import trading as T


def _bars() -> list[T.MarketBar]:
    closes = [
        100, 101, 102, 103, 105, 106, 104, 102, 99, 97,
        98, 100, 103, 106, 108, 109, 107, 104, 101, 99,
    ]
    regimes = [
        "bull", "bull", "bull", "bull", "bull", "bull", "bear", "bear",
        "bear", "bear", "range", "range", "range", "bull", "bull", "bull",
        "bear", "bear", "bear", "bear",
    ]
    out = []
    for index, (close, regime) in enumerate(zip(closes, regimes)):
        value = Decimal(close)
        out.append(
            T.MarketBar(
                symbol="BTCUSD",
                ts=1_700_000_000.0 + index * 86_400,
                open=value - Decimal("0.2"),
                high=value + Decimal("1"),
                low=value - Decimal("1"),
                close=value,
                volume=Decimal("100"),
                regime=regime,
            )
        )
    return out


def test_backtest_is_deterministic_and_never_looks_ahead(strategy):
    engine = T.BacktestEngine()
    first = engine.run(_bars(), strategy)
    second = engine.run(_bars(), strategy)

    assert first.digest == second.digest
    assert first.to_dict()["trades"] == second.to_dict()["trades"]
    assert first.trades
    for trade in first.trades:
        assert trade.execute_index == trade.signal_index + 1
        assert trade.execute_ts > trade.signal_ts
        source = _bars()[trade.signal_index]
        target = _bars()[trade.execute_index]
        assert trade.signal_ts == source.ts
        assert trade.execute_ts == target.ts


def test_execution_price_is_the_next_open_with_slippage(strategy):
    result = T.BacktestEngine().run(_bars(), strategy)
    bars = _bars()
    slip = Decimal(strategy.slippage_bps) / Decimal("10000")
    for trade in result.trades:
        expected = bars[trade.execute_index].open
        if trade.side == "buy":
            expected = expected * (Decimal("1") + slip)
        else:
            expected = expected * (Decimal("1") - slip)
        assert trade.price == expected


def test_fees_and_slippage_reduce_equity(strategy):
    free = T.StrategySpec(
        strategy_id=strategy.strategy_id,
        hypothesis=strategy.hypothesis,
        universe=strategy.universe,
        timeframe=strategy.timeframe,
        entry=strategy.entry,
        exit=strategy.exit,
        fees_bps=0,
        slippage_bps=0,
        risk=strategy.risk,
        version=strategy.version,
        status=strategy.status,
        params=strategy.params,
    )
    engine = T.BacktestEngine()
    with_costs = engine.run(_bars(), strategy)
    without = engine.run(_bars(), free)

    assert with_costs.metrics.fees_minor > 0
    assert with_costs.ending_equity_minor < without.ending_equity_minor
    assert with_costs.metrics.net_pnl_minor == sum(
        trade.pnl_minor for trade in with_costs.trades if trade.side == "sell"
    )


def test_in_sample_out_of_sample_and_multiple_regimes(strategy):
    result = T.BacktestEngine().run(_bars(), strategy)
    names = {segment.name for segment in result.segments}
    assert names == {"in_sample", "out_of_sample"}
    assert result.split_index == int(len(_bars()) * 0.7)
    assert {"bear", "bull", "range"} <= set(result.regimes)
    assert all(metric.trades >= 0 for metric in result.regimes.values())


def test_unknown_rule_and_unsorted_bars_are_refused(strategy):
    bad = T.StrategySpec(
        strategy_id="bad",
        hypothesis="bad rule",
        universe=("BTCUSD",),
        timeframe="1d",
        entry="fabricated_signal",
        exit="close_below_sma",
        fees_bps=0,
        slippage_bps=0,
        risk={},
        version="1.0.0",
        status="draft",
    )
    with pytest.raises(T.TradingError) as exc:
        T.BacktestEngine().run(_bars(), bad)
    assert exc.value.code == T.BAD_INPUT

    bars = list(_bars())
    bars[3] = T.MarketBar(
        symbol="BTCUSD",
        ts=bars[2].ts,
        open=bars[3].open,
        high=bars[3].high,
        low=bars[3].low,
        close=bars[3].close,
        regime=bars[3].regime,
    )
    with pytest.raises(T.TradingError) as exc:
        T.BacktestEngine().run(bars, strategy)
    assert exc.value.code == T.BAD_INPUT


def test_backtest_rejects_short_series_and_foreign_symbol(strategy):
    with pytest.raises(T.TradingError):
        T.BacktestEngine().run(_bars()[:3], strategy)
    other = [
        T.MarketBar(
            symbol="ETHUSD",
            ts=bar.ts,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            regime=bar.regime,
        )
        for bar in _bars()
    ]
    with pytest.raises(T.TradingError):
        T.BacktestEngine().run(other, strategy)
