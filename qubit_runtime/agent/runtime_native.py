"""Native (vendored AIAgent) RuntimePort adapter.

The caller supplies the agent factory; this module owns only the turn/event
mapping so later engines share one contract. The turn runs on a worker thread
because ``run_conversation`` is synchronous while the port yields events as
they arrive.
"""
from __future__ import annotations

import queue
import threading
from typing import Any, Callable, Iterator

from agent.runtime_port import RuntimeCapabilities, TurnEvent, TurnRequest


class NativeRuntime:
    name = "native"
    capabilities = RuntimeCapabilities(streaming=True, cancel=True, tools=True, images=True)

    def __init__(self, agent_factory: Callable[[TurnRequest], Any]) -> None:
        self._agent_factory = agent_factory

    def run_turn(self, request: TurnRequest, cancel: Any | None = None) -> Iterator[TurnEvent]:
        events: "queue.Queue[TurnEvent | None]" = queue.Queue()
        agent = self._agent_factory(request)

        def _emit(event: TurnEvent) -> None:
            events.put(event)

        def _watch_cancel() -> None:
            if cancel is None:
                return
            cancel.wait()
            try:
                agent.interrupt(
                    "runtime port cancel", hard_cancel=True, tool_reason="runtime_port.cancel")
            except Exception:
                pass

        if cancel is not None:
            threading.Thread(
                target=_watch_cancel, daemon=True, name="runtime-port-cancel").start()

        def _run() -> None:
            try:
                result = agent.run_conversation(
                    request.prompt,
                    system_message=request.system,
                    conversation_history=list(request.history) or None,
                    task_id=request.task_id,
                    stream_callback=lambda chunk: _emit(TurnEvent("delta", text=str(chunk))),
                )
                result = result if isinstance(result, dict) else {}
                if result.get("completed") is False:
                    _emit(TurnEvent(
                        "error",
                        text=str(result.get("error") or "turn did not complete"),
                        data={"interrupted": bool(
                            result.get("interrupted") or (cancel is not None and cancel.is_set()))},
                    ))
                else:
                    _emit(TurnEvent(
                        "done",
                        text=str(result.get("final_response") or ""),
                        data={
                            "completed": True,
                            "turn_id": result.get("turn_id"),
                            "interrupted": bool(result.get("interrupted")),
                        },
                    ))
            except Exception as exc:  # noqa: BLE001 - a turn failure is an event, not a crash
                _emit(TurnEvent("error", text=f"{type(exc).__name__}: {exc}"))
            finally:
                events.put(None)

        threading.Thread(target=_run, daemon=True, name="runtime-port-turn").start()
        while True:
            event = events.get()
            if event is None:
                return
            yield event
