"""Wave B2 Task 3 (backend only): /qubit-chat PC actions via the shared local path.

Scope: `voicebridge.local.actions.match` / `execute` only. The
`chat_transport` `local_action` branch is Wave C's — this file must NOT
import or drive `chat_transport`.
"""
import subprocess

import pytest

from voicebridge.local import actions as LA


def test_open_app_knows_editors_and_ides():
    for app in ("vscode", "cursor", "chromium", "terminal"):
        m = LA.match(f"open {app}")
        assert m is not None and m.name == "open_app", app


def test_open_app_knows_file_manager():
    for phrase in ("open file-manager", "open file manager"):
        m = LA.match(phrase)
        assert m is not None and m.name == "open_app", phrase
        assert m.params.get("app") == "files", m.params


@pytest.mark.parametrize("phrase,launcher", [
    ("open vscode", "code"),
    ("open cursor", "cursor"),
    ("open chromium", "chromium"),
    ("open terminal", "gnome-terminal"),
    ("open file-manager", "xdg-open"),
])
def test_open_app_execute_uses_launcher_mocked(monkeypatch, phrase, launcher):
    calls = []
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda argv, **kw: (calls.append(argv), _DummyProc())[1],
    )
    m = LA.match(phrase)
    assert m is not None and m.name == "open_app", phrase
    out = LA.execute(m)
    assert calls and calls[0][0] == launcher, (phrase, calls)
    assert "opening" in out.lower(), out


def test_open_app_unknown_launcher_is_honest():
    m = LA.ActionMatch(name="open_app", params={"app": "no-such-app"}, text="open no-such-app")
    out = LA.execute(m)
    assert "don't have a launcher" in out, out


class _DummyProc:
    pass
