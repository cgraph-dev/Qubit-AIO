"""Packet 8U — paired node host: enrollment, capabilities, dispatch gates.

Real behavior proofs (hermetic, tmp state dirs): empty-state status, the
confirmation gate with nothing persisted, enroll/list/show, endpoint
policy, confirmation-gated revoke, honest typed UNAVAILABLE for remote
capabilities, dispatch refusals that never spawn a shell, secret redaction
in output and on disk, emergency-stop refusal, and human-source gating.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _enroll(cfg, NH, **kw):
    kw.setdefault("name", "desk-node")
    kw.setdefault("endpoint", "http://127.0.0.1:8443/")
    kw.setdefault("confirmed", True)
    return NH.enroll(cfg, **kw)


def test_status_on_empty_state_never_raises(tmp_path, monkeypatch):
    from voicebridge import node_host as NH

    cfg = _cfg(tmp_path, monkeypatch)
    out = NH.status(cfg)
    assert out["state"] == "OK"
    assert out["nodes"] == 0
    assert out["node_ids"] == []
    assert out["host_state"] in ("READY", "ESTOP")
    assert "device.inventory" in out["supported_capabilities"]
    assert "screen" in out["remote_capabilities"]
    assert NH.list_nodes(cfg)["nodes"] == []
    assert NH.show(cfg, "node_missing")["code"] == NH.NODE_UNKNOWN


def test_enroll_requires_confirmation_and_persists_nothing(tmp_path, monkeypatch):
    from voicebridge import node_host as NH

    cfg = _cfg(tmp_path, monkeypatch)
    out = NH.enroll(cfg, name="desk-node", endpoint="http://127.0.0.1:8443/")
    assert out["state"] == "REFUSED"
    assert out["code"] == NH.NODE_ENROLL_CONFIRM_REQUIRED
    assert NH.list_nodes(cfg)["nodes"] == []
    assert not (cfg.state_dir() / "nodes.json").exists()


def test_enroll_list_show_and_private_file(tmp_path, monkeypatch):
    from voicebridge import node_host as NH

    cfg = _cfg(tmp_path, monkeypatch)
    out = _enroll(cfg, NH, capabilities=["screen", "device.inventory"])
    assert out["state"] == "OK"
    node = out["node"]
    nid = node["id"]
    assert nid.startswith("node_")
    assert node["endpoint"] == "http://127.0.0.1:8443/"
    assert node["capabilities"] == ["device.inventory", "screen"]

    listed = NH.list_nodes(cfg)
    assert listed["state"] == "OK" and len(listed["nodes"]) == 1
    assert listed["nodes"][0]["id"] == nid

    shown = NH.show(cfg, nid)
    assert shown["state"] == "OK"
    assert shown["node"]["name"] == "desk-node"
    assert NH.show(cfg, "node_missing")["code"] == NH.NODE_UNKNOWN

    mode = stat.S_IMODE(os.stat(cfg.state_dir() / "nodes.json").st_mode)
    assert mode == 0o600


def test_endpoint_validation_rejects_unsafe_values(tmp_path, monkeypatch):
    from voicebridge import node_host as NH

    cfg = _cfg(tmp_path, monkeypatch)
    bad = ("", "notaurl", "javascript:alert(1)", "file:///etc/passwd",
           "http://user:pw@host/x", "http://host/x; rm -rf /",
           "http://host/\x01")
    for value in bad:
        out = NH.enroll(cfg, name="n", endpoint=value, confirmed=True)
        assert out["state"] == "REFUSED", value
        assert out["code"] == NH.NODE_BAD_INPUT, value
    assert NH.list_nodes(cfg)["nodes"] == []
    assert not (cfg.state_dir() / "nodes.json").exists()


def test_revoke_requires_confirmation(tmp_path, monkeypatch):
    from voicebridge import node_host as NH

    cfg = _cfg(tmp_path, monkeypatch)
    nid = _enroll(cfg, NH)["node"]["id"]
    out = NH.revoke(cfg, nid)
    assert out["code"] == NH.NODE_REVOKE_CONFIRM_REQUIRED
    assert len(NH.list_nodes(cfg)["nodes"]) == 1

    out = NH.revoke(cfg, nid, confirmed=True)
    assert out["state"] == "OK" and out["removed"] == nid
    assert NH.list_nodes(cfg)["nodes"] == []
    assert NH.revoke(cfg, nid, confirmed=True)["code"] == NH.NODE_UNKNOWN


def test_capabilities_reports_remote_unavailable(tmp_path, monkeypatch):
    from voicebridge import node_host as NH

    cfg = _cfg(tmp_path, monkeypatch)
    nid = _enroll(cfg, NH,
                  capabilities=["screen", "audio", "camera",
                                "media_playback", "device.inventory"])["node"]["id"]
    out = NH.capabilities(cfg, nid)
    assert out["state"] == "OK"
    rows = {r["capability"]: r for r in out["capabilities"]}
    for cap in ("screen", "audio", "camera", "media_playback"):
        assert rows[cap]["state"] == "UNAVAILABLE", cap
        assert rows[cap]["code"] == NH.NODE_CAPABILITY_UNAVAILABLE, cap
    assert rows["device.inventory"]["state"] == "READY"
    assert out["declared"] == ["audio", "camera", "device.inventory",
                               "media_playback", "screen"]
    assert NH.capabilities(cfg, "node_missing")["code"] == NH.NODE_UNKNOWN


def test_dispatch_refusals_never_spawn_a_shell(tmp_path, monkeypatch):
    from voicebridge import node_host as NH

    cfg = _cfg(tmp_path, monkeypatch)
    nid = _enroll(cfg, NH, capabilities=["screen"])["node"]["id"]

    def _boom(*_a, **_k):
        raise AssertionError("node host touched a shell")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(os, "system", _boom)

    assert NH.dispatch(cfg, "node_missing",
                       "device.inventory")["code"] == NH.NODE_UNKNOWN
    assert NH.dispatch(cfg, nid,
                       "quantum.teleport")["code"] == NH.NODE_CAPABILITY_UNAVAILABLE
    assert NH.dispatch(cfg, nid, "screen",
                       confirmed=True)["code"] == NH.NODE_DISPATCH_REFUSED
    assert NH.dispatch(cfg, nid, "shell",
                       confirmed=True)["code"] == NH.NODE_DISPATCH_REFUSED
    assert NH.dispatch(cfg, nid, "exec")["code"] == NH.NODE_DISPATCH_REFUSED

    ok = NH.dispatch(cfg, nid, "device.inventory")
    assert ok["state"] == "OK"
    assert "machine_id" in ok["result"]
    assert ok["result"]["nodes"] == 1
    assert NH.show(cfg, nid)["node"]["last_outcome"] == "DISPATCH_OK"


def test_secrets_never_appear_in_output_or_on_disk(tmp_path, monkeypatch):
    from voicebridge import node_host as NH

    cfg = _cfg(tmp_path, monkeypatch)
    secret = "sk-abcdef1234567890"
    out = _enroll(cfg, NH,
                  name="alpha api_key=" + secret,
                  endpoint="http://127.0.0.1:8443/hook?api_key=" + secret)
    assert out["state"] == "OK"
    blob = json.dumps(out)
    assert secret not in blob
    assert "[REDACTED]" in blob

    on_disk = (cfg.state_dir() / "nodes.json").read_text(encoding="utf-8")
    assert secret not in on_disk
    assert "[REDACTED]" in on_disk


def test_estop_refuses_enroll_dispatch_and_revoke(tmp_path, monkeypatch):
    from voicebridge import node_host as NH
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    nid = _enroll(cfg, NH)["node"]["id"]
    power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                 issued_by="user")

    out = NH.enroll(cfg, name="other", endpoint="http://127.0.0.1:8444/",
                    confirmed=True)
    assert out["code"] == NH.NODE_ESTOP
    assert NH.dispatch(cfg, nid, "device.inventory")["code"] == NH.NODE_ESTOP
    assert NH.revoke(cfg, nid, confirmed=True)["code"] == NH.NODE_ESTOP
    assert len(NH.list_nodes(cfg)["nodes"]) == 1
    assert NH.status(cfg)["estop"] is True
    assert NH.status(cfg)["host_state"] == "ESTOP"


def test_non_human_source_cannot_enroll(tmp_path, monkeypatch):
    from voicebridge import node_host as NH

    cfg = _cfg(tmp_path, monkeypatch)
    out = NH.enroll(cfg, name="n", endpoint="http://127.0.0.1:8443/",
                    confirmed=True, source="model")
    assert out["code"] == NH.NODE_DISPATCH_REFUSED
    assert NH.list_nodes(cfg)["nodes"] == []
    assert not (cfg.state_dir() / "nodes.json").exists()
