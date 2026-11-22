"""Hermetic isolation regressions for the packaged-install rehearsal.

These tests never install packages, access a live service, or mutate an operator home.
"""
from pathlib import Path

from scripts import clean_install_rehearsal as rehearsal


def test_isolated_plan_uses_throwaway_paths(tmp_path: Path):
    plan = rehearsal.build_plan(work_root=tmp_path / "rehearsal", profile="acceptance")
    assert rehearsal.isolation_findings(plan) == []
    assert Path(plan["profile_home"]) == tmp_path / "rehearsal" / "state" / "profiles" / "acceptance"


def test_rejects_escape_from_throwaway_root(tmp_path: Path):
    plan = rehearsal.build_plan(work_root=tmp_path / "rehearsal")
    plan["models"] = str(tmp_path / "outside-models")
    findings = rehearsal.isolation_findings(plan)
    assert any("models escapes work_root" in issue for issue in findings)


def test_rejects_live_operator_home_even_inside_work_root(tmp_path: Path, monkeypatch):
    live = tmp_path / "rehearsal" / "operator"
    monkeypatch.setenv("QUBIT_HOME", str(live))
    plan = rehearsal.build_plan(work_root=tmp_path / "rehearsal")
    plan["models"] = str(live / "models")
    findings = rehearsal.isolation_findings(plan)
    assert any("live QUBIT_HOME" in issue for issue in findings)


def test_rejects_forbidden_hermes_directory(tmp_path: Path):
    plan = rehearsal.build_plan(work_root=tmp_path / "rehearsal")
    plan["models"] = str(tmp_path / "rehearsal" / ".hermes" / "models")
    findings = rehearsal.isolation_findings(plan)
    assert any("forbidden .hermes" in issue for issue in findings)
