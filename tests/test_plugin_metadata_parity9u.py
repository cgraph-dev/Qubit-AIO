"""Plugin surface metadata parity between ``plugin_runtime`` and ``plugin_catalog``.

Two catalog surfaces describe the same installable plugin. They disagreed on two
things the UI acts on:

* ``plugin_runtime._catalog_row`` set ``name`` to the display label (other rows
  use the machine id the enable/install actions address) and reported a
  reviewed-but-not-installed entry as ``enabled: true``;
* ``plugin_catalog.local_catalog`` copied only tools/hooks/skills from a runtime
  record, dropping commands / CLI commands / memory providers / context engines /
  dashboard metadata that the runtime surface exposes.

Both are fixed at the source; these tests pin the two surfaces to one contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


REC = {
    "id": "acme-tools",
    "kind": "tool",
    "label": "Acme Tools",
    "description": "d",
    "version": "1.2.3",
    "enabled": True,
    "provides": {
        "tools": ["acme_add"],
        "hooks": ["on_session_end"],
        "skills": ["howto"],
        "commands": ["acme"],
        "cli_commands": ["acme"],
        "memory_providers": ["acme-mem"],
        "context_engines": ["acme-ctx"],
        "unsupported": ["register_web_search_provider"],
        "dashboard": {"name": "acme-tools"},
    },
}


def test_catalog_row_name_is_the_machine_id_not_the_label():
    from voicebridge import plugin_runtime as PR

    row = PR._catalog_row(REC, installed=True, source="qubit-local")
    assert row["id"] == "acme-tools"
    assert row["name"] == "acme-tools", row
    assert row["label"] == "Acme Tools", row


def test_uninstalled_catalog_entry_is_never_enabled():
    from voicebridge import plugin_runtime as PR

    reviewed = PR._catalog_row(REC, installed=False, source="qubit-reviewed")
    assert reviewed["installed"] is False
    assert reviewed["enabled"] is False, reviewed
    assert reviewed["can_install"] is True

    installed = PR._catalog_row(REC, installed=True, source="qubit-local")
    assert installed["enabled"] is True, installed


def test_local_catalog_preserves_runtime_provides_metadata(tmp_path,
                                                           monkeypatch):
    from voicebridge import plugin_catalog as PC

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(PC, "_vendored_agent_entries", lambda _cfg: [])
    monkeypatch.setattr(PC, "_vendored_dashboard_entries", lambda _cfg: [])
    monkeypatch.setattr(PC, "_overlay_manifests", lambda: [])
    monkeypatch.setattr(
        PC, "_runtime_entries",
        lambda _cfg: [{
            "id": "acme-tools", "label": "Acme Tools",
            "description": "d", "version": "1.2.3", "source": "user",
            "kind": "tool", "installed": True, "enabled": True,
            "source_kind": "directory", "install_dir": "/tmp/acme-tools",
            "provides": REC["provides"],
        }])

    out = PC.local_catalog(cfg)
    row = next(r for r in out["entries"] if r["id"] == "acme-tools")
    provides = row["provides"]
    assert provides["tools"] == ["acme_add"]
    assert provides["hooks"] == ["on_session_end"]
    assert provides["skills"] == ["howto"]
    assert provides["commands"] == ["acme"]
    assert provides["cli_commands"] == ["acme"]
    assert provides["memory_providers"] == ["acme-mem"]
    assert provides["context_engines"] == ["acme-ctx"]
    assert provides["unsupported"] == ["register_web_search_provider"]
    assert provides["dashboard"] == {"name": "acme-tools"}
