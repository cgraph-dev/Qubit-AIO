"""SQLite completion and fleet verification remain independent update outcomes."""

import json

import pytest

from qubit_cli import update_cmd, update_cmd_fleet, update_cmd_maint, update_receipt
from qubit_constants import get_qubit_home


@pytest.mark.parametrize(
    "update_complete,state",
    [(True, "current"), (False, "current"), (True, "stale"), (True, "down"), (True, None)],
)
def test_fleet_completion_preserves_runtime_verdict_and_restart_obligation(
    update_complete, state, monkeypatch,
):
    refreshed, migrated = [], []
    snapshot = [{"profile": "default", "pid": 1234, "state": state}] if state else []
    restart = update_cmd_fleet._GatewayRestartOutcome(
        incomplete=False, phase_errors=[], pre_restart_gateway_pids=[1234],
        restarted_services=["hermes-gateway"], failed_or_stale_units=[],
        relaunched_profiles=[], externally_supervised_profiles=[], killed_pids=set(),
    )
    monkeypatch.setattr(update_cmd_fleet, "_print_legacy_units_warning", lambda: None)
    monkeypatch.setattr(update_cmd, "_surviving_pre_update_serve_runtimes", lambda plan: [])
    monkeypatch.setattr(
        update_cmd_maint, "_refresh_dashboard_after_update",
        lambda **kwargs: refreshed.append(kwargs),
    )
    monkeypatch.setattr(update_receipt, "_code_identity", lambda **kwargs: {})
    monkeypatch.setattr(
        "qubit_cli.gateway_migrate.maybe_auto_migrate_after_update", lambda: migrated.append(True),
    )

    def collect(outcome, rows_expected):
        assert outcome is restart
        assert rows_expected is True
        return snapshot

    # ``_verify_fleet_after_update`` lives in the split verify module and resolves the
    # collector from its own globals, so patch it there (``update_cmd_fleet`` only re-exports).
    monkeypatch.setattr("qubit_cli.update_cmd_fleet_verify._collect_fleet_snapshot", collect)
    update_cmd_fleet._write_fleet_restart_pending_marker()
    assert update_cmd_fleet._fleet_restart_obligation_armed()
    healthy = update_complete and state == "current"
    with update_receipt.update_receipt_scope():
        update_receipt.begin_update_receipt()
        # Fork contract C3 (post-freeze): the code is committed, so an owed gateway restart is a
        # recorded follow-up, never a failed update — no SystemExit even when the fleet is stale
        # or down. Validation.py's sibling tests pin the same success-with-follow-up outcome.
        update_cmd_fleet._verify_fleet_after_update(
            restart, _pre_update_plan=None, _windows_gateway_resume=None,
            update_complete=update_complete,
        )

    receipt = json.loads((get_qubit_home() / "logs/update_receipts/latest.json").read_text())
    assert receipt["outcome"] == "success"
    assert receipt["fleet"] == snapshot
    assert restart.incomplete is (state != "current")
    assert update_cmd_fleet._fleet_restart_obligation_armed() is (state != "current")
    assert migrated == ([True] if healthy else [])
    assert refreshed == [{"already_restarted_units": {"hermes-gateway"}}]
