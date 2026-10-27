"""Typed refusals for offline prompt-variant selection.

The CLI maps every :class:`OptimizeError` to exit 2 and prints the stable
``code``. A refusal is deliberate: there is no model call and no hidden
optimizer that could invent a winner from unlabeled data.
"""
from __future__ import annotations


class OptimizeError(ValueError):
    """Base class for optimizer input/refusal failures."""

    code = "optimize_invalid_input"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        if code:
            self.code = code
        self.message = message
        super().__init__(message)


__all__ = ["OptimizeError"]
