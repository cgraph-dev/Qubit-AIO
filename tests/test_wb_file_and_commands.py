"""Behaviour of the workbench file + command endpoint families.

WHAT THESE TESTS PROVE (not source shape — real request → real response)
---------------------------------------------------------------------------
Every test drives ``workbench_ui_routes.h_wb_gateway``, the SAME entry point the
listener calls for ``/api/wb/api/...``, with a real temp workspace registered as
an approved project and real files on disk. Nothing here asserts that a handler
"is wired"; each asserts what a browser request would get back.

The four things worth breaking:
1. a file write through ``file/create``/``file/save``/``file/delete`` actually
   lands on disk, and a delete is REVERSIBLE (archive path in the receipt);
2. a path outside the workspace — ``..``, an absolute escape, or a SYMLINK that
   points out — is refused, not silently allowed. This is the traversal case;
3. an endpoint Qubit has no authority for (``commands/bundles``,
   ``commands/moa/resolve``, ``file/move``, ``file/open-vscode``) answers a TYPED
   refusal naming why — never a fabricated 200 and never a bare 501;
4. ``/btw`` records the question on the parent session and delivers the answer
   ONLY on its stream.
"""
from __future__ import annotations

import json

import pytest

from voicebridge import workbench_ui_routes as R


# --------------------------------------------------------------- fixtures
@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A registered project folder + a cleared e-stop latch + a sibling secret.

    ``voicebridge.file_patch`` keeps its own approved-roots helper (a
    ``Path``-only view of ``projects.list_projects``); pointing it at the SAME
    temp root the project record holds keeps the two in lockstep without
    touching either implementation.
    """
    from voicebridge import file_patch as FP
    from voicebridge import files as F
    from voicebridge import power
    from voicebridge import projects as PJ

    (tmp_path / "state").mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_text("TOP SECRET\n")
    root = tmp_path / "proj"
    root.mkdir()

    class Cfg:
        def get(self, _section, _key=None, default=None, **_kw):
            return default

        def state_dir(self):
            return tmp_path / "state"

    cfg = Cfg()
    # A REAL, cleared latch file: the gate reads this store, and an absent file
    # is "not stopped" anyway, but writing it makes a misfire visible.
    power.power_control_store(cfg).set(emergency_stop=False,
                                       global_disable=False,
                                       issued_by="dashboard")
    assert PJ.create(cfg, "proj", [str(root)], confirmed=True,
                     requested_by="test")["state"] == "OK"
    monkeypatch.setattr(FP, "_approved_roots", lambda _c: [root.resolve()])
    monkeypatch.setattr(F, "approved_roots", lambda _c: [root.resolve()])
    return {"cfg": cfg, "root": root, "tmp": tmp_path,
            "state": tmp_path / "state"}


def _call(workspace, method, sub, body=None, query=None):
    """One workbench API request, exactly as the listener dispatches it."""
    return R.h_wb_gateway(workspace["cfg"], dict(query or {}),
                          dict(body or {}), {"sub": sub, "method": method})


# ------------------------------------------------------------- file writes
def test_create_then_read_then_save_roundtrips_on_disk(workspace):
    """create → read → save are the same file, and the bytes are real."""
    status, out = _call(workspace, "POST", "file/create",
                        {"session_id": "s1", "path": "notes.md", "content": ""})
    assert status == 200, out
    assert out["path"] == "notes.md"
    target = workspace["root"] / "notes.md"
    assert target.exists() and target.read_bytes() == b""

    status, out = _call(workspace, "POST", "file/save",
                        {"session_id": "s1", "path": "notes.md",
                         "content": "# hello\n"})
    assert status == 200, out
    # The client caches what the server says it wrote (workspace.js:1429).
    assert out["content"] == "# hello\n"
    assert target.read_text() == "# hello\n"
    assert "before_sha256" in out["receipt"]

    status, out = _call(workspace, "GET", "file",
                        query={"session_id": "s1", "path": "notes.md"})
    assert status == 200, out
    assert out["content"] == "# hello\n"
    assert out["lines"] == 2
    assert out["binary"] is False


def test_create_never_clobbers_an_existing_file(workspace):
    """``file/create`` on an existing path is a typed conflict, not an overwrite."""
    target = workspace["root"] / "keep.txt"
    target.write_text("precious\n")
    status, out = _call(workspace, "POST", "file/create",
                        {"session_id": "s1", "path": "keep.txt",
                         "content": ""})
    assert status == 409
    assert out["code"] == "FILE_PATCH_CONFLICT"
    assert target.read_text() == "precious\n"


def test_create_dir_then_delete_dir_is_reversible(workspace):
    """mkdir + delete land on disk, and the receipt names a recoverable archive."""
    status, out = _call(workspace, "POST", "file/create-dir",
                        {"session_id": "s1", "path": "reports"})
    assert status == 200, out
    created = workspace["root"] / "reports"
    assert created.is_dir()

    status, out = _call(workspace, "POST", "file/delete",
                        {"session_id": "s1", "path": "reports",
                         "recursive": True})
    assert status == 200, out
    assert not created.exists()
    assert out["kind"] == "directory"
    archived = out["archived_to"]
    assert archived and (workspace["state"] / "file_patch_backups").as_posix() \
        in archived.replace("\\", "/")


def test_delete_file_is_archived_not_destroyed(workspace):
    """A deleted file's bytes are still on disk under the state dir."""
    (workspace["root"] / "gone.txt").write_text("still here\n")
    status, out = _call(workspace, "POST", "file/delete",
                        {"session_id": "s1", "path": "gone.txt"})
    assert status == 200, out
    assert not (workspace["root"] / "gone.txt").exists()
    archived = out["archived_to"]
    from pathlib import Path

    archived_bytes = Path(archived).read_text()
    assert archived_bytes == "still here\n"


