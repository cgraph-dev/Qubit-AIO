"""Named RuntimePort backends. Phase 1 registers only ``native``."""
from __future__ import annotations

from typing import Any, Callable

from agent.runtime_native import NativeRuntime
from agent.runtime_port import RuntimeBackend, UnknownRuntimeError


class RuntimeRegistry:
    def __init__(self) -> None:
        self._backends: dict[str, RuntimeBackend] = {}

    def register(self, backend: RuntimeBackend) -> None:
        self._backends[backend.name] = backend

    def get(self, name: str) -> RuntimeBackend:
        if name not in self._backends:
            raise UnknownRuntimeError(name, self.available())
        return self._backends[name]

    def available(self) -> list[str]:
        return sorted(self._backends)


def build_registry(agent_factory: Callable[[Any], Any]) -> RuntimeRegistry:
    registry = RuntimeRegistry()
    registry.register(NativeRuntime(agent_factory))
    return registry
