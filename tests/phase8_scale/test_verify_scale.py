"""P8-01 — the scale verifier's report, budgets and honesty guard."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_scale.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_scale_test", SCRIPT)
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


def test_report_passes_with_honest_not_measured_browser_metrics():
    report = _report(_run())
    assert report["schema"] == "qoffice-scale/1"
    assert report["task_id"] == "P8-01"
    assert report["status"] == "PASS"
    assert report["ok"] is True
    assert report["checks_failed"] == 0
    assert report["fixture"]["counts"]["floors"] == 100
    assert report["fixture"]["counts"]["wings"] == 5
    assert report["fixture"]["counts"]["rooms"] == 300
    assert report["fixture"]["counts"]["desks"] == 3075
    assert report["fixture"]["counts"]["agents"] == 1000
    assert report["fixture"]["projection"] is True
    assert report["fixture"]["operational"] is False
    assert report["tenant"]["tenant_id"] == "tenant:scale-fixture"
    assert report["tenant"]["seed_is_production"] is False
    names = {check["name"] for check in report["checks"]}
    for required in (
        "tower_projection_valid",
        "fixture_counts_match_contract",
        "fixture_is_deterministic",
        "agents_are_projection_records",
        "tenant_isolation_and_synthetic_scope",
        "agent_seats_are_real",
        "active_set_uses_canonical_policy",
        "active_agents_within_budget",
        "visible_avatars_within_budget",
        "active_rooms_within_budget",
        "dormant_rooms_and_agents_selected",
        "hierarchical_path_graph_built",
        "pathfinding_avg_within_budget",
        "pathfinding_p95_within_budget",
        "event_normal_within_budget",
        "event_burst_within_budget",
        "event_model_coalesces",
        "device_tiers_defined",
        "no_fabricated_metrics",
    ):
        assert required in names
    assert report["active_set"]["active_agents"] <= 40
    assert report["active_set"]["visible_avatars"] <= 80


def test_browser_metrics_are_not_measured_by_default_and_never_fabricated():
    report = _report(_run())
    rows = {row["metric"]: row for row in report["budgets"]}
    for metric in (
        "frame_p95_ms",
        "heap_mb",
        "decoded_texture_mb",
        "map_chunk_mb",
        "visible_floor_bundle_mb",
        "adjacent_prefetch_mb",
    ):
        assert rows[metric]["status"] == "not_measured"
        assert rows[metric]["value"] is None
        assert rows[metric]["ok"] is None
        assert rows[metric]["measured"] is False
        assert metric in report["gpu_not_measured"]
    # Active/visible/pathfinding are measured from the fixture data path.
    for metric in (
        "active_agents",
        "visible_avatars",
        "pathfinding_avg_ms",
        "pathfinding_p95_ms",
    ):
        assert rows[metric]["kind"] == "measured"
        assert rows[metric]["measured"] is True
        assert isinstance(rows[metric]["value"], (int, float))
    # Event rows are a labelled model, not a browser measurement.
    for metric in ("events_per_second", "event_burst"):
        assert rows[metric]["kind"] == "budget_model"
        assert rows[metric]["measured"] is False
        assert isinstance(rows[metric]["value"], (int, float))
    assert report["events"]["measured"] is False
    assert report["no_fabricated_metrics"] is True
    assert report["budget_sample"]["supplied"] is False


def test_report_is_deterministic_except_for_clock_and_timing():
    first = _report(_run("--iterations", "1"))
    second = _report(_run("--iterations", "1"))
    assert first["fixture"]["digest"] == second["fixture"]["digest"]
    assert first["fixture"]["checks"] == second["fixture"]["checks"]
    assert first["active_set"] == second["active_set"]
    assert first["events"] == second["events"]
    assert first["device_tiers"] == second["device_tiers"]
    first_checks = [(c["name"], c["ok"]) for c in first["checks"]]
    second_checks = [(c["name"], c["ok"]) for c in second["checks"]]
    assert first_checks == second_checks


def test_a_real_browser_sample_is_used_and_can_pass(tmp_path):
    sample = tmp_path / "sample.json"
    sample.write_text(
        json.dumps(
            {
                "frameP95Ms": 12.0,
                "heapMb": 300,
                "eventsPerSecond": 15,
                "eventBurst": 40,
                "decodedTextureMb": 200,
                "mapChunkMb": 100,
                "visibleFloorBundleMb": 6,
                "adjacentPrefetchMb": 10,
            }
        ),
        encoding="utf-8",
    )
    report = _report(_run("--sample", str(sample)))
    assert report["status"] == "PASS"
    assert report["budget_sample"]["supplied"] is True
    assert report["budget_sample"]["measured_budget_count"] == 12
    rows = {row["metric"]: row for row in report["budgets"]}
    assert rows["events_per_second"]["kind"] == "measured"
    assert rows["events_per_second"]["value"] == 15
    assert rows["event_burst"]["value"] == 40
    assert report["gpu_not_measured"] == []
    assert report["no_fabricated_metrics"] is True


def test_a_real_sample_over_budget_fails_the_verifier(tmp_path):
    sample = tmp_path / "sample.json"
    sample.write_text(
        json.dumps({"frameP95Ms": 25.0, "heapMb": 700}), encoding="utf-8"
    )
    proc = _run("--sample", str(sample))
    assert proc.returncode == 1
    report = json.loads(proc.stdout)
    assert report["status"] == "FAIL"
    failed = {row["metric"] for row in report["budgets"] if row["status"] == "fail"}
    assert {"frame_p95_ms", "heap_mb"} <= failed
    # A sample does not force omitted browser metrics to be invented.
    omitted = [
        row for row in report["budgets"] if row["metric"] == "decoded_texture_mb"
    ][0]
    assert omitted["status"] == "not_measured"
    assert omitted["value"] is None


def test_low_tier_applies_its_own_limits(tmp_path):
    sample = tmp_path / "sample.json"
    sample.write_text(json.dumps({"frameP95Ms": 25.0}), encoding="utf-8")
    low = _report(_run("--sample", str(sample), "--tier", "low"))
    assert low["tier"] == "low"
    assert low["status"] == "PASS"
    desktop = _run("--sample", str(sample), "--tier", "desktop")
    assert desktop.returncode == 1
    assert json.loads(desktop.stdout)["status"] == "FAIL"


def test_structural_counts_cannot_be_faked_through_a_sample(tmp_path):
    # activeAgents/visibleAvatars are measured from the fixture, not sampled;
    # a sample containing only them is therefore empty and rejected.
    sample = tmp_path / "sample.json"
    sample.write_text(
        json.dumps({"activeAgents": 1, "visibleAvatars": 1}), encoding="utf-8"
    )
    proc = _run("--sample", str(sample))
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["code"] == "QOFFICE_SCALE_SAMPLE_EMPTY"


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
    assert json.loads(missing.stdout)["code"] == "QOFFICE_SCALE_SAMPLE_MISSING"


def test_out_writes_the_same_json_report(tmp_path):
    out = tmp_path / "report.json"
    proc = _run("--out", str(out), "--iterations", "1")
    assert proc.returncode == 0
    printed = json.loads(proc.stdout)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert printed == written
    assert written["task_id"] == "P8-01"


def test_unknown_flags_and_bad_iterations_are_usage_errors():
    unknown = subprocess.run(
        [sys.executable, str(SCRIPT), "--not-a-real-flag"],
        cwd=ROOT,
        env=_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert unknown.returncode == 2
    bad = _run("--iterations", "0")
    assert bad.returncode == 2
    assert json.loads(bad.stdout)["code"] == "QOFFICE_SCALE_BAD_ITERATIONS"


def test_no_fabricated_metrics_guard_rejects_a_measured_null():
    verifier = _load_verifier()
    assert verifier._no_fabricated_metrics([
        {"status": "pass", "value": 1, "ok": True, "kind": "measured",
         "measured": True},
        {"status": "not_measured", "value": None, "ok": None,
         "kind": "not_measured", "measured": False},
    ]) is True
    assert verifier._no_fabricated_metrics([
        {"status": "pass", "value": None, "ok": True, "kind": "measured",
         "measured": True},
    ]) is False
    assert verifier._no_fabricated_metrics([
        {"status": "not_measured", "value": 1, "ok": None,
         "kind": "not_measured", "measured": False},
    ]) is False
    assert verifier._no_fabricated_metrics([
        {"status": "pass", "value": 1, "ok": True, "kind": "budget_model",
         "measured": True},
    ]) is False
