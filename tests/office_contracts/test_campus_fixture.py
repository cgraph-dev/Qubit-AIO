"""P0-07 — the campus fixture is a bounded, non-production template.

Runs the generator CLI end to end: the measured invariants, the bounded output
(no 3,075-desk projection), the template/no-tenant flags, and a deliberately
corrupted registry proving ``--check`` fails a wrong count.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "generate_campus_fixture.py"

EXPECTED = {
    "floors": 100,
    "wings": 5,
    "rooms": 300,
    "cells": 575,
    "desks": 3075,
    "staffed_agents": 0,
}


def _run(*args: str) -> subprocess.CompletedProcess:
    env = {"PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def test_check_passes_with_measured_invariants() -> None:
    result = _run("--check")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["check"] == "PASS"
    for key, expected in EXPECTED.items():
        assert payload["counts"][key] == expected, (key, payload["counts"])
    assert len(payload["digest"]) == 64


def test_check_writes_nothing(tmp_path: Path) -> None:
    sentinel = tmp_path / "not-written.json"
    result = _run("--check")
    assert result.returncode == 0
    assert not sentinel.exists()
    assert not (tmp_path / "campus.json").exists()


def test_out_is_bounded_template_without_tenant_ids(tmp_path: Path) -> None:
    out = tmp_path / "campus.json"
    result = _run("--out", str(out))
    assert result.returncode == 0, result.stderr
    assert out.is_file()

    fixture = json.loads(out.read_text(encoding="utf-8"))
    assert fixture["template"] is True
    assert fixture["production"] is False
    assert fixture["staffed_agents"] == 0
    for key, expected in EXPECTED.items():
        assert fixture["counts"][key] == expected

    assert len(fixture["floors"]) == 100
    assert len(fixture["wings"]) == 5
    for floor in fixture["floors"]:
        assert floor["staffed_agents"] == 0
        assert set(floor) >= {"floor_id", "index", "wing", "name", "active",
                              "rooms", "desks", "cells", "staffed_agents"}
        # Bounded: no per-room or per-desk projection is carried.
        assert "room_list" not in floor and "desks_detail" not in floor

    # The bounded projection must be far smaller than the 3,075-desk tower.
    assert out.stat().st_size < 200_000
    text = json.dumps(fixture)
    assert re.search(r"\b(?:tenant|business|agent)[_-][0-9a-f]{6,}\b", text, re.I) is None


def test_corrupted_registry_fails_check() -> None:
    result = _run("--check", "--fixture-bad", "desks")
    assert result.returncode == 1
    assert "counts.desks" in result.stderr


def test_corrupted_staffed_agents_fails_check() -> None:
    result = _run("--check", "--fixture-bad", "staffed")
    assert result.returncode == 1
    assert "staffed_agents" in result.stderr


def test_no_mode_is_usage_error() -> None:
    result = _run()
    assert result.returncode != 0
    assert "choose --check and/or --out" in result.stderr
