"""OC-D02 — workspace-conformance doctor / findings / attestation tests.

Hermetic throwaway workspaces; proves typed findings, policy-file loading,
deny behavior for forbidden/key files, deterministic content-addressed
attestations, and that secret values never appear in an attestation.
"""
from __future__ import annotations

import json

import pytest

from voicebridge.evaluation import policy_conformance as PC


def _clean_ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "pyproject.toml").write_text("[project]\nname='x'\n")
    (ws / "LICENSE").write_text("MIT\n")
    (ws / "module.py").write_text("X = 1\n")
    return ws


# --------------------------------------------------------------- defaults
def test_clean_workspace_passes_default_rules(tmp_path):
    rep = PC.doctor(_clean_ws(tmp_path))
    assert rep["state"] == "PASS", rep["findings"]
    assert rep["counts"][PC.FAIL] == 0
    assert PC.pass_conformance(rep) is True


def test_missing_required_file_fails(tmp_path):
    ws = _clean_ws(tmp_path)
    (ws / "LICENSE").unlink()
    rep = PC.doctor(ws)
    assert rep["state"] == "FAIL"
    missing = [f for f in rep["findings"] if f["state"] == PC.MISSING]
    assert missing and missing[0]["path"] == "LICENSE"
    assert PC.pass_conformance(rep) is False


def test_forbidden_globs_fail_and_do_not_leak_contents(tmp_path):
    ws = _clean_ws(tmp_path)
    secret_value = "sk-super-secret-1234567890abcdef"
    (ws / ".env").write_text(f"API_KEY={secret_value}\n")
    rules = [{"id": "keys", "kind": "forbidden_globs", "severity": "critical",
              "globs": [".env"]}]
    rep = PC.doctor(ws, rules)
    assert rep["state"] == "FAIL"
    blob = json.dumps(rep)
    assert secret_value not in blob
    assert any(f["path"] == ".env" for f in rep["findings"])


def test_file_modes_rejects_group_other_access(tmp_path):
    ws = _clean_ws(tmp_path)
    env = ws / ".env"
    env.write_text("X=1\n")
    env.chmod(0o644)
    rules = [{"id": "modes", "kind": "file_modes", "severity": "high",
              "paths": [".env"]}]
    assert PC.doctor(ws, rules)["state"] == "FAIL"
    env.chmod(0o600)
    assert PC.doctor(ws, rules)["state"] == "PASS"


def test_file_modes_missing_is_skipped(tmp_path):
    ws = _clean_ws(tmp_path)
    rules = [{"id": "modes", "kind": "file_modes", "severity": "high",
              "paths": [".env"]}]
    rep = PC.doctor(ws, rules)
    assert rep["state"] == "PASS"
    assert rep["counts"][PC.SKIPPED] == 1


# ----------------------------------------------------------- policy file
def test_policy_file_is_loaded(tmp_path):
    ws = _clean_ws(tmp_path)
    (ws / PC.POLICY_FILE).write_text(json.dumps({
        "rules": [{"id": "must-have", "kind": "required_files",
                   "severity": "high", "paths": ["module.py"]}]}))
    rep = PC.doctor(ws)
    assert rep["ruleset"] == ["must-have"]
    assert rep["state"] == "PASS"


def test_malformed_policy_file_is_typed(tmp_path):
    ws = _clean_ws(tmp_path)
    (ws / PC.POLICY_FILE).write_text("{not json")
    with pytest.raises(PC.ConformanceError) as ei:
        PC.doctor(ws)
    assert ei.value.code == "POLICY_MALFORMED"


def test_bad_rule_rejected():
    with pytest.raises(PC.ConformanceError):
        PC.validate_rules([{"id": "x", "kind": "nope"}])
    with pytest.raises(PC.ConformanceError):
        PC.validate_rules([{"id": "x", "kind": "required_files",
                            "severity": "ultra"}])


def test_nonexistent_workspace_is_typed(tmp_path):
    with pytest.raises(PC.ConformanceError) as ei:
        PC.findings(tmp_path / "nope")
    assert ei.value.code == "WORKSPACE_UNREADABLE"


# ----------------------------------------------------------- attestation
def test_attestation_is_deterministic_and_content_addressed(tmp_path):
    ws = _clean_ws(tmp_path)
    rules = [{"id": "r", "kind": "required_files", "severity": "high",
              "paths": ["pyproject.toml"]}]
    a = PC.attestation(ws, rules)
    b = PC.attestation(ws, rules)
    assert a["attestation_id"] == b["attestation_id"]
    assert len(a["attestation_id"]) == 64
    assert a["immutable"] is True
    c = PC.attestation(ws, [{"id": "r", "kind": "required_files",
                             "severity": "high", "paths": ["LICENSE"]}])
    assert c["attestation_id"] != a["attestation_id"]


def test_attestation_carries_no_file_contents(tmp_path):
    ws = _clean_ws(tmp_path)
    (ws / "module.py").write_text("TOKEN = 'do-not-leak-abcdef123456'\n")
    att = PC.attestation(ws, [{"id": "r", "kind": "required_files",
                               "severity": "high", "paths": ["module.py"]}])
    assert "do-not-leak" not in json.dumps(att)
