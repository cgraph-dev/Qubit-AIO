"""The workbench git/terminal/workspace families, through the REAL listener.

``test_workbench_command_families.py`` exercises the handlers; this file
exercises the seam around them, which is where the ported page actually talks to
Qubit:

* ``/wb/api/<sub>`` REACHES the family — the same dispatch table the served page
  hits, not a direct handler call. A path that answers 501 or is shadowed by an
  older exact-name row is the failure this asserts away.
* A path outside every registered workspace root is refused by the full stack
  with a typed code and a ``REFUSED`` state, never a 200 and never a 500.
* ``terminal/output`` is classified as a STREAM (the pane opens an
  ``EventSource``), so the listener serves SSE instead of a JSON 501.
* The blocking families are classified as blocking, so the listener runs them
  off its request thread.

The repo fixture is real: a git repository in a tmp dir registered through the
canonical ``projects.json`` the CLI writes.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from voicebridge import workbench_ui_routes as R


class Cfg:
    def __init__(self, state: Path) -> None:
        self._state = state

    def state_dir(self) -> Path:
        self._state.mkdir(parents=True, exist_ok=True)
        return self._state

    def get(self, *path: str, default=None):
        return default

    def section(self, name: str) -> dict:
        return {}


def _git(cwd: Path, *args: str) -> None:
    import os

    env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, env=env)


@pytest.fixture()
def wired(tmp_path: Path):
    state = tmp_path / "state"
    repo = tmp_path / "ws" / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    _git(repo, "add", "--", "a.txt")
    _git(repo, "commit", "-q", "-m", "initial")
    state.mkdir(parents=True, exist_ok=True)
    (state / "projects.json").write_text(json.dumps({
        "schema": "qubit-projects/1",
        "active_slug": "ws",
        "projects": {"ws": {
            "slug": "ws", "name": "Workspace", "folders": [str(tmp_path / "ws")],
            "primary": str(tmp_path / "ws"), "archived": False,
            "created_at": 1.0, "updated_at": 1.0, "updated_by": "test"}},
    }), encoding="utf-8")
    return Cfg(state), repo, tmp_path


def call(sub: str, cfg, method: str = "GET", q=None, b=None):
    return R.h_wb_gateway(cfg, dict(q or {}), dict(b or {}),
                          {"sub": sub, "method": method})


@pytest.mark.parametrize("sub,method,body", [
    ("git/status", "GET", {}),
    ("git/branches", "GET", {}),
    ("git/log", "GET", {}),
    ("git/diff", "GET", {}),
    ("git/worktrees", "GET", {}),
    ("worktree/list", "GET", {}),
    ("worktrees", "GET", {}),
    ("git/review/rev-parse", "GET", {}),
    ("workspaces", "GET", {}),
])
def test_git_and_workspace_reads_reach_the_family(wired, sub, method, body):
    """A read through the real dispatch answers 200 with real payload keys."""
    cfg, repo, _tmp = wired
    q = {"path": str(repo)} if sub.startswith(("git/", "worktree")) else {}
    status, payload = call(sub, cfg, method, q, body)
    assert status == 200, (sub, payload)
    assert payload.get("ok") is True, (sub, payload)
    assert payload.get("code") in (None, ""), (sub, payload)


@pytest.mark.parametrize("sub,method,body", [
    ("terminal/start", "POST", {"session_id": "r1"}),
    ("terminal/resize", "POST", {"session_id": "r1", "cols": 90, "rows": 20}),
    ("terminal/close", "POST", {"session_id": "r1"}),
])
def test_terminal_paths_reach_the_agent_family_and_never_a_pty(wired, sub,
                                                               method, body):
    """``terminal/*`` is answered by the agent terminal, not the old 403 row."""
    cfg, _repo, _tmp = wired
    status, payload = call(sub, cfg, method, {}, body)
    assert status == 200, (sub, payload)
    assert payload["backend"] == "agent"
    assert payload["shell"] is False
    assert payload["session_id"] == "r1"


def test_an_unported_path_is_still_a_typed_refusal(wired):
    cfg, _repo, _tmp = wired
    status, payload = call("wiki/page", cfg)
    assert status == 501 and payload["code"] == "WB_NOT_IN_PORT_SET"


def test_the_kanban_board_is_served_from_the_native_store(wired):
    """The twelve kanban paths stopped being GAP: this one answers real data.

    It used to be the "unported path" witness for the refusal above; the board
    now resolves ``voicebridge.kanban`` (``workbench_ui/kanban_family.py``), so
    an EMPTY store answers an empty board rather than a 501.
    """
    cfg, _repo, _tmp = wired
    status, payload = call("kanban/board", cfg)
    assert status == 200, payload
    assert payload["ok"] is True
    assert [c["name"] for c in payload["columns"]] == \
        ["open", "claimed", "blocked", "done"]


def test_outside_every_root_is_refused_through_the_full_stack(wired, tmp_path):
    cfg, _repo, tmp = wired
    outside = tmp / "elsewhere"
    outside.mkdir()
    _git(outside, "init", "-q")
    for sub in ("git/status", "git/log", "git/diff", "git/branches",
                "worktrees"):
        status, payload = call(sub, cfg, "GET", {"path": str(outside)})
        assert status == 403, (sub, payload)
        assert payload["code"] == "WB_WORKSPACE_OUTSIDE", (sub, payload)
        assert payload["state"] == "REFUSED"
    # ...and the POST lanes refuse it before staging anything.
    status, payload = call("git/stage", cfg, "POST",
                           {}, {"path": str(outside), "paths": ["a.txt"]})
    assert status == 403 and payload["code"] == "WB_WORKSPACE_OUTSIDE"


def test_a_family_mutation_stages_on_the_shared_journal(wired):
    cfg, repo, _tmp = wired
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    status, payload = call("git/stage", cfg, "POST", {},
                           {"path": str(repo), "paths": ["b.txt"]})
    assert status == 409, payload
    assert payload["code"] == "CONFIRM_REQUIRED"
    assert payload["confirmation_id"]
    assert payload["endpoint"] == "git/stage"
    # Nothing staged: the contract is stage-then-confirm.
    out = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                         capture_output=True, text=True).stdout
    assert out.strip() == "?? b.txt"


def test_terminal_output_is_classified_as_a_stream(wired):
    cfg, _repo, _tmp = wired
    assert R.stream_kind("terminal/output") == "terminal"
    # And its frames come from the terminal family, not a JSON answer.
    frames = R.stream_frames("terminal/output", cfg, {"session_id": "nope"})
    first = next(iter(frames))
    assert "terminal_error" in first


def test_blocking_families_are_classified_for_offloading():
    for sub in ("git/status", "git/diff", "git/stage", "worktree/list",
                "worktrees"):
        assert R.is_blocking(sub) is True, sub
    for sub in ("sessions", "terminal/start", "workspaces", "file/read"):
        assert R.is_blocking(sub) is False, sub
