"""Native (vendored AIAgent) RuntimePort adapter.

The caller supplies the agent factory; this module owns only the turn/event
mapping so later engines share one contract. The turn runs on a worker thread
because ``run_conversation`` is synchronous while the port yields events as
they arrive.

Exactly one terminal event is emitted: the first of the turn's result, an
exception (including an agent-construction failure), or the cancellation
deadline. Later events from a stubborn turn are discarded.
"""
from __future__ import annotations

import queue
import threading
from typing import Any, Callable, Iterator

from agent.runtime_port import RuntimeCapabilities, TurnEvent, TurnRequest

_TERMINAL = ("done", "error")


class NativeRuntime:
    name = "native"
    capabilities = RuntimeCapabilities(streaming=True, cancel=True, tools=True, images=True)

    def __init__(
        self,
        agent_factory: Callable[[TurnRequest], Any],
        *,
        cancel_grace_seconds: float = 5.0,
    ) -> None:
        """*cancel* is a ``threading.Event`` (``wait``/``is_set``); the port types it
        loosely so later engines can adapt their own token. ``request.session_id`` is
        advisory for the native adapter: the caller's factory owns session wiring."""
        self._agent_factory = agent_factory
        self._cancel_grace = max(0.0, float(cancel_grace_seconds))

    def run_turn(self, request: TurnRequest, cancel: Any | None = None) -> Iterator[TurnEvent]:
        events: "queue.Queue[TurnEvent | None]" = queue.Queue()
        state = {"terminal": False}
        terminal_lock = threading.Lock()
        turn_done = threading.Event()
        agent_box: dict[str, Any] = {}

        def _emit(event: TurnEvent) -> None:
            events.put(event)

        def _terminal(event: TurnEvent) -> None:
            with terminal_lock:
                if state["terminal"]:
                    return
                state["terminal"] = True
            events.put(event)

        def _interrupt(agent: Any) -> None:
            try:
                agent.interrupt(
                    "runtime port cancel", hard_cancel=True, tool_reason="runtime_port.cancel")
            except Exception:
                pass

        def _watch_cancel() -> None:
            if cancel is None:
                return
            # Poll so a normal completion releases the watcher (no thread leak).
            while not cancel.wait(0.1):
                if turn_done.is_set():
                    return
            agent = agent_box.get("agent")
            if agent is not None:
                _interrupt(agent)
            if not turn_done.wait(self._cancel_grace):
                _terminal(TurnEvent(
                    "error", text="turn cancelled",
                    data={"interrupted": True, "cancelled": True},
                ))

        def _run() -> None:
            try:
                agent = self._agent_factory(request)
                agent_box["agent"] = agent
                if cancel is not None and cancel.is_set():
                    _interrupt(agent)
                result = agent.run_conversation(
                    request.prompt,
                    system_message=request.system,
                    conversation_history=list(request.history) or None,
                    task_id=request.task_id,
                    stream_callback=lambda chunk: _emit(TurnEvent("delta", text=str(chunk))),
                )
                result = result if isinstance(result, dict) else {}
                if result.get("completed") is False:
                    _terminal(TurnEvent(
                        "error",
                        text=str(result.get("error") or "turn did not complete"),
                        data={"interrupted": bool(
                            result.get("interrupted") or (cancel is not None and cancel.is_set()))},
                    ))
                else:
                    _terminal(TurnEvent(
                        "done",
                        text=str(result.get("final_response") or ""),
                        data={
                            "completed": True,
                            "turn_id": result.get("turn_id"),
                            "interrupted": bool(result.get("interrupted")),
                        },
                    ))
            except Exception as exc:  # noqa: BLE001 - a turn failure is an event, not a crash
                _terminal(TurnEvent("error", text=f"{type(exc).__name__}: {exc}"))
            finally:
                turn_done.set()
                events.put(None)

        if cancel is not None:
            threading.Thread(
                target=_watch_cancel, daemon=True, name="runtime-port-cancel").start()
        threading.Thread(target=_run, daemon=True, name="runtime-port-turn").start()
        while True:
            event = events.get()
            if event is None:
                return
            yield event
            if event.kind in _TERMINAL:
                return
