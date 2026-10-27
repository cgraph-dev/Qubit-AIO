"""Named RuntimePort backends.

``native`` is the default (vendored ``AIAgent``). ``openjarvis`` is an optional
out-of-process engine and ``rust_local`` is an optional in-process PyO3 engine:
both are always registered so ``qubit runtime engines`` can name them, but each
refuses cleanly when its dependency is absent, unpinned or unverifiable.
Availability never changes the default engine and a refusal never falls back.
"""
from __future__ import annotations

from typing import Any, Callable

from agent.runtime_native import NativeRuntime
from agent.runtime_openjarvis import OpenJarvisRuntime
from agent.runtime_port import RuntimeBackend, UnknownRuntimeError
from agent.runtime_rust_local import RustLocalRuntime


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


def build_registry(
    agent_factory: Callable[[Any], Any], *, native_stream: bool = True,
) -> RuntimeRegistry:
    registry = RuntimeRegistry()
    registry.register(NativeRuntime(agent_factory, stream=native_stream))
    registry.register(OpenJarvisRuntime())
    registry.register(RustLocalRuntime())
    return registry
