"""P5-02 — safety gates: live refusal, role separation, keys and kill switch."""
from __future__ import annotations

from decimal import Decimal

import pytest

from voicebridge.business import trading as T


def _order(client_order_id: str = "gate-1", *, quantity: str = "2"):
    return T.OrderRequest(
        client_order_id=client_order_id,
        symbol="BTCUSD",
        side="buy",
        quantity=Decimal(quantity),
        strategy_id="paper-sma",
    )


def _signoff(clock, policy) -> T.OwnerRiskPolicySignoff:
    return T.OwnerRiskPolicySignoff(
        owner_id="owner-1",
        policy_version=policy.policy_version,
        limits=policy.to_dict(),
        signed_at=clock.t,
    )


def _trade_key() -> T.ExchangeKeyPolicy:
    return T.ExchangeKeyPolicy(
        key_id="paper-key",
        venue="paper",
        trade_only=True,
        withdrawals_enabled=False,
        scopes=("trade",),
    )


def test_live_mode_refused_without_signoff(system_factory):
    with pytest.raises(T.TradingError) as exc:
        system_factory(mode=T.TradingMode.LIVE)
    assert exc.value.code == T.LIVE_NOT_AUTHORIZED


def test_live_mode_refused_on_mismatched_or_missing_key(
    clock, system_factory, policy
):
    wrong = T.OwnerRiskPolicySignoff(
        owner_id="owner-1",
        policy_version=policy.policy_version,
        limits={"max_leverage": "99"},
        signed_at=clock.t,
    )
    with pytest.raises(T.TradingError) as exc:
        system_factory(mode=T.TradingMode.LIVE, signoff=wrong)
    assert exc.value.code == T.LIVE_NOT_AUTHORIZED

    matching = _signoff(clock, policy)
    with pytest.raises(T.TradingError) as exc:
        system_factory(mode=T.TradingMode.LIVE, signoff=matching)
    assert exc.value.code == T.LIVE_NOT_AUTHORIZED

    expired = T.OwnerRiskPolicySignoff(
        owner_id="owner-1",
        policy_version=policy.policy_version,
        limits=policy.to_dict(),
        signed_at=clock.t - 100,
        expires_at=clock.t - 1,
    )
    with pytest.raises(T.TradingError) as exc:
        system_factory(
            mode=T.TradingMode.LIVE,
            signoff=expired,
            exchange_keys=(_trade_key(),),
        )
    assert exc.value.code == T.LIVE_NOT_AUTHORIZED


def test_owner_can_enable_live_and_policy_change_revokes_it(
    clock, system_factory, policy, owner, risk_manager
):
    system = system_factory(exchange_keys=(_trade_key(),))
    assert system.mode is T.TradingMode.PAPER
    system.enable_live(actor=owner, signoff=_signoff(clock, policy))
    assert system.mode is T.TradingMode.LIVE
    assert system.events.of_type("mode.live_authorized")

    changed = T.RiskPolicy(
        policy_version="tightened",
        max_order_notional_minor=100_000,
        max_leverage=Decimal("1"),
        max_concentration_pct=30,
        min_liquidity_multiple=2,
        max_stale_seconds=5,
        max_drawdown_pct=Decimal("10"),
        max_daily_loss_minor=100_000,
    )
    system.set_risk_policy(changed, actor=risk_manager)
    assert system.mode is T.TradingMode.PAPER
    assert system.events.of_type("mode.live_revoked")


def test_only_owner_enables_live(
    clock, system_factory, policy, execution, risk_manager
):
    system = system_factory(exchange_keys=(_trade_key(),))
    for actor in (execution, risk_manager):
        with pytest.raises(T.TradingError) as exc:
            system.enable_live(actor=actor, signoff=_signoff(clock, policy))
        assert exc.value.code == T.ROLE_REFUSED
    assert system.mode is T.TradingMode.PAPER


def test_withdrawal_capable_key_is_refused_at_construction(system_factory):
    bad = T.ExchangeKeyPolicy(
        key_id="bad",
        venue="paper",
        trade_only=False,
        withdrawals_enabled=True,
        scopes=("trade", "withdraw"),
    )
    with pytest.raises(T.TradingError) as exc:
        system_factory(exchange_keys=(bad,))
    assert exc.value.code == T.KEY_WITHDRAWAL_REFUSED


