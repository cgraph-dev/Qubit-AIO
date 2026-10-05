"""Fix lane 2026-10-04: three journal-proven voice-action failures.

BUG 1 (open_document parses "for me" as the filename): "Open a text file
for me." matched open_document with ``name="me"`` and answered "couldn't
find 'me' in Documents". The beneficiary phrase must be stripped so a bare
open carries no name (blank-editor path), and trailing beneficiaries must
not pollute real names.

BUG 2 (dictate transcribes but doesn't type): Right-Ctrl "Type this for
me. 1 plus 1 equals 99." (a) failed insertion (empty method = focus-gate
refusal on a Firefox app_class spelling variant) and (b) kept a leading
". " cue artifact. The helper insert boundary strips leading cue
punctuation; the focus gate tolerates WM_CLASS spelling variants for the
SAME window id (window id stays the hard gate).

BUG 3 ("open it" -> coding): the bare-open guard covered exactly "open".
Open + a bare pronoun/no-object ("open it/that/this", "open it please",
the journal shape "Open it, hey Qubit, open it.") must clarify, never
task. Named/site opens stay tasks.
"""
from __future__ import annotations

import subprocess

import pytest

from voicebridge.local import actions as LA
from voicebridge.voice_edit.router import VoiceRouter


class FakeCfg:
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, *path, default=None):
        cur = self._data
        for p in path:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return default
        return cur


class FakeIntent:
    def __init__(self, kind, task_text=""):
        self.kind = kind
        self.task_text = task_text


class FakeClassifier:
    def __init__(self, kind="coding"):
        self.kind = kind
        self.calls = []

    def _task_hint_score(self, text):
        return 0.0

    def classify(self, text):
        self.calls.append(text)
        return FakeIntent(self.kind, text)


def _router(kind="coding"):
    cfg = FakeCfg({"voice_edit": {"routing": "local", "allow_api": True,
                                  "router": "deterministic"}})
    clf = FakeClassifier(kind)
    return VoiceRouter(cfg, classifier=clf), clf


# --- BUG 1: beneficiary phrases are not document names -----------------------

@pytest.mark.parametrize("phrase", [
    "open a text file for me.",
    "open a text file for me",
    "open a text document for me",
    "open a text file please",
    "open a text document for you",
])
def test_bug1_bare_open_carries_no_name(phrase):
    m = LA.match(phrase)
    assert m is not None and m.name == "open_document", (phrase, m)
    assert (m.params.get("name") or "") == "", (phrase, m.params)


def test_bug1_trailing_beneficiary_stripped_from_real_name():
    m = LA.match("open the shopping list document for me")
    assert m is not None and m.name == "open_document", m
    assert (m.params.get("name") or "") == "shopping list", m.params


def test_bug1_create_name_strips_trailing_beneficiary():
    m = LA.match("create a txt note called shopping list for me")
    assert m is not None and m.name == "create_document", m
    assert (m.params.get("name") or "") == "shopping list", m.params


@pytest.mark.parametrize("phrase,expected", [
    ("open the shopping list document", "shopping list"),
    ("open a text document called meeting notes", "meeting notes"),
    ("create a txt note called shopping list", "shopping list"),
])
def test_bug1_named_opens_unaffected(phrase, expected):
    m = LA.match(phrase)
    assert m is not None, phrase
    assert expected in (m.params.get("name") or "").lower(), (phrase, m.params)


def test_bug1_bare_open_still_opens_editor(tmp_path, monkeypatch):
    """A no-name open DOES something sensible (newest note, else blank),
    never a 'couldn't find me' miss. Empty dir keeps the blank-page
    contract."""
    from pathlib import Path

    calls = []

    class _DummyProc:
        pass

    monkeypatch.setattr(
        subprocess, "Popen",
        lambda argv, **kw: (calls.append(argv), _DummyProc())[1])
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    m = LA.match("open a text file for me.")
    assert m is not None and m.name == "open_document", m
    out = LA.execute(m)
    assert calls, "the editor must actually open"
    assert "couldn't find" not in out.lower(), out


