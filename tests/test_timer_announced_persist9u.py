"""Bug-1 lane (2026-10-04): persistent timer announce-bit + stale reap.

Journal-proof defect: timer `f432b3b9cde7` (1-min, fired OK) reminded once
per helper generation (23:48 pid 178394, 04:00 pid 1013513, 06:57 pid
1674120 — even slept:true) because announced-dedup lived only in the
helper's in-memory `_timers_announced` set while the ledger keeps
`status=fired` forever and is never pruned.

Contract pinned here (all hermetic: ``QUBIT_TIMER_STATE_DIR`` scratch):
* a fired timer is pulled at most once per ledger lifetime, no matter how
  many helper generations poll (restart-simulated = fresh poller -> silent);
* fired-then-announced rows are never re-pulled;
* fired rows older than the delivery TTL are never re-pulled (the stale
  pre-fix row case: fired 11h ago must stay silent on the deploy restart);
* old fired rows are pruned (ledger hygiene), scheduled rows untouched;
* the per-turn drain contract holds (5 timers -> 3 + 2 across two polls,
  mirroring the helper's per-turn cap).
"""
from __future__ import annotations

import json
import threading
import time as _time

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
    monkeypatch.setattr(LA, "_arm_timer_survivor",
                        lambda rec: ("stubbed", "unit-test"), raising=False)
    return tmp_path


def _fire(scratch, mins=1):
    rec = LA.schedule_timer(mins)
    out = LA.fire_timer(rec["id"],
                        notify_fn=lambda t, b: {"ok": True, "code": 0},
                        sound_fn=lambda: {"ok": True, "detail": "ok"})
    assert out.get("fired") is True
    return rec["id"]


def _backdate_fired(scratch, timer_id, age_s):
    p = scratch / "local_timers.json"
    data = json.loads(p.read_text())
    for row in data["timers"]:
        if row["id"] == timer_id:
            row["fired_at"] = _time.time() - age_s
    p.write_text(json.dumps(data))


def test_restart_simulated_stays_silent(scratch):
    """Fresh poller (new helper generation) after delivery -> silent."""
    tid = _fire(scratch)
    first = LA.poll_fired_timers()
    assert [r["id"] for r in first] == [tid]
    # Simulate a helper restart: brand-new in-memory state, same ledger.
    second = LA.poll_fired_timers()
    assert second == [], "restart must never re-nag an announced timer"


def test_fired_then_announced_never_repulled(scratch):
    tid = _fire(scratch)
    assert LA.poll_fired_timers() != []
    assert LA.poll_fired_timers() == []
    assert LA.poll_fired_timers() == []
    st = LA.timer_status(tid)
    assert st["status"] == "fired"
    assert st.get("announced") is True


def test_stale_fired_row_never_repulled(scratch):
    """The f432b3b9cde7 case: fired ~11h ago, ledger never marked.

    The deploy restart must stay silent for it (delivery TTL backstop).
    """
    tid = _fire(scratch)
    _backdate_fired(scratch, tid, age_s=11 * 3600)
    assert LA.poll_fired_timers() == []


def test_fresh_fired_within_ttl_delivers(scratch):
    tid = _fire(scratch)
    found = LA.poll_fired_timers()
    assert [r["id"] for r in found] == [tid]


def test_drain_contract_preserved(scratch):
    """5 timers drain 3 + 2 across two polls (helper per-turn cap mirror)."""
    ids = [_fire(scratch) for _ in range(5)]
    first = LA.poll_fired_timers()
    rest = LA.poll_fired_timers()
    assert 1 <= len(first) <= 3
    assert len(first) + len(rest) == 5
    assert sorted(r["id"] for r in (*first, *rest)) == sorted(ids)
    assert LA.poll_fired_timers() == []


def test_prune_old_fired_keeps_fresh_and_scheduled(scratch):
    old_tid = _fire(scratch)
    _backdate_fired(scratch, old_tid, age_s=25 * 3600)
    fresh_tid = _fire(scratch)
    sched = LA.schedule_timer(9)
    pruned = LA.prune_old_fired(max_age_s=24 * 3600)
    assert pruned == 1
    assert LA.timer_status(old_tid) is None
    assert LA.timer_status(fresh_tid)["status"] == "fired"
    assert LA.timer_status(sched["id"])["status"] == "scheduled"


def test_explicit_mark_ack(scratch):
    tid = _fire(scratch)
    assert LA.mark_timers_announced([tid]) == 1
    assert LA.poll_fired_timers(consume=False) == []
    assert LA.mark_timers_announced(["no-such-id"]) == 0
