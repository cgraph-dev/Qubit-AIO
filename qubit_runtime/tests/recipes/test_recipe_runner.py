import threading

import pytest

from agent.runtime_port import (
    RuntimeCapabilities,
    TurnEvent,
    UnknownRuntimeError,
)
from recipes.morning_digest import MORNING_DIGEST
from recipes.runner import build_prompt, run_recipe
from recipes.sections import available


class _FakeBackend:
    name = "fake"
    capabilities = RuntimeCapabilities(streaming=True, cancel=True, tools=False)

    def __init__(self, events):
        self.events = events
        self.requests = []

    def run_turn(self, request, cancel=None):
        self.requests.append(request)
        for event in self.events:
            yield event


class _Registry:
    def __init__(self, backends):
        self._backends = backends

    def get(self, name):
        if name not in self._backends:
            raise UnknownRuntimeError(name, sorted(self._backends))
        return self._backends[name]


def _sections():
    return (
        available("weather", "Weather", ["Sunny 20C"]),
        available("calendar", "Calendar", ["09:00 — Standup"]),
        available("unread_mail", "Unread mail", ["Invoice — from billing@example.com"]),
        available("health", "Health", []),
    )


def test_done_maps_to_a_brief_and_uses_collected_facts():
    backend = _FakeBackend([
        TurnEvent("delta", text="Good morning. "),
        TurnEvent("done", text="Good morning. Sunny, standup at 9."),
    ])
    run = run_recipe(
        MORNING_DIGEST, registry=_Registry({"fake": backend}),
        engine="fake", fixture=_sections())

    assert run.exit_code == 0
    assert run.brief == "Good morning. Sunny, standup at 9."
    assert run.terminal == "done"
    assert [event.kind for event in run.events] == ["delta", "done"]
    prompt = backend.requests[0].prompt
    assert "Sunny 20C" in prompt
    assert "09:00 — Standup" in prompt
    assert "Write the user's morning digest for" in prompt


def test_prompt_names_unavailable_sections_and_forbids_invention():
    from recipes.sections import unavailable

    sections = (
        available("weather", "Weather", ["Sunny"]),
        unavailable("health", "Health", "connector_not_configured", "no connector"),
    )
    prompt = build_prompt(MORNING_DIGEST, sections)
    assert "unavailable (connector_not_configured): no connector" in prompt
    assert "Never invent" in prompt


def test_typed_refusal_exits_two_and_keeps_the_code():
    backend = _FakeBackend([
        TurnEvent("error", text="openjarvis unavailable",
                  data={"refusal": "openjarvis_unavailable", "hint": "set QUBIT_OPENJARVIS_BIN"}),
    ])
    run = run_recipe(MORNING_DIGEST, registry=_Registry({"fake": backend}),
                     engine="fake", fixture=_sections())
    assert run.refusal == "openjarvis_unavailable"
    assert run.hint == "set QUBIT_OPENJARVIS_BIN"
    assert run.exit_code == 2


def test_failed_turn_exits_one():
    backend = _FakeBackend([TurnEvent("error", text="provider failed")])
    run = run_recipe(MORNING_DIGEST, registry=_Registry({"fake": backend}),
                     engine="fake", fixture=_sections())
    assert run.failed
    assert run.refusal == ""
    assert run.exit_code == 1


def test_cancelled_turn_exits_130():
    cancel = threading.Event()
    cancel.set()
    backend = _FakeBackend([
        TurnEvent("error", text="turn cancelled", data={"cancelled": True}),
    ])
    run = run_recipe(MORNING_DIGEST, registry=_Registry({"fake": backend}),
                     engine="fake", fixture=_sections(), cancel=cancel)
    assert run.cancelled
    assert run.exit_code == 130


def test_done_with_empty_text_is_a_failed_turn():
    backend = _FakeBackend([TurnEvent("done", text="")])
    run = run_recipe(MORNING_DIGEST, registry=_Registry({"fake": backend}),
                     engine="fake", fixture=_sections())
    assert run.failed
    assert run.exit_code == 1


def test_final_brief_is_redacted():
    backend = _FakeBackend([
        TurnEvent("done", text="key sk-abcdefghijklmnop leaked"),
    ])
    run = run_recipe(MORNING_DIGEST, registry=_Registry({"fake": backend}),
                     engine="fake", fixture=_sections())
    assert "sk-abcdefghijklmnop" not in run.brief


def test_dry_run_never_calls_the_backend():
    class _Explode(_FakeBackend):
        def run_turn(self, request, cancel=None):
            raise AssertionError("dry run must not call a model")
            yield

    backend = _Explode([])
    run = run_recipe(MORNING_DIGEST, registry=_Registry({"fake": backend}),
                     engine="fake", dry_run=True, fixture=_sections())
    assert run.exit_code == 0
    assert run.brief.startswith("# Morning digest")
    assert "Sunny 20C" in run.brief
    assert backend.requests == []


def test_unknown_engine_raises_before_collecting():
    with pytest.raises(UnknownRuntimeError):
        run_recipe(MORNING_DIGEST, registry=_Registry({}), engine="nope",
                   fixture=_sections())


def test_a_delta_only_stream_is_a_failed_run_not_a_brief():
    backend = _FakeBackend([TurnEvent("delta", text="NOT-A-BRIEF")])
    run = run_recipe(
        MORNING_DIGEST, registry=_Registry({"fake": backend}),
        engine="fake", fixture=_sections())

    assert run.failed is True
    assert run.exit_code == 1
    assert run.terminal == ""
    assert run.brief == ""


def test_unavailable_section_uses_the_spec_title():
    from dataclasses import replace

    spec = replace(
        MORNING_DIGEST,
        id="title_probe",
        sections=("weather",),
        collectors={},
        section_titles={"weather": "Weather"},
    )
    run = run_recipe(spec, dry_run=True, fixture=None,
                     collectors={})
    assert run.sections[0].title == "Weather"
