"""Redo results land in the clipboard; selected text is redoable (owner ask).

TDD red-first (2026-10-04 voice-final lane):

1. EVERY successful redo/rewrite transform output is copied to the system
   clipboard (X11 xclip/xsel vs Wayland wl-copy — whatever is present; LOUD
   audited failure when none, never silently skipped). Spoken receipt appends
   "copied to clipboard" (short).
2. SELECTION: when a selection is available on the turn (the PTT/capture path
   carries it; the wake path is selection-less BY DESIGN since the no-key fix
   — key synthesis must never return to the wake path), the SELECTED text is
   rewritten (not trailing words) and the result is copied to clipboard.
   Replacement-in-place (retyping over the selection) is FORBIDDEN on the wake
   path; on the explicit PTT path paste happens ONLY through the existing
   safe paste primitive (DesktopAdapter.insert_text) — else clipboard +
   honest spoken status.

No test touches the live DISPLAY, clipboard, microphone, or model: the
clipboard backend is faked (recording desktop / injected which+run).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit.schema import TransformResult  # noqa: E402
from voicebridge.voice_edit.service import VoiceEditService  # noqa: E402


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------
class FakeCfg:
    def __init__(self, data=None):
        self._data = data or {}

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


class FakeEngine:
    """Deterministic transform engine stub (no model). Records its input."""

    def __init__(self, text="REWRITTEN"):
        self.calls = []
        self.text = text

    def transform(self, cfg, cmd, selected_text="", **kw):
        self.calls.append((getattr(cmd, "action", ""), selected_text))
        return TransformResult(ok=True, text=self.text,
                               action=getattr(cmd, "action", ""),
                               model="test-engine", provider="local",
                               locality="local", privacy_mode="local")


class FakeClipboardDesktop:
    """Recording clipboard surface (stands in for DesktopAdapter)."""

    def __init__(self, *, selection="SELECTED BODY", available=True,
                 clipboard_ok=True):
        self.selection = selection
        self.available = available
        self.clipboard_ok = clipboard_ok
        self.copied = []
        self.inserted = []

    def session(self):
        return {"session_type": "x11", "display": ":0", "x11": True}

    def _tool(self, name):
        return True

    def _clip_tool(self):
        return "xclip"

    def set_clipboard(self, text):
        self.copied.append(text)
        return self.clipboard_ok

    def is_fullscreen(self, window_id=""):
        return False

    def capture_selection(self, cfg=None):
        from voicebridge.voice_edit.schema import SelectionSnapshot
        from voicebridge.voice_edit.transform import digest_text
        snap = SelectionSnapshot(
            available=self.available,
            text=self.selection if self.available else "",
            char_count=len(self.selection) if self.available else 0,
            app_class="code", app_name="code", window_id="42",
            focused=True, captured_via="clipboard")
        if self.available:
            snap.digest = digest_text(self.selection)
        return snap

    def insert_text(self, text, snapshot, cfg=None, *, method=""):
        from voicebridge.voice_edit.schema import InsertionResult
        self.inserted.append(text)
        return InsertionResult(ok=True, method="clipboard_paste",
                               app_class="code", focus_restored=True,
                               clipboard_restored=True)


class FakeStore:
    def messages(self, session_id, limit=200):
        return {"messages": []}


def _cfg(**ve):
    base = {"enabled": True, "routing": "local",
            "confirm_before_replace": False}
    base.update(ve)
    return FakeCfg({"voice_edit": base})


def _service(engine=None, desktop=None):
    audits = []

    def audit(op, actor, detail):
        audits.append((op, detail))

    svc = VoiceEditService(
        _cfg(), desktop=desktop or FakeClipboardDesktop(),
        engine=engine or FakeEngine(),
        router=None, audit=audit, estop=lambda cfg: False)
    svc._session_store = FakeStore()
    return svc, audits


# ---------------------------------------------------------------------------
# clipboard backend detection (X11 xclip/xsel vs Wayland wl-copy)
# ---------------------------------------------------------------------------
def test_detect_backend_x11_prefers_xclip():
    from voicebridge.voice_edit import clipboard as CB
    which = lambda name: "/usr/bin/" + name  # noqa: E731
    env = {"DISPLAY": ":0", "WAYLAND_DISPLAY": ""}
    assert CB.detect_backend(which=which, env=env) == "xclip"


def test_detect_backend_x11_falls_back_to_xsel():
    from voicebridge.voice_edit import clipboard as CB

    def which(name):
        return "/usr/bin/xsel" if name == "xsel" else None

    env = {"DISPLAY": ":0", "WAYLAND_DISPLAY": ""}
    assert CB.detect_backend(which=which, env=env) == "xsel"


def test_detect_backend_wayland_prefers_wl_copy():
    from voicebridge.voice_edit import clipboard as CB

    def which(name):
        return "/usr/bin/wl-copy" if name in ("wl-copy", "wl-clipboard") else None

    env = {"DISPLAY": "", "WAYLAND_DISPLAY": "wayland-0"}
    assert CB.detect_backend(which=which, env=env) == "wl-copy"


def test_detect_backend_none_when_no_tool_present():
    from voicebridge.voice_edit import clipboard as CB
    assert CB.detect_backend(which=lambda name: None, env={}) == "none"


def test_copy_text_prefers_desktop_adapter():
    from voicebridge.voice_edit import clipboard as CB
    desk = FakeClipboardDesktop()
    res = CB.copy_text("REWRITTEN", desktop=desk,
                       which=lambda name: None, env={})
    assert res["ok"] is True
    assert desk.copied == ["REWRITTEN"]


def test_copy_text_xsel_direct_fallback_when_adapter_has_no_tool():
    from voicebridge.voice_edit import clipboard as CB
    ran = []

    class NoToolDesktop:
        def _clip_tool(self):
            return None

        def set_clipboard(self, text):
            return False

    def which(name):
        return "/usr/bin/xsel" if name == "xsel" else None

    def run(argv, **kw):
        ran.append(argv)
        return (0, b"", b"")

    res = CB.copy_text("REWRITTEN", desktop=NoToolDesktop(),
                       run=run, which=which,
                       env={"DISPLAY": ":0", "WAYLAND_DISPLAY": ""})
    assert res["ok"] is True
    assert res["backend"] == "xsel"
    assert ran and ran[0][0] == "xsel"


def test_copy_text_no_tool_fails_loud_never_silent():
    from voicebridge.voice_edit import clipboard as CB

    class NoToolDesktop:
        def _clip_tool(self):
            return None

        def set_clipboard(self, text):
            return False

    res = CB.copy_text("REWRITTEN", desktop=NoToolDesktop(),
                       run=lambda argv, **kw: (1, b"", b"no"),
                       which=lambda name: None, env={})
    assert res["ok"] is False
    assert res["code"] == "VOICE_EDIT_CLIPBOARD_UNAVAILABLE"


def test_copy_text_empty_is_not_a_copy():
    from voicebridge.voice_edit import clipboard as CB
    desk = FakeClipboardDesktop()
    res = CB.copy_text("   ", desktop=desk,
                       which=lambda name: None, env={})
    assert res["ok"] is False
    assert desk.copied == []


# ---------------------------------------------------------------------------
# wake path (selection-less): redo suffix is the input, result is copied
# ---------------------------------------------------------------------------
def test_wake_redo_copies_rewrite_to_clipboard():
    desk = FakeClipboardDesktop()
    engine = FakeEngine(text="REWRITTEN OUTPUT")
    svc, audits = _service(engine=engine, desktop=desk)
    out = svc.ask("redo this text for me hello world")
    assert out.get("answer") == "REWRITTEN OUTPUT"
    # The redo suffix (no selection on the wake path) was the transform input.
    assert engine.calls and engine.calls[0][1] == "hello world"
    # ... and the successful output landed in the clipboard.
    assert desk.copied == ["REWRITTEN OUTPUT"]
    assert out.get("clipboard_ok") is True
    clip_audits = [a for a in audits if a[0] == "voice_edit.ask.transform_clipboard"]
    assert clip_audits and clip_audits[0][1].get("ok") is True


def test_wake_redo_clipboard_missing_still_answers_but_flags_loud(monkeypatch):
    import voicebridge.voice_edit.clipboard as CB
    # Hermetic: simulate a host with NO clipboard tool at all (this dev host
    # has xclip, so pin the detector — the direct fallback must not fire).
    monkeypatch.setattr(CB, "detect_backend", lambda **kw: "none")
    desk = FakeClipboardDesktop(clipboard_ok=False)
    engine = FakeEngine(text="REWRITTEN OUTPUT")
    svc, audits = _service(engine=engine, desktop=desk)
    out = svc.ask("redo this text for me hello world")
    # The answer is never withheld ...
    assert out.get("answer") == "REWRITTEN OUTPUT"
    # ... but the missing clipboard is LOUD (fields + audit), never silent.
    assert out.get("clipboard_ok") is False
    assert out.get("clipboard_code") == "VOICE_EDIT_CLIPBOARD_UNAVAILABLE"
    clip_audits = [a for a in audits if a[0] == "voice_edit.ask.transform_clipboard"]
    assert clip_audits and clip_audits[0][1].get("ok") is False


# ---------------------------------------------------------------------------
# PTT path (selection carried): SELECTED text is rewritten, not trailing words
# ---------------------------------------------------------------------------
def test_ptt_selection_wins_over_trailing_words_and_is_copied():
    desk = FakeClipboardDesktop(selection="SELECTED BODY")
    engine = FakeEngine(text="REWRITTEN SELECTION")
    svc, audits = _service(engine=engine, desktop=desk)
    rid = svc.begin(request_id="r-sel")["request_id"]
    svc.capture(rid)
    out = svc.transform(rid, "redo this text for me some trailing words")
    assert out.get("mode") == "preview"
    # The SELECTED text — not the trailing words — was the transform input.
    assert engine.calls and engine.calls[0][1] == "SELECTED BODY"
    assert out.get("proposed") == "REWRITTEN SELECTION"
    # The successful preview output is already in the clipboard.
    assert "REWRITTEN SELECTION" in desk.copied
    assert out.get("clipboard_ok") is True


def test_ptt_apply_copies_final_text_to_clipboard():
    desk = FakeClipboardDesktop(selection="SELECTED BODY")
    engine = FakeEngine(text="REWRITTEN SELECTION")
    svc, audits = _service(engine=engine, desktop=desk)
    rid = svc.begin(request_id="r-apply")["request_id"]
    svc.capture(rid)
    svc.transform(rid, "redo this text for me")
    out = svc.apply(rid, confirm=True)
    assert out.get("ok") is True
    # Paste went through the safe primitive AND the final text is on clipboard.
    assert desk.inserted == ["REWRITTEN SELECTION"]
    assert desk.copied[-1] == "REWRITTEN SELECTION"
    assert out.get("clipboard_ok") is True


# ---------------------------------------------------------------------------
# no-keys boundary: the wake path never touches capture/key synthesis
# ---------------------------------------------------------------------------
def test_wake_path_is_selectionless_without_key_synthesis():
    import voicebridge.desktop_helper as dh

    class BoomClient:
        def capture(self, rid):
            raise AssertionError("wake must never invoke service capture")

    class FakeOverlay:
        def set_status(self, state, detail=""):
            pass

        def show_preview(self, payload):
            pass

    class FocusDesktop:
        def session(self):
            return {"session_type": "x11", "x11": True}

        def focused_window(self):
            return {"window_id": "42", "app_class": "code",
                    "app_name": "code", "window_title": ""}

        def _tool(self, name):
            return True

    helper = dh.DesktopHelper(FakeCfg({"voice_edit": {"enabled": True}}),
                              client=BoomClient(), overlay=FakeOverlay(),
                              desktop=FocusDesktop())
    helper.request_id = "r-wake"
    assert helper._capture_selection_local() is True
    assert helper.snapshot is not None
    assert helper.snapshot.available is False
    assert helper.snapshot.captured_via == "wake_no_capture"


# ---------------------------------------------------------------------------
# spoken receipt: "copied to clipboard" is spoken, short
# ---------------------------------------------------------------------------
def test_spoken_receipt_appends_copied_to_clipboard():
    import voicebridge.desktop_helper as dh

    class AskClient:
        def __init__(self):
            self.calls = []

        def ask_result(self, text, request_id=""):
            return {"ok": True, "answer": "REWRITTEN OUTPUT",
                    "mode": "answer", "spoken": False,
                    "clipboard_ok": True, "clipboard_backend": "xclip"}

        def speak(self, text):
            self.calls.append(("speak", text))
            return b""

    class FakeOverlay:
        def __init__(self):
            self.statuses = []

        def set_status(self, state, detail=""):
            self.statuses.append((state, detail))

        def show_preview(self, payload):
            self.statuses.append(("preview", ""))

    client = AskClient()
    helper = dh.DesktopHelper(FakeCfg({"voice_edit": {"enabled": True}}),
                              client=client, overlay=FakeOverlay(),
                              desktop=FakeClipboardDesktop())
    helper.request_id = "r-spoken"
    helper._answer_question("redo this text for me hello world", mode="answer")
    spoken = [c[1] for c in client.calls if c[0] == "speak"]
    assert spoken and spoken[0] == "REWRITTEN OUTPUT"
    assert "Copied to clipboard." in spoken


def test_spoken_receipt_honest_when_clipboard_failed():
    import voicebridge.desktop_helper as dh

    class AskClient:
        def __init__(self):
            self.calls = []

        def ask_result(self, text, request_id=""):
            return {"ok": True, "answer": "REWRITTEN OUTPUT",
                    "mode": "answer", "spoken": False,
                    "clipboard_ok": False,
                    "clipboard_code": "VOICE_EDIT_CLIPBOARD_UNAVAILABLE"}

        def speak(self, text):
            self.calls.append(("speak", text))
            return b""

    class FakeOverlay:
        def set_status(self, state, detail=""):
            pass

        def show_preview(self, payload):
            pass

    client = AskClient()
    helper = dh.DesktopHelper(FakeCfg({"voice_edit": {"enabled": True}}),
                              client=client, overlay=FakeOverlay(),
                              desktop=FakeClipboardDesktop())
    helper.request_id = "r-spoken-fail"
    helper._answer_question("redo this text for me hello world", mode="answer")
    spoken = [c[1] for c in client.calls if c[0] == "speak"]
    assert spoken and spoken[0] == "REWRITTEN OUTPUT"
    # Honesty, not silence: the failure is spoken short (audit holds detail).
    assert any("Clipboard" in s for s in spoken[1:])
