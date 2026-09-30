"""9U — OpenClaw system/operator family handlers are behavior-proven.

Two independent proofs, mirroring ``tests/test_qubit_dispositions_b07m.py``:

1. static: every method registered by
   ``voicebridge.gateway.qubit_handlers.system`` resolves to a real handler
   (a backend attribute call/import) OR, for a typed refusal, actually emits
   its declared code — never a stub and never a fabricated success;
2. runtime: the handlers are invoked in-process against a temp state dir and
   their real behavior is asserted.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import qubit_upgrade_manifest as OUM  # noqa: E402

HANDLER_FILE = REPO_ROOT / "voicebridge" / "gateway" / "qubit_handlers" / "system.py"

#: Typed refusals registered by the module, keyed by the code they must emit.
REFUSALS = {
    "exec.approval.waitDecision": "EXEC_APPROVAL_NO_BLOCKING_WAIT",
    "plugin.approval.waitDecision": "PLUGIN_APPROVAL_NO_BLOCKING_WAIT",
    "question.waitAnswer": "QUESTION_NO_BLOCKING_WAIT",
    "secrets.resolve": "SECRET_READ_REFUSED",
    "environments.create": "ENVIRONMENT_CLOUD_OPERATOR_REQUIRED",
    "environments.destroy": "ENVIRONMENT_CLOUD_OPERATOR_REQUIRED",
    "gateway.restart.request": "GATEWAY_RESTART_OPERATOR_REQUIRED",
    "update.hold": "UPDATE_HOLD_OPERATOR_ONLY",
}


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    home = tmp_path / "home" / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(home))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _registered_methods() -> list:
    import re

    return sorted(set(re.findall(r'registry\.register\("([^"]+)"',
                                 HANDLER_FILE.read_text(encoding="utf-8"))))


# ---------------------------------------------------------------- static proof
def test_registered_methods_resolve_to_real_handler_or_typed_code():
    sources = OUM.registry_sources(REPO_ROOT / "voicebridge" / "gateway"
                                   / "rpc.py")
    methods, funcs, consts, _ = OUM._parse_rpc_handlers_multi(sources)
    from voicebridge.gateway.rpc import default_registry

    registered = set(default_registry().names())
    named = _registered_methods()
    assert len(named) >= 80, named
    for method in named:
        assert method in registered, f"{method} is not registered"
        node = methods.get(method)
        assert node is not None, f"{method} has no AST handler"
        if method in REFUSALS:
            assert OUM._emits_code(node, funcs, REFUSALS[method], consts), (
                f"{method} declares {REFUSALS[method]!r} but never emits it")
            # A refusal must not look like an implementation to the manifest
            # generator: any backend call would classify it IMPLEMENTED.
            assert not OUM._is_non_trivial(node, funcs), (
                f"{method} is a typed refusal but performs backend work")
        else:
            assert OUM._is_non_trivial(node, funcs), (
                f"{method} is IMPLEMENTED but its handler does no backend work")


def test_no_registered_method_overlaps_the_canonical_rpc_registry():
    import re

    rpc_src = (REPO_ROOT / "voicebridge" / "gateway" / "rpc.py").read_text(
        encoding="utf-8")
    rpc_registered = set(re.findall(r'registry\.register\("([^"]+)"', rpc_src))
    overlap = rpc_registered & set(_registered_methods())
    assert not overlap, f"system.py overrides rpc.py methods: {sorted(overlap)}"


# --------------------------------------------------------------- runtime proof
def test_approval_journal_roundtrip(tmp_path, monkeypatch):
    from voicebridge.gateway.rpc import default_registry
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg = default_registry()
    created = reg.handle(cfg, "exec.approval.request",
                         {"capability": "exec.shell", "summary": "ls -la"})
    assert created["state"] == "OK", created
    approval_id = created["approval"]["id"]

    listed = reg.handle(cfg, "exec.approval.list", {})
    assert approval_id in [a["id"] for a in listed["approvals"]]

    fetched = reg.handle(cfg, "approval.get", {"id": approval_id})
    assert fetched["approval"]["id"] == approval_id

    resolved = reg.handle(cfg, "exec.approval.resolve",
                          {"id": approval_id, "decision": "approve"})
    assert resolved["approval"]["decision"] == "approve"
    assert resolved["approval"]["state"] == "executing"
    # re-approving an executing record is idempotent.
    again = reg.handle(cfg, "exec.approval.resolve",
                       {"id": approval_id, "decision": "approve"})
    assert again["approval"]["decision"] == "approve"

    with pytest.raises(GatewayError):
        reg.handle(cfg, "exec.approval.resolve",
                   {"id": "does-not-exist", "decision": "approve"})


def test_question_contract_roundtrip(tmp_path, monkeypatch):
    from voicebridge.gateway.rpc import default_registry

    cfg = _cfg(tmp_path, monkeypatch)
    reg = default_registry()
    created = reg.handle(cfg, "question.request", {
        "questions": [{"id": "q1", "text": "Proceed?", "kind": "choice",
                       "choices": ["yes", "no"]}],
        "confirmed": True,
    })
    assert created["state"] == "OK", created
    prompt_id = created["prompt_id"]

    listed = reg.handle(cfg, "question.list", {})
    assert prompt_id in [p["prompt_id"] for p in listed["pending"]]

    fetched = reg.handle(cfg, "question.get", {"prompt_id": prompt_id})
    assert fetched["state"] == "OK"
    assert fetched["prompt"]["prompt_id"] == prompt_id

    answered = reg.handle(cfg, "question.resolve",
                          {"prompt_id": prompt_id, "answers": {"q1": "yes"}})
    assert answered["answers"] == {"q1": "yes"}

    # answered prompts are settled out of the pending list.
    assert prompt_id not in [
        p["prompt_id"] for p in reg.handle(cfg, "question.list", {})["pending"]]


def test_typed_refusals_emit_declared_codes(tmp_path, monkeypatch):
    from voicebridge.gateway.rpc import default_registry

    cfg = _cfg(tmp_path, monkeypatch)
    reg = default_registry()
    for method, code in REFUSALS.items():
        out = reg.handle(cfg, method, {})
        assert out.get("code") == code, (method, out)
        assert out.get("state") == "UNAVAILABLE", (method, out)
        assert out.get("operator_prerequisite") or out.get("qubit_counterpart")


def test_vault_reference_and_config_writes(tmp_path, monkeypatch):
    from voicebridge.gateway.rpc import default_registry

    cfg = _cfg(tmp_path, monkeypatch)
    reg = default_registry()

    reloaded = reg.handle(cfg, "secrets.reload", {})
    assert reloaded["state"] == "OK"
    assert "env_references" in reloaded

    written = reg.handle(cfg, "secrets.store.set",
                         {"name": "OPENAI_API_KEY", "value": "vault:openai",
                          "confirm": True})
    assert written.get("ok") is True, written
    assert "OPENAI_API_KEY" in reg.handle(cfg, "secrets.reload",
                                          {})["env_references"]

    removed = reg.handle(cfg, "secrets.store.delete",
                         {"name": "OPENAI_API_KEY", "confirm": True})
    assert removed.get("removed") is True, removed

    set_out = reg.handle(cfg, "config.set",
                         {"config": {"hotkey.tap_ms": 420}, "confirm": True})
    assert set_out.get("ok") is True, set_out
    assert set_out.get("count") == 1, set_out


def test_read_delegates_return_real_inventory(tmp_path, monkeypatch):
    from voicebridge.gateway.rpc import default_registry

    cfg = _cfg(tmp_path, monkeypatch)
    reg = default_registry()

    audit = reg.handle(cfg, "audit.list", {})
    assert audit["state"] == "OK" and isinstance(audit["audit"], list)

    activity = reg.handle(cfg, "audit.activity.list", {})
    assert activity["state"] == "OK" and isinstance(activity["activity"], list)

    update = reg.handle(cfg, "update.status", {})
    assert update["state"] == "OK" and "history" in update

    plan = reg.handle(cfg, "migrations.memory.plan", {})
    assert plan.get("dry_run") is True, plan

    doctor = reg.handle(cfg, "doctor.memory.status", {})
    assert doctor["state"] == "OK" and doctor["providers"]["providers"]

    nodes = reg.handle(cfg, "node.list", {})
    assert nodes["state"] == "OK" and nodes["nodes"] == []

    stability = reg.handle(cfg, "diagnostics.stability", {})
    assert stability["state"] == "OK" and "runtime" in stability

    plugins = reg.handle(cfg, "plugins.controlUi.status", {})
    assert plugins["state"] in ("READY", "ESTOP"), plugins

    envs = reg.handle(cfg, "environments.list", {})
    assert envs["state"] == "OK" and "images" in envs

    suspend = reg.handle(cfg, "gateway.suspend.status", {})
    assert suspend["state"] == "OK" and "power" in suspend

    heartbeat = reg.handle(cfg, "last-heartbeat", {})
    assert heartbeat["state"] == "OK" and heartbeat["heartbeats"] == []

    identity = reg.handle(cfg, "agent.identity.get", {})
    assert identity["state"] == "OK" and "profile" in identity

    observe = reg.handle(cfg, "desktop.observe", {})
    assert observe["state"] == "OK"

    runs = reg.handle(cfg, "update.runs.list", {})
    assert runs["state"] == "OK" and isinstance(runs["entries"], list)


def test_confirmation_gates_and_typed_states(tmp_path, monkeypatch):
    from voicebridge.gateway.rpc import default_registry

    cfg = _cfg(tmp_path, monkeypatch)
    reg = default_registry()

    # node enrollment is confirmation-gated by the canonical node host.
    gate = reg.handle(cfg, "node.pair.approve",
                      {"name": "edge", "endpoint": "http://127.0.0.1:9"})
    assert gate["state"] != "OK"
    assert gate.get("code"), gate

    # heartbeat creation is confirmation-gated too.
    hb = reg.handle(cfg, "set-heartbeats",
                    {"name": "tick", "interval_min": 5, "confirmed": True})
    assert hb["state"] == "OK", hb

    # device token mutations are typed not-found for an unknown id.
    missing = reg.handle(cfg, "device.token.revoke",
                         {"token_id": "dtok_missing", "confirmed": True})
    assert missing["state"] == "NOT_FOUND", missing

    # pairing setup requires confirmation; with it the code is real and listed.
    setup = reg.handle(cfg, "device.pair.setupCode",
                       {"label": "phone", "confirmed": True})
    assert setup["state"] == "OK" and setup["code"], setup
    listed = reg.handle(cfg, "device.pair.setupStatus", {})
    assert any(row.get("id") == setup["code_id"] for row in listed["codes"])


def test_openclaw_chat_history_reads_the_canonical_session_store(
        tmp_path, monkeypatch):
    from voicebridge.gateway.rpc import default_registry
    from voicebridge.qubit_session_store import QubitSessionStore

    cfg = _cfg(tmp_path, monkeypatch)
    reg = default_registry()
    store = QubitSessionStore(cfg)
    created = store.create("handoff")
    sid = created["session"]["id"]
    store.append(sid, "user", "hello")

    out = reg.handle(cfg, "openclaw.chat.history", {"session": sid})
    assert out["state"] == "OK", out
    assert [m["content"] for m in out["messages"]] == ["hello"]

    missing = reg.handle(cfg, "openclaw.chat.history", {"session": "nope"})
    assert missing["state"] == "MISSING"
    assert missing["code"] == "SESSION_NOT_FOUND"


def test_openclaw_approval_list_and_presence_are_real(tmp_path, monkeypatch):
    from voicebridge.gateway.rpc import default_registry

    cfg = _cfg(tmp_path, monkeypatch)
    reg = default_registry()
    created = reg.handle(cfg, "exec.approval.request",
                         {"capability": "system-agent.tool", "summary": "x"})
    listed = reg.handle(cfg, "openclaw.approval.list", {})
    assert listed["state"] == "OK"
    assert created["approval"]["id"] in [a["id"] for a in listed["approvals"]]

    presence = reg.handle(cfg, "system-presence", {})
    assert presence["state"] == "OK"
    assert set(("discovery", "nodes", "sessions")) <= set(presence)


def test_system_event_queue_is_confirmation_gated(tmp_path, monkeypatch):
    from voicebridge.gateway.rpc import default_registry
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg = default_registry()
    with pytest.raises(GatewayError):
        reg.handle(cfg, "system-event", {"kind": "internal", "text": "x"})
    queued = reg.handle(cfg, "system-event",
                        {"kind": "internal", "text": "x", "confirmed": True})
    assert queued["state"] == "OK" and queued["event"]["kind"] == "internal"


def test_openclaw_setup_and_changes_read_from_lifecycle(tmp_path, monkeypatch):
    from voicebridge.gateway.rpc import default_registry

    cfg = _cfg(tmp_path, monkeypatch)
    reg = default_registry()
    detect = reg.handle(cfg, "openclaw.setup.detect", {"offline": True})
    assert detect["state"] in ("READY", "DEGRADED", "UNAVAILABLE"), detect

    verify = reg.handle(cfg, "openclaw.setup.verify", {"offline": True})
    assert verify["state"] in ("READY", "DEGRADED", "UNAVAILABLE"), verify

    plan = reg.handle(cfg, "openclaw.setup.prepare.start", {})
    assert plan["state"] in ("PENDING", "READY", "OK"), plan

    changes = reg.handle(cfg, "openclaw.changes.list", {})
    assert changes["state"] in ("READY", "UNAVAILABLE"), changes
    assert isinstance(changes.get("entries"), list)

    surface = reg.handle(cfg, "plugin.surface.refresh", {})
    assert surface["state"] == "OK" and "fleet" in surface


def test_fs_list_dir_is_typed_when_no_roots(tmp_path, monkeypatch):
    from voicebridge.gateway.rpc import default_registry
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg = default_registry()
    with pytest.raises(GatewayError) as exc:
        reg.handle(cfg, "fs.listDir", {"path": str(tmp_path)})
    assert not exc.value.code.startswith("GATEWAY_METHOD_NOT_FOUND")
