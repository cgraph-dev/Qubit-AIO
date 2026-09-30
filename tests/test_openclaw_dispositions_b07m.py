"""B-07M — OpenClaw delta dispositions are behavior-proven, not name-matched.

Two independent proofs:

1. every disposition in ``voicebridge.gateway.qubit_dispositions`` is checked
   against the AST of the real RPC handler: IMPLEMENTED handlers must perform
   backend work, and SAFE_EQUIVALENT / OPERATOR_ONLY handlers must actually
   emit the declared typed code;
2. the live handlers are invoked (in-process, temp state) and their emitted
   codes/behavior are asserted directly, so the map cannot drift from runtime.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import qubit_upgrade_manifest as OUM  # noqa: E402


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


# ---------------------------------------------------------------- static proof
def test_dispositions_match_registered_handler_behavior():
    from voicebridge.gateway import qubit_dispositions as OD
    from voicebridge.gateway import rpc as R

    methods, funcs, consts = OUM._parse_rpc_handlers(
        REPO_ROOT / "voicebridge" / "gateway" / "rpc.py")
    registry = set(R.default_registry().names())
    for method, rec in OD.DISPOSITIONS.items():
        assert method in registry, f"{method} is not registered"
        node = methods.get(method)
        assert node is not None, f"{method} has no AST handler"
        status = str(rec.get("status", "OPEN"))
        if status == "IMPLEMENTED":
            assert OUM._is_non_trivial(node, funcs), (
                f"{method} is IMPLEMENTED but its handler does no backend work")
        elif status in ("SAFE_EQUIVALENT", "OPERATOR_ONLY"):
            code = str(rec.get("code", ""))
            assert code, f"{method} refusal declares no code"
            assert OUM._emits_code(node, funcs, code, consts), (
                f"{method} declares {code!r} but the handler never emits it")
        else:
            raise AssertionError(f"{method} has unhandled status {status}")


# ------------------------------------------------------------- runtime proof
def test_delta_handlers_emit_declared_codes(tmp_path, monkeypatch):
    from voicebridge.gateway import rpc as R

    cfg = _cfg(tmp_path, monkeypatch)
    reg = R.default_registry()

    prepared = reg.handle(cfg, "environments.prepare", {})
    assert prepared["code"] == "ENVIRONMENT_PREPARE_LOCAL_PLAN", prepared
    assert prepared["mode"] == "plan", prepared

    report = reg.handle(cfg, "update.report", {})
    assert report["code"] == "UPDATE_REPORT_LOCAL_READ", report
    assert report["mode"] == "local-read", report

    # plugins.reload is a real on-disk re-read with a truthful changed flag.
    first = reg.handle(cfg, "plugins.reload", {})
    assert first["code"] == "PLUGIN_RELOAD_LOCAL_REREAD", first
    assert first["reloaded"] is True and first["changed"] is False, first
    assert first["first_reload"] is True, first
    second = reg.handle(cfg, "plugins.reload", {})
    assert second["changed"] is False and second["first_reload"] is False, second

    assert reg.handle(cfg, "models.authSetApiKey", {})["code"] == \
        "SECRET_WRITE_REFUSED"
    assert reg.handle(cfg, "models.authLogin", {})["code"] == \
        "OAUTH_MANUAL_HANDOFF"
    assert reg.handle(cfg, "mcp.authLogin", {})["code"] == \
        "OAUTH_MANUAL_HANDOFF"
    assert reg.handle(cfg, "diagnostics.cpuProfile", {})["code"] == \
        "DIAGNOSTICS_OPERATOR_REQUIRED"
    assert reg.handle(cfg, "controlUi.sessionPullRequests.checks", {})["code"] \
        == "FORGE_API_OPERATOR_REQUIRED"


def test_talk_voice_handlers_are_real_and_emit_no_phantom_code(tmp_path,
                                                               monkeypatch):
    from voicebridge.gateway import rpc as R

    cfg = _cfg(tmp_path, monkeypatch)
    reg = R.default_registry()
    out = reg.handle(cfg, "talk.voice.get", {})
    assert out["state"] == "OK" and "devices" in out, out
    # The retired phantom TALK_VOICE_* codes are never emitted.
    assert "TALK_VOICE_DEVICE_SURFACE" not in str(out)
    assert not str(out.get("code", "")).startswith("TALK_VOICE")
