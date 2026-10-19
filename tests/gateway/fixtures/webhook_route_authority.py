"""Real durable admission for route tests; only execution is held at scheduling."""

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
from contextlib import suppress


def mount_authority(app, adapter):
    authority = None
    async def start(app):
        nonlocal authority
        from gateway.config import Platform
        from gateway.run import GatewayRunner
        from gateway.session_authority import initialize_session_authority
        from gateway.session import SessionStore
        from qubit_constants import get_qubit_home
        runner = GatewayRunner()
        # Route tests mount ONE default-profile authority with no profile reservation; a profiles/
        # directory in the test home must not flip the runner into (unreserved) multiplex mode.
        runner.config.multiplex_profiles = False
        runner.session_store = SessionStore(sessions_dir=get_qubit_home() / "sessions", config=runner.config)
        from qubit_state import SessionDB
        runner.session_store._db = SessionDB(db_path=get_qubit_home() / "state.db")
        runner._session_db = runner.session_store._db
        authority = await initialize_session_authority(runner, profile_id='default', instance_id='route-test')
        runner.adapters[Platform.WEBHOOK] = adapter
        runner._wire_adapter_handlers(adapter)
        # These tests inspect routing/rendering, not inference. Leave the actual
        # committed FIFO untouched; execution is covered by test_webhook_authority.
        authority._schedule = lambda ref: None
        original = authority.admit_native
        async def capture(event):
            receipt = await original(event)
            observer = adapter.__dict__.get('handle_message')
            if observer is not None:
                await observer(event)
            return receipt
        authority.admit_native = capture

    async def cleanup(app):
        for task in list(adapter._background_tasks):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        authority.db.close()

    app.on_startup.append(start)
    app.on_cleanup.append(cleanup)
