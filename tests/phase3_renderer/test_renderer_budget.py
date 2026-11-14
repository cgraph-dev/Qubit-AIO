from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_renderer_budget.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_renderer_budget_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env["TMPDIR"] = "/tmp"
    return env


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--json", *args],
        cwd=ROOT,
        env=_env(),
        capture_output=True,
        text=True,
        timeout=300,
    )


def _report(proc: subprocess.CompletedProcess[str]) -> dict:
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


def test_report_passes_with_honest_not_measured_budgets():
    report = _report(_run())
    assert report["schema"] == "qoffice-renderer-budget/1"
    assert report["task_id"] == "P3-04"
    assert report["status"] == "PASS"
    assert report["ok"] is True
    assert report["tower"]["valid"] is True
    assert report["tower"]["counts"] == {
        "floors": 100,
        "wings": 5,
        "active_floors": report["tower"]["counts"]["active_floors"],
        "dormant_floors": report["tower"]["counts"]["dormant_floors"],
        "rooms": 300,
        "cells": report["tower"]["counts"]["cells"],
        "desks": 3075,
        "staffed_agents": 0,
    }
    assert report["active_floor"]["one_active_floor"] is True
    assert report["active_floor"]["render_floor_count"] == 1
    assert all(item["ok"] for item in report["checks"])
    assert report["checks_failed"] == 0


def test_default_budgets_are_not_measured_and_never_fabricated():
    report = _report(_run())
    for row in report["budgets"]:
        assert row["status"] == "not_measured"
        assert row["value"] is None
        assert row["ok"] is None
    assert report["budget_sample"]["supplied"] is False
    assert report["no_fabricated_metrics"] is True
    assert "frame_p95_ms" in report["gpu_not_measured"]
    assert "heap_mb" in report["gpu_not_measured"]
    assert "frame_p95_ms" in report["budget_sample"]["not_measured"]


def test_one_active_floor_and_no_all_floor_draw():
    report = _report(_run())
    culling = report["culling"]
    assert culling["one_floor_only"] is True
    assert culling["floor_rooms"] > 0
    assert culling["culled_rooms"] == 300 - culling["floor_rooms"]
    assert culling["culled_cells"] > 0
    assert culling["culled_desks"] > 0
    data_path = report["data_path"]
    assert data_path["all_floor_draw"] is False
    assert data_path["payload_floor_count"] == 1
    assert data_path["payload_detail"] == "full"
    assert data_path["floor_projection"]["iterations"] >= 1
    names = {item["name"]: item for item in report["checks"]}
    assert names["no_all_floor_draw"]["ok"] is True
    assert names["one_floor_payload_only"]["ok"] is True


def test_report_is_deterministic_except_for_timing_and_clock():
    first = _report(_run("--iterations", "2"))
    second = _report(_run("--iterations", "2"))
    assert first["tower"]["digest"] == second["tower"]["digest"]
    assert first["culling"] == second["culling"]
    assert first["active_floor"] == second["active_floor"]
    assert first["budgets"] == second["budgets"]
    first_checks = [(c["name"], c["ok"]) for c in first["checks"]]
    second_checks = [(c["name"], c["ok"]) for c in second["checks"]]
    assert first_checks == second_checks


def test_unknown_floor_is_a_typed_usage_error():
    proc = _run("--floor", "does-not-exist")
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["code"] == "QOFFICE_RENDERER_FLOOR_UNKNOWN"


def test_supplied_sample_is_measured_and_can_pass(tmp_path):
    sample = tmp_path / "sample.json"
    sample.write_text(json.dumps({
        "frameP95Ms": 12.0,
        "activeAgents": 12,
        "visibleAvatars": 40,
        "pathfindingAvgMs": 1.1,
        "pathfindingP95Ms": 5.0,
        "heapMb": 300,
        "eventsPerSecond": 8,
        "eventBurst": 20,
    }), encoding="utf-8")
    proc = _run("--sample", str(sample))
    assert proc.returncode == 0, proc.stderr or proc.stdout
    report = json.loads(proc.stdout)
    assert report["status"] == "PASS"
    assert report["budget_sample"]["supplied"] is True
    assert report["budget_sample"]["measured_budget_count"] == 8
    for row in report["budgets"]:
        assert row["status"] == "pass"
        assert isinstance(row["value"], (int, float))
    assert report["no_fabricated_metrics"] is True
    assert report["gpu_not_measured"] == []


