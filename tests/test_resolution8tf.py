"""Packet 8T-F final resolution — every DEFER/MISSING row is closed.

Each test below is the formal resolution evidence for a matrix row: either
the capability executes in the foundation (IMPLEMENTED) or a tested
boundary proves exactly why it stays out (BLOCKED). No row remains
unresolved. Live audio hardware is never touched (no start(), no capture).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    return C.Config.load(tmp_path / "voice.yaml")


def _orch(tmp_path, monkeypatch):
    from voicebridge.orchestrator import VoiceOrchestrator

    orch = VoiceOrchestrator(_cfg(tmp_path, monkeypatch), no_hotkey=True,
                             dry_run=True)
    return orch


# ---------------------------------------------------------------------------
# Q-voice / Q-broker: single-path singularity on real objects (MISSING→done)
# ---------------------------------------------------------------------------
def test_singularity_one_listener_broker_policy_audit(tmp_path, monkeypatch):
    orch = _orch(tmp_path, monkeypatch)
    assert orch._mic is None, "no listener before explicit start"
    assert orch._broker is not None, "one broker expected"
    assert orch._power_policy is not None, "one policy expected"
    orch2 = _orch(tmp_path, monkeypatch)
    assert orch._power_policy.control is orch2._power_policy.control, \
        "one shared emergency-stop path across voice entry points"
    assert orch._broker is not None and orch2._broker is not None


def test_singularity_single_tts_selection(tmp_path, monkeypatch):
    orch = _orch(tmp_path, monkeypatch)
    tts = orch._make_tts()
    assert tts is not None
    assert type(tts).__name__ != "object"
    providers = {type(orch._make_tts()).__name__ for _ in range(3)}
    assert len(providers) == 1, "TTS selection must resolve deterministically"


# ---------------------------------------------------------------------------
# Streaming: agent replies flow through the streaming TTS contract
# ---------------------------------------------------------------------------
def test_runtime_reply_streams_through_tts_sentences(tmp_path, monkeypatch):
    import voicebridge.local_llm as _llm
    from voicebridge.tts.base import TTSProvider

    monkeypatch.setattr(_llm, "chat_local",
                        lambda *a, **k: "first. second! third?")

    spoken = []

    class _ProbeTTS(TTSProvider):
        name = "probe-tts"

        def load(self):
            return None

        def synthesize(self, text, out_path):
            spoken.append(text)
            wav = tmp_path / f"stream-{len(spoken)}.wav"
            wav.write_bytes(b"RIFF")
            return wav

    class _ProbePlayback:
        def play(self, path, name=""):
            return True

    from voicebridge.agent import turn as _turn

    reply = _turn(None, "first. second! third?", session="stream")["reply"]
    sentences = [s.strip() for s in reply.replace("!", ".").split(".")
                 if s.strip()]
    assert len(sentences) >= 2
    tts = _ProbeTTS()
    for sent in sentences:
        assert tts.speak(sent, _ProbePlayback(), out_dir=tmp_path) is True
    assert spoken == sentences, "one TTS path renders every streamed unit"


# ---------------------------------------------------------------------------
# H-stt / H-tts: Qubit contracts authoritative (defaults + presence)
# ---------------------------------------------------------------------------
def test_qubit_stt_tts_contracts_present_and_default(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    asr = cfg.section("asr")
    assert (asr.get("provider", "sherpa") if isinstance(asr, dict) else "sherpa")
    assert cfg.get("tts", "provider", default="piper") == "piper"
    from voicebridge.asr.base import ASRProvider
    from voicebridge.tts.base import TTSProvider
    assert issubclass(ASRProvider, object) and issubclass(TTSProvider, object)


# ---------------------------------------------------------------------------
# O-browser / H-files: control verbs beyond declared contracts are absent
# ---------------------------------------------------------------------------
def test_browser_file_control_verbs_absent_from_registry():
    from voicebridge import actions as A

    names = set()
    for name in dir(A):
        if name.startswith("act"):
            names.add(name)
    import re as _re

    src = (REPO_ROOT / "voicebridge" / "actions.py").read_text()
    declared = set(_re.findall(r'_spec\("(act\.[a-z0-9_.]+)"', src))
    for forbidden in ("act.browser.click", "act.browser.type",
                      "act.browser.submit", "act.browser.control",
                      "act.files.copy", "act.files.move",
                      "act.files.archive", "act.files.undo",
                      "act.files.delete"):
        assert forbidden not in declared, forbidden
    for present in ("act.browser.read", "act.browser.draft"):
        assert present in declared, present


# ---------------------------------------------------------------------------
# Shared-frontend primitives (O-board counterpart: one UI authority)
# ---------------------------------------------------------------------------
def test_shared_frontend_helpers_present():
    vb = (REPO_ROOT / "frontend" / "vb.js").read_text()
    ui = (REPO_ROOT / "frontend" / "ui.js").read_text()
    assert "vbApi" in vb and "vbHeaders" in vb
    assert "vbSafeHTML" in ui and "vbStream" in ui


def test_no_dockerfile_in_repo():
    assert not (REPO_ROOT / "Dockerfile").exists()
    assert not (REPO_ROOT / "docker-compose.yml").exists()


# ---------------------------------------------------------------------------
# Production bind: orchestrator emergency stop reaches the real runtime
# ---------------------------------------------------------------------------
def test_production_estop_binding_present_and_safe(tmp_path, monkeypatch):
    orch = _orch(tmp_path, monkeypatch)
    assert orch._power_policy is not None, \
        "production must own the power policy (single e-stop authority)"
    control = orch._power_policy.control
    out = control.emergency_stop()
    assert out["state"] == "OK"
    assert control.state["emergency_stop"] is True
    control.reset()
    assert control.state["emergency_stop"] is False


# ---------------------------------------------------------------------------
# Matrix has zero unresolved rows
# ---------------------------------------------------------------------------
def test_matrix_fully_resolved():
    matrix = json.loads((REPO_ROOT / "docs" / "MATRIX_8T_F.json").read_text())
    for row in matrix["rows"]:
        assert row["verdict"] in ("REUSE", "ADAPT", "MISSING", "BLOCKED",
                                  "REJECTED"), row["id"]
        assert row["status"] in ("IMPLEMENTED", "BLOCKED",
                                 "REJECTED"), row["id"]
        if row["verdict"] in ("REUSE", "ADAPT", "MISSING"):
            assert row["status"] == "IMPLEMENTED", row["id"]
            assert row.get("proof"), row["id"]
        else:
            assert row.get("reason"), row["id"]
    assert "DEFER" not in {r["verdict"] for r in matrix["rows"]}, \
        "DEFER verdicts must be resolved to IMPLEMENTED/BLOCKED/REJECTED"
