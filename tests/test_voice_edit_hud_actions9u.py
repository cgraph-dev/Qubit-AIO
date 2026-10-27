"""End-to-end action pass for the native Tk Voice Edit HUD.

The panel is presentation only; every click routes through
``DesktopHelper.on_overlay_action`` so the helper's state machine owns the
side effects. These tests wire a REAL Tk ``PreviewOverlay`` to a REAL
``DesktopHelper`` (fake service client / desktop / mic) and invoke the real
button widgets, so the click -> helper -> service-client path that the owner
uses is exercised rather than only the isolated handlers.

Also pins the stale-transcript clear and the no-lift/no-geometry rule for a
whole turn of streaming partials.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.desktop_helper as dh
from voicebridge.voice_edit.schema import InsertionResult, SelectionSnapshot


class FakeCfg:
    def __init__(self, data=None):
        self._data = data or {"voice_edit": {"enabled": True, "history_panel": True}}

    def get(self, *path, default=None):
        node = self._data
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return default
        return node


class FakeClient:
    def __init__(self):
        self.calls = []
        self.proposed = "alpha beta gamma delta"
        self.capture_available = True

    def status(self):
        return {"enabled": True, "estop": False}

    def begin(self):
        self.calls.append(("begin",))
        return {"ok": True, "request_id": "req-hud"}

    def capture(self, rid):
        self.calls.append(("capture", rid))
        return {"ok": True, "selection": {
            "available": self.capture_available,
            "captured_via": "clipboard", "app_class": "code",
            "char_count": len(self.proposed)}}

    def _request(self, method, path, payload=None, timeout=90.0):
        self.calls.append(("request", method, path))
        return {"ok": True, "proposed": self.proposed}

    def edit(self, rid, text):
        self.calls.append(("edit", rid, text))
        self.proposed = text
        return {"ok": True, "request_id": rid, "proposed": text}

    def retry(self, rid, transcript=""):
        self.calls.append(("retry", rid, transcript))
        self.proposed = "RETRIED TEXT"
        return {"ok": True, "mode": "preview", "original": "alpha beta gamma delta",
                "proposed": self.proposed, "detected": {"action": "rewrite"}}

    def apply(self, rid, *, confirm=True, method=""):
        self.calls.append(("apply", rid, confirm))
        return {"ok": True, "mode": "applied", "request_id": rid}

    def revert(self, rid):
        self.calls.append(("revert", rid))
        return {"ok": True, "mode": "reverted", "request_id": rid}

    def cancel(self, rid):
        self.calls.append(("cancel", rid))
        return {"ok": True}


class FakeDesktop:
    def __init__(self):
        self.clipboard = ""

    def session(self):
        return {"session_type": "x11", "x11": True, "wayland": False}

    def focused_window(self):
        return {"window_id": "42", "app_class": "code", "app_name": "code",
                "window_title": ""}

    def insert_text(self, text, snapshot, cfg=None, method=""):
        return InsertionResult(ok=True, method="clipboard_paste", app_class="code",
                               focus_restored=True)

    def activate_window(self, window_id):
        return True

    def set_clipboard(self, text):
        self.clipboard = text
        return True

    def is_fullscreen(self, window_id=""):
        return False

    def _read_clipboard_text(self):
        return self.clipboard


class FakeMic:
    def start(self):
        pass

    def read_frame(self, timeout=0.2):
        return None

    def stop(self):
        pass


class FakeStt:
    error = None


def _make():
    try:
        from voicebridge.voice_edit import overlay as OV
    except Exception as exc:  # pragma: no cover - import environment
        pytest.skip(f"overlay module unavailable: {exc}")
    client = FakeClient()
    desktop = FakeDesktop()
    holder = {}
    try:
        ov = OV.PreviewOverlay(on_action=lambda a, p: holder["h"].on_overlay_action(a, p))
    except Exception as exc:
        pytest.skip(f"no display for tkinter: {exc}")
    helper = dh.DesktopHelper(
        FakeCfg(), client=client, overlay=ov, desktop=desktop,
        mic_factory=lambda: FakeMic(), stt_factory=lambda t: FakeStt(),
        notify=lambda *a: None)
    helper._warm_state = "warm"
    helper._ui_q = None  # run UI callbacks inline on the test (Tk) thread
    helper._deliver_fired_timers = lambda: []
    holder["h"] = helper
    return OV, ov, helper, client, desktop


def _pump(ov, helper, seconds=0.15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ov.root.update()
        time.sleep(0.01)


def _preview(ov, helper):
    ov.show_preview({
        "ok": True, "mode": "preview", "original": "alpha beta gamma delta",
        "proposed": "Alpha beta gamma delta.", "model": "local-test",
        "provider": "local", "privacy_mode": "local", "locality": "local",
        "detected": {"action": "rewrite"}})
    helper._preview = {"mode": "preview", "proposed": "Alpha beta gamma delta.",
                       "original": "alpha beta gamma delta"}
    helper.state = helper.PREVIEW
    helper.request_id = "req-hud"
    _pump(ov, helper)


def test_actions_are_disabled_until_a_preview_exists():
    OV, ov, helper, client, desktop = _make()
    try:
        ov.show()
        _pump(ov, helper)
        for action in ("apply", "copy", "edit", "retry", "revert"):
            assert ov._buttons[action].cget("state") == "disabled", action
        assert ov._buttons["cancel"].cget("state") == "normal"
        _preview(ov, helper)
        for action in ("apply", "copy", "edit", "retry", "revert"):
            assert ov._buttons[action].cget("state") == "normal", action
    finally:
        ov.close()


def test_real_buttons_drive_copy_edit_retry_apply_revert_cancel():
    OV, ov, helper, client, desktop = _make()
    try:
        ov.show()
        _preview(ov, helper)
        assert "+" in ov._diff_stat_var.get()

        client.proposed = helper._preview["proposed"]
        ov._buttons["copy"].invoke()
        _pump(ov, helper)
        assert desktop.clipboard == helper._preview["proposed"]
        assert ov._status_var.get().lower() == "done"
        assert any(c[0] == "request" and c[2].endswith("/result") for c in client.calls)

        ov._buttons["edit"].invoke()
        _pump(ov, helper)
        assert ov._edit_entry.winfo_ismapped()
        assert ov._edit_entry.get("1.0", "end-1c") == helper._preview["proposed"]
        ov._edit_entry.delete("1.0", "end")
        ov._edit_entry.insert("1.0", "EDITED BY HUD")
        ov._submit_edit()
        _pump(ov, helper)
        assert ("edit", "req-hud", "EDITED BY HUD") in client.calls
        assert helper._preview["proposed"] == "EDITED BY HUD"

        ov._buttons["retry"].invoke()
        _pump(ov, helper)
        assert any(c[0] == "retry" for c in client.calls)
        assert helper._preview["proposed"] == "RETRIED TEXT"
        assert "RETRIED" in ov.text.get("1.0", "end-1c")

        ov._buttons["apply"].invoke()
        _pump(ov, helper)
        assert ("apply", "req-hud", True) in client.calls
        assert ov._status_var.get().lower() == "done"

        ov._buttons["revert"].invoke()
        _pump(ov, helper)
        assert ("revert", "req-hud") in client.calls

        # A fresh turn, then Cancel.
        _preview(ov, helper)
        ov._buttons["cancel"].invoke()
        _pump(ov, helper)
        assert any(c[0] == "cancel" for c in client.calls)
        assert helper.state == helper.IDLE
    finally:
        ov.close()


def test_new_turn_clears_the_previous_transcript_and_preview():
    OV, ov, helper, client, desktop = _make()
    try:
        ov.show()
        _preview(ov, helper)
        ov.set_live_transcript("OLD WORDS FROM THE PREVIOUS TURN", final=True)
        _pump(ov, helper)
        assert "OLD WORDS" in ov.live_text.get("1.0", "end-1c")

        # The hotkey path opens the turn and clears the panel: the helper
        # calls set_live_transcript("", final=False) right after capture.
        ov.set_live_transcript("", final=False)
        _pump(ov, helper)
        assert ov.live_text.get("1.0", "end-1c").strip() == ""
        assert "Nothing to review yet" in ov.text.get("1.0", "end-1c")
        assert ov._buttons["apply"].cget("state") == "disabled"
    finally:
        ov.close()


def test_streaming_partials_never_lift_or_re_geometry_the_window():
    OV, ov, helper, client, desktop = _make()
    try:
        ov.show()
        _pump(ov, helper)
        calls = {"geometry": 0, "lift": 0}
        real_geometry, real_lift = ov.root.geometry, ov.root.lift

        def counting_geometry(*args, **kwargs):
            calls["geometry"] += 1
            return real_geometry(*args, **kwargs)

        def counting_lift(*args, **kwargs):
            calls["lift"] += 1
            return real_lift(*args, **kwargs)

        ov.root.geometry = counting_geometry
        ov.root.lift = counting_lift
        for i in range(40):
            ov.set_live_transcript(f"streaming partial {i}", final=(i == 39))
            ov.set_level(0.4 + (i % 5) / 10.0)
            _pump(ov, helper, 0.02)
        assert calls == {"geometry": 0, "lift": 0}
        assert ov.root.state() == "normal"
        assert "streaming partial 39" in ov.live_text.get("1.0", "end-1c")
    finally:
        ov.close()
