"""The last viewer closing a session ends it on the owner, lifecycle included (#134889 LC-1/LC-2/LC-6).

Main's classic CLI and TUI ran the end of a conversation in their own process: ``end_session``,
the memory providers' ``on_session_end`` extraction and plugin ``on_session_finalize`` (which also
closes the Relay conversation scope). Under the gateway those clients are viewers: the agent, its
memory manager and the Relay scope live in the owner, so ``session.detach {end_reason}`` from the
last viewer is where the owner runs them. A close that lands while a turn is still queued or running
is remembered and completed when the FIFO goes idle, so exiting mid-turn still ends the row.
The pending close is also kept in ``state_meta`` (``CLOSE_PREFIX + session_id``): a gateway stop or
restart before the FIFO goes idle would otherwise drop it, and nothing else ends a local row. Boot
recovery re-arms it (``rearm_persisted_closes``), so the restarted owner's first idle point ends it.
Each stored close carries its own operation id, and every delete is compare-and-delete on that exact
record under the owner-epoch fence: a cancelled older close, a reattach or a stale owner can only
remove the record it saw, never a newer close stored since.
"""
import asyncio
import json
import logging
import uuid
from functools import partial

from qubit_state_local import local_receipt
from qubit_state_mutation_retirement import VIEWER_CLOSE_PREFIX
from qubit_state_runtime import RuntimeStoreError

logger = logging.getLogger(__name__)

CLOSE_PREFIX = VIEWER_CLOSE_PREFIX  # route retirement drops it too

_BUSY_SQL = ("SELECT 1 FROM session_admissions WHERE target_session_id=? AND status IN ('queued','started','unknown') "
             "UNION ALL SELECT 1 FROM worker_executions WHERE session_id IN (?,?) "
             "AND status IN ('registered','running','unknown') LIMIT 1")


def _physical_target(authority, session_id):
    try:
        return local_receipt(authority.db, session_id)['entry']['session_id']
    except (RuntimeStoreError, KeyError, TypeError):
        return authority.db.get_compression_tip(session_id) or session_id


# --- Persisted pending close -------------------------------------------------
# The in-memory ``authority.pending_closes`` entry dies with the process; a gateway stop before the
# FIFO reaches its idle point would drop the close and NOTHING else ends a local row. The record is
# therefore mirrored into ``state_meta`` under ``CLOSE_PREFIX + session_id`` carrying its own op id,
# and every removal is compare-and-delete on that exact record: a reattach, a cancelled older close
# or a stale owner can only delete the record it observed, never a newer close stored since.


def _close_record(close):
    """The stored form of *close* — its op id plus the reason to stamp on the row."""
    record = getattr(close, "record", None)
    if record is None:
        record = {"op": uuid.uuid4().hex, "reason": close.reason}
    return record


def _persist_close(authority, session_id, record) -> None:
    authority.db._write_sql('INSERT OR REPLACE INTO state_meta(key,value) VALUES(?,?)',
                            (CLOSE_PREFIX + session_id, json.dumps(record)))


def forget_persisted_close(authority, session_id, record=None) -> bool:
    """Drop the stored close for *session_id*, but only the exact *record* the caller saw.

    Compare-and-delete on the record's op id: a newer close stored after this one was read survives
    (the reattach/cancel that raced us only cancels what it saw). ``True`` when a row was removed.
    """
    if record is not None:
        row = authority.db._read_one('SELECT value FROM state_meta WHERE key=?',
                                     (CLOSE_PREFIX + session_id,))
        if row is not None and _op_of(row['value']) != record.get("op"):
            return False
    return authority.db._write_rowcount('DELETE FROM state_meta WHERE key=?',
                                        (CLOSE_PREFIX + session_id,)) > 0


def _op_of(raw):
    try:
        return json.loads(raw).get("op")
    except (ValueError, TypeError, AttributeError):
        return None


def cancel_persisted_close(authority, session_id) -> bool:
    """A viewer re-attached: the stored close is no longer wanted. Unconditional delete (the
    attach itself supersedes any record), reporting whether one was there."""
    return authority.db._write_rowcount('DELETE FROM state_meta WHERE key=?',
                                        (CLOSE_PREFIX + session_id,)) > 0


