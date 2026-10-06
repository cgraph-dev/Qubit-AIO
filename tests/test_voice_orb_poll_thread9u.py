"""Orb poll off the mainloop: worker thread + queue handoff (2026-10-05).

Root causes (proven live): ``_poll_tick`` ran 2-3 blocking HTTP calls
(status + log + every-5th settings, 2 s timeouts each) on the Tk
mainloop, so 33 ms frame ticks starved and the animation froze exactly
during turns; POLL_MS=2000 + MIN_DWELL 1 s lagged color 0-3 s behind
reality; ``orb.state`` transition rows were invisible via
``GET /api/voice-edit/log`` (``logview.VOICE_PREFIXES`` lacked ``orb.``).

Contract pinned here:
- HTTP fetching runs on a daemon worker thread; results cross to the
  mainloop through a ``queue.Queue`` (newest wins, stale dropped).
  The worker never touches a widget — only HTTP + queue.
- ``_poll_tick`` drains the queue and applies on the mainloop; it only
  fetches synchronously when no worker is alive (tests/--once-style
  paths), never while a worker owns fetching.
- POLL_MS is 500 in both views; status/log timeouts are 2 s.
- ``orb.`` is an allowed log prefix (read-only visibility, no text).

Hermetic: fake clients/queues, bare orbs (no Tk/Qt, no display).
"""
from __future__ import annotations

import inspect
import queue
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.orb import app as orb_app  # noqa: E402
from voicebridge.orb import client as orb_client  # noqa: E402
from voicebridge.voice_edit import logview  # noqa: E402


# -- cadence / timeouts / prefix --------------------------------------------

def test_poll_ms_is_500_both_views():
    # Owner 2026-10-06: 1s poll + dwell lagged color up to ~2s; halved
    # to 500ms for "super responsive" state changes (localhost only).
    from voicebridge.orb import qt_app as qa
    assert orb_app.POLL_MS == 500
    assert qa.POLL_MS == orb_app.POLL_MS == 500


def _timeout_recorder(payload):
    import json as _json

    seen = {}

    class _Resp:
        def read(self):
            return _json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(req, timeout=10.0):
        seen[req.full_url] = timeout
        return _Resp()

    return seen, _open


def test_status_and_log_timeouts_are_2s():
    seen, _open = _timeout_recorder({"ok": True, "events": []})
    cli = orb_client.OrbClient("http://127.0.0.1:8890", "t", opener=_open)
    cli.get_status()
    cli.get_log(60)
    assert seen["http://127.0.0.1:8890/agent/api/voice-edit/status"] == 2
    assert seen["http://127.0.0.1:8890/agent/api/voice-edit/log?limit=60"] == 2


def test_orb_prefix_visible_in_logview():
    assert "orb." in logview.VOICE_PREFIXES
    assert logview._is_voice_event("orb.state") is True


# -- shared fetch helper (worker body; no widgets, never raises) -------------

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


def test_fetch_payload_settings_every_5th_tick_only():
    cli = _FakeClient()
    for tick in range(1, 7):
        payload = orb_client.fetch_poll_payload(cli, tick)
        assert payload["status"] is not None
    assert cli.status_calls == 6, cli.status_calls
    assert cli.log_calls == 6, cli.log_calls
    assert cli.settings_calls == 2, cli.settings_calls  # ticks 1 and 6


def test_fetch_payload_never_fetches_settings_offline():
    cli = _DeadClient()
    payload = orb_client.fetch_poll_payload(cli, 1)
    assert payload["status"] is None
    assert payload["settings"] is None
    assert cli.settings_calls == 0, cli.settings_calls


# -- queue handoff ordering ---------------------------------------------------

def test_queue_handoff_newest_wins_stale_dropped():
    q: queue.Queue = queue.Queue(maxsize=1)
    orb_client.offer_latest(q, {"status": {"active_requests": 0},
                                "events": [], "settings": None})
    orb_client.offer_latest(q, {"status": {"active_requests": 3},
                                "events": [], "settings": None})
    latest = orb_client.drain_latest(q)
    assert latest is not None
    assert latest["status"] == {"active_requests": 3}
    assert orb_client.drain_latest(q) is None  # queue empty after drain


class _FakeRoot:
    def after(self, *a, **k):
        return None


def _bare_orb_with_queue(client, payload):
    orb = object.__new__(orb_app.VoiceOrb)
    orb._client = client
    orb._display = None
    orb.state = "idle"
    orb.offline = False
    orb.ingame_override = False
    orb.root = _FakeRoot()
    orb._apply_fullscreen_rule = lambda: None
    orb._poll_queue = queue.Queue(maxsize=1)
    orb_client.offer_latest(orb._poll_queue, payload)
    return orb


def test_poll_tick_prefers_queue_over_sync_fetch():
    class _ExplodingClient(_FakeClient):
        def get_status(self):
            raise AssertionError("mainloop must not block on HTTP "
                                 "while the worker owns fetching")

    orb = _bare_orb_with_queue(
        _ExplodingClient(),
        {"status": {"active_requests": 0}, "events": [],
         "settings": {"waveform_ingame": True}})
    orb_app.VoiceOrb._poll_tick(orb)
    assert orb.state == "idle", orb.state
    assert orb.offline is False
    assert orb.ingame_override is True


def test_poll_tick_without_worker_still_fetches_synchronously():
    # Bare orbs (tests, worker-less paths) keep the old synchronous
    # behavior: no queue, no thread -> direct fetch + apply.
    cli = _FakeClient()
    orb = object.__new__(orb_app.VoiceOrb)
    orb._client = cli
    orb._display = None
    orb.state = "idle"
    orb.offline = False
    orb.ingame_override = False
    orb.root = _FakeRoot()
    orb._apply_fullscreen_rule = lambda: None
    orb_app.VoiceOrb._poll_tick(orb)
    assert orb.state == "idle", orb.state
    assert cli.status_calls == 1, cli.status_calls


def test_poll_worker_never_touches_widgets():
    # The worker entry runs HTTP + queue only. Any widget/mainloop call
    # here would execute off-mainloop and race Tk/Qt: fail closed.
    src = inspect.getsource(orb_app.VoiceOrb._poll_loop)
    for banned in ("self.root", "self.canvas", "self.show(",
                   "hide_to_background", "toggle_visible", "_display",
                   "_apply_fullscreen_rule", "DisplayState"):
        assert banned not in src, (
            f"worker must never touch widgets/state ({banned})")
    assert "queue" in src.lower() or "_fetch_once" in src
