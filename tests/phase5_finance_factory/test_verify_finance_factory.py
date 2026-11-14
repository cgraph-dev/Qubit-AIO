"""The independent Phase 5 verifier must pass and must be able to fail."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_finance_factory.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "verify_finance_factory_test", SCRIPT
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verifier_passes_all_checks(tmp_path):
    module = _load()
    report = module.run(tmp_path / "verify")
    assert report["status"] == "PASS"
    assert report["failed"] == 0
    names = {item["name"] for item in report["checks"]}
    assert {
        "balanced_posting",
        "unbalanced_posting_refused",
        "posting_idempotent",
        "payment_idempotent",
        "payment_requires_approval",
        "narrative_is_not_truth",
        "unknown_resolution_explicit",
        "financial_resolution_needs_receipt",
        "payment_completion_posts_balance",
        "refund_chargeback_balance",
        "reconciliation_reports_truth",
        "entity_ledger_isolation",
        "clone_has_no_inherited_power",
        "implicit_inheritance_refused",
        "creation_is_audited",
        "tenant_isolation",
    } <= names


def test_verifier_balance_predicate_can_fail(tmp_path):
    module = _load()
    report = module.run(tmp_path / "verify")
    planted = next(
        item for item in report["checks"] if item["name"] == "balance_check_can_fail"
    )
    assert planted["ok"] is True
    assert "debit=" in planted["detail"]


def test_verifier_cli_json_exits_zero(capsys):
    module = _load()
    assert module.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
