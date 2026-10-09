"""Reader-level regression: the deferred-startup status line must read the var the
launcher PRODUCES.

Half-rebrand bug (wave f5, fixed in this change): the sync commit renamed the
``CLISessionMixin._show_status`` reader to ``QUBIT_DEFER_AGENT_STARTUP`` while
``main.py`` (and four other readers) use ``HERMES_DEFER_AGENT_STARTUP``. The
deferred-status optimization at this one site was therefore dead: the status line
always took the ``else`` branch and eagerly built the full tool registry, even on
the interactive-prompt path the producer marks deferred.

These tests exercise the READER: set the producer's exact spelling and assert the
deferred branch is taken (no tool-registry build); clear it and assert the
registry is built. A future rename on either side fails here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import cli
from qubit_cli import cli_session_mixin


@pytest.fixture(autouse=True)
def _no_inherited_defer_env(monkeypatch):
    monkeypatch.delenv("HERMES_DEFER_AGENT_STARTUP", raising=False)
    monkeypatch.delenv("QUBIT_DEFER_AGENT_STARTUP", raising=False)


def _cli_stub():
    return SimpleNamespace(
        enabled_toolsets=None,
        disabled_toolsets=None,
        model="openai/gpt-x",
        api_key="sk-test",
        provider="openai",
        _provider_source="",
        _console_print=lambda *a, **k: None,
    )


def _run_show_status(monkeypatch):
    """Call the real mixin method; return (output, registry_build_count)."""
    captured = []
    builds = []

    monkeypatch.setattr(
        cli, "get_tool_definitions",
        lambda **kw: builds.append(kw) or [object(), object(), object()],
        raising=False,
    )
    stub = _cli_stub()
    stub._console_print = lambda line: captured.append(str(line))
    cli_session_mixin.CLISessionMixin._show_status(stub)
    return "".join(captured), builds


def test_deferred_status_line_uses_producer_spelling(monkeypatch):
    """With the launcher's HERMES_DEFER_AGENT_STARTUP=1, _show_status must take the
    deferred branch and NOT build the tool registry."""
    monkeypatch.setenv("HERMES_DEFER_AGENT_STARTUP", "1")
    out, builds = _run_show_status(monkeypatch)
    assert builds == [], (
        "the deferred branch must skip get_tool_definitions — reading the "
        "producer's HERMES_DEFER_AGENT_STARTUP spelling is what gates this"
    )
    assert out  # a status line was still printed


def test_status_line_builds_registry_when_not_deferred(monkeypatch):
    """Control: with no defer flag the registry is built (else branch)."""
    out, builds = _run_show_status(monkeypatch)
    assert len(builds) == 1
    assert out


def test_reader_is_inert_to_the_stale_qubit_spelling(monkeypatch):
    """The half-rebrand spelling (QUBIT_DEFER_AGENT_STARTUP) is not the producer's
    wire; setting it alone must not trigger the deferred branch, while the HERMES_
    spelling must."""
    monkeypatch.setenv("QUBIT_DEFER_AGENT_STARTUP", "1")
    _, builds = _run_show_status(monkeypatch)
    assert len(builds) == 1, "QUBIT_DEFER_AGENT_STARTUP is not the wire; it must be inert"

    monkeypatch.delenv("QUBIT_DEFER_AGENT_STARTUP")
    monkeypatch.setenv("HERMES_DEFER_AGENT_STARTUP", "1")
    _, builds = _run_show_status(monkeypatch)
    assert builds == []
