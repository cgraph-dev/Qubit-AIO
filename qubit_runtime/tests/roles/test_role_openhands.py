"""Contract tests for the native ``openhands`` coding role.

The role is Qubit-owned content: it must satisfy the AgentRole contract, name
only real toolsets, expose unique voice aliases, serialize safely, and never
reach for OpenJarvis (import or subprocess).
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

import toolsets
from roles import openhands
from roles.base import CONFIRMATION_MODES, MODEL_TIERS
from roles.openhands import ROLE, SYSTEM_PROMPT

ROLE_SOURCE = Path(openhands.__file__)


def test_contract_fields():
    assert ROLE.id == "openhands"
    assert ROLE.title.strip()
    assert ROLE.description.strip()
    assert ROLE.system_prompt.strip() == SYSTEM_PROMPT.strip()
    assert ROLE.model_tier in MODEL_TIERS
    assert ROLE.confirmation in CONFIRMATION_MODES
    assert ROLE.source == "openjarvis:native_openhands (mapped)"
    assert ROLE.recipe_id == ""
    assert isinstance(ROLE.toolsets, tuple) and ROLE.toolsets
    assert all(isinstance(name, str) and name.strip() for name in ROLE.toolsets)


def test_prompt_states_the_coding_safeguards():
    prompt = ROLE.system_prompt.lower()
    for phrase in ("inspect", "bounded", "secret", "unrelated", "focused", "evidence"):
        assert phrase in prompt, f"prompt must state the {phrase!r} safeguard"
    assert "never claim success without evidence" in prompt


def test_toolsets_exist_and_resolve():
    for name in ROLE.toolsets:
        assert name in toolsets.TOOLSETS, f"unknown toolset: {name}"
        assert toolsets.get_toolset(name, include_registry=False), name
        resolved = toolsets.resolve_toolset(name, include_registry=False)
        assert resolved, f"toolset {name} resolves to no tools"

    resolved_all = set()
    for name in ROLE.toolsets:
        resolved_all.update(toolsets.resolve_toolset(name, include_registry=False))
    assert {"read_file", "write_file", "patch", "terminal"} <= resolved_all


def test_confirmation_is_confirm_for_a_mutating_role():
    # Edits and terminal commands change the tree; explicit confirmation is the
    # safer posture than an unattended "auto" run.
    assert ROLE.confirmation == "confirm"


def test_voice_aliases_are_non_empty_and_unique():
    assert len(ROLE.voice_aliases) >= 3
    aliases = list(ROLE.voice_aliases)
    assert all(isinstance(alias, str) and alias.strip() for alias in aliases)
    assert len(aliases) == len(set(aliases))
    lowered = [alias.lower() for alias in aliases]
    assert len(lowered) == len(set(lowered))
    assert "run the coding agent" in lowered


def test_to_wire_is_json_safe_and_lossless():
    wire = ROLE.to_wire()
    assert wire["toolsets"] == list(ROLE.toolsets)
    assert wire["voice_aliases"] == list(ROLE.voice_aliases)
    assert isinstance(wire["toolsets"], list)
    assert isinstance(wire["voice_aliases"], list)

    encoded = json.dumps(wire)
    decoded = json.loads(encoded)
    assert decoded == wire
    assert decoded["id"] == "openhands"
    assert decoded["source"] == "openjarvis:native_openhands (mapped)"


def test_module_never_imports_or_shells_out_to_openjarvis():
    tree = ast.parse(ROLE_SOURCE.read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name) else ""
            )
            assert name not in {"system", "popen", "Popen", "run", "check_output"}

    assert not any(name.split(".")[0] == "openjarvis" for name in imported)
    assert "subprocess" not in imported


def test_importing_the_role_does_not_pull_in_openjarvis():
    assert "openjarvis" not in sys.modules


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
