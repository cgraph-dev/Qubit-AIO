"""CLI waveform flips go through the running bridge first (toggle-sync fix).

Owner gap: ``qubit voice waveform hide`` wrote the FILE but the running
bridge's in-memory cfg stayed stale (GET read True until a dashboard PUT
synced). The CLI now PUTs through the SAME authed localhost path the
dashboard switch uses (``PUT /agent/api/voice-edit/settings`` with the
local-client base/token convention — no new auth invented), so file,
bridge memory, and GET agree after ANY writer (voice, CLI, dashboard).
When no bridge listens it falls back to the direct file write; when the
bridge ANSWERS non-2xx it refuses loudly (no file write — that would
desync file vs bridge memory).

Hermetic: every test injects a fake opener (no loopback, no live bridge).
The live three-writer agreement is proven in the deploy step, not here.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


class _A:
    def __init__(self, **kw):
        self.voice_command = kw.pop("cmd", "waveform")
        self.waveform_action = kw.pop("action", "status")
        for k, v in kw.items():
            setattr(self, k, v)


class _Resp:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run(cfg, **kw):
    from voicebridge import cli as CLI

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_voice(cfg, _A(**kw))
    return rc, buf.getvalue()


# -- wire shape ---------------------------------------------------------------

def test_put_uses_dashboard_path_method_payload_and_auth(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    seen = {}

    def _opener(req, timeout=5.0):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["payload"] = json.loads(req.data.decode())
        return _Resp(200)

    cfg = _cfg(tmp_path, monkeypatch)
    assert CLI._put_waveform_via_service(cfg, False, opener=_opener) == "ok"
    assert seen["url"].endswith("/agent/api/voice-edit/settings"), seen["url"]
    assert seen["method"] == "PUT"
    assert seen["payload"] == {"waveform_visible": False}
    assert seen["headers"].get("content-type") == "application/json"


def test_put_returns_ok_value_for_show(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    def _opener(req, timeout=5.0):
        assert json.loads(req.data.decode()) == {"waveform_visible": True}
        return _Resp(200)

    cfg = _cfg(tmp_path, monkeypatch)
    assert CLI._put_waveform_via_service(cfg, True, opener=_opener) == "ok"


def test_put_unreachable_never_raises(tmp_path, monkeypatch):
    import urllib.error

    from voicebridge import cli as CLI

    def _dead(req, timeout=5.0):
        raise urllib.error.URLError("connection refused")

    cfg = _cfg(tmp_path, monkeypatch)
    assert CLI._put_waveform_via_service(cfg, True, opener=_dead) == "unreachable"


def test_put_refused_raises_typed_never_unreachable(tmp_path, monkeypatch):
    import urllib.error

    from voicebridge import cli as CLI

    def _deny(req, timeout=5.0):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    cfg = _cfg(tmp_path, monkeypatch)
    try:
        CLI._put_waveform_via_service(cfg, True, opener=_deny)
    except CLI._WaveformServiceRefused as exc:
        assert exc.code == 403
    else:
        raise AssertionError("a 403 answer must refuse, not fall back")


# -- CLI behavior --------------------------------------------------------------

def test_cli_hide_via_bridge_skips_file_write(tmp_path, monkeypatch):
    """PUT ok → the SERVER owns the write; the CLI must not double-write."""
    from voicebridge import cli as CLI
    from voicebridge.voice_edit import service as svc_mod

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CLI, "_put_waveform_via_service",
                        lambda cfg, visible, opener=None: "ok")
    wrote: list = []
    monkeypatch.setattr(svc_mod.VoiceEditService, "set_waveform_visible",
                        lambda self, on: wrote.append(on) or on)
    rc, out = _run(cfg, action="hide")
    assert rc == 0
    assert "off" in out.lower()
    assert wrote == [], "bridge PUT owns the write; CLI must not file-write"


def test_cli_hide_offline_falls_back_to_file(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge.voice_edit import settings as settings_mod

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CLI, "_put_waveform_via_service",
                        lambda cfg, visible, opener=None: "unreachable")
    rc, out = _run(cfg, action="hide")
    assert rc == 0
    assert settings_mod.settings_view(cfg)["waveform_visible"] is False


def test_cli_hide_refused_leaves_file_unchanged(tmp_path, monkeypatch):
    """Bridge answered non-2xx → loud refusal, NO file write (no split-brain)."""
    from voicebridge import cli as CLI
    from voicebridge.voice_edit import settings as settings_mod

    cfg = _cfg(tmp_path, monkeypatch)
    settings_mod.update_voice_edit(cfg, {"waveform_visible": True})

    def _deny(cfg, visible, opener=None):
        raise CLI._WaveformServiceRefused(403, "forbidden")

    monkeypatch.setattr(CLI, "_put_waveform_via_service", _deny)
    rc, out = _run(cfg, action="hide")
    assert rc == 1
    assert "refused" in out.lower()
    assert settings_mod.settings_view(cfg)["waveform_visible"] is True


def test_cli_hide_via_bridge_audits_source_and_path(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge.voice_edit import service as svc_mod

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(CLI, "_put_waveform_via_service",
                        lambda cfg, visible, opener=None: "ok")
    seen: list = []
    monkeypatch.setattr(svc_mod.VoiceEditService, "_audit",
                        lambda self, op, detail: seen.append((op, detail)))
    rc, _out = _run(cfg, action="hide")
    assert rc == 0
    assert any(op == "voice_edit.waveform.set"
               and d.get("source") == "cli"
               and d.get("visible") is False
               and d.get("via") == "ok" for op, d in seen)
