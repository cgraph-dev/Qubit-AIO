"""Engine-neutral runtime boundary: one turn in, an ordered event stream out.

Product surfaces depend on this module, never on a concrete engine. The native
adapter wraps today's ``AIAgent``; later phases add out-of-process engines
behind the same contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, Protocol, runtime_checkable

EventKind = Literal["delta", "tool", "usage", "done", "error"]


class UnknownRuntimeError(ValueError):
    """No backend is registered under this name."""

    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = available
        super().__init__(
            f"Unknown runtime engine {name!r}; registered: {', '.join(available) or '(none)'}"
        )


@dataclass(frozen=True)
class RuntimeCapabilities:
    streaming: bool = True
    cancel: bool = True
    tools: bool = True
    images: bool = False


@dataclass(frozen=True)
class TurnRequest:
    prompt: str
    session_id: str
    history: tuple[dict[str, Any], ...] = ()
    system: str | None = None
    task_id: str | None = None


@dataclass(frozen=True)
class TurnEvent:
    kind: EventKind
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class RuntimeBackend(Protocol):
    name: str
    capabilities: RuntimeCapabilities

    def run_turn(self, request: TurnRequest, cancel: Any | None = None) -> Iterator[TurnEvent]:
        """Yield ordered events with exactly one terminal ``done`` or ``error``."""
        ...