def test_bug1_bare_open_resumes_newest_note(tmp_path, monkeypatch):
    """With saved notes, a beneficiary-only open resumes the most recently
    modified one — it never searches Documents for 'me'."""
    import os
    from pathlib import Path

    calls = []

    class _DummyProc:
        pass

    docs = tmp_path / "Documents"
    docs.mkdir(parents=True, exist_ok=True)
    old = docs / "older.txt"
    new = docs / "newer.txt"
    old.write_text("old")
    new.write_text("new")
    os.utime(old, (1000000000, 1000000000))
    os.utime(new, (1800000000, 1800000000))
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda argv, **kw: (calls.append(argv), _DummyProc())[1])
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    m = LA.match("open a text file for me.")
    assert m is not None and m.name == "open_document", m
    assert (m.params.get("name") or "") == "", m.params
    out = LA.execute(m)
    assert calls and calls[0][-1].endswith("newer.txt"), (calls, out)
    assert "newer.txt" in out and "most recent" in out.lower(), out


# --- BUG 2: dictate strips cue punctuation + Firefox focus tolerance --------

class _FakeOverlay:
    def set_status(self, *a):
        pass

    def show_preview(self, *a):
        pass

    def show_error(self, *a):
        pass

    def show(self):
        pass

    def hide(self):
        pass

    def append_history(self, *a):
        pass


class _FakeClient:
    def status(self):
        return {"enabled": True}

    def begin(self):
        return {"request_id": "req-1"}

    def capture(self, rid):
        return {"selection": {"available": False}}


class _FakeDesktop:
    def __init__(self):
        self.inserted = []
        self.activated = []

    def session(self):
        return {"session_type": "x11", "x11": True}

    def focused_window(self):
        return {"window_id": "42", "app_class": "firefox",
                "app_name": "firefox", "window_title": ""}

    def insert_text(self, text, snapshot, cfg=None, method=""):
        from voicebridge.voice_edit.schema import InsertionResult
        self.inserted.append(text)
        return InsertionResult(ok=True, method="clipboard_paste",
                               app_class="firefox", focus_restored=True)

    def activate_window(self, window_id):
        self.activated.append(window_id)
        return True

    def set_clipboard(self, text):
        return True

    def is_fullscreen(self, window_id=""):
        return False


def _helper():
    import voicebridge.desktop_helper as dh
    from voicebridge.voice_edit.schema import SelectionSnapshot
    helper = dh.DesktopHelper(
        FakeCfg({"voice_edit": {"enabled": True}}),
        client=_FakeClient(), overlay=_FakeOverlay(),
        desktop=_FakeDesktop())
    helper.snapshot = SelectionSnapshot(
        available=False, app_class="firefox", window_id="42")
    helper.request_id = "req-1"
    helper._transcript = "Type this for me. 1 plus 1 equals 99."
    return helper


def test_bug2_dictate_strips_leading_cue_punctuation():
    """Journal 07:19: inserted '". 1 plus 1 equals 99."' — the cue-strip
    left '. '. The insert boundary must strip it."""
    h = _helper()
    h._insert_dictation(". 1 plus 1 equals 99.")
    assert h.desktop.inserted == ["1 plus 1 equals 99."], h.desktop.inserted


def test_bug2_dictate_clean_text_untouched():
    h = _helper()
    h._insert_dictation("1 plus 1 equals 99.")
    assert h.desktop.inserted == ["1 plus 1 equals 99."], h.desktop.inserted


