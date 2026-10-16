"""Focused contracts for Studio configuration, not proof of session recall."""
import pytest

from voicebridge.qubit_runtime import ensure_path
ensure_path()
from voicebridge import viking_studio as studio
from plugins.memory import openviking as ov
from qubit_cli import config
from agent import secret_scope


@pytest.mark.parametrize('memory,expected', [
    ({'provider': 'openviking', 'semantic': {'provider': 'local'}}, 'openviking'),
    ({'provider': 'builtin', 'semantic': {'provider': 'openviking'}}, 'builtin'),
    ({'semantic': {'provider': 'openviking'}}, ''),
    ({}, ''),
])
def test_external_provider_not_semantic_adapter(monkeypatch, memory, expected):
    monkeypatch.setattr(config, 'load_config_readonly', lambda: {'memory': memory})
    result = studio.studio_wiring()[1]['result']
    assert result['memory']['provider'] == expected
    assert result['scope'] == 'configured'
    assert not result['error']


def test_wiring_error_is_not_silently_unset(monkeypatch):
    def fail():
        raise RuntimeError('config unavailable')
    monkeypatch.setattr(config, 'load_config_readonly', fail)
    assert 'config unavailable' in studio.studio_wiring()[1]['result']['error']


def test_real_resolver_precedence_and_scoped_identity(monkeypatch):
    monkeypatch.setenv('OPENVIKING_API_KEY', 'wrong-process-key')
    monkeypatch.setattr(ov, '_load_qubit_openviking_config', lambda: {
        'endpoint': 'http://config:1933', 'account': 'config', 'use_ovcli_config': True})
    monkeypatch.setattr(ov, '_ovcli_values_for', lambda cfg: {
        'endpoint': 'http://linked:1933', 'api_key': 'linked-key', 'user': 'linked-user'})
    captured = []
    class Client:
        def __init__(self, **kwargs):
            captured.append(kwargs)
        def health_payload(self):
            return {'healthy': True}
    monkeypatch.setattr(ov, '_VikingClient', Client)
    token = secret_scope.set_secret_scope({'OPENVIKING_API_KEY': 'scoped-key',
        'OPENVIKING_ACCOUNT': '', 'OPENVIKING_USER': 'scoped-user', 'OPENVIKING_AGENT': 'scoped-peer'})
    try:
        assert studio.studio_health()[1]['result']['ok']
    finally:
        secret_scope.reset_secret_scope(token)
    assert captured[0] == dict(endpoint='http://linked:1933', api_key='scoped-key',
                               account='', user='scoped-user', agent='scoped-peer')


def test_no_public_fallback_after_client_error(monkeypatch):
    monkeypatch.setattr(ov, '_load_qubit_openviking_config', lambda: {})
    monkeypatch.setattr(ov, '_resolve_connection_settings', lambda cfg: {})
    class Client:
        def __init__(self, **kwargs):
            raise RuntimeError('scoped credentials unavailable')
    monkeypatch.setattr(ov, '_VikingClient', Client)
    result = studio.studio_health()[1]['result']
    assert not result['ok']
    assert 'scoped credentials unavailable' in result['error']


def test_real_client_authenticated_health_retry(monkeypatch):
    import httpx
    requests = []
    def serve(request):
        requests.append(request)
        if not request.headers.get('x-api-key'):
            return httpx.Response(401, json={'error': {'message': 'auth required'}})
        return httpx.Response(200, json={'healthy': True})
    client = httpx.Client(transport=httpx.MockTransport(serve))
    monkeypatch.setattr(ov, '_get_httpx', lambda: client)
    try:
        viking = ov._VikingClient('http://test', api_key='test-key', account='tenant', user='user')
        assert viking.health_payload()['healthy']
    finally:
        client.close()
    assert len(requests) == 2
    assert 'authorization' not in requests[0].headers
    assert requests[1].headers['x-api-key'] == 'test-key'
    assert 'x-openviking-account' not in requests[1].headers


@pytest.mark.parametrize('method,path,status', [
    ('POST', '/api/qubit/viking/studio-health', 405),
    ('DELETE', '/api/qubit/viking/studio-wiring', 405),
])
def test_read_only_routes(method, path, status):
    assert studio.handle(None, method, path)[0] == status
