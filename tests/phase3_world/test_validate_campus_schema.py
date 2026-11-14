"""The P3-01/P3-03 machine gate script.

Hermetic: runs the validator in a subprocess with an explicit PYTHONPATH; no
store, network or live service is touched.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "validate_campus_schema.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONDONTWRITEBYTECODE"}
    }
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["TMPDIR"] = "/tmp"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )


def test_validator_passes_and_reports_the_full_campus():
    result = _run("--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "PASS"
    assert report["task_ids"] == ["P3-01", "P3-03"]
    assert report["campus"]["counts"] == {
        "floors": 100,
        "wings": 5,
        "active_floors": 23,
        "dormant_floors": 77,
        "rooms": 300,
        "cells": 575,
        "desks": 3075,
        "staffed_agents": 0,
    }
    assert report["trading"]["floor_id"] == "floor-023"
    assert report["trading"]["missing"] == []
    assert len(report["trading"]["rooms"]) == 16
    assert report["map"]["floors"] == 100
    assert report["map"]["rooms"] == 300
    assert report["map"]["desks"] == 3075
    assert report["map"]["capacity"] == 3075
    assert report["map"]["map_version"] == 1
    assert report["problems"] == {"map": [], "tower": []}
    assert len(report["map"]["digest"]) == 64


def test_validator_usage_error_is_exit_two():
    result = _run("--not-a-flag")
    assert result.returncode == 2
