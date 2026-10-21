"""The classic CLI's /loop and /heartbeat managers on a gateway session are proxies over
``session.automation``: the local managers apply off the gateway, and a gateway store selects
``GatewayLoopManager`` / ``GatewayHeartbeatManager`` once its session is open."""
from types import SimpleNamespace

import pytest


class _Conn:
    """A fake gateway: runs every call against main's in-process managers on a private store."""

    def __init__(self):
        self.calls = []

    def call(self, method, **params):
        from gateway.session_viewer_automation import invoke
        self.calls.append((params["kind"], params["call"]))
        return invoke(SimpleNamespace(), params["kind"], params["session_id"], params["call"],
                      params["args"], params["kwargs"])


@pytest.fixture
def cli(tmp_path, monkeypatch):
    from qubit_cli import heartbeat, loops
    from qubit_cli.gateway_session_store import GatewaySessionStore
    from qubit_state import SessionDB
    db = SessionDB(db_path=tmp_path / "owner.db")
    monkeypatch.setattr(loops, "_get_session_db", lambda: db)
    monkeypatch.setattr(heartbeat, "_get_session_db", lambda: db)
    conn = _Conn()
    store = GatewaySessionStore.__new__(GatewaySessionStore)
    store.connection = conn
    from qubit_cli.cli_loops_mixin import CLILoopsMixin
    obj = CLILoopsMixin.__new__(CLILoopsMixin)
    obj._session_db, obj._gateway_session_open, obj.session_id = store, True, "sid-1"
    yield SimpleNamespace(cli=obj, conn=conn, db=db)
    db.close()


def test_loop_and_heartbeat_managers_are_the_gateways(cli):
    from qubit_cli.gateway_automation import GatewayHeartbeatManager, GatewayLoopManager
    loop = cli.cli._get_loop_manager()
    hb = cli.cli._get_heartbeat_manager()
    assert type(loop) is GatewayLoopManager
    assert type(hb) is GatewayHeartbeatManager
    assert cli.conn.calls == [("loop", "state"), ("heartbeat", "state")]

    hb.set("pulse", 60)
    assert hb.is_active() and hb.status_line().startswith("♥ Heartbeat (every 1m")
    loop.set("check", interval_seconds=60, times=1)
    assert loop.is_due() and loop.fire_tick().startswith("[/loop wakeup #1")
    assert loop.complete_tick("done")["message"] == "✓ Loop finished — ran 1/1 times."
    assert cli.cli._get_loop_manager() is loop  # cached per session id, like main


def test_idle_polls_cost_no_round_trip(cli):
    hb = cli.cli._get_heartbeat_manager()
    loop = cli.cli._get_loop_manager()
    before = len(cli.conn.calls)
    assert hb.due_prompt() is None and not loop.is_due()
    assert len(cli.conn.calls) == before


def test_no_manager_before_the_gateway_session_is_open(cli):
    cli.cli._gateway_session_open = False
    assert cli.cli._get_loop_manager() is None
    assert cli.cli._get_heartbeat_manager() is None
    assert cli.conn.calls == []


def test_off_the_gateway_the_local_managers_apply(tmp_path):
    from qubit_cli.cli_loops_mixin import CLILoopsMixin
    from qubit_cli.heartbeat import HeartbeatManager
    from qubit_cli.loops import LoopManager
    from qubit_state import SessionDB
    db = SessionDB(db_path=tmp_path / "local.db")
    obj = CLILoopsMixin.__new__(CLILoopsMixin)
    obj._session_db, obj._gateway_session_open, obj.session_id = db, False, "sid-1"
    try:
        assert type(obj._get_loop_manager()) is LoopManager
        assert type(obj._get_heartbeat_manager()) is HeartbeatManager
    finally:
        db.close()
