"""Dashboard bind hardening: loopback default, explicit LAN opt-in.

The voicebridge dashboard serves the session-token bootstrap from `/chat`.
A copied or stale ``webui.host: 0.0.0.0`` must not expose that bootstrap to
the LAN unless the operator explicitly opts in with ``webui.allow_lan: true``
(and supplies ``webui.token``). These tests pin the effective bind host.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.config import Config, DEFAULTS, deep_merge  # noqa: E402
from voicebridge.webui import WebUI  # noqa: E402


class _FakeOrch:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._route = "gateway"
        self._web_active = 0
        self.task = type("T", (), {"active": False})()


def _cfg(tmp_path: Path, **webui) -> Config:
    data = deep_merge(DEFAULTS, {"webui": webui})
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    data.setdefault("logging", {})["jsonl_path"] = str(state)
    return Config(data=data)


def test_default_is_loopback_and_ignores_nonloopback_host(tmp_path):
    cfg = _cfg(tmp_path, host="0.0.0.0")
    assert cfg.webui_bind_host() == "127.0.0.1"
    ui = WebUI(_FakeOrch(cfg), port=0, state_dir=tmp_path / "state")
    assert ui.host == "127.0.0.1"
    # Explicit constructor host cannot bypass the opt-in either.
    ui2 = WebUI(_FakeOrch(cfg), host="192.168.1.5", port=0,
                state_dir=tmp_path / "state")
    assert ui2.host == "127.0.0.1"


def test_default_listener_actually_binds_loopback(tmp_path):
    cfg = _cfg(tmp_path, host="0.0.0.0")
    ui = WebUI(_FakeOrch(cfg), port=0, state_dir=tmp_path / "state")
    ui.start()
    try:
        assert ui._server.server_address[0] == "127.0.0.1"
    finally:
        ui.stop()


def test_lan_opt_in_needs_token_fail_closed(tmp_path):
    cfg = _cfg(tmp_path, host="0.0.0.0", allow_lan=True, token="")
    ui = WebUI(_FakeOrch(cfg), port=0, state_dir=tmp_path / "state")
    assert ui.host == "0.0.0.0"
    with pytest.raises(RuntimeError, match="requires webui.token"):
        ui.start()


def test_lan_opt_in_with_token_is_honoured(tmp_path):
    cfg = _cfg(tmp_path, host="0.0.0.0", allow_lan=True, token="lan-token")
    assert cfg.webui_bind_host() == "0.0.0.0"
    ui = WebUI(_FakeOrch(cfg), port=0, state_dir=tmp_path / "state")
    assert ui.host == "0.0.0.0"
    assert ui.api_token == "lan-token"


def test_malformed_allow_lan_fails_closed(tmp_path):
    cfg = _cfg(tmp_path, host="0.0.0.0", allow_lan="maybe")
    assert cfg.webui_bind_host() == "127.0.0.1"
    ui = WebUI(_FakeOrch(cfg), port=0, state_dir=tmp_path / "state")
    assert ui.host == "127.0.0.1"