def rearm_persisted_closes(authority) -> tuple:
    """Boot recovery: session ids whose close a previous owner stored but never completed.

    The record is re-seeded into ``pending_closes`` so the restarted owner's first idle point runs
    it. A session whose record cannot be read is dropped rather than left to re-arm forever.
    """
    rows = authority.db._read_all('SELECT key,value FROM state_meta WHERE key GLOB ?',
                                  (CLOSE_PREFIX + '*',))
    armed = []
    for row in rows:
        session_id = row['key'][len(CLOSE_PREFIX):]
        try:
            record = json.loads(row['value'])
        except (ValueError, TypeError):
            forget_persisted_close(authority, session_id)
            continue
        skip = authority.sessions.get(session_id)
        if skip is not None and skip.subscribers:
            continue  # someone is watching again; the close is moot
        authority.pending_closes[session_id] = _PendingClose(record.get("reason", ""), record=record)
        armed.append(session_id)
    return tuple(armed)


def _busy(authority, session_id, target):
    with authority.db._read_ctx() as conn:
        return conn.execute(_BUSY_SQL, (session_id, session_id, target)).fetchone() is not None


# The viewer's /new: the classic CLI's ``new_session``; Ink's ``tui_close`` (/new or a resume switch,
# both of which main's TUI followed with on_session_reset for the session it opened next).
_RESET_REASONS = frozenset({'new_session', 'tui_close'})


class _PendingClose:
    """One viewer close; identity, not the reason, tells a settler its close is still the pending one.

    ``record`` is the durable form (``{"op": ..., "reason": ...}``) mirrored into ``state_meta`` so a
    restart can re-arm the close; its op id is what makes every removal compare-and-delete.
    """
    __slots__ = ('reason', 'record')

    def __init__(self, reason, record=None):
        self.reason = reason
        self.record = record or {"op": uuid.uuid4().hex, "reason": reason}


async def close_viewer_session(authority, session_id, reason):
    """``session.detach {end_reason}`` left *session_id* without viewers: close it now, or once idle.
    Its own async delegations end now, mid-turn too, as main's CLI exit and TUI close ended them
    (#134889 LC-7); each returns as an interrupted completion, and ones its still-running turn starts
    later are refused until a viewer attaches again. Other sessions' work is untouched."""
    live = authority.sessions[session_id]
    if live.subscribers:
        return  # a viewer attached while the detach was off the loop: nothing closes or ends
    if live.superseded:
        return  # a read-only historic mount: its route's delegations and agent are the newer session's
    from tools.async_delegation import interrupt_for_session, refuse_for_session
    route = live.route
    parents = tuple(dict.fromkeys((session_id, _physical_target(authority, session_id))))
    refuse_for_session((authority.profile_id, session_id), session_key=route, parent_session_ids=parents)
    for parent in parents:
        interrupt_for_session(session_key=route, parent_session_id=parent, reason=f'viewer_close:{reason}')
    authority.pending_closes[session_id] = _PendingClose(reason)
    await asyncio.to_thread(_persist_close, authority, session_id,
                            authority.pending_closes[session_id].record)
    await settle_viewer_close(authority, session_id)


async def settle_viewer_close(authority, session_id):
    """Complete a pending viewer close once *session_id* has no viewer and an idle FIFO: stamp the
    row with the viewer's reason, commit the cached agent's memory and fire ``on_session_finalize``.
    The drain calls this at every idle point; a viewer attaching meanwhile cancels the close."""
    close = authority.pending_closes.get(session_id)
    live = authority.sessions.get(session_id)
    if close is None or live is None or live.subscribers:
        authority.pending_closes.pop(session_id, None)
        return
    if not await _resolve_abandoned_unknown(authority, session_id, live, close):
        return  # left paused (a viewer came back, or the resolution was refused)
    target = _physical_target(authority, session_id)
    # Every await below can let a viewer reattach (attach drops the pending close) or another
    # settler run, so the close is re-checked after each one.
    if await asyncio.to_thread(_busy, authority, session_id, target):
        return  # the drain's next idle point completes it
    row = await asyncio.to_thread(authority.db.get_session, target)
    if not _still_pending(authority, session_id, live, close):
        return
    # Claimed with no await since the check: exactly one settler stamps the row and runs the
    # lifecycle, and a close cancelled before its claim never wrote ``ended_at``.
    del authority.pending_closes[session_id]
    from gateway.session_acp_lifecycle import end_idle_viewer_session, reopen_if_reattached
    from gateway.session_runtime_workers import tracked_write
    ended = await tracked_write(authority, partial(end_idle_viewer_session, authority, session_id, close.reason),
                                then=partial(reopen_if_reattached, authority, session_id))
    if not ended:
        await _requeue_if_busy(authority, session_id, live, target, close)
        return
    await asyncio.to_thread(forget_persisted_close, authority, session_id, close.record)
    if live.subscribers:
        return  # a viewer reattached during the stamp; reopen_if_reattached reopened the row
    platform = (row or {}).get('source') or (live.source.platform.value if live.source else '')
    await _run_close_lifecycle(authority, live, target, platform, close.reason)


