"""9U — invocation closure: every enabled skill/plugin has a real path or a reason.

Complements ``test_invocation_matrix9u.py`` with the permissioned-store case:
an operator-enabled permissioned skill is NOT on disk, so the vendored scanner
registers no slash command for it — yet it IS invocable by id through the one
canonical ``skills`` operation (voice ``skills show/enable/disable <id>``, CLI
``qubit skills show <id>``, dashboard ``GET /api/skills`` /
``/api/skills/content``). The matrix must classify it ``reachable`` with that
id-keyed management invocation, not ``no-invocation``.

It also asserts the authoritative closure self-check: every in-scope item is
either reachable through a concrete invocation or carries a typed reason, and
the matrix never fabricates reachability when no surface exists.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DIGEST = "b" * 64


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


def _manifest(skill_id: str) -> "P.SkillManifest":
    from voicebridge import permissions as P

    return P.SkillManifest(
        skill_id=skill_id,
        version="1.0.0",
        name=skill_id,
        description=f"permissioned {skill_id}",
        source="https://example.com/repo",
        revision="c" * 40,
        license="MIT",
        content_hash=DIGEST,
        capabilities=("skills.run",),
        scopes=("skill:read",),
        tier=P.Tier.READ_ONLY,
        sensitivity="internal",
        approval_policy="confirm",
        timeout_s=30.0,
        resource_limits={"memory_mb": 64},
        audit_events=("skill.install",),
        compatibility="qubit>=1",
        rollback="remove staged record",
    )


def _seed_permissioned_skill(cfg, skill_id: str, *, enable: bool = True) -> None:
    """Install a permissioned skill WITH a real SKILL.md.

    A permissioned record is a visible catalog skill only when a readable
    SKILL.md resolves on disk (the manifest-only lane is not a catalog row); the
    reachability contract below is about real permissioned skills.
    """
    import os

    from voicebridge.permissions import permissions_store

    qhome = Path(os.environ.get("QUBIT_HOME", ""))
    if str(qhome):
        d = qhome / "skills" / skill_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {skill_id}\ndescription: seeded {skill_id}\n---\nbody\n",
            encoding="utf-8")
    store = permissions_store(cfg)
    store.install_skill(_manifest(skill_id), requested_by="test:user",
                        confirmed=True)
    if enable:
        store.enable_skill(skill_id, requested_by="test:user", confirmed=True)


def _seed_catalog_skill(qhome: Path, name: str) -> None:
    d = qhome / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: seeded {name}\n---\nbody\n",
        encoding="utf-8")


def test_permissioned_skill_is_reachable_by_id_via_management(
        tmp_path, monkeypatch):
    """A permissioned-store skill with no registered slash command resolves to
    the canonical ``skills`` operation by id — reachable, not no-invocation."""
    from voicebridge import invocation_matrix as IM

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_permissioned_skill(cfg, "acme_skill")

    surfaces = IM.surface_index()
    # Simulate a scanner miss (the same seam test_catalog_only_... uses): with no
    # slash command the permissioned id-keyed management path must carry it.
    surfaces["skill_commands"] = {
        k: v for k, v in surfaces["skill_commands"].items()
        if str(v.get("name")) != "acme_skill"}
    assert not any(str(info.get("name")) == "acme_skill"
                   for info in surfaces["skill_commands"].values())

    out = IM.build(cfg, surfaces=surfaces)
    row = next(r for r in out["rows"] if r["id"] == "acme_skill")
    assert row["kind"] == "skill"
    assert row["origin"] == "permissioned"
    assert row["enabled"] is True and row["in_scope"] is True
    assert row["disposition"] == "reachable"
    assert row["invocation"] == "skills.show:acme_skill"
    assert row["invocation_kind"] == "management"
    assert row["text"] is False
    # The id-keyed management invocation resolves on the live surfaces.
    assert row["voice"] and row["cli"] and row["dashboard"]
    assert out["closure_errors"] == []


def test_staged_permissioned_skill_is_out_of_scope(tmp_path, monkeypatch):
    """A staged (installed-but-not-enabled) skill is not in scope; enabling it
    moves it in scope with the same id-keyed invocation."""
    from voicebridge import invocation_matrix as IM

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_permissioned_skill(cfg, "staged_skill", enable=False)

    out = IM.build(cfg)
    row = next(r for r in out["rows"] if r["id"] == "staged_skill")
    assert row["enabled"] is False
    assert row["in_scope"] is False
    assert "staged_skill" not in out["in_scope"]
    assert out["closure_errors"] == []


def test_catalog_only_skill_without_slash_is_no_invocation(
        tmp_path, monkeypatch):
    """A bundled on-disk skill the scanner did not register (no slash) has no
    per-item invocation: ``skills show`` reads only the permissioned store, so
    it is honestly ``no-invocation`` with a typed reason."""
    from voicebridge import invocation_matrix as IM

    cfg = _cfg(tmp_path, monkeypatch)
    qhome = Path(tmp_path / "qhome")
    _seed_catalog_skill(qhome, "bundled_only")

    surfaces = IM.surface_index()
    surfaces["skill_commands"] = {}  # simulate a scanner miss for this skill
    out = IM.build(cfg, surfaces=surfaces)

    row = next(r for r in out["rows"] if r["id"] == "bundled_only")
    assert row["origin"] == "catalog"
    assert row["disposition"] == "no-invocation"
    assert "SKILL_MANAGEMENT_ONLY" in row["reason"]
    assert row["invocation"] == ""
    assert out["closure_errors"] == []


def test_no_surfaces_never_yields_reachability(tmp_path, monkeypatch):
    """With every surface gone, a permissioned skill is NOT reachable: the
    management invocation needs a real surface to resolve against."""
    from voicebridge import invocation_matrix as IM

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_permissioned_skill(cfg, "acme_skill")

    empty = {
        "voice_backends": set(), "voice_entries": {}, "cli_families": set(),
        "dashboard_ops": set(), "dashboard_routes": set(),
        "skill_commands": {},
    }
    out = IM.build(cfg, surfaces=empty)
    row = next(r for r in out["rows"] if r["id"] == "acme_skill")
    assert row["disposition"] == "no-invocation"
    assert row["reason"] == "NO_SURFACE_BINDING"
    assert row["invocation"] == ""
    assert out["reachable"] == []
    assert out["closure_errors"] == []


def test_closure_errors_is_empty_across_kinds(tmp_path, monkeypatch):
    """Every in-scope item across skills/plugins/MCP is closed: reachable with
    a concrete invocation, or a typed reason. The self-check must be empty."""
    from voicebridge import invocation_matrix as IM
    from voicebridge import mcp_servers as MS
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_permissioned_skill(cfg, "acme_skill")
    _seed_catalog_skill(Path(tmp_path / "qhome"), "demo")

    PR.register(cfg, manifest={"id": "acme.manifest", "kind": "tool",
                               "entrypoint": "acme.plugin:main", "digest": DIGEST,
                               "label": "Acme manifest", "version": "1.0.0",
                               "capabilities": ["run"]}, confirmed=True)
    PR.bind_handler(cfg, manifest={"id": "acme.bound", "kind": "tool",
                                   "entrypoint": "acme.plugin:main",
                                   "digest": DIGEST, "label": "Acme bound",
                                   "version": "1.0.0", "capabilities": ["run"]},
                    handler=lambda args: {"ok": True}, confirmed=True)
    MS.upsert(cfg, "fs", {"transport": "stdio", "command": "npx",
                          "args": ["-y", "server-fs"]})
    MS.set_enabled(cfg, "fs", True)

    out = IM.build(cfg)
    assert out["closure_errors"] == []
    assert out["summary"]["closure_errors"] == 0

    by_id = {r["id"]: r for r in out["rows"]}
    assert by_id["acme_skill"]["disposition"] == "reachable"
    assert by_id["acme.bound"]["disposition"] == "reachable"
    assert by_id["acme.manifest"]["disposition"] == "unsupported"
    assert by_id["acme.manifest"]["reason"]
    assert by_id["fs"]["disposition"] == "unsupported"
    assert by_id["fs"]["reason"]

    # Every reachable row names a concrete invocation the closure check accepts.
    for row in out["rows"]:
        if row["in_scope"] and row["disposition"] == "reachable":
            assert row["invocation"]


def test_closure_self_check_flags_a_fabricated_reachability():
    """A reachable row without an invocation is a matrix bug; the closure
    self-check must surface it (fail-closed anti-fabrication)."""
    from voicebridge import invocation_matrix as IM

    bad = [{
        "kind": "skill", "id": "x", "enabled": True, "in_scope": True,
        "origin": "permissioned", "canonical_operation": "skills",
        "invocation": "", "invocation_kind": "management",
        "surfaces": ["cli"], "disposition": "reachable", "reason": "",
    }]
    errors = IM._closure_errors(bad)
    assert errors == [{"id": "x", "error": "REACHABLE_WITHOUT_INVOCATION"}]

    gap = [{
        "kind": "skill", "id": "y", "enabled": True, "in_scope": True,
        "origin": "catalog", "canonical_operation": "skills",
        "invocation": "", "invocation_kind": "none",
        "surfaces": [], "disposition": "no-invocation", "reason": "",
    }]
    assert IM._closure_errors(gap) == [
        {"id": "y", "error": "GAP_WITHOUT_TYPED_REASON"}]


def test_catalog_entries_carry_origin(tmp_path, monkeypatch):
    """The shared catalog marks each entry's source so the matrix can reason
    about which invocation path applies."""
    from voicebridge import config as C
    from voicebridge import skill_catalog

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    qhome = tmp_path / "qhome"
    monkeypatch.setenv("QUBIT_HOME", str(qhome))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    cfg = C.Config.load(p)

    _seed_permissioned_skill(cfg, "acme_skill")
    _seed_catalog_skill(qhome, "bundled_only")

    entries = {e["name"]: e for e in skill_catalog.catalog_entries(cfg)}
    assert entries["acme_skill"]["origin"] == "permissioned"
    assert entries["acme_skill"]["enabled"] is True
    assert entries["bundled_only"]["origin"] == "catalog"


def test_matrix_stays_read_only_with_permissioned_store(tmp_path, monkeypatch):
    """Reading the permissioned store must not mutate it."""
    from voicebridge import invocation_matrix as IM

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_permissioned_skill(cfg, "acme_skill")

    from voicebridge.permissions import permissions_store

    path = Path(str(permissions_store(cfg)._path))
    before = path.read_bytes()

    out = IM.build(cfg)
    assert any(r["id"] == "acme_skill" for r in out["rows"])
    assert path.read_bytes() == before, "invocation matrix must not mutate store"
