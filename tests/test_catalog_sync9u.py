"""Objective B — local Hermes catalog import (MCP + counts).

Proves the read-only migration imports compatible MCP server definitions into
the one canonical registry, converts secret VALUES to references, is
idempotent, never overwrites a Qubit-owned record, and reports source-vs-
destination catalog counts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _source(home: Path):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "mcp_servers:\n"
        "  graft:\n"
        "    command: graft\n"
        "    args: [mcp, /repo]\n"
        "    enabled: true\n"
        "  webhook:\n"
        "    url: https://example.com/mcp\n"
        "    enabled: false\n"
        "    env:\n"
        "      API_KEY: ${MY_MCP_KEY}\n"
        "      RAW_SECRET: sk-live-DO-NOT-COPY\n"
        "  broken:\n"
        "    enabled: true\n")
    (home / ".env").write_text("MY_MCP_KEY=abc123\n")


def test_mcp_import_is_reference_only_idempotent_and_non_overwriting(
        tmp_path, monkeypatch):
    from voicebridge import mcp_servers as MS
    from voicebridge import qubit_migration as HM

    cfg = _cfg(tmp_path, monkeypatch)
    home = tmp_path / "hermes"
    _source(home)

    report = HM._import_mcp_servers(cfg, home)
    assert set(report["imported"]) == {"graft", "webhook"}
    # A definition with neither command nor url is unsupported, not fabricated.
    assert any(u["name"] == "broken" for u in report["unsupported"])

    servers = {s["name"]: s for s in MS.list_servers(cfg)["servers"]}
    assert servers["graft"]["transport"] == "stdio"
    assert servers["graft"]["enabled"] is True
    assert servers["webhook"]["transport"] == "http"
    # The ${VAR} reference was preserved; the raw secret was dropped, never copied.
    assert servers["webhook"]["env"].get("API_KEY") == "env:MY_MCP_KEY"
    assert "RAW_SECRET" not in servers["webhook"]["env"]
    assert "sk-live-DO-NOT-COPY" not in str(servers["webhook"])

    # Idempotent: a second run imports nothing new and reports the conflicts.
    again = HM._import_mcp_servers(cfg, home)
    assert again["imported"] == []
    assert set(again["skipped"]) == {"graft", "webhook"}

    # A Qubit-owned record is never overwritten by a re-import.
    MS.upsert(cfg, "graft", {"transport": "stdio", "command": "graft",
                             "args": ["custom"], "enabled": False})
    HM._import_mcp_servers(cfg, home)
    kept = {s["name"]: s for s in MS.list_servers(cfg)["servers"]}["graft"]
    assert kept["args"] == ["custom"]
    assert kept["enabled"] is False


def test_catalog_report_counts_source_and_destination(tmp_path, monkeypatch):
    from voicebridge import qubit_migration as HM
    from voicebridge import qubit_runtime

    cfg = _cfg(tmp_path, monkeypatch)
    home = tmp_path / "hermes"
    _source(home)
    (home / "skills" / "alpha").mkdir(parents=True)
    (home / "skills" / "alpha" / "SKILL.md").write_text("# alpha")
    (home / "skills" / "beta").mkdir(parents=True)
    (home / "skills" / "beta" / "SKILL.md").write_text("# beta")
    (home / "plugins" / "p1").mkdir(parents=True)

    dest = qubit_runtime.runtime_home_for_cfg(cfg)
    (dest / "skills" / "alpha").mkdir(parents=True)
    (dest / "skills" / "alpha" / "SKILL.md").write_text("# alpha")

    report = HM.catalog_report(cfg, home)
    assert report["skills"]["source"] == 2
    assert report["skills"]["destination"] == 1
    assert report["plugins"]["source"] == 1
    assert report["mcp"]["source"] == 3


def test_three_surfaces_share_one_skill_catalog(tmp_path, monkeypatch):
    from voicebridge import config as C
    from voicebridge import qubit_compat as HC
    from voicebridge import skill_catalog

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    qhome = tmp_path / "qhome"
    skill_dir = qhome / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: A demo skill\n---\nbody\n")
    monkeypatch.setenv("QUBIT_HOME", str(qhome))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    cfg = C.Config.load(p)

    entries = skill_catalog.catalog_entries(cfg)
    names = {e["name"] for e in entries}
    assert "demo" in names

    # Dashboard route returns exactly the shared catalog.
    status, body = HC.h_skills(cfg, {}, {}, {})
    assert status == 200
    assert {s["name"] for s in body} == names

    # CLI/voice merged view covers the same catalog (permission records + catalog).
    view = skill_catalog.merged_store_view(cfg)
    assert names <= {r["skill_id"] for r in view}


# ---------------------------------------------------------------------------
# Objective B remainder — skill/plugin enabled-state import
# ---------------------------------------------------------------------------
def _source_skill_config(home: Path, body: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(body, encoding="utf-8")


def _read_yaml(path: Path):
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_skill_state_import_unions_without_dropping_existing(
        tmp_path, monkeypatch):
    from voicebridge import qubit_migration as HM
    from voicebridge import qubit_runtime

    cfg = _cfg(tmp_path, monkeypatch)
    home = tmp_path / "hermes"
    _source_skill_config(
        home,
        "skills:\n"
        "  disabled: [alpha, beta]\n"
        "  platform_disabled:\n"
        "    telegram: [gamma]\n")
    dest = qubit_runtime.runtime_home_for_cfg(cfg)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "config.yaml").write_text(
        "model:\n  default: keep-me\n"
        "skills:\n  disabled:\n    - existing-skill\n", encoding="utf-8")

    report = HM._import_skill_state(cfg, home)
    assert report["state"] == "OK"
    assert report["source_disabled"] == ["alpha", "beta", "gamma"]
    assert report["added"] == ["alpha", "beta", "gamma"]
    assert report["already_present"] == []
    assert report["platform_added"] == ["gamma"]

    data = _read_yaml(dest / "config.yaml")
    # UNION: the pre-existing destination entry survives; unrelated keys too.
    assert data["skills"]["disabled"] == [
        "alpha", "beta", "existing-skill", "gamma"]
    assert data["model"]["default"] == "keep-me"


def test_skill_state_import_is_idempotent(tmp_path, monkeypatch):
    from voicebridge import qubit_migration as HM
    from voicebridge import qubit_runtime

    cfg = _cfg(tmp_path, monkeypatch)
    home = tmp_path / "hermes"
    _source_skill_config(home, "skills:\n  disabled: [alpha, beta]\n")
    dest = qubit_runtime.runtime_home_for_cfg(cfg)

    first = HM._import_skill_state(cfg, home)
    assert first["state"] == "OK"
    assert first["added"] == ["alpha", "beta"]
    before = (dest / "config.yaml").read_bytes()

    second = HM._import_skill_state(cfg, home)
    assert second["state"] == "NOOP"
    assert second["added"] == []
    assert second["already_present"] == ["alpha", "beta"]
    # A no-op run must not touch the file at all.
    assert (dest / "config.yaml").read_bytes() == before


def test_skill_state_import_corrupt_destination_is_left_untouched(
        tmp_path, monkeypatch):
    from voicebridge import qubit_migration as HM
    from voicebridge import qubit_runtime

    cfg = _cfg(tmp_path, monkeypatch)
    home = tmp_path / "hermes"
    _source_skill_config(home, "skills:\n  disabled: [alpha]\n")
    dest = qubit_runtime.runtime_home_for_cfg(cfg)
    dest.mkdir(parents=True, exist_ok=True)
    corrupt = "skills: [unclosed\nmodel: {unclosed\n"
    (dest / "config.yaml").write_text(corrupt, encoding="utf-8")

    report = HM._import_skill_state(cfg, home)
    assert report["state"] == "SKIPPED"
    assert report["code"] == "SKILL_STATE_DEST_UNREADABLE"
    assert report["added"] == []
    # Data loss guard: the unreadable file is byte-for-byte untouched.
    assert (dest / "config.yaml").read_text(encoding="utf-8") == corrupt


def test_skill_state_import_creates_missing_destination(tmp_path, monkeypatch):
    from voicebridge import qubit_migration as HM
    from voicebridge import qubit_runtime

    cfg = _cfg(tmp_path, monkeypatch)
    home = tmp_path / "hermes"
    _source_skill_config(
        home, "skills:\n  disabled: [alpha]\n  platform_disabled: [beta]\n")
    dest = qubit_runtime.runtime_home_for_cfg(cfg)
    assert not (dest / "config.yaml").is_file()

    report = HM._import_skill_state(cfg, home)
    assert report["state"] == "OK"
    assert (dest / "config.yaml").is_file()
    data = _read_yaml(dest / "config.yaml")
    assert data["skills"]["disabled"] == ["alpha", "beta"]


def test_skill_state_import_without_yaml_is_typed_skip(tmp_path, monkeypatch):
    from voicebridge import qubit_migration as HM

    cfg = _cfg(tmp_path, monkeypatch)
    home = tmp_path / "hermes"
    _source_skill_config(home, "skills:\n  disabled: [alpha]\n")
    monkeypatch.setitem(sys.modules, "yaml", None)

    report = HM._import_skill_state(cfg, home)
    assert report["state"] == "SKIPPED"
    assert report["code"] == "SKILL_STATE_NO_YAML"
    assert report["added"] == []


def test_plugin_state_import_is_typed_unsupported(tmp_path, monkeypatch):
    from voicebridge import qubit_migration as HM

    cfg = _cfg(tmp_path, monkeypatch)
    home = tmp_path / "hermes"
    (home / "plugins" / "achievements").mkdir(parents=True)
    (home / "plugins" / "achievements" / "state.json").write_text(
        json.dumps({"enabled": False, "unlocks": {}}), encoding="utf-8")

    report = HM._import_plugin_state(cfg, home)
    assert report["state"] == "UNSUPPORTED"
    assert report["code"] == "PLUGIN_STATE_NO_PUBLIC_API"
    assert report["recorded"] == []
    assert {"name": "achievements", "state": "DISABLED"} in \
        report["source_plugins"]


def test_active_skill_is_reported_enabled(tmp_path, monkeypatch):
    """The permissioned store's enabled state is ``active`` (enable_skill sets
    it); the merged catalog must report it enabled, not disabled. A permissioned
    record is only a catalog skill when its SKILL.md resolves on disk, so seed a
    real file (the manifest-only lane is no longer a visible catalog row)."""
    from voicebridge import config as C
    from voicebridge import permissions as PERM
    from voicebridge import skill_catalog

    qhome = tmp_path / "qhome"
    demo = qhome / "skills" / "demo"
    demo.mkdir(parents=True)
    (demo / "SKILL.md").write_text(
        "---\nname: demo\ndescription: d\n---\nbody\n", encoding="utf-8")
    monkeypatch.setenv("QUBIT_HOME", str(qhome))

    class _Rec:
        def __init__(self, status):
            self._d = {"skill_id": "demo", "status": status,
                       "manifest": {"description": "d", "category": "qubit"}}

        def to_dict(self):
            return dict(self._d)

    class _Store:
        def skills(self):
            return [_Rec("active")]

    monkeypatch.setattr(PERM, "permissions_store", lambda _cfg: _Store())
    entries = {e["name"]: e for e in skill_catalog.catalog_entries(_Cfg({}))}
    assert entries["demo"]["enabled"] is True
    assert entries["demo"]["origin"] == "permissioned"


class _Cfg:
    def __init__(self, data=None):
        self._d = data or {}

    def get(self, *path, default=None):
        node = self._d
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node
