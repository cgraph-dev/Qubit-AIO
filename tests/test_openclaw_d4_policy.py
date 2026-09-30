"""D4 — OpenClaw policy rows closed by real capability + negative proof.

* OC-D02 workspace-conformance doctor / findings / attestation — the one
  authority ``voicebridge/evaluation/policy_conformance.py`` plus the plugin
  runtime ``policy_*`` rule broker (``voicebridge/plugin_runtime.py``).
* OC-D08 secrets egress — ``voicebridge/proxy_secrets.py`` supplies the
  Qubit-safe equivalent of a forwarding proxy: a deny-by-default allowlist,
  reference-only secret registry, and stream substitution that fails closed.
  No forwarding server exists (``proxy_status.refuse_start``); provider calls
  flow through the brokered gateway.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from voicebridge import plugin_runtime as PR
from voicebridge import proxy_secrets as PS
from voicebridge.evaluation import policy_conformance as PC


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ---------------------------------------------------------------- OC-D02
def test_ocd02_conformance_doctor_findings_and_attestation(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "pyproject.toml").write_text("[project]\n")
    (ws / "LICENSE").write_text("MIT\n")
    rules = [
        {"id": "need-license", "kind": "required_files", "severity": "high",
         "paths": ["pyproject.toml", "LICENSE"]},
        {"id": "no-keys", "kind": "forbidden_globs", "severity": "critical",
         "globs": ["*.pem"]},
    ]
    report = PC.doctor(ws, rules)
    assert report["state"] == "PASS"
    assert PC.pass_conformance(report) is True
    att1 = PC.attestation(ws, rules)
    att2 = PC.attestation(ws, rules)
    assert att1["attestation_id"] == att2["attestation_id"]
    assert att1["immutable"] is True

    (ws / "id.pem").write_text("private\n")
    failed = PC.doctor(ws, rules)
    assert failed["state"] == "FAIL"
    assert failed["critical"], failed
    assert PC.pass_conformance(failed) is False
    # findings carry names/paths only, never file contents
    assert "private" not in json.dumps(failed)


def test_ocd02_malformed_policy_is_typed(tmp_path):
    with pytest.raises(PC.ConformanceError) as exc:
        PC.validate_rules([{"id": "x", "kind": "not-a-kind"}])
    assert exc.value.code == "POLICY_MALFORMED"
    with pytest.raises(PC.ConformanceError) as exc:
        PC.validate_rules([
            {"id": "dup", "kind": "no_secrets"},
            {"id": "dup", "kind": "no_secrets"},
        ])
    assert exc.value.code == "POLICY_MALFORMED"


def test_ocd02_plugin_policy_broker_rules_doctor_attest(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("value = 1  # TODO: fix\n")
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.policy_add_rule(cfg, rule={"id": "no-todo", "pattern": "TODO",
                                      "level": "deny"})
    assert exc.value.code == PR.POLICY_CONFIRM_CODE
    PR.policy_add_rule(cfg, rule={"id": "no-todo", "pattern": "TODO",
                                  "level": "deny", "message": "no todos"},
                       confirmed=True)
    assert PR.policy_rules(cfg)["rules"][0]["id"] == "no-todo"
    report = PR.policy_doctor(cfg, str(ws))
    assert report["state"] == "FAIL" and report["deny"] >= 1
    assert any(f["rule"] == "no-todo" for f in report["findings"])
    # only a confirmed human principal may attest
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.policy_attest(cfg, str(ws), requested_by="model")
    assert exc.value.code == PR.POLICY_ATTEST_ACTOR_CODE
    out = PR.policy_attest(cfg, str(ws), requested_by="user")
    assert out["attestation"]["digest"]


# ---------------------------------------------------------------- OC-D08
def test_ocd08_registry_stores_references_never_values():
    registry = PS.SecretRegistry(environ={"API_KEY": "sk-live-secret"})
    with pytest.raises(PS.EgressError) as exc:
        registry.register("api", "sk-live-secret")
    assert exc.value.code == PS.EGRESS_BAD_REFERENCE
    registry.register("api", "env:API_KEY")
    status = registry.status()
    assert "sk-live-secret" not in json.dumps(status)
    assert registry.resolve("api") == "sk-live-secret"
    with pytest.raises(PS.EgressError) as exc:
        registry.resolve("missing")
    assert exc.value.code == PS.EGRESS_SECRET_MISSING


def test_ocd08_egress_deny_by_default_and_stream_substitution():
    registry = PS.SecretRegistry(environ={"API_KEY": "sk-live-secret"})
    registry.register("api", "env:API_KEY")
    policy = PS.EgressPolicy(allowed_hosts=["api.example.test"])
    assert policy.check("api.example.test")["allowed"] is True
    assert policy.check("evil.example.test")["allowed"] is False

    out = PS.substitute("Authorization: Bearer {{secret:api}}", policy,
                        registry, "api.example.test")
    assert out == "Authorization: Bearer sk-live-secret"

    with pytest.raises(PS.EgressError) as exc:
        PS.substitute("x {{secret:api}}", policy, registry, "evil.example.test")
    assert exc.value.code == PS.EGRESS_DENIED

    chunks = ["Bearer {{secret:", "api}} ok"]
    joined = "".join(PS.substitute_stream(chunks, policy, registry,
                                          "api.example.test"))
    assert joined == "Bearer sk-live-secret ok"


def test_ocd08_fails_closed_on_unknown_and_incomplete_placeholders():
    registry = PS.SecretRegistry(environ={"API_KEY": "sk-live-secret"})
    registry.register("api", "env:API_KEY")
    policy = PS.EgressPolicy(allowed_hosts=["api.example.test"])
    with pytest.raises(PS.EgressError) as exc:
        PS.substitute("{{secret:nope}}", policy, registry, "api.example.test")
    assert exc.value.code == PS.EGRESS_SECRET_MISSING
    with pytest.raises(PS.EgressError) as exc:
        PS.substitute("{{secret:api", policy, registry, "api.example.test")
    assert exc.value.code == PS.EGRESS_INCOMPLETE_PLACEHOLDER

    def _stream():
        yield "{{secret:api"

    with pytest.raises(PS.EgressError) as exc:
        list(PS.substitute_stream(_stream(), policy, registry,
                                  "api.example.test"))
    assert exc.value.code == PS.EGRESS_INCOMPLETE_PLACEHOLDER


def test_ocd08_no_forwarding_server_is_ever_started():
    from voicebridge import proxy_status

    refused = proxy_status.refuse_start()
    assert refused["state"] in ("REFUSED", "REJECTED")
    assert refused["code"] == proxy_status.NO_SERVER_CODE