async def _resolve_abandoned_unknown(authority, session_id, live, close):
    """A turn the previous owner lost (hard kill) recovers ``unknown`` and pauses the FIFO until
    someone Discards it, but the last viewer closed the session: nobody is left to. The viewer's
    close is that decision, so the owner resolves it as Discard does (interrupted, never replayed,
    transcript boundary written) and the drain moves on; a queued successor still runs before the
    close. A viewer back first cancels the close and keeps the decision. Returns whether the
    close may go on to its idle check (no ``unknown`` turn is left)."""
    from qubit_state_runtime import list_session_admissions
    rows = await asyncio.to_thread(list_session_admissions, authority.db, session_id=session_id)
    unknown = [row for row in rows if row['status'] == 'unknown']
    if not unknown:
        return True
    from gateway.session_contract import SessionRef
    ref = SessionRef(authority.profile_id, session_id)
    for row in unknown:
        if not _still_pending(authority, session_id, live, close):
            return False
        try:
            await authority.resolve_unknown_as_owner(ref, row['admission_id'], row['generation'])
        except RuntimeStoreError as exc:
            logger.warning('Unknown turn %s on closed session %s left paused: %s',
                           row['admission_id'], session_id, exc.reason)
            return False
        logger.info('Unknown turn %s resolved interrupted: its session %s was closed by its last viewer',
                    row['admission_id'], session_id)
    # The resolution also rescheduled the drain; the idle check below and its next idle point
    # are both safe (the close is claimed exactly once).
    return _still_pending(authority, session_id, live, close)


def _still_pending(authority, session_id, live, close):
    return not live.subscribers and authority.pending_closes.get(session_id) is close


async def _requeue_if_busy(authority, session_id, live, target, close):
    """The stamp was refused. If input was admitted after the idle check, the close goes back to the
    drain's next idle point; any other refusal (row already ended) leaves nothing to finish."""
    if not await asyncio.to_thread(_busy, authority, session_id, target):
        if session_id not in authority.pending_closes:
            await asyncio.to_thread(forget_persisted_close, authority, session_id, close.record)  # already ended
        return
    if live.subscribers or session_id in authority.pending_closes:
        return  # a reattach cancelled it, or a newer close replaced it
    authority.pending_closes[session_id] = close
    # The drain may have passed its idle point while the close was out of the map.
    await settle_viewer_close(authority, session_id)


async def _run_close_lifecycle(authority, live, target, platform, reason):
    """What main's client ran at exit and ``/new``, in the process that owns the session: the
    memory providers' end-of-session commit (the cached agent is evicted at a boundary, as a
    gateway ``/new`` does) and the finalize hooks under the owning profile's scope."""
    runner = authority.runner
    evict = getattr(runner, '_evict_cached_agent_at_boundary', None)
    if evict is not None:
        evict(live.route)
    finalize = getattr(runner, '_finalize_session_off_loop', None)
    if finalize is None:
        return
    messages = await finalize(session_id=target, platform=platform, reason=reason, session_key=live.route)
    if messages:
        # No viewer is left to show them (session.detach carries no messages yet).
        logger.info('Session-end plugin message(s) for %s dropped: no viewer', target)
    reset = getattr(runner, '_reset_session_off_loop', None)
    if reason in _RESET_REASONS and reset is not None:
        # The gateway's own /new arguments. The viewer opens its next session only after this
        # detach, so the new id is not known here.
        await reset(session_key=live.route, session_id=None, reason='new_session', platform=platform,
                    old_session_id=target, new_session_id=None)
