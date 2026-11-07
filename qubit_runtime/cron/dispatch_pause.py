"""Dispatch window of a gateway paused for an update (durable, cross-process).

A POSIX ``qubit update`` asks the running gateway to drain and exit before the checkout moves, and
the gateway relaunches on the new code afterwards. While it drains, cron dispatch is paused (the
ticker must not fire agent jobs into a stopping process); the drain can wait for in-flight work
longer than a one-shot's grace window. Without a record of that window the new gateway's due scan
sees an overdue one-shot past ``ONESHOT_GRACE_SECONDS`` and retires it as missed -- the update ate
a job the user scheduled.

This marker records the pause: ``since`` when the gateway accepted the pause request, ``until``
when a later dispatch first observes it (bounded by :data:`MAX_WINDOW_SECONDS` so a gateway that
never came back cannot resurrect ancient one-shots). A one-shot due inside the window is treated
like a store outage (``store_health.outage_covers``): skipped by the pause, not missed, and run
once on recovery. The marker is cleared after the scan that used it.

Best effort everywhere: a marker that cannot be written/read must never block a pause, a scan or
the update.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from contextlib import suppress
from pathlib import Path

logger = logging.getLogger("cron.jobs")

MARKER_NAME = ".dispatch-pause.json"
#: Longest window a pause may cover. An update is minutes; anything older is a dead install's
#: leftover, and the one-shot contract ("never fire hours late") wins.
MAX_WINDOW_SECONDS = 2 * 60 * 60


def marker_path(cron_dir: Path) -> Path:
    return Path(cron_dir) / MARKER_NAME


def _write(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(body, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        with suppress(OSError):
            os.unlink(tmp_name)


def _read(path: Path) -> dict | None:
    try:
        body = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def note(cron_dir: Path, *, now: float | None = None) -> None:
    """Record that dispatch is paused for an update in this store (no-op on failure).

    Re-noting extends an open window instead of replacing its start, so a pause that is requested
    twice does not shrink the window it already owes.
    """
    path = marker_path(cron_dir)
    now = time.time() if now is None else now
    try:
        existing = _read(path)
        if existing is not None and isinstance(existing.get("since"), (int, float)) \
                and existing.get("until") is None:
            return
        _write(path, {"since": now})
    except OSError as exc:
        logger.debug("Could not record the update's cron dispatch pause in %s: %s", cron_dir, exc)


def note_for_gateway(config: object) -> None:
    """Record the pause for every profile store the gateway's ticker serves (never raises)."""
    try:
        from gateway.run import _cron_tick_profile_homes

        homes = [Path(home) for _name, home in _cron_tick_profile_homes(config)]
    except Exception:  # health: allow BLE001 -- a pause marker is advisory; the pause proceeds
        try:
            from qubit_constants import get_process_qubit_home

            homes = [Path(get_process_qubit_home())]
        except Exception:
            return
    for home in homes:
        note(home / "cron")


def window(cron_dir: Path) -> tuple[float, float] | None:
    """``(since, until)`` for an open/just-closed pause; None when this store has no window.

    The first read fixes ``until`` (bounded by :data:`MAX_WINDOW_SECONDS`) and persists it, so a
    gateway that never came back cannot keep extending the window over a later outage.
    """
    path = marker_path(cron_dir)
    body = _read(path)
    if body is None:
        return None
    since = body.get("since")
    if not isinstance(since, (int, float)):
        return None
    until = body.get("until")
    if not isinstance(until, (int, float)):
        until = min(time.time(), float(since) + MAX_WINDOW_SECONDS)
        try:
            _write(path, {"since": float(since), "until": until})
        except OSError:
            pass  # in-memory verdict is still valid for this scan
    return float(since), float(until)


def covers(cron_dir: Path, due_at: float) -> bool:
    """Whether a run due at *due_at* fell inside the update pause of this store."""
    span = window(cron_dir)
    return span is not None and span[0] <= due_at <= span[1]


def clear(cron_dir: Path) -> None:
    with suppress(OSError):
        marker_path(cron_dir).unlink()
