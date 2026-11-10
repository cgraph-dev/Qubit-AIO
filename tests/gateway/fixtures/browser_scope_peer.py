"""Authenticated WS fixture: browser.manage actions on the shared owner; no inference, no browser."""

# laneA bootstrap: Qubit repo root + fork runtime root on sys.path.
# The Qubit repo root must be ahead of the runtime root so ``tests`` resolves
# to this checkout's tests package, not the vendored ``qubit_runtime/tests``.
import os as _os, sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve()
while _ROOT.name and not (_ROOT / 'voicebridge').is_dir():
    _ROOT = _ROOT.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))
try:
    from voicebridge import qubit_runtime as _qbh
    _qbh.ensure_path()
except Exception:  # pragma: no cover - standalone/off-repo invocation
    pass

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import traceback


class _Cdp(BaseHTTPRequestHandler):
    def do_GET(self):  # /json/version: this operator's logged-in browser answers
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"webSocketDebuggerUrl": "ws://127.0.0.1/devtools/browser/x"}')

    def log_message(self, *args):
        pass


async def probe():
    from gateway.run import GatewayRunner
    from gateway.session_authority import initialize_session_authority
    from gateway.run_api import start_gateway_api, stop_gateway_api
    from qubit_cli import web_server
    from qubit_cli.dashboard_auth.ws_tickets import mint_ticket
    from tests.gateway.fixtures.local_recovery_probe import rpc
    import tools.browser_session_lifecycle as lifecycle
    from websockets.asyncio.client import connect

    home = Path(os.environ['QUBIT_HOME'])
    (home / 'config.yaml').write_text('gateway:\n  multiplex_profiles: false\nmodel:\n  provider: custom\n  default: no-inference\n  base_url: http://127.0.0.1:1/v1\nauxiliary:\n  title_generation:\n    enabled: false\n')
    runner = GatewayRunner()
    await initialize_session_authority(runner, profile_id=str(home), instance_id='browser-scope')
    api = await start_gateway_api(runner)
    web_server.app.state.auth_required = True
    reaps = []
    lifecycle.cleanup_profile_browsers = reaps.append  # only this profile's browsers are closed here
    lifecycle.cleanup_all_browsers = lambda: reaps.append('ALL')  # every profile's would be
    os.environ['BROWSER_CDP_URL'] = 'http://127.0.0.1:9333'  # the operator's env endpoint
    bee = home / 'profiles' / 'bee'
    bee.mkdir(parents=True)
    (bee / 'config.yaml').write_text('browser:\n  cdp_url: ws://bee.invalid/devtools/browser/PROFILE-B\n')
    from gateway.run import _profile_runtime_scope
    from qubit_constants import qubit_home_key
    from tools import browser_session_cdp

    def resolved(profile):
        with _profile_runtime_scope(profile, hydrate_secrets=False):
            return browser_session_cdp.get_cdp_override_raw()
    receipt = {'home_key': qubit_home_key(home)}
    cdp = ThreadingHTTPServer(('127.0.0.1', 0), _Cdp)
    threading.Thread(target=cdp.serve_forever, daemon=True).start()
    try:
        ticket = mint_ticket(user_id='owner', provider='fixture')
        async with connect(api.api_origin.replace('http:', 'ws:') + '/api/ws?ticket=' + ticket) as ws:
            sid = (await rpc(ws, 'session.create', request_id='browser', source='tui', toolsets=[]))['result']['session_id']
            # CLI-9: phase 1 serves ONE profile, phase 2 TWO (bee already holds its own live connection).
            real = runner.session_authorities if hasattr(runner, 'session_authorities') else None
            for phase, serving in (('one', None), ('two', 2)):
                if serving:
                    import types
                    other = types.SimpleNamespace(profile_id=str(bee), retiring=False)
                    runner.session_authorities = types.SimpleNamespace(
                        serving=lambda: [runner.session_authority, other], multiplexed=True)
                    with _profile_runtime_scope(bee, hydrate_secrets=False):
                        browser_session_cdp.set_live_cdp_override('http://127.0.0.1:9444')
                reaps.clear()
                for action, extra in [('connect', {'url': f'http://127.0.0.1:{cdp.server_port}'}),
                                      ('status', {}), ('disconnect', {}), (None, {})]:
                    params = {'session_id': sid, **extra, **({'action': action} if action else {})}
                    reply = await rpc(ws, 'browser.manage', **params)
                    receipt[f"{phase}:{action or 'missing'}"] = {
                        'code': reply.get('error', {}).get('code'), 'result': reply.get('result'),
                        'env': os.environ.get('BROWSER_CDP_URL'), 'reaps': list(reaps),
                        'own': browser_session_cdp._live_overrides.get(receipt['home_key']),
                        'bee': resolved(bee), 'cdp_port': cdp.server_port}
            runner.session_authorities = real
    finally:
        cdp.shutdown()
        await stop_gateway_api(api)
    (home / 'receipt.json').write_text(json.dumps(receipt))


if __name__ == '__main__':
    status = 0
    try:
        asyncio.run(probe())
    except BaseException:
        traceback.print_exc()
        status = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(status)
