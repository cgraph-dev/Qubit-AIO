"""P2-03 — framework-neutral agent adapter and registry (hermetic)."""
from __future__ import annotations

import contextvars

import pytest

from voicebridge.business import agent_adapter as aa
from voicebridge.business.errors import BusinessError
from _p2_helpers import FALLBACK, ROUTE, fake, registry, turn


# -- registry resolution ---------------------------------------------------
def test_unknown_engine_is_a_typed_refusal():
    reg = registry()
    with pytest.raises(BusinessError) as exc:
        reg.get("does-not-exist")
    assert exc.value.code == aa.BACKEND_UNKNOWN_CODE
    with pytest.raises(BusinessError) as exc:
        reg.start_turn("does-not-exist", turn())
    assert exc.value.code == aa.BACKEND_UNKNOWN_CODE


def test_duplicate_engine_registration_is_refused():
    reg = registry(fake())
    with pytest.raises(BusinessError) as exc:
        reg.register(fake(), engine="fake")
    assert exc.value.code == aa.BACKEND_DUPLICATE_CODE


def test_unsupported_engine_is_typed_and_never_falls_back():
    backend = fake(engine="openjarvis")
    reg = aa.AgentAdapterRegistry()
    reg.register(
        backend,
        availability="unsupported",
        reason="openjarvis_not_installed",
    )
    # Nothing else is registered: there is no native fallback to resolve to.
    assert reg.engines() == ("openjarvis",)
    with pytest.raises(aa.BackendUnavailable) as exc:
        reg.get("openjarvis")
    assert exc.value.code == aa.BACKEND_UNSUPPORTED_CODE
    assert "openjarvis_not_installed" in exc.value.hint
    with pytest.raises(aa.BackendUnavailable):
        reg.start_turn("openjarvis", turn())
    # Inspection still works so an operator can see the pinned state.
    assert reg.describe("openjarvis").availability == "unsupported"


def test_unavailable_engine_is_typed():
    reg = aa.AgentAdapterRegistry()
    reg.register(
        fake(engine="remote"),
        availability="unavailable",
        reason="provider_down",
    )
    with pytest.raises(aa.BackendUnavailable) as exc:
        reg.get("remote")
    assert exc.value.code == aa.BACKEND_UNAVAILABLE_CODE


def test_pinned_model_route_fallback_and_versions_are_carried():
    reg = aa.AgentAdapterRegistry()
    reg.register(
        fake(engine="native"),
        version="3.2.1",
        health="degraded",
        model_route=ROUTE,
        fallback=FALLBACK,
    )
    registration = reg.get("native")
    assert registration.version == "3.2.1"
    assert registration.health == "degraded"
    assert registration.model_route == ROUTE
    assert registration.fallback == FALLBACK
    descriptor = reg.descriptors()[0]
    assert descriptor["model_route"]["provider"] == "opencode-go"
    assert descriptor["fallback"][0]["model"] == "qubit-brain"
    assert descriptor["version"] == "3.2.1"


def test_turn_input_carries_the_route_to_the_backend():
    backend = fake()
    reg = registry(backend, model_route=ROUTE, fallback=FALLBACK)
    reg.collect(
        "fake",
        turn(model_route=ROUTE, fallback=FALLBACK),
    )
    assert backend.turns[0].model_route == ROUTE
    assert backend.turns[0].fallback == FALLBACK


# -- turn contract ---------------------------------------------------------
def test_fake_backend_events_keep_scripted_order():
    events = [
        aa.TurnEvent("delta", "a"),
        aa.TurnEvent("tool", "search", {"name": "search"}),
        aa.TurnEvent("delta", "b"),
        aa.TurnEvent("usage", "", {"tokens": 3}),
        aa.TurnEvent("done", "ab"),
    ]
    reg = registry(fake(events=events))
    rows = reg.collect("fake", turn())
    assert [row.kind for row in rows] == [
        "delta",
        "tool",
        "delta",
        "usage",
        "done",
    ]
    assert rows[-1].kind == "done"
    assert sum(row.kind in aa.TERMINAL_KINDS for row in rows) == 1


def test_cancel_mid_turn_emits_exactly_one_terminal_event():
    backend = fake(chunks=("one", "two", "three", "four"))
    reg = registry(backend)
    iterator = reg.start_turn("fake", turn())
    assert next(iterator).text == "one"
    reg.cancel("fake")
    assert backend.cancel_calls == 1
    rest = aa.drain_events(iterator)
    assert [row.kind for row in rest] == ["done"]
    assert rest[0].data["cancelled"] is True


def test_cancel_before_the_first_event_still_terminates():
    reg = registry(fake())
    reg.cancel("fake")
    rows = reg.collect("fake", turn())
    assert rows == (aa.TurnEvent("done", data={"cancelled": True}),)