def test_execution_cannot_edit_limits_and_roles_are_separate(
    system_factory, execution, risk_manager, auditor
):
    system = system_factory()
    with pytest.raises(T.TradingError) as exc:
        system.set_risk_policy(
            T.RiskPolicy(
                policy_version="exec",
                max_order_notional_minor=10**12,
                max_leverage=Decimal("100"),
                max_concentration_pct=100,
                min_liquidity_multiple=1,
                max_stale_seconds=999,
                max_drawdown_pct=Decimal("100"),
                max_daily_loss_minor=10**12,
            ),
            actor=execution,
        )
    assert exc.value.code == T.RISK_LIMIT_IMMUTABLE

    for actor in (risk_manager, auditor):
        with pytest.raises(T.TradingError) as exc:
            system.submit_order(actor, _order("nope"))
        assert exc.value.code == T.ROLE_REFUSED

    with pytest.raises(T.TradingError) as exc:
        system.set_risk_policy(
            T.RiskPolicy(
                policy_version="audit-edit",
                max_order_notional_minor=1,
                max_leverage=Decimal("1"),
                max_concentration_pct=1,
                min_liquidity_multiple=1,
                max_stale_seconds=1,
                max_drawdown_pct=Decimal("1"),
                max_daily_loss_minor=1,
            ),
            actor=auditor,
        )
    assert exc.value.code == T.ROLE_REFUSED


def test_kill_switch_cancels_orders_revokes_and_freezes(
    clock, system_factory, execution, risk_manager
):
    calls = {"leases": 0, "keys": 0, "strategies": 0}

    def lease_counter() -> int:
        calls["leases"] += 1
        return 4

    def key_counter() -> int:
        calls["keys"] += 1
        return 2

    def strategy_counter() -> int:
        calls["strategies"] += 1
        return 1

    broker = T.PaperBroker(
        T.PaperBrokerConfig(fee_bps=10, fill_ratio=Decimal("0.5"))
    )
    kill = T.KillSwitch(
        revoke_leases=lease_counter,
        revoke_keys=key_counter,
        freeze_strategies=strategy_counter,
    )
    system = system_factory(broker=broker, kill_switch=kill)
    report = system.submit_order(execution, _order("partial", quantity="10"))
    assert report.order.status == "partially_filled"

    with pytest.raises(T.TradingError) as exc:
        system.kill_switch.engage(
            system, actor=execution, reason="not allowed"
        )
    assert exc.value.code == T.ROLE_REFUSED
    assert system.kill_switch.engaged is False

    engaged = system.kill_switch.engage(
        system, actor=risk_manager, reason="readiness drill"
    )
    assert engaged.engaged is True
    assert engaged.cancelled_orders == 1
    assert engaged.revoked_leases == 4
    assert engaged.revoked_keys == 2
    assert engaged.frozen_strategies == 1
    assert calls == {"leases": 1, "keys": 1, "strategies": 1}
    assert system.oms.get(report.order.order_id).status == "cancelled"
    assert system.frozen is True
    assert system.events.of_type("kill_switch.engaged")
    assert system.kill_switch.engage(
        system, actor=risk_manager, reason="again"
    ).already_engaged is True

    with pytest.raises(T.TradingError) as exc:
        system.submit_order(execution, _order("after-kill"))
    assert exc.value.code == T.KILL_SWITCH_ENGAGED


def test_auditor_can_freeze_and_callback_errors_are_recorded(
    system_factory, auditor
):
    def broken() -> int:
        raise RuntimeError("vault offline")

    kill = T.KillSwitch(revoke_keys=broken)
    system = system_factory(kill_switch=kill)
    report = system.kill_switch.engage(
        system, actor=auditor, reason="independent freeze"
    )
    assert report.engaged is True
    assert report.callback_errors
    assert "vault offline" in report.callback_errors[0]
    assert system.frozen is True


def test_stale_data_refuses_submission_before_an_order_exists(
    clock, system_factory, snapshot_factory, market_factory, execution
):
    frozen = snapshot_factory()
    system = system_factory(
        market=market_factory(fixed=frozen, max_stale=5)
    )
    clock.advance(6)
    with pytest.raises(T.TradingError) as exc:
        system.submit_order(execution, _order("stale"))
    assert exc.value.code == T.STALE_DATA
    assert system.oms.list() == ()
