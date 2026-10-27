"""Revert must target the text widget, not the withdrawn panel.

Regression (owner live test, 2026-10-27): Revert reported ok but the .txt
document did not change. `activate_window` only set the WM-active window; the
keyboard focus can still be on the topmost panel, so Ctrl+Z was delivered to
the wrong widget. Pin both focus commands (and the bounded settle in
`revert_last`) without touching a real desktop.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.voice_edit.desktop import DesktopAdapter


def test_activate_window_sets_wm_and_input_focus():
    calls = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        return 0, b"", b""

    adapter = DesktopAdapter(run=run, which=lambda name: f"/usr/bin/{name}",
                             env={"DISPLAY": ":0"})
    adapter.session = lambda: {"x11": True, "session_type": "x11",
                               "wayland": False}
    assert adapter.activate_window("42") is True
    assert ["xdotool", "windowactivate", "--sync", "42"] in calls
    assert ["xdotool", "windowfocus", "--sync", "42"] in calls
