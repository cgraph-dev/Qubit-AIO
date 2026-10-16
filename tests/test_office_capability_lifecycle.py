"""Office uses the terminal adapter and registry; no provider/network calls."""
import sys
import types

import pytest

from test_agent_tool_capable_worker9u import _install_fake_vendored_agent, _FakeStore


def test_office_invokes_registry_capabilities(monkeypatch):
    from voicebridge import agent as AG, qubit_runtime as QR
    from voicebridge.office import runner

    QR.ensure_path()
    from tools.registry import ToolRegistry
    registry = ToolRegistry()
    names = ['skill_view', 'mcp__office_probe__echo', 'office_plugin_echo']
    calls = []
    for name in names:
        registry.register(name, 'office_probe',
                          {'name': name, 'parameters': {'type': 'object'}},
                          lambda args, n=name: calls.append((n, args)) or args['value'])
    captured = {}
    _install_fake_vendored_agent(monkeypatch, captured)
    import qubit_cli.tools_config as TC
    monkeypatch.setattr(TC, '_get_platform_tools', lambda cfg, platform: {'office_probe'})
    monkeypatch.setattr('agent.skill_utils.parse_config_string_list', lambda value: ['browser'])
    startup = types.ModuleType('qubit_cli.mcp_startup')
    startup.__dict__['ensure_mcp_discovery_before_agent_build'] = lambda **kw: captured.update(discovered=True)
    monkeypatch.setitem(sys.modules, 'qubit_cli.mcp_startup', startup)

    class RegistryAgent:
        def __init__(self, **kwargs):
            assert captured['discovered']
            assert kwargs['enabled_toolsets'] == ['office_probe']
            assert kwargs['disabled_toolsets'] == ['browser']
            self.tools = registry.get_definitions(set(names), quiet=True)
            assert {t['function']['name'] for t in self.tools} == set(names)
        def run_conversation(self, text):
            for name in names:
                assert registry.dispatch(name, {'value': text}) == text
            return {'final_response': 'invoked', 'messages': [
                {'role': 'tool', 'name': name, 'content': 'invoked'} for name in names]}
        def close(self):
            captured['closed'] = True

    monkeypatch.setattr(sys.modules['run_agent'], 'AIAgent', RegistryAgent)
    monkeypatch.setattr(AG, '_canonical_session_store', lambda cfg: _FakeStore())
    monkeypatch.setattr(runner, '_seat', lambda cfg, aid: {'slug': 'office-capabilities'})
    monkeypatch.setattr(runner.store, 'estop_engaged', lambda cfg: False)
    out = runner._default_executor({'agent': {'enabled': True}}, 1, 'probe')
    assert out['state'] == 'OK'
    assert {name for name, args in calls} == set(names)
    assert captured['closed']


def test_office_toolset_resolution_failure_never_grants_defaults(monkeypatch):
    from voicebridge import agent as AG
    from voicebridge.office import runner
    captured = {}
    _install_fake_vendored_agent(monkeypatch, captured)
    import qubit_cli.tools_config as TC
    def fail(*args):
        raise ValueError('broken tool permission configuration')
    monkeypatch.setattr(TC, '_get_platform_tools', fail)
    monkeypatch.setattr(AG, '_canonical_session_store', lambda cfg: _FakeStore())
    monkeypatch.setattr(runner, '_seat', lambda cfg, aid: {'slug': 'office-denied'})
    monkeypatch.setattr(runner.store, 'estop_engaged', lambda cfg: False)
    out = runner._default_executor({'agent': {'enabled': True}}, 1, 'probe')
    assert out['code'] == 'TOOLSET_CONFIG_ERROR'
    assert 'agent_kwargs' not in captured


def test_disabled_bound_plugin_cannot_bypass_lifecycle(tmp_path, monkeypatch):
    from test_oc_skills_agent8_plugins import _cfg, _manifest
    from voicebridge import plugin_runtime as PR
    cfg = _cfg(tmp_path, monkeypatch)
    calls = []
    PR.bind_handler(cfg, manifest=_manifest('office.lifecycle'),
                    handler=lambda args: calls.append(args), confirmed=True)
    state = PR._load(cfg)
    state['plugins']['office.lifecycle']['enabled'] = False
    PR._save(cfg, state)
    assert not next(p for p in PR.list_plugins(cfg)['plugins']
                    if p['id'] == 'office.lifecycle')['enabled']
    with pytest.raises(PR.PluginRuntimeError) as error:
        PR.invoke(cfg, 'office.lifecycle', confirmed=True)
    assert error.value.code == PR.DISABLED_CODE
    assert calls == []
