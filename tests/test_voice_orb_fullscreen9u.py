"""Orb hides while a fullscreen game/window is focused (X11, EWMH).

Owner demand: the OS orb is visible over EVERYTHING except fullscreen games.
While the FOCUSED window carries ``_NET_WM_STATE_FULLSCREEN``, the orb
withdraws; when focus leaves fullscreen it returns (deiconify + lift only —
never focus-stealing). Polled alongside the existing state poll (no new
threads/processes beyond one cheap ``xprop`` per poll tick).

Hermetic: every test injects a fake ``run`` (no X11, no subprocess); no-X11
is a safe no-op (False, never raises).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.orb import fullscreen as fs


class _Done:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _run_factory(outputs):
    """Fake subprocess.run: substring match on argv -> _Done (default empty)."""
    def _run(argv, **kw):
        cmd = " ".join(str(a) for a in argv)
        for needle, done in outputs:
            if needle in cmd:
                return done
        return _Done("")
    return _run


def _active_run(win="0x7e00004", states="_NET_WM_STATE_FULLSCREEN"):
    return _run_factory([
        ("_NET_ACTIVE_WINDOW",
         _Done(f"_NET_ACTIVE_WINDOW(WINDOW): window id # {win}\n")),
        (f"-id {win}",
         _Done(f"_NET_WM_STATE(ATOM) = {states}\n")),
    ])


# -- detection ---------------------------------------------------------------

def test_fullscreen_focused_detected():
    assert fs.active_window_is_fullscreen(run=_active_run()) is True


def test_windowed_focused_not_fullscreen():
    run = _active_run(states="_NET_WM_STATE_MAXIMIZED_HORZ, _NET_WM_STATE_FOCUSED")
    assert fs.active_window_is_fullscreen(run=run) is False


def test_no_active_window_is_safe_noop():
    run = _run_factory([("_NET_ACTIVE_WINDOW", _Done(""))])
    assert fs.active_window_is_fullscreen(run=run) is False


def test_missing_xprop_is_safe_noop():
    def _boom(argv, **kw):
        raise FileNotFoundError("no xprop")
    assert fs.active_window_is_fullscreen(run=_boom) is False


def test_failing_xprop_is_safe_noop():
    def _rc(argv, **kw):
        return _Done("", returncode=1)
    assert fs.active_window_is_fullscreen(run=_rc) is False


def test_garbage_output_never_raises():
    def _junk(argv, **kw):
        return _Done("\x00\xff not xprop at all \n" * 3)
    assert fs.active_window_is_fullscreen(run=_junk) is False


# -- visibility rule (pure) ---------------------------------------------------

def test_fullscreen_focused_hides_visible_orb():
    action, auto = fs.next_visibility(True, True, False)
    assert (action, auto) == ("hide", True)


def test_windowed_focus_restores_auto_hidden_orb():
    action, auto = fs.next_visibility(False, False, True)
    assert (action, auto) == ("show", False)


def test_user_closed_stays_closed_under_fullscreen():
    # Withdrawn WITHOUT our flag = the owner closed it: never force anything.
    assert fs.next_visibility(True, False, False) == ("none", False)
    assert fs.next_visibility(False, False, False) == ("none", False)


def test_windowed_visible_orb_untouched():
    assert fs.next_visibility(False, True, False) == ("none", False)


def test_already_auto_hidden_stays_hidden():
    assert fs.next_visibility(True, True, True) == ("none", True)
    assert fs.next_visibility(True, False, True) == ("none", True)


# -- app wiring (no Tk: drive the rule method on a bare instance) --------------

from contextlib import contextmanager


@contextmanager
def _bare_orb(probe, visible, auto):
    import unittest.mock as _mock

    from voicebridge.orb import app as orb_app
    orb = object.__new__(orb_app.VoiceOrb)
    orb._fullscreen_probe = probe
    orb._fs_auto_hidden = auto
    calls: list = []
    orb.hide_to_background = lambda: calls.append("hide")  # type: ignore[method-assign]
    orb.show = lambda: calls.append("show")  # type: ignore[method-assign]
    orb._calls = calls
    # ``visible`` is a read-only property on the class: stub it per-test.
    with _mock.patch.object(orb_app.VoiceOrb, "visible",
                            new_callable=_mock.PropertyMock,
                            return_value=visible):
        yield orb


def test_app_rule_hides_on_fullscreen():
    from voicebridge.orb import app as orb_app
    with _bare_orb(lambda: True, True, False) as orb:
        orb_app.VoiceOrb._apply_fullscreen_rule(orb)
        assert orb._calls == ["hide"]
        assert orb._fs_auto_hidden is True


def test_app_rule_restores_when_fullscreen_leaves():
    from voicebridge.orb import app as orb_app
    with _bare_orb(lambda: False, False, True) as orb:
        orb_app.VoiceOrb._apply_fullscreen_rule(orb)
        assert orb._calls == ["show"]
        assert orb._fs_auto_hidden is False


def test_app_rule_never_shows_user_closed_orb():
    from voicebridge.orb import app as orb_app
    with _bare_orb(lambda: False, False, False) as orb:
        orb_app.VoiceOrb._apply_fullscreen_rule(orb)
        assert orb._calls == []


def test_app_rule_probe_failure_never_raises_or_hides():
    from voicebridge.orb import app as orb_app
    def _boom():
        raise RuntimeError("no X11")
    with _bare_orb(_boom, True, False) as orb:
        orb_app.VoiceOrb._apply_fullscreen_rule(orb)
        assert orb._calls == []
        assert orb._fs_auto_hidden is False


def test_app_source_never_takes_focus():
    # Focus discipline preserved: restore path uses show() (deiconify+lift),
    # and no focus-forcing call is introduced anywhere.
    from pathlib import Path as _P
    src = (_P(__file__).resolve().parent.parent / "voicebridge" / "orb" / "app.py").read_text()
    for banned in ("focus_force", "focus_set", "grab_set", "grab_current"):
        assert banned not in src
