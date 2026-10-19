"""``gateway_session_store.create_titled_session`` — the gateway is the single writer.

Pins the request payload ``main._create_titled_session`` now reaches (#86794): the classic CLI's
named-thread create is a ``session.create`` with ``source="cli"`` and an empty ``row_cwd``, exactly
as upstream / the store's ``create_titled_session`` comment specifies.
"""

from __future__ import annotations

import os

import pytest


class _FakeStore:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result if result is not None else {"stored_session_id": "gw-session-id"}
        self.error = error

    def call(self, method, **kwargs):
        self.calls.append((method, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


def _install(monkeypatch, store):
    from qubit_cli import gateway_session_store

    monkeypatch.setattr(gateway_session_store, "session_store", lambda: store)
    return gateway_session_store


def test_create_titled_session_sends_the_cli_row_shape(monkeypatch, tmp_path):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.setenv("TERMINAL_CWD", str(cwd))
    store = _FakeStore()
    gss = _install(monkeypatch, store)

    assert gss.create_titled_session("Bot Chat") == "gw-session-id"

    [(method, params)] = store.calls
    assert method == "session.create"
    assert params["title"] == "Bot Chat"
    assert params["source"] == "cli", "main's titled row is labelled cli"
    assert params["row_cwd"] == "", "the explicit create leaves the row's cwd empty"
    assert params["cwd"] == os.path.realpath(str(cwd))
    assert len(params["request_id"]) == 32
    int(params["request_id"], 16)  # uuid4 hex


def test_create_titled_session_returns_none_when_the_owner_refuses(monkeypatch, tmp_path):
    from qubit_cli.gateway_client import GatewayClientError

    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    store = _FakeStore(error=GatewayClientError("not_ready"))
    gss = _install(monkeypatch, store)

    assert gss.create_titled_session("Bot Chat") is None
