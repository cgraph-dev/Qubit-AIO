"""W-BRAND skills lane — de-brand + legacy-name rename migration.

Covers the two halves of the skills brand closure:

* :func:`agent.skill_utils.debrand_skill_text` removes upstream assistant
  branding from shipped skill prose while preserving load-bearing
  wire/provenance/legal identifiers and collapsing the upstream docs host to
  the Qubit-owned docs endpoint.
* :func:`agent.skill_utils.migrate_legacy_skill_dirs` renames legacy-named
  skill dirs (non-destructive read-old/write-new) and retires the leftover
  frame an interrupted rename leaves behind, never deleting content.
* The read-old fallback resolves a legacy skill name to its Qubit name through
  the production ``skill_view`` path.

Hermetic: a tmp skills tree only; no network, no live QUBIT_HOME.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "qubit_runtime"))


# --------------------------------------------------------- debrand_skill_text
def test_prose_words_rewritten_but_wire_identifiers_preserved():
    from agent.skill_utils import debrand_skill_text

    text = (
        "Hermes runs the agent. Nous Research built it. OpenClaw is the gateway.\n"
        "Wire: X-Hermes-Session-Token and __HERMES_SESSION_TOKEN__ stay.\n"
        "Event: hermes.task_run.started stays. Service: hermes-gateway stays.\n"
    )
    new = debrand_skill_text(text, is_doc=True)
    assert "Hermes runs" not in new and "Qubit runs" in new
    assert "Nous Research" not in new and "Qubit Labs" in new
    assert "OpenClaw is" not in new
    # Load-bearing identifiers survive verbatim.
    for kept in ("X-Hermes-Session-Token", "__HERMES_SESSION_TOKEN__",
                 "hermes.task_run.started", "hermes-gateway"):
        assert kept in new, kept


def test_upstream_docs_host_collapsed_to_qubit_endpoint():
    from agent.skill_utils import debrand_skill_text

    text = (
        "Docs: https://hermes-agent.nousresearch.com/docs/reference/tools\n"
        "Install: https://hermes-agent.nousresearch.com/install.sh\n"
        "Repo: https://github.com/NousResearch/hermes-agent\n"
        "Portal: https://portal.nousresearch.com\n"
    )
    new = debrand_skill_text(text, is_doc=True)
    assert "hermes-agent.nousresearch.com" not in new
    assert "/agent/openapi.json" in new
    # The rendered docs site is rewritten; the real provenance source stays.
    assert "https://github.com/NousResearch/hermes-agent" in new
    assert "https://portal.nousresearch.com" in new


def test_debrand_is_idempotent():
    from agent.skill_utils import debrand_skill_text

    text = "Hermes at https://hermes-agent.nousresearch.com/docs/ runs Nous Research.\n"
    once = debrand_skill_text(text, is_doc=True)
    assert debrand_skill_text(once, is_doc=True) == once


def test_legal_attribution_line_is_preserved():
    from agent.skill_utils import debrand_skill_text

    text = "Copyright (c) 2026 Nous Research. All rights reserved.\n"
    assert debrand_skill_text(text, is_doc=True) == text


# ------------------------------------------------- migrate_legacy_skill_dirs
def _seed_skill(base: Path, rel: str, name: str) -> Path:
    d = base / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test.\n---\n\nBody.\n")
    return d


def test_rename_moves_legacy_dir_and_rewrites_frontmatter(tmp_path):
    from agent.skill_utils import migrate_legacy_skill_dirs

    base = tmp_path / "skills"
    _seed_skill(base, "autonomous-ai-agents/hermes-agent", "hermes-agent")
    report = migrate_legacy_skill_dirs(base, apply=True)
    assert not (base / "autonomous-ai-agents/hermes-agent").exists()
    moved = base / "autonomous-ai-agents/qubit-agent"
    assert moved.is_dir()
    assert "name: qubit-agent" in (moved / "SKILL.md").read_text()
    assert any(r["old"].endswith("hermes-agent") for r in report["renamed"])


def test_retire_archives_leftover_frame_without_losing_content(tmp_path):
    from agent.skill_utils import migrate_legacy_skill_dirs

    base = tmp_path / "skills"
    _seed_skill(base, "autonomous-ai-agents/qubit-agent", "qubit-agent")
    frame = _seed_skill(
        base, "autonomous-ai-agents/hermes-agent", "hermes-agent")
    frame_content = (frame / "SKILL.md").read_text()
    report = migrate_legacy_skill_dirs(base, apply=True, retire=True)
    assert not frame.exists(), "leftover legacy frame must be retired"
    archived = base / ".archive/hermes-agent/SKILL.md"
    assert archived.is_file(), "retired frame must be archived, not deleted"
    assert archived.read_text() == frame_content
    assert report["retired"], "retire must be reported"


def test_retire_copies_backup_before_moving(tmp_path):
    from agent.skill_utils import migrate_legacy_skill_dirs

    base = tmp_path / "skills"
    _seed_skill(base, "autonomous-ai-agents/qubit-agent", "qubit-agent")
    _seed_skill(base, "autonomous-ai-agents/hermes-agent", "hermes-agent")
    backup = tmp_path / "backup"
    migrate_legacy_skill_dirs(base, apply=True, retire=True, backup_dir=backup)
    assert (backup / "hermes-agent/SKILL.md").is_file()


def test_migrate_usage_json_key(tmp_path):
    from agent.skill_utils import migrate_legacy_skill_dirs

    base = tmp_path / "skills"
    _seed_skill(base, "autonomous-ai-agents/hermes-agent", "hermes-agent")
    (base / ".usage.json").write_text(
        '{"hermes-agent": {"used": 1}}', encoding="utf-8")
    migrate_legacy_skill_dirs(base, apply=True)
    import json
    data = json.loads((base / ".usage.json").read_text())
    assert "qubit-agent" in data and "hermes-agent" not in data


# ------------------------------------------------------ read-old resolution
def test_resolve_legacy_skill_name_maps_known_and_passes_unknown():
    from agent.skill_utils import resolve_legacy_skill_name

    assert resolve_legacy_skill_name("hermes-agent") == "qubit-agent"
    assert resolve_legacy_skill_name("openclaw-migration") == "qubit-migration"
    assert resolve_legacy_skill_name("qubit-agent") == "qubit-agent"
    assert resolve_legacy_skill_name("brand-new-skill") == "brand-new-skill"


def test_skill_view_resolves_legacy_name_via_read_old_fallback(tmp_path, monkeypatch):
    """The production skill_view path accepts the legacy name after rename."""
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    (tmp_path / "qhome/skills/demo").mkdir(parents=True)
    (tmp_path / "qhome/skills/demo/SKILL.md").write_text(
        "---\nname: demo\ndescription: test.\n---\n\nBody.\n")

    from tools import skills_tool as ST
    import json

    # Seed a renamed skill the same way the migration would.
    from agent.skill_utils import migrate_legacy_skill_dirs
    _seed_skill(tmp_path / "qhome/skills",
                "autonomous-ai-agents/hermes-agent", "hermes-agent")
    migrate_legacy_skill_dirs(tmp_path / "qhome/skills", apply=True)

    result = json.loads(ST.skill_view("hermes-agent"))
    assert result.get("success") is True, result
    assert result.get("name") == "qubit-agent"
