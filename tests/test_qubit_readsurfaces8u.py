"""Packet 8U read surfaces: scoped Git / Files / Audio compat routes.

Adds the audit §1 absent read surfaces (Git 21, Files/FS 12, Audio 6) so the
vendored Hermes dashboard receives real Qubit data instead of a 404:

* Git reads (``GET /api/git/status|log|diff|show`` + review/diff + file-diff)
  execute ``voicebridge.git_scope`` (read-only, bounded, redacted); every
  mutating/review verb without a backend returns a typed gate.
* Files/FS reads (``/api/fs/list|tree|stat|read|read-text|read-data-url``,
  ``/api/fs/git-root``, ``/api/fs/default-cwd``) execute
  ``voicebridge.files`` / ``voicebridge.git_scope``; binary streams and the
  write verbs stay typed-gated.
* Audio reads (``/api/audio/status|list|devices``) execute
  ``voicebridge.audio.settings``; streaming/WS + external catalogs + actions
  stay typed-gated.

Assertions: every new route resolves (``HC.match``) and dispatches to 200 or
its typed gate -- never 404/500; shapes are spot-checked against the real
module output; gated rows carry ``code`` + ``qubit_counterpart`` +
``state=UNAVAILABLE``; and pre-existing routes are not shadowed.
"""
from __future__ import annotations

import base64
import shutil
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
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _repo(tmp_path, name="work"):
    assert shutil.which("git"), "git is required for the scoped git reads"
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    env = {"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(tmp_path),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
    (root / "notes.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"],
                   check=True, env=env)
    return root, env


def _project(cfg, folder):
    from voicebridge import projects as PJ

    created = PJ.create(cfg, "Work", [str(folder)], requested_by="t",
                        confirmed=True, use=True)
    assert created.get("state") == "OK", created
    return created


# (method, path, handler) -- implemented reads backed by real modules.
IMPLEMENTED = [
    ("GET", "/api/git/status", "h_git_status"),
    ("GET", "/api/git/log", "h_git_log"),
    ("GET", "/api/git/diff", "h_git_diff"),
    ("GET", "/api/git/show", "h_git_show"),
    ("GET", "/api/git/review/diff", "h_git_file_diff"),
    ("GET", "/api/git/file-diff", "h_git_file_diff"),
    ("GET", "/api/git/worktrees", "h_git_worktrees"),
    ("GET", "/api/git/branches", "h_git_branches"),
    ("GET", "/api/git/base-branches", "h_git_base_branches"),
    ("GET", "/api/git/review/list", "h_git_review_list"),
    ("GET", "/api/git/review/commit-context", "h_git_review_commit_context"),
    ("GET", "/api/git/review/rev-parse", "h_git_review_rev_parse"),
    ("GET", "/api/fs/list", "h_fs_list"),
    ("GET", "/api/fs/tree", "h_fs_tree"),
    ("GET", "/api/fs/stat", "h_fs_stat"),
    ("GET", "/api/fs/read", "h_fs_read_text"),
    ("GET", "/api/fs/read-text", "h_fs_read_text"),
    ("GET", "/api/fs/read-data-url", "h_fs_read_data_url"),
    ("GET", "/api/fs/git-root", "h_fs_git_root"),
    ("GET", "/api/fs/default-cwd", "h_fs_default_cwd"),
    ("GET", "/api/audio/status", "h_audio_status"),
    ("GET", "/api/audio/list", "h_audio_devices"),
    ("GET", "/api/audio/devices", "h_audio_devices"),
    ("GET", "/api/audio/voice-config", "h_audio_voice_config"),
]

# (method, path) -- implemented HTTP audio actions with a real local backend:
# a valid upload/speak request reaches the model; in a hermetic env the model
# is absent, so the handler answers a TYPED refusal (never 404/500/fabrication).
AUDIO_ACTIONS = [
    ("POST", "/api/audio/transcribe"),
    ("POST", "/api/audio/speak"),
]

