"""A failed turn can be replayed from its receipt without duplicating transcript history."""
from types import SimpleNamespace

import pytest

from gateway.session_contract import Submission
from qubit_state_runtime import claim_session_input, list_session_admissions, settle_session_input
from tests.gateway.test_prompt_queued_controls import _call, _connection
from tests.gateway.test_session_authority_cancel_settlement import REF, _authority, _submit


@pytest.mark.asyncio
@pytest.mark.parametrize('changed', [None, 'newer', 'transcript', 'tool'])
async def test_retry_is_bound_to_the_latest_exact_failed_turn(tmp_path, monkeypatch, changed):
    db, authority = _authority(tmp_path, monkeypatch)
    monkeypatch.setattr(authority, '_schedule', lambda ref: None)
    authority.runner.session_store = SimpleNamespace(_clear_dirty_transcript=lambda sid: None)
    connection = _connection(authority)
    with db:
        prefix = db.append_message('s', 'user', 'old exchange')
        db.append_message('s', 'assistant', 'old answer')
        admission = await authority.submit(connection.actor, Submission('first', REF, {'text': 'same words'}, 'queue'))
        pending = await _call(connection, 'prompt.retry', retry_of='first', submission_id='retry')
        assert pending['error']['message'] == 'retry_unavailable', pending
        claimed = claim_session_input(db, epoch=authority.epoch, session_id='s')
        anchor = db.append_message('s', 'user', 'same words')
        role = 'tool' if changed == 'tool' else 'assistant'
        tail = db.append_message('s', role, 'failed reply')
        settle_session_input(db, epoch=authority.epoch, admission_id=admission.admission_id,
                             generation=claimed['generation'], outcome='failed', result={'result': {
                                 'persisted_turn': {'user_row_id': anchor, 'row_ids': [anchor, tail], 'complete': False},
                             }})
        if changed == 'transcript':
            db.append_message('s', 'assistant', 'another writer')
        if changed == 'newer':
            await _submit(authority, 'newer')
        active_before = db.get_active_message_ids('s')

        reply = await _call(connection, 'prompt.retry', retry_of='first', submission_id='retry')
        rows = list_session_admissions(db, session_id='s', pending_only=False)
        if changed:
            assert reply['error']['message'] == 'retry_unavailable', reply
            assert db.get_active_message_ids('s') == active_before
            assert all(row['request_id'] != 'retry' for row in rows)
        else:
            assert reply.get('result', {}).get('status') == 'queued', reply
            assert rows[-1]['payload'] == rows[0]['payload']
            assert db.get_active_message_ids('s') == active_before[:2]
            assert db.get_active_message_ids('s')[0] == prefix
            assert (await _call(connection, 'prompt.retry', retry_of='first', submission_id='retry')) == reply
            collision = await _call(connection, 'prompt.retry', retry_of='first', submission_id='different')
            assert collision['error']['message'] == 'retry_unavailable', collision
