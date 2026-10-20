"""Both live call sites of ``same_create_retry``: a sequential retry and an overlapping owner key.

``gateway/session_local.py`` judges a ``session.create`` that finds the request already committed;
``gateway/session_policy.py::bind_launch_key`` judges an owner-resolved alias key overlapping the
attempt that holds the session's key slot, so an env rotation is not a changed request.
"""

from __future__ import annotations

import dataclasses
import json
from types import SimpleNamespace

import pytest

from qubit_state_runtime import RuntimeStoreError


def _config():
    return {'model': {'provider': 'custom', 'base_url': 'http://127.0.0.1:1/v1', 'default': 'model-main'}}


async def _authority(tmp_path, monkeypatch):
    from gateway import run
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore
    from gateway.session_authority import initialize_session_authority
    import qubit_yaml as yaml

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
    return authority, store


@pytest.mark.asyncio
async def test_create_local_session_retries_and_refuses_a_changed_request(tmp_path, monkeypatch):
    from gateway.session_controls import AuthorityConnection
    from gateway.session_local import create_local_session

    authority, store = await _authority(tmp_path, monkeypatch)
    try:
        actor = AuthorityConnection(authority, object(), {'user_id': 'u', 'profile_id': 'fixture'}).actor
        base = {'request_id': 'r', 'source': 'cli', 'cwd': str(tmp_path), 'model': 'model-a', 'toolsets': []}
        first = create_local_session(authority, actor, dict(base))
        again = create_local_session(authority, actor, dict(base))
        assert again.session_id == first.session_id
        assert authority.db.get_session(first.session_id) is not None

        changed_model = {**base, 'model': 'model-b'}
        with pytest.raises(RuntimeStoreError) as caught:
            create_local_session(authority, actor, changed_model)
        assert caught.value.reason == 'invalid_params'
        assert caught.value.field == 'request_id'

        changed_payload = {**base, 'yolo': True}
        with pytest.raises(RuntimeStoreError) as caught:
            create_local_session(authority, actor, changed_payload)
        assert caught.value.reason == 'invalid_params'
        assert caught.value.field == 'request_id'
    finally:
        store.close_all_db_handles()


@dataclasses.dataclass
class _Policy:
    request_json: str
    client_env_json: str | None = None
    client_env_rules_json: str | None = None
    credential_ref: str | None = None
    config_secret_ref: str | None = None


def _request(model):
    return json.dumps({'source': 'cli', 'cwd': '/tmp', 'model': model}, sort_keys=True)


def test_bind_launch_key_lets_an_exact_retry_take_the_held_key(monkeypatch):
    from gateway import session_local_mcp, session_policy

    monkeypatch.setattr(session_local_mcp, 'bind_editor_mcp', lambda authority, session_id, policy, secret: policy)
    authority = SimpleNamespace(instance_id='owner', epoch=3)
    ref = 'owner:3:sid'
    held = _Policy(request_json=_request('model-a'))
    authority._local_launch_keys = {ref: 'held-key'}
    authority._local_owner_key_requests = {ref: {
        'request_json': held.request_json, 'client_env_json': None, 'client_env_rules_json': None}}

    bound = session_policy.bind_launch_key(
        authority, 'sid', _Policy(request_json=_request('model-a')), 'rotated-key',
        owner_key=True, client_request={})

    assert bound.credential_ref is not None and bound.credential_ref.split('#', 1)[0] == ref


def test_bind_launch_key_refuses_a_different_request(monkeypatch):
    from gateway import session_local_mcp, session_policy

    monkeypatch.setattr(session_local_mcp, 'bind_editor_mcp', lambda authority, session_id, policy, secret: policy)
    authority = SimpleNamespace(instance_id='owner', epoch=3)
    ref = 'owner:3:sid'
    held = _Policy(request_json=_request('model-a'))
    authority._local_launch_keys = {ref: 'held-key'}
    authority._local_owner_key_requests = {ref: {
        'request_json': held.request_json, 'client_env_json': None, 'client_env_rules_json': None}}

    with pytest.raises(RuntimeStoreError) as caught:
        session_policy.bind_launch_key(
            authority, 'sid', _Policy(request_json=_request('model-b')), 'rotated-key',
            owner_key=True, client_request={})
    assert caught.value.reason == 'admission_conflict'
