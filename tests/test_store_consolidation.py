"""F3 — single writable authority per domain (boards + sessions).

Proves the ownership contract the final wave requires:

(a) exactly one writable board authority: ``voicebridge.kanban`` (native). The
    vendored Hermes kanban store's dashboard write path is a typed
    ``KANBAN_WRITE_BROKERED`` refusal and the in-process compatibility
    boundary (``kanban_bridge.vendored_write``) refuses identically;
(b) board data written to the native store survives a reload and is served by
    the vendored dashboard READ path (which delegates to the same authority);
(c) the chat-transcript store (``chat_sessions.json``) and the receipt
    session store (``sessions.json``) are disjoint authorities: different
    files, schemas, record namespaces, and neither writes the other's records.

Hermetic: temp ``XDG_*`` + ``QUBIT_HOME``; no network; no live services.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    qubit = tmp_path / "home" / ".hermes"
    qubit.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(qubit))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# ---------------------------------------------------------------------------
# (a) no second writable board authority
# ---------------------------------------------------------------------------
def test_single_writable_board_authority_is_typed_refused(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import kanban as N
    from voicebridge import kanban_bridge as KBG

    cfg = _cfg(tmp_path, monkeypatch)

    # The contract declares exactly one writable authority.
    auth = KBG.BOARD_AUTHORITY
    assert auth["writable"] == ["qubit-native"], auth
    assert auth["read_only"] == ["hermes-vendored"], auth
    native = KBG.native_store(cfg)
    vendored = KBG.vendored_store(cfg)
    assert native["writable"] is True and native["authoritative"] is True
    assert vendored["writable"] is False and vendored["authoritative"] is False
    assert vendored["read_only"] is True

    # A route with no native-schema equivalent is a typed refusal on the one
    # shared gate; core board/task writes are native-backed (one authority) and
    # stage a real approval rather than a second-store write.
    for method, path, body in (
            ("POST", "/api/plugins/kanban/tasks", {"title": "x"}),
            ("POST", "/api/plugins/kanban/boards", {"slug": "probe"})):
        status, out = HC.dispatch(cfg, method, path, {}, body)
        assert status == 409, (method, path, status, out)
        assert out.get("code") == "CONFIRM_REQUIRED", (method, path, out)
        assert out.get("confirmation_id"), (method, path, out)

    status, out = HC.dispatch(cfg, "PUT",
                              "/api/plugins/kanban/orchestration", {}, {})
    assert status == 403, (status, out)
    assert out.get("code") == "KANBAN_WRITE_BROKERED", out
    assert out.get("qubit_counterpart"), out

    # A confirmed compat write lands on the NATIVE store (the one writable
    # authority), never the vendored store.
    status, made = HC.dispatch(
        cfg, "POST", "/api/plugins/kanban/tasks", {},
        {"title": "native-via-compat", "confirm": True})
    assert status == 200 and made.get("ok") is True, (status, made)
    native_titles = {t["title"] for t in N.list_tasks(
        cfg, board="main")["tasks"]}
    assert "native-via-compat" in native_titles, native_titles

    # The in-process compatibility boundary refuses the same way.
    refused = KBG.vendored_write("create", board="default", title="nope")
    assert refused["ok"] is False
    assert refused["code"] == "KANBAN_WRITE_BROKERED"
    assert refused["authoritative_store"] == "qubit-native"
    assert refused["writable_store"] == "qubit-native"
    assert refused["qubit_counterpart"]

    # The native store is the one surface a write lands on.
    made = N.create(cfg, "native-ok", board="main", confirmed=True)
    assert made["task"]["id"] is not None
    stores = KBG.stores(cfg)
    assert stores["authoritative"] == "qubit-native"
    assert stores["writable"] == "qubit-native"
    # Two native rows: the direct ``N.create`` above plus the compat-confirmed
    # create (which also landed on the native store, never the vendored one).
    assert stores["native"]["task_count"] == 2
    assert stores["vendored"]["task_count"] == 0


def test_vendored_seed_is_not_a_competing_authority(tmp_path, monkeypatch):
    """A legacy vendored row is neither served nor silently merged."""
    from voicebridge import qubit_compat as HC
    from voicebridge import qubit_plugins as HP
    from voicebridge import kanban as N
    from voicebridge import kanban_bridge as KBG

    cfg = _cfg(tmp_path, monkeypatch)

    # Simulate an old fork store with a vendored-only row.
    engine = HP._kanban_engine(cfg)
    conn = engine._conn(board=None)
    try:
        engine.kanban_db.create_task(
            conn, title="legacy-vendored", created_by="test", board=None,
            initial_status="running")
        conn.commit()
    finally:
        conn.close()

    # The dashboard READ (the single authority) does not serve the legacy row.
    status, board = HC.dispatch(
        cfg, "GET", "/api/plugins/kanban/board", {}, {})
    assert status == 200 and board.get("ok") is True, board
    titles = [t["title"] for col in board["columns"] for t in col["tasks"]]
    assert "legacy-vendored" not in titles, titles
    stores = KBG.stores(cfg)
    assert stores["native"]["task_count"] == 0
    assert stores["vendored"]["task_count"] == 1

    # The only sanctioned convergence is the explicit migration.
    out = KBG.migrate(cfg)
    assert out["imported"] == 1, out
    assert "legacy-vendored" in [
        t["title"] for t in N.list_tasks(cfg)["tasks"]]


# ---------------------------------------------------------------------------
# (b) board data survives a reload; vendored read path serves the authority
# ---------------------------------------------------------------------------
def test_board_data_survives_reload_and_vendored_read(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import kanban as N
    from voicebridge import kanban_bridge as KBG

    cfg = _cfg(tmp_path, monkeypatch)
    N.create(cfg, "durable-task", board="main", confirmed=True)

    # A fresh list (new connection) still sees the persisted row.
    reopened = N.list_tasks(cfg)
    assert [t["title"] for t in reopened["tasks"]] == ["durable-task"]

    # The vendored dashboard READ delegates to the same single authority.
    status, board = HC.dispatch(
        cfg, "GET", "/api/plugins/kanban/board", {}, {})
    assert status == 200 and board.get("ok") is True, board
    titles = [t["title"] for col in board["columns"] for t in col["tasks"]]
    assert "durable-task" in titles, titles

    # And the contract still reports one writable authority after the reload.
    stores = KBG.stores(cfg)
    assert stores["native"]["task_count"] == 1
    assert stores["native"]["writable"] is True
    assert stores["vendored"]["writable"] is False


# ---------------------------------------------------------------------------
# (c) the two session stores do not both own the same records
# ---------------------------------------------------------------------------
def test_session_stores_are_disjoint_authorities(tmp_path, monkeypatch):
    from voicebridge import auth_status as AS
    from voicebridge import chat_transport as CT

    cfg = _cfg(tmp_path, monkeypatch)
    state = Path(cfg.state_dir())
    state.mkdir(parents=True, exist_ok=True)

    # Canonical chat transcript authority (C7): own session + conversation turn
    # in the vendored state.db the terminal/agent and every native surface use.
    chat = CT.store(cfg)
    assert chat.create("Chat One", session_id="chat-1")["state"] == "OK"
    assert chat.append("chat-1", "user", "hello transcript")["state"] == "OK"

    # Receipt-session authority in the SAME state dir, over actions.json.
    (state / "actions.json").write_text(json.dumps({
        "receipts": {"r1": {"receipt_id": "r1", "action_id": "a1",
                            "state": "done", "code": "OK", "created_at": 1.0,
                            "detail": "ran a thing"}}}), encoding="utf-8")
    sess = AS.SessionStore(state)
    renamed = sess.rename("r1", "Receipt One")
    assert renamed["state"] == "OK", renamed

    # Two stores, two schemas, no shared record namespace.
    sess_file = state / AS.SessionStore.STORE_FILE
    assert sess_file.name == "sessions.json" and sess_file.is_file()
    sess_doc = json.loads(sess_file.read_text(encoding="utf-8"))
    assert sess_doc["schema"] == AS.LIFECYCLE_SCHEMA
    export_doc = chat.export("chat-1")
    assert export_doc["session"]["id"] == "chat-1"
    assert "r1" not in json.dumps(export_doc)
    assert "chat-1" not in sess_doc["sessions"]
    assert "messages" not in sess_doc
    assert "hello transcript" not in json.dumps(sess_doc)

    # The boundary is documented and names both authorities + exclusions.
    boundary = AS.store_boundary()
    assert boundary["schema"] == "qubit-store-boundary/1"
    assert boundary["sessions"]["authority"] == \
        "voicebridge.auth_status.SessionStore"
    assert boundary["chat"]["authority"] == \
        "voicebridge.qubit_session_store.QubitSessionStore"
    assert boundary["sessions"]["file"] == "sessions.json"
    assert boundary["chat"]["file"] == "state.db"
    assert "state.db" in boundary["sessions"]["not_owner_of"]
    assert "sessions.json" in boundary["chat"]["not_owner_of"]
