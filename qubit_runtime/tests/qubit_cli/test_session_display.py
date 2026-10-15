"""The dashboard /chat PTY child must carry a reachable X11 DISPLAY.

A dashboard/gateway started as a systemd user service inherits no ``DISPLAY``;
xclip inside the spawned TUI then fails with "Can't open display: (null)" and
every image paste reports "No image found in clipboard". These tests pin the
resolver's contract: only a genuinely reachable display is written, and a
headless/ambiguous session is left untouched.
"""

from __future__ import annotations

import pytest

from qubit_cli import session_display


def test_no_sockets_leaves_env_untouched(monkeypatch):
    """Headless host: nothing is written, ever."""
    monkeypatch.setattr(session_display, "_x11_display_numbers", lambda: [])
    env: dict[str, str] = {}
    assert session_display.resolve_session_display(env) is None
    session_display.apply_session_display(env)
    assert "DISPLAY" not in env


def test_single_reachable_socket_is_used(monkeypatch):
    """Exactly one real socket that answers a probe wins."""
    monkeypatch.setattr(session_display, "_x11_display_numbers", lambda: [0])
    monkeypatch.setattr(session_display, "_session_hint_display", lambda: None)
    monkeypatch.setattr(session_display, "_probe_display", lambda d, e=None: d == ":0")
    env: dict[str, str] = {}
    session_display.apply_session_display(env)
    assert env["DISPLAY"] == ":0"


def test_multiple_reachable_sockets_are_never_guessed(monkeypatch):
    """Ambiguous multi-seat host: refusal, not 'the highest display'."""
    monkeypatch.setattr(session_display, "_x11_display_numbers", lambda: [0, 1])
    monkeypatch.setattr(session_display, "_session_hint_display", lambda: None)
    monkeypatch.setattr(session_display, "_probe_display", lambda d, e=None: True)
    assert session_display.resolve_session_display({}) is None


def test_unreachable_display_is_not_trusted(monkeypatch):
    """A set-but-dead DISPLAY is replaced by a reachable one, never kept."""
    monkeypatch.setattr(session_display, "_x11_display_numbers", lambda: [0, 1])
    monkeypatch.setattr(session_display, "_session_hint_display", lambda: None)
    monkeypatch.setattr(
        session_display, "_probe_display", lambda d, e=None: d == ":1")
    assert session_display.resolve_session_display({"DISPLAY": ":0"}) == ":1"


def test_reachable_configured_display_is_kept(monkeypatch):
    """An already-valid DISPLAY is honoured as-is."""
    monkeypatch.setattr(session_display, "_x11_display_numbers", lambda: [0])
    monkeypatch.setattr(session_display, "_probe_display", lambda d, e=None: True)
    assert session_display.resolve_session_display({"DISPLAY": ":0"}) == ":0"


def test_apply_never_overwrites_a_live_display(monkeypatch):
    """Only a missing DISPLAY triggers resolution."""
    def _boom(*_a, **_kw):
        raise AssertionError("resolve must not run when DISPLAY is set")

    monkeypatch.setattr(session_display, "resolve_session_display", _boom)
    env = {"DISPLAY": ":7"}
    session_display.apply_session_display(env)
    assert env["DISPLAY"] == ":7"


def test_xauthority_is_filled_when_missing(monkeypatch, tmp_path):
    """A per-user xauth file is attached so xclip can actually connect."""
    auth = tmp_path / "Xauthority"
    auth.write_text("cookie")
    env = {"DISPLAY": ":0", "HOME": str(tmp_path)}
    monkeypatch.setattr(
        session_display, "_candidate_xauthority", lambda _env: str(auth))
    session_display.apply_session_display(env)
    assert env["XAUTHORITY"] == str(auth)


def test_display_number_parsing():
    parse = session_display._display_number
    assert parse(":0") == 0
    assert parse(":12.1") == 12
    assert parse("localhost:3") == 3
    assert parse("") is None
    assert parse("bogus") is None


@pytest.mark.parametrize("bad", ["", "   "])
def test_blank_display_never_resolves_to_a_number(bad):
    assert session_display._display_number(bad) is None
