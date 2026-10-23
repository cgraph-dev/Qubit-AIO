"""The CLI's session store is the gateway's, never a state.db writer in the CLI process.

Main acquired ``qubit_state_registry`` (a writer handle) at startup. Rewired, the gateway is the
single writer: ``_init_session_store`` builds a ``GatewaySessionStore`` and opens no SessionDB.
The same-store assertion this file used to make described the pre-gateway port; it pinned the
local writer that left ``GatewayAgent`` without a connection ("no local fallback" on every turn).
"""

from types import SimpleNamespace

from cli import HermesCLI


def test_cli_session_store_is_the_gateway_store_and_opens_no_writer(monkeypatch):
    import qubit_state_registry
    from qubit_cli.gateway_session_store import GatewaySessionStore

    constructed = []
    monkeypatch.setattr(qubit_state_registry, "_open_session_db", lambda path: constructed.append(path))
    cli = SimpleNamespace()
    HermesCLI._init_session_store(cli)
    assert isinstance(cli._session_db, GatewaySessionStore) and not cli._session_db_unavailable
    assert constructed == []
