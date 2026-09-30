"""Contract tests for the restored kanban toolset catalog parity.

Qubit must expose ``kanban`` as a first-class configurable catalog entry (Hermes
parity: the dashboard offered 29 toolsets, Qubit 27) while keeping the read-time
platform opt-in, and the offer-time kanban environment detector must honour a
saved ``platform_toolsets`` opt-in so environment-gated skills become visible.

These assert behavior contracts (membership + detection), never the catalog
length, so routine toolset churn does not fail them.
"""

from qubit_cli.tools_config import _get_effective_configurable_toolsets


def test_kanban_is_in_the_effective_configurable_catalog():
    keys = [key for key, _label, _desc in _get_effective_configurable_toolsets()]
    assert "kanban" in keys


def test_kanban_toolset_resolves_real_member_tools():
    from toolsets import resolve_toolset, validate_toolset

    assert validate_toolset("kanban")
    assert resolve_toolset("kanban"), "kanban must resolve to real Qubit tools"


def test_profile_has_kanban_toolset_honours_saved_platform_optin(monkeypatch):
    import tools.kanban_tools as kanban_tools

    monkeypatch.setattr(
        kanban_tools,
        "load_config",
        lambda: {"toolsets": [], "platform_toolsets": {"cli": ["terminal", "kanban"]}},
    )
    assert kanban_tools._profile_has_kanban_toolset() is True


def test_profile_has_kanban_toolset_off_without_optin(monkeypatch):
    import tools.kanban_tools as kanban_tools

    monkeypatch.setattr(
        kanban_tools,
        "load_config",
        lambda: {"toolsets": [], "platform_toolsets": {"cli": ["terminal"]}},
    )
    assert kanban_tools._profile_has_kanban_toolset() is False