def test_missing_terminal_is_synthesized_as_one_error():
    def run(_turn):
        return [aa.TurnEvent("delta", "partial")]

    reg = registry(aa.CallbackBackend("cb", run))
    rows = reg.collect("cb", turn())
    assert [row.kind for row in rows] == ["delta", "error"]
    assert sum(row.kind in aa.TERMINAL_KINDS for row in rows) == 1
    assert rows[-1].data["code"] == aa.TERMINAL_MISSING_CODE


def test_event_after_terminal_is_not_emitted():
    reg = registry(
        fake(
            events=[
                aa.TurnEvent("done", "ok"),
                aa.TurnEvent("delta", "late"),
            ]
        )
    )
    rows = reg.collect("fake", turn())
    assert rows == (aa.TurnEvent("done", "ok"),)


def test_oversized_text_becomes_one_typed_terminal_error():
    caps = aa.BackendCapabilities(max_event_bytes=1024, max_event_text_chars=8)
    reg = registry(
        fake(
            capabilities=caps,
            events=[
                aa.TurnEvent("delta", "x" * 40),
                aa.TurnEvent("done"),
            ],
        )
    )
    rows = reg.collect("fake", turn())
    assert len(rows) == 1
    assert rows[0].kind == "error"
    assert rows[0].data["code"] == aa.EVENT_TOO_LARGE_CODE


def test_oversized_data_becomes_one_typed_terminal_error():
    caps = aa.BackendCapabilities(max_event_bytes=16, max_event_text_chars=64)
    reg = registry(
        fake(
            capabilities=caps,
            events=[
                aa.TurnEvent("delta", "ok", {"blob": "y" * 100}),
                aa.TurnEvent("done"),
            ],
        )
    )
    rows = reg.collect("fake", turn())
    assert len(rows) == 1
    assert rows[0].data["code"] == aa.EVENT_TOO_LARGE_CODE


def test_unknown_event_kind_becomes_one_typed_terminal_error():
    reg = registry(
        fake(events=[aa.TurnEvent("teleport", "?"), aa.TurnEvent("done")])
    )
    rows = reg.collect("fake", turn())
    assert len(rows) == 1
    assert rows[0].data["code"] == aa.EVENT_INVALID_CODE


def test_invalid_turn_input_is_a_typed_refusal():
    reg = registry()
    with pytest.raises(BusinessError) as exc:
        reg.start_turn("fake", {"prompt": "x"})
    assert exc.value.code == aa.TURN_INVALID_CODE
    with pytest.raises(BusinessError) as exc:
        reg.start_turn("fake", turn(prompt="   "))
    assert exc.value.code == aa.TURN_INVALID_CODE


# -- injected callables ----------------------------------------------------
def test_callback_backend_wraps_an_injected_callable():
    seen: list[aa.TurnInput] = []

    def run(turn_input):
        seen.append(turn_input)
        yield aa.TurnEvent("delta", "cb")
        yield aa.TurnEvent("done", "cb")

    reg = registry(aa.CallbackBackend("cb", run))
    rows = reg.collect("cb", turn("hello"))
    assert [row.text for row in rows] == ["cb", "cb"]
    assert seen[0].prompt == "hello"


def test_native_backend_adapts_a_runtime_port_shaped_object():
    class Runtime:
        def __init__(self):
            self.requests = []

        def run_turn(self, request):
            self.requests.append(request)
            return [aa.TurnEvent("done", "native")]

    runtime = Runtime()
    backend = aa.NativeBackend.from_runtime_backend(
        "native", runtime, request_factory=lambda turn_input: turn_input.prompt
    )
    reg = registry(backend)
    rows = reg.collect("native", turn("port"))
    assert rows[-1].text == "native"
    assert runtime.requests == ["port"]


# -- context propagation ---------------------------------------------------
def test_backend_sees_the_caller_context_snapshot():
    marker = contextvars.ContextVar("p2_marker", default="unset")

    def run(_turn):
        yield aa.TurnEvent("delta", marker.get())
        yield aa.TurnEvent("done")

    reg = registry(aa.CallbackBackend("cb", run))
    marker.set("before")
    iterator = reg.start_turn("cb", turn())
    marker.set("after")
    rows = tuple(iterator)
    assert rows[0].text == "before"


def test_backend_exception_becomes_one_typed_terminal_error():
    def run(_turn):
        yield aa.TurnEvent("delta", "started")
        raise RuntimeError("provider exploded")

    reg = registry(aa.CallbackBackend("cb", run))
    rows = reg.collect("cb", turn())
    assert [row.kind for row in rows] == ["delta", "error"]
    assert rows[-1].data["code"] == aa.BACKEND_FAILED_CODE
    assert "RuntimeError" in rows[-1].text
