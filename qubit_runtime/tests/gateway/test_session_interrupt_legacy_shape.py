"""The shipped Ink TUI / Desktop ``session.interrupt`` stops the turn that is running now.

The canonical owner control shape carries ``execution_generation`` so a stop that raced a
replaced turn is refused as stale. The legacy wire shape (``SessionInterruptParams`` in
``tui_gateway/contracts/sessions.py``, generated into ``apps/shared``) has no generation field:
Ctrl+C in an attached TUI sends only ``session_id``. The authority compared that missing value
against the live generation and answered ``stale_generation``, so the keypress silently no-oped
while the turn kept streaming and persisted its full reply instead of the partial the user saw.

An omitted generation means "the current turn". An explicitly supplied stale generation still
fails closed: approvals/cancel and generation-aware clients keep their fence.
"""
from types import SimpleNamespace

import pytest


async def _session(tmp_path, monkeypatch):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    from gateway.session_local import create_local_session

    monkeypatch.setattr(run, '_load_gateway_config', lambda: {'platform_toolsets': {'cli': [], 'acp': []}})
    store = SessionStore(tmp_path / 'sessions', GatewayConfig())
    runner = SimpleNamespace(
        session_store=store, _session_db=store._db, adapters={}, _draining=False,
        _cached_agent_for=lambda route: None, _adapter_for_source=lambda source: None,
        _evict_cached_agent=lambda route: None)
    authority = await initialize_session_authority(runner, profile_id=str(tmp_path), instance_id='fixture')
    connection = AuthorityConnection(authority, object(), {'user_id': 'owner'})
    ref = create_local_session(
        authority, connection.actor,
        {'request_id': 'create', 'source': 'cli', 'cwd': str(tmp_path), 'model': 'old', 'toolsets': []})
    return authority, connection, ref


class _Agent:
    """A live turn agent: only the hard-interrupt seam is reached."""

    def __init__(self):
        self.stops = 0

    def hard_interrupt(self, *args, **kwargs):
        self.stops += 1


async def _running(authority, connection, ref):
    from qubit_state_runtime import admit_session_input, claim_session_input

    admit_session_input(
        authority.db, epoch=authority.epoch, principal_id=connection.actor.subject,
        session_id=ref.session_id, request_id='run', payload={'text': 'stream then stop'})
    assert claim_session_input(authority.db, epoch=authority.epoch, session_id=ref.session_id) is not None
    handle = authority._handle(ref)
    assert handle.execution_state == 'running'
    return handle


@pytest.mark.asyncio
async def test_interrupt_without_generation_stops_the_current_turn(tmp_path, monkeypatch):
    authority, connection, ref = await _session(tmp_path, monkeypatch)
    try:
        handle = await _running(authority, connection, ref)
        agent = _Agent()
        authority.adopted[ref.session_id] = (handle.execution_generation, agent)

        reply = await connection.dispatch(
            {'id': 1, 'method': 'session.interrupt', 'params': {'session_id': ref.session_id}})

        assert reply.get('error') is None, reply
        assert reply['result']['execution_generation'] == handle.execution_generation
        assert agent.stops == 1, 'the legacy shape did not reach the running turn'
    finally:
        await connection.close()
        authority.db.close()


@pytest.mark.asyncio
async def test_explicit_stale_generation_is_still_refused(tmp_path, monkeypatch):
    authority, connection, ref = await _session(tmp_path, monkeypatch)
    try:
        handle = await _running(authority, connection, ref)
        agent = _Agent()
        authority.adopted[ref.session_id] = (handle.execution_generation, agent)

        reply = await connection.dispatch(
            {'id': 1, 'method': 'session.interrupt',
             'params': {'session_id': ref.session_id,
                        'execution_generation': handle.execution_generation - 1}})

        assert reply['error']['data']['reason'] == 'stale_generation', reply
        assert agent.stops == 0
    finally:
        await connection.close()
        authority.db.close()
