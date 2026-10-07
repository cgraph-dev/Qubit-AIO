"""Typed AG-UI-compatible event contract + journal + stream (Packets 2A/2B).

Public surface for the Phase 2 event stream. 2A: contract, journal, RFC 6902
subset. 2B: hub replay, run coordination, snapshot/delta tracking, SSE
serving. Upstream provenance: docs/archive/2026-09-21/UPSTREAM_REUSE.md R1/R15.
"""
from .core import (CONTENT_TYPE, SCHEMA_VERSION, TERMINAL_EVENT_TYPES,
                   EventEncoder, EventValidationError, EventType,
                   decode_sse, encode_sse, from_wire, iter_wire_lines,
                   make_event, now_ms, to_wire)
from .journal import EventJournal
from .hub import EventHub, resync_frame
from .runs import RunCoordinator, StateTracker
from .state import PatchError, PatchUnsupportedError, apply_patch, diff_state

__all__ = [
    "CONTENT_TYPE", "SCHEMA_VERSION", "TERMINAL_EVENT_TYPES",
    "EventEncoder", "EventHub", "EventJournal", "EventValidationError",
    "EventType", "PatchError", "PatchUnsupportedError", "RunCoordinator",
    "StateTracker",
    "apply_patch", "decode_sse", "diff_state", "encode_sse", "from_wire",
    "iter_wire_lines", "make_event", "now_ms", "resync_frame", "to_wire",
]
