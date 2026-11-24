"""Messaging ``/retry`` under a session authority: the transcript rewrite commits in the re-sent
turn's own admission transaction (#139257 step 2, MSG-11).

``/retry`` drops the last exchange and re-sends its user text. Rewriting first and admitting
afterwards loses the turn whenever admission refuses (a drain or retirement that begins after the
pre-check, a full FIFO). Here the handler only PLANS the rewrite: it reads the transcript, pins the
active row ids it read, and binds the plan to the re-send. ``admit_native`` takes the binding and
runs the rewrite as a write guard after its own admission gate and queue cap, in the same
``BEGIN IMMEDIATE`` that inserts the row. A refusal rolls both back; a transcript that changed since
the read refuses both (``RetryRewriteRefused``).
"""
from __future__ import annotations

import sqlite3
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from qubit_state_runtime import RuntimeStoreError

_bound: ContextVar[Optional[RetryRewrite]] = ContextVar('retry_rewrite', default=None)


class RetryRewriteRefused(RuntimeError):
    """The planned rewrite no longer applies (the transcript changed since the read, a turn lease is
    live, the session closed): the re-send was refused with it and nothing was written."""


@dataclass
class RetryRewrite:
    """One ``/retry`` rewrite, applied at most once inside the re-send's admission write."""
    db: Any
    session_id: str
    source: Any  # the re-send's own SessionSource object: only that admission takes the binding
    write: Callable[[Any], None]  # (conn) -> None, inside the admission transaction
    on_commit: Callable[[], None]  # on the loop, once the admission that applied it committed
    applied: bool = False
    taken: bool = field(default=False, repr=False)

    def guard(self, conn):
        self.applied = False  # the writer may retry the whole transaction
        try:
            self.write(conn)
        except (RuntimeStoreError, sqlite3.OperationalError):
            raise  # gate refusals and writer contention keep their own handling
        except Exception as exc:
            raise RetryRewriteRefused(str(exc)) from exc
        self.applied = True


def bind(rewrite: RetryRewrite):
    return _bound.set(rewrite)


def unbind(token) -> None:
    _bound.reset(token)


def take(event, db) -> Optional[RetryRewrite]:
    """The bound rewrite for this native admission, once, and only for the re-send itself (the same
    source object on the same store). A drain task inheriting the context never takes it."""
    rewrite = _bound.get()
    if (rewrite is None or rewrite.taken or getattr(event, 'source', None) is not rewrite.source
            or rewrite.db is not db):
        return None
    rewrite.taken = True
    return rewrite


def compose(rewrite: Optional[RetryRewrite], authorize):
    """``authorize`` (the admission gate) first, then the rewrite, in one write transaction."""
    if rewrite is None:
        return authorize

    def guard(conn):
        authorize(conn)
        rewrite.guard(conn)
    return guard


