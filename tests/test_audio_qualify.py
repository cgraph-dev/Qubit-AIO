"""Packet 8H tests: operator-gated real audio / PTT / Right Ctrl qualification.

Hermetic-only. Covers:
- the live gate failing closed (live device work impossible by default);
- session-context verification (XDG_RUNTIME_DIR, DISPLAY/Wayland, Pulse/
  PipeWire) with typed failures and NEVER guessing the highest display;
- effective source/sink resolution: a missing configured pin is DEVICE_MISMATCH
  (aggregate fallback exposed), never silently passed through;
- provider/model identity recording so a real response is distinguishable from
  an unavailable-provider fallback;
- the latency stage decomposition (first-audio vs end-to-end) and fallback
  detection;
- synthetic signal classification (silence / clipping / speech) and VAD
  boundaries with a webrtc Endpointer (no model);
- Right Ctrl duplicate-press / stuck-key protection;
- concurrent web-PTT + Right-Ctrl turn isolation (no cross-turn answer);
- the restart circuit (authorization / backoff / circuit breaker);
- temporary-audio cleanup;
- isolated redacted qualification summaries that never persist private audio.
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
sys.path.insert(0, str(REPO_ROOT))

import voicebridge.audio.qualify as Q  # noqa: E402
from voicebridge.audio.qualify import (  # noqa: E402
    QUALIFY_ENV, RestartCircuit, deny_live, effective_devices,
    isolate_cfg, latency_probe, live_authorized, provider_identity,
    ptt_edge_probe, restart_guard_probe, run_concurrent_turns,
    run_qualification, session_context, signal_probe,
    signal_qualification, temp_audio_cleanup_probe, vad_boundaries_probe,
)
from voicebridge.audio.settings import DeviceInfo  # noqa: E402


def _cfg(tmp_path):
    from voicebridge.config import Config
    state = tmp_path / "production-state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl_path: '" + str(state) + "'\n")
    return Config.load(p)


def _devices(mic_name="", sink_name="", default_mic=True, default_sink=True):
    inputs = [DeviceInfo(name=mic_name or "pulse", role="input",
                         backend="Pulse", channels=1, default_samplerate=48000,
                         is_default=default_mic)]
    outputs = [DeviceInfo(name=sink_name or "pipewire", role="output",
                          backend="Pulse", channels=2,
                          default_samplerate=48000, is_default=default_sink)]
    return {"inputs": inputs, "outputs": outputs}


# ---------------------------------------------------------------- live gate
def test_live_gate_fails_closed_by_default(monkeypatch):
    monkeypatch.delenv(QUALIFY_ENV, raising=False)
    assert live_authorized() is False
    denied = deny_live(True)
    assert denied is not None
    assert denied["state"] == "denied"
    assert denied["code"] == "LIVE_NOT_AUTHORIZED"
    assert deny_live(False) is None


def test_live_gate_opens_only_with_operator_env(monkeypatch):
    monkeypatch.setenv(QUALIFY_ENV, "1")
    assert live_authorized() is True
    assert deny_live(True) is None
    monkeypatch.setenv(QUALIFY_ENV, "0")
    assert deny_live(True) is not None


def test_cli_live_without_gate_exits_denied():
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "audio_qualify.py"),
         "--live"],
        capture_output=True, text=True, timeout=60,
        env={k: v for k, v in os.environ.items() if k != QUALIFY_ENV})
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["code"] == "LIVE_NOT_AUTHORIZED"


def test_cli_preflight_runs_without_audio(tmp_path):
    cfg = _cfg(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "audio_qualify.py"),
         "--preflight", "--config", str(cfg.path)],
        capture_output=True, text=True, timeout=60,
        env={k: v for k, v in os.environ.items() if k != QUALIFY_ENV})
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert "session" in payload and "devices" in payload
    assert "providers" in payload


# ------------------------------------------------------------ session context
def test_session_context_missing_xdg_is_typed(tmp_path):
    env = {"PATH": os.environ.get("PATH", ""), "DISPLAY": ":0"}
    out = session_context(env, display_resolver=lambda e: ":0",
                          socket_count=lambda: 1,
                          pactl_fn=lambda e: True)
    assert out["states"]["xdg_runtime_dir"] == "MISSING"
    assert "xdg_runtime_dir" in out["recovery"]
    assert out["state"] == "degraded"


def test_session_context_ambiguous_display_never_guesses(tmp_path):
    env = {"PATH": os.environ.get("PATH", ""),
           "XDG_RUNTIME_DIR": str(tmp_path)}
    out = session_context(env, display_resolver=lambda e: None,
                          socket_count=lambda: 2,
                          pactl_fn=lambda e: True)
    assert out["states"]["display"] == "AMBIGUOUS"
    assert out["display"] == ""
    assert "never guesses" in out["recovery"]["display"]


def test_session_context_headless_is_typed():
    env = {"PATH": os.environ.get("PATH", "")}
    out = session_context(env, display_resolver=lambda e: None,
                          socket_count=lambda: 0,
                          pactl_fn=lambda e: True)
    assert out["states"]["display"] == "NO_SESSION"
    assert out["recovery"]["display"]


def test_session_context_pulse_unavailable_is_typed():
    env = {"PATH": os.environ.get("PATH", "")}
    out = session_context(env, display_resolver=lambda e: None,
                          socket_count=lambda: 0,
                          pactl_fn=lambda e: False)
    assert out["states"]["pulse_pipewire"] == "UNAVAILABLE"
    assert "pulse_pipewire" in out["recovery"]


def test_session_context_ok():
    import tempfile
    with tempfile.TemporaryDirectory() as xdg:
        env = {"PATH": os.environ.get("PATH", ""), "XDG_RUNTIME_DIR": xdg}
        out = session_context(env, display_resolver=lambda e: ":1",
                              socket_count=lambda: 1,
                              pactl_fn=lambda e: True)
        assert out["state"] == "OK"
        assert out["states"]["display"] == "OK"
        assert out["display"] == ":1"


# ---------------------------------------------------------- effective devices
def test_effective_devices_default_route(tmp_path):
    cfg = _cfg(tmp_path)
    out = effective_devices(cfg, devices=_devices(),
                            resolve=lambda pin, role: 0)
    assert out["state"] == "OK"
    assert out["mic"]["route"] == "system_default"
    assert out["sink"]["route"] == "system_default"


def test_effective_devices_pinned_present(tmp_path):
    cfg = _cfg(tmp_path)
    data = cfg.data
    data["mic"] = {"device_name": "Scarlett 2i2 4th Gen"}
    data["audio"] = {"output_device_name": "pipewire"}
    cfg = Q.Config(data=data, path=cfg.path)
    out = effective_devices(cfg, devices=_devices(mic_name="Scarlett 2i2 4th Gen",
                                                  sink_name="pipewire"),
                            resolve=lambda pin, role: 1)
    assert out["state"] == "OK"
    assert out["mic"]["state"] == "OK" and out["mic"]["route"] == "pinned"
    assert out["sink"]["state"] == "OK" and out["sink"]["route"] == "pinned"


def test_effective_devices_missing_pin_is_device_mismatch(tmp_path):
    cfg = _cfg(tmp_path)
    data = cfg.data
    data["mic"] = {"device_name": "Focusrite Scarlett 2i2"}
    data["audio"] = {"output_device_name": "Some Missing Headset"}
    cfg = Q.Config(data=data, path=cfg.path)
    out = effective_devices(cfg, devices=_devices(mic_name="pulse",
                                                  sink_name="pipewire"),
                            resolve=lambda pin, role: None)
    # A missing configured pin must be DEVICE_MISMATCH/degraded and expose the
    # aggregate fallback — never a clean pass.
    assert out["state"] == "degraded"
    assert out["mic"]["state"] == "DEVICE_MISMATCH"
    assert out["mic"]["route"] == "aggregate_fallback"
    assert out["sink"]["state"] == "DEVICE_MISMATCH"
    assert out["sink"]["route"] == "aggregate_fallback"


def test_device_mismatch_guidance_names_configured_pin_and_available(tmp_path):
    cfg = _cfg(tmp_path)
    data = cfg.data
    data["mic"] = {"device_name": "Focusrite Scarlett 2i2"}
    cfg = Q.Config(data=data, path=cfg.path)
    out = effective_devices(cfg, devices=_devices(mic_name="pulse",
                                                  sink_name="pipewire"),
                            resolve=lambda pin, role: None)
    mic = out["mic"]
    # the operator must see the exact configured pin AND the present names
    assert mic["configured_pin"] == "Focusrite Scarlett 2i2"
    assert mic["state"] == "DEVICE_MISMATCH"
    assert mic["available_devices"] == ["pulse"]
    # the pin name appears verbatim in the guidance and the guidance must not
    # silently suggest a specific replacement device
    assert "Focusrite Scarlett 2i2" in mic["detail"]
    assert "pulse" in mic["detail"]
    assert out["sink"]["state"] == "OK"


def test_effective_devices_enumeration_error_is_degraded(tmp_path, monkeypatch):
    import voicebridge.audio.settings as settings_mod
    from voicebridge.audio.settings import AudioSettingsError
    cfg = _cfg(tmp_path)

    def _boom():
        raise AudioSettingsError("permission denied")

    monkeypatch.setattr(settings_mod, "list_devices", _boom)
    out = effective_devices(cfg, resolve=lambda pin, role: None)
    assert out["state"] == "degraded"
    assert out["mic"]["state"] == "UNAVAILABLE"
    assert "permission" in out["enumeration_error"]


# ---------------------------------------------------------- provider identity
def test_provider_identity_records_configured_identity(tmp_path):
    cfg = _cfg(tmp_path)
    data = cfg.data
    data["asr"] = {"provider": "sherpa", "engine": "parakeet",
                   "language": "en"}
    data["tts"] = {"provider": "piper", "voice": "en_US-amy-medium",
                   "sample_rate": 22050}
    data["intent"] = {"local_llm": {"model": "qubit-brain"}}
    cfg = Q.Config(data=data, path=cfg.path)
    out = provider_identity(cfg)
    assert out["asr"]["configured_provider"] == "sherpa"
    assert out["asr"]["engine"] == "parakeet"
    assert out["tts"]["voice"] == "en_US-amy-medium"
    assert out["reasoning_model"]["configured"] == "qubit-brain"
    assert out["tts"]["fallback_to_piper"] is False


def test_provider_identity_flags_openai_fallback(tmp_path):
    cfg = _cfg(tmp_path)
    data = cfg.data
    data["tts"] = {"provider": "openai", "voice": "nova"}
    cfg = Q.Config(data=data, path=cfg.path)
    out = provider_identity(cfg)
    assert out["tts"]["configured_provider"] == "openai"
    assert out["tts"]["fallback_to_piper"] is True


def test_provider_identity_prefers_live_provider_object(tmp_path):
    cfg = _cfg(tmp_path)
    class _Asr:
        name = "sherpa"
        engine = "parakeet"
    class _Tts:
        name = "piper"
        voice = "en_US-amy-medium"
    out = provider_identity(cfg, asr=_Asr(), tts=_Tts())
    assert out["asr"]["actual_provider"] == "sherpa"
    assert out["tts"]["actual_provider"] == "piper"


# ------------------------------------------------------------ latency stages
def test_stage_clock_measures_first_audio_vs_end_to_end():
    def _t():
        return time.monotonic()
    out = Q.stage_clock({
        "capture": lambda: (True, ""),
        "asr": lambda: (_sleep(0.02), "ok"),
        "reasoning": lambda: (_sleep(0.02), ""),
        "synthesis": lambda: (_sleep(0.01), ""),
        "playback": lambda: (_sleep(0.02), ""),
    })
    assert out["stages"]["asr"]["ms"] >= 10
    assert out["first_audio_ms"] is not None
    assert out["first_audio_ms"] < out["end_to_end_ms"]
    assert out["failed"] == []


def _sleep(seconds: float):
    time.sleep(seconds)
    return (True, "")


def test_stage_clock_detects_fallback():
    def _boom():
        raise RuntimeError("provider unavailable")
    out = Q.stage_clock({
        "capture": lambda: (True, ""),
        "asr": _boom,
        "reasoning": lambda: (True, ""),
        "synthesis": lambda: (True, ""),
        "playback": lambda: (True, ""),
    })
    assert out["failed"] == ["asr"]
    assert out["real_vs_fallback"] == "fallback"


def test_latency_probe_hermetic(tmp_path):
    cfg = _cfg(tmp_path)
    out = latency_probe(identity=provider_identity(cfg))
    assert out["first_audio_ms"] is not None
    assert out["end_to_end_ms"] >= out["first_audio_ms"]
    assert out["real_vs_fallback"] == "real"


# -------------------------------------------------------------- signal probe
def test_signal_probe_classifies_silence_speech_clipping():
    sil = signal_probe(Q.silence_pcm())
    assert sil["state"] == "SILENCE" and sil["below_floor"] is True
    speech = signal_probe(Q.utterance_pcm())
    assert speech["state"] == "SPEECH"
    clip = signal_probe(Q.clipped_pcm())
    assert clip["state"] == "CLIPPING" and clip["clipped"] is True


def test_signal_probe_48k_rate_conversion():
    out = signal_probe(Q.utterance_pcm(), rate=48000)
    assert out["state"] == "SPEECH"
    assert out["rate"] == 48000


def test_vad_boundaries_with_webrtc():
    from voicebridge.audio.vad import Endpointer
    ep = Endpointer(vad_backend="webrtc", aggressiveness=2,
                    speech_start_ms=220, silence_timeout_s=1.2)
    out = vad_boundaries_probe(ep, Q.utterance_pcm())
    assert out["speech_started"] is True
    assert out["done"] is False


def test_vad_boundaries_silence_never_starts():
    from voicebridge.audio.vad import Endpointer
    ep = Endpointer(vad_backend="webrtc", aggressiveness=2,
                    speech_start_ms=220, silence_timeout_s=1.2)
    out = vad_boundaries_probe(ep, Q.silence_pcm(1.0))
    assert out["speech_started"] is False
    assert out["final_state"] == "idle"


def test_signal_qualification_all_checks():
    out = signal_qualification()
    assert out["state"] == "OK"
    assert all(out["checks"].values())


# ------------------------------------------------------------------ PTT edges
def test_ptt_edge_probe_no_stuck_key():
    out = ptt_edge_probe()
    assert out["state"] == "OK"
    assert out["starts"] >= 1 and out["ends"] >= 1
    assert out["no_stuck_key"] is True
    assert out["held_after_release"] == []


# ------------------------------------------------------------- concurrency
def test_run_concurrent_turns_no_cross_turn_answer():
    shared = {}   # a deliberately shared container

    def _runner(tid, kind):
        # each turn touches ONLY its own key -> provably isolated
        shared[tid] = shared.get(tid, 0) + 1
        if kind == "cancel":
            return {"turn": tid, "kind": kind, "answer_turn": tid}
        time.sleep(0.001)
        return {"turn": tid, "kind": kind, "answer_turn": tid,
                "answer": f"answer-{tid}"}

    plan = ([(f"web-{i}", "web") for i in range(6)] +
            [(f"rctrl-{i}", "rctrl") for i in range(6)] +
            [(f"cancel-{i}", "cancel") for i in range(2)])
    out = run_concurrent_turns(plan, runner=_runner, workers=6)
    assert out["state"] == "OK"
    assert out["turns_total"] == 14
    assert out["no_cross_turn_answer"] is True
    assert out["missing"] == [] and out["contaminated"] == []
    assert out["cancel_leak"] == []
    # every turn actually ran exactly once
    assert sum(shared.values()) == 14


def test_run_concurrent_turns_detects_leak():
    # a runner that attributes the WRONG turn must be caught by the probe
    def _bad_runner(tid, kind):
        return {"turn": tid, "answer_turn": "web-0"}   # hijacked answer
    out = run_concurrent_turns([("web-0", "web"), ("rctrl-0", "rctrl")],
                               runner=_bad_runner)
    assert out["state"] == "failed"
    assert out["no_cross_turn_answer"] is False
    assert out["contaminated"] == ["rctrl-0"]


# ------------------------------------------------------------ restart circuit
def test_restart_circuit_requires_authorization():
    out = restart_guard_probe(authorized=False)
    assert out["state"] == "OK"
    assert all(o["reason"] == "RESTART_NOT_AUTHORIZED" for o in out["outcomes"])


def test_restart_circuit_backoff_and_breaker():
    ticks = iter([0.0, 0.0, 0.0, 0.0, 5.0, 5.0, 8.0, 8.0])
    circ = RestartCircuit(max_attempts=2, window_s=300.0,
                          base_backoff_s=1.0, max_backoff_s=10.0,
                          clock=lambda: next(ticks))
    reasons = []
    for _ in range(8):
        _, reason = circ.allow_restart(authorized=True)
        reasons.append(reason)
    # 2 allowed, then backoff, then the circuit opens once the backoff expires
    assert reasons.count("OK") >= 2
    assert any(r == "CIRCUIT_OPEN" for r in reasons)
    assert any(str(r).startswith("BACKOFF") for r in reasons)


# ------------------------------------------------------------- temp cleanup
def test_temp_audio_cleanup_probe(tmp_path):
    out = temp_audio_cleanup_probe(tmp_path)
    assert out["state"] == "OK"
    assert out["created"] == 2
    assert out["removed"] == 2
    assert out["remaining"] == []


# ------------------------------------------------------------ orchestration
def test_run_qualification_dry_run_no_persist(tmp_path):
    cfg = _cfg(tmp_path)
    state_dir = tmp_path / "qualify"
    out = run_qualification(cfg, state_dir=state_dir, dry_run=True)
    assert out["state"] == "OK"
    assert not list((state_dir / "qualify").rglob("summary.json"))


def test_run_qualification_persists_redacted_summary(tmp_path):
    cfg = _cfg(tmp_path)
    state_dir = tmp_path / "qualify"
    out = run_qualification(cfg, state_dir=state_dir, dry_run=False)
    assert out["state"] == "OK"
    summary = state_dir / "qualify" / out["run_id"] / "summary.json"
    assert summary.exists()
    assert (summary.stat().st_mode & 0o077) == 0
    payload = json.loads(summary.read_text())
    blob = json.dumps(payload)
    # no absolute paths / machine-only indices in the persisted record
    assert "qualify/" not in blob or "recovery" not in blob
    assert "effective_index" not in blob and "index" not in blob


def test_run_qualification_isolated_from_production_state(tmp_path):
    cfg = _cfg(tmp_path)
    prod = cfg.state_dir()
    state_dir = tmp_path / "qualify"
    run_qualification(cfg, state_dir=state_dir, dry_run=False)
    assert not (prod / "qualification").exists()
    # only the isolated state dir holds qualification artifacts
    assert (state_dir / "qualify").is_dir()


def test_run_qualification_live_without_gate_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALIFY_ENV, raising=False)
    cfg = _cfg(tmp_path)
    state_dir = tmp_path / "qualify"
    out = run_qualification(cfg, state_dir=state_dir, live=True, dry_run=True)
    assert out["state"] == "failed"
    assert out["stages"]["capture"]["code"] == "LIVE_NOT_AUTHORIZED"
    assert out["stages"]["playback"]["code"] == "LIVE_NOT_AUTHORIZED"
    assert out["stages"]["ptt"]["code"] == "LIVE_NOT_AUTHORIZED"


def test_isolate_cfg_redirects_state(tmp_path):
    cfg = _cfg(tmp_path)
    iso = isolate_cfg(cfg, tmp_path / "isolated")
    assert iso.state_dir() == tmp_path / "isolated"
    assert cfg.state_dir() != tmp_path / "isolated"


# -------------------------------------------------- -W error / warning scope
def test_webrtcvad_import_clean_under_w_error():
    """webrtcvad's pkg_resources-deprecation warning must be scoped away.

    The whole harness (and the fallback VAD gate) must import and build a
    webrtc gate under ``-W error`` without tripping over the known third-party
    deprecation warning emitted by webrtcvad 2.0.10.
    """
    code = (
        "from voicebridge.audio.vad import _make_vad_gate\n"
        "gate = _make_vad_gate('webrtc', 2)\n"
        "print('webrtc-gate-ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-W", "error", "-c", code],
        capture_output=True, text=True, timeout=60,
        cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "webrtc-gate-ok" in proc.stdout
    assert "pkg_resources is deprecated" not in proc.stderr


def test_webrtcvad_warning_filter_is_narrow():
    """The scoped filter must NOT suppress any unrelated warning.

    After the webrtcvad gate has been imported (filter applied), an unrelated
    UserWarning raised in the same interpreter must still become an error
    under ``-W error`` — proving the filter is pinned to the known third-party
    warning and never suppresses warnings globally.
    """
    code = (
        "import warnings\n"
        "from voicebridge.audio.vad import _make_vad_gate\n"
        "_make_vad_gate('webrtc', 2)\n"
        "warnings.warn('unrelated-qualify-warning', UserWarning)\n"
        "print('unrelated-warning-suppressed')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-W", "error", "-c", code],
        capture_output=True, text=True, timeout=60,
        cwd=str(REPO_ROOT))
    assert proc.returncode != 0, proc.stdout[-1000:]
    assert "unrelated-qualify-warning" in proc.stderr
    assert "unrelated-warning-suppressed" not in proc.stdout


def test_filter_does_not_touch_runtime_warnings_globally():
    """Confirm the harness does not alter warnings for other modules.

    Emitting a warning from a non-webrtcvad module after the harness has
    imported must still surface (never globally suppressed).
    """
    code = (
        "import warnings\n"
        "from voicebridge.audio.qualify import signal_qualification\n"
        "signal_qualification()\n"
        "warnings.warn('qualify-runtime-warning', RuntimeWarning)\n"
        "print('runtime-warning-suppressed')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-W", "error", "-c", code],
        capture_output=True, text=True, timeout=60,
        cwd=str(REPO_ROOT))
    assert proc.returncode != 0, proc.stdout[-1000:]
    assert "qualify-runtime-warning" in proc.stderr
    assert "runtime-warning-suppressed" not in proc.stdout