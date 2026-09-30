"""Isolation-worker entry for the capability broker (Packet 3A).

This module is intentionally MINIMAL: it imports only ``os`` and ``cloudpickle``
(no broker/audio/model dependencies). The broker's per-call isolation workers
are forked from the single-threaded multiprocessing forkserver; keeping this
module light means each worker child starts in ~1-2 ms instead of paying the
full ``voicebridge.broker`` import, which kept the read-only fast path well
inside its 50 ms budget.
"""
from __future__ import annotations

import os

import cloudpickle


def broker_child_main(payload: bytes, child_conn) -> None:
    """Run ONE cloudpickled ``(callable, args)`` in a fresh worker child.

    The child calls ``setsid()`` so it leads its own process group; the parent
    can SIGKILL the whole group on cancel/timeout — identical isolation to the
    former fork target, but the forkserver child is forked from a single-
    threaded server process, so CPython 3.12+ never emits the fork-in-thread
    DeprecationWarning. The result is one bounded message over the pipe;
    redaction stays in the parent.
    """
    try:
        os.setsid()
    except Exception:
        pass
    try:
        fn, args = cloudpickle.loads(payload)
        out = fn(dict(args))
        child_conn.send({"ok": True, "out": str(out)[:10000]})
    except BaseException as exc:
        child_conn.send({"ok": False, "err": str(exc)[:500]})
    finally:
        try:
            child_conn.close()
        except Exception:
            pass