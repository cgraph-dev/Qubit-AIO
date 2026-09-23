"""9U — achievements plugin invocation through the canonical broker.

The source ``~/.hermes/plugins/hermes-achievements/`` directory holds only
legacy durable-state JSON (``state.json`` / ``scan_checkpoint.json`` /
``scan_snapshot.json``) — no manifest and no code. The real plugin is the
vendored *dashboard* plugin (``qubit_runtime/plugins/hermes-achievements/``)
whose one canonical Qubit execution path is
:mod:`voicebridge.qubit_plugins` (the vendored engine over the real
``qubit_state.SessionDB``).

This proves the host-owned adapter registered in :mod:`voicebridge.plugin_runtime`
(``builtin.achievements``) invokes that ONE backend through
``plugin_runtime.invoke`` with e-stop + shared durable confirmation + audit,
delegating to ``qubit_plugins`` rather than loading a second engine. Hermetic:
temp ``XDG_*`` + ``QUBIT_HOME``; no network; no live services.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ADAPTER = "builtin.achievements"


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _qubit_home(cfg) -> Path:
    return Path(cfg.state_dir()) / "hermes"


def _seed_history(cfg, monkeypatch):
    """Seed a real SessionDB under QUBIT_HOME with synthetic history."""
    from voicebridge import qubit_runtime

    home = _qubit_home(cfg)
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(home))
    qubit_runtime.ensure_path()
    from qubit_state import SessionDB

    db = SessionDB()
    try:
        db.create_session("sess-local", "cli", model="ollama/llama3",
                          display_name="Local model session")
        db.append_message("sess-local", "user",
                          content="hit a permission denied and an error")
        db.append_message("sess-local", "assistant", content="working on it",
                          tool_calls=[{"function": {"name": "terminal"}}])
    finally:
        db.close()
    return "sess-local"


def _no_exec(monkeypatch):
    import socket
    import subprocess
    import urllib.request

    def _boom(*a, **k):
        raise AssertionError("plugin invocation must never connect or spawn")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# Discovery: host capability is separate from the registered catalog
# ---------------------------------------------------------------------------
def test_host_adapter_is_discoverable_and_not_an_installable_manifest(
        tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    caps = PR.host_capabilities(cfg)
    rows = {c["id"]: c for c in caps["capabilities"]}
    assert ADAPTER in rows, rows
    assert rows[ADAPTER]["actions"] == ["snapshot", "status", "rescan"]
    assert rows[ADAPTER]["invocation"] == f"plugin_runtime.invoke:{ADAPTER}"
    assert rows[ADAPTER]["handler"] is True
    assert PR.status(cfg)["host_capabilities"] == [ADAPTER]

    got = PR.get_plugin(cfg, ADAPTER)["plugin"]
    assert got["handler"] is True and got["host_adapter"] is True
    # Not a registered/installable catalog row: the exact catalog set is
    # unchanged (no shadow manifest was fabricated for the adapter).
    assert ADAPTER not in {r["id"] for r in PR.list_plugins(cfg)["plugins"]}
    # It is exposed on the loopback MCP tool list too.
    assert ADAPTER in {t["name"] for t in PR._mcp_tools()}
    # No upstream product name leaks on the advertised surface.
    assert "hermes" not in json.dumps(caps).lower()


def test_host_adapter_cannot_be_registered_over_or_removed(tmp_path,
                                                            monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.unregister(cfg, ADAPTER, confirmed=True)
    assert exc.value.code == PR.REFUSED_CODE
    manifest = {"id": ADAPTER, "kind": "tool", "entrypoint": "x:y",
                "digest": "a" * 64, "label": "Nope", "version": "1.0.0"}
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.register(cfg, manifest=manifest, confirmed=True)
    assert exc.value.code == PR.REFUSED_CODE


# ---------------------------------------------------------------------------
# Confirmation + e-stop gates
# ---------------------------------------------------------------------------
def test_host_adapter_requires_confirmation(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.invoke(cfg, ADAPTER)
    assert exc.value.code == PR.CONFIRM_CODE
    # Nothing ran and no engine state was created.
    assert not (_qubit_home(cfg) / "plugin-data").exists()


def test_host_adapter_estop_fail_closed(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        with pytest.raises(PR.PluginRuntimeError) as exc:
            PR.invoke(cfg, ADAPTER, args={"action": "snapshot"}, confirmed=True)
        assert exc.value.code == PR.ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")


def test_host_adapter_bad_action_is_typed(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.invoke(cfg, ADAPTER, args={"action": "bogus"}, confirmed=True)
    assert exc.value.code == PR.BAD_ARGS_CODE


def test_unknown_plugin_and_manifest_only_gate(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.invoke(cfg, "ghost", confirmed=True)
    assert exc.value.code == PR.NOT_FOUND_CODE
    PR.register(cfg, manifest={"id": "acme.tool", "kind": "tool",
                               "entrypoint": "acme:main", "digest": "a" * 64,
                               "label": "Acme", "version": "1.0.0"},
                confirmed=True)
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.invoke(cfg, "acme.tool", confirmed=True)
    assert exc.value.code == PR.UNAVAILABLE_CODE


# ---------------------------------------------------------------------------
# Real engine delegation (no second backend, no network)
# ---------------------------------------------------------------------------
def test_host_adapter_invokes_the_real_achievements_engine(tmp_path,
                                                           monkeypatch):
    from voicebridge import plugin_runtime as PR
    from voicebridge import qubit_plugins as HP

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_history(cfg, monkeypatch)
    HP.reset_plugin_caches()
    _no_exec(monkeypatch)

    rescan = PR.invoke(cfg, ADAPTER, args={"action": "rescan"}, confirmed=True)
    assert rescan["state"] == "OK", rescan
    data = rescan["result"]
    assert data["unlocked_count"] >= 1, data
    assert data["total_count"] >= 60, data
    by_id = {a["id"]: a for a in data["achievements"]}
    assert by_id["open_weights_pilgrim"]["unlocked"] is True, \
        by_id["open_weights_pilgrim"]

    snap = PR.invoke(cfg, ADAPTER, args={"action": "snapshot"}, confirmed=True)
    assert snap["state"] == "OK", snap
    assert snap["result"]["unlocked_count"] >= 1, snap["result"]

    status = PR.invoke(cfg, ADAPTER, args={"action": "status"}, confirmed=True)
    assert status["state"] == "OK", status
    assert status["result"]["run_count"] >= 1, status["result"]

    # The canonical engine persisted its durable state under QUBIT_HOME.
    state_root = _qubit_home(cfg) / "plugin-data" / "hermes-achievements"
    assert (state_root / "scan_snapshot.json").is_file(), sorted(
        p.name for p in state_root.iterdir())
    assert (state_root / "state.json").is_file()


def test_adapter_default_action_is_snapshot(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR
    from voicebridge import qubit_plugins as HP

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_history(cfg, monkeypatch)
    HP.reset_plugin_caches()
    PR.invoke(cfg, ADAPTER, args={"action": "rescan"}, confirmed=True)
    out = PR.invoke(cfg, ADAPTER, confirmed=True)
    assert out["state"] == "OK"
    assert "achievements" in out["result"], out["result"]
