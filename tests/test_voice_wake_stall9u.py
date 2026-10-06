"""Wake "worked once then stopped" — media false-fire + fullscreen stall.

Journal evidence (2026-10-06):

* A YouTube outro playing through the room speakers
  ("I'm Qubit. Thank you for watching. I hope you enjoyed. I'll see you in the
  next video. Bye! Bye!") reached the wake path WHILE the helper's
  ``_await_reply`` had armed the listener for a spoken follow-up. The armed
  branch executes any non-wake text as the command, so the 19-word outro was
  emitted (journal ``voice_edit.wake.command`` 96 chars), the media guard
  missed it (``_MEDIA_WORD_LIMIT`` was 20), the arm was consumed, and the
  outro staged itself as a new confirmation — after which real "hey qubit"
  attempts were swallowed by the pending guard. That is the false fire that
  starts the cascade.
* Separately the wake loop stalled for 20+ minutes (``busy_skip`` every 30 s)
  with the helper otherwise idle and zero TTS events in the window: the only
  wake-only busy component left was ``_fullscreen_active``. A focused
  fullscreen game/video disabled listening entirely while Right-Ctrl — which
  has no fullscreen gate — kept working.

These tests pin both fixes. Hermetic: no X, mic, socket, model or service.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voicebridge.desktop_helper as dh
from voicebridge.voice_edit import wake as wake_mod
from voicebridge.voice_edit.wake import WakeMonitor, _is_media_like

#: The exact journal outro: 19 words, opens with "I'm" (not a command verb).
OUTRO = ("I'm Qubit. Thank you for watching. I hope you enjoyed. "
         "I'll see you in the next video. Bye! Bye!")


class FakeCfg:
    def __init__(self, wake=None):
        self._d = {"voice_edit": {"beep": False, "wake": wake or
                                  {"enabled": True}}}

    def get(self, *p, default=None):
        node = self._d
        for seg in p:
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                return default
        return node


def _monitor(commands, *, busy=False, cfg=None):
    return WakeMonitor(
        cfg or FakeCfg(),
        on_command=commands.append,
        is_busy=(busy if callable(busy) else (lambda: busy)),
        transcribe=lambda pcm: "",
        pending_probe=lambda: False,
    )


def _silence_journal(monkeypatch):
    """Neutralize the shared-journal pending read so tests stay hermetic."""
    monkeypatch.setattr(wake_mod, "_live_voice_pending", lambda cfg: "")


# ---------------------------------------------------------------------------
# 1. media in the ARMED window (the journal false fire)
# ---------------------------------------------------------------------------
def test_outro_is_media_like_after_limit_tightening():
    # The exact journal text must be classified as media now (was 19 <= 20).
    assert _is_media_like(OUTRO) is True
    # Short commands and verb-led dictation must NOT be media.
    assert _is_media_like("open youtube") is False
    assert _is_media_like("hey qubit, what's the weather") is False
    assert _is_media_like(
        "hey qubit, type this for me: the quarterly report is due on Friday"
    ) is False


def test_media_in_armed_window_is_dropped_and_arm_stays_usable(monkeypatch):
    _silence_journal(monkeypatch)
    stages = []
    monkeypatch.setattr(wake_mod, "_trace",
                        lambda stage, **kw: stages.append(stage))
    commands = []
    m = _monitor(commands)

    m._handle_transcript("hey qubit")          # bare wake opens the arm window
    assert m._armed_until != 0.0
    m._handle_transcript(OUTRO)                # room/media audio in the window
    assert commands == [], "the media outro must never execute"
    assert "media_drop" in stages
    assert m._armed_until != 0.0, "the arm must survive the media drop"

    m._handle_transcript("open youtube")       # the real follow-up still fires
    assert commands == ["open youtube"]


def test_media_while_speaking_does_not_consume_arm(monkeypatch):
    _silence_journal(monkeypatch)
    commands = []
    m = _monitor(commands, busy=True)          # helper is speaking (echo tail)

    m._armed_until = wake_mod.time.monotonic() + 12.0
    m._handle_transcript(OUTRO)
    assert commands == []
    assert m._armed_until != 0.0
    m._handle_transcript("open youtube")
    assert commands == ["open youtube"]


def test_forced_media_is_dropped_but_a_real_forced_wake_fires(monkeypatch):
    _silence_journal(monkeypatch)
    commands = []
    m = _monitor(commands)
    m._handle_transcript(OUTRO, forced=True)
    assert commands == []
    m._handle_transcript("hey qubit, open youtube", forced=True)
    assert commands == ["open youtube"]


# ---------------------------------------------------------------------------
# 2. after a fired command the loop returns to a usable state (no stall)
# ---------------------------------------------------------------------------
def test_fired_command_does_not_stall_the_next_real_wake(monkeypatch):
    _silence_journal(monkeypatch)
    commands = []
    m = _monitor(commands)
    # First real turn fires and consumes the window (forced = KWS hit).
    m._handle_transcript("hey qubit, what's my name?", forced=True)
    assert commands == ["what's my name?"]
    assert m._armed_until == 0.0, "a fired command consumes the window"
    # Ambient media right after must be a harmless discard ...
    m._handle_transcript(OUTRO)
    assert commands == ["what's my name?"]
    # ... and the next real "hey qubit" must still fire.
    m._handle_transcript("hey qubit, open youtube")
    assert commands == ["what's my name?", "open youtube"]


def test_short_real_command_in_armed_window_still_emits(monkeypatch):
    _silence_journal(monkeypatch)
    commands = []
    m = _monitor(commands)
    m._handle_transcript("hey qubit")
    m._handle_transcript("fix the grammar")   # short, verb-led: not media
    assert commands == ["fix the grammar"]


# ---------------------------------------------------------------------------
# 3. the wake busy gate must not deadlock on fullscreen (Right-Ctrl parity)
# ---------------------------------------------------------------------------
class _FakeOverlay:
    def set_status(self, *a, **k):
        pass

    def show_preview(self, *a, **k):
        pass

    def show_error(self, *a, **k):
        pass

    def show(self):
        pass

    def hide(self):
        pass

    def close(self):
        pass


class _FakeDesktop:
    def __init__(self, fullscreen=False):
        self._fullscreen = fullscreen

    def session(self):
        return {"session_type": "x11", "x11": True, "wayland": False}

    def focused_window(self):
        return {"window_id": "42", "app_class": "code", "app_name": "code",
                "window_title": ""}

    def is_fullscreen(self, window_id=""):
        return self._fullscreen


class _FakeMic:
    def start(self):
        pass

    def read_frame(self, timeout=0.2):
        return None

    def stop(self):
        pass


class _FakeStt:
    error = None


def _helper(monkeypatch, fullscreen=False):
    h = dh.DesktopHelper(
        FakeCfg(),
        client=object(),
        overlay=_FakeOverlay(),
        desktop=_FakeDesktop(fullscreen=fullscreen),
        mic_factory=lambda: _FakeMic(),
        stt_factory=lambda t: _FakeStt(),
    )
    # Keep the gate hermetic: no journal read for the echo guard.
    monkeypatch.setattr(h, "_tts_echo_busy", lambda *a, **k: False)
    return h


def test_wake_busy_is_not_true_merely_because_fullscreen(monkeypatch):
    """RED on the old gate: a fullscreen window used to disable wake entirely."""
    h = _helper(monkeypatch, fullscreen=True)
    assert h._fullscreen_active() is True, "fixture must report fullscreen"
    h.state = h.IDLE
    h._speaking = False
    assert h._wake_busy() is False, (
        "fullscreen must not make the wake loop yield forever (journal: "
        "20+ min of busy_skip while the helper was idle)")


def test_wake_busy_true_while_capturing_and_while_speaking(monkeypatch):
    h = _helper(monkeypatch)
    h.state = h.IDLE
    h._speaking = False
    assert h._wake_busy() is False
    h.state = h.LISTENING
    assert h._wake_busy() is True
    h.state = h.PROCESSING
    assert h._wake_busy() is True
    h.state = h.IDLE
    h._speaking = True
    assert h._wake_busy() is True


def test_wake_busy_never_raises_on_broken_probes(monkeypatch):
    h = _helper(monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("echo probe down")

    monkeypatch.setattr(h, "_tts_echo_busy", _boom)
    h.state = h.IDLE
    h._speaking = False
    assert h._wake_busy() is False


# ---------------------------------------------------------------------------
# 4. the monitor decision seam never raises on junk
# ---------------------------------------------------------------------------
def test_handle_transcript_never_raises(monkeypatch):
    _silence_journal(monkeypatch)
    commands = []
    m = _monitor(commands)
    m._armed_until = wake_mod.time.monotonic() + 12.0
    for bad in (None, 123, "", "   ", "\x00\x01", [], {}, OUTRO, "hey qubit,"):
        m._handle_transcript(bad)  # must never raise