# (method, path, code) -- typed gates (writes / binary / credentialed / WS).
GATED = [
    ("GET", "/api/git/gh-auth", "GH_AUTH_OPERATOR"),
    ("GET", "/api/git/review/ship-info", "GIT_SHIP_GH_EXTERNAL"),
    ("POST", "/api/git/review/pr-list", "GIT_WRITE_BROKERED"),
    ("POST", "/api/git/review/push", "GIT_WRITE_BROKERED"),
    ("POST", "/api/git/review/create-pr", "GIT_WRITE_BROKERED"),
    ("GET", "/api/media", "MEDIA_NOT_SERVED"),
    ("GET", "/api/files/download", "FILES_BINARY_STREAM"),
    ("GET", "/api/files/stream", "FILES_BINARY_STREAM"),
    ("GET", "/api/fs/download", "FILES_BINARY_STREAM"),
    ("GET", "/api/audio/elevenlabs/voices", "PROVIDER_EXTERNAL"),
    ("POST", "/api/audio/tts-lease", "AUDIO_LEASE_DESKTOP"),
    ("GET", "/api/audio/speak-stream", "AUDIO_STREAM_LOCAL"),
    ("POST", "/api/audio/speak-stream", "AUDIO_STREAM_LOCAL"),
]

# (method, path) -- real brokered writes converted in Wave 4. Unconfirmed they
# answer a typed 409 CONFIRM_REQUIRED; confirmed they run the real Qubit
# file/git broker (see test_qubit_routes_agent2b_wiring.py).
BROKERED = [
    ("POST", "/api/fs/write-text"),
    ("POST", "/api/files/upload"),
    ("POST", "/api/git/review/stage"),
    ("POST", "/api/git/review/unstage"),
    ("POST", "/api/git/review/revert"),
    ("POST", "/api/git/review/commit"),
    ("POST", "/api/git/worktree/add"),
    ("POST", "/api/git/worktree/remove"),
    ("POST", "/api/git/branch/switch"),
]


def _query_for(path, root):
    """Parameters that make each implemented read answer with real data."""
    if path in ("/api/git/show", "/api/git/diff",
                "/api/git/review/diff", "/api/git/file-diff"):
        return {"file": "notes.md"}
    if path in ("/api/fs/read", "/api/fs/read-text", "/api/fs/read-data-url"):
        return {"path": str(root / "notes.md")}
    return {}


def _brokered_body(path, root):
    """Unconfirmed request body for a Wave 4 brokered write."""
    if path == "/api/fs/write-text":
        return {"path": str(root / "notes.md"), "content": "changed"}
    if path == "/api/files/upload":
        return {"path": str(root / "uploaded.txt"), "content": "uploaded"}
    return {}


# ------------------------------------------------------------------ resolution
def test_new_read_routes_resolve_to_handlers():
    """Every added reader is matched by the real route table (no 404 path)."""
    from voicebridge import qubit_compat as HC

    for method, path, handler in IMPLEMENTED:
        route, _ = HC.match(method, path)
        assert route is not None, (method, path)
        assert route["disposition"] == "implemented", (method, path, route)
        assert route["handler"] == handler, (method, path, route["handler"])

    for method, path in AUDIO_ACTIONS:
        route, _ = HC.match(method, path)
        assert route is not None, (method, path)
        assert route["disposition"] == "implemented", (method, path, route)
        assert route["handler"], (method, path, route)


def test_gated_routes_resolve_to_typed_gates():
    """Every added non-read resolves to a gate carrying a typed code."""
    from voicebridge import qubit_compat as HC

    for method, path, code in GATED:
        route, _ = HC.match(method, path)
        assert route is not None, (method, path)
        assert route["disposition"] == "gated", (method, path, route)
        assert route["gate"]["code"] == code, (method, path, route["gate"])
        assert route["gate"]["counterpart"], (method, path, route["gate"])


