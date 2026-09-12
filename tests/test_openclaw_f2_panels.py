"""OC-71 — Control-UI panel registry (F2 production proofs).

Drives ``voicebridge.gateway.panels`` and its shared gateway RPC methods
(``ui.panels`` / ``ui.panel_layout``): all four panels are advertised with a
typed state, the terminal panel is honestly GATED (no shell starts), browser
and desktop report a live probe state, side chat is real, and a validated,
persisted, confirmed layout round-trips.
"""
from __future__ import annotations

import pytest

from voicebridge.gateway import panels as PAN


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_control_panels_all_typed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    inv = PAN.control_panels(cfg)
    assert inv["schema"] == PAN.SCHEMA
    assert inv["state"] == "OK"
    assert set(inv["panels"]) == set(PAN.PANELS)
    for name, panel in inv["panels"].items():
        assert isinstance(panel.get("state"), str) and panel["state"], name
        assert "available" in panel, name
    # Side chat is real; terminal stays an honest typed gate.
    assert inv["panels"]["side_chat"]["state"] == "OK"
    assert inv["panels"]["side_chat"]["available"] is True
    assert inv["panels"]["terminal"]["state"] == "GATED"
    assert inv["panels"]["terminal"]["code"] == "PTY_NO_SHELL"
    assert inv["panels"]["terminal"]["available"] is False
    # Browser/desktop report a real probe result (never a fabricated OK).
    for name in ("browser", "desktop"):
        panel = inv["panels"][name]
        assert panel["available"] is (panel["state"] == "OK")
        assert panel["detail"]["state"] == panel["state"]


def test_panel_layout_needs_confirm_and_validates(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    denied = PAN.arrange(cfg, ["side_chat"], confirmed=False)
    assert denied["state"] == "denied"
    assert denied["code"] == PAN.CONFIRM_CODE
    bad = PAN.arrange(cfg, ["nope"], confirmed=True)
    assert bad["state"] == "FAILED"
    assert bad["code"] == PAN.BAD_INPUT_CODE
    dup = PAN.arrange(cfg, ["side_chat", "side_chat"], confirmed=True)
    assert dup["code"] == PAN.BAD_INPUT_CODE
    empty = PAN.arrange(cfg, [], confirmed=True)
    assert empty["code"] == PAN.BAD_INPUT_CODE


def test_panel_layout_roundtrips_through_gateway_rpc(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge.gateway.rpc import default_registry

    reg = default_registry()
    assert "ui.panels" in reg.names()
    inv = reg.handle(cfg, "ui.panels")
    assert inv["schema"] == PAN.SCHEMA

    arranged = reg.handle(cfg, "ui.panel_layout", {
        "action": "arrange", "panels": ["side_chat", "browser"],
        "layout": "split", "confirmed": True})
    assert arranged["state"] == "OK", arranged
    assert arranged["panels"] == ["side_chat", "browser"]

    status = reg.handle(cfg, "ui.panel_layout", {"action": "status"})
    assert status["arranged"] is True
    assert status["layout"] == "split"
    assert status["panels"] == ["side_chat", "browser"]
    # Persisted across a fresh registry/read.
    assert PAN.panel_layout(cfg)["panels"] == ["side_chat", "browser"]


def test_panel_layout_requires_gateway_confirm(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge.gateway.rpc import default_registry

    out = default_registry().handle(cfg, "ui.panel_layout", {
        "action": "arrange", "panels": ["side_chat"], "confirmed": False})
    assert out["state"] == "denied"
    assert out["code"] == PAN.CONFIRM_CODE
