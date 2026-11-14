"""P5-02 — the standalone verifier replays the readiness contract and can fail."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_trading_readiness.py"

REQUIRED_CHECKS = {
    "backtest_deterministic",
    "no_lookahead",
    "fees_and_slippage_apply",
    "walk_forward_split",
    "multiple_regimes",
    "paper_fill",
    "order_idempotency",
    "risk_veto_notional",
    "risk_veto_liquidity",
    "double_entry_balanced",
    "audit_event_and_ledger_backed",
    "unknown_not_completed",
    "unknown_reconciled",
    "live_refused_without_signoff",
    "execution_cannot_edit_limits",
    "kill_switch_cancels_all",
    "kill_switch_revokes_injected",
    "stale_data_refused",
}


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _load_verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_trading_readiness_test", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verifier_passes_on_the_current_tree():
    result = _run("--json")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["checks_failed"] == 0
    assert report["schema"] == "qubit-office-trading-readiness/1"
    assert report["mode"] == "paper"
    assert report["live_capital_touched"] is False
    names = {check["name"] for check in report["checks"]}
    assert REQUIRED_CHECKS <= names


def test_verifier_human_output_passes():
    result = _run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "checks passed" in result.stdout
    assert "FAIL" not in result.stdout


def test_verifier_reports_a_failed_check(monkeypatch):
    module = _load_verifier()

    def broken_checks():
        return [
            module._check("synthetic_failure", False, "planted defect")
        ]

    monkeypatch.setattr(module, "_backtest_checks", broken_checks)
    report = module.run()
    assert report["ok"] is False
    assert report["checks_failed"] >= 1
    names = {check["name"] for check in report["checks"]}
    assert "synthetic_failure" in names
