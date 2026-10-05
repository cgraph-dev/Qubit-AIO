"""Slice 3 — VS Code edits by voice (9u).

Shape: "in chat: in repo X, change ..." enters through Agent 1's route-word
bypass (``chat_transport.parse_route_word`` — called through here, never
reimplemented), and the stripped task text is handled by three new local
actions in ``voicebridge.local.actions``:

- ``repo_edit`` — parses repo + instruction; stages confirm-gated (zero
  writes until the voice-"yes" settle re-invokes with ``confirmed=yes``);
  dictated line-by-line edits are refused (OUT per the Slice-3 decision).
- ``vscode_open`` — opens a file (``code -g path:line``) in VS Code;
  honest when ``code`` is absent.
- ``code_mcp_probe`` — read-only probe of the official VS Code native MCP
  integration (``code --add-mcp`` presence); never installs anything and
  never invents an MCP server. Absent -> CLI-open + worker-edit fallback.

Plus the pure helper ``format_edit_report`` (spoken diff summary: file +
lines changed, never file content).

Scope: ``LA.match`` / ``LA.execute`` / helpers only. This file MAY import
``chat_transport`` for the entry call-through (the B2 no-CT rule belongs to
``test_qubitchat_local_actions9u.py``, not here).
"""
import subprocess

from voicebridge.local import actions as LA


class _DummyProc:
    pass


def _scratch_repo(tmp_path, name="demo"):
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    target = root / "hello.txt"
    target.write_text("Hello world\n", encoding="utf-8")
    return root, target


# --- Entry: through Agent 1's route-word bypass, not around it -------------

def test_entry_in_chat_prefix_routes_to_repo_edit():
    from voicebridge import chat_transport as CT
    rw = CT.parse_route_word("in chat: in repo demo, change the greeting")
    assert rw is not None and rw["action"] == "agent_task", rw
    m = LA.match(rw["task_text"])
    assert m is not None and m.name == "repo_edit", m
    assert m.params.get("repo") == "demo", m.params
    assert "greeting" in (m.params.get("instruction") or ""), m.params


def test_repo_edit_routing_variants():
    for phrase in (
        "in repo demo change the greeting",
        "in repo demo, change the greeting to goodbye",
        "change the greeting in repo demo",
        "in repo demo change hello.txt to say goodbye",
    ):
        m = LA.match(phrase)
        assert m is not None and m.name == "repo_edit", (phrase, m)
        assert m.params.get("repo") == "demo", (phrase, m.params)


def test_repo_edit_params_are_scalar():
    m = LA.match("in repo demo, change the greeting")
    assert m is not None and m.name == "repo_edit", m
    for v in m.params.values():
        assert isinstance(v, str), m.params


# --- repo_edit: staging only until the voice-"yes" settle -------------------

def test_repo_edit_unconfirmed_stages_zero_writes(tmp_path):
    root, target = _scratch_repo(tmp_path)
    before = target.read_text(encoding="utf-8")
    m = LA.ActionMatch(
        name="repo_edit",
        params={"repo": "demo", "path": str(root),
                "instruction": "change the greeting to goodbye",
                "confirmed": ""},
        text="in repo demo, change the greeting to goodbye",
    )
    out = LA.execute(m)
    assert target.read_text(encoding="utf-8") == before, "no write before settle"
    assert "yes" in out.lower(), out  # staged prompt names the settle word


def test_repo_edit_confirmed_applies_bounded_replace(tmp_path):
    root, target = _scratch_repo(tmp_path)
    m = LA.ActionMatch(
        name="repo_edit",
        params={"repo": "demo", "path": str(root),
                "file": "hello.txt",
                "old": "Hello world", "new": "Goodbye world",
                "instruction": "replace Hello world with Goodbye world in hello.txt",
                "confirmed": "yes"},
        text="in repo demo, replace Hello world with Goodbye world in hello.txt",
    )
    out = LA.execute(m)
    assert target.read_text(encoding="utf-8") == "Goodbye world\n", out
    assert "hello.txt" in out, out  # spoken summary names the file


def test_repo_edit_confirmed_reports_line_numbers(tmp_path):
    root, target = _scratch_repo(tmp_path)
    target.write_text("one\nHello world\nthree\n", encoding="utf-8")
    m = LA.ActionMatch(
        name="repo_edit",
        params={"repo": "demo", "path": str(root),
                "file": "hello.txt",
                "old": "Hello world", "new": "Goodbye world",
                "instruction": "replace Hello world with Goodbye world in hello.txt",
                "confirmed": "yes"},
        text="in repo demo, replace Hello world with Goodbye world in hello.txt",
    )
    out = LA.execute(m)
    assert "hello.txt" in out and "2" in out, out  # file + changed line


def test_repo_edit_refuses_line_by_line_dictation(tmp_path):
    root, target = _scratch_repo(tmp_path)
    before = target.read_text(encoding="utf-8")
    for phrase in (
        "in repo demo, line 1 hello line 2 world line 3 goodbye",
        "in repo demo, type exactly: import os newline import sys",
    ):
        m = LA.match(phrase)
        assert m is not None and m.name == "repo_edit", (phrase, m)
        settled = LA.ActionMatch(
            name="repo_edit", params={**m.params, "path": str(root),
                                      "confirmed": "yes"}, text=phrase)
        out = LA.execute(settled)
        assert "describ" in out.lower() or "dictat" in out.lower(), (phrase, out)
    assert target.read_text(encoding="utf-8") == before, "dictation never writes"


