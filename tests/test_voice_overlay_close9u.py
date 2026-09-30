"""Voice-edit overlay: the title-bar X always dismisses the window.

Regression: ``WM_DELETE_WINDOW`` only notified ``cancel`` to the helper, so
with an idle state machine (the normal case after a finished turn) the X
button visibly did nothing and the window stayed on screen. The handler now
hides first, then notifies.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest


def _make_overlay():
    try:
        from voicebridge.voice_edit import overlay as OV
    except Exception as exc:
        pytest.skip(f"overlay module unavailable: {exc}")
    actions = []
    try:
        ov = OV.PreviewOverlay(on_action=lambda a, p: actions.append(a))
    except Exception as exc:
        pytest.skip(f"no display for tkinter: {exc}")
    return ov, actions


def test_close_button_hides_window_and_notifies_cancel():
    ov, actions = _make_overlay()
    try:
        ov.show()
        assert ov.root.winfo_viewable() == 1
        ov._close_button()
        assert ov.root.winfo_viewable() == 0
        assert actions == ["cancel"]
        # Showing again still works (hide, not destroy).
        ov.show()
        assert ov.root.winfo_viewable() == 1
    finally:
        ov.close()


def test_close_button_hides_even_when_helper_ignores_cancel():
    ov, actions = _make_overlay()
    try:
        ov.show()
        # Simulate the idle helper dropping the cancel: the window must
        # still be gone — dismissal never depends on the state machine.
        actions.clear()
        ov._close_button()
        assert ov.root.winfo_viewable() == 0
    finally:
        ov.close()
