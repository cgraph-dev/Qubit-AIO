"""Continuous ("keep listening") wake mode tests (hermetic).

Goal: after Qubit executes a command, it can accept a FOLLOW-UP command for a
bounded window without the user repeating "hey qubit" — layered on the existing
arm mechanism, without breaking the single-turn / confirmation behavior.

Contract pinned here:
* ``voice_edit.continuous_mode`` (default OFF) + ``continuous_window_s``
  (default 15 s, clamped) + ``continuous_max_followups`` (default 5, clamped);
* a follow-up inside the window routes with no wake;
* the window expires -> the next command requires a wake;
* the follow-up budget is bounded; a spent run locks until a fresh wake;
* a live confirmation still only accepts yes/no (the pending guard owns it);
* media/own-voice while Qubit is speaking is not accepted;
* continuous OFF is byte-identical to today;
* nothing ever raises on junk config/listener/args.

No X, mic, socket, model, or service.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.desktop_helper as dh
from voicebridge.voice_edit.wake import WakeMonitor


class FakeCfg:
    def __init__(self, data=None):
        self._data = data or {"voice_edit": {"enabled": True}}

    def get(self, *path, default=None):
        node = self._data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node


def _wake_cfg(**voice_edit):
    base = {"enabled": True, "wake": {"enabled": True, "engine": "asr"}}
    base.update(voice_edit)
    return FakeCfg({"voice_edit": base})


def _wake_monitor(cfg, commands):
    return WakeMonitor(cfg, on_command=commands.append,
                       is_busy=lambda: False,
                       transcribe=lambda pcm: "")


# ---------------------------------------------------------------------------
# Setting: defaults + clamps
# ---------------------------------------------------------------------------
def test_continuous_settings_default_off():
    assert dh.continuous_settings(FakeCfg()) == (False, 15.0, 5)


def test_continuous_settings_high_values_clamped():
    cfg = FakeCfg({"voice_edit": {"continuous_mode": True,
                                  "continuous_window_s": 999,
                                  "continuous_max_followups": 99}})
    on, window, maxf = dh.continuous_settings(cfg)
    assert on is True
    assert window == 120.0
    assert maxf == 20


def test_continuous_settings_low_values_clamped():
    cfg = FakeCfg({"voice_edit": {"continuous_window_s": 0.1,
                                  "continuous_max_followups": 0}})
    on, window, maxf = dh.continuous_settings(cfg)
    assert on is False
    assert window == 3.0
    assert maxf == 1


def test_continuous_settings_never_raises_on_bad_config():
    class Bad:
        def get(self, *p, default=None):
            raise RuntimeError("boom")

    assert dh.continuous_settings(Bad()) == (False, 15.0, 5)
    assert dh.continuous_settings(None) == (False, 15.0, 5)


# ---------------------------------------------------------------------------
# WakeMonitor primitives
# ---------------------------------------------------------------------------
def test_followup_within_window_routes_without_wake():
    commands = []
    m = _wake_monitor(_wake_cfg(), commands)
    m.expect_continuous(15.0, 5)
    assert m.continuous_active() is True
    m._handle_transcript("open youtube")   # no wake phrase
    assert commands == ["open youtube"]


def test_expired_window_requires_a_wake():
    commands = []
    m = _wake_monitor(_wake_cfg(), commands)
    m.expect_continuous(15.0, 5)
    m._handle_transcript("open youtube")
    m._armed_until = 0.0               # window expired
    m._handle_transcript("open gmail")  # bare -> discarded
    assert commands == ["open youtube"]


def test_followup_budget_is_bounded_then_locks():
    commands = []
    m = _wake_monitor(_wake_cfg(), commands)
    m.expect_continuous(15.0, 2)
    m._handle_transcript("one")
    m._handle_transcript("two")
    assert commands == ["one", "two"]
    assert m.continuous_locked() is True
    assert m.continuous_active() is False
    m._handle_transcript("three")       # spent -> needs a fresh wake
    assert commands == ["one", "two"]


def test_rearm_keeps_budget_and_extends_window():
    commands = []
    m = _wake_monitor(_wake_cfg(), commands)
    m.expect_continuous(15.0, 2)
    m._handle_transcript("one")
    m._armed_until = 0.0               # long turn consumed the first window
    m.rearm_continuous()
    assert m._armed_until > time.monotonic()
    m._handle_transcript("two")
    assert commands == ["one", "two"]


def test_fresh_wake_resets_spent_run():
    commands = []
    m = _wake_monitor(_wake_cfg(), commands)
    m.expect_continuous(15.0, 1)
    m._handle_transcript("one")
    assert m.continuous_locked() is True
    m._handle_transcript("hey qubit open youtube")
    assert m.continuous_locked() is False
    assert commands == ["one", "open youtube"]


def test_expect_reply_resets_continuous_run():
    # A confirmation/clarify arm is NOT a continuous run: the "yes" that
    # answers it must not consume the follow-up budget.
    m = _wake_monitor(_wake_cfg(), [])
    m.expect_continuous(15.0, 5)
    assert m.continuous_active() is True
    m.expect_reply()
    assert m.continuous_active() is False
    assert m.continuous_locked() is False


def test_continuous_off_followup_requires_wake():
    commands = []
    m = _wake_monitor(_wake_cfg(), commands)  # never armed continuous
    m._handle_transcript("open youtube")
    assert commands == []


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
def test_pending_confirmation_only_accepts_yes_no():
    commands = []
    m = WakeMonitor(_wake_cfg(), on_command=commands.append,
                    is_busy=lambda: False, transcribe=lambda pcm: "",
                    pending_probe=lambda: True)
    m.expect_continuous(15.0, 5)
    m._handle_transcript("open youtube")   # not yes/no -> pending guard keeps it
    assert commands == []
    m._handle_transcript("yes")            # bare affirmation settles
    assert commands == ["yes"]


def test_media_while_speaking_is_not_accepted():
    commands = []
    m = WakeMonitor(_wake_cfg(), on_command=commands.append,
                    is_busy=lambda: True, transcribe=lambda pcm: "")
    m.expect_continuous(15.0, 5)
    media = ("I'm Qubit thank you for watching I hope you enjoyed this video "
             "see you in the next one bye bye now")
    m._handle_transcript(media)            # long non-command media -> dropped
    assert commands == []
    m._handle_transcript("open youtube")   # the arm was kept for a real command
    assert commands == ["open youtube"]


def test_continuous_never_raises_on_junk():
    class Junk:
        def get(self, *p, default=None):
            raise RuntimeError("boom")

    m = WakeMonitor(Junk(), on_command=lambda c: None, is_busy=lambda: False)
    m.expect_continuous(None, "not-an-int")
    m.rearm_continuous()
    assert m.continuous_active() in (True, False)
    assert m.continuous_locked() in (True, False)
    m._handle_transcript(object())         # junk transcript
    assert dh.continuous_settings(Junk()) == (False, 15.0, 5)


# ---------------------------------------------------------------------------
# DesktopHelper wiring (turn lifecycle)
# ---------------------------------------------------------------------------
class FakeOverlay:
    def __init__(self):
        self.statuses = []
        self.previews = []
        self.errors = []

    def set_status(self, state, detail=""):
        self.statuses.append((state, detail))

    def show_preview(self, payload):
        self.previews.append(payload)

    def show_error(self, code, detail):
        self.errors.append((code, detail))

    def show(self):
        pass

    def hide(self):
        pass

    def close(self):
        pass


class FakeDesktop:
    def __init__(self):
        self.inserted = []

    def session(self):
        return {"session_type": "x11", "x11": True, "wayland": False}

    def focused_window(self):
        return {"window_id": "42", "app_class": "code", "app_name": "code",
                "window_title": ""}

    def insert_text(self, text, snapshot, cfg=None, method=""):
        from voicebridge.voice_edit.schema import InsertionResult
        self.inserted.append(text)
        return InsertionResult(ok=True, method="clipboard_paste",
                               app_class="code", focus_restored=True)

    def activate_window(self, window_id):
        return True

    def set_clipboard(self, text):
        return True

    def is_fullscreen(self, window_id=""):
        return False


class FakeMic:
    def start(self):
        pass

    def read_frame(self, timeout=0.2):
        return None

    def stop(self):
        pass


class FakeStt:
    error = None


class FakeClient:
    def __init__(self, routes=None):
        self.calls = []
        self.runs = []
        self._routes = routes or {}
        self._seq = 0

    def status(self):
        return {"enabled": True, "estop": False}

    def begin(self, session_id=""):
        self._seq += 1
        return {"ok": True, "request_id": f"req-{self._seq}"}

    def capture(self, rid):
        return {"ok": True, "selection": {"available": False,
                                          "captured_via": "clipboard",
                                          "app_class": "code"}}

    def speak(self, text):
        return b""

    def route(self, rid, text):
        low = (text or "").lower()
        for key, out in self._routes.items():
            if key in low:
                return dict(out)
        return {"ok": True, "mode": "answer", "question": text}

    def task(self, text, confirm=True, request_id=""):
        self.runs.append(text)
        return {"ok": True, "reply": f"ran: {text}"}

    def ask(self, text, rid=""):
        return f"answer: {text}"

    def ask_result(self, text, rid=""):
        return {"answer": f"answer: {text}", "spoken": True}

    def cancel(self, rid):
        return {"ok": True, "cancelled": True}


def _helper(cfg=None, routes=None):
    overlay = FakeOverlay()
    client = FakeClient(routes=routes)
    desktop = FakeDesktop()
    h = dh.DesktopHelper(
        cfg or FakeCfg(), client=client, overlay=overlay, desktop=desktop,
        mic_factory=lambda: FakeMic(), stt_factory=lambda t: FakeStt())
    return h, overlay, client, desktop


def test_helper_continuous_off_is_inert():
    h, overlay, client, desktop = _helper()
    commands = []
    wake = _wake_monitor(h.cfg, commands)
    h._wake = wake
    h._finish_error()
    assert wake.continuous_active() is False
    assert wake.continuous_locked() is False


def test_helper_arms_continuous_after_turn_when_enabled():
    cfg = _wake_cfg(continuous_mode=True)
    h, overlay, client, desktop = _helper(cfg=cfg)
    commands = []
    wake = _wake_monitor(cfg, commands)
    h._wake = wake
    h._finish_error()
    assert wake.continuous_active() is True
    # A bare follow-up then routes with no wake.
    wake._handle_transcript("open youtube")
    assert commands == ["open youtube"]


def test_helper_does_not_arm_over_a_pending_confirmation():
    cfg = _wake_cfg(continuous_mode=True)
    h, overlay, client, desktop = _helper(cfg=cfg)
    commands = []
    wake = _wake_monitor(cfg, commands)
    h._wake = wake
    h._pending_task = {"text": "delete everything", "request_id": "r",
                       "staged_at": time.monotonic()}
    h._finish_error()
    assert wake.continuous_active() is False


def test_helper_followups_share_one_budget():
    cfg = _wake_cfg(continuous_mode=True, continuous_window_s=15.0,
                    continuous_max_followups=2)
    h, overlay, client, desktop = _helper(cfg=cfg)
    commands = []
    wake = _wake_monitor(cfg, commands)
    h._wake = wake
    h._finish_error()                  # fresh run, budget 2
    assert wake.continuous_active() is True
    wake._handle_transcript("open youtube")
    h._finish_error()                  # rearm keeps the budget
    assert wake.continuous_active() is True
    wake._handle_transcript("open gmail")
    h._finish_error()                  # spent -> helper must not restart it
    assert wake.continuous_locked() is True
    assert wake.continuous_active() is False
    wake._handle_transcript("open maps")
    assert commands == ["open youtube", "open gmail"]


def test_helper_run_task_success_arms_continuous():
    cfg = _wake_cfg(continuous_mode=True)
    routes = {"delete": {"mode": "task", "task_text": "delete everything"}}
    h, overlay, client, desktop = _helper(cfg=cfg, routes=routes)
    commands = []
    wake = _wake_monitor(cfg, commands)
    h._wake = wake
    h._process_transcript("delete everything")   # stages a confirmation
    assert not wake.continuous_active(), "must not arm over a pending"
    h._process_transcript("yes")                 # runs the task
    assert client.runs == ["delete everything"]
    assert wake.continuous_active() is True


def test_helper_busy_gate_covers_working_and_speaking():
    # The wake loop yields while this is True, so a continuous follow-up can
    # never overlap Qubit working or talking.
    h, overlay, client, desktop = _helper()
    h.state = h.PROCESSING
    assert h._wake_busy() is True
    h.state = h.IDLE
    h._speaking = True
    assert h._wake_busy() is True


def test_helper_never_raises_with_broken_wake_listener():
    cfg = _wake_cfg(continuous_mode=True)
    h, overlay, client, desktop = _helper(cfg=cfg)

    class BrokenWake:
        def continuous_locked(self):
            raise RuntimeError("boom")

        def continuous_active(self):
            raise RuntimeError("boom")

        def expect_continuous(self, *a, **k):
            raise RuntimeError("boom")

        def rearm_continuous(self):
            raise RuntimeError("boom")

    h._wake = BrokenWake()
    h._finish_error()   # must not raise
    assert h.state == h.IDLE
