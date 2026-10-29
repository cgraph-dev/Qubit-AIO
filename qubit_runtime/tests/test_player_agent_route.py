"""The session-bound player route: closed validation and typed refusals, no session scan."""

from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter, ValidationError

import tui_gateway.server as server
from tui_gateway.contracts.server_requests import PlayerAgentOperation

_route = server.request_player_agent_for_session


@pytest.fixture(autouse=True)
def _ui_session(monkeypatch):
    monkeypatch.setattr("gateway.session_context.get_session_env", lambda name, default="": "ui-sess")


@pytest.fixture
def record():
    """A live UI session dict whose agent's durable id differs from the UI id."""
    calls = []

    class Agent:
        session_id = "durable"
        _interrupt_requested = False

        def player_agent_callback(self, operation):
            calls.append(operation)
            return json.dumps({"ok": True, "operation": operation.get("type")})

    session = {"agent": Agent(), "_finalized": False}
    return session, calls


def _wire(monkeypatch, session, *, transport=object(), current=None, stored=None):
    monkeypatch.setattr(server, "_current_session_steer_authority", lambda sid: (transport, session))
    monkeypatch.setattr(server, "_sessions", {"ui-sess": session if stored is None else stored})
    if current is None:
        current = session
    monkeypatch.setattr(server, "_current_runtime_session_record", _ConstVar(current))


class _ConstVar:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def test_valid_operation_reaches_the_agent_callback(monkeypatch, record):
    session, calls = record
    _wire(monkeypatch, session)
    result = json.loads(_route("durable", {"type": "context"}))
    assert result == {"ok": True, "operation": "context"}
    assert calls == [{"type": "context"}]


def test_operation_is_normalized_before_forwarding(monkeypatch, record):
    session, calls = record
    _wire(monkeypatch, session)
    _route("durable", {"type": "download.start", "context_token": "t" * 24})
    assert calls == [{"type": "download.start", "context_token": "t" * 24}]


@pytest.mark.parametrize("session_id", ["", None, "   "])
def test_empty_session_is_refused(monkeypatch, session_id):
    assert json.loads(_route(session_id, {"type": "context"})) == {"error": "session-unavailable"}


@pytest.mark.parametrize("operation", [
    {"type": "unknown"},
    {"type": "context", "extra": 1},
    {"type": "control", "context_token": "short", "navigation_generation": 1, "action": "play"},
    {"type": "job.status", "job_id": "bad id"},
])
def test_invalid_operation_is_refused(monkeypatch, operation):
    assert json.loads(_route("durable", operation)) == {"error": "invalid-operation"}


@pytest.mark.parametrize("denial", [
    "durable-mismatch", "finalized", "no-agent", "no-callback", "replaced-record", "stale-record", "no-transport",
])
def test_stale_or_mismatched_session_is_a_typed_refusal(monkeypatch, record, denial):
    session, calls = record

    if denial == "durable-mismatch":
        session["agent"].session_id = "other"
        _wire(monkeypatch, session)
    elif denial == "finalized":
        session["_finalized"] = True
        _wire(monkeypatch, session)
    elif denial == "no-agent":
        session["agent"] = None
        _wire(monkeypatch, session)
    elif denial == "no-callback":
        session["agent"].player_agent_callback = None
        _wire(monkeypatch, session)
    elif denial == "replaced-record":
        _wire(monkeypatch, session, stored={"agent": session["agent"]})
    elif denial == "stale-record":
        _wire(monkeypatch, session, current={"agent": session["agent"]})
    else:
        _wire(monkeypatch, session, transport=None)

    assert json.loads(_route("durable", {"type": "context"})) == {"error": "desktop-player-unavailable"}
    assert calls == []


def test_callback_exception_is_a_typed_refusal(monkeypatch, record):
    session, _calls = record

    def boom(operation):
        raise RuntimeError("renderer gone")

    session["agent"].player_agent_callback = boom
    _wire(monkeypatch, session)
    assert json.loads(_route("durable", {"type": "context"})) == {"error": "desktop-player-unavailable"}


def test_contract_rejects_open_or_ill_typed_operations():
    adapter = TypeAdapter(PlayerAgentOperation)
    assert adapter.validate_python({"type": "context"}).type == "context"
    for bad in [
        {"type": "control", "context_token": "t" * 24, "navigation_generation": 1, "action": "seek"},
        {"type": "control", "context_token": "t" * 24, "navigation_generation": 1, "action": "play",
         "position_seconds": 4},
        {"type": "transcript.start", "context_token": "t" * 24, "language": "e"},
        {"type": "job.cancel", "job_id": "bad id"},
        {"type": "download.start", "context_token": "t" * 24, "quality": "4k"},
    ]:
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)
