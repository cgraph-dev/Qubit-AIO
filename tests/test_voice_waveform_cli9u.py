"""Terminal toggle for the dashboard waveform widget (micro-lane).

``qubit voice waveform show|hide|status`` flips
``voice_edit.waveform_visible`` through the canonical writer
(``VoiceEditService.set_waveform_visible`` -> ``update_voice_edit``,
audited ``voice_edit.waveform.set`` like the "show/hide waveform" voice
intent) and reports the current value. No services, no browser, no model,
no live config writes (tmp_path only).
"""
from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest


@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    """Hermetic: the file-writer tests must never PUT to a live bridge.

    ``_cmd_voice_waveform`` tries the running bridge's authed localhost PUT
    first; on this host a bridge IS listening, and the tmp config would
    resolve its address + the real token file. Force the offline fallback
    so these tests exercise the file writer only (the PUT path is pinned
    in ``test_voice_waveform_cli_sync9u.py`` with a fake opener).
    """
    from voicebridge import cli as CLI

    monkeypatch.setattr(CLI, "_put_waveform_via_service",
                        lambda cfg, visible, opener=None: "unreachable")


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


def _run(cfg, **kw):
    from voicebridge import cli as CLI

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_voice(cfg, _A(**kw))
    return rc, buf.getvalue()


def test_waveform_status_reports_default_off(tmp_path, monkeypatch):
    # Owner 2026-10-04: fresh/absent key means HIDDEN (OS orb is the only
    # always-on waveform). Deliberate reversal of the old default-on pin.
    cfg = _cfg(tmp_path, monkeypatch)
    rc, out = _run(cfg, action="status")
    assert rc == 0
    assert "off" in out.lower()


def test_waveform_hide_then_show_roundtrip(tmp_path, monkeypatch):
    from voicebridge.voice_edit import settings as settings_mod

    cfg = _cfg(tmp_path, monkeypatch)
    rc, out = _run(cfg, action="hide")
    assert rc == 0
    assert "off" in out.lower()
    assert settings_mod.settings_view(cfg)["waveform_visible"] is False
    rc, out = _run(cfg, action="status")
    assert rc == 0
    assert "off" in out.lower()
    rc, out = _run(cfg, action="show")
    assert rc == 0
    assert "on" in out.lower()
    assert settings_mod.settings_view(cfg)["waveform_visible"] is True


def test_waveform_no_arg_reports_current(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge.voice_edit import settings as settings_mod

    cfg = _cfg(tmp_path, monkeypatch)
    settings_mod.update_voice_edit(cfg, {"waveform_visible": False})

    class _NoAction:
        voice_command = "waveform"

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_voice(cfg, _NoAction())
    assert rc == 0
    assert "off" in buf.getvalue().lower()


def test_waveform_unknown_action_errors(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rc, _out = _run(cfg, action="blink")
    assert rc == 2


def test_waveform_cli_emits_voice_like_audit(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge.voice_edit import service as svc_mod

    cfg = _cfg(tmp_path, monkeypatch)
    seen: list = []
    monkeypatch.setattr(svc_mod.VoiceEditService, "_audit",
                        lambda self, op, detail: seen.append((op, detail)))
    rc, _out = _run(cfg, action="hide")
    assert rc == 0
    assert any(op == "voice_edit.waveform.set"
               and d.get("visible") is False for op, d in seen), \
        "CLI flip must audit the same op as the voice intent"


def test_waveform_help_mentions_voice_phrases_and_dashboard(tmp_path, monkeypatch):
    import pytest

    from voicebridge import cli as CLI

    top_help = CLI.build_parser().format_help()
    assert "voice" in top_help

    def _help_for(argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with pytest.raises(SystemExit) as exc:
                CLI.build_parser().parse_args(argv + ["--help"])
        assert exc.value.code == 0
        return buf.getvalue()

    # Family help carries the voice phrases + dashboard switch pointer
    # (argparse wraps lines, so compare whitespace-normalized).
    voice_help = " ".join(_help_for(["voice"]).split())
    assert "show waveform" in voice_help
    assert "hide waveform" in voice_help
    assert "dashboard" in voice_help.lower()
    # Nested help lists the three actions.
    nested_help = _help_for(["voice", "waveform"])
    for verb in ("show", "hide", "status"):
        assert verb in nested_help
