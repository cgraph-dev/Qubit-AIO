"""AG-UI-compatible event contract (Packet 2A).

Adapted from AG-UI sdks/python/ag_ui/core/events.py, ag_ui/core/types.py and
ag_ui/encoder/encoder.py at pinned commit 821b8c227c6fe7b78de2c8259173dd7cbbdf181b
(MIT, Copyright (c) 2025). See docs/UPSTREAM_REUSE.md R1 and
THIRD_PARTY_NOTICES.md. stdlib-only: the pydantic validation machinery is
intentionally replaced by explicit per-type field specs (same wire contract,
fail-closed validation, no undeclared dependency).

Wire contract (pinned behavior kept):
- event ``type`` strings verbatim from the upstream EventType enum;
- snake_case local keys, camelCase wire keys (messageId, toolCallId,
  toolCallName, parentMessageId, parentRunId, threadId, stepName,
  activityType, rawEvent);
- optional-None fields omitted centrally on every serialization path;
- SSE frame ``data: <single-line json>\\n\\n``, content type text/event-stream;
- STATE_DELTA carries an ordered RFC 6902 patch list against the last
  STATE_SNAPSHOT baseline (see voicebridge.events.state).

Local envelope (journal-only, stripped from AG-UI wire output): ``seq``,
``schema``, plus ``run_id``/``parent_id`` correlation on event types whose
pinned AG-UI shape lacks them. ``RUN_CANCELLED`` is a documented local
addition (plan Phase 2 requires an explicit terminal cancel event; pinned
AG-UI maps aborts onto RUN_ERROR).
"""
from __future__ import annotations

import json
import re
import time
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = "v1"
CONTENT_TYPE = "text/event-stream"


class EventType(str, Enum):
    """AG-UI event types (subset the bridge emits) plus local RUN_CANCELLED."""

    TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TEXT_MESSAGE_END = "TEXT_MESSAGE_END"
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
    TOOL_CALL_END = "TOOL_CALL_END"
    TOOL_CALL_RESULT = "TOOL_CALL_RESULT"
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    STATE_DELTA = "STATE_DELTA"
    MESSAGES_SNAPSHOT = "MESSAGES_SNAPSHOT"
    ACTIVITY_SNAPSHOT = "ACTIVITY_SNAPSHOT"
    ACTIVITY_DELTA = "ACTIVITY_DELTA"
    RAW = "RAW"
    CUSTOM = "CUSTOM"
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"
    STEP_STARTED = "STEP_STARTED"
    STEP_FINISHED = "STEP_FINISHED"
    RUN_CANCELLED = "RUN_CANCELLED"  # local addition (divergence, see above)


TERMINAL_EVENT_TYPES = frozenset({
    EventType.RUN_FINISHED, EventType.RUN_ERROR, EventType.RUN_CANCELLED,
})

_TEXT_ROLES = ("developer", "system", "assistant", "user")

# Per-type field specs mirroring the pinned events.py classes for the adapted
# subset: (required, optional). ``raw_event`` is optional on every type
# (upstream BaseEvent). Envelope keys (seq/schema/run_id/parent_id) are NOT
# declared here; make_event handles them separately.
_EVENT_FIELDS: Dict[str, tuple] = {
    "TEXT_MESSAGE_START": ({"message_id"}, {"role", "name"}),
    "TEXT_MESSAGE_CONTENT": ({"message_id", "delta"}, set()),
    "TEXT_MESSAGE_END": ({"message_id"}, set()),
    "TOOL_CALL_START": ({"tool_call_id", "tool_call_name"}, {"parent_message_id"}),
    "TOOL_CALL_ARGS": ({"tool_call_id", "delta"}, set()),
    "TOOL_CALL_END": ({"tool_call_id"}, set()),
    "TOOL_CALL_RESULT": ({"message_id", "tool_call_id", "content"}, {"role"}),
    "STATE_SNAPSHOT": ({"snapshot"}, set()),
    "STATE_DELTA": ({"delta"}, set()),
    "MESSAGES_SNAPSHOT": ({"messages"}, set()),
    "ACTIVITY_SNAPSHOT": ({"message_id", "activity_type", "content"}, {"replace"}),
    "ACTIVITY_DELTA": ({"message_id", "activity_type", "patch"}, set()),
    "RAW": ({"event"}, {"source"}),
    "CUSTOM": ({"name", "value"}, set()),
    "RUN_STARTED": ({"thread_id", "run_id"}, {"parent_run_id"}),
    "RUN_FINISHED": ({"thread_id", "run_id"}, {"result"}),
    "RUN_ERROR": ({"message"}, {"code"}),
    "STEP_STARTED": ({"step_name"}, set()),
    "STEP_FINISHED": ({"step_name"}, set()),
    "RUN_CANCELLED": ({"thread_id", "run_id"}, {"reason"}),
}