async def retry_failed_turn(authority, actor, ref, request_id, retry_of):
    """Use the original accepted input; archive only the exact failed transcript suffix."""
    from gateway.session_ingress_media import capture_lease, hold_retained_media
    from gateway.session_results import admission_result
    from gateway.session_runtime_workers import tracked_write
    from qubit_state_runtime import admission_fingerprint, admit_session_input, find_session_admission

    if (any(not isinstance(value, str) or not 0 < len(value) <= 256 for value in (request_id, retry_of))
            or request_id == retry_of):
        raise RuntimeStoreError('invalid_params')
    authority.authorize(actor, ref, 'session:submit')
    db, session_id = authority.db, ref.session_id
    lookup = dict(principal_id=actor.subject, session_id=session_id)
    binding = 'gateway.failed_retry.v1.' + admission_fingerprint(
        canonical_target=session_id, payload={'principal': actor.subject, 'request': request_id})
    applied = None

    def admit():
        original = find_session_admission(db, **lookup, request_id=retry_of)
        if original is None or (original['status'], original['outcome']) != ('terminal', 'failed'):
            raise RuntimeStoreError('retry_unavailable')
        payload = original['payload']
        if not {'text'} <= payload.keys() <= {'text', 'attachments_v1', 'cli_turn_v1',
                                             'local_operator_v1'}:
            raise RuntimeStoreError('retry_unavailable')
        saved = admission_result(db, original['admission_id'])
        persisted = ((saved or {}).get('result') or {}).get('persisted_turn') or {}

        def rewind(conn):
            nonlocal applied
            applied = None
            applied = _rewind_failed_turn(db, conn, session_id, original, persisted)
            conn.execute('INSERT INTO state_meta(key,value) VALUES(?,?)', (binding, original['admission_id']))

        def reconcile(conn):
            saved = conn.execute('SELECT value FROM state_meta WHERE key=?', (binding,)).fetchone()
            if saved is None or saved[0] != original['admission_id']:
                raise RuntimeStoreError('admission_conflict')

        with capture_lease():
            # An exact replay needs only the digest and can still answer after media retention.
            if find_session_admission(db, **lookup, request_id=request_id) is None:
                try:
                    hold_retained_media(payload.get('attachments_v1', {}).get('media', []))
                except RuntimeStoreError as exc:
                    raise RuntimeStoreError('retry_attachment_missing') from exc
            return admit_session_input(db, **lookup, epoch=authority.epoch, request_id=request_id,
                                       payload=payload, _authorize_write=authority._admission_gate(rewind), _replay_guard=reconcile)

    def committed(row):
        if applied is not None:
            authority.runner.session_store._clear_dirty_transcript(applied)
        return authority._admitted(ref, None, row)

    async with authority.sessions[session_id].mutation_lock:
        authority.authorize(actor, ref, 'session:submit')
        return await tracked_write(authority, admit, then=committed, ordered=True,
                                   after=lambda _receipt: authority._schedule_admitted(ref))


def _rewind_failed_turn(db, conn, session_id, original, persisted):
    from agent.context_compressor import split_user_originated_turn
    from qubit_state_local_lineage import local_physical_target
    from qubit_state_mutation_guards import MUTATION_GUARD_REFUSALS, busy_refusal, require_not_executing
    from qubit_state_mutation_transcript import rewind_in_transaction
    from qubit_state_runtime import _canonical_chain

    current = conn.execute(
        "SELECT status,outcome FROM session_admissions WHERE target_session_id=? AND admission_id=?",
        (session_id, original['admission_id'])).fetchone()
    if current is None or tuple(current) != ('terminal', 'failed'):
        raise RuntimeStoreError('retry_unavailable')
    if conn.execute(
        "SELECT 1 FROM session_admissions WHERE target_session_id=? "
        "AND (seq>? OR status!='terminal') LIMIT 1", (session_id, original['seq'])
    ).fetchone():
        raise RuntimeStoreError('retry_unavailable')
    target = _canonical_chain(conn, local_physical_target(conn, session_id))[-1]
    anchor = persisted.get('user_row_id')
    if (type(anchor) is not int or persisted.get('complete') is not False
            or persisted.get('user_row_ids', [anchor]) != [anchor]):
        raise RuntimeStoreError('retry_unavailable')
    suffix = conn.execute(
        "SELECT id,role,tool_calls,content,display_kind,display_metadata,_compressed_summary "
        "FROM messages WHERE session_id=? AND active=1 AND id>=? ORDER BY id", (target, anchor)
    ).fetchall()
    if (not suffix or suffix[0]['id'] != anchor or suffix[0]['role'] != 'user'
            or [row['id'] for row in suffix] != sorted(persisted.get('row_ids', ()))
            or any(row['role'] == 'tool' or row['tool_calls'] for row in suffix)):
        raise RuntimeStoreError('retry_unavailable')
    first = dict(suffix[0])
    first['content'] = db._decode_content(first['content'])
    first['display_metadata'] = db._decode_display_metadata(first['display_metadata'])
    scaffold, message = split_user_originated_turn(first)
    if message is None:
        raise RuntimeStoreError('retry_unavailable')
    require_not_executing(conn, {session_id, target})
    try:
        rewind_in_transaction(db, conn, target, anchor, preserve_compaction_handoff=scaffold is not None)
    except MUTATION_GUARD_REFUSALS as exc:
        raise busy_refusal(exc) from exc
    return target
