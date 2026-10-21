"""Shared fixtures for tests/acp_adapter.

Keeps the ACP server tests offline: ``HermesACPAgent._build_model_state``
calls ``qubit_cli.inventory.build_models_payload``, which (without this
fixture) performs live network fetches — models.dev registry, GitHub model
catalog, Copilot token exchange, Anthropic model list — adding ~3s of real
SSL/socket time to every test that creates or loads a session (~147s total
for test_server.py alone).

Tests that assert model-state behavior re-patch these same attributes with
``unittest.mock.patch`` / ``monkeypatch``; inner patches win, so this
default is transparent to them.
"""

import pytest


@pytest.fixture(autouse=True)
def _offline_model_inventory(monkeypatch):
    """Stub the shared model inventory so ACP tests never hit the network."""
    import qubit_cli.inventory as inventory

    class _StubPickerContext:
        def with_overrides(self, **_kwargs):
            return self

    monkeypatch.setattr(inventory, "load_picker_context", lambda: _StubPickerContext())
    monkeypatch.setattr(
        inventory,
        "build_models_payload",
        lambda *_args, **_kwargs: {"providers": []},
    )


@pytest.fixture
def server_spec(tmp_path):
    """A stdio MCP server spec whose tool echoes a borrowed credential."""
    import sys
    script = tmp_path / 'owned_mcp.py'
    script.write_text('''import json, os, sys
for line in sys.stdin:
 r=json.loads(line); method=r.get('method'); ident=r.get('id')
 if ident is None: continue
 if method=='initialize': result={'protocolVersion':r['params']['protocolVersion'],'capabilities':{'tools':{}},'serverInfo':{'name':'owned-peer','version':'1'}}
 elif method=='tools/list': result={'tools':[{'name':'echo','description':'owned echo','inputSchema':{'type':'object','properties':{}}}]}
 elif method=='tools/call': result={'content':[{'type':'text','text':os.environ['BORROWED_CREDENTIAL']}]}
 else: result={}
 print(json.dumps({'jsonrpc':'2.0','id':ident,'result':result}),flush=True)
''')
    return {'name': 'same-editor-name', 'command': sys.executable, 'args': [str(script)],
            'env': [{'name': 'BORROWED_CREDENTIAL', 'value': 'PRIVATE_A'}]}
