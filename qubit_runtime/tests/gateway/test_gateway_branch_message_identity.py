"""The gateway transcript branch path must carry the parent's message identity.

``gateway/slash_commands_session.py:_branch_row`` rebuilds each child row from an
allow-list (``_BRANCH_COPIED_FIELDS``) that omits the identity fields. The CLI
(``cli_commands_mixin.py``), workdir (``session_workdir.py:_persist_branch_seed``)
and Desktop (``tui_gateway/methods_session.py:_persist_branch``) branch/seed copies
all apply ``message_identity()``; this one did not, so a child minted fresh
``message_uid`` values while the parent's live dicts kept their own. A context
engine keying on ``message_uid`` (agent/message_metadata.py:102-106) then saw a
different identity on every gateway branch boundary.

The regression the sibling fix missed: fixing ``_persist_branch`` alone left this
call path broken.
"""

from __future__ import annotations

import pytest

from qubit_state import SessionDB

UID_RE = r"^[0-9a-f]{32}$"


@pytest.fixture()
def db(tmp_path):
    handle = SessionDB(db_path=tmp_path / "state.db")
    try:
        yield handle
    finally:
        handle.close()


def _gateway_branch_rows(history):
    """Import the real helper under test (not a reimplementation)."""
    from gateway.slash_commands_session import _branch_row

    return [_branch_row(msg) for msg in history]


def test_gateway_branch_rows_carry_the_parent_message_identity(db):
    db.create_session("parent", source="gateway", model="m")
    history = [
        {"role": "user", "content": "hello", "message_uid": "a" * 32},
        {"role": "assistant", "content": "hi"},
    ]
    db.append_messages_batch("parent", history)
    copied = db.get_messages("parent")
    parent_uids = [m["message_uid"] for m in copied]
    # A pre-existing uid must survive verbatim; a dict without one gets one minted
    # on BOTH sides (row and the live dict).
    assert parent_uids[0] == "a" * 32

    rows = _gateway_branch_rows(copied)
    assert rows[0]["message_uid"] == parent_uids[0]

    db.create_session("child", source="gateway", model="m")
    db.append_messages_batch("child", rows)
    stored = [r["message_uid"] for r in db._conn.execute(
        "SELECT message_uid FROM messages WHERE session_id='child' ORDER BY id").fetchall()]
    assert stored == parent_uids, (
        "the branch child must restore with the SAME identity the parent's live "
        "history carries; minting fresh uids diverges them forever"
    )


def test_gateway_branch_row_stamps_missing_uid_on_the_live_dict(db):
    """A dict that had no uid is stamped, exactly like the Desktop branch path."""
    db.create_session("parent", source="gateway", model="m")
    live = [{"role": "user", "content": "unflushed"}]
    rows = _gateway_branch_rows(live)
    assert live[0]["message_uid"] == rows[0]["message_uid"]


def test_gateway_branch_row_keeps_tool_call_uids_paired(db):
    """``with_tool_uids=True``: an assistant row's calls and a result's uid ride along."""
    db.create_session("parent", source="gateway", model="m")
    call = {"id": "call_1", "type": "function", "function": {"name": "t", "arguments": "{}"}}
    msgs = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {"role": "tool", "content": "r", "tool_call_id": "call_1", "tool_name": "t"},
    ]
    db.append_messages_batch("parent", msgs, chunk_rows=2)
    # The conversation projection restores the per-occurrence tool-call uids; the raw
    # ``get_messages`` projection does not (it does not pair calls), so use the former.
    copied = db.get_messages_as_conversation("parent", repair_alternation=False)
    rows = _gateway_branch_rows(copied)
    assert rows[1]["_tool_call_uids"] == copied[1]["_tool_call_uids"]
    assert rows[2]["_tool_call_uid"] == copied[2]["_tool_call_uid"]
