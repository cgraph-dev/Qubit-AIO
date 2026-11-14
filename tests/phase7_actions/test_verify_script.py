"""P7-01 — the acceptance script contract (hermetic, in-process)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from voicebridge.business import simulation as sim

pytestmark = pytest.mark.unit

SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "verify_simulation_actions.py"
)


def _module():
    spec = importlib.util.spec_from_file_location(
        "verify_simulation_actions", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_json_report_is_pass_and_checks_every_acceptance_item(capsys):
    module = _module()
    assert module.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["checks_failed"] == 0
    assert payload["checks_total"] >= 15
    names = {check["name"] for check in payload["checks"]}
    for required in (
        "catalog_valid",
        "required_categories",
        "mature_flagged",
        "deterministic_selection",
        "deterministic_replay",
        "precondition_rejection",
        "cooldown_rejection",
        "prop_rejection",
        "room_rejection",
        "approval_enforced",
        "consent_enforced",
        "effects_change_state",
        "relationship_change",
        "mature_off_by_default",
        "mature_routing",
        "no_business_truth",
        "bounded_state",
    ):
        assert required in names
    assert payload["counts_by_category"]["work"] > 0
    assert payload["counts_by_category"]["career"] > 0
    assert payload["mature_action_count"] > 0


def test_unknown_flag_is_a_usage_error():
    module = _module()
    with pytest.raises(SystemExit) as exc:
        module.main(["--not-a-real-flag"])
    assert exc.value.code == 2


def test_invalid_catalog_exits_one(capsys, monkeypatch):
    module = _module()

    def _boom():
        raise sim.SimulationError(
            sim.SIMULATION_INVALID_CODE, "catalog is missing actions"
        )

    monkeypatch.setattr(module.sim, "default_catalog", _boom)
    assert module.main(["--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FAIL"
    assert payload["code"] == sim.SIMULATION_INVALID_CODE