def test_a_real_sample_over_budget_fails_the_verifier(tmp_path):
    sample = tmp_path / "sample.json"
    sample.write_text(json.dumps({
        "frameP95Ms": 25.0,
        "heapMb": 700,
        "activeAgents": 41,
    }), encoding="utf-8")
    proc = _run("--sample", str(sample))
    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    assert report["status"] == "FAIL"
    failed = {
        row["metric"] for row in report["budgets"]
        if row["status"] == "fail"
    }
    assert {"frame_p95_ms", "heap_mb", "active_agents"} <= failed
    # A sample does not force the metrics it omitted to be invented.
    omitted = [
        row for row in report["budgets"] if row["metric"] == "visible_avatars"
    ][0]
    assert omitted["status"] == "not_measured"
    assert omitted["value"] is None


def test_snake_case_sample_aliases_are_accepted(tmp_path):
    sample = tmp_path / "sample.json"
    sample.write_text(json.dumps({
        "frame_p95_ms": 10,
        "pathfinding_p95_ms": 4,
    }), encoding="utf-8")
    report = _report(_run("--sample", str(sample)))
    measured = {
        row["metric"]: row for row in report["budgets"]
        if row["status"] != "not_measured"
    }
    assert measured["frame_p95_ms"]["value"] == 10
    assert measured["pathfinding_p95_ms"]["value"] == 4


def test_low_tier_limits_are_applied(tmp_path):
    sample = tmp_path / "sample.json"
    sample.write_text(json.dumps({"frameP95Ms": 25, "heapMb": 200}),
                      encoding="utf-8")
    low = _report(_run("--sample", str(sample), "--tier", "low"))
    assert low["tier"] == "low"
    assert low["status"] == "PASS"
    desktop = _run("--sample", str(sample), "--tier", "desktop")
    assert desktop.returncode == 1
    assert json.loads(desktop.stdout)["status"] == "FAIL"


def test_malformed_samples_are_typed_usage_errors(tmp_path):
    cases = {
        "not-json.json": "{",
        "empty.json": "{}",
        "boolean.json": json.dumps({"frameP95Ms": True}),
        "negative.json": json.dumps({"frameP95Ms": -1}),
        "string.json": json.dumps({"frameP95Ms": "fast"}),
    }
    for name, text in cases.items():
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        proc = _run("--sample", str(path))
        assert proc.returncode == 2, (name, proc.stdout, proc.stderr)
        assert json.loads(proc.stdout)["ok"] is False
    missing = _run("--sample", str(tmp_path / "missing.json"))
    assert missing.returncode == 2
    assert json.loads(missing.stdout)["code"] == "QOFFICE_RENDERER_SAMPLE_MISSING"


def test_out_writes_the_same_json_report(tmp_path):
    out = tmp_path / "report.json"
    proc = _run("--out", str(out), "--iterations", "1")
    assert proc.returncode == 0
    printed = json.loads(proc.stdout)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert printed == written
    assert written["task_id"] == "P3-04"


def test_no_fabricated_metrics_guard_rejects_a_measured_null():
    verifier = _load_verifier()
    assert verifier._no_fabricated_metrics([
        {"status": "pass", "value": 1, "ok": True},
        {"status": "not_measured", "value": None, "ok": None},
    ]) is True
    assert verifier._no_fabricated_metrics([
        {"status": "pass", "value": None, "ok": True},
    ]) is False
    assert verifier._no_fabricated_metrics([
        {"status": "not_measured", "value": 1, "ok": None},
    ]) is False
