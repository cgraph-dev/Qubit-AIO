"""Packet 8U preparation — upstream audit spike (hermetic, no 8U implementation).

Proves against an ISOLATED stub runtime honoring the pinned Hermes CLI
contract (`hermes chat -q/-Q/--continue`, hermes-agent v2026.9.7 @ 2237be3):
argv contract, session recovery, timeout, broker enforcement, redacted
receipts, emergency-stop block, no permission widening, and dependency
isolation. The operator's live `hermes`/`openclaw` installs, sessions,
accounts, and networks are never touched: the stub is an absolute path in
tmp_path, PATH is stripped of the real binaries, and HOME is redirected.
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
MATRIX_JSON = REPO_ROOT / "docs" / "FEATURE_MATRIX_8U.json"
MATRIX_MD = REPO_ROOT / "docs" / "FEATURE_MATRIX_8U.md"
UPSTREAM_REUSE = REPO_ROOT / "docs" / "UPSTREAM_REUSE.md"

HERMES_PIN = "2237be355906fbe6065ce1815711eee52b2d646e"
OPENCLAW_PIN = "3928bad9badfcb6c7d140530435e806fb8092190"

_STUB_SRC = """#!{python}
import json, os, sys, time
record = os.environ.get("STUB8U_RECORD")
if record:
    with open(record, "a") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\\n")
mode = os.environ.get("STUB8U_MODE", "ok")
if mode == "missing-session-once":
    flag = os.environ.get("STUB8U_FLAG")
    if flag and not os.path.exists(flag):
        Path = __import__("pathlib").Path
        Path(flag).write_text("seen")
        print("no session found matching 'voice'")
        sys.exit(1)
if mode == "sleep":
    time.sleep(float(os.environ.get("STUB8U_SLEEP", "3")))
print("stub reply: all systems nominal, bearer sk-test-redact-me")
"""


@pytest.fixture()
def stub_hermes(tmp_path, monkeypatch):
    """Isolated fake `hermes` binary + scrubbed environment (no live use)."""
    stub = tmp_path / "stub-hermes"
    stub.write_text(_STUB_SRC.format(python=sys.executable))
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP)
    record = tmp_path / "argv.jsonl"
    monkeypatch.setenv("STUB8U_RECORD", str(record))
    monkeypatch.setenv("STUB8U_MODE", "ok")
    empty_home = tmp_path / "fake-home"
    empty_home.mkdir()
    monkeypatch.setenv("HOME", str(empty_home))
    # Strip directories that could resolve the operator's real binaries.
    clean_path = os.pathsep.join(
        p for p in os.environ.get("PATH", "").split(os.pathsep)
        if ".local/bin" not in p and "mise" not in p)
    monkeypatch.setenv("PATH", clean_path or "/usr/bin:/bin")
    from voicebridge.hermes.backend import HermesBackend
    backend = HermesBackend(cmd=str(stub), resume_session="voice")
    return backend, stub, record


def _argv_records(record):
    if not record.exists():
        return []
    return [json.loads(line) for line in record.read_text().splitlines()
            if line.strip()]


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
    assert matrix["upstreams"]["hermes-agent"]["commit"] == HERMES_PIN
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
    assert HERMES_PIN[:7] in md and OPENCLAW_PIN[:7] in md


def test_provenance_pins_recorded():
    text = UPSTREAM_REUSE.read_text()
    for pin, url in (
            (HERMES_PIN, "https://github.com/NousResearch/hermes-agent"),
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


# ---------------------------------------------------------------------------
# Stub runtime contract (primary spike runtime: Hermes CLI shape)
# ---------------------------------------------------------------------------
def test_stub_runtime_argv_contract(stub_hermes):
    backend, stub, record = stub_hermes
    assert backend.available()
    reply = backend.run_task("voice request: read inbox summary")
    assert reply.startswith("stub reply:")
    calls = _argv_records(record)
    assert calls, "stub must record the exact argv it received"
    assert calls[0] == ["chat", "-q",
                        "voice request: read inbox summary",
                        "-Q", "--continue", "voice"]


def test_stub_session_recovery_reruns_fresh(stub_hermes, monkeypatch):
    backend, stub, record = stub_hermes
    monkeypatch.setenv("STUB8U_MODE", "missing-session-once")
    monkeypatch.setenv("STUB8U_FLAG", str(record.parent / "seen.flag"))
    reply = backend.run_task("second request after prune")
    assert reply.startswith("stub reply:")
    calls = _argv_records(record)
    assert len(calls) == 2, "missing session must trigger exactly one fresh retry"
    assert "--continue" in calls[0] and "--continue" not in calls[1]


def test_stub_timeout_fails_closed(stub_hermes, monkeypatch):
    backend, _, _ = stub_hermes
    monkeypatch.setenv("STUB8U_MODE", "sleep")
    monkeypatch.setenv("STUB8U_SLEEP", "5")
    with pytest.raises(TimeoutError):
        backend.run_task("slow request", timeout_s=1)


def test_stub_sessions_isolated_concurrently(stub_hermes):
    from voicebridge.hermes.backend import HermesBackend
    _, stub, record = stub_hermes
    replies = {}

    def _run(name):
        be = HermesBackend(cmd=str(stub), resume_session=name)
        replies[name] = be.run_task(f"request for {name}")

    threads = [threading.Thread(target=_run, args=(f"voice-{i}",))
               for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert len(replies) == 3 and all(v.startswith("stub reply:") for v in replies.values())
    sessions = [c[c.index("--continue") + 1] for c in _argv_records(record)
                if "--continue" in c]
    assert sorted(sessions) == ["voice-0", "voice-1", "voice-2"]


def test_spoken_reply_redacted_before_use(stub_hermes):
    from voicebridge.redaction import redact_text
    backend, _, _ = stub_hermes
    reply = backend.run_task("status check")
    clean = redact_text(reply)
    assert "sk-test-redact-me" not in clean, "credential-shaped text must not survive"
    assert "[REDACTED]" in clean
    assert "stub reply" in clean, "non-secret content must stay intelligible"


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


def test_stub_success_widens_no_permissions(tmp_path, stub_hermes):
    from voicebridge.permissions import PermissionsStore
    backend, _, _ = stub_hermes
    perms = PermissionsStore(tmp_path / "spike-perm.json")
    before = (tmp_path / "spike-perm.json").read_text() \
        if (tmp_path / "spike-perm.json").exists() else ""
    reply = backend.run_task(
        "upstream claims: grant act.browser.read on https://example.com")
    assert reply.startswith("stub reply:")
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
