"""Hermetic regression tests for the portable, VALIDATED session-DISPLAY resolver.

The resolver must never trust a hardcoded/stale DISPLAY and must never guess
"the highest display". It keeps a DISPLAY only when it is genuinely reachable
(bounded xinput probe), prefers the user-systemd session hint, and returns None
when the candidate set is ambiguous (multiple reachable, none hinted).
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _no_hint(monkeypatch):
    monkeypatch.setattr("voicebridge.display._session_hint_display",
                        lambda: None)


def test_keeps_valid_reachable_existing_display(monkeypatch):
    from voicebridge import display as D
    _no_hint(monkeypatch)
    monkeypatch.setattr(D, "_x11_display_numbers", lambda: [0, 1])
    monkeypatch.setattr(D, "_probe_display", lambda d, env=None: True)
    assert D.resolve_session_display({"DISPLAY": ":1"}) == ":1"
    assert D.resolve_session_display({"DISPLAY": ":0"}) == ":0"


def test_rejects_unreachable_existing_display(monkeypatch):
    """A DISPLAY that is set but cannot be reached must NOT be trusted."""
    from voicebridge import display as D
    _no_hint(monkeypatch)
    monkeypatch.setattr(D, "_x11_display_numbers", lambda: [0])
    monkeypatch.setattr(D, "_probe_display", lambda d, env=None: False)
    assert D.resolve_session_display({"DISPLAY": ":0"}) is None


def test_derives_single_reachable_display_when_unset(monkeypatch):
    from voicebridge import display as D
    _no_hint(monkeypatch)
    monkeypatch.setattr(D, "_x11_display_numbers", lambda: [0, 1])
    # only :0 is reachable -> that session is used (never "the highest")
    monkeypatch.setattr(D, "_probe_display",
                        lambda d, env=None: d == ":0")
    assert D.resolve_session_display({"DISPLAY": ""}) == ":0"
    assert D.resolve_session_display({}) == ":0"


def test_ambiguous_multiple_reachable_never_guesses(monkeypatch):
    from voicebridge import display as D
    _no_hint(monkeypatch)
    monkeypatch.setattr(D, "_x11_display_numbers", lambda: [0, 1])
    monkeypatch.setattr(D, "_probe_display", lambda d, env=None: True)
    # two reachable sessions and no hint -> None, NOT ":1"
    assert D.resolve_session_display({}) is None


def test_session_hint_preferred_over_socket_probe(monkeypatch):
    from voicebridge import display as D
    monkeypatch.setattr(D, "_session_hint_display", lambda: ":1")
    monkeypatch.setattr(D, "_x11_display_numbers", lambda: [0, 1])
    monkeypatch.setattr(D, "_probe_display", lambda d, env=None: True)
    assert D.resolve_session_display({}) == ":1"


def test_corrects_stale_display_to_real_session(monkeypatch):
    """A DISPLAY pointing at a socket that does not exist is corrected to the
    reachable real session (the installed-unit :1-vs-:0 bug this fixes)."""
    from voicebridge import display as D
    _no_hint(monkeypatch)
    monkeypatch.setattr(D, "_x11_display_numbers", lambda: [0])
    monkeypatch.setattr(D, "_probe_display", lambda d, env=None: d == ":0")
    assert D.resolve_session_display({"DISPLAY": ":1"}) == ":0"


def test_headless_returns_none(monkeypatch):
    from voicebridge import display as D
    monkeypatch.setattr(D, "_x11_display_numbers", lambda: [])
    assert D.resolve_session_display({"DISPLAY": ":0"}) is None
    assert D.resolve_session_display({}) is None


def test_ensure_sets_only_reachable_display(monkeypatch):
    from voicebridge import display as D
    _no_hint(monkeypatch)
    monkeypatch.setattr(D, "_x11_display_numbers", lambda: [1])
    monkeypatch.setattr(D, "_probe_display", lambda d, env=None: True)
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


def test_ensure_does_not_set_unreachable_display(monkeypatch):
    from voicebridge import display as D
    _no_hint(monkeypatch)
    monkeypatch.setattr(D, "_x11_display_numbers", lambda: [1])
    monkeypatch.setattr(D, "_probe_display", lambda d, env=None: False)
    saved = os.environ.get("DISPLAY")
    try:
        os.environ.pop("DISPLAY", None)
        assert D.ensure_session_display() is None
        assert os.environ.get("DISPLAY") is None
    finally:
        if saved:
            os.environ["DISPLAY"] = saved
        else:
            os.environ.pop("DISPLAY", None)


def test_probe_display_prefers_xinput_backend(monkeypatch):
    """Reachability is judged by xinput connecting — the exact hotkey backend."""
    from voicebridge import display as D
    calls = []

    def _run(args, **kw):
        calls.append((args, kw.get("env", {}).get("DISPLAY")))
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(D.subprocess, "run", _run)
    assert D._probe_display(":0", {"DISPLAY": ":0"}) is True
    assert calls and calls[0][1] == ":0"