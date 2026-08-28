"""Hermetic regression tests for the portable session-DISPLAY resolver.

The installed systemd units carry an explicit DISPLAY; the bridge hotkey
listener and the monitor notify-send self-heal to the active X session via
``voicebridge/display``. These tests are hermetic (fake X sockets, no live X).
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def test_keeps_valid_existing_display(monkeypatch):
    from voicebridge import display as D
    monkeypatch.setattr(D, "_x11_display_numbers", lambda: [0, 1])
    assert D.resolve_session_display({"DISPLAY": ":1"}) == ":1"
    assert D.resolve_session_display({"DISPLAY": ":0"}) == ":0"


def test_derives_display_when_unset(monkeypatch):
    from voicebridge import display as D
    monkeypatch.setattr(D, "_x11_display_numbers", lambda: [0, 1])
    # highest real socket wins (single active session = the last X server)
    assert D.resolve_session_display({"DISPLAY": ""}) == ":1"
    assert D.resolve_session_display({}) == ":1"


def test_corrects_stale_display_to_real_session(monkeypatch):
    """A DISPLAY pointing at a socket that does not exist is corrected to the
    real session (the installed-unit :0-vs-:1 bug this resolver fixes)."""
    from voicebridge import display as D
    monkeypatch.setattr(D, "_x11_display_numbers", lambda: [1])
    assert D.resolve_session_display({"DISPLAY": ":0"}) == ":1"


def test_headless_returns_none(monkeypatch):
    from voicebridge import display as D
    monkeypatch.setattr(D, "_x11_display_numbers", lambda: [])
    assert D.resolve_session_display({"DISPLAY": ":0"}) is None
    assert D.resolve_session_display({}) is None


def test_ensure_sets_process_env(monkeypatch):
    from voicebridge import display as D
    monkeypatch.setattr(D, "_x11_display_numbers", lambda: [1])
    saved = os.environ.get("DISPLAY")
    try:
        os.environ.pop("DISPLAY", None)
        assert D.ensure_session_display() == ":1"
        assert os.environ.get("DISPLAY") == ":1"
    finally:
        if saved:
            os.environ["DISPLAY"] = saved
        else:
            os.environ.pop("DISPLAY", None)