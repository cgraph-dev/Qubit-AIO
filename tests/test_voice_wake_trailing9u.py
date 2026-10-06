"""Trailing wake activation ("open youtube, qubit") — OpenClaw port.

OpenClaw's realtime activation-name matcher accepts the spoken name at the
LEADING or TRAILING edge of a transcript. Qubit only had the leading edge;
this pins the trailing edge, comma-gated so an ordinary sentence that merely
ends in "qubit" never becomes a command.
"""
from __future__ import annotations


def test_trailing_bare_name_extracts_command():
    from voicebridge.voice_edit.wake import strip_wake_trailing
    assert strip_wake_trailing("open youtube, qubit") == "open youtube"
    assert strip_wake_trailing("check the weather, cubit") == "check the weather"
    assert strip_wake_trailing("type this for me, qbit") == "type this for me"


def test_trailing_full_alias_extracts_command():
    from voicebridge.voice_edit.wake import strip_wake_trailing
    assert strip_wake_trailing("open youtube, hey qubit") == "open youtube"
    assert strip_wake_trailing("do it now, okay qubit") == "do it now"


def test_trailing_tolerates_punctuation_and_case():
    from voicebridge.voice_edit.wake import strip_wake_trailing
    assert strip_wake_trailing("Hello Qubit, QUBIT!") == "Hello Qubit"
    assert strip_wake_trailing("stop the music , qubit .") == "stop the music"


def test_no_comma_is_not_trailing_activation():
    from voicebridge.voice_edit.wake import strip_wake_trailing
    # The comma gate: a sentence ending in "qubit" is NOT a command.
    assert strip_wake_trailing("what is qubit") is None
    assert strip_wake_trailing("tell me about the qubit") is None


def test_nothing_before_name_is_none():
    from voicebridge.voice_edit.wake import strip_wake_trailing
    assert strip_wake_trailing("qubit") is None
    assert strip_wake_trailing(", qubit") is None
    assert strip_wake_trailing("") is None
    assert strip_wake_trailing(None) is None


def test_never_raises_on_junk():
    from voicebridge.voice_edit.wake import strip_wake_trailing
    assert strip_wake_trailing(12345) is None
    assert strip_wake_trailing(", , qubit ,,") is None
    assert strip_wake_trailing(", hey qubit") is None