_STR_FIELDS = {
    "message_id", "name", "delta", "tool_call_id", "tool_call_name",
    "parent_message_id", "content", "source", "thread_id", "run_id",
    "parent_run_id", "message", "code", "step_name", "reason",
}

_CAMEL_RE = re.compile(r"_([a-zA-Z0-9])")


def _to_camel(key: str) -> str:
    return _CAMEL_RE.sub(lambda m: m.group(1).upper(), key)


def _to_snake(key: str) -> str:
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower()


def _now_ms() -> int:
    return int(time.time() * 1000)


class EventValidationError(ValueError):
    """Raised on malformed or unknown event input (fail closed)."""


def make_event(type_: "EventType | str", *, timestamp: Optional[int] = None,
               seq: Optional[int] = None, schema: str = SCHEMA_VERSION,
               envelope_run_id: Optional[str] = None,
               envelope_parent_id: Optional[str] = None,
               redacted: bool = False, **fields: Any) -> Dict[str, Any]:
    """Build one validated event dict (snake_case keys, local envelope included).

    Envelope correlation: ``envelope_run_id``/``envelope_parent_id`` carry the
    journal-scoped run correlation for every type; for RUN_* types callers
    normally pass the spec'd ``run_id``/``parent_run_id``/``thread_id`` fields
    instead, which survive on the wire.
    """
    etype = EventType(type_)
    required, optional = _EVENT_FIELDS.get(etype.value, (set(), set()))
    allowed = required | optional | {"raw_event"}
    unknown = set(fields) - allowed
    if unknown:
        raise EventValidationError(f"{etype.value}: unknown fields {sorted(unknown)}")
    missing = {k for k in required if fields.get(k) is None}
    if missing:
        raise EventValidationError(f"{etype.value}: missing required {sorted(missing)}")
    for key, val in fields.items():
        if val is None:
            continue
        if etype is EventType.STATE_DELTA and key == "delta":
            if not isinstance(val, list):
                raise EventValidationError("STATE_DELTA.delta must be a list of RFC 6902 ops")
            continue
        if etype is EventType.STATE_SNAPSHOT and key == "snapshot" and not isinstance(val, dict):
            raise EventValidationError("STATE_SNAPSHOT.snapshot must be a dict")
        if key == "role":
            ok = val in (_TEXT_ROLES if etype is EventType.TEXT_MESSAGE_START
                         else ("tool",) if etype is EventType.TOOL_CALL_RESULT else _TEXT_ROLES)
            if not ok:
                raise EventValidationError(f"{etype.value}.role invalid: {val!r}")
            continue
        if key in _STR_FIELDS and not isinstance(val, str):
            raise EventValidationError(f"{etype.value}.{key} must be str")
    event: Dict[str, Any] = {"type": etype.value,
                             "timestamp": int(timestamp) if timestamp is not None else _now_ms()}
    if seq is not None:
        event["seq"] = int(seq)
    if schema:
        event["schema"] = schema
    if envelope_run_id is not None:
        event["run_id"] = envelope_run_id
    if envelope_parent_id is not None:
        event["parent_id"] = envelope_parent_id
    if redacted:
        event["redacted"] = True
    if etype is EventType.TEXT_MESSAGE_START:
        fields.setdefault("role", "assistant")
    if etype is EventType.ACTIVITY_SNAPSHOT:
        fields.setdefault("replace", True)
    event.update(fields)
    return event


