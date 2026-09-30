"""Packet 8U — chat projection backend (A33/A35/A36/A40).

Covers the S5-owned backend:

* A35 — line-separator preservation on the confined write path;
* A36 — per-session file-draft store (single writer, no orphan rows);
* A33 — per-session comment / CI-detail store;
* A40 — session-activity projection (git scope + media + summary + events).

No fabricated success: each section/store is asserted through its typed
envelope, and the projection's unavailable paths are exercised explicitly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from voicebridge import chat_stores as CS
from voicebridge import chat_transport as CT
from voicebridge import file_patch as FP
from voicebridge import session_activity as SA


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _project(cfg, folder):
    from voicebridge import projects as PJ

    created = PJ.create(cfg, "Work", [str(folder)], requested_by="t",
                        confirmed=True, use=True)
    assert created.get("state") == "OK", created
    return created


def _confinement_ok():
    return FP.confinement_available()[0]


def _apply(cfg, target, patch, **kw):
    kw.setdefault("requested_by", "cli:user")
    kw.setdefault("confirmed", True)
    return FP.apply_patch(cfg, str(target), patch, **kw)


def _insert_patch(old="beta", new="inserted", start=1, count=3):
    return (f"--- a/notes.txt\n+++ b/notes.txt\n"
            f"@@ -{start},{count} +{start},{count + 1} @@\n alpha\n+{new}\n"
            f" {old}\n gamma\n")


# ============================================================ A35: separators
def test_detect_line_separator_dominant():
    assert FP.detect_line_separator("a\nb\nc\n") == "\n"
    assert FP.detect_line_separator("a\r\nb\r\nc\r\n") == "\r\n"
    assert FP.detect_line_separator("a\rb\rc\r") == "\r"
    assert FP.detect_line_separator("a\r\nb\nc\r\n") == "\r\n"
    # No terminator at all -> conventional default.
    assert FP.detect_line_separator("just one line") == "\n"
    assert FP.detect_line_separator("", default="\r\n") == "\r\n"


def test_split_lines_with_sep_preserves_each_terminator():
    assert FP._split_lines_with_sep("a\r\nb\nc") == [
        ("a", "\r\n"), ("b", "\n"), ("c", "")]
    assert FP._split_lines_with_sep("") == []
    assert FP._split_lines_with_sep("a\n") == [("a", "\n")]


def test_apply_hunks_preserves_crlf(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")

    receipt = _apply(cfg, target, _insert_patch())
    assert receipt["state"] == "OK", receipt
    assert target.read_bytes() == b"alpha\r\ninserted\r\nbeta\r\ngamma\r\n"

    FP.rollback(cfg, str(target), backup=receipt["backup"],
                requested_by="cli:user", confirmed=True)
    assert target.read_bytes() == b"alpha\r\nbeta\r\ngamma\r\n"


def test_apply_hunks_preserves_bare_cr(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_bytes(b"alpha\rbeta\rgamma\r")
    receipt = _apply(cfg, target, _insert_patch())
    assert receipt["state"] == "OK", receipt
    assert target.read_bytes() == b"alpha\rinserted\rbeta\rgamma\r"


def test_apply_hunks_preserves_missing_trailing_newline(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_bytes(b"alpha\nbeta\ngamma")
    receipt = _apply(cfg, target, _insert_patch())
    assert receipt["state"] == "OK", receipt
    assert target.read_bytes() == b"alpha\ninserted\nbeta\ngamma"


def test_apply_hunks_preserves_mixed_endings(tmp_path, monkeypatch):
    if not _confinement_ok():
        pytest.skip("bubblewrap confinement unavailable")
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "notes.txt"
    target.write_bytes(b"alpha\r\nbeta\ngamma\r\n")
    patch = "--- a\n+++ b\n@@ -2,1 +2,1 @@\n-beta\n+BETA\n"
    receipt = _apply(cfg, target, patch)
    assert receipt["state"] == "OK", receipt
    assert target.read_bytes() == b"alpha\r\nBETA\r\ngamma\r\n"


# ================================================ A36: per-session file drafts
def test_file_draft_store_roundtrip_and_upsert(tmp_path):
    st = CS.FileDraftStore(tmp_path)
    st.reload()
    first = st.put("s1", "src/app.py", "print('a')\n")
    assert first["state"] == "OK" and first["code"] == "DRAFT_CREATED"
    draft = first["draft"]
    assert draft["path"] == "src/app.py"
    assert draft["content"] == "print('a')\n"
    assert draft["sha256"]

    again = st.put("s1", "src/app.py", "print('b')\n", base_sha256="deadbeef")
    assert again["state"] == "OK" and again["code"] == "DRAFT_SAVED"
    assert again["draft"]["id"] == draft["id"]
    assert again["draft"]["base_sha256"] == "deadbeef"
    assert again["draft"]["created_at"] <= again["draft"]["updated_at"]

    got = st.get("s1", "src/app.py")
    assert got["state"] == "OK"
    assert got["draft"]["content"] == "print('b')\n"

    listed = st.list("s1")
    assert listed["total"] == 1
    assert listed["drafts"][0]["content"] == "print('b')\n"
    hidden = st.list("s1", include_content=False)
    assert "content" not in hidden["drafts"][0]


def test_file_draft_store_is_single_writer_across_instances(tmp_path):
    a = CS.FileDraftStore(tmp_path)
    b = CS.FileDraftStore(tmp_path)
    a.put("s1", "one.txt", "1")
    # The second instance re-hydrates from disk; it is not a stale copy.
    assert b.get("s1", "one.txt")["state"] == "OK"
    b.put("s1", "two.txt", "2")
    assert a.list("s1")["total"] == 2


def test_file_draft_delete_and_clear_need_confirm(tmp_path):
    st = CS.FileDraftStore(tmp_path)
    st.put("s1", "one.txt", "1")
    assert st.delete("s1", "one.txt")["state"] == "CONFIRM_REQUIRED"
    assert st.delete("s1", "one.txt", confirmed=True)["state"] == "OK"
    assert st.delete("s1", "one.txt", confirmed=True)["state"] == "MISSING"

    st.put("s1", "a.txt", "a")
    st.put("s1", "b.txt", "b")
    assert st.clear_session("s1")["state"] == "CONFIRM_REQUIRED"
    cleared = st.clear_session("s1", confirmed=True)
    assert cleared["state"] == "OK" and cleared["removed"] == 2


def test_file_draft_bounds_and_typed_errors(tmp_path):
    st = CS.FileDraftStore(tmp_path)
    with pytest.raises(CS.ChatStoreError) as bad_sid:
        st.put("", "a.txt", "x")
    assert bad_sid.value.code == "CHAT_STORE_BAD_INPUT"
    with pytest.raises(CS.ChatStoreError) as bad_path:
        st.put("s1", "", "x")
    assert bad_path.value.code == "CHAT_STORE_BAD_PATH"
    with pytest.raises(CS.ChatStoreError) as too_big:
        st.put("s1", "a.txt", "x" * (CS.MAX_DRAFT_CHARS + 1))
    assert too_big.value.code == "CHAT_STORE_TOO_LARGE"


def test_file_draft_orphan_row_refused_when_session_check_given(tmp_path,
                                                                monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    st = CT.store(cfg)
    drafts = CS.FileDraftStore(tmp_path / "state")
    with pytest.raises(CS.ChatStoreError) as missing:
        drafts.put("nope", "a.txt", "x", session_check=st.show)
    assert missing.value.code == "CHAT_STORE_SESSION_NOT_FOUND"

    st.create("real", session_id="s1")
    saved = drafts.put("s1", "a.txt", "x", session_check=st.show)
    assert saved["state"] == "OK"


# ============================================= A33: comments / CI-detail store
def test_comment_store_add_list_delete(tmp_path):
    st = CS.CommentStore(tmp_path)
    added = st.add_comment("s1", "please review", target="src/app.py")
    assert added["state"] == "OK" and added["code"] == "COMMENT_ADDED"
    cid = added["comment"]["id"]
    st.add_comment("s1", "second")
    listed = st.list_comments("s1")
    assert listed["total"] == 2
    only_target = st.list_comments("s1", target="src/app.py")
    assert only_target["total"] == 1

    assert st.delete_comment("s1", cid)["state"] == "CONFIRM_REQUIRED"
    assert st.delete_comment("s1", cid, confirmed=True)["state"] == "OK"
    assert st.delete_comment("s1", cid, confirmed=True)["state"] == "MISSING"


def test_comment_store_ci_details(tmp_path):
    st = CS.CommentStore(tmp_path)
    ok = st.add_ci_detail("s1", check="pytest", status="success",
                          detail="41 passed", url="https://ci/1", ref="abc")
    assert ok["state"] == "OK" and ok["code"] == "CI_DETAIL_ADDED"
    st.add_ci_detail("s1", check="lint", status="failure", detail="E501")
    assert st.list_ci_details("s1")["total"] == 2
    assert st.list_ci_details("s1", check="pytest")["total"] == 1
    with pytest.raises(CS.ChatStoreError) as bad:
        st.add_ci_detail("s1", check="x", status="exploded")
    assert bad.value.code == "CI_STATUS_INVALID"
    with pytest.raises(CS.ChatStoreError) as empty:
        st.add_comment("s1", "   ")
    assert empty.value.code == "COMMENT_EMPTY"


def test_comment_store_persistence_and_clear(tmp_path):
    a = CS.CommentStore(tmp_path)
    a.add_comment("s1", "hello")
    a.add_ci_detail("s1", check="build", status="success")
    b = CS.CommentStore(tmp_path)
    assert b.list_comments("s1")["total"] == 1
    assert b.list_ci_details("s1")["total"] == 1
    assert b.clear_session("s1")["state"] == "CONFIRM_REQUIRED"
    cleared = b.clear_session("s1", confirmed=True)
    assert cleared["removed_comments"] == 1 and cleared["removed_ci"] == 1


def test_comment_session_check_refuses_orphan(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    st = CT.store(cfg)
    comments = CS.CommentStore(tmp_path / "state")
    with pytest.raises(CS.ChatStoreError) as missing:
        comments.add_comment("ghost", "hi", session_check=st.show)
    assert missing.value.code == "CHAT_STORE_SESSION_NOT_FOUND"


# =================================================== A40: session-activity log
def test_session_activity_store_record_list_counts(tmp_path):
    st = SA.SessionActivityStore(tmp_path)
    st.reload()
    st.record("s1", "git_scope", detail="3 files changed")
    st.record("s1", "media_generation", detail="image ready")

    assert st.counts("s1")["git_scope"] == 1
    assert st.counts("s1")["media_generation"] == 1
    listed = st.list("s1")
    assert listed["total"] == 2
    # newest-first
    assert listed["events"][0]["kind"] == "media_generation"
    assert st.list("s1", kind="git_scope")["total"] == 1

    exported = st.export("s1")
    assert exported["count"] == 2


def test_session_activity_bad_kind_raises(tmp_path):
    st = SA.SessionActivityStore(tmp_path)
    with pytest.raises(SA.SessionActivityError) as exc:
        st.record("s1", "not_really")
    assert exc.value.code == "SESSION_ACTIVITY_BAD_KIND"
    assert not st.list("s1")["events"]


def test_session_activity_persists_and_clears_with_confirm(tmp_path):
    a = SA.SessionActivityStore(tmp_path)
    a.record("s1", "note", detail="first")
    b = SA.SessionActivityStore(tmp_path)
    b.reload()
    assert b.list("s1")["total"] == 1
    assert b.clear("s1")["state"] == "CONFIRM_REQUIRED"
    assert b.clear("s1", confirmed=True)["removed"] == 1
    assert b.list("s1")["total"] == 0


def test_session_activity_redacts_secret_detail(tmp_path):
    st = SA.SessionActivityStore(tmp_path)
    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWX"
    st.record("s1", "note", detail=f"token {secret}")
    blob = json.dumps(st.export("s1"))
    assert secret not in blob


def test_build_projection_composes_real_sections(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    # Real canonical chat session store, not a test double.
    st = CT.store(cfg)
    st.create("S", session_id="s1")
    st.append("s1", "user", "do the thing")
    st.append("s1", "assistant", "Built the parser and fixed the bug. Tests pass.")
    acts = SA.store(cfg)
    acts.record("s1", "git_scope", detail="2 files changed")

    monkeypatch.setattr(
        "voicebridge.git_scope.status",
        lambda cfg_, root="", requested_by="cli:user": {
            "state": "OK", "root": "/proj", "status": " M a.py\n?? b.py\n",
            "truncated": False, "rollback": "read-only"})
    monkeypatch.setattr(
        "voicebridge.media_gen.status",
        lambda cfg_: {"state": "OK", "estop": False,
                      "configured": ["openai:image"], "unavailable": []})

    out = SA.build_projection(cfg, "s1", git_root="/proj", session_store=st,
                              activity_store=acts)
    assert out["state"] == "OK"
    assert out["git_scope"]["state"] == "OK"
    assert out["git_scope"]["changed_count"] == 2
    assert out["media"]["state"] == "OK"
    assert out["media"]["configured"] == ["openai:image"]
    assert out["summary"]["state"] == "OK"
    assert out["summary"]["source"] == "session"
    assert "parser" in out["summary"]["text"]
    assert out["counts"]["git_scope"] == 1
    assert len(out["events"]) == 1


def test_build_projection_reports_unavailable_not_empty_ok(tmp_path,
                                                           monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)

    def _boom(*_a, **_k):
        raise RuntimeError("no repo here")

    monkeypatch.setattr("voicebridge.git_scope.status", _boom)
    monkeypatch.setattr(
        "voicebridge.media_gen.status",
        lambda cfg_: {"state": "FAILED", "code": "MEDIA_GEN_ERROR",
                      "detail": "transport down"})

    out = SA.build_projection(cfg, "s1", activity_store=SA.store(cfg))
    assert out["state"] == "OK"
    assert out["git_scope"]["state"] == "UNAVAILABLE"
    assert out["git_scope"]["code"] == "GIT_SCOPE_ERROR"
    assert out["media"]["state"] == "UNAVAILABLE"
    assert out["media"]["code"] == "MEDIA_GEN_ERROR"
    # No session source and no events -> honestly EMPTY, never invented text.
    assert out["summary"]["state"] == "EMPTY"


def test_build_projection_summarizes_activity_when_no_session(tmp_path,
                                                              monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "voicebridge.git_scope.status",
        lambda *_a, **_k: {"state": "OK", "root": "/p", "status": "",
                           "truncated": False})
    monkeypatch.setattr(
        "voicebridge.media_gen.status",
        lambda cfg_: {"state": "OK", "estop": False, "configured": [],
                      "unavailable": []})
    acts = SA.store(cfg)
    acts.record("s1", "note", detail="Shipped the fix. Reviewer must sign off.")
    out = SA.build_projection(cfg, "s1", activity_store=acts)
    assert out["summary"]["state"] == "OK"
    assert out["summary"]["source"] == "activity"
    assert "Shipped" in out["summary"]["text"]


# ===================================== transport delegators (S5 in chat_transport)
def test_transport_draft_and_comment_stores_guard_the_session_authority(
        tmp_path, monkeypatch):
    from voicebridge import auth_status as AS

    cfg = _cfg(tmp_path, monkeypatch)
    CT.store(cfg).create("S", session_id="s1")

    drafts = CT.draft_store(cfg)
    assert isinstance(drafts, CS.FileDraftStore)
    assert drafts.put("s1", "src/app.py", "print('x')\n")["state"] == "OK"
    with pytest.raises(CS.ChatStoreError) as missing:
        drafts.put("ghost", "src/app.py", "x")
    assert missing.value.code == "CHAT_STORE_SESSION_NOT_FOUND"

    comments = CT.comment_store(cfg)
    assert isinstance(comments, CS.CommentStore)
    assert comments.add_comment("s1", "please review")["state"] == "OK"
    assert comments.add_ci_detail("s1", check="pytest",
                                  status="success")["state"] == "OK"
    with pytest.raises(CS.ChatStoreError):
        comments.add_comment("ghost", "hi")

    # The delegates persist in their own files; the transcript authority is
    # the ONE canonical ``state.db`` (QubitSessionStore), not a JSON sidecar.
    state = Path(cfg.state_dir())
    assert CT.SESSION_AUTHORITY == \
        "voicebridge.qubit_session_store.QubitSessionStore"
    assert AS.CHAT_TRANSCRIPT_STORE == "state.db"
    assert Path(AS.CHAT_TRANSCRIPT_STORE).name == "state.db"
    assert (state / CS.DRAFT_FILE).is_file()
    assert (state / CS.COMMENT_FILE).is_file()


def test_transport_build_projection_reads_the_canonical_session_store(
        tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    st = CT.store(cfg)
    st.create("S", session_id="s1")
    st.append("s1", "user", "do the thing")
    st.append("s1", "assistant", "Built the parser and fixed the bug.")

    monkeypatch.setattr(
        "voicebridge.git_scope.status",
        lambda *_a, **_k: {"state": "OK", "root": "/p", "status": "",
                           "truncated": False})
    monkeypatch.setattr(
        "voicebridge.media_gen.status",
        lambda cfg_: {"state": "OK", "estop": False, "configured": [],
                      "unavailable": []})

    # No ``session_store`` argument: the delegator injects the one authority.
    out = CT.build_projection(cfg, "s1")
    assert out["state"] == "OK"
    assert out["summary"]["state"] == "OK"
    assert out["summary"]["source"] == "session"
    assert "parser" in out["summary"]["text"]
