"""8U A10 — one writable chat-session authority (canonical C7 model).

The unified session contract (``voicebridge.auth_status.session_authority``)
names exactly one writable authority per concern. For chat transcripts that
authority is ``voicebridge.qubit_session_store.QubitSessionStore`` — a strict
adapter over the vendored ``qubit_state.SessionDB`` (``state.db`` under the
Qubit runtime home). The terminal/TUI/agent and every native surface
(``/chat``, ``/qubit-chat``, CLI, dashboard, voice) read/write that ONE
database. The receipt-lifecycle store (``auth_status.SessionStore``,
``sessions.json``) is a disjoint domain, and the S5 projection stores
(drafts/comments/activity) live in their own files and may not reference an
unknown session. The legacy JSON ``ChatSessionStore`` is retained only as a
non-production utility and is never the named authority.

Hermetic: isolated XDG dirs + QUBIT_HOME, no network, no live services.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from voicebridge import auth_status as AS
from voicebridge import chat_stores as CS
from voicebridge import chat_transport as CT


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    home = tmp_path / "home" / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(home))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


# --------------------------------------------------------- declared authority
def test_chat_transport_reads_the_single_session_authority():
    contract = CT.session_authority()
    assert contract == AS.session_authority()
    writable = contract["writable_authorities"]
    assert writable["chat_transcript"] == CT.SESSION_AUTHORITY
    assert CT.SESSION_AUTHORITY == \
        "voicebridge.qubit_session_store.QubitSessionStore"
    assert writable["receipt_lifecycle"] == \
        "voicebridge.auth_status.SessionStore"
    assert CT.owns_chat_session_authority() is True

    # The canonical chat store is the vendored SessionDB (state.db), NOT the
    # retired JSON file the legacy utility still writes.
    assert AS.CHAT_TRANSCRIPT_STORE == "state.db"
    assert CT.STORE_FILE == "chat_sessions.json"  # legacy utility only
    boundary = CT.store_boundary()
    assert boundary["chat"]["authority"] == CT.SESSION_AUTHORITY
    assert boundary["chat"]["file"] == AS.CHAT_TRANSCRIPT_STORE == "state.db"
    assert boundary["chat"]["schema_id"] == AS.CHAT_TRANSCRIPT_SCHEMA


def test_canonical_chat_store_is_the_fork_session_db(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    store = CT.store(cfg)
    from voicebridge.qubit_session_store import QubitSessionStore

    assert isinstance(store, QubitSessionStore)
    assert store.BACKEND == "qubit_state.SessionDB"

    boundary = CT.store_boundary()
    fork = boundary["fork_agent_sessions"]
    # C7: the fork's state.db IS the canonical native chat store — one
    # database, one writer path per surface, no copy-sync.
    assert fork["writable"] is True
    assert fork["file"] == "state.db"
    contract = CT.session_authority()
    assert contract["backing_stores"]["chat_transcript"] == \
        "qubit-runtime (qubit_state.SessionDB)"
    assert "fork_agent_sessions" not in contract["writable_authorities"]


# ------------------------------------------------ one writer, one reader file
def test_canonical_store_writes_only_state_db(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    st = CT.store(cfg)
    sid = st.create("Only Writer")["session"]["id"]
    st.append(sid, "user", "hello")

    root = Path(cfg.state_dir())
    # The canonical writer creates state.db (its vendored home), never the
    # retired chat_sessions.json file.
    assert not (root / "chat_sessions.json").exists()
    assert not (root / AS.RECEIPT_SESSION_STORE).exists()

    # Reopening reads the same single database, not a copy or a second store.
    fresh = CT.store(cfg)
    assert fresh.show(sid)["state"] == "OK"
    assert [m["content"] for m in fresh.show(sid)["messages"]] == ["hello"]


def test_chat_lifecycle_reads_through_the_authority(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # Every lifecycle verb goes through ``CT.store`` — the single authority —
    # so the list surface can only ever reflect authority state.
    st = CT.store(cfg)
    sid = st.create("Lifecycle")["session"]["id"]
    st.append(sid, "user", "q")
    st.append(sid, "assistant", "a")

    listed = CT.store(cfg).list()
    assert listed["total"] == 1
    assert listed["sessions"][0]["id"] == sid
    # The list surface passes through the REAL state.db source; the native
    # create path records ``dashboard`` (never the retired hardcoded label).
    assert listed["sessions"][0]["source"] == "dashboard"

    assert CT.store(cfg).rename(sid, "Renamed")["state"] == "OK"
    assert CT.store(cfg).show(sid)["session"]["name"] == "Renamed"
    assert CT.store(cfg).archive(sid, True)["state"] == "OK"
    assert CT.store(cfg).list(include_archived=False)["total"] == 0
    assert CT.store(cfg).delete(sid)["state"] == "CONFIRM_REQUIRED"
    assert CT.store(cfg).delete(sid, confirm=True)["state"] == "OK"
    assert CT.store(cfg).show(sid)["state"] == "MISSING"
    # Delete is a reversible archive: restore brings the SAME session back.
    assert CT.store(cfg).restore(sid)["state"] == "OK"
    assert CT.store(cfg).show(sid)["state"] == "OK"


# --------------------------------------- projection stores are not a 2nd writer
def test_projection_stores_are_guarded_and_not_a_chat_writer(tmp_path,
                                                              monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    CT.store(cfg).create("S", session_id="s1")

    drafts = CT.draft_store(cfg)
    assert isinstance(drafts, CS.FileDraftStore)
    assert drafts.put("s1", "src/app.py", "print('x')\n")["state"] == "OK"
    with pytest.raises(CS.ChatStoreError) as draft_missing:
        drafts.put("ghost", "src/app.py", "x")
    assert draft_missing.value.code == "CHAT_STORE_SESSION_NOT_FOUND"

    comments = CT.comment_store(cfg)
    assert isinstance(comments, CS.CommentStore)
    assert comments.add_comment("s1", "review")["state"] == "OK"
    assert comments.add_ci_detail("s1", check="build",
                                  status="success")["state"] == "OK"
    with pytest.raises(CS.ChatStoreError) as comment_missing:
        comments.add_comment("ghost", "hi")
    assert comment_missing.value.code == "CHAT_STORE_SESSION_NOT_FOUND"

    # They persist in their own files; the canonical transcript authority is
    # untouched and the session still resolves through it.
    root = Path(cfg.state_dir())
    assert (root / CS.DRAFT_FILE).is_file()
    assert (root / CS.COMMENT_FILE).is_file()
    assert CT.store(cfg).show("s1")["state"] == "OK"
    boundary = CT.store_boundary()
    assert AS.CHAT_TRANSCRIPT_STORE not in boundary["chat"]["not_owner_of"]
    assert set(boundary["chat"]["owns"]) == {"chat sessions",
                                             "conversation messages",
                                             "tool/system rows",
                                             "session ids/timestamps/order"}
