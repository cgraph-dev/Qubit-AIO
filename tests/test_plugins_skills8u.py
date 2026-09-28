"""Packet 8U — skills/plugins delta closure (A46 + A27).

A46: the bundled ``visualize`` skill manifest really loads and validates from
``skills/visualize/SKILL.md`` through the verified-skills bundled registry and
lists without a network dependency. A27 (backend): ``plugin_runtime`` returns
installable catalog rows and a canonical ``install`` path wired to the shared
``register``/``bind_handler`` op (estop + confirmation journal unchanged).

Hermetic: tmp XDG state only; no network.
"""
from __future__ import annotations

import json
import socket
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


# ---------------------------------------------------------------------------
# A46 — bundled visualize skill
# ---------------------------------------------------------------------------
def test_bundled_visualize_loads_and_validates():
    from voicebridge import verified_skills as V

    assert "visualize" in V.BUNDLED_SKILLS
    man = V.load_bundled_skill("visualize")
    assert man["name"] == "visualize"
    assert man["kind"] == "bundled-skill"
    assert man["source"] == "qubit-bundled"
    assert man["branded"] is True
    assert man["path"] == "skills/visualize/SKILL.md"
    assert len(man["digest"]) == 64
    assert man["capabilities"]


def test_bundled_visualize_lists_without_network(monkeypatch):
    from voicebridge import verified_skills as V

    def _boom(*_a, **_k):
        raise AssertionError("bundled skill loading must not touch the network")

    monkeypatch.setattr(socket, "socket", _boom)
    listed = V.bundled_skill_manifests()
    assert [m["name"] for m in listed] == ["visualize"]
    out = V.register_bundled_skills()
    assert out["state"] == "OK" and out["count"] == 1
    assert out["skills"][0]["name"] == "visualize"
    # aliases resolve to the same registry
    assert V.bundled_skills() == listed
    assert V.get_bundled_skill("ghost") is None


def test_bundled_manifest_is_qubit_branded():
    from voicebridge import verified_skills as V

    blob = json.dumps(V.bundled_skill_manifests()).lower()
    assert "hermes" not in blob and "openclaw" not in blob
    with pytest.raises(V.BundledSkillError):
        V.validate_bundled_skill_manifest({
            "name": "ghost", "description": "A Hermes helper",
            "kind": "bundled-skill", "source": "qubit-bundled"})
    with pytest.raises(V.BundledSkillError):
        V.load_bundled_skill("not-a-bundled-skill")


def test_bundled_skill_is_not_agent_memory(tmp_path, monkeypatch):
    """Bundled skills list separately; they never contaminate recall/list."""
    from voicebridge import verified_skills as V

    cfg = _cfg(tmp_path, monkeypatch)
    store = V.verified_skills_store(cfg)
    assert store.list() == []              # advisory memory untouched
    assert [m["name"] for m in store.bundled()] == ["visualize"]
    assert store.register_bundled_skills()["count"] == 1
    assert store.stats()["bundled"] == 1
    assert store.bundled_skill("visualize")["name"] == "visualize"
    store.close()


# ---------------------------------------------------------------------------
# A27 — plugin catalog + canonical install
# ---------------------------------------------------------------------------
def test_list_plugins_exposes_install_catalog(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    out = PR.list_plugins(cfg)
    assert out["install_capability"] == PR.INSTALL_CAPABILITY
    catalog = out["catalog"]
    builtin = next(r for r in catalog if r["id"] == "builtin.echo")
    assert builtin["installed"] is True and builtin["can_install"] is False
    assert builtin["source"] == "qubit-builtin"
    cat = PR.plugin_catalog(cfg)
    assert cat["remote"]["state"] == "REFUSED"


def test_reviewed_catalog_manifest_installs(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    cat_dir = Path(cfg.state_dir()) / PR.CATALOG_DIR
    cat_dir.mkdir(parents=True, exist_ok=True)
    (cat_dir / "acme.json").write_text(
        json.dumps(_manifest(description="Qubit reviewed tool")),
        encoding="utf-8")

    rows = {r["identifier"]: r for r in PR.plugin_catalog(cfg)["plugins"]}
    assert rows["acme"]["installed"] is False
    assert rows["acme"]["can_install"] is True
    assert rows["acme"]["source"] == "qubit-reviewed"

    out = PR.install(cfg, identifier="acme", confirmed=True)
    assert out["state"] == "OK" and out["installed"] is True
    assert out["plugin"]["id"] == "acme.tool"
    assert any(r["id"] == "acme.tool"
               for r in PR.list_plugins(cfg)["plugins"])
    # re-install is a typed exists, never a silent overwrite
    with pytest.raises(PR.PluginRuntimeError) as excinfo:
        PR.install(cfg, identifier="acme", confirmed=True)
    assert excinfo.value.code == PR.EXISTS_CODE


def test_install_manifest_file_and_confirm_gate(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    with pytest.raises(PR.PluginRuntimeError) as excinfo:
        PR.install(cfg, manifest=_manifest())
    assert excinfo.value.code == PR.CONFIRM_CODE
    path = tmp_path / "acme.json"
    path.write_text(json.dumps(_manifest()), encoding="utf-8")
    out = PR.install(cfg, identifier=str(path), confirmed=True)
    assert out["state"] == "OK" and out["source"] == "path"


def test_install_handler_binds_in_process(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    # Isolate the process-local handler map so this test cannot leak a binding
    # into other tests in the same interpreter.
    monkeypatch.setattr(PR, "_HANDLERS", dict(PR._HANDLERS))
    cfg = _cfg(tmp_path, monkeypatch)
    out = PR.install(cfg, manifest=_manifest(),
                     handler=lambda args: {"got": args.get("x")},
                     confirmed=True)
    assert out["plugin"]["handler"] is True
    invoked = PR.invoke(cfg, "acme.tool", args={"x": 3}, confirmed=True)
    assert invoked["result"] == {"got": 3}


def test_install_refuses_remote_and_branding(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    remote = PR.install(cfg, identifier="owner/repo", confirmed=True)
    assert remote["state"] == "BLOCKED"
    assert remote["code"] == "BLOCKED_REMOTE_PLUGIN"
    with pytest.raises(PR.PluginRuntimeError) as excinfo:
        PR.install(cfg, manifest=_manifest(label="Hermes Helper"),
                   confirmed=True)
    assert excinfo.value.code == PR.BRANDING_CODE
    with pytest.raises(PR.PluginRuntimeError) as excinfo:
        PR.install(cfg, identifier="./missing-catalog.json", confirmed=True)
    assert excinfo.value.code == PR.INSTALL_SOURCE_CODE


def test_install_estop_fail_closed(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR
    from voicebridge.power import power_control_store

    cfg = _cfg(tmp_path, monkeypatch)
    store = power_control_store(cfg)
    store.set(emergency_stop=True, global_disable=False, issued_by="cli:user")
    try:
        with pytest.raises(PR.PluginRuntimeError) as excinfo:
            PR.install(cfg, manifest=_manifest(), confirmed=True)
        assert excinfo.value.code == PR.ESTOP_CODE
    finally:
        store.set(emergency_stop=False, global_disable=False,
                  issued_by="cli:user")
