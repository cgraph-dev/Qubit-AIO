"""Artifact-install E2E gate: hermetic contract + opt-in real install.

The real gate installs a built wheel into a throwaway venv with a throwaway
HOME/prefix and probes the *installed* artifact. These tests are hermetic: they
exercise the isolation planner, the wheel/dashboard validation, the typed
report/exit contract, and the CLI/report writing. The one real build/install
test is opt-in (``QUBIT_ARTIFACT_E2E=1``) because it needs a build backend and
network for declared dependencies.

Nothing here touches the live service, the operator's real HOME/config/state,
or ``~/.hermes``.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

GATE = REPO / "scripts" / "artifact_install_e2e.py"
ENGINE = REPO / "scripts" / "clean_install_rehearsal.py"


def _engine():
    return importlib.import_module("scripts.clean_install_rehearsal")


def _write_wheel(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, body in members.items():
            z.writestr(name, body)
    return path


def _fake_repo(tmp_path: Path, *, dashboard: bool = False) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(
        "[project]\nname='x'\n[project.scripts]\n"
        "qubit='voicebridge.cli:main'\n"
        "voicebridge='voicebridge.cli:main'\n"
        "qubit-dev='voicebridge.terminal:main'\n"
        "qubit-agent='voicebridge.qubit_runtime:entry'\n",
        encoding="utf-8")
    if dashboard:
        d = repo / "frontend" / "qubit-dist"
        d.mkdir(parents=True)
        (d / "index.html").write_text("<html></html>", encoding="utf-8")
    return repo


# ---------------------------------------------------------------------------
# isolation plan
# ---------------------------------------------------------------------------
def test_profile_plan_is_isolated_and_under_work_root(tmp_path):
    R = _engine()
    work = tmp_path / "work"
    plan = R.build_plan(REPO, work_root=work, profile="minimal")
    assert plan["profile"] == "minimal"
    assert Path(plan["profile_home"]) == work / "state" / "profiles" / "minimal"
    assert Path(plan["profile_home"]).is_relative_to(work)
    assert R.isolation_findings(plan) == []


def test_isolation_rejects_a_live_qubit_home(tmp_path, monkeypatch):
    R = _engine()
    live = tmp_path / "live-qubit-home"
    live.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(live))
    plan = R.build_plan(REPO, work_root=live)
    findings = R.isolation_findings(plan)
    assert findings
    assert any("live QUBIT_HOME" in f for f in findings)


def test_isolation_rejects_profile_home_escaping_work_root(tmp_path):
    R = _engine()
    plan = R.build_plan(REPO, work_root=tmp_path / "work", profile="p")
    plan["profile_home"] = str(tmp_path / "elsewhere" / "p")
    findings = R.isolation_findings(plan)
    assert any("profile_home escapes work_root" in f for f in findings)


def test_live_home_snapshot_detects_a_write(tmp_path):
    R = _engine()
    home = tmp_path / "home"
    home.mkdir()
    before = R._home_snapshot(home)
    (home / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    after = R._home_snapshot(home)
    assert before != after
    assert "config.yaml" in after["markers"]


# ---------------------------------------------------------------------------
# wheel / dashboard validation
# ---------------------------------------------------------------------------
def test_check_rejects_missing_wheel(tmp_path):
    R = _engine()
    out = R.check(REPO, work_root=tmp_path / "work",
                  wheel=tmp_path / "missing.whl", without_dashboard=True)
    assert out["state"] == "FAIL"
    assert out["verdict"] == "FAIL"
    assert any("not a readable .whl" in r for r in out["reasons"])


def test_check_rejects_dashboardless_wheel_unless_opted_out(tmp_path):
    R = _engine()
    wheel = _write_wheel(tmp_path / "plain-0.0.1-py3-none-any.whl",
                         {"pkg/__init__.py": b""})
    failed = R.check(REPO, work_root=tmp_path / "work1", wheel=wheel)
    assert failed["state"] == "FAIL"
    assert any("dashboard-less" in r for r in failed["reasons"])

    allowed = R.check(REPO, work_root=tmp_path / "work2", wheel=wheel,
                      without_dashboard=True)
    assert allowed["state"] == "OK", allowed


def test_check_rejects_wheel_sweeping_node_modules(tmp_path):
    R = _engine()
    wheel = _write_wheel(
        tmp_path / "swept-0.0.1-py3-none-any.whl",
        {"pkg/__init__.py": b"",
         "pkg/_data/frontend/qubit-dist/index.html": b"<html></html>",
         "pkg/node_modules/dev-only/package.json": b"{}"})
    out = R.check(REPO, work_root=tmp_path / "work", wheel=wheel)
    assert out["state"] == "FAIL"
    assert any("node_modules" in r for r in out["reasons"])


def test_check_without_dashboard_does_not_require_the_bundle(tmp_path):
    R = _engine()
    repo = _fake_repo(tmp_path, dashboard=False)
    out = R.check(repo, work_root=tmp_path / "work",
                  without_dashboard=True)
    assert out["state"] == "OK", out
    assert out["dashboard_bundle_present"] is False
    assert out["without_dashboard"] is True


def test_run_missing_wheel_fails_before_creating_a_venv(tmp_path):
    R = _engine()
    work = tmp_path / "work"
    out = R.run(REPO, work_root=work, wheel=tmp_path / "missing.whl")
    assert out["state"] == "FAIL"
    assert out["verdict"] == "FAIL"
    assert not (work / "venv").exists()


# ---------------------------------------------------------------------------
# CLI + JSON report
# ---------------------------------------------------------------------------
def _run_gate(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GATE), *args],
                          capture_output=True, text=True, env=env,
                          timeout=180)


def test_gate_help_lists_the_release_flags():
    r = _run_gate("--help")
    assert r.returncode == 0, r.stderr
    for token in ("--wheel", "--profile", "--without-models",
                  "--without-dashboard", "--require-network", "--report"):
        assert token in r.stdout, token


def test_gate_check_writes_a_typed_json_report(tmp_path):
    work = tmp_path / "work"
    report = tmp_path / "report.json"
    r = _run_gate("--check", "--without-dashboard", "--work-root", str(work),
                  "--report", str(report), "--json")
    assert r.returncode == 0, r.stdout + r.stderr
    body = json.loads(r.stdout)
    assert body["schema"] == "qubit.artifact-install-e2e.v1"
    assert body["gate"] == "artifact-install-e2e"
    assert body["verdict"] == "PASS"
    assert body["plan"]["work_root"] == str(work.resolve())
    assert body["isolation_findings"] == []
    assert report.is_file()
    on_disk = json.loads(report.read_text(encoding="utf-8"))
    assert on_disk["schema"] == body["schema"]


def test_gate_fail_exit_and_report_are_typed(tmp_path):
    report = tmp_path / "fail.json"
    r = _run_gate("--wheel", str(tmp_path / "missing.whl"),
                  "--without-dashboard", "--work-root", str(tmp_path / "work"),
                  "--report", str(report), "--json")
    assert r.returncode == 2, r.stdout + r.stderr
    body = json.loads(r.stdout)
    assert body["verdict"] == "FAIL"
    assert body["state"] == "FAIL"
    assert report.is_file()
    assert json.loads(report.read_text(encoding="utf-8"))["verdict"] == "FAIL"


def test_engine_source_pins_health_version_and_live_home_guard():
    text = ENGINE.read_text(encoding="utf-8")
    assert "/api/health" in text
    assert "VB_FORBIDDEN_HOMES" in text
    assert "addaudithook" in text
    assert 'qubit"), "--version"' in text or "qubit --version" in text
    assert "QUBIT_HOME" in text and "HERMES_HOME" in text


# ---------------------------------------------------------------------------
# opt-in real build/install (needs build backend + network for deps)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    os.environ.get("QUBIT_ARTIFACT_E2E") != "1",
    reason="set QUBIT_ARTIFACT_E2E=1 to run the real build/install gate")
def test_real_artifact_install_e2e(tmp_path):
    R = _engine()
    out = R.run(REPO, work_root=tmp_path / "work", require_network=True,
                without_dashboard=not (REPO / "frontend/qubit-dist/index.html").is_file())
    assert out["state"] == "OK", out
    assert out["verdict"] == "PASS"
    assert out["wheel_sha256"]
    assert out["isolation"]["live_homes_unchanged"] is True
