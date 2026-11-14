"""Shared hermetic stubs for integration-adapter tests."""
from __future__ import annotations

from typing import Any


class RecordingTransport:
    """A transport double: records calls, returns a canned response."""

    def __init__(self, response: Any = None, *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = {} if response is None else response
        self.fail = fail

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        json_body: Any = None,
    ) -> Any:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "params": params,
                "json_body": json_body,
            }
        )
        if self.fail:
            raise RuntimeError("transport exploded")
        return self.response


class FakeClock:
    """A deterministic clock for rate-limit tests."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
