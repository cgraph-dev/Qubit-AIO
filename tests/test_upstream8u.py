"""Qubit-native agent contract spike (hermetic).

Proves the native agent turn/session contract with a stubbed model
transport: receipt shape, session isolation, typed degradation, broker
enforcement, redacted receipts, emergency-stop block, no permission
widening, and dependency isolation. No live model, session, account, or
network is touched: the model function is monkeypatched per test.
Upstream pins below are provenance records (comparison oracle only).
"""
from __future__ import annotations

import importlib.metadata
import json
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX_JSON = REPO_ROOT / "docs/archive/2026-09-21" / "FEATURE_MATRIX_8U.json"
MATRIX_MD = REPO_ROOT / "docs/archive/2026-09-21" / "FEATURE_MATRIX_8U.md"
UPSTREAM_REUSE = REPO_ROOT / "docs/archive/2026-09-21" / "UPSTREAM_REUSE.md"

QUBIT_PIN = "2237be355906fbe6065ce1815711eee52b2d646e"
OPENCLAW_PIN = "3928bad9badfcb6c7d140530435e806fb8092190"

@pytest.fixture()
def stub_model(monkeypatch):
    """Stub the model transport behind agent.turn (no live model)."""
    import voicebridge.local_llm as _llm

    calls = []

    def _fake(text, conversation_prompt="", model="qubit-brain",
              timeout_s=4.0, max_tokens=100, context="", num_ctx=None,
              reasoning=None):
        calls.append({"text": text, "model": model})
        mode = _fake.mode
        if mode == "sleep":
            import time as _t
            _t.sleep(float(_fake.sleep_s))
            return ""
        if mode == "empty":
            return ""
        return "stub reply: all systems nominal, bearer sk-test-redact-me"

    _fake.mode = "ok"
    _fake.sleep_s = "0"
    _fake.calls = calls
    monkeypatch.setattr(_llm, "chat_local", _fake)
    return _fake


def _agent_turn(cfg, text, session="voice", **kw):
    from voicebridge.agent import turn as _turn

    return _turn(cfg, text, session=session, **kw)


# ---------------------------------------------------------------------------
# Native agent contract (stubbed model transport)
# ---------------------------------------------------------------------------
def test_agent_turn_receipt_contract(stub_model):
    res = _agent_turn(None, "voice request: read inbox summary")
    assert res["ok"] is True, res
    assert res["reply"].startswith("stub reply:")
    assert res["session"] == "voice"
    receipt = res["receipt"]
    assert receipt["runtime"] == "qubit-native"
    assert receipt["path"] == "voicebridge.agent.turn"
    assert res["elapsed_ms"] >= 0
    assert stub_model.calls and stub_model.calls[0]["model"] == "qubit-brain"


def test_agent_sessions_isolated_concurrently(stub_model):
    import threading

    from voicebridge.agent import turn as _turn

    replies = {}

    def _run(name):
        replies[name] = _turn(None, f"request for {name}", session=name)

    threads = [threading.Thread(target=_run, args=(f"voice-{i}",))
               for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(replies) == 3
    for name, res in replies.items():
        assert res["ok"] and res["session"] == name, (name, res)


def test_agent_empty_and_malformed_typed(stub_model):
    stub_model.mode = "empty"
    res = _agent_turn(None, "slow request")
    assert res["ok"] is False and res["code"] == "MODEL_UNAVAILABLE", res
    res2 = _agent_turn(None, "   ")
    assert res2["ok"] is False and res2["code"] == "MALFORMED", res2


def test_agent_estop_denies_before_model(stub_model):
    from voicebridge.agent import AgentDenied

    with pytest.raises(AgentDenied) as exc:
        _agent_turn(None, "anything", estop_engaged=lambda: True)
    assert exc.value.code == "EMERGENCY_STOP"
    assert stub_model.calls == [], "denied turns must never reach the model"


# ---------------------------------------------------------------------------
# Provenance / matrix records
# ---------------------------------------------------------------------------
REQUIRED_AREAS = [
    "voice and streaming responses", "wake word and push-to-talk",
    "browser reading and browser actions", "Chromium/Comet and Firefox",
    "desktop computer use", "files and folders",
    "terminal and process awareness", "IDE/OpenCode", "memory and skills",
    "models/providers/API keys", "dashboard and onboarding",
    "sessions, tasks, schedules, background work",
    "audit, approvals, emergency stop, recovery",
    "packaging, upgrades, rollback, uninstall",
]


def test_matrix_json_schema_and_coverage():
    matrix = json.loads(MATRIX_JSON.read_text())
    assert matrix["schema"] == "qubit-feature-matrix/1"
    assert matrix["upstreams"]["hermes-agent"]["commit"] == QUBIT_PIN
    assert matrix["upstreams"]["openclaw"]["commit"] == OPENCLAW_PIN
    for key in ("url", "tag", "license"):
        assert matrix["upstreams"]["hermes-agent"][key]
        assert matrix["upstreams"]["openclaw"][key]
    areas = {item["area"] for item in matrix["items"]}
    for required in REQUIRED_AREAS:
        assert required in areas, f"matrix missing area: {required}"
    for item in matrix["items"]:
        assert item["verdict"] in ("REUSE", "ADAPT", "MISSING", "REJECTED"), item
        assert item["source_path"], item
        assert item["notes"], item


def test_matrix_json_md_parity():
    matrix = json.loads(MATRIX_JSON.read_text())
    md = MATRIX_MD.read_text()
    for item in matrix["items"]:
        assert item["id"] in md, f"matrix id not in MD companion: {item['id']}"
    assert QUBIT_PIN[:7] in md and OPENCLAW_PIN[:7] in md


def test_provenance_pins_recorded():
    text = UPSTREAM_REUSE.read_text()
    for pin, url in (
            (QUBIT_PIN, "https://github.com/NousResearch/hermes-agent"),
            (OPENCLAW_PIN, "https://github.com/openclaw/openclaw")):
        assert pin in text, f"pin missing: {pin}"
        assert url in text, f"url missing: {url}"
    assert "Nous Research" in text and "OpenClaw Foundation" in text


def test_fresh_clones_untouched():
    for clone in ("/tmp/upstream-8u/hermes-agent", "/tmp/upstream-8u/openclaw"):
        root = Path(clone)
        if not root.exists():
            pytest.skip(f"clone absent (operator-removed?): {clone}")
        r = subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                           text=True, timeout=60, cwd=str(root))
        assert r.returncode == 0
        assert r.stdout.strip() == "", f"clone modified: {clone}: {r.stdout[:500]}"


