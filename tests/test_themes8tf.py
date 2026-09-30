"""Qubit themes and cosmetics.

Proves presets switch with confirmation/audit/rollback, token tweaks are
validated, gallery downloads stay refused, the dashboard serves the
override layer, and every page links it.
"""
from __future__ import annotations

import io
import json
import time
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_list_and_show_default(tmp_path, monkeypatch):
    from voicebridge import themes as TH

    cfg = _cfg(tmp_path, monkeypatch)
    out = TH.list_themes()
    assert {t["name"] for t in out["themes"]} == \
        {"qubit-dark", "qubit-light", "qubit-contrast"}
    out = TH.show(cfg)
    assert out["theme"] == "qubit-dark" and out["tweaks"] == {}
    assert out["css_href"] == "/static/theme.css"


def test_set_reset_roundtrip_with_receipt(tmp_path, monkeypatch):
    from voicebridge import themes as TH

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(TH.ThemeError) as exc:
        TH.set_theme(cfg, "qubit-light", confirmed=False)
    assert exc.value.code == "THEME_CONFIRM_REQUIRED"
    out = TH.set_theme(cfg, "qubit-light", confirmed=True)
    assert out["theme"] == "qubit-light" and out["previous"] == "qubit-dark"
    assert TH.show(cfg)["theme"] == "qubit-light"
    out = TH.reset(cfg, confirmed=True)
    assert out["theme"] == "qubit-dark" and out["tweaks"] == {}
    with pytest.raises(TH.ThemeError) as exc:
        TH.set_theme(cfg, "neon", confirmed=True)
    assert exc.value.code == "THEME_UNKNOWN"


def test_tweak_validation_and_clear(tmp_path, monkeypatch):
    from voicebridge import themes as TH

    cfg = _cfg(tmp_path, monkeypatch)
    out = TH.set_tweak(cfg, "--primary", "#123abc", confirmed=True)
    assert out["tweaks"] == {"--primary": "#123abc"}
    out = TH.set_tweak(cfg, "primary", "#fff", confirmed=True)
    assert out["tweaks"]["--primary"] == "#fff", "bare names gain --"
    with pytest.raises(TH.ThemeError) as exc:
        TH.set_tweak(cfg, "--primary", "red", confirmed=True)
    assert exc.value.code == "THEME_BAD_COLOR"
    with pytest.raises(TH.ThemeError) as exc:
        TH.set_tweak(cfg, "--font-family", "#fff", confirmed=True)
    assert exc.value.code == "THEME_UNKNOWN"
    out = TH.clear_tweak(cfg, "--primary", confirmed=True)
    assert out["tweaks"] == {}
    assert TH.clear_tweak(cfg, "--primary", confirmed=True)["tweaks"] == {}, \
        "clear is idempotent"


def test_corrupt_state_falls_back_to_default(tmp_path, monkeypatch):
    from voicebridge import themes as TH

    cfg = _cfg(tmp_path, monkeypatch)
    path = Path(cfg.state_dir()) / "ui-theme.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{corrupt")
    assert TH.show(cfg)["theme"] == "qubit-dark"
    assert "app.css as shipped" in TH.css(cfg)


def test_css_renders_preset_and_tweaks(tmp_path, monkeypatch):
    from voicebridge import themes as TH

    cfg = _cfg(tmp_path, monkeypatch)
    assert "app.css as shipped" in TH.css(cfg)
    TH.set_theme(cfg, "qubit-light", confirmed=True)
    TH.set_tweak(cfg, "--primary", "#123456", confirmed=True)
    css = TH.css(cfg)
    assert ":root" in css and "--bg: #f5f5ef" in css
    assert "--primary: #123456" in css, "tweaks win over presets"


def test_pet_downloads_refused():
    from voicebridge import themes as TH

    out = TH.refuse_pet_downloads()
    assert out["code"] == "PETS_NO_DOWNLOADS"
    assert out["owner"] == "qubit-policy"


def test_estop_gate_and_audit(tmp_path, monkeypatch):
    from voicebridge import themes as TH
    from voicebridge.logging_setup import setup
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    setup(level="INFO", jsonl=True, state_dir=cfg.state_dir())
    try:
        TH.set_theme(cfg, "qubit-contrast", confirmed=True)
        blob = (Path(cfg.state_dir()) / "events.jsonl").read_text()
        assert "theme.set" in blob
        power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                     issued_by="cli:user")
        try:
            assert TH.estop_engaged(cfg) is True
        finally:
            power_control_store(cfg).set(emergency_stop=False,
                                         global_disable=False,
                                         issued_by="cli:user")
    finally:
        from voicebridge import logging_setup as LS

        LS._EVENT_FILE = None


def test_cli_wiring(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    cfg = _cfg(tmp_path, monkeypatch)

    class _A:
        def __init__(self, cmd, **kw):
            self.theme_command = cmd
            self.name = kw.get("name", "")
            self.token = kw.get("token", "")
            self.color = kw.get("color", "")
            self.confirm = kw.get("confirm", False)
            self.dry_run = kw.get("dry_run", False)

    def run(**kw):
        cmd = kw.pop("cmd", "show")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = CLI._cmd_theme(cfg, _A(cmd, **kw))
        return rc, json.loads(buf.getvalue())

    assert run(cmd="list")[0] == 0
    assert run(cmd="show")[1]["theme"] == "qubit-dark"
    assert run(cmd="set", name="qubit-light")[0] == 3, "confirm required"
    assert run(cmd="set", name="qubit-light", confirm=True)[1]["theme"] \
        == "qubit-light"
    assert run(cmd="set-tweak", token="--text", color="#111111",
               confirm=True)[0] == 0
    assert run(cmd="reset", confirm=True)[1]["theme"] == "qubit-dark"
    assert run(cmd="set", name="nope", confirm=True)[0] == 2
    assert "theme" in CLI.build_parser().format_help()


def test_pages_link_theme_stylesheet():
    repo = Path(__file__).resolve().parents[1] / "frontend"
    for page in ("activity.html", "approvals.html", "index.html",
                 "reports.html", "settings.html", "setup.html",
                 "token-setup.html"):
        text = (repo / page).read_text(encoding="utf-8")
        assert "/static/theme.css" in text, f"{page}: missing theme layer"
        assert text.index("/static/app.css") < text.index("/static/theme.css"), \
            f"{page}: theme layer must load after app.css"


def test_dashboard_serves_theme_css(tmp_path, monkeypatch):
    import time as _time

    from voicebridge import themes as TH
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir()
    cfg = SimpleNamespace(state_dir=lambda: state)
    TH.set_theme(cfg, "qubit-light", confirmed=True)

    fake = SimpleNamespace(
        task=SimpleNamespace(active=False), _route="gateway", _t0=_time.time(),
        _tts=None, _last_web_summary="", _last_web_route="", _web_mode=False,
        _web_spoken=[],
        cfg=SimpleNamespace(
            get=lambda *p, **kw: kw.get("default"),
            section=lambda n: {}, model_dir=lambda: tmp_path,
            state_dir=lambda: state))

    ui = WebUI(fake, host="127.0.0.1", port=0, state_dir=tmp_path)
    ui.start()
    try:
        port = ui._server.server_address[1]
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/static/theme.css",
                timeout=5) as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type") == "text/css"
            body = resp.read().decode("utf-8")
        assert "--bg: #f5f5ef" in body, "served CSS reflects the active preset"
    finally:
        ui.stop()


def test_capability_catalog_reports_pets_skin_implemented():
    from voicebridge.capability_catalog import check

    for token in ("pets", "skin"):
        out = check(token)
        assert out["state"] == "IMPLEMENTED", (token, out)
