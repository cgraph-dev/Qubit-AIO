"""Bridge vendored agent progress events onto the /qubit-chat SSE hub.

The ``/qubit-chat`` work panel reads ``CUSTOM`` frames named ``todo.updated``
and ``subagent.*`` from the Packet 2B typed-event feed
(``voicebridge/webui.py:_events_stream``).  The client half
(``frontend/qubit-web/src/hooks/useChatActivity.ts``) unwraps the ``CUSTOM``
envelope and reduces those frames; the missing half was a **producer**: a
``/qubit-chat`` turn builds its own ``run_agent.AIAgent`` and never attached the
``tool_progress_callback`` that carries the real events.

Why one callback carries both event families
--------------------------------------------
``AIAgent.tool_progress_callback`` is the single feed the vendored runtime
already fires for:

* child-agent lifecycle — the delegation relay
  (``tools/delegate_tool_progress.py::_ChildProgressRelay._relay``) calls the
  parent callback with ``subagent.start`` / ``subagent.complete`` /
  ``subagent.text`` / ``subagent.progress`` / ``subagent.thinking`` /
  ``subagent.tool`` and threads the identity kwargs (``subagent_id``,
  ``goal``, ``depth``, ``tool_count`` …);
* the tool-result projection (``agent/tool_executor.py`` fires
  ``tool.completed`` with ``result=<raw tool result>``), from which the todo
  snapshot is normalized exactly as the TUI does
  (``tui_gateway/tool_progress.py::_normalize_todo_state`` + the todo-tool
  predicate).

The TUI's own ``todo.updated`` emitter is NOT reused: ``_emit`` writes
JSON-RPC frames onto the TUI client transport (``tui_gateway.server.write_json``),
a different process/transport than this hub.  Its *data* arrives on the same
``tool_progress_callback`` this bridge attaches to, so the payload is
reconstructed here rather than importing an unreachable transport.

Scoping
-------
Every frame is published with ``envelope_run_id=session_id`` so the hub's
``Subscriber.run_filter`` (``events/hub.py``) can scope a connection to one
session — the panel never renders another session's agents on a filtered feed.
The session id also rides inside ``value.session_id`` for clients that filter on
the payload (the wire strips the journal-only ``run_id`` for non-RUN_* events).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)

# Lifecycle strings the child relay emits (tools/delegate_tool_progress.py:122)
# plus the two the relay also produces (thinking/tool and the batched summary).
_SUBAGENT_EVENTS = frozenset({
    "subagent.start", "subagent.complete", "subagent.text",
    "subagent.progress", "subagent.thinking", "subagent.tool",
    "subagent_progress",  # legacy spelling the relay forwards for nested summaries
})

# Identity kwargs the relay threads into every subagent event; carried verbatim
# into the CUSTOM value so the roster can rebuild the live spawn tree.
_IDENTITY_KEYS = (
    "subagent_id", "parent_id", "child_session_id", "delegation_id",
    "task_index", "task_count", "goal", "depth", "model", "toolsets",
    "tool_count", "status", "duration_seconds", "summary", "failure_reason",
)

# Guard rail: a preview that is a giant tool dump is not panel material.
_MAX_PREVIEW = 2000


def _normalize_todo_state(value: object) -> Optional[dict]:
    """Client-safe full todo snapshot or ``None`` when malformed.

    Mirrors ``tui_gateway.tool_progress._normalize_todo_state`` (the producer's
    field names are the contract shared with the frontend reducer).  Kept as a
    local copy rather than importing the tui_gateway module: importing it pulls
    the whole TUI transport graph (``method_ctx``/``server``) into the webui
    process, which is exactly the coupling this bridge avoids.
    """
    if not isinstance(value, dict) or not isinstance(value.get("todos"), list):
        return None
    try:
        revision = max(0, int(value.get("revision") or 0))
    except (TypeError, ValueError):
        return None
    todos = list(value["todos"])
    if not todos and revision == 0:
        return None
    return {"todos": todos, "revision": revision}


def _is_todo_tool_name(name: Any) -> bool:
    """True for the Todo tool's current name or a legacy alias."""
    try:
        from tools.todo_tool import is_todo_tool_name

        return bool(is_todo_tool_name(name))
    except Exception:  # noqa: BLE001 - vendored runtime may be absent
        return isinstance(name, str) and name == "todo_list"


