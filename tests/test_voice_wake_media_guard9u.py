"""Wake-loop media/own-voice + pending-confirmation guard tests.

Hermetic: no X, no mic, no socket. The post-ASR decision seam
``WakeMonitor._handle_transcript`` is driven directly with stub monitors.

Context (journal-proven): a YouTube video's audio ("I'm Qubit. Thank you for
watching ...") tripped the spotter and the whole sentence was executed as a
command, clobbering a real staged "open youtube" confirmation; the later
"yes" then ran the wrong action. Two guards are tested here:

* pending-aware gate — with a confirmation staged, only a bare
  affirmative/negative is accepted; any other transcript is dropped
  (``pending_guard``) and the pending is kept;
* media/own-voice suppression — a long non-command-verb utterance is dropped
  (``media_drop``) instead of executing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit import wake as wake_mod
from voicebridge.voice_edit.wake import WakeMonitor, _is_media_like


class FakeCfg:
    def __init__(self, wake=None):
        self._d = {"voice_edit": {"beep": False,
                                   "wake": wake or {"enabled": True}}}

    def get(self, *p, default=None):
        node = self._d
        for seg in p:
            if isinstance(node, dict) and seg in node:
                node = node[seg]
            else:
                return default
        return node


#: >20 words, opens with "I'm" (not a command verb/question word) — the
#: YouTube-outro shape from the bug report.
MEDIA = ("I'm Qubit. Thank you for watching this video, please remember to "
         "like and subscribe to the channel and ring the bell for more "
         "content about building things with artificial intelligence today")

#: >20 words but opens with the "type" command verb: long dictation must live.
LONG_DICTATION = ("hey qubit, type this for me: the quarterly report needs to "
                  "be finished by Friday and sent to the whole team before the "
                  "meeting starts tomorrow morning")


@pytest.fixture(autouse=True)
def _no_journal_pending(monkeypatch):
    """Neutralize the shared journal read so tests stay hermetic/fast.

    Pending scenarios use the injected ``pending_probe`` instead.
    """
    monkeypatch.setattr(wake_mod, "_live_voice_pending", lambda cfg: "")


def _monitor(commands, *, busy=False, pending=None, cfg=None):
    return WakeMonitor(
        cfg or FakeCfg(),
        on_command=commands.append,
        is_busy=(busy if callable(busy) else (lambda: busy)),
        transcribe=lambda pcm: "",
        pending_probe=pending,
    )


def _stages(monkeypatch):
    stages = []
    monkeypatch.setattr(wake_mod, "_trace",
                        lambda stage, **kw: stages.append(stage))
    return stages


# ---------------------------------------------------------------------------
# 1. pending-aware gate
# ---------------------------------------------------------------------------
def test_pending_plus_long_unrelated_sentence_is_guarded(monkeypatch):
    stages = _stages(monkeypatch)
    commands = []
    m = _monitor(commands, pending=lambda: True)
    m._handle_transcript(MEDIA)
    assert commands == []
    assert "pending_guard" in stages


def test_pending_plus_short_unrelated_sentence_is_guarded(monkeypatch):
    stages = _stages(monkeypatch)
    commands = []
    m = _monitor(commands, pending=lambda: True)
    m._handle_transcript("what's the weather like today")
    assert commands == []
    assert "pending_guard" in stages


def test_pending_yes_settles(monkeypatch):
    commands = []
    m = _monitor(commands, pending=lambda: True)
    m._handle_transcript("yes")
    assert commands == ["yes"]


def test_pending_no_settles(monkeypatch):
    commands = []
    m = _monitor(commands, pending=lambda: True)
    m._handle_transcript("no")
    assert commands == ["no"]


def test_pending_guard_keeps_pending_for_later_yes(monkeypatch):
    commands = []
    m = _monitor(commands, pending=lambda: True)
    m._handle_transcript("open youtube")  # non-affirm while pending -> dropped
    assert commands == []
    m._handle_transcript("yes")           # pending was kept -> still settles
    assert commands == ["yes"]


def test_forced_non_affirm_dropped_while_pending(monkeypatch):
    stages = _stages(monkeypatch)
    commands = []
    m = _monitor(commands, pending=lambda: True)
    m._handle_transcript("open youtube", forced=True)
    assert commands == []
    assert "pending_guard" in stages


def test_forced_affirm_emitted_while_pending(monkeypatch):
    # Preserve the existing forced-KWS contract: a directed "yes" still emits.
    commands = []
    m = _monitor(commands, pending=lambda: True)
    m._handle_transcript("yes", forced=True)
    assert commands == ["yes"]


def test_no_pending_non_wake_sentence_still_discards(monkeypatch):
    commands = []
    m = _monitor(commands)
    m._handle_transcript("someone said something random")
    assert commands == []


# ---------------------------------------------------------------------------
# 2. own-voice / media suppression
# ---------------------------------------------------------------------------
def test_is_media_like_recognizes_outro_and_long_dictation():
    assert _is_media_like(MEDIA) is True
    # A long command that opens with a verb is NOT media.
    assert _is_media_like(LONG_DICTATION) is False
    # Short commands are never media.
    assert _is_media_like("open youtube") is False
    assert _is_media_like("hey qubit, what's the weather") is False
    # Malformed input never raises.
    assert _is_media_like(None) is False
    assert _is_media_like(1234) is False


def test_media_sentence_dropped_when_forced(monkeypatch):
    stages = _stages(monkeypatch)
    commands = []
    m = _monitor(commands)
    m._handle_transcript(MEDIA, forced=True)
    assert commands == []  # the YouTube outro never executes
    assert "media_drop" in stages


def test_media_sentence_dropped_while_speaking(monkeypatch):
    stages = _stages(monkeypatch)
    commands = []
    m = _monitor(commands, busy=True)
    m._handle_transcript(MEDIA)
    assert commands == []
    assert "media_drop" in stages


def test_short_command_inside_echo_tail_still_emits():
    # The media guard must not touch a short real command.
    commands = []
    m = _monitor(commands, busy=True)
    m._handle_transcript("hey qubit, open youtube")
    assert commands == ["open youtube"]


def test_long_verb_led_command_still_emits(monkeypatch):
    # Long dictation opens with a command verb, so it is never media.
    stages = _stages(monkeypatch)
    commands = []
    m = _monitor(commands)
    m._handle_transcript(LONG_DICTATION)
    assert stages.count("media_drop") == 0
    assert commands and commands[0].startswith("type this for me:")


# ---------------------------------------------------------------------------
# 3. normal behaviour + robustness
# ---------------------------------------------------------------------------
def test_short_command_with_leading_wake_still_emits():
    commands = []
    m = _monitor(commands)
    m._handle_transcript("hey qubit, open youtube")
    assert commands == ["open youtube"]


def test_short_command_forced_still_emits():
    commands = []
    m = _monitor(commands)
    m._handle_transcript("open youtube", forced=True)
    assert commands == ["open youtube"]


def test_trailing_wake_still_works():
    commands = []
    m = _monitor(commands)
    m._handle_transcript("open youtube, qubit")
    assert commands == ["open youtube"]


def test_never_raises_on_bad_input_or_broken_probes():
    commands = []

    def _boom():
        raise RuntimeError("probe down")

    m = _monitor(commands, pending=_boom)
    for bad in (None, 123, "", "   ", "\x00\x01", [], {}, "hey qubit," * 5):
        m._handle_transcript(bad)  # must never raise

    # A broken is_busy probe degrades to not-speaking, never raises.
    m2 = WakeMonitor(
        FakeCfg(), on_command=commands.append,
        is_busy=lambda: (_ for _ in ()).throw(RuntimeError("busy probe down")),
        transcribe=lambda pcm: "", pending_probe=_boom)
    m2._handle_transcript(MEDIA, forced=True)
    m2._handle_transcript("hey qubit, open youtube")


def test_empty_transcript_never_emits():
    commands = []
    m = _monitor(commands)
    m._handle_transcript("")
    m._handle_transcript("   ")
    assert commands == []
