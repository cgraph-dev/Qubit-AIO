"""Contract tests for the absorbed operative role."""
from __future__ import annotations

import builtins
import importlib
import json
import subprocess
import sys

import pytest

from roles import operative
from roles.base import CONFIRMATION_MODES, MODEL_TIERS
from toolsets import resolve_toolset, validate_toolset

ROLE = operative.ROLE


def test_contract_fields_are_complete():
    assert ROLE.id == "operative"
    assert ROLE.title.strip()
    assert len(ROLE.description.strip()) >= 40
    assert ROLE.system_prompt.strip()
    assert ROLE.model_tier in MODEL_TIERS
    assert ROLE.confirmation in CONFIRMATION_MODES
    assert ROLE.confirmation == "confirm"
    assert ROLE.recipe_id == ""
    assert ROLE.source == "openjarvis:operative (mapped)"


def test_toolsets_are_real_and_resolve():
    assert ROLE.toolsets
    for name in ROLE.toolsets:
        assert isinstance(name, str) and name.strip()
        assert validate_toolset(name), f"unknown toolset: {name}"
        assert resolve_toolset(name), f"toolset resolves to no tools: {name}"


def test_voice_aliases_are_non_empty_and_unique():
    assert len(ROLE.voice_aliases) >= 3
    for alias in ROLE.voice_aliases:
        assert isinstance(alias, str)
        assert alias == alias.strip()
        assert alias
    folded = [a.casefold() for a in ROLE.voice_aliases]
    assert len(folded) == len(set(folded))


def test_to_wire_is_json_safe():
    payload = json.loads(json.dumps(ROLE.to_wire()))
    assert payload["id"] == "operative"
    assert isinstance(payload["toolsets"], list)
    assert isinstance(payload["voice_aliases"], list)
    assert payload["toolsets"] == list(ROLE.toolsets)
    assert payload["voice_aliases"] == list(ROLE.voice_aliases)
    assert payload["source"] == "openjarvis:operative (mapped)"


def test_role_import_does_not_import_openjarvis_or_spawn(monkeypatch):
    def _fail(*_args, **_kwargs):
        raise AssertionError("importing the operative role spawned a process")

    for name in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _fail)

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "openjarvis" or name.startswith("openjarvis."):
            raise AssertionError("the operative role imported OpenJarvis")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    reloaded = importlib.reload(operative)

    assert reloaded.ROLE.id == "operative"
    assert not any(
        key == "openjarvis" or key.startswith("openjarvis.") for key in vars(reloaded)
    )
    assert "subprocess" not in vars(reloaded)
    assert not any(
        key == "openjarvis" or key.startswith("openjarvis.")
        for key in sys.modules
    )
