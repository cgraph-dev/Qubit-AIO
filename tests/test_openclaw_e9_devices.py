"""OpenClaw E9 — device capability inventory (OC-55) production proofs.

The inventory is exposed through the one shared ``runtime_inventory`` surface
that the CLI ``status``, the dashboard ``/api/runtime`` route, and voice all
read. These tests drive that production path and assert every family is a
real probe (never invented) with typed availability, that node-host-derived
capabilities and the durable emergency-stop latch show through, and that the
read-only probe never raises when an actuator is absent.
"""
from __future__ import annotations

import json
import time


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _enroll(cfg, name="local", endpoint="http://127.0.0.1:8443/"):
    from voicebridge import node_host as NH

    out = NH.enroll(cfg, name=name, endpoint=endpoint,
                    capabilities=["file.transfer"], confirmed=True)
    assert out["state"] == "OK", out
    return out["node"]["id"]


_FAMILIES = ("browser", "desktop", "ide", "vision", "runtime", "node_host")


def test_device_inventory_has_all_families_typed(tmp_path, monkeypatch):
    from voicebridge import runtime_inventory as RINV

    cfg = _cfg(tmp_path, monkeypatch)
    inv = RINV.device_inventory(cfg)
    assert inv["schema"] == "qubit-device-capabilities/1"
    assert inv["state"] == "OK"
    for family in _FAMILIES:
        assert family in inv, family
        assert "state" in inv[family], family
        assert isinstance(inv[family]["state"], str)
        assert inv[family]["state"]
    # Browser is a real probe: cdp is true only when doctor succeeded.
    assert isinstance(inv["browser"]["cdp"], bool)
    if inv["browser"]["state"] == "OK":
        assert inv["browser"]["cdp"] is True
    else:
        assert inv["browser"]["cdp"] is False
    assert isinstance(inv["ide"]["editors"], list)
    assert isinstance(inv["runtime"]["runtimes"], list)
    assert isinstance(inv["desktop"]["display_present"], bool)
    assert isinstance(inv["desktop"]["atspi_reachable"], bool)
    assert inv["authority"]


def test_runtime_inventory_exposes_devices_surface(tmp_path, monkeypatch):
    from voicebridge import runtime_inventory as RINV

    cfg = _cfg(tmp_path, monkeypatch)
    inv = RINV.runtime_inventory(cfg)
    assert inv["devices"]["schema"] == "qubit-device-capabilities/1"
    assert set(_FAMILIES) <= set(inv["devices"])


def test_device_inventory_reflects_enrolled_nodes(tmp_path, monkeypatch):
    from voicebridge import runtime_inventory as RINV

    cfg = _cfg(tmp_path, monkeypatch)
    empty = RINV.device_inventory(cfg)
    assert empty["node_host"]["nodes"] == 0

    node_id = _enroll(cfg)
    inv = RINV.device_inventory(cfg)
    assert inv["node_host"]["nodes"] == 1
    assert inv["node_host"]["host_state"] == "READY"
    assert "device.inventory" in inv["node_host"]["supported_capabilities"]

    # The node is visible through node_host.show on the same id.
    from voicebridge import node_host as NH

    assert NH.show(cfg, node_id)["state"] == "OK"


def test_device_inventory_reflects_estop(tmp_path, monkeypatch):
    from voicebridge import runtime_inventory as RINV
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    _enroll(cfg)
    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="user")
    inv = RINV.device_inventory(cfg)
    assert inv["node_host"]["host_state"] == "ESTOP"
    assert inv["node_host"]["state"] == "OK"


def test_device_inventory_never_raises_without_actuators(tmp_path,
                                                         monkeypatch):
    import socket
    import urllib.request

    from voicebridge import runtime_inventory as RINV

    def _boom(*_a, **_k):
        raise AssertionError("outbound connection attempted")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    cfg = _cfg(tmp_path, monkeypatch)
    inv = RINV.device_inventory(cfg)
    # A missing actuator is typed, never an exception.
    assert inv["state"] == "OK"
    assert inv["browser"]["state"] in ("UNAVAILABLE", "DEGRADED")
    assert inv["browser"]["cdp"] is False


def test_runtime_route_serves_devices(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(f"logging:\n  jsonl_path: '{state}'\nwebui:\n  token: t\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg
            self._t0 = time.time()

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    try:
        import urllib.request

        port = ui._server.server_address[1]
        req = urllib.request.Request("http://127.0.0.1:%d/api/runtime" % port)
        req.add_header("X-Api-Token", "t")
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
        assert body["devices"]["schema"] == "qubit-device-capabilities/1"
        assert set(_FAMILIES) <= set(body["devices"])
    finally:
        ui.stop()
