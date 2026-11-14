"""P8-05 — dependency lock validation and final-matrix SHA/scope checks."""
from __future__ import annotations

from pathlib import Path

import pytest

import p8r_fixture as fx


RE = fx.load_script("release_evidence")
FM = fx.load_script("final_matrix")


@pytest.fixture
def now():
    return RE.parse_time(fx.NOW)


def evaluate(root: Path, now):
    return RE.evaluate(root, now=now, max_age_days=30.0,
                       commit=fx.FROZEN_COMMIT, tree=fx.FROZEN_TREE)


def test_imported_dependency_without_verified_license_is_unapproved(
        tmp_path, now):
    root = fx.valid_root(tmp_path)
    fx.write_dependency_lock(root, fx.dependency_lock([{
        "id": "copied-code",
        "name": "Copied Code",
        "revision": "b" * 40,
        "license_status": "absent",
        "reuse_status": "imported",
        "permitted_use": "Vendored.",
        "notices": True,
    }]))

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    assert report["dependency_lock"]["ok"] is False
    assert any("copied-code" in item
               for item in report["dependency_lock"]["unapproved"])


def test_imported_dependency_without_revision_or_notices_is_unapproved(
        tmp_path, now):
    root = fx.valid_root(tmp_path)
    fx.write_dependency_lock(root, fx.dependency_lock([{
        "id": "half-recorded",
        "name": "Half Recorded",
        "license_status": "present",
        "reuse_status": "imported",
        "permitted_use": "Vendored.",
    }]))

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    unapproved = " ".join(report["dependency_lock"]["unapproved"])
    assert "recorded revision" in unapproved
    assert "notices" in unapproved


def test_malformed_dependency_lock_is_refused(tmp_path, now):
    root = fx.valid_root(tmp_path)
    lock_path = root / "docs" / "QUBIT-OFFICE-DEPENDENCY-LOCK.json"
    lock_path.write_text("{not json", encoding="utf-8")

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    assert report["dependency_lock"]["ok"] is False


def test_final_matrix_on_the_wrong_commit_is_refused(tmp_path, now):
    root = fx.valid_root(tmp_path)
    fx.write_final_matrix(root, fx.final_matrix(commit="f" * 40))

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    assert any("not the frozen commit" in problem
               for problem in report["problems"])
    assert report["final_matrix"]["present"] is True


def test_final_matrix_on_the_wrong_tree_is_refused(tmp_path, now):
    root = fx.valid_root(tmp_path)
    fx.write_final_matrix(root, fx.final_matrix(tree="f" * 40))

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    assert any("not the frozen tree" in problem
               for problem in report["problems"])


def test_missing_final_matrix_is_reported_as_required(tmp_path, now):
    root = fx.valid_root(tmp_path, include_matrix=False)

    report = evaluate(root, now)

    assert report["status"] == "READY_FOR_OWNER_DECISION"
    assert any("FINAL_MATRIX_REQUIRED" in item for item in report["missing"])
    assert report["final_matrix"]["present"] is False


def test_final_matrix_that_claims_repo_reach_is_refused(tmp_path, now):
    root = fx.valid_root(tmp_path)
    fx.write_final_matrix(
        root, fx.final_matrix(scope="Full repo-wide matrix"))

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    assert any("office feature matrix" in problem
               for problem in report["problems"])


def test_final_matrix_without_explicit_scope_boundary_is_refused(tmp_path, now):
    root = fx.valid_root(tmp_path)
    fx.write_final_matrix(
        root, fx.final_matrix(scope="Qubit Office feature matrix"))

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    assert any("not the repo-wide matrix" in problem
               for problem in report["problems"])


def test_final_matrix_with_failures_is_refused(tmp_path, now):
    root = fx.valid_root(tmp_path)
    fx.write_final_matrix(root, fx.final_matrix(failed=2, status="FAIL"))

    report = evaluate(root, now)

    assert report["status"] == "FAIL"
    assert any("2 failed test" in problem for problem in report["problems"])


def test_final_matrix_scope_text_is_explicit():
    assert "office feature matrix" in FM.SCOPE.lower()
    assert "not the repo-wide matrix" in FM.SCOPE.lower()


def test_collect_targets_finds_the_declared_matrix(tmp_path):
    for name in ("phase1_x", "phase8_release"):
        (tmp_path / "tests" / name).mkdir(parents=True)
    (tmp_path / "tests" / "office_contracts").mkdir(parents=True)
    for name in ("test_business_a.py", "test_office_business.py",
                 "test_office_runner.py"):
        (tmp_path / "tests" / name).write_text("", encoding="utf-8")

    existing, missing = FM.collect_targets(tmp_path)

    assert missing == []
    joined = "\n".join(existing)
    assert "tests/phase1_x" in joined
    assert "tests/phase8_release" in joined
    assert "tests/office_contracts" in joined
    assert "tests/test_business_a.py" in joined
    assert "tests/test_office_business.py" in joined
    assert "tests/test_office_runner.py" in joined


def test_collect_targets_reports_missing_globs(tmp_path):
    (tmp_path / "tests").mkdir()

    existing, missing = FM.collect_targets(tmp_path)

    assert existing == []
    assert "tests/phase*" in missing
    assert "tests/office_contracts" in missing


def test_build_evidence_passes_only_for_clean_zero_failure_run():
    passing = FM.build_evidence(
        commit="a" * 40, tree="b" * 40, working_tree="clean",
        targets=["tests/phase8_release"], command="pytest -q",
        exit_code=0, passed=11, failed=0)
    dirty = FM.build_evidence(
        commit="a" * 40, tree="b" * 40, working_tree="dirty",
        targets=["tests/phase8_release"], command="pytest -q",
        exit_code=0, passed=11, failed=0)

    assert passing["status"] == "PASS"
    assert dirty["status"] == "FAIL"
    assert any("not clean" in problem for problem in dirty["problems"])


@pytest.mark.parametrize("stdout,expected", [
    ("11 passed in 0.12s\n", (11, 0)),
    ("9 passed, 2 failed in 1.00s\n", (9, 2)),
    ("1 failed in 0.10s\n", (0, 1)),
    ("no tests ran in 0.01s\n", (0, 0)),
])
def test_parse_counts(stdout, expected):
    assert FM.parse_counts(stdout) == expected
