"""Closing helpers for swallowed ``urllib`` errors.

``urllib.request.urlopen`` raises :class:`urllib.error.HTTPError` *before* a
``with`` block is ever entered, and the raised error object owns the response
body: on several code paths that body is a ``tempfile``-backed file object.

Code that treats HTTP failures as best-effort — ``except Exception: pass``, or
catching ``HTTPError`` to raise a typed domain error — therefore leaks that
handle. Nothing obvious fails at the time: the handle survives until the GC
finalises it, and CPython reports it then as ``ResourceWarning: unclosed
tempfile`` / ``Implicitly cleaning up <HTTPError ...>``. Because the
finalisation happens whenever the collector next runs, the warning lands on
whichever code happens to be executing — frequently a later, unrelated test.
Under a parallel suite that is how a leak in one test fails another.

Closing the error's body at the point it is swallowed keeps the failure
best-effort without the leak, and keeps the warning attributed to the code
that actually owns the handle.
"""

from __future__ import annotations

from typing import Any

__all__ = ["close_http_error"]


def close_http_error(exc: Any) -> None:
    """Close the response body carried by a ``urllib`` HTTP error.

    Safe on anything: objects without ``close`` are ignored, and a closer that
    itself raises is suppressed — this only ever runs on an error path that is
    already being swallowed, so it must not introduce a new failure.
    """
    closer = getattr(exc, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:  # noqa: BLE001 - already on a swallowed path
            pass
