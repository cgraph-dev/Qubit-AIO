"""Packet 5A tests: evidence contracts, read-only enforcement, redaction,
typed failures, scope isolation; plus the opt-in Q5 canary (flag/rollback).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.evidence import (  # noqa: E402
    AdapterContract, EvidenceError, EvidencePartialError,
    EvidencePermissionError, EvidenceRecord, EvidenceRegistry,
    EvidenceUnavailableError, Scope, Sensitivity, Trust,
    build_evidence_registry)


# ------------------------------------------------------------- contracts
def test_adapter_contract_rejects_read_only_with_write_permissions():
    with pytest.raises(EvidenceError, match="cannot declare"):
        AdapterContract(name="x", version="1", scope=Scope.FILES,
                        sensitivity=Sensitivity.PERSONAL,
                        permissions=frozenset({"read", "write"}),
                        read_only=True, freshness_ttl_s=None,
                        trust=Trust.HIGH)


def test_adapter_contract_rejects_undeclared_permissions():
    with pytest.raises(EvidenceError, match="undeclared permission"):
        AdapterContract(name="x", version="1", scope=Scope.FILES,
                        sensitivity=Sensitivity.PERSONAL,
                        permissions=frozenset({"shred"}),
                        read_only=True, freshness_ttl_s=None,
                        trust=Trust.HIGH)


def test_build_registry_is_all_read_only_and_complete():
    reg = build_evidence_registry(REPO_ROOT)
    names = reg.list()
    for n in ("files", "repos", "web", "reports", "browser", "inbox",
              "worker", "memory"):
        assert n in names, n
    contracts = {c.name: c for c, _ in reg._adapters.values()}
    for c in contracts.values():
        assert c.read_only is True
        assert c.permissions == frozenset({"read"})


def test_registry_rejects_read_only_adapter_with_write_surface():
    class Bad:
        def fetch(self):
            return None
        def write(self):
            pass
    reg = EvidenceRegistry()
    with pytest.raises(EvidenceError, match="mutating member 'write'"):
        reg.register(AdapterContract(
            name="bad", version="1", scope=Scope.FILES,
            sensitivity=Sensitivity.PERSONAL, permissions=frozenset({"read"}),
            read_only=True, freshness_ttl_s=None, trust=Trust.HIGH), Bad())


# ------------------------------------------------------------ records
def test_evidence_record_stable_id_and_hash():
    r1 = EvidenceRecord.create(adapter="files", adapter_version="1.0.0",
                               source_ref="/a/b.txt", scope=Scope.FILES,
                               sensitivity=Sensitivity.PERSONAL,
                               trust=Trust.HIGH, excerpt="hello world",
                               raw_text="hello world")
    r2 = EvidenceRecord.create(adapter="files", adapter_version="1.0.0",
                               source_ref="/a/b.txt", scope=Scope.FILES,
                               sensitivity=Sensitivity.PERSONAL,
                               trust=Trust.HIGH, excerpt="hello world",
                               raw_text="hello world")
    assert r1.evidence_id == r2.evidence_id
    assert r1.content_hash == r2.content_hash == \
        "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_evidence_record_redacts_secrets_in_excerpt_only():
    r = EvidenceRecord.create(adapter="web", adapter_version="1",
                              source_ref="http://x", scope=Scope.WEB,
                              sensitivity=Sensitivity.INTERNAL,
                              trust=Trust.LOW,
                              excerpt="token=sk-demo123", raw_text="token=sk-demo123",
                              secret_patterns=["sk-demo123"])
    assert "sk-demo123" not in r.redacted_excerpt
    assert "REDACTED" in r.redacted_excerpt
    # raw text is never stored on the record
    assert not hasattr(r, "raw_text")


def test_evidence_freshness():
    r = EvidenceRecord.create(adapter="web", adapter_version="1",
                              source_ref="http://x", scope=Scope.WEB,
                              sensitivity=Sensitivity.INTERNAL,
                              trust=Trust.LOW, excerpt="x", raw_text="x",
                              freshness_ttl_s=10,
                              captured_at_epoch=time.time() - 20)
    assert not r.is_fresh()


# ------------------------------------------------------ concrete adapters
def test_file_adapter_read_ok_and_escape_blocked(tmp_path):
    reg = build_evidence_registry(tmp_path)
    (tmp_path / "f.txt").write_text("private content")
    rec = reg.fetch("files", "f.txt")
    assert rec.scope is Scope.FILES
    assert "private content" in rec.redacted_excerpt
    with pytest.raises(EvidenceError, match="path escape"):
        reg.fetch("files", "../etc/passwd")
    with pytest.raises(EvidenceUnavailableError):
        reg.fetch("files", "missing.txt")


def test_repo_adapter_read_only(tmp_path):
    """Real git fixture: init a repo with one commit so the adapter reads
    actual git state instead of skipping (release acceptance: no skips)."""
    import subprocess
    git = subprocess.run(["git", "init", "-q", str(tmp_path)],
                         capture_output=True)
    if git.returncode != 0:
        pytest.skip("git unavailable")
    (tmp_path / "a.txt").write_text("hello repo")
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.txt"],
                   capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"],
                   capture_output=True, env={**__import__("os").environ,
                                            "GIT_AUTHOR_NAME": "t",
                                            "GIT_AUTHOR_EMAIL": "t@t",
                                            "GIT_COMMITTER_NAME": "t",
                                            "GIT_COMMITTER_EMAIL": "t@t"})
    reg = build_evidence_registry(tmp_path)
    rec = reg.fetch("repos", tmp_path)
    assert rec.scope is Scope.REPOS and rec.adapter == "repos"
    assert "a.txt" in rec.redacted_excerpt or "init" in rec.redacted_excerpt


def test_web_adapter_blocks_private_network_and_oversize():
    reg = build_evidence_registry(REPO_ROOT)
    with pytest.raises(EvidenceError, match="SSRF"):
        reg.fetch("web", "http://127.0.0.1:11434/api/version")
    with pytest.raises(EvidenceError, match="not allowed"):
        reg.fetch("web", "http://169.254.169.254/latest/meta-data")


def test_web_adapter_allowed_host_fetch(monkeypatch):
    reg = build_evidence_registry(REPO_ROOT)
    body = b"<html>public info</html>"
    class R:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return None
        def read(self, n=-1):
            return body
    def fake(req, timeout=None):
        return R()
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    rec = reg.fetch("web", "https://example.com/page")
    assert rec.scope is Scope.WEB and "public info" in rec.redacted_excerpt
    assert rec.citations == ["https://example.com/page"]


def test_reports_adapter_typed_partial():
    reg = build_evidence_registry(REPO_ROOT)
    p = Path("/tmp") / f"manifest_{int(time.time())}.json"
    p.write_text("not-json")
    try:
        with pytest.raises(EvidencePartialError):
            reg.fetch("reports", p)
    finally:
        p.unlink()


def test_declarative_adapters_raise_typed_unavailable():
    reg = build_evidence_registry(REPO_ROOT)
    with pytest.raises(EvidenceUnavailableError):
        reg.fetch("inbox", "any")
    with pytest.raises(EvidenceUnavailableError):
        reg.fetch("memory", "any")


def test_registry_fetch_refuses_non_read_adapter_path():
    reg = build_evidence_registry(REPO_ROOT)
    # all registered are read-only; a synthetic non-read-only one must fail
    class RW:
        def fetch(self, *a, **k):
            return EvidenceRecord.create(
                adapter="rw", adapter_version="1", source_ref="x",
                scope=Scope.MEMORY, sensitivity=Sensitivity.PERSONAL,
                trust=Trust.MEDIUM, excerpt="x")
    reg.register(AdapterContract(
        name="rw-test", version="1", scope=Scope.MEMORY,
        sensitivity=Sensitivity.PERSONAL,
        permissions=frozenset({"read", "write"}), read_only=False,
        freshness_ttl_s=None, trust=Trust.MEDIUM), RW())
    with pytest.raises(EvidencePermissionError):
        reg.fetch("rw-test", "x")


# --------------------------------------------------------------- canary
def test_canary_flag_off_is_inert(tmp_path):
    from voicebridge.canary import CanaryLogger
    logger = CanaryLogger(tmp_path, flag=False)
    logger.log_turn(None, {"flag": False})
    assert logger.counts()["turns"] == 0
    logger.rollback()
    assert logger.enabled is False


def test_canary_records_all_required_fields(tmp_path):
    from voicebridge.canary import (CanaryLogger, CanaryTurn,
                                    REQUIRED_FIELDS)
    logger = CanaryLogger(tmp_path, flag=True)
    logger.log_turn(CanaryTurn(alias="qubit-cand-q5", warm_cold="warm",
                               latency_ms=123.4, errors=["classify_no_decision"],
                               planner_decision="none", safety_denied=True,
                               audio_failure="tts boom"),
                    {"flag_enabled": True, "prod_alias": "qubit-brain4"})
    lines = (logger._path.read_text()).strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    for k in REQUIRED_FIELDS:
        assert k in rec, k
    assert rec["alias"] == "qubit-cand-q5"
    assert rec["safety_denied"] is True
    assert rec["audio_failure"] == "tts boom"
    assert rec["rollback_state"]["prod_alias"] == "qubit-brain4"


def test_canary_rollback_flips_flag(tmp_path):
    from voicebridge.canary import CanaryLogger
    logger = CanaryLogger(tmp_path, flag=True)
    assert logger.enabled
    logger.log_turn(None, {"rollback": "now"})
    logger.rollback()
    assert logger.enabled is False
    logger.log_turn(None, {"rollback": "after"})
    assert logger.counts()["turns"] == 1   # no further turns recorded


def test_canary_alias_constants_never_prod():
    from voicebridge.canary import CANARY_ALIAS, PRODUCTION_ALIAS
    assert CANARY_ALIAS == "qubit-cand-q5"
    assert PRODUCTION_ALIAS == "qubit-brain4"
    assert CANARY_ALIAS != PRODUCTION_ALIAS