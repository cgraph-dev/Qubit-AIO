"""Contract tests for the ``orchestrator`` role.

The role is a Qubit-native preset, not a wrapper: it must import cleanly, name
only toolsets the real registry resolves, and contain no OpenJarvis code path.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from roles import orchestrator as orchestrator_module
from roles.base import MODEL_TIERS, CONFIRMATION_MODES, AgentRole
from toolsets import resolve_toolset, validate_toolset

ROLE = orchestrator_module.ROLE


def _module_ast() -> ast.Module:
    source = Path(orchestrator_module.__file__).read_text(encoding="utf-8")
    return ast.parse(source, filename=orchestrator_module.__file__)


def test_role_is_an_agent_role_with_the_expected_identity():
    assert isinstance(ROLE, AgentRole)
    assert ROLE.id == "orchestrator"
    assert ROLE.title == "Orchestrator"
    assert ROLE.description.strip()
    assert ROLE.source == "openjarvis:orchestrator (mapped)"
    assert ROLE.recipe_id == ""


def test_role_posture_is_plan_delegate_verify_without_bypassing_authority():
    prompt = ROLE.system_prompt.lower()
    assert prompt.strip()
    assert "delegate" in prompt
    assert "verif" in prompt
    assert "confirm" in prompt
    assert "authority" in prompt
    assert ROLE.confirmation == "preview"
    assert ROLE.confirmation in CONFIRMATION_MODES
    assert ROLE.model_tier in MODEL_TIERS
    assert ROLE.model_tier


def test_declared_toolsets_all_resolve_in_the_real_registry():
    assert ROLE.toolsets
    assert "delegation" in ROLE.toolsets
    assert "todo" in ROLE.toolsets
    for name in ROLE.toolsets:
        assert validate_toolset(name), f"unknown toolset: {name}"
        tools = resolve_toolset(name)
        assert tools, f"toolset resolved to nothing: {name}"
        assert all(isinstance(tool, str) and tool for tool in tools)


def test_voice_aliases_are_unique_and_non_empty():
    aliases = ROLE.voice_aliases
    assert len(aliases) >= 3
    assert all(isinstance(alias, str) and alias.strip() for alias in aliases)
    assert len(set(aliases)) == len(aliases)
    assert "orchestrate this" in aliases
    assert "delegate this" in aliases


def test_to_wire_is_json_safe():
    payload = ROLE.to_wire()
    encoded = json.dumps(payload, sort_keys=True)
    decoded = json.loads(encoded)
    assert decoded == payload
    assert isinstance(decoded["toolsets"], list)
    assert isinstance(decoded["voice_aliases"], list)
    assert decoded["id"] == "orchestrator"


def test_module_has_no_openjarvis_import_or_subprocess_call():
    tree = _module_ast()

    def top_level(name: str | None) -> str:
        return (name or "").split(".", 1)[0]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert top_level(alias.name) != "openjarvis"
                assert top_level(alias.name) != "subprocess"
        elif isinstance(node, ast.ImportFrom):
            assert top_level(node.module) != "openjarvis"
            assert top_level(node.module) != "subprocess"
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                assert not func.id.startswith("openjarvis")
            elif isinstance(func, ast.Attribute):
                chain = func
                while isinstance(chain, ast.Attribute):
                    chain = chain.value
                if isinstance(chain, ast.Name):
                    assert chain.id != "subprocess"
                    assert not chain.id.startswith("openjarvis")

    assert not hasattr(orchestrator_module, "subprocess")
