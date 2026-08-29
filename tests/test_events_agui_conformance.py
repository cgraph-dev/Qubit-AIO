"""Packet 2A conformance: local event contract vs pinned AG-UI fixtures.

Each test mirrors the identically-named upstream vector in
sdks/python/tests/test_encoder.py @ ag-ui commit
821b8c227c6fe7b78de2c8259173dd7cbbdf181b (MIT) — see docs/UPSTREAM_REUSE.md R1.
The local adaptation is stdlib-only (dicts + explicit field specs instead of
pydantic), so pydantic-specific assertions (model_dump_json / model_validate)
map onto to_wire/encode_sse/from_wire round-trips.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge.events import (CONTENT_TYPE, EventEncoder, EventType,
                                encode_sse, from_wire, make_event)


# --- upstream test_encoder_initialization -----------------------------------

def test_encoder_initialization():
    encoder = EventEncoder()
    assert isinstance(encoder, EventEncoder)
    encoder_with_accept = EventEncoder(accept="application/vnd.ag-ui.event+proto")
    assert isinstance(encoder_with_accept, EventEncoder)
    # pinned get_content_type behavior
    assert encoder.get_content_type() == CONTENT_TYPE == "text/event-stream"


# --- upstream test_encode_method --------------------------------------------

def test_encode_method():
    timestamp = 1648214400000
    event = make_event(EventType.RAW, timestamp=timestamp,
                       raw_event=None, event={"kind": "internal"})
    encoder = EventEncoder()
    encoded = encoder.encode(event)
    # SSE format with compact json body
    assert encoded.startswith("data: ") and encoded.endswith("\n\n")
    assert '"type":"RAW"' in encoded          # compact separators, like pydantic
    assert '"timestamp":1648214400000' in encoded
    assert "rawEvent" not in encoded          # None omitted centrally
    assert "raw_event" not in encoded


# --- upstream test_encode_sse_method ----------------------------------------

def test_encode_sse_method():
    event = make_event(EventType.TEXT_MESSAGE_CONTENT,
                       message_id="msg_123", delta="Hello, world!",
                       timestamp=1648214400000)
    encoded_sse = encode_sse(event)

    assert encoded_sse.startswith("data: ")
    assert encoded_sse.endswith("\n\n")

    decoded = json.loads(encoded_sse[6:-2])

    assert decoded["type"] == "TEXT_MESSAGE_CONTENT"
    assert decoded["messageId"] == "msg_123"      # snake_case -> camelCase wire
    assert decoded["delta"] == "Hello, world!"
    assert decoded["timestamp"] == 1648214400000

    assert "messageId" in decoded                 # camelCase key exists
    assert "message_id" not in decoded            # snake_case key absent


# --- upstream test_encode_with_different_event_types ------------------------

def test_encode_with_different_event_types():
    encoder = EventEncoder()

    base_event = make_event(EventType.RAW, timestamp=1648214400000,
                            event="x")
    encoded_base = encoder.encode(base_event)
    assert '"type":"RAW"' in encoded_base

    content_event = make_event(EventType.TEXT_MESSAGE_CONTENT,
                               message_id="msg_456",
                               delta="Testing different events",
                               timestamp=1648214400000)
    encoded_content = encoder.encode(content_event)

    assert '"type":"TEXT_MESSAGE_CONTENT"' in encoded_content
    assert '"messageId":"msg_456"' in encoded_content
    assert '"delta":"Testing different events"' in encoded_content

    json_content = encoded_content.split("data: ")[1].rstrip("\n\n")
    decoded = json.loads(json_content)
    assert "messageId" in decoded
    assert "message_id" not in decoded


# --- upstream test_null_value_exclusion -------------------------------------

def test_null_value_exclusion():
    event = make_event(EventType.RAW, timestamp=1648214400000,
                       raw_event=None, event={"any": "payload"})
    encoded = EventEncoder().encode(event)
    decoded = json.loads(encoded.split("data: ")[1].rstrip("\n\n"))

    assert "type" in decoded
    assert "timestamp" in decoded
    assert "rawEvent" not in decoded              # explicitly-None optional gone

    tool_start = make_event(EventType.TOOL_CALL_START,
                            tool_call_id="call_123",
                            tool_call_name="test_tool",
                            parent_message_id=None,
                            timestamp=1648214400000)
    encoded_optional = EventEncoder().encode(tool_start)
    decoded_optional = json.loads(
        encoded_optional.split("data: ")[1].rstrip("\n\n"))

    assert "toolCallId" in decoded_optional       # required fields survive
    assert "toolCallName" in decoded_optional
    assert "parentMessageId" not in decoded_optional  # None omitted


# --- upstream test_round_trip_serialization ---------------------------------

def test_round_trip_serialization():
    original = make_event(EventType.TOOL_CALL_START,
                          tool_call_id="call_abc123",
                          tool_call_name="search_tool",
                          parent_message_id="msg_parent_456",
                          timestamp=1648214400000)

    from voicebridge.events import to_wire
    wire = to_wire(original)

    assert "toolCallId" in wire and "toolCallName" in wire \
        and "parentMessageId" in wire
    assert "tool_call_id" not in wire and "tool_call_name" not in wire \
        and "parent_message_id" not in wire

    json_str = json.dumps(wire, separators=(",", ":"))
    deserialized = from_wire(json_str)

    assert deserialized["type"] == original["type"]
    for snake in ("tool_call_id", "tool_call_name", "parent_message_id",
                  "timestamp"):
        assert deserialized[snake] == original[snake], snake

    # envelope-free equality through the full wire round trip
    assert to_wire(deserialized) == to_wire(original)


def test_run_lifecycle_wire_shapes_match_pinned_specs():
    """RUN_STARTED/RUN_FINISHED carry threadId/runId(/parentRunId); RUN_ERROR
    carries only message/code at the pin — journal correlation stays local."""
    started = make_event(EventType.RUN_STARTED, thread_id="t1", run_id="r1",
                         parent_run_id=None, timestamp=1)
    finished = make_event(EventType.RUN_FINISHED, thread_id="t1", run_id="r1",
                          result=None, timestamp=2)
    error = make_event(EventType.RUN_ERROR, message="boom", code=None,
                       envelope_run_id="r1", timestamp=3)
    cancelled = make_event(EventType.RUN_CANCELLED, thread_id="t1", run_id="r1",
                           reason=None, timestamp=4)

    s = json.loads(encode_sse(started)[6:-2])
    f = json.loads(encode_sse(finished)[6:-2])
    e = json.loads(encode_sse(error)[6:-2])
    c = json.loads(encode_sse(cancelled)[6:-2])

    assert s["type"] == "RUN_STARTED" and s["threadId"] == "t1" and s["runId"] == "r1"
    assert "parentRunId" not in s                  # None omitted
    assert f["runId"] == "r1" and "result" not in f
    assert e["message"] == "boom" and "code" not in e
    assert "runId" not in e                        # pinned RUN_ERROR has no runId
    assert c["type"] == "RUN_CANCELLED" and c["runId"] == "r1"
