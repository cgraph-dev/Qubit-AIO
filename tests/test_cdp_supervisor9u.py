"""Slice-6 CDP supervisor tests (hermetic: processes + CDP stubbed)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge import cdp_supervisor as sup


def test_health_cdp_down_no_browser(monkeypatch):
    monkeypatch.setattr(sup, "chromium_processes", lambda: [])
    monkeypatch.setattr(sup, "cdp_tabs", lambda timeout=2.0: None)
    h = sup.health()
    assert h["cdp_up"] is False
    assert h["chromium_running"] is False
    assert h["verdict"] == "down_no_browser"


def test_health_browser_running_without_cdp(monkeypatch):
    monkeypatch.setattr(sup, "chromium_processes",
                        lambda: [{"pid": 7821, "cmd": "chrome"}])
    monkeypatch.setattr(sup, "cdp_tabs", lambda timeout=2.0: None)
    h = sup.health()
    assert h["chromium_running"] is True
    assert h["cdp_up"] is False
    assert h["verdict"] == "browser_without_cdp"


def test_health_cdp_up_with_tabs(monkeypatch):
    monkeypatch.setattr(sup, "chromium_processes",
                        lambda: [{"pid": 1, "cmd": "chrome"}])
    monkeypatch.setattr(
        sup, "cdp_tabs",
        lambda timeout=2.0: [{"url": "https://www.perplexity.ai/x",
                              "title": "p"},
                             {"url": "https://mail.google.com/mail/u/0/",
                              "title": "g"}])
    h = sup.health()
    assert h["cdp_up"] is True
    assert h["verdict"] == "healthy"
    assert h["pinned_missing"] == []


def test_health_cdp_up_missing_pin(monkeypatch):
    monkeypatch.setattr(sup, "chromium_processes",
                        lambda: [{"pid": 1, "cmd": "chrome"}])
    monkeypatch.setattr(
        sup, "cdp_tabs",
        lambda timeout=2.0: [{"url": "https://example.com", "title": "e"}])
    h = sup.health()
    assert h["cdp_up"] is True
    assert h["verdict"] == "tabs_missing"
    assert len(h["pinned_missing"]) == len(sup.PINNED_TABS)


def test_launch_refuses_when_browser_running(monkeypatch):
    monkeypatch.setattr(sup, "chromium_processes",
                        lambda: [{"pid": 7821, "cmd": "chrome"}])
    launched = []
    monkeypatch.setattr(sup, "_spawn",
                        lambda *a, **k: launched.append(a) or 1)
    res = sup.launch()
    assert res["ok"] is False
    assert res["code"] == "SUPERVISOR_REFUSES_RUNNING_INSTANCE"
    assert launched == []


def test_launch_when_no_browser(monkeypatch):
    monkeypatch.setattr(sup, "chromium_processes", lambda: [])
    monkeypatch.setattr(sup, "_spawn", lambda *a, **k: 9999)
    res = sup.launch()
    assert res["ok"] is True
    assert res["pid"] == 9999
    assert "--remote-debugging-port=9222" in res["argv"]


def test_never_kills_owner_browser():
    import inspect
    src = inspect.getsource(sup)
    assert "pkill" not in src
    assert "kill(" not in src
    assert "SIGKILL" not in src and "SIGTERM" not in src


def test_health_roundtrip_through_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sup, "chromium_processes", lambda: [])
    monkeypatch.setattr(sup, "cdp_tabs", lambda timeout=2.0: None)
    path = tmp_path / "cdp_health.json"
    written = sup.write_health(str(path))
    assert written["verdict"] == "down_no_browser"
    back = sup.read_health(str(path), max_age_s=60.0)
    assert back is not None and back["verdict"] == "down_no_browser"
    stale = sup.read_health(str(path), max_age_s=-1.0)
    assert stale is None


def test_capture_source_put_validation_rejects_garbage():
    from voicebridge.voice_edit import settings as vs

    class Cfg:
        path = "/tmp/qubit-capture-source-validation-probe.yaml"
        data = {"voice_edit": {}}

    import pytest
    from voicebridge.voice_edit.schema import VoiceEditError
    with pytest.raises(VoiceEditError):
        vs.update_voice_edit(Cfg(), {"capture_source": " Sonora "})
    bak = vs.update_voice_edit(Cfg(), {"capture_source": "AEC"})
    assert Cfg.data["voice_edit"]["capture_source"] == "aec"
