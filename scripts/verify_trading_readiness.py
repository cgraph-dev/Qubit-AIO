#!/usr/bin/env python3
"""Independent P5-02 trading-readiness replay for the Qubit Office.

WHY THIS EXISTS
---------------
A readiness claim is not its own witness. This script builds a throwaway paper
scenario from the real :mod:`voicebridge.business.trading` surface and then
re-checks the properties the trading department plan requires, from the
observable records rather than from a status flag:

* a deterministic backtest (two runs, one digest) with no look-ahead (every
  fill executes on the bar AFTER its signal) and fees/slippage applied;
* in-sample/out-of-sample split and multiple market regimes;
* typed risk vetoes (notional, concentration, liquidity, short) that stop the
  order before any fill;
* paper fills with fees and slippage, plus client-order idempotency;
* a balanced double-entry ledger and a fill/event/receipt audit trail;
* an ``unknown`` execution outcome that stays uncompleted until reconciled;
* a typed live-mode refusal without an owner sign-off;
* a kill switch that cancels open orders and invokes injected lease/key
  revocation callbacks;
* role separation: execution cannot edit risk limits, risk/audit cannot submit.

There is no exchange API, no credential, no network and no live order here. The
broker is the deterministic paper simulator.

``--json`` emits a machine report. Exit 0 = every check passed, 1 = a check
failed, 2 = usage/internal error.
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voicebridge.business import trading as T  # noqa: E402

SCHEMA = "qubit-office-trading-readiness/1"


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


class _Clock:
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)


def _snapshot(clock: _Clock, *, marks: dict[str, str] | None = None) -> T.MarketSnapshot:
    mark = Decimal((marks or {}).get("BTCUSD", "100"))
    return T.MarketSnapshot(
        taken_at=clock.t,
        source="synthetic-paper-feed",
        marks={"BTCUSD": mark},
        bids={"BTCUSD": mark - Decimal("0.1")},
        asks={"BTCUSD": mark + Decimal("0.1")},
        liquidity_notional_minor={"BTCUSD": 100_000_000},
    )


def _market(clock: _Clock) -> T.MarketDataService:
    return T.MarketDataService(
        lambda: _snapshot(clock), clock, max_stale_seconds=5
    )


def _strategy() -> T.StrategySpec:
    return T.StrategySpec(
        strategy_id="paper-sma",
        hypothesis="A short SMA cross above a long SMA continues for a bar.",
        universe=("BTCUSD",),
        timeframe="1d",
        entry="close_above_sma",
        exit="close_below_sma",
        fees_bps=10,
        slippage_bps=5,
        risk={"max_leverage": "1"},
        version="1.0.0",
        status="paper",
        params={"short_window": 2, "long_window": 3},
    )


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
    bars: list[T.MarketBar] = []
    for index, (close, regime) in enumerate(zip(closes, regimes)):
        value = Decimal(close)
        bars.append(
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
    return bars


def _policy() -> T.RiskPolicy:
    return T.RiskPolicy(
        policy_version="paper-readiness-v1",
        max_order_notional_minor=500_000,
        max_leverage=Decimal("1"),
        max_concentration_pct=50,
        min_liquidity_multiple=2,
        max_stale_seconds=5,
        max_drawdown_pct=Decimal("20"),
        max_daily_loss_minor=200_000,
        allowed_symbols=("BTCUSD",),
        allow_short=False,
    )


def _system(
    clock: _Clock,
    *,
    broker: T.PaperBroker | None = None,
    kill_switch: T.KillSwitch | None = None,
) -> T.TradingSystem:
    return T.TradingSystem(
        mode=T.TradingMode.PAPER,
        clock=clock,
        market=_market(clock),
        strategies=T.StrategyRegistry([_strategy()]),
        risk_policy=_policy(),
        broker=broker
        or T.PaperBroker(T.PaperBrokerConfig(fee_bps=10, slippage_bps=5)),
        starting_cash_minor=10_000_000,
        kill_switch=kill_switch,
    )


def _order(
    client_order_id: str,
    *,
    side: str = "buy",
    quantity: str = "2",
    symbol: str = "BTCUSD",
    strategy_id: str = "paper-sma",
) -> T.OrderRequest:
    return T.OrderRequest(
        client_order_id=client_order_id,
        symbol=symbol,
        side=side,
        quantity=Decimal(quantity),
        strategy_id=strategy_id,
    )


def _backtest_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    engine = T.BacktestEngine()
    spec = _strategy()
    first = engine.run(_bars(), spec)
    second = engine.run(_bars(), spec)
    checks.append(
        _check(
            "backtest_deterministic",
            first.digest == second.digest
            and first.to_dict()["trades"] == second.to_dict()["trades"],
            f"digest={first.digest[:16]}",
        )
    )
    checks.append(
        _check(
            "no_lookahead",
            bool(first.trades)
            and all(
                trade.execute_index == trade.signal_index + 1
                for trade in first.trades
            ),
            f"trades={len(first.trades)}"
            " signal_index+1==execute_index for all",
        )
    )
    sides = {trade.side for trade in first.trades}
    checks.append(
        _check(
            "backtest_round_trip",
            {"buy", "sell"} <= sides,
            f"sides={sorted(sides)} trades={len(first.trades)}",
        )
    )
    zero_cost = T.BacktestEngine().run(
        _bars(),
        T.StrategySpec(
            strategy_id=spec.strategy_id,
            hypothesis=spec.hypothesis,
            universe=spec.universe,
            timeframe=spec.timeframe,
            entry=spec.entry,
            exit=spec.exit,
            fees_bps=0,
            slippage_bps=0,
            risk=spec.risk,
            version=spec.version,
            status=spec.status,
            params=spec.params,
        ),
    )
    checks.append(
        _check(
            "fees_and_slippage_apply",
            first.metrics.fees_minor > 0
            and first.ending_equity_minor < zero_cost.ending_equity_minor,
            f"with_costs={first.ending_equity_minor}"
            f" zero_cost={zero_cost.ending_equity_minor}"
            f" fees={first.metrics.fees_minor}",
        )
    )
    segment_names = {segment.name for segment in first.segments}
    checks.append(
        _check(
            "walk_forward_split",
            segment_names == {"in_sample", "out_of_sample"},
            f"segments={sorted(segment_names)} split={first.split_index}",
        )
    )
    checks.append(
        _check(
            "multiple_regimes",
            len(first.regimes) >= 3,
            f"regimes={sorted(first.regimes)}",
        )
    )
    return checks


def _execution_checks() -> tuple[list[dict[str, Any]], T.TradingSystem]:
    checks: list[dict[str, Any]] = []
    clock = _Clock()
    system = _system(clock)
    execution = T.Actor("desk-execution", T.Role.EXECUTION)

    normal = system.submit_order(execution, _order("paper-1"))
    checks.append(
        _check(
            "paper_fill",
            normal.order.status == "filled"
            and len(normal.fills) == 1
            and normal.fills[0].fee_minor > 0,
            f"status={normal.order.status}"
            f" filled={normal.order.filled_quantity}"
            f" fee={normal.fills[0].fee_minor if normal.fills else None}",
        )
    )
    checks.append(
        _check(
            "execution_receipt",
            normal.receipt is not None
            and normal.receipt.status == "filled"
            and any(
                r.event_type == "broker.receipt"
                for r in system.events.records()
            ),
            f"receipt={None if normal.receipt is None else normal.receipt.status}",
        )
    )
    replay = system.submit_order(execution, _order("paper-1"))
    checks.append(
        _check(
            "order_idempotency",
            replay.created is False
            and replay.order.order_id == normal.order.order_id
            and len(system.oms.fills_for(normal.order.order_id)) == 1,
            f"created={replay.created}"
            f" fills={len(system.oms.fills_for(normal.order.order_id))}",
        )
    )
    veto = system.submit_order(execution, _order("too-big", quantity="100000"))
    checks.append(
        _check(
            "risk_veto_notional",
            veto.risk is not None
            and not veto.risk.allowed
            and "max_order_notional" in veto.risk.reasons
            and veto.order.status == "rejected"
            and not veto.fills,
            f"reasons={None if veto.risk is None else veto.risk.reasons}"
            f" status={veto.order.status}",
        )
    )
    short = system.submit_order(execution, _order("short", side="sell", quantity="3"))
    checks.append(
        _check(
            "risk_veto_short",
            short.risk is not None
            and not short.risk.allowed
            and "no_short" in short.risk.reasons,
            f"reasons={None if short.risk is None else short.risk.reasons}",
        )
    )
    tiny_liquidity = T.MarketSnapshot(
        taken_at=clock.t,
        source="synthetic-thin-book",
        marks={"BTCUSD": Decimal("100")},
        bids={"BTCUSD": Decimal("99.9")},
        asks={"BTCUSD": Decimal("100.1")},
        liquidity_notional_minor={"BTCUSD": 100},
    )
    thin_market = T.MarketDataService(
        lambda: tiny_liquidity, clock, max_stale_seconds=5
    )
    thin_system = T.TradingSystem(
        mode=T.TradingMode.PAPER,
        clock=clock,
        market=thin_market,
        strategies=T.StrategyRegistry([_strategy()]),
        risk_policy=_policy(),
        broker=T.PaperBroker(T.PaperBrokerConfig(fee_bps=10)),
        starting_cash_minor=10_000_000,
    )
    thin = thin_system.submit_order(execution, _order("thin"))
    checks.append(
        _check(
            "risk_veto_liquidity",
            thin.risk is not None
            and not thin.risk.allowed
            and "liquidity" in thin.risk.reasons,
            f"reasons={None if thin.risk is None else thin.risk.reasons}",
        )
    )
    concentration = system.submit_order(
        execution, _order("concentrated", quantity="30000")
    )
    checks.append(
        _check(
            "risk_veto_concentration_or_notional",
            concentration.risk is not None and not concentration.risk.allowed,
            f"reasons={None if concentration.risk is None else concentration.risk.reasons}",
        )
    )
    checks.append(
        _check(
            "risk_vetoes_have_no_fills",
            all(
                not report.fills
                for report in (veto, short, thin, concentration)
            ),
            "every vetoed order has zero fills",
        )
    )
    return checks, system


def _ledger_checks(system: T.TradingSystem) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    journals = system.ledger.journals()
    balanced_journals = all(
        journal.debit_minor == journal.credit_minor for journal in journals
    )
    checks.append(
        _check(
            "double_entry_balanced",
            balanced_journals
            and system.ledger.is_balanced()
            and system.ledger.total_minor() == 0,
            f"journals={len(journals)}"
            f" net={system.ledger.total_minor()}",
        )
    )
    position = system.portfolio.position("BTCUSD")
    checks.append(
        _check(
            "position_cost_basis",
            position.quantity == Decimal("2")
            and position.cost_basis_minor > 0,
            f"quantity={position.quantity}"
            f" cost_basis={position.cost_basis_minor}",
        )
    )
    audit = system.audit()
    checks.append(
        _check(
            "audit_event_and_ledger_backed",
            audit["ok"]
            and audit["fills_ledger_backed"]
            and audit["orders_event_backed"]
            and audit["broker_receipts_present"],
            json.dumps(
                {
                    "ok": audit["ok"],
                    "fills": audit["fill_count"],
                    "receipts": audit["broker_receipts_present"],
                },
                sort_keys=True,
            ),
        )
    )
    checks.append(
        _check(
            "ledger_cash_reconciles",
            system.portfolio.cash_minor
            == system.ledger.balance("cash"),
            f"cash={system.portfolio.cash_minor}"
            f" ledger={system.ledger.balance('cash')}",
        )
    )
    return checks


def _unknown_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    clock = _Clock()
    broker = T.PaperBroker(
        T.PaperBrokerConfig(fee_bps=10, slippage_bps=5, unknown_once=True)
    )
    system = _system(clock, broker=broker)
    execution = T.Actor("desk-execution", T.Role.EXECUTION)
    report = system.submit_order(execution, _order("unknown-1"))
    checks.append(
        _check(
            "unknown_not_completed",
            report.order.status == "unknown"
            and not report.fills
            and system.portfolio.position("BTCUSD").quantity == 0,
            f"status={report.order.status}"
            f" fills={len(report.fills)}"
            f" position={system.portfolio.position('BTCUSD').quantity}",
        )
    )

    def resolver(order: T.Order) -> dict[str, Any]:
        return {
            "status": "filled",
            "fills": [
                {"quantity": str(order.quantity), "price": "100.25", "fee_minor": 10}
            ],
            "reason": "venue ledger shows the fill",
        }

    reconciled = system.reconcile_unknown(
        report.order.order_id, resolver, actor=execution
    )
    checks.append(
        _check(
            "unknown_reconciled",
            reconciled.order.status == "filled"
            and len(reconciled.fills) == 1
            and system.portfolio.position("BTCUSD").quantity
            == reconciled.order.quantity
            and system.ledger.is_balanced()
            and system.audit()["ok"],
            f"status={reconciled.order.status}"
            f" fills={len(reconciled.fills)}"
            f" balanced={system.ledger.is_balanced()}",
        )
    )
    return checks


def _live_refusal_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    clock = _Clock()
    policy = _policy()
    base = dict(
        clock=clock,
        market=_market(clock),
        strategies=T.StrategyRegistry([_strategy()]),
        risk_policy=policy,
    )
    try:
        T.TradingSystem(mode=T.TradingMode.LIVE, **base)
        refused = False
        code = "no-refusal"
    except T.TradingError as exc:
        refused = True
        code = exc.code
    checks.append(
        _check(
            "live_refused_without_signoff",
            refused and code == T.LIVE_NOT_AUTHORIZED,
            f"code={code}",
        )
    )
    wrong = T.OwnerRiskPolicySignoff(
        owner_id="owner-1",
        policy_version=policy.policy_version,
        limits={"max_leverage": "99"},
        signed_at=clock.t,
    )
    try:
        T.TradingSystem(mode=T.TradingMode.LIVE, signoff=wrong, **base)
        refused = False
        code = "no-refusal"
    except T.TradingError as exc:
        refused = True
        code = exc.code
    checks.append(
        _check(
            "live_refused_on_policy_mismatch",
            refused and code == T.LIVE_NOT_AUTHORIZED,
            f"code={code}",
        )
    )
    try:
        T.TradingSystem(
            mode=T.TradingMode.PAPER,
            exchange_keys=(
                T.ExchangeKeyPolicy(
                    key_id="bad",
                    venue="paper",
                    trade_only=False,
                    withdrawals_enabled=True,
                    scopes=("trade", "withdraw"),
                ),
            ),
            **base,
        )
        refused = False
        code = "no-refusal"
    except T.TradingError as exc:
        refused = True
        code = exc.code
    checks.append(
        _check(
            "withdrawal_key_refused",
            refused and code == T.KEY_WITHDRAWAL_REFUSED,
            f"code={code}",
        )
    )
    return checks


def _role_and_kill_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    clock = _Clock()
    broker = T.PaperBroker(
        T.PaperBrokerConfig(fee_bps=10, slippage_bps=5, fill_ratio=Decimal("0.5"))
    )
    calls = {"leases": 0, "keys": 0, "strategies": 0}

    def revoke_leases() -> int:
        calls["leases"] += 1
        return 3

    def revoke_keys() -> int:
        calls["keys"] += 1
        return 1

    def freeze_strategies() -> int:
        calls["strategies"] += 1
        return 1

    kill_switch = T.KillSwitch(
        revoke_leases=revoke_leases,
        revoke_keys=revoke_keys,
        freeze_strategies=freeze_strategies,
    )
    system = _system(clock, broker=broker, kill_switch=kill_switch)
    execution = T.Actor("desk-execution", T.Role.EXECUTION)
    risk = T.Actor("risk-manager", T.Role.RISK_MANAGER)
    auditor = T.Actor("auditor", T.Role.AUDITOR)
    partial = system.submit_order(execution, _order("partial-1", quantity="10"))
    checks.append(
        _check(
            "partial_fill",
            partial.order.status == "partially_filled"
            and partial.order.filled_quantity == Decimal("5")
            and partial.order.remaining == Decimal("5"),
            f"status={partial.order.status}"
            f" filled={partial.order.filled_quantity}",
        )
    )
    try:
        system.set_risk_policy(
            T.RiskPolicy(
                policy_version="exec-edit",
                max_order_notional_minor=10**12,
                max_leverage=Decimal("100"),
                max_concentration_pct=100,
                min_liquidity_multiple=1,
                max_stale_seconds=60,
                max_drawdown_pct=Decimal("100"),
                max_daily_loss_minor=10**12,
            ),
            actor=execution,
        )
        refused = False
        code = "no-refusal"
    except T.TradingError as exc:
        refused = True
        code = exc.code
    checks.append(
        _check(
            "execution_cannot_edit_limits",
            refused and code == T.RISK_LIMIT_IMMUTABLE,
            f"code={code}",
        )
    )
    for name, actor in (("risk", risk), ("auditor", auditor)):
        try:
            system.submit_order(actor, _order(f"role-{name}"))
            refused = False
            code = "no-refusal"
        except T.TradingError as exc:
            refused = True
            code = exc.code
        checks.append(
            _check(
                f"{name}_cannot_submit",
                refused and code == T.ROLE_REFUSED,
                f"code={code}",
            )
        )
    try:
        system.kill_switch.engage(
            system, actor=execution, reason="rogue execution"
        )
        refused = False
        code = "no-refusal"
    except T.TradingError as exc:
        refused = True
        code = exc.code
    checks.append(
        _check(
            "execution_cannot_engage_kill",
            refused and code == T.ROLE_REFUSED,
            f"code={code}",
        )
    )
    report = system.kill_switch.engage(system, actor=risk, reason="readiness drill")
    checks.append(
        _check(
            "kill_switch_cancels_all",
            report.engaged
            and report.cancelled_orders == 1
            and system.oms.get(partial.order.order_id).status == "cancelled",
            f"cancelled={report.cancelled_orders}"
            f" status={system.oms.get(partial.order.order_id).status}",
        )
    )
    checks.append(
        _check(
            "kill_switch_revokes_injected",
            report.revoked_leases == 3
            and report.revoked_keys == 1
            and report.frozen_strategies == 1
            and calls == {"leases": 1, "keys": 1, "strategies": 1},
            f"report={report.to_dict()}",
        )
    )
    try:
        system.submit_order(execution, _order("after-kill"))
        refused = False
        code = "no-refusal"
    except T.TradingError as exc:
        refused = True
        code = exc.code
    checks.append(
        _check(
            "frozen_after_kill",
            refused and code == T.KILL_SWITCH_ENGAGED,
            f"code={code}",
        )
    )
    return checks


def _stale_checks() -> list[dict[str, Any]]:
    clock = _Clock()
    frozen = _snapshot(clock)
    stale_market = T.MarketDataService(
        lambda: frozen, clock, max_stale_seconds=5
    )
    system = T.TradingSystem(
        mode=T.TradingMode.PAPER,
        clock=clock,
        market=stale_market,
        strategies=T.StrategyRegistry([_strategy()]),
        risk_policy=_policy(),
        broker=T.PaperBroker(T.PaperBrokerConfig(fee_bps=10)),
        starting_cash_minor=10_000_000,
    )
    execution = T.Actor("desk-execution", T.Role.EXECUTION)
    clock.advance(6)
    try:
        system.submit_order(execution, _order("stale"))
        refused = False
        code = "no-refusal"
    except T.TradingError as exc:
        refused = True
        code = exc.code
    return [
        _check(
            "stale_data_refused",
            refused and code == T.STALE_DATA,
            f"code={code}",
        )
    ]


def run() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.extend(_backtest_checks())
    execution_checks, system = _execution_checks()
    checks.extend(execution_checks)
    checks.extend(_ledger_checks(system))
    checks.extend(_unknown_checks())
    checks.extend(_live_refusal_checks())
    checks.extend(_role_and_kill_checks())
    checks.extend(_stale_checks())
    failed = [c for c in checks if not c["ok"]]
    return {
        "schema": SCHEMA,
        "ok": not failed,
        "checks_total": len(checks),
        "checks_failed": len(failed),
        "checks": checks,
        "mode": "paper",
        "live_capital_touched": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay the P5-02 paper-trading readiness contract."
    )
    parser.add_argument(
        "--json", action="store_true", help="emit a machine-readable report"
    )
    args = parser.parse_args(argv)
    try:
        report = run()
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        if args.json:
            print(json.dumps({"schema": SCHEMA, "ok": False, "error": str(exc)}))
        else:
            print(f"trading readiness replay failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for check in report["checks"]:
            mark = "PASS" if check["ok"] else "FAIL"
            print(f"{mark} {check['name']}: {check['detail']}")
        print(
            f"{report['checks_total'] - report['checks_failed']}"
            f"/{report['checks_total']} checks passed"
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
