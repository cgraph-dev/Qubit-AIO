"""``sessions prune`` under a live gateway: the owner deletes, the CLI never writes.

``sessions_owner.owner_prune`` / ``report_owner_failure`` had zero callers, so a prune beside a
running gateway still wrote the store from the CLI. These pin the upstream wiring: ``cmd_sessions``
discovers the ready owner and opens the store read-only, and both prune paths send the RPC.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest


class _FakeDB:
    def __init__(self, *, read_only=False):
        self.read_only = read_only
        self.local_prune_calls = 0

    def close(self):
        pass

    def list_prune_candidates(self, **_kwargs):
        return [
            {
                "id": "s1",
                "last_active": 0,
                "source": "cli",
                "model": "m",
                "message_count": 1,
            }
        ]

    def count_open_prune_matches(self, **_kwargs):
        return 0

    def count_prune_matches(self, **_kwargs):
        return 0

    def list_never_active_keyed_sessions(self, **_kwargs):
        return [
            {"id": "s1", "started_at": 0, "source": "cli", "session_key": "k"},
            {"id": "s2", "started_at": 0, "source": "cli", "session_key": "k2"},
        ]

    def prune_sessions(self, **_kwargs):
        self.local_prune_calls += 1
        return 0

    def prune_never_active_keyed_sessions(self, **_kwargs):
        self.local_prune_calls += 1
        return (0, 0, 0)


def _prune_args(**overrides):
    base = dict(
        sessions_action="prune",
        dry_run=False,
        yes=True,
        include_archived=False,
        include_pinned=False,
        never_active=False,
        older_than=None,
        owner_endpoint="ep-live",
    )
    for attr in (
        "before",
        "after",
        "older_than",
        "newer_than",
        "source",
        "model",
        "title",
        "min_messages",
        "max_messages",
        "workspace",
        "branch",
    ):
        base.setdefault(attr, None)
    base.update(overrides)
    return Namespace(**base)


@pytest.fixture
def owner_calls(monkeypatch):
    calls = []

    def _owner_prune(endpoint, **params):
        calls.append((endpoint, params))
        return {"deleted": 3}

    from qubit_cli import sessions_owner

    monkeypatch.setattr(sessions_owner, "owner_prune", _owner_prune)
    return calls


def test_bulk_prune_sends_the_rpc_to_the_owner(monkeypatch, owner_calls, capsys):
    from qubit_cli import sessions_cmd

    monkeypatch.setattr("qubit_cli.session_filters.build_prune_filters", lambda args: {})
    monkeypatch.setattr("qubit_cli.session_filters.describe_filters", lambda filters: "filters")

    db = _FakeDB()
    assert sessions_cmd._cmd_prune_or_archive(db, _prune_args(), "prune") is None

    assert owner_calls and owner_calls[0][0] == "ep-live"
    assert owner_calls[0][1] == {"filters": {"archived": False, "include_pinned": False, "lineage_tips_only": False}}
    assert db.local_prune_calls == 0, "the CLI must not write under a live owner"
    assert "Pruned 3 session(s)." in capsys.readouterr().out


def test_never_active_prune_sends_the_owner_its_days(monkeypatch, owner_calls, capsys):
    from qubit_cli import sessions_cmd

    result = {"deleted": 2, "routing_deleted": 1, "skipped": 0}

    def _owner_prune(endpoint, **params):
        owner_calls.append((endpoint, params))
        return result

    monkeypatch.setattr("qubit_cli.sessions_owner.owner_prune", _owner_prune)

    db = _FakeDB()
    assert sessions_cmd._prune_never_active_keyed(db, _prune_args(never_active=True)) is None

    assert owner_calls and owner_calls[0][0] == "ep-live"
    assert owner_calls[0][1] == {"never_active_days": 30.0}
    assert db.local_prune_calls == 0
    assert "Deleted 2 never-active session(s) and 1 stale routing entr(ies)." in capsys.readouterr().out


def test_cmd_sessions_discovers_the_owner_and_reads_only(monkeypatch, tmp_path):
    from qubit_cli import sessions_cmd, sessions_owner

    opened = []
    seen = {}

    class _RecordingDB(_FakeDB):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            opened.append(kwargs.get("read_only"))

    import qubit_state

    monkeypatch.setattr(qubit_state, "SessionDB", _RecordingDB)
    monkeypatch.setattr(qubit_state, "_default_db_path", lambda: Path(tmp_path / "state.db"))
    monkeypatch.setattr(sessions_owner, "live_owner", lambda: "ep-live")
    monkeypatch.setitem(
        sessions_cmd._DB_HANDLERS,
        "prune",
        lambda db, args: seen.update(owner_endpoint=getattr(args, "owner_endpoint", None)),
    )

    assert sessions_cmd.cmd_sessions(Namespace(sessions_action="prune")) is None

    assert seen["owner_endpoint"] == "ep-live"
    assert opened == [True], "a prune beside a live owner reads the preview, never writes"


def test_cmd_sessions_without_an_owner_keeps_the_local_writer(monkeypatch, tmp_path):
    from qubit_cli import sessions_cmd, sessions_owner

    opened = []
    seen = {}

    class _RecordingDB(_FakeDB):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            opened.append(kwargs.get("read_only"))

    import qubit_state

    monkeypatch.setattr(qubit_state, "SessionDB", _RecordingDB)
    monkeypatch.setattr(qubit_state, "_default_db_path", lambda: Path(tmp_path / "state.db"))
    monkeypatch.setattr(sessions_owner, "live_owner", lambda: None)
    monkeypatch.setitem(
        sessions_cmd._DB_HANDLERS,
        "prune",
        lambda db, args: seen.update(owner_endpoint=getattr(args, "owner_endpoint", None)),
    )

    assert sessions_cmd.cmd_sessions(Namespace(sessions_action="prune")) is None

    assert seen["owner_endpoint"] is None
    assert opened == [False]
