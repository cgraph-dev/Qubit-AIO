"""In-game orb override via voice (owner contract 2026-10-04).

``voice_edit.waveform_ingame`` (default False = today's auto-hide) is a
SEPARATE flag from ``voice_edit.waveform_visible`` (dashboard widget):

- bare "show/hide waveform" keeps its CURRENT meaning (dashboard widget,
  ``waveform_visible``) — never touches the override;
- "show/hide waveform ingame" (hey-prefix handled upstream) flips ONLY the
  override, with the unambiguous confirms "Game overlay on/off." (never
  "Waveform on/off.");
- "show waveform in game settings" must NOT fire the override.

No services, no browser, no model, no live config writes (tmp_path only).
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import settings as settings_mod
from voicebridge.voice_edit.service import VoiceEditService
from voicebridge.voice_edit.wake import strip_wake


class FakeCfg:
    """Scratch config: .get contract + .data (live sync) + .path (file)."""

    def __init__(self, data=None, path=None):
        self._data = data or {}
        self.data = self._data
        self.path = path

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


class FakeDesktop:
    def session(self):
        return {"session_type": "x11", "display": ":0", "x11": True}

    def _tool(self, name):
        return False


def _svc(cfg, audit_log=None):
    return VoiceEditService(
        cfg,
        desktop=FakeDesktop(),
        brain=None,
        audit=(lambda op, sec, detail: audit_log.append((op, detail))
               if audit_log is not None else lambda *a: None),
        estop=lambda cfg: False,
    )


def _cfg_with_file(tmp_path, data=None):
    path = tmp_path / "voice.yaml"
    import yaml

    path.write_text(yaml.safe_dump(data or {}), encoding="utf-8")
    return FakeCfg(data={}, path=path)


def _route_cfg(tmp_path):
    path = tmp_path / "voice.yaml"
    import yaml

    path.write_text(yaml.safe_dump({}), encoding="utf-8")
    live = {"voice_edit": {"enabled": True}}
    return FakeCfg(data=live, path=path)


# -- settings key -----------------------------------------------------------

def test_waveform_ingame_defaults_false_when_unset(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is False


def test_waveform_ingame_put_roundtrip(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    settings_mod.update_voice_edit(cfg, {"waveform_ingame": False})
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is False
    settings_mod.update_voice_edit(cfg, {"waveform_ingame": True})
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is True


def test_waveform_ingame_coerced_like_sibling_bools(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    settings_mod.update_voice_edit(cfg, {"waveform_ingame": 0})
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is False
    settings_mod.update_voice_edit(cfg, {"waveform_ingame": 1})
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is True


def test_ingame_and_visible_are_independent_flags(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    settings_mod.update_voice_edit(cfg, {"waveform_visible": True})
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is False
    settings_mod.update_voice_edit(cfg, {"waveform_ingame": True})
    assert settings_mod.settings_view(cfg)["waveform_visible"] is True
    settings_mod.update_voice_edit(cfg, {"waveform_visible": False})
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is True


# -- voice intents ----------------------------------------------------------

def test_show_waveform_ingame_flips_only_ingame(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    audit_log: list = []
    svc = _svc(cfg, audit_log)
    settings_mod.update_voice_edit(cfg, {"waveform_visible": False})
    out = svc.ask("show waveform ingame")
    assert out.get("ok") is True
    assert out.get("mode") == "waveform-ingame"
    assert out.get("model") == "waveform-ingame"
    assert str(out.get("answer") or "") == "Game overlay on."
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is True
    # The dashboard widget flag is untouched.
    assert settings_mod.settings_view(cfg)["waveform_visible"] is False
    assert any(op == "voice_edit.waveform_ingame.set"
               and d.get("ingame") is True for op, d in audit_log)
    # Distinct audit: never the bare-widget op.
    assert not any(op == "voice_edit.waveform.set" for op, _ in audit_log)


def test_hide_waveform_ingame_flips_only_ingame(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    audit_log: list = []
    svc = _svc(cfg, audit_log)
    settings_mod.update_voice_edit(
        cfg, {"waveform_ingame": True, "waveform_visible": True})
    out = svc.ask("hide waveform ingame")
    assert out.get("ok") is True
    assert str(out.get("answer") or "") == "Game overlay off."
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is False
    assert settings_mod.settings_view(cfg)["waveform_visible"] is True
    assert any(op == "voice_edit.waveform_ingame.set"
               and d.get("ingame") is False for op, d in audit_log)


def test_ingame_intent_with_wake_prefix_and_the(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    out = svc.ask("hey qubit, hide the waveform ingame")
    assert out.get("mode") == "waveform-ingame"
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is False


def test_bare_show_hide_never_touch_ingame(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    out = svc.ask("show waveform")
    assert out.get("mode") == "waveform"
    assert "waveform on" in str(out.get("answer") or "").lower()
    assert settings_mod.settings_view(cfg)["waveform_visible"] is True
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is False
    settings_mod.update_voice_edit(cfg, {"waveform_ingame": True})
    out = svc.ask("hide waveform")
    assert out.get("mode") == "waveform"
    assert settings_mod.settings_view(cfg)["waveform_visible"] is False
    # Bare hide clears the widget, never the game-overlay override.
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is True


def test_ingame_near_miss_does_not_fire_override(tmp_path):
    cfg = _cfg_with_file(tmp_path)
    svc = _svc(cfg)
    # Boundary pins: bare widget phrases carry no ingame qualifier ...
    assert svc.waveform_ingame_command("show waveform") is None
    assert svc.waveform_ingame_command("hide the waveform") is None
    assert svc.waveform_ingame_command("show me the waveform settings") is None
    # ... and a settings question that merely mentions games is not a command.
    assert svc.waveform_ingame_command("show waveform in game settings") is None
    out = svc.ask("show waveform in game settings")
    assert out.get("mode") != "waveform-ingame", out
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is False


# -- route stage ------------------------------------------------------------

def _route(cfg, svc, text):
    rid = svc.begin(request_id="r-ingame")["request_id"]
    return svc.route(rid, text)


def test_route_ingame_steers_to_answer_not_task(tmp_path):
    cfg = _route_cfg(tmp_path)
    svc = _svc(cfg)
    out = _route(cfg, svc, "show waveform ingame")
    assert out.get("mode") == "answer", out
    out = _route(cfg, svc, "hide waveform ingame")
    assert out.get("mode") == "answer", out


def test_full_wake_path_ingame_flips(tmp_path):
    cfg = _route_cfg(tmp_path)
    audit_log: list = []
    svc = _svc(cfg, audit_log)
    settings_mod.update_voice_edit(cfg, {"waveform_ingame": False})
    remainder = strip_wake("hey qubit, show waveform ingame")
    assert remainder == "show waveform ingame", remainder
    routed = _route(cfg, svc, remainder)
    assert routed.get("mode") == "answer", routed
    out = svc.ask(routed["question"])
    assert out.get("ok") is True
    assert out.get("mode") == "waveform-ingame"
    assert str(out.get("answer") or "") == "Game overlay on."
    assert settings_mod.settings_view(cfg)["waveform_ingame"] is True


# -- CLI --------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_live_bridge(monkeypatch):
    from voicebridge import cli as CLI

    monkeypatch.setattr(
        CLI, "_put_voice_edit_flag_via_service",
        lambda cfg, key, visible, opener=None: "unreachable")


def _cli_cfg(tmp_path, monkeypatch):
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


def test_cli_status_reports_both_flags(tmp_path, monkeypatch):
    cfg = _cli_cfg(tmp_path, monkeypatch)
    rc, out = _run(cfg, action="status")
    assert rc == 0
    low = out.lower()
    assert "waveform" in low and "off" in low
    assert "game overlay" in low and "off" in low


def test_cli_ingame_on_off_roundtrip(tmp_path, monkeypatch):
    from voicebridge.voice_edit import settings as sm

    cfg = _cli_cfg(tmp_path, monkeypatch)
    rc, out = _run(cfg, action="ingame-off")
    assert rc == 0
    assert "game overlay off" in out.lower()
    assert sm.settings_view(cfg)["waveform_ingame"] is False
    # The widget flag is untouched by the overlay verbs.
    assert sm.settings_view(cfg)["waveform_visible"] is False
    rc, out = _run(cfg, action="ingame-on")
    assert rc == 0
    assert "game overlay on" in out.lower()
    assert sm.settings_view(cfg)["waveform_ingame"] is True


def test_cli_ingame_emits_distinct_audit(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge.voice_edit import service as svc_mod

    cfg = _cli_cfg(tmp_path, monkeypatch)
    seen: list = []
    monkeypatch.setattr(svc_mod.VoiceEditService, "_audit",
                        lambda self, op, detail: seen.append((op, detail)))
    rc, _out = _run(cfg, action="ingame-on")
    assert rc == 0
    assert any(op == "voice_edit.waveform_ingame.set"
               and d.get("ingame") is True for op, d in seen)
