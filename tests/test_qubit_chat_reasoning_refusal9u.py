"""Dashboard audit fix — /qubit-chat invalid reasoning must be a typed error.

Regression for the NameError: the stream refusal generator referenced the
``except``-scope variable ``exc``, which Python deletes at the end of the
block. Iterating the returned generator then raised NameError after the SSE
headers were already sent as HTTP 200, producing a 0-byte body with no error
frame. The message must be bound to a plain local BEFORE the generator is
defined so the refusal is a real, typed SSE error body.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_invalid_reasoning_yields_typed_error_frame_not_empty(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    iterator = HC.h_chat_stream(
        cfg, {}, {"text": "hi", "session": "ai3-regression",
                  "reasoning": "turbo"}, {})
    frames = list(iterator)

    # Before the fix this raised NameError while iterating (the caller saw an
    # empty 200 body). A non-empty, typed error frame is the contract.
    assert frames, "stream refusal produced no frame (0-byte body)"
    blob = "".join(frames)
    assert "REASONING_INVALID" in blob
    assert "event: error" in blob
    assert "turbo" in blob
    assert "ai3-regression" in blob


def test_valid_reasoning_still_streams_a_turn(tmp_path, monkeypatch):
    """The except path is the only change; a valid level is unaffected."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    # ``none`` is a valid reasoning level; the handler must not take the
    # refusal branch (it returns the transport's SSE iterator instead).
    iterator = HC.h_chat_stream(
        cfg, {}, {"text": "hi", "session": "ok", "reasoning": "none"}, {})
    assert hasattr(iterator, "__iter__")
    assert not isinstance(iterator, list)
