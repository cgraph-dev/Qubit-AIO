"""P8-04 — the acceptance verifier's JSON contract and honesty."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from _p8_helpers import ROOT, VERIFIER


def test_verifier_reports_pass_with_every_check(verifier_report):
    report = verifier_report
    assert report["schema"] == "qubit-office-observability-verification/1"
    assert report["task_id"] == "P8-04"
    assert report["status"] == "PASS"
    assert report["ok"] is True
    assert report["checks_failed"] == 0
    assert report["checks_total"] >= 15
    assert all(check["ok"] for check in report["checks"])
    names = {check["name"] for check in report["checks"]}
    for required in (
        "one_trace",
        "canonical_stage_order",
        "all_required_ids",
        "required_metrics",
        "content_capture_off",
        "no_prompt_or_pii_leakage",
        "eval_event_linked_to_judged_span",
        "sampling_policy",
        "eval_regression_blocks_release",
        "accessibility_matrix_honest",
        "accessibility_fake_axe_claim_rejected",
        "performance_matrix_real_data_path",
        "gpu_frame_and_heap_not_fabricated",
        "performance_fake_gpu_claim_rejected",
        "device_tiers_exercised",
        "context_loss_recovery_exercised",
    ):
        assert required in names, required


def test_verifier_never_claims_unperformed_accessibility_work(verifier_report):
    report = verifier_report
    a11y = report["accessibility"]
    assert a11y["axe"]["status"] == "not_available"
    assert a11y["axe"]["claimed_run"] is False
    for review in a11y["reviews"]:
        assert review["status"] in ("not_performed", "unit_proven")
    assert any(
        "axe" in line and "NOT performed" in line
        for line in report["limitations"]
    )


def test_verifier_keeps_gpu_and_heap_unmeasured(verifier_report):
    perf = verifier_report["performance"]
    assert perf["sample_supplied"] is False
    assert "frame_p95_ms" in perf["gpu_not_measured"]
    assert "heap_mb" in perf["gpu_not_measured"]
    for row in perf["budgets"]:
        if row["metric"] in ("frame_p95_ms", "heap_mb"):
            assert row["status"] == "not_measured"
            assert row["value"] is None


def test_verifier_inputs_and_limitations_are_recorded(verifier_report):
    report = verifier_report
    assert report["inputs"]
    for item in report["inputs"]:
        assert len(item["sha256"]) == 64
        assert (ROOT / item["path"]).is_file()
    text = " ".join(report["limitations"])
    assert "no full matrix" in text
    assert "WebGL" in text


def test_cli_writes_the_same_report(tmp_path):
    out = tmp_path / "report.json"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env.setdefault("TMPDIR", "/tmp")
    proc = subprocess.run(
        [sys.executable, str(VERIFIER), "--json", "--out", str(out)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    printed = json.loads(proc.stdout)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert printed == written
    assert written["task_id"] == "P8-04"


def test_cli_usage_error_is_exit_two(tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env.setdefault("TMPDIR", "/tmp")
    proc = subprocess.run(
        [sys.executable, str(VERIFIER), "--json", "--node-timeout", "0"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    # A zero timeout either leaves the probe unexercised (FAIL) or fails it;
    # it must never claim PASS.
    assert proc.returncode in (1, 2)
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
