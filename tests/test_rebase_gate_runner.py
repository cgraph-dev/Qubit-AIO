# tests/test_rebase_gate_runner.py
"""Contract tests for ``scripts/rebase_gate.sh`` -- the v0.21.5 rebase gate.

The gate itself runs the (slow) rebase/rebrand suite, the deterministic
generated-artifact ``--check``s and ``run_audit.py --check``.  These tests pin
the runner's *interface* without paying that cost: they exercise ``--help`` and
``--dry-run`` (which prints the exact commands and runs nothing), the
reference-check opt-in, non-zero propagation when a step fails, and the
``scripts/tmp_reclaim.sh`` hint under simulated disk pressure.

Hermetic: ``HOME`` is redirected to a throwaway path so the gate can never
discover or read the operator's real reference install; the interpreter defaults
to the one running this suite.  Nothing is written outside ``tmp_path``/``/tmp``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "rebase_gate.sh"

RECLAIM = "scripts/tmp_reclaim.sh"

SUITE_TARGETS = (
    "tests/test_rebase_",
    "tests/test_rebrand_",
    "tests/test_overlay_regressions.py",
    "tests/test_config_alignment.py",
    "tests/test_forensic_audit_harness.py",
)

HERMETIC_ARTIFACTS = (
    "page_coverage.py --check",
    "identifier_rename.py --check",
    "rebuild_ledger_qubit.py --check",
    "voice_inventory_coverage.py --check",
    "route_ownership_matrix.py --check",
)

REFERENCE_ARTIFACTS = (
    "parity_inventory.py --check",
    "hermes_vendor_manifest.py --check",
)


def _run(*args: str, extra_env: dict | None = None, timeout: int = 120):
    env = dict(os.environ)
    env["HOME"] = "/tmp/qubit-gate-test-home"
    env["QUBIT_GATE_PYTHON"] = sys.executable
    env["QUBIT_GATE_BASETEMP"] = "/tmp/qubit-rebase-gate-test"
    # Keep the runner hermetic: no real reference home, no opt-in.
    for key in ("QUBIT_PARITY_REF_HOME", "QUBIT_GATE_REFERENCE"):
        env.pop(key, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=timeout,
    )


def test_script_exists_and_is_executable():
    assert SCRIPT.is_file(), f"missing {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} is not executable"


def test_help_lists_the_three_steps_and_reclaim():
    res = _run("--help")
    assert res.returncode == 0, res.stderr
    assert "rebase_gate.sh" in res.stdout
    for step in ("suite", "artifacts", "audit"):
        assert step in res.stdout, f"--help does not describe the {step!r} step"
    assert RECLAIM in res.stdout, "--help does not mention scripts/tmp_reclaim.sh"


def test_dry_run_suite_targets_the_rebase_gate_files():
    res = _run("--dry-run", "--only", "suite")
    assert res.returncode == 0, res.stderr
    assert "DRY:" in res.stdout
    assert "-m pytest" in res.stdout
    for target in SUITE_TARGETS:
        assert target in res.stdout, f"suite target missing: {target}"
    assert "-p no:cacheprovider" in res.stdout
    assert "--basetemp=" in res.stdout


def test_dry_run_artifacts_include_the_hermetic_checks():
    res = _run("--dry-run", "--only", "artifacts")
    assert res.returncode == 0, res.stderr
    for check in HERMETIC_ARTIFACTS:
        assert check in res.stdout, f"artifact check missing: {check}"
    # Reference-pinned checks are opt-in; the default gate stays hermetic.  The
    # skip notice names them, so assert on the executed (DRY:) commands only.
    executed = [ln for ln in res.stdout.splitlines() if ln.startswith("DRY:")]
    for check in REFERENCE_ARTIFACTS:
        assert all(check not in ln for ln in executed), (
            f"{check} ran without QUBIT_GATE_REFERENCE=1")


def test_dry_run_audit_runs_run_audit_check():
    res = _run("--dry-run", "--only", "audit")
    assert res.returncode == 0, res.stderr
    assert "forensic_audit/run_audit.py --check" in res.stdout


def test_reference_artifact_checks_are_opt_in():
    res = _run("--dry-run", "--only", "artifacts",
               extra_env={"QUBIT_GATE_REFERENCE": "1"})
    assert res.returncode == 0, res.stderr
    for check in REFERENCE_ARTIFACTS:
        assert check in res.stdout, f"opt-in reference check missing: {check}"


def test_space_pressure_references_tmp_reclaim():
    res = _run("--dry-run", "--only", "audit",
               extra_env={"QUBIT_GATE_MIN_FREE_MB": "999999999"})
    assert res.returncode == 0, res.stderr
    combined = res.stdout + res.stderr
    assert RECLAIM in combined, "no tmp_reclaim hint under simulated pressure"
    assert "tmp_reclaim.sh --aggressive" in combined


def test_nonzero_when_a_step_fails():
    res = _run("--only", "suite",
               extra_env={"QUBIT_GATE_PYTHON": "/bin/false"})
    assert res.returncode != 0, "a failing step did not fail the gate"
    assert "FAIL" in (res.stdout + res.stderr)


def test_dry_run_never_executes_the_steps():
    # `/bin/false` would fail every step if the commands were actually run;
    # --dry-run must print them and exit 0.
    res = _run("--dry-run", extra_env={"QUBIT_GATE_PYTHON": "/bin/false"})
    assert res.returncode == 0, res.stderr
    assert res.stdout.count("DRY:") >= 3


def test_unknown_only_value_is_rejected():
    res = _run("--only", "nope")
    assert res.returncode == 2
    assert "unknown" in res.stderr.lower() or "--only must be" in res.stderr


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
