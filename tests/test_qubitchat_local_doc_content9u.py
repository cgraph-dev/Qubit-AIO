"""Spoken document content is written, not dropped (live 2026-10-04).

"Open a txt document and write hello world in it" opened an (empty)
document and silently dropped "hello world" — the param branch extracted
a name but no content, and the writers always wrote "". Now the branch
extracts ``content`` ("write X in it" / "with the text X" / "saying X" /
"containing X" / "that says X"), create writes it into the new file,
and open appends it to the resolved note (never overwrites).
"""
import subprocess
from pathlib import Path

from voicebridge.local import actions as LA


class _DummyProc:
    def poll(self):
        return 0


def _stub_editor(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda argv, **kw: (calls.append(argv), _DummyProc())[1])
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return calls


def test_owner_utterance_extracts_content_not_name():
    m = LA.match("open a txt document and write hello world in it")
    assert m is not None, "must still match a doc action"
    assert (m.params.get("content") or "") == "hello world", m.params
    assert "hello" not in (m.params.get("name") or "").lower(), m.params


def test_named_create_with_content():
    m = LA.match("create a text document called shopping with the text milk and eggs")
    assert m is not None and m.name == "create_document", m
    assert "shopping" in (m.params.get("name") or "").lower(), m.params
    assert (m.params.get("content") or "") == "milk and eggs", m.params


def test_saying_and_containing_shapes():
    assert LA.match("create a note saying call mom").params.get("content") == "call mom"
    assert LA.match("open a txt file containing the plan").params.get("content") == "the plan"


def test_create_writes_content_to_new_file(tmp_path, monkeypatch):
    _stub_editor(monkeypatch, tmp_path)
    m = LA.match("create a txt note called proveit with the text hello world")
    out = LA.execute(m)
    made = list((tmp_path / "Documents").glob("*.txt"))
    assert len(made) == 1, (made, out)
    assert made[0].read_text() == "hello world", made[0].read_text()
    assert "hello world" not in made[0].name
    assert "11 chars written" in out, out


def test_open_appends_content_to_existing_note(tmp_path, monkeypatch):
    calls = _stub_editor(monkeypatch, tmp_path)
    docs = tmp_path / "Documents"
    docs.mkdir()
    (docs / "shopping.txt").write_text("milk")
    before = (docs / "shopping.txt").read_text()
    m = LA.match("open the shopping document and write eggs in it")
    assert m is not None and m.name == "open_document", m
    out = LA.execute(m)
    after = (docs / "shopping.txt").read_text()
    assert after == before + "\neggs", after
    assert "Added" in out and calls, out


def test_no_content_keeps_old_empty_behavior(tmp_path, monkeypatch):
    _stub_editor(monkeypatch, tmp_path)
    m = LA.match("create a txt note called blanknote")
    assert (m.params.get("content") or "") == "", m.params
    out = LA.execute(m)
    made = list((tmp_path / "Documents").glob("*.txt"))
    assert made[0].read_text() == ""
    assert "empty" in out, out
