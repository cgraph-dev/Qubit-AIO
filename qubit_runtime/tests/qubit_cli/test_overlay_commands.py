"""CLI parity for the ported overlay surfaces.

Per command: registry parse (resolve_command + subcommands), slash dispatch
(``_handle_<name>_command`` on the mixin, the ``_slash_handler`` fallback
convention), and the backend call (monkeypatched ``qubit_cli.overlay``
function receives the parsed args; destructive ops stay dry-run without
``--confirm``).
"""

import pytest

from qubit_cli.cli_commands_mixin import CLICommandsMixin
from qubit_cli.commands import SUBCOMMANDS, resolve_command
from qubit_cli.overlay import OverlayUnavailable


class _Host(CLICommandsMixin):
    pass


@pytest.fixture()
def host():
    return _Host()


def _out(capsys):
    return capsys.readouterr().out


# ---- registry ---------------------------------------------------------------

EXPECTED = {
    "board": ("open", "tabs", "widgets", "roles", "log"),
    "harness": ("list", "submit", "sessions", "files"),
    "viking": ("status", "tree", "search", "add", "resource"),
    "bot": ("ideas", "goals", "launch", "pause", "resume", "card"),
    "webchat": ("list", "show", "messages", "delete"),
    "memory-backend": ("status", "provider", "probe"),
    "canvas": ("list", "get", "add", "remove"),
    "taskflow": ("flows", "show", "pause", "resume", "trigger", "delete", "hooks"),
}