def test_non_empty_folder_needs_recursive(workspace):
    """Without ``recursive`` a non-empty folder is refused, nothing removed."""
    folder = workspace["root"] / "docs"
    folder.mkdir()
    (folder / "a.txt").write_text("a")
    status, out = _call(workspace, "POST", "file/delete",
                        {"session_id": "s1", "path": "docs"})
    assert status == 409
    assert out["code"] == "FILE_PATCH_DELETE_NOT_EMPTY"
    assert (folder / "a.txt").exists()


def test_save_requires_a_session_and_string_content(workspace):
    """The contract's two required fields are enforced before any write."""
    status, out = _call(workspace, "POST", "file/save",
                        {"path": "x.txt", "content": "hi"})
    assert status == 400 and out["code"] == "WB_FILE_SESSION_REQUIRED"

    status, out = _call(workspace, "POST", "file/save",
                        {"session_id": "s1", "path": "x.txt",
                         "content": {"not": "text"}})
    assert status == 400 and out["code"] == "FILE_PATCH_BAD_INPUT"
    assert not (workspace["root"] / "x.txt").exists()


# ------------------------------------------- confinement / traversal refusal
@pytest.mark.parametrize("path", [
    "../outside/secret.txt",
    "../../../../etc/passwd",
    "sub/../../outside/secret.txt",
])
def test_traversal_reads_are_refused(workspace, path):
    """A ``..`` path that leaves the workspace is a 403, never the bytes."""
    (workspace["root"] / "sub").mkdir()
    status, out = _call(workspace, "GET", "file", query={"path": path})
    assert status == 403, out
    assert out["code"] == "WB_WORKSPACE_OUTSIDE"
    assert "TOP SECRET" not in json.dumps(out)


def test_absolute_path_outside_the_workspace_is_refused(workspace):
    """An absolute path outside every approved root is refused for read AND write."""
    secret = str(workspace["tmp"] / "outside" / "secret.txt")
    status, out = _call(workspace, "GET", "file", query={"path": secret})
    assert status == 403 and out["code"] == "WB_WORKSPACE_OUTSIDE"

    status, out = _call(workspace, "POST", "file/create",
                        {"session_id": "s1", "path": secret, "content": "x"})
    assert status == 403 and out["code"] == "WB_WORKSPACE_OUTSIDE"
    assert (workspace["tmp"] / "outside" / "secret.txt").read_text() \
        == "TOP SECRET\n"


def test_symlink_escape_is_refused_for_read_and_write(workspace):
    """A symlink INSIDE the workspace that points out does not escape it."""
    link = workspace["root"] / "escape.txt"
    link.symlink_to(workspace["tmp"] / "outside" / "secret.txt")

    status, out = _call(workspace, "GET", "file", query={"path": "escape.txt"})
    assert status == 403 and out["code"] == "WB_WORKSPACE_OUTSIDE"

    status, out = _call(workspace, "POST", "file/save",
                        {"session_id": "s1", "path": "escape.txt",
                         "content": "pwned\n"})
    assert status == 403, out
    assert out["code"] in ("WB_WORKSPACE_OUTSIDE", "FILE_PATCH_OUTSIDE_ROOT")
    assert (workspace["tmp"] / "outside" / "secret.txt").read_text() \
        == "TOP SECRET\n"


