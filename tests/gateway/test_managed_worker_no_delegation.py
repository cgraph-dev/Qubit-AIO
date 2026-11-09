"""A managed worker turn IS offered ``delegate_task``, and any child it spawns is reserved by the
owner under the worker's execution (#141793) — never self-registered by the worker.

This file used to pin the retired contract ("a single-turn worker cannot reserve a child, so never
offer the tool"). That held only while ``run_agent`` passed
``disabled_toolsets=list(WORKER_DISABLED_TOOLSETS)`` with ``WORKER_DISABLED_TOOLSETS = ('delegation',)``.
Both the constant and the argument are gone from ``agent/managed_worker.py``; a child is now
reserved by the owner in ``gateway/session_worker_child.py::reserve_child_execution`` over the
worker's ``child.reserve`` RPC (``agent/runtime_session_child.py::reserve_child_store``).
The behavioural successor — a full delegation round trip, per-child lineage and restarts — is
``qubit_runtime/tests/gateway/test_managed_worker_delegation.py``. This file keeps the original
"what tools does the worker's turn actually carry" probe and asserts the live contract.

(real daemon, real managed worker, loopback model)
"""
import asyncio
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import threading

import pytest

from tests.gateway.fixtures.local_recovery_probe import daemon, rpc, websocket
from tests.waiting import async_wait_until


class Model(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if body.get('messages'):
            self.server.offered.append(json.dumps(body.get('tools') or []))
        message = {'role': 'assistant', 'content': 'WORKER_DONE'}
        frame = {'id': 'tools', 'model': 'm', 'choices': [{'index': 0, 'delta': message, 'finish_reason': 'stop'}],
                 'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
        if body.get('stream'):
            payload, kind = ('data: ' + json.dumps(frame) + '\n\ndata: [DONE]\n\n').encode(), 'text/event-stream'
        else:
            frame['choices'][0]['message'] = frame['choices'][0].pop('delta')
            payload, kind = json.dumps(frame).encode(), 'application/json'
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.mark.platforms("linux")
def test_managed_worker_turn_offers_delegate_task(tmp_path):
    root = Path(__file__).resolve().parents[2]
    home, user = tmp_path / 'state', tmp_path / 'user'
    home.mkdir(mode=0o700)
    user.mkdir()
    peer = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    peer.offered = []
    threading.Thread(target=peer.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{peer.server_port}/v1'
    (home / 'config.yaml').write_text(json.dumps({
        'gateway': {'multiplex_profiles': False, 'managed_workers': True},
        'model': {'provider': 'custom', 'default': 'm', 'base_url': url},
        'auxiliary': {'title_generation': {'enabled': False}},
        'platform_toolsets': {'cli': ['delegation', 'todo']}}))
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'TZ') if k in os.environ}
    env.update(HOME=str(user), USERPROFILE=str(user), QUBIT_HOME=str(home), HERMES_HOME=str(home), PYTHONPATH=str(root),
               OPENAI_API_KEY='loopback-only', OPENAI_BASE_URL=url)

    def rows(sql):
        with closing(sqlite3.connect(f'file:{home / "state.db"}?mode=ro', uri=True, timeout=2)) as db:
            return db.execute(sql).fetchall()

    async def exercise(desc):
        async with websocket(home, desc) as ws:
            session = await rpc(ws, 'session.create', request_id='tools', source='cli', cwd=str(home), model='m',
                                provider='custom', base_url=url, api_key='loopback-only',
                                toolsets=['delegation', 'todo'], ignore_rules=True)
            sid = session['result']['session_id']
            submitted = await rpc(ws, 'prompt.submit', session_id=sid, input_id='run', text='hello')
            assert 'result' in submitted, submitted
            await async_wait_until(lambda: rows("SELECT status FROM session_admissions WHERE request_id='run'") == [
                ('terminal',)], timeout=90, interval=.1, what='run terminal')
    try:
        with daemon(root, home, env, barrier=False) as (_owner, desc):
            asyncio.run(exercise(desc))
    finally:
        peer.shutdown()
        peer.server_close()
    assert peer.offered, 'the managed worker never reached the model'
    # One tool-name set per request the worker's turn made to the model. A greedy loop nests the
    # deferred-tool calls inside delegate_task's own parameters, so parse it top-level only.
    offered = [{tool['function']['name'] for tool in json.loads(payload, parse_constant=str)} for payload in peer.offered]
    assert offered, peer.offered
    # The live contract (#141793): delegate_task is offered, where it used to be filtered out by the
    # since-deleted WORKER_DISABLED_TOOLSETS constant. This is the assertion that used to fail.
    assert all('delegate_task' in names for names in offered), offered
    # And it is offered alongside the turn's other tools rather than instead of them: the probe still
    # receives a full toolset, so the assertion above is not satisfied by an empty or truncated one.
    assert all(len(names) > 1 and 'tool_search' in names for names in offered), offered
    # Nothing was delegated in this plain turn, and the worker did NOT self-register an execution:
    # the only rows are the admission's own. Children only ever arrive via the owner's
    # ``child.reserve`` (gateway/session_worker_child.py), which the worker cannot issue for itself.
    assert not rows("SELECT kind FROM worker_executions WHERE kind='child'"), rows(
        'SELECT kind, status FROM worker_executions')


