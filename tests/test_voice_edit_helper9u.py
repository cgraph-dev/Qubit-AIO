"""Desktop helper state-machine tests (no X, mic, socket, or model)."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.desktop_helper as dh
from voicebridge.voice_edit.schema import SelectionSnapshot


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

    def close(self):
        pass


class FakeClient:
    def __init__(self, *, available=True, mode="preview", estop=False,
                 enabled=True):
        self.calls = []
        self._available = available
        self._mode = mode
        self._estop = estop
        self._enabled = enabled

    def status(self):
        return {"enabled": self._enabled, "estop": self._estop}

    def begin(self):
        self.calls.append(("begin",))
        return {"ok": True, "request_id": "req-1"}

    def capture(self, rid):
        self.calls.append(("capture", rid))
        return {"ok": True, "selection": {
            "available": self._available, "captured_via": "clipboard",
            "app_class": "code"}}

    def mint_stt_ticket(self):
        self.calls.append(("ticket",))
        return "ticket-123"

    def speak(self, text):
        self.calls.append(("speak", text))
        return b""

    def transform(self, rid, transcript):
        self.calls.append(("transform", rid, transcript))
        if self._mode == "dictate":
            return {"ok": True, "mode": "dictate", "transcript": transcript}
        if self._mode == "answer":
            return {"ok": True, "mode": "answer", "question": transcript}
        if self._mode == "ignore":
            return {"ok": True, "mode": "ignore"}
        if self._mode == "clarify":
            return {"ok": True, "mode": "clarify", "question": "Which change?"}
        return {"ok": True, "mode": "preview", "original": "hello world",
                "proposed": "NEW TEXT", "model": "qubit-brain",
                "provider": "ollama", "privacy_mode": "local",
                "detected": {"action": "shorten", "style": ""}}

    def edit(self, rid, text):
        self.calls.append(("edit", rid, text))
        return {"proposed": text}

    def retry(self, rid, transcript=""):
        self.calls.append(("retry", rid))
        return {"mode": "preview", "original": "hello world",
                "proposed": "NEW TEXT", "detected": {"action": "shorten"}}

    def apply(self, rid, *, confirm=True, method=""):
        self.calls.append(("apply", rid, confirm))
        return {"ok": True, "applied": "NEW TEXT"}

    def revert(self, rid):
        self.calls.append(("revert", rid))
        return {"ok": True}

    def cancel(self, rid):
        self.calls.append(("cancel", rid))
        return {"ok": True, "cancelled": True}


class FakeDesktop:
    def __init__(self):
        self.inserted = []
        self.activated = []
        self.clipboard = None

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
        self.activated.append(window_id)
        return True

    def set_clipboard(self, text):
        self.clipboard = text
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


def _helper(monkeypatch, *, final="make it shorter", **client_kw):
    monkeypatch.setattr(dh, "transcribe_once",
                        lambda client, mic, stop, **kw: (
                            kw.get("on_partial") and
                            kw["on_partial"]("make it shor"), final)[1])
    overlay = FakeOverlay()
    client = FakeClient(**client_kw)
    desktop = FakeDesktop()
    helper = dh.DesktopHelper(
        FakeCfg(), client=client, overlay=overlay, desktop=desktop,
        mic_factory=lambda: FakeMic(), stt_factory=lambda t: FakeStt())
    return helper, overlay, client, desktop


def _run(helper, *, hold=True):
    helper.on_hotkey("ptt_start")
    if helper._worker is not None:
        helper._worker.join(timeout=5)
    if hold:
        helper.on_hotkey("ptt_end")


def _join_wake_turn(h, timeout=10):
    """on_wake_command processes async (P0-2 worker thread): wait for it."""
    th = getattr(h, "_wake_thread", None)
    if th is not None:
        th.join(timeout=timeout)


def test_helper_disabled_refuses(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch)
    h.cfg = FakeCfg({"voice_edit": {"enabled": False}})
    assert h.start() is False


def test_helper_selection_flow_shows_preview(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch)
    h._start_listening()
    h._worker.join(timeout=5)
    assert overlay.previews and overlay.previews[-1]["proposed"] == "NEW TEXT"
    assert ("capture", "req-1") in client.calls
    assert ("transform", "req-1", "make it shorter") in client.calls
    assert desktop.inserted == []


def test_wake_command_runs_the_same_session_and_transform(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch)
    h.on_wake_command("type this for me: hello world")
    _join_wake_turn(h)
    assert ("begin",) in client.calls
    # P0 2026-10-03: the wake turn must NEVER hit the service capture
    # (it synthesizes Ctrl+C into the focused window = SIGINT in a
    # terminal). Local focus snapshot only; the turn still routes.
    assert ("capture", "req-1") not in client.calls
    assert ("transform", "req-1", "type this for me: hello world") in client.calls
    # Wake dictation flows through the same path: the service returns dictate.
    h2, overlay2, client2, desktop2 = _helper(monkeypatch, available=False,
                                              mode="dictate",
                                              final="")
    h2.on_wake_command("hello world")
    _join_wake_turn(h2)
    assert desktop2.inserted == ["hello world"]


def test_wake_command_ignored_while_busy(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch)
    h.state = h.LISTENING
    h.on_wake_command("type this for me: nope")
    assert ("begin",) not in client.calls


def test_helper_apply_inserts(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch)
    h._start_listening()
    h._worker.join(timeout=5)
    h.on_overlay_action("apply", {})
    assert ("apply", "req-1", True) in client.calls
    assert desktop.activated == ["42"]


def test_helper_question_reaches_brain_not_the_field(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch, available=False,
                                          mode="answer",
                                          final="what's my name")
    client.ask = lambda text, rid="": "Your name is Trick."
    h._start_listening()
    h._worker.join(timeout=5)
    # The question must NOT be typed into the field.
    assert desktop.inserted == []
    # The answer is shown in the preview/overlay.
    assert overlay.previews and any(
        "Trick" in str(p.get("proposed", "")) for p in overlay.previews)


def test_helper_dictation_still_inserts_non_question(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch, available=False,
                                          mode="dictate",
                                          final="hello there friend")
    client.ask = lambda text, rid="": "SHOULD NOT BE CALLED"
    h._start_listening()
    h._worker.join(timeout=5)
    assert desktop.inserted == ["hello there friend"]


def test_helper_no_selection_dictates(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch, available=False,
                                          mode="dictate",
                                          final="hello dictated words")
    h._start_listening()
    h._worker.join(timeout=5)
    assert desktop.inserted == ["hello dictated words"]


def test_helper_dictation_refuses_fullscreen(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch, available=False,
                                          mode="dictate", final="hello there")
    desktop.is_fullscreen = lambda window_id="": True
    h._start_listening()
    h._worker.join(timeout=5)
    assert desktop.inserted == []
    assert desktop.clipboard == "hello there"  # copied instead of injected
    assert any(s[0] == "error" for s in overlay.statuses)


def test_helper_clarify_does_not_insert(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch, mode="clarify")
    h._start_listening()
    h._worker.join(timeout=5)
    assert desktop.inserted == []
    assert any(s[0] == "clarify" for s in overlay.statuses)


def test_helper_escape_cancels(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch)
    h._start_listening()
    h._worker.join(timeout=5)
    h.on_hotkey("cancel")
    assert any(c[0] == "cancel" for c in client.calls)


def test_helper_estop_refuses(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch, estop=True)
    h._start_listening()
    assert h._worker is None
    assert any(s[0] == "error" for s in overlay.statuses)


def test_helper_toggle_mode(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch)
    h.toggle = True
    h.on_hotkey("ptt_start")
    h._worker.join(timeout=5)
    assert h.state == dh.DesktopHelper.PREVIEW
    # second press cancels
    h.on_hotkey("ptt_start")
    assert any(c[0] == "cancel" for c in client.calls)


def test_helper_copy_action(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch)
    # add the result endpoint behaviour
    client._request = lambda method, path, payload=None, timeout=90: {
        "proposed": "NEW TEXT"}
    h._start_listening()
    h._worker.join(timeout=5)
    h.on_overlay_action("copy", {})
    assert desktop.clipboard == "NEW TEXT"


def test_helper_retry(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch)
    h._start_listening()
    h._worker.join(timeout=5)
    h.on_overlay_action("retry", {})
    assert any(c[0] == "retry" for c in client.calls)


def test_helper_revert(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch)
    h._start_listening()
    h._worker.join(timeout=5)
    h.on_overlay_action("revert", {})
    assert any(c[0] == "revert" for c in client.calls)


# -- accurate-model warm readiness (no real model) ---------------------------

def _warm_helper(monkeypatch, *, state="pending", timeout=0.2, mode="accurate",
                 event_set=False):
    cfg = FakeCfg({"voice_edit": {"enabled": True, "stt_mode": mode,
                                  "warm_timeout_s": timeout}})
    overlay = FakeOverlay()
    client = FakeClient()
    desktop = FakeDesktop()
    h = dh.DesktopHelper(
        cfg, client=client, overlay=overlay, desktop=desktop,
        mic_factory=lambda: FakeMic(), stt_factory=lambda t: FakeStt())
    # Injectable seams: a test sets the event/state directly (no model).
    h._warm_state = state
    h._warm_event = threading.Event()
    if event_set:
        h._warm_event.set()
    return h, overlay


def _warming(overlay):
    return [s for s in overlay.statuses if s[0] == "warming"]


def test_open_request_returns_immediately_when_already_warm(monkeypatch):
    h, overlay = _warm_helper(monkeypatch, state="pending", timeout=5.0,
                              event_set=True)
    t0 = time.monotonic()
    assert h._open_request() is True
    assert time.monotonic() - t0 < 0.5
    assert _warming(overlay) == []


def test_open_request_waits_for_warm_event_then_returns(monkeypatch):
    h, overlay = _warm_helper(monkeypatch, state="pending", timeout=5.0,
                              event_set=False)

    def _set_soon():
        time.sleep(0.05)
        h._warm_event.set()

    threading.Thread(target=_set_soon, daemon=True).start()
    t0 = time.monotonic()
    assert h._open_request() is True
    elapsed = time.monotonic() - t0
    assert 0.02 <= elapsed < 2.0
    assert _warming(overlay)


def test_open_request_warm_timeout_proceeds_with_cue(monkeypatch):
    h, overlay = _warm_helper(monkeypatch, state="pending", timeout=0.2,
                              event_set=False)
    t0 = time.monotonic()
    assert h._open_request() is True
    assert time.monotonic() - t0 < 2.0
    # The user is not left blind: a warming cue was shown before proceeding.
    assert _warming(overlay)


def test_open_request_no_wait_when_unavailable_or_skipped(monkeypatch):
    for state in ("unavailable", "skipped"):
        h, overlay = _warm_helper(monkeypatch, state=state, timeout=5.0,
                                  event_set=False)
        t0 = time.monotonic()
        assert h._open_request() is True
        assert time.monotonic() - t0 < 0.5
        assert _warming(overlay) == []


def test_open_request_streaming_mode_does_not_wait(monkeypatch):
    h, overlay = _warm_helper(monkeypatch, state="pending", timeout=5.0,
                              mode="streaming", event_set=False)
    t0 = time.monotonic()
    assert h._open_request() is True
    assert time.monotonic() - t0 < 0.5
    assert _warming(overlay) == []


def test_start_heartbeat_reports_stt_warm(monkeypatch):
    import voicebridge.voice_edit.heartbeat as hb
    import voicebridge.voice_edit.asr_accurate as acc
    captured = {}
    monkeypatch.setattr(hb, "write", lambda **kw: captured.update(kw))
    monkeypatch.setattr(acc, "is_available", lambda cfg: False)
    monkeypatch.setattr(dh.DesktopHelper, "_tame_key_repeat", lambda self: None)
    h, overlay = _warm_helper(monkeypatch, state="pending", timeout=5.0)
    assert h.start() is True
    try:
        extra = captured.get("extra", {})
        assert "stt_warm" in extra
        assert extra["stt_warm"] in ("pending", "warming", "warm",
                                     "unavailable", "skipped")
    finally:
        h.shutdown()



def test_wake_command_shows_heard_text_preview(monkeypatch):
    # Wake had no live transcript surface: Right-Ctrl streams interim
    # partials into the status line, but a wake turn jumped straight to
    # the answer. The heard words must appear the moment they arrive.
    h, overlay, client, desktop = _helper(monkeypatch)
    h.on_wake_command("Hey Qubit, what is the weather?")
    _join_wake_turn(h)
    heard = [d for s, d in overlay.statuses if "Hey Qubit" in d]
    assert heard, overlay.statuses
    assert "weather" in heard[0]
    # Default (verbatim off): routing receives the transcript as heard.
    assert ("transform", "req-1",
            "Hey Qubit, what is the weather?") in client.calls


def test_wake_command_preview_honours_verbatim(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch)
    h.cfg = FakeCfg({"voice_edit": {"enabled": True,
                                    "verbatim_transcript": True}})
    h.on_wake_command("Hey Qubit, what is the weather?")
    _join_wake_turn(h)
    heard = [d for s, d in overlay.statuses if "hey qubit" in d]
    assert heard, overlay.statuses
    assert ("transform", "req-1",
            "hey qubit what is the weather") in client.calls


def test_verbatim_transcript_normalization():
    # Chrome-style: exactly what was heard — lowercase, no punctuation,
    # words and numbers kept, diacritics preserved.
    assert dh.verbatim_transcript("Hey Qubit, Type THIS: Hello, World!",
                                  True) == "hey qubit type this hello world"
    assert dh.verbatim_transcript("1 plus 1 equals 2.", True) == \
        "1 plus 1 equals 2"
    assert dh.verbatim_transcript("don't stop", True) == "don't stop"
    assert dh.verbatim_transcript("București, vreme?", True) == \
        "bucurești vreme"
    assert dh.verbatim_transcript("", True) == ""
    # Disabled (the product default) -> input untouched.
    assert dh.verbatim_transcript("Hey Qubit!") == "Hey Qubit!"
    assert dh.verbatim_transcript("Hey Qubit!", False) == "Hey Qubit!"


def test_verbatim_setting_round_trips(tmp_path):
    from voicebridge.voice_edit import settings as settings_mod
    p = tmp_path / "voice.yaml"
    p.write_text("voice_edit:\n  enabled: true\n")

    class Cfg:
        def __init__(self):
            self.data = {"voice_edit": {"enabled": True}}
            self.path = p

        def get(self, *path, default=None):
            node = self.data
            for seg in path:
                if isinstance(node, dict) and seg in node:
                    node = node[seg]
                else:
                    return default
            return node

    cfg = Cfg()
    assert settings_mod.settings_view(cfg)["verbatim_transcript"] is False
    settings_mod.update_voice_edit(cfg, {"verbatim_transcript": True})
    assert settings_mod.settings_view(cfg)["verbatim_transcript"] is True


def test_propose_task_arms_wake_for_yes(monkeypatch):
    # Staging "say yes" is useless if the mic isn't waiting: the arm
    # window from the original wake expired during brain latency.
    h, overlay, client, desktop = _helper(monkeypatch)
    armed = []
    h._wake = type("W", (), {"expect_reply": lambda self, **kw: armed.append(kw)})()
    h._propose_task({"task_text": "open a text document", "summary": "s"})
    assert armed, "wake must be re-armed when asking for confirmation"
    assert any("yes" in d.lower() for _, d in overlay.statuses), overlay.statuses


def test_clarify_arms_wake_for_answer(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch, mode="clarify")
    armed = []
    h._wake = type("W", (), {"expect_reply": lambda self, **kw: armed.append(kw)})()
    h._process_transcript("make this")
    assert armed, "a clarify question must also wait for the answer"


def test_affirmative_variants():
    assert DesktopHelper_affirmative("yup")
    assert DesktopHelper_affirmative("nah") is False


def DesktopHelper_affirmative(text):
    import voicebridge.desktop_helper as _dh
    return _dh.DesktopHelper._affirmative(text)


def test_answer_failure_resets_state_to_idle(monkeypatch):
    # P0-1: a failed ask left the helper in PROCESSING forever — wake
    # busy_skips forever and Right-Ctrl refuses. Both voice paths bricked
    # until helper restart.
    from voicebridge.desktop_helper import HelperError
    h, overlay, client, desktop = _helper(monkeypatch)
    def _boom(text, request_id):
        raise HelperError("VOICE_EDIT_ASK_FAILED", "brain down")
    client.ask_result = _boom
    h.state = h.PROCESSING
    h._answer_question("will this work", mode="answer")
    assert h.state == h.IDLE, h.state
    assert overlay.errors, "the failure must still surface"


def test_pending_task_expires(monkeypatch):
    # P0-3: a staged confirm lived forever — "yes" hours later in an
    # unrelated context would execute a stale destructive-tier task.
    import time as _t
    h, overlay, client, desktop = _helper(monkeypatch)
    ran = []
    client.task = lambda text, **kw: ran.append(text) or {"ok": True}
    h._pending_task = {"text": "delete everything", "request_id": "old",
                       "staged_at": _t.monotonic() - 9999.0}
    h._process_transcript("yes")
    assert ran == [], "a stale confirmation must never execute"
    assert h._pending_task == {}, "expiry must clear the stage"


def test_wake_turn_runs_off_monitor_thread(monkeypatch):
    # P0-2: the whole wake turn (HTTP + model + blocking TTS + agent)
    # ran inside WakeMonitor._loop's thread — the detector was blind for
    # the entire turn, exactly when §71 asks the user to answer.
    import time as _t
    import threading as _th
    h, overlay, client, desktop = _helper(monkeypatch, mode="answer")
    def _asked(text, rid):
        client.calls.append(("ask_result", text))
        return {"answer": "done", "spoken": True}
    client.ask_result = _asked
    t0 = _t.monotonic()
    h.on_wake_command("hey qubit what time is it")
    dt = _t.monotonic() - t0
    assert dt < 2.0, f"on_wake_command blocked {dt:.1f}s on the caller thread"
    for th in list(_th.enumerate()):
        if th.name == "voice-wake-turn":
            th.join(timeout=10)
    assert any(c[0] == "ask_result" for c in client.calls), client.calls


def test_yes_with_comma_confirms():
    # P1-5: whisper emits "Yes, please." — inner comma missed the exact
    # set and the staged task was silently discarded as a new command.
    import voicebridge.desktop_helper as _dh
    assert _dh.DesktopHelper._affirmative("Yes, please.")
    assert _dh.DesktopHelper._affirmative("No, thanks.") is False
    assert _dh.DesktopHelper._negative("No, thanks.")


def test_recent_tts_speak_detects_service_tts(tmp_path):
    # Own-TTS echo guard: a recent service/helper speak event means the
    # room audio is our own reply, not a directed command.
    import json as _json
    import time as _t

    import voicebridge.desktop_helper as _dh
    p = tmp_path / "events.jsonl"
    now = _t.time()
    p.write_text(_json.dumps({"event": "tts.speak", "ts": now - 1.0}) + "\n")
    assert _dh.recent_tts_speak(p, window_s=5.0) is True
    p.write_text(_json.dumps({"event": "tts.speak", "ts": now - 60.0}) + "\n")
    assert _dh.recent_tts_speak(p, window_s=5.0) is False
    assert _dh.recent_tts_speak(tmp_path / "missing.jsonl",
                                window_s=5.0) is False


def test_recent_tts_speak_ignores_wake_commands(tmp_path):
    import json as _json
    import time as _t

    import voicebridge.desktop_helper as _dh
    p = tmp_path / "events.jsonl"
    p.write_text(_json.dumps(
        {"event": "voice_edit.wake.command", "ts": _t.time() - 1.0,
         "command": "open youtube"}) + "\n")
    assert _dh.recent_tts_speak(p, window_s=5.0) is False


def test_wake_pending_probe_sees_fresh_stage(monkeypatch):
    # Lane 1 (2026-10-03): the wake loop's bare-confirm precheck reads the
    # helper's staged confirmation through this probe (e.g. the timer).
    import time as _t
    h, overlay, client, desktop = _helper(monkeypatch)
    assert h._wake_has_live_pending() is False
    h._pending_task = {"text": "set a timer", "request_id": "r1",
                       "staged_at": _t.monotonic()}
    assert h._wake_has_live_pending() is True


def test_wake_pending_probe_expired_and_malformed(monkeypatch):
    # Expired or malformed stages read as no-pending (never a settle);
    # the probe never raises and never consumes the stage.
    import time as _t
    h, overlay, client, desktop = _helper(monkeypatch)
    h._pending_task = {"text": "delete everything", "request_id": "old",
                       "staged_at": _t.monotonic() - 9999.0}
    assert h._wake_has_live_pending() is False
    assert h._pending_task != {}, "the probe must not consume the stage"
    h._pending_task = {"text": "x"}  # no staged_at
    assert h._wake_has_live_pending() is False
    h._pending_task = {}
    assert h._wake_has_live_pending() is False


def test_wake_hint_wired_to_heard_status_path(monkeypatch):
    # 2026-10-03 (silent-discard fix): start() must wire the wake
    # monitor's on_hint hook to the heard-preview status line, and the
    # hook must be status-line-only (no beep/speak/emit). A monitor
    # built without the hook stays journal-only and never raises.
    import voicebridge.voice_edit.wake as wake_mod
    captured = {}
    real = wake_mod.WakeMonitor

    class _Cap(real):
        def __init__(self, *a, **kw):
            captured.update(kw)
            super().__init__(*a, **kw)

        def start(self):
            return True  # no live mic thread in tests

    monkeypatch.setattr(wake_mod, "WakeMonitor", _Cap)
    import voicebridge.voice_edit.heartbeat as hb
    monkeypatch.setattr(hb, "write", lambda **kw: None)
    monkeypatch.setattr(dh.DesktopHelper, "_tame_key_repeat",
                        lambda self: None)
    h, overlay, client, desktop = _helper(monkeypatch)
    try:
        assert h.start() is True
        hint = captured.get("on_hint")
        assert callable(hint), captured.keys()
        hint("heard: Redo this text for me — say hey qubit first")
        assert ("heard",
                "heard: Redo this text for me — say hey qubit first") \
            in overlay.statuses
        # Status line only: nothing spoken, no turn staged, no state move.
        assert client.calls == []
        assert h.state == h.IDLE
    finally:
        h.shutdown()
    # None-hook still safe: a command-shaped discard with no hook wired
    # emits nothing, posts nothing, and never raises.
    commands = []
    m = real(FakeCfg(), on_command=commands.append,
             is_busy=lambda: False,
             transcribe=lambda pcm: "Redo this text for me.")
    m._handle_segment(b"\x00" * 9600)
    assert commands == []


def test_bare_stop_phrase_set():
    assert dh.is_bare_stop("Stop.")
    assert dh.is_bare_stop("stop it")
    assert dh.is_bare_stop("Never mind.")
    assert not dh.is_bare_stop("stop the brain")
    assert not dh.is_bare_stop("stop brain")


def test_bare_stop_interrupts_instead_of_staging(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch)
    h.state = h.PROCESSING
    h._process_transcript("Stop.")
    assert h.state == h.IDLE
    assert h._turn_stopped_seq == h._turn_seq
    assert not any(c[0] == "transform" for c in client.calls), client.calls
    assert not any(c[0] == "task" for c in client.calls), client.calls


# -- wake after a reply / stale capture (owner 2026-10-29) -------------------

def test_recent_tts_speak_ends_at_playback_stop(tmp_path, monkeypatch):
    # The full 5 s window measured from the START of a short reply made the
    # first "Hey Qubit" after it look dead. A journaled playback END cuts
    # the guard to a short echo tail.
    import json as _json
    import time as _t

    fixed = _t.time()
    monkeypatch.setattr(dh.time, "time", lambda: fixed)
    p = tmp_path / "events.jsonl"

    def write(*events):
        p.write_text("".join(_json.dumps(e) + "\n" for e in events))

    write({"event": "voice_edit.helper.speak", "ts": fixed - 4.0},
          {"event": "voice_edit.helper.speak_done", "ts": fixed - 2.0})
    assert dh.recent_tts_speak(p, window_s=5.0, tail_s=0.6) is False

    write({"event": "voice_edit.helper.speak", "ts": fixed - 4.0})
    # The helper's own playback is gated by ``_speaking``; its journal START
    # must not hold the generic echo guard for the full 5 s window.
    assert dh.recent_tts_speak(p, window_s=5.0) is False

    write({"event": "voice_edit.helper.speak", "ts": fixed - 4.0},
          {"event": "voice_edit.talk.playback.stopped", "ts": fixed})
    assert dh.recent_tts_speak(p, window_s=5.0, tail_s=0.6) is True


def test_wake_busy_clears_after_journaled_playback_stop(tmp_path, monkeypatch):
    import json as _json
    import time as _t
    import voicebridge.config as config_mod

    fixed = _t.time()
    monkeypatch.setattr(dh.time, "time", lambda: fixed)
    monkeypatch.setattr(config_mod, "canonical_state_dir", lambda: tmp_path)
    p = tmp_path / "events.jsonl"
    p.write_text("".join(_json.dumps(e) + "\n" for e in (
        {"event": "voice_edit.helper.speak", "ts": fixed - 4.0},
        {"event": "voice_edit.helper.speak_done", "ts": fixed - 2.0})))

    h, overlay, client, desktop = _helper(monkeypatch)
    h._tts_echo_checked = 0.0
    assert h._wake_busy() is False

    p.write_text(_json.dumps(
        {"event": "voice_edit.helper.speak", "ts": fixed - 1.0}) + "\n")
    h._tts_echo_checked = 0.0
    # ``_speaking`` owns the helper's own playback; the journal START alone is
    # not a wake-busy signal once the reply has ended.
    assert h._wake_busy() is False


def test_wake_preempts_a_stale_listening_capture(monkeypatch):
    # Live journal: wake.turn_refused state=listening right after a key-down
    # capture waiting on the warm model — the fully transcribed wake command
    # was lost and the owner had to repeat it. A directed wake now takes the
    # microphone back from the stale capture.
    h, overlay, client, desktop = _helper(monkeypatch)
    h._active = True
    h.state = h.LISTENING
    h.request_id = "old-request"
    h.on_wake_command("type this for me: hello world")
    _join_wake_turn(h)
    assert ("cancel", "old-request") in client.calls, client.calls
    assert h.state == h.PREVIEW, h.state
    assert ("begin",) in client.calls


def test_wake_command_after_reply_is_accepted(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch)
    h.state = h.PREVIEW
    h._speaking = False
    monkeypatch.setattr(h, "_tts_echo_busy", lambda: False)
    h.on_wake_command("type this for me: hello world")
    _join_wake_turn(h)
    assert ("begin",) in client.calls
    assert h.state == h.PREVIEW, h.state


def test_stt_error_clears_pending_and_returns_idle(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch)
    h._pending_task = {"text": "delete everything", "request_id": "r",
                       "staged_at": time.monotonic()}
    h._on_stt_error("VOICE_EDIT_NO_TRANSCRIPT", "empty")
    assert h._pending_task == {}
    assert h.state == h.IDLE
    assert overlay.errors


def test_errored_wake_turn_never_stays_non_idle(monkeypatch):
    h, overlay, client, desktop = _helper(monkeypatch)
    h._pending_task = {"text": "delete everything", "request_id": "r",
                       "staged_at": time.monotonic()}

    def _boom(_cmd=""):
        raise RuntimeError("boom")

    monkeypatch.setattr(h, "_process_transcript", _boom)
    h.on_wake_command("type this for me: hello world")
    _join_wake_turn(h)
    assert h.state == h.IDLE, h.state
    assert h._pending_task == {}
