"""9U — real directory-plugin lifecycle in the ONE Qubit registry.

Proves the source-first port of the vendored plugin loading path
(``qubit_runtime/plugins/plugin_loader.py`` + ``qubit_cli/plugins.py``):

* a local reviewed plugin directory is copied into the QUBIT_HOME-owned plugin
  store and loaded by calling its real ``register(ctx)``;
* the captured tools/hooks/skills/commands/memory/context registrations are
  persisted as metadata and the tools are invocable through the canonical
  e-stop + shared-journal + audit gate;
* enable/disable/reload/uninstall own the lifecycle and survive a simulated
  process restart (code re-imported from disk);
* dependency/compat checks, source-escape bounds, and remote refusal all fail
  truthfully.

Hermetic: temp XDG_* + QUBIT_HOME; the local install path never touches the
network or a subprocess.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qh"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _no_exec(monkeypatch):
    import socket
    import subprocess
    import urllib.request

    def _boom(*a, **k):
        raise AssertionError("the local plugin path must never connect or spawn")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def _write_plugin(root: Path, name: str, *, yaml_extra: str = "",
                  body: str = "", dashboard: bool = False) -> Path:
    plugin = root / name
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text(
        f'name: {name}\nversion: "1.2.3"\n'
        f'description: "a real local test plugin"\n'
        f"provides_tools:\n  - {name.replace('-', '_')}_add\n"
        f"provides_hooks:\n  - on_session_end\n{yaml_extra}")
    (plugin / "helpers.py").write_text(
        "def add(a, b):\n    return {'sum': a + b}\n")
    if not body:
        body = (
            "from . import helpers\n\n\n"
            "def register(ctx):\n"
            "    ctx.register_tool(\n"
            f"        name='{name.replace('-', '_')}_add', toolset='acme',\n"
            "        schema={'type': 'object'}, handler=_add)\n"
            "    ctx.register_hook('on_session_end', lambda **k: None)\n"
            "    ctx.register_skill('howto', __file__.replace('__init__.py',\n"
            "                                                  'SKILL.md'))\n"
            "    ctx.register_command('acme', lambda raw: 'ok')\n"
            "    ctx.register_memory_provider(_Fake('acme-mem'))\n"
            "    ctx.register_context_engine(_Fake('acme-ctx'))\n"
            "    ctx.register_web_search_provider(_Fake('acme-search'))\n\n\n"
            "class _Fake:\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n\n\n"
            "def _add(args):\n"
            "    return helpers.add(int(args.get('a', 0)), int(args.get('b', 0)))\n")
    (plugin / "__init__.py").write_text(body)
    (plugin / "SKILL.md").write_text("# howto\n")
    if dashboard:
        (plugin / "dashboard").mkdir()
        (plugin / "dashboard" / "manifest.json").write_text(json.dumps({
            "name": name, "label": "Acme Board", "description": "board",
            "version": "1.0.0", "tab": {"label": "Board"},
            "entry": "dist/index.js", "api": "api.py"}))
    return plugin


# ------------------------------------------------------------ real install
def test_install_from_directory_copies_and_loads(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    _no_exec(monkeypatch)
    plugin = _write_plugin(tmp_path / "src", "acme-tools", dashboard=True)

    out = PR.install(cfg, source=str(plugin), confirmed=True,
                     requested_by="test")
    assert out["state"] == "OK" and out["installed"] is True
    rec = out["plugin"]
    assert rec["id"] == "acme-tools"
    assert rec["source_kind"] == PR.SOURCE_DIRECTORY
    assert rec["enabled"] is True and rec["load_state"] == "LOADED"
    assert rec["handler"] is True
    # Real code was copied into the QUBIT_HOME-owned store, not referenced.
    install_dir = Path(rec["install_dir"])
    assert install_dir.is_dir()
    assert plugin not in install_dir.parents
    assert (install_dir / "__init__.py").is_file()
    assert (install_dir / "helpers.py").is_file()
    provides = rec["provides"]
    assert provides["tools"] == ["acme_tools_add"]
    assert provides["hooks"] == ["on_session_end"]
    assert provides["skills"] == ["howto"]
    assert provides["commands"] == ["acme"]
    assert provides["memory_providers"] == ["acme-mem"]
    assert provides["context_engines"] == ["acme-ctx"]
    # A registrar Qubit does not own is reported, never silently faked.
    assert "register_web_search_provider" in provides["unsupported"]
    assert rec["provides"]["dashboard"]["name"] == "acme-tools"
    # Durable record persists in the one registry.
    assert PR.get_plugin(cfg, "acme-tools")["plugin"]["enabled"] is True


def test_install_requires_confirmation_and_sources_are_local(tmp_path,
                                                             monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    plugin = _write_plugin(tmp_path / "src", "acme-tools")
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.install(cfg, source=str(plugin))
    assert exc.value.code == PR.CONFIRM_CODE
    # Nothing copied without confirmation.
    assert not (PR._plugin_home(cfg) / "acme-tools").exists()


# ------------------------------------------------------ invoke via the gate
def test_invoke_directory_plugin_tool_through_the_gate(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    _no_exec(monkeypatch)
    PR.install(cfg, source=str(_write_plugin(tmp_path / "src", "acme-tools")),
               confirmed=True)
    # Confirmation is required, then the real handler output is returned.
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.invoke(cfg, "acme-tools", args={"a": 2, "b": 3})
    assert exc.value.code == PR.CONFIRM_CODE
    out = PR.invoke(cfg, "acme-tools", args={"a": 2, "b": 40}, confirmed=True)
    assert out["state"] == "OK"
    assert out["result"] == {"sum": 42}
    assert out["tool"] == "acme_tools_add"
    # Explicit tool selection is honored; an unknown tool is typed.
    out2 = PR.invoke(cfg, "acme-tools", args={"a": 1, "b": 1},
                     tool="acme_tools_add", confirmed=True)
    assert out2["result"] == {"sum": 2}
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.invoke(cfg, "acme-tools", tool="nope", confirmed=True)
    assert exc.value.code == PR.NOT_FOUND_CODE


def test_invoke_manifest_only_plugin_stays_unavailable(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    PR.register(cfg, manifest={"id": "acme.tool", "kind": "tool",
                               "entrypoint": "acme:main", "digest": "a" * 64,
                               "label": "Acme", "version": "1.0.0"},
                confirmed=True)
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.invoke(cfg, "acme.tool", confirmed=True)
    assert exc.value.code == PR.UNAVAILABLE_CODE


def test_invoke_estop_fails_closed(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    PR.install(cfg, source=str(_write_plugin(tmp_path / "src", "acme-tools")),
               confirmed=True)
    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        with pytest.raises(PR.PluginRuntimeError) as exc:
            PR.invoke(cfg, "acme-tools", args={"a": 1, "b": 1}, confirmed=True)
        assert exc.value.code == PR.ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False, issued_by="cli:user")


# -------------------------------------------------------- lifecycle + restart
def test_disable_enable_survives_simulated_restart(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    PR.install(cfg, source=str(_write_plugin(tmp_path / "src", "acme-tools")),
               confirmed=True)
    assert PR.disable_plugin(cfg, "acme-tools", confirmed=True)["plugin"][
        "enabled"] is False
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.invoke(cfg, "acme-tools", args={"a": 1, "b": 1}, confirmed=True)
    assert exc.value.code == PR.DISABLED_CODE
    assert PR.enable_plugin(cfg, "acme-tools",
                            confirmed=True)["plugin"]["load_state"] == "LOADED"
    # Simulate a process restart: in-process cache dropped, code re-imported.
    PR._LOADED.clear()
    assert PR.invoke(cfg, "acme-tools", args={"a": 5, "b": 5},
                     confirmed=True)["result"] == {"sum": 10}


def test_reload_reimports_changed_plugin(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    plugin = _write_plugin(tmp_path / "src", "acme-tools")
    PR.install(cfg, source=str(plugin), confirmed=True)
    assert PR.invoke(cfg, "acme-tools", args={"a": 1, "b": 1},
                     confirmed=True)["result"] == {"sum": 2}
    installed = PR.get_plugin(cfg, "acme-tools")["plugin"]["install_dir"]
    (Path(installed) / "helpers.py").write_text(
        "def add(a, b):\n    return {'sum': a + b + 100}\n")
    out = PR.reload(cfg)
    assert out["reloaded_plugins"] == ["acme-tools"], out
    assert PR.invoke(cfg, "acme-tools", args={"a": 1, "b": 1},
                     confirmed=True)["result"] == {"sum": 102}


def test_uninstall_removes_code_and_record(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    PR.install(cfg, source=str(_write_plugin(tmp_path / "src", "acme-tools")),
               confirmed=True)
    install_dir = PR.get_plugin(cfg, "acme-tools")["plugin"]["install_dir"]
    out = PR.unregister(cfg, "acme-tools", confirmed=True)
    assert out["removed"] == "acme-tools"
    assert out["removed_dir"] == install_dir
    assert not Path(install_dir).exists()
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.get_plugin(cfg, "acme-tools")
    assert exc.value.code == PR.NOT_FOUND_CODE
    # Host-owned built-ins can never be removed or shadowed.
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.unregister(cfg, "builtin.echo", confirmed=True)
    assert exc.value.code == PR.REFUSED_CODE


# ------------------------------------------------- dependencies + compat
def test_missing_python_dependency_is_setup_required(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    plugin = _write_plugin(
        tmp_path / "src", "dep-plugin",
        yaml_extra="python_dependencies:\n  - totally-not-real-pkg-xyz123\n")
    out = PR.install(cfg, source=str(plugin), confirmed=True)
    rec = out["plugin"]
    assert rec["enabled"] is False
    assert rec["load_state"] == "SETUP_REQUIRED"
    assert rec["setup_required"] is True
    assert out["requirements"]["missing_dependencies"] == [
        "totally-not-real-pkg-xyz123"]
    # Truthful refusal names the exact requirement; no fake enable.
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.enable_plugin(cfg, "dep-plugin", confirmed=True)
    assert exc.value.code == PR.DEPENDENCY_CODE
    assert "totally-not-real-pkg-xyz123" in exc.value.detail


def test_incompatible_manifest_version_refused_without_copy(tmp_path,
                                                            monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    plugin = _write_plugin(tmp_path / "src", "compat-plugin",
                           yaml_extra="manifest_version: 99\n")
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.install(cfg, source=str(plugin), confirmed=True)
    assert exc.value.code == PR.COMPAT_CODE
    assert not (PR._plugin_home(cfg) / "compat-plugin").exists()


def test_source_escape_symlink_rejected(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    plugin = _write_plugin(tmp_path / "src", "escape-plugin")
    try:
        (plugin / "leak.txt").symlink_to("/etc/hostname")
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(PR.PluginRuntimeError) as exc:
        PR.install(cfg, source=str(plugin), confirmed=True)
    assert exc.value.code == PR.SOURCE_INVALID_CODE
    assert not (PR._plugin_home(cfg) / "escape-plugin").exists()


# ------------------------------------------------------------ remote refusal
def test_remote_install_stays_a_typed_refusal(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    _no_exec(monkeypatch)
    out = PR.install(cfg, identifier="https://github.com/evil/plugin.git",
                     confirmed=True)
    assert out["state"] == "BLOCKED"
    assert out["code"] == "BLOCKED_REMOTE_PLUGIN"
    assert PR._plugin_home(cfg).exists() is False or not any(
        PR._plugin_home(cfg).iterdir())


# --------------------------------------------------------- dashboard tabs
def test_dashboard_tabs_from_installed_plugin(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    PR.install(cfg, source=str(_write_plugin(tmp_path / "src", "acme-tools",
                                             dashboard=True)), confirmed=True)
    tabs = PR.dashboard_tabs(cfg)["tabs"]
    assert [t["plugin"] for t in tabs] == ["acme-tools"]
    assert tabs[0]["label"] == "Acme Board"
    assert tabs[0]["has_api"] is True