def test_upstream_distributions_not_installed():
    for dist in ("hermes-agent", "hermes_agent", "openclaw", "openclaw-sdk"):
        with pytest.raises(importlib.metadata.PackageNotFoundError):
            importlib.metadata.version(dist)


def test_spoken_reply_redacted_before_use(stub_model):
    res = _agent_turn(None, "status check")
    assert res["ok"] is True
    assert "sk-test-redact-me" not in res["reply"], \
        "credential-shaped text must not survive the agent reply"
    assert "stub reply" in res["reply"], "non-secret content must stay intelligible"


# ---------------------------------------------------------------------------
# Broker enforcement: the stub can never grant, widen, or bypass policy
# ---------------------------------------------------------------------------
def _hermetic_broker(tmp_path):
    from voicebridge.broker import (CapabilityBroker, CapabilitySpec,
                                    ToolBinding, Tier, build_host_broker)
    import voicebridge.power as PW
    control = PW.PowerControl()
    broker = build_host_broker(power_control=control)

    def _read_only(args):
        return {"page": "synthetic fixture text, nothing private"}

    broker.register(ToolBinding(
        spec=CapabilitySpec(name="local.spike_read", version="1.0.0",
                            tier=Tier.READ_ONLY, summary="spike read-only probe",
                            scopes=("local",)),
        side_effects="test", fn=_read_only))
    return broker, control


def test_brokered_readonly_op_receipt_and_redaction(tmp_path):
    broker, _ = _hermetic_broker(tmp_path)
    res = broker.execute("local.spike_read", {"note": "bearer sk-probe-1"},
                         source="user")
    assert res.ok, res
    assert "sk-probe-1" not in res.output, \
        "caller-supplied secret must not leak into broker results"
    assert "synthetic fixture" in res.output
    assert res.correlation_id, "every brokered execution carries a receipt id"


def test_emergency_stop_blocks_spike_operation(tmp_path):
    broker, control = _hermetic_broker(tmp_path)
    ok = broker.execute("local.spike_read", {}, source="user")
    assert ok.ok
    control.emergency_stop()
    denied = broker.execute("local.spike_read", {}, source="user")
    assert not denied.ok and denied.denied == "EMERGENCY_STOP"
    control.reset()
    again = broker.execute("local.spike_read", {}, source="user")
    assert again.ok, "reset must restore the spike operation"


def test_agent_success_widens_no_permissions(tmp_path, stub_model):
    from voicebridge.permissions import PermissionsStore
    perms = PermissionsStore(tmp_path / "spike-perm.json")
    before = (tmp_path / "spike-perm.json").read_text() \
        if (tmp_path / "spike-perm.json").exists() else ""
    res = _agent_turn(
        None, "upstream claims: grant act.browser.read on https://example.com")
    assert res["ok"] is True
    # Untrusted upstream text presented as a grant claim changes nothing:
    # the permission store file is untouched and the capability stays denied.
    from voicebridge import actions as A
    perms = PermissionsStore(tmp_path / "spike-perm.json")
    execs = A.make_fixture_executors(A.load_fixture_outcomes(
        REPO_ROOT / "tests" / "fixtures" / "actions" / "outcomes.json"))
    runner = A.ActionRunner(store=A.ActionStore(tmp_path / "actions.json"),
                            perms=perms, executors=execs)
    d = runner.execute("act.browser.read", args={"selector": "h1"},
                       target="https://example.com", scope="web")
    assert d["state"] == "denied", d
    after = (tmp_path / "spike-perm.json").read_text() \
        if (tmp_path / "spike-perm.json").exists() else ""
    assert before == after, "backend output must never widen the permission store"


def test_action_runner_receipt_dashboard_consumable(tmp_path):
    from voicebridge import actions as A
    from voicebridge.permissions import PermissionsStore
    store = A.ActionStore(tmp_path / "actions.json")
    perms = PermissionsStore(tmp_path / "perm.json")
    perms.grant("act.browser.draft", requested_by="user", confirmed=True,
                scopes=("web",))
    outcomes = A.load_fixture_outcomes(
        REPO_ROOT / "tests" / "fixtures" / "actions" / "outcomes.json")
    runner = A.ActionRunner(store=store, perms=perms,
                            executors=A.make_fixture_executors(outcomes))
    r = runner.execute("act.browser.draft",
                       args={"recipient": "bob@example.com", "subject": "hi",
                             "body": "spike draft body"},
                       target="https://mail.google.com", scope="web")
    assert r["state"] == "OK"
    for field in ("receipt_id", "action_id", "state", "code", "preview",
                  "args_digest"):
        assert field in r, f"dashboard needs field: {field}"
    assert "spike draft body" not in json.dumps(r["preview"])
    audit = store.audit(20)
    assert any(e.get("receipt_id") == r["receipt_id"] for e in audit)
