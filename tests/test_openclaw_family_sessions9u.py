"""9U — OpenClaw contractual-pin ``sessions`` family handlers are real.

The family module ``voicebridge/gateway/qubit_handlers/sessions.py`` composes
into the single canonical gateway RPC registry. These tests invoke the handlers
in-process against a temp Qubit config and assert real backend behavior/state:

* session lifecycle is the canonical ``QubitSessionStore`` (create -> rename ->
  confirm-gated delete -> recover), not a fabricated response;
* per-session file drafts round-trip through the canonical
  ``chat_stores.FileDraftStore`` and are re-surfaced as artifacts;
* a real operator comment is written and read back through the canonical
  ``CommentStore``;
* the agent-backed terminal (the ``/chat`` path) opens/feeds/resizes/closes;
* usage/tasks/transcripts/checkpoints/git diff delegate to their canonical
  modules;
* the SAFE_EQUIVALENT / OPERATOR_ONLY refusals actually emit their typed codes
  and never fabricate success.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


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


def _registry():
    from voicebridge.gateway.rpc import default_registry

    return default_registry()


def test_sessions_family_is_registered():
    names = set(_registry().names())
    for method in (
        "sessions.preview", "sessions.describe", "sessions.resolve",
        "sessions.create", "sessions.delete", "sessions.recover",
        "sessions.patch", "sessions.patchMany", "chat.history",
        "chat.message.get", "chat.startup", "chat.metadata",
        "sessions.files.list", "sessions.files.get", "sessions.files.set",
        "artifacts.list", "progressCard.get", "session.discussion.info",
        "sessions.usage", "taskSuggestions.list", "sessions.branches.list",
        "terminal.open", "session.members.list", "session.visibility.set",
        "sessions.github.status", "sessions.dispatch",
    ):
        assert method in names, f"{method} is not registered"


def test_session_lifecycle_uses_canonical_store(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    created = reg.handle(cfg, "sessions.create",
                         {"id": "sess-9u", "name": "Nine U"})
    assert created["state"] == "OK", created
    assert created["session"]["id"] == "sess-9u"

    listed = reg.handle(cfg, "sessions.list", {})
    assert any(s["id"] == "sess-9u" for s in listed["sessions"])

    renamed = reg.handle(cfg, "sessions.patch",
                         {"sessionKey": "sess-9u", "name": "Renamed"})
    assert renamed["state"] == "OK"
    assert reg.handle(cfg, "sessions.preview",
                      {"sessionKey": "sess-9u"})["session"]["name"] == "Renamed"

    denied = reg.handle(cfg, "sessions.delete", {"sessionKey": "sess-9u"})
    assert denied["state"] == "CONFIRM_REQUIRED", denied
    deleted = reg.handle(cfg, "sessions.delete",
                         {"sessionKey": "sess-9u", "confirm": True})
    assert deleted["state"] == "OK", deleted
    assert reg.handle(cfg, "sessions.preview",
                      {"sessionKey": "sess-9u"})["state"] == "MISSING"

    recovered = reg.handle(cfg, "sessions.recover", {"sessionKey": "sess-9u"})
    assert recovered["state"] == "OK", recovered
    assert reg.handle(cfg, "sessions.preview",
                      {"sessionKey": "sess-9u"})["state"] == "OK"


def test_chat_history_and_inject_are_real(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    reg.handle(cfg, "sessions.create", {"id": "chat-9u", "name": "C"})

    injected = reg.handle(cfg, "chat.inject",
                          {"sessionKey": "chat-9u", "message": "injected text",
                           "label": "unit"})
    assert injected["state"] == "OK", injected

    history = reg.handle(cfg, "chat.history", {"sessionKey": "chat-9u"})
    assert history["state"] == "OK"
    assert any(m.get("content") == "injected text"
               for m in history["messages"]), history

    # A message with a preserved platform id is retrievable by that id; the
    # canonical store carries the identity through import.
    from voicebridge import chat_transport as CT

    imported = CT.store(cfg).import_session({
        "session": {"id": "imp-9u", "name": "Imported"},
        "messages": [{"role": "user", "content": "hello id",
                      "message_id": "m-1"}],
    })
    assert imported["state"] == "OK", imported
    one = reg.handle(cfg, "chat.message.get",
                     {"sessionKey": "imp-9u", "messageId": "m-1"})
    assert one["state"] == "OK" and one["message"]["content"] == "hello id"
    assert one["message"]["platform_message_id"] == "m-1"

    missing = reg.handle(cfg, "chat.message.get",
                         {"sessionKey": "chat-9u", "messageId": "nope"})
    assert missing["state"] == "MISSING"

    meta = reg.handle(cfg, "chat.metadata", {"sessionKey": "chat-9u"})
    assert meta["state"] == "OK" and meta.get("model")


def test_files_and_artifacts_roundtrip(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    reg.handle(cfg, "sessions.create", {"id": "files-9u", "name": "F"})

    saved = reg.handle(cfg, "sessions.files.set",
                       {"sessionKey": "files-9u", "path": "src/a.py",
                        "content": "print(1)\n"})
    assert saved["state"] == "OK", saved

    listing = reg.handle(cfg, "sessions.files.list", {"sessionKey": "files-9u"})
    assert listing["total"] == 1

    got = reg.handle(cfg, "sessions.files.get",
                     {"sessionKey": "files-9u", "path": "src/a.py"})
    assert got["draft"]["content"] == "print(1)\n"

    artifacts = reg.handle(cfg, "artifacts.list", {"sessionKey": "files-9u"})
    assert artifacts["total"] == 1
    assert artifacts["authority"].startswith("voicebridge.chat_stores")

    downloaded = reg.handle(cfg, "artifacts.download",
                            {"sessionKey": "files-9u", "path": "src/a.py"})
    assert downloaded["state"] == "OK"
    assert downloaded["content"] == "print(1)\n"
    assert downloaded["sha256"]


def test_progress_card_and_discussion(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    reg.handle(cfg, "sessions.create", {"id": "proj-9u", "name": "P"})

    card = reg.handle(cfg, "progressCard.get", {"sessionKey": "proj-9u"})
    assert card["state"] == "OK"
    assert card["projection"]["session_id"] == "proj-9u"

    from voicebridge import chat_transport as CT

    added = CT.comment_store(cfg).add_comment("proj-9u", "operator note")
    assert added["state"] == "OK", added
    info = reg.handle(cfg, "session.discussion.info",
                      {"sessionKey": "proj-9u"})
    assert info["state"] == "OK"
    assert any(c.get("body") == "operator note" for c in info["comments"])


def test_usage_transcripts_and_checkpoints(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    reg.handle(cfg, "sessions.create", {"id": "u-9u", "name": "U"})
    reg.handle(cfg, "chat.inject", {"sessionKey": "u-9u", "message": "hi"})

    usage = reg.handle(cfg, "sessions.usage", {})
    assert usage["state"] in ("OK", "EMPTY")
    assert "authority" in usage

    ts = reg.handle(cfg, "sessions.usage.timeseries", {})
    assert ts["state"] in ("OK", "EMPTY")

    tlist = reg.handle(cfg, "transcripts.list", {})
    assert tlist["state"] == "OK"
    assert any(t["id"] == "u-9u" for t in tlist["transcripts"])

    tget = reg.handle(cfg, "transcripts.get", {"selector": "u-9u"})
    assert tget["state"] == "OK"

    branches = reg.handle(cfg, "sessions.branches.list", {})
    assert branches["state"] == "OK"
    assert isinstance(branches["branches"], list)

    suggestions = reg.handle(cfg, "taskSuggestions.list", {})
    assert suggestions["state"] == "OK"
    assert suggestions["authority"].startswith("derived read-only")


def test_terminal_is_the_canonical_agent_backed_surface(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    reg.handle(cfg, "sessions.create", {"id": "t-9u", "name": "T"})

    opened = reg.handle(cfg, "terminal.open",
                        {"sessionKey": "t-9u", "cols": 120, "rows": 40})
    assert opened["ok"] is True and opened["sessionId"] == "t-9u"
    assert opened["cols"] == 120 and opened["rows"] == 40
    assert opened["banner"]

    fed = reg.handle(cfg, "terminal.input", {"sessionId": "t-9u", "data": ""})
    assert fed["ok"] is True and fed["state"] == "OK"

    resized = reg.handle(cfg, "terminal.resize",
                         {"sessionId": "t-9u", "cols": 90, "rows": 30})
    assert resized["cols"] == 90 and resized["rows"] == 30

    attached = reg.handle(cfg, "terminal.attach", {"sessionId": "t-9u"})
    assert attached["ok"] is True and attached["alive"] is True

    closed = reg.handle(cfg, "terminal.close", {"sessionId": "t-9u"})
    assert closed["ok"] is True and closed["alive"] is False


def test_sessions_diff_delegates_to_git_scope(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    from voicebridge import projects as PJ

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "f.txt").write_text("one\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=n",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    (repo / "f.txt").write_text("two\n")

    PJ.create(cfg, "t9u", [str(repo)], confirmed=True, use=True)
    out = reg.handle(cfg, "sessions.diff", {"root": str(repo)})
    assert out["state"] == "OK", out
    assert "two" in out["diff"] and out["authority"] == "voicebridge.git_scope.diff"


def test_safe_equivalent_and_operator_only_emit_typed_codes(tmp_path,
                                                            monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    for method in ("session.members.list", "session.members.add",
                   "session.members.remove", "session.members.listEvidence"):
        out = reg.handle(cfg, method, {})
        assert out["code"] == "SESSION_MEMBERS_LOCAL_SHARE", (method, out)
        assert out["state"] == "UNAVAILABLE"

    out = reg.handle(cfg, "session.visibility.set", {})
    assert out["code"] == "SESSION_VISIBILITY_LOCAL_SHARE", out

    for method in ("sessions.github.publish", "sessions.github.options",
                   "sessions.github.status", "sessions.github.confirm"):
        out = reg.handle(cfg, method, {})
        assert out["code"] == "FORGE_API_OPERATOR_REQUIRED", (method, out)
        assert out["operator_prerequisite"]

    for method in ("sessions.dispatch", "sessions.reclaim", "sessions.move"):
        out = reg.handle(cfg, method, {})
        assert out["code"] == "RUNTIME_CLOUD_UNAVAILABLE", (method, out)
        assert out["operator_prerequisite"]


def test_bad_params_are_typed(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    for method, params in (
        ("sessions.preview", {}),
        ("chat.history", {}),
        ("sessions.files.get", {"sessionKey": "x", "path": ""}),
        ("terminal.input", {}),
    ):
        try:
            reg.handle(cfg, method, params)
        except GatewayError as exc:
            assert exc.code == "GATEWAY_BAD_PARAMS", (method, exc.code)
        else:  # pragma: no cover - a missing key must never be swallowed
            raise AssertionError(f"{method} accepted bad params")


# --------------------------------------------------------------------------- #
# 9U wave 2 — remaining family methods dispose to real backends / typed codes
# --------------------------------------------------------------------------- #
def test_new_family_methods_are_registered():
    names = set(_registry().names())
    for method in (
        "conversations.list", "conversations.send", "conversations.turn",
        "conversations.turn.cancel", "sessions.steer", "sessions.fork",
        "sessions.usage.logs", "sessions.compaction.list",
        "session.discussion.open", "sessions.title.prepare", "terminal.list",
        "terminal.upload", "sessions.files.reveal",
        "sessions.observer.visibility", "send", "message.action", "poll",
    ):
        assert method in names, f"{method} is not registered"


def test_conversations_delegate_to_the_session_authority(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    reg.handle(cfg, "sessions.create", {"id": "conv-9u", "name": "C"})

    listed = reg.handle(cfg, "conversations.list", {})
    assert listed["state"] == "OK"
    assert listed["code"] == "CONVERSATIONS_LOCAL_SESSIONS"
    assert any(c["id"] == "conv-9u" for c in listed["conversations"])

    sent = reg.handle(cfg, "conversations.send",
                      {"conversationRef": "conv-9u", "message": "hello conv"})
    assert sent["state"] == "OK"
    assert sent["code"] == "CONVERSATIONS_LOCAL_SESSION_APPEND"

    history = reg.handle(cfg, "chat.history", {"sessionKey": "conv-9u"})
    assert any(m.get("content") == "hello conv" for m in history["messages"])

    cancel = reg.handle(cfg, "conversations.turn.cancel",
                        {"conversationRef": "conv-9u"})
    assert cancel["code"] == "CONVERSATIONS_LOCAL_SESSION_CANCEL"
    # Real behavior: there is no in-flight turn, so the canonical cancel is a
    # truthful no-op, never a fabricated success.
    assert cancel["ok"] is False
    assert cancel["cancel_code"] == "NO_ACTIVE_TURN"


def test_sessions_steer_injects_into_the_canonical_transcript(tmp_path,
                                                               monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    reg.handle(cfg, "sessions.create", {"id": "steer-9u", "name": "S"})

    out = reg.handle(cfg, "sessions.steer",
                     {"sessionKey": "steer-9u", "message": "steer me"})
    assert out["state"] == "OK"
    assert out["code"] == "SESSIONS_STEER_LOCAL_INJECT"

    history = reg.handle(cfg, "chat.history", {"sessionKey": "steer-9u"})
    assert any(m.get("content") == "steer me" for m in history["messages"])


def test_usage_logs_compaction_and_discussion_open(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    reg.handle(cfg, "sessions.create", {"id": "reads-9u", "name": "R"})
    reg.handle(cfg, "chat.inject", {"sessionKey": "reads-9u", "message": "hi"})

    logs = reg.handle(cfg, "sessions.usage.logs", {"days": 7})
    assert logs["state"] in ("OK", "EMPTY")
    assert logs["code"] == "USAGE_LOGS_LOCAL_DAILY"
    assert isinstance(logs["logs"], list)

    comp = reg.handle(cfg, "sessions.compaction.list", {})
    assert comp["state"] == "OK"
    assert comp["code"] == "COMPACTION_LOCAL_CHECKPOINTS"
    assert isinstance(comp["checkpoints"], list)

    from voicebridge import chat_transport as CT

    added = CT.comment_store(cfg).add_comment("reads-9u", "thread note")
    assert added["state"] == "OK", added
    opened = reg.handle(cfg, "session.discussion.open",
                        {"sessionKey": "reads-9u"})
    assert opened["state"] == "OK" and opened["open"] is True
    assert opened["code"] == "DISCUSSION_LOCAL_COMMENTS"
    assert any(c.get("body") == "thread note" for c in opened["comments"])


def test_title_prepare_is_a_real_local_derivation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    out = reg.handle(cfg, "sessions.title.prepare",
                     {"agentId": "qubit",
                      "message": "Implement the voice bridge parity checker "
                                 "for the sessions family gateway handlers."})
    assert out["state"] == "OK"
    assert out["code"] == "SESSIONS_TITLE_LOCAL_EXTRACTIVE"
    assert isinstance(out["title"], str) and out["title"]

    empty = reg.handle(cfg, "sessions.title.prepare",
                       {"agentId": "qubit", "message": ""})
    assert empty["state"] == "OK" and empty["title"] is None


def test_terminal_list_upload_and_files_reveal(tmp_path, monkeypatch):
    import base64

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    reg.handle(cfg, "sessions.create", {"id": "term9u", "name": "T"})

    opened = reg.handle(cfg, "terminal.open", {"sessionKey": "term9u"})
    assert opened["ok"] is True

    listed = reg.handle(cfg, "terminal.list", {})
    assert listed["state"] == "OK"
    assert listed["authority"].startswith("voicebridge.chat_transport")
    assert any(t["sessionId"] == "term9u" and t["alive"]
               for t in listed["terminals"])

    payload = base64.b64encode(b"uploaded body\n").decode("ascii")
    up = reg.handle(cfg, "terminal.upload",
                    {"sessionId": "term9u", "name": "upload.txt",
                     "contentBase64": payload})
    assert up["state"] == "OK"
    assert up["code"] == "TERMINAL_UPLOAD_LOCAL_DRAFT"

    got = reg.handle(cfg, "sessions.files.get",
                     {"sessionKey": "term9u", "path": "upload.txt"})
    assert got["draft"]["content"] == "uploaded body\n"

    reveal = reg.handle(cfg, "sessions.files.reveal",
                        {"sessionKey": "term9u", "path": "upload.txt"})
    assert reveal["state"] == "OK"
    assert reveal["code"] == "FILES_REVEAL_LOCAL_PATH"
    assert reveal["path"] == "upload.txt" and reveal["sha256"]


def test_sessions_fork_copies_the_transcript(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    reg.handle(cfg, "sessions.create", {"id": "fork-9u", "name": "F"})
    reg.handle(cfg, "chat.inject", {"sessionKey": "fork-9u", "message": "one"})
    reg.handle(cfg, "chat.inject", {"sessionKey": "fork-9u", "message": "two"})

    out = reg.handle(cfg, "sessions.fork",
                     {"sessionKey": "fork-9u", "entryId": "1"})
    assert out["state"] == "OK", out
    assert out["code"] == "SESSIONS_FORK_LOCAL_TRANSCRIPT_COPY"
    assert out["forked_from"] == "fork-9u" and out["entry_index"] == 1

    child = out["sessionId"]
    assert child != "fork-9u"
    history = reg.handle(cfg, "chat.history", {"sessionKey": child})
    assert [m.get("content") for m in history["messages"]] == ["one"]
    # The source transcript is untouched.
    src = reg.handle(cfg, "chat.history", {"sessionKey": "fork-9u"})
    assert len(src["messages"]) == 2


def test_messaging_and_observer_emit_typed_codes(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()

    for method in ("send", "message.action", "poll"):
        out = reg.handle(cfg, method, {})
        assert out["code"] == "MESSAGING_TRANSPORT_OPERATOR_REQUIRED", \
            (method, out)
        assert out["state"] == "UNAVAILABLE"
        assert out["operator_prerequisite"]

    obs = reg.handle(cfg, "sessions.observer.visibility", {})
    assert obs["code"] == "SESSION_OBSERVER_LOCAL_SHARE"
    assert obs["state"] == "UNAVAILABLE"


def test_wave2_bad_params_are_typed(tmp_path, monkeypatch):
    from voicebridge.gateway.gateway_ws import GatewayError

    cfg = _cfg(tmp_path, monkeypatch)
    reg = _registry()
    for method, params in (
        ("conversations.send", {"conversationRef": "x"}),
        ("conversations.send", {"message": "hi"}),
        ("sessions.steer", {"message": "no session"}),
        ("sessions.files.reveal", {"sessionKey": "x", "path": ""}),
        ("terminal.upload", {"sessionId": "x"}),
        ("sessions.fork", {}),
    ):
        try:
            reg.handle(cfg, method, params)
        except GatewayError as exc:
            assert exc.code == "GATEWAY_BAD_PARAMS", (method, exc.code)
        else:  # pragma: no cover - a missing key must never be swallowed
            raise AssertionError(f"{method} accepted bad params {params}")
