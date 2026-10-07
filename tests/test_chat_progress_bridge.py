"""The /qubit-chat work-panel producer: tool-progress -> hub CUSTOM frames.

These exercise the REAL seam (`ChatProgressBridge`), not a mock of it: a fake
hub records what `publish` receives, and `make_event` is the production
validator from `voicebridge.events.core` so a malformed frame fails here.
"""
from __future__ import annotations

import json

import pytest

from voicebridge.events.chat_progress import (
    ChatProgressBridge,
    build_progress_sink,
)
from voicebridge.events.core import make_event


class _RecHub:
    def __init__(self) -> None:
        self.records = []

    def publish(self, event):
        self.records.append(event)
        return event


def _bridge(session="sess-1"):
    hub = _RecHub()
    return hub, ChatProgressBridge(hub, session, make_event)


def _custom(rec):
    """Wire name + payload as the frontend `resolveActivityFrame` sees them."""
    assert rec["type"] == "CUSTOM"
    return rec["name"], rec["value"]


def test_subagent_start_publishes_custom_with_identity():
    hub, cb = _bridge()
    # The exact call shape tools/delegate_tool_progress._ChildProgressRelay._relay
    # produces for subagent.start.
    cb("subagent.start", None, "research the thing", None,
       subagent_id="sa-1", parent_id="p-1", depth=1, goal="research the thing",
       task_index=0, task_count=2, model="brain4", toolsets=["web"])
    name, value = _custom(hub.records[-1])
    assert name == "subagent.start"
    assert value["subagent_id"] == "sa-1"
    assert value["goal"] == "research the thing"
    assert value["depth"] == 1
    assert value["task_count"] == 2
    assert value["session_id"] == "sess-1"
    # run_id on the journal record enables hub-side run filtering (1c).
    assert hub.records[-1]["run_id"] == "sess-1"


def test_subagent_complete_and_legacy_spelling_normalized():
    hub, cb = _bridge()
    cb("subagent.complete", None, None, None, subagent_id="sa-1",
       status="ok", duration_seconds=1.5)
    assert _custom(hub.records[-1])[0] == "subagent.complete"
    assert hub.records[-1]["value"]["status"] == "ok"
    # Nested-summary legacy spelling must land on the frontend's vocabulary.
    cb("subagent_progress", "🔀 batch summary", None, None)
    assert _custom(hub.records[-1])[0] == "subagent.progress"


def test_todo_completed_projects_nonempty_state():
    hub, cb = _bridge()
    result = json.dumps({
        "todos": [
            {"id": "t1", "text": "ship the panel", "status": "in_progress"},
            {"id": "t2", "text": "write the test", "status": "completed"},
        ],
        "revision": 7,
    })
    # tool_executor fires tool.completed with result=<raw tool result>.
    cb("tool.completed", "todo_list", None, None, result=result)
    name, value = _custom(hub.records[-1])
    assert name == "todo.updated"
    assert value["revision"] == 7
    assert len(value["todos"]) == 2
    assert value["session_id"] == "sess-1"


def test_todo_ignores_non_todo_tools_and_malformed_results():
    hub, cb = _bridge()
    cb("tool.completed", "web_search", None, None,
       result=json.dumps({"todos": [{"id": "x"}], "revision": 1}))
    cb("tool.completed", "todo_list", None, None, result="not json")
    cb("tool.completed", "todo_list", None, None,
       result=json.dumps({"todos": "nope", "revision": 1}))
    assert hub.records == []


def test_empty_revision_zero_clear_is_dropped():
    hub, cb = _bridge()
    cb("tool.completed", "todo_list", None, None,
       result=json.dumps({"todos": [], "revision": 0}))
    assert hub.records == []


def test_bridge_never_raises_into_agent():
    class _Boom:
        def publish(self, event):
            raise RuntimeError("hub down")

    cb = ChatProgressBridge(_Boom(), "s", make_event)
    # Must be swallowed: a broken panel never breaks the turn.
    cb("subagent.start", None, "x", None, subagent_id="a")


def test_build_sink_none_without_hub_and_works_with_hub():
    class _Cfg:
        data = {}

    cfg = _Cfg()
    assert build_progress_sink(cfg, "s") is None
    hub = _RecHub()
    cfg._qubit_chat_hub = hub
    cfg._qubit_chat_make_event = make_event
    sink = build_progress_sink(cfg, "s")
    assert sink is not None
    sink("subagent.start", None, None, None, subagent_id="z")
    assert _custom(hub.records[-1])[0] == "subagent.start"
