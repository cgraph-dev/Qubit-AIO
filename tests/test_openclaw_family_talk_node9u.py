"""9U — OpenClaw ``talk.*`` and ``node.*`` families.

Static behavior proof (the manifest generator's own checks) plus runtime proof
against the canonical authorities: the one chat-session store
(``voicebridge.qubit_session_store``), the canonical ``CapabilityBroker``, the
canonical speech-profile resolver (``voicebridge.providers.speech``) and the
canonical paired-node host (``voicebridge.node_host``).

Qubit removed the realtime/duplex lane, so every ``talk.session.*`` and the
client-owned ``talk.client.create`` / ``talk.client.close`` are typed
OPERATOR_ONLY refusals; the paired-node relay is likewise absent, so the node
queue/invoke/event/catalog methods are OPERATOR_ONLY. The remaining methods have
real local counterparts.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import openclaw_upgrade_manifest as OUM  # noqa: E402

MODULE = "voicebridge/gateway/openclaw_handlers/talk_node.py"
MODULE_PATH = "voicebridge.gateway.openclaw_handlers.talk_node"

#: No pinned method in this family resolves to a mechanically-IMPLEMENTED
#: handler; each is either a typed local counterpart or an operator-only gate.
IMPL: tuple = ()
SAFE = {
    "talk.mode": "TALK_MODE_LOCAL_SPEECH_PROFILE",
    "talk.client.steer": "TALK_CLIENT_STEER_LOCAL_TRANSCRIPT",
    "talk.client.transcript": "TALK_CLIENT_TRANSCRIPT_LOCAL_STORE",
    "talk.client.toolCall": "TALK_CLIENT_TOOLCALL_LOCAL_BROKER",
    "node.rename": "NODE_RENAME_LOCAL_REENROLL",
}
OPS = {
    "talk.client.create": "TALK_REALTIME_OPERATOR_REQUIRED",
    "talk.client.close": "TALK_REALTIME_OPERATOR_REQUIRED",
    "talk.session.create": "TALK_REALTIME_OPERATOR_REQUIRED",
    "talk.session.appendAudio": "TALK_REALTIME_OPERATOR_REQUIRED",
    "talk.session.cancelOutput": "TALK_REALTIME_OPERATOR_REQUIRED",
    "talk.session.close": "TALK_REALTIME_OPERATOR_REQUIRED",
    "talk.session.acknowledgeMark": "TALK_REALTIME_OPERATOR_REQUIRED",
    "talk.session.submitToolResult": "TALK_REALTIME_OPERATOR_REQUIRED",
    "talk.session.steer": "TALK_REALTIME_OPERATOR_REQUIRED",
    "node.event": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
    "node.invoke.progress": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
    "node.invoke.result": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
    "node.pending.ack": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
    "node.pending.drain": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
    "node.pending.enqueue": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
    "node.pending.pull": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
    "node.pluginTools.update": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
    "node.runnerInventory.update": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
    "node.skills.update": "NODE_DEVICE_RELAY_OPERATOR_REQUIRED",
}
ALL = set(IMPL) | set(SAFE) | set(OPS)
assert len(ALL) == 24


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _registry():
    """Own registry — does not depend on the composed FAMILY_MODULES."""
    from voicebridge.gateway.rpc import MethodRegistry

    mod = importlib.import_module(MODULE_PATH)
    reg = MethodRegistry()
    mod.register(reg)
    return reg


# --------------------------------------------------------------- static proof
def test_family_static_proof():
    sources = OUM.registry_sources(REPO_ROOT / "voicebridge" / "gateway"
                                   / "rpc.py")
    methods, funcs, consts, origins = OUM._parse_rpc_handlers_multi(sources)
    registry = set(_registry().names())
    ours = {m for m, origin in origins.items() if origin.endswith(MODULE)}
    assert ours == ALL, ours
    assert ours <= registry, f"unregistered: {ours - registry}"
    for method in sorted(ours):
        node = methods[method]
        code = SAFE.get(method) or OPS.get(method)
        assert code, f"{method} has no declared typed code"
        assert OUM._emits_code(node, funcs, code, consts), (
            f"{method} never emits {code!r}")
        if method in SAFE:
            # A real local counterpart must actually touch a backend.
            assert OUM._is_non_trivial(node, funcs), f"{method} does no work"


def test_registry_only_owns_this_family():
    assert set(_registry().names()) == ALL


# ------------------------------------------------------------------ talk.mode
def test_talk_mode_reports_the_real_speech_profile(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "talk.mode", {"mode": "realtime"})
    assert out["state"] == "OK", out
    assert out["code"] == "TALK_MODE_LOCAL_SPEECH_PROFILE", out
    assert out["effectiveMode"] == "local-stt-tts", out
    assert isinstance(out["realtime"], dict), out
    assert isinstance(out["profiles"], dict), out


# --------------------------------------------------------- talk.client.steer
def test_talk_client_steer_records_on_the_canonical_session(tmp_path,
                                                            monkeypatch):
    from voicebridge.qubit_session_store import QubitSessionStore

    cfg = _cfg(tmp_path, monkeypatch)
    QubitSessionStore(cfg).create("Steer target", session_id="sess-steer")
    out = _registry().handle(cfg, "talk.client.steer",
                             {"sessionKey": "sess-steer", "text": "go left",
                              "mode": "steer"})
    assert out["state"] == "OK", out
    assert out["code"] == "TALK_CLIENT_STEER_LOCAL_TRANSCRIPT", out
    msgs = QubitSessionStore(cfg).messages("sess-steer")["messages"]
    assert any("go left" in str(m.get("content", "")) for m in msgs), msgs


def test_talk_client_steer_requires_text(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(GatewayError) as exc:
        _registry().handle(cfg, "talk.client.steer",
                           {"sessionKey": "sess-steer"})
    assert exc.value.code == "GATEWAY_BAD_PARAMS", exc.value


# ----------------------------------------------------- talk.client.transcript
def test_talk_client_transcript_appends_to_the_canonical_store(tmp_path,
                                                               monkeypatch):
    from voicebridge.qubit_session_store import QubitSessionStore

    cfg = _cfg(tmp_path, monkeypatch)
    QubitSessionStore(cfg).create("Transcript target", session_id="sess-tr")
    out = _registry().handle(
        cfg, "talk.client.transcript",
        {"sessionKey": "sess-tr", "role": "assistant", "text": "hello voice",
         "entryId": "entry-1"})
    assert out["state"] == "OK", out
    assert out["code"] == "TALK_CLIENT_TRANSCRIPT_LOCAL_STORE", out
    assert out["role"] == "assistant", out
    msgs = QubitSessionStore(cfg).messages("sess-tr")["messages"]
    assert any(m.get("role") == "assistant" and "hello voice" in
               str(m.get("content", "")) for m in msgs), msgs


# --------------------------------------------------------- talk.client.toolCall
def test_talk_client_tool_call_uses_the_broker_without_fabricating(tmp_path,
                                                                   monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "talk.client.toolCall",
                             {"toolName": "no.such.capability", "args": {}})
    assert out["code"] == "TALK_CLIENT_TOOLCALL_LOCAL_BROKER", out
    assert out["ok"] is False, out
    assert out["state"] == "UNAVAILABLE", out


def test_talk_client_tool_call_requires_a_tool_name(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(GatewayError) as exc:
        _registry().handle(cfg, "talk.client.toolCall", {})
    assert exc.value.code == "GATEWAY_BAD_PARAMS", exc.value


# --------------------------------------------------------------- node.rename
def test_node_rename_requires_confirmation(tmp_path, monkeypatch):
    from voicebridge import node_host as NH

    cfg = _cfg(tmp_path, monkeypatch)
    NH.enroll(cfg, name="orig", endpoint="http://node.example:1",
              confirmed=True, source="cli")
    node_id = NH.list_nodes(cfg)["nodes"][0]["id"]
    out = _registry().handle(cfg, "node.rename",
                             {"nodeId": node_id, "name": "renamed"})
    assert out["state"] == "CONFIRM_REQUIRED", out
    assert out["code"] == "NODE_RENAME_LOCAL_REENROLL", out
    assert NH.list_nodes(cfg)["nodes"][0]["name"] == "orig"


def test_node_rename_re_registers_the_node(tmp_path, monkeypatch):
    from voicebridge import node_host as NH

    cfg = _cfg(tmp_path, monkeypatch)
    NH.enroll(cfg, name="orig", endpoint="http://node.example:1",
              capabilities=["node.status"], confirmed=True, source="cli")
    node_id = NH.list_nodes(cfg)["nodes"][0]["id"]
    out = _registry().handle(cfg, "node.rename",
                             {"nodeId": node_id, "name": "renamed",
                              "confirmed": True})
    assert out["state"] == "OK", out
    assert out["code"] == "NODE_RENAME_LOCAL_REENROLL", out
    nodes = NH.list_nodes(cfg)["nodes"]
    assert len(nodes) == 1, nodes
    assert nodes[0]["name"] == "renamed", nodes
    assert nodes[0]["endpoint"] == "http://node.example:1", nodes
    assert nodes[0]["capabilities"] == ["node.status"], nodes
    assert nodes[0]["id"] != node_id, nodes


def test_node_rename_without_name_is_bad_params(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(GatewayError) as exc:
        _registry().handle(cfg, "node.rename", {"nodeId": "node_x"})
    assert exc.value.code == "GATEWAY_BAD_PARAMS", exc.value


# -------------------------------------------------------- typed refusals
def test_every_operator_only_method_is_a_typed_refusal(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    for method, code in sorted({**SAFE, **OPS}.items()):
        if method in SAFE:
            continue
        out = reg.handle(cfg, method, {})
        assert out["state"] == "UNAVAILABLE", (method, out)
        assert out["code"] == code, (method, out)
        assert out["operator_prerequisite"], (method, out)
        assert out["qubit_counterpart"], (method, out)


def test_operator_only_refusals_fabricate_no_success(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    for method in OPS:
        out = reg.handle(cfg, method, {})
        assert "OK" not in str(out.get("state", "")), (method, out)
        assert out.get("created") is not True, (method, out)
        assert out.get("ok") is not True, (method, out)
