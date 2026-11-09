"""The project-workflow router must fire on app/project work, and stay quiet otherwise.

``project-workflow-router`` injects a ``pre_llm_call`` context block naming the
ordered workflow-skill chain, so the ported ``qubit-project-workflow`` routing is
enforced rather than remembered. The classification is the whole contract: fire on
a create/change/scope request, stay silent on ordinary conversation and on plain
debugging (a "failing test" is not a project request — dragging a five-stage
workflow into it would teach the model to ignore the block).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PLUGIN = (
    Path(__file__).resolve().parents[3]
    / "qubit_runtime"
    / "plugins"
    / "project-workflow-router"
    / "__init__.py"
)


def _load():
    """Import the plugin by path — its directory name has a dash, so not a package."""
    spec = importlib.util.spec_from_file_location("project_workflow_router", _PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


router = _load()


@pytest.mark.parametrize(
    "message,intent",
    [
        ("create an app", "create_app"),
        ("build me a kanban app", "create_app"),
        ("I want to create an app for tracking habits", "create_app"),
        ("make me a new app", "create_app"),
        ("create a project", "create_project"),
        ("scaffold a new project", "create_project"),
        ("start a repo for this", "create_project"),
        ("update the app", "change_app"),
        ("add dark mode to the app", "change_app"),
        ("change the project to use postgres", "change_app"),
        ("refactor my project", "refactor_project"),
        ("restructure the app", "refactor_project"),
        ("the app is broken", "update_app"),
        ("fix the login bug in my app", "update_app"),
        ("my app stopped working", "update_app"),
    ],
)
def test_app_and_project_requests_route(message: str, intent: str) -> None:
    assert router.classify(message) == intent


@pytest.mark.parametrize(
    "message",
    [
        "what time is it",
        "explain this function",
        "deploy 5 agents and continue",
        "why is the build red",
        "fix this failing test",
        "run the tests",
        "what does this error mean",
        "",
        "thanks",
    ],
)
def test_ordinary_requests_do_not_route(message: str) -> None:
    # Silent here is the point: a router that fires on everything is noise.
    assert router.classify(message) is None


def test_context_names_the_skill_and_the_order() -> None:
    block = router.build_context("create_app")
    assert router.ROUTER_SKILL in block
    # The first skill of the chain must be named to load, and the order must be explicit.
    first = router.CHAIN_BY_INTENT["create_app"][0]
    assert f"skill_view(name='{first}')" in block
    assert " → ".join(router.CHAIN_BY_INTENT["create_app"][1]) in block
    assert "understand → scope → spec → implement → verify" in block


def test_every_intent_has_a_chain_whose_first_entry_is_itself() -> None:
    # A chain that does not start with the skill it tells you to load first is a
    # contradiction the model would have to resolve on every turn.
    for intent, (first, chain, _scope) in router.CHAIN_BY_INTENT.items():
        assert chain[0] == first, f"{intent}: chain starts with {chain[0]}, names {first}"


def test_hook_injects_for_app_work_and_is_silent_otherwise(monkeypatch) -> None:
    monkeypatch.delenv("QU_WORKFLOW_ROUTER_DISABLE", raising=False)
    fired = router._on_pre_llm_call(user_message="build me a notes app")
    assert isinstance(fired, dict) and "qubit-project-workflow" in fired["context"]
    assert router._on_pre_llm_call(user_message="what time is it") is None


def test_hook_honours_the_kill_switch(monkeypatch) -> None:
    monkeypatch.setenv("QU_WORKFLOW_ROUTER_DISABLE", "1")
    assert router._on_pre_llm_call(user_message="create an app") is None


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "create an app"},
        [{"type": "text", "text": "build me a project tracker"}],
        [{"text": "create a project"}],
        "update the app",
    ],
)
def test_structured_user_messages_are_understood(payload) -> None:
    # Clients send the message as a string, a list of content blocks, or a dict.
    assert router._on_pre_llm_call(user_message=payload) is not None


def test_an_unreadable_message_never_raises() -> None:
    for payload in (None, 42, object(), {"unexpected": "shape"}):
        assert router._on_pre_llm_call(user_message=payload) is None