HANDLERS = {
    "board": "_handle_board_command",
    "harness": "_handle_harness_command",
    "viking": "_handle_viking_command",
    "bot": "_handle_bot_command",
    "webchat": "_handle_webchat_command",
    "memory-backend": "_handle_memory_backend_command",
    "canvas": "_handle_canvas_command",
    "taskflow": "_handle_taskflow_command",
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_registry_resolves_with_subcommands(name):
    cmd = resolve_command(name)
    assert cmd is not None and cmd.name == name
    for sub in EXPECTED[name]:
        assert sub in cmd.subcommands
    assert f"/{name}" in SUBCOMMANDS


@pytest.mark.parametrize("name,handler", sorted(HANDLERS.items()))
def test_dispatch_handler_exists_on_mixin(name, handler):
    # cli.HermesCLI._slash_handler falls back to _handle_<name>_command;
    # the handler must exist and take the raw command string.
    assert callable(getattr(_Host, handler, None)), name


def test_unavailable_backend_prints_reason_not_traceback(host, capsys, monkeypatch):
    def _boom(*a, **k):
        raise OverlayUnavailable("voicebridge.kanban is not importable here")

    monkeypatch.setattr("qubit_cli.overlay.board_snapshot", _boom)
    host._handle_board_command("/board")
    assert "backend unavailable" in _out(capsys)


# ---- /board -----------------------------------------------------------------

def test_board_open_default_calls_snapshot(host, capsys, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "qubit_cli.overlay.board_snapshot",
        lambda board: seen.update(board=board) or {
            "board": board, "columns": ["open", "done"],
            "by_column": {"open": [{"id": 1, "title": "T"}]}, "widgets": [],
            "roles": [], "tabs": []})
    host._handle_board_command("/board")
    assert seen == {"board": "main"}
    out = _out(capsys)
    assert "Board: main" in out and "open, done" in out


def test_board_tabs_widgets_roles_log(host, capsys, monkeypatch):
    monkeypatch.setattr(
        "qubit_cli.overlay.board_tabs",
        lambda board: {"tabs": [{"slug": "t", "name": "T", "widget_count": 2}]})
    monkeypatch.setattr(
        "qubit_cli.overlay.board_widgets",
        lambda board: {"widgets": [{"id": 7, "kind": "note", "title": "W"}]})
    monkeypatch.setattr(
        "qubit_cli.overlay.board_roles",
        lambda board: {"roles": [{"principal": "p", "role": "viewer"}]})
    monkeypatch.setattr(
        "qubit_cli.overlay.board_log",
        lambda board, limit: {"events": [{"at": "now", "kind": "k", "detail": "d"}]})
    host._handle_board_command("/board tabs --board aux")
    host._handle_board_command("/board widgets")
    host._handle_board_command("/board roles")
    host._handle_board_command("/board log --limit 5")
    out = _out(capsys)
    assert "Tabs on aux:" in out and "#7 [note] W" in out
    assert "p — viewer" in out and "Board events" in out


def test_board_unknown_subcommand(host, capsys):
    host._handle_board_command("/board frobnicate")
    assert "Unknown board command" in _out(capsys)


# ---- /harness ---------------------------------------------------------------

def test_harness_list_default(host, capsys, monkeypatch):
    monkeypatch.setattr(
        "qubit_cli.overlay.harness_kits",
        lambda: {"kits": [{"id": "k1", "title": "Kit", "description": "d"}]})
    host._handle_harness_command("/harness")
    assert "k1 — Kit" in _out(capsys)


def test_harness_submit_parses_and_calls(host, capsys, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "qubit_cli.overlay.harness_submit",
        lambda harness, prompt: seen.update(harness=harness, prompt=prompt) or {
            "session": "s1", "harness": harness})
    host._handle_harness_command("/harness submit coder fix the bug")
    assert seen == {"harness": "coder", "prompt": "fix the bug"}
    assert "s1" in _out(capsys)
    host._handle_harness_command("/harness submit onlyone")
    assert "Usage: /harness submit" in _out(capsys)


def test_harness_sessions_files(host, capsys, monkeypatch):
    monkeypatch.setattr(
        "qubit_cli.overlay.harness_sessions",
        lambda limit=50: {"sessions": [{"session_id": "s9", "title": "Hi"}]})
    monkeypatch.setattr(
        "qubit_cli.overlay.harness_files",
        lambda group="": {"state": "OK", "group": group or "all",
                           "files": {"a.md": "..."}})
    host._handle_harness_command("/harness sessions 10")
    host._handle_harness_command("/harness files")
    out = _out(capsys)
    assert "s9" in out and "a.md" in out


# ---- /viking ----------------------------------------------------------------

def test_viking_status_default(host, capsys, monkeypatch):
    monkeypatch.setattr(
        "qubit_cli.overlay.viking_status",
        lambda: {"engine": "qubit-local-context", "connection": "local",
                 "seeded": True, "counts": {"user": 3}})
    host._handle_viking_command("/viking")
    out = _out(capsys)
    assert "qubit-local-context" in out and "user: 3" in out


def test_viking_search_tree_add_resource(host, capsys, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "qubit_cli.overlay.viking_tree",
        lambda: {"root": "context/", "groups": [
            {"key": "user", "nodes": [{"label": "USER.md", "size": 10}]}]})
    monkeypatch.setattr(
        "qubit_cli.overlay.viking_search",
        lambda q, limit=20: seen.update(q=q, limit=limit) or {
            "hits": [{"path": "user/USER.md", "snippet": "snip"}]})
    monkeypatch.setattr(
        "qubit_cli.overlay.viking_add",
        lambda text, scope="user": seen.update(add=(text, scope)) or {"id": "m1"})
    monkeypatch.setattr(
        "qubit_cli.overlay.viking_add_resource",
        lambda uri, title="", notes="": seen.update(res=uri) or {"state": "OK"})
    host._handle_viking_command("/viking tree")
    host._handle_viking_command("/viking search cats 5")
    host._handle_viking_command("/viking add remember this")
    host._handle_viking_command("/viking resource viking://user/X.md")
    assert seen["q"] == "cats" and seen["limit"] == 5
    assert seen["add"] == ("remember this", "user")
    assert seen["res"] == "viking://user/X.md"
    out = _out(capsys)
    assert "USER.md" in out and "No hits" not in out
    host._handle_viking_command("/viking search")
    assert "Usage: /viking search" in _out(capsys)


# ---- /bot -------------------------------------------------------------------

def test_bot_ideas_default_and_launch(host, capsys, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "qubit_cli.overlay.bot_ideas",
        lambda: {"considered": 1,
                 "suggestions": [{"id": "i1", "title": "Idea"}]})
    monkeypatch.setattr(
        "qubit_cli.overlay.bot_launch",
        lambda prompt, title="": seen.update(prompt=prompt, title=title) or {
            "session": "w1"})
    host._handle_bot_command("/bot")
    host._handle_bot_command("/bot launch do things --title T")
    assert seen == {"prompt": "do things", "title": "T"}
    out = _out(capsys)
    assert "i1 — Idea" in out and "w1" in out
    host._handle_bot_command("/bot launch")
    assert "Usage: /bot launch" in _out(capsys)


def test_bot_pause_resume_card_goals(host, capsys, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "qubit_cli.overlay.bot_pause",
        lambda session: seen.update(paused=session) or {"state": "OK"})
    monkeypatch.setattr(
        "qubit_cli.overlay.bot_resume",
        lambda session: seen.update(resumed=session) or {"state": "OK"})
    monkeypatch.setattr(
        "qubit_cli.overlay.bot_card",
        lambda card: {"card": card, "session": "s", "artifact": "body"})
    monkeypatch.setattr(
        "qubit_cli.overlay.bot_goals",
        lambda: {"goals": [{"id": "g1", "title": "G"}], "jobs": []})
    host._handle_bot_command("/bot pause w1")
    host._handle_bot_command("/bot resume w1")
    host._handle_bot_command("/bot card s/plan")
    host._handle_bot_command("/bot goals")
    assert seen == {"paused": "w1", "resumed": "w1"}
    out = _out(capsys)
    assert "body" in out and "g1 — G" in out


# ---- /webchat ---------------------------------------------------------------

def test_webchat_list_show_messages(host, capsys, monkeypatch):
    monkeypatch.setattr(
        "qubit_cli.overlay.webchat_list",
        lambda limit=50: {"sessions": [{"session_id": "c1", "name": "Chat"}]})
    monkeypatch.setattr(
        "qubit_cli.overlay.webchat_show",
        lambda sid: {"session_id": sid, "name": "Chat", "message_count": 2})
    monkeypatch.setattr(
        "qubit_cli.overlay.webchat_messages",
        lambda sid, limit=50: {"messages": [
            {"role": "user", "content": "hello"}]})
    host._handle_webchat_command("/webchat")
    host._handle_webchat_command("/webchat show c1")
    host._handle_webchat_command("/webchat messages c1")
    out = _out(capsys)
    assert "c1 — Chat" in out and "[user] hello" in out


def test_webchat_delete_confirm_gated(host, capsys, monkeypatch):
    called = []
    monkeypatch.setattr(
        "qubit_cli.overlay.webchat_delete",
        lambda sid, confirmed=False: called.append((sid, confirmed)) or {
            "state": "OK"})
    host._handle_webchat_command("/webchat delete c1")
    assert called == []  # dry run: backend untouched
    assert "Dry run" in _out(capsys)
    host._handle_webchat_command("/webchat delete c1 --confirm")
    assert called == [("c1", True)]
    assert "Deleted webchat session: c1" in _out(capsys)


# ---- /memory-backend --------------------------------------------------------

def test_memory_backend_status_provider_probe(host, capsys, monkeypatch):
    monkeypatch.setattr(
        "qubit_cli.overlay.memory_status",
        lambda: {"state": "OK", "provider": "qubit-local"})
    monkeypatch.setattr(
        "qubit_cli.overlay.memory_provider",
        lambda: {"state": "OK", "provider": "qubit-local"})
    monkeypatch.setattr(
        "qubit_cli.overlay.memory_probe",
        lambda: {"state": "OK", "latency_ms": 3})
    host._handle_memory_backend_command("/memory-backend")
    host._handle_memory_backend_command("/memory-backend provider")
    host._handle_memory_backend_command("/memory-backend probe")
    out = _out(capsys)
    assert "qubit-local" in out and "latency_ms" in out


def test_memory_backend_unknown(host, capsys):
    host._handle_memory_backend_command("/memory-backend nuke")
    assert "Unknown memory-backend command" in _out(capsys)


# ---- /canvas ----------------------------------------------------------------

def test_canvas_list_get(host, capsys, monkeypatch):
    monkeypatch.setattr(
        "qubit_cli.overlay.canvas_list",
        lambda: {"files": [{"path": "a.md", "size": 12}]})
    monkeypatch.setattr(
        "qubit_cli.overlay.canvas_get",
        lambda path: {"path": path, "size": 5, "text": "hello"})
    host._handle_canvas_command("/canvas")
    host._handle_canvas_command("/canvas get a.md")
    out = _out(capsys)
    assert "a.md" in out and "hello" in out


def test_canvas_add_remove_confirm_gated(host, capsys, monkeypatch):
    called = []
    monkeypatch.setattr(
        "qubit_cli.overlay.canvas_add",
        lambda path, content, confirmed=False: called.append(
            ("add", path, content, confirmed)) or {"path": path})
    monkeypatch.setattr(
        "qubit_cli.overlay.canvas_remove",
        lambda path, confirmed=False: called.append(
            ("remove", path, confirmed)) or {"removed": path})
    host._handle_canvas_command("/canvas add a.md text")
    assert called == []
    assert "Dry run" in _out(capsys)
    host._handle_canvas_command("/canvas add a.md some text --confirm")
    host._handle_canvas_command("/canvas remove a.md")
    assert ("remove", "a.md", False) not in called  # dry run untouched
    assert ("add", "a.md", "some text", True) in called
    host._handle_canvas_command("/canvas remove a.md --confirm")
    assert ("remove", "a.md", True) in called
    assert "Canvas file removed: a.md" in _out(capsys)


# ---- /taskflow --------------------------------------------------------------

def test_taskflow_flows_default_and_hooks(host, capsys, monkeypatch):
    monkeypatch.setattr(
        "qubit_cli.overlay.taskflow_flows",
        lambda limit=50: {"jobs": [
            {"id": 3, "enabled": True, "name": "Nightly"}]})
    monkeypatch.setattr(
        "qubit_cli.overlay.taskflow_hooks",
        lambda: {"hooks": [{"name": "h1", "event": "e", "enabled": True}]})
    host._handle_taskflow_command("/taskflow")
    host._handle_taskflow_command("/taskflow hooks")
    out = _out(capsys)
    assert "#3 [on] Nightly" in out and "h1 [e] enabled" in out


def test_taskflow_show_pause_resume_trigger(host, capsys, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "qubit_cli.overlay.taskflow_flow",
        lambda job_id: {"job": {"id": job_id, "name": "N",
                                "schedule": "daily", "enabled": True}})
    import qubit_cli.overlay as ov
    monkeypatch.setattr(ov, "taskflow_pause", lambda i: seen.update(p=i) or {})
    monkeypatch.setattr(ov, "taskflow_resume", lambda i: seen.update(r=i) or {})
    monkeypatch.setattr(ov, "taskflow_trigger", lambda i: seen.update(t=i) or {})
    host._handle_taskflow_command("/taskflow show 4")
    host._handle_taskflow_command("/taskflow pause 4")
    host._handle_taskflow_command("/taskflow resume 4")
    host._handle_taskflow_command("/taskflow trigger 4")
    assert seen == {"p": 4, "r": 4, "t": 4}
    assert "Flow #4: N" in _out(capsys)


def test_taskflow_delete_confirm_gated(host, capsys, monkeypatch):
    called = []
    monkeypatch.setattr(
        "qubit_cli.overlay.taskflow_delete",
        lambda i: called.append(i) or {"state": "OK"})
    host._handle_taskflow_command("/taskflow delete 9")
    assert called == []
    assert "Dry run" in _out(capsys)
    host._handle_taskflow_command("/taskflow delete 9 --confirm")
    assert called == [9]
    assert "Deleted flow #9" in _out(capsys)