def test_bug2_focus_gate_tolerates_firefox_spelling_variant():
    """Journal 07:19: helper.insert ok:false, method:'', app firefox_firefox.
    Same window id, WM_CLASS spelling variant (instance_class doubling) must
    still insert — the window id is the focus proof, not the class string."""
    from voicebridge.voice_edit.desktop import DesktopAdapter
    from voicebridge.voice_edit.schema import SelectionSnapshot

    seen = {}

    def fake_run(argv, *, input_bytes=None, timeout=4.0, env=None):
        if argv[0] == "xdotool" and "getactivewindow" in argv:
            return 0, b"42\n", b""
        if argv[0] == "xdotool" and "getwindowclassname" in argv:
            return 0, b"firefox\n", b""
        if argv[0] == "xdotool" and "getwindowname" in argv:
            return 0, b"New Tab - Firefox\n", b""
        if argv[0] == "xdotool" and "key" in argv:
            seen["paste"] = argv[-1]
            return 0, b"", b""
        if argv[0] == "xclip":
            return 0, b"UTF8_STRING\n", b""
        return 1, b"", b""

    class _Sink:
        def __init__(self):
            self.buf = bytearray()

        def write(self, data):
            self.buf.extend(data)

        def close(self):
            pass

    class _P:
        def __init__(self):
            self.stdin = _Sink()

        def kill(self):
            pass

    def fake_popen(argv, *, input_bytes=None, env=None):
        if argv[0] == "xclip" and "-i" in argv:
            return _P()
        return _P()

    def fake_which(name):
        if name in ("xdotool", "xclip"):
            return "/usr/bin/" + name
        if name == "xprop":
            return None
        return None

    adapter = DesktopAdapter(
        run=fake_run, popen=fake_popen, which=fake_which,
        env={"DISPLAY": ":0"})
    snap = SelectionSnapshot(available=False, app_class="firefox_firefox",
                             window_id="42")
    res = adapter.insert_text("1 plus 1 equals 99.", snap)
    assert res.ok is True, res
    assert res.method in ("clipboard_paste", "terminal_paste"), res


def test_bug2_focus_gate_still_refuses_other_window():
    """The tolerance must not open a hole: a different window id still
    refuses, even with a similar class name."""
    from voicebridge.voice_edit.desktop import DesktopAdapter
    from voicebridge.voice_edit.schema import SelectionSnapshot

    def fake_run(argv, *, input_bytes=None, timeout=4.0, env=None):
        if argv[0] == "xdotool" and "getactivewindow" in argv:
            return 0, b"43\n", b""
        if argv[0] == "xdotool" and "getwindowclassname" in argv:
            return 0, b"firefox\n", b""
        if argv[0] == "xdotool" and "getwindowname" in argv:
            return 0, b"Other - Firefox\n", b""
        return 1, b"", b""

    adapter = DesktopAdapter(
        run=fake_run, popen=lambda *a, **k: None,
        which=lambda name: "/usr/bin/" + name
        if name in ("xdotool", "xclip") else None,
        env={"DISPLAY": ":0"})
    snap = SelectionSnapshot(available=False, app_class="firefox",
                             window_id="42")
    res = adapter.insert_text("hello", snap)
    assert res.ok is False and res.code == "VOICE_EDIT_FOCUS_LOST", res


# --- BUG 3: open + bare pronoun clarifies ------------------------------------

@pytest.mark.parametrize("text", [
    "open it",
    "Open it.",
    "open it please",
    "open that",
    "open this",
    "Open that please.",
    "open it for me",
    "Qubit, open it.",
    "hey qubit open it",
    "Open it, hey Qubit, open it.",
])
def test_bug3_bare_pronoun_open_clarifies_never_tasks(text):
    router, clf = _router(kind="coding")
    d = router.decide(text, has_selection=False)
    assert d.mode == "chat", (text, d)
    assert d.mode != "task"
    assert clf.calls == [], (text, "bare open must short-circuit before classify")


@pytest.mark.parametrize("text,kind", [
    ("open youtube", "local"),
    ("Open YouTube.", "local"),
    ("Open a text document.", "coding"),
    ("open this document", "local"),
    ("open that file", "local"),
    ("open it in a tab", "local"),
    ("Open the pod bay doors.", "agent"),
])
def test_bug3_named_and_site_opens_still_task(text, kind):
    router, _ = _router(kind=kind)
    d = router.decide(text, has_selection=False)
    assert d.mode == "task", (text, d)
