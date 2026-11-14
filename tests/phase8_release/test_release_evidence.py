"""P8-05 — release evidence verdicts (hermetic fixtures only)."""
from __future__ import annotations

from pathlib import Path

import pytest

import p8r_fixture as fx


RE = fx.load_script("release_evidence")


@pytest.fixture
def now():
    return RE.parse_time(fx.NOW)


def evaluate(root: Path, now):
    return RE.evaluate(root, now=now, max_age_days=30.0,
                       commit=fx.FROZEN_COMMIT, tree=fx.FROZEN_TREE)


def test_missing_phase_manifest_is_not_pass(tmp_path, now):
    root = fx.valid_root(tmp_path, phases=range(8))
    (root / "evidence" / "phase-8" / "manifest.json").unlink(missing_ok=True)

    report = evaluate(root, now)

    assert report["status"] != "PASS"
    assert report["status"] == "READY_FOR_OWNER_DECISION"
    assert any("phase 8 manifest" in item for item in report["missing"])
    assert report["phases"]["missing"] == [8]


def test_phase_manifest_with_failing_status_is_a_fail(tmp_path, now):
    root = fx.valid_root(tmp_path)
    fx.write_phase_manifest(root, 3, status="FAIL")

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    assert any("phase 3 manifest" in problem for problem in report["problems"])


def test_stale_phase_manifest_is_refused(tmp_path, now):
    root = fx.valid_root(tmp_path)
    fx.write_phase_manifest(root, 2, generated_at="2020-01-01T00:00:00Z")

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    assert any("days old" in problem for problem in report["problems"])


def test_tampered_evidence_hash_is_refused(tmp_path, now):
    root = fx.valid_root(tmp_path)
    evidence = root / "evidence" / "phase-4" / "P4-evidence.json"
    evidence.write_text('{"task_id": "P4-01", "status": "PASS", "x": 1}\n',
                        encoding="utf-8")

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    assert any("sha256 does not match" in problem
               for problem in report["problems"])


def test_valid_complete_fixture_passes(tmp_path, now):
    root = fx.valid_root(tmp_path)

    report = evaluate(root, now)

    assert report["status"] == "PASS", report["problems"]
    assert report["missing"] == []
    assert report["problems"] == []
    assert report["phases"]["present"] == list(range(9))
    assert report["dependency_lock"]["ok"] is True
    assert report["final_matrix"]["ok"] is True
    assert report["owner_decision"]["ok"] is True
    assert report["owner_decision"]["signed"] is True


def test_report_is_deterministic_apart_from_timestamp(tmp_path, now):
    root = fx.valid_root(tmp_path)

    first = evaluate(root, now)
    second = evaluate(root, now)

    assert first == second


def test_write_report_creates_release_json(tmp_path, now):
    root = fx.valid_root(tmp_path)
    report = evaluate(root, now)

    out = RE.write_report(report, root / "evidence" / "phase-8" / "release.json")

    assert out.is_file()
    written = RE._load_json(out)[0]
    assert written["schema"] == RE.RELEASE_SCHEMA
    assert written["status"] == "PASS"
    assert written["release_task"] == "P8-05"
