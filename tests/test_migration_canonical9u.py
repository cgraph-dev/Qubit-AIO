"""Owner decision #3 — `qubit-migration` is the canonical migration skill.

Covers the four required guarantees:

* the canonical `qubit-migration` skill/entry point resolves first;
* the retired `openclaw-migration` frame is a read-old compatibility alias that
  forwards to the canonical implementation (with a loud deprecation note);
* no user data is deleted — the legacy frame is archived, never removed; and
* the canonical surface is brand-clean (names `qubit-migration`, no standalone
  upstream prose token).

Hermetic: tmp skills trees + monkeypatched module paths; no live QUBIT_HOME.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "qubit_runtime"))


# --------------------------------------------------------------- entry points
def test_repo_canonical_script_resolves_first():
    """The real repo tree resolves the canonical `qubit-migration` script."""
    from qubit_cli import claw

    found = claw._find_migration_script()
    assert found is not None, "migration script must resolve from the repo tree"
    assert found.name == "openclaw_to_hermes.py"
    assert "qubit-migration" in found.parts, found
    assert not claw._is_legacy_migration_script(found)


def test_canonical_wins_when_both_frames_exist(tmp_path, monkeypatch):
    from qubit_cli import claw

    canon = tmp_path / "canon" / "openclaw_to_hermes.py"
    legacy = tmp_path / "legacy" / "openclaw_to_hermes.py"
    canon.parent.mkdir(parents=True)
    legacy.parent.mkdir(parents=True)
    canon.write_text("# canonical\n")
    legacy.write_text("# legacy\n")

    monkeypatch.setattr(claw, "_OPENCLAW_SCRIPT", canon)
    monkeypatch.setattr(claw, "_OPENCLAW_SCRIPT_INSTALLED", tmp_path / "nope1")
    monkeypatch.setattr(claw, "_LEGACY_OPENCLAW_SCRIPT", legacy)
    monkeypatch.setattr(claw, "_LEGACY_OPENCLAW_SCRIPT_INSTALLED", tmp_path / "nope2")

    assert claw._find_migration_script() == canon
    assert claw._is_legacy_migration_script(canon) is False


def test_legacy_frame_is_read_old_fallback_only(tmp_path, monkeypatch):
    """With no canonical copy, the retired frame is used (never deleted)."""
    from qubit_cli import claw

    legacy = tmp_path / "legacy" / "openclaw_to_hermes.py"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# legacy\n")

    monkeypatch.setattr(claw, "_OPENCLAW_SCRIPT", tmp_path / "nope1")
    monkeypatch.setattr(claw, "_OPENCLAW_SCRIPT_INSTALLED", tmp_path / "nope2")
    monkeypatch.setattr(claw, "_LEGACY_OPENCLAW_SCRIPT", legacy)
    monkeypatch.setattr(claw, "_LEGACY_OPENCLAW_SCRIPT_INSTALLED", tmp_path / "nope3")

    found = claw._find_migration_script()
    assert found == legacy
    assert claw._is_legacy_migration_script(found) is True


def test_deprecation_note_names_canonical(capsys, tmp_path, monkeypatch):
    from qubit_cli import claw

    legacy = tmp_path / "legacy" / "openclaw_to_hermes.py"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# legacy\n")
    monkeypatch.setattr(claw, "_LEGACY_OPENCLAW_SCRIPT", legacy)

    claw._warn_legacy_migration_script(legacy)
    out = capsys.readouterr().out
    assert "qubit-migration" in out
    # No standalone upstream prose product word in the notice.
    assert not re.search(r"(?<![A-Za-z0-9_\-./@])(Hermes|OpenClaw)(?![A-Za-z0-9_\-./@])", out)


def test_canonical_frame_emits_no_deprecation(capsys, tmp_path, monkeypatch):
    from qubit_cli import claw

    canon = tmp_path / "canon" / "openclaw_to_hermes.py"
    canon.parent.mkdir(parents=True)
    canon.write_text("# canonical\n")
    monkeypatch.setattr(claw, "_OPENCLAW_SCRIPT", canon)

    claw._warn_legacy_migration_script(canon)
    assert capsys.readouterr().out == ""


def test_setup_migration_resolves_canonical_then_legacy(tmp_path, monkeypatch):
    from qubit_cli import setup_migration as SM

    canon = tmp_path / "canon" / "openclaw_to_hermes.py"
    legacy = tmp_path / "legacy" / "openclaw_to_hermes.py"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# legacy\n")

    monkeypatch.setattr(SM, "_OPENCLAW_SCRIPT", canon)
    monkeypatch.setattr(SM, "_LEGACY_OPENCLAW_SCRIPT", legacy)
    assert SM._migration_script_path() == legacy  # canonical absent -> read-old

    canon.parent.mkdir(parents=True)
    canon.write_text("# canonical\n")
    assert SM._migration_script_path() == canon  # canonical present -> canonical


def test_setup_migration_returns_none_when_no_script(tmp_path, monkeypatch):
    from qubit_cli import setup_migration as SM

    monkeypatch.setattr(SM, "_OPENCLAW_SCRIPT", tmp_path / "a")
    monkeypatch.setattr(SM, "_LEGACY_OPENCLAW_SCRIPT", tmp_path / "b")
    assert SM._migration_script_path() is None


# ------------------------------------------------------- skill-name alias
def test_legacy_skill_name_forwards_to_canonical():
    from agent.skill_utils import LEGACY_SKILL_NAME_RENAMES, resolve_legacy_skill_name

    assert LEGACY_SKILL_NAME_RENAMES["openclaw-migration"] == "qubit-migration"
    assert resolve_legacy_skill_name("openclaw-migration") == "qubit-migration"
    # The canonical name is stable (identity).
    assert resolve_legacy_skill_name("qubit-migration") == "qubit-migration"


# ------------------------------------------------------- no data deleted
def _seed_skill(base: Path, rel: str, name: str) -> Path:
    d = base / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test.\n---\n\nBody of {name}.\n")
    return d


def test_legacy_frame_archived_not_deleted(tmp_path):
    """The retired `openclaw-migration` frame survives archival, byte-for-byte."""
    from agent.skill_utils import migrate_legacy_skill_dirs

    base = tmp_path / "skills"
    canonical = _seed_skill(base, "migration/qubit-migration", "qubit-migration")
    frame = _seed_skill(base, "migration/openclaw-migration", "openclaw-migration")
    frame_bytes = (frame / "SKILL.md").read_bytes()

    report = migrate_legacy_skill_dirs(base, apply=True, retire=True)

    assert not frame.exists(), "retired frame must not remain beside the canonical"
    assert canonical.is_dir() and (canonical / "SKILL.md").is_file()
    archived = base / ".archive/openclaw-migration/SKILL.md"
    assert archived.is_file(), "retired frame must be archived, not deleted"
    assert archived.read_bytes() == frame_bytes
    assert report["retired"], report


def test_canonical_skill_survives_legacy_rename(tmp_path):
    from agent.skill_utils import migrate_legacy_skill_dirs

    base = tmp_path / "skills"
    canonical = _seed_skill(base, "migration/qubit-migration", "qubit-migration")
    canonical_bytes = (canonical / "SKILL.md").read_bytes()
    _seed_skill(base, "migration/openclaw-migration", "openclaw-migration")

    migrate_legacy_skill_dirs(base, apply=True, retire=True)
    assert (canonical / "SKILL.md").read_bytes() == canonical_bytes


# ------------------------------------------------------- branding / live proof
def test_canonical_skill_frontmatter_is_qubit_named():
    skill_md = (
        REPO / "qubit_runtime/optional-skills/migration/qubit-migration/SKILL.md"
    ).read_text(encoding="utf-8")
    assert re.search(r"(?m)^name:\s*qubit-migration\s*$", skill_md)


def test_canonical_script_loads_and_is_functional(tmp_path):
    """The resolved canonical script loads as a module and runs a dry-run plan."""
    from qubit_cli import claw

    script = claw._find_migration_script()
    assert script is not None and not claw._is_legacy_migration_script(script)
    mod = claw._load_migration_module(script)
    assert mod is not None and hasattr(mod, "Migrator")
    assert hasattr(mod, "resolve_selected_options")

    source = tmp_path / ".qubit"
    (source / "workspace").mkdir(parents=True)
    (source / "workspace/SOUL.md").write_text("# Persona\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()

    selected = mod.resolve_selected_options(None, None, preset="user-data")
    report = mod.Migrator(
        source_root=source.resolve(), target_root=target.resolve(), execute=False,
        workspace_target=None, overwrite=False, migrate_secrets=False, output_dir=None,
        selected_options=selected, preset_name="user-data",
    ).migrate()
    assert "summary" in report
    # Dry run must not have written into the target home.
    assert not (target / "SOUL.md").exists()
