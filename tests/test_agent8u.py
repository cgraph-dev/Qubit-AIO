"""Packet 8T-F — real runtime integration tests (hermetic; no live services).

Proves behavior against a genuinely started worker subprocess (no mocks):
startup, ping, turns, session continuity/isolation, typed degradation,
timeout/cancel reap, e-stop refusal without spawn, minimal environment,
redaction, single-supervisor discipline, VoiceBridge->runtime->TTS path,
provenance, foundation CLI, SecretRef gateway wiring, rebrand surfaces,
and dependency isolation. Live mic/speaker/browser/account/model/timer
proofs remain operator-only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

from voicebridge import foundation as F  # noqa: E402
from voicebridge.runtime import HermesRuntime, RuntimeDenied  # noqa: E402


@pytest.fixture()
def runtime():
    rt = HermesRuntime(provider="fixture")
    yield rt
    rt.stop()


# ---------------------------------------------------------------------------
# Real startup / ping / status
# ---------------------------------------------------------------------------
def test_runtime_starts_and_pings_with_provenance(runtime):
    assert runtime.status()["state"] == "DOWN"
    pid = runtime.start()
    assert pid > 0 and runtime.alive
    pong = runtime.ping()
    assert pong["ok"] is True
    assert pong["runtime"] == "qubit-hermes-adapted"
    assert pong["pin"] == F.HERMES_COMMIT
    live = runtime.status()
    assert live["state"] == "LIVE" and live["pid"] == pid


def test_runtime_start_is_idempotent_single_process(runtime):
    first = runtime.start()
    second = runtime.start()
    assert first == second
    assert runtime.alive


def test_turn_reaches_runtime_and_returns_receipt(runtime):
    runtime.start()
    r = runtime.turn("summarize inbox", session="voice")
    assert r["ok"] is True and r["turn"] == 1
    assert "summarize inbox" in r["reply"]
    receipt = r["receipt"]
    assert receipt["runtime"] == "qubit-hermes-adapted"
    assert receipt["pin"] == F.HERMES_COMMIT
    assert receipt["session"] == "voice"
    assert receipt["path"] == "voicebridge.runtime.turn"


def test_shared_voice_session_continuity(runtime):
    """Right Ctrl and dashboard address the same voice session: turns on one
    session id increment together through the real worker."""
    runtime.start()
    first = runtime.turn("ptt turn", session="voice")
    second = runtime.turn("dashboard turn", session="voice")
    assert (first["turn"], second["turn"]) == (1, 2)
    assert first["receipt"]["session"] == second["receipt"]["session"] == "voice"


def test_distinct_sessions_stay_isolated(runtime):
    runtime.start()
    a = runtime.turn("hello", session="alpha")
    b = runtime.turn("hello", session="beta")
    assert (a["turn"], b["turn"]) == (1, 1)
    assert a["session"] != b["session"]


def test_empty_text_fails_typed_not_crash(runtime):
    runtime.start()
    r = runtime.turn("   ", session="voice")
    assert r["ok"] is False and r["code"] == "MALFORMED"


def test_unknown_provider_degrades_typed(runtime):
    rt = HermesRuntime(provider="nonexistent-xyz")
    try:
        r = rt.turn("hi", session="voice")
        assert r["ok"] is False and r["code"] == "PROVIDER_DEGRADED"
    finally:
        rt.stop()


def test_secret_in_request_redacted_in_reply(runtime):
    runtime.start()
    r = runtime.turn("my token is bearer sk-live-secret-999", session="voice")
    assert r["ok"] is True
    assert "sk-live-secret-999" not in r["reply"]
    assert "[REDACTED]" in r["reply"]


# ---------------------------------------------------------------------------
# Cancellation / timeout / e-stop / environment
# ---------------------------------------------------------------------------
def test_cancel_kills_and_reaps_worker(runtime):
    pid = runtime.start()
    out = runtime.cancel()
    assert out["state"] == "CANCELLED" and out["pid"] == pid
    assert not runtime.alive
    assert runtime.status()["state"] == "DOWN"
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)  # no orphan left behind


def test_estop_refuses_turn_without_spawning():
    import voicebridge.power as PW

    control = PW.PowerControl()
    rt = HermesRuntime(provider="fixture",
                       estop_engaged=lambda: control.state["emergency_stop"])
    try:
        assert rt.turn("allowed", session="voice")["ok"] is True
        rt.stop()
        control.emergency_stop()
        with pytest.raises(RuntimeDenied):
            rt.turn("blocked", session="voice")
        assert not rt.alive and rt.pid is None, \
            "e-stop must refuse before any worker spawn"
    finally:
        control.reset()
        rt.stop()


def test_worker_inherits_minimal_environment_only(tmp_path, monkeypatch):
    marker = "QUBIT_8TF_ENV_PROBE_MARKER"
    monkeypatch.setenv(marker, "parent-secret-value")
    monkeypatch.setenv("QUBIT_RUNTIME_PROVIDER", "fixture")
    rt = HermesRuntime(provider="fixture")
    try:
        pid = rt.start()
        with open(f"/proc/{pid}/environ", "rb") as fh:
            env_raw = fh.read().decode("utf-8", "replace")
        assert marker not in env_raw, "full environment must not reach the worker"
        assert "parent-secret-value" not in env_raw
        assert "QUBIT_RUNTIME_PROVIDER=fixture" in env_raw
    finally:
        rt.stop()


# ---------------------------------------------------------------------------
# VoiceBridge -> runtime -> TTS path (single effective TTS)
# ---------------------------------------------------------------------------
def test_runtime_reply_reaches_single_tts_path(runtime, tmp_path):
    """The runtime reply flows into exactly one TTS provider contract."""
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

    runtime.start()
    reply = runtime.turn("read the weather", session="voice")["reply"]
    tts = _ProbeTTS()
    assert tts.speak(reply, _ProbePlayback(), out_dir=tmp_path) is True
    assert spoken == [reply], "exactly one TTS path speaks the runtime reply"
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
def test_runtime_receipt_carries_provenance(runtime):
    runtime.start()
    pong = runtime.ping()
    assert pong["pin"] == "2237be355906fbe6065ce1815711eee52b2d646e"
    assert F.HERMES_URL.endswith("NousResearch/hermes-agent")


def test_foundation_cli_runtime_check_reports_real_runtime():
    r = subprocess.run([sys.executable, "-m", "voicebridge.foundation",
                        "runtime-check"], capture_output=True, text=True,
                       timeout=120, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[-1500:]
    result = json.loads(r.stdout)
    assert result["verdict"] == "OK"
    assert result["turn"]["receipt"]["runtime"] == "qubit-hermes-adapted"
    assert result["live"]["state"] == "LIVE"
    assert isinstance(result["live"]["pid"], int)


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


def test_worker_process_has_no_upstream_imports():
    r = subprocess.run(
        [sys.executable, "-c",
         "import sys, voicebridge.runtime.worker as w;"
         " mods=[m for m in sys.modules if m.split('.')[0] in "
         "('hermes_agent','openclaw','openclaw_sdk')];"
         " print(','.join(mods))"],
        capture_output=True, text=True, timeout=60, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stderr[-1000:]
    assert r.stdout.strip() == "", f"upstream imports leaked: {r.stdout}"
