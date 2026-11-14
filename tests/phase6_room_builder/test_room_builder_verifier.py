"""The P6-02 machine gate script.

Hermetic: runs the verifier in a subprocess with an explicit PYTHONPATH; no
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
SCRIPT = REPO_ROOT / "scripts" / "verify_room_builder.py"


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


def test_verifier_passes_and_reports_every_check():
    result = _run("--json")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["schema"] == "qubit-office-room-builder-validation/1"
    assert report["task_id"] == "P6-02"
    assert report["status"] == "PASS"
    assert report["problems"] == []
    ids = {row["id"] for row in report["checks"]}
    for required in (
        "sample_valid",
        "preview_valid",
        "publish_v1",
        "publish_v2",
        "undo_restores_draft",
        "redo_restores_edit",
        "rollback_to_v1",
        "add_cell_within_contract",
        "add_desk_updates_capacity",
        "style_controls_recorded",
        "assignment_recorded",
        "clone_department_valid",
        "merge_departments_valid",
        "split_department_moves_rooms",
        "stretched_cell_refused",
        "missing_door_refused",
        "unreachable_room_refused",
        "cell_collision_refused",
        "paid_art_refused",
        "raw_sprite_sheet_refused",
        "department_style_collision_refused",
        "invalid_publish_refused_atomically",
        "asset_report_clean",
    ):
        assert required in ids
    assert report["counts"] == {
        "floors": 1,
        "rooms": 10,
        "cells": 10,
        "desks": 17,
        "portals": 9,
    }
    assert report["assets"]["paid_refs"] == 0


def test_verifier_usage_error_is_exit_two():
    result = _run("--not-a-flag")
    assert result.returncode == 2
