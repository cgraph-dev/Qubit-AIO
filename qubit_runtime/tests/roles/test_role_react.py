"""Contract tests for the native ``react`` role.

These pin the role content, that its toolsets exist in Qubit's toolset table,
that its voice aliases are usable, that the wire form is JSON-safe, and that
the module neither imports nor spawns OpenJarvis.
"""
from __future__ import annotations

import ast
import inspect
import json

from roles import react
from roles.base import AgentRole, CONFIRMATION_MODES, MODEL_TIERS
from toolsets import TOOLSETS, get_toolset

ROLE = react.ROLE

WIRE_FIELDS = {
    "id",
    "title",
    "description",
    "system_prompt",
    "toolsets",
    "model_tier",
    "confirmation",
    "voice_aliases",
    "recipe_id",
    "source",
}


def test_role_contract_fields():
    assert isinstance(ROLE, AgentRole)
    assert ROLE.id == "react"
    assert ROLE.title.strip()
    assert ROLE.description.strip()
    assert ROLE.system_prompt.strip()
    assert ROLE.recipe_id == ""
    assert ROLE.model_tier in MODEL_TIERS
    assert ROLE.confirmation in CONFIRMATION_MODES
    assert ROLE.model_tier == "detail"
    assert ROLE.confirmation == "preview"
    assert ROLE.source == "openjarvis:native_react (mapped)"


def test_toolsets_resolve_to_real_toolsets():
    assert ROLE.toolsets
    assert len(set(ROLE.toolsets)) == len(ROLE.toolsets)
    for name in ROLE.toolsets:
        assert name in TOOLSETS, f"unknown toolset {name!r}"
        resolved = get_toolset(name, include_registry=False)
        assert resolved is not None, f"toolset {name!r} did not resolve"
        assert resolved["tools"], f"toolset {name!r} is empty"


def test_voice_aliases_are_non_empty_and_unique():
    aliases = ROLE.voice_aliases
    assert len(aliases) >= 3
    assert all(isinstance(alias, str) and alias.strip() == alias for alias in aliases)
    assert all(alias for alias in aliases)
    assert len({alias.lower() for alias in aliases}) == len(aliases)


def test_to_wire_is_json_safe_and_isolated():
    wire = ROLE.to_wire()
    assert set(wire) == WIRE_FIELDS
    assert json.loads(json.dumps(wire)) == wire
    assert wire["id"] == "react"
    assert wire["toolsets"] == list(ROLE.toolsets)
    assert wire["voice_aliases"] == list(ROLE.voice_aliases)
    assert wire["model_tier"] == ROLE.model_tier
    assert wire["confirmation"] == ROLE.confirmation
    assert wire["source"] == ROLE.source

    wire["toolsets"].append("not-a-toolset")
    wire["voice_aliases"].append("not-an-alias")
    assert "not-a-toolset" not in ROLE.toolsets
    assert "not-an-alias" not in ROLE.voice_aliases


def test_system_prompt_encodes_the_reason_act_contract():
    prompt = ROLE.system_prompt.lower()
    for token in ("think", "tool", "observe", "repeat", "honest", "approval"):
        assert token in prompt, f"prompt is missing {token!r}"


def test_module_does_not_import_or_spawn_openjarvis():
    source = inspect.getsource(react)
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert not [name for name in imported if name == "openjarvis" or name.startswith("openjarvis.")]
    assert not imported & {"subprocess", "multiprocessing"}
    for forbidden in ("os.system", "popen", "subprocess.run", "subprocess.popen"):
        assert forbidden not in source.lower()
    # The only OpenJarvis mention is the role's provenance string.
    assert "openjarvis" not in inspect.getsource(react).replace(ROLE.source, "")
