"""9U — OpenClaw singleton / small pinned-method families.

Static behavior proof (the manifest's own checks) plus runtime proof against the
canonical Qubit authorities: the plugin runtime, the question-prompt store, the
memory workspace, the model-profile registry, the session activity projection,
the control panels, the automation queue and the canonical session store.
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

MODULE_PATH = "voicebridge/gateway/openclaw_handlers/misc_surface.py"
MODULE_NAME = "voicebridge.gateway.openclaw_handlers.misc_surface"

SAFE = {
    "plugins.sessionAction": "PLUGIN_SESSION_ACTION_LOCAL_INVOKE",
    "plugins.uiDescriptors": "PLUGIN_UI_DESCRIPTORS_LOCAL_SURFACE",
    "wizard.next": "WIZARD_NEXT_LOCAL_QUESTION_STORE",
    "wizard.cancel": "WIZARD_CANCEL_LOCAL_QUESTION_STORE",
    "assistant.media.get": "MEDIA_NOT_SERVED",
    "chat.toolTitles": "TOOL_TITLES_AGENT_PROVIDED",
    "doctor.memory.resetGroundedShortTerm":
        "MEMORY_GROUNDED_SHORTTERM_LOCAL_RESET",
    "models.authOrderSet": "AUTH_ORDER_LOCAL_PROFILE_PRIORITY",
    "progressCard.put": "PROGRESS_CARD_PUT_LOCAL_PROJECTION",
    "ui.command": "UI_COMMAND_LOCAL_PANELS",
    "wake": "WAKE_LOCAL_AUTOMATION",
}
OPS = {
    "voicewake.set": "WAKE_TRIGGERS_OPERATOR_REQUIRED",
    "voicewake.routing.get": "WAKE_ROUTING_OPERATOR_REQUIRED",
    "controlUi.githubPreview": "FORGE_API_OPERATOR_REQUIRED",
    "nativeHook.invoke": "NATIVE_HOOK_RELAY_OPERATOR_REQUIRED",
}
ALL = set(SAFE) | set(OPS)


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _registry():
    from voicebridge.gateway.rpc import MethodRegistry

    reg = MethodRegistry()
    importlib.import_module(MODULE_NAME).register(reg)
    return reg


def test_static_proof():
    sources = OUM.registry_sources(REPO_ROOT / "voicebridge" / "gateway"
                                   / "rpc.py")
    methods, funcs, consts, origins = OUM._parse_rpc_handlers_multi(sources)
    ours = {m for m, origin in origins.items() if origin.endswith(MODULE_PATH)}
    assert ours == ALL, ours
    for method in sorted(ALL):
        node = methods[method]
        code = SAFE.get(method) or OPS.get(method)
        assert OUM._emits_code(node, funcs, code, consts), (
            f"{method} never emits {code!r}")


def test_every_method_is_registered_in_our_registry():
    reg = _registry()
    assert set(reg.names()) == ALL, reg.names()


# ------------------------------------------------------------- plugins
def test_plugins_session_action_invokes_the_in_process_host(tmp_path,
                                                            monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "plugins.sessionAction",
                             {"pluginId": "builtin.echo", "actionId": "invoke",
                              "payload": {"text": "hi"}, "confirm": True})
    assert out["code"] == "PLUGIN_SESSION_ACTION_LOCAL_INVOKE", out
    assert out["state"] == "OK", out
    assert out["result"]["echo"] == "hi", out


def test_plugins_session_action_requires_ids(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(GatewayError) as exc:
        _registry().handle(cfg, "plugins.sessionAction", {"pluginId": "x"})
    assert exc.value.code == "GATEWAY_BAD_PARAMS", exc.value


def test_plugins_ui_descriptors_reads_surface_registry(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "plugins.uiDescriptors", {})
    assert out["code"] == "PLUGIN_UI_DESCRIPTORS_LOCAL_SURFACE", out
    assert out["ok"] is True and isinstance(out["descriptors"], list), out


# ------------------------------------------------------------- voicewake
def test_voicewake_typed_refusals(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    assert reg.handle(cfg, "voicewake.set", {})["code"] == \
        "WAKE_TRIGGERS_OPERATOR_REQUIRED"
    assert reg.handle(cfg, "voicewake.routing.get", {})["code"] == \
        "WAKE_ROUTING_OPERATOR_REQUIRED"


# ---------------------------------------------------------------- wizard
def test_wizard_next_answers_the_real_prompt_store(tmp_path, monkeypatch):
    from voicebridge import question_prompt as QP

    cfg = _cfg(tmp_path, monkeypatch)
    prompt = QP.ask(cfg, [{"id": "q1", "text": "Name?", "kind": "text"}],
                    confirmed=True)
    out = _registry().handle(
        cfg, "wizard.next",
        {"promptId": prompt["prompt_id"],
         "answer": {"value": {"q1": "Ada"}}})
    assert out["code"] == "WIZARD_NEXT_LOCAL_QUESTION_STORE", out
    assert out["answer"]["answers"] == {"q1": "Ada"}, out


def test_wizard_cancel_reports_no_session_registry(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "wizard.cancel", {"sessionId": "s1"})
    assert out["code"] == "WIZARD_CANCEL_LOCAL_QUESTION_STORE", out
    assert out["cancelled"] is False, out


# --------------------------------------------------------- media/titles
def test_assistant_media_get_typed_refusal(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "assistant.media.get",
                             {"sessionKey": "s1", "mediaId": "m1"})
    assert out["code"] == "MEDIA_NOT_SERVED", out


def test_chat_tool_titles_disabled_but_reads_session(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "chat.toolTitles", {"sessionKey": "s1"})
    assert out["code"] == "TOOL_TITLES_AGENT_PROVIDED", out
    assert out["titles"] == {} and out["disabled"] is True, out


# ------------------------------------------------------------- control ui
def test_controlui_github_preview_operator_gate(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "controlUi.githubPreview", {"url": "u"})
    assert out["code"] == "FORGE_API_OPERATOR_REQUIRED", out
    assert out["operator_prerequisite"], out


# ------------------------------------------------------------ doctor memory
def test_doctor_memory_reset_grounded_short_term_reads_workspace(
        tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "doctor.memory.resetGroundedShortTerm", {})
    assert out["code"] == "MEMORY_GROUNDED_SHORTTERM_LOCAL_RESET", out
    assert isinstance(out["notes"], list), out


def test_doctor_memory_reset_grounded_short_term_forgets_named_note(
        tmp_path, monkeypatch):
    from voicebridge.memory import workspace as MW

    cfg = _cfg(tmp_path, monkeypatch)
    MW.write_note(cfg, "scratch", "hello", confirmed=True)
    out = _registry().handle(
        cfg, "doctor.memory.resetGroundedShortTerm",
        {"name": "scratch", "confirm": True})
    assert out["code"] == "MEMORY_GROUNDED_SHORTTERM_LOCAL_RESET", out
    assert out.get("forgotten") == "scratch", out


# ---------------------------------------------------------------- models
def test_models_auth_order_set_reads_profile_registry(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "models.authOrderSet", {"provider": "ollama"})
    assert out["code"] == "AUTH_ORDER_LOCAL_PROFILE_PRIORITY", out
    assert isinstance(out["profileIds"], list), out


# ------------------------------------------------------------- native hook
def test_native_hook_invoke_operator_gate(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "nativeHook.invoke",
                             {"provider": "p", "relayId": "r"})
    assert out["code"] == "NATIVE_HOOK_RELAY_OPERATOR_REQUIRED", out


# --------------------------------------------------------- progress card
def test_progress_card_put_builds_projection(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "progressCard.put", {"sessionKey": "s1"})
    assert out["code"] == "PROGRESS_CARD_PUT_LOCAL_PROJECTION", out
    assert out["projection"]["session_id"] == "s1", out


# ---------------------------------------------------------------- ui/wake
def test_ui_command_reads_panel_state(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "ui.command",
                             {"command": {"kind": "open", "panel": "terminal"}})
    assert out["code"] == "UI_COMMAND_LOCAL_PANELS", out
    assert isinstance(out["panels"], dict), out


def test_wake_queues_internal_automation_event(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "wake",
                             {"mode": "now", "text": "ping", "confirm": True})
    assert out["code"] == "WAKE_LOCAL_AUTOMATION", out
    assert out["event"]["text"] == "ping", out


def test_wake_requires_text(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(GatewayError) as exc:
        _registry().handle(cfg, "wake", {"mode": "now"})
    assert exc.value.code == "GATEWAY_BAD_PARAMS", exc.value
