"""Orb poll tick: settings refresh cadence (2026-10-04).

The tick ran three blocking HTTP round-trips (status + log + settings,
up to 10 s each) on the Tk mainloop while the UI cannot repaint — a slow
bridge wedged the painted state for the whole tick. The ingame override
flips rarely, so settings now refresh every 5th tick and never while
offline; status + log still refresh every tick (they drive the state).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.orb import app as orb_app  # noqa: E402


class _FakeClient:
    def __init__(self, status=None):
        self.status_payload = (
            {"active_requests": 0} if status is None else status)
        self.settings_calls = 0
        self.status_calls = 0
        self.log_calls = 0

    def get_status(self):
        self.status_calls += 1
        return dict(self.status_payload)

    def get_log(self, limit=60):
        self.log_calls += 1
        return {"events": []}

    def get_settings(self):
        self.settings_calls += 1
        return {"waveform_ingame": False}


class _DeadClient(_FakeClient):
    def get_status(self):
        self.status_calls += 1
        raise OSError("bridge down")


class _FakeRoot:
    def after(self, *a, **k):
        return None


def _bare_orb(client):
    orb = object.__new__(orb_app.VoiceOrb)
    orb._client = client
    orb._display = None
    orb.state = "idle"
    orb.offline = False
    orb.ingame_override = False
    orb.root = _FakeRoot()
    orb._apply_fullscreen_rule = lambda: None
    return orb


def test_settings_refreshes_on_first_and_sixth_tick_only():
    cli = _FakeClient()
    orb = _bare_orb(cli)
    for _ in range(6):
        orb_app.VoiceOrb._poll_tick(orb)
    assert cli.status_calls == 6, cli.status_calls
    assert cli.log_calls == 6, cli.log_calls
    assert cli.settings_calls == 2, cli.settings_calls  # ticks 1 and 6


def test_settings_never_fetched_while_offline():
    cli = _DeadClient()
    orb = _bare_orb(cli)
    for _ in range(6):
        orb_app.VoiceOrb._poll_tick(orb)
    assert orb.offline is True
    assert orb.state == "error", orb.state
    assert cli.settings_calls == 0, cli.settings_calls


def test_state_still_resolves_every_tick():
    cli = _FakeClient()
    orb = _bare_orb(cli)
    orb_app.VoiceOrb._poll_tick(orb)
    assert orb.state == "idle", orb.state
    assert orb.offline is False


def test_state_transitions_are_journaled(monkeypatch):
    # Stuck-state reports need transition rows: idle->speaking->idle must
    # leave exactly two orb.state rows (2026-10-04: zero rows existed
    # because the orb never initialized journal logging).
    import time as _time

    import voicebridge.logging_setup as _ls

    rows = []
    monkeypatch.setattr(_ls, "log_event",
                        lambda event, **kw: rows.append((event, kw)))

    class _SpeakingClient(_FakeClient):
        def __init__(self):
            super().__init__()
            self._n = 0

        def get_log(self, limit=60):
            self.log_calls += 1
            self._n += 1
            if self._n == 1:
                return {"events": [{"ts": _time.time(), "event":
                                    "voice_edit.helper.speak"}]}
            return {"events": []}

    cli = _SpeakingClient()
    orb = _bare_orb(cli)
    orb_app.VoiceOrb._poll_tick(orb)
    assert orb.state == "speaking", orb.state
    import time as _t2
    _t2.sleep(0.05)
    orb_app.VoiceOrb._poll_tick(orb)
    assert orb.state == "idle", orb.state
    got = [(e, kw.get("frm"), kw.get("to")) for e, kw in rows
           if e == "orb.state"]
    assert got == [("orb.state", "idle", "speaking"),
                   ("orb.state", "speaking", "idle")], got


def test_qt_poll_tick_shares_the_cadence():
    # The Qt view is the one actually running (orb.qt_stats in the unit
    # journal) — its tick carries the same every-5th-tick settings rule.
    pytest = __import__("pytest")
    qt_app = pytest.importorskip("voicebridge.orb.qt_app")
    if qt_app.QWidget is None:
        pytest.skip("Qt not importable here")
    cli = _FakeClient()
    orb = qt_app.QtVoiceOrb.__new__(qt_app.QtVoiceOrb)
    orb._client = cli
    orb._display = None
    orb.state = "idle"
    orb.offline = False
    orb.ingame_override = False
    orb.root = None
    orb._apply_fullscreen_rule = lambda: None
    for _ in range(6):
        qt_app.QtVoiceOrb._poll_tick(orb)
    assert cli.status_calls == 6, cli.status_calls
    assert cli.settings_calls == 2, cli.settings_calls  # ticks 1 and 6
    assert orb.state == "idle", orb.state
