"""P8-04 — accessibility, performance/device and context-loss matrices.

The positive path consumes the real deterministic instruments. The negative
path proves that a fabricated axe run or a GPU number without a supplied sample
is rejected rather than counted as evidence.
"""
from __future__ import annotations

import copy
import json

from _p8_observability_helpers import PLUGIN


def test_accessibility_matrix_consumes_the_real_audit(a11y_audit, tensor):
    OBS, _, _ = tensor
    report = a11y_audit.build_report(PLUGIN)
    matrix = OBS.accessibility_matrix(report)
    assert matrix["ok"] is True
    assert matrix["axe"]["status"] == "not_available"
    assert matrix["axe"]["claimed_run"] is False
    assert matrix["axe"]["honest"] is True
    assert matrix["static"]["errors"] == 0
    assert {"target_size", "semantic_mirror", "reduced_motion"} <= set(
        matrix["static_scope"]
    )
    statuses = {item["item"]: item["status"] for item in matrix["reviews"]}
    assert statuses["axe-core automated scan"] == "not_performed"
    assert statuses["NVDA / VoiceOver / TalkBack"] == "not_performed"
    assert statuses["200% zoom / reflow"] == "not_performed"
    assert statuses["keyboard-only traversal"] == "unit_proven"
    assert all(
        status in ("not_performed", "unit_proven")
        for status in statuses.values()
    )


def test_accessibility_matrix_rejects_an_invented_axe_run(a11y_audit, tensor):
    OBS, _, _ = tensor
    report = copy.deepcopy(a11y_audit.build_report(PLUGIN))
    report["axe"] = {"status": "ran", "claimed_run": True}
    matrix = OBS.accessibility_matrix(report)
    assert matrix["ok"] is False
    assert matrix["axe"]["honest"] is False


def test_accessibility_matrix_rejects_an_overclaimed_manual_review(
    a11y_audit, tensor
):
    OBS, _, _ = tensor
    report = copy.deepcopy(a11y_audit.build_report(PLUGIN))
    for item in report["manual_review"]:
        if item["item"] == "NVDA / VoiceOver / TalkBack":
            item["status"] = "pass"
    matrix = OBS.accessibility_matrix(report)
    assert matrix["ok"] is False


def test_performance_matrix_uses_the_real_data_path(verifier, tensor):
    OBS, _, _ = tensor
    budget = verifier.renderer_report()
    contract = verifier.renderer_contract_probe()
    matrix = OBS.performance_matrix(budget, device_contract=contract)
    assert matrix["ok"] is True
    assert matrix["data_path"]["measured"] is True
    assert matrix["data_path"]["iterations"] >= 1
    assert isinstance(matrix["data_path"]["p95_ms"], (int, float))
    assert "frame_p95_ms" in matrix["gpu_not_measured"]
    assert "heap_mb" in matrix["gpu_not_measured"]
    for row in matrix["budgets"]:
        if row["metric"] in ("frame_p95_ms", "heap_mb"):
            assert row["status"] == "not_measured"
            assert row["value"] is None
    tiers = {row["name"]: row["tier"] for row in matrix["device_tiers"]}
    assert tiers["default"] == "desktop"
    assert tiers["save_data"] == "low"
    assert tiers["low_memory"] == "low"
    assert tiers["low_cores"] == "low"
    assert tiers["capable"] == "desktop"


def test_a_gpu_number_without_a_supplied_sample_is_rejected(verifier, tensor):
    OBS, _, _ = tensor
    budget = verifier.renderer_report()
    fabricated = copy.deepcopy(budget)
    fabricated["budget_sample"]["supplied"] = False
    for row in fabricated["budgets"]:
        if row["metric"] == "frame_p95_ms":
            row["status"] = "pass"
            row["value"] = 15.0
            row["ok"] = True
    matrix = OBS.performance_matrix(fabricated, device_contract=None)
    assert matrix["ok"] is False
    assert any(
        "claimed without a supplied sample" in check["detail"]
        for check in matrix["checks"]
    )


def test_a_measured_null_or_an_unmeasured_value_is_rejected(verifier, tensor):
    OBS, _, _ = tensor
    budget = copy.deepcopy(verifier.renderer_report())
    for row in budget["budgets"]:
        if row["metric"] == "visible_avatars":
            row["status"] = "pass"
            row["value"] = None
            row["ok"] = True
        if row["metric"] == "active_agents":
            row["status"] = "not_measured"
            row["value"] = 7.0
            row["ok"] = None
    matrix = OBS.performance_matrix(budget, device_contract=None)
    assert matrix["ok"] is False
    assert any(
        "not_measured with a value" in check["detail"]
        or "measured without a numeric value" in check["detail"]
        for check in matrix["checks"]
    )


def test_context_loss_states_are_exercised_by_the_renderer_contract(
    verifier, tensor
):
    OBS, _, _ = tensor
    contract = verifier.renderer_contract_probe()
    assert contract["ok"] is True
    matrix = OBS.context_loss_matrix(contract)
    assert matrix["ok"] is True
    by_name = {row["name"]: row for row in matrix["sequences"]}
    assert by_name["recover"]["observed"] == [
        "ready",
        "lost",
        "recovering",
        "ready",
    ]
    assert by_name["recover"]["modes"] == [
        "gpu",
        "suspended",
        "suspended",
        "gpu",
    ]
    assert by_name["fallback"]["observed"][-1] == "fallback"
    assert by_name["fallback"]["modes"][-1] == "static"
    assert by_name["no_fallback"]["observed"][-1] == "lost"
    assert by_name["invalid_event"]["ok"] is True


def test_context_loss_without_an_execution_invents_no_state(tensor):
    OBS, _, _ = tensor
    matrix = OBS.context_loss_matrix(None)
    assert matrix["ok"] is False
    assert matrix["sequences"] == []
    assert "not exercised" in matrix["limitations"][0]


def test_device_probe_reports_the_contract_limits(verifier):
    contract = verifier.renderer_contract_probe()
    assert contract["ok"] is True
    devices = {row["name"]: row for row in contract["devices"]}
    assert devices["default"]["limits"]["frameP95Ms"] == 16.7
    assert devices["save_data"]["limits"]["frameP95Ms"] == 33.3
    assert devices["save_data"]["limits"]["activeAgents"] == 40
    assert devices["save_data"]["limits"]["heapMb"] == 256
    assert devices["default"]["limits"]["heapMb"] == 512
    assert json.dumps(contract, sort_keys=True)


def test_combined_matrix_is_pass_only_when_every_matrix_is_pass(
    a11y_audit, verifier, tensor
):
    OBS, _, _ = tensor
    matrix = OBS.matrices_report(
        accessibility=OBS.accessibility_matrix(a11y_audit.build_report(PLUGIN)),
        performance=OBS.performance_matrix(
            verifier.renderer_report(),
            device_contract=verifier.renderer_contract_probe(),
        ),
        context_loss=OBS.context_loss_matrix(
            verifier.renderer_contract_probe()
        ),
    )
    assert matrix["ok"] is True
    assert matrix["checks"]