class ChatProgressBridge:
    """One turn's ``tool_progress_callback`` → ``CUSTOM`` frames on the hub.

    Constructed per turn with the session id; ``__call__`` matches the
    ``tool_progress_callback(event_type, tool_name=None, preview=None, args=None,
    **kwargs)`` signature the vendored runtime invokes.  Fail-soft by contract:
    a publish error is logged and swallowed — a broken panel must never break the
    turn.
    """

    def __init__(self, hub: Any, session_id: str,
                 make_event: Callable[..., Dict[str, Any]]) -> None:
        self._hub = hub
        self._session_id = str(session_id or "")
        self._make_event = make_event
        # The relay flushes ``subagent.tool`` batched into ``subagent.progress``;
        # both arrive here, so no state is needed.  Kept minimal on purpose.
        self._closed = False

    # -- publish -----------------------------------------------------------

    def _publish(self, name: str, value: Dict[str, Any]) -> None:
        if self._closed or self._hub is None:
            return
        try:
            event = self._make_event(
                "CUSTOM", name=name, value=value,
                envelope_run_id=self._session_id or None)
            self._hub.publish(event)
        except Exception:  # noqa: BLE001 - panel glue is never fatal
            log.debug("chat progress: publish %s failed", name, exc_info=True)

    # -- callback surface --------------------------------------------------

    def __call__(self, event_type: Any, tool_name: str = None,
                 preview: str = None, args: Any = None,
                 **kwargs: Any) -> None:
        try:
            self._dispatch(str(event_type or ""), tool_name, preview, kwargs)
        except Exception:  # noqa: BLE001 - never raise into the agent
            log.debug("chat progress: dispatch failed", exc_info=True)

    def _dispatch(self, event_type: str, tool_name: Optional[str],
                  preview: Optional[str], kwargs: Dict[str, Any]) -> None:
        if event_type in _SUBAGENT_EVENTS:
            self._emit_subagent(event_type, tool_name, preview, kwargs)
            return
        if event_type == "tool.completed":
            self._maybe_emit_todo(tool_name, kwargs)
            return

    def _emit_subagent(self, event_type: str, tool_name: Optional[str],
                       preview: Optional[str],
                       kwargs: Dict[str, Any]) -> None:
        # Frontend reducer matches ``subagent.*``; the legacy relay spelling is
        # normalized so the roster sees one vocabulary.
        name = "subagent.progress" if event_type == "subagent_progress" \
            else event_type
        value: Dict[str, Any] = {"session_id": self._session_id}
        if self._session_id:
            value["run_id"] = self._session_id
        for key in _IDENTITY_KEYS:
            if key in kwargs and kwargs[key] is not None:
                value[key] = kwargs[key]
        if tool_name:
            value.setdefault("tool_name", str(tool_name))
        if preview:
            text = str(preview)
            value["preview"] = text[:_MAX_PREVIEW]
        self._publish(name, value)

    def _maybe_emit_todo(self, tool_name: Optional[str],
                         kwargs: Dict[str, Any]) -> None:
        if not _is_todo_tool_name(tool_name):
            return
        raw = kwargs.get("result")
        if isinstance(raw, str):
            import json

            try:
                raw = json.loads(raw)
            except Exception:  # noqa: BLE001 - non-JSON result is not todo state
                return
        state = _normalize_todo_state(raw)
        if state is None:
            return
        value = dict(state)
        if self._session_id:
            value["session_id"] = self._session_id
            value["run_id"] = self._session_id
        self._publish("todo.updated", value)

    def close(self) -> None:
        self._closed = True


def build_progress_sink(cfg: Any, session_id: str) -> Optional[ChatProgressBridge]:
    """Return a bridge for ``session_id`` when the webui hub is available.

    The hub is attached to the shared ``cfg`` object at webui startup
    (``_events_hub`` + ``make_event``); a headless caller (no webui, no hub)
    gets ``None`` and the vendored runtime falls back to its no-op default —
    zero behavior change for every non-``/qubit-chat`` path.
    """
    hub = getattr(cfg, "_qubit_chat_hub", None)
    make_event = getattr(cfg, "_qubit_chat_make_event", None)
    if hub is None or make_event is None:
        return None
    try:
        return ChatProgressBridge(hub, session_id, make_event)
    except Exception:  # noqa: BLE001 - missing hub is not fatal
        return None
