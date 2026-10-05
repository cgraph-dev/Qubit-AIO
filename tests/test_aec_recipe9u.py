"""Slice-5 AEC recipe module tests (hermetic: pactl is stubbed)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.audio import aec


class FakeCfg:
    def __init__(self, data):
        self._d = data

    def get(self, *p, default=None):
        node = self._d
        for seg in p:
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                return default
        return node


def test_source_names_match_noise_module():
    from voicebridge.voice_edit import noise
    assert aec.SOURCE_NAME == noise.SOURCE_NAME
    assert aec.SINK_NAME == noise.SINK_NAME


def test_status_reports_absent_when_no_pactl(monkeypatch):
    monkeypatch.setattr(aec, "_run", lambda *a, **k: None)
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    st = aec.status()
    assert st["loaded"] is False
    assert st["source_present"] is False
    assert st["pactl_available"] is False


def test_status_reports_present(monkeypatch):
    def fake_run(args, timeout=8.0):
        if args[:3] == ["list", "short", "sources"]:
            return "54\tqubit_aec\tPipeWire\tfloat32le 2ch 48000Hz\tSUSPENDED"
        if args[:3] == ["list", "short", "sinks"]:
            return "55\tqubit_aec_out\tPipeWire\tfloat32le 2ch 48000Hz\tSUSPENDED"
        if args == ["info"]:
            return "Default Source: qubit_aec\nDefault Sink: foo"
        if args[:2] == ["list", "short"]:
            return ""
        return ""
    monkeypatch.setattr(aec, "_run", fake_run)
    st = aec.status()
    assert st["loaded"] is True
    assert st["source_present"] is True
    assert st["sink_present"] is True
    assert st["is_default_source"] is True
    assert "48000" in (st["source_spec"] or "")


def test_load_is_idempotent_when_source_exists(monkeypatch):
    calls = []
    monkeypatch.setattr(aec, "_source_exists", lambda name: True)
    monkeypatch.setattr(aec, "_run",
                        lambda *a, **k: calls.append(a) or "12345")
    assert aec.load() == aec.SOURCE_NAME
    assert calls == []


def test_load_issues_explicit_masters(monkeypatch):
    seen = []
    monkeypatch.setattr(aec, "_source_exists", lambda name: False)
    def fake_run(args, timeout=8.0):
        seen.append(args)
        return "536870914"
    monkeypatch.setattr(aec, "_run", fake_run)
    out = aec.load(source_master="mic_master", sink_master="sink_master")
    assert out == "536870914"
    assert any("source_master=mic_master" in a for a in seen[0])
    assert any("sink_master=sink_master" in a for a in seen[0])
    assert any("aec_method=webrtc" in a for a in seen[0])


def test_load_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(aec, "_source_exists", lambda name: False)
    monkeypatch.setattr(aec, "_run", lambda *a, **k: None)
    assert aec.load() == ""


def test_is_aec_enabled_reads_capture_source_flag():
    assert aec.is_aec_enabled(
        FakeCfg({"voice_edit": {"capture_source": "aec"}})) is True
    assert aec.is_aec_enabled(
        FakeCfg({"voice_edit": {"capture_source": "auto"}})) is False
    assert aec.is_aec_enabled(FakeCfg({"voice_edit": {}})) is False


def test_recipe_text_names_masters_and_module():
    text = aec.recipe_text()
    assert "module-echo-cancel" in text
    assert "aec_method=webrtc" in text
    assert aec.SOURCE_NAME in text
    assert aec.DEFAULT_SOURCE_MASTER in text
    assert aec.DEFAULT_SINK_MASTER in text
