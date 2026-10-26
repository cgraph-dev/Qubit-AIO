import dataclasses

import pytest

from agent.runtime_port import (
    RuntimeBackend, RuntimeCapabilities, TurnEvent, TurnRequest, UnknownRuntimeError,
)


def test_request_defaults_are_immutable():
    req = TurnRequest(prompt="hi", session_id="s1")
    assert req.history == ()
    assert req.system is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        req.prompt = "changed"


def test_event_payload_is_plain_and_bounded():
    ev = TurnEvent("done", text="ok", data={"completed": True})
    assert ev.kind == "done" and ev.data["completed"] is True


def test_protocol_is_satisfied_by_a_fake_backend():
    class Fake:
        name = "fake"
        capabilities = RuntimeCapabilities(streaming=False, cancel=False, tools=False, images=False)

        def run_turn(self, request, cancel=None):
            yield TurnEvent("delta", text="a")
            yield TurnEvent("done", text="a")

    assert isinstance(Fake(), RuntimeBackend)


def test_unknown_runtime_error_lists_the_registered_names():
    exc = UnknownRuntimeError("nope", ["native"])
    assert isinstance(exc, ValueError)
    assert "native" in str(exc) and "nope" in str(exc)
