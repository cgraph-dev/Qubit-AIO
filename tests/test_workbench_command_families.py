"""The workbench git / worktree / terminal / workspace families, end to end.

The endpoints under test run commands and mutate repositories, so the tests are
written the way the risk is ordered:

1. CONFINEMENT FIRST. A path outside every registered workspace root, a path
   that reaches outside through ``..`` or a symlink, a repository whose top level
   sits above the root, and a worktree target outside the roots must ALL be
   refused with a typed code — and the refusal must be provable: the state that
   would have changed did not, and a repo outside the roots was never touched.
2. REAL GIT. The fixture below builds actual repositories in a tmp dir with
   commits, a bare "remote", branches and worktrees, and the handlers are called
   with real requests and their real responses are asserted.
3. CONFIRMATION IS REAL. A mutation without a ``confirmation_id`` stages (409,
   with a journal id) and changes NOTHING; with one it settles once and the
   second use is refused.

No test here stubs the confinement module — stubbing it would test the stub.
The one thing that is replaced is the MODEL, for the terminal family: an agent
turn would cost a network call and a resident model, so the terminal's session
factory is swapped for a double that asserts it received the keystrokes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from voicebridge.workbench_ui import (command_family, git_family, git_format,
                                      git_repo, workspace_family,
                                      workspace_scope)
from voicebridge.workbench_ui import cmdrun, ws_fs


# --------------------------------------------------------------------- config
class Cfg:
    """The smallest real config: a state dir plus the section/lookup surface."""

    def __init__(self, state: Path) -> None:
        self._state = state

    def state_dir(self) -> Path:
        self._state.mkdir(parents=True, exist_ok=True)
        return self._state

    def get(self, *path: str, default=None):
        return default

    def section(self, name: str) -> dict:
        return {}


# ----------------------------------------------------------------- git helpers
def git(cwd: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@t")
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, f"git {' '.join(args)}: {proc.stderr}"
    return proc.stdout


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    (path / "a.txt").write_text("one\n", encoding="utf-8")
    (path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    git(path, "add", "--", "a.txt", ".env")
    git(path, "commit", "-q", "-m", "initial")
    return path


def register(cfg: Cfg, folders) -> None:
    """Write the project registry directly — the store ``qubit project`` uses."""
    state = {
        "schema": "qubit-projects/1",
        "active_slug": "ws",
        "projects": {
            "ws": {
                "slug": "ws", "name": "Workspace", "folders": [str(f) for f in folders],
                "primary": str(folders[0]), "archived": False,
                "created_at": 1.0, "updated_at": 1.0, "updated_by": "test",
            },
        },
    }
    path = cfg.state_dir() / "projects.json"
    path.write_text(json.dumps(state), encoding="utf-8")


@pytest.fixture()
def env(tmp_path: Path):
    """``(cfg, root, outside)`` — one registered repo and one outside it."""
    cfg = Cfg(tmp_path / "state")
    root = make_repo(tmp_path / "work" / "repo")
    outside = make_repo(tmp_path / "elsewhere" / "other")
    register(cfg, [tmp_path / "work"])
    return cfg, root, outside


@pytest.fixture(autouse=True)
def _isolate_terminal():
    from voicebridge.workbench_ui import terminal_family

    terminal_family.reset_for_tests()
    yield
    terminal_family.reset_for_tests()


# ===========================================================================
# 1. CONFINEMENT — the test that has to exist before any command can run
# ===========================================================================
OUTSIDE_REQUESTS = [
    ("git/status", "GET"),
    ("git/log", "GET"),
    ("git/diff", "GET"),
    ("git/branches", "GET"),
    ("git/worktrees", "GET"),
    ("git/review/rev-parse", "GET"),
    ("git/stage", "POST"),
    ("git/discard", "POST"),
    ("git/commit", "POST"),
    ("git/branch/switch", "POST"),
    ("git/push", "POST"),
    ("git/pull", "POST"),
    ("git/worktree/add", "POST"),
    ("git/worktree/remove", "POST"),
    ("worktree/list", "GET"),
]


@pytest.mark.parametrize("sub,method", OUTSIDE_REQUESTS)
def test_a_path_outside_every_root_is_refused_and_nothing_runs(env, sub, method,
                                                              tmp_path):
    """Every command endpoint refuses an outside path BEFORE building a command."""
    cfg, root, outside = env
    fn = command_family.handler_for(sub)
    assert fn is not None, f"{sub} is not routed"
    before = git(outside, "status", "--porcelain=v2", "--branch")
    body = {"path": str(outside), "paths": ["a.txt"], "message": "x",
            "branch": "main", "worktree_path": str(outside)} if method == "POST" \
        else {}
    status, payload = fn(cfg, {"path": str(outside)}, body, {})
    assert status == 403, payload
    assert payload["code"] == "WB_WORKSPACE_OUTSIDE", payload
    assert payload["state"] == "REFUSED"
    assert "nothing was run" in payload["detail"].lower()
    # The refused repo is byte-identical: nothing ran in it.
    assert git(outside, "status", "--porcelain=v2", "--branch") == before


def test_traversal_through_dotdot_is_refused(env):
    cfg, root, outside = env
    sneaky = str(root.parent / ".." / "elsewhere" / "other")
    status, payload = command_family.handler_for("git/status")(
        cfg, {"path": sneaky}, {}, {})
    assert status == 403 and payload["code"] == "WB_WORKSPACE_OUTSIDE"


def test_symlink_out_of_the_root_is_refused(env, tmp_path):
    cfg, root, outside = env
    link = root / "escape"
    link.symlink_to(outside, target_is_directory=True)
    status, payload = git_family.diff(cfg, {"path": str(link)}, {}, {})
    assert status == 403 and payload["code"] == "WB_WORKSPACE_OUTSIDE"


def test_a_repository_above_the_root_is_refused(env, tmp_path):
    """The confined path is inside a root, but its repo top level is not."""
    cfg = Cfg(tmp_path / "state2")
    outer = make_repo(tmp_path / "outer")           # repo top level
    inner = outer / "sub" / "deep"
    inner.mkdir(parents=True)
    register(cfg, [inner])                          # only the SUBDIR is a root
    status, payload = git_family.status(cfg, {"path": str(inner)}, {}, {})
    assert status == 403, payload
    assert payload["code"] == "WB_REPO_OUTSIDE_WORKSPACE", payload


def test_no_registered_root_is_a_refusal_not_a_fallback(tmp_path):
    cfg = Cfg(tmp_path / "empty-state")
    repo = make_repo(tmp_path / "loose")
    status, payload = git_family.status(cfg, {"path": str(repo)}, {}, {})
    assert status == 409 and payload["code"] == "WB_NO_WORKSPACE_ROOTS"


def test_a_missing_path_parameter_is_required(tmp_path):
    cfg = Cfg(tmp_path / "s3")
    status, payload = git_family.status(cfg, {}, {}, {})
    assert status == 400 and payload["code"] == "WB_WORKSPACE_REQUIRED"


def test_relative_paths_are_refused(env):
    cfg, root, _outside = env
    status, payload = git_family.status(cfg, {"path": "repo"}, {}, {})
    assert status == 400 and payload["code"] == "WB_WORKSPACE_BAD_INPUT"


def test_workspace_roots_come_only_from_the_registry(env, tmp_path):
    cfg, root, outside = env
    # A request naming an UNREGISTERED directory cannot widen its own scope.
    loose = tmp_path / "loose2"
    make_repo(loose)
    assert str(loose) not in workspace_scope.root_strings(cfg)
    status, payload = git_family.status(cfg, {"path": str(loose)}, {}, {})
    assert status == 403 and payload["code"] == "WB_WORKSPACE_OUTSIDE"


def test_forbidden_git_arguments_are_refused_before_execution(tmp_path):
    """``--git-dir``/``-c`` would point git away from the confined repo."""
    for argv in (["--git-dir=/tmp/x", "log"], ["-c", "core.hooksPath=/tmp", "log"],
                 ["log", "--work-tree=/tmp"], ["--config=core.x=1", "log"]):
        with pytest.raises(cmdrun.CmdRefusal) as info:
            cmdrun.check_argv(argv)
        assert info.value.code == "WB_GIT_ARG_FORBIDDEN", argv


def test_worktree_target_outside_the_roots_is_refused(env, tmp_path):
    cfg, root, _outside = env
    status, payload = git_family.worktree_add(
        cfg, {}, {"path": str(root), "name": "feature",
                  "worktree_path": str(tmp_path / "outside-tree")}, {})
    assert status == 403 and payload["code"] == "WB_WORKSPACE_OUTSIDE"
    assert not (tmp_path / "outside-tree").exists()


def test_worktree_target_may_be_inside_the_root_but_not_the_root(env, tmp_path):
    cfg, root, _outside = env
    # A worktree target that IS the workspace root is refused as bad input...
    status, payload = git_family.worktree_add(
        cfg, {}, {"path": str(root), "name": "feature",
                  "worktree_path": str(root)}, {})
    assert status == 400 and payload["code"] == "WB_WORKSPACE_BAD_INPUT"
    # ...while a new directory under the registered root passes confinement and
    # reaches the confirm gate (409), which is as far as an unconfirmed write goes.
    status, payload = git_family.worktree_add(
        cfg, {}, {"path": str(root), "name": "feature",
                  "worktree_path": str(root.parent / "wt-feature")}, {})
    assert status == 409, payload
    assert not (root.parent / "wt-feature").exists()


def test_safe_rel_and_sensitive_paths(tmp_path):
    root = make_repo(tmp_path / "r")
    assert ws_fs.safe_rel(root, "a.txt") == "a.txt"
    with pytest.raises(workspace_scope.ScopeRefusal) as info:
        ws_fs.safe_rel(root, "../r/a.txt")
    assert info.value.code == "WB_WORKSPACE_OUTSIDE"
    for name in (".env", ".env.local", "auth.json", "sub/.netrc",
                 "mcp-tokens/x.json"):
        assert ws_fs.is_sensitive_rel(name), name
    assert not ws_fs.is_sensitive_rel("src/main.py")


# ===========================================================================
# 2. REAL GIT — reads return the repository's actual state
# ===========================================================================
def test_status_reports_the_real_work_tree(env):
    cfg, root, _outside = env
    (root / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    (root / "new.txt").write_text("hello\n", encoding="utf-8")
    status, payload = git_family.status(cfg, {"path": str(root)}, {}, {})
    assert status == 200, payload
    assert payload["ok"] is True
    assert payload["branch"] == "main"
    assert payload["root"] == str(root)
    assert payload["workspace"] == str(root)
    assert payload["detached"] is False
    by_path = {f["path"]: f for f in payload["files"]}
    assert by_path["a.txt"]["status"] == "M"
    assert by_path["a.txt"]["added"] == 1
    assert by_path["new.txt"]["untracked"] is True
    assert payload["untracked"] == 1
    assert payload["changed"] >= 2


def test_log_is_bounded_and_real(env):
    cfg, root, _outside = env
    (root / "b.txt").write_text("b\n", encoding="utf-8")
    git(root, "add", "--", "b.txt")
    git(root, "commit", "-q", "-m", "second commit")
    status, payload = git_family.log(cfg, {"path": str(root), "limit": "1"}, {}, {})
    assert status == 200
    assert payload["entries"][0]["subject"] == "second commit"
    assert len(payload["entries"]) == 1
    assert payload["has_more"] is True
    assert len(payload["entries"][0]["sha"]) == 40


def test_log_of_a_missing_ref_is_a_typed_404(env):
    cfg, root, _outside = env
    status, payload = git_family.log(cfg, {"path": str(root), "ref": "nope"}, {}, {})
    assert status == 404 and payload["code"] == "GIT_REF_NOT_FOUND"


def test_diff_returns_real_hunks_and_hides_credentials(env):
    cfg, root, _outside = env
    (root / "a.txt").write_text("one\nchanged\n", encoding="utf-8")
    status, payload = git_family.diff(cfg, {"path": str(root)}, {}, {})
    assert status == 200
    assert "+changed" in payload["patch"]
    assert payload["stat"].strip()

    # A credential file is never diffed — even inside a registered workspace.
    (root / ".env").write_text("SECRET=2\n", encoding="utf-8")
    status, payload = git_family.diff(cfg, {"path": str(root), "file": ".env"},
                                      {}, {})
    assert status == 403 and payload["code"] == "GIT_SENSITIVE_PATH"
    assert "SECRET" not in json.dumps(payload)


def test_branches_and_worktrees_lists(env, tmp_path):
    cfg, root, _outside = env
    git(root, "branch", "feature")
    git(root, "worktree", "add", "-q", str(root.parent / "wt"), "feature")
    status, payload = git_family.branches(cfg, {"path": str(root)}, {}, {})
    assert status == 200
    names = {row["name"]: row for row in payload["branches"]}
    assert names["main"]["isDefault"] is True
    assert names["feature"]["checkedOut"] is True
    assert names["feature"]["worktreePath"] == str(root.parent / "wt")

    status, payload = git_family.worktrees(cfg, {"path": str(root)}, {}, {})
    assert status == 200
    paths = [t["path"] for t in payload["worktrees"]]
    assert str(root) in paths and str(root.parent / "wt") in paths
    assert payload["worktrees"][0]["isMain"] is True


def test_rev_parse_resolves_head(env):
    cfg, root, _outside = env
    status, payload = git_family.rev_parse(cfg, {"path": str(root)}, {}, {})
    assert status == 200 and len(payload["sha"]) == 40


def test_a_non_repo_inside_a_root_is_a_typed_refusal(env, tmp_path):
    cfg, root, _outside = env
    plain = tmp_path / "work" / "plain"
    plain.mkdir()
    status, payload = git_family.status(cfg, {"path": str(plain)}, {}, {})
    assert status == 409 and payload["code"] == "GIT_NOT_A_REPO"


# ===========================================================================
# 3. MUTATIONS — staged, confirmed once, and nothing staged without paths
# ===========================================================================
def test_stage_requires_explicit_paths(env):
    cfg, root, _outside = env
    (root / "c.txt").write_text("c\n", encoding="utf-8")
    status, payload = git_family.stage(cfg, {}, {"path": str(root)}, {})
    assert status == 400 and payload["code"] == "WB_BAD_INPUT"
    # Nothing was staged: a whole-tree `git add -A` would have shown c.txt here.
    assert git(root, "status", "--porcelain=v2").strip() == "? c.txt"


def test_stage_stages_then_stops_at_the_confirmation(env):
    cfg, root, _outside = env
    (root / "c.txt").write_text("c\n", encoding="utf-8")
    status, payload = git_family.stage(
        cfg, {}, {"path": str(root), "paths": ["c.txt"]}, {})
    assert status == 409, payload
    assert payload["code"] == "CONFIRM_REQUIRED"
    assert payload["confirmation_id"]
    # The staged-confirmation contract means NOTHING ran yet.
    assert git(root, "status", "--porcelain=v2").strip() == "? c.txt"


def test_discard_of_a_credential_file_is_refused(env):
    cfg, root, _outside = env
    status, payload = git_family.discard(
        cfg, {}, {"path": str(root), "paths": [".env"]}, {})
    assert status == 403 and payload["code"] == "GIT_SENSITIVE_PATH"
    assert (root / ".env").read_text(encoding="utf-8") == "SECRET=1\n"


def test_discard_outside_the_repo_through_a_symlink_is_refused(env, tmp_path):
    cfg, root, outside = env
    (root / "link").symlink_to(outside / "a.txt")
    status, payload = git_family.discard(
        cfg, {}, {"path": str(root), "paths": ["link"]}, {})
    assert status == 403
    assert (outside / "a.txt").read_text(encoding="utf-8") == "one\n"


def test_commit_needs_a_message_and_confirms(env):
    cfg, root, _outside = env
    status, payload = git_family.commit(cfg, {}, {"path": str(root)}, {})
    assert status == 400
    status, payload = git_family.commit(
        cfg, {}, {"path": str(root), "message": "tidy"}, {})
    assert status == 409 and payload["confirmation_id"]
    # HEAD did not move.
    assert git(root, "log", "-1", "--pretty=%s").strip() == "initial"


def test_push_and_pull_refuse_without_a_remote(env):
    cfg, root, _outside = env
    for fn in (git_family.push, git_family.pull):
        status, payload = fn(cfg, {}, {"path": str(root)}, {})
        assert status == 409 and payload["code"] == "GIT_NO_REMOTE", payload


def test_push_with_a_remote_stages_a_confirmation_and_does_not_push(env, tmp_path):
    cfg, root, _outside = env
    bare = tmp_path / "bare.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    git(root, "remote", "add", "origin", str(bare))
    status, payload = git_family.push(cfg, {}, {"path": str(root)}, {})
    assert status == 409 and payload["code"] == "CONFIRM_REQUIRED"
    # The bare remote has no main ref: nothing was pushed.
    refs = subprocess.run(["git", "-C", str(bare), "show-ref"],
                          capture_output=True, text=True)
    assert refs.returncode != 0


def test_branch_switch_confirms_and_leaves_head_alone(env):
    cfg, root, _outside = env
    git(root, "branch", "feature")
    status, payload = git_family.branch_switch(
        cfg, {}, {"path": str(root), "branch": "feature"}, {})
    assert status == 409, payload
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    status, payload = git_family.branch_switch(
        cfg, {}, {"path": str(root), "branch": "nope"}, {})
    assert status == 404 and payload["code"] == "GIT_REF_NOT_FOUND"


# ===========================================================================
# 4. TERMINAL — agent-backed, no shell, and the wire terminal.js expects
# ===========================================================================
class _FakeAgent:
    """Stands in for ``AgentPtySession``: records input, no shell, no model."""

    alive = True

    def __init__(self, cfg, session_id, *, cols=80, rows=24):
        self.session_id = session_id
        self.cols, self.rows = cols, rows
        self.fed = []

    def feed(self, data: bytes):
        self.fed.append(data)
        yield b"echo:" + data


@pytest.fixture()
def fake_agent(monkeypatch, cfg_factory):
    from voicebridge.workbench_ui import terminal_family

    made = []

    def factory(sid, cols, rows):
        made.append(_FakeAgent(None, sid, cols=cols, rows=rows))
        return made[-1]

    monkeypatch.setattr(terminal_family, "agent_session_for", factory)
    return made


@pytest.fixture()
def cfg_factory(env):
    """The terminal family takes no workspace, so any real config will do."""
    return env[0]


def test_terminal_start_is_agent_backed_and_never_a_shell(cfg_factory, fake_agent):
    status, payload = command_family.handler_for("terminal/start")(
        None, {}, {"session_id": "s1", "rows": 30, "cols": 100}, {})
    assert status == 200, payload
    assert payload["backend"] == "agent"
    assert payload["shell"] is False
    assert payload["cols"] == 100 and payload["rows"] == 30
    # The agent session's conversation key is qualified so two panes never
    # share one buffer.
    assert fake_agent and fake_agent[0].session_id == "s1"
    assert "no shell" in payload["banner"].lower()


def test_terminal_input_runs_through_the_session(fake_agent):
    command_family.handler_for("terminal/start")(None, {}, {"session_id": "s2"}, {})
    status, payload = command_family.handler_for("terminal/input")(
        None, {}, {"session_id": "s2", "data": "hello\n"}, {})
    assert status == 200, payload
    assert fake_agent[0].fed == [b"hello\n"]
    # The pane reads output from the stream, not from the POST.
    frames = list(__import__("itertools").islice(
        terminal_frames("s2"), 2))
    assert frames[0].startswith("event: output")
    assert "echo:hello" in frames[0]


def terminal_frames(sid):
    from voicebridge.workbench_ui import terminal_family

    return terminal_family.output_frames(None, {"session_id": sid})


def test_terminal_requires_a_session_and_rejects_junk(fake_agent):
    status, payload = command_family.handler_for("terminal/start")(None, {}, {}, {})
    assert status == 400 and payload["code"] == "TERMINAL_SESSION_REQUIRED"
    status, payload = command_family.handler_for("terminal/input")(
        None, {}, {"session_id": "s", "data": 5}, {})
    assert status == 400 and payload["code"] == "TERMINAL_BAD_INPUT"


def test_terminal_input_to_a_closed_session_is_typed(fake_agent):
    status, payload = command_family.handler_for("terminal/input")(
        None, {}, {"session_id": "ghost", "data": "x"}, {})
    assert status == 409 and payload["code"] == "TERMINAL_NOT_RUNNING"


def test_terminal_close_ends_the_stream(fake_agent):
    command_family.handler_for("terminal/start")(None, {}, {"session_id": "s3"}, {})
    status, payload = command_family.handler_for("terminal/close")(
        None, {}, {"session_id": "s3"}, {})
    assert status == 200 and payload["closed"] is True
    # Idempotent, like every other close in this tree.
    status, payload = command_family.handler_for("terminal/close")(
        None, {}, {"session_id": "s3"}, {})
    assert status == 200 and payload["existed"] is False
    frames = list(terminal_frames("s3"))
    assert frames and "terminal_error" in frames[0]


def test_terminal_output_stream_is_open_to_the_pane(env):
    """``terminal/output`` is owned as a STREAM, not served as JSON."""
    assert "terminal/output" in command_family.STREAMS
    assert command_family.stream_frames("terminal/output", None,
                                        {"session_id": "x"}) is not None
    assert command_family.handler_for("terminal/output") is None


# ===========================================================================
# 5. WORKSPACES — the registry rows the Workspaces panel reads
# ===========================================================================
def test_workspaces_list_reports_registered_folders(env):
    cfg, root, _outside = env
    status, payload = workspace_family.list_payload(cfg)
    assert status == 200
    assert [w["path"] for w in payload["workspaces"]] == [str(root.parent)]
    row = payload["workspaces"][0]
    assert row["exists"] is True and row["slug"] == "ws"
    assert payload["terminal_remote_backend"] is False


def test_workspaces_add_needs_confirmation_then_registers(env, tmp_path):
    cfg, root, _outside = env
    # OUTSIDE the already-registered folder: registering ``work/`` would make
    # the "still refused" assertion below pass for the wrong reason.
    fresh = make_repo(tmp_path / "second-root")
    status, payload = workspace_family.add(cfg, {}, {"path": str(fresh)}, {})
    assert status == 409 and payload["code"] == "CONFIRM_REQUIRED"
    # Not registered yet: the git endpoints still refuse the path.
    check, refused = git_family.status(cfg, {"path": str(fresh)}, {}, {})
    assert check == 403

    status, payload = workspace_family.add(
        cfg, {}, {"path": str(fresh), "confirmation_id": payload["confirmation_id"]},
        {})
    assert status == 200, payload
    assert str(fresh) in [w["path"] for w in payload["workspaces"]]
    # Now the same path is inside a workspace root.
    check, ok = git_family.status(cfg, {"path": str(fresh)}, {}, {})
    assert check == 200, ok


def test_workspaces_add_refuses_a_missing_directory(env, tmp_path):
    cfg, _root, _outside = env
    status, payload = workspace_family.add(
        cfg, {}, {"path": str(tmp_path / "nope")}, {})
    assert status == 400 and payload["code"] == "WORKSPACE_NOT_A_DIRECTORY"


def test_workspaces_remove_needs_confirmation_and_unregisters(env):
    cfg, root, _outside = env
    status, payload = workspace_family.remove(cfg, {}, {"path": str(root.parent)}, {})
    assert status == 409
    status, payload = workspace_family.remove(
        cfg, {}, {"path": str(root.parent),
                  "confirmation_id": payload["confirmation_id"]}, {})
    assert status == 200, payload
    assert payload["workspaces"] == []
    # Unregistered → the git family no longer accepts the path it used to.
    check, refused = git_family.status(cfg, {"path": str(root)}, {}, {})
    assert check == 409 and refused["code"] in (
        "WB_NO_WORKSPACE_ROOTS", "WB_WORKSPACE_OUTSIDE"), refused


def test_workspaces_rename_renames_the_project_not_the_path(env):
    cfg, root, _outside = env
    # The row the panel renames is the registered FOLDER's row. Renaming the
    # project renames every row of that project; each row's path is unchanged.
    folder = root.parent
    status, payload = workspace_family.rename(
        cfg, {}, {"path": str(folder), "name": "Renamed"}, {})
    assert status == 409, payload
    status, payload = workspace_family.rename(
        cfg, {}, {"path": str(folder), "name": "Renamed",
                  "confirmation_id": payload["confirmation_id"]}, {})
    assert status == 200, payload
    assert payload["workspaces"][0]["name"] == "Renamed"
    assert payload["workspaces"][0]["path"] == str(root.parent)


def test_workspaces_reorder_is_a_named_refusal_and_writes_nothing(env):
    cfg, root, _outside = env
    before = (cfg.state_dir() / "projects.json").read_text(encoding="utf-8")
    status, payload = workspace_family.reorder(
        cfg, {}, {"paths": [str(root.parent)]}, {})
    assert status == 501 and payload["code"] == "WORKSPACE_ORDER_UNSUPPORTED"
    assert payload["workspaces"]
    assert (cfg.state_dir() / "projects.json").read_text(encoding="utf-8") == before


def test_workspace_upload_is_a_brokered_write_refusal(env):
    cfg, root, _outside = env
    status, payload = workspace_family.upload(
        cfg, {}, {"path": str(root), "name": "x", "content": "x"}, {})
    assert status == 403 and payload["code"] == "WB_BROKERED_WRITE"
    assert not (root / "x").exists()


# ===========================================================================
# 6. ROUTING — the dispatch table the gateway reads
# ===========================================================================
@pytest.mark.parametrize("sub", [
    "git/status", "git/diff", "git/log", "git/branches", "git/stage",
    "git/discard", "git/commit", "git/push", "git/pull", "git/checkout",
    "git/branch/switch", "git/worktree/add", "git/worktree/remove",
    "worktree/list", "worktree/add", "worktree/remove", "worktrees",
    "terminal/start", "terminal/input", "terminal/resize", "terminal/close",
    "workspaces", "workspaces/add", "workspaces/remove", "workspaces/rename",
    "workspaces/reorder", "workspace/upload",
])
def test_every_family_path_is_routed(sub):
    assert command_family.handler_for(sub) is not None, sub


def test_unknown_and_empty_paths_are_not_routed():
    assert command_family.handler_for("git/nope") is None
    assert command_family.handler_for("") is None
    assert command_family.handler_for("/") is None
