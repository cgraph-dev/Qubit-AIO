"""Contract tests for the simple / researcher / digest / monitor role pack.

These are Qubit-native roles: they may name OpenJarvis as provenance, but they
must never import it or spawn it. Recipes stay the single source of truth for
the digest and monitor prompts.
"""
from __future__ import annotations

import importlib
import inspect
import json
import re

import pytest

from recipes.registry import UnknownRecipeError, build_recipe_registry
from toolsets import get_toolset, resolve_toolset

PACK = ("simple", "researcher", "digest", "monitor")
MAPPED = ("simple", "researcher")
RECIPE_BACKED = {"digest": "morning_digest", "monitor": "monitor_operative"}
EXPECTED_SOURCES = {
    "simple": "openjarvis:simple (mapped)",
    "researcher": "openjarvis:deep_research (mapped)",
    "digest": "openjarvis:morning_digest (recipe)",
    "monitor": "openjarvis:monitor_operative (recipe)",
}


def _modules():
    return {name: importlib.import_module(f"roles.{name}") for name in PACK}


@pytest.fixture(scope="module")
def roles():
    return {name: module.ROLE for name, module in _modules().items()}


def test_every_module_exports_one_role(roles):
    assert tuple(sorted(roles)) == tuple(sorted(PACK))
    for name, role in roles.items():
        assert role.id == name
        assert role.title.strip()
        assert len(role.description) > 40
        assert role.model_tier in ("local", "detail", "api", "")
        assert role.confirmation in ("preview", "confirm", "auto")
        assert role.source == EXPECTED_SOURCES[name]


def test_every_role_has_three_unique_voice_aliases(roles):
    for role in roles.values():
        aliases = tuple(alias.strip() for alias in role.voice_aliases)
        assert len(aliases) >= 3, role.id
        assert all(aliases), role.id
        assert len({alias.lower() for alias in aliases}) == len(aliases), role.id


def test_voice_aliases_are_unique_across_the_lane(roles):
    seen: dict[str, str] = {}
    for role in roles.values():
        for alias in role.voice_aliases:
            key = alias.strip().lower()
            assert key not in seen, f"{alias!r} used by {seen.get(key)} and {role.id}"
            seen[key] = role.id


def test_ids_are_unique(roles):
    assert len({role.id for role in roles.values()}) == len(PACK)


def test_to_wire_is_json_safe_and_matches_the_contract(roles):
    for role in roles.values():
        wire = role.to_wire()
        encoded = json.dumps(wire)
        decoded = json.loads(encoded)
        assert decoded["id"] == role.id
        assert decoded["title"] == role.title
        assert decoded["description"] == role.description
        assert decoded["system_prompt"] == role.system_prompt
        assert decoded["toolsets"] == list(role.toolsets)
        assert decoded["voice_aliases"] == list(role.voice_aliases)
        assert decoded["recipe_id"] == role.recipe_id
        assert decoded["source"] == role.source
        assert isinstance(decoded["toolsets"], list)
        assert isinstance(decoded["voice_aliases"], list)


def test_mapped_roles_own_a_prompt_and_use_existing_toolsets(roles):
    for name in MAPPED:
        role = roles[name]
        assert role.recipe_id == ""
        assert len(role.system_prompt.strip()) > 40
        assert role.toolsets, role.id
        for toolset in role.toolsets:
            spec = get_toolset(toolset)
            assert spec is not None, f"{role.id} names unknown toolset {toolset!r}"
            assert resolve_toolset(toolset), f"{role.id} toolset {toolset!r} is empty"


def test_simple_is_a_minimal_read_only_posture(roles):
    role = roles["simple"]
    assert role.toolsets == ("search",)
    assert "fabricat" in role.system_prompt.lower()


def test_researcher_requires_citations_and_gaps(roles):
    prompt = roles["researcher"].system_prompt.lower()
    assert "cite" in prompt
    assert "source" in prompt
    assert "inference" in prompt
    assert "bounded" in prompt or "under 250 words" in prompt


def test_recipe_roles_dispatch_the_real_recipes_without_duplicated_prompts(roles):
    registry = build_recipe_registry()
    for name, recipe_id in RECIPE_BACKED.items():
        role = roles[name]
        assert role.recipe_id == recipe_id
        assert role.system_prompt == "", "recipe roles must not duplicate the prompt"
        spec = registry.get(recipe_id)
        assert spec.id == recipe_id
        assert spec.prompt.strip(), f"recipe {recipe_id} has no prompt"
        assert recipe_id in registry.available()


def test_unknown_recipe_is_typed_for_the_lane(roles):
    with pytest.raises(UnknownRecipeError):
        build_recipe_registry().get("definitely_not_a_recipe")


def test_role_modules_never_import_or_spawn_openjarvis():
    for name, module in _modules().items():
        source = inspect.getsource(module)
        assert not re.search(r"(?m)^\s*(?:import|from)\s+openjarvis\b", source), name
        assert "subprocess" not in source, name
        assert "os.system" not in source, name
        assert "Popen" not in source, name
