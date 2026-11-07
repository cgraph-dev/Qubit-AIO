"""Behavioural test: the browser-companion plugin through real discovery.

Loads the plugin the way the runtime does (``discover_plugins`` with a temp
``QUBIT_HOME``) and asserts outcomes, never counts: the plugin is present with no
load error, its three declared tools are in the real registry, both hooks fire,
a full BCP v2 turn is stored for its trusted owner, the payload is consumed
exactly once, and a stranger's call is refused.
"""

from __future__ import annotations

import json

import pytest

OWNER = {"session_id": "sess-companion-1", "turn_id": "turn-1", "task_id": "task-1"}


def _bcp_turn(scope: str = "page") -> str:
    return json.dumps({
        "protocol": "qubit.browser.turn.v2",
        "human_input": {"source": "composer", "text": "what does this page say?"},
        "attachment_context": {"items": []},
        "browser_context": {
            "delivery": "full",
            "payload": {
                "protocol": "qubit.browser.context.v1",
                "contextScope": {"mode": scope},
                "settings": {},
                "activeTab": {"title": "Example"},
                "pageContext": {"text": "untrusted page text"},
                "tabs": [],
                "selectedTabs": [],
            },
        },
        "source_receipt": {
            "protocol": "qubit.browser.turn.v2", "version": 2, "delivery": "full",
            "context_hash": "a1b2c3d4e5f60718a1b2c3d4e5f60718",
        },
    })


@pytest.fixture()
def companion(monkeypatch, tmp_path):
    """Discover plugins with an isolated home, and return the loaded plugin module."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    # A bundled standalone plugin is opt-in; enable it exactly as the operator does.
    (home / "config.yaml").write_text("plugins:\n  enabled:\n    - browser-companion\n", encoding="utf-8")
    monkeypatch.setenv("QUBIT_HOME", str(home))
    monkeypatch.setattr("qubit_cli.plugins.PluginManager.home_path", home, raising=False)
    from qubit_cli.plugins import discover_plugins, get_plugin_manager

    discover_plugins(force=True)
    entry = get_plugin_manager()._plugins.get("browser-companion")
    assert entry is not None, "browser-companion did not load"
    assert not entry.error, entry.error
    module = entry.module
    module.STORE.close()
    module.tools.clear_leases()
    yield module
    module.STORE.close()
    module.tools.clear_leases()


def test_declared_tools_are_registered(companion):
    from tools.registry import registry

    for name in ("browser_context_status", "browser_get_context", "browser_clear_context"):
        entry = registry.get_entry(name)
        assert entry is not None, name
        assert entry.toolset == "browser-companion"


def test_fail_soft_without_context(companion):
    for name in ("browser_context_status", "browser_get_context", "browser_clear_context"):
        out = json.loads(getattr(companion.tools, name)({}))
        assert out == {"available": False, "reason": "Browser context unavailable."}


def test_a_full_turn_is_stored_and_consumed_exactly_once(companion):
    notice = companion.hooks.pre_llm_call(user_message=_bcp_turn(), **OWNER)
    assert notice and "context_id=" in notice["context"]

    # The pre-tool hook authorizes the call; the handler then reads the one-shot lease.
    assert companion.hooks.pre_tool_call(tool_name="browser_context_status", args={}, **OWNER) is None
    status = json.loads(companion.tools.browser_context_status({}, **OWNER))
    assert status["available"] is True
    assert status["scope"] == "page"
    context_id = status["context_id"]

    assert companion.hooks.pre_tool_call(
        tool_name="browser_get_context", args={"context_id": context_id}, **OWNER) is None
    got = json.loads(companion.tools.browser_get_context({"context_id": context_id}, **OWNER))
    assert got["available"] is True
    assert got["payload"]["pageContext"]["text"] == "untrusted page text"

    # Consume-once: the record is gone, so the second authorized call is refused.
    assert companion.hooks.pre_tool_call(
        tool_name="browser_get_context", args={"context_id": context_id}, **OWNER) == {
        "action": "block", "message": "Browser context unavailable."}
    assert json.loads(companion.tools.browser_get_context({"context_id": context_id}, **OWNER))["available"] is False


def test_an_untrusted_identity_is_refused(companion):
    companion.hooks.pre_llm_call(user_message=_bcp_turn(), **OWNER)
    # Not a refusal at the hook (the plugin holds nothing for a stranger, so there is nothing to
    # hand over), but the stranger's handler call can never see the other owner's payload.
    stranger = {"session_id": "sess-other", "turn_id": "turn-9", "task_id": "task-9"}
    companion.hooks.pre_tool_call(tool_name="browser_context_status", args={}, **stranger)
    assert json.loads(companion.tools.browser_context_status({}, **stranger))["available"] is False

    context_id = companion.STORE.status_for_owner(
        companion.context_store.owner_from_hook_kwargs(OWNER))["context_id"]
    assert companion.hooks.pre_tool_call(
        tool_name="browser_get_context", args={"context_id": context_id}, **stranger) == {
        "action": "block", "message": "Browser context unavailable."}
    assert json.loads(companion.tools.browser_get_context({"context_id": context_id}, **stranger))["available"] is False

    # And the owner's record is untouched by the stranger's attempts.
    assert companion.hooks.pre_tool_call(tool_name="browser_context_status", args={}, **OWNER) is None
    assert json.loads(companion.tools.browser_context_status({}, **OWNER))["available"] is True


def test_no_trusted_identity_means_no_context(companion):
    # No session/turn/task ids: the hook must not invent an owner.
    assert companion.hooks.pre_llm_call(user_message=_bcp_turn()) is None
    assert companion.hooks.pre_tool_call(tool_name="browser_context_status", args={}) == {
        "action": "block", "message": "Browser context unavailable."}


def test_clear_is_owner_scoped(companion):
    companion.hooks.pre_llm_call(user_message=_bcp_turn(scope="tabs"), **OWNER)
    assert companion.hooks.pre_tool_call(tool_name="browser_clear_context", args={}, **OWNER) is None
    cleared = json.loads(companion.tools.browser_clear_context({}, **OWNER))
    assert cleared["available"] is True and cleared["cleared"] == 1
    assert companion.hooks.pre_tool_call(tool_name="browser_context_status", args={}, **OWNER) is None
    assert json.loads(companion.tools.browser_context_status({}, **OWNER))["available"] is False


def test_malformed_turn_is_not_stored(companion):
    assert companion.hooks.pre_llm_call(user_message="just a normal message", **OWNER) is None
    assert companion.hooks.pre_llm_call(user_message=json.dumps({"protocol": "nope"}), **OWNER) is None
