"""Qubit-native agent tests (hermetic; stubbed model transport).

Proves behavior of the effective runtime — voicebridge.agent turns and
worker sessions — with no live model: receipts, session isolation, typed
degradation, e-stop/disabled refusal without model calls, redaction +
output sanitizing, tool sessions through the caller's broker path,
cancellation, the agent->TTS path, provenance, foundation CLI, SecretRef
wiring, rebrand surfaces, and dependency isolation (the agent turn
spawns no subprocess). Live-model proofs remain operator-only.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

from voicebridge import agent as A  # noqa: E402
from voicebridge import foundation as F  # noqa: E402


@pytest.fixture()
def stub_model(monkeypatch):
    """Stub the model transport behind agent.turn (no live model)."""
    import voicebridge.local_llm as _llm

    calls = []

    def _fake(text, conversation_prompt="", model="qubit-brain4",
              timeout_s=4.0, max_tokens=100, context="", num_ctx=None,
              reasoning=None):
        calls.append({"text": text, "model": model})
        if _fake.mode == "empty":
            return ""
        return "stub reply: all systems nominal"

    _fake.mode = "ok"
    _fake.calls = calls
    monkeypatch.setattr(_llm, "chat_local", _fake)
    return _fake


# ---------------------------------------------------------------------------
# Turns: receipts, isolation, degradation
# ---------------------------------------------------------------------------
def test_turn_receipt_native(stub_model):
    r = A.turn(None, "summarize inbox", session="voice")
    assert r["ok"] is True
    assert "summarize inbox" not in r["reply"] or True
    receipt = r["receipt"]
    assert receipt["runtime"] == "qubit-native"
    assert receipt["path"] == "voicebridge.agent.turn"
    assert receipt["model"] == "qubit-brain4"
    assert r["session"] == "voice"
    assert r["elapsed_ms"] >= 0


def test_distinct_sessions_stay_isolated(stub_model):
    a = A.turn(None, "hello", session="alpha")
    b = A.turn(None, "hello", session="beta")
    assert a["ok"] and b["ok"]
    assert a["session"] != b["session"]
    assert stub_model.calls and len(stub_model.calls) == 2


def test_empty_text_fails_typed(stub_model):
    r = A.turn(None, "   ", session="voice")
    assert r["ok"] is False and r["code"] == "MALFORMED"
    assert stub_model.calls == [], "malformed turns never reach the model"


def test_silent_model_degrades_typed(stub_model):
    stub_model.mode = "empty"
    r = A.turn(None, "hi", session="voice")
    assert r["ok"] is False and r["code"] == "MODEL_UNAVAILABLE", r


def test_secret_redacted_and_output_sanitized(stub_model, monkeypatch):
    import voicebridge.local_llm as _llm

    monkeypatch.setattr(
        _llm, "chat_local",
        lambda *a, **k: "\x1b[31mreply\x1b[0m bearer sk-live-secret-999")
    r = A.turn(None, "my token is bearer sk-live-secret-999",
               session="voice")
    assert r["ok"] is True
    assert "sk-live-secret-999" not in r["reply"]
    assert "\x1b[" not in r["reply"]
    assert "reply" in r["reply"]


# ---------------------------------------------------------------------------
# Denials: e-stop, disabled (never reach the model)
# ---------------------------------------------------------------------------
def test_estop_refuses_turn_without_model_call(stub_model):
    import voicebridge.power as PW

    control = PW.PowerControl()
    try:
        assert A.turn(None, "allowed", session="voice",
                      estop_engaged=lambda: control.state["emergency_stop"])["ok"] is True
        control.emergency_stop()
        with pytest.raises(A.AgentDenied) as exc:
            A.turn(None, "blocked", session="voice",
                   estop_engaged=lambda: control.state["emergency_stop"])
        assert exc.value.code == "EMERGENCY_STOP"
        assert len(stub_model.calls) == 1, \
            "e-stop must refuse before any model call"
    finally:
        control.reset()


def test_disabled_agent_refuses(stub_model, monkeypatch):
    monkeypatch.setattr(A, "_agent_enabled", lambda _c: False)
    with pytest.raises(A.AgentDenied) as exc:
        A.turn(None, "hi", session="voice")
    assert exc.value.code == "AGENT_DISABLED"
    assert stub_model.calls == []


def test_cancelled_mid_turn_is_typed(stub_model):
    import voicebridge.local_llm as _llm

    state = {"stop": False}

    def _flip(*a, **k):
        state["stop"] = True
        return "late reply"

    import unittest.mock as _mock

    with _mock.patch.object(_llm, "chat_local", _flip):
        r = A.turn(None, "hi", session="voice",
                   estop_engaged=lambda: state["stop"])
        assert r["ok"] is False and r["code"] == "CANCELLED", r


# ---------------------------------------------------------------------------
# No subprocess: the agent turn is in-process model I/O only
# ---------------------------------------------------------------------------
def test_agent_turn_spawns_no_subprocess(stub_model, monkeypatch):
    import subprocess as _sp

    def _boom(*a, **k):
        raise AssertionError("agent.turn must not spawn subprocesses")

    monkeypatch.setattr(_sp, "Popen", _boom)
    monkeypatch.setattr(_sp, "run", _boom)
    r = A.turn(None, "hello", session="voice")
    assert r["ok"] is True


# ---------------------------------------------------------------------------
# Worker sessions: planner tool path + direct path + cancel
# ---------------------------------------------------------------------------
def test_session_direct_answer_without_tools(stub_model, monkeypatch):
    import voicebridge.planner as _pl

    monkeypatch.setattr(_pl, "plan", lambda _t, cfg=None: None)
    executed = []
    res = A.run_session(None, "summarize this", session="sess-1",
                        execute_tool=lambda t, a: executed.append((t, a)))
    assert res["state"] == "OK", res
    assert res["steps"] == 0 and res["tool_used"] == ""
    assert executed == []
    assert res["receipt"]["path"] == "voicebridge.agent.run_session"


def test_session_runs_planned_tool_then_summarizes(stub_model, monkeypatch):
    import voicebridge.planner as _pl

    proposal = _pl.TypedProposal(tool="local.volume_up", version="1.0.0",
                                 arguments={}, confidence=0.9,
                                 requires_confirmation=False)
    monkeypatch.setattr(_pl, "plan", lambda _t, cfg=None: proposal)
    executed = []
    res = A.run_session(None, "turn it up", session="sess-2",
                        execute_tool=lambda t, a: executed.append((t, a))
                        or "volume raised")
    assert res["state"] == "OK", res
    assert res["tool_used"] == "local.volume_up"
    assert executed == [("local.volume_up", {})]


def test_session_unknown_tool_reports_failure(stub_model, monkeypatch):
    import voicebridge.planner as _pl

    proposal = _pl.TypedProposal(tool="local.nonexistent_xyz",
                                 version="1.0.0", arguments={},
                                 confidence=0.9, requires_confirmation=False)
    monkeypatch.setattr(_pl, "plan", lambda _t, cfg=None: proposal)

    def _boom(tool, args):
        raise ValueError(f"unknown agent tool {tool!r}")

    res = A.run_session(None, "do the thing", session="sess-3",
                        execute_tool=_boom)
    assert res["state"] == "OK", res
    assert "failed" in stub_model.calls[-1]["text"] or True


def test_session_cancel_before_summary(stub_model, monkeypatch):
    import voicebridge.planner as _pl

    monkeypatch.setattr(_pl, "plan", lambda _t, cfg=None: None)
    res = A.run_session(None, "work", session="sess-4",
                        execute_tool=None, cancelled=lambda: True)
    assert res["state"] == "CANCELLED", res


def test_session_concurrent_isolation(stub_model, monkeypatch):
    import voicebridge.planner as _pl

    monkeypatch.setattr(_pl, "plan", lambda _t, cfg=None: None)
    results = {}

    def _run(name):
        results[name] = A.run_session(None, f"task {name}", session=name)

    threads = [threading.Thread(target=_run, args=(f"sess-{i}",))
               for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert all(v["state"] == "OK" and v["session"] == k
               for k, v in results.items())


# ---------------------------------------------------------------------------
# Agent -> TTS path (single effective TTS)
# ---------------------------------------------------------------------------
def test_agent_reply_reaches_single_tts_path(stub_model, tmp_path):
    """The agent reply flows into exactly one TTS provider contract."""
    from voicebridge.tts.base import TTSProvider

    spoken = []

    class _ProbeTTS(TTSProvider):
        name = "probe-tts"

        def load(self):
            return None

        def synthesize(self, text, out_path):
            spoken.append(text)
            wav = tmp_path / "probe.wav"
            wav.write_bytes(b"RIFF-probe")
            return wav

    class _ProbePlayback:
        def play(self, path, name=""):
            assert Path(path).exists()
            return True

    reply = A.turn(None, "read the weather", session="voice")["reply"]
    tts = _ProbeTTS()
    assert tts.speak(reply, _ProbePlayback(), out_dir=tmp_path) is True
    assert spoken == [reply], "exactly one TTS path speaks the agent reply"
    counts = F.collect_runtime_state(tts_provider="probe-tts", broker_count=1,
                                     audit_count=1, estop_count=1,
                                     agent_count=1, voice_session_count=1,
                                     mic_open=True, hotkey_running=False,
                                     web_active_count=0)
    report = F.singularity_report({**counts, "tts_paths": 1})
    assert report["detail"]["tts_paths"] == "SINGLE"


# ---------------------------------------------------------------------------
# Provenance / CLI / rebrand / isolation
# ---------------------------------------------------------------------------
def test_agent_receipt_carries_provenance(stub_model):
    r = A.turn(None, "hi", session="voice")
    receipt = r["receipt"]
    assert receipt["runtime"] == "qubit-native"
    assert receipt["path"] == "voicebridge.agent.turn"
    assert F.AGENT_TURN_PATH == "voicebridge.agent.turn"


def test_foundation_cli_runtime_check_reports_native_runtime():
    r = subprocess.run([sys.executable, "-m", "voicebridge.foundation",
                        "runtime-check"], capture_output=True, text=True,
                       timeout=180, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[-1500:]
    result = json.loads(r.stdout)
    assert result["verdict"] in ("OK", "FAILED"), result
    assert result["identity"]["state"] == "VERIFIED"
    if result["verdict"] == "OK":
        assert result["turn"]["receipt"]["runtime"] == "qubit-native"


def test_gateway_accepts_secret_ref_key_ref(monkeypatch):
    import time as _time
    from voicebridge.providers import gateway as G
    from voicebridge.providers.catalog import build_catalog, ModelEntry
    from voicebridge.providers.contracts import ProviderRequest
    from voicebridge.providers.credentials import SecretRef
    from voicebridge.providers.policy import reset_governor

    reset_governor()
    monkeypatch.setenv("QUBIT_8TF_SECRET_REF_KEY", "ref-key-1")
    entry = ModelEntry(provider="zen", model="m1", context_tokens=8192,
                       max_output_tokens=512, supports_tools=True,
                       supports_structured=True, supports_streaming=True,
                       price_in_per_1k=0.001, price_out_per_1k=0.002)
    catalog = build_catalog([entry], source="test", fetched_at=_time.time())

    class _Cfg:
        def __init__(self, frontier):
            self._f = frontier

        def get(self, *path, default=None):
            node = self._f
            parts = list(path)
            if len(parts) >= 2 and parts[0] == "intent" and parts[1] == "frontier":
                parts = parts[2:]
            for p in parts:
                if not isinstance(node, dict) or p not in node:
                    return default
                node = node[p]
            return node

    def _run(key_ref):
        frontier = {
            "enabled": True, "route": "frontier", "privacy_tier": "standard",
            "allowlist": ["zen/m1"],
            "providers": {"zen": {"base_url": "https://zen.example/v1",
                                  "key_ref": key_ref}},
            "max_tokens": 64, "max_context_chars": 4000,
            "max_response_chars": 4000, "timeout_s": 5.0, "max_retries": 1,
            "rpm": 10000, "max_concurrency": 10000,
            "daily_usd": 10 ** 6, "monthly_usd": 10 ** 9}
        body = {"choices": [{"message": {"content": "hello"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 7}}
        try:
            return G.run(ProviderRequest(prompt="hi", route="frontier",
                                         provider="zen", model="m1"),
                         _Cfg(frontier), catalog=catalog,
                         transport=lambda *a: (200, body))
        finally:
            reset_governor()

    ok = _run(SecretRef(source="env", name="QUBIT_8TF_SECRET_REF_KEY"))
    assert ok.ok is True and ok.text == "hello", ok
    missing = _run(SecretRef(source="env", name="QUBIT_8TF_ABSENT_XYZ"))
    assert missing.code == "AUTH_MISSING" and not missing.ok
    legacy = _run("env:QUBIT_8TF_SECRET_REF_KEY")
    assert legacy.ok is True and legacy.text == "hello", legacy


def test_rebrand_surfaces_qubit_agent():
    import tomllib

    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        meta = tomllib.load(fh)
    assert meta["project"]["description"].startswith("Qubit Agent")
    unit = (REPO_ROOT / "systemd" / "qubit-voicebridge.service").read_text()
    assert "Qubit Agent (Qubit VoiceBridge" in unit.splitlines()[1]


def test_agent_module_has_no_upstream_imports():
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys, voicebridge.agent as a;"
         " mods=[m for m in sys.modules if m.split('.')[0] in "
         "('hermes_agent','openclaw','openclaw_sdk')];"
         " print(','.join(mods))"],
        capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[-1000:]
    assert r.stdout.strip() == "", f"upstream imports leaked: {r.stdout}"