def test_repo_edit_refuses_path_escape(tmp_path):
    root, _target = _scratch_repo(tmp_path)
    m = LA.ActionMatch(
        name="repo_edit",
        params={"repo": "demo", "path": str(root),
                "file": "../outside.txt",
                "old": "a", "new": "b",
                "instruction": "replace a with b in ../outside.txt",
                "confirmed": "yes"},
        text="in repo demo, replace a with b",
    )
    out = LA.execute(m)
    assert "outside" in out.lower() or "refus" in out.lower(), out
    assert not (tmp_path / "outside.txt").exists(), "escape writes nothing"


def test_repo_edit_unknown_repo_is_honest(tmp_path):
    m = LA.match("in repo no-such-repo-xyz, change the greeting")
    assert m is not None and m.name == "repo_edit", m
    out = LA.execute(m)
    assert "don't know" in out.lower() or "which repo" in out.lower(), out


def test_repo_edit_ambiguous_replace_is_fail_closed(tmp_path):
    root, target = _scratch_repo(tmp_path)
    target.write_text("ha\nha\n", encoding="utf-8")
    m = LA.ActionMatch(
        name="repo_edit",
        params={"repo": "demo", "path": str(root),
                "file": "hello.txt",
                "old": "ha", "new": "ho",
                "instruction": "replace ha with ho in hello.txt",
                "confirmed": "yes"},
        text="in repo demo, replace ha with ho in hello.txt",
    )
    out = LA.execute(m)
    assert target.read_text(encoding="utf-8") == "ha\nha\n", "ambiguous: no write"
    assert "more than once" in out.lower() or "ambiguous" in out.lower(), out


def test_repo_edit_is_not_compoundable():
    # A write half must never fold into a "both at once" run: the whole
    # utterance falls through to the agent path (which confirm-gates it).
    assert LA.match("in repo demo change the greeting and open youtube") is None


# --- vscode_open -------------------------------------------------------------

def test_vscode_open_routes():
    for phrase in (
        "open hello.txt in vscode",
        "open hello.txt in code",
        "show notes.txt in vscode",
    ):
        m = LA.match(phrase)
        assert m is not None and m.name == "vscode_open", (phrase, m)


def test_vscode_open_executes_code_goto(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda argv, **kw: (calls.append(argv), _DummyProc())[1])
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/code")
    f = tmp_path / "hello.txt"
    f.write_text("hi\n", encoding="utf-8")
    m = LA.match("open hello.txt in vscode")
    assert m is not None and m.name == "vscode_open", m
    m.params["path"] = str(f)
    out = LA.execute(m)
    assert calls and calls[0][:2] == ["code", "-g"], calls
    assert str(f) in calls[0][2], calls
    assert "vs code" in out.lower(), out


def test_vscode_open_carries_line_number(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda argv, **kw: (calls.append(argv), _DummyProc())[1])
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/code")
    f = tmp_path / "hello.txt"
    f.write_text("hi\n", encoding="utf-8")
    m = LA.ActionMatch(
        name="vscode_open",
        params={"path": str(f), "line": "2"},
        text="open hello.txt in vscode at line 2")
    LA.execute(m)
    assert calls and calls[0][2] == f"{f}:2", calls


def test_vscode_open_missing_code_is_honest(monkeypatch, tmp_path):
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda argv, **kw: (_ for _ in ()).throw(
            AssertionError("must not launch when code is absent")))
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    f = tmp_path / "hello.txt"
    f.write_text("hi\n", encoding="utf-8")
    m = LA.ActionMatch(
        name="vscode_open",
        params={"path": str(f), "line": ""},
        text="open hello.txt in vscode")
    out = LA.execute(m)
    assert "isn't installed" in out.lower(), out


def test_vscode_open_does_not_shadow_documents_apps_projects():
    assert LA.match("open a text document").name == "open_document"
    assert LA.match("open a text document in vscode").name == "open_document"
    assert LA.match("open discord").name == "open_app"
    assert LA.match("open youtube").name == "open_website"
    assert LA.match("open downloads").name == "open_folder"


# --- code_mcp_probe (read-only; never installs, never invents) ----------------

def test_code_mcp_probe_routes():
    for phrase in (
        "is the vscode mcp integration available",
        "check code mcp support",
        "is code --add-mcp present",
    ):
        m = LA.match(phrase)
        assert m is not None and m.name == "code_mcp_probe", (phrase, m)


def test_code_mcp_probe_present(monkeypatch):
    monkeypatch.setattr(
        LA, "_run",
        lambda argv, **kw: "add-mcp  Add an MCP server  --add-mcp <name>")
    out = LA.execute(LA.match("is the vscode mcp integration available"))
    assert "available" in out.lower(), out
    assert "--add-mcp" in out, out


def test_code_mcp_probe_absent_falls_back(monkeypatch):
    monkeypatch.setattr(LA, "_run", lambda argv, **kw: "usage: code [options]")
    out = LA.execute(LA.match("is the vscode mcp integration available"))
    assert "fall" in out.lower() and "worker" in out.lower(), out


def test_code_mcp_probe_never_installs(monkeypatch):
    launched = []
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda argv, **kw: (launched.append(argv), _DummyProc())[1])
    monkeypatch.setattr(LA, "_run", lambda argv, **kw: "usage: code [options]")
    LA.execute(LA.match("check code mcp support"))
    assert launched == [], launched  # probe reads only; zero launches


# --- format_edit_report: file + lines, never content --------------------------

def test_format_edit_report_names_file_and_lines():
    from voicebridge.local.actions import format_edit_report
    secret = "sk-live-THIS-MUST-NEVER-APPEAR"
    out = format_edit_report(
        {"file": "hello.txt", "added": 3, "removed": 1,
         "start_line": 2, "end_line": 4, "preview": secret})
    assert "hello.txt" in out, out
    assert "3" in out and "1" in out, out
    assert secret not in out, "content must never reach the spoken path"