def test_brokered_routes_are_real_confirm_gated(tmp_path, monkeypatch):
    """Wave 4 brokered writes resolve to implemented handlers that stage a
    typed 409 until confirmed (the confirmed apply is proven separately)."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)
    for method, path in BROKERED:
        route, _ = HC.match(method, path)
        assert route is not None, (method, path)
        assert route["disposition"] == "implemented", (method, path, route)
        code, out = HC.dispatch(cfg, method, path, {},
                                _brokered_body(path, root))
        assert code == 409, (method, path, code, str(out)[:200])
        assert out.get("ok") is False, (method, path, out)
        assert out.get("code") == "CONFIRM_REQUIRED", (method, path, out)
        assert out.get("qubit_counterpart"), (method, path, out)


# ------------------------------------------------------------------ dispatch
def test_all_new_routes_dispatch_200_or_typed_never_404_500(
        tmp_path, monkeypatch):
    """Each new route answers 200 (real data) or its typed gate."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)

    for method, path, _handler in IMPLEMENTED:
        code, out = HC.dispatch(cfg, method, path, _query_for(path, root), {})
        assert code == 200, (method, path, code, str(out)[:200])
        assert isinstance(out, dict) and out.get("ok") is True, (path, out)

    for method, path, _code in GATED:
        code, out = HC.dispatch(cfg, method, path, {}, {})
        assert code not in (404, 500), (method, path, code, str(out)[:200])
        assert code != 200, (method, path, out)

    for method, path in BROKERED:
        code, out = HC.dispatch(cfg, method, path, {},
                                _brokered_body(path, root))
        assert code == 409, (method, path, code, str(out)[:200])
        assert out.get("code") == "CONFIRM_REQUIRED", (method, path, out)

    for method, path in AUDIO_ACTIONS:
        code, out = HC.dispatch(cfg, method, path, {}, {})
        assert code not in (404, 500), (method, path, code, str(out)[:200])
        assert out.get("ok") is False, (method, path, out)


def test_audio_action_routes_are_real_handlers_with_owner_rollback(
        tmp_path, monkeypatch):
    """transcribe/speak are implemented handlers; without a model they answer
    a typed refusal carrying code + owner + rollback (never a fabricated 2xx)."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for method, path in AUDIO_ACTIONS:
        route, _ = HC.match(method, path)
        assert route is not None and route["disposition"] == "implemented", \
            (method, path, route)
        code, out = HC.dispatch(cfg, method, path, {}, {})
        assert code not in (404, 500), (method, path, code, out)
        assert out.get("ok") is False, (method, path, out)
        assert out.get("code"), (method, path, out)
        assert out.get("owner"), (method, path, out)
        assert out.get("rollback"), (method, path, out)


def test_new_routes_never_500_without_project(tmp_path, monkeypatch):
    """With no active project, reads are honest (200 empty or typed 400)."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for method, path, _handler in IMPLEMENTED:
        code, out = HC.dispatch(cfg, method, path, {}, {})
        assert code not in (404, 500), (method, path, code, str(out)[:200])


