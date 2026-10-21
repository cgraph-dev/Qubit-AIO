"""Real daemon restart driver; model and preclaim crash barriers are owned fixtures.

Partial port of upstream ``43faacfb8de7`` ``tests/gateway/fixtures/local_recovery_probe.py``.
Only the environment helper is ported so far: the daemon/model/rpc helpers need the
``local_recovery_daemon.py`` fixture script, which is not ported in this tree. Files that
need the daemon are recorded as skipped rather than faked.
"""
import asyncio
import json
import os


# Windows children need the system environment (Winsock, temp, PATHEXT); POSIX uses the first three.
CHILD_ENV_KEYS = ('PATH', 'LANG', 'TZ', 'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'PATHEXT',
                  'LOCALAPPDATA', 'APPDATA', 'PROGRAMDATA', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE')


def child_env():
    return {k: os.environ[k] for k in CHILD_ENV_KEYS if k in os.environ}


async def rpc(ws, method, **params):
    await ws.send(json.dumps({'jsonrpc': '2.0', 'id': method, 'method': method, 'params': params}))
    async with asyncio.timeout(20):
        while True:
            reply = json.loads(await ws.recv())
            if reply.get('id') == method:
                return reply
