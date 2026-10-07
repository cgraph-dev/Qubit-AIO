"""Regression: the skills catalog must reach the vendored provenance/usage helpers.

``voicebridge/skill_catalog.catalog_entries`` imports ``_read_bundled_names`` and
``load_usage`` from the vendored ``tools.skill_usage`` inside a ``try`` whose
``except`` degrades provenance to ``agent`` and usage to ``0``. A sync renamed
the helper (``_read_bundled_manifest_names`` -> ``_read_bundled_names``) and the
overlay kept importing the old name, so the ImportError was swallowed and EVERY
skill silently reported ``provenance == "agent"`` / ``usage == 0`` on the live
``/api/skills`` payload.

Behavioral: seed a bundled-manifest name + a usage record and assert the catalog
reflects them. On the stale import this fails (bundled -> agent, usage 0).
"""
from __future__ import annotations

import json


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    qhome = tmp_path / "qhome"
    qhome.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(qhome))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p), qhome


def _seed_skill(qhome, name: str) -> None:
    d = qhome / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: A test skill.\n---\n\nBody.\n")


def test_catalog_marks_a_bundled_manifest_skill_bundled(tmp_path, monkeypatch):
    from voicebridge import skill_catalog as SC

    cfg, qhome = _cfg(tmp_path, monkeypatch)
    _seed_skill(qhome, "demo")
    (qhome / "skills" / ".bundled_manifest").write_text("demo:deadbeef\n")

    names = {e["name"]: e for e in SC.catalog_entries(cfg)}
    assert names["demo"]["provenance"] == "bundled"


def test_catalog_reports_usage_from_the_usage_store(tmp_path, monkeypatch):
    from voicebridge import skill_catalog as SC

    cfg, qhome = _cfg(tmp_path, monkeypatch)
    _seed_skill(qhome, "demo")
    (qhome / "skills" / ".usage.json").write_text(json.dumps(
        {"demo": {"use_count": 5, "view_count": 2, "patch_count": 1}}))

    names = {e["name"]: e for e in SC.catalog_entries(cfg)}
    assert names["demo"]["usage"] == 8


def test_catalog_keeps_human_authored_skill_as_agent(tmp_path, monkeypatch):
    """The provenance signal must stay discriminating, not just non-empty."""
    from voicebridge import skill_catalog as SC

    cfg, qhome = _cfg(tmp_path, monkeypatch)
    _seed_skill(qhome, "mine")

    names = {e["name"]: e for e in SC.catalog_entries(cfg)}
    assert names["mine"]["provenance"] == "agent"
