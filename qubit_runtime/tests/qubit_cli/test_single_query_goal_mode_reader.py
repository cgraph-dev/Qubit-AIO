"""Reader-level regression: the one-shot goal_mode gate must read the var the
dispatcher PRODUCES.

Half-rebrand bug (wave f5, fixed in this change): the sync commit renamed the
two ``cli_single_query`` readers to ``QUBIT_KANBAN_GOAL_MODE`` while the Kanban
dispatcher kept setting ``HERMES_KANBAN_GOAL_MODE`` (``kanban_db_dispatch.py``).
The producer-side test (``test_kanban_goal_mode.py``) only asserts the spawned
env — it never drives the reader — so the drift was invisible and the goal judge
loop silently never fired for a goal_mode card.

These tests exercise the READER end-to-end: set the producer's exact spelling
(``HERMES_KANBAN_GOAL_MODE=1``) and assert the judge loop runs; clear it and
assert it does not. A future rename on either side fails here.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import cli


@pytest.fixture(autouse=True)
def _no_inherited_kanban_env(monkeypatch):
    # Neither spelling may leak in from the ambient environment.
    monkeypatch.delenv("HERMES_KANBAN_GOAL_MODE", raising=False)
    monkeypatch.delenv("QUBIT_KANBAN_GOAL_MODE", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    monkeypatch.delenv("QUBIT_KANBAN_TASK", raising=False)


def _run_non_quiet(monkeypatch, turn_result):
    """Drive ``_run_single_query_mode`` down the non-quiet tail; return the exit code.

    Same harness shape as ``test_single_query_exit_contract.py::_run_non_quiet``.
    """
    monkeypatch.setattr(cli, "_should_seed_interactive", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_collect_query_images", lambda q, i: (q, []))
    monkeypatch.setattr(cli, "_collect_kanban_task_images", lambda imgs: [])
    monkeypatch.setattr(cli, "_finalize_single_query", lambda c: None)
    stub = SimpleNamespace(
        _single_query_mode=False,
        _claim_active_session=lambda *a, **k: True,
        console=SimpleNamespace(print=lambda *a, **k: None),
        _show_security_advisories=lambda: None,
        chat=lambda *a, **k: "response",
        _print_exit_summary=lambda **k: None,
        _last_turn_result=turn_result,
    )
    try:
        cli._run_single_query_mode(stub, "do the thing", None, False, True)
    except SystemExit as exc:
        return exc.code
    return None


def test_goal_loop_fires_on_the_producer_spelling(monkeypatch):
    """``HERMES_KANBAN_GOAL_MODE=1`` (the dispatcher's spelling) must reach the
    reader and dispatch the goal judge loop on the non-quiet ``-q`` tail."""
    monkeypatch.setenv("HERMES_KANBAN_GOAL_MODE", "1")
    calls: list = []
    monkeypatch.setattr(cli, "_run_kanban_goal_loop_chat", lambda c, r: calls.append(r))

    code = _run_non_quiet(monkeypatch, {"final_response": "done", "completed": True})

    assert calls == ["response"], (
        "the goal judge loop must run when the dispatcher sets "
        "HERMES_KANBAN_GOAL_MODE=1 (reader/producer spelling drift)"
    )
    assert code == 0


def test_goal_loop_does_not_fire_without_the_flag(monkeypatch):
    """Control: with no goal_mode flag the loop is never dispatched."""
    calls: list = []
    monkeypatch.setattr(cli, "_run_kanban_goal_loop_chat", lambda c, r: calls.append(r))

    code = _run_non_quiet(monkeypatch, {"final_response": "done", "completed": True})

    assert calls == []
    assert code == 0


def test_reader_reads_hermes_spelling_not_qubit(monkeypatch):
    """The reader must key on ``HERMES_``: the QUBIT_ spelling (the old broken
    reader name) must NOT trigger the loop, and the HERMES_ spelling must."""
    # The stale half-rebrand spelling alone must be inert.
    monkeypatch.setenv("QUBIT_KANBAN_GOAL_MODE", "1")
    calls: list = []
    monkeypatch.setattr(cli, "_run_kanban_goal_loop_chat", lambda c, r: calls.append(r))
    _run_non_quiet(monkeypatch, {"final_response": "done", "completed": True})
    assert calls == [], "QUBIT_KANBAN_GOAL_MODE is not the producer's wire; it must be inert"

    # The real wire fires.
    monkeypatch.delenv("QUBIT_KANBAN_GOAL_MODE")
    monkeypatch.setenv("HERMES_KANBAN_GOAL_MODE", "1")
    _run_non_quiet(monkeypatch, {"final_response": "done", "completed": True})
    assert calls == ["response"]


def test_worker_registration_gate_reads_producer_spelling(monkeypatch):
    """``_run_single_query_mode`` gates ``register_current_worker_from_env`` on
    ``HERMES_KANBAN_TASK`` (the dispatcher sets that name). With the producer's
    spelling present the worker must be registered."""
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_reader_probe")
    registered: list = []
    import tools.kanban_tools as kt
    monkeypatch.setattr(kt, "register_current_worker_from_env",
                        lambda: registered.append(True) or True)

    _run_non_quiet(monkeypatch, {"final_response": "done", "completed": True})

    assert registered == [True], (
        "HERMES_KANBAN_TASK (the dispatcher's spelling) must trip the worker-"
        "registration gate on the -q path"
    )
