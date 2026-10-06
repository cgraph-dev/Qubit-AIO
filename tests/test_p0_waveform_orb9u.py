"""P0 waveform/orb regression pins (audit 2026-10-05, TDD red-first).

Each test below failed against the pre-fix tree (reason in the docstring);
the fix it pins lives in ``voicebridge/orb/*.py`` (disjoint lane files only).
Hermetic: no display, no network, no service contact.
"""
from __future__ import annotations

import time


# -- P0-4/P0-5: click + settings HTTP budgets ---------------------------------
# Pre-fix: begin_session/cancel_request/get_settings used timeout=10, so one
# click could block the Tk mainloop up to 10 s and every 5th worker round
# stalled status+log behind a 10 s settings fetch.

class _TimeoutRecorder:
    """Fake urlopen: records the timeout kwarg, returns an empty JSON body."""

    def __init__(self) -> None:
        self.timeouts: list = []

    def __call__(self, req, timeout=None):
        self.timeouts.append(timeout)

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"{}"

        return _Resp()


def test_p0_click_path_timeouts_bounded_2s():
    from voicebridge.orb import client as OC

    rec = _TimeoutRecorder()
    cli = OC.OrbClient("http://127.0.0.1:8890", "tok", opener=rec)
    cli.begin_session()
    cli.cancel_request("abc123")
    for t in rec.timeouts:
        assert t is not None and float(t) <= 2.0, t


def test_p0_settings_fetch_timeout_bounded_2s():
    from voicebridge.orb import client as OC

    rec = _TimeoutRecorder()
    cli = OC.OrbClient("http://127.0.0.1:8890", "tok", opener=rec)
    cli.get_settings()
    assert rec.timeouts and all(float(t) <= 2.0 for t in rec.timeouts), \
        rec.timeouts


# -- P0-6: in-flight thinking capped by turn-evidence age ----------------------
# Pre-fix: resolve_orb_state returned "thinking" on any active>0 with no age
# check, so a leaked active_requests count stuck the orb yellow forever;
# THINK_WINDOW_S was a dead constant (nothing referenced it).

def _ev(name: str, age_s: float, now: float) -> dict:
    return {"ts": now - age_s, "event": name}


def test_p0_thinking_capped_when_turn_evidence_stale():
    from voicebridge.orb import state as S

    assert S.THINK_WINDOW_S == 12.0  # the cap constant is the ceiling
    now = time.time()
    # active>0 but the newest transcript marker is 30 s old -> must fall
    # through (idle), not stick yellow.
    events = [_ev("stt.final", 30.0, now)]
    assert S.resolve_orb_state({"active_requests": 3}, events, now) != \
        "thinking"


def test_p0_thinking_live_while_turn_evidence_fresh():
    from voicebridge.orb import state as S

    now = time.time()
    events = [_ev("stt.final", 2.0, now)]
    assert S.resolve_orb_state({"active_requests": 1}, events, now) == \
        "thinking"


def test_p0_thinking_fail_closed_without_evidence():
    from voicebridge.orb import state as S

    # No turn evidence at all but the status says in-flight: trust the
    # status (fail-closed) rather than second-guessing it to idle.
    now = time.time()
    assert S.resolve_orb_state({"active_requests": 1}, [], now) == "thinking"


# -- P0-7: fullscreen probe subprocess budget ----------------------------------
# Pre-fix: two blocking xprop runs at timeout=3 each on the Tk/Qt mainloop
# (up to ~6 s of wedged frames per poll tick on a sick X server).

def test_p0_fullscreen_probe_timeouts_bounded():
    from voicebridge.orb import fullscreen as FS

    seen: list = []

    class _Done:
        stdout = ("_NET_ACTIVE_WINDOW(WINDOW): window id # 0x7e00004\n")

    def _run(argv, **kw):
        seen.append(kw.get("timeout"))
        cmd = " ".join(str(a) for a in argv)
        if "-id" in cmd:
            d = _Done()
            d.stdout = ("_NET_WM_STATE(ATOM) = "
                        "_NET_WM_STATE_FULLSCREEN\n")
            return d
        return _Done()

    assert FS.active_window_is_fullscreen(run=_run) is True
    assert seen and all(t is not None and float(t) <= 0.6 for t in seen), \
        seen


# -- P0-3: Qt talk-meter parity with Tk ---------------------------------------
# Pre-fix: Qt paintEvent read resolve_params directly and never sampled
# _frame_level/mic_level (Tk samples in _apply_poll_payload); the Qt orb
# pumped at full amplitude in silence.

def test_p0_qt_listening_level_modulates_like_tk():
    from voicebridge.orb import qt_app as QA

    for bi in (0, 2, 4):
        _rgb_none, a_none, _w = QA.band_pen(bi, "listening")
        _rgb_calm, a_calm, _ = QA.band_pen(bi, "listening", level=0.0)
        _rgb_loud, a_loud, _ = QA.band_pen(bi, "listening", level=1.0)
        assert a_calm < a_none, (bi, a_calm, a_none)  # silence settles
        assert a_loud == a_none, (bi, a_loud, a_none)  # full == loop


def test_p0_qt_other_states_byte_identical_with_level():
    from voicebridge.orb import qt_app as QA

    for st in ("idle", "thinking", "speaking", "error"):
        assert QA.band_pen(1, st, level=0.9) == QA.band_pen(1, st), st
        assert QA.core_stops(st, level=0.9) == QA.core_stops(st), st
        assert QA.ring_pen(0.3, 0, st, level=0.9) == \
            QA.ring_pen(0.3, 0, st), st