def test_no_workspace_root_means_no_read(workspace, monkeypatch):
    """With no registered project, nothing is guessed — the caller is told."""
    from voicebridge import files as F

    monkeypatch.setattr(F, "approved_roots", lambda _c: [])
    status, out = _call(workspace, "GET", "file", query={"path": "x.txt"})
    assert status == 409
    assert out["code"] == "WB_NO_WORKSPACE_ROOTS"


def test_emergency_stop_latch_refuses_writes(workspace):
    """The durable e-stop gate reaches every file write (no bypass)."""
    from voicebridge import power

    power.power_control_store(workspace["cfg"]).set(
        emergency_stop=True, global_disable=False, issued_by="dashboard")
    status, out = _call(workspace, "POST", "file/create",
                        {"session_id": "s1", "path": "nope.txt", "content": ""})
    assert status == 403
    assert out["code"] == "FILE_PATCH_ESTOP"
    assert not (workspace["root"] / "nope.txt").exists()


# ---------------------------------------------------- typed refusals (no fake)
@pytest.mark.parametrize("sub,body,code", [
    ("file/move", {"session_id": "s1", "path": "a.txt", "dest_dir": "."},
     "WB_BROKERED_WRITE"),
    ("file/rename", {"session_id": "s1", "path": "a.txt", "new_name": "b.txt"},
     "WB_BROKERED_WRITE"),
    ("file/open-vscode", {"session_id": "s1", "path": "a.txt"},
     "WB_CAPABILITY_UNBOUND"),
])
def test_missing_capabilities_answer_a_typed_refusal(workspace, sub, body, code):
    """No authority → named refusal with a next step, never a 200."""
    (workspace["root"] / "a.txt").write_text("a")
    status, out = _call(workspace, "POST", sub, body)
    assert status >= 400, out
    assert out["ok"] is False
    assert out["code"] == code
    assert out["detail"].strip()
    assert (workspace["root"] / "a.txt").exists()
    assert not (workspace["root"] / "b.txt").exists()


def test_file_path_returns_the_confined_absolute_path(workspace):
    """``r.path`` is real and absolute — the UI copies it to the clipboard."""
    status, out = _call(workspace, "POST", "file/path",
                        {"session_id": "s1", "path": "a.txt"})
    assert status == 200, out
    assert out["path"] == str(workspace["root"] / "a.txt")


def test_raw_handler_serves_bytes_and_refuses_escapes(workspace):
    """The BYTES route answers a body, and an outside path is refused as JSON."""
    data = b"\x89PNG\r\n\x1a\n" + b"0" * 16
    (workspace["root"] / "px.png").write_bytes(data)
    handler = R.raw_handler("file/raw")
    assert handler is not None
    assert R.raw_handler("file/save") is None

    class _H:
        sent: dict = {}

        def __init__(self):
            self.sent = {}

        def _send(self, code, body, ctype, extra_headers=None,
                  cache_control="no-store"):
            self.sent = {"code": code, "body": body, "ctype": ctype,
                         "headers": dict(extra_headers or [])}

        def _json(self, payload, code=200):
            self.sent = {"code": code, "json": payload}

    ok = _H()
    handler(ok, workspace["cfg"], {"path": "px.png", "download": "1"})
    assert ok.sent["code"] == 200
    assert ok.sent["body"] == data
    assert ok.sent["ctype"] == "image/png"
    assert ok.sent["headers"]["Content-Disposition"].startswith("attachment;")

    bad = _H()
    handler(bad, workspace["cfg"], {"path": "../outside/secret.txt"})
    assert bad.sent["code"] == 403
    assert bad.sent["json"]["code"] == "WB_WORKSPACE_OUTSIDE"


# ------------------------------------------------------------------ commands
def test_commands_lists_the_real_slash_catalog(workspace):
    """``data.commands`` is Qubit's catalog in upstream's row shape."""
    status, out = _call(workspace, "GET", "commands")
    assert status == 200, out
    rows = out["commands"]
    assert out["total"] == len(rows) and len(rows) > 50
    for key in ("name", "description", "category", "aliases", "args_hint",
                "cli_only"):
        assert key in rows[0], key
    names = {row["name"] for row in rows}
    assert "help" in names or "model" in names


def test_commands_exec_runs_a_real_slash_command(workspace):
    """``/help`` answers the executor's real help text as ``data.output``."""
    status, out = _call(workspace, "POST", "commands/exec",
                        {"command": "/help"})
    assert status == 200, out
    assert "Available slash commands" in out["output"]


