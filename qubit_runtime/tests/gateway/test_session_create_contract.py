"""The canonical ``session.create`` reply is validated by its OWN contract, not the TUI one.

Both transports declare a ``session.create``. ``CANONICAL_METHODS`` carries the canonical snapshot
(``running``, ``authority_epoch``, ``subscription_id``, …); ``METHODS`` carries the TUI result. Before
``contracts/__init__.py`` imported the canonical topic, the canonical dispatcher fell back to
``METHODS['session.create']`` and every boot create logged ``SessionCreateResult`` ``extra_forbidden``
contract noise (or, under ``HERMES_TEST_ISOLATION``, failed). These pin the registration and the real
handler's result shape.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tui_gateway.contracts.canonical_sessions import CanonicalSnapshot
from tui_gateway.contracts.registry import CANONICAL_METHODS, METHODS
from tui_gateway.contracts.sessions import SessionCreateResult


def _config():
    return {'model': {'provider': 'custom', 'base_url': 'http://127.0.0.1:1/v1', 'default': 'model-main'}}


@pytest.mark.asyncio
async def test_canonical_session_create_validates_as_a_canonical_snapshot(tmp_path, monkeypatch, caplog):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    from gateway.session_controls import AuthorityConnection
    import qubit_yaml as yaml

    assert CANONICAL_METHODS['session.create'].result is CanonicalSnapshot
    assert METHODS['session.create'].result is SessionCreateResult

    home = tmp_path / 'owner'
    home.mkdir()
    config = _config()
    (home / 'config.yaml').write_text(yaml.safe_dump(config))
    monkeypatch.setenv('QUBIT_HOME', str(home))
    monkeypatch.setattr(run, '_load_gateway_config', lambda: config)
    store = SessionStore(home / 'sessions', GatewayConfig())
    runner = SimpleNamespace(session_store=store, _session_db=store._db, adapters={}, _draining=False,
                             _cached_agent_for=lambda route: None)
    runner._adapter_for_source = lambda source: runner.adapters.get(source.platform)
    authority = await initialize_session_authority(runner, profile_id='fixture', instance_id='owner')
    try:
        connection = AuthorityConnection(authority, object(), {'user_id': 'u', 'profile_id': 'fixture'})
        with caplog.at_level(logging.ERROR):
            response = await connection.dispatch({
                'jsonrpc': '2.0', 'id': 1, 'method': 'session.create',
                'params': {'source': 'gui', 'cwd': str(tmp_path)}})
        assert response.get('error') is None, response
        result = response['result']
        # The fields the TUI result lacks are the live snapshot the canonical client expects.
        for field in ('running', 'authority_epoch', 'replay_epoch', 'last_sequence', 'subscription_id',
                      'revision', 'execution_generation', 'pending', 'prompts'):
            assert field in result
        CanonicalSnapshot.model_validate(result)
        assert not [r for r in caplog.records if 'violates its wire contract' in r.getMessage()]
    finally:
        store.close_all_db_handles()


def test_the_tui_create_result_is_not_the_canonical_shape():
    """Why contract selection matters: the same canonical reply is ``extra_forbidden`` there."""
    result = {'session_id': 's', 'stored_session_id': 'k', 'message_count': 0, 'messages': [],
              'info': {}, 'running': False, 'authority_epoch': 1, 'replay_epoch': 'r',
              'last_sequence': 0, 'subscription_id': 'sub', 'revision': 0, 'execution_generation': 0,
              'pending': [], 'prompts': []}
    with pytest.raises(ValidationError) as caught:
        SessionCreateResult.model_validate(result)
    assert 'extra_forbidden' in str(caught.value)
