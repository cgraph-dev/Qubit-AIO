"""9U — invocation matrix: every enabled skill/plugin/MCP item is classified.

Seeds a couple of skills, plugins, and MCP servers hermetically, runs the
read-only traverser, and asserts each enabled item is either ``reachable`` with
at least one concrete surface or a typed ``no-invocation`` / ``unsupported``.
It also asserts the matrix never claims reachability for a fabricated binding.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DIGEST = "a" * 64


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    qhome = tmp_path / "qhome"
    monkeypatch.setenv("QUBIT_HOME", str(qhome))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seed_skill(qhome: Path, name: str) -> None:
    d = qhome / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: seeded {name}\n---\nbody\n",
        encoding="utf-8")


def _seed(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    qhome = Path(tmp_path / "qhome")
    _seed_skill(qhome, "demo")
    _seed_skill(qhome, "notes")

    from voicebridge import mcp_servers as MS
    from voicebridge import plugin_runtime as PR

    PR.register(
        cfg,
        manifest={"id": "acme.manifest", "kind": "tool",
                  "entrypoint": "acme.plugin:main", "digest": DIGEST,
                  "label": "Acme manifest", "version": "1.0.0",
                  "capabilities": ["run"]},
        confirmed=True)
    PR.bind_handler(
        cfg,
        manifest={"id": "acme.bound", "kind": "tool",
                  "entrypoint": "acme.plugin:main", "digest": DIGEST,
                  "label": "Acme bound", "version": "1.0.0",
                  "capabilities": ["run"]},
        handler=lambda args: {"ok": True},
        confirmed=True)
    MS.upsert(cfg, "fs", {"transport": "stdio", "command": "npx",
                          "args": ["-y", "server-fs"]})
    MS.set_enabled(cfg, "fs", True)
    return cfg


def test_matrix_classifies_every_enabled_item(tmp_path, monkeypatch):
    from voicebridge import invocation_matrix as IM

    cfg = _seed(tmp_path, monkeypatch)
    out = IM.build(cfg)

    assert out["schema"] == IM.SCHEMA
    # The declared bindings are real, not fabricated.
    assert out["dangling_bindings"] == []

    by_id = {r["id"]: r for r in out["rows"]}
    # Every seeded skill is enabled and has a real slash invocation.
    for name in ("demo", "notes"):
        assert by_id[name]["kind"] == "skill"
        assert by_id[name]["enabled"] is True
        assert by_id[name]["disposition"] == "reachable"
        assert by_id[name]["text"] is True

    # A handler-bound plugin is invocable; a manifest-only plugin is not.
    assert by_id["acme.bound"]["disposition"] == "reachable"
    assert by_id["acme.bound"]["invocation"] == "plugin_runtime.invoke:acme.bound"
    assert by_id["acme.manifest"]["disposition"] == "unsupported"
    assert by_id["acme.manifest"]["reason"]

    # A configured MCP server has no client-side invocation path.
    assert by_id["fs"]["kind"] == "mcp_server"
    assert by_id["fs"]["disposition"] == "unsupported"
    assert by_id["fs"]["reason"]

    # Every in-scope item carries a valid disposition and, when reachable, a
    # concrete surface; otherwise a typed reason.
    for row in out["rows"]:
        if not row["in_scope"]:
            continue
        assert row["disposition"] in IM.DISPOSITIONS
        assert row["canonical_operation"]
        if row["disposition"] == "reachable":
            assert row["surfaces"] or row["text"]
        else:
            assert row["reason"]
    assert out["summary"]["in_scope"] == len(out["in_scope"])


def test_management_only_skill_is_no_invocation(tmp_path, monkeypatch):
    from voicebridge import invocation_matrix as IM

    cfg = _seed(tmp_path, monkeypatch)
    surfaces = IM.surface_index()
    assert surfaces["skill_commands"], "seeded skills must be scanned"

    # Drop the slash invocation index but keep the management surfaces: the
    # skill can still be listed/enabled, yet has no per-skill invocation.
    management_only = dict(surfaces)
    management_only["skill_commands"] = {}
    out = IM.build(cfg, surfaces=management_only)

    skills = [r for r in out["rows"] if r["kind"] == "skill"]
    assert skills
    for row in skills:
        assert row["surfaces"], "management surfaces remain reachable"
        assert row["disposition"] == "no-invocation"
        assert "SKILL_MANAGEMENT_ONLY" in row["reason"]


def test_matrix_never_claims_reachability_for_a_fabricated_operation(
        tmp_path, monkeypatch):
    from voicebridge import invocation_matrix as IM

    cfg = _seed(tmp_path, monkeypatch)
    surfaces = IM.surface_index()
    # Sanity: the shipped bindings resolve against the live registries.
    assert IM.verify_bindings(surfaces) == []

    # Fabricate the plugin binding's surface keys: the handler-bound plugin must
    # become no-invocation, never reachable, and the binding must be reported.
    monkeypatch.setitem(IM.BINDINGS, "plugin", {
        "operation": "plugin_runtime",
        "voice": "NO_SUCH_VOICE_BACKEND",
        "cli": "NO_SUCH_CLI_FAMILY",
        "dashboard": "/no/such/route",
    })
    out = IM.build(cfg, surfaces=surfaces)

    assert any(d["kind"] == "plugin" for d in out["dangling_bindings"])
    bound = next(r for r in out["rows"] if r["id"] == "acme.bound")
    assert bound["voice"] is False
    assert bound["cli"] is False
    assert bound["dashboard"] is False
    assert bound["disposition"] == "no-invocation"


def test_empty_surface_index_yields_no_reachability(tmp_path, monkeypatch):
    from voicebridge import invocation_matrix as IM

    cfg = _seed(tmp_path, monkeypatch)
    empty = {
        "voice_backends": set(), "voice_entries": {}, "cli_families": set(),
        "dashboard_ops": set(), "dashboard_routes": set(),
        "skill_commands": {},
    }
    out = IM.build(cfg, surfaces=empty)

    assert IM.verify_bindings(empty), "all bindings must be reported missing"
    assert out["reachable"] == []
    assert all(r["disposition"] != "reachable" for r in out["rows"])


def test_matrix_is_read_only(tmp_path, monkeypatch):
    from voicebridge import invocation_matrix as IM

    cfg = _seed(tmp_path, monkeypatch)
    state_dir = Path(cfg.state_dir())
    before = sorted(str(p) for p in state_dir.rglob("*"))

    out = IM.build(cfg)
    assert out["rows"]

    after = sorted(str(p) for p in state_dir.rglob("*"))
    assert after == before, "invocation matrix must not mutate state"
