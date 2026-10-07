"""Regression tests for rebrand/sync symbols that were CALLED but defined nowhere.

Each case was a live `NameError` (or a swallowed one) found by the lazy-import and
undefined-global probes on 2026-10-07. The assertions are behavioural where a real
call path exists, and module-attribute existence where the symbol is a private
module constant the code reads directly.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# agent/skill_commands.py — the sync added the interactive cache's USES but
# dropped its module-level definition.
# ---------------------------------------------------------------------------

def test_interactive_skill_commands_cache_is_defined():
    from agent import skill_commands

    assert isinstance(
        getattr(skill_commands, "_last_interactive_skill_commands_by_key", None), dict)
    # The function that writes it must run without NameError.
    commands = skill_commands.get_interactive_skill_commands()
    assert isinstance(commands, dict)


# ---------------------------------------------------------------------------
# agent/skill_utils.py — `_migrate_usage_keys` used `json` with no import.
# ---------------------------------------------------------------------------

def test_migrate_usage_keys_reads_and_writes_json(tmp_path):
    from agent import skill_utils

    state = tmp_path / ".usage.json"
    state.write_text(json.dumps({"old-key": {"count": 3}}), encoding="utf-8")

    assert skill_utils._migrate_usage_keys(state, "old-key", "new-key") is True
    assert json.loads(state.read_text(encoding="utf-8")) == {"new-key": {"count": 3}}
    # Idempotent second run (nothing left to migrate).
    assert skill_utils._migrate_usage_keys(state, "old-key", "new-key") is False


# ---------------------------------------------------------------------------
# plugins/model-providers/router/__init__.py — the Qubit-added `_DISK_TTL_SECONDS`
# constant was lost in the v0.21.5 reset, leaving `_efforts_cache_only` to NameError.
# ---------------------------------------------------------------------------

def test_router_disk_ttl_constant_is_defined():
    runtime = Path(__file__).resolve().parents[2]
    module_path = runtime / "plugins" / "model-providers" / "router" / "__init__.py"
    spec = importlib.util.spec_from_file_location("_router_probe", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert isinstance(module._DISK_TTL_SECONDS, (int, float))
    assert module._DISK_TTL_SECONDS > 0


# ---------------------------------------------------------------------------
# tools/skills_tool.py — the read-old legacy-name fallback passed an undefined
# `project_dirs` (and the wrong arity) to `_locate_skill`; the NameError was
# swallowed by skill_view's broad except, so the fallback silently never worked.
# ---------------------------------------------------------------------------

def _make_skill(skills_dir: Path, name: str) -> Path:
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Description for {name}.\n---\n\n# {name}\n\nbody\n",
        encoding="utf-8",
    )
    return skill_dir


def test_skill_view_resolves_a_legacy_name_via_the_fallback(tmp_path, monkeypatch):
    import tools.skills_tool as skills_tool

    skills_dir = tmp_path / "skills"
    _make_skill(skills_dir, "qubit-agent")
    monkeypatch.setattr(skills_tool, "SKILLS_DIR", skills_dir)

    payload = json.loads(skills_tool.skill_view("hermes-agent"))
    assert payload.get("success") is True, payload
    assert payload.get("name") == "qubit-agent"
