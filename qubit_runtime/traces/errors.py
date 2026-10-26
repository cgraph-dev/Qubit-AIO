"""Typed errors for the local trace store and trace recording.

A typed error is the difference between "the store is malformed" and a crash:
the CLI maps these to exit 2, and a recorder integration logs and continues.
"""
from __future__ import annotations


class TraceError(RuntimeError):
    """Base class for trace failures; ``code`` is the stable machine key."""

    code = "trace_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        if code:
            self.code = code
        self.message = message
        super().__init__(message)


class TraceInputError(TraceError):
    """The caller supplied an invalid trace field."""

    code = "invalid_trace"


class TraceStoreError(TraceError):
    """The trace store cannot be opened, migrated or written."""

    code = "trace_store_unavailable"


class TraceStoreCorruptError(TraceStoreError):
    """The store exists but is not a compatible trace database."""

    code = "trace_store_corrupt"


class TraceStoreVersionError(TraceStoreError):
    """The store was written by a newer, unsupported schema."""

    code = "trace_store_version_unsupported"


class TraceNotFoundError(TraceError):
    """No trace is stored under the requested id."""

    code = "trace_not_found"


__all__ = [
    "TraceError",
    "TraceInputError",
    "TraceStoreError",
    "TraceStoreCorruptError",
    "TraceStoreVersionError",
    "TraceNotFoundError",
]
