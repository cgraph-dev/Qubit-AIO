"""Board metadata + lifecycle for the split-out ``kanban_db_boards`` module.

Covers the ``board.json``-as-identity-marker rule (#43243): discovery ignores a
DB-only stub directory, an archive leaves an ``archived`` tombstone at the slug,
and explicit creation clears that tombstone so the board can be recreated.
Also pins that the split module is reachable through ``kanban_db`` (the tail
re-import) so every existing ``kanban_db.<name>`` caller keeps working.
"""

from __future__ import annotations

import json

import pytest

from qubit_cli import kanban_db as kb


@pytest.fixture
def fresh_home(tmp_path, monkeypatch):
    """Isolated root with no prior kanban state."""
    home = tmp_path / "qubit_home"
    home.mkdir()
    monkeypatch.setenv("QUBIT_HOME", str(home))
    for var in (
        "HERMES_KANBAN_DB",
        "HERMES_KANBAN_WORKSPACES_ROOT",
        "HERMES_KANBAN_HOME",
        "HERMES_KANBAN_BOARD",
    ):
        monkeypatch.delenv(var, raising=False)
    kbc = pytest.importorskip("qubit_cli.kanban_db_connect")
    kbc._INITIALIZED_PATHS.clear()
    yield home


def test_names_are_reexported_from_kanban_db():
    """The split module's public names resolve through ``kanban_db`` (tail import)."""
    for name in (
        "_dir_holds_board", "board_metadata_path", "read_board_metadata",
        "write_board_metadata", "create_board", "list_boards", "remove_board",
        "_default_board_display_name",
    ):
        assert hasattr(kb, name), f"kanban_db must re-export {name}"


def test_board_json_is_the_identity_marker(fresh_home):
    """A directory holding only ``kanban.db`` (no board.json) is a stub, not a board."""
    boards_root = kb.boards_root()
    stub = boards_root / "db-only-stub"
    stub.mkdir(parents=True)
    (stub / "kanban.db").write_bytes(b"")
    assert kb._dir_holds_board(stub) is False
    assert "db-only-stub" not in [b["slug"] for b in kb.list_boards()]


def test_create_then_list_round_trip(fresh_home):
    kb.create_board("atm10-server", name="ATM10")
    slugs = [b["slug"] for b in kb.list_boards()]
    assert slugs[0] == "default"  # always first
    assert "atm10-server" in slugs
    meta = kb.read_board_metadata("atm10-server")
    assert meta["name"] == "ATM10"
    assert meta["created_at"] and meta["archived"] is False


def test_archive_leaves_tombstone_then_recreate_clears_it(fresh_home):
    """remove_board(archive=True) leaves an ``archived`` board.json tombstone; a
    later explicit create_board clears it so the board comes back (#43243)."""
    kb.create_board("proj")
    result = kb.remove_board("proj", archive=True)
    assert result["action"] == "archived"
    tombstone = kb.read_board_metadata("proj")
    assert tombstone["archived"] is True
    assert json.loads(kb.board_metadata_path("proj").read_text())["archived"] is True

    # archived board hidden unless requested
    assert "proj" not in [b["slug"] for b in kb.list_boards(include_archived=False)]

    # explicit create clears the tombstone
    kb.create_board("proj")
    assert kb.read_board_metadata("proj")["archived"] is False
    assert "proj" in [b["slug"] for b in kb.list_boards()]


def test_hard_delete_removes_directory(fresh_home):
    kb.create_board("gone")
    result = kb.remove_board("gone", archive=False)
    assert result["action"] == "deleted"
    assert kb.board_dir("gone").exists() is False


def test_default_board_cannot_be_removed(fresh_home):
    with pytest.raises(ValueError):
        kb.remove_board("default")


def test_default_board_display_name():
    assert kb._default_board_display_name("atm10-server") == "Atm10 Server"
    assert kb._default_board_display_name("my_proj") == "My Proj"