_ENVELOPE_WIRE_KEYS = {"seq", "schema", "redacted"}


def to_wire(event: Dict[str, Any]) -> Dict[str, Any]:
    """AG-UI wire view: camelCase top-level keys, None values omitted centrally.

    Top-level event keys only; nested payloads (snapshot state, custom values,
    raw events, message lists) are opaque application data and pass through
    untransformed — same as the pinned encoder, which aliases model fields but
    never rewrites payload contents.
    """
    etype = EventType(str(event.get("type")))
    run_scoped = etype in (EventType.RUN_STARTED, EventType.RUN_FINISHED,
                           EventType.RUN_CANCELLED)
    wire: Dict[str, Any] = {}
    for key, val in event.items():
        if val is None or key in _ENVELOPE_WIRE_KEYS:
            continue
        if key in ("run_id", "parent_id") and not run_scoped:
            continue  # journal-only correlation outside the pinned RUN_* shapes
        wire[_to_camel(key)] = val
    return wire


def encode_sse(event: Dict[str, Any]) -> str:
    """Frame one event as ``data: <compact json>\\n\\n`` (pinned framing)."""
    body = json.dumps(to_wire(event), separators=(",", ":"), ensure_ascii=False)
    return f"data: {body}\n\n"


def decode_sse(frame: str) -> List[Dict[str, Any]]:
    """Parse SSE frames back into wire dicts (tolerates CRLF and comments)."""
    events: List[Dict[str, Any]] = []
    for block in re.split(r"\n\r?\n", frame.strip("\r\n")):
        data = [line[5:].lstrip(" ") for line in block.splitlines()
                if line.startswith("data:")]
        if data and any(d.strip() for d in data):
            events.append(json.loads("".join(data)))
    return events


class EventEncoder:
    """Pinned encoder surface: content type plus SSE encoding."""

    def __init__(self, accept: Optional[str] = None) -> None:
        self._accept = accept  # proto media type intentionally not supported

    def get_content_type(self) -> str:
        return CONTENT_TYPE

    def encode(self, event: Dict[str, Any]) -> str:
        return encode_sse(event)


def from_wire(payload: "str | bytes | Dict[str, Any]") -> Dict[str, Any]:
    """Validate a wire payload and restore the local snake_case event view.

    Accepts either key style per field (populate-by-name analog) but rejects
    ambiguous duplicates, unknown fields, unknown types, and missing required
    fields — malformed input fails closed.
    """
    obj = json.loads(payload) if isinstance(payload, (str, bytes)) else dict(payload)
    if not isinstance(obj, dict) or "type" not in obj:
        raise EventValidationError("event payload must be an object with a type")
    try:
        etype = EventType(str(obj["type"]))
    except ValueError as exc:
        raise EventValidationError(f"unknown event type: {obj.get('type')!r}") from exc
    required, optional = _EVENT_FIELDS.get(etype.value, (set(), set()))
    allowed = required | optional | {"raw_event", "type", "timestamp"}
    fields: Dict[str, Any] = {}
    for key, val in obj.items():
        snake = key if key in ("type", "timestamp") else _to_snake(key)
        if snake != key and snake in obj:
            raise EventValidationError(f"ambiguous key pair: {key}/{snake}")
        if snake in ("type", "timestamp"):
            continue
        if snake not in allowed:
            raise EventValidationError(f"{etype.value}: unknown wire field {key!r}")
        if val is None:
            continue
        fields[snake] = val
    missing = {k for k in required if fields.get(k) is None}
    if missing:
        raise EventValidationError(f"{etype.value}: missing required {sorted(missing)}")
    return make_event(etype, timestamp=obj.get("timestamp"), **fields)


def iter_wire_lines(frames: Iterable[str]):
    for frame in frames:
        for ev in decode_sse(frame):
            yield ev


def now_ms() -> int:
    return _now_ms()
