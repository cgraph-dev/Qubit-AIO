"""A completion the TUI poller turned into a turn must stop being offered.

Root cause of the 2026-10-17 replay storm: ``unread_completions_owned_by`` keeps
offering an exited ``notify_on_complete`` process until its id lands in
``_completion_consumed`` or ``_poll_observed``, and BOTH are written only by
agent read tools (``wait`` / ``log`` / ``poll``). The TUI poller turns a
completion into an agent turn and renders the result, but never marked it, so
every subsequent drain re-offered the same process. One envelope became a fresh
user message every ~13s for hours.

``mark_completion_consumed`` is that missing acknowledgement.
"""

from __future__ import annotations

import pytest

from tools.process_registry import ProcessRegistry


class _Session:
    def __init__(self, sid: str, owner: str):
        self.id = sid
        self.owner_task_id = owner
        self.notify_on_complete = True
        self.exit_code = 0
        self.exited = True


@pytest.fixture
def registry(monkeypatch):
    reg = ProcessRegistry()
    reg._finished = {"proc_loop": _Session("proc_loop", "session:abc")}
    reg._running = {}
    return reg


def test_an_offered_completion_stops_after_acknowledgement(registry):
    """The TUI's acknowledgement must remove the process from the unread set."""
    assert [s.id for s in registry.unread_completions_owned_by("session:abc")] == ["proc_loop"]

    registry.mark_completion_consumed("proc_loop")

    assert registry.unread_completions_owned_by("session:abc") == [], (
        "an acknowledged completion must never be offered again"
    )
    assert registry.is_completion_consumed("proc_loop") is True
