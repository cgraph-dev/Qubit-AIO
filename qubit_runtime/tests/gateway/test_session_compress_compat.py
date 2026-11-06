"""Legacy Ink ``session.compress`` is an adapter over canonical ``session.mutate(compress)``.

The shell TUI still sends the deprecated verb; the gateway must serve it without becoming a
second compression implementation or session authority. These tests pin the delegation, the
committed-state projection and the negotiation capability.
"""
from types import SimpleNamespace

import pytest

from gateway.session_authority import SessionAuthority
from gateway.session_controls import AuthorityConnection
from gateway.session_local import create_local_session
from gateway.session_tui_ops import SESSION_COMPRESS_COMPAT_CAPABILITY, handlers
import qubit_state_runtime as rt


def _authority(tmp_path, monkeypatch, *, compress, cached_agent=None):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from agent.context_compressor import ContextCompressor

    monkeypatch.setattr(run, '_load_gateway_config', dict)
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    db = store._db
    epoch = rt.begin_runtime_epoch(db, instance_id='owner')
    monkeypatch.setattr(ContextCompressor, '__init__', lambda self, *a, **k: None)
    monkeypatch.setattr(ContextCompressor, 'compress', compress)
    runner = SimpleNamespace(session_store=store, _session_db=db, adapters={}, _draining=False,
                             _evict_cached_agent=lambda route: None,
                             _adapter_for_source=lambda source: None,
                             _cached_agent_for=lambda route: cached_agent,
                             _resolve_session_agent_runtime=lambda **k: ('frozen', {}))
    authority = SessionAuthority(runner, profile_id=str(tmp_path), instance_id='owner', db=db, epoch=epoch)
    owner = AuthorityConnection(authority, object(), {'user_id': 'human'})
    ref = create_local_session(authority, owner.actor, dict(request_id='compress-compat', source='cli',
                                                             cwd=str(tmp_path), model='frozen', toolsets=[]))
    for i in range(4):
        db.append_message(ref.session_id, 'user', f'question {i}')
        db.append_message(ref.session_id, 'assistant', f'answer {i}')
    return authority, owner, ref


def _summarizing(self, messages, **kwargs):
    return [{'role': 'user', 'content': 'SUMMARY_OF_FACTS', '_compressed_summary': True}]


def _unchanged(self, messages, **kwargs):
    return messages


@pytest.mark.asyncio
async def test_compress_is_registered_and_advertised(tmp_path, monkeypatch):
    authority, owner, ref = _authority(tmp_path, monkeypatch, compress=_summarizing)
    try:
        assert 'session.compress' in handlers(owner)
        reply = await owner.dispatch({'id': 1, 'method': 'runtime.describe', 'params': {}})
        assert reply['result']['capabilities'].count(SESSION_COMPRESS_COMPAT_CAPABILITY) == 1
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_compress_delegates_to_session_mutate_and_never_compresses_itself(tmp_path, monkeypatch):
    authority, owner, ref = _authority(tmp_path, monkeypatch, compress=_summarizing)
    calls = []
    original = owner.mutate

    async def recording_mutate(target_ref, params):
        calls.append((target_ref, params))
        return await original(target_ref, params)

    owner.mutate = recording_mutate
    try:
        reply = await owner.dispatch({'id': 1, 'method': 'session.compress',
                                      'params': {'session_id': ref.session_id, 'focus_topic': 'billing'}})
        assert 'result' in reply, reply
        call_ref, params = calls[0]
        assert call_ref.session_id == ref.session_id
        assert params['operation'] == 'compress'
        assert params['payload'] == {'focus': 'billing'}
        assert isinstance(params['expected_revision'], int)
        assert isinstance(params['expected_generation'], int)
        assert reply['result']['compat'] == {
            'capability': SESSION_COMPRESS_COMPAT_CAPABILITY, 'deprecated': True,
            'method': 'session.compress',
            'canonical': {'method': 'session.mutate', 'operation': 'compress'}}
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_compress_returns_the_typed_tui_shape_from_committed_state(tmp_path, monkeypatch):
    authority, owner, ref = _authority(tmp_path, monkeypatch, compress=_summarizing)
    try:
        reply = await owner.dispatch({'id': 1, 'method': 'session.compress',
                                      'params': {'session_id': ref.session_id}})
        assert 'result' in reply, reply
        result = reply['result']
        assert result['status'] == 'compressed'
        assert result['compressed'] is True
        assert result['before_messages'] == 8
        assert result['after_messages'] == 1
        assert result['removed'] == 7
        assert isinstance(result['before_tokens'], int) and result['before_tokens'] > 0
        assert isinstance(result['after_tokens'], int) and result['after_tokens'] > 0
        assert result['summary']['headline']
        assert result['summary']['aborted'] is False
        assert result['compat']['deprecated'] is True
        assert isinstance(result['info'], dict) and result['info']['source'] == 'local'
        assert isinstance(result['messages'], list) and result['messages']
        # The transcript rows are the committed ones, not a second in-memory compression.
        assert any(row.get('text') == 'SUMMARY_OF_FACTS' or row.get('content') == 'SUMMARY_OF_FACTS'
                   for row in result['messages'])
        # Canonical owner changed: the old logical id still resolves to the mutated transcript.
        assert authority.db.get_session(ref.session_id)['runtime_generation'] >= 1
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_compress_preview_is_read_only(tmp_path, monkeypatch):
    calls = []

    def compress(self, messages, **kwargs):
        calls.append(messages)
        return messages

    authority, owner, ref = _authority(tmp_path, monkeypatch, compress=compress)
    before = authority.db.get_session(ref.session_id)
    try:
        reply = await owner.dispatch({'id': 1, 'method': 'session.compress',
                                      'params': {'session_id': ref.session_id, 'focus_topic': '--preview'}})
        assert 'result' in reply, reply
        result = reply['result']
        assert result['status'] == 'preview'
        assert result['compressed'] is False
        assert result['removed'] == 0
        assert result.get('message', '').startswith('Preview')
        assert calls == [], 'a preview reached the summarizer'
        after = authority.db.get_session(ref.session_id)
        assert (after['runtime_revision'], after['runtime_generation']) == (
            before['runtime_revision'], before['runtime_generation'])
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_compress_noop_reports_without_writing(tmp_path, monkeypatch):
    authority, owner, ref = _authority(tmp_path, monkeypatch, compress=_unchanged)
    before = authority.db.get_session(ref.session_id)
    try:
        reply = await owner.dispatch({'id': 1, 'method': 'session.compress',
                                      'params': {'session_id': ref.session_id}})
        assert 'result' in reply, reply
        result = reply['result']
        assert result['status'] == 'noop'
        assert result['compressed'] is False
        assert result['removed'] == 0
        assert result['summary']['noop'] is True
        assert result['before_messages'] == result['after_messages'] == 8
        after = authority.db.get_session(ref.session_id)
        assert (after['runtime_revision'], after['runtime_generation']) == (
            before['runtime_revision'], before['runtime_generation'])
    finally:
        await owner.close()
