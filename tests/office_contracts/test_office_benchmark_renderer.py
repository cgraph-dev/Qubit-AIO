"""P0-08 contract tests for ``scripts/office_benchmark_renderer.py``.

These assert what the harness may honestly claim: deterministic data-path
counts for one floor, a pinned renderer selection, explicit absence of GPU
metrics, and budget enforcement. They never assert frame or heap numbers.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "office_benchmark_renderer.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH"}
    }
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )


def _report(args: tuple[str, ...] = ("--json",)) -> dict:
    result = _run(*args)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_benchmark_reports_honest_one_floor_data_path() -> None:
    report = _report()

    assert report["status"] == "PASS"
    assert report["task_id"] == "P0-08"
    assert report["fixture"]["valid"] is True
    assert report["fixture"]["problems"] == []
    assert report["fixture"]["tower_counts"] == {
        "floors": 100,
        "wings": 5,
        "active_floors": 23,
        "dormant_floors": 77,
        "rooms": 300,
        "cells": 575,
        "desks": 3075,
        "staffed_agents": 0,
    }

    culling = report["culling"]
    assert culling["one_floor_only"] is True
    assert culling["rooms"] < report["fixture"]["tower_counts"]["rooms"]
    assert culling["cells"] < report["fixture"]["tower_counts"]["cells"]
    assert culling["desks"] < report["fixture"]["tower_counts"]["desks"]
    assert culling["culled_rooms"] == (
        report["fixture"]["tower_counts"]["rooms"] - culling["rooms"]
    )
    assert culling["culled_desks"] == (
        report["fixture"]["tower_counts"]["desks"] - culling["desks"]
    )

    path = report["backends"]["phaser"]["data_path"]
    assert path["path_nodes"] > 1
    assert path["portal_count"] > 0
    assert path["path_reachable"] is True
    assert path["path_hops"] is not None
    assert path["atlas_sheet_count"] > 0
    assert path["atlas_plan_bytes"] > 0

    selection = report["selection"]
    assert selection["renderer"] == "phaser"
    assert selection["selection_basis"] == "existing_renderer_pinned"
    assert "DECISIONS.md" in selection["ruling"]
    assert selection["candidates"] == ["phaser", "pixi"]

    for backend_name in ("phaser", "pixi"):
        backend = report["backends"][backend_name]
        assert backend["renderer"] == backend_name
        assert backend["gpu_measured"] is False
        assert backend["frame_metrics"] is None
        assert backend["heap_metrics"] is None
        assert backend["shared_projection"] is True
        assert backend["data_path"] == path

    assert report["limitations"], "the GPU limitation must be recorded"
    assert any("GPU frame time" in item for item in report["limitations"])
    assert all(item["passed"] for item in report["budgets"])


def test_benchmark_is_deterministic() -> None:
    first = _report()
    second = _report()
    assert first["deterministic_digest"] == second["deterministic_digest"]
    assert first["fixture"]["tower_digest"] == second["fixture"]["tower_digest"]
    assert first["culling"] == second["culling"]
    assert first["backends"]["pixi"]["data_path"] == second["backends"]["pixi"]["data_path"]


def test_benchmark_forces_a_budget_failure() -> None:
    result = _run("--json", "--budget-ms", "0.000001")
    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["status"] == "FAIL"
    floor_budget = next(
        item for item in report["budgets"] if item["name"] == "floor_projection_p95_ms"
    )
    assert floor_budget["passed"] is False


def test_benchmark_writes_out_file(tmp_path: Path) -> None:
    out = tmp_path / "benchmark.json"
    report = _report(("--json", "--out", str(out)))
    assert out.is_file()
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["deterministic_digest"] == report["deterministic_digest"]


def test_benchmark_rejects_bad_iterations() -> None:
    result = _run("--json", "--iterations", "0")
    assert result.returncode == 2
    assert "QOFFICE_BENCH_BAD_ITERATIONS" in result.stderr
