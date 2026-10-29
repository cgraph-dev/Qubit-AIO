"""Voice display switch: move the active window between the two X11 outputs.

The owner hardware is DP-2 primary (2580x1080+0+0, "first") and HDMI-1
(1920x1080+2580+0, "second"). These tests pin the deterministic route and the
wmctrl/xdotool calls; no real X server or window manager is touched.
"""
from types import SimpleNamespace

import pytest

from voicebridge.local import actions as LA


_DISPLAYS = [
    SimpleNamespace(name="DP-2", x=0, y=0, w=2580, h=1080, primary=True),
    SimpleNamespace(name="HDMI-1", x=2580, y=0, w=1920, h=1080, primary=False),
]


class _R:
    def __init__(self, out="", rc=0, err=""):
        self.stdout = out
        self.stderr = err
        self.returncode = rc


def _fake_run(calls, fullscreen=False):
    def run(args, timeout=10.0):
        calls.append(list(args))
        if args[:2] == ["xdotool", "getactivewindow"]:
            return _R(out="12345\n")
        if args[:2] == ["xdotool", "getwindowgeometry"]:
            return _R(out=("WINDOW=12345\nX=66\nY=32\n"
                           "WIDTH=2514\nHEIGHT=1048\nSCREEN=0\n"))
        if args[:1] == ["xprop"]:
            state = "_NET_WM_STATE_FULLSCREEN\n" if fullscreen else ""
            return _R(out=state)
        if args[:1] == ["wmctrl"]:
            return _R()
        return _R(rc=1, err="unexpected")
    return run


@pytest.mark.parametrize(("phrase", "which"), [
    ("switch tab to first display", 1),
    ("switch tab to the second display", 2),
    ("hey qubit switch this tab to the second display", 2),
    ("move this window to the second screen", 2),
    ("send it to the first monitor", 1),
    ("put this on display 2", 2),
    ("switch to the first monitor", 1),
    ("switch display to second", 2),
])
def test_display_switch_routes(phrase, which):
    m = LA.match(phrase)
    assert m is not None and m.name == "display_switch", (phrase, m)
    assert m.params.get("display") == which, (phrase, m.params)


def test_display_switch_does_not_steal_tab_switch():
    m = LA.match("switch to the inbox tab")
    assert m is not None and m.name == "tab_switch", m
    m = LA.match("go to the github tab")
    assert m is not None and m.name == "tab_switch", m


def test_display_switch_moves_and_fits_active_window(monkeypatch):
    calls = []
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(LA.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(LA, "_query_displays", lambda: list(_DISPLAYS))
    monkeypatch.setattr(LA, "_display_run", _fake_run(calls))

    msg = LA.execute(LA.match("switch tab to the second display"))
    assert msg == "Switched to the second display."
    move = [c for c in calls if c[:4] == ["wmctrl", "-i", "-r", "0x3039"]
            and c[4] == "-e"]
    assert move, calls
    assert move[0][5] == "0,2592,12,1896,1048", move[0]
    assert any(c[:4] == ["wmctrl", "-i", "-a", "0x3039"] for c in calls), calls
    assert not any("fullscreen" in c for c in calls), calls


def test_display_switch_selects_primary_for_first(monkeypatch):
    calls = []
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(LA.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(LA, "_query_displays", lambda: list(_DISPLAYS))
    monkeypatch.setattr(LA, "_display_run", _fake_run(calls))

    msg = LA.execute(LA.match("switch to the first display"))
    assert msg == "Switched to the first display."
    move = [c for c in calls if c[:4] == ["wmctrl", "-i", "-r", "0x3039"]
            and c[4] == "-e"]
    assert move and move[0][5] == "0,12,12,2514,1048", move


def test_display_switch_preserves_fullscreen(monkeypatch):
    calls = []
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(LA.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(LA, "_query_displays", lambda: list(_DISPLAYS))
    monkeypatch.setattr(LA, "_display_run", _fake_run(calls, fullscreen=True))

    msg = LA.execute(LA.match("move this window to the second screen"))
    assert msg == "Switched to the second display."
    seq = [c[4:6] for c in calls if c[:4] == ["wmctrl", "-i", "-r", "0x3039"]]
    assert ["-b", "remove,fullscreen"] in seq, seq
    assert any(c[:6] == ["wmctrl", "-i", "-r", "0x3039", "-b", "add,fullscreen"]
               for c in calls), calls


def test_display_switch_reports_one_display(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(LA.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(LA, "_query_displays", lambda: list(_DISPLAYS[:1]))
    msg = LA.execute(LA.match("switch to the second display"))
    assert "only see one display" in msg, msg


def test_display_switch_requires_x11(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    msg = LA.execute(LA.match("switch to the second display"))
    assert "X11 display" in msg, msg


def test_display_switch_requires_tools(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(LA.shutil, "which", lambda name: None)
    msg = LA.execute(LA.match("switch to the second display"))
    assert "wmctrl" in msg and "xdotool" in msg, msg


def test_display_switch_reports_unknown_target(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(LA.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(LA, "_query_displays", lambda: list(_DISPLAYS))
    msg = LA.execute(LA.match("switch to the third display"))
    assert "third" in msg and "connected" in msg, msg


def test_display_switch_removes_and_restores_maximized(monkeypatch):
    calls = []
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(LA.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(LA, "_query_displays", lambda: list(_DISPLAYS))
    monkeypatch.setattr(LA, "time", type("T", (), {"sleep": staticmethod(lambda s: None)}))

    def run(args, timeout=10.0):
        calls.append(list(args))
        if args[:2] == ["xdotool", "getactivewindow"]:
            return _R(out="12345\n")
        if args[:2] == ["xdotool", "getwindowgeometry"]:
            return _R(out="WIDTH=2514\nHEIGHT=1048\n")
        if args[:1] == ["xprop"]:
            return _R(out=("_NET_WM_STATE_MAXIMIZED_VERT\n"
                           "_NET_WM_STATE_MAXIMIZED_HORZ\n"))
        return _R()

    monkeypatch.setattr(LA, "_display_run", run)
    msg = LA.execute(LA.match("move this window to the second display"))
    assert msg == "Switched to the second display."
    assert any(c[:6] == ["wmctrl", "-i", "-r", "0x3039", "-b",
                         "remove,maximized_vert,maximized_horz"]
               for c in calls), calls
    assert any(c[:6] == ["wmctrl", "-i", "-r", "0x3039", "-b",
                         "add,maximized_vert,maximized_horz"]
               for c in calls), calls


def test_display_switch_catalog_examples_are_verified():
    from voicebridge.voice_edit import custom_commands as CC
    examples = CC._verified_examples("display_switch", LA.match)
    assert examples, examples
    assert all(e["verified"] for e in examples), examples
