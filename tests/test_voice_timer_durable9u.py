"""Lane 1 (2026-10-03) — durable one-shot voice timers (TDD).

Journal-proof defect: an owner 1-minute timer staged->settled->spoke
"Timer set for 1 minute. I'll notify you." and NOTHING fired 60 s later.
Root cause (verified in code, see module docstring of the fix):
``_a_timer`` armed only an in-process ``threading.Timer`` (daemon-inheriting:
dies with the executing worker/helper process), emitted ZERO audit events,
and ignored notify-send/paplay failures silently.

Contract pinned here (all hermetic: ``QUBIT_TIMER_STATE_DIR`` scratch):
* setting a timer persists a ledger record SYNCHRONOUSLY (before reply);
* the claim is exactly-once (second claim returns None);
* firing records typed notify/sound evidence on the record (never silent);
* clamp 1..180 min is kept;
* arming failure degrades LOUD (no phantom "I'll notify you");
* firing touches only the timer ledger (never owner chat/session state).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

import pytest

from voicebridge.local import actions as LA


@pytest.fixture()
def scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("QUBIT_TIMER_STATE_DIR", str(tmp_path))

    class _FakeTimer:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr(threading, "Timer", _FakeTimer)
    # Never spawn real survivor processes in unit tests.
    monkeypatch.setattr(LA, "_arm_timer_survivor",
                        lambda rec: ("stubbed", "unit-test"), raising=False)
    return tmp_path


def _ledger(scratch):
    p = scratch / "local_timers.json"
    assert p.is_file(), "timer ledger must exist synchronously after set"
    return json.loads(p.read_text())


def test_set_persists_synchronously(scratch):
    reply = LA._a_timer({"minutes": 1})
    data = _ledger(scratch)
    assert len(data["timers"]) == 1
    rec = data["timers"][0]
    assert rec["mins"] == 1 and rec["status"] == "scheduled"
    assert "restart" in reply.lower(), reply


def test_claim_is_exactly_once(scratch):
    rec = LA.schedule_timer(5)
    first = LA.claim_timer(rec["id"])
    assert first is not None and first["id"] == rec["id"]
    assert LA.claim_timer(rec["id"]) is None
    st = LA.timer_status(rec["id"])
    assert st["status"] in ("firing", "fired")


def test_fire_records_notify_evidence(scratch):
    rec = LA.schedule_timer(5)
    calls = []

    def _notify(title, body):
        calls.append((title, body))
        return {"ok": True, "code": 0}

    def _sound():
        calls.append(("sound",))
        return {"ok": True, "detail": "alarm played"}

    out = LA.fire_timer(rec["id"], notify_fn=_notify, sound_fn=_sound)
    assert out["fired"] is True
    st = LA.timer_status(rec["id"])
    assert st["status"] == "fired"
    assert st["notify"]["ok"] is True
    assert st["sound"]["ok"] is True
    assert calls, "notify/sound seams must be exercised, never skipped"


def test_fire_notify_failure_is_loud(scratch):
    rec = LA.schedule_timer(5)

    def _bad_notify(title, body):
        return {"ok": False, "detail": "notify-send exited with code 1"}

    out = LA.fire_timer(rec["id"], notify_fn=_bad_notify,
                        sound_fn=lambda: {"ok": False, "detail": "no player"})
    assert out["fired"] is True  # the claim still happened exactly once
    st = LA.timer_status(rec["id"])
    assert st["status"] == "fired"
    assert st["notify"]["ok"] is False
    assert "notify-send" in st["notify"]["detail"]


def test_clamp_kept(scratch):
    LA._a_timer({"minutes": 0})
    LA._a_timer({"minutes": 999})
    data = _ledger(scratch)
    mins = sorted(r["mins"] for r in data["timers"])
    assert mins == [1, 180]


def test_arm_failure_degrades_loud(scratch, monkeypatch):
    def _boom(rec):
        raise RuntimeError("no systemd, no setsid")
    monkeypatch.setattr(LA, "_arm_timer_survivor", _boom)
    reply = LA._a_timer({"minutes": 1})
    assert "even if I restart" not in reply.lower(), reply
    assert ("warn" in reply.lower() or "only" in reply.lower()
            or "could not" in reply.lower()), reply
    data = _ledger(scratch)
    assert data["timers"][0]["status"] == "scheduled"


def test_main_entry_fires_timer(scratch):
    rec = LA.schedule_timer(5)
    env = dict(os.environ, QUBIT_TIMER_STATE_DIR=str(scratch))
    proc = subprocess.run(
        [sys.executable, "-m", "voicebridge.local.actions",
         "--fire-timer", rec["id"]],
        capture_output=True, text=True, timeout=60, env=env,
        cwd="/home/trick/Projects/Repos/qubit-voicebridge")
    assert proc.returncode == 0, proc.stderr[-500:]
    assert LA.timer_status(rec["id"])["status"] == "fired"


def test_fire_writes_only_the_ledger(scratch):
    rec = LA.schedule_timer(5)
    before = {p.name for p in scratch.iterdir()}
    LA.fire_timer(rec["id"],
                  notify_fn=lambda t, b: {"ok": True, "code": 0},
                  sound_fn=lambda: {"ok": True, "detail": "ok"})
    after = {p.name for p in scratch.iterdir()}
    assert after == before | {"local_timers.json"}


def test_poll_fired_timers_reader(scratch):
    rec = LA.schedule_timer(5)
    assert LA.poll_fired_timers(since=0) == []
    LA.fire_timer(rec["id"],
                  notify_fn=lambda t, b: {"ok": True, "code": 0},
                  sound_fn=lambda: {"ok": True, "detail": "ok"})
    found = LA.poll_fired_timers(since=0)
    assert [r["id"] for r in found] == [rec["id"]]


def test_stale_firing_claim_is_reaped(scratch):
    import time as _t
    rec = LA.schedule_timer(5)
    assert LA.claim_timer(rec["id"]) is not None
    assert LA.claim_timer(rec["id"]) is None  # fresh claim holds
    assert LA.reap_stale_firing(max_age_s=3600) == 0
    # Simulate a firer killed mid-fire long ago.
    import json as _j
    p = scratch / "local_timers.json"
    data = _j.loads(p.read_text())
    for row in data["timers"]:
        if row["id"] == rec["id"]:
            row["claimed_at"] = _t.time() - 7200.0
    p.write_text(_j.dumps(data))
    assert LA.reap_stale_firing(max_age_s=300) == 1
    st = LA.timer_status(rec["id"])
    assert st["status"] == "scheduled" and st["reaped"] == 1
    assert LA.claim_timer(rec["id"]) is not None  # fires again, once


def test_main_entry_unknown_id_exits_two(scratch):
    env = dict(os.environ, QUBIT_TIMER_STATE_DIR=str(scratch))
    proc = subprocess.run(
        [sys.executable, "-m", "voicebridge.local.actions",
         "--fire-timer", "no-such-timer"],
        capture_output=True, text=True, timeout=60, env=env,
        cwd="/home/trick/Projects/Repos/qubit-voicebridge")
    assert proc.returncode == 2, proc.stderr[-500:]


def test_main_entry_already_fired_exits_zero(scratch):
    rec = LA.schedule_timer(5)
    env = dict(os.environ, QUBIT_TIMER_STATE_DIR=str(scratch))
    args = [sys.executable, "-m", "voicebridge.local.actions",
            "--fire-timer", rec["id"]]
    kw = dict(capture_output=True, text=True, timeout=60, env=env,
              cwd="/home/trick/Projects/Repos/qubit-voicebridge")
    assert subprocess.run(args, **kw).returncode == 0
    assert subprocess.run(args, **kw).returncode == 0  # idempotent no-op
    assert LA.timer_status(rec["id"])["status"] == "fired"
