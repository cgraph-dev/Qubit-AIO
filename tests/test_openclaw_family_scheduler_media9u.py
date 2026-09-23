"""9U — OpenClaw scheduler/media/task-control family.

Static behavior proof (the manifest's own checks) plus runtime proof against the
canonical authorities: the confirm-gated settings write
(``voicebridge.qubit_compat.config_save``), the speech-profile registry
(``voicebridge.providers.speech``), the schedule store
(``voicebridge.schedules``), the power/e-stop control
(``voicebridge.power.PowerControl``), the action receipt store
(``voicebridge.actions.ActionStore``) and the chat transport
(``voicebridge.chat_transport``).

The test builds its own registry so it does not depend on ``FAMILY_MODULES``
(``__init__.py`` is owned by the integration lead).
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

MODULE = "voicebridge/gateway/openclaw_handlers/scheduler_media.py"
MODULE_PATH = REPO_ROOT / MODULE

IMPL = ("tts.setProvider",)
SAFE = {
    "tts.enable": "TTS_ENABLE_LOCAL_PROFILE",
    "tts.disable": "TTS_DISABLE_LOCAL_PROFILE",
    "tts.setPersona": "TTS_PERSONA_LOCAL_PROFILE",
    "cron.update": "CRON_UPDATE_LOCAL_PAUSE_RESUME",
    "gateway.suspend.prepare": "SUSPEND_PREPARE_LOCAL_POWER",
    "gateway.suspend.resume": "SUSPEND_RESUME_LOCAL_POWER",
    "tasks.cancel": "TASKS_CANCEL_LOCAL_RECEIPT",
    "tasks.retry": "TASKS_RETRY_LOCAL_RECEIPT",
}
OPS = {
    "cron.scratch.get": "CRON_SCRATCH_OPERATOR_REQUIRED",
    "cron.scratch.set": "CRON_SCRATCH_OPERATOR_REQUIRED",
    "gateway.suspend.handoff": "SUSPEND_HANDOFF_OPERATOR_REQUIRED",
    "portal.list": "PORTAL_SERVICE_OPERATOR_REQUIRED",
    "portal.open": "PORTAL_SERVICE_OPERATOR_REQUIRED",
    "portal.close": "PORTAL_SERVICE_OPERATOR_REQUIRED",
    "taskSuggestions.create": "TASK_SUGGESTIONS_OPERATOR_REQUIRED",
    "taskSuggestions.accept": "TASK_SUGGESTIONS_OPERATOR_REQUIRED",
    "taskSuggestions.dismiss": "TASK_SUGGESTIONS_OPERATOR_REQUIRED",
    "tasks.dismiss": "TASKS_DISMISS_OPERATOR_REQUIRED",
    "mentions.list": "MENTIONS_INBOX_OPERATOR_REQUIRED",
    "mentions.dismiss": "MENTIONS_INBOX_OPERATOR_REQUIRED",
}
ALL = set(IMPL) | set(SAFE) | set(OPS)


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

    mod = importlib.import_module(
        "voicebridge.gateway.openclaw_handlers.scheduler_media")
    reg = MethodRegistry()
    mod.register(reg)
    return reg


# ----------------------------------------------------------- static proof
def test_family_static_proof():
    methods, funcs, consts, origins = OUM._parse_rpc_handlers_multi(
        [MODULE_PATH])
    assert set(methods) == ALL, set(methods) ^ ALL
    assert all(o.endswith(MODULE) for o in origins.values())
    for method, code in {**SAFE, **OPS}.items():
        assert OUM._emits_code(methods[method], funcs, code, consts), (
            f"{method} never emits {code!r}")
    for method in IMPL:
        assert OUM._is_non_trivial(methods[method], funcs), (
            f"{method} does no backend work")


def test_registry_registers_every_assigned_method():
    assert set(_registry().names()) == ALL


# -------------------------------------------------------- runtime per method
def test_every_method_returns_its_disposition(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import schedules as S

    jid = S.create_job(cfg, "sweep", "tidy", "every 15 minutes",
                       confirmed=True)["job"]["id"]
    reg = _registry()
    params = {
        "tts.enable": {},
        "tts.disable": {},
        "tts.setPersona": {"persona": "piper-amy-local"},
        "tts.setProvider": {"provider": "piper", "confirm": True},
        "cron.scratch.get": {"id": "1"},
        "cron.scratch.set": {"id": "1", "content": "x"},
        "cron.update": {"id": jid, "patch": {"enabled": False},
                        "confirm": True},
        "gateway.suspend.prepare": {},
        "gateway.suspend.resume": {},
        "gateway.suspend.handoff": {},
        "portal.list": {},
        "portal.open": {"port": 3000},
        "portal.close": {"id": "p1"},
        "taskSuggestions.create": {"sessionKey": "s", "prompt": "p"},
        "taskSuggestions.accept": {"taskId": "t"},
        "taskSuggestions.dismiss": {"taskId": "t"},
        "tasks.cancel": {"taskId": "nope"},
        "tasks.retry": {"taskId": "nope"},
        "tasks.dismiss": {"taskIds": ["t"]},
        "mentions.list": {},
        "mentions.dismiss": {"ids": ["m1"]},
    }
    assert set(params) == ALL
    expected = {**SAFE, **OPS}
    for method, args in params.items():
        out = reg.handle(cfg, method, args)
        assert isinstance(out, dict), method
        if method in expected:
            assert out.get("code") == expected[method], (method, out)
    # IMPLEMENTED setProvider performs the real settings write.
    out = reg.handle(cfg, "tts.setProvider",
                     {"provider": "piper", "confirm": True})
    assert out["state"] == "OK" and out["provider"] == "piper", out


# ----------------------------------------------------------- tts specifics
def test_tts_set_provider_rejects_unknown_provider(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(GatewayError) as exc:
        _registry().handle(cfg, "tts.setProvider", {"provider": "nope"})
    assert exc.value.code == "GATEWAY_BAD_PARAMS"


def test_tts_set_provider_requires_confirm(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(GatewayError):
        _registry().handle(cfg, "tts.setProvider", {"provider": "piper"})


def test_tts_toggle_never_fabricates_a_global_flag(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "tts.disable", {})
    assert out["applied"] is False, out
    assert out["code"] == "TTS_DISABLE_LOCAL_PROFILE", out


# ---------------------------------------------------------- cron specifics
def test_cron_update_applies_the_pause_subset(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import schedules as S

    job = S.create_job(cfg, "nightly", "summarize", "every 30 minutes",
                       confirmed=True)
    jid = job["job"]["id"]
    out = _registry().handle(cfg, "cron.update",
                             {"id": jid, "patch": {"enabled": False},
                              "confirm": True})
    assert out["code"] == "CRON_UPDATE_LOCAL_PAUSE_RESUME", out
    assert out["applied"]["enabled"] is False, out


def test_cron_scratch_is_a_typed_refusal(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "cron.scratch.set",
                             {"id": "1", "content": "hello"})
    assert out["state"] == "UNAVAILABLE", out
    assert out["written"] is False, out
    assert out["operator_prerequisite"], out


# ------------------------------------------------------- suspend specifics
def test_suspend_reports_real_power_state(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "gateway.suspend.prepare", {})
    assert out["code"] == "SUSPEND_PREPARE_LOCAL_POWER", out
    assert out["prepared"] is False, out
    assert "power" in out and out["power"].get("state") == "OK", out


# -------------------------------------------------------- tasks specifics
def test_tasks_cancel_missing_identity_fails_closed(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(GatewayError) as exc:
        _registry().handle(cfg, "tasks.cancel", {})
    assert exc.value.code == "GATEWAY_BAD_PARAMS"


def test_tasks_cancel_no_fabricated_cancellation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "tasks.cancel", {"taskId": "does-not-exist"})
    assert out["code"] == "TASKS_CANCEL_LOCAL_RECEIPT", out
    assert out["cancelled"] is False, out


def test_tasks_retry_no_fabricated_retry(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _registry().handle(cfg, "tasks.retry", {"taskId": "does-not-exist"})
    assert out["code"] == "TASKS_RETRY_LOCAL_RECEIPT", out
    assert out.get("result") is None, out


def test_mentions_dismiss_requires_ids(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(GatewayError) as exc:
        _registry().handle(cfg, "mentions.dismiss", {})
    assert exc.value.code == "GATEWAY_BAD_PARAMS"
