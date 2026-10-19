"""A session turn reads its frozen `auxiliary:` section, never live config.yaml.

`policy_scope` is the only place a gateway session's turn binds its frozen state. The auxiliary
section has to ride along: an auxiliary task resolved from live config.yaml would pick up a key
rotated after launch, or a route the user changed mid-session — while the primary route keeps
using the frozen one.
"""

from agent.auxiliary_task_config import _SESSION_AUXILIARY


def test_auxiliary_section_is_bound_for_the_turn_and_reset_after():
    from agent.auxiliary_task_config import session_auxiliary_scope

    assert _SESSION_AUXILIARY.get() is None
    with session_auxiliary_scope({"title": {"model": "frozen-model"}}):
        assert _SESSION_AUXILIARY.get() == {"title": {"model": "frozen-model"}}
    assert _SESSION_AUXILIARY.get() is None


def test_non_dict_section_binds_an_empty_mapping():
    from agent.auxiliary_task_config import session_auxiliary_scope

    with session_auxiliary_scope(None):
        assert _SESSION_AUXILIARY.get() == {}


def test_policy_scope_binds_the_sessions_auxiliary_section():
    """`policy_scope` must enter the auxiliary scope, not just the editor/MCP ones."""
    import inspect

    from gateway import session_policy

    source = inspect.getsource(session_policy.policy_scope)
    assert "session_auxiliary_scope(" in source
    assert "aux_scope" in source.split("with editor_scope")[1]
