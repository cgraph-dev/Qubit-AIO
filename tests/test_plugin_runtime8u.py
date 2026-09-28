"""Packet 8U — in-process plugin host contract (OpenClaw audit #40).

Proves a real registry over tool/channel/provider/hook manifests with typed
UNAVAILABLE gates, no arbitrary code execution, manifest validation bounds,
redaction, and estop fail-closed. Hermetic: tmp XDG state only.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

DIGEST = "a" * 64


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _manifest(pid: str = "acme.tool", **over):
    m = {"id": pid, "kind": "tool", "entrypoint": "acme.plugin:main",
         "digest": DIGEST, "label": "Acme", "version": "1.0.0",
         "capabilities": ["run"]}
    m.update(over)
    return m


def _no_exec(monkeypatch):
    import socket
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("plugin runtime must never spawn or connect")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def test_registry_kinds_and_builtin():
    from voicebridge import plugin_runtime as PR

    assert PR.PLUGIN_KINDS == ("tool", "channel", "provider", "hook")
    assert "builtin.echo" in PR._BUILTIN
    assert "builtin.echo" in PR._HANDLERS


def test_register_valid_manifest_redacted(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    out = PR.register(cfg, manifest=_manifest(), confirmed=True)
    assert out["state"] == "OK"
    assert out["plugin"]["id"] == "acme.tool"
    assert out["plugin"]["digest"] == DIGEST
    assert out["plugin"]["handler"] is False
    got = PR.get_plugin(cfg, "acme.tool")
    assert got["plugin"]["entrypoint"] == "acme.plugin:main"
    listed = PR.list_plugins(cfg)["plugins"]
    assert {r["id"] for r in listed} == {"builtin.echo", "acme.tool"}
    # the manifest is metadata only; nothing was executed
    assert "no code was imported" in out["note"]


def test_register_requires_confirm_and_writes_nothing(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(PR.PluginRuntimeError) as excinfo:
        PR.register(cfg, manifest=_manifest())
    assert excinfo.value.code == PR.CONFIRM_CODE
    assert not (Path(cfg.state_dir()) / PR.STORE_NAME).exists()


def test_manifest_validation_bounds(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    bad = [
        (_manifest(pid="Bad ID"), PR.INVALID_MANIFEST_CODE),
        (_manifest(kind="daemon"), PR.INVALID_MANIFEST_CODE),
        (_manifest(entrypoint="/bin/sh -c run"), PR.INVALID_MANIFEST_CODE),
        (_manifest(entrypoint="https://evil.example/x"),
         PR.INVALID_MANIFEST_CODE),
        (_manifest(digest="nothex"), PR.INVALID_MANIFEST_CODE),
        (_manifest(extra="nope"), PR.INVALID_MANIFEST_CODE),
    ]
    for manifest, code in bad:
        with pytest.raises(PR.PluginRuntimeError) as excinfo:
            PR.register(cfg, manifest=manifest, confirmed=True)
        assert excinfo.value.code == code, manifest
    huge = _manifest(entrypoint="a" * (PR.MAX_MANIFEST_BYTES + 10))
    with pytest.raises(PR.PluginRuntimeError) as excinfo:
        PR.register(cfg, manifest=huge, confirmed=True)
    assert excinfo.value.code in (PR.TOO_LARGE_CODE, PR.INVALID_MANIFEST_CODE)
    assert not (Path(cfg.state_dir()) / PR.STORE_NAME).exists()


def test_duplicate_and_unregister(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    PR.register(cfg, manifest=_manifest(), confirmed=True)
    with pytest.raises(PR.PluginRuntimeError) as excinfo:
        PR.register(cfg, manifest=_manifest(), confirmed=True)
    assert excinfo.value.code == PR.EXISTS_CODE
    with pytest.raises(PR.PluginRuntimeError) as excinfo:
        PR.unregister(cfg, "acme.tool")
    assert excinfo.value.code == PR.CONFIRM_CODE
    assert PR.unregister(cfg, "acme.tool", confirmed=True)["removed"] == \
        "acme.tool"
    with pytest.raises(PR.PluginRuntimeError) as excinfo:
        PR.unregister(cfg, "acme.tool", confirmed=True)
    assert excinfo.value.code == PR.NOT_FOUND_CODE
    with pytest.raises(PR.PluginRuntimeError) as excinfo:
        PR.unregister(cfg, "builtin.echo", confirmed=True)
    assert excinfo.value.code == PR.REFUSED_CODE


def test_invoke_unknown_and_unavailable(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(PR.PluginRuntimeError) as excinfo:
        PR.invoke(cfg, "ghost", confirmed=True)
    assert excinfo.value.code == PR.NOT_FOUND_CODE
    PR.register(cfg, manifest=_manifest(), confirmed=True)
    with pytest.raises(PR.PluginRuntimeError) as excinfo:
        PR.invoke(cfg, "acme.tool", confirmed=True)
    assert excinfo.value.code == PR.UNAVAILABLE_CODE
    assert "never executed" in excinfo.value.detail


def test_invoke_builtin_handler(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    _no_exec(monkeypatch)
    with pytest.raises(PR.PluginRuntimeError) as excinfo:
        PR.invoke(cfg, "builtin.echo")
    assert excinfo.value.code == PR.CONFIRM_CODE
    out = PR.invoke(cfg, "builtin.echo", args={"text": "hello"}, confirmed=True)
    assert out["state"] == "OK"
    assert out["result"]["echo"] == "hello"
    assert "nothing external executed" in out["note"]
    with pytest.raises(PR.PluginRuntimeError) as excinfo:
        PR.invoke(cfg, "builtin.echo", args="not-an-object", confirmed=True)
    assert excinfo.value.code == PR.BAD_ARGS_CODE


def test_invoke_args_bounds(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(PR.PluginRuntimeError) as excinfo:
        PR.invoke(cfg, "builtin.echo",
                  args={"text": "x" * (PR.MAX_ARGS_BYTES + 10)}, confirmed=True)
    assert excinfo.value.code == PR.BAD_ARGS_CODE


def test_redaction_no_secret_leak(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR
    from voicebridge.logging_setup import setup

    cfg = _cfg(tmp_path, monkeypatch)
    secret = "sk-live-plugin-secret-1234567890"
    setup(level="INFO", jsonl=True, state_dir=cfg.state_dir())
    try:
        PR.register(cfg, manifest=_manifest(), confirmed=True)
        out = PR.invoke(cfg, "builtin.echo",
                        args={"text": f"token={secret}"}, confirmed=True)
        assert secret not in json.dumps(out)
        blob = (Path(cfg.state_dir()) / "events.jsonl").read_text()
        assert secret not in blob
    finally:
        from voicebridge import logging_setup as LS

        LS._EVENT_FILE = None


def test_estop_fail_closed(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        assert PR.status(cfg)["estop"] is True
        assert PR.status(cfg)["state"] == "ESTOP"
        assert PR.list_plugins(cfg)["state"] == "ESTOP"
        with pytest.raises(PR.PluginRuntimeError) as excinfo:
            PR.register(cfg, manifest=_manifest(), confirmed=True)
        assert excinfo.value.code == PR.ESTOP_CODE
        with pytest.raises(PR.PluginRuntimeError) as excinfo:
            PR.invoke(cfg, "builtin.echo", confirmed=True)
        assert excinfo.value.code == PR.ESTOP_CODE
        assert PR.refuse_estop("register")["code"] == PR.ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")


def test_doctor_reports_health(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    out = PR.doctor(cfg)
    assert out["state"] == "OK" and out["store_ok"] is True
    assert out["external_runtime"] == PR.UNAVAILABLE_CODE
    assert "builtin.echo" in out["handlers"]