def test_commands_exec_refuses_a_non_command(workspace):
    """A body without the leading slash is a caller error, not a turn."""
    status, out = _call(workspace, "POST", "commands/exec",
                        {"command": "hello there"})
    assert status == 400 and out["code"] == "WB_COMMAND_REQUIRED"


@pytest.mark.parametrize("method,sub,body,code", [
    ("GET", "commands/bundles", {}, "WB_CAPABILITY_UNBOUND"),
    ("POST", "commands/bundles/resolve", {"command": "/whatever"},
     "WB_CAPABILITY_UNBOUND"),
    ("GET", "commands/moa/resolve", {}, "WB_CAPABILITY_UNBOUND"),
])
def test_command_paths_without_authority_refuse_and_say_why(workspace, method,
                                                            sub, body, code):
    """Bundles / MoA are typed 501s naming the missing concept — not empty lists."""
    status, out = _call(workspace, method, sub, body)
    assert status == 501, out
    assert out["ok"] is False and out["code"] == code
    assert out["qubit_counterpart"]
    assert "bundle" in out["detail"].lower() or "mixture-of-agents" in \
        out["detail"].lower()


# ----------------------------------------------------------------------- btw
def test_btw_answers_on_its_stream_and_keeps_the_parent_clean(workspace,
                                                              monkeypatch):
    """A finished side question: question recorded, answer only on the stream."""
    from voicebridge import chat_transport as CT
    from voicebridge.qubit_session_store import QubitSessionStore

    monkeypatch.setattr(CT, "stream_preflight",
                        lambda *_a, **_k: (None, None, "fake-model"))

    def _turn(_cfg, _text, **_kw):
        yield {"event": "start"}
        yield {"event": "delta", "text": "po"}
        yield {"event": "delta", "text": "ng"}
        yield {"event": "done", "reply": "pong", "receipt": {}}

    monkeypatch.setattr(CT, "stream_turn", _turn)
    store = QubitSessionStore(workspace["cfg"])
    parent = store.create(name="parent")["session"]["id"]

    status, out = _call(workspace, "POST", "btw",
                        {"session_id": parent, "question": "coin flip?"})
    assert status == 200, out
    assert out["parent_session_id"] == parent
    frames = "".join(R.stream_frames("btw/stream", workspace["cfg"],
                                     {"stream_id": out["stream_id"]}))
    assert "event: done" in frames
    # The terminal frame is the LAST data line with the answer in it.
    payload = json.loads(frames.rsplit("data: ", 1)[1].split("\n", 1)[0])
    assert payload["answer"] == "pong"

    msgs = store.messages(parent)["messages"]
    assert [m["content"] for m in msgs] == ["coin flip?"]
    sidecar = (msgs[0].get("meta") or {}).get("display_metadata") or {}
    assert sidecar.get("btw", {}).get("stream_id") == out["stream_id"]
    # The ANSWER is ephemeral: never appended to the parent transcript.
    assert all("pong" not in str(m.get("content")) for m in msgs)


def test_btw_failure_is_an_apperror_frame(workspace, monkeypatch):
    """A dead model is a typed ``apperror`` the client already handles."""
    from voicebridge import chat_transport as CT
    from voicebridge.qubit_session_store import QubitSessionStore

    monkeypatch.setattr(CT, "stream_preflight",
                        lambda *_a, **_k: (None, None, "fake-model"))
    monkeypatch.setattr(CT, "stream_turn",
                        lambda *_a, **_k: iter([{"event": "error",
                                                 "code": "MODEL_DEAD",
                                                 "detail": "no provider"}]))
    parent = QubitSessionStore(workspace["cfg"]).create(
        name="parent")["session"]["id"]
    status, out = _call(workspace, "POST", "btw",
                        {"session_id": parent, "question": "q"})
    assert status == 200, out
    frames = "".join(R.stream_frames("btw/stream", workspace["cfg"],
                                     {"stream_id": out["stream_id"]}))
    assert "event: apperror" in frames and "MODEL_DEAD" in frames


def test_btw_requires_an_existing_session(workspace):
    """A side question is about a parent's context; an unknown id is a 409."""
    status, out = _call(workspace, "POST", "btw",
                        {"session_id": "no-such-session", "question": "q"})
    assert status == 409 and out["code"] == "WB_SESSION_NOT_FOUND"

    status, out = _call(workspace, "POST", "btw", {"question": "q"})
    assert status == 400 and out["code"] == "WB_SESSION_ID_REQUIRED"