def test_gated_routes_carry_code_counterpart_unavailable(
        tmp_path, monkeypatch):
    """Gated rows answer the typed envelope: code + counterpart + owner +
    rollback + UNAVAILABLE (audit-mandated gate fields)."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for method, path, code in GATED:
        resp, out = HC.dispatch(cfg, method, path, {}, {})
        assert resp not in (200, 404, 500), (method, path, resp, out)
        assert out["ok"] is False, (method, path, out)
        assert out["state"] == "UNAVAILABLE", (method, path, out)
        assert out["code"] == code, (method, path, out)
        assert out["qubit_counterpart"], (method, path, out)
        assert out.get("owner"), (method, path, out)
        assert out.get("rollback"), (method, path, out)


# --------------------------------------------------------------- git shapes
def test_git_status_log_diff_show_shapes(tmp_path, monkeypatch):
    """Git reads reflect the real scoped repository, redacted and bounded."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)
    (root / "notes.md").write_text("hello changed\n")

    code, out = HC.dispatch(cfg, "GET", "/api/git/status", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["root"] == str(root)
    assert "notes.md" in out["status"], out
    assert out["schema"] == "qubit-runtime-git/1"

    code, out = HC.dispatch(cfg, "GET", "/api/git/log", {"limit": "5"}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["entries"] and out["entries"][0]["subject"] == "init", out
    for key in ("commit", "author", "date", "subject"):
        assert key in out["entries"][0], (key, out["entries"][0])

    code, out = HC.dispatch(cfg, "GET", "/api/git/diff",
                            {"file": "notes.md"}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert "hello changed" in out["patch"], out

    code, out = HC.dispatch(cfg, "GET", "/api/git/show",
                            {"file": "notes.md", "ref": "HEAD"}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["content"].strip() == "hello", out


def test_git_reads_are_scoped_and_typed_on_missing_repo(tmp_path, monkeypatch):
    """A missing/outside root is a typed 400, never a 500."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/git/status",
                            {"root": str(tmp_path / "nowhere")}, {})
    assert code == 400, (code, out)
    assert out["ok"] is False and out["code"], out
    assert out["qubit_counterpart"]


# ------------------------------------------------------------- files shapes
def test_fs_read_routes_backed_by_files(tmp_path, monkeypatch):
    """FS reads list/tree/stat/read/data-url the real project folder."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    (root / "notes.md").write_text("hello files\n")
    (root / "sub").mkdir(exist_ok=True)
    (root / "sub" / "deep.txt").write_text("deep\n")
    _project(cfg, root)

    code, out = HC.dispatch(cfg, "GET", "/api/fs/list", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    names = {e["name"] for e in out["entries"]}
    assert "notes.md" in names and "sub" in names, out
    row = [e for e in out["entries"] if e["name"] == "notes.md"][0]
    assert row["isDirectory"] is False, row
    assert row["path"] == "notes.md", row

    code, out = HC.dispatch(cfg, "GET", "/api/fs/tree", {"depth": "2"}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert any(e["name"] == "deep.txt" for e in out["entries"]), out

    code, out = HC.dispatch(cfg, "GET", "/api/fs/stat", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["stat"]["state"] in ("OK", "EMPTY"), out["stat"]

    fp = str(root / "notes.md")
    code, out = HC.dispatch(cfg, "GET", "/api/fs/read-text", {"path": fp}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    for key in ("binary", "byteSize", "language", "mimeType", "path", "text",
                "truncated"):
        assert key in out, (key, out)
    assert out["binary"] is False
    assert out["text"].strip() == "hello files"
    assert out["language"] == "markdown"
    assert out["mimeType"] == "text/markdown"

    code, out = HC.dispatch(cfg, "GET", "/api/fs/read-data-url",
                            {"path": fp}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["dataUrl"].startswith("data:text/markdown;base64,"), out
    decoded = base64.b64decode(out["dataUrl"].split(",", 1)[1]).decode()
    assert decoded.strip() == "hello files"


def test_fs_binary_read_is_typed_not_fabricated(tmp_path, monkeypatch):
    """A binary file reports binary without leaking bytes (never a 500)."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    (root / "blob.bin").write_bytes(b"\x00\x01\x02\xff")
    _project(cfg, root)

    code, out = HC.dispatch(cfg, "GET", "/api/fs/read-text",
                            {"path": str(root / "blob.bin")}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["binary"] is True and out["text"] == "", out


def test_fs_git_root_and_default_cwd(tmp_path, monkeypatch):
    """git-root discovers the repo; default-cwd is the active project."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)

    code, out = HC.dispatch(cfg, "GET", "/api/fs/git-root",
                            {"path": str(root)}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["root"] == str(root), out

    code, out = HC.dispatch(cfg, "GET", "/api/fs/default-cwd", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["cwd"] == str(root), out
    assert out["branch"] in ("master", "main"), out


# ------------------------------------------------------------ audio shapes
def test_audio_read_routes_backed_by_settings(tmp_path, monkeypatch):
    """Audio reads expose the real mic/output status and device inventory."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/audio/status", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["schema"] == "qubit-runtime-audio/1"
    for key in ("mic", "output"):
        assert key in out and isinstance(out[key], dict), (key, out)
        assert "state" in out[key] and "detail" in out[key], out[key]

    code, out = HC.dispatch(cfg, "GET", "/api/audio/devices", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert isinstance(out["inputs"], list), out
    assert isinstance(out["outputs"], list), out
    for row in out["inputs"] + out["outputs"]:
        for key in ("stable_id", "name", "backend", "is_default"):
            assert key in row, (key, row)


# ----------------------------------------------------------- no shadowing
def test_preexisting_routes_not_shadowed(tmp_path, monkeypatch):
    """The audit's pre-existing routes still resolve to their handlers."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    expected = {
        "/api/status": "h_status",
        "/api/sessions": "h_sessions_list",
        "/api/ops": "h_ops_catalog",
        "/api/tools/toolsets": "h_toolsets",
        "/api/files": "h_files",
        "/api/files/read": "h_files_read",
    }
    for path, handler in expected.items():
        route, _ = HC.match("GET", path)
        assert route is not None, path
        assert route["handler"] == handler, (path, route["handler"])

    for path in ("/api/status", "/api/sessions", "/api/ops",
                 "/api/tools/toolsets", "/api/files"):
        code, out = HC.dispatch(cfg, "GET", path, {}, {})
        assert code == 200, (path, code, str(out)[:200])
        # some pre-existing reads are bare arrays (toolsets); others enveloped
        assert (isinstance(out, list) or out.get("ok") is True), (path, out)
